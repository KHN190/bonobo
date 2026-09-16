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
            ctx = skills.Context(b.mem, b.policy_cache, snap.dimension, b.blacklist)
            bans = sum(1 for exp in b.blacklist.values() if exp > now)
            ids = [s["id"] for s in snap.inv.slots]
            b.sig = retry.signature(snap.feet, ids, night, bans)
            b.coarse = retry.signature(snap.feet, ids, night, 0, bin_size=16)
            pool, filtered = b.candidates(ctx, snap, night, force=row.get("force", False))
            pick = priority.choose(pool, b.committed, b.stalled_seconds())
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
            ctx = skills.Context(b.mem, b.policy(snap, snap.night), snap.dimension, b.blacklist)
            b.sig = b.coarse = None
            with mock.patch.object(type(b), "attempt", lambda self, name, fn, cooldown=None: picked.append(name)):
                b.survival(snap, ctx)
        finally:
            tape.REPLAY = None
    return picked[0] if picked else None


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
    ctx = skills.Context(brain.mem, brain.policy_cache, snap.dimension, brain.blacklist)
    now = time.time()
    ids = [s["id"] for s in snap.inv.slots]
    brain.sig = retry.signature(snap.feet, ids, night, sum(1 for e in brain.blacklist.values() if e > now))
    brain.coarse = retry.signature(snap.feet, ids, night, 0, bin_size=16)
    pool, filtered = brain.candidates(ctx, snap, night, force=force)
    pick = priority.choose(pool, brain.committed, brain.stalled_seconds())
    row = tape.row_for(brain, pick, pool, filtered, force)
    tape._calls = None
    return row


def save_golden(name, row, expect_pick=None, expect_filtered=None):
    """Freeze a situation with its expected decision: tests/golden/<name>.json (memory inlined)."""
    os.makedirs(GOLDEN, exist_ok=True)
    frozen = dict(row, mem=tape.load_mem(row["mem"]) if isinstance(row["mem"], str) else row["mem"],
                  expect_pick=expect_pick, expect_filtered=expect_filtered or {})
    with open(os.path.join(GOLDEN, name + ".json"), "w") as f:
        json.dump(frozen, f, default=str)
    return os.path.join(GOLDEN, name + ".json")


def golden_results():
    """[(name, ok, message)] for every golden situation under the current code."""
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
        ok = row.get("expect_pick") is None or name == row["expect_pick"]
        for cand, why_part in (row.get("expect_filtered") or {}).items():
            ok = ok and why_part in str(filtered.get(cand, ""))
        out.append((fn[:-5], ok, f"picked {name}; top {top[:3]}"))
    return out
