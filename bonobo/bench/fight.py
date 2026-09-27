"""The fighting benches: seven waves of a siege, a swept combat arena, and one escape per enemy kind.

All three are the same sweep — build a cell, price it, live in it, write one row of intent, execution and
outcome — so a new enemy or a new column is a row in a table here, never another scenario to keep in step.
"""
import math
import random
import time

from .. import estimate, paths
from . import core
from .core import SCENARIOS, SWEEP, SetupInvalid, _by, _c, _chat, _platform, _sweep, _sweep_check, at

# -- one table of dimensions, three benches -----------------------------------------------------------------------
# The same shape as the offline sweep (`tests/world.py`): a cell is a point in a named product, `_cells` moves ONE
# dimension off a baseline, and `_build` is the only thing that turns a cell into a world. What differs from
# offline is only the realisation — commands to a running game instead of a state vector — so the dimension names
# are the same on both sides and a row of `bench/combat.jsonl` reads next to an offline cell.
#
# A dimension is {value: commands}. Nothing else in this file may know what "hurt" or "corridor" means.

COMBAT_ROWS = paths.data("bench/combat.jsonl")
ESCAPE_ROWS = paths.data("bench/escape.jsonl")

# Enemies are named by what they DO, and the kind behind each name is the belief table's business.
# How each value of the shared vocabulary (`bench.cells.DIMENSIONS`) is realised in a running game. The names
# come from there; this says only how to build them with commands.
ENEMY = {"none": None, "walker": "minecraft:zombie", "archer": "minecraft:skeleton", "climber": "minecraft:spider",
         "bomb": "minecraft:creeper", "teleporter": "minecraft:enderman"}
# "pack" and "mixed" (bench/cells.py) were zombies here too, the number coming from `count`: the same world as
# "walker" under another name, so the same row twice. The pack is walker × count=three.

COUNT = {"one": 1, "three": 3}

# Ground that gives each shaping column something to be worth: a corridor has one gap a block would close, a
# roofed cell has a floor worth digging into, and the open cell has a step to stand up on. A billiard table
# prices `reshape` at nothing whatever the model believes, so a bench built on one can only ever test swinging.
GROUND = {
    "open": [f"fill {_c(at(4, 0, 2))} {_c(at(5, 1, 3))} stone"],          # a step to stand up on
    "corridor": [f"fill {_c(at(-1, 1, -2))} {_c(at(9, 3, -2))} cobblestone",
                 f"fill {_c(at(-1, 1, 2))} {_c(at(9, 3, 2))} cobblestone",
                 f"fill {_c(at(4, 1, -1))} {_c(at(4, 3, 1))} air"],        # one gap, wide enough to close
    "roofed": [f"fill {_c(at(-4, 3, -4))} {_c(at(9, 3, 4))} cobblestone",
               f"fill {_c(at(-4, 1, -4))} {_c(at(-4, 2, 4))} cobblestone",
               f"fill {_c(at(-2, -3, -2))} {_c(at(2, -1, 2))} dirt"],      # a floor worth digging into
}

# The body, as the dimensions it really has — a bundle cannot state a relation, because moving it moves five
# things at once and the baseline moves with them.
WEAPON = {"fist": [], "iron": ["give @p iron_sword"]}
# Armour is what is worn, and nothing else: with the shield in here, "iron armour" moved the protection AND the
# shield column at once, and neither could be stated about on its own. The shield is a kit.
ARMOUR = {"skin": [], "iron": ["item replace entity @p armor.chest with iron_chestplate",
                               "item replace entity @p armor.head with iron_helmet"]}
BLOOD = {"whole": [], "hurt": ["damage @p 12 minecraft:magic"]}
# What the bag holds, one column's worth at a time: a cell that carries everything can never say which column
# was missing what. `NEEDS` is what each column needs to exist at all, and the check before the window reads it.
KIT = {
    "nothing": [],
    "blocks": [core.BEST_TOOLS["pickaxe"], "give @p cobblestone 64", "give @p dirt 64"],
    "food": ["give @p cooked_beef 8"],
    "shield": ["item replace entity @p weapon.offhand with shield"],
    "full": [core.BEST_TOOLS["pickaxe"], "give @p cobblestone 64", "give @p dirt 64", "give @p cooked_beef 8",
             "item replace entity @p weapon.offhand with shield"],
}
# What a kit value puts within reach. Read off the CELL, not off the priced state: "the bag was read as empty"
# is the fault this is here to catch, so a check that asks the same state the pricing asked cannot see it.
NEEDS = {"blocks": ("reshape", "wall_in"), "food": ("eat",), "shield": ("shield",),
         "full": ("reshape", "wall_in", "eat", "shield"), "nothing": ()}
DISTANCE = {"near": 5, "across": 10}

DIMS = {"enemy": ENEMY, "count": COUNT, "ground": GROUND, "weapon": WEAPON, "armour": ARMOUR, "blood": BLOOD,
        "kit": KIT, "distance": DISTANCE}

ARMED = {"enemy": "walker", "count": "one", "ground": "open", "weapon": "iron", "armour": "iron",
         "blood": "whole", "kit": "full", "distance": "near"}
UNARMED = dict(ARMED, weapon="fist", armour="skin", kit="blocks", distance="across")


def _siege_kit():
    """The armed baseline, plus what a long fight needs more of. Built from the dimension table, so a change to
    what "iron armour" means reaches the siege too — it was a third copy of the same list of gives."""
    return (WEAPON["iron"] + ARMOUR["iron"] + KIT["full"]
            + ["item replace entity @p armor.legs with iron_leggings",
               "item replace entity @p armor.feet with iron_boots",
               "give @p cooked_beef 16", "give @p cobblestone 128"])


def _scatter(seed):
    """A few blocks of relief on the floor: a step to stand on, a dip to drop into, something to put between us
    and it. A billiard table has no shapes to answer with, so half the columns could never be worth anything.

    Laid out from the cell's seed, like everything else a cell randomises: a row that cannot be rebuilt is a
    measurement of a world nobody can visit again.
    """
    rng = random.Random(seed)
    out = []
    for _ in range(rng.randint(3, 6)):
        x, z = rng.randint(-ARENA_REACH + 4, ARENA_REACH - 4), rng.randint(-ARENA_REACH + 4, ARENA_REACH - 4)
        if abs(x) < 3 and abs(z) < 3:
            continue                      # not under our own feet
        if rng.random() < 0.5:
            height = rng.randint(1, 2)
            out.append(f"fill {_c(at(x, 0, z))} {_c(at(x + rng.randint(0, 2), height, z + rng.randint(0, 2)))} stone")
        else:
            out.append(f"fill {_c(at(x, -2, z))} {_c(at(x + rng.randint(1, 2), -1, z + rng.randint(1, 2)))} air")
    return out


def _roof():
    """A lid and a floor on the walled platform: a cell is a room, not a clearing."""
    lo, hi = at(-ARENA_REACH, 5, -ARENA_REACH), at(ARENA_REACH + 3, 5, ARENA_REACH)
    return [f"fill {_c(lo)} {_c(hi)} stone"]


def _cells(base, dims=None, repeat=1, over=None, table=None):
    """The cells a pass visits: one dimension off the baseline at a time, or the product of `over`, each repeated.

    A single window is one sample of a noisy world — a fight that happens to end in one swing says nothing about
    what a fight costs — so a cell that carries a measurement is visited `repeat` times and the rule reads the
    middle of them. `over` is for the few dimensions whose combination genuinely differs (what is coming at us and
    what ground we stand on); everything else still moves one at a time.
    """
    import itertools
    table = DIMS if table is None else table     # another sheet's dimensions (the brain tier's) walk the same way
    seen = []
    if over:
        for values in itertools.product(*(table[name] for name in over)):
            seen.append(dict(base, **dict(zip(over, values))))
    else:
        seen.append(dict(base))
    for name in (dims or ()):
        for value in table[name]:
            if value != base[name]:
                seen.append(dict(base, **{name: value}))
    for cell in seen:
        for run in range(max(1, repeat)):
            yield dict(cell, run=run)


def _seed_of(cell):
    """One seed per cell, per pass: the same number lays out the ground, places the enemies and sets their mood,
    and it goes into the row so the whole situation can be built again exactly."""
    return cell.get("seed") if cell.get("seed") is not None else random.randrange(1 << 30)


def _build(cell):
    """A cell, realised: a sealed room, then each dimension's own commands. The only place in this file that
    knows how a dimension becomes a world.

    Sealed on purpose. With the world left open the other planner had somewhere to go — a village to seek, a seam
    to mine — so a cell measured a fight with somebody else's errands mixed into its seconds, and one `seek` even
    ended a whole pass. What is in the cell is what we put there.
    """
    seed = cell.setdefault("seed", _seed_of(cell))
    out = (_platform(reach=ARENA_REACH, walled=True) + _roof() + _scatter(seed) + _alive()
           # Full health, food and no leftovers: a cell is one variable off the baseline, and health carried over
           # from the last cell makes `blood` two variables at once — the residuals then compare nothing.
           + ["clear @p", "effect clear @p", "effect give @p minecraft:instant_health 10 1 true",
              "effect give @p minecraft:saturation 1 10 true",
              "difficulty normal", "time set day"])
    for name in ("ground", "weapon", "armour", "kit", "blood"):
        out += DIMS[name][cell[name]]
    out += FIGHT_BUCKET
    kind = ENEMY[cell["enemy"]]
    if kind is None:
        return out
    return out + _summon(((kind, COUNT[cell["count"]]),), spread=DISTANCE[cell["distance"]], seed=seed)


def _kinds_of(cell):
    return {ENEMY[cell["enemy"]]}


# -- the fighting benches ----------------------------------------------------------------------------------------
# Waves that each ask for an answer the one before did not (swing, back off, break the line of sight, block the
# way, dig down), every enemy kind the threat model knows, and a kit with all of those answers in it — without the
# pickaxe and the blocks they are not choices the agent HAS, and the bench would test a narrower agent.
# A wave is written in the same vocabulary as a cell: what is coming, by what it does, and how many. The kinds
# behind the names live in `ENEMY`, below, and nowhere else.
# `carry`: what the waves before typically leave (hp lost, meals eaten, blocks spent) — each wave is its own row
# now and starts from that state. bench/siege.jsonl holds no per-wave results yet: estimates (≈ 2 hp and a few
# meals/blocks per cleared wave); replace with the median of the wave before once rows are recorded.
WAVES = (
    ("one walker", (("walker", 1),), (0, 0, 0)),
    ("three walkers", (("walker", 3),), (2, 1, 4)),
    ("two archers", (("archer", 2),), (4, 2, 10)),
    ("walkers and climbers", (("walker", 2), ("climber", 3)), (6, 3, 18)),
    ("two bombs", (("bomb", 2),), (7, 4, 28)),
    ("three teleporters", (("teleporter", 3),), (8, 5, 34)),
    ("everything", (("walker", 4), ("archer", 2), ("climber", 2), ("bomb", 1)), (9, 6, 38)),
)


def _carry(hp_lost, meals, blocks):
    """The state the waves before left, in BLOOD's own form (magic damage: armour-proof)."""
    return ([f"damage @p {hp_lost} minecraft:magic"] if hp_lost else []) + \
        ([f"clear @p cooked_beef {meals}"] if meals else []) + ([f"clear @p cobblestone {blocks}"] if blocks else [])

SIEGE_ROWS = paths.data("bench/siege.jsonl")


SHAPE_COLUMNS = {"reshape", "wall_in"}

# Ground wide enough for every answer the model may pick: `escape_spot` walks up to sixteen blocks, and on the
# nine-block platform the first online cell walked off the edge of the sky island and fell.
ARENA_REACH = 24


def _alive():
    """Put the player back on their feet before a cell is built.

    A dead player cannot be teleported, healed or given anything, so one death used to poison every cell after it:
    the first online pass measured one fall and twelve empty rows. Respawning costs a second and makes each cell
    independent of the one before, which is what a cell is for.
    """
    from .. import api as _api
    try:
        if _api.get("/state").get("dead"):
            _api.post("/respawn")
            time.sleep(1.0)
    except Exception:
        pass
    return ["gamemode survival @p", "effect clear @p"]


def _columns_possible(cell):
    """The columns this cell paid for: what the kit gave, minus what the situation cannot use.

    A column that the cell made possible and the planner never offered is a fault worth a row of its own — it is
    invisible in every other reading, because an option that is never priced also never loses.
    """
    want = set(NEEDS.get(cell["kit"], ()))
    if cell["blood"] != "hurt":
        want.discard("eat")          # eating at full health is not an option anywhere
    return want


def _combat_intent(state):
    """What the threat model wants, before anything moves: every column, its price, and the state it priced from.

    The state goes into the row so a surprising cell can be read back later: a bench
    that keeps only the answer cannot say what the answer assumed, and a prediction nobody can re-derive is not
    evidence about anything.
    """
    from .. import api as _api, fight_loop, perception, threat
    near = _api.get("/entities?radius=24").get("entities", []) or []
    now = time.time()
    rows = perception.note_threats(near, now, here=(state["x"], state["y"], state["z"]))
    if not rows:
        return {"rows": 0, "held": "ignore", "worth_s": 0.0, "options": {}, "state": None}
    try:
        state = dict(state, field=perception.ground(state),
                     **perception.kit(str(state.get("selected", "")) + str(state.get("screen"))))
    except Exception:
        pass
    sstate = threat.price_state(hp=max(1, int(state.get("health", 20))), armor=int(state.get("armor", 0)))
    price = lambda dhp: threat.hp_seconds(sstate, dhp)
    st = fight_loop.threat_state(state, rows)
    horizon, opts = threat.horizon_for(st), threat.options(st)
    fight_loop.HELD = None
    chosen = fight_loop.bid(state, rows, price, now=now)
    return {"rows": len(rows),
            "options": {o.kind: {"hp": round(o.hp, 2), "seconds": round(o.seconds, 2),
                                 "leaves": round(o.leaves, 3), "why": o.why,
                                 "saves": round(threat.saves(o, opts, price, horizon), 2)} for o in opts},
            "held": chosen[0].kind if chosen else "ignore",
            "worth_s": chosen[1] if chosen else 0.0,
            "horizon_s": round(horizon, 2),
            "hp_tax_s": round(estimate.pressure_hp_s(st["here"], rows, st.get("protection", 0.0),
                                                     ground=st.get("field")), 3),
            "no_go": len(threat.no_go(st)),
            "seen_at": perception.seen_at(),
            "state": st}          # the live state: measured from, then written down by `_plain`


TRACE_EVERY_S = 0.2


def _sampler(stop, out, began):
    """The trace, on its own thread.

    Sampling between rounds does not work: one `BRAIN.round()` blocks for as long as its task takes, and the first
    online pass came back with a single sample for eleven seconds of fighting. What the estimates are checked
    against has to be read on a clock of its own.
    """
    from .. import api as _api
    from ..world import entities
    kinds = _threat_kinds()
    while not stop.is_set():
        try:
            state = _api.get("/state")
            near = [(e["type"], round(e["distance"], 2), round(e.get("health", 0.0), 1))
                    for e in entities(24) if e.get("type") in kinds]
            out.append({"t": round(time.time() - began, 2), "hp": state["health"],
                        "pos": [round(state[k], 2) for k in ("x", "y", "z")], "near": near})
        except Exception:
            pass
        stop.wait(TRACE_EVERY_S)


def _restock(cell):
    """Put the cell's enemies back. A window measures a fight, and a fight that ended in four seconds leaves
    twenty seconds of quiet that say nothing — so what the cell declares is kept present for its whole window."""
    if not cell:
        return
    kind = ENEMY.get(cell.get("enemy"))
    if not kind:
        return
    for command in _summon(((kind, COUNT[cell["count"]]),), spread=DISTANCE[cell["distance"]],
                           seed=cell.get("seed")):
        _chat(command)


def _combat_execute(seconds, until=None, cell=None):
    """Live in the cell with ONE layer driving, recording every look the threat layer took and a 5 Hz trace.

    Only the threat layer acts. Ordinary play stands itself down for the window
    (`brain.not_taking_part`) rather than being skipped by a branch here: it still builds its candidates and still
    writes its tape, they are simply all refused, in the open, with a reason. Two decision-makers on one body put
    somebody else's errands into the seconds this cell is measuring, and one of those errands ended a whole pass.
    """
    import threading
    from .. import perception
    from ..world import Snapshot
    if not perception.watching():
        raise SetupInvalid("the threat layer is not running: nothing would answer, and nothing would be measured")
    mark = len(perception.ANSWERED)
    began, worst = time.time(), Snapshot().state["health"]
    trace, stop = [], threading.Event()
    watcher = threading.Thread(target=_sampler, args=(stop, trace, began), daemon=True)
    watcher.start()
    aside = getattr(core.BRAIN, "not_taking_part", None)
    if not callable(aside):
        # Two decision-makers on one body cannot be measured. Said before the window rather than discovered in
        # the rows afterwards — a pass that runs without it costs ten minutes and measures somebody else.
        raise SetupInvalid("the planner offers no way to stand down: a cell cannot measure one layer alone")
    with aside("threat bench cell"):
        try:
            while time.time() - began < seconds and (until is None or until()):
                state = Snapshot().state
                worst = min(worst, state["health"])
                if state["health"] <= 0:
                    break
                if cell and not _hostiles(radius=24, kinds={ENEMY.get(cell.get("enemy"))} - {None}):
                    _restock(cell)
                # The planner keeps taking rounds — it has to, or its refusals never reach the tape — and every
                # candidate it offers is refused while it is standing down.
                try:
                    core.BRAIN.round()
                except Exception as e:
                    from .. import api as _api
                    _api.log(f"!! round: {type(e).__name__}: {e}")
                    time.sleep(TRACE_EVERY_S)
        finally:
            stop.set()
            watcher.join(1.0)
    worst = min([worst] + [s["hp"] for s in trace])
    return perception.answered_since(mark), worst, round(time.time() - began, 1), trace


def blind_s(looks, seconds):
    """Seconds of the window in which the threat layer could not see: it had no rows, or only stale ones.

    A residual computed across a blind stretch is arithmetic over a guess. The row carries this so a cell can be
    thrown away for what it is — not looked at — rather than read as a model that chose to carry on.
    """
    if not looks:
        return round(float(seconds), 2)
    # "Quiet" is an observation: the tick looked and there was nothing. Blindness is the tick that could not look
    # — the read failed, the layer was not wired, a soft skill had the body.
    blind = sum(1 for look in looks if look["outcome"] in ("stale", "unwired", "soft"))
    return round(float(seconds) * blind / len(looks), 2)


def _threat_kinds():
    from .. import threat
    return set(threat.MOBS)


def _mob_reach(kind):
    from .. import threat
    return threat.MOBS.get(kind, {}).get("reach", 3.0)


def _player_speed():
    from .. import threat
    return threat.PLAYER["speed"]


BLIND_SHARE = 0.1      # a cell blind for more of its window than this measured nothing
# A window has to be long enough to contain a fight: eight seconds caught one or two swings and then went quiet.
# Repeating a cell inside one pass is not how the noise is averaged out — the rows accumulate across passes, and
# what makes those rows independent is the jitter below, not a loop.
CELL_SECONDS = 15.0
CELL_REPEAT = 1


def _plain(state):
    """The priced state as JSON: the row has to carry what the prediction assumed, and a row is data. The ground
    is an object, so it goes in as what it says about the world rather than as itself."""
    if not state:
        return None
    ground = state.get("field")
    return dict(state, hazards=[list(h) for h in state.get("hazards", ())],
                field=None if ground is None else {"bucket": ground.bucket, "blocks": ground.blocks})


def _fought(kinds, seconds):
    """The shared record: price the cell, live in it, and say what came of it — and what the clock said about the
    numbers the pricing was built on."""
    def record(cell):
        from ..world import Snapshot
        before = Snapshot()
        intent = _combat_intent(dict(before.state))
        answered, worst, took, trace = _combat_execute(seconds, cell=cell)
        after = Snapshot()
        near = _hostiles(radius=24, kinds=kinds(cell))
        # How much of the window the layer was blind for decides whether this cell is evidence at all.
        dark = blind_s(answered, took)
        priced = intent.get("state") or {}
        missing = sorted(_columns_possible(cell) - set(intent.get("options") or {})) if intent.get("rows") else []
        intent = dict(intent, state=_plain(intent.get("state")), missing_column=missing,
                      carried={k: priced.get(k) for k in ("blocks", "food_items", "shield", "sword")})
        return {"intent": intent, "answered": answered, "trace": trace,
                "blind_s": dark, "invalid": dark > took * BLIND_SHARE,
                "outcome": {"hp": after.state["health"], "hp_before": before.state["health"], "worst_hp": worst,
                            "hp_lost": round(before.state["health"] - after.state["health"], 1),
                            "seconds": took, "moved": round(math.dist(before.pos, after.pos), 1),
                            "left": len(near),
                            "gap": round(min((e["distance"] for e in near), default=0.0), 1),
                            "blocks_spent": before.inv.count("building") - after.inv.count("building")}}
    return record


def _where(row):
    """A row's cell, named by the dimensions it actually carries — a rule that lists the keys is a second copy of
    the dimension table."""
    return "/".join(f"{k}={row[k]}" for k in DIMS if k in row) or str(row.get("line_up", "?"))


def _answers_are_closed(rows):
    """True of every cell: alive, a held column that went out and did not raise, a no-go zone for the planner."""
    bad = []
    for r in rows:
        where = _where(r)
        held, opts = r["intent"].get("held"), r["intent"].get("options") or {}
        if r["outcome"]["hp"] <= 0:
            bad.append(f"{where}: died")
        if held != "ignore" and not r["answered"]:
            bad.append(f"{where}: held '{held}' and nothing went out")
        if held != "ignore" and opts.get(held, {}).get("saves", 0) <= 0:
            bad.append(f"{where}: held a column that saves nothing")
        missing = r["intent"].get("missing_column") or []
        if missing:
            bad.append(f"{where}: the cell paid for {missing} and no such column was offered "
                       f"(carried {r['intent'].get('carried')})")
        if r["intent"].get("rows") and not r["intent"].get("no_go"):
            bad.append(f"{where}: threats in reach but no no-go circle for the planner")
        looks = r["answered"]
        bad += [f"{where}: {a.get('kind')} failed ({a['failed']})" for a in looks if a.get("failed")]
        bad += [f"{where}: {a.get('kind')} was priced but the body refused it ({a.get('refused')})"
                for a in looks if a["outcome"] == "refused" and a.get("refused") not in ("held",)]
        hurt = r["outcome"]["worst_hp"] < r["outcome"]["hp_before"]
        if hurt and not any(a["outcome"] == "answered" for a in looks):
            silent = sorted({a["outcome"] for a in looks}) or ["never looked"]
            bad.append(f"{where}: took damage and never answered ({', '.join(silent)})")
        if not looks:
            bad.append(f"{where}: the threat layer never looked at the world")
    return bad


def _shapes_fit_the_enemy(rows):
    """What a column is FOR, read off the belief table rather than off an enemy's name."""
    from .. import beliefs, field as _field
    bad = []
    for r in rows:
        where, held = _where(r), r["intent"].get("held")
        kind = ENEMY.get(r.get("enemy"))
        mob = beliefs.mob(kind) if kind in beliefs.MOBS else {}
        if held == "fight" and r.get("weapon") == "fist":
            bad.append(f"{where}: swung with nothing in hand")
        if held == "fight" and mob.get("burst"):
            bad.append(f"{where}: traded health against a blast")
        if held in SHAPE_COLUMNS and mob.get("squeezes"):
            bad.append(f"{where}: shaped the ground against something that walks over it")
        if held in SHAPE_COLUMNS and r.get("ground") == "open" \
                and not _field.Field(bucket="open").blocks_worth_placing():
            bad.append(f"{where}: placed blocks where there is nothing to place them against")
    return bad


def _more_of_them_costs_more(rows):
    """Two rows differing in exactly one dimension: more of them can never cost less to ignore, or tax less."""
    keys = [k for k in DIMS if k != "count"]
    by, bad = {}, []
    for r in rows:
        if "count" in r:
            by[tuple(r.get(k) for k in keys) + (r["count"],)] = r
    for key, one in by.items():
        if key[-1] != "one":
            continue
        three = by.get(key[:-1] + ("three",))
        if not three:
            continue
        owed = lambda row: ((row["intent"].get("options") or {}).get("ignore", {}) or {}).get("leaves")
        a, b = owed(one), owed(three)
        if a is not None and b is not None and b < a - 1e-6:
            bad.append(f"{_where(three)}: three leave less coming than one ({b} < {a})")
        if one["intent"].get("hp_tax_s", 0) > three["intent"].get("hp_tax_s", 0) + 1e-6:
            bad.append(f"{_where(three)}: three taxed the planner less than one")
    return bad


def _hostiles(radius=32, kinds=None):
    from ..world import entities
    kinds = kinds or {ENEMY[name] for _line_up, mobs, _carry_ in WAVES for name, _n in mobs}
    return [e for e in entities(radius) if e["type"] in kinds and e.get("health", 1) > 0]


def _summon(mobs, spread=4, seed=None):
    """Where the enemies appear. Jittered from the cell's seed: ten runs of an identical situation are ten copies
    of one sample, and a residual averaged over copies is no better than the one. The dimension says "near";
    exactly how near, and from which side, is what makes two passes independent evidence about the same cell —
    and the seed is what lets a surprising row be visited again.
    """
    out = []
    rng = random.Random(seed)
    for kind, n in mobs:
        turn = rng.random() * 2 * math.pi if seed is not None else 0.0
        for i in range(n):
            angle = turn + 2 * math.pi * i / max(1, n)
            # Never closer than the dimension says: "near" is a distance the cell declares, and a mob that spawns
            # on top of us is a different cell. Jitter opens the range, it does not close it.
            reach = spread * (rng.uniform(1.0, 1.4) if seed is not None else 1.0)
            dx, dz = round(reach * math.cos(angle)), round(reach * math.sin(angle))
            out.append(f"summon {kind} ~{dx} ~ ~{dz}")
    return out


def _siege_cells():
    for index, (name, mobs, _carry_) in enumerate(WAVES, start=1):
        yield {"wave": index, "line_up": name}


def _siege_build(cell):
    """No reset between waves: the siege is cumulative, and what a wave costs is the point of the next one."""
    return _summon(tuple((ENEMY[name], n) for name, n in {w[0]: w[1] for w in WAVES}[cell["line_up"]]))


def _siege_record(per_wave_s=90.0):
    def record(cell):
        from .. import api as _api
        _api.log(f"=== siege wave {cell['wave']}: {cell['line_up']}")
        row = _fought(lambda _c: None, seconds=per_wave_s)(cell)
        row["cleared"] = row["outcome"]["left"] == 0 and row["outcome"]["hp"] > 0
        _api.log(f"   wave {cell['wave']}: {'cleared' if row['cleared'] else 'NOT cleared'}"
                 f" with {row['outcome']['hp']:.0f} hp")
        return row
    return record


def _wave_cleared(rows):
    return [] if rows and all(r["cleared"] for r in rows) else [f"wave {rows[-1]['wave'] if rows else '?'} not cleared"]


def _siege_detail_of(name):
    rows = SWEEP.get(name) or []
    return (("cleared" if rows and rows[-1]["cleared"] else "not cleared")
            + (f", {rows[-1]['outcome']['hp']:.0f} hp" if rows else ""))


_FIGHT_SETUP = (["gamemode survival @p", "difficulty normal", "time set day", "clear @p"]
                + _platform(reach=ARENA_REACH, walled=True) + ["kill @e[type=!player,type=!item,distance=..48]"])
# Every fight carries a water bucket: a knock off a ledge is part of fighting (`_build` adds it to every cell).
FIGHT_BUCKET = ["give @p water_bucket"]


def _shards(cells, size):
    """Cut a sweep's cells into shards of `size`: one bench row each, so every row fits the 60 s limit."""
    cells = list(cells)
    return [cells[i:i + size] for i in range(0, len(cells), size)]


# The siege, one wave per row (the old row was cumulative, 820 s): each starts from its wave's `carry`.
for _wave, (_line_up, _, _left) in enumerate(WAVES, start=1):
    SCENARIOS[f"siege__w{_wave}"] = {
        "doc": f"Siege wave {_wave} of {len(WAVES)} ({_line_up}), sword, pickaxe, full iron, shield, food and blocks: "
               "every answer the model offers is available → the wave cleared alive.",
        "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:overworld",
        "setup": _FIGHT_SETUP + ["effect give @p minecraft:instant_health 3 10 true"] + _siege_kit()
        + _carry(*_left),
        "run": _sweep(f"siege__w{_wave}", lambda w=_wave, n=_line_up: iter([{"wave": w, "line_up": n}]), _siege_build,
                      _siege_record(per_wave_s=24.0), SIEGE_ROWS, settle=0.6),
        "check": _sweep_check(f"siege__w{_wave}", SIEGE_ROWS, [_answers_are_closed, _wave_cleared], least=1),
        "detail": lambda inv, w=_wave: _siege_detail_of(f"siege__w{w}"),
        "budget": 30,
    }

# combat_arena, one cell per row (15 s a cell, the row under 30 s): the same cells, the same per-row rules.
# Nothing to answer is one control cell, not one per ground: the ground only prices answers to an enemy.
ARENA_SHARDS = _shards([c for c in _cells(ARMED, dims=("kit", "blood"), over=("enemy", "ground"), repeat=CELL_REPEAT)
                        if ENEMY[c["enemy"]] is not None or c["ground"] == "open"], 1)
for _i, _shard in enumerate(ARENA_SHARDS, start=1):
    SCENARIOS[f"combat_arena__{_i}"] = {
        "doc": "combat_arena shard " + "; ".join(f"{c['enemy']}/{c['ground']}/{c['kit']}/{c['blood']}" for c in _shard)
               + ": each cell writes the whole decision into bench/combat.jsonl; the rules are relations between rows.",
        "module": "threat", "raw": True, "combat": True, "dimension": "minecraft:overworld", "sweep": True,
        "variant": [sorted(c.items()) for c in _shard],
        "setup": list(_FIGHT_SETUP),
        "expect": [(at(-9, -1, -9), at(12, -1, 9), "stone", 418, 418)],
        "run": _sweep(f"combat_arena__{_i}", lambda sh=_shard: iter(sh), _build,
                      _fought(_kinds_of, seconds=CELL_SECONDS), COMBAT_ROWS, settle=0.6),
        "check": _sweep_check(f"combat_arena__{_i}", COMBAT_ROWS,
                              [_answers_are_closed, _shapes_fit_the_enemy, _more_of_them_costs_more], least=len(_shard)),
        "tick_rate": 60, "budget": 30,
    }


# -- getting away -----------------------------------------------------------------------------------------------
# With a sword in hand the model rightly answers most things by swinging, so the other half of it — back off, put
# something in the way, get below the ground, eat, leave an enderman alone — is never exercised. One scenario, one
# cell per enemy: nothing to fight with, one enemy, sixty seconds, and the question is whether it is alive and
# further away than it started. The row says how it managed it, so a pass is still a measurement.

# The window is the threshold (alive and further away at its end). The cell is built in the row's setup (enemies
# summoned last), so the exposure starts at setup's end; the run watches the rest of it.
ESCAPE_SECONDS = 25.0      # the row's limit is 30 s (the user's rule): the window is what is left of it
ESCAPE_WATCH = ESCAPE_SECONDS - 2.0     # setup's end → the run's first look: ~2 s of the window already spent
for _cell in _cells(UNARMED, dims=("enemy", "ground", "kit")):
    _key = "_".join(str(_cell[k]) for k in ("enemy", "ground", "kit"))
    SCENARIOS[f"escape__{_key}"] = {
        "doc": f"No weapon, no armour, {_cell['enemy']} on {_cell['ground']} ground with {_cell['kit']}, "
               f"{ESCAPE_SECONDS:.0f} s: the answer has to come from somewhere other than swinging — back off, block "
               "the way, dig down, eat, or leave a teleporter alone (bench/escape.jsonl).",
        "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:overworld", "sweep": True,
        "setup": ["gamemode survival @p", "kill @e[type=!player,type=!item,distance=..48]"] + _build(_cell),
        "expect": [(at(-9, -1, -9), at(12, -1, 9), "stone", 418, 418)],
        "run": _sweep(f"escape__{_key}", lambda c=_cell: iter([c]), lambda c: [],
                      _fought(_kinds_of, seconds=ESCAPE_WATCH), ESCAPE_ROWS, settle=0.0),
        "check": _sweep_check(f"escape__{_key}", ESCAPE_ROWS, [_answers_are_closed, _shapes_fit_the_enemy], least=1),
        "detail": lambda inv, k=f"escape__{_key}": "; ".join(
            f"{r['enemy']}: {r['outcome']['hp']:.0f} hp, gap {r['outcome']['gap']}" for r in (SWEEP.get(k) or [])),
        "tick_rate": 60, "budget": 30,
    }



# -- the fight's behaviours, one cell each ---------------------------------------------------------------------
# Built by the same walker and the same `_build` as the arena; each cell is set up so that one answer is worth the
# most, and the row asks that it was chosen AND that it worked, read from the world (the gap's blocks, how far down
# or up the body went) and from the recorded row (what went out, what it cost in health).
BEHAVIOUR_ROWS = paths.data("bench/behaviour.jsonl")
BEHAVIOUR_SECONDS = 20.0
GAP = [at(4, y, z) for y in (1, 2, 3) for z in (-1, 0, 1)]          # the corridor's one gap (GROUND["corridor"])


def _last(name):
    rows = SWEEP.get(name) or []
    return rows[-1] if rows else None


def _went_out(row, *kinds):
    return any(a.get("kind") in kinds for a in row["answered"] if a.get("outcome") == "answered")


def _first_out(row):
    return next((a.get("kind") for a in row["answered"] if a.get("outcome") == "answered"), None)


def _ys(row):
    return [s["pos"][1] for s in row["trace"]] or [row["trace_start_y"]]


def _gap_blocked(api):
    from ..world import Region
    lo, hi = at(4, 1, -1), at(4, 3, 1)
    region = Region(lo, hi)
    return sum(1 for c in GAP if region.solid(c))


def _walled(row):
    """Cobblestone (or any placed solid) on all four sides of the feet AND the head cell, where the body ended."""
    from ..world import Region
    x, y, z = (math.floor(v) for v in row["trace"][-1]["pos"])
    region = Region((x - 1, y, z - 1), (x + 1, y + 1, z + 1))
    return all(region.solid((x + dx, y + dy, z + dz))
               for dy in (0, 1) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))


def _offhand_shield():
    from ..data import bare
    from ..world import Inventory
    return bare((Inventory().equipment.get("offhand") or {}).get("id", "")) == "shield"


def _less_hurt_than(row, control):
    base = _last(f"combat__{control}")
    return base is not None and row["outcome"]["hp_lost"] < base["outcome"]["hp_lost"]


START_Y = at(0, 0, 0)[1]
# name: (cell moved off ARMED, what must be true of the recorded row and the world, why). A control runs before
# the cell that is compared to it (dict order is bench order).
BEHAVIOURS = {
    "block_gap": (dict(ground="corridor", kit="blocks", distance="across"),
                  lambda r, api: _went_out(r, "reshape") and _gap_blocked(api) >= 1 and r["outcome"]["gap"] >= 2
                  and r["outcome"]["hp_lost"] <= 4,
                  "a corridor with one gap, blocks carried: the gap closed, the walker kept outside it"),
    "dig_in": (dict(ground="roofed", kit="blocks"),
               lambda r, api: _went_out(r, "reshape", "wall_in") and min(_ys(r)) <= START_Y - 2
               and r["outcome"]["hp_lost"] <= 4,
               "a roof overhead, blocks and a pickaxe: dug two down into the floor out of reach, health kept"),
    "pillar": (dict(ground="open", kit="blocks"),
               lambda r, api: _went_out(r, "reshape") and max(_ys(r)) >= START_Y + 2,
               "open ground, blocks: stood two up out of a walker's reach"),
    "shield_arrows": (dict(enemy="archer", kit="shield", distance="across"),
                      lambda r, api: _went_out(r, "shield") and _offhand_shield() and r["outcome"]["hp_lost"] <= 4,
                      "an archer across open ground, a shield: raised against the arrows (still in the offhand)"),
    "fight_without_shield": (dict(kit="nothing"),
                             lambda r, api: _went_out(r, "fight") and not _went_out(r, "shield")
                             and r["outcome"]["hp_lost"] > 0,
                             "a walker, no shield (control): fought, never blocked, and hurt for it"),
    "fight_and_block": (dict(kit="shield"),
                        lambda r, api: _went_out(r, "fight") and _went_out(r, "shield") and r["outcome"]["left"] == 0
                        and _less_hurt_than(r, "fight_without_shield"),
                        "the same walker, sword and shield: struck and blocked in turn, the walker dead, and less "
                        "hurt than the no-shield control"),
    "wall_in": (dict(count="three", kit="blocks", blood="hurt"),
                lambda r, api: _went_out(r, "wall_in") and _walled(r) and r["outcome"]["hp"] > 0,
                "three walkers, hurt, blocks: walled in (feet and head cells closed on four sides)"),
    "surrounded_low": (dict(count="three", blood="hurt", kit="full"),
                       lambda r, api: _first_out(r) in ("evade", "wall_in", "reshape") and not _went_out(r, "fight")
                       and r["outcome"]["hp"] > 0,
                       "three walkers at 8 hp: got away or walled in, never swung; alive"),
}


def _behaviour_check(name, rule):
    def check(api, _inv):
        row = _last(name)
        return row is not None and bool(rule(row, api))
    return check


def _record_with_start(record):
    def rec(cell):
        from ..world import Snapshot
        y = Snapshot().state["y"]
        return dict(record(cell), trace_start_y=y)
    return rec


for _bname, (_moved, _rule, _why) in BEHAVIOURS.items():
    _bcell = dict(next(iter(_cells(dict(ARMED, **_moved)))), seed=0)
    SCENARIOS[f"combat__{_bname}"] = {
        "doc": f"Fight behaviour: {_why}",
        "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:overworld", "stochastic": True,
        "variant": sorted(_bcell.items()),
        "setup": list(_FIGHT_SETUP),
        "expect": [(at(-9, -1, -9), at(12, -1, 9), "stone", 418, 418)],
        "run": _sweep(f"combat__{_bname}", lambda c=_bcell: iter([c]), _build,
                      _record_with_start(_fought(_kinds_of, seconds=BEHAVIOUR_SECONDS)), BEHAVIOUR_ROWS, settle=0.6),
        "check": _behaviour_check(f"combat__{_bname}", _rule),
        "tick_rate": 60, "budget": 30,
    }
