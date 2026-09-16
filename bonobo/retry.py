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
MAX_BACKSTOP = 900
MIN_GAP = 5
EXHAUSTED_AFTER = 3
LOG_EVERY = 10


def cause_of(err):
    name = type(err).__name__
    text = str(err).lower()
    if name == "Interrupted":
        return "interrupt"   # a danger stopped it: not the skill's fault, retry as soon as it's safe
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


class Retry:
    def __init__(self):
        self.entries = {}   # name -> {state, n, since, until, cause, message}
        self.holds = {}     # name -> time: explicit throttles ("deposit at most once a minute"), state-independent

    def hold(self, name, seconds, now):
        self.holds[name] = max(self.holds.get(name, 0), now + seconds)

    def release(self, name):
        self.holds.pop(name, None)
        self.entries.pop(name, None)

    def failed(self, name, cause, message, state, now):
        """Record a failure. Returns (repeats in this state, backstop seconds, worth logging)."""
        e = self.entries.get(name)
        same = e is not None and e["state"] == state
        n = e["n"] + 1 if same else 1
        wait = min(MAX_BACKSTOP, BACKSTOP.get(cause, 60) * 2 ** (min(n, 6) - 1))
        worth_logging = not same or e["message"] != message or n == EXHAUSTED_AFTER or n % LOG_EVERY == 0
        self.entries[name] = {"state": state, "n": n, "since": now, "until": now + wait, "cause": cause,
                              "message": message}
        return n, wait, worth_logging

    def ready(self, name, state, now):
        if self.holds.get(name, 0) > now:
            return False
        e = self.entries.get(name)
        if e is None:
            return True
        if e["state"] != state and now >= e["since"] + MIN_GAP:
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

    def exhausted(self, name, state):
        e = self.entries.get(name)
        return e is not None and e["state"] == state and e["n"] >= EXHAUSTED_AFTER
