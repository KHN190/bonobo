"""One failure policy for every attempt the brain makes (goal steps, maintenance, rescues, fallbacks).

Timers alone retry an unchanged world and fail the same way (unstuck ran 29 times from one block). Instead:
- a failure is remembered with the state it happened in (`signature`: position bin, item kinds held, day/night);
- a changed state may retry soon (after MIN_GAP); an unchanged one waits a backstop that doubles per repeat;
- EXHAUSTED_AFTER failures in one state = exhausted: callers switch method or escalate, they don't retry;
- every failure gets a cause (nav / tool / unavailable / stuck / error) so statistics blame the right thing;
- a failure is logged when it says something new, not on every repeat.
Pure (time is passed in): offline-testable."""

NAV_MARKERS = ("no path", "unreachable", "not reachable", "no reachable face", "gave up after", "could not get",
               "cannot reach", "can't reach", "positions explored")
BACKSTOP = {"tool": 20, "nav": 120, "unavailable": 180, "stuck": 120, "error": 60, "interrupt": 0}
# The ceiling the doubling runs into, by what went wrong. One ceiling for every cause said a world that does not
# offer something HERE is as hopeless as a bug: a stone pickaxe that failed four times waited a quarter of an hour
# while the agent repaired tools it did not need — 3840 s to a pickaxe in the offline life, against a 900 s budget.
# "The world does not offer this" ages fast (mobs wander, the sun moves, we walk); a bug does not.
MAX_BACKSTOP = {"unavailable": 300, "nav": 300, "tool": 120, "stuck": 300, "interrupt": 0, "error": 900}
MAX_BACKSTOP_DEFAULT = 900
MIN_GAP = 5
EXHAUSTED_AFTER = 3
LOG_EVERY = 10


def cause_of(err):
    name = type(err).__name__
    text = str(err).lower()
    if name == "Interrupted":
        return "interrupt"   # a danger stopped it: not the skill's fault, retry as soon as it's safe
    if name == "CommitmentExpired":
        return "replan"      # the plan grew stale mid-action: nothing failed, decide again now
    if name == "ToolMissing":
        return "tool"
    if name == "NavFailed" or any(m in text for m in NAV_MARKERS):   # text only for mod task messages
        return "nav"
    if name == "NotAvailable":
        return "unavailable"
    if name == "TaskStuck":
        return "stuck"
    return "error"


def signature(feet, item_ids, night, bans=0, bin_size=4):
    """Coarse world state: moving a few blocks or a new kind of item counts as a change. Blacklist size is NOT part
    of it: bans expiring (234 → 0) read as "the world changed" and reset the failure count, so one unreachable iron
    vein was retried forever (loop simulation of a recorded round). `bans` is accepted for callers and ignored."""
    return tuple(int(c) // bin_size for c in feet), frozenset(item_ids), bool(night), 0


def place_signature(feet, night, bin_size=16):
    """Where we are, coarsely, and whether it is dark. No inventory.

    This is the signature failures are counted against. "This step cannot be done HERE" is a fact about the place,
    but the fuller `signature` includes the bag — and failing to mine something changes the bag (a half-stack
    picked up, a tool worn), so the count reset on almost every attempt and the give-up rule never fired. A mining
    step was retried five times in six rounds while other goals waited.
    """
    return tuple(int(c) // bin_size for c in feet), bool(night)


class Retry:
    def __init__(self):
        self.entries = {}   # name -> {state, n, since, until, cause, message}
        self.holds = {}     # name -> time: explicit throttles ("deposit at most once a minute"), state-independent

    def hold(self, name, seconds, now):
        self.holds[name] = max(self.holds.get(name, 0), now + seconds)

    def release(self, name):
        self.holds.pop(name, None)
        self.entries.pop(name, None)

    def failed(self, name, cause, message, state, now, place=None):
        """Record a failure. Returns (repeats in the same situation, backstop seconds, worth logging).

        "The same situation" is the PLACE when one is given. Counting against the full state (bag included) meant
        the count reset on nearly every attempt, because failing changes the bag — so the give-up rule never fired.
        """
        e = self.entries.get(name)
        same = e is not None and (e.get("place") == place if place is not None and e.get("place") is not None
                                  else e["state"] == state)
        n = e["n"] + 1 if same else 1
        ceiling = MAX_BACKSTOP.get(cause, MAX_BACKSTOP_DEFAULT)
        wait = min(ceiling, BACKSTOP.get(cause, 60) * 2 ** (min(n, 6) - 1))
        worth_logging = not same or e["message"] != message or n == EXHAUSTED_AFTER or n % LOG_EVERY == 0
        self.entries[name] = {"state": state, "place": place, "n": n, "since": now, "until": now + wait,
                              "cause": cause, "message": message}
        return n, wait, worth_logging

    def ready(self, name, state, now, place=None):
        if self.holds.get(name, 0) > now:
            return False
        e = self.entries.get(name)
        if e is None:
            return True
        moved = (e.get("place") != place) if place is not None and e.get("place") is not None \
            else (e["state"] != state)
        if moved and now >= e["since"] + MIN_GAP:
            return True
        return now >= e["until"]

    def cap(self, name, seconds, now):
        e = self.entries.get(name)
        if e is not None:
            e["until"] = min(e["until"], now + seconds)
        if name in self.holds:
            self.holds[name] = min(self.holds[name], now + seconds)

    def succeeded(self, name):
        self.entries.pop(name, None)

    def exhausted(self, name, state, place=None):
        """Has this failed enough times in the same situation to stop offering it?

        `place` is the coarse location signature; when given it is what "the same situation" means, and the fuller
        state is ignored. Moving somewhere else still earns a fresh try — that is the point of counting per place —
        but standing still and failing does not.
        """
        e = self.entries.get(name)
        if e is None or e["n"] < EXHAUSTED_AFTER:
            return False
        if place is not None and e.get("place") is not None:
            return e["place"] == place
        return e["state"] == state
