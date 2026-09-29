"""The bench's one vocabulary: the words a table row (bench_<tier>.py) is written in, and every helper they name.
The words live in `words/` (scene, checks, runs, fight, brain, door), one home each; this module gathers them, keeps
the sheet's own helpers and templates, and registers every word once (a name in two modules is an error).
Scene templates (→ console commands), predicates and checks (→ judged by the world and the bag), the run, hook
and interrupt words, and the row templates that make a family of rows from its parameters. `table.py` reads the
rows; the runner runs them.

Data conventions, one each:
- a position relative to the bench ORIGIN is ("@", dx, dy, dz) — `at(dx, dy, dz)`;
- a callable written inside another's arguments is a tuple whose head is "!" + its kind: ("!gain", "log", 2);
  at the top of a row's `check`, `before` and `run` the head is the bare kind (the slot says what it is);
  ("&name",) is the function `name` itself, not a call of it;
- ("$data", "bench/siege.jsonl") is a file in this player's data directory (paths.data); ("$set", [...]) a set;
- "$api", "$inv", "$ctx" stand for the objects a predicate or a run is called with; ("$ctx", "policy") is an
  attribute of the context, ("$call", name, *args) a helper called at run time (its args may hold these markers).
"""
import importlib
import json
import math
import operator
import os
import re
import sys
import time

import importlib
import json
import math
import operator
import os
import re
import sys
import time
from . import core, runner
from .core import bag_now
from ..api import McError
from ..data import DAY_TICKS, POD_BLOCKS
from .core import *          # noqa: F403  (the bench's primitives are this module's own vocabulary)
from .core import (BOX, FLAG, NOTES, ORIGIN, SCENARIOS, SetupInvalid, _achieve, _c, _chat, _checked,
                         _command, _count_blocks, _drain, _inv_has, _near, at, server_count, set_brain)
from .runner import *        # noqa: F403
from .runner import (LAST_FEEDBACK, LAST_LINES, _setup, _trace, classify, code_for, feedback_errors, load_table,
                           module_deps, record, run, save_table, setup_mismatches, silent_failure, status)
from .bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK   # the bases' data: one home
from .words import brain, checks, door, fight, runs, scene as _scene_words
from .words.scene import *  # noqa: F401,F403
from .words.checks import *  # noqa: F401,F403
from .words.runs import *  # noqa: F401,F403
from .words.fight import *  # noqa: F401,F403
from .words.brain import *  # noqa: F401,F403
from .words.door import *  # noqa: F401,F403

_BEFORE = set(globals())
# real structures in the test world (seed 1234): no box; /locate gives the truth
LEG_START = (10400, 200, 10400)

LEG = 200        # nether.locate_stronghold's sideways leg between the two throws

LEG_PAD = 12     # the eye's reading is a few degrees off /locate's: the plane is wider than the line

def _leg_box(start, stronghold):
    """Pure: (x0, z0, x1, z1) around the leg the skill walks, perpendicular to the line to the stronghold, padded."""
    x, z = start
    d = math.dist(stronghold, start) or 1.0
    ex, ez = round(x - (stronghold[1] - z) / d * LEG), round(z + (stronghold[0] - x) / d * LEG)
    return (min(x, ex) - LEG_PAD, min(z, ez) - LEG_PAD, max(x, ex) + LEG_PAD, max(z, ez) + LEG_PAD)

def _stronghold_leg(ctx):
    """The walk between throws on a flat stone plane at sky height (real hills were most of a 60 s row)."""
    real = locate_reply(LAST_FEEDBACK)
    if not real:
        raise SetupInvalid("no /locate answer for the stronghold")
    x, y, z = LEG_START
    box = _leg_box((x, z), real)
    _load_area(*box)
    _overworld(_flat(*box, y, "stone") + [f"tp @p {x} {y + 1} {z}", "effect give @p speed 60 3 true"])
    time.sleep(1)

def _load_area(x0, z0, x1, z1):
    """Force-load a footprint and wait until its corners answer ("That position is not loaded" otherwise)."""
    _command(f"execute in minecraft:overworld run forceload add {x0} {z0} {x1} {z1}", [])
    probes = [(px, pz) for px in (x0, x1) for pz in (z0, z1)]
    for _ in range(60):
        if not any("not loaded" in l for px, pz in probes for l in
                   _command(f"execute in minecraft:overworld run fill {px} 300 {pz} {px} 300 {pz} air", [])):
            return
        time.sleep(0.5)
    raise SetupInvalid(f"area {x0},{z0}..{x1},{z1} never loaded")

def _overworld(cmds):
    """Commands run in the Overworld from a hook; a refused one is a setup that did not happen."""
    for cmd in cmds:
        lines = _command(f"execute in minecraft:overworld run {cmd}", [])
        if any("not loaded" in l or "Unknown" in l or "Too many" in l for l in lines):
            raise SetupInvalid(f"{cmd}: {lines[:1]}")

def _flat(x0, z0, x1, z1, y, block):
    """Pure: fills covering a flat rectangle, each under the game's 32768-block limit."""
    step = max(1, 32768 // (z1 - z0 + 1))
    return [f"fill {a} {y} {z0} {min(a + step - 1, x1)} {y} {z1} {block}" for a in range(x0, x1 + 1, step)]

STRONGHOLD_AT = (20000, 150, 20000)     # a built stronghold piece, in a sealed stone block in the sky

ROOM_OFF = 64          # the ring's centre along +x: past the skill's 48-block scan, so the bricks are followed first

def _stronghold_piece(x, y, z):
    """Commands for a sealed stone-brick corridor ending in a portal room with 12 empty frames, without a /place structure."""
    f = lambda a, b, block: f"fill {a[0]} {a[1]} {a[2]} {b[0]} {b[1]} {b[2]} {block}"   # noqa: E731
    cx = x + ROOM_OFF
    return [f((x - 3, y - 2, z - 6), (cx + 6, y + 5, z + 6), "stone"),
            f((x - 1, y - 1, z - 2), (x + 58, y + 3, z + 2), "stone_bricks"),
            f((x, y, z - 1), (x + 58, y + 2, z + 1), "air"),
            f((cx - 5, y - 1, z - 5), (cx + 5, y + 4, z + 5), "stone_bricks"),
            f((cx - 4, y, z - 4), (cx + 4, y + 3, z + 4), "air"),
            f((x + 58, y, z - 1), (cx - 4, y + 2, z + 1), "air"),
            f((cx - 1, y, z - 2), (cx + 1, y, z - 2), "end_portal_frame[facing=south]"),
            f((cx - 1, y, z + 2), (cx + 1, y, z + 2), "end_portal_frame[facing=north]"),
            f((cx - 2, y, z - 1), (cx - 2, y, z + 1), "end_portal_frame[facing=east]"),
            f((cx + 2, y, z - 1), (cx + 2, y, z + 1), "end_portal_frame[facing=west]"),
            f"tp @p {x + 1} {y} {z}"]

def _built_stronghold(ctx):
    """The piece built fresh every run (a run digs it up), the estimate at the corridor's start where we stand."""
    x, y, z = STRONGHOLD_AT
    _load_area(x - 8, z - 8, x + ROOM_OFF + 8, z + 8)
    _overworld(_stronghold_piece(x, y, z))
    ctx.mem.add_site("stronghold", (x + 1, y, z), "minecraft:overworld", name="stronghold")
    time.sleep(1)

PORTAL_ROOM_OK = []

def _portal_room_run(ctx):
    """Run the search and note whether the skill itself succeeded (a frame in range is not success)."""
    from ..end import find_portal_room
    PORTAL_ROOM_OK.clear()
    find_portal_room(ctx)
    PORTAL_ROOM_OK.append(True)
    return True

def _portal_room_found():
    from ..end import ROOM_REACH
    from ..world import find as _find
    # the same radius as the skill contract (three different radii let a failed verify pass)
    return bool(PORTAL_ROOM_OK) and bool(_find(["end_portal_frame"], radius=ROOM_REACH, limit=1))

def _worn_head():
    from ..world import Inventory
    return ((bag_now().equipment.get("head") or {}).get("id") or "")

def _wait_landed(ctx, seconds=10):
    from .. import api
    from ..skillcore import settle
    s = settle(lambda: api.get("/state"), lambda st: st.get("onGround") or st.get("inWater") or st.get("dead"),
               timeout=seconds, stable_s=1.0, soft=True)
    return bool(s.get("onGround") or s.get("inWater") or s.get("dead"))

def _snap_survival(ctx, seconds=20):
    """Brain rounds until the body is back in the Overworld (leaving the Nether is an upkeep row)."""
    from .. import api
    t0 = time.time()
    home = lambda: api.get("/state")["dimension"] == "minecraft:overworld"  # noqa: E731
    core.BRAIN.wake = home               # an idle round ends the moment the body is home
    try:
        while time.time() - t0 < seconds and not home():
            core.BRAIN.round()
    finally:
        core.BRAIN.wake = None
    return home()

# the bucket sits in the main bag, not the hotbar: the clutch must select it; the water is scooped back after
_FALL_FLOOR = [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone", f"tp @p {_c(at(0, 0, 0))}", "clear @p",
               "give @p dirt 576"]

GHAST_MAX_HP = fight.GHAST_HP
GHAST_TARGET_DY = 4.0   # vanilla GhastEntity targets a player only within this height (|dy| ≤ 4.0, its target predicate)
GHAST_FIRE_R = 64.0     # vanilla ShootFireballGoal: fires within 64 blocks (4096 squared)
GHAST_OFF = GHAST_FIRE_R / 4    # out: well past melee, well inside its fire range

GHAST_HEIGHT = 4.0       # vanilla EntityType GHAST: 4 × 4


def ghast_cage():
    """Pure: the barrier shell (lo, hi offsets) that keeps a ghast's feet within GHAST_TARGET_DY of ours: its ceiling a
    ghast's height over the band's top, its floor under the band's bottom; across the row's box (the next row's setup
    clears it). The ghast drifted up and out of the band (231849: dy 3.4 → 17, never shooting)."""
    band = int(GHAST_TARGET_DY)
    return (BOX[0][0], -band - 1, BOX[0][2]), (BOX[1][0], band + int(GHAST_HEIGHT), BOX[1][2])


def _summon_ghast(ctx):
    """`before` hook: a ghast inside vanilla's targeting rule — its height off ours under GHAST_TARGET_DY — and a
    barrier shell that keeps it there (ghast_cage; `outline`: the hall inside is kept)."""
    lo, hi = ghast_cage()
    _chat(f"execute in minecraft:the_nether run fill {_c(at(*lo))} {_c(at(*hi))} barrier outline")
    x, y, z = at(GHAST_OFF, GHAST_TARGET_DY - 1.0, 0)
    _chat(f"execute in minecraft:the_nether run summon ghast {x} {y} {z} {{PersistenceRequired:1b}}")
GHAST = {}              # the ghast row's watch: its fireballs, the player's start and worst health, the ghast's last read

def ghast_health(lines):
    """Pure: the ghast's health from '/data get entity … Health' feedback, or None (dead, or never there)."""
    return fight.data_health(lines)

def ghast_readout(watch):
    """Pure: the ghast watch as the report reads it — seen, its last Health, fireballs seen, our start and worst hp,
    and each read of the ghast (ghast_read): why it fired or not."""
    return {"seen": watch.get("seen"), "ghast_hp": watch.get("hp"), "fireballs": len(watch.get("fireballs") or ()),
            "start_hp": watch.get("start"), "worst_hp": watch.get("worst"), "reads": list(watch.get("reads") or ())}

def ghast_read(ghast, body, t):
    """Pure: one read of the ghast (a read_combat view) against the body's /state: its height off ours (vanilla
    targets within GHAST_TARGET_DY), its distance, and whether it is shooting (busy); None when no ghast."""
    if ghast is None:
        return None
    return {"t": round(t, 1), "dy": round(ghast["y"] - body["y"], 2), "dist": round(ghast["distance"], 1),
            "shooting": bool(ghast.get("busy"))}

def ghast_was_answered(seen, ghast_hp, fireballs, start_hp, worst_hp):
    """Pure: the ghast was answered — it was there and is now hurt or dead, or it fired and nothing hit us."""
    if not seen:
        return False                 # no ghast ever read: nothing was answered
    if ghast_hp is None or ghast_hp < GHAST_MAX_HP:
        return True
    return fireballs >= 1 and worst_hp >= start_hp

def _ghast_hp():
    return ghast_health(_command("execute in minecraft:the_nether run data get entity "
                                 "@e[type=minecraft:ghast,limit=1,sort=nearest] Health", []))

def _ghast_watch(seconds=20):
    """The reflexes (invariants: a fireball in reach is struck back) until the ghast is hurt or dead, or a fireball it
    fired has resolved (hit, missed or struck back), at most `seconds` — never a fixed wait past the budget."""
    def run(ctx):
        from .. import api
        from ..perception import read_combat
        from ..world import entities
        t0 = time.time()
        hp0 = float(api.get("/state")["health"])
        GHAST.clear()
        GHAST.update(fireballs=set(), start=hp0, worst=hp0, seen=False, hp=None, reads=[])
        while time.time() - t0 < seconds:
            core.BRAIN.invariants()
            body = api.get("/state")
            GHAST["worst"] = min(GHAST["worst"], float(body["health"]))
            ghast = next(iter(read_combat(entities(64, ["minecraft:ghast"]))), None)
            read = ghast_read(ghast, body, time.time() - t0)
            if read is not None:
                GHAST["reads"].append(read)
            flying = {e.get("id") for e in entities(64, ["minecraft:fireball"])}
            resolved = bool(GHAST["fireballs"]) and not flying
            GHAST["fireballs"] |= flying
            GHAST["hp"] = _ghast_hp()
            GHAST["seen"] = GHAST["seen"] or GHAST["hp"] is not None
            if GHAST["seen"] and (GHAST["hp"] is None or GHAST["hp"] < GHAST_MAX_HP) or resolved:
                break
            time.sleep(0.2)
        return True
    return run

def _ghast_answered():
    """Check (the server's word): the ghast hurt or dead, or ≥ 1 fireball fired and the player never hit."""
    def check(api_, inv):
        return ghast_was_answered(GHAST.get("seen", False), _ghast_hp() if GHAST.get("seen") else None,
                              len(GHAST.get("fireballs", ())), GHAST.get("start", 0.0), GHAST.get("worst", 0.0)) \
            and not api_.get("/state")["dead"]
    return check

FORTRESS_RUN = {}

def _far_from_fortress(ctx):
    """Stand ~20 blocks from the real fortress, below the roof (spreadplayers 'under 90' picks a floor there)."""
    real = locate_reply(LAST_FEEDBACK)
    if not real:
        raise SetupInvalid("no /locate answer for the fortress")
    _chat(f"execute in minecraft:the_nether run spreadplayers {real[0] + 20} {real[1]} 0 6 under 90 false @p")
    time.sleep(3)
    # the memory held a fortress from an earlier row, which passed this one without a step
    from .. import api
    from ..memory import Memory
    s = api.get("/state")
    FORTRESS_RUN.clear()
    FORTRESS_RUN["start"] = (s["x"], s["y"], s["z"])
    FORTRESS_RUN["before"] = {tuple(round(c) for c in site["pos"])
                              for site in Memory(NOTES).sites("minecraft:the_nether", kinds=["fortress"])}

def _found_fortress_now():
    """The run passed only if it wrote a fortress site this time, and one the player actually walked to."""
    from ..memory import Memory
    sites = Memory(NOTES).sites("minecraft:the_nether", kinds=["fortress"])
    start = FORTRESS_RUN.get("start")
    for site in sites:
        pos = tuple(round(c) for c in site["pos"])
        if pos in FORTRESS_RUN.get("before", ()):
            continue
        if start and math.hypot(pos[0] - start[0], pos[2] - start[2]) >= 60:
            return True
    return False

def _trek(dx, dz, dimension="minecraft:overworld"):
    """Walk a straight-line distance over real terrain; note seconds per 100 blocks (travel was 47 % of task time)."""
    def run(ctx):
        from .. import api, nav
        s = api.get("/state")
        start = (s["blockX"], s["blockY"], s["blockZ"])
        target = (start[0] + dx, start[1], start[2] + dz)
        # in this process, not through the notes file: a reload from disk found no trek and failed a finished walk
        TREK.clear()
        TREK.update(start=start, target=target, t0=time.time())
        ok = nav.go_to(target, ctx.policy, range_=12, attempts=1)
        stop = api.get("/state")
        TREK["end"] = (stop["x"], stop["y"], stop["z"])
        TREK["seconds"] = time.time() - TREK["t0"]
        if not ok and math.hypot(stop["x"] - target[0], stop["z"] - target[2]) > 14:
            raise api.NavFailed(f"trek to {target} stopped short")
        return True
    return run

TREK = {}

def _trek_detail(inv):
    if not TREK.get("end"):
        return ""
    dist = math.hypot(TREK["target"][0] - TREK["start"][0], TREK["target"][2] - TREK["start"][2])
    left = math.hypot(TREK["end"][0] - TREK["target"][0], TREK["end"][2] - TREK["target"][2])
    return f"{dist:.0f} blocks, {TREK['seconds'] / dist * 100:.1f} s/100 (wall), ended {left:.0f} from target"

def _trek_check(api):
    if not TREK.get("end"):
        return False
    left = math.hypot(TREK["end"][0] - TREK["target"][0], TREK["end"][2] - TREK["target"][2])
    return left <= 14 and not api.get("/state")["dead"]

def _road_reuse(ctx):
    """There, back, and there again over 150 blocks: the third trip follows the remembered legs, no slower than the first."""
    from .. import api, nav
    s = api.get("/state")
    a = (s["blockX"], s["blockY"], s["blockZ"])
    b = (a[0] + ROAD_LEG, a[1], a[2])
    times = []
    for target in (b, a, b):
        t0 = time.time()
        if not nav.go_to(target, ctx.policy, range_=12, attempts=1):
            raise api.NavFailed(f"road trip to {target} stopped short")
        times.append(time.time() - t0)
    ROAD_TIMES[:] = times
    return True

ROAD_TIMES = []
from .. import lifecycle as _lifecycle  # noqa: E402
_lifecycle.in_place(__name__, "PORTAL_ROOM_OK", "FORTRESS_RUN", "TREK", "ROAD_TIMES", "GHAST")     # a row's own records

ROAD_LEG = 10      # three legs of 10 blocks: the reuse is what is judged, not the distance

def cover(conditions, bases, pinned=()):
    """Pure: the (condition, base) pairs the sheet runs — coverage, not the full product."""
    order = list(bases)
    pairs = [(c, b) for c, v in conditions.items() for b in order if b in v["bases"]]
    must = lambda c, b: {("must", b)} if conditions[c].get("fails") else set()     # noqa: E731
    need = {("cond", c) for c, _b in pairs} | {("axis", conditions[c]["axis"], b) for c, b in pairs}
    need |= {m for c, b in pairs for m in must(c, b)}
    new = lambda p: ({("cond", p[0]), ("axis", conditions[p[0]]["axis"], p[1])} | must(*p)) & need   # noqa: E731
    out = [p for p in pairs if p in set(pinned)]
    for p in out:
        need -= new(p)
    while need:
        best = max(pairs, key=lambda p: (len(new(p)), -pairs.index(p)))
        out.append(best)
        need -= new(best)
    return sorted(out, key=pairs.index)

def _broken_hut(ctx):
    """The hut in the arena as a remembered site whose snapshot is the whole wall (taken before it was broken)."""
    lo, hi = at(2, 0, -2), at(6, 2, 2)
    blocks = {f"{x},{y},{z}": "cobblestone" for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1)
              for z in range(lo[2], hi[2] + 1) if x in (lo[0], hi[0]) or y in (lo[1], hi[1]) or z in (lo[2], hi[2])}
    site = ctx.mem.add_site("shelter", at(4, 0, 0), "minecraft:overworld", name="bench-hut",
                            snapshot={"lo": list(lo), "hi": list(hi), "blocks": blocks})
    return site

# searching needs real terrain: raw rows, judged by what they found
def _found_near(blocks, r=6):
    def check(api, inv):
        from ..world import find
        return bool(find(blocks, radius=r, limit=1))
    return check

def _on_rim(top):
    """Standing dry on the rim at `top` (the block under the feet is the rim, not water)."""
    def check(api, inv):
        s = api.get("/state")
        return s["onGround"] and not s["inWater"] and s["y"] >= at(0, top + 1, 0)[1] - 0.5
    return check

def _surfaced(top, hold_s=2.0):
    """Out of the water's grip: on the rim, or a full air bar with the head out for `hold_s`."""
    def check(api, inv):
        if _on_rim(top)(api, inv):
            return True
        if not _breathing(300)(api, inv):
            return False
        time.sleep(hold_s)
        return _breathing(300)(api, inv) or _on_rim(top)(api, inv)
    return check

# -- jar 0.1.39 gaps: placing by facing, boats, awkward start cells
def placed_facing_in(region, pos, item, facing):
    """Pure: the block at `pos` is `item` and reports `facing`."""
    from ..data import bare
    return bare(region.name(pos) or "air") == bare(item) and region.prop(pos, "facing") == facing

def _placed_facing(pos, facing, item):
    """The block at `pos` is the asked `item` and reports `facing`."""
    def check(api, inv):
        from ..world import Region
        return placed_facing_in(Region(pos, pos, props=True), pos, item, facing)
    return check

def _place_facing(item, pos, facing):
    def run(ctx):
        from ..building import place_oriented
        return place_oriented(ctx, pos, item, facing) or True
    return run

def _queue(goal):
    """`before` hook: put a task at the head of the queue, as L3 would."""
    def hook(ctx):
        from .. import tasks
        tasks.add(goal, front=True, source="bench")
    return hook

# -- where things come from (decompose.SOURCES): the plan is under test. A frame with its bottom two cells missing, resumed by the cast
PORTAL_8_OF_10 = [f"fill {_c(at(-3, 0, 2))} {_c(at(0, 4, 2))} obsidian",
                  f"fill {_c(at(-2, 0, 2))} {_c(at(-1, 3, 2))} air"] + \
                 [f"setblock {_c(at(x, y, 2))} cobblestone" for x in (-3, 0) for y in (0, 4)]

# -- the producers skills' `gives` added (farm, villager, bucket), console-built, judged by the bag
def _villager(pos, buy, n_buy, sell, n_sell, profession="farmer"):
    """A villager that stays put (NoAI) with one offer: `n_buy` of `buy` → `n_sell` of `sell`."""
    return (f'summon villager {_c(pos)} {{NoAI:1b,VillagerData:{{profession:"minecraft:{profession}",level:2,'
            f'type:"minecraft:plains"}},Offers:{{Recipes:[{{buy:{{id:"minecraft:{buy}",count:{n_buy}}},'
            f'sell:{{id:"minecraft:{sell}",count:{n_sell}}},maxUses:12}}]}}}}')

FARM_KIT = ["give @p diamond_hoe", "give @p wheat_seeds 8", "give @p water_bucket",
            "give @p diamond_shovel"]      # the centre's dig at once (dirt and grass: a shovel's)

FARM_TICK_SPEED = 4096      # random ticks per section per tick: every block ticked ~once a tick — a sown crop ripe in ~2-4 s (1000 left the 8 cells unripe 16 s after sowing: bread_from_a_farm 10:43 TIMEOUT); the bench keeps 0

RIPE_PLOT = [f"fill {_c(at(4, -1, -1))} {_c(at(6, -1, 1))} farmland", f"fill {_c(at(4, 0, -1))} {_c(at(6, 0, 1))} wheat[age=7]"]

def _growing(run):
    """The run with fast crop ticks once the plot is watered — the plot is built at the normal tick speed (sped-up
    ticks turned the dug centre's dirt to grass and the break lagged the server by seconds) — reset to 0 however it
    ends: the plan's own plot ripens within the row."""
    def go(ctx):
        from ..world import find
        stop = _threading.Event()

        def watered():
            while not stop.is_set():
                try:
                    if find(["water"], radius=10, limit=1):
                        _checked(f"execute in minecraft:overworld run gamerule random_tick_speed {FARM_TICK_SPEED}", [])
                        return
                except (McError, SetupInvalid):
                    pass
                stop.wait(0.25)
        _threading.Thread(target=watered, daemon=True).start()
        try:
            return run(ctx)
        finally:
            stop.set()
            _checked("execute in minecraft:overworld run gamerule random_tick_speed "
                     + core.BENCH_WORLD["gamerule random_tick_speed"], [])
    return go

# == tiers: core runs on every change, common when a related module changed, exception before a merge, acceptance alone
TIERS = ("core", "common", "brain", "combat", "exception", "acceptance")

# fighting is its own tier
COMBAT_PREFIXES = ("fight_", "combat_arena", "siege__", "escape__", "fight_before_upkeep", "combat__")

# fights whose names say otherwise; resume_after_combat left out on purpose
COMBAT_ROWS = ("collect_blaze_rods", "ghast_fireball")

# the chain's first slice is common, not core: core is what every change can afford
CORE = tuple(f"{b}__base" for b in BASES) + ("lava_edge_walk", "drowning_in_a_pit", "buried_by_sand",
                                             "iron_ingots", "bed_in_nether", "slice_start_tools", "water_clutch")

COMMON_CONDITIONS = ("night", "canopy", "cave", "full_bag", "interrupt_mid_work")

# upkeep's rows and point-B hazards are everyday: common
COMMON = ("dig_in_night", "reach_land_swim", "chest_or_tree", "cross_lava_8", "cave_escape",
          "slice_nether_kit")

ACCEPTANCE = (ACCEPTANCE_D,)

def tier_of(name, row):
    """Pure: the tier a row belongs to (a row that states its own tier keeps it)."""
    if name.startswith(COMBAT_PREFIXES) or name in COMBAT_ROWS or row.get("module") == "fight_loop":
        return "combat"
    if row.get("tier_fixed") in ("core", "common", "brain", "combat", "exception"):
        return row["tier_fixed"]
    if name in CORE:
        return "core"
    if name in ACCEPTANCE:
        return "acceptance"
    if name in COMMON:
        return "common"
    tags = row.get("tags", {})
    if tags.get("base") in BASES and any(tags.get(ax) in COMMON_CONDITIONS for ax in ("terrain", "timing", "inventory")):
        return "common"
    return "exception"

def proves(entry, registry):
    """The skill names a row's `skills` entry proves: a registered name, or every skill providing that effect."""
    by_effect = {n for n, c in registry.items() if entry in getattr(c, "provides", {})}
    return ({entry} if entry in registry else set()) | by_effect

def select(rows, tier="core", changed=None, registry=None):
    """Pure: the names to run."""
    picked = [n for n, r in rows.items() if tier == "all" or r.get("tier") == tier]
    if changed is None:
        return picked
    registry = registry or {}
    changed = set(changed)
    hit = [n for n in picked if any(proves(e, registry) & changed for e in rows[n].get("skills", ()))]
    if hit:
        return hit
    return [n for n, r in rows.items() if r.get("tier") == "core"]

def touched_skills(hunks, spans):
    """Pure: skills whose function body a diff touched."""
    out = set()
    for name, (path, lo, hi) in spans.items():
        if any(lo <= ln <= hi for ln in hunks.get(path, ())):
            out.add(name)
    return out

def diff_hunks(diff_text):
    """Pure: {path: [line numbers]} of lines added or changed, from `git diff -U0` output."""
    out, path = {}, None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and path:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m is None:
                continue
            start, n = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(path, []).extend(range(start, start + max(n, 1)))
    return out

def skill_spans(registry, root):
    """{skill name: (path relative to `root`, first line, last line)} of each registered skill's function."""
    import inspect
    out = {}
    for name, c in registry.items():
        try:
            lines, first = inspect.getsourcelines(c.fn)
            src = inspect.getsourcefile(c.fn)
            if src is None:
                continue
            path = os.path.relpath(src, root)
        except (OSError, TypeError):
            continue
        out[name] = (path, first, first + len(lines) - 1)
    return out

def _kit_gives(row, jobs):
    """The gives the kit rule adds to `row`: the best work tool per job, and for a fight the sword its mobs call for."""
    import re
    from .core import BEST_TOOLS, kit_sword
    mobs = set(re.findall(r"summon (?:minecraft:)?(\w+)", " ".join(map(str, row.get("setup", ())))))
    enemy = (row.get("tags") or {}).get("enemy")
    mobs |= {enemy} if enemy else set()
    if any(k in s for s in list(row.get("skills", ())) + [str(row.get("doc", ""))] for k in ("dragon", "crystal")):
        mobs.add("ender_dragon")          # the End fight: the dragon is there, not summoned
    return [kit_sword(sorted(mobs)) if j == "sword" else BEST_TOOLS[j] for j in jobs]

def base_row(name, base, cond=None, surprise=None):
    """A base changed by a condition or a surprise (the old sheet's `_row`), as data."""
    from .bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK
    from .runner import ROW_LIMIT_S
    b, c = BASES[base], CONDITIONS[cond] if cond else {}
    x = SURPRISES[surprise] if isinstance(surprise, str) else surprise or {}     # a one-off row: its own surprise
    scene = list(x["scene"]) if x.get("replace_setup") else b["scene"] + c.get("scene", []) + x.get("scene", [])
    hooks: list[tuple]
    run, check, hooks = x.get("run", b["run"]), x.get("check", b["check"]), [("start", name)]
    fails = x.get("fails", c.get("fails"))
    scene += c.get("scene_for", {}).get(base, [])
    hooks += [h for h in (b.get("pre"), x.get("before"), c.get("before")) if h]
    if x.get("run_n"):
        run = ("skill", "chop", x["run_n"])
    if c.get("goal_met"):
        scene += [("give", "oak_log" if t == "log" else t.split(":")[-1], n) for t, n in b["needs"]]
        run, check = ("plan_is_empty", b["needs"]), ("same_bag_and_place",)
    resume = ("!unless_done", nest(b["check"]), ("!achieve_needs", b["needs"]) if b.get("needs") else nest(b["run"]))
    kind = c.get("interrupt")
    if kind:
        act = {"mid": "inject_interrupt", "twice": "inject_interrupt", "contested": "post_foreign_task",
               "player": "take_over"}.get(kind)
        if act == "inject_interrupt":
            # while a jar task runs: on the bag's gain it landed after the work's last check
            hooks.append(("on_task", name, ("&" + act,)) + ((2,) if kind == "twice" else ()))
        elif act:
            hooks.append(("on_progress", name, _progress(b), ("&" + act,)) + ((2,) if kind == "twice" else ()))
        else:
            hooks.append(("interrupt_when", ("!gained_at_least",) + tuple(b["effect"]), None,
                          "bench: interrupt at the moment of success"))
        run = ("resume", name, nest(run), resume)
        if kind == "success" and b.get("bound"):
            check = ("gain",) + tuple(b["bound"])
        check = ("all", nest(check), ("!interrupted", 2) if kind == "twice" else ("!interrupted",))
    if c.get("hazard"):
        if c["hazard"] == "sand":
            hooks.append(("on_progress", name, _progress(b), ("&sand_on_head",)))
        run = ("after_l0", name, nest(run), resume)
        check = ("all", nest(check), ("!alive", 8), ("!call", "head_clear", []), ("!not", ("!state", "inLava")))
    if fails:
        run = ("expect_failure", name, nest(run), fails)
        effect = x.get("check") or c.get("fails_check", {}).get(base) or ("same_bag",)
        check = ("all", ("!failed_as_expected",), ("!alive",), nest(effect))
    target = TARGET_S[base] * TARGET_SLACK if not c and not x and base in TARGET_S else None
    if target:
        run = ("timed", nest(run))
    row = {"name": name, "doc": f"{b['doc']} — {x.get('doc') or c.get('doc', 'as is')}", "module": "skills",
           "scene": scene, "before": hooks, "run": run, "check": items(check),
           **({"target_s": target} if target else {}),
           "budget": min(ROW_LIMIT_S, b["budget"] * (2 if c.get("tick_rate", 20) < 20 else 1)),
           "skills": list(b["skills"]), "point": x.get("point", b.get("point", "A")),
           "tags": {"base": base, **({c["axis"]: cond} if c else {}), **({"surprise": name} if x else {})}}
    if fails:
        row["fails"] = fails
    for key in ("tick_rate", "dimension"):
        if c.get(key) or x.get(key) or b.get(key):
            row[key] = x.get(key) or c.get(key) or b.get(key)
    if b.get("entities"):
        row["expect_entities"] = list(b["entities"])
    if b.get("combat"):
        row["combat"] = True
    row["expect"] = BOX_EXPECT
    return row

def one_row(name, skills, doc, scene, run, check, budget, tick_rate=None):
    """One skill proven in the world, once (the old `_ONE`): timed when its skill has a speed target."""
    target = TARGET_S[skills[0]] * TARGET_SLACK if skills[0] in TARGET_S else None
    return _row(name, doc, "skills", scene, ("timed", nest(run)) if target else run, items(check), budget=budget,
                skills=list(skills), tags={"base": skills[0]}, **({"target_s": target} if target else {}),
                **({"tick_rate": tick_rate} if tick_rate else {}))

# Real terrain at 14200: its chunks loaded first, then a dry standing spot anywhere within 64 — a radius of 4 found
# none ("Could not spread 1 entity": water or leaves all round the centre), and the row never began.
REAL_KIT = [("cmd", "forceload add 14136 14136 14264 14264"), ("cmd", "spreadplayers 14200 14200 0 64 false @p"),
            ("cmd", "clear @p"), ("give", "stone_pickaxe"),
            ("give", "torch", 8), ("give", "cobblestone", 32), ("give", "cooked_beef", 8)]

def real_row(name, skills, doc, run, check, budget, extra=(), stochastic=False, before=()):
    """On real terrain (raw), a target put in scan range: judged by what was found."""
    row = _row(name, doc, "skills", REAL_KIT + list(extra), run, items(check), budget=budget, raw=True, release=True,
               skills=list(skills), tags={"base": skills[0], "terrain": "real"}, before=before)
    if stochastic:
        row["stochastic"] = True
    del row["expect"]
    return row

def place_row(name, item, asked, want, tier):
    """Place a block asking a facing: the block reports the facing its own rule gives."""
    p = ("@", 3, 0, 0)
    return _row(name, f"Place {item.split(':')[1]} asking facing={asked} (the jar turns the body by the block's own "
                      f"rule) → the block reports facing={want}", "building",
                [("floor",), ("stand",), ("give", item.split(":")[1], 2)], ("place_facing", item, p, asked),
                [("placed_facing", p, want, item)], budget=20, skills=[], tier_fixed=tier, tags={"base": "place"},
                variant=(item, asked))

def start_row(name, what, start_scene, stand):
    """Walk 10 blocks from an awkward start cell → at the target."""
    return _row(name, f"Walk 10 blocks starting on {what} (GotoTask's start cell: '1 positions explored' reproduces "
                      f"here) → at the target", "nav",
                [("floor",), ("fill", ("@", 8, -3, -3), ("@", 12, -1, 3), "stone")] + list(start_scene)
                + [("stand",) + tuple(stand)], ("skill", "travel_to", ("@", 10, 0, 0), 2),
                [("_at", ("@", 10, 0, 0), 3.5)], skills=["goto"], tier_fixed="common",
                tags={"base": "nav", "start": what})

TEMPLATES = {t: globals()[f"{t}_row"] for t in ("base", "one", "real", "place", "start")}
NAMES = {"base": lambda base, cond=None, surprise=None: surprise or f"{base}__{cond or 'base'}",
         **{t: (lambda name, *p: name) for t in ("one", "real", "place", "start")}}
NAMED = {"arena", "fight_cell", "deflect", "one", "real", "place", "start", "brain", "dirt", "door"}  # templates whose first parameter is only the row's name
WORD_MODULES = (_scene_words, checks, runs, fight, brain, door)


def merged(tables, what):
    """One table from each module's own: a key two modules define is an error, never a silent pick."""
    out = {}
    for t in tables:
        clash = sorted(set(out) & set(t))
        if clash:
            raise ValueError(f"{what} defined twice: {clash}")
        out.update(t)
    return out


TEMPLATES = merged([fight.TEMPLATES, brain.TEMPLATES, door.TEMPLATES, TEMPLATES], "templates")
NAMES = merged([fight.NAMES, brain.NAMES, door.NAMES, NAMES], "template names")
_OWN = set(globals()) - _BEFORE - {"TEMPLATES", "NAMES", "NAMED", "WORD_MODULES", "merged", "_BEFORE"}
merged([dict.fromkeys(m.__all__) for m in WORD_MODULES] + [dict.fromkeys(_OWN)], "words")    # one home each
# pyright's view of the words: a static __all__ it can follow; at run time every name here is a word (below)
__all__ = ["BASES", "FLAG", "MAX_RUNS", "NEXT_ROW", "NOTES", "PORTAL_8_OF_10", "ROAD_TIMES", "SCENARIOS", "_achieve", "_c",
           "_chat", "_count_blocks", "_drain", "_inv_has", "_queue", "_road_reuse", "_worn_head", "at", "base_row",
           "cached_timeout", "code_for", "core", "diff_hunks", "failed_last", "jar_matches_source", "load_table",
           "one_row", "pending", "real_row", "fresh_row", "reset_brain", "run", "save_table", "select", "set_brain", "skill_spans",
           "status", "tier_of", "time", "touched_skills", "verdict"]
__all__ += _scene_words.__all__
__all__ += checks.__all__
__all__ += runs.__all__
__all__ += fight.__all__
__all__ += brain.__all__
__all__ += door.__all__
globals()["__all__"] = [n for n in dir() if not n.startswith("__")]      # the tables write in every word here
REGISTRY.update({n: globals()[n] for n in globals()["__all__"]})
