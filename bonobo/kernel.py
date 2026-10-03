"""The planner. One function; the fight planner and ordinary play are the same function with a different model. They were two planners because they look different from inside — one thinks in hit points over four seconds, the other in beds and pickaxes over a day. They are not different. Both hold a state, both can price a state in seconds, both have a set of things they could do, each of which changes the state and costs time, and both want the thing that buys the most seconds. That is this module, and it is the whole of it: score(action) = price(state) − price(action.effect(state)) − action.cost_s `price` is the model's, and it is the ONLY place a number turns into seconds. There are no benefit tables and no urgency multipliers: a thing is worth the difference it makes to what the future costs. A bed is worth the night it removes; a tunnel is worth the exposure it removes from the windows still to come; neither needs a number written next to it. What the two models differ in is the horizon and the SCALE of the commitment, nothing else. Both commit; neither re-decides continuously, because a decision that can be revised at any instant is not a decision: normal   horizon a day       commitment = the plan's assumptions, minutes; released when one stops holding combat   horizon seconds     commitment = the action's atomicity, seconds; released when the action ends `cost_s` is what the action takes; `commitment_s` is how much of that cannot be abandoned half-way. They differ: a 0.4 s firing window is open-loop — there is no state in the middle of it to re-decide from — while a walk to a vein can be dropped after one step. The veto reads `commitment_s`, not `cost_s`: what must be survived is the part that cannot be called off. A model is any object with: price(state)                  → seconds the future costs from here actions(state)                → iterable of actions; or a plain attribute holding them, when the set does not depend on the state (a fight's actions come from its profile and never change) admissible(state, action)     → (allowed, why_not) — a veto, never a ranking default                       → the action taken when nothing else survives (may be None) An action is any object with: name                          → str cost_s                        → seconds it takes (the time is charged here, never inside `price`) commitment_s                  → seconds of that which cannot be abandoned (optional; defaults to cost_s) effect(state)                 → the state afterwards (pure: it must not mutate `state`) Nothing here knows about Minecraft, health, dragons or inventories."""
import math
from collections.abc import Iterable
from typing import cast

from . import estimate

class Choice:
    """What the planner decided, and everything needed to argue with it."""

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
        """Seconds of the action that cannot be called off once begun."""

        return commitment(self.action)

    def __repr__(self):
        return f"Choice({self.name}, {self.score:+.1f}s)"

    def explain(self):
        return (f"{self.name}: saves {self.value_s:.1f}s − {self.cost_s:.1f}s work = {self.score:+.1f}s"
                if self.action is not None else "nothing admissible")

def commitment(action):
    """How much of an action cannot be abandoned half-way."""

    if action is None:
        return 0.0
    return float(getattr(action, "commitment_s", getattr(action, "cost_s", 0.0)))

def action_value(model, state, action):
    """Seconds this action saves: what the future costs now, minus what it costs after the action has happened."""

    return estimate.saved_s(lambda s: estimate.state_price_s(model, s), state, action.effect(state))

def survivors(model, state):
    """([action], [(name, why)]) — what the veto lets through, and why it stopped the rest."""

    allowed, rejected = [], []
    default = getattr(model, "default", None)
    # a fn of the state, or a plain attribute holding them (the module doc's model contract)
    actions = cast(Iterable, model.actions(state) if callable(model.actions) else model.actions)
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

def choose(model, state, floor=0.0) -> Choice:
    """The action with the best score, or the model's default when nothing beats `floor`."""

    fault = list(model.fault(state)) if hasattr(model, "fault") else []
    assumptions = list(getattr(model, "assumptions", ()))
    default = getattr(model, "default", None)
    allowed, rejected = survivors(model, state)
    if not [a for a in allowed if a is not default]:
        fault.append(("no action", f"every productive action was refused ({len(rejected)})"))

    scored = sorted(((action_value(model, state, a) - a.cost_s, a) for a in allowed), key=lambda p: -p[0])
    best = next(((s, a) for s, a in scored if s > floor), None)
    if best is None:
        return Choice(default, considered=scored, rejected=rejected, fault=fault, assumptions=assumptions,
                      cost_s=getattr(default, "cost_s", 0.0) if default is not None else 0.0)
    score, action = best
    return Choice(action, value_s=score + action.cost_s, cost_s=action.cost_s, considered=scored,
                  rejected=rejected, fault=fault, assumptions=assumptions)

def lost_s(action, elapsed):
    """Pure: seconds of `action`'s work an abandon now throws away — what it did so far, less the share its effect
    keeps in the world (`kept`, 0..1: dug cells stay dug, a hit's damage stays; 0 when the action does not say);
    nothing once it has run its course (cost_s)."""
    if elapsed >= float(getattr(action, "cost_s", 0.0)):
        return 0.0
    return elapsed * (1.0 - float(getattr(action, "kept", 0.0)))

def switches(fresh_score, staying, lost, noise=0.0):
    """Pure: the switch rule — a new choice replaces the held one only when its gain over what is left of the held
    one (re-priced now) pays for the work the switch throws away and for the noise of the two estimates themselves
    (`noise`: the spread their re-pricings showed). A tie keeps."""
    return fresh_score - staying > lost + noise

def spread(values):
    """Pure: the population standard deviation of an estimate's recent re-pricings (0 with fewer than two)."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

class Held:
    """A chosen action, kept until a new one's gain pays the switch (`switches`), or its assumption breaks."""

    def __init__(self):
        self.choice = None
        self.since = 0.0
        self.because = None
        self.seen = {}           # action name → its scores on every reading since the held choice was made

    def note_denied(self, why=""):
        """The body would not run this. Drop it: whatever was assumed when it was chosen no longer holds."""
        self.choice, self.because = None, f"denied:{why}" if why else "denied"

    def _take(self, choice, now):
        self.choice, self.since, self.seen = choice, now, {}
        return choice

    def decide(self, model, state, now, holds=None):
        """The action to run now. `holds(choice, state)` answers whether its assumptions still stand."""
        held = self.choice if self.choice is not None and self.choice.action is not None else None
        if held is not None and holds is not None and not holds(held, state):
            self.because = "assumption"
            return self._take(choose(model, state), now)
        fresh = choose(model, state)
        for score, action in fresh.considered:
            self.seen.setdefault(action.name, []).append(score)
        if held is not None and (fresh.action is None or fresh.name == held.name):
            self.because = None
            return held
        if held is not None:
            elapsed = now - self.since
            # the held action re-priced on this state for what is LEFT of it
            staying = action_value(model, state, held.action) - max(0.0, held.action.cost_s - elapsed)
            noise = spread(self.seen.get(held.name, [])) + spread(self.seen.get(fresh.name, []))
            if not switches(fresh.score, staying, lost_s(held.action, elapsed), noise):
                self.because = None
                return held
            self.because = "better"
        return self._take(fresh, now)
