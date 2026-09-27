"""The bench's one vocabulary: the words a table row (bench_<tier>.py) is written in, and every helper they name.
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
from . import core, runner
from ..data import POD_BLOCKS
from .core import *          # noqa: F403  (the bench's primitives are this module's own vocabulary)
from .core import (BOX, FLAG, NOTES, ORIGIN, SCENARIOS, SetupInvalid, _achieve, _c, _chat, _checked,
                         _command, _count_blocks, _drain, _inv_has, _near, at, server_count, set_brain)
from .runner import *        # noqa: F403
from .runner import (LAST_FEEDBACK, LAST_LINES, _setup, _trace, classify, code_for, feedback_errors, load_table,
                           module_deps, record, run, save_table, setup_mismatches, silent_failure, status)
from .bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK   # the bases' data: one home
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

# a dragon worn down, crystals gone: the fight's last phase fits a row; the full fight is the acceptance run's
WORN_DRAGON = ["kill @e[type=end_crystal]", "data merge entity @e[type=ender_dragon,limit=1] {Health:8f}"]

def _wear_dragon(ctx):
    for cmd in WORN_DRAGON:
        _chat(f"execute in minecraft:the_end run {cmd}")
    time.sleep(0.5)

def _worn_head():
    from ..world import Inventory
    return ((Inventory().equipment.get("head") or {}).get("id") or "")

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
    while time.time() - t0 < seconds and api.get("/state")["dimension"] != "minecraft:overworld":
        core.BRAIN.round()
    return api.get("/state")["dimension"] == "minecraft:overworld"

# the bucket sits in the main bag, not the hotbar: the clutch must select it; the water is scooped back after
_FALL_FLOOR = [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone", f"tp @p {_c(at(0, 0, 0))}", "clear @p",
               "give @p dirt 576"]

def _reflex_for(seconds):
    def run(ctx):
        t0 = time.time()
        while time.time() - t0 < seconds:
            core.BRAIN.invariants()
            time.sleep(0.2)
        return True
    return run

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
ROAD_LEG = 10      # three legs of 10 blocks: the reuse is what is judged, not the distance
# -- slices: the cerebellum itself over a private task queue; measures scheduling and chaining (loops, idle holds, wrong-way unstucks)
SLICE = {}
# rounds given to a waiting kind: with work queued, any is a waste
MAX_WAITS_WITH_QUEUE = 0

def slice_report(lines, positions, target, idle_s, picks=None):
    """Pure: loops, the longest idle, and the total walked away from `target`, from a slice's log lines and samples."""
    from .. import review
    entries = []
    for raw in lines:
        parts = raw.split(" ", 1)
        if len(parts) == 2 and len(parts[0]) == 8 and parts[0].count(":") == 2:
            entries.append((parts[0], parts[1]))
    # review.repeated returns text: "- none" read as a loop
    loops = [l[2:] for l in review.repeated(entries, at_least=4).splitlines() if l.strip() and l != "- none"]
    away = 0.0
    if target is not None:
        d = [math.dist((p[0], p[2]), (target[0], target[2])) for _, p in positions]
        away = sum(max(0.0, b - a) for a, b in zip(d, d[1:]))
    from ..arbiter import waits
    return {"loops": loops, "idle_s": round(idle_s), "away_m": round(away), "waits": waits(picks or {})}

def queue_finished(items):
    """Pure: every task of a slice's queue left the live states — the slice's own work is over."""
    from ..tasks import LIVE
    return bool(items) and all(t["state"] not in LIVE for t in items)

def tier_rows(rows, tier, named):
    """Pure: the rows a tier selects for `--failed` / `--pending`: never acceptance; only `tier`'s when one was named."""
    return [n for n, r in rows.items() if r["tier"] != "acceptance"
            and (not named or tier == "all" or r["tier"] == tier)]

def _slice(done, minutes, target=None, queue=(), max_idle=15):
    """Run the whole cerebellum until done() or `minutes`, on a private task queue holding `queue`."""
    def run(ctx):
        from .. import api, tasks
        from ..world import Snapshot
        saved = tasks.FILE
        tasks.FILE = os.path.join(os.path.dirname(NOTES), "slice-tasks.json")
        tasks.save([])
        for goal in queue:
            tasks.add(goal, source="bench")
        core.BRAIN.idle_since, core.BRAIN.committed = None, None
        core.BRAIN.picks.clear()              # this slice's rounds only
        t0, positions, idle = time.time(), [], 0.0
        start_line = len(sys.stdout.lines) if hasattr(sys.stdout, "lines") else 0
        stopped = None
        try:
            while time.time() - t0 < minutes * 60:
                # the queue's goals finished ends the slice: idle stocking after is not the row's work
                if (done is not None and done()) or (queue and queue_finished(tasks.load())):
                    break
                try:
                    core.BRAIN.round()
                except api.McError as e:
                    api.log(f"!! round: {e}")
                s = Snapshot()
                positions.append((time.time(), s.feet))
                if core.BRAIN.idle_since:
                    idle = max(idle, time.time() - core.BRAIN.idle_since)
                lines = sys.stdout.lines[start_line:] if hasattr(sys.stdout, "lines") else []
                rep = slice_report(lines, positions, target, idle, core.BRAIN.picks)
                if rep["loops"] or rep["idle_s"] > max_idle:
                    stopped = f"loop: {rep['loops'][0]}" if rep["loops"] else f"idle {rep['idle_s']}s"
                    break
        finally:
            tasks.FILE = saved             # the slice's private queue must not leak into the next scenario
            SLICE.update(seconds=time.time() - t0, positions=positions, idle=idle, target=target, queued=bool(queue),
                         picks=dict(core.BRAIN.picks))
        if stopped:
            raise api.McError(f"slice stopped early — {stopped}")
        SLICE.update(seconds=time.time() - t0, positions=positions, idle=idle, target=target)
        if done is not None and not done():
            last = [l.strip() for l in (sys.stdout.lines[start_line:] if hasattr(sys.stdout, "lines") else [])
                    if "→" in l or "!!" in l or "task" in l or "upkeep" in l]
            raise api.McError(f"slice not done after {minutes} min, last decision: {last[-1] if last else 'no decision logged'}")
        return True
    return run

def _slice_detail(inv):
    if not SLICE:
        return ""
    rep = slice_report(LAST_LINES, SLICE["positions"], SLICE["target"], SLICE["idle"], SLICE.get("picks"))
    return (f"loops: {'; '.join(rep['loops'][:3]) or '0'}, {SLICE['seconds']:.0f}s, longest idle {rep['idle_s']}s, "
            f"walked away {rep['away_m']} m, waits {rep['waits']}")

def slice_verdict(finished, rep, queued, max_idle, max_loops, picks=None):
    """Pure: (passed, the one line that says why) — every part named, so a failed slice carries its reason."""
    from .. import arbiter
    waited = {k: n for k, n in (picks or {}).items() if k in arbiter.WAIT_KINDS}
    ok = finished and rep["idle_s"] <= max_idle and len(rep["loops"]) <= max_loops \
        and not (queued and rep["waits"] > MAX_WAITS_WITH_QUEUE)
    return ok, (f"slice check: done={finished} idle_s={rep['idle_s']}/{max_idle} loops={len(rep['loops'])}/{max_loops}"
                f" waits={rep['waits']}/{MAX_WAITS_WITH_QUEUE if queued else '-'} {waited}")

def _slice_check(done, max_idle=15, max_loops=0):
    said = []
    def check(api, inv):
        if not SLICE:
            return False
        finished = done is None or bool(done())
        rep = slice_report(LAST_LINES, SLICE["positions"], SLICE["target"], SLICE["idle"], SLICE.get("picks"))
        ok, why = slice_verdict(finished, rep, SLICE.get("queued"), max_idle, max_loops, SLICE.get("picks"))
        if not ok and why not in said:          # polled: the same reason once
            said.append(why)
            api_mod = __import__("bonobo.api", fromlist=["log"])
            api_mod.log(why)
        return ok
    return check

def _has_stone_pickaxe():
    from ..world import Inventory
    return Inventory().count("minecraft:stone_pickaxe") >= 1

def _nether_kit_ready():
    from ..knowledge import nether_kit_missing
    from ..world import Inventory
    return not nether_kit_missing(Inventory())

def _in_overworld():
    from .. import api
    return api.get("/state")["dimension"] == "minecraft:overworld"

def _portal_beside_player(ctx):
    """A lit portal three blocks east of the player, remembered as built."""
    from .. import api
    s = api.get("/state")
    x, y, z = s["blockX"] + 3, s["blockY"], s["blockZ"]
    for cmd in (f"fill {x} {y - 1} {z - 1} {x} {y + 3} {z + 2} obsidian",
                f"fill {x} {y} {z} {x} {y + 2} {z + 1} nether_portal[axis=z]"):
        _chat(f"execute in minecraft:overworld run {cmd}")
    ctx.mem.add_machine("nether_portal", (x, y - 1, z - 1), 1, "minecraft:overworld", ["portal"])
    ctx.mem.add_site("portal", (x, y, z), "minecraft:overworld", name="portal-overworld")

def _dragon_health():
    import re
    lines = _command("execute in minecraft:the_end run data get entity @e[type=minecraft:ender_dragon,limit=1] Health",
                     [])
    for line in lines:
        m = re.search(r"([\d.]+)f", line)
        if m:
            return float(m.group(1))
    return None

SPEEDRUN_END_KIT = ["clear @p", "give @p stone_sword", "give @p stone_pickaxe", "give @p white_bed 6",
                    "give @p cobblestone 64", "give @p cooked_beef 16", "give @p water_bucket"]

def _summon_perched_dragon(phase=6):
    """After setup: a dragon standing on the exit-portal pillar's real top (one summoned mid-air never perched)."""
    def before(ctx):
        from ..end import find_pillar_top
        top = find_pillar_top()
        if top is None:
            raise SetupInvalid("no exit-portal bedrock found near the island centre")
        # on the island floor east of the pillar (spreadplayers put the player on an obsidian tower)
        _chat(f"execute in minecraft:the_end run spreadplayers 12 0 0 3 under {top + 8} false @p")
        # never /kill the dragon (its death opens the exit portal); reuse a living one, summon only when none
        count = lambda: server_count(_command("execute in minecraft:the_end as @p at @s if entity "
                                              "@e[type=minecraft:ender_dragon,distance=..300]", []))
        if count() < 1:
            _chat(f"execute in minecraft:the_end run summon ender_dragon 0 {top} 0 {{DragonPhase:{phase}}}")
            time.sleep(2)
        sel = "@e[type=minecraft:ender_dragon,limit=1]"
        for cmd in (f"data modify entity {sel} Health set value 200f",
                    f"data modify entity {sel} DragonPhase set value {phase}",
                    f"tp {sel} 0 {top} 0"):
            _chat(f"execute in minecraft:the_end run {cmd}")
        time.sleep(1.5)
        # a fresh summon needs a moment to sync: check again before calling it missing
        for attempt in range(8):
            if count() >= 1:
                break
            if attempt % 3 == 2:
                _chat(f"execute in minecraft:the_end run summon ender_dragon 0 {top} 0 {{DragonPhase:{phase}}}")
            time.sleep(1.0)
        else:
            raise SetupInvalid("no living dragon after setup")
        # full health before the skill: a perched dragon left by the last row chews on the player here
        _chat("execute in minecraft:the_end run effect give @p minecraft:instant_health 2 10 true")
        _chat("execute in minecraft:the_end run effect clear @p minecraft:instant_health")
    return before

def _worn_perched_dragon(ctx, _perch=_summon_perched_dragon(6)):
    """The fight's last phase, built: the dragon on the pillar, crystals gone, 8 hp (WORN_DRAGON)."""
    _perch(ctx)
    _wear_dragon(ctx)

def locate_reply(feedback):
    """Pure: (x, z) from '/locate structure' feedback ('... is at [x, ~, z] (N blocks away)'), or None."""
    import re
    for f in feedback:
        for line in f.get("reply", []):
            m = re.search(r"at \[(-?\d+), [^,]+, (-?\d+)\]", line)
            if m and "locate" in f.get("cmd", ""):
                return int(m.group(1)), int(m.group(2))
    return None

def _stronghold_error():
    import math
    from ..memory import Memory
    real = locate_reply(LAST_FEEDBACK)
    sites = Memory(NOTES).sites("minecraft:overworld", kinds=["stronghold"])
    if not real or not sites:
        return 1e9
    return math.dist(real, (sites[0]["pos"][0], sites[0]["pos"][2]))

# milestones → the rows proving their skills; the review lists those not ready
MILESTONE_SCENARIOS = {
    "stone tools": ["craft_stone_tools", "slice_start_tools"], "station kit": ["slice_start_tools"],
    "food": ["hunt_food"], "iron pickaxe": ["iron_ingots"], "water bucket": ["fill_water_bucket"],
    "nether kit": ["slice_nether_kit"], "blaze rods": ["collect_blaze_rods"], "eyes of ender": ["craft_eyes"],
}

def readiness_lines(table=None):
    """Lines for the review: every scenario's verdict for the current code, then milestones not yet proven."""
    table = load_table() if table is None else table
    out, verdict = [], {}
    for name in SCENARIOS:
        st, med = status(table, name, code_for(name))
        verdict[name] = st
        budget = SCENARIOS[name]["budget"]
        out.append(f"  {name:22} {st:9}" + ("" if med is None else f" median {med}s (budget {budget}s)"))
    unproven = [g for g, names in MILESTONE_SCENARIOS.items() if any(verdict.get(n) != "scenario" for n in names)]
    if unproven:
        out.append(f"  milestones not proven on the bench: {', '.join(unproven)}")
    return out

def _trades(inv):
    """Items a piglin trade can give (anything but what the scenario handed out)."""
    given = ("minecraft:gold_ingot", "minecraft:golden_helmet", "minecraft:iron_sword", "minecraft:iron_helmet")
    return sum(s["count"] for s in inv.slots if s["id"] not in given)

# -- fights (the combat table's machinery): a cell of bench_combat's dimensions built, fought, recorded, judged ---
import random  # noqa: E402
from .. import estimate, paths  # noqa: E402,F401
from .core import SWEEP, _platform  # noqa: E402

def _siege_kit():
    """The armed baseline, plus what a long fight needs more of."""
    return (scene(WEAPON["iron"] + ARMOUR["iron"] + KIT["full"])
            + ["item replace entity @p armor.legs with iron_leggings",
               "item replace entity @p armor.feet with iron_boots",
               "give @p cooked_beef 16", "give @p cobblestone 128"])

def _scatter(seed):
    """A few blocks of relief on the floor: a step to stand on, a dip to drop into, something to put between us and it."""
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
    """The cells a pass visits: one dimension off the baseline at a time, or the product of `over`, each repeated."""
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
    """One seed per cell per pass lays out ground, enemies and mood, and goes into the row so it can be rebuilt exactly."""
    return cell.get("seed") if cell.get("seed") is not None else random.randrange(1 << 30)

def _build(cell):
    """A cell, realised: a sealed room, then each dimension's own commands."""
    seed = cell.setdefault("seed", _seed_of(cell))
    out = (_platform(reach=ARENA_REACH, walled=True) + _roof() + _scatter(seed) + _revive()
           # full health and food: health carried over makes `blood` two variables at once
           + ["clear @p", "effect clear @p", "effect give @p minecraft:instant_health 10 1 true",
              "effect give @p minecraft:saturation 1 10 true",
              "difficulty normal", "time set day"])
    for name in ("ground", "weapon", "armour", "kit", "blood"):
        out += scene(DIMS[name][cell[name]])
    out += FIGHT_BUCKET
    kind = ENEMY[cell["enemy"]]
    if kind is None:
        return out
    return out + _summon(((kind, COUNT[cell["count"]]),), spread=DISTANCE[cell["distance"]], seed=seed)

def _kinds_of(cell):
    return {ENEMY[cell["enemy"]]}

def _carry(hp_lost, meals, blocks):
    """The state the waves before left, in BLOOD's own form (magic damage: armour-proof)."""
    return ([f"damage @p {hp_lost} minecraft:magic"] if hp_lost else []) + \
        ([f"clear @p cooked_beef {meals}"] if meals else []) + ([f"clear @p cobblestone {blocks}"] if blocks else [])

SHAPE_COLUMNS = {"reshape", "wall_in"}
# wide enough for every answer (escape_spot walks up to 16; the first cell fell off a 9-block platform)
ARENA_REACH = 24

def _revive():
    """Put the player back on their feet before a cell is built."""
    from .. import api as _api
    try:
        if _api.get("/state").get("dead"):
            _api.post("/respawn")
            time.sleep(1.0)
    except Exception:
        pass
    return ["gamemode survival @p", "effect clear @p"]

def _columns_possible(cell):
    """The columns this cell paid for: what the kit gave, minus what the situation cannot use."""
    want = set(NEEDS.get(cell["kit"], ()))
    if cell["blood"] != "hurt":
        want.discard("eat")          # eating at full health is not an option anywhere
    return want

def _combat_intent(state):
    """What the threat model wants, before anything moves: every column, its price, and the state it priced from."""
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
    """The trace, on its own thread."""
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
    """Put the cell's enemies back."""
    if not cell:
        return
    kind = ENEMY.get(cell.get("enemy"))
    if not kind:
        return
    for command in _summon(((kind, COUNT[cell["count"]]),), spread=DISTANCE[cell["distance"]],
                           seed=cell.get("seed")):
        _chat(command)

def _combat_execute(seconds, until=None, cell=None):
    """Live in the cell with ONE layer driving, recording every look the threat layer took and a 5 Hz trace."""
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
        # two decision-makers on one body cannot be measured: said before the window
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
                # the planner keeps taking rounds (its refusals must reach the tape), each refused while standing down
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
    """Seconds of the window in which the threat layer could not see: it had no rows, or only stale ones."""
    if not looks:
        return round(float(seconds), 2)
    # quiet is an observation; blind is a tick that could not look
    blind = sum(1 for look in looks if look["outcome"] in ("stale", "unwired", "soft"))
    return round(float(seconds) * blind / len(looks), 2)

def _threat_kinds():
    from .. import threat
    return set(threat.MOBS)

BLIND_SHARE = 0.1      # a cell blind for more of its window than this measured nothing
# long enough to contain a fight (8 s caught a swing or two); noise averages across passes, by the jitter
CELL_SECONDS = 15.0

def _plain(state):
    """The priced state as JSON: the row has to carry what the prediction assumed, and a row is data."""
    if not state:
        return None
    ground = state.get("field")
    return dict(state, hazards=[list(h) for h in state.get("hazards", ())],
                field=None if ground is None else {"bucket": ground.bucket, "blocks": ground.blocks})

def _fought(kinds, seconds):
    """The shared record: price the cell, live in it, report the outcome and the clock's word on the pricing's numbers."""
    def record(cell):
        from ..world import Snapshot
        before = Snapshot()
        intent = _combat_intent(dict(before.state))
        answered, worst, took, trace = _combat_execute(seconds, cell=cell)
        after = Snapshot()
        near = _hostiles(radius=24, kinds=kinds(cell))
        # a window blind too long is not evidence
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
    """A row's cell, named by the dimensions it carries (listing keys would copy the dimension table)."""
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
    """Where the enemies appear."""
    out = []
    rng = random.Random(seed)
    for kind, n in mobs:
        turn = rng.random() * 2 * math.pi if seed is not None else 0.0
        for i in range(n):
            angle = turn + 2 * math.pi * i / max(1, n)
            # never closer than the dimension says; jitter only opens the range
            reach = spread * (rng.uniform(1.0, 1.4) if seed is not None else 1.0)
            dx, dz = round(reach * math.cos(angle)), round(reach * math.sin(angle))
            out.append(f"summon {kind} ~{dx} ~ ~{dz}")
    return out

def _siege_build(cell):
    """No reset between waves: the siege is cumulative."""
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
# every fight carries a water bucket: a knock off a ledge is part of fighting
FIGHT_BUCKET = ["give @p water_bucket"]
# the cell is built in setup, so the exposure starts at setup's end
ESCAPE_SECONDS = 25.0      # the row's limit is 30 s (the user's rule): the window is what is left of it
ESCAPE_WATCH = ESCAPE_SECONDS - 2.0     # setup's end → the run's first look: ~2 s of the window already spent
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
# name: (cell off ARMED, what must hold, why); a control runs before the cell compared to it
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

# == the generated sheet (points A–D): BASES × CONDITIONS, each row judged by the world (bag delta, blocks, where the body stands), never by the skill's return
import threading as _threading
BASE = {}                 # the bag, the /state and the time at the start of the run (`_start`)
FAILED_AS_EXPECTED = {}   # scenario → the failure message that matched its `fails` pattern
INTERRUPTS = {}           # scenario → interruptions the run absorbed (injected or not)
ACCEPTANCE_D = "accept_fresh_iron_pickaxe"

def _skill(name):
    """The registered runner of a skill (every skill module is imported by the brain)."""
    from .. import brain  # noqa: F401
    from ..skill import REGISTRY
    return REGISTRY[name].runner

def _inv_now():
    from ..world import Inventory
    return Inventory()

def _start(name):
    """`before` hook head: forget the last run's verdicts and remember the bag and body this run starts from."""
    def hook(ctx):
        from .. import api
        FAILED_AS_EXPECTED.pop(name, None)
        INTERRUPTS[name] = 0
        BASE.clear()
        BASE.update(name=name, inv=api.get("/inventory"), state=api.get("/state"), t=time.time())
    return hook

def _base_count(token):
    from ..world import Inventory
    return Inventory(BASE["inv"]).count(token) if BASE.get("inv") else 0

# -- checks: what the world must show afterwards
def _gain(token, n, at_most=None):
    """The bag holds at least `n` more `token` than at the start (and, with `at_most`, not more than that)."""
    def check(api, inv):
        got = inv.count(token) - _base_count(token)
        return got >= n and (at_most is None or got <= at_most)
    return check

def _same_bag_and_place(r=1.5):
    """Goal already met: nothing taken, nothing spent, the body did not wander off."""
    def check(api, inv):
        from ..world import Inventory
        before = sorted((s["id"], s["count"]) for s in Inventory(BASE["inv"]).slots)
        after = sorted((s["id"], s["count"]) for s in inv.slots)
        s0, s1 = BASE["state"], api.get("/state")
        return before == after and math.dist((s0["x"], s0["y"], s0["z"]), (s1["x"], s1["y"], s1["z"])) <= r
    return check

def _alive(min_hp=1.0):
    return lambda api, inv: not api.get("/state")["dead"] and api.get("/state")["health"] >= min_hp

def _at(pos, r):
    return lambda api, inv: _near(api, pos, r)

def _blocks(lo, hi, name, least, most=None):
    """`name` blocks (or any of a tuple of names) in the box: at least `least`, at most `most`."""
    names = (name,) if isinstance(name, str) else tuple(name)
    def check(api, inv):
        n = sum(_count_blocks(api, lo, hi, nm) for nm in names)
        return n >= least and (most is None or n <= most)
    return check

def _same_bag():
    """Nothing taken, nothing spent: the bag reads as it did at the start."""
    def check(api, inv):
        from ..world import Inventory
        before = sorted((s_["id"], s_["count"]) for s_ in Inventory(BASE["inv"]).slots)
        return before == sorted((s_["id"], s_["count"]) for s_ in inv.slots)
    return check

def _not(check):
    return lambda api, inv: not check(api, inv)

def _slot_has(item, *words):
    """A carried `item` whose slot mentions all of `words` (enchantments, potion contents), as the mod reports it."""
    def check(api, inv):
        return any(s_["id"] == item and all(w in json.dumps(s_) for w in words) for s_ in inv.slots)
    return check

def _mobs_near(kind, least, r=16):
    def check(api, inv):
        from ..world import entities
        return len(entities(r, [kind])) >= least
    return check

def _under_feet(*names):
    def check(api, inv):
        from ..world import Region
        s_ = api.get("/state")
        p = (s_["blockX"], s_["blockY"] - 1, s_["blockZ"])
        return Region(p, p).name(p) in names
    return check

def _room_to_work():
    def check(api, inv):
        from ..skillcore import free_spots_here
        return bool(free_spots_here())
    return check

def _dropped_nothing():
    def check(api, inv):
        from ..world import entities
        return not entities(8, ["minecraft:item"])
    return check

def _no_block_suffix(lo, hi, suffix):
    def check(api, inv):
        from ..world import Region
        return not any(n.endswith(suffix) for n in Region(lo, hi).blocks.values())
    return check

def fed_as_needed(food_before, carried_before, food_after, carried_after):
    """Pure: the eating filled the bar — every bite called for eaten, and the bar within the last bite of FULL_BAR."""
    from ..data import FULL_BAR, NUTRITION
    from ..skills import bite_plan
    plan = bite_plan(food_before, carried_before)
    eaten = sum(carried_before.get(k, 0) - carried_after.get(k, 0) for k in carried_before)
    if not plan:
        return eaten == 0
    return eaten == len(plan) and food_after >= FULL_BAR - (NUTRITION[plan[-1].split(":")[-1]] - 1)

def _food_up():
    """The row's eating filled the bar as needed (`fed_as_needed`), from where it stood when the eating began."""
    def check(api, inv):
        from ..knowledge import ALL_FOOD
        from ..world import Inventory
        inv = inv if inv is not None else Inventory()
        before = Inventory(BASE["inv"])
        return fed_as_needed(BASE.get("food_before", BASE["state"]["food"]), {f: before.count(f) for f in ALL_FOOD},
                             api.get("/state")["food"], {f: inv.count(f) for f in ALL_FOOD})
    return check

def _is_day():
    return lambda api, inv: int(api.get("/state")["timeOfDay"]) % 24000 < 12500

def _free_slots(n):
    return lambda api, inv: inv.free_slots() >= n

def _interrupted(least=1):
    return lambda api, inv: INTERRUPTS.get(BASE.get("name"), 0) >= least

def _failed_as_expected():
    return lambda api, inv: BASE.get("name") in FAILED_AS_EXPECTED

def _all(*checks):
    return lambda api, inv: all(c(api, inv) for c in checks)

def _named_all(named):
    """`_all` over (check, why) pairs that logs which part failed, once."""
    def check(api, inv):
        for c, why in named:
            if not c(api, inv):
                __import__("bonobo.api", fromlist=["log"]).log(f"check: False — {why} (first seen: {dict(FIRST)})")
                return False
        return True
    return check

# -- run wrappers: timing and expected failures
def _expect_failure(name, run, pattern):
    """An expected-failure row: the run must end with a failure naming the reason — not succeed, not fail otherwise."""
    def go(ctx):
        from .. import api
        try:
            out = run(ctx)
        except api.McError as e:
            if re.search(pattern, str(e), re.I):
                FAILED_AS_EXPECTED[name] = str(e)
                return True
            raise
        raise api.McError(f"expected to fail ({pattern}), reported success instead: {out!r}")
    return go

def _resume(name, run, resume, tries=4):
    """Run; when an interruption stops it, count it and resume by what is still missing (`resume`, plan-driven)."""
    def go(ctx):
        from .. import api
        fn = run
        try:
            for _ in range(tries):
                try:
                    return fn(ctx)
                except api.INTERRUPTIONS:
                    INTERRUPTS[name] = INTERRUPTS.get(name, 0) + 1
                    fn = resume
            raise api.McError(f"still interrupted after {tries} tries")
        finally:
            if api.INTERRUPT and str(api.INTERRUPT).startswith("bench:"):
                api.INTERRUPT = None       # an injected interrupt that landed after the end must not stop the next row
    return go

def _progress_of(base):
    """Pure: how this base's progress counts — ("bag", token), ("walk", target), or None."""
    token = (base.get("progress") or base.get("effect") or (None,))[0]
    if token:
        return "bag", token
    if base.get("target"):
        return "walk", base["target"]
    return None

def _on_progress(name, base, action, times=1):
    """`before` hook: `action()` the moment the run first makes progress."""
    how = _progress_of(base)
    def hook(ctx):
        from .. import api
        start = BASE["state"]
        here0 = (start["x"], start["y"], start["z"])
        def made(k):
            if how[0] == "bag":
                return _inv_now().count(how[1]) - _base_count(how[1]) >= k
            s_ = api.get("/state")
            whole = math.dist(here0, how[1])
            return whole - math.dist((s_["x"], s_["y"], s_["z"]), how[1]) >= whole * k / (times + 1)
        def watch():
            t0, k = time.time(), 1
            while k <= times and time.time() - t0 < 120 and BASE.get("name") == name:
                try:
                    if made(k):
                        action()
                        k += 1
                        continue
                except api.McError:
                    pass
                time.sleep(0.05)
        _threading.Thread(target=watch, daemon=True).start()
    return hook

def _inject_interrupt():
    __import__("bonobo.api", fromlist=["INTERRUPT"]).INTERRUPT = "bench: injected interrupt"

def _post_foreign_task():
    """Another commander posts a task straight to the mod (BodyContested for the skill)."""
    __import__("bonobo.api", fromlist=["api"]).api("POST", "/task?wait=0", {"type": "wait", "ticks": 40})

def _take_over(hold=3):
    """The player presses the toggle key (jar /control, as the key does), then hands back after `hold` s."""
    from .. import api
    api.api("POST", "/control", {"paused": True})
    _threading.Timer(hold, lambda: api.api("POST", "/control", {"paused": False})).start()

def _sand_on_head():
    from .. import api
    s_ = api.get("/state")
    x, y, z = s_["blockX"], s_["blockY"], s_["blockZ"]
    _chat(f"fill {x} {y + 1} {z} {x} {y + 3} {z} sand")

def gained_at_least(token, n):
    """Progress: the bag holds `n` more `token` than at the start."""
    return lambda: _inv_now().count(token) - _base_count(token) >= n

def placed_at_least(lo, hi, block, n):
    """Progress: `n` or more `block` stand in the box lo..hi (a build's parts in the world)."""
    return lambda: _count_blocks(None, lo, hi, block) >= n

def walked_at_least(m):
    """Progress: the body stands `m` or more blocks (sideways) from where the row began."""
    def done():
        s = __import__("bonobo.api", fromlist=["get"]).get("/state")
        b = BASE["state"]
        return math.hypot(s["x"] - b["x"], s["z"] - b["z"]) >= m
    return done

def _when(progress, act, limit_s=120):
    """`before` hook: `act()` the first moment `progress()` holds — the world changes by progress, never by the clock."""
    def hook(ctx):
        def watch():
            from .. import api
            t0 = time.time()
            while time.time() - t0 < limit_s:
                try:
                    if progress():
                        act()
                        return
                except api.McError:
                    pass
                time.sleep(0.05)
        _threading.Thread(target=watch, daemon=True).start()
    return hook

def _interrupt_when(when, n=None, message="bench: interrupt at the moment of success"):
    """`before` hook: interrupt the moment progress is first seen (a progress predicate, or a token with `n`)."""
    progress = when if callable(when) else gained_at_least(when, n)
    return _when(progress, lambda: setattr(__import__("bonobo.api", fromlist=["INTERRUPT"]), "INTERRUPT", message))

def _unless_done(check, run):
    """Resume only what is not done: an interruption that landed at the moment of success leaves nothing to redo."""
    def go(ctx):
        from .. import api
        return True if check(api, _inv_now()) else run(ctx)
    return go

def _after_l0(name, run, resume, tries=4):
    """Run; on an L0 interruption let the brain's rounds rescue the body, then resume by what is still missing."""
    def go(ctx):
        from .. import api, hazard
        fn = run
        for _ in range(tries):
            try:
                return fn(ctx)
            except api.INTERRUPTIONS:
                INTERRUPTS[name] = INTERRUPTS.get(name, 0) + 1
                t0 = time.time()
                while time.time() - t0 < 20 and hazard.due(api.get("/state")) is not None:
                    core.BRAIN.round()
                fn = resume
        raise api.McError(f"still interrupted after {tries} tries")
    return go

def _sprint_after(delay, ticks):
    """`before` hook: `delay` s in, sprint the game `ticks` ahead so waits pass in a second."""
    def hook(ctx):
        def fire():
            time.sleep(delay)
            _chat(f"tick sprint {ticks}")
        _threading.Thread(target=fire, daemon=True).start()
    return hook

def _hooks(*hooks):
    hooks = [h for h in hooks if h is not None]
    return lambda ctx: [h(ctx) for h in hooks] and None

def _achieve_needs(needs, rounds=12):
    """Resume by amount: plan the needs from the bag as it is now and run the plan (the brain's own path)."""
    def run(ctx):
        want = [(t, n + _base_count(t)) if t != "tool" else (t, n, *rest) for t, n, *rest in needs]
        done = lambda: all(_inv_now().count(t) >= n for t, n, *_ in want if t != "tool")  # noqa: E731
        return _achieve(ctx, want, done, rounds=rounds)
    return run

def _plan_is_empty(needs):
    """Goal already met: the planner, asked from the real bag, plans nothing — and nothing is run."""
    def run(ctx):
        from .. import api, decompose, goals
        from ..cost import Cost
        from ..world import Snapshot
        snap = Snapshot()
        steps = decompose.decompose(snap.inv, goals.have(*needs), Cost(snap, ctx.mem))
        if steps:
            raise api.McError(f"goal already met, but planned {' → '.join(map(str, steps))}")
        return True
    return run

def _brain_rounds(seconds, until):
    """The whole cerebellum for up to `seconds` (L0 and upkeep included), until `until()`."""
    def run(ctx):
        t0 = time.time()
        while time.time() - t0 < seconds and not until():
            core.BRAIN.round()
        return until()
    return run

def _enclosed():
    """Walled in, feet and head, and covered: what a burrow, a pod or a dug-in hole must leave."""
    from .. import skills
    return skills.enclosed()

def _breathing(least=280):
    """Out of the water's grip: the air bar back near full and the head out of the water."""
    def check(api, inv):
        from .. import skills
        s = api.get("/state")
        return s["air"] >= least and not skills.head_underwater(s)
    return check

def _buried_first(ctx, polls=5):
    """`before` hook: the head is inside the sand the row dropped — else the body was pushed clear, the check (head
    clear) holds before any round and the row passes without a rescue: SetupInvalid."""
    from .. import skills
    for _ in range(polls):
        if skills.head_buried():
            return
        time.sleep(0.2)
    raise SetupInvalid("the sand did not bury the head: head clear before any round, nothing to rescue")

def _head_clear():
    from .. import skills
    return not skills.head_buried()

# -- arena pieces (relative to ORIGIN)
def _floor(block="stone", half=8, depth=3):
    return [f"fill {_c(at(-half, -depth, -half))} {_c(at(half, -1, half))} {block}"]

def _tp(dx=0, dy=0, dz=0):
    return f"tp @p {_c(at(dx + 0.5, dy, dz + 0.5))}"

def _tree(x, z, wood="oak", height=5):
    """One tree built block by block: the same shape every run (a generated tree's log count decided rows by chance)."""
    return [f"fill {_c(at(x - 2, height - 2, z - 2))} {_c(at(x + 2, height - 1, z + 2))} {wood}_leaves[persistent=true]",
            f"fill {_c(at(x - 1, height, z - 1))} {_c(at(x + 1, height, z + 1))} {wood}_leaves[persistent=true]",
            f"fill {_c(at(x, 0, z))} {_c(at(x, height - 1, z))} {wood}_log"]

CHOP_TREE = (2, 0)       # the chop base's one oak (x, z): rows that must leave it standing read it here

def _grove(*spots, wood="oak"):
    return [f"fill {_c(at(-8, -1, -8))} {_c(at(8, -1, 8))} grass_block"] + [c for x, z in spots for c in _tree(x, z, wood)]

def _chest(pos, *items):
    return [f"setblock {_c(pos)} chest"] + \
        [f"item replace block {_c(pos)} container.{i} with {item}" for i, item in enumerate(items)]

def _pen(mob, n, half=7):
    walls = [f"fill {_c(at(a, 0, b))} {_c(at(c, 0, d))} oak_fence" for a, b, c, d in
             ((-half, -half, half, -half), (-half, half, half, half), (-half, -half + 1, -half, half - 1),
              (half, -half + 1, half, half - 1))]
    spots = [(3, 2), (-3, 2), (2, -4), (-4, -3), (4, -1), (-1, 4)][:n]
    return walls + [f"summon {mob} {_c(at(x, 0, z))}" for x, z in spots]

def _tank(x0, x1, z0, z1, top, water_top=None, floor_y=-4, wall="glass", open_side=None):
    """A glass tank inside the box: floor at `floor_y`, four walls up to `top`, open above, water up to `water_top`."""
    lo, hi = (x0 - 1, floor_y, z0 - 1), (x1 + 1, top, z1 + 1)
    out = [f"fill {_c(at(lo[0], floor_y, lo[2]))} {_c(at(hi[0], floor_y, hi[2]))} stone"]
    for side, a, b in (("north", (lo[0], lo[2]), (hi[0], lo[2])), ("south", (lo[0], hi[2]), (hi[0], hi[2])),
                       ("west", (lo[0], lo[2]), (lo[0], hi[2])), ("east", (hi[0], lo[2]), (hi[0], hi[2]))):
        height = water_top if side == open_side and water_top is not None else top
        out.append(f"fill {_c(at(a[0], floor_y + 1, a[1]))} {_c(at(b[0], height, b[1]))} {wall}")
    if water_top is not None:
        out.append(f"fill {_c(at(x0, floor_y + 1, z0))} {_c(at(x1, water_top, z1))} water")
    return out

def eat_target_s(food, carried):
    """Pure: an eat row's speed target: per bite × the bites the bar's gap takes, with slack."""
    from ..skills import bites_to_full
    _item, bites = bites_to_full(food, carried)
    return TARGET_S["eat"] * bites * TARGET_SLACK if bites else None

def _eat_target(ctx):
    """`before` hook (after the drain): the eat row's target from the bar and the bag it starts with."""
    from ..world import Inventory
    from ..knowledge import ALL_FOOD
    inv = Inventory()
    food = __import__("bonobo.api", fromlist=["get"]).get("/state")["food"]
    BASE["target_s"] = eat_target_s(food, {f: inv.count(f) for f in ALL_FOOD})

def _timed(run):
    """The run, its own seconds kept in BASE["run_s"] (the runner's `judge` reads them against row["target_s"])."""
    def go(ctx):
        t0 = time.time()
        try:
            return run(ctx)
        finally:
            BASE["run_s"] = time.time() - t0
    return go

def _skill_within(name, seconds):
    """The skill `name` itself (skill.LAST_S, no planning or walk) finished inside `seconds`."""
    return lambda api, inv: __import__("bonobo.skill", fromlist=["LAST_S"]).LAST_S.get(name, 1e9) <= seconds

def _forget_skill_time(name):
    return lambda ctx: __import__("bonobo.skill", fromlist=["LAST_S"]).LAST_S.pop(name, None)

# -- the full bag: filled after the base's kit, leaving exactly `free` slots
def _fill_bag(free, item="dirt", stack=64):
    """`before` hook: fill the bag with `item` until `free` slots are left (the kit the setup gave stays)."""
    def hook(ctx):
        from ..world import Inventory
        room = Inventory().free_slots() - free
        if room > 0:
            _chat(f"give @p {item} {room * stack}")
            time.sleep(0.5)
    return hook

def _kept(token):
    """None of `token` left the bag (what the start held is still there)."""
    return lambda api, inv: inv.count(token) >= _base_count(token)

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

def _bench_machine(ctx, origin):
    """The arena's auto smelter as memory knows a built one (the dict `upkeep.ready_machine` hands the skill)."""
    name = ctx.mem.add_machine("auto_smelter", origin, 0, "minecraft:overworld", ["smelting"])
    return next(m for m in ctx.mem.machines("minecraft:overworld") if m["name"] == name)

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

# -- CT3: fights on a walled platform, the whole agent running; judged by the world and the decision rhythm (no bid gap over 1.5 × FIGHT_POLL_S)
FIGHT_LOG = {"bids": []}

def _record_bids(ctx):
    """`before` hook: time every bid the threat layer makes during this row (the fight's decision clock)."""
    from .. import fight_loop
    FIGHT_LOG["bids"] = []
    real = FIGHT_LOG.setdefault("real_bid", fight_loop.bid)
    def bid(*a, **k):
        FIGHT_LOG["bids"].append(time.time())
        return real(*a, **k)
    fight_loop.bid = bid

def _fight_until(kinds, seconds, clear=True):
    """Brain rounds (which yield while the fight holds the body) until the line-up is gone, or `seconds`."""
    def run(ctx):
        from .. import fight_loop
        t0 = time.time()
        try:
            while time.time() - t0 < seconds:
                if clear and not _hostiles(24, set(kinds)):
                    return True
                core.BRAIN.round()
            return not clear or not _hostiles(24, set(kinds))
        finally:
            fight_loop.bid = FIGHT_LOG.get("real_bid", fight_loop.bid)
    return run

def _decision_gaps_ok(factor=1.5):
    def check(api, inv):
        from .. import fight_loop
        t = FIGHT_LOG["bids"]
        gaps = [b - a for a, b in zip(t, t[1:])]
        return bool(t) and max(gaps, default=0.0) <= fight_loop.FIGHT_POLL_S * factor
    return check

def _gone(kinds):
    return lambda api, inv: not _hostiles(24, set(kinds))

def _hp_kept(least):
    return lambda api, inv: api.get("/state")["health"] >= least and not api.get("/state")["dead"]

# glass walls (visible), a stone roof: undead under the sky burned before the row began
_ARENA = [f"fill {_c(at(-9, -2, -9))} {_c(at(9, -1, 9))} stone", f"fill {_c(at(-9, 0, -9))} {_c(at(9, 4, 9))} glass hollow",
          f"fill {_c(at(-9, 4, -9))} {_c(at(9, 4, 9))} stone",
          f"fill {_c(at(-8, 0, -8))} {_c(at(8, 3, 8))} air", f"fill {_c(at(-9, -1, -9))} {_c(at(9, -1, 9))} stone", _tp(),
          "give @p iron_sword", "give @p stone_pickaxe",       # a pickaxe: upkeep's "no pickaxe" row stays quiet
          "item replace entity @p armor.chest with iron_chestplate",
          "item replace entity @p armor.head with iron_helmet", "give @p cooked_beef 16", "give @p cobblestone 64",
          "item replace entity @p weapon.offhand with shield"]
# (name, mob, count, tier, seconds, health kept at least, cleared?) — cleared False: a neutral mob left alone
RESOLVE_GAP, RESOLVE_HOLD_S, RESOLVE_HP_LOSS = 6.0, 5.0, 4.0

def _threat_resolved(kinds, gap=RESOLVE_GAP, hold_s=RESOLVE_HOLD_S, hp_loss=RESOLVE_HP_LOSS):
    """The threat is over: every `kinds` dead or `gap` off and not closing for `hold_s`, health within `hp_loss`."""
    def check(api, inv):
        from ..world import feet
        start_hp = BASE["state"]["health"]
        if api.get("/state")["health"] < start_hp - hp_loss:
            return False
        def gaps():
            here = feet()
            return [math.dist(here, (e["x"], e["y"], e["z"])) for e in _hostiles(32, set(kinds))]
        first = gaps()
        if not first:
            return True
        time.sleep(hold_s)
        last = gaps()
        return (not last or (min(first) >= gap and min(last) >= gap and min(last) >= min(first) - 1.0)) and \
            api.get("/state")["health"] >= start_hp - hp_loss
    return check

def _one_crystal(ctx):
    rows = __import__("bonobo.world", fromlist=["entities"]).entities(16, ["minecraft:end_crystal"])
    if not rows:
        raise SetupInvalid("no end crystal after setup")
    return rows[0]

def _crystals_left(api, inv):
    return __import__("bonobo.world", fromlist=["entities"]).entities(16, ["minecraft:end_crystal"])

# tidying in the Nether with lava on one side: junk is thrown the other way
NETHER_LAVA = [f"fill {_c(at(1, 0, -1))} {_c(at(4, 0, 1))} lava"]

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

FARM_KIT = ["give @p diamond_hoe", "give @p wheat_seeds 8", "give @p water_bucket"]
FARM_TICK_SPEED = 1000      # random ticks per chunk section: a sown crop ripens within seconds (the bench keeps 0)
RIPE_PLOT = [f"fill {_c(at(4, -1, -1))} {_c(at(6, -1, 1))} farmland", f"fill {_c(at(4, 0, -1))} {_c(at(6, 0, 1))} wheat[age=7]"]

def _growing(run):
    """The run with fast crop ticks, reset to 0 however it ends: the plan's own plot ripens within the row."""
    def go(ctx):
        _checked(f"execute in minecraft:overworld run gamerule random_tick_speed {FARM_TICK_SPEED}", [])
        try:
            return run(ctx)
        finally:
            _checked("execute in minecraft:overworld run gamerule random_tick_speed 0", [])
    return go

# -- tier "brain": the whole brain on a private queue, the world set to the deciding moment, judged by the world and its log
BRAIN_LOG = {"replans": 0}
FIRST = {}      # token → the run second it first showed in the bag (a watcher thread, `_first_times`)

def furnace_slots(reply):
    """Pure: {slot: (item id, count)} from the game's `data get block <pos> Items` answer."""
    import re
    text = " ".join(reply)
    out = {}
    for entry in re.findall(r"\{([^{}]*)\}", text):
        slot = re.search(r"Slot:\s*(\d+)b", entry)
        item = re.search(r'id:\s*"([^"]+)"', entry)
        count = re.search(r"count:\s*(\d+)", entry)
        if slot and item:
            out[int(slot.group(1))] = (item.group(1), int(count.group(1)) if count else 1)
    return out

def _furnace_holds(items, radius=16):
    """A furnace near holds one of `items` in input or output, read from the world."""
    from ..world import find
    for h in find(["furnace"], radius=radius, limit=8):
        slots = furnace_slots(_command(f"data get block {h['x']} {h['y']} {h['z']} Items", []))
        if any(slots.get(s, ("", 0))[0] in items for s in (0, 2)):
            return True
    return False

FIRST_WATCH = {"gen": 0}   # the row whose watcher may write FIRST: a new row's hook retires the last row's watcher

def first_step(gen, t0, inv, base_count, furnace_beef, now):
    """One look of a row's watcher: stamp each token first above the row's start, on this row's clock. False (and
    nothing written) once another row's hook has started its own watcher — a stale watcher's clock is not this row's."""
    if gen != FIRST_WATCH["gen"]:
        return False
    for s_ in inv.slots:
        for tok in (s_["id"], s_["id"].rsplit("_", 1)[-1]):     # "minecraft:white_bed" → also "bed"
            if tok not in FIRST and inv.count(tok) > base_count(tok):
                FIRST[tok] = now - t0
    if "furnace_beef" not in FIRST and furnace_beef():
        FIRST["furnace_beef"] = now - t0
    return True

def _first_times(ctx):
    """`before` hook: note when each token first rises above the row's start: the brain's decision order, read from the world."""
    FIRST_WATCH["gen"] += 1
    gen = FIRST_WATCH["gen"]
    FIRST.clear()
    t0 = time.time()

    def furnace_beef():
        try:
            return _furnace_holds(("minecraft:beef", "minecraft:cooked_beef"))    # beef in a furnace, read from the world
        except Exception:
            return False

    def watch():
        from ..world import Inventory
        while time.time() - t0 < 70:
            try:
                inv = Inventory()
            except Exception:
                time.sleep(0.5)
                continue
            if not first_step(gen, t0, inv, _base_count, furnace_beef, time.time()):
                return
            time.sleep(0.5)
    _threading.Thread(target=watch, daemon=True).start()

def _before_in_bag(first, then, or_never=False):
    """`first` appeared in the bag before `then` did (with `or_never`: or `then` never did)."""
    def check(api, inv):
        a, b = FIRST.get(first), FIRST.get(then)
        if b is None:
            return or_never and a is not None
        return a is not None and a < b
    return check

def _count_replans(ctx):
    """`before` hook: count the brain's plans for the row (brain.replan), the repair measure."""
    from .. import brain
    BRAIN_LOG["replans"] = 0
    real = BRAIN_LOG.setdefault("real_replan", brain.replan)
    def replan(*a, **k):
        BRAIN_LOG["replans"] += 1
        return real(*a, **k)
    brain.replan = replan

def _replans_at_most(n):
    def check(api, inv):
        from .. import brain
        brain.replan = BRAIN_LOG.get("real_replan", brain.replan)
        return 1 <= BRAIN_LOG["replans"] <= n
    return check

def _goal(template, **kw):
    return __import__("bonobo.goals", fromlist=["goals"]).make(template, **kw)

def _have(*needs):
    return __import__("bonobo.goals", fromlist=["goals"]).have(*needs)

_count = gained_at_least          # a queue's done: the same progress

def _remove_table_when_placed(ctx):
    """`before` hook: remove the plan's crafting table the moment it stands (the plan must repair one step, not restart)."""
    def watch():
        from ..world import find
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                hit = find(["crafting_table"], radius=6, limit=1)
            except Exception:
                hit = []
            if hit:
                h = hit[0]
                _chat(f"setblock {h['x']} {h['y']} {h['z']} air")
                return
            time.sleep(0.2)
    _threading.Thread(target=watch, daemon=True).start()

def _not_banned(pos):
    return lambda api, inv: core.BRAIN.blacklist.get(tuple(pos), 0) <= time.time()

def _clear_bans(ctx):
    core.BRAIN.blacklist.clear()

def _seen(kind, pos):
    def before(ctx):
        core.BRAIN.mem.note_seen(kind, pos, "minecraft:overworld")
    return before

def _not_remembered(kind):
    return lambda api, inv: core.BRAIN.mem.seen(kind, "minecraft:overworld") == []

def _forget_all(kind):
    def before(ctx):
        for r in core.BRAIN.mem.seen(kind, "minecraft:overworld"):
            core.BRAIN.mem.forget_seen(kind, r["pos"], "minecraft:overworld")
    return before

_ARENA_B = [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} grass_block", "clear @p"]
IRON_ORE_FREE, IRON_ORE_CAGED = at(4, 0, 0), at(-4, 0, 0)
# -- the brain's decisions as a grid: one world holding every reachable resource, the moment set by dimensions; ore sealed nearby so three goals fit 30 s
DIAMOND_UP, DIAMOND_DOWN = at(2, 0, -2), at(4, -9, 0)
POCKET = at(0, -9, 0)
LOW_FOOD, LOW_FOOD_MAX_S = 10, 20     # food drained to ~10 before the run (a `before` hook: harness, not budget)
DRAIN_POLL_S = 0.05
DRAIN_FAST, DRAIN_SLOW = 255, 30     # hunger amplifiers: ~6 points a second; ~0.8 (≤ 1 point between two polls)
DRAIN_SLOW_FROM = 4                  # the last points above the stop are taken slowly

def drain_step(food, saturation, level):
    """Pure: the drain's next move — "fast" with saturation or a high bar, "slow" for the last points, "stop" at `level` + 1."""
    if food <= level + 1:
        return "stop"
    if saturation >= 2 or food > level + 1 + DRAIN_SLOW_FROM:
        return "fast"
    return "slow"

def _drain_to(level, max_s=LOW_FOOD_MAX_S, window=None):
    """`before` hook: drain hunger to `level` + 1 (drain_step), read from /state every 50 ms."""
    def hook(ctx):
        from .. import api
        t0, now_amp = time.time(), None
        while time.time() - t0 < max_s:
            s = api.get("/state")
            step = drain_step(s.get("food", 20), s.get("saturation", 0), level)
            if step == "stop":
                break
            amp = DRAIN_FAST if step == "fast" else DRAIN_SLOW
            if amp != now_amp:
                # a weaker effect does not replace a stronger one: clear, then give
                _chat("effect clear @p minecraft:hunger")
                _chat(f"effect give @p minecraft:hunger 30 {amp} true")
                now_amp = amp
            time.sleep(DRAIN_POLL_S)
        _chat("effect clear @p minecraft:hunger")
        time.sleep(1.0)                   # what exhaustion was left takes its last point, if any
        food = api.get("/state").get("food", 20)
        BASE["food_drained"] = food
        from ..reflexes import EAT_BELOW, STARVE
        lo, hi = window or (STARVE, EAT_BELOW)
        if not lo < food < hi:
            raise SetupInvalid(f"food {food} after the drain: wanted between {lo} and {hi}")
    return hook

THROW_START = (6, 0, 3)     # east of the grove's oak (3, 3), clear of the stone at x 5..7, z -1..1; +x: 7, 8, the edge
BRAIN_DIMS = {
    # "tight": dusk inside the bed's lead (needs.due_now), so the bed comes first
    "dusk": {"plenty": ["time set 1000"], "tight": ["time set 11930"], "night": ["time set 18000"]},
    # the effect only marks the row hungry for the body reset; the drain proper is the hook's
    "food": {"full": [], "low": ["effect give @p minecraft:hunger 1 0 true"]},
    # one_use: a crafting table stands by the start, so the table's place-and-take-back is not measured
    "tool": {"fresh": ["give @p iron_pickaxe"],
             "one_use": ["give @p iron_pickaxe[damage=249]", f"setblock {_c(at(-2, 0, -1))} crafting_table"]},
    "head": {"surface": [_tp()], "underground": [_tp(0.5, -9, 0.5)]},
    "seen": {"none": [], "noted": []},                  # a memory note, set by the `before` hook
    # filled by the hook; junk_full starts where the only open side is +x over the edge, so junk is thrown away from the tree
    "bag": {"room": [], "junk_full": [_tp(*THROW_START)], "valuables_full": []},
}
BRAIN_BASE = {"dusk": "plenty", "food": "full", "tool": "fresh", "head": "surface", "seen": "none", "bag": "room"}
BAG_FILL = {"room": None, "junk_full": (0, "dirt"), "valuables_full": (0, "diamond")}
KIT_COBBLE = 16         # the brain rows' kit: a goal of cobblestone must ask for more than this, or it is met at once
BRAIN_WORLD = (_ARENA_B + [f"fill {_c(at(-8, -12, -8))} {_c(at(8, -3, 8))} stone"] + _grove((3, 3))
               + [f"fill {_c(POCKET)} {_c(at(0, -8, 0))} air",
                  f"fill {_c(at(1, -11, 1))} {_c(at(2, -10, 2))} iron_ore",
                  f"fill {_c(at(5, 0, -1))} {_c(at(7, 2, 1))} stone", f"fill {_c(at(1, 0, -3))} {_c(at(3, 2, -1))} stone",
                  f"setblock {_c(DIAMOND_UP)} diamond_ore",
                  f"setblock {_c(DIAMOND_DOWN)} diamond_ore", f"setblock {_c(at(-2, 0, 0))} furnace",
                  "give @p white_wool 3", "give @p oak_planks 8", "give @p crafting_table", "give @p stick 4",
                  "give @p iron_ingot 3", "give @p beef 2", "give @p coal 2", f"give @p cobblestone {KIT_COBBLE}",
                  "give @p diamond_axe"])       # the best axe: logs are not the tool test here (kit rule)

def _diamond_of(cell):
    return DIAMOND_DOWN if cell["head"] == "underground" else DIAMOND_UP

# family → (grid, queue, what each cell must show): checks read the world and bag order, never log text
def _bed_then_log(cell):
    if cell["dusk"] == "plenty" and cell["food"] == "full":
        return (_all(_before_in_bag("log", "bed", or_never=True), _gain("log", 2),
                     lambda api, inv: inv.count("bed") == 0),
                "a day ahead: the task first, no bed made (must not)")
    if cell["food"] == "low":
        # food first, from the world: beef in a furnace or cooked beef before any log, the bar no lower at the end
        food_first = lambda api, inv: (_before_in_bag("furnace_beef", "log")(api, inv)             # noqa: E731
                                       or _before_in_bag("minecraft:cooked_beef", "log")(api, inv))
        kept = lambda api, inv: api.get("/state")["food"] >= BASE.get("food_drained", 0)        # noqa: E731
        return _all(food_first, kept), "hungry: food before the task (cooking it counts)"
    return _before_in_bag("bed", "log"), "dusk or night on the surface, no bed: the night first"

def _tool_rule(cell):
    if cell["tool"] == "one_use":
        return (_all(lambda api, inv: inv.count("minecraft:iron_pickaxe") >= 1,
                     lambda api, inv: inv.count("minecraft:stone_pickaxe") + inv.count("minecraft:wooden_pickaxe") == 0,
                     _gain("minecraft:cobblestone", 3)), "broken: the best tier this bag crafts (iron)")
    return (_all(lambda api, inv: inv.count("minecraft:iron_ingot") == 3, _gain("minecraft:cobblestone", 3)),
            "fresh: nothing crafted, the ingots kept (must not craft)")

def _night_rule(cell):
    below = lambda api, inv: api.get("/state")["blockY"] < at(0, -3, 0)[1]      # noqa: E731
    if cell["head"] == "underground" and cell["dusk"] == "night":
        return _all(_gain("minecraft:raw_iron", 1), below), "night underground: work there (ore), no climb"
    if cell["head"] == "underground" and cell["dusk"] == "tight":
        return below, "dusk underground: already under cover, no climb to the surface (boundary)"
    if cell["dusk"] != "plenty":
        return _before_in_bag("bed", "minecraft:raw_iron", or_never=True), "dusk or night on the surface: a bed first"
    return (lambda api, inv: int(api.get("/state")["timeOfDay"]) % 24000 < 13000 and inv.count("bed") == 0,
            "daylight: no bed made, no sleep (must not)")

def _bag_rule(cell):
    if cell["bag"] == "valuables_full":
        return _kept("minecraft:diamond"), "a bag of diamonds: not one thrown to make room (must not)"
    if cell["bag"] == "junk_full":
        return (_all(_gain("log", 2), lambda api, inv: inv.count("minecraft:dirt") < _base_count("minecraft:dirt")),
                "a bag past BAG_FULL, junk: junk thrown, then the task done")
    return _gain("log", 2), "room for it: the task done as usual"

FINDS = {"diamond": 0}

def is_diamond_scan(path):
    """Pure: a /find that looks for diamond ore — not the estimates' one look per round (world.nearest's perBlock=1
    over every source block, diamond among them), which sees what is near and searches for nothing."""
    return path.startswith("/find") and "diamond" in path and "perBlock=1" not in path

def _count_finds(ctx):
    """`before` hook: count /find scans for diamond ore during the row, at api.get."""
    from .. import api
    FINDS["diamond"] = 0
    real = FINDS.setdefault("real", api.get)
    def get(path, *a, **k):
        if is_diamond_scan(path):
            FINDS["diamond"] += 1
        return real(path, *a, **k)
    api.get = get

def _no_scan():
    def check(api_, inv):
        from .. import api
        api.get = FINDS.get("real", api.get)
        return FINDS["diamond"] == 0
    return check

def _seen_rule(cell):
    # seeing through stone is allowed: a noted ore is walked to straight, an unnoted one found by scanning
    if cell["seen"] == "noted":
        return (_all(_gain("minecraft:diamond", 1), _not_remembered("diamond_ore"), _no_scan()),
                "noted: straight there without a scan (must not scan), the note retired")
    return _gain("minecraft:diamond", 1), "not noted: found anyway, by scanning"

# one value off the base at a time: which combination wins is tested offline; a row confirms the decision is carried out
BRAIN_FAMILIES = {
    "night_first": (list(_cells(BRAIN_BASE, dims=("dusk", "food"), table=BRAIN_DIMS)), [_have(("log", 2))], _bed_then_log),
    "tool_tier": (list(_cells(BRAIN_BASE, dims=("tool", "head"), table=BRAIN_DIMS)),
                  # 3 more than the kit carries, or the kit alone meets it
                  [_have(("minecraft:cobblestone", KIT_COBBLE + 3))], _tool_rule),
    "night_under": (list(_cells(BRAIN_BASE, dims=("dusk", "head"), table=BRAIN_DIMS)), [], _night_rule),
    "tidy_then_task": (list(_cells(BRAIN_BASE, dims=("bag",), table=BRAIN_DIMS)), [_have(("log", 2))], _bag_rule),
    "seen_store": (list(_cells(BRAIN_BASE, dims=("seen", "head"), table=BRAIN_DIMS)),
                   [_have(("minecraft:diamond", 1))], _seen_rule),
}

def _cell_name(family, cell):
    moved = [cell[d] for d in BRAIN_DIMS if cell[d] != BRAIN_BASE[d]]
    return f"{family}__" + ("_".join(moved) or "base")

def _cell_before(cell):
    hooks = [_clear_bans, _forget_all("diamond_ore"), _first_times]
    if cell["seen"] == "noted":
        hooks.append(_seen("diamond_ore", _diamond_of(cell)))
    hooks.append(_count_finds)
    if cell["food"] == "low":
        hooks.append(_drain_to(LOW_FOOD))       # setup's hunger drains the bar; cleared at LOW_FOOD
    return hooks

def _cell_setup_hooks(cell):
    """`before` hooks that make the row's world, run before `_start` reads the base."""
    return [_fill_bag(*BAG_FILL[cell["bag"]])] if BAG_FILL[cell["bag"]] else []

def _grid_cells():
    """Cells of every family, one row per distinct cell: a shared cell checks every family's expectation."""
    cells = {}
    for fam, (grid, queue, rule) in BRAIN_FAMILIES.items():
        for cell in grid:
            key = tuple(cell[d] for d in BRAIN_DIMS)
            entry = cells.setdefault(key, {"cell": cell, "families": [], "queue": [], "rules": []})
            entry["families"].append(fam)
            entry["queue"] += [g for g in queue if g not in entry["queue"]]
            entry["rules"].append(rule)
    return cells

def grid_name(families, cell):
    """The row of a cell: its family's name when only one family has it, else "brain"."""
    return _cell_name(families[0] if len(families) == 1 else "brain", cell)

# -- every upkeep line through the whole brain: the moment built, the answer shown in the world; (line, setup, hooks, done, check)
def _job_ready_at(pos, item, n):
    """`before` hook: memory holds a finished background furnace job at `pos` (its output already in the furnace)."""
    def hook(ctx):
        ctx.mem.add_job("furnace", pos, "minecraft:overworld", item, n, time.time() - 1, False)
    return hook

def _is_day_now():
    return _is_day()(__import__("bonobo.api", fromlist=["get"]), None)

def _blocked_toward(pos):
    """`before` hook: upkeep's memory of a walk that failed here, toward `pos` (what `Upkeep.failed` writes)."""
    def hook(ctx):
        from .. import retry
        from ..world import Snapshot
        snap = Snapshot()
        core.BRAIN.reflexes.blocked = {"t": time.time(), "place": retry.place_signature(snap.feet, snap.night),
                                    "pos": pos}
    return hook

def _stuck_for(seconds):
    """`before` hook: upkeep's history says we stood here, bag unchanged, for `seconds`."""
    def hook(ctx):
        from ..needs import bag_signature
        from ..world import Snapshot
        snap = Snapshot()
        core.BRAIN.reflexes.history = [(time.time() - seconds, snap.feet, bag_signature(snap.inv))]
    return hook

def _machine_due(origin, n):
    """`before` hook: memory holds an auto smelter at `origin` with an order of `n` ingots already due."""
    def hook(ctx):
        m = _bench_machine(ctx, origin)
        ctx.mem.add_pending(m["name"], "minecraft:iron_ingot", n, time.time() - 1)
    return hook

_st = lambda api: api.get("/state")     # noqa: E731

def _regen_fed(api, inv):
    """eat_to_regen's eating: bread went down and the bar reached 18 (regen's threshold) or more."""
    from ..world import Inventory
    inv = inv if inv is not None else Inventory()
    return inv.count("minecraft:bread") < 4 and api.get("/state")["food"] >= 18

# the night's shelter by what the bag allows; a bed makes none of them (must not)
_NIGHT_FLOOR = [f"fill {_c(at(-8, -6, -8))} {_c(at(8, -1, 8))} stone", _tp(), "time set 18000"]
# no pickaxe in a fight: the "no pickaxe" row waits until it ends; a dirt patch on this ground is dug into by hand, one across a drop never
DIRT_PATCH = (at(7, -3, -1), at(8, -1, 1))

def _in_the_patch_underground(api, inv):
    s = api.get("/state")
    (x0, _y0, z0), (x1, _y1, z1) = DIRT_PATCH
    return (x0 <= s["blockX"] <= x1 and z0 <= s["blockZ"] <= z1 and s["blockY"] <= at(0, 0, 0)[1] - 2
            and _enclosed())

# eating on the move by the jar's autoeat, still walking while chewing; control: mining is not interrupted to eat
WALK = {}
BITE_S = 1.6        # one bite (32 ticks): the window before the bar rises in which the body must keep moving

def ate_on_the_way(frames):
    """Pure over trace frames: food rose during the walk, no frame ran "eat", and x grew through the bite (autoeat while walking)."""
    fed = [f for f in frames if f.get("food") is not None]
    if not fed or any((f.get("task") or {}).get("type") == "eat" for f in frames):
        return False
    rise = next((f for f in fed if f["food"] > fed[0]["food"]), None)
    if rise is None:
        return False
    window = [f for f in frames if rise["t"] - BITE_S <= f["t"] <= rise["t"] and f.get("x") is not None]
    if len(window) < 2:
        return False
    # x at each whole second of the window, and at its end: every step forward.
    at_s = lambda sec: min(window, key=lambda f: abs(f["t"] - (window[0]["t"] + sec)))["x"]   # noqa: E731
    xs = [at_s(sec) for sec in range(int(window[-1]["t"] - window[0]["t"]) + 1)] + [window[-1]["x"]]
    xs = [x for k, x in enumerate(xs) if k == 0 or x != xs[k - 1] or k < len(xs) - 1]
    return all(b > a for a, b in zip(xs, xs[1:]))

def walk_ate():
    """ate_on_the_way over the last walk's trace (`_walk_once`), read when the check runs — not when the row is built."""
    return ate_on_the_way(WALK.get("frames", []))

def fed_up(frames, level):
    """Pure: the walk's done — the bar rose during it and reached `level` (the jar's autoeat eats to it)."""
    fed = [f["food"] for f in frames if f.get("food") is not None]
    return bool(fed) and fed[-1] > fed[0] and fed[-1] >= level

def _walk_once(ctx):
    """Walk east hungry until fed (`fed_up` at the autoeat's level), the walk traced (position, food, the jar's
    task) — the row is the eating, so the walk stops once the bar is where the jar eats it to."""
    import threading
    from .. import api, nav
    frames, stop = [], threading.Event()

    def watch():
        while not stop.is_set():
            if fed_up(frames, nav.WALK_EAT_BELOW):
                api.INTERRUPT = "bench: fed on the walk"
                return
            stop.wait(0.1)
    threading.Thread(target=_trace, args=(stop, frames), daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    try:
        _skill("travel_to")(ctx, at(18, 0, 0), 2)
    except api.Interrupted:
        api.post("/stop")
        if not fed_up(frames, nav.WALK_EAT_BELOW):
            raise
    finally:
        stop.set()
        WALK["frames"] = list(frames)
    return True

def worked_fed(frames):
    """Pure over trace frames: eaten while working, never paused for it — no "eat" task, every bite (the BITE_S
    before a rise) inside a running work task, and the bar no lower at the end than at the start."""
    fed = [f for f in frames if f.get("food") is not None]
    if not fed or any((f.get("task") or {}).get("type") == "eat" for f in frames):
        return False
    if fed[-1]["food"] < fed[0]["food"]:
        return False
    for prev, rise in zip(fed, fed[1:]):
        if rise["food"] <= prev["food"]:
            continue
        bite = [f for f in frames if rise["t"] - BITE_S <= f["t"] <= rise["t"]]
        if any((f.get("task") or {}).get("status") != "running" for f in bite):
            return False
    return True

def mine_fed():
    """worked_fed over the last traced mine (`_mine_hungry`), read when the check runs."""
    return worked_fed(WALK.get("mine", []))

def _mine_hungry(ctx):
    """3 cobblestone mined hungry, the whole run traced (position, food, the jar's task)."""
    import threading
    frames, stop = [], threading.Event()
    threading.Thread(target=_trace, args=(stop, frames), daemon=True).start()
    try:
        return _skill("mine")(ctx, "minecraft:cobblestone", 3, ["stone"], 0)
    finally:
        stop.set()
        WALK["mine"] = frames

def _hunger_drained(ctx):
    """`before` hook: food drained to about half (hunger at full strength for 5 s), and the level remembered — no eat
    target set (that is the eat base's `hungry`, whose word a row saying "&hungry" got)."""
    WALK.clear()
    _chat("effect give @p minecraft:hunger 5 255 true")
    time.sleep(5.5)
    BASE.update(food_before=__import__("bonobo.api", fromlist=["get"]).get("/state")["food"])

# three furnaces, a chain each: interrupted after the first, the resume loads only what is still in the bag
SMELT_FURNACES = (at(2, 0, -2), at(2, 0, 2), at(-2, 0, 2))

def _interrupt_once_loaded(ctx):
    """`before` hook: the interrupt lands the moment raw iron first leaves the bag (a furnace took its share)."""
    def watch():
        t0 = time.time()
        while time.time() - t0 < 30 and BASE.get("name") == "smelt_job_interrupted":
            try:
                if _inv_now().count("minecraft:raw_iron") < _base_count("minecraft:raw_iron"):
                    _inject_interrupt()
                    return
            except Exception:
                pass
            time.sleep(0.05)
    _threading.Thread(target=watch, daemon=True).start()

def _iron_in_furnaces():
    """Raw iron and iron ingots the three furnaces hold, read from the world (data get block … Items)."""
    total = 0
    for p in SMELT_FURNACES:
        slots = furnace_slots(_command(f"data get block {p[0]} {p[1]} {p[2]} Items", []))
        total += sum(n for s_, (item, n) in slots.items() if s_ in (0, 2)
                     and item in ("minecraft:raw_iron", "minecraft:iron_ingot"))
    return total

def _load_the_rest(ctx):
    """Resume: what is still in the bag, loaded — recomputed from the world, not from where the chain stopped."""
    left = _inv_now().count("minecraft:raw_iron")
    if left:
        _skill("start_smelt_job")(ctx, "minecraft:iron_ingot", "minecraft:raw_iron", left, "coal")
    return True

HOME_BED, HOME_FURNACE = (at(4, 0, 2), at(5, 0, 2)), at(4, 0, -2)

def _home_is_ours(ctx):
    """`before` hook: the bed and furnace are a site of ours and the furnace a station — to be kept out of the blast."""
    blocks = {f"{c[0]},{c[1]},{c[2]}": "red_bed" for c in HOME_BED}
    blocks[f"{HOME_FURNACE[0]},{HOME_FURNACE[1]},{HOME_FURNACE[2]}"] = "furnace"
    core.BRAIN.mem.add_site("home", HOME_BED[0], "minecraft:overworld", snapshot={"blocks": blocks}, name="home")
    core.BRAIN.mem.add_station("minecraft:furnace", HOME_FURNACE, "minecraft:overworld")

# knocked off a raised platform mid-fight: the combat kit's water bucket must catch the fall
EDGE_Y = 4
# -- a search interrupted mid-way (for the night) and taken up again: no section searched twice, no ore scanned again
_goal = lambda *needs: __import__("bonobo.goals", fromlist=["have"]).have(*needs)     # noqa: E731
SEARCH_ARENA = [f"fill {_c(at(-8, -3, -8))} {_c(at(20, -1, 8))} stone",               # the bench box's whole floor
                f"fill {_c(at(6, 0, -6))} {_c(at(8, 4, 6))} stone", _tp()]                # a hill in the way
SEARCH_ORE = at(14, -1, 3)                   # a diamond remembered past the hill, one down (dig one to it)

def _set_time(t):
    return lambda: _chat(f"time set {t}")

SEARCH_FLAGS = {}
# == tiers: core runs on every change, common when a related module changed, exception before a merge, acceptance alone
TIERS = ("core", "common", "brain", "combat", "exception", "acceptance")
# fighting is its own tier
COMBAT_PREFIXES = ("fight_", "combat_arena", "siege__", "escape__", "fight_before_upkeep", "combat__")
# fights whose names say otherwise; resume_after_combat left out on purpose
COMBAT_ROWS = ("bed_bomb_kill", "collect_blaze_rods", "ghast_fireball")
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
            path = os.path.relpath(inspect.getsourcefile(c.fn), root)
        except (OSError, TypeError):
            continue
        out[name] = (path, first, first + len(lines) - 1)
    return out

def _kit_gives(row, jobs):
    """The gives the kit rule adds to `row`: the best work tool per job, and for a fight the sword its mobs call for."""
    import re
    from .core import BEST_TOOLS, weapon_for
    mobs = set(re.findall(r"summon (?:minecraft:)?(\w+)", " ".join(map(str, row.get("setup", ())))))
    enemy = (row.get("tags") or {}).get("enemy")
    mobs |= {enemy} if enemy else set()
    if any(k in s for s in list(row.get("skills", ())) + [str(row.get("doc", ""))] for k in ("dragon", "crystal")):
        mobs.add("ender_dragon")          # the End fight: the dragon is there, not summoned
    return [weapon_for(sorted(mobs)) if j == "sword" else BEST_TOOLS[j] for j in jobs]

# -- positions ----------------------------------------------------------------------------------------------------
def pos(p):
    """("@", dx, dy, dz) → the absolute position; anything else unchanged."""
    return at(*p[1:]) if isinstance(p, tuple) and len(p) == 4 and p[0] == "@" else p

def _c(p):
    p = pos(p)
    return f"{p[0]} {p[1]} {p[2]}"

# -- scene templates: (template, *params) → console commands -----------------------------------------------------

SCENE = {
    "cmd": lambda text: [text],                                                   # a command with no position
    "fill": lambda lo, hi, block: [f"fill {_c(lo)} {_c(hi)} {block}"],
    "setblock": lambda p, block: [f"setblock {_c(p)} {block}"],
    "tp": lambda p: [f"tp @p {_c(p)}"],
    "stand": lambda *d: [_tp(*d)],                                                  # the body, mid-block
    "give": lambda item, n=None: [f"give @p {item}" + ("" if n is None else f" {n}")],
    "time": lambda t: [f"time set {t}"],
    "summon": lambda mob, p, nbt=None: [f"summon {mob} {_c(p)}" + ("" if nbt is None else f" {nbt}")],
    "at": lambda template, *ps: [template.format(*[_c(p) for p in ps])],        # any other command with positions
    "floor": _floor, "tree": _tree, "grove": _grove, "pen": _pen, "tank": _tank,       # the sheet's own builders
    "chest": lambda p, *items: _chest(pos(p), *items),
}

def _scene_params(v):
    """Scene params → values: positions absolute, containers kept."""
    if isinstance(v, tuple) and len(v) == 4 and v[0] == "@":
        return pos(v)
    if isinstance(v, (list, tuple)):
        return type(v)(_scene_params(x) for x in v)
    return v

def scene(items):
    """A row's scene → the setup command list, in order. ("sheet", NAME) is one of the old sheet's command lists
    (a world several rows share: the fight arena, the brain's world); ("built", "mod:fn", *params) a builder
    that draws its commands (a fight cell from its seed)."""
    out = []
    for kind, *params in items:
        if kind == "sheet":
            out += list(resolve(params[0]))
        elif kind == "built":
            out += list(resolve(params[0])(*[_scene_params(p) for p in params[1:]]))
        else:
            out += SCENE[kind](*params)
    return out

# -- names --------------------------------------------------------------------------------------------------------
def resolve(name):
    """A word → the sheet's function or value: "mod.path:attr", a sheet name (its own, or with the leading
    underscore the table drops), or a dotted module function."""
    if ":" in name:
        mod, attr = name.split(":")
        return getattr(importlib.import_module(mod), attr)
    found = [n for n in (name, "_" + name) if n in globals()]
    if len(found) == 2 and globals()[found[0]] is not globals()[found[1]]:
        raise KeyError(f"ambiguous word {name!r}: both {found[0]!r} and {found[1]!r} are defined — rename one")
    if found:
        return globals()[found[0]]
    if "." in name:
        mod, attr = name.rsplit(".", 1)
        return getattr(importlib.import_module(mod), attr)
    raise KeyError(f"no word {name!r}")

# -- predicates: (kind, *args) over (api, inv) ---------------------------------------------------------------------
OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "==": operator.eq,
       "!=": operator.ne, "is": operator.is_, "is not": operator.is_not,
       "in": lambda a, b: a in b, "not in": lambda a, b: a not in b}

def cmp(value, op=None, want=None):
    """Pure: `value op want`, or the value's truth when no op is given."""
    return bool(value) if op is None else OPS[op](value, want)

def count(api, inv, token, op, want):
    """The bag's count of `token` (a group name counts its members) compared: ("count", "log", ">=", 4)."""
    return cmp(inv.count(token), op, want)

def state(api, inv, key, op=None, want=None):
    """A /state field compared (or its truth): ("state", "dimension", "==", "minecraft:the_nether")."""
    return cmp(api.get("/state")[key], op, want)

def bag(api, inv, method, args=(), op=None, want=None):
    """An Inventory reading compared: ("bag", "used_slots", [], "<", 34)."""
    return cmp(getattr(inv, method)(*args), op, want)

def call(api, inv, name, args=(), op=None, want=None, resolve=None):
    """A named world helper's answer compared (or its truth); "$api"/"$inv" in `args` are the check's own."""
    fn = resolve(name)
    return cmp(fn(*[api if a == "$api" else inv if a == "$inv" else a for a in args]), op, want)

PREDICATES = {"count": count, "state": state, "bag": bag, "call": call}
LOGIC = ("all", "any", "not", "now")      # composition: all/any of predicates, not one, one read on the bag now

# -- hooks the old sheet wrote as a lambda of several steps ---------------------------------------------------------
def hungry(ctx):
    """The eat base's start: hunger at full strength for 5 s, the bar read before eating, the eat target set."""
    from .. import api
    _chat("effect give @p minecraft:hunger 5 255 true")
    time.sleep(5.5)
    BASE.update(food_before=api.get("/state")["food"])
    _eat_target(ctx)

HOOKS = {"hungry": hungry}

# -- row templates: (template, params) → row data in these words ---------------------------------------------------
def limit():
    """The bench's hard limit per row (runner.ROW_LIMIT_S): no template asks for more."""
    from .runner import ROW_LIMIT_S
    return ROW_LIMIT_S

def nest(w):
    """A word at the top of a slot → the same word inside another's arguments (a one-off row's code: as is)."""
    return w if callable(w) or w[0].startswith(("!", "&")) else ("!" + w[0],) + tuple(w[1:])

def top(w):
    return w if callable(w) else (w[0][1:],) + tuple(w[1:]) if w[0].startswith("!") else w

def items(w):
    """A check word → the row's check list (an `all` is its parts)."""
    w = top(w)
    return [w] if callable(w) else [top(x) for x in w[1:]] if w[0] == "all" else [w]

def _progress(b):
    return {k: b[k] for k in ("progress", "effect", "target") if k in b}

BOX_EXPECT = [(("@", -10, -17, -10), ("@", 20, 9, 10), "*", 1, 10 ** 6)]      # a generated row's box signature
SHEET_EXPECT = [(at(*BOX[0]), at(*BOX[1]), "*", 1, 10 ** 6)]                    # the same, for a one-off row

def base_row(name, base, cond=None, surprise=None):
    """A base changed by a condition or a surprise (the old sheet's `_row`), as data."""
    from .bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK
    from .runner import ROW_LIMIT_S
    b, c = BASES[base], CONDITIONS[cond] if cond else {}
    x = SURPRISES[surprise] if isinstance(surprise, str) else surprise or {}     # a one-off row: its own surprise
    scene = list(x["scene"]) if x.get("replace_setup") else b["scene"] + c.get("scene", []) + x.get("scene", [])
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
        if act:
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

# -- the fight rows: a cell of bench_combat's dimensions, recorded by a sweep and judged by rules --------------------
FIGHT_EXPECT = [(("@", -9, -1, -9), ("@", 12, -1, 9), "stone", 418, 418)]
RULES = ("&_answers_are_closed",), ("&_shapes_fit_the_enemy",), ("&_more_of_them_costs_more",), ("&_wave_cleared",)

def _fight_row(name, doc, scene, cells, build, record, jsonl, settle, rules, **more):
    """A swept fight row: its cells built and fought one by one, each recorded, the rules judged over the rows."""
    return {"name": name, "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:overworld",
            "doc": doc, "scene": scene, "run": ("bonobo.bench.core:_sweep", name, ("!iter", cells), build, record,
                                                ("$data", jsonl), settle),
            "check": [("bonobo.bench.core:_sweep_check", name, ("$data", jsonl), list(rules), 1)] if rules else [],
            "budget": limit(), **more}

def _fought_for(seconds):
    return ("!_fought", ("&_kinds_of",), seconds)

def arena_row(name, enemy, ground, kit, blood):
    """combat_arena: one cell of (enemy, ground) × (kit, blood) per row."""
    cell = dict(ARMED, enemy=enemy, ground=ground, kit=kit, blood=blood, run=0)
    return _fight_row(name, f"combat_arena shard {enemy}/{ground}/{kit}/{blood}: each cell writes the whole decision "
                            "into bench/combat.jsonl; the rules are relations between rows.",
                      [("sheet", "_FIGHT_SETUP")], [cell], ("&_build",), _fought_for(CELL_SECONDS),
                      "bench/combat.jsonl", 0.6, RULES[:3], module="threat", sweep=True,
                      variant=[sorted(cell.items())], expect=FIGHT_EXPECT, tick_rate=60)

def siege_row(name, wave):
    """The siege, one wave per row: from the wave's carry, cleared alive."""
    line_up, _, left = WAVES[wave - 1]
    return _fight_row(name, f"Siege wave {wave} of {len(WAVES)} ({line_up}), sword, pickaxe, full iron, shield, food "
                            "and blocks: every answer the model offers is available → the wave cleared alive.",
                      [("sheet", "_FIGHT_SETUP"), ("cmd", "effect give @p minecraft:instant_health 3 10 true"),
                       ("built", "_siege_kit"), ("built", "_carry") + tuple(left)],
                      [{"wave": wave, "line_up": line_up}], ("&_siege_build",), ("!_siege_record", 24.0),
                      "bench/siege.jsonl", 0.6, (RULES[0], RULES[3]), detail=("siege_detail", name))

def escape_row(name, enemy, ground, kit, seed=None):
    """No weapon, no armour, one enemy: away by any answer but swinging; no seed given, one is drawn (as at import)."""
    seed = random.randrange(1 << 30) if seed is None else seed
    cell = dict(UNARMED, enemy=enemy, ground=ground, kit=kit, run=0, seed=seed)
    return _fight_row(name, f"No weapon, no armour, {enemy} on {ground} ground with {kit}, {ESCAPE_SECONDS:.0f} s: the "
                            "answer has to come from somewhere other than swinging — back off, block the way, dig "
                            "down, eat, or leave a teleporter alone (bench/escape.jsonl).",
                      [("cmd", "gamemode survival @p"), ("cmd", "kill @e[type=!player,type=!item,distance=..48]"),
                       ("built", "_build", cell)], [cell], ("!constant", []), _fought_for(ESCAPE_WATCH),
                      "bench/escape.jsonl", 0.0, RULES[:2], sweep=True, expect=FIGHT_EXPECT,
                      detail=("escape_detail", name), tick_rate=60)

def behaviour_row(name, behaviour):
    """One fight behaviour: a cell moved off ARMED so that one answer is worth the most; chosen and working."""
    moved, _rule, why = BEHAVIOURS[behaviour]
    cell = dict(dict(ARMED, **moved), run=0, seed=0)
    row = _fight_row(name, f"Fight behaviour: {why}", [("sheet", "_FIGHT_SETUP")], [cell], ("&_build",),
                     ("!_record_with_start", _fought_for(BEHAVIOUR_SECONDS)), "bench/behaviour.jsonl", 0.6, (),
                     stochastic=True, variant=sorted(cell.items()), expect=FIGHT_EXPECT, tick_rate=60)
    return dict(row, check=[("behaviour", behaviour)])

def fight_cell_row(name, mob, n, tier, secs, hp, clear):
    """A walled arena, the iron kit, `n` of one mob: all dead (or kept off, or left alone when neutral)."""
    kinds = [f"minecraft:{mob}"]
    spots = ([(7, 0, 0)] if mob == "creeper" else [(4, 0, 0), (-3, 0, 3), (1, 0, -4)])[:n]
    nbt = "{PersistenceRequired:1b,Health:10f}" if mob == "blaze" and n > 1 else "{PersistenceRequired:1b}"
    verdict = {True: "all dead", False: "left alone (neutral)",
               "resolved": f"dead, or kept off and not following for {RESOLVE_HOLD_S:.0f} s"}[clear]
    check = ([("threat_resolved", kinds), ("decision_gaps_ok",)] if clear == "resolved"
             else [("hp_kept", hp), ("gone", kinds), ("decision_gaps_ok",)] if clear
             else [("hp_kept", hp), ("call", "hostiles", [24, ("$set", kinds)])])
    return _row(name, f"Walled platform, iron kit: {n} {mob} → {verdict}, health ≥ {hp}, a threat decision every "
                      "≤ 1.5 × FIGHT_POLL_S while engaged", "fight_loop",
                [("sheet", "_ARENA")] + [("summon", mob, ("@", x, y, z), nbt) for x, y, z in spots],
                ("fight_until", kinds, secs - RESOLVE_HOLD_S - 2 if clear == "resolved" else secs - 2)
                + (() if clear is not False else (False,)), check, point="B", budget=min(secs + 5, limit()),
                before=[("&record_bids",)], combat=True, skills=[], tier_fixed=tier,
                tags={"base": "fight", "enemy": mob, "count": n}, expect_entities=[(f"minecraft:{mob}", n)])

def siege_detail(name):
    return lambda inv: _siege_detail_of(name)

def escape_detail(name):
    return lambda inv: "; ".join(f"{r['enemy']}: {r['outcome']['hp']:.0f} hp, gap {r['outcome']['gap']}"
                                 for r in (SWEEP.get(name) or []))

def behaviour(name):
    """The behaviour's own rule over its recorded row (BEHAVIOURS)."""
    return _behaviour_check(f"combat__{name}", BEHAVIOURS[name][1])

WORDS = {"siege_detail": siege_detail, "escape_detail": escape_detail, "behaviour": behaviour}

# -- one-skill rows, the start-cell and placing rows, the upkeep lines and the brain's rows --------------------------
def _row(name, doc, module, scene, run, check, point="A", budget=None, before=(), **more):
    """A row's common frame: started (`_start`), the box signature, the bench's limit unless it asks less."""
    return {"name": name, "doc": doc, "module": module, "point": point, "scene": list(scene),
            "before": [("start", name)] + list(before), "run": run, "check": check,
            "budget": budget or limit(), "expect": BOX_EXPECT, **more}

def one_row(name, skills, doc, scene, run, check, budget, tick_rate=None):
    """One skill proven in the world, once (the old `_ONE`): timed when its skill has a speed target."""
    target = TARGET_S[skills[0]] * TARGET_SLACK if skills[0] in TARGET_S else None
    return _row(name, doc, "skills", scene, ("timed", nest(run)) if target else run, items(check), budget=budget,
                skills=list(skills), tags={"base": skills[0]}, **({"target_s": target} if target else {}),
                **({"tick_rate": tick_rate} if tick_rate else {}))

REAL_KIT = [("cmd", "spreadplayers 14200 14200 0 4 false @p"), ("cmd", "clear @p"), ("give", "stone_pickaxe"),
            ("give", "torch", 8), ("give", "cobblestone", 32), ("give", "cooked_beef", 8)]

def real_row(name, skills, doc, run, check, budget, extra=(), stochastic=False):
    """On real terrain (raw), a target put in scan range: judged by what was found."""
    row = _row(name, doc, "skills", REAL_KIT + list(extra), run, items(check), budget=budget, raw=True, release=True,
               skills=list(skills), tags={"base": skills[0], "terrain": "real"},
               **({"stochastic": True} if stochastic else {}))
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

def upkeep_row(name, line, doc, scene, hooks, done, check):
    """One upkeep line through the whole brain, nothing queued: the moment built, the answer in the world."""
    return _row(name, f"upkeep, {doc}", "reflexes", scene,
                ("brain_rounds", 10 if line == "eat_when_full" else 22, nest(done)), items(check), point="C",
                before=hooks, skills=[], tier_fixed="brain", combat=line == "eat",
                tags={"base": "upkeep", "line": line})

def brain_row(name, doc, scene, queue, done, minutes, check, hooks=(), variant=()):
    """The whole brain on a private queue, the world built up to the decision; the slice judged too."""
    return _row(name, doc, "brain", scene, ("slice", nest(done), min(minutes, 0.4), None, queue),
                [top(check), ("slice_check", None)], point="C", before=hooks, skills=[], tier_fixed="brain",
                combat=name == "resume_after_combat", tags={"base": "brain"}, queue=queue, variant=list(variant))

def dirt_row(name, doc, extra, done, check):
    """Dusk on stone, an empty bag, a dirt patch along the platform: dug in there by hand (or never walked to)."""
    return _row(name, doc, "brain", [("floor",), ("fill", ("@", 7, -3, -1), ("@", 8, -1, 1), "dirt"),
                                     ("fill", ("@", 7, -4, -1), ("@", 8, -4, 1), "stone")]
                + list(extra) + [("stand",), ("time", 12500)], ("brain_rounds", 25, nest(done)), items(check),
                point="C", skills=["shelter:dig in"], tier_fixed="brain",
                tags={"base": "brain", "family": "night_dirt"})

def cell_row(name, *key):
    """A cell of the brain's grid: its families' goals queued, every family's rule judged, the slice too."""
    entry = _grid_cells()[key]
    cell, fams = entry["cell"], entry["families"]
    judged = [BRAIN_FAMILIES[f][2](cell) for f in fams]
    row = _row(name, f"{'+'.join(fams)}: " + ", ".join(f"{d} {cell[d]}" for d in BRAIN_DIMS) + " → "
               + "; ".join(why for _c, why in judged), "brain", [("sheet", "BRAIN_WORLD"), ("brain_dims",) + key],
               ("slice", None, 0.4, None, list(entry["queue"])),
               [("brain_rule", f) + key for f in fams] + [("slice_check", None)], point="C", skills=[],
               tier_fixed="brain", combat=False, queue=list(entry["queue"]),
               tags={"base": "brain", "family": "+".join(fams), **{d: cell[d] for d in BRAIN_DIMS}},
               why=[why for _c, why in judged] + ["the slice"])
    return dict(row, before=[("brain_cell_hooks", name) + key])

def _grid_cell(key):
    return _grid_cells()[tuple(key)]["cell"]

def brain_rule(fam, *key):
    """A grid family's rule for one cell (BRAIN_FAMILIES): its check."""
    return BRAIN_FAMILIES[fam][2](_grid_cell(key))[0]

def brain_cell_hooks(name, *key):
    cell = _grid_cell(key)
    return _hooks(*_cell_setup_hooks(cell), _start(name), *_cell_before(cell))

WORDS.update(brain_rule=brain_rule, brain_cell_hooks=brain_cell_hooks)
SCENE["brain_dims"] = lambda *key: [c for d, v in zip(BRAIN_DIMS, key) for c in BRAIN_DIMS[d][v]]
TEMPLATES = {t: globals()[f"{t}_row"] for t in ("base", "arena", "siege", "escape", "behaviour", "fight_cell", "one",
                                                "real", "place", "start", "upkeep", "brain", "dirt", "cell")}
NAMES = {"base": lambda base, cond=None, surprise=None: surprise or f"{base}__{cond or 'base'}",
         "arena": lambda i, *cell: f"combat_arena__{i}", "siege": lambda w: f"siege__w{w}",
         "escape": lambda enemy, ground, kit, seed=None: f"escape__{enemy}_{ground}_{kit}",
         "behaviour": lambda b: f"combat__{b}", "fight_cell": lambda name, *p: name,
         "upkeep": lambda line, *p: f"upkeep__{line}",
         "cell": lambda *key: grid_name(_grid_cells()[key]["families"], _grid_cell(key)),
         **{t: (lambda name, *p: name) for t in ("one", "real", "place", "start", "brain", "dirt")}}
NAMED = {"arena", "fight_cell", "one", "real", "place", "start", "brain", "dirt"}          # templates whose first parameter is only the row's name
__all__ = [n for n in dir() if not n.startswith("__")]      # the tables write in every word here
from .bench_combat import (ARMED, ARMOUR, BLOOD, COUNT, DIMS, DISTANCE, ENEMY, GROUND, KIT, NEEDS,  # noqa: E402
                           UNARMED, WAVES, WEAPON)     # combat's dimensions, its data; last: its rows use these words
