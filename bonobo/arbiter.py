"""One body, one exit, priority by time scale. The survey found 124 places that issue movement and no one adjudicating between them. Most of them do not need rewriting, because HOW to move already funnels through nav.go_to and api.run; what had no funnel was WHO may move the body right now. In a multi-threat fight that is where it breaks: the perception thread stops a task, the dispatcher starts an action, a recovery walks somewhere else, all inside one second. Subsumption, not scoring. Layers run at their own time scale and a faster layer overrides a slower one unconditionally — a reflex is not weighed against a plan, it vetoes it: REFLEX   (tick, in the jar)   lava, drowning, a fireball already in the air SAFETY   (~0.2 s, here)       stop what is running, leave a hazard, get into cover TACTIC   (~1 s)               take position, retreat, shake pursuit PLAN     (~10 s)              dig, place, reinforce, fire a window Two channels, kept distinct on purpose: * BODY (this module) is the ONLY thing that drives the body. Slow layers `submit` and wait for `step`; fast layers `preempt`, which runs at once and marks the slow layers stale until they re-plan. * api.INTERRUPT is a message, not a command: "a faster layer has spoken". A slow action that is already running reads it and abandons itself. It never moves the body. The body is a singleton — one process, one player — so BODY is module state. Actions are per fight; the body is not."""

import threading
import time

REFLEX, SAFETY, TACTIC, MAINTAIN, PLAN = 0.05, 0.2, 1.0, 3.0, 10.0
FRESH_WITHIN_S = 1.0     # a reading older than this describes a world that has moved on
# MAINTAIN (reflexes.TABLE): faster than any plan, slower than a fight
SCALES = {"reflex": REFLEX, "safety": SAFETY, "tactic": TACTIC, "maintain": MAINTAIN, "plan": PLAN}


def fresh_enough(seen_at, now=None, within=1.0):
    """Was this reading taken recently enough to compare with?"""

    if seen_at is None:
        return False
    return (now if now is not None else time.time()) - float(seen_at) <= float(within)

class Intent:
    """What a layer would like the body to do; the arbiter decides."""

    def __init__(self, layer, action, reason="", deadline_s=None, at=None, commit_s=None,
                 cost_rate=0.0, cost_s=None, resumable=True, redo_s=0.0, kind=None, seq=0, key=None):
        if layer not in SCALES:
            raise ValueError(f"unknown layer {layer!r}: expected one of {sorted(SCALES)}")
        self.layer = layer
        self.action = action
        # within PLAN: the proposal's kind (PLAN_ORDER) and its place among several of one kind
        self.kind, self.seq = kind, seq
        # What the round's facts know this proposal by (a need's or a task's retry name): `viable` reads it.
        self.key = key if key is not None else (reason or None)
        self.reason = reason
        self.deadline_s = deadline_s
        # how long the body may stay on this before the planner is asked again (deadline_s is when it is too old to start)
        self.commit_s = commit_s
        self.cost_rate = float(cost_rate)
        self.cost_s = None if cost_s is None else float(cost_s)
        # data for the log and tape only: whether stopping loses work is the layer's call, through `release`
        self.resumable = bool(resumable)
        self.redo_s = float(redo_s)
        self.at = time.time() if at is None else at

    @property
    def scale(self):
        return SCALES[self.layer]

    def expired(self, now=None):
        if self.deadline_s is None:
            return False
        return (now if now is not None else time.time()) - self.at > self.deadline_s

    def __repr__(self):
        return f"Intent({self.layer}, {self.reason!r})"

# PLAN_ORDER ranks every planned proposal here only; RESUME_RULES: one declared rule per interrupt source (the offline sweep refuses a source without one)
RESUME_RULES = {
    "same": (True, None),             # the same target, the next frontier; nothing cooled, nothing banned
    "recheck": (True, "recheck"),     # the bag changed under it: re-read the remaining amount first
    "recover": (True, "recover"),     # died: recover the items first, then replan from where we stand (target kept)
    "dimension": (True, "back"),      # the map is per dimension: resumed only back in the one it was left in
    "handback": (True, "handback"),   # the player holds control: wait until it is handed back, then the same
    "stand down": (True, "stand_down"),   # someone else drives the body: stand down a while, then the same
    "fight": (True, "fight"),         # our own fight holds the body: back when it ends, not 10 s later
    "game": (True, "wait_game"),      # the game cannot be reached: wait for it, then the same
    "none": (False, None),            # the user cancelled: nothing resumes
    "cooled": (False, "cool"),        # a real failure, not an interrupt: counted, /stop, cooled under the retry policy
    "crashed": (False, "hold"),       # a bug of ours: held a while, the trace logged
}
RESUME_OF = {
    **{f"layer:{k}": "same" for k in ("reflex", "safety", "maintain", "plan")}, "layer:tactic": "fight",
    **{f"hazard:{k}": "same" for k in ("lava", "burning", "drowning", "suffocating", "falling")},
    **{f"row:{k}": "same" for k in ("eat", "reach land", "dig out", "sleep", "shelter", "collect job",
                                    "collect machine", "path blocked", "unstuck", "recover items")},
    "row:empty the bag": "recheck", "row:leave the Nether": "dimension",
    "manual": "stand down", "player": "handback", "game lost": "game", "jar reflex": "same", "death": "recover",
    "dimension change": "dimension", "user cancel": "none", "stuck": "cooled", "crash": "crashed",
}

def resume_of(source):
    """Pure: (resumes, what first) for work interrupted by `source` — KeyError for a source nobody declared."""
    return RESUME_RULES[RESUME_OF[source]]

PLAN_ORDER = ("night prep", "broken tool", "water bucket", "bridge stock", "food stock",
              "queue", "night stock", "wait for day", "idle")

# offered only when nothing else is (`gate`)
LAST_RESORT = ("wait for day", "idle")
# rounds that did nothing, the waste the bench counts (idle stocking is work)
WAIT_KINDS = ("wait for day", "wait")

def viable(intent, facts):
    """Pure: may this proposal be offered at all?"""

    key = intent.key
    return key is None or not any(key in facts.get(f, ()) for f in ("met", "cooling", "unplannable"))

def gate(intents, facts=None):
    """Pure: only the useful proposals — the viable ones, and a waiting kind only when nothing else is left."""

    live = [i for i in intents if viable(i, facts or {})]
    work = [i for i in live if not (i.layer == "plan" and i.kind in LAST_RESORT)]
    return work or live

def note_pick(picks, intent):
    """Count the chosen intent by kind: rounds spent waiting are read here, not from log text."""

    if intent is not None:
        picks[intent.kind or intent.layer] += 1
    return picks

def waits(picks):
    """Rounds spent on a waiting kind."""
    return sum(picks.get(k, 0) for k in WAIT_KINDS)

def plan_rank(kind):
    """Pure: a PLAN proposal's place in PLAN_ORDER (an unknown kind after all of them)."""
    return PLAN_ORDER.index(kind) if kind in PLAN_ORDER else len(PLAN_ORDER)

def first_live(groups, facts_of):
    """Pure: the proposals of the first group that still has one after the gate."""

    for ask in groups:
        intents = ask()
        facts = facts_of(intents)
        live = gate(intents, facts)
        if live:
            return live, facts
    return [], {}

def arbitrate(intents, now=None, facts=None):
    """Pure: the one intent that may drive the body, or None."""

    live = gate([i for i in intents if not i.expired(now)], facts)
    if not live:
        return None
    return min(live, key=lambda i: (i.scale, plan_rank(i.kind) if i.layer == "plan" else 0, i.seq, -i.at))

class Motion:
    """The body's single entry point."""

    def __init__(self, log=None):
        self._lock = threading.RLock()
        self._local = threading.local()
        self.last = None
        self.engaged = False
        self.preempted_at = 0.0          # when a fast layer last overrode; plans older than this are stale
        self.preempted_by = None
        self.violations = []
        self.driving = None       # the preemption currently executing, if any
        self.lease = None         # (intent, release, worth_s): the decision the body holds, and what ends it
        # the slowest layer that may still drive: None = ordinary play; REFLEX = reflexes only, the body driven from outside
        self.ceiling = None
        self._log = log or (lambda *_: None)

    # -- engagement ------------------------------------------------------------------------------------------------

    def engage(self, log=None):
        with self._lock:
            self.engaged = True
            self.preempted_at, self.preempted_by = 0.0, None
            if log:
                self._log = log

    def disengage(self):
        with self._lock:
            self.engaged = False

    def ceiling_now(self):
        """The ceiling in force: this process's own."""
        return self.ceiling

    def allows(self, layer):
        """May this layer still decide for itself?"""
        ceiling = self.ceiling_now()
        return ceiling is None or SCALES[layer] <= ceiling

    # -- ownership -------------------------------------------------------------------------------------------------

    def _run(self, intent):
        """Execute an intent with this thread marked as its owner for the duration."""
        prev = getattr(self._local, "current", None)
        was_driving = self.driving
        self._local.current = intent
        self.driving = intent
        try:
            intent.action()
        finally:
            self._local.current = prev
            self.driving = was_driving
        self.last = intent

    def current(self):
        return getattr(self._local, "current", None)

    def carry(self, intent, action):
        """Run `action` on THIS thread as `intent`: a worker thread keeps the ownership of the one that took it."""

        prev = getattr(self._local, "current", None)
        self._local.current = intent
        try:
            return action()
        finally:
            self._local.current = prev

    def hand_back(self, intent):
        """The decision `intent` is over: drop its lease if it still holds one, so the plan drives again."""
        with self._lock:
            if self.lease is not None and self.lease[0] is intent:
                self.lease = None
                self._log(f"   motion: {intent.layer} '{intent.reason}' hands the body back")

    def holder(self):
        """The decision currently held by the body, or None."""

        with self._lock:
            if self.lease is None:
                return None
            intent, release, _worth = self.lease
            try:
                done = bool(release())
            except Exception:
                done = True       # a judgement we cannot make is not a reason to keep the body
            if done:
                self.lease = None
                self._log(f"   motion: {intent.layer} '{intent.reason}' hands the body back")
                return None
            return intent

    def owns(self, what):
        """Is the calling thread allowed to drive the body right now?"""

        holder = self.holder()
        if holder is not None and self.current() is not holder:
            self.violations.append((time.time(), what))
            self._log(f"?? {what} drove the body while '{holder.reason}' held it (refused)")
            return False
        if not self.engaged or self.current() is not None:
            return True
        self.violations.append((time.time(), what))
        self._log(f"?? {what} drove the body outside the arbiter (refused)")
        return False

    # -- fast layers: preempt ----------------------------------------------------------------------------------------

    def preempt(self, layer, action, reason="", worth_s=None, now=None, clear_first=False, release=None,
                held=None, seen_at=None, fresh_within=FRESH_WITHIN_S):
        """A fast layer speaks: run now, on this thread, and mark every slower intent stale."""

        intent = Intent(layer, action, reason)

        def refuse(why, note=""):
            if note:
                self._log(note)
            if held is not None and hasattr(held, "note_denied"):
                held.note_denied(why)
            return None, why

        if not self.allows(layer):
            return refuse("stood_down", f"   motion: {layer} '{reason}' stands down: the body is Claude's")
        holder = self.holder()
        if holder is not None and holder is not self.current():
            if holder.scale < intent.scale:
                return refuse("layer", f"   motion: {layer} '{reason}' waits: {holder.layer} "
                                       f"'{holder.reason}' holds the body")
            if holder.scale == intent.scale and self.lease is not None:
                _intent, _release, held_at = self.lease
                # a decision from a world nobody looked at lately is not worth defending
                if fresh_enough(held_at, now, fresh_within):
                    return refuse("held", f"   motion: {layer} '{reason}' waits: {holder.layer} "
                                          f"'{holder.reason}' still holds its answer")
        driving = self.driving
        if driving is not None and driving.scale < intent.scale:
            return refuse("layer", f"   motion: {layer} '{reason}' waits: {driving.layer} "
                                   f"'{driving.reason}' is driving")
        if driving is not None and driving.scale == intent.scale:
            # a layer does not cut off its own running answer (the threat layer once stopped itself every tick)
            return refuse("held", f"   motion: {layer} '{reason}' waits: its own '{driving.reason}' is running")
        with self._lock:
            self.preempted_at, self.preempted_by = intent.at, intent
            if release is not None:
                # take the body and record the lease in one step, or the woken planner posts into the gap
                self.lease = (intent, release, now if seen_at is None else seen_at)
            if intent.scale <= SAFETY:
                # the message channel has one writer, a preemption; the running slow action reads it and abandons itself
                from . import api
                api.INTERRUPT = reason
        if clear_first:
            # stop the slower layer's running task, after the lease stands so whoever wakes is refused
            from . import api
            try:
                api.post("/stop")
            except api.McError:
                pass
        self._run(intent)  # outside the lock: a long action must not freeze the body
        return (intent.layer, intent.reason), None

    def drive(self, layer, action, reason="", commit_s=None, resumable=True, redo_s=0.0):
        """Run `action` now, on this thread, under a commitment (ordinary play's entry)."""

        if not self.allows(layer):
            self._log(f"   motion: {layer} '{reason}' stands down: the body is Claude's")
            return False
        self._run(Intent(layer, action, reason, commit_s=commit_s, resumable=resumable, redo_s=redo_s))
        return True

BODY = Motion()      # the one player this process drives
