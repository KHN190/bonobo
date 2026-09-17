"""Test the cerebellum's decisions apart from execution (no game):

- `decide(row)`         one recorded round (tape.py) decided again by the current code → pick, top 5, filtered
- `diff(rows)`          rounds whose pick changed (or that can't be replayed: new world queries) since recording
- `simulate(row, fn)`   one recorded world for hundreds of rounds; `fn(pick, i)` says success or which failure, the
                        clock advances by the pick's cost — to check loops, retries, method switches
- `record_live(brain)`  the decision the brain would make right now (recorded, not executed): decision scenarios

`mc.py decide replay|diff|golden` wraps these; tests/golden/*.json are recorded situations with the expected pick.
"""
import json
import os
import tempfile
from contextlib import contextmanager
from unittest import mock

from . import tape

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests", "golden")


def load(path=None, last=None):
    rows = []
    with open(path or tape.FILE) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue          # the line autoplay is still writing (read while recording)
    return rows[-last:] if last else rows


@contextmanager
def _files(row):
    """The recorded priorities/route/directives files, in a temp dir the modules point at."""
    from . import directives, priority, route
    tmp = tempfile.mkdtemp(prefix="decide-")
    saved = {}
    for mod in (priority, route, directives):
        key = mod.__name__.split(".")[-1]
        path = os.path.join(tmp, key + ".json")
        if row["files"].get(key) is not None:
            with open(path, "w") as f:
                f.write(row["files"][key])
        saved[mod] = mod.FILE
        mod.FILE = path
    try:
        yield
    finally:
        for mod, path in saved.items():
            mod.FILE = path


def make_brain(row):
    """A Brain holding the recorded decision inputs, with a throwaway memory file."""
    from . import skill as skillkit
    from .brain import Brain
    from .memory import Memory
    b = Brain()
    b.mem = Memory(os.path.join(tempfile.mkdtemp(prefix="decide-mem-"), "notes.json"))
    b.mem.data = tape.load_mem(row["mem"]) if isinstance(row["mem"], str) else row["mem"]
    skillkit.STATS = b.mem
    tape.decode_retry(row["retry"], b.retry)
    b.committed = row.get("committed")
    b.idle_since = row.get("idle_since")
    b.recent_fail = tuple(row["recent_fail"]) if row.get("recent_fail") else None
    b.fail_sig = {k: tape.decode_sig(v) for k, v in row.get("fail_sig", {}).items()}
    b.blacklist = {tuple(k): v for k, v in row.get("blacklist", [])}
    stalled = row.get("stalled", 0)
    b.stalled_seconds = lambda: stalled
    b.snap_cache = None
    return b


def decide(row, brain=None, now=None):
    """(pick name or None, top [(name, score)], filtered, pick) for a recorded round, decided by the current code."""
    from . import loot, priority, retry, skills
    from .world import Snapshot
    b = brain or make_brain(row)
    now = now or row["t"]
    _clear_caches(b, loot)
    cached = row.get("loot_cache")
    if cached and cached.get("pos") is not None and cached.get("age", 1e9) < 60:
        # The live round used a fresh chest-scan cache: replay with the same cache, not a query it never made.
        loot._CACHE.update(t=now - cached["age"], pos=tuple(cached["pos"]), hits=[tuple(h) for h in cached["hits"]])
    with _files(row), mock.patch("time.time", return_value=now):
        tape.REPLAY = row["calls"]
        try:
            snap = Snapshot()
            night = snap.night
            b.policy_cache = b.policy(snap, night)
            ctx = skills.Context(b.mem, b.policy_cache, snap.dimension, b.blacklist, prices=b.price_table)
            bans = sum(1 for exp in b.blacklist.values() if exp > now)
            ids = [s["id"] for s in snap.inv.slots]
            b.sig = retry.signature(snap.feet, ids, night, bans)
            b.coarse = retry.signature(snap.feet, ids, night, 0, bin_size=16)
            b.place = retry.place_signature(snap.feet, night)
            pool, filtered = b.candidates(ctx, snap, night, force=row.get("force", False))
            pick = priority.choose(pool, b.committed, held=b.assumptions_hold(snap, night),
                                   here=(snap.state["x"], snap.state["y"], snap.state["z"]))
        finally:
            tape.REPLAY = None
    top = [(c.name, round(c.score, 6)) for c in sorted(pool, key=lambda c: c.score, reverse=True)[:5]]
    return (pick.name if pick else None), top, filtered, pick


def diff(rows):
    """Rounds whose decision differs from the recording: [(time, recorded pick, new pick or 'MISS ...', new top)]."""
    out = []
    for row in rows:
        try:
            name, top, _, _ = decide(row)
        except tape.ReplayMiss as e:
            out.append((row["t"], row["pick"], f"MISS {e}", None))
            continue
        if name != row["pick"]:
            out.append((row["t"], row["pick"], name, top))
    return out


def synth(row, *, add_items=(), remove_items=(), state=None, mem=None, calls=None):
    """A situation made from a recorded one: the same world answers, with the bag, the player state or memory
    edited (a portal already built, food missing, 6 hp in the Nether…). Returns a new row; the source is untouched.
    add_items: [(id, count)], remove_items: ids, state: {field: value}, mem: function(mem_data) editing it in place,
    calls: extra world answers the edited situation needs ({"/entities?radius=10": {"entities": []}})."""
    import copy
    new = copy.deepcopy(row)
    for path, answer in (calls or {}).items():
        new["calls"].setdefault(path, answer)
    if isinstance(new["mem"], str):
        new["mem"] = tape.load_mem(new["mem"])
    inv = new["calls"].get("/inventory")
    if inv is not None:
        inv["slots"] = [s for s in inv["slots"] if s["id"] not in set(remove_items)]
        for item, n in add_items:
            inv["slots"].append({"id": item, "count": n})
    if state and "/state" in new["calls"]:
        new["calls"]["/state"].update(state)
    if mem:
        mem(new["mem"])
    return new


def survival_pick(row):
    """The rescue the survival layer would run in a recorded situation (nothing executed): (name or None)."""
    from . import skills
    from .world import Snapshot
    b = make_brain(row)
    picked = []
    with _files(row), mock.patch("time.time", return_value=row["t"]):
        tape.REPLAY = row["calls"]
        try:
            snap = Snapshot()
            ctx = skills.Context(b.mem, b.policy(snap, snap.night), snap.dimension, b.blacklist, prices=b.price_table)
            b.sig = b.coarse = None
            with mock.patch.object(type(b), "attempt", lambda self, name, fn, cooldown=None: picked.append(name)):
                b.survival(snap, ctx)
        finally:
            tape.REPLAY = None
    return picked[0] if picked else None


def item_for(dim):
    """One concrete item id that satisfies a planner dimension, or None for dimensions that are not items.

    A playthrough has to put something in the bag when a goal is achieved, and goals are written against group
    tokens ("planks", "bed") and tool levels ("tool:pickaxe:1"), not item ids. Any member of the group will do:
    the question a playthrough asks is what the agent does next, and the planner counts members through groups.
    """
    from .data import GROUPS, MATERIAL_TOKEN, TOOL_MATERIALS
    if dim.startswith("tool:"):
        _, kind, tier = dim.split(":")
        material = TOOL_MATERIALS[min(int(tier), len(TOOL_MATERIALS) - 1)]
        return f"minecraft:{material}_{kind}"
    if dim in GROUPS:
        return GROUPS[dim][0] if ":" in GROUPS[dim][0] else f"minecraft:{GROUPS[dim][0]}"
    if ":" in dim:
        return dim
    return None                      # sheltered, slept, at:… — states, not things to hold


def playthrough(row, rounds=12, tick=None, until=None, script=None, slowdown=1.0):
    """Play forward from a recorded round: WHAT the agent does and WHEN. Returns [(seconds from the start, name)].

    `simulate` replays the same world over and over — it answers "does it get stuck". This answers the question no
    offline test could ask before: does the agent do things in a sensible ORDER. Nothing is executed; the world
    moves the way the plan says it will:

      * the clock advances by what the chosen candidate was estimated to cost (the action table's own seconds, so
        walking, mining and crafting are each modelled by the column that priced them);
      * whatever the goal was for lands in the bag, and the goal is then done;
      * hunger falls with the clock, sleeping puts the sun back up, eating fills the bar.

    That is enough to show the order, which is what the objective decides and what kept going wrong: tools before
    luxuries, shelter before dusk, food before starving.

    Each entry is (started, name, finished) in seconds from the start, so a milestone can be timed by when the
    round that reached it ENDED rather than when it began.

    `until(row)` stops the playthrough when a milestone is reached, so a test can ask HOW LONG something took, not
    only what order it came in. `script(name, i)` returns True (it worked) or an exception (it did not) — the same
    shape `simulate` uses — and `slowdown` multiplies every estimate. Three scripts is all the uncertainty this
    needs: everything works, the measured failures happen, everything takes twice as long. No distributions are
    sampled: the point is that failure and delay travel through the real回路 (cooldown, re-plan, commitment), and
    that the milestone is still reached, not what the variance of the arrival time is.
    """
    from .brain import goals as open_goals
    from .actions import target_of
    from .survival import CONFIG as _PLAY
    b = make_brain(row)
    t0 = t = row["t"]
    out = []
    was_lenient, tape.LENIENT = tape.LENIENT, True   # the life walks off the edge of the recording; see tape
    try:
        return _play(b, row, rounds, tick, until, script, slowdown, t0, t, out)
    finally:
        tape.LENIENT = was_lenient


def _play(b, row, rounds, tick, until, script, slowdown, t0, t, out):
    from .brain import goals as open_goals
    from .actions import target_of
    from .survival import CONFIG as _PLAY
    from .brain import IDLE_LIMIT
    idle_since = None
    for i in range(rounds):
        if until is not None and until(row):
            break
        # The idle rule, as in `Brain.round`: after IDLE_LIMIT with nothing runnable, cooling work comes back. A
        # playthrough without it reports an idle round the live agent would never have had.
        row["force"] = idle_since is not None and t - idle_since >= IDLE_LIMIT
        name, _top, _filtered, pick = decide(row, b, now=t)
        entry = [round(t - t0, 1), name, round(t - t0, 1)]
        out.append(entry)
        if pick is None:
            idle_since = idle_since or t
            t += 5.0
            entry[2] = round(t - t0, 1)
            continue
        idle_since = None
        b.committed = name
        spent = max(1.0, min(pick.cost / 20.0, 1200.0)) * float(slowdown)
        if script is not None:
            result = script(name, i)
            if result is not True:
                # It failed. The failure goes through the brain's own retry policy, and nothing is produced — the
                # clock still moves, because a failed attempt costs time too.
                with mock.patch("time.time", return_value=t):
                    b.failed(pick.key, result)
                    if "/" in (pick.key or ""):
                        b.failed("step:" + pick.key.split("/", 1)[1], result)
                t += max(2.0, min(spent, 30.0))
                entry[2] = round(t - t0, 1)
                if tick:
                    tick(row, name, t - t0)
                continue
        add, state = [], {}
        with _files(row), mock.patch("time.time", return_value=t):
            tape.REPLAY = row["calls"]
            try:
                from .world import Snapshot
                snap = Snapshot()
                goal = next((g for g in open_goals(snap, b.mem) if g.name == name), None)
                held = snap.state
            finally:
                tape.REPLAY = None
        if goal is not None:
            for dim, n in target_of(goal.needs).items():
                item = item_for(dim)
                if item:
                    add.append((item, int(n)))
        food = float(held.get("food", 20)) - spent / float(_PLAY["risk"]["food_drain_s"])
        if "eat" in (name or ""):
            food = 20.0
        state["food"] = max(0.0, min(20.0, food))
        day = float(_PLAY["time"]["day_s"]) * 20.0
        clock = (float(held.get("timeOfDay", 0)) + spent * 20.0) % day
        state["timeOfDay"] = 1000.0 if "sleep" in (name or "") else clock
        before_bag = sorted((sl["id"], sl["count"]) for sl in row["calls"].get("/inventory", {}).get("slots", []))
        row = synth(row, add_items=add, state=state)
        # A tool with no durability is not a tool: `Inventory.tools` reads maxDamage − damage, so a pickaxe added
        # with neither field counted as broken and the goal that had just produced it stayed open forever (twelve
        # rounds of "iron sword" in a row, each one making another one).
        from .actions import TOOL_USES
        from .data import bare
        for slot in row["calls"].get("/inventory", {}).get("slots", []):
            material, _, kind = bare(slot["id"]).rpartition("_")
            if material in TOOL_USES and not slot.get("maxDamage"):
                slot["maxDamage"], slot["damage"] = TOOL_USES[material], 0
        # The live brain's own spin guard (`Brain._attempt`): succeeding twice without changing anything is a
        # failure. A goal whose product is not a thing — a bucket of water, a lit room — otherwise repeats forever
        # here, which says nothing about the planner and hides everything after it.
        after_bag = sorted((sl["id"], sl["count"]) for sl in row["calls"].get("/inventory", {}).get("slots", []))
        if after_bag == before_bag:
            if getattr(b, "last_quick", None) == name:
                from .api import McError
                with mock.patch("time.time", return_value=t):
                    b.failed(pick.key, McError("no progress (succeeded twice without changing anything)"))
            b.last_quick = name
        else:
            b.last_quick = None
        t += spent
        entry[2] = round(t - t0, 1)
        if tick:
            tick(row, name, t - t0)
    return [tuple(e) for e in out]


def reached(row, *items):
    """True when the bag holds all of `items` (ids or group tokens) — a milestone for `playthrough(until=…)`."""
    from .data import GROUPS
    slots = row["calls"].get("/inventory", {}).get("slots", [])
    for want in items:
        ids = set(GROUPS.get(want, [want]))
        ids |= {i if ":" in i else f"minecraft:{i}" for i in ids}
        if not any(s["id"] in ids for s in slots):
            return False
    return True


def simulate(row, outcome, rounds=200):
    """Replay one recorded world `rounds` times. `outcome(pick_name, i)` returns True (success) or an exception
    instance (that failure is recorded through the brain's retry policy). Returns [(time, pick name)]."""
    from .brain import IDLE_LIMIT
    b = make_brain(row)
    t = row["t"]
    picks = []
    idle_since = None
    for i in range(rounds):
        # The idle rule as in Brain.round: after IDLE_LIMIT with nothing runnable, cooling fallbacks come back.
        row["force"] = idle_since is not None and t - idle_since >= IDLE_LIMIT
        name, _, _, pick = decide(row, b, now=t)
        picks.append((t, name, pick.key if pick else None))
        if pick is None:
            idle_since = idle_since or t
            t += 5
            continue
        idle_since = None
        result = outcome(name, i)
        with mock.patch("time.time", return_value=t):
            if result is True:
                b.retry.succeeded(pick.key)
            else:
                b.failed(pick.key, result)
                if "/" in pick.key:        # same shared step cooldown as Brain._attempt
                    b.failed("step:" + pick.key.split("/", 1)[1], result)
        b.committed = name
        # A failure usually comes within a minute (no path, nothing found); advancing by the full estimate (26 700
        # ticks) jumped past every backstop and hid nothing but the clock.
        t += max(2.0, min(pick.cost / 20, 30.0))
    return picks


def longest_run(picks, name):
    """Pure: the most consecutive rounds `name` was picked."""
    best = cur = 0
    for p in picks:
        cur = cur + 1 if p[1] == name else 0
        best = max(best, cur)
    return best


def step_rotation(picks, window=6):
    """Pure: the most times one plan step (the key without its goal) was attempted within `window` consecutive
    rounds, across goals — the "different goals take turns failing the same step" loop."""
    steps = [p[2].split("/", 1)[1] if p[2] and "/" in p[2] else None for p in picks]
    best = 0
    for i in range(len(steps)):
        win = [s for s in steps[i:i + window] if s]
        if win:
            best = max(best, max(win.count(s) for s in set(win)))
    return best


def _clear_caches(brain, loot):
    """Caches skip world queries (chest scan once a minute, plans reused while the bag is unchanged): a recording
    made on a cache hit lacks those queries and its replay misses. Recording and replay both start cold."""
    loot._CACHE.update(t=0, pos=None, hits=[])
    brain.plan_cache = {}


def record_live(brain, force=False):
    """The decision the brain would make now, recorded (not executed). Returns the tape row."""
    from . import loot, priority, retry, skills
    from .world import Snapshot
    import time
    _clear_caches(brain, loot)
    tape.begin()
    snap = Snapshot()
    night = snap.night
    brain.policy_cache = brain.policy(snap, night)
    ctx = skills.Context(brain.mem, brain.policy_cache, snap.dimension, brain.blacklist, prices=brain.price_table)
    now = time.time()
    ids = [s["id"] for s in snap.inv.slots]
    brain.sig = retry.signature(snap.feet, ids, night, sum(1 for e in brain.blacklist.values() if e > now))
    brain.coarse = retry.signature(snap.feet, ids, night, 0, bin_size=16)
    pool, filtered = brain.candidates(ctx, snap, night, force=force)
    pick = priority.choose(pool, brain.committed, held=True)
    row = tape.row_for(brain, pick, pool, filtered, force)
    tape._calls = None
    return row


def save_golden(name, row, expect_pick=None, expect_filtered=None):
    """Freeze a situation: tests/golden/<name>.json (memory inlined).

    `expect_pick` is recorded as `was` — what this code chose the day it was frozen. It is printed, never
    asserted: the assertions are that the round still replays and that the pick is the best of its pool.
    """
    os.makedirs(GOLDEN, exist_ok=True)
    frozen = dict(row, mem=tape.load_mem(row["mem"]) if isinstance(row["mem"], str) else row["mem"],
                  was=expect_pick, expect_filtered=expect_filtered or {})
    with open(os.path.join(GOLDEN, name + ".json"), "w") as f:
        json.dump(frozen, f, default=str)
    return os.path.join(GOLDEN, name + ".json")


def golden_results():
    """[(name, ok, message)] for every golden situation under the current code.

    A golden asserts that the situation still DECIDES, not that it decides the same thing. Two questions:

      * can the round be replayed at all — a decision that cannot be replayed cannot be judged by anything;
      * is the pick the best of the pool under the pricing in force — self-consistency, which breaks the moment
        the chooser and the scorer disagree.

    What it deliberately does NOT assert is the name. The recorded pick is kept as `was` and printed, because a
    change there is worth a look and is not by itself a fault: better prices pick different things, and a test
    that forbids that turns every improvement red and gets edited into agreement. `expect_filtered` stays an
    assertion — "this candidate is refused, for this reason" is a fact about the situation, not about the ranking.
    """
    out = []
    if not os.path.isdir(GOLDEN):
        return out
    for fn in sorted(os.listdir(GOLDEN)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(GOLDEN, fn)) as f:
            row = json.load(f)
        try:
            name, top, filtered, _ = decide(row)
        except tape.ReplayMiss as e:
            out.append((fn[:-5], False, f"replay miss: {e}"))
            continue
        ok, why = True, f"picked {name}; top {top[:3]}"
        scores = [s for _n, s, *_ in top]
        if name is not None and scores and max(scores) - scores[0] > 1e-6:
            ok, why = False, f"picked {name} at {scores[0]:.6f} while {max(scores):.6f} was on offer: {top[:3]}"
        for cand, why_part in (row.get("expect_filtered") or {}).items():
            if why_part not in str(filtered.get(cand, "")):
                ok, why = False, f"{cand}: expected refusal {why_part!r}, got {filtered.get(cand)!r}"
        was = row.get("was", row.get("expect_pick"))
        if was and was != name:
            why += f" (was {was!r} when recorded)"
        out.append((fn[:-5], ok, why))
    return out
