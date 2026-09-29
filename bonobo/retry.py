"""One failure policy for every attempt the brain makes (task steps, upkeep, rescues). A failure is (task, cause). Two facts, kept apart on purpose: - the COUNT belongs to the pair: "mine iron failed for nav" three times means three sources were tried and none could be reached. After SOURCES_TRIED the task stops retrying and is reported upward (L3: task state + reason); - the COOLDOWN belongs to the cause, at a place: `cause@place`. The same wall stops every task that would walk into it, and is asked about once between them — a body treading water fails to dig, place, build and shelter, one fact. Nothing else cools: there is no per-task timer. An interruption (`api.interrupted`) is not a failure and never reaches this module. Pure (time is passed in): offline-testable."""
from __future__ import annotations

from .data import EXCEPTIONS, UNREACHABLE  # noqa: E402  (the one list: api raises Unreachable on the same words)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .shapes import Cause, Source

BACKSTOP = {"game": 10, "tool": 20, "nav": 120, "unavailable": 180, "stuck": 120, "error": 60}
# the doubling's ceiling by cause: "not here" ages fast (mobs wander, we walk); a bug does not
MAX_BACKSTOP = {"game": 60, "unavailable": 300, "nav": 300, "tool": 120, "stuck": 300, "error": 900}
MAX_BACKSTOP_DEFAULT = 900
SOURCES_TRIED = 3          # failures of one (task, cause) — each after changing source — before reporting upward
LOG_EVERY = 10
NOT_FAILURES = ("interrupt", "replan")
REPLAN_LIMIT = 2  # replanning this often in a row with nothing done is a failure ("unavailable")


def row_of(err) -> tuple[Cause, Source]:
    """(cause, source) of an exception: its nearest class with a row. An exception class of ours with no row of its
    own is a KeyError naming it — never read silently as its base's; a foreign class (a builtin) is its base's."""
    for cls in type(err).__mro__:
        if cls.__name__ in EXCEPTIONS:
            return EXCEPTIONS[cls.__name__]
        if cls.__module__.split(".")[0] == "bonobo":
            raise KeyError(f"exception {cls.__name__} ({cls.__module__}) has no row in retry.EXCEPTIONS")
    raise KeyError(f"exception {type(err).__name__} is no Exception")

def cause_of(err) -> Cause:
    """The cause a failure is counted and cooled under (EXCEPTIONS; a bare mod failure by its text)."""

    cause = row_of(err)[0]
    if cause in ("error", "unavailable", "stuck") and any(m in str(err).lower() for m in UNREACHABLE):
        return "nav"
    return cause

def source_of(err) -> Source:
    """The interrupt source an exception stands for (EXCEPTIONS): what is done about it is arbiter.RESUME_OF's."""
    return row_of(err)[1]

def place_signature(feet, night, bin_size=16):
    """Where we are, coarsely, and whether it is dark. What a cause is cooled against."""
    return tuple(int(c) // bin_size for c in feet), bool(night)

def cause_key(cause, place):
    return f"{cause}@{place}"

class Verdict:
    """What a failure means: its count for (task, cause), how long the cause cools here, and whether to report upward."""

    def __init__(self, n, wait, escalate, worth_logging):
        self.n, self.wait, self.escalate, self.worth_logging = n, wait, escalate, worth_logging

    def __iter__(self):            # (n, wait, worth_logging), the shape callers unpacked before
        return iter((self.n, self.wait, self.worth_logging))

class Retry:
    def __init__(self):
        self.entries = {}   # (task, cause) -> {n, since, message, place}
        self.cooling = {}   # "cause@place" -> {until, n}
        self.holds = {}     # name -> time: explicit throttles ("deposit at most once a minute")

    def hold(self, name, seconds, now):
        self.holds[name] = max(self.holds.get(name, 0), now + seconds)

    def release(self, name):
        self.holds.pop(name, None)
        for key in [k for k in self.entries if k[0] == name]:
            self.entries.pop(key)

    def failed(self, task, cause, message, now, place=None, also_at=()):
        """Record a failure of `task` for `cause`, cooled at `place` and at each of `also_at` (where the failure
        happened, when a long step walked away from where it began). Returns a Verdict, or None for what is not one."""
        if cause == "replan":
            e = self.entries.get((task, "replan"))
            n = e["n"] + 1 if e else 1
            self.entries[(task, "replan")] = {"n": n, "since": now, "message": message, "place": place}
            if n < REPLAN_LIMIT:
                return None
            cause = "unavailable"
        elif cause in NOT_FAILURES:
            return None
        e = self.entries.get((task, cause))
        n = e["n"] + 1 if e else 1
        worth_logging = e is None or e["message"] != message or n == SOURCES_TRIED or n % LOG_EVERY == 0
        self.entries[(task, cause)] = {"n": n, "since": now, "message": message, "place": place}
        key = cause_key(cause, place)
        c = self.cooling.get(key)
        ceiling = MAX_BACKSTOP.get(cause, MAX_BACKSTOP_DEFAULT)
        # the same cause at the same place before its cooling was forgotten: it doubles
        repeats = c["n"] + 1 if c and c["until"] > now - ceiling else 1
        wait = min(ceiling, BACKSTOP.get(cause, 60) * 2 ** (min(repeats, 6) - 1))
        self.cooling[key] = {"until": now + wait, "n": repeats}
        for other in also_at:
            if other != place:
                self.cooling[cause_key(cause, other)] = {"until": now + wait, "n": repeats}
        return Verdict(n, wait, n >= SOURCES_TRIED, worth_logging)

    def causes(self, task):
        """The causes this task has failed with since it last succeeded."""
        return [cause for (t, cause) in self.entries if t == task]

    def cool(self, cause, place, now):
        """Seconds the cause still cools at this place (0 when it does not)."""
        c = self.cooling.get(cause_key(cause, place))
        return max(0.0, c["until"] - now) if c else 0.0

    def ready(self, task, now, place=None, cause=None):
        """May `task` be tried now?"""

        if self.holds.get(task, 0) > now:
            return False
        for c in set(self.causes(task)) | ({cause} if cause else set()):
            if self.cool(c, place, now) > 0:
                return False
        return True

    def cap(self, name, seconds, now, place=None):
        """Shorten a hold on `name`, and the cooling of every cause `name` failed with here, to `seconds`."""
        if name in self.holds:
            self.holds[name] = min(self.holds[name], now + seconds)
        for cause in self.causes(name):
            c = self.cooling.get(cause_key(cause, place))
            if c is not None:
                c["until"] = min(c["until"], now + seconds)

    def succeeded(self, task):
        for key in [k for k in self.entries if k[0] == task]:
            self.entries.pop(key)

    def exhausted(self, task):
        """(cause, message) once some cause has beaten SOURCES_TRIED sources for this task, else None."""

        worst = max(((cause, e) for (t, cause), e in self.entries.items() if t == task and cause != "replan"),
                    key=lambda ce: ce[1]["n"], default=None)
        if worst is None or worst[1]["n"] < SOURCES_TRIED:
            return None
        return worst[0], worst[1]["message"]

    def last_failure(self, task):
        """When `task` last failed, or None."""
        times = [e["since"] for (t, _c), e in self.entries.items() if t == task]
        return max(times) if times else None

    def cooling_now(self, now):
        """The causes cooling right now, for the track log."""
        return sorted(k for k, c in self.cooling.items() if c["until"] > now)
