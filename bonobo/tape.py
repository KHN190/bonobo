"""Round tape: what one brain round saw, held and did — recorded live, read offline. Each line: the snapshot (state + bag), the world GETs the round made, the memory it held, the task and the plan it worked from, the act it chose, the EVENTS of the round (plan made or repaired, step ok / failed / interrupted), and every post-action READING SEQUENCE a skill judged (`settle`: what the counter said, poll by poll, and the verdict). The reading sequences are what gets replayed: the judgment layer (settle, death, outcome classification) run again on the very numbers the game gave, so a timing bug seen once is reproduced forever — and they are real conditions for the scenario tables. Decisions are not replayed: the brain is a fixed order now, and a change to it would only invalidate the recordings. `recorded`/`replayed` keep serving world reads from a line (`REPLAY`) for whoever wants a round's world back."""

import hashlib
import json
import os
import time
from . import paths

FILE = paths.data("rounds.jsonl", env="MC_TAPE")
MEM_DIR = paths.data("tape-mem")         # memory snapshots the rounds point at (by hash)
MAX_BYTES = 2 * 1024 * 1024        # one file, 2 MB: the newest rounds are kept, older ones dropped
MIN_GAP_S = 20          # a quiet round with the same act as the last is recorded at most every 20 s

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

# lenient replay answers a miss with "nothing there" (playing forward); strict is for replaying one round
LENIENT = False
# shaped like the real answers: callers read their fields directly
_EMPTY = {"blocks": [], "entities": [], "slots": [], "tasks": [], "palette": ["minecraft:air"],
          "data": [], "size": [0, 0, 0], "items": [], "notes": [], "count": 0}

def replayed(method, path):
    if method != "GET":
        raise ReplayMiss(f"{method} {path}: a decision must not act")
    if REPLAY is None or path not in REPLAY:
        if LENIENT:
            return dict(_EMPTY)
        raise ReplayMiss(path)
    return REPLAY[path]

def store_mem(data):
    blob = json.dumps(data, sort_keys=True, default=str)
    h = hashlib.sha1(blob.encode()).hexdigest()[:12]
    os.makedirs(MEM_DIR, exist_ok=True)
    path = os.path.join(MEM_DIR, h + ".json")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(blob)
    return h

# what else a decision line carries, registered from the top (brain), so the recorder imports nothing above `paths`
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

_events = []
_readings = []
SKILL = None           # the skill running now (skill.py sets it): whose readings these are

def event(name, outcome, detail=""):
    """Something that happened this round: a plan made or repaired, a step's outcome."""
    _events.append({"t": round(time.time(), 2), "name": name, "outcome": outcome, "detail": detail})

def reading(seq, verdict, label=None):
    """One post-action reading sequence [(seconds since the action, value)] and what it was judged to mean."""
    _readings.append({"skill": label or SKILL, "seq": [[round(dt, 2), _plain(v)] for dt, v in seq],
                      "verdict": _plain(verdict)})

def _plain(v):
    return v if isinstance(v, (int, float, str, bool, type(None))) else str(v)

def row_for(brain, act, snap, now=None):
    """The round's line (pure apart from reading the memory it stores)."""
    task = getattr(act, "task", None)
    held = brain.held.get(task["id"]) if task else None
    return {
        "t": now or time.time(), "calls": dict(_calls or {}), "mem": store_mem(brain.mem.data),
        **_extras(),
        "snap": {"state": snap.state, "slots": snap.inv.slots, "equipment": snap.inv.equipment} if snap else None,
        "task": task, "plan": [str(s) for s in held["steps"]] if held else None,
        "act": repr(act) if act else None, "step": str(act.step) if act is not None and act.step else None,
        "events": list(_events), "readings": list(_readings),
        "blacklist": [[list(k), v] for k, v in brain.blacklist.items()],
    }

def trim(path, keep_bytes):
    """Keep the newest rounds that fit, drop the rest."""

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

def end(brain, act, snap, path=None, always=False):
    """Write this round's line (throttled: the same act with nothing new to say is skipped). Returns the row."""
    global _calls
    if _calls is None:
        return None
    now = time.time()
    name = repr(act) if act else None
    quiet = not _events and not _readings
    if not always and quiet and now - _last["t"] < MIN_GAP_S and name == _last["pick"]:
        _calls = None
        return None
    row = row_for(brain, act, snap, now)
    _calls = None
    _events.clear()
    _readings.clear()
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
