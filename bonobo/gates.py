"""Four doors. Everything that answers in seconds comes through one of them.

The planner grew sixty-one estimators. They were not sixty-one ideas — they were four, written down in a dozen
places, drifting apart between them: a bed behind a lava sheet came out worth MORE than one underfoot because the
value side knew about the lava and the cost side did not; an untested yield prior outbid a measured one because
nobody asked the same question about confidence twice.

    V(situation)              what finishing everything costs from here, in seconds
    takes_s(situation, work)  how long a piece of work takes, here, for this body
    p(situation, event)       how likely, how often: success, encounters, wear, what a world gives back
    marginal(situation, res)  what one more unit of something scarce costs: a slot, a second of drift, a hit

Each takes a `Situation` — the world, the body and what has been measured — rather than numbers somebody else
worked out first. That is the difference between a door and a wrapper: with a scalar, every quantity that MAKES a
price move (how full the bag already is, how hurt the body is, what this body can dig through) is flattened before
the door sees it, and no amount of arithmetic inside can put it back. A bag with two slots left and a bag with
thirty both arrived as "free"; a body that cannot dig and one that can both arrived as "a region"; a body at two
hearts and one at ten both arrived as "a survival state".

Pure: the situation carries a state vector, a region, a memory and a policy — never a live query — so all of this
is testable without a game and replays exactly.
"""
import math

from . import survival
from .beliefs import (CONFIG, TICKS_PER_S, cautious, detour_s, encounter_prior, expected_uses, slot_cost_s,
                      slots_cost_s, staleness_s, use_rate)
from .solve import reach_cost

ENDS = list(survival.END_DIMS.values())
UNREACHABLE_S = 1e6      # a good this world cannot offer is dear, not undefined: states stay comparable


class Go:
    """Getting from here to there. The body and the ground come from the situation, not from this."""
    __slots__ = ("here", "there")

    def __init__(self, here, there):
        self.here, self.there = tuple(here), tuple(there)


class Seek:
    """Going to the nearest one of these. `facts` answers where they are; the door turns that into seconds."""
    __slots__ = ("kinds", "facts", "ignore_known", "misses", "nearest_only")

    def __init__(self, kinds, facts=None, ignore_known=False, misses=0, nearest_only=False):
        self.kinds, self.facts = list(kinds), facts
        self.ignore_known, self.misses, self.nearest_only = bool(ignore_known), int(misses), bool(nearest_only)


class Do:
    """Work once we are there: this kind of thing, this many times."""
    __slots__ = ("kind", "token", "count", "facts")

    def __init__(self, kind, token, count=1, facts=None):
        self.kind, self.token, self.count, self.facts = kind, token, float(count), facts


class Run:
    """A planned step, at this world's measured durations."""
    __slots__ = ("step", "cost")

    def __init__(self, step, cost=None):
        self.step, self.cost = step, cost


class Short:
    """What a goal still lacks, priced off the round's table — how long, before a plan exists."""
    __slots__ = ("shortfall", "prices", "state")

    def __init__(self, shortfall, prices=None, state=None):
        self.shortfall, self.prices, self.state = dict(shortfall or {}), dict(prices or {}), state


class Situation:
    """Everything the four doors are allowed to look at: the world, the body in it, and what has been measured.

    One object, built once per round (or once per test cell), so every door sees the SAME facts. Nothing here is
    computed — these are the raw arguments, and each door derives what it needs.

        state     the solver's vector: what is held, what is within reach, what we are
        region    the blocks around us, when terrain matters (None: the straight line will do)
        policy    what this body may do to get through them — dig, build, climb, none of it
        mem       what this world has actually been observed to give back
        inv_free  slots still empty, so scarcity is a fact rather than an afterthought
        hp        health now, so what a hit costs can depend on how much is left
        wants     {id: (effect, worth_s)} the cerebrum asked for — handed over, like the columns, so the door
                  does not import the module that keeps them (`want.py`, docs/api.md)
    """

    __slots__ = ("state", "region", "policy", "mem", "costs", "columns", "route", "here", "hp", "inv_free",
                 "dark", "underground", "wants")

    def __init__(self, state=None, region=None, policy=None, mem=None, costs=None, columns=None, route=None,
                 here=None, hp=None, inv_free=None, dark=None, underground=False, wants=None):
        self.state = dict(state or {})
        self.region, self.policy, self.mem, self.costs = region, policy, mem, costs
        # The columns this world offers. A FACT, handed over — a door that builds its own action table has to
        # import the module that defines actions, and the bottom of the package then depends on the middle.
        self.columns = columns
        # How this body gets through that ground, as a function (region, here, there, policy) -> seconds. Injected
        # so the door does not import the navigator: what a cell costs is movement's business, and asking is the
        # door's.
        self.route = route
        self.here = tuple(here) if here else None
        self.hp = float(self.state.get("lever:hp", 20.0) if hp is None else hp)
        self.inv_free = float(self.state.get("bag_free", 36) if inv_free is None else inv_free)
        self.dark = bool(self.state.get("lever:dark") if dark is None else dark)
        self.underground = bool(underground)
        self.wants = dict(wants or {})

    def with_state(self, state):
        """The same situation seen from a different state — what `value.worth_s` does when it imagines a change."""
        return Situation(state=state, region=self.region, policy=self.policy, mem=self.mem, costs=self.costs,
                         columns=self.columns, here=self.here, hp=state.get("lever:hp", self.hp),
                         inv_free=state.get("bag_free", self.inv_free), dark=self.dark,
                         underground=self.underground, wants=self.wants)

    def sstate(self):
        """The survival model's levers for this situation. One reader, so an imagined state and a real one are
        described the same way."""
        return survival_of(self.state, hp=self.hp, bag_free=self.inv_free, dark=self.dark)

    def table(self):
        """The columns for this state. Handed over by whoever knows what this world offers; built here only when
        nobody did, and then through the one builder rather than a second copy of it."""
        if self.columns is None:
            raise ValueError("a situation prices nothing without the columns this world offers: pass columns=")
        return self.columns


def situation(state=None, **facts):
    """A `Situation`, however it is handed to us. Callers that still pass a bare state vector keep working, and
    what they lose is exactly what a bare vector cannot say."""
    if isinstance(state, Situation):
        return state
    return Situation(state=state, **facts)


# ------------------------------------------------------------------------------------------------ V: what is left

def V(state, parts=False, costs=None, sstate=None):
    """Seconds from here to everything finished: the terminal goods, plus what being without them costs.

    Takes a `Situation` (or a bare state vector, for callers that have nothing more to say). In PARTS, because
    the terminal goods compete for the same stock — sixteen planks can become a bed or a shelter, not both — and
    `value.gain` takes the biggest single saving among them rather than their sum. The loss term is why anyone
    wants a bed at all: the price says how far away one is, `survival.expected_loss` says what the night without
    it costs.
    """
    s = situation(state, costs=costs)
    prices = reach_cost(s.table(), s.state)
    out = {f"end:{dim}": min(prices.get(dim, UNREACHABLE_S), UNREACHABLE_S) for dim in ENDS}
    # What the cerebrum asked for is a terminal good like any other: it costs seconds to be without it, capped at
    # min(what it is worth, what reaching it costs), and it competes with the bed rather than adding to it.
    for wid, (effect, worth_s) in s.wants.items():
        short = sum(max(0.0, count - float(s.state.get(dim, 0) or 0)) for dim, count in effect.items())
        price = sum(prices.get(dim, UNREACHABLE_S) for dim in effect) if short else 0.0
        out[f"end:want:{wid}"] = min(worth_s, price)
    out["loss"] = survival.expected_loss(sstate or s.sstate())
    return out if parts else sum(out.values())


def survival_of(state, hp=None, bag_free=None, dark=None):
    """The survival model's levers, read off a state vector plus the facts a vector has no dimension for.

    Health, room in the bag and the dark are not things the solver can hold, so they arrive alongside — and they
    have to arrive, because they are exactly what makes a hit, a slot or an hour cost what it costs.
    """
    held = lambda dim: float(state.get(dim, 0) or 0)   # noqa: E731
    return survival.make_state(
        night=bool(state.get("lever:night")), ticks_until_dusk=int(state.get("lever:dusk", 6000)),
        hp=float(state.get("lever:hp", 20) if hp is None else hp),
        food=float(state.get("food", state.get("lever:food", 20))),
        bed=bool(held("bed")), sheltered=bool(held("sheltered")),
        torches=held(survival.END_DIMS["torches"]) >= survival.TORCHES_MEAN,
        sword=1 if held(survival.END_DIMS["sword"]) else 0,
        pickaxe=1 if held(survival.END_DIMS["pickaxe"]) else 0,
        food_items=held("food"), nights_missed=int(state.get("lever:nights_missed", 0)),
        armor=float(state.get("lever:armor", 0)), shield=bool(state.get("lever:shield")),
        bag_free=float(state.get("bag_free", 36) if bag_free is None else bag_free),
        dark=bool(state.get("lever:dark") if dark is None else dark))


# --------------------------------------------------------------------------------------- takes_s: how long, here

def takes_s(situation_, work=None):
    """Seconds one piece of work takes, for THIS body, HERE.

        takes_s(s, Go(here, there))          getting there: walking, digging, bridging, whatever it takes
        takes_s(s, Seek(kinds, facts))       going to the nearest one of something
        takes_s(s, Do(kind, token, count))   work once we are there
        takes_s(s, Run(step, cost))          a planned step, at this world's measured durations
        takes_s(s, Short(shortfall, prices)) what a goal still lacks, before a plan exists

    The work is a TYPE, not a dictionary with magic keys: a door that dispatches on `"go" in work` is a door
    whose interface is whatever its callers happened to write. The situation carries the body and the ground, so
    the same wall costs one thing to a body with a pickaxe and another to a body without.
    """
    if work is None:
        situation_, work = None, situation_
    s = situation_ if isinstance(situation_, Situation) else (situation(situation_) if situation_ else None)
    costs = s.costs if s else None
    if isinstance(work, Go):
        straight = max(0.0, math.dist(work.here, work.there) / float(CONFIG["player"]["speed"]))
        if s is None or s.region is None:
            return straight
        if s.route is None:
            # A region with nobody to read it is no better than no region: the straight line, and the caller is
            # told what it left out by getting exactly that. (The brain injects `route=nav.estimate_price_s`.)
            return straight
        return s.route(s.region, work.here, work.there, s.policy)
    if isinstance(work, Seek):
        facts = work.facts or costs
        if work.nearest_only:
            return facts.walk_s(work.kinds, misses=work.misses)
        return facts.seek_s(work.kinds, ignore_known=work.ignore_known)
    if isinstance(work, Do):
        facts = work.facts or costs
        return float(facts.work_s(work.kind, work.token)) * float(work.count)
    if isinstance(work, Run):
        oracle = work.cost or costs
        if oracle is None:
            return 0.0
        return float(oracle.estimate(work.step)) / TICKS_PER_S
    if isinstance(work, Short):
        state = work.state if work.state is not None else (s.state if s else {})
        total = 0.0
        for dim, want in work.shortfall.items():
            left = float(want) - float(state.get(dim, 0))
            if left <= 0:
                continue
            per = work.prices.get(dim)
            if per is None or per == float("inf"):
                return float(UNREACHABLE_S)
            total += float(per) * left
        return total
    raise TypeError(f"no such work: {work!r}")


# ------------------------------------------------------------------------------------- p: how likely, how often

def p(situation_, event=None, mem=None, **facts):
    """How likely, or how often per second, IN THIS SITUATION.

    Unmeasured beliefs are read at their cautious end (`beliefs.cautious`), so faith never outbids measurement;
    what a world has actually given back moves them and nothing else does.

        p(s, "yield", name=…)       what an attempt of this returns, against what was declared
        p(s, "encounter")           hostiles met per second of being out there, in this light, at this depth
        p(s, "success", key=…)      how often this step has worked
        p(s, "tool_use", kind=…)    how often a tool of this kind is reached for, per second
        p(s, "tool_left", kind=…)   how many uses of it are still ahead of us
        p(s, "stale", age_s=…)      how much worse a note has become with age
    """
    if isinstance(situation_, str) and event is None:
        situation_, event = None, situation_       # p("stale", age_s=…) — no situation to speak of
    s = situation_ if isinstance(situation_, Situation) else (situation(situation_) if situation_ else None)
    mem = mem if mem is not None else (s.mem if s else None)
    if event == "yield":
        rate = mem.yield_rate(facts["name"]) if mem is not None else 1.0
        return max(0.0, float(rate))
    if event == "encounter":
        dark = bool(facts.get("dark", s.dark if s else False))
        underground = bool(facts.get("underground", s.underground if s else False))
        prior = encounter_prior(dark)
        return prior if mem is None else mem.encounter_rate(dark, underground, prior_rate=prior)
    if event == "success":
        rate = mem.success_rate(facts["key"]) if mem is not None else None
        return 1.0 if rate is None else float(rate)
    if event == "tool_use":
        return use_rate(facts["kind"], mem)
    if event == "tool_left":
        return expected_uses(facts["kind"], mem, float(facts.get("left", 0.0)),
                             float(facts.get("horizon_s", CONFIG["time"]["day_s"])))
    if event == "stale":
        fresh = float(CONFIG["memory"]["fresh_s"])
        return math.log2(1.0 + max(0.0, float(facts.get("age_s", 0.0))) / fresh)
    raise KeyError(f"no such chance: {event!r}")


def exposure_s(seconds, sstate, mem=None, underground=False):
    """Seconds that being out there for `seconds` is expected to cost, in blood priced as time.

        seconds × p(encounter) × what one encounter costs

    Not a fifth door: it is `p` and `marginal("blood")` multiplied out, and it enters the world through V — a
    state where we are standing in the dark is a state whose completion costs more.
    """
    if seconds <= 0:
        return 0.0
    dark = bool(sstate.get("dark", sstate.get("night")))
    rate = p(None, "encounter", mem=mem, dark=dark, underground=underground)
    fight_s, damage = survival.encounter_damage(sstate)
    return round(float(seconds) * rate * (survival.hp_seconds(sstate, damage) + fight_s), 2)


# ---------------------------------------------------------------- marginal: what one more unit of scarcity costs

def marginal(resource, situation_=None, **facts):
    """Seconds one more unit of a scarce thing costs, IN THIS SITUATION.

        marginal("slot", s)             the inventory slot this would eat, dearer as the bag fills
        marginal("staleness", age_s=…)  what an old note costs: a worse guess, never a deleted one
        marginal("pickup")              picking one more item up once it is broken and we are standing there
        marginal("detour", …)           what going out of the way adds to a journey we were making anyway
        marginal("blood", s)            what one point of health costs, given how much of it is left

    The situation is what makes these prices move. Handed a number instead — "free slots: 12" — the door can only
    repeat what the caller already decided, which is how a full bag came to charge the same as an empty one for
    everything except the very last slot.
    """
    s = situation_ if isinstance(situation_, Situation) else (situation(situation_) if situation_ else None)
    if resource == "slot":
        free = float(facts.get("free", s.inv_free if s else 36))
        # What this one slot costs, and what the ones after it will: a bag with two left is not "nearly the same"
        # as a bag with thirty, and the caller should not have to know that.
        cost = slot_cost_s(free)
        return 0.0 if cost < 1e-3 else cost
    if resource == "slots":
        # Several at once, each priced against the bag as it will be by then (`beliefs.slots_cost_s`).
        return slots_cost_s(int(facts.get("count", 1)), float(facts.get("free", s.inv_free if s else 36)))
    if resource == "staleness":
        return staleness_s(facts.get("age_s", 0.0))
    if resource == "pickup":
        return cautious("batch.pick_s", "cost")
    if resource == "detour":
        return detour_s(float(facts["distance"]), here=facts.get("here"), there=facts.get("there"),
                        via=facts.get("via"))
    if resource == "blood":
        sstate = facts.get("sstate") or (s.sstate() if s else None)
        if sstate is None:
            raise KeyError("blood needs a situation or a survival state: what a hit costs depends on the body")
        return survival.hp_seconds(sstate, 1.0)
    raise KeyError(f"nothing scarce called {resource!r}")
