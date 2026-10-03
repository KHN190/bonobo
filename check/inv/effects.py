"""E1 and E3 judged offline on the jar tasks the round's chosen step would send (ctx["tasks"]: the step's runner's
commands as the door sends them, api.walk_only applied — built, never posted). Without that field the round has not
said what it would send: Unchecked, naming it. E2 (the declared effect appears) and P1 (perceived facts match a
re-read) need the world to change between reads: live only."""
from bonobo import api

from ..oracle import Unchecked

NEEDS = "ctx['tasks']: the chosen step's jar tasks as the door sends them (check/round.py, a46)"
DIG_PLACE = ("break", "place", "voidBridge")        # a walk that may change the world on its own
HELD = api.HELD_TYPES                               # tasks that act with what is in the hand


def E3(b, d, a, ctx):
    """A walk neither digs nor places: every walking task goes out with breaking, placing and bridging off."""
    if "tasks" not in ctx:
        return Unchecked(NEEDS)
    bad = [t for t in ctx["tasks"] if t.get("type") in api.WALKS and any(t.get(k) for k in DIG_PLACE)]
    return f"a walk that may dig or place: {bad[0]}" if bad else None


def E1(b, d, a, ctx):
    """Outside the reflexes, the hand is named by the step: every task that acts with the hand names its item (or the
    door's hold puts one there: api.HOLD wired)."""
    if "tasks" not in ctx:
        return Unchecked(NEEDS)
    if api.HOLD is not None:
        return None
    bad = [t for t in ctx["tasks"] if t.get("type") in HELD and not t.get("item")]
    return f"a task acting with whatever is in the hand: {bad[0]}" if bad else None


def P1(b, d, a, ctx):
    """Facts no older than they may be when the round uses them: safety a perception cycle, terrain its TTL (the
    agreement with a re-read is live only)."""
    if "fact_ages" not in ctx:
        return Unchecked("no reading ages taken this round")
    for name, (age, most) in sorted(ctx["fact_ages"].items()):
        if age > most:
            return f"{name}: {age:.1f} s old, at most {most} s"
    return None


def live(_b, _d, _a, _ctx):
    return Unchecked("live only: the world must change between two reads (a skill's effect, a re-read)")


CHECKS = {"E1": E1, "E3": E3, "E2": live, "P1": P1}
