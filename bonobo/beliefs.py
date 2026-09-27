"""What this agent believes about the world, in one place, with how much each belief is worth trusting. Every number the planner uses is a belief about Minecraft: how far a skeleton shoots, what a death costs, how much of the damage a shield removes. They were spread across two TOML files and two Python tables, and the same fact was written down twice — a skeleton's reach was 15 in `play.toml` and 3 in `combat_model.HAZARD_R`, a death cost 240 s in ordinary play and 120 s in a fight. Nobody wrote a bug: two copies of one fact drift, always. So: one table, read through one door. value("time.death_cost_s")   → the number belief("time.death_cost_s")  → (number, observations behind it) mob("minecraft:skeleton")    → the row `observations` is 0 for everything today — every number here is a declared guess, which is what `unmeasured` says. The count is in the interface now, before anything reads it, because the thing that will read it (a planner that explores when it is unsure) must not require changing every call site again to arrive. `fit` raises the count as recorded play accumulates; until it does, nothing should pretend to know more than it does. Measurements are WRITTEN DOWN as they are taken (`note`), to `MC_DATA/beliefs.jsonl`, and read back at import. A count that lives only in one process is not an observation count: the bench measured a route, the run ended, and the next round believed the prior again. One line per measurement, never rewritten, so the history is the record and the count is derived from it — a wrong fit can be re-derived, a wrong average cannot be undone. This module imports one thing, `paths`, which is where a filesystem layout lives. It still depends on no decision: facts do not depend on decisions."""

import json
import os
import tomllib

from . import paths

CONFIG_PATH = os.environ.get("MC_PLAY_CONFIG", os.path.join(os.path.dirname(__file__), "play.toml"))

with open(CONFIG_PATH, "rb") as _f:
    CONFIG = tomllib.load(_f)

WIKI_FIELDS = ("hp", "attack", "notice_r")
WIKI_N = 10 ** 6          # "known", in the same unit as an observation count, so one comparison works everywhere

def _with_dps(row):
    """`dps` is derived, never stored: a published hit divided by how often it lands."""

    out = dict(row)
    out["dps"] = row["attack"] / row["attack_s"]
    return out

MOBS = {kind: _with_dps(row) for kind, row in CONFIG["mobs"].items()}
PLAYER = CONFIG["player"]
# The numbers nobody has measured. Named here so a plan can carry the list and nobody mistakes a ranking built on
# them for a measurement.
UNMEASURED = tuple(CONFIG["tools"].get("unmeasured", ()))

# Observation counts, keyed the same way as `value`, and the measurements behind them. Filled from the log at
# import; `note` adds to both and appends the line.
COUNTS = {}
OBSERVED = {}
LOG = paths.data("beliefs.jsonl", env="MC_BELIEFS")

# How much a declared number is discounted while nothing has been measured. One observation is worth this many
# "prior" observations' worth of doubt: with n = 0 a benefit is read at half, and it climbs toward the declared
# value as the count grows. It also weighs the prior against the measurements in `value`: the number moves as the count grows.
PRIOR_STRENGTH = 1.0

def declared(path):
    """The number as WRITTEN DOWN in play.toml: a guess, or something Mojang publishes. No measurement in it."""
    if path.startswith("mobs."):
        _, kind, field = path.split(".", 2)
        return MOBS[kind][field]
    section, _, key = path.partition(".")
    return CONFIG[section][key]

def value(path):
    """The believed number at "section."""

    prior = declared(path)
    if not is_unmeasured(path):
        return prior
    seen = [m for m, _at in OBSERVED.get(path, ())]
    if not seen:
        return prior
    return (PRIOR_STRENGTH * float(prior) + len(seen) * _median(seen)) / (PRIOR_STRENGTH + len(seen))

def is_unmeasured(path):
    """Is this one of the numbers `play.toml` declares as a guess? Only those are ours to move."""
    return path.rsplit(".", 1)[-1] in UNMEASURED

def _median(xs):
    """The middle measurement, not the mean: one round that walked into a wall should not be able to drag a belief, and there is no theory here to fit — only what happened."""

    s = sorted(float(x) for x in xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0

def count(path):
    """How many observations stand behind this belief."""

    if path.startswith("mobs.") and path.rsplit(".", 1)[-1] in WIKI_FIELDS:
        return WIKI_N
    return int(COUNTS.get(path, 0))

def belief(path):
    return value(path), count(path)

# What play has actually measured, keyed like `value`: [(observed, when)]. The bench writes here from the residual
# between what an estimator said and what the clock said; `fit` reads it. Nothing is overwritten in `CONFIG` — a
# belief moves when there are enough observations to move it, and that decision is not this module's.
OBSERVED = {}

def note(path, measured, now=None, where=""):
    """Record one measurement of a believed number, and return (believed, observations)."""

    import time as _time
    believed = value(path)          # what was believed BEFORE this measurement joined the history
    at = now if now is not None else _time.time()
    OBSERVED.setdefault(path, []).append((float(measured), at))
    COUNTS[path] = int(COUNTS.get(path, 0)) + 1
    _append({"path": path, "measured": float(measured), "believed": believed,
             "n": COUNTS[path], "at": at, "where": where})
    return belief(path)

# Measurements arrive at the speed of the world — one per broken block while mining — so they are written in
# batches rather than one file open per block. Nothing is dropped: the buffer is part of the history until it is
# on disk, and it is flushed when it fills, when it gets old, and when the process ends.
_PENDING = []
FLUSH_EVERY = 25
FLUSH_AFTER_S = 30.0
_last_flush = 0.0

def _append(row):
    """Queue one line."""

    import time as _time
    global _last_flush
    # The row remembers WHERE it is to be written. A test points LOG at a temporary file, takes a measurement and
    # puts LOG back; without this the batch lands wherever LOG happens to be at flush time, and a test's invented
    # numbers end up in the player's real history — which is exactly what happened.
    _PENDING.append((LOG, row))
    now = _time.time()
    if len(_PENDING) >= FLUSH_EVERY or now - _last_flush >= FLUSH_AFTER_S:
        _last_flush = now
        flush()

def flush():
    """Write the queued measurements out. Safe to call at any time; it is what `atexit` calls."""
    if not _PENDING:
        return 0
    written = 0
    for target in dict.fromkeys(path for path, _row in _PENDING):
        rows = [row for path, row in _PENDING if path == target]
        try:
            paths.ensure(target)
            with open(target, "a") as out:
                for row in rows:
                    out.write(json.dumps(row) + "\n")
            written += len(rows)
        except OSError as err:
            print(f"?? beliefs: {type(err).__name__} writing {target}: {len(rows)} measurements are not in it")
    _PENDING.clear()
    return written

import atexit          # noqa: E402  (registered after `flush` exists, which is the only order that works)

atexit.register(flush)

def load(path=None):
    """Read the measurement log back into OBSERVED/COUNTS."""

    path = path or LOG
    if not os.path.exists(path):
        return 0
    read = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                value(row["path"])
            except (ValueError, KeyError):
                continue
            OBSERVED.setdefault(row["path"], []).append((float(row["measured"]), float(row.get("at", 0.0))))
            COUNTS[row["path"]] = int(COUNTS.get(row["path"], 0)) + 1
            read += 1
    return read

load()

def observed(path):
    """Every measurement of this belief, newest last."""
    return list(OBSERVED.get(path, ()))

def mob(kind):
    """One mob's row: `reach` (how far it hurts), `keep_out` (how close movement may plan), dps, speed, hp."""
    return MOBS[kind]

def fights_back(types):
    """Does any of these mob types hit back (a row in the table: its dps is known)? Animals do not."""
    return any(t in MOBS for t in types or ())

def keep_out():
    """{kind: radius} movement refuses to plan inside. A view of the table, never a second copy of it."""
    return {kind: m["keep_out"] for kind, m in MOBS.items() if m.get("keep_out")}

def protection(armor_points, shield=False):
    """Fraction of incoming damage removed: armour points (0–20, as /state reports them) and a shield in hand."""

    return min(PLAYER["protection_cap"],
               armor_points * PLAYER["protection_per_point"] + (PLAYER["shield"] if shield else 0.0))

def slot_cost_s(bag_free):
    """Seconds one more occupied inventory slot costs, given how many are still free."""

    free = max(1.0, float(bag_free))
    return float(CONFIG["plan"]["slot_fill_s"]) / (free * free)

def cautious(path, direction="benefit"):
    """The pessimistic end of a belief: what to use when being wrong is not symmetric."""

    v, n = belief(path)
    trust = float(n) / (float(n) + PRIOR_STRENGTH)
    if direction == "cost":
        return float(v) * (2.0 - trust)
    return float(v) * (0.5 + 0.5 * trust)

def slots_cost_s(slots, free):
    """What `slots` more occupied slots cost, each priced against the bag as it will be by then."""

    free = float(free)
    return sum(slot_cost_s(max(1.0, free - i)) for i in range(int(slots)))

TICKS_PER_S = 20.0      # the game's clock, in one place

