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
SCALES = {"reflex": REFLEX, "safety": SAFETY, "tactic": TACTIC, "plan": PLAN}


class Intent:
    """What a layer would like the body to do. Data, not a command — the arbiter decides whether it happens."""

    def __init__(self, layer, action, reason="", deadline_s=None, at=None, commit_s=None,
                 cost_rate=0.0, cost_s=None):
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
        self.at = time.time() if at is None else at

    @property
    def scale(self):
        return SCALES[self.layer]

    def expired(self, now=None):
        if self.deadline_s is None:
            return False
        return (now if now is not None else time.time()) - self.at > self.deadline_s

    def interrupt_cost_s(self, now=None):
        """Seconds of work already put in, which abandoning this would throw away. Asked at the moment of the
        interruption: a price frozen when the intent was submitted is always zero."""
        spent = max(0.0, (now if now is not None else time.time()) - self.at) * self.cost_rate
        return min(spent, self.cost_s) if self.cost_s is not None else spent

    def over_commitment(self, now=None):
        """Has the body owed the world a fresh decision since `commit_s`? The action is not wrong, only stale."""
        if self.commit_s is None:
            return False
        return (now if now is not None else time.time()) - self.at > self.commit_s

    def __repr__(self):
        return f"Intent({self.layer}, {self.reason!r})"


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

    def owns(self, what):
        """Is the calling thread allowed to drive the body right now? Always, outside a fight. Inside one, only
        while executing the chosen intent. Refusals are recorded so a test can assert none happened."""
        if not self.engaged or self.current() is not None:
            return True
        self.violations.append((time.time(), what))
        self._log(f"?? {what} drove the body outside the arbiter (refused)")
        return False

    # -- fast layers: preempt ----------------------------------------------------------------------------------------

    def preempt(self, layer, action, reason="", worth_s=None, now=None, clear_first=False):
        """A fast layer speaks: run now, on this thread, and mark every slower intent stale. Returns (layer,
        reason), or None when the layer was not worth the interruption.

        Not queued. Waiting for the fight loop's next step would make a 0.2 s layer answer at a 10 s cadence,
        which is no priority at all. Slower pending intents are dropped; a slower action already running is told
        through api.INTERRUPT (the message channel) and abandons itself.

        `worth_s` prices the interruption for the ONE layer that is a judgement call rather than an emergency.
        REFLEX and SAFETY are never priced: "you are drowning" is not a bid, and an emergency that has to argue
        its case is not an emergency. TACTIC is a bid — taking position, shaking pursuit — and it competes with
        whatever the body is already committed to, at that commitment's own price (`interrupt_cost_s`). Without
        this the two planners were not exchanging prices at all: the faster one simply took the body, and a
        two-second reposition could abandon a minute of work worth far more than it.
        """
        intent = Intent(layer, action, reason)
        # Subsumption applies to what is RUNNING, not only to what is pending: an answer that is half carried out
        # may be cut off by a faster layer and by nothing else. Without this the threat layer stopped its own
        # answer 0.05 s after starting it, every tick, and the log filled with contested tasks.
        driving = self.driving
        if driving is not None and driving.scale <= intent.scale:
            self._log(f"   motion: {layer} '{reason}' waits: {driving.layer} '{driving.reason}' is driving")
            return None
        if intent.scale > SAFETY and worth_s is not None:
            running = self.last if self.last is not None and self.last is self.current() else None
            defended = running or arbitrate(self.pending, now)
            price = defended.interrupt_cost_s(now) if defended is not None else None
            if price is not None and worth_s < price:
                self._log(f"   motion: {layer} '{reason}' worth {worth_s:.0f}s, "
                          f"not taking the body from '{defended.reason}' ({price:.0f}s)")
                return None
        with self._lock:
            self.pending = [p for p in self.pending if p.scale <= intent.scale]
            self.preempted_at, self.preempted_by = intent.at, intent
            if intent.scale <= SAFETY:
                # The message channel has ONE writer: a preemption. Whatever slow action is running reads it and
                # abandons itself. Anyone else writing it was a second commander with a different opinion.
                from . import api
                api.INTERRUPT = reason
        if clear_first:
            # The slower layer's task is still running in the mod, and posting on top of it is two commanders.
            # Cancelling goes through this module like every other command to the body.
            from . import api
            try:
                api.post("/stop")
            except api.McError:
                pass
        self._run(intent)              # outside the lock: a long action must not freeze submit/step
        return intent.layer, intent.reason

    def drive(self, layer, action, reason="", commit_s=None):
        """Run `action` now, on this thread, under a commitment. Ordinary play's entry point.

        The fight submits intents and steps them; ordinary play runs one chosen candidate per round, which is the
        same thing with a queue of one. What it needs from the arbiter is the commitment: `api.await_task` reads
        the current intent, and without one a skill keeps the body for as long as it likes — which is why a zombie
        could beat on the agent for the length of a mining task.
        """
        self._run(Intent(layer, action, reason, commit_s=commit_s))

    # -- slow layers: submit + step ---------------------------------------------------------------------------------

    def submit(self, layer, action, reason="", deadline_s=None, commit_s=None, cost_rate=0.0, cost_s=None):
        with self._lock:
            self.pending.append(Intent(layer, action, reason, deadline_s, commit_s=commit_s,
                                       cost_rate=cost_rate, cost_s=cost_s))

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
