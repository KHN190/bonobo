"""The cerebrum's door: a wanted state, and what it is worth in seconds (docs/api.md).

Claude does not drive the body and does not name skills. It says WHAT STATE it wants and HOW MANY SECONDS that
state is worth; the want enters `gates.V` as a temporary terminal dimension, so it competes with the bed and the
pickaxe on the one scale the planner has, and the planner chooses the route itself.

Three rules make that safe, and they are the whole module:
  * seconds or nothing — a caller who cannot price a want cannot ask for it;
  * everything expires — no instruction outlives the situation that motivated it;
  * a deadline only discounts and a scope only narrows — neither can make an infeasible thing feasible.

`priority` and `directives` are sugar over this: a nudge is extra seconds on a want, a goto or a skill is a want
with a scope. Neither keeps a queue of its own.
"""
import math
import time
import uuid

from . import api, knowledge, paths, survival

FILE = paths.data("wants.json", env="MC_WANTS")
DEFAULT_EXPIRES_S = 600.0
HORIZON_S = 600.0

TOOL_KINDS = ("pickaxe", "sword", "axe", "shovel", "hoe")
STATES = ("pending", "chosen", "running", "met", "expired", "stopped")

_wants = {}


class Want:
    """One wanted state. Mutable: the planner writes back what it made of it, the clock ages it."""

    __slots__ = ("id", "want", "worth_s", "deadline_s", "expires_s", "scope", "note", "created",
                 "state", "chosen", "blocked_by")

    def __init__(self, id, want, worth_s, deadline_s, expires_s, scope, note):
        self.id, self.want, self.worth_s = id, want, worth_s
        self.deadline_s, self.expires_s, self.scope, self.note = deadline_s, expires_s, scope, note
        self.created, self.state, self.chosen, self.blocked_by = time.time(), "pending", None, None

    def age_s(self):
        return max(0.0, time.time() - self.created)

    def expires_in_s(self):
        return self.expires_s - self.age_s()

    def alive(self):
        return self.state not in ("stopped", "met") and not self.state.startswith("refused") \
            and self.expires_in_s() > 0

    def reading(self):
        state = self.state if self.alive() or self.state not in ("pending", "chosen", "running") else "expired"
        return {"id": self.id, "want": dict(self.want), "worth_s": self.worth_s, "priced_s": priced_s(self),
                "chosen": self.chosen, "blocked_by": self.blocked_by, "age_s": round(self.age_s(), 3),
                "expires_in_s": round(self.expires_in_s(), 3), "state": state}

    def __repr__(self):
        return f"Want({self.id}, {self.want}, {self.worth_s}s, {self.state})"


# ------------------------------------------------------------------ what a want may say

def _producible():
    """Every token anything in the skill table can end up holding. A want outside this is refused, with a reason:
    an unreachable ask is answered, never left to look like a decision the planner made."""
    out = set()
    for table in (knowledge.RECIPES, knowledge.GROUP_RECIPES, knowledge.MINE_YIELD, knowledge.SMELTS,
                  knowledge.HUNT_YIELD, knowledge.STATIONS):
        out |= set(table)                             # every one of these is keyed by what it produces
    for entry in knowledge.TAKEABLE.values():
        out |= set(entry.get("gives") or ())
    return {_token(t) for t in out}


def _token(name):
    return name if ":" in name else "minecraft:" + name


def _check_want(want):
    """The want as an effect on the state vector, or an exception saying why it is not one."""
    if not isinstance(want, dict) or not want:
        raise TypeError(f"a want is a non-empty effect on the state vector, not {want!r}")
    effect = {}
    for key, count in want.items():
        if not isinstance(key, str):
            raise TypeError(f"a want names a dimension, not {key!r}")
        if not isinstance(count, (int, float)) or isinstance(count, bool) or count <= 0:
            raise ValueError(f"{key}: a want asks for a positive amount, not {count!r}")
        if key.startswith("end:"):
            dim = key[4:]
            if dim not in survival.END_DIMS:
                raise ValueError(f"{key}: no such terminal dimension {sorted(survival.END_DIMS)}")
            effect[survival.END_DIMS[dim]] = count
        elif key.startswith("tool:"):
            kind = key.split(":")[1]
            if kind not in TOOL_KINDS:
                raise ValueError(f"{key}: no such tool {TOOL_KINDS}")
            effect[f"tool:{kind}:1"] = count
        else:
            effect[_token(key)] = count
    return effect


def _seconds(name, value, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} is seconds, not {value!r}")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} is a finite positive number of seconds, not {value!r}")
    return value


# ------------------------------------------------------------------ the door

def offer(want, worth_s, *, deadline_s=None, expires_s=DEFAULT_EXPIRES_S, scope=None, note="", id=None):
    """Ask for a state and say what it is worth. Returns the `Want`, refused ones included — with the reason."""
    effect = _check_want(want)
    worth_s = _seconds("worth_s", worth_s)
    deadline_s = _seconds("deadline_s", deadline_s, allow_none=True)
    expires_s = _seconds("expires_s", expires_s)
    got = Want(id or uuid.uuid4().hex[:8], dict(want), worth_s, deadline_s, expires_s, scope, note)
    ends = set(survival.END_DIMS.values())
    items = [k for k in effect if not k.startswith("tool:") and k not in ends]
    missing = sorted(k for k in items if k not in _producible())
    if missing:
        got.state = f"refused(nothing in the skill table produces {', '.join(missing)})"
    _wants[got.id] = got
    _save()
    return got


def stop(id=None, *, hard=False, reason=""):
    """Soft: the remaining value goes to zero, so the next round chooses something else by itself.
    Hard: take the body now, through the one body door the threat layer uses."""
    going = [w for w in _wants.values() if w.alive()] if id is None else \
        [w for w in _wants.values() if w.id == id]
    for w in going:
        w.state, w.blocked_by = "stopped", reason or w.blocked_by
    if hard and going:
        from . import arbiter
        # The body has ONE door, and what it answers is (taken, why). A hard stop that is refused is not a
        # failure to report later: it is written on the want, so `status` says the body was never handed over.
        answer = arbiter.BODY.preempt("plan", lambda: None,
                                      reason=f"want stopped: {reason or 'cerebrum'}")
        taken, why = answer if isinstance(answer, tuple) else (bool(answer), None)
        if not taken:
            for w in going:
                w.blocked_by = f"body not handed over ({why or 'refused'})"
    _save()
    return going


def status(id=None):
    """What the planner made of each want: the seconds it was priced at, what it chose, what outscored it."""
    rows = _wants.values() if id is None else [w for w in _wants.values() if w.id == id]
    return [w.reading() for w in rows if id is not None or w.alive()]


def list(active=True):
    """The wants themselves. `active=False` includes the expired, stopped and refused."""
    return [w for w in _wants.values() if w.alive() or not active]


def forget(id):
    """Drop a want entirely — for a caller cleaning up, never as an answer to a caller who asked for something."""
    _wants.pop(id, None)
    _save()


# ------------------------------------------------------------------ pricing, on the ordinary path

def effects():
    """The live wants as effects on the state vector, for whoever is building this round's value world."""
    return {w.id: _check_want(w.want) for w in _wants.values() if w.alive()}


def priced_wants():
    """{id: (effect, worth_s)} for the live wants — what `gates.V` prices as temporary terminal dimensions.

    The worth is already discounted for how late the want would land, so the deadline reaches the planner as a
    smaller number of seconds and nothing else: it can never unlock what the doors refuse.
    """
    return {w.id: (_check_want(w.want), priced_s(w)) for w in _wants.values() if w.alive()}


def priced_s(w, snap=None, brain=None):
    """What the want is worth THIS round: its seconds, discounted by how late it would land, less what reaching
    it would take. The work term comes from `Brain.worth_of_change` — the one entry every candidate goes through
    — so a want can never be worth more here than the same change is worth anywhere else."""
    from . import value
    late = w.deadline_s if w.deadline_s is not None else HORIZON_S
    out = w.worth_s * value.discount(late, HORIZON_S)
    if snap is None or brain is None:
        return round(out, 3)
    try:
        return round(min(out, brain.worth_of_change(snap, _check_want(w.want))), 3)
    except Exception as err:
        api.swallowed("want.priced_s", err)          # no world to price against: the offer's own seconds stand
        return round(out, 3)


def note_pick(id, chosen=None, blocked_by=None, state=None):
    """The planner writing back what it did with a want, so `status` can answer how it was implemented."""
    w = _wants.get(id)
    if w is None:
        return None
    w.chosen, w.blocked_by = chosen, blocked_by
    if state is not None:
        if state not in STATES:
            raise ValueError(f"unknown want state {state!r}: {STATES}")
        w.state = state
    _save()
    return w


# ------------------------------------------------------------------ the store

def _save():
    import json
    import os
    try:
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        with open(FILE, "w") as f:
            json.dump([{k: getattr(w, k) for k in Want.__slots__} for w in _wants.values()], f)
    except OSError as err:
        api.swallowed("want._save", err)             # the wants still hold for this process


def load(path=None):
    """Read the store back after a restart. Expired wants are not revived: everything expires, including a file."""
    import json
    import os
    path = path or FILE
    if not os.path.exists(path):
        return []                                    # nothing has been asked for yet: not a failure
    try:
        with open(path) as f:
            rows = json.load(f)
    except (OSError, ValueError) as err:
        return api.swallowed("want.load", err) or []
    for row in rows:
        w = Want(row["id"], row["want"], row["worth_s"], row["deadline_s"], row["expires_s"],
                 row["scope"], row["note"])
        w.created, w.state = row["created"], row["state"]
        if w.alive():
            _wants[w.id] = w
    return list()
