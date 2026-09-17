"""One body, one exit, priority by time scale.

The survey found 124 places that issue movement and no one adjudicating between them. Most of them do not need
rewriting, because HOW to move already funnels through nav.go_to and api.run; what had no funnel was WHO may move
the body right now. In a multi-threat fight that is where it breaks: the perception thread stops a task, the
dispatcher starts an action, a recovery walks somewhere else, all inside one second.

Subsumption, not scoring. Layers run at their own time scale and a faster layer overrides a slower one
unconditionally — a reflex is not weighed against a plan, it vetoes it:

    REFLEX   (tick, in the jar)   lava, drowning, a fireball already in the air
    SAFETY   (~0.2 s, here)       stop what is running, leave a hazard, get into cover
    TACTIC   (~1 s)               take position, retreat, shake pursuit
    PLAN     (~10 s)              dig, place, reinforce, fire a window

Two channels, kept distinct on purpose:
  * BODY (this module) is the ONLY thing that drives the body. Slow layers `submit` and wait for `step`; fast
    layers `preempt`, which runs at once and marks the slow layers stale until they re-plan.
  * api.INTERRUPT is a message, not a command: "a faster layer has spoken". A slow action that is already running
    reads it and abandons itself. It never moves the body.

The body is a singleton — one process, one player — so BODY is module state. Actions are per fight; the body is not.
"""
import threading
import time

REFLEX, SAFETY, TACTIC, PLAN = 0.05, 0.2, 1.0, 10.0
FRESH_WITHIN_S = 1.0     # a reading older than this describes a world that has moved on
SCALES = {"reflex": REFLEX, "safety": SAFETY, "tactic": TACTIC, "plan": PLAN}

# Why an answer did not get the body. A closed set, because "it did not happen" is not an observation: a bench
# that cannot tell "outbid" from "locked out" reads fourteen empty cells and learns nothing from any of them.
#
#   layer    something faster holds or drives the body; subsumption, not a contest
#   margin   the same layer already holds a decision this one does not clearly beat (`kernel.MARGIN`)
#   price    the same layer is part-way through open-loop work worth more than this answer saves
#   expired  the intent was too old to start
REFUSED = ("layer", "held", "expired")


def fresh_enough(seen_at, now=None, within=1.0):
    """Was this reading taken recently enough to compare with?

    Every layer polls the same world at its own rate, so two decisions can be made from readings seconds apart.
    Without a timestamp neither can tell, and a decision made from a thirty-second-old world defends itself
    against one made from the current world as if they were equals.
    """
    if seen_at is None:
        return False
    return (now if now is not None else time.time()) - float(seen_at) <= float(within)


class Intent:
    """What a layer would like the body to do. Data, not a command — the arbiter decides whether it happens."""

    def __init__(self, layer, action, reason="", deadline_s=None, at=None, commit_s=None,
                 cost_rate=0.0, cost_s=None, resumable=True, redo_s=0.0):
        if layer not in SCALES:
            raise ValueError(f"unknown layer {layer!r}: expected one of {sorted(SCALES)}")
        self.layer = layer
        self.action = action
        self.reason = reason
        self.deadline_s = deadline_s
        # How long the body may stay on this before the planner is asked again. Distinct from deadline_s, which says
        # when the intent is too old to START: a plan that takes 40 s is not a plan that may ignore the world for
        # 40 s. The planner computed a commitment for every action and nothing ever read it, so a fight round lasted
        # as long as its action and the 10 Hz perception could only interrupt, never re-decide.
        self.commit_s = commit_s
        self.cost_rate = float(cost_rate)
        self.cost_s = None if cost_s is None else float(cost_s)
        # Whether stopping this throws anything away is the LAYER's question, answered through the `release` its
        # held decision hands the body. Kept as data for the log and the tape, never read by a judgement here.
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

    def spent_s(self, now=None):
        """Seconds already put into this, for the log and the tape. Never for a comparison."""
        from . import estimate
        return estimate.sunk_s(self.cost_rate, (now if now is not None else time.time()) - self.at, self.cost_s)

    def over_commitment(self, now=None):
        """Has the body owed the world a fresh decision since `commit_s`? The action is not wrong, only stale."""
        if self.commit_s is None:
            return False
        return (now if now is not None else time.time()) - self.at > self.commit_s

    def __repr__(self):
        return f"Intent({self.layer}, {self.reason!r})"


def wants_body(body, running, now=None):
    """Is there a reason to take the body from `running` right now?

    A faster layer waiting for it — and nothing else. The commitment used to be read as a timer, so a walk to a
    chest forty blocks away was abandoned every ten seconds, re-scored, and walked again: the agent paced back and
    forth for a session. Time passing is not a reason; somebody faster wanting the body is.
    """
    if running is None:
        return False
    with body._lock:
        pending = list(body.pending)
    return any(p.scale < running.scale and not p.expired(now) for p in pending)


def arbitrate(intents, now=None):
    """Pure: the one intent that may drive the body, or None. Fastest layer wins; within a layer the newest wins;
    expired intents are dropped rather than run late."""
    live = [i for i in intents if not i.expired(now)]
    if not live:
        return None
    return min(live, key=lambda i: (i.scale, -i.at))


class Motion:
    """The body's single entry point. Thread-safe: the perception thread preempts, the fight loop steps.

    `engaged` is true while a fight owns the body. Outside a fight nothing is refused — ordinary play (mining,
    building) keeps its skills unchanged. Inside one, any call that drives the body from outside the current
    intent is a violation: refused, counted, logged. That is the ownership check nav.go_to and api.run make.
    """

    def __init__(self, log=None):
        self._lock = threading.RLock()
        self._local = threading.local()
        self.pending = []
        self.last = None
        self.engaged = False
        self.preempted_at = 0.0          # when a fast layer last overrode; plans older than this are stale
        self.preempted_by = None
        self.violations = []
        self.driving = None       # the preemption currently executing, if any
        self.lease = None         # (intent, release, worth_s): the decision the body holds, and what ends it
        self._log = log or (lambda *_: None)

    # -- engagement ------------------------------------------------------------------------------------------------

    def engage(self, log=None):
        with self._lock:
            self.engaged = True
            self.pending = []
            self.preempted_at, self.preempted_by = 0.0, None
            if log:
                self._log = log

    def disengage(self):
        with self._lock:
            self.engaged = False
            self.pending = []

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

    def holder(self):
        """The decision currently held by the body, or None.

        A held decision outlives the call that took it: an answer is not done when its first task returns, it is
        done when answering stops being worth more than working. It is a DECISION, not a lock — `kernel.Held`
        says when one may be replaced, and the same rule applies here so that the second answer of one fight is a
        re-decision rather than an intruder.
        """
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
        """Is the calling thread allowed to drive the body right now?

        While a lease stands, only the thread running that intent may drive. This used to be gated on `engaged`
        (a boss fight), so in ordinary play every thread was allowed and whoever posted last won: the threat
        answer took the body, the planner's next mine task replaced it, and the agent stood still being hit.
        """
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
        """A fast layer speaks: run now, on this thread, and mark every slower intent stale.

        Returns `((layer, reason), None)` when it took the body, or `(None, why)` when it did not, with `why` one
        of REFUSED. A refusal that cannot say which rule stopped it is indistinguishable from a threat nobody
        priced, which is what a whole bench pass could not tell apart.

        This is the only judgement here, and it is not a price:

            faster layer   takes the body, unconditionally — subsumption, not a contest
            slower layer   refused: "layer"
            same layer     the LAYER's own held decision answers, through `release()`: still paying means keep,
                           stopped paying means hand over. The arbiter does not compare two worths; the moment it
                           does it is both the lock and the judge, and the layer above is left holding a decision
                           nobody will run.

        `held` is the challenger's own held decision, told `note_denied(why)` when it is refused — being unable to
        reach the body is an assumption that has stopped holding, and a layer that is not told re-decides the same
        thing every tick. `seen_at` is when the reading behind this answer was taken: a decision made from a stale
        world does not defend itself against one made from the current world.
        """
        intent = Intent(layer, action, reason)

        def refuse(why, note=""):
            if note:
                self._log(note)
            if held is not None and hasattr(held, "note_denied"):
                held.note_denied(why)
            return None, why

        holder = self.holder()
        if holder is not None and holder is not self.current():
            if holder.scale < intent.scale:
                return refuse("layer", f"   motion: {layer} '{reason}' waits: {holder.layer} "
                                       f"'{holder.reason}' holds the body")
            if holder.scale == intent.scale and self.lease is not None:
                _intent, _release, held_at = self.lease
                # A decision from a world nobody has looked at lately is not a decision worth defending.
                if fresh_enough(held_at, now, fresh_within):
                    return refuse("held", f"   motion: {layer} '{reason}' waits: {holder.layer} "
                                          f"'{holder.reason}' still holds its answer")
        driving = self.driving
        if driving is not None and driving.scale < intent.scale:
            return refuse("layer", f"   motion: {layer} '{reason}' waits: {driving.layer} "
                                   f"'{driving.reason}' is driving")
        if driving is not None and driving.scale == intent.scale:
            # A layer does not cut off its own running answer: that is one decision being carried out, and
            # re-entering it mid-action is how the threat layer once stopped itself every tick.
            return refuse("held", f"   motion: {layer} '{reason}' waits: its own '{driving.reason}' is running")
        with self._lock:
            self.pending = [p for p in self.pending if p.scale <= intent.scale]
            self.preempted_at, self.preempted_by = intent.at, intent
            if release is not None:
                # Taking the body and saying so are one step. While they were two, the /stop below woke the
                # planner, it asked `owns` before the lease existed, and posted its next task into the gap — which
                # replaced this answer 0.05 s after it started, three fixes in a row. The reading's timestamp is
                # kept with it, because that is what says whether this decision still describes the world.
                self.lease = (intent, release, now if seen_at is None else seen_at)
            if intent.scale <= SAFETY:
                # The message channel has ONE writer: a preemption. Whatever slow action is running reads it and
                # abandons itself. Anyone else writing it was a second commander with a different opinion.
                from . import api
                api.INTERRUPT = reason
        if clear_first:
            # The slower layer's task is still running in the mod, and posting on top of it is two commanders.
            # Sent after the lease stands, so whoever this wakes is already refused.
            from . import api
            try:
                api.post("/stop")
            except api.McError:
                pass
        self._run(intent)              # outside the lock: a long action must not freeze submit/step
        return (intent.layer, intent.reason), None

    def drive(self, layer, action, reason="", commit_s=None, resumable=True, redo_s=0.0):
        """Run `action` now, on this thread, under a commitment. Ordinary play's entry point.

        The fight submits intents and steps them; ordinary play runs one chosen candidate per round, which is the
        same thing with a queue of one. What it needs from the arbiter is the commitment: `api.await_task` reads
        the current intent, and without one a skill keeps the body for as long as it likes — which is why a zombie
        could beat on the agent for the length of a mining task.
        """
        self._run(Intent(layer, action, reason, commit_s=commit_s, resumable=resumable, redo_s=redo_s))

    # -- slow layers: submit + step ---------------------------------------------------------------------------------

    def submit(self, layer, action, reason="", deadline_s=None, commit_s=None, cost_rate=0.0, cost_s=None,
               resumable=True, redo_s=0.0):
        with self._lock:
            self.pending.append(Intent(layer, action, reason, deadline_s, commit_s=commit_s,
                                       cost_rate=cost_rate, cost_s=cost_s,
                                       resumable=resumable, redo_s=redo_s))

    def step(self, now=None):
        """Run the winning pending intent and drop the rest. Returns (layer, reason) or None.

        A plan submitted before the last preemption is stale — it was made for a situation a faster layer has
        since changed — and is dropped even if it would otherwise win.
        """
        with self._lock:
            fresh = [p for p in self.pending if not (p.layer == "plan" and p.at < self.preempted_at)]
            chosen = arbitrate(fresh, now)
            dropped = [p for p in self.pending if p is not chosen]
            self.pending = []
            if chosen is None:
                return None
            if dropped:
                self._log(f"   motion: {chosen.layer} '{chosen.reason}' over "
                          f"{', '.join(f'{d.layer}:{d.reason}' for d in dropped)}")
        self._run(chosen)              # outside the lock
        return chosen.layer, chosen.reason


BODY = Motion()      # the one player this process drives
