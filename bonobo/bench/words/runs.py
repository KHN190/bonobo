"""Run and hook words: slices of the whole brain, timing, resumes, interruptions by progress, expected failures."""
import importlib
import json
import math
import operator
import os
import random
import re
import sys
import threading as _threading
import time

from ... import estimate, paths  # noqa: F401
from ..core import bag_now
import importlib
import json
import math
import operator
import os
import re
import sys
import time
from .. import core, runner
from ...data import DAY_TICKS, POD_BLOCKS  # noqa: F401
from ..core import *          # noqa: F403  (the bench's primitives are this module's own vocabulary)
from ..core import (BOX, FLAG, NOTES, ORIGIN, SCENARIOS, SetupInvalid, _achieve, _c, _chat, _checked,
                         _command, _count_blocks, _drain, at, server_count, set_brain)
from ..runner import *        # noqa: F403
from ..runner import (LAST_FEEDBACK, LAST_LINES, _setup, _trace, classify, code_for, feedback_errors, load_table,
                           module_deps, record, run_named, save_table, setup_mismatches, silent_failure, status)
from ..bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK   # the bases' data: one home
from ..core import SWEEP, _platform  # noqa: F401
from .scene import *  # noqa: F401,F403
from .checks import *  # noqa: F401,F403

# -- slices: the cerebellum itself over a private task queue; measures scheduling and chaining (loops, idle holds, wrong-way unstucks)
SLICE = {}
from ... import lifecycle as _lifecycle  # noqa: E402
_lifecycle.in_place(__name__, "SLICE")     # a row's own record

# rounds given to a waiting kind: with work queued, any is a waste
MAX_WAITS_WITH_QUEUE = 0

def slice_report(lines, positions, target, idle_s, picks=None):
    """Pure: loops, the longest idle, and the total walked away from `target`, from a slice's log lines and samples."""
    from ... import review
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
    from ...arbiter import waits
    return {"loops": loops, "idle_s": round(idle_s), "away_m": round(away), "waits": waits(picks or {})}

def queue_finished(items):
    """Pure: every task of a slice's queue left the live states — the slice's own work is over."""
    from ...tasks import LIVE
    return bool(items) and all(t["state"] not in LIVE for t in items)

def tier_rows(rows, tier, named):
    """Pure: the rows a tier selects for `--failed` / `--pending`: never acceptance; only `tier`'s when one was named."""
    return [n for n, r in rows.items() if r["tier"] != "acceptance"
            and (not named or tier == "all" or r["tier"] == tier)]

def _slice(done, minutes, target=None, queue=(), max_idle=15):
    """Run the whole cerebellum until done() or `minutes`, on a private task queue holding `queue`."""
    def run(ctx):
        from ... import api, tasks
        from ... import api
        from ...world import Inventory, Snapshot
        saved = tasks.FILE
        tasks.FILE = os.path.join(os.path.dirname(NOTES), "slice-tasks.json")
        tasks.save([])
        for goal in queue:
            tasks.add(goal, source="bench")
        core.BRAIN.idle_since, core.BRAIN.committed = None, None
        core.BRAIN.picks.clear()              # this slice's rounds only
        t0, positions, idle = time.time(), [], 0.0
        start_line = len(getattr(sys.stdout, "lines", []))       # the bench's capturing stdout keeps its lines
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
                s = Snapshot.from_readings(api.get("/state"), bag_now())
                positions.append((time.time(), s.feet))
                if core.BRAIN.idle_since:
                    idle = max(idle, time.time() - core.BRAIN.idle_since)
                lines = getattr(sys.stdout, "lines", [])[start_line:]
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
            last = [l.strip() for l in getattr(sys.stdout, "lines", [])[start_line:]
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
    from ... import arbiter
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
    from ...world import Inventory
    return bag_now().count("minecraft:stone_pickaxe") >= 1

def _nether_kit_ready():
    from ...knowledge import nether_kit_missing
    from ...world import Inventory
    return not nether_kit_missing(bag_now())

def _in_overworld():
    from ... import api
    return api.get("/state")["dimension"] == "minecraft:overworld"

def _portal_beside_player(ctx):
    """A lit portal three blocks east of the player, remembered as built."""
    from ... import api
    s = api.get("/state")
    x, y, z = s["blockX"] + 3, s["blockY"], s["blockZ"]
    for cmd in (f"fill {x} {y - 1} {z - 1} {x} {y + 3} {z + 2} obsidian",
                f"fill {x} {y} {z} {x} {y + 2} {z + 1} nether_portal[axis=z]"):
        _chat(f"execute in minecraft:overworld run {cmd}")
    ctx.mem.add_machine("nether_portal", (x, y - 1, z - 1), 1, "minecraft:overworld", ["portal"])
    ctx.mem.add_site("portal", (x, y, z), "minecraft:overworld", name="portal-overworld")

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
    from ...memory import Memory
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

# -- run wrappers: timing and expected failures
def _expect_failure(name, run, pattern):
    """An expected-failure row: the run must end with a failure naming the reason — not succeed, not fail otherwise."""
    def go(ctx):
        from ... import api
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
        from ... import api
        fn = run
        try:
            for _ in range(tries):
                try:
                    return fn(ctx)
                except api.INTERRUPTIONS as e:
                    INTERRUPTS[name] = INTERRUPTS.get(name, 0) + 1
                    api.detail(f"bench: {name} caught {type(e).__name__} ({e}), resumed ({INTERRUPTS[name]})")
                    fn = resume
            raise api.McError(f"still interrupted after {tries} tries")
        finally:
            left = api.interrupt_pending()
            if str(left or "").startswith("bench:"):
                api.consume_interrupt()    # an injected interrupt that landed after the end must not stop the next row
            api.detail(f"bench: {name} run over, interrupts caught {INTERRUPTS.get(name, 0)}"
                       + (f", a bench interrupt still pending consumed ({left})" if left and str(left).startswith("bench:")
                          else ""))
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
    if how is None:
        raise ValueError(f"{name}: on_progress on a base with no progress, effect or target to watch")
    def hook(ctx):
        from ... import api
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
                        api.detail(f"bench: {name} progress {k} seen at {time.time() - t0:.1f}s")
                        action()
                        k += 1
                        continue
                except api.McError:
                    pass
                time.sleep(0.05)
        _threading.Thread(target=watch, daemon=True).start()
    return hook

INJECTED = "bench: injected interrupt"


def task_due(task, fired_ids, caught, fired, times):
    """Pure: inject now? A jar task running that no injection has hit, the last injection caught (the work resumed),
    fewer than `times` injected. Injected on the bag's gain it landed after the work's last check (caught 0)."""
    if fired >= times or caught < fired or not task or task.get("status") != "running":
        return False
    return task.get("id") not in fired_ids


def _on_task(name, action, times=1):
    """`before` hook: `action()` while a jar task of the run is running (/state control.task) — its wait sees the
    interrupt at the task's end; for more than one, each on the next running task after the last was caught."""
    def hook(ctx):
        from ... import api

        def watch():
            t0, fired, fired_ids = time.time(), 0, set()
            while fired < times and time.time() - t0 < 120 and BASE.get("name") == name:
                try:
                    task = (api.get("/state").get("control") or {}).get("task")
                    if task is not None and task_due(task, fired_ids, INTERRUPTS.get(name, 0), fired, times):
                        api.detail(f"bench: {name} injects on running task {task.get('id')} {task.get('type')}"
                                   f" at {time.time() - t0:.1f}s")
                        action()
                        fired_ids.add(task.get("id"))
                        fired += 1
                        continue
                except api.McError:
                    pass
                time.sleep(0.05)
        _threading.Thread(target=watch, daemon=True).start()
    return hook


def _inject_interrupt(message=INJECTED):
    """Leave an interrupt for the running work, as perception does (api.request_interrupt)."""
    from ... import api
    api.request_interrupt(message)
    t = time.time() - BASE.get("t", time.time())
    api.detail(f"bench: {BASE.get('name')} interrupt injected at {t:.1f}s ({message})")

def _post_foreign_task():
    """Another commander posts a task straight to the mod (BodyContested for the skill)."""
    __import__("bonobo.api", fromlist=["api"]).api("POST", "/task?wait=0", {"type": "wait", "ticks": 40})

def _take_over(hold=3):
    """The player presses the toggle key (jar /control, as the key does), then hands back after `hold` s."""
    from ... import api
    api.api("POST", "/control", {"paused": True})
    _threading.Timer(hold, lambda: api.api("POST", "/control", {"paused": False})).start()

def _sand_on_head():
    from ... import api
    s_ = api.get("/state")
    x, y, z = s_["blockX"], s_["blockY"], s_["blockZ"]
    _chat(f"fill {x} {y + 1} {z} {x} {y + 3} {z} sand")

def gained_at_least(token, n):
    """Progress: the bag holds `n` more `token` than at the start — the gain check, read now."""
    return _now(_gain(token, n))

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
        row = BASE.get("name")

        def watch():
            from ... import api
            t0 = time.time()
            # this row's only: a watcher left from search_night_resume set the clock to night inside
            # upkeep__collect_job (20260928-082806: "collect job interrupted (night)")
            while time.time() - t0 < limit_s and BASE.get("name") == row:
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
    return _when(progress, lambda: _inject_interrupt(message))

def _unless_done(check, run):
    """Resume only what is not done: an interruption that landed at the moment of success leaves nothing to redo."""
    def go(ctx):
        from ... import api
        return True if check(api, _inv_now()) else run(ctx)
    return go

def _after_l0(name, run, resume, tries=4):
    """Run; on an L0 interruption let the brain's rounds rescue the body, then resume by what is still missing."""
    def go(ctx):
        from ... import api, hazard
        fn = run
        for _ in range(tries):
            try:
                return fn(ctx)
            except api.INTERRUPTIONS:
                INTERRUPTS[name] = INTERRUPTS.get(name, 0) + 1
                t0 = time.time()
                while time.time() - t0 < 20 and hazard.rescue_due(api.get("/state")) is not None:
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
        from ... import api, decompose, goals
        from ...cost import Cost
        from ... import api
        from ...world import Inventory, Snapshot
        snap = Snapshot.from_readings(api.get("/state"), bag_now())
        steps = decompose.decompose(snap.inv, goals.have(*needs), Cost(snap, ctx.mem))
        if steps:
            raise api.McError(f"goal already met, but planned {' → '.join(map(str, steps))}")
        return True
    return run

def _brain_rounds(seconds, until):
    """The whole cerebellum for up to `seconds` (L0 and upkeep included), until `until()`."""
    def run(ctx):
        t0 = time.time()
        core.BRAIN.wake = until          # an idle round ends the moment the outcome is there
        try:
            while time.time() - t0 < seconds and not until():
                core.BRAIN.round()
        finally:
            core.BRAIN.wake = None
        return until()
    return run

def _enclosed():
    """Walled in, feet and head, and covered: what a burrow, a pod or a dug-in hole must leave."""
    from ... import survive
    return survive.enclosed()

def _breathing(least=280):
    """Out of the water's grip: the air bar back near full and the head out of the water."""
    def check(api, inv):
        from ... import skillcore
        s = api.get("/state")
        return s["air"] >= least and not skillcore.head_underwater(s)
    return check

def _buried_first(ctx, polls=5):
    """`before` hook: the head is inside the sand the row dropped — else the body was pushed clear, the check (head
    clear) holds before any round and the row passes without a rescue: SetupInvalid."""
    from ... import skillcore
    for _ in range(polls):
        if skillcore.head_buried():
            return
        time.sleep(0.2)
    raise SetupInvalid("the sand did not bury the head: head clear before any round, nothing to rescue")

def _brain_idle():
    """Done: the brain looked and found nothing to do — a must-not row's window judged (nothing it may do fired)."""
    return core.BRAIN.idle_since is not None

def _drowning_first(ctx):
    """`before` hook: the head is under water as the row starts — else the pit did not hold the body under and the
    row proves no surfacing: SetupInvalid."""
    from ... import skillcore
    if not skillcore.head_underwater():
        raise SetupInvalid("the body's head is not under water: nothing to surface from")

def _head_clear():
    from ... import skillcore
    return not skillcore.head_buried()

def eat_target_s(food, carried):
    """Pure: an eat row's speed target: per bite × the bites the bar's gap takes, with slack."""
    from ...survive import bites_to_full
    _item, bites = bites_to_full(food, carried)
    return TARGET_S["eat"] * bites * TARGET_SLACK if bites else None

def _eat_target(ctx):
    """`before` hook (after the drain): the eat row's target from the bar and the bag it starts with."""
    from ...world import Inventory
    from ...knowledge import ALL_FOOD
    inv = bag_now()
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

# -- hooks the old sheet wrote as a lambda of several steps ---------------------------------------------------------
def hungry(ctx):
    """The eat base's start: hunger at full strength for 5 s, the bar read before eating, the eat target set."""
    from ... import api
    _chat("effect give @p minecraft:hunger 5 255 true")
    time.sleep(5.5)
    BASE.update(food_before=api.get("/state")["food"])
    _eat_target(ctx)

HOOKS = {"hungry": hungry}


__all__ = ['HOOKS', 'MAX_WAITS_WITH_QUEUE', 'MILESTONE_SCENARIOS', 'SLICE', '_achieve_needs', '_after_l0', '_brain_rounds', '_breathing', '_buried_first', '_drowning_first', '_brain_idle', '_eat_target', '_enclosed', '_expect_failure', '_forget_skill_time', '_has_stone_pickaxe', '_head_clear', '_hooks', '_in_overworld', 'task_due', '_on_task', 'INJECTED', '_inject_interrupt', '_interrupt_when', '_nether_kit_ready', '_on_progress', '_plan_is_empty', '_portal_beside_player', '_post_foreign_task', '_progress_of', '_resume', '_sand_on_head', '_skill_within', '_slice', '_slice_check', '_slice_detail', '_sprint_after', '_stronghold_error', '_take_over', '_timed', '_trades', '_unless_done', '_when', 'eat_target_s', 'gained_at_least', 'hungry', 'locate_reply', 'placed_at_least', 'queue_finished', 'readiness_lines', 'slice_report', 'slice_verdict', 'tier_rows', 'walked_at_least']
