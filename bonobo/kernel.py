"""The planner. One function; the fight planner and ordinary play are the same function with a different model.

They were two planners because they look different from inside — one thinks in hit points over four seconds, the
other in beds and pickaxes over a day. They are not different. Both hold a state, both can price a state in
seconds, both have a set of things they could do, each of which changes the state and costs time, and both want
the thing that buys the most seconds. That is this module, and it is the whole of it:

    score(action) = price(state) − price(action.effect(state)) − action.cost_s

`price` is the model's, and it is the ONLY place a number turns into seconds. There are no benefit tables and no
urgency multipliers: a thing is worth the difference it makes to what the future costs. A bed is worth the night
it removes; a tunnel is worth the exposure it removes from the windows still to come; neither needs a number
written next to it.

What the two models differ in is the horizon and the SCALE of the commitment, nothing else. Both commit; neither
re-decides continuously, because a decision that can be revised at any instant is not a decision:

    normal   horizon a day       commitment = the plan's assumptions, minutes; released when one stops holding
    combat   horizon seconds     commitment = the action's atomicity, seconds; released when the action ends

`cost_s` is what the action takes; `commitment_s` is how much of that cannot be abandoned half-way. They differ:
a 0.4 s firing window is open-loop — there is no state in the middle of it to re-decide from — while a walk to a
vein can be dropped after one step. The veto reads `commitment_s`, not `cost_s`: what must be survived is the
part that cannot be called off.

A model is any object with:

    price(state)                  → seconds the future costs from here
    actions(state)                → iterable of actions; or a plain attribute holding them, when the set does not
                                    depend on the state (a fight's actions come from its profile and never change)
    admissible(state, action)     → (allowed, why_not) — a veto, never a ranking
    default                       → the action taken when nothing else survives (may be None)

An action is any object with:

    name                          → str
    cost_s                        → seconds it takes (the time is charged here, never inside `price`)
    commitment_s                  → seconds of that which cannot be abandoned (optional; defaults to cost_s)
    effect(state)                 → the state afterwards (pure: it must not mutate `state`)

Nothing here knows about Minecraft, health, dragons or inventories.
"""


class Choice:
    """What the planner decided, and everything needed to argue with it.

    `fault` non-empty means do not trust this: either the model reported a malformed state, or every productive
    action was refused and the default is paralysis wearing a decision's clothes.
    """

    __slots__ = ("action", "value_s", "cost_s", "considered", "rejected", "fault", "assumptions")

    def __init__(self, action, value_s=0.0, cost_s=0.0, considered=(), rejected=(), fault=(), assumptions=()):
        self.action = action
        self.value_s = value_s          # seconds the effect saves, before its own time is charged
        self.cost_s = cost_s            # seconds it takes
        self.considered = list(considered)   # [(score, action)] admissible, best first
        self.rejected = list(rejected)       # [(name, why)] refused by the veto
        self.fault = list(fault)
        self.assumptions = list(assumptions)

    @property
    def score(self):
        return self.value_s - self.cost_s

    @property
    def name(self):
        return getattr(self.action, "name", None)

    @property
    def commitment_s(self):
        """Seconds of the action that cannot be called off once begun. The next decision point is here, not
        earlier: re-deciding inside an atomic action is how an open-loop window gets abandoned half-open."""
        return commitment(self.action)

    def __repr__(self):
        return f"Choice({self.name}, {self.score:+.1f}s)"

    def explain(self):
        return (f"{self.name}: saves {self.value_s:.1f}s − {self.cost_s:.1f}s work = {self.score:+.1f}s"
                if self.action is not None else "nothing admissible")


def commitment(action):
    """How much of an action cannot be abandoned half-way. Defaults to all of it: an action that has not said
    which part is atomic is treated as atomic, which is the safe direction to be wrong in."""
    if action is None:
        return 0.0
    return float(getattr(action, "commitment_s", getattr(action, "cost_s", 0.0)))


def value(model, state, action):
    """Seconds this action saves: what the future costs now, minus what it costs after the action has happened.

    `estimate.saved_s` with nothing charged: its own duration is NOT subtracted here — that is `cost_s`, and it is
    subtracted once, in `score`. Folding the time into the price is how an action comes to look free.
    """
    from . import estimate
    return estimate.saved_s(lambda s: estimate.state_price_s(model, s), state, action.effect(state))


def survivors(model, state):
    """([action], [(name, why)]) — what the veto lets through, and why it stopped the rest.

    The default action is exempt: a veto that can refuse "get into cover" leaves the planner with no answer, and
    no answer is standing still.
    """
    allowed, rejected = [], []
    default = getattr(model, "default", None)
    actions = model.actions(state) if callable(model.actions) else model.actions
    for action in actions:
        if action is default:
            allowed.append(action)
            continue
        ok, why = model.admissible(state, action)
        if ok:
            allowed.append(action)
        else:
            rejected.append((action.name, why))
    return allowed, rejected


def choose(model, state, floor=0.0):
    """The action with the best score, or the model's default when nothing beats `floor`.

    `floor` is what doing nothing is worth — zero for a model whose price already accounts for carrying on. An
    action that scores below it is not offered: doing nothing is cheaper than doing that.
    """
    fault = list(model.fault(state)) if hasattr(model, "fault") else []
    assumptions = list(getattr(model, "assumptions", ()))
    default = getattr(model, "default", None)
    allowed, rejected = survivors(model, state)
    if not [a for a in allowed if a is not default]:
        fault.append(("no action", f"every productive action was refused ({len(rejected)})"))

    scored = sorted(((value(model, state, a) - a.cost_s, a) for a in allowed), key=lambda p: -p[0])
    best = next(((s, a) for s, a in scored if s > floor), None)
    if best is None:
        return Choice(default, considered=scored, rejected=rejected, fault=fault, assumptions=assumptions,
                      cost_s=getattr(default, "cost_s", 0.0) if default is not None else 0.0)
    score, action = best
    return Choice(action, value_s=score + action.cost_s, cost_s=action.cost_s, considered=scored,
                  rejected=rejected, fault=fault, assumptions=assumptions)


MARGIN = 1.05


class Held:
    """A chosen action, kept until there is a reason to change it.

    Three reasons, and no others: its commitment ran out, an assumption it was made under stopped holding, or a
    challenger beats it by `MARGIN` once it is revisitable. Every layer that re-decides faster than it acts needs
    this, and each one had been growing its own half of it.

    "The body refused me" is the second reason wearing different clothes: a decision that cannot be carried out is
    a decision made under an assumption that does not hold, so `note_denied` drops it and the next call decides
    afresh. Without that, a layer re-proposes the same refused answer at its own cadence, for ever.
    """

    def __init__(self, margin=MARGIN):
        self.margin = float(margin)
        self.choice = None
        self.since = 0.0
        self.because = None

    def note_denied(self, why=""):
        """The body would not run this. Drop it: whatever was assumed when it was chosen no longer holds."""
        self.choice, self.because = None, f"denied:{why}" if why else "denied"

    def expired(self, now):
        if self.choice is None or self.choice.action is None:
            return True
        return now - self.since >= max(1e-6, self.choice.commitment_s)

    def decide(self, model, state, now, holds=None):
        """The action to run now. `holds(choice, state)` answers whether its assumptions still stand."""
        if self.choice is not None and self.choice.action is not None:
            broken = holds is not None and not holds(self.choice, state)
            if not broken and not self.expired(now):
                self.because = None
                return self.choice
            self.because = "assumption" if broken else "commitment"
        fresh = choose(model, state)
        if self.choice is not None and self.choice.action is not None and fresh.action is not None \
                and fresh.name != self.choice.name and self.because != "assumption":
            staying = value(model, state, self.choice.action) - self.choice.action.cost_s
            if fresh.score < staying * self.margin:
                self.since = now
                return self.choice
        self.choice, self.since = fresh, now
        return fresh
