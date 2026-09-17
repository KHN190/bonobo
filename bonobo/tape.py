"""Decision tape: the world queries one brain round reads, recorded live and replayed offline.

Live (autoplay, decision scenarios): a round that reaches the scored pool writes one line to decisions.jsonl — the
GET responses it read, the inputs the brain holds (retry state, committed goal, idle clock, blacklist, the files it
reads) and what it picked. Offline (`decide.py`): the round runs again with every GET served from that line — new
code, old world — so a changed decision shows up as a diff without the game. A decision never acts: a POST during
replay is an error, and so is a query the recording doesn't have (new code asking something new).
"""
import hashlib
import json
import os
import time
from . import paths

FILE = paths.data("decisions.jsonl", env="MC_DECISIONS")
MEM_DIR = paths.data("decisions-mem")
MAX_BYTES = 2 * 1024 * 1024        # one file, 2 MB: the newest rounds are kept, older ones dropped
MIN_GAP_S = 20          # record at most one round per 20 s unless the pick changed (regions make lines big)

_calls = None
_last = {"t": 0, "pick": None}
REPLAY = None


class ReplayMiss(Exception):
    """The replayed decision asked something the recording doesn't have, or tried to act."""


def begin():
    global _calls
    _calls = {}


def recorded(method, path, response):
    if _calls is not None and REPLAY is None and method == "GET" and not path.startswith(("/task", "/status")):
        _calls.setdefault(path, response)


# A playthrough walks the world forward, so it asks questions the recording never asked (a region three blocks
# further on, a chest that now matters). Strictness is right for replaying ONE round — the answer must be the one
# the agent really got — and wrong for playing a life forward, where an unknown corner is simply unknown. When this
# is on, a miss answers "nothing there" instead of aborting the round.
LENIENT = False
# Shaped like the real answers, because callers read their fields directly: a region has a palette and a grid, an
# entity query has a list. "Nothing there" must still be a well-formed nothing.
_EMPTY = {"blocks": [], "entities": [], "slots": [], "tasks": [], "palette": ["minecraft:air"],
          "data": [], "size": [0, 0, 0], "items": [], "notes": [], "count": 0}


def replayed(method, path):
    if method != "GET":
        raise ReplayMiss(f"{method} {path}: a decision must not act")
    if path not in REPLAY:
        if LENIENT:
            return dict(_EMPTY)
        raise ReplayMiss(path)
    return REPLAY[path]


# -- encoding of the brain's own decision inputs (tuples and frozensets aren't JSON)
def encode_sig(sig):
    if sig is None:
        return None
    pos, ids, night, bans = sig
    return [list(pos), sorted(ids), night, bans]


def decode_sig(v):
    """Recordings made before signatures dropped the blacklist size carry it in slot 4; replays normalise it to 0 so
    a cooling step isn't mistaken for a changed state (13 of 51 replayed picks differed only because of that)."""
    if v is None:
        return None
    return tuple(v[0]), frozenset(v[1]), v[2], 0


def encode_retry(r):
    return {"entries": {k: {**e, "state": encode_sig(e.get("state"))} for k, e in r.entries.items()},
            "holds": dict(r.holds)}


def decode_retry(d, r):
    r.entries = {k: {**e, "state": decode_sig(e.get("state"))} for k, e in d["entries"].items()}
    r.holds = dict(d["holds"])


def store_mem(data):
    blob = json.dumps(data, sort_keys=True, default=str)
    h = hashlib.sha1(blob.encode()).hexdigest()[:12]
    os.makedirs(MEM_DIR, exist_ok=True)
    path = os.path.join(MEM_DIR, h + ".json")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(blob)
    return h


def load_mem(h):
    with open(os.path.join(MEM_DIR, h + ".json")) as f:
        return json.load(f)


# What else belongs on a decision line, registered by whoever owns it. The recorder used to import the four
# modules whose state it wanted — a file that exists to WATCH the others reached upward into them, which put loot,
# and through it nav and perception, into the dependency closure of everything that records anything. Now the top
# wires it (`brain` calls `register` at start-up) and this module imports nothing above `paths`.
SOURCES = {}
FILES = {}


def register(name, snapshot=None, file_path=None):
    """`snapshot()` → JSON-able extra for each row, or `file_path()` → a path whose text is recorded."""
    if snapshot is not None:
        SOURCES[name] = snapshot
    if file_path is not None:
        FILES[name] = file_path


def _extras():
    out = {}
    for name, fn in SOURCES.items():
        try:
            out[name] = fn()
        except Exception:
            out[name] = None
    return out


def _files():
    out = {}
    for name, path in FILES.items():
        try:
            with open(path() if callable(path) else path) as f:
                out[name] = f.read()
        except OSError:
            out[name] = None
    return out


def row_for(brain, pick, pool, filtered, force, now=None):
    """The decision line (pure apart from reading the files the pool reads)."""
    files = _files()
    return {
        "t": now or time.time(), "calls": dict(_calls or {}), "mem": store_mem(brain.mem.data), "files": files,
        # Cached world reads the round didn't re-query (chest scan once a minute): without them 33 of 36 replays
        # missed "/find?blocks=minecraft:chest…". The replay restores the cache instead of querying.
        **_extras(),
        "retry": encode_retry(brain.retry), "committed": brain.committed, "idle_since": brain.idle_since,
        "stalled": brain.stalled_seconds(), "force": force,
        "recent_fail": list(brain.recent_fail) if brain.recent_fail else None,
        "fail_sig": {k: encode_sig(v) for k, v in brain.fail_sig.items()},
        "blacklist": [[list(k), v] for k, v in brain.blacklist.items()],
        "pick": pick.name if pick else None,
        "top": [(c.name, round(c.score, 6)) for c in sorted(pool, key=lambda c: c.score, reverse=True)[:5]],
        "filtered": filtered,
    }


def trim(path, keep_bytes):
    """Keep the newest rounds that fit, drop the rest. One file, no second copy: what a replay is worth is in the
    recent rounds, and older ones are the same situations again."""
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return 0
    kept, size = [], 0
    for line in reversed(lines):
        size += len(line.encode())
        if size > keep_bytes:
            break
        kept.append(line)
    kept.reverse()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.writelines(kept)
        os.replace(tmp, path)
    except OSError:
        return 0
    return len(kept)


def end(brain, pick, pool, filtered, force, path=None, always=False):
    """Write this round's decision line (throttled). Returns the row, or None when skipped."""
    global _calls
    if _calls is None:
        return None
    now = time.time()
    name = pick.name if pick else None
    if not always and now - _last["t"] < MIN_GAP_S and name == _last["pick"]:
        _calls = None
        return None
    row = row_for(brain, pick, pool, filtered, force, now)
    _calls = None
    _last.update(t=now, pick=name)
    path = path or FILE
    try:
        line = json.dumps(row, default=str) + "\n"
        if os.path.exists(path) and os.path.getsize(path) + len(line) > MAX_BYTES:
            trim(path, MAX_BYTES // 2)
        with open(path, "a") as f:
            f.write(line)
    except OSError:
        pass
    return row
