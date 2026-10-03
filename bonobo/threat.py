"""Ordinary play's threat layer: who can hurt us, how soon, and whether to fight, walk away or wall in. Pure. The old rule was two if-statements — "hostile within 5 blocks and hp > 10 → attack it", "hp ≤ 10 and hostile within 6 → run straight away" — so a skeleton twelve blocks out shot the agent dead while it kept mining, and a zombie was punched bare-handed for thirty seconds. Neither statement knew what the enemy does, only where it stands. Same shape as the fight planner: threats are (centre, reach, velocity, kind) rows with several futures each (combat_model.hypotheses), the state is a dict, the currency is seconds and hit points, and the answer is the cheapest option — fight when the expected damage of killing everything leaves a reserve, otherwise leave its reach, or wall in when leaving costs more than building does. Numbers live in play.toml under [mobs], [player], [engage]."""

import math
from typing import Any

from . import beliefs, estimate, kernel, lifecycle
from . import formulas, game
from .beliefs import MOBS, protection
from .estimate import row
from .game import ARROWS

ENGAGE = beliefs.CONFIG["engage"]
# the account's length is `estimate.horizon_s`, only one (two horizons measured over 4 s and charged over 20)

class Decision:
    """kind: ignore | fight | evade | wall_in. target: entity id (fight) or spot (evade). why: for the log."""

    def __init__(self, kind, target=None, why="", cost_hp=0.0):
        self.kind, self.target, self.why, self.cost_hp = kind, target, why, cost_hp

    def __repr__(self):
        return f"Decision({self.kind}, {self.target}, {self.why!r})"

# -- perception → rows

# facts about mobs: which ones are worth noticing is the mob's property; fighting is priced below

NEUTRAL_MOBS = {"minecraft:zombified_piglin", "minecraft:piglin", "minecraft:enderman", "minecraft:wolf",
                "minecraft:bee", "minecraft:iron_golem", "minecraft:polar_bear", "minecraft:llama", "minecraft:panda",
                "minecraft:dolphin", "minecraft:spider", "minecraft:cave_spider"}

def aggro(e, context=None) -> bool:
    """Pure: is this mob after us, from its reading and ours — the one place neutral is told from hostile.
    A provoked mob (the jar's `angry`) always is; a hostile kind always is; a spider only out of daylight; a piglin
    unless we wear gold; every other neutral (enderman, zombified piglin, wolf …) only when provoked.
    `context`: {"day": the sun is up where we are, "gold_worn": a golden armour piece on}."""
    kind = e.get("type")
    if e.get("provoked") or e.get("angry") or kind not in NEUTRAL_MOBS:     # perception.read_combat
        return True
    ctx = context or {}
    if kind in ("minecraft:spider", "minecraft:cave_spider"):
        return not ctx.get("day", True)
    if kind == "minecraft:piglin":
        return not ctx.get("gold_worn", False)
    return False

def context_of(state, kit) -> dict:
    """Pure: what `aggro` reads from our side — daylight (the overworld's day half) and gold worn (the kit)."""
    day = (state.get("dimension", "minecraft:overworld") == "minecraft:overworld"
           and int(state.get("timeOfDay", 6000)) % 24000 < 12000)
    return {"day": day, "gold_worn": bool((kit or {}).get("gold_worn"))}

def awareness(e, here, context=None) -> float:
    """0..1: how much of this mob's damage is coming at us (a neutral not after us is 0)."""

    if not aggro(e, context):
        return 0.0
    # being in the hazard table is the hostility test (reading `hostile` too dropped the dragon's parts)
    d = math.dist(here, (e["x"], e["y"], e["z"]))
    notice = float(ENGAGE["notice_r"])
    if d <= notice:
        return 1.0
    return max(0.0, 1.0 - (d - notice) / notice)

def threat_rows(near, memory, now, kinds, here, context=None, reaches=None):
    """(centre, reach, velocity, kind, aware, dps) for every entity whose type is in `kinds` ({type: reach}).
    `reaches(pos, reach)` → False when no way leads from there to us: a mob that cannot come, nor is at us already
    (in reach, shooting, charging, provoked), is no threat — behind rock it is only noise."""

    out = []
    for e in near or []:
        kind = e.get("type")
        if kind not in kinds:
            continue
        if e.get("health") is not None and float(e["health"]) <= 0:
            # dying: listed through its death animation (~1 s), no longer a target — as the nearest row it was
            # chosen again and again, 'target not found' with a live zombie beside us (fight_zombie_3 23:46:01)
            continue
        pos = (e["x"], e["y"], e["z"])
        vel = (0.0, 0.0, 0.0)
        key = e.get("id")
        if key is not None:
            prev = memory.get(key)
            if prev and 0.01 < now - prev[1] < 2.0:
                dt = now - prev[1]
                vel = tuple((pos[i] - prev[0][i]) / dt for i in range(3))
            memory[key] = (pos, now)
        seen = awareness(e, here, context)
        if seen <= 0.0:
            continue
        if reaches is not None and not at_us(e) and reaches(pos, kinds[kind], kind) is False:
            continue
        out.append(row(pos, kinds[kind], vel, kind, aware=seen, dps=e.get("dps")))
    for key in [k for k, (_, t) in memory.items() if now - t > 10.0]:
        del memory[key]          # or the table grows for the length of the session
    return out

CLIMBERS = ("minecraft:spider", "minecraft:cave_spider")      # up any wall: a drop is no barrier to them


def at_us(e):
    """Pure: the mob is at us already (perception.read_combat's fields): in reach, shooting or charging, provoked."""
    return bool(e.get("reach_now") or e.get("busy") or e.get("provoked"))

def hostile_rows(near, memory, now, here, context=None, reaches=None):
    """Rows for the mobs the table knows."""

    kinds = {k: float(m["reach"]) for k, m in MOBS.items()}
    return threat_rows(near or [], memory, now, kinds, here=here, context=context, reaches=reaches)

BAIT_R = 6.0      # blocks: inside the fuse's range (7), outside most of the blast

def bait_spot(here, creeper, lit, clear):
    """Pure: where to stand baiting a creeper — out to `clear` while it walks, hold there, then a short step to
    BAIT_R once lit (still inside 7: it blows, we are past the blast)."""
    d = math.dist(here, creeper) or 1e-6
    want = BAIT_R if lit else clear
    if not lit and d >= clear:
        return tuple(round(c) for c in here)               # hold: let it close to lit
    if lit and d >= BAIT_R:
        return tuple(round(c) for c in here)
    k = want / d
    return (round(creeper[0] + (here[0] - creeper[0]) * k), round(here[1]), round(creeper[2] + (here[2] - creeper[2]) * k))

def bait_blast(d, attack, prot):
    """Pure: a creeper's blast at `d` blocks (falls off to nothing at 6)."""
    return round(formulas.explosion_damage(game.EXPLOSION_POWER["minecraft:creeper"], d) * (1.0 - prot), 2)

def bait_option(here, hazards, ids, creepers, lit, clear, prot):
    first = min(creepers, key=lambda i: math.dist(here, hazards[i][0]))
    h = hazards[first]
    is_lit = ids[first] in lit
    spot = bait_spot(here, h[0], is_lit, clear)
    walk_s = math.dist(here, spot) / float(beliefs.PLAYER["speed"])
    return Option("bait", spot, bait_blast(BAIT_R, MOBS[h[3]]["attack"], prot), round(walk_s + float(ENGAGE["fuse_s"]), 2),
                  "bait it: " + ("step out, it blows" if is_lit else "out to 7.5, let it come")
                  + f" (creeper {ids[first]} at {tuple(round(c, 1) for c in h[0])}, {math.dist(here, h[0]):.1f} off)")

def fuse_lit(e) -> bool:
    """Pure: this creeper's fuse is lit (perception.read_combat)."""
    return bool(e.get("lit"))


def dodgeable(kind):
    """Pure: a hit worth side-stepping — a projectile or a blast; a melee lunge follows us (dodging it only postpones:
    escape__walker_open_blocks sidestepped a walker to death)."""
    return kind in ARROWS or bool(MOBS.get(kind, {}).get("burst"))

def impacts_of(near) -> list:
    """Pure: [(point, seconds, radius)] where each predicted projectile or blast lands (read_combat's impact_at,
    hit_s)."""
    return [(e["impact_at"], float(e["hit_s"]),
             ARROWS.get(e.get("type"), float(MOBS.get(e.get("type"), {}).get("keep_out", 3.0))))
            for e in near or [] if e.get("impact_at") is not None and e.get("hit_s") is not None
            and dodgeable(e.get("type"))]

def dodge_spot(here, impacts, candidates, speed=None):
    """Pure: the nearest candidate out of every predicted impact that we reach before the impact it leaves lands,
    or None. An impact we already stand outside of needs no race."""
    speed = float(beliefs.PLAYER["speed"]) if speed is None else speed
    best = None
    for spot in candidates:
        walk_s = math.dist(here, spot) / speed
        clear = all(math.dist(spot, p) > r and (math.dist(here, p) > r or walk_s < t) for p, t, r in impacts)
        if clear and (best is None or walk_s < best[0]):
            best = (walk_s, spot)
    return None if best is None else best[1]

def ids_by_row(near, hazards):
    """Entity id for each row (matched on position), so a fight decision can name its target."""
    by_pos = {(e["x"], e["y"], e["z"]): e.get("id") for e in near or []}
    return [by_pos.get(h[0]) for h in hazards]

# names for `estimate`'s quantities: aliases, never copies; this module adds only columns and shapes
arrival = estimate.arrival_s
pressure = estimate.pressure_hp_s
burst_damage = estimate.burst_hp
time_to_die = estimate.time_to_die_s
hide_ratio = estimate.reaches_share

# -- the model

FIREBALLS = ("minecraft:fireball", "minecraft:small_fireball", "minecraft:dragon_fireball")

def escape_spot(here, hazards, blocks=None, cover=None, footing=None, impacts=None):
    """Where to leave every threat's reach: `blocks` away from their dps-weighted centre, under the fight's slack rule; `cover` a candidate."""

    blocks = float(ENGAGE["evade_blocks"]) if blocks is None else blocks
    weights = [float(MOBS.get(h[3], {}).get("dps", 1.0)) for h in hazards]
    wsum = sum(weights) or 1.0
    cx = sum(h[0][0] * w for h, w in zip(hazards, weights)) / wsum
    cz = sum(h[0][2] * w for h, w in zip(hazards, weights)) / wsum
    dx, dz = here[0] - cx, here[2] - cz
    norm = math.hypot(dx, dz)
    if norm < 1e-6:
        dx, dz, norm = 1.0, 0.0, 1.0
    away = (here[0] + blocks * dx / norm, here[1], here[2] + blocks * dz / norm)
    options = [away]
    for a in (math.pi / 4, -math.pi / 4):
        rx = dx * math.cos(a) - dz * math.sin(a)
        rz = dx * math.sin(a) + dz * math.cos(a)
        options.append((here[0] + blocks * rx / norm, here[1], here[2] + blocks * rz / norm))
    if footing is not None:
        options = [s for s in (footing(o) for o in options) if s is not None]
    if cover is not None:
        options.append(tuple(cover))
    speed = float(beliefs.PLAYER["speed"])
    if impacts:
        # what the jar predicts lands where and when: a step out of its path in time, the nearest that clears it
        ring = [(here[0] + (max(r for _p, _t, r in impacts) + 1.0) * math.cos(k * math.pi / 4), here[1],
                 here[2] + (max(r for _p, _t, r in impacts) + 1.0) * math.sin(k * math.pi / 4)) for k in range(8)]
        if footing is not None:
            ring = [s for s in (footing(o) for o in ring) if s is not None]
        spot = dodge_spot(here, impacts, ring + options, speed)
        if spot is not None:
            return tuple(round(c) for c in spot)
    if any(h[3] in FIREBALLS for h in hazards):
        return None      # a fireball not outrun in time: hold, the jar's reflex swings (ghast_fireball: walked into it)
    if not options:
        return None
    best, best_key = None, None
    for opt in options:
        p = pressure(opt, hazards)
        walk_s = math.dist(here, opt) / speed
        key = (-round(p, 3), -round(walk_s, 2))
        if best_key is None or key > best_key:
            best, best_key = opt, key
    assert best is not None, "options is not empty"
    return tuple(round(c) for c in best)

def evade_cost(here, spot, hazards, prot, ground=None):
    """hp lost walking from here to `spot`: the pressure here (over the ground we stand in), over the walk."""
    walk_s = math.dist(here, spot) / float(beliefs.PLAYER["speed"])
    return estimate.leaving_hp(estimate.pressure_hp_s(here, hazards, prot, ground=ground), walk_s)

class Option:
    """One answer to the threats, priced: `hp` lost, `seconds` spent acting, and what it leaves behind."""

    def __init__(self, kind: str, target: Any, hp: float, seconds: float, why: str, leaves: float = 0.0,
                 heals: float = 0.0, blast_after: float = 0.0):
        self.kind, self.target, self.hp, self.seconds, self.why = kind, target, hp, seconds, why
        self.heals = heals
        self.leaves = leaves          # hp/s still coming at us after this answer (fleeing does not kill anything)
        self.blast_after = blast_after   # one-off damage still owed afterwards; a rate cannot carry an explosion

    def __repr__(self):
        return f"Option({self.kind}, {self.hp}hp, {self.seconds}s, {self.why!r})"

    # -- kernel's action contract: an option is an action, the field below the model

    @property
    def name(self):
        return self.kind

    def effect(self, state):
        """The state afterwards: what is still coming at us for the rest of the work."""

        return dict(state, pending_hp=self.leaves * state["work_s"] + self.blast_after)

SHAPES = ("between", "under", "down", "roof")      # roof: a 3×3 lid 2 up, too low for a tall mob
ROOF_BLOCKS = 9     # one block over the head is not enough: a tall mob hits from the next cell, diagonals too

def _sealed_off(ground, h, here=None):
    """Pure: the ground shuts this mob out for good (a sealed passage on its side: its slowdown is infinite)."""
    side = ground.side_of(here, h[0]) if here is not None and hasattr(ground, "side_of") else None
    return ground.slowdown(bool(MOBS[h[3]].get("squeezes")), side) == float("inf")

DETOUR_WALK_BLOCKS = 2.0     # a block that seals nothing is walked round: about two blocks more of the mob's walk

def delayed_pressure(here, hazards, prot, before, after, work_s):
    """Pure: hp/s over the work that the mobs still coming put on us once `after` delays them: each one's rate now
    (over `before`), for the share of the work left after its new arrival. A block that seals nothing costs a mob
    its detour round it (DETOUR_WALK_BLOCKS at its speed), not a multiple of its whole walk: 1.8^4 read four blocks on
    open ground as a 30 s wall and the walker as never coming (escape__walker_open_blocks walled in the open)."""
    added = (after.blocks - before.blocks) if after is not None and before is not None else 0
    total = 0.0
    for h in hazards:
        rate = pressure(here, [h], prot, ground=before)
        if rate <= 0.0 or _sealed_off(after, h, here):
            continue
        t = arrival(here, h, ground=before, horizon=work_s)
        if t == float("inf"):
            continue
        t += added * DETOUR_WALK_BLOCKS / float(MOBS[h[3]].get("speed", 2.5))
        total += rate * max(0.0, work_s - t) / work_s
    return total

def shaping_s(where, each_s, n, here, hazards, ground=None):
    """Pure: seconds to build `n` of a shape, or inf when the first walker arrives before it is done (not started)."""
    quiet = each_s * n
    if where == "roof":
        return quiet
    melee = [h for h in hazards if not MOBS[h[3]].get("ranged") and not MOBS[h[3]].get("burst")]
    t_free = min((arrival(here, h, ground=ground) for h in melee), default=float("inf"))
    return round(quiet, 2) if quiet <= t_free else float("inf")



def reshape_options(state, grid, hazards, here, press, prot, blast_here, work_s):
    """Blocking, standing on a block and digging down are one column: seconds (and blood while exposed) buying delay or no sight."""

    carried = int(state.get("blocks", 0))
    cap = int(ENGAGE.get("block_max", 4))
    creeper = any(h[3] == "minecraft:creeper" for h in hazards)     # a blast breaks a wall and a hole
    # digging down spends no blocks, only diggable ground; the other two shapes spend what is carried
    most_of = {"between": min(carried, cap), "under": min(carried, cap),
               "down": cap if state.get("dig_ok") else 0,
               "roof": ROOF_BLOCKS if carried >= ROOF_BLOCKS and any(MOBS[h[3]].get("tall") for h in hazards) else 0}
    if not any(most_of.values()):
        return []
    nearest = min(hazards, key=lambda h: math.dist(here, h[0]))
    cell = grid.choke(here, nearest[0], within=float(ENGAGE.get("block_reach", 4.0)))
    out = []
    for where in SHAPES:
        if where == "between" and (cell is None or not grid.blocks_worth_placing()):
            continue
        if creeper and where in ("between", "down"):
            continue
        each_s = float(ENGAGE["dig_s"] if where == "down" else ENGAGE["block_s"])
        after = grid
        for n in ((most_of[where],) if where == "roof" and most_of[where] else range(1, most_of[where] + 1)):
            if where == "between":
                after = after.with_block()
            seconds = shaping_s(where, each_s, n, here, hazards, grid)
            if seconds == float("inf"):
                continue
            # shaping kills nothing: what can still come at us afterwards follows as walking away's does (else a pillar
            # outbid killing a zombie) — but what the shape shuts out for good (a sealed passage: arrival inf over the
            # ground after) follows no one, and leaves nothing
            coming = [h for h in hazards if not _sealed_off(after, h, here)]
            if where == "between":
                # blocks in the way buy time, they kill nothing: what still comes is the pressure from its new, later
                # arrival to the end of the work — a seal buys it all (0), a delay part of it (neither 0 nor full; a
                # delay past the 4 s look-ahead read as a seal: escape__walker_open_blocks walled in the open)
                leaves = delayed_pressure(here, coming, prot, grid, after, work_s)
            else:
                follows = pressure(here, coming, prot, ground=grid) * float(ENGAGE["follow_p"]) if coming else 0.0
                leaves = max(estimate.pressure_hp_s(here, hazards, prot, ground=after, shape=(where, n)), follows)
            still = [h for h in coming if arrival(here, h, ground=after) <= work_s]
            blast_after = burst_damage(here, still, prot) if still else 0.0
            if leaves > press - float(ENGAGE["shape_min_gain"]) * max(press, 1e-6) \
                    and blast_after >= blast_here - 1e-6:
                continue
            # health lost while building: only what reaches us before the shape is done (pressure over its own seconds)
            during = pressure(here, hazards, prot, ground=grid, horizon=seconds) * seconds if seconds > 0 else 0.0
            out.append(Option("reshape", (where, n), round(during, 2), seconds,
                              f"{where}: {n} × {each_s}s", leaves=round(leaves, 3), blast_after=blast_after))
    return out

def eat_options(state, hp, press, blast_here):
    """Pure: eating in a fight."""

    max_hp = float(beliefs.PLAYER.get("max_hp", 20))
    if hp >= max_hp:
        return []
    eat_s = float(ENGAGE["eat_s"])
    if int(state.get("golden_apples", 0)) > 0:
        heal = min(float(ENGAGE["golden_heals"]), max_hp - hp)
        return [Option("eat", "minecraft:golden_apple", round(press * eat_s + blast_here, 2), eat_s,
                       f"a golden apple: +{heal:.0f} hp now", leaves=press, heals=heal)]
    if int(state.get("food_items", 0)) > 0 and estimate.eat_due(float(state.get("hunger", 0)), hp, 0, max_hp, float(_R["food_full"])) \
            and press <= float(ENGAGE["eat_safe_press"]):
        heal = min(float(ENGAGE["eat_heals"]), max_hp - hp)
        return [Option("eat", None, round(press * eat_s + blast_here, 2), eat_s,
                       f"eat: +{heal:.0f} hp by regen, nothing reaching us", leaves=press, heals=heal)]
    return []

def horizon_for(state) -> float:
    """Seconds of "carrying on" the options are priced over, which is `estimate.horizon_s` and nothing else."""

    return estimate.horizon_s(state.get("work_s"))

def _evade_option(here, spot, hazards, prot, press, out, ground=None):
    """Pure: the evade column to `spot`, priced against the options already in `out` (a fight on offer)."""
    walk_s = round(math.dist(here, spot) / float(beliefs.PLAYER["speed"]), 2)
    # leaving costs the walk out and back; what follows is the next round's account — unless every threat still reaches us there and a fight is on offer (then leaving only postpones it)
    # a melee follower walks after us: leaving only postpones it, fight or not (combat__dig_in evaded instead of digging)
    melee = all(not MOBS[h[3]].get("ranged") and not MOBS[h[3]].get("burst") for h in hazards)
    postpones = (melee or any(o.kind == "fight" for o in out)) and all(estimate.follows_to(spot, h) for h in hazards)
    follows = round(press if postpones else press * float(ENGAGE["follow_p"]), 3)
    # a creeper is not a rate that leaving ends: with a fight on offer its blast stays owed
    fight_on = any(o.kind == "fight" for o in out)
    blast = burst_damage(here, hazards, prot, fuse_s=float("inf")) if fight_on else burst_damage(spot, hazards, prot)
    return Option("evade", spot, evade_cost(here, spot, hazards, prot, ground=ground),
                  round(walk_s * 2, 2), f"leave their reach, ~{walk_s}s out and back", leaves=follows,
                  blast_after=blast)

def options(state):
    """Pure: every answer worth considering, priced."""

    here, hp = tuple(state["here"]), float(state["hp"])
    hazards = [h for h in state.get("hazards", ()) if h[3] in MOBS]
    prot = float(state.get("protection", 0.0))
    grid = state.get("field")
    press = pressure(here, hazards, prot, ground=grid) if hazards else 0.0
    blast_here = burst_damage(here, hazards, prot) if hazards else 0.0
    work_s = horizon_for(state)
    if not hazards or (press <= 0.0 and blast_here <= 0.0):
        # a creeper exerts no pressure (a blast is not a rate): tested separately, or it is always ignored
        return [Option("ignore", None, 0.0, 0.0, "nothing in reach")]
    ids = list(state.get("ids") or [None] * len(hazards))
    out = [Option("ignore", None, 0.0, 0.0,
                  f"carrying on takes ~{press:.1f} hp/s"
                  + (f" and a {blast_here:.0f} hp blast" if blast_here else ""),
                  leaves=press, blast_after=blast_here)]
    # fight: kill them and nothing is coming; a creeper with a sword is fought hit-and-back first (walking away only postpones it)
    sword = state.get("sword")       # the sword item carried, None: the hand (data.weapon_hit prices it)
    creepers = [i for i, h in enumerate(hazards) if MOBS[h[3]].get("burst") and h[3] == "minecraft:creeper"]
    clear = float(MOBS["minecraft:creeper"]["keep_out"])
    lit = set(state.get("lit") or ())
    # armed, lit or not: hit-and-back — the jar's keepoff steps out past keep_out while it swells and strikes again
    # once it stops (a baited creeper blows up: no kill, fight_creeper_1 05:07)
    if creepers and sword and all(MOBS[h[3]].get("burst") is None or i in creepers
                                       for i, h in enumerate(hazards)):
        first = min(creepers, key=lambda i: math.dist(here, hazards[i][0]))
        t_c, lost_c = estimate.keepoff_cost(here, hazards[first], sword, prot)
        rest = [h for i, h in enumerate(hazards) if i != first]
        t_r, lost_r = estimate.fight_cost(hazards[first][0], rest, sword, prot, shapes=getattr(grid, "shape_now", ()),
                                          shield=bool(state.get("shield"))) if rest else (0.0, 0.0)
        out.append(Option("fight", ids[first], round(lost_c + lost_r, 2), round(t_c + t_r, 2),
                          f"kill the creeper hit-and-back in ~{t_c}s"
                          + (f", then {len(rest)} more" if rest else "")))
    # bait: no sword, no timed hit — it fuses out away from us
    bait = bait_option(here, hazards, ids, creepers, lit, clear, prot) if creepers and not sword else None
    if bait is not None:
        out.append(bait)
    # melee only what we can reach (ghast_fireball: swung at a ghast 6 up, hit)
    reach = [i for i, h in enumerate(hazards) if estimate.melee_reachable(here, h, ground=grid)]
    above = [hazards[i] for i in range(len(hazards)) if i not in reach]
    if reach and not any(MOBS[h[3]].get("burst") for h in hazards):
        t_fight, lost = estimate.fight_cost(here, [hazards[i] for i in reach], sword, prot,
                                            shapes=getattr(grid, "shape_now", ()), shield=bool(state.get("shield")))
        nearest = min(reach, key=lambda i: math.dist(here, hazards[i][0]))
        out.append(Option("fight", ids[nearest], lost + blast_here, t_fight,
                          f"kill {len(reach)} in ~{t_fight}s for ~{lost} hp",
                          leaves=pressure(here, above, prot, ground=grid) if above else 0.0))
    if above and state.get("bow"):
        # out of reach: the bow; else evade or hold (the jar deflects)
        first = min(above, key=lambda h: math.dist(here, h[0]))
        t_shoot = estimate.shoot_cost(here, above, prot)
        i = hazards.index(first)
        out.append(Option("shoot", {"id": ids[i], "x": first[0][0], "y": first[0][1], "z": first[0][2],
                                    "height": 1.0},
                          round(press * t_shoot + blast_here, 2), t_shoot,
                          f"shoot {len(above)} out of reach in ~{t_shoot}s",
                          leaves=pressure(here, [h for i2, h in enumerate(hazards) if i2 in reach], prot, ground=grid)
                          if reach else 0.0))
    # no shield column: the jar's reflex raises the shield for every predicted hit, whatever the answer (a shield in
    # the offhand is protection, `state["protection"]`), so it is never an answer of its own
    spot = escape_spot(here, hazards, cover=state.get("cover"), footing=state.get("footing"),
                       impacts=state.get("impacts"))
    if spot is not None:             # else nowhere to leave to (a lethal drop all round): fight, eat, wall in
        # a fight the veto removes is not "a fight on offer" for leaving to postpone (before the one veto, the fight's
        # own gate kept it out of `out`: evade at low health priced as postponing a fight nobody could take)
        out.append(_evade_option(here, spot, hazards, prot, press, [o for o in out if survivable(o, hp)], ground=grid))
    cover = state.get("low_cover")
    tall = [h for h in hazards if MOBS[h[3]].get("tall")]
    if tall and cover is not None:
        # a 2-high space near: under it a tall mob can't reach (fight_enderman_provoked)
        walk_s = round(math.dist(here, cover) / float(beliefs.PLAYER["speed"]), 2)
        rest = [h for h in hazards if not MOBS[h[3]].get("tall")]
        out.append(Option("cover", tuple(cover), round(press * walk_s, 2), walk_s, f"under a 2-high roof, {walk_s}s off",
                          leaves=pressure(here, rest, prot, ground=grid) if rest else 0.0))
    hide = state.get("hide")
    shooters = [h for h in hazards if MOBS[h[3]].get("ranged")]
    if shooters and hide is not None:
        # out of every archer's line of fire (terrain between): their pressure until there, the rest's after
        walk_s = round(math.dist(here, hide) / float(beliefs.PLAYER["speed"]), 2)
        rest = [h for h in hazards if not MOBS[h[3]].get("ranged")]
        out.append(Option("hide", tuple(hide), round(press * walk_s, 2), walk_s, f"out of their line of fire, {walk_s}s off",
                          leaves=pressure(tuple(hide), rest, prot, ground=grid) if rest else 0.0))
    for option in eat_options(state, hp, press, blast_here):
        out.append(option)
    if grid is not None:
        for option in reshape_options(state, grid, hazards, here, press, prot, blast_here, work_s):
            out.append(option)
    if int(state.get("blocks", 0)) >= int(ENGAGE["wall_in_blocks"]):
        wall_s = float(ENGAGE["wall_in_s"])
        # a pod stops only what walks in; climbers and teleporters are still coming
        through = estimate.pressure_hp_s(here, [h for h in hazards if MOBS[h[3]].get("squeezes")], prot,
                                         ground=grid)
        out.append(Option("wall_in", None, round(press * wall_s + blast_here, 2), wall_s,
                          f"wall in, ~{wall_s}s exposed", leaves=round(through, 3)))
    return passing_columns(out, hp)

def survivable(option, hp):
    """Pure: what this answer expects to lose over its own seconds stays under the health we have, less a margin."""

    return float(option.hp) < hp - float(ENGAGE["survive_margin_hp"])

def passing_columns(out, hp):
    """Pure: the one veto every column passes (`survivable`) — only the fight had it, and a 1.2 s pillar at 3.1 hp
    beside three zombies was offered, taken, and died on (fight_zombie_3). It removes an answer only while a survivable
    one remains: when none does, the one expected to lose least is kept (leaving, usually), or low health would have
    no way out left at all — never a fight, which ends nothing before it has cost what we have. Carrying on is never vetoed: it is the account the others are priced against."""

    acts = [o for o in out if o.kind != "ignore"]
    kept = [o for o in acts if survivable(o, hp)]
    # a fight expected to cost all we have is never the way out (the fight's own gate before): it kills nothing first
    way_out = [o for o in acts if not o.kind.startswith("fight")]
    if way_out and not kept:
        kept = [min(way_out, key=lambda o: float(o.hp))]
    return [o for o in out if o.kind == "ignore" or o in kept]

def action_cost(option, price, work_s=None):
    """kernel's `cost_s` for an option: its seconds plus its health spent, priced on top of what it leaves owed — one damage price."""

    if work_s is None:
        return estimate.act_cost_s(option.seconds, option.hp, price)
    left = owed(option, work_s)
    return float(option.seconds) + price(left + float(option.hp)) - price(left)

class Answer:
    """One option, wearing kernel's action contract."""

    __slots__ = ("option", "name", "cost_s")
    kept = 1.0      # what an answer did stays in the world when it is dropped: damage dealt, blocks placed, ground gained

    def __init__(self, option, price, work_s=None):
        self.option, self.name, self.cost_s = option, option.kind, action_cost(option, price, work_s)

    def effect(self, state):
        return self.option.effect(state)

    def __repr__(self):
        return f"Answer({self.name}, {self.cost_s:.1f}s)"

FALLBACK = (("reshape", "cover", "wall_in"), ("evade",), ("fight", "fight_shielded"))   # shape, flee, fists


def fallback(opts, state):
    """Pure: the answer when nothing saves but a melee follower is closing and would hurt us — never ignore: a
    shape if one is on offer, else flight while every follower is slower than us, else the fight; the least costly
    of the first group there is; the table spent, flight even from a follower as fast as us. None only when nothing
    melee closes, or no answer exists at all (ignore stands)."""
    here, grid = tuple(state["here"]), state.get("field")
    melee = [h for h in state.get("hazards", ()) if h[3] in MOBS and not MOBS[h[3]].get("ranged")
             and not MOBS[h[3]].get("burst")]
    if not melee or pressure(here, melee, float(state.get("protection", 0.0)), ground=grid) <= 0.0:
        return None
    slower = all(float(MOBS[h[3]].get("speed", 2.5)) < float(beliefs.PLAYER["speed"]) for h in melee)
    for kinds in FALLBACK:
        if kinds == ("evade",) and not slower:
            continue
        group = [o for o in opts if o.kind in kinds]
        if group:
            return min(group, key=lambda o: float(o.hp) + float(o.seconds))
    # the table spent and a follower as fast as us: flight only buys distance, still better than standing under it
    # (a provoked enderman, fists: no fight offered, evade skipped — ignore stood, S1)
    flight = [o for o in opts if o.kind == "evade"]
    return min(flight, key=lambda o: float(o.hp) + float(o.seconds)) if flight else None


class Field:
    """The threats around us, as a kernel model: one state, one price, a column per answer."""

    def __init__(self, state, price=None, refused=None):
        self.field = state
        self.price_hp = price or (lambda dhp: dhp)
        self.refused = refused          # option → why it may not be chosen now (fight_loop: it just failed), or None
        self.work_s = horizon_for(state)
        self.opts = [Answer(o, self.price_hp, self.work_s) for o in options(state)]
        self.idle = next(a for a in self.opts if a.name == "ignore")
        # the answer when nothing saves: never standing still under a closing melee follower (fallback)
        forced = fallback([a.option for a in self.opts], state)
        self.default = next((a for a in self.opts if a.option is forced), self.idle) if forced else self.idle

    def state(self):
        """The kernel state: what carrying on still owes us, which is exactly what the `ignore` column leaves."""

        return {"pending_hp": self.idle.option.leaves * self.work_s + self.idle.option.blast_after,
                "work_s": self.work_s}

    def price(self, state):
        return self.price_hp(state["pending_hp"])

    def actions(self, state):
        return self.opts

    def admissible(self, state, option):
        """Refused only what the caller says it may not choose now (an answer that just failed)."""

        why = self.refused(getattr(option, "option", option)) if self.refused is not None else None
        return (False, why) if why else (True, "")

def owed(option, work_s):
    """Pure: health still owed to us after this answer — the rate it leaves, over the work, plus any blast that still reaches us."""

    return option.leaves * work_s + option.blast_after

def saves(option, opts, price, work_s):
    """Pure: seconds this answer saves against carrying on."""

    doing_nothing = next(o for o in opts if o.kind == "ignore")
    return estimate.saved_s(price, owed(doing_nothing, work_s), owed(option, work_s),
                            action_cost(option, price, work_s))

def decide(state, price=None):
    """Pure: the best answer, decided by `kernel.choose` like every other plan this agent makes."""

    field = Field(state, price)
    best = (kernel.choose(field, field.state()).action or field.default).option
    return Decision(best.kind, best.target, best.why, best.hp)

# -- what the fight tells ordinary play: a rate (estimate.pressure_hp_s) and no-go circles, never a decision

def no_go(state, margin=None):
    """Pure: [(centre, radius)] the normal planner must not route through or plan work inside."""

    margin = float(ENGAGE["no_go_margin"] if margin is None else margin)
    return [(tuple(h[0]), float(h[1]) + margin) for h in state.get("hazards", ()) if h[3] in MOBS]

# -- the price of health: depends on the state it is lost from (death chance plus lost margin)
_T, _R, _K = beliefs.CONFIG["time"], beliefs.CONFIG["risk"], beliefs.CONFIG["tools"]

def bag_loss(s):
    """Seconds lost to a full bag: work whose output falls on the floor."""

    free = float(s.get("bag_free", 36))
    if free >= _R["bag_comfortable"]:
        return 0.0
    share = (_R["bag_comfortable"] - free) / _R["bag_comfortable"]
    return share * _T["day_s"] * _K["mining_share_of_day"]

def price_state(**kw):
    """The survival state health and time are priced in (`hp_seconds`); unknown keys are refused."""
    s = {"night": False, "ticks_until_dusk": 6000, "hp": 20, "food": 20, "bed": False, "sheltered": False,
         "torches": False, "sword": None, "pickaxe": 0, "food_items": 0, "nights_missed": 0, "armor": 0,
         "shield": False, "bag_free": 36,
         # dark where we stand, where mobs come from — not the same as night
         "dark": False,
         "ground": None}             # perception's blocks (Cost's region)
    unknown = set(kw) - set(s)
    if unknown:
        raise KeyError(f"not survival state: {sorted(unknown)}")
    s.update(kw)
    return s

def _protection(s):
    """This state's damage reduction, from the belief table."""

    return beliefs.protection(s["armor"], s["shield"], hit=beliefs.hardest_hit([_R["reference_mob"]]))

def encounter_damage(s):
    """(seconds, health) one ordinary encounter costs at this weapon and armour."""

    kind = _R["reference_mob"]
    here = (0.0, 0.0, 0.0)
    row = estimate.row((float(beliefs.PLAYER["melee_reach"]), 0.0, 0.0), beliefs.mob(kind)["reach"],
                       (0.0, 0.0, 0.0), kind)
    return estimate.fight_cost(here, [row], s["sword"], _protection(s))

_fatal_chance = estimate.fatal_chance     # one curve, in the module that owns the five quantities

def fight_loss(s):
    """Seconds a day of ordinary encounters costs at this weapon and armour — at full health."""

    kill_s, damage = encounter_damage(s)
    return _R["encounters_per_day"] * (kill_s + _fatal_chance(20, damage) * _T["death_cost_s"])

def hurt_loss(s):
    """Seconds the current health deficit costs: regeneration time plus the extra death chance before it is back."""

    hp = max(0.1, float(s["hp"]))
    if hp >= 20:
        return 0.0
    _kill_s, damage = encounter_damage(s)
    regen_s = (20.0 - hp) * _R["regen_s_per_hp"]
    meetings = _R["encounters_per_day"] * regen_s / _T["day_s"]
    extra = _fatal_chance(hp, damage) - _fatal_chance(20, damage)
    return regen_s + meetings * max(0.0, extra) * _T["death_cost_s"]

def night_loss(s):
    """Seconds the coming night is expected to cost."""

    if s["bed"]:
        return 0.0 if s["sheltered"] else _R["night_bed_open"] * (1.0 - _protection(s)) * _T["death_cost_s"]
    p = _R["night_sheltered"] if s["sheltered"] else _R["night_open"]
    if not s["sword"]:
        p += _R["no_sword_night"]
    if s["nights_missed"] >= 3:
        p += _R["phantom_night_death"]
    # without a bed the night is also 420 s of not working (underground counts as working)
    idle = 0.0 if s["sheltered"] else _T["night_s"]
    return p * (1.0 - _protection(s)) * _T["death_cost_s"] + idle

def hunger_slowed(food):
    """The share of working time this hunger loses while it lasts: no sprint, no regeneration (the one hunger rate)."""
    food = float(food)
    if food >= _R["food_full"]:
        return 0.0
    slowed = (_R["food_full"] - food) / _R["food_full"] * _R["hunger_slowdown"]
    if food <= _R["food_low"]:
        slowed = max(slowed, _R["starving_slowdown"])   # below the floor nothing sprints and nothing heals
    return slowed

def hunger_loss(s):
    """Seconds the CURRENT hunger costs before the next meal: work lost to not sprinting and not regenerating."""
    return _T["day_s"] * _R["meal_share_of_day"] * hunger_slowed(s["food"])     # carried until the next meal

def larder_loss(s):
    """Seconds the lack of MEALS costs over the next day: hunger we will not be able to answer."""
    if s["food_items"] >= 8:
        return 0.0
    if s["food_items"] >= 2:
        return 0.15 * _T["day_s"]           # will run out before the day is done
    loss = _R["starving_slowdown"] * _T["day_s"]
    if s["food_items"] == 0:
        loss += _R["starving_death"] * _T["death_cost_s"]
    return loss

def food_loss(s):
    """What hunger costs: what it is costing now, plus what having nothing to eat will cost."""

    return hunger_loss(s) + larder_loss(s)

def tool_loss(s):
    """Seconds the day's mining costs beyond what an iron pickaxe would take, plus fighting unarmed."""
    mult = {0: _K["mine_time_no_pickaxe"], 1: _K["mine_time_stone"]}.get(s["pickaxe"], _K["mine_time_iron"])
    mining = _K["mining_share_of_day"] * _T["day_s"]
    loss = mining * (mult - _K["mine_time_iron"])
    return loss

def light_loss(s):
    return 0.0 if s["torches"] else _R["dark_work_death"] * _T["death_cost_s"]

def expected_loss(s):
    """The fifth quantity for ordinary play: seconds expected to be lost from here, given what we lack."""

    return (night_loss(s) + food_loss(s) + tool_loss(s) + light_loss(s) + fight_loss(s) + hurt_loss(s)
            + bag_loss(s))

def hp_seconds(s, dhp) -> float:
    """Seconds that expecting to lose `dhp` health costs from this state."""

    if dhp <= 0:
        return 0.0
    hp = float(s["hp"])
    p = _fatal_chance(hp, dhp)
    survived = dict(s, hp=max(1.0, hp - min(dhp, hp - 1.0)))
    margin = expected_loss(survived) - expected_loss(s)
    # a death costs the respawn and walk back, never less for being hurt already
    reset = max(0.0, expected_loss(dict(s, hp=20)) - expected_loss(s))
    return round(p * (_T["death_cost_s"] + reset) + (1.0 - p) * margin, 1)

THREAT_ROWS, THREAT_IDS, THREAT_AT = [], [], 0.0
THREAT_ALIVE: set = set()   # every living entity id the last reading listed (x-ray: an occluded mob is still there)
THREAT_IMPACTS: list = []   # the jar's predicted impacts in the last reading (impacts_of)
THREAT_LIT: set = set()     # ids of creepers whose fuse the last reading shows lit
# seconds until the soonest hit on the body lands, as the JAR predicts it (/entities tti_ticks: every projectile and
# melee mob stepped as the game ticks them, anaka combat.Impact), or None: no hit coming. Never re-derived here.
THREAT_HIT_S = None


def unanswered(field_model):
    """Pure: why this reading leaves us under a threat with no answer — carrying on still costs health and the
    fallback table (a shape, flight, a fight) offers nothing — or None."""
    idle = field_model.idle.option
    if field_model.default is not field_model.idle or (idle.leaves <= 0.0 and idle.blast_after <= 0.0):
        return None
    return f"no answer to the threats: {idle.why}, and no shape, flight or fight on offer"


def _forget_threats():
    """The last life's threats (their ids, their rows, the hit it predicted) are nobody's now."""
    global THREAT_ROWS, THREAT_IDS, THREAT_AT, THREAT_ALIVE, THREAT_IMPACTS, THREAT_HIT_S, THREAT_LIT
    THREAT_ROWS, THREAT_IDS, THREAT_AT, THREAT_ALIVE, THREAT_IMPACTS, THREAT_HIT_S = [], [], 0.0, set(), [], None
    THREAT_LIT = set()


lifecycle.on_reset(_forget_threats, covers=("THREAT_ROWS", "THREAT_IDS", "THREAT_AT", "THREAT_ALIVE",
                                            "THREAT_IMPACTS", "THREAT_HIT_S", "THREAT_LIT"))

def alive_ids(near):
    """Pure: the ids of the entities a reading lists alive (a dying one, health 0, is gone)."""
    return {e.get("id") for e in near or [] if e.get("id") is not None
            and (e.get("health") is None or float(e["health"]) > 0)}


def soonest_hit_s(near):
    """Pure: seconds until the soonest predicted hit among `near` (read_combat rows), or None."""
    hits = [float(e["hit_s"]) for e in near or [] if e.get("hit_s") is not None]
    return min(hits) if hits else None


def hit_due_s(max_age_s=3.0, now=None):
    """The jar's soonest predicted hit (seconds) as perception last read it, or None (none, or not read lately)."""
    import time as _t
    if THREAT_HIT_S is None or (now or _t.time()) - THREAT_AT > max_age_s:
        return None
    return THREAT_HIT_S

def threats_seen(max_age_s=3.0, now=None):
    """(rows, ids) as perception last saw them, or ([], []) when it has not looked recently enough to be trusted."""
    import time as _t
    if not THREAT_ROWS or (now or _t.time()) - THREAT_AT > max_age_s:
        return [], []
    return list(THREAT_ROWS), list(THREAT_IDS)

def seen_at():
    """When the rows above were read."""

    return THREAT_AT
