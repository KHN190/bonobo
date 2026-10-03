"""The one planner: needs like [('minecraft:bucket', 1), ('tool', 'pickaxe', 2)] in, the cheapest ordered steps out.

Every way to get a thing is an action read from the registered skills' producing tables (knowledge.sources): each
producer, each tool tier, each fuel, each food, each speed tool a step's work pays for. One search over them:
  1. a lower bound per unit of every token (`unit_lb`: the cheapest derivation's work, a relaxation — positive costs,
     so a fixpoint, cycles included); a token it cannot price has no way at all;
  2. the requirement chain resolved in order (inputs before the step that uses them), the inventory simulated as it
     goes; where a need has more than one way, the search branches (A*: f = the steps chosen + the bound of what is
     left; the first plan found by the cheapest bound is the incumbent, nothing at or above it is expanded);
  3. a complete plan is priced exactly by a forward pass (`forward`): run in an order that walks least between
     the places its steps happen, each step's walk from where the one before left the body.
Pure apart from what the cost model reads, so it is tested offline (NullCost)."""

import heapq
import itertools
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import api, lifecycle
from .api import McError
from .data import GROUPS, OVERWORLD, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER, TOOL_USES, bare, mid
from .beliefs import CONFIG, TICKS_PER_S, fights_back
from .knowledge import (ALL_FOOD, body_facts, dig_to_ticks, have_remainder, members, needs_rows, own_work, prior_work_ticks, sources, step_call, step_station, tool_item, tool_kind, spare_uses, work_s, working)
from .data import HUNT_YIELD, MINE_YIELD, TAKEABLE

MAX_DEPTH = 14
MAX_NODES = 400       # A* expansions past the incumbent (~0.1 s measured): spent, the best complete plan found
                      # stands — the incumbent at least (D1: an answer, never a hang)
MERGEABLE = {"mine", "gather", "hunt", "smelt"}
STATIONS = frozenset(("minecraft:crafting_table", "minecraft:furnace"))     # what way() works at, never used up
FOOD_IDS = frozenset(mid(f) for f in ALL_FOOD)          # what the eat reflex eats
MAKES_FOOD = ("smelt", "craft", "take", "withdraw", "trade", "await")   # steps that put food in the bag
MERGEABLE_CRAFTS = {"planks", "minecraft:stick", "minecraft:torch", "minecraft:ladder"}
REORDER_MAX = 10      # steps with a known place an order is searched over exactly (2^n states)


class Unplannable(McError):
    pass


@dataclass
class Step:
    kind: str            # craft | smelt | mine | gather | hunt | ... — or any effect a skill provides (skill.providers)
    token: str           # item id or group token produced
    count: int           # units of `token` to end up with (craft: output items)
    detail: dict[str, Any] = field(default_factory=dict)    # the call's arguments: pos, types, blocks, args, ...
    est: int = 0         # estimated ticks

    def key(self):
        return self.kind, self.token

    def __str__(self):
        return f"{self.kind} {self.count}× {bare(self.token)} (~{self.est // TICKS_PER_S}s)"


class VirtualInventory:
    """Counts what we'd hold after the planned steps run."""

    def __init__(self, counts, tools, pending=None, facts=None):
        self.facts = dict(facts or {})      # what is true of the world for the plan (dimension, a portal here, …)
        self.counts = Counter(counts)       # item id -> count (held, and `pending` on its way)
        self.produced = Counter()           # group token -> count produced by planned steps
        self.tools = [list(t) for t in tools]   # [kind, tier, uses left]
        self.pending = Counter(pending or {})   # item id -> of `counts`, what a job is still making (not in the bag)
        self.awaited = Counter()            # item id -> pending the plan uses: to be collected before it is used

    def clone(self):
        out = VirtualInventory.__new__(VirtualInventory)
        out.counts, out.produced, out.pending, out.awaited = (Counter(self.counts), Counter(self.produced),
                                                              Counter(self.pending), Counter(self.awaited))
        out.tools = [list(t) for t in self.tools]
        out.facts = dict(self.facts)
        return out

    def available(self, token):
        return sum(self.counts[m] for m in members(token)) + self.produced[token]

    def consume(self, token, n, awaits=True):
        """Use `n` of token: what the plan makes first, then the bag, then what a job is still making — that last
        noted as awaited (`awaits`: a step uses it; a top-level need met by an output on its way awaits nothing)."""
        take = min(n, self.produced[token])
        self.produced[token] -= take
        n -= take
        for m in members(token):
            if n <= 0:
                break
            take = min(n, self.counts[m])
            held = self.counts[m] - self.pending[m]
            if take > held:                  # the rest comes from a job not yet collected
                late = take - max(0, held)
                self.pending[m] -= late
                if awaits:
                    self.awaited[m] += late
            self.counts[m] -= take
            n -= take

    def add(self, token, n):
        if token in GROUPS or token == "food":
            self.produced[token] += n
        else:
            self.counts[mid(token)] += n

    def signature(self):
        return (tuple(sorted((k, v) for k, v in self.counts.items() if v)),
                tuple(sorted((k, v) for k, v in self.produced.items() if v)),
                tuple(sorted(tuple(t) for t in self.tools)),
                tuple(sorted((k, v) for k, v in self.pending.items() if v)),
                tuple(sorted((k, v) for k, v in self.awaited.items() if v)),
                tuple(sorted(self.facts.items())))

    def has_tool(self, kind, tier, uses=1):
        return any(k == kind and t >= tier and u >= uses for k, t, u in self.tools)

    def held(self):
        """{tool kind: the best tier with a use left}."""
        out = {}
        for k, t, u in self.tools:
            if u > 0 and t in TOOL_MATERIAL_FOR_TIER:
                out[k] = max(out.get(k, t), t)
        return out

    def wear(self, kind, tier, n):
        """`n` uses off the best tool of `kind` at `tier` or above that has them (the one the work holds)."""
        fit = [t for t in self.tools if t[0] == kind and t[1] >= tier and t[2] >= n]
        if fit:
            max(fit, key=lambda t: (t[1], -t[2]))[2] -= n


def signature(tasks):
    """The tasks as a key: steps by what they are, not by which object."""
    def part(x):
        if isinstance(x, Step):
            return (x.kind, x.token, x.count, repr(sorted(x.detail.items())))
        return x
    return tuple(tuple(part(x) for x in t) for t in tasks)


def from_bag(inv, extra=None, pending=None, reserved=(), facts=None):
    """A virtual inventory from the bag: `extra` counted as held beyond it (a planned source's output, a running job's
    output), `pending` the part of it a job is still making (awaited when a step takes it), `reserved` (bag.RESERVED:
    what the held plans will consume) left out."""
    counts = Counter()
    for item, n in (extra or {}).items():
        counts[item] += n
    for s in inv.slots:
        counts[s["id"]] += s["count"]
    off = inv.equipment.get("offhand") or {}
    if off.get("count"):
        counts[off["id"]] += off["count"]
    for slot in ("head", "chest", "legs", "feet"):
        s = inv.equipment.get(slot) or {}
        if s.get("count"):
            counts[s["id"]] += 1
    for item in reserved or ():
        counts.pop(mid(item), None)
    tools = [(kind, t, spare_uses(d)) for kind in TOOL_KINDS for t, d, _ in inv.tools(kind) if working(d)] \
        if hasattr(inv, "tools") else []
    return VirtualInventory(counts, tools, {mid(k): v for k, v in (pending or {}).items()}, facts)


def hunts_a_fighter(types):
    """Does this hunt target something that fights back (beliefs.fights_back)? Animals do not."""
    return fights_back(types)


def use_rank(kind):
    """A tool kind's place in play.toml tools.use_order (most used first): the tie between equal plans."""
    order = CONFIG["tools"]["use_order"]
    return next((i for i, g in enumerate(order) if kind in g), len(order))


# -- the ways one source makes `n` of a token: (step, the needs before it, what its run adds back)

def way(src, token, n, ripe=0):
    """(step, [(input token, amount)] in the order they are needed, station or None, [(token, amount)] added after)
    for making `n` of token by `src`; None for a source the planner does not run."""
    kind = src[0]
    if kind == "craft":
        _, pattern, out = src
        times = math.ceil(n / out)
        inputs = Counter(p for p in pattern if p)
        step = Step("craft", token, times * out, {"times": times, "inputs": {t: c * times for t, c in inputs.items()}})
        return step, [(t, c * times) for t, c in inputs.items()], \
            ("minecraft:crafting_table" if len(pattern) == 9 else None), [(token, times * out)]
    if kind == "smelt":
        _, inp = src
        return Step("smelt", token, n, {"input": inp, "inputs": {inp: n}}), [(inp, n)], "minecraft:furnace", []
    if kind == "mine":
        _, blocks, tier = src
        per = MINE_YIELD.get(mid(token), 1)
        return Step("mine", token, n, {"blocks": blocks, "tier": tier, "breaks": math.ceil(n / per)}), [], None, []
    if kind == "gather":
        return Step("gather", token, n, {}), [], None, []
    if kind == "fill":
        _, container = src
        return Step("fill", token, n, {"container": container, "inputs": {container: 1}}), [(container, 1)], None, []
    if kind == "hunt":
        _, types = src
        per = HUNT_YIELD.get(token, HUNT_YIELD.get(mid(token), 1))
        return Step("hunt", token, n, {"types": types, "kills": math.ceil(n / per),
                                       "fighter": hunts_a_fighter(types)}), [], None, []
    if kind == "farm":
        if ripe:
            return Step("take", token, n, {"blocks": list(TAKEABLE[token]["blocks"])}), [], None, []
        _, seeds, per = src
        plots = math.ceil(n / per)
        step = Step("farm", token, plots * per, {"plots": plots, "inputs": {"minecraft:water_bucket": plots}})
        return step, [(seeds, per), ("minecraft:water_bucket", plots)], None, [(seeds, per), (token, plots * per)]
    if kind == "trade":
        _, types = src
        return Step("trade", token, n, {"types": types}), [], None, []
    if kind == "take":
        _, blocks = src
        return Step("take", token, n, {"blocks": list(blocks)}), [], None, []
    if kind == "barter":
        _, types, per_ingot = src
        ingots = math.ceil(n / per_ingot)
        return Step("barter", bare(types[0]), n, {"ingots": ingots, "types": list(types),
                                                  "inputs": {"minecraft:gold_ingot": ingots}}), \
            [("minecraft:gold_ingot", ingots)], None, []
    return None


FUELS = (("coal", 8), ("planks", 1.5))      # what a furnace burns: items smelted per unit (Minecraft Wiki, Fuel)


def fuel_need(fuel, n):
    per = dict(FUELS)[fuel]
    return math.ceil(n / per)


# -- the bound: what one unit of a token costs at the least, from the tables alone

BEST_TOOLS = {k: max(TOOL_MATERIAL_FOR_TIER) for k in TOOL_KINDS}


class Bound:
    """What a token costs at the least, from the tables: never above what a plan pays, whatever the bag holds.
    `scratch[token]` — ticks one unit's whole derivation takes from nothing (each way's own work with the best tools,
    no walking, the tools it needs free): a fixpoint over the producers, cycles included;
    `least(token, n, held)` — the same, crediting what is held at every level (an item held is free wherever it is
    asked; credited to each branch, so never too high); where nothing of a token's derivation is held, `scratch`;
    `tools[token]` — {tool kind: tier} every way to make it needs held at some point (the intersection over its
    sources, through its inputs). A plan costs at least the sum of its needs' `least`, and at least any one tool it
    must still make — never their sum."""

    def __init__(self, cost):
        from .knowledge import producers
        self.ways = {}      # asked token → [(ticks per unit, {input: per unit}, {tool kind: tier})]
        self.shapes = {}    # asked token → [(its unit step, the station it works at or None, {input: per unit})]
        tokens = {t for g in producers() for t in g.keys()} | set(GROUPS) | {"food"}
        for asked in sorted(tokens):
            for token, src in sources(asked) if asked != "food" else [(f, s) for f in ALL_FOOD for _m, s in
                                                                         sources(f)]:
                got = way(src, token, 1)
                if got is None:
                    continue
                step, inputs, _station, _adds = got
                out = max(step.count, 1)
                if step.kind == "mine":
                    step = Step("mine", token, 1, {**step.detail, "breaks": 1})
                    out = MINE_YIELD.get(mid(token), 1)
                elif step.kind == "hunt":
                    step = Step("hunt", token, 1, {**step.detail, "kills": 1})
                    out = HUNT_YIELD.get(token, HUNT_YIELD.get(mid(token), 1))
                need = {}
                for dim in step_call(step):
                    if dim.startswith("tool:"):
                        _, kind, tier = dim.split(":")
                        need[kind] = max(need.get(kind, 0), int(tier))
                ins = {t: c / out for t, c in inputs if step.kind != "farm"}
                self.ways.setdefault(asked, []).append((self.per_run(cost, step) / out, ins, need))
                self.shapes.setdefault(asked, []).append((step, _station, ins))
        scratch = {}
        for _ in range(len(self.ways) + 1):
            changed = False
            for asked, ways_ in self.ways.items():
                best = min((per + sum(self._of(scratch, t) * c for t, c in ins.items()) for per, ins, _n in ways_),
                           default=math.inf)
                if best < scratch.get(asked, math.inf) - 1e-9:
                    scratch[asked] = best
                    changed = True
            if not changed:
                break
        self.scratch = scratch
        self.reach = {}     # token → every token its derivation may use (its inputs, theirs, …)
        for asked in self.ways:
            seen, todo = set(), [asked]
            while todo:
                t = todo.pop()
                for _per, ins, _n in self.ways.get(t, self.ways.get(mid(t), ())):
                    for x in ins:
                        if x not in seen:
                            seen.add(x)
                            todo.append(x)
            self.reach[asked] = seen | {mid(x) for x in seen} | {mid(m) for x in seen for m in GROUPS.get(x, ())}
        tools: dict = {}
        for _ in range(len(self.ways) + 1):
            changed = False
            for asked, ways_ in self.ways.items():
                got = None
                for _per, ins, need in ways_:
                    here = dict(need)
                    for t in ins:
                        for k, v in self.tools_of(tools, t).items():
                            here[k] = max(here.get(k, v), v)
                    got = here if got is None else {k: min(v, here[k]) for k, v in got.items() if k in here}
                if got is not None and got != tools.get(asked, {}):
                    tools[asked] = got
                    changed = True
            if not changed:
                break
        self.tools = tools
        stations: dict = {}  # token → the stations every way to make it works at (through its inputs)
        for _ in range(len(self.shapes) + 1):
            changed = False
            for asked, shapes in self.shapes.items():
                got = None
                for _step, station, ins in shapes:
                    here = ({station} if station else set()).union(*(self.tools_of(stations, t) or set() for t in ins))
                    got = here if got is None else got & here
                if got and got != stations.get(asked):
                    stations[asked] = got
                    changed = True
            if not changed:
                break
        self.stations = stations

    @staticmethod
    def per_run(cost, step):
        """Ticks one more run of `step` adds to its work (its own work for two runs less for one, the best tools):
        what scales with the count — a step's fixed part (a craft's sitting, a furnace's setup) is paid once
        however many needs share it, so never counted per unit."""
        def runs(k):
            detail = dict(step.detail)
            for key in ("breaks", "kills", "times"):
                if key in detail:
                    detail[key] = detail[key] * k
            return Step(step.kind, step.token, step.count * k, detail)
        return max(0, cost.work(runs(2), BEST_TOOLS) - cost.work(runs(1), BEST_TOOLS))

    def _of(self, table, token):
        for t in (token, mid(token)):
            if t in table:
                return table[t]
        return math.inf

    @staticmethod
    def tools_of(tools, token):
        for t in (token, mid(token)):
            if t in tools:
                return tools[t]
        return {}

    def needed(self, token):
        return self.tools_of(self.tools, token)

    def least(self, token, n, held, memo, path=()):
        """Ticks `n` of token cost at the least, crediting what is held (`held(token)` → count) at every level."""
        n = n - held(token)
        if n <= 0:
            return 0.0
        key = (token, n)
        if key in memo:
            return memo[key]
        heldset = memo.setdefault("held", None)
        reach = self.reach.get(token, self.reach.get(mid(token), set()))
        if heldset is not None and not (reach & heldset) or token in path:
            got = n * self._of(self.scratch, token)
        else:
            got = min((n * per + sum(self.least(t, c * n, held, memo, path + (token,)) for t, c in ins.items())
                       for per, ins, _need in self.ways.get(token, self.ways.get(mid(token), ()))),
                      default=math.inf)
        memo[key] = got
        return got


_BOUNDS = {}      # the offline bound (NullCost reads nothing): one per registry state
lifecycle.in_place(__name__, "_BOUNDS")


def bound(cost):
    """The tables' bound (`Bound`), cached on the cost model (the offline one: per registry state)."""
    from . import knowledge
    if isinstance(cost, NullCost):
        key = (tuple(id(p) for p in knowledge.PRODUCERS), id(knowledge.STEP_CALL))
        if key not in _BOUNDS:
            _BOUNDS.clear()
            _BOUNDS[key] = Bound(cost)
        return _BOUNDS[key]
    cache = getattr(cost, "cache", None)
    if cache is not None and "bound" in cache:
        return cache["bound"]
    got = Bound(cost)
    if cache is not None:
        cache["bound"] = got
    return got


# -- the search

@dataclass
class Node:
    inv: VirtualInventory
    stack: list                 # tasks still to run, last first
    steps: list                 # (step, held tiers when it runs, indexes of the steps it needs)
    g: float = 0.0              # ticks of the chosen steps (their walks at the least)
    tie: tuple = ()             # the choices made, as (tier, use order, option): the lesser plan of two equal ones
    horizon: int = 0            # the stack's length once the choice that made this node is settled
    asked: str = ""             # what that choice was about (said when none of its options can be had)

    def child(self):
        return Node(self.inv.clone(), list(self.stack), list(self.steps), self.g, self.tie, self.horizon, self.asked)


class Search:
    def __init__(self, cost, kinds=None):
        self.hungry = getattr(cost, "hunger_rate", lambda: 0.0)()
        self.cost = cost
        self.kinds = kinds              # step kinds allowed (None: all)
        self.lb = bound(cost)
        self.counter = itertools.count()
        self.reasons = []
        self.memo = {}
        self._sources = {}
        self.considered = []        # every complete plan priced: (name, seconds, steps) — the round's alternatives
        from . import knowledge
        knowledge.producers()                 # the skills registered: their hooks below are wired
        self.call = knowledge.STEP_CALL or (lambda step: {})
        self.when = knowledge.STEP_WHEN or (lambda step, facts: [])
        self.sets = knowledge.STEP_SETS or (lambda step: {})
        self.used = knowledge.STEP_USES or (lambda step: {})
        self.fact_steps = knowledge.FACT_STEPS or (lambda fact, value: [])

    def sources(self, token):
        """knowledge.sources, asked once a plan (the registry does not change within one)."""
        if token not in self._sources:
            self._sources[token] = sources(token)
        return self._sources[token]

    # -- what is left: its bound
    def h(self, node, floor=0):
        """The least what is left (above `floor`) can cost: its needs' least (what is held credited), or the dearest
        tool it must still make — the larger."""
        units, tool = 0.0, 0.0
        inv = node.inv
        held = inv.available
        memo: dict = {"held": {k for k, v in inv.counts.items() if v > 0} | {k for k, v in inv.produced.items() if v > 0}}
        for task in node.stack[floor:]:
            op = task[0]
            if op == "need":
                _op, token, n, _depth, fresh = task
                units += self.lb.least(token, n + (held(token) if fresh else 0), held, memo)
                if not self.lb.reach.get(token, set()) & memo["held"]:     # nothing of its making held: a tool it
                    for kind, tier in self.lb.needed(token).items():       # must have is still to be had
                        if not inv.has_tool(kind, tier):
                            tool = max(tool, self.lb.least(tool_item(kind, tier), 1, held, memo))
            elif op == "tool":
                _op, kind, tier, uses, _depth = task
                if not inv.has_tool(kind, tier, uses):
                    tool = max(tool, self.lb.least(tool_item(kind, tier), 1, held, memo))
        station, walk = 0.0, 0.0
        for task in node.stack[floor:]:
            if task[0] != "need" or held(task[1]) >= task[2]:
                continue
            for s in self.lb.tools_of(self.lb.stations, task[1]) or ():
                if held(s) <= 0 and not self.cost.station_near(s) and not getattr(self.cost, "stored", lambda t: [])(s):
                    station = max(station, self.lb.least(s, 1, held, memo))
            walk = max(walk, self.reach_lb(task[1], held, set()))
        return max(units + walk, tool, station)

    def reach_lb(self, token, held, seen):
        """Ticks no plan for `token` can walk less than: over its every way, the straight walk to the nearest known
        source of what that way's work or its inputs take from the world (0 where none is known, where some is
        held or stored, or where a way needs no walk) — the least over its ways."""
        if token in seen or held(token) > 0 or getattr(self.cost, "stored", lambda t: [])(token):
            return 0.0
        seen = seen | {token}
        best = math.inf
        for step, _station, ins in self.lb.shapes.get(token, self.lb.shapes.get(mid(token), ())):
            own = 0.0
            if step.kind in ("mine", "gather", "hunt", "take") and getattr(self.cost, "site", lambda s: None)(step):
                own = float(self.cost.walk_lb(step))
            best = min(best, max([own] + [self.reach_lb(t, held, seen) for t in ins]))
            if best <= 0:
                return 0.0
        return 0.0 if best == math.inf else best

    # -- one node to its next choice: resolved in place; returns children, [] when it died, None when complete
    def advance(self, node, floor=0):
        while len(node.stack) > floor:
            task = node.stack.pop()
            op = task[0]
            if op == "need":
                got = self.need(node, *task[1:])
            elif op == "tool":
                got = self.tool(node, *task[1:])
            elif op == "station":
                got = self.station(node, *task[1:])
            elif op == "prep":
                got = self.prep(node, *task[1:])
            elif op == "speed":
                got = self.speed(node, *task[1:])
            elif op == "fuel":
                got = self.fuel(node, *task[1:])
            elif op == "fact":
                got = self.fact(node, *task[1:])
            elif op == "emit":
                got = self.emit(node, *task[1:])
            elif op == "add":
                node.inv.add(task[1], task[2])
                got = None
            elif op == "use":
                node.inv.consume(task[1], task[2])
                got = None
            elif op == "taken":
                node.inv.facts[task[1]] = node.inv.facts.get(task[1], 0) + task[2]
                got = None
            elif op == "addtool":
                node.inv.tools.append([task[1], task[2], task[3]])
                got = None
            else:
                raise AssertionError(op)
            if got is not None:
                return got
        return None

    def dead(self, why):
        self.reasons.append(why)
        return []

    def options(self, node, opts, horizon=None, asked=""):
        """Children for each option ([(tie, [tasks in run order])]); one option runs in place (no branch).
        `horizon`: the stack's length once the choice is settled (by default: once the option's own tasks ran)."""
        if not opts:
            return None
        if len(opts) == 1:
            node.tie = node.tie + (opts[0][0],)
            node.stack.extend(reversed(opts[0][1]))
            return None
        out = []
        for tie, tasks in opts:
            c = node.child()
            c.tie = c.tie + (tie,)
            c.horizon = len(node.stack) if horizon is None else horizon
            c.asked = asked
            c.stack.extend(reversed(tasks))
            out.append(c)
        return out

    @staticmethod
    def after(node, step):
        """The stack's length once `step` is emitted: what a choice about how it runs is judged over."""
        return next((i for i in range(len(node.stack) - 1, -1, -1)
                     if node.stack[i][0] == "emit" and node.stack[i][1] is step), len(node.stack))

    def await_first(self, node):
        """What a step is about to take from a job still running (a sown crop, a furnace) is collected right before
        that step — after every step planned so far, which need nothing of it (never idle while it is on its way)."""
        for item, n in sorted(node.inv.awaited.items()):
            if n > 0:
                step = Step("await", item, n)
                step.est = self.cost.estimate(step)
                node.steps.append((step, node.inv.held(), len(node.steps)))
                node.g += step.est
        node.inv.awaited.clear()

    def need(self, node, token, n, depth, fresh):
        if depth > MAX_DEPTH:
            return self.dead(f"requirement chain too deep at {token}")
        have = 0 if fresh else node.inv.available(token)
        if not fresh:
            node.inv.consume(token, min(have, n), awaits=depth > 0)
            self.await_first(node)
        missing = n - have if not fresh else n
        if missing <= 0:
            return None
        opts = self.ways(node, token, missing, depth)
        if not opts:
            return self.dead(self.no_way(token))
        return self.options(node, opts, asked=token)

    def no_way(self, token):
        known = [src for made, src in self.sources(token) if way(src, made, 1) is None]
        if known and len(known) == len(self.sources(token)):
            return f"{token}: its only source ({known[0][0]}) is not one the planner plans"
        return f"no known way to obtain {token}"

    def ways(self, node, token, n, depth):
        """[(tie, tasks)]: every way to make `n` of token from here."""
        if token == "food":
            return self.food(node, n, depth)
        out = []
        for i, (made, src) in enumerate(self.sources(token)):
            if self.kinds is not None and src[0] not in self.kinds:
                continue
            if src[0] == "farm":
                ripe_n = getattr(self.cost, "ripe", lambda t: 0)(made) * TAKEABLE[made]["gives"][made]
                for r in ((1,) if ripe_n >= n else ()) + (0,):
                    got = way(src, made, n, ripe=r)
                    if got is not None:
                        out.append(((0, 0, i, r), self.tasks(token, n, depth, len(node.steps), *got)))
                continue
            got = way(src, made, n)
            if got is None or any(node.inv.available(t) < c and not self.sources(t) for t, c in got[1]):
                continue                # an input nothing makes and the bag lacks: not a way from here
            step = got[0]
            if src[0] == "take" and getattr(self.cost, "site", lambda s: None)(step) is None:
                continue                # a thing standing in the world is taken only where one is known
            out.append(((0, 0, i), self.tasks(token, n, depth, len(node.steps), *got)))
            carried = self.from_carried(node, src, made, n)
            if carried:
                # the action keyed by its inputs' source: what the bag's inputs make is its own step, run when it pays
                out.append(((0, 0, i, 1), self.tasks(token, carried, depth, len(node.steps), *way(src, made, carried))
                            + [("need", token, n - carried, depth, False)]))
        if self.kinds is None:
            out += self.withdrawals(node, token, n, depth)
        return out

    @staticmethod
    def from_carried(node, src, made, n):
        """The most of `n` a craft or smelt makes from inputs the bag already holds (0 < k < n), else 0: when only
        part is carried, that part is a step of its own (an input on its way never holds the carried part back)."""
        if src[0] not in ("craft", "smelt") or n < 2:
            return 0
        if all(node.inv.available(t) >= c for t, c in way(src, made, n)[1]):
            return 0
        for k in range(n - 1, 0, -1):
            got = way(src, made, k)
            if got[0].count < n and all(node.inv.available(t) >= c for t, c in got[1]):
                return got[0].count
        return 0

    def withdrawals(self, node, token, n, depth):
        """Taking it from a container that holds it (memory.stored, each weighed by the chance it still does): as
        much as that one holds, the rest needed again."""
        out = []
        for j, (pos, item, have, p) in enumerate(getattr(self.cost, "stored", lambda t: [])(token)):
            taken = f"taken:{tuple(pos)}:{item}"          # what this plan already takes out of that container
            have -= node.inv.facts.get(taken, 0)
            if p <= 0 or have <= 0:
                continue
            k = min(n, have)
            step = Step("withdraw", item, k, {"pos": list(pos), "p": p})
            tasks: list[tuple] = [("emit", step, depth, len(node.steps)), ("taken", taken, k)]
            if k < n:
                tasks.append(("need", token, n - k, depth, False))
            out.append(((0, 1, j), tasks))
        return out

    def tasks(self, token, n, depth, start, step, inputs, station, adds):
        """The run-order tasks of one way: its inputs, its station, its call's own needs, the step, what it adds."""
        station = station or step_station(step)       # the contract's own station where the recipe names none
        out: list[tuple] = [("need", t, c, depth + 1, False) for t, c in inputs]
        if step.kind == "smelt":
            out.append(("fuel", step, depth))
        if station:
            out.append(("station", station, depth))
        out.append(("prep", step, depth))
        out.append(("emit", step, depth, start))
        out += [("add", t, c) for t, c in adds]
        if step.kind in ("craft", "farm"):
            out.append(("use", token, n))
        return out

    def food(self, node, n, depth):
        """Each food that could fill the gap: a whole kind, or what carried raw meat cooks into first."""
        out = []
        for i, item in enumerate(ALL_FOOD):
            raw = item.replace("cooked_", "") if "cooked_" in item else None
            carried = node.inv.available(raw) if raw else 0
            if 0 < carried < n:
                out.append(((0, 0, i, 0), [("need", item, carried, depth + 1, False), ("need", "food", n - carried,
                                                                                     depth, False)]))
            if self.sources(item):
                out.append(((0, 0, i, 1), [("need", item, n, depth + 1, False)]))
        return out

    def fuel(self, node, step, depth):
        n = step.count
        opts = []
        for i, (fuel, _per) in enumerate(FUELS):
            k = fuel_need(fuel, n)
            opts.append(((0, 0, i), [("need", fuel, k, depth + 1, False), ("setfuel", step, fuel, k)]))
        # the fuel lands in the step's detail when its option runs
        children = self.options(node, [(t, [x for x in tasks if x[0] != "setfuel"]) for t, tasks in opts],
                                self.after(node, step))
        picks = [tasks[-1] for _t, tasks in opts]
        if children is None:
            _op, s, fuel, k = picks[0]
            self.set_fuel(node, s, fuel, k)
            return None
        for c, (_op, _s, fuel, k) in zip(children, picks):
            self.set_fuel(c, step, fuel, k)
        return children

    def set_fuel(self, node, step, fuel, k):
        """The fuel this node's copy of the smelt step burns (the step is copied: siblings choose their own)."""
        new = Step(step.kind, step.token, step.count, {**step.detail, "fuel": fuel,
                                                        "inputs": {**step.detail["inputs"], fuel: k}})
        node.stack = [(t[0], new) + t[2:] if len(t) > 1 and t[1] is step else t for t in node.stack]

    def station(self, node, block, depth):
        """Stations are required, never consumed: once planned or held, every later step reuses them."""
        if node.inv.available(block) > 0 or self.cost.station_near(block):
            return None
        node.stack.append(("add", block, 1))
        node.stack.append(("need", block, 1, depth + 1, False))
        return None

    def tool(self, node, kind, tier, uses, depth):
        """A tool of `kind` at `tier` or above with `uses` left; else one made — of every tier that could serve."""
        if node.inv.has_tool(kind, tier, uses):
            return None
        making = any(t[0] == "addtool" and t[1] == kind for t in node.stack)
        opts = []
        for t in sorted(TOOL_MATERIAL_FOR_TIER):
            if t < tier or not self.sources(tool_item(kind, t)):
                continue
            new = TOOL_USES[TOOL_MATERIAL_FOR_TIER[t]]
            if t != tier and (self.kinds is not None or making):
                continue                # a tool made to make one of its own kind: the tier asked, no better
            opts.append(((t, use_rank(kind), 0),
                         [("need", tool_item(kind, t), 1, depth + 1, True), ("addtool", kind, t, spare_uses(new))]))
        if not opts:
            return self.dead(self.no_way(tool_item(kind, tier)))
        return self.options(node, opts)

    def uses(self, step, kind):
        """Uses of a `kind` tool the step's work takes (a break or a hit each)."""
        breaks, kills = own_work(step)
        if kills:
            return len(kills)
        return max(1, sum(1 for b in breaks if tool_kind(b) == kind)) if breaks else 1

    def prep(self, node, step, depth):
        """Before a step: its skill's needs for the call — tools first, kept not used up; items had and set aside
        until the call, so nothing else this prep plans takes them — the facts it needs, then the tools its work pays
        for (`speed`)."""
        needs = self.call(step)
        own = step.detail.get("inputs") or {}       # held at its call too, though the step uses them up
        tasks, held = [], []
        for dim, n in sorted(needs.items(), key=lambda kv: not kv[0].startswith("tool:")):
            if dim.startswith("tool:"):
                _, kind, tier = dim.split(":")
                tasks.append(("tool", kind, int(tier), self.uses(step, kind), depth))
            elif n - own.get(dim, 0) > 0:
                tasks.append(("need", dim, n - own.get(dim, 0), depth + 1, False))
                held.append(("add", dim, n - own.get(dim, 0)))
        got = self.when(step, node.inv.facts)
        if isinstance(got, str):
            return self.dead(f"{step.kind}: {got}")
        tasks += [("fact", fact, value, depth + 1) for fact, value in got]
        tasks.append(("speed", step, depth))
        at = self.after(node, step)         # what the run uses up goes once it has run
        node.stack[at:at] = [("use", item, n) for item, n in self.used(step).items()]
        node.stack.extend(reversed(tasks + held))
        return None

    def fact(self, node, fact, value, depth):
        """A fact the next step needs (its contract's `when`): true already, else each step whose run makes it so."""
        if node.inv.facts.get(fact) == value:
            return None
        if depth > MAX_DEPTH:
            return self.dead(f"requirement chain too deep at {fact}")
        opts = []
        for i, (kind, token) in enumerate(self.fact_steps(fact, value)):
            step = Step(kind, token, 1, {})
            opts.append(((0, 0, i), [("prep", step, depth), ("emit", step, depth, len(node.steps))]))
        if not opts:
            return self.dead(f"no way to make {fact} {value}")
        return self.options(node, opts, asked=f"{fact} {value}")

    def speed(self, node, step, depth):
        """The tools a step's work pays for: none, or one more of each kind it uses at any tier above the held one —
        each an option, the plan's own price decides (a tool made only when the whole plan is cheaper with it)."""
        if self.kinds is not None:
            return None
        breaks, kills = self.cost.work_of(step)
        kinds = {k for k in map(tool_kind, breaks) if k is not None} | ({"sword"} if kills else set())
        if not kinds:
            return None
        held = node.inv.held()
        base = work_s(breaks, kills, held, TICKS_PER_S)
        opts = [((0, 0, 0), [])]
        for kind in sorted(kinds):
            for t in sorted(TOOL_MATERIAL_FOR_TIER):
                if t <= held.get(kind, -1) or not self.sources(tool_item(kind, t)):
                    continue
                saved = (base - work_s(breaks, kills, {**held, kind: t}, TICKS_PER_S)) * TICKS_PER_S
                if saved <= 0 or saved <= self.lb.least(tool_item(kind, t), 1, node.inv.available, {}):
                    continue            # what it saves here cannot pay even the least the tool costs
                opts.append(((t + 1, use_rank(kind), 1), [("tool", kind, t, self.uses(step, kind), depth)]))
        return self.options(node, opts, self.after(node, step))

    def emit(self, node, step, depth, start):
        held = node.inv.held()
        # S5: an optional fight only above the line, with the weapon the plan holds by then (brain's one judge)
        ok, why = getattr(self.cost, "fight_line", lambda s, held=None: (True, None))(step, held)
        if not ok:
            return self.dead(f"{step.kind}: {why}")
        step = Step(step.kind, step.token, step.count, dict(step.detail))
        breaks, kills = own_work(step)
        for kind in {k for k in map(tool_kind, breaks) if k is not None} | ({"sword"} if kills else set()):
            if kind in held:
                node.inv.wear(kind, 0, self.uses(step, kind))
        ticks = self.cost.work(step, held) + self.cost.dig_to(step, held) + self.cost.walk_lb(step)
        if self.hungry and not node.inv.facts.get("fed"):
            ticks += round(ticks * self.hungry)         # F1l: hunger's seconds until a step makes food
            node.inv.facts["fed"] = mid(step.token) in FOOD_IDS and step.kind in MAKES_FOOD
        node.g += ticks
        node.inv.facts.update(self.sets(step))
        node.steps.append((step, held, start))      # start: where the steps it needs begin
        return None

    # -- the whole search
    def finish(self, node):
        steps, ticks = forward(node.steps, self.cost)
        self.considered.append((plan_name(steps), ticks / TICKS_PER_S, steps))
        return ticks, node.tie, steps

    def settle(self, node, floor=0, cap=math.inf):
        """`node` run down to `floor`, each choice on the way settled by what its options' own runs cost (each run
        to its horizon the same way, cheapest bound first, one that cannot beat the best so far given up; the same
        question from the same bag answered once): the node, or None when it died."""
        while len(node.stack) > floor:
            if node.g + self.h(node, floor) > cap:
                return None
            got = self.advance(node, floor)
            if got is None:
                return node
            best, before = None, len(self.reasons)
            for c in sorted(got, key=lambda c: (c.g + self.h(c, c.horizon), c.tie)):
                done = self.settled(c, cap if best is None else min(cap, best.g))
                if done is not None and (best is None or (done.g, done.tie) < (best.g, best.tie)):
                    best = done
            if best is None:
                if cap == math.inf and len(self.reasons) > before:      # every way died, not merely dearer
                    self.dead(f"no way to {got[0].asked}: " + "; ".join(dict.fromkeys(self.reasons[before:])))
                return None
            node = best
        return node

    def settled(self, node, cap):
        """`settle` of a node down to its horizon, memoised on what is asked and the bag it is asked from."""
        key = (signature(node.stack[node.horizon:]), node.inv.signature())
        hit = self.memo.get(key)
        if hit is not None:
            ok, inv, steps, g, tie = hit
            if not ok or node.g + g > cap:
                return None
            out = node.child()
            out.inv, out.stack = inv.clone(), out.stack[:node.horizon]
            out.steps, out.g, out.tie = node.steps + list(steps), node.g + g, node.tie + tie
            return out
        start, g0, t0 = len(node.steps), node.g, len(node.tie)
        done = self.settle(node, node.horizon, cap)
        if done is None:
            if cap == math.inf:
                self.memo[key] = (False, None, (), 0.0, ())
            return None
        self.memo[key] = (True, done.inv.clone(), tuple(done.steps[start:]), done.g - g0, done.tie[t0:])
        return done

    def dive(self, root):
        """The incumbent: every choice settled by its options' own cost (`settle`)."""
        node = self.settle(root)
        return None if node is None else self.finish(node)

    def plan(self, root, needs):
        held, after, run = [], [], []       # run order: what is had, then counted back as held, then what is done
        for need in needs:
            if need[0] == "tool":
                run.append(("tool", need[1], int(need[2]), 1, 0))
            elif need[0] == "fact":
                after.append(("fact", need[1], need[2], 0))
            elif need[0] == "do":
                station = step_station(need[1])
                after += ([("station", station, 0)] if station else []) + [("prep", need[1], 0), ("emit", need[1], 0, 0)]
            elif mid(need[0]) in STATIONS:
                # a station the goal asks for stands from when it is had: every later step reuses it, none makes another
                run += [("need", need[0], int(need[1]), 0, False), ("add", need[0], int(need[1]))]
            else:
                # a goal's items are to be held: had, then counted back for what the plan does after them
                run.append(("need", need[0], int(need[1]), 0, False))
                held.append(("add", need[0], int(need[1])))
        root.stack.extend(reversed(run + held + after))
        best = self.dive(root.child())
        first_reason = self.reasons[-1] if self.reasons else None
        heap: list[tuple[float, tuple, int]] = []
        open_ = {}                          # seq → (node, or None for a complete plan, its steps)

        def push(f, tie, node, steps=None):
            n = next(self.counter)
            open_[n] = (node, steps)
            heapq.heappush(heap, (f, tie, n))

        push(self.h(root), (), root)
        nodes = 0
        while heap and nodes <= MAX_NODES:
            f, tie, n = heapq.heappop(heap)
            if best is not None and (f, tie) >= (best[0], best[1]):
                break
            node, steps = open_.pop(n)
            if steps is not None:
                best = (f, tie, steps)
                break
            nodes += 1
            got = self.advance(node)
            if got is None:
                ticks, tie, steps = self.finish(node)
                push(ticks, tie, node, steps)
                continue
            for c in got:
                fc = c.g + self.h(c)
                if fc < math.inf and (best is None or (fc, c.tie) < (best[0], best[1])):
                    push(fc, c.tie, c)
        if best is None:
            raise Unplannable(first_reason or (self.reasons[0] if self.reasons else "no way found"))
        return best[2]


def forward(entries, cost):
    """The plan as it will run: same-kind steps of one token merged into the earliest (intermediates made once for
    everything), then ordered to walk least between the places its steps happen (an exact search over the order of
    those, dependencies kept), each step priced from where the one before left the body."""
    steps, held = [], []
    index, where = {}, []        # key → its first step's place; each entry's place among the merged steps
    for i, (step, h, start) in enumerate(entries):
        k = step.key()
        mergeable = step.kind in MERGEABLE or (step.kind == "craft" and step.token in MERGEABLE_CRAFTS)
        # merged only where everything it needs already comes before the step it joins
        if k in index and mergeable and all(where[j] < index[k] for j in range(start, i)):
            where.append(index[k])
            first = steps[index[k]]
            first.count += step.count
            for key in ("breaks", "kills", "times"):
                if key in step.detail:
                    first.detail[key] = first.detail.get(key, 0) + step.detail[key]
            for tok, c in step.detail.get("inputs", {}).items():
                first.detail.setdefault("inputs", {})[tok] = first.detail["inputs"].get(tok, 0) + c
            continue
        index.setdefault(k, len(steps))
        where.append(len(steps))
        steps.append(Step(step.kind, step.token, step.count, {**step.detail, "inputs": dict(step.detail["inputs"])}
                          if "inputs" in step.detail else dict(step.detail)))
        held.append(h)
    order = walk_order(steps, cost)
    out, at, total = [], None, 0
    hungry = getattr(cost, "hunger_rate", lambda: 0.0)()      # F1l: hunger's seconds until a step makes food
    for i in order:
        step = steps[i]
        step.est = cost.estimate(step, held[i], at)
        if hungry:
            step.est += round(step.est * hungry)
            hungry = 0.0 if mid(step.token) in FOOD_IDS and step.kind in MAKES_FOOD else hungry
        site = cost.site(step) if hasattr(cost, "site") else None
        at = site if site is not None else at
        total += step.est
        out.append(step)
    return out, total


def walk_order(steps, cost):
    """Indexes of `steps` in run order: as planned, except the steps whose place is known, which run in the order
    that walks least between them (Held–Karp over those, each kept after every step planned before it that it
    could need — the planned order is a valid order, so it is kept among equals)."""
    if not hasattr(cost, "site") or getattr(cost, "snap", None) is None:
        return list(range(len(steps)))
    sites = {i: cost.site(s) for i, s in enumerate(steps)}
    placed = [i for i, p in sites.items() if p is not None]
    if len(placed) < 2 or len(placed) > REORDER_MAX:
        return list(range(len(steps)))
    feet = tuple(cost.snap.feet)
    # a placed step may run before an earlier one only when it uses nothing that one makes
    makes = [set(members(s.token)) | {s.token} for s in steps]
    uses = [set(s.detail.get("inputs", {})) | {s.detail.get("container"), s.detail.get("input")} for s in steps]
    before = {j: {i for i in placed if i < j and (makes[i] & uses[j] or steps[i].kind == "craft"
                                                   and steps[i].token.endswith(("_pickaxe", "_axe", "_shovel",
                                                                                "_sword", "_hoe")))}
              for j in placed}
    n = len(placed)
    pos = {i: k for k, i in enumerate(placed)}
    best: dict[tuple[int, int], tuple[float, tuple[int, ...]]] = {
        (1 << pos[j], j): (math.dist(feet, sites[j]), (j,)) for j in placed if not before[j]}
    for size in range(1, n):
        for (mask, last), (d, seq) in sorted(((k, v) for k, v in best.items() if bin(k[0]).count("1") == size),
                                             key=lambda kv: kv[1][1]):
            for j in placed:
                bit = 1 << pos[j]
                if mask & bit or any(not mask & (1 << pos[i]) for i in before[j]):
                    continue
                nd = d + math.dist(sites[last], sites[j])
                key = (mask | bit, j)
                if key not in best or (nd, seq + (j,)) < best[key]:
                    best[key] = (nd, seq + (j,))
    full = [v for (mask, _j), v in best.items() if mask == (1 << n) - 1]
    planned: tuple[float, tuple[int, ...]] = (sum(math.dist(a, b) for a, b in zip([feet] + [sites[i] for i in placed[:-1]],
                                                    [sites[i] for i in placed])), tuple(placed))
    if not full:
        return list(range(len(steps)))
    route = min(full + [planned])[1]
    if route == tuple(placed):
        return list(range(len(steps)))
    # the unplaced steps keep their planned place relative to what they come after
    out, queue = [], list(route)
    for i in range(len(steps)):
        if i in pos:
            out.append(queue.pop(0))
        else:
            out.append(i)
    return out


def plan_name(steps):
    """A complete plan's name: the ways it takes, in order, each once."""
    return " → ".join(dict.fromkeys(f"{s.kind} {bare(s.token)}" for s in steps)) or "nothing to do"


def plan_candidates(inv, needs, cost, pending=None, jobs=None, kinds=None):
    """The plans the search priced for `needs` from this bag, cheapest first: [(name, seconds, steps)] — the one
    chosen first, the alternatives it beat after (what a check of the choice reads). `pending`: counted as held
    (planned sources' and jobs' outputs); `jobs`: of it, what running jobs make (awaited when used); `kinds`: the
    step kinds allowed (a query: what crafting alone makes)."""
    if not needs:
        return [(plan_name([]), 0.0, [])]
    root = Node(from_bag(inv, pending, jobs, getattr(cost, "reserved", ()), cost.facts()), [], [])
    # one plan per question a round asks (the same needs from the same bag: needs, upkeep, queue, night)
    cache = getattr(cost, "cache", None)
    key = ("plan", tuple(repr(tuple(n)) for n in needs), root.inv.signature(), tuple(sorted(kinds)) if kinds else None)
    if cache is not None and key in cache:
        return [(name, seconds, [Step(s.kind, s.token, s.count, dict(s.detail), s.est) for s in steps])
                for name, seconds, steps in cache[key]]
    search = Search(cost, kinds)
    chosen = search.plan(root, list(needs))
    out = {plan_name(chosen): (plan_name(chosen), sum(s.est for s in chosen) / TICKS_PER_S, chosen)}
    for name, seconds, steps in sorted(search.considered, key=lambda c: c[1]):
        out.setdefault(name, (name, seconds, steps))
    got = list(out.values())
    if cache is not None:
        cache[key] = [(name, seconds, [Step(s.kind, s.token, s.count, dict(s.detail), s.est) for s in steps])
                      for name, seconds, steps in got]
    return got


def plan_needs(inv, needs, cost, pending=None, jobs=None, kinds=None):
    """The cheapest steps that make `needs` held from this bag (`plan_candidates`' first)."""
    return plan_candidates(inv, needs, cost, pending, jobs, kinds)[0][2]


def craftable_tier(inv, kind, reserved=()):
    """The best tier of `kind` this bag crafts with crafting steps only (`reserved`, what the held plans will consume,
    left out), or 0."""
    for tier in sorted((t for t in TOOL_MATERIAL_FOR_TIER if t > 0), reverse=True):
        try:
            plan_needs(inv, [("tool", kind, tier)], NullCost(reserved), kinds={"craft"})
        except Unplannable as e:
            api.swallowed("planner.craftable_tier", e)
            continue
        return tier
    return 0


def runnable(step, inv):
    """Can this step start from the bag now?"""

    needs = step_call(step)
    if have_remainder(inv, needs_rows(needs)):
        return False
    return all(inv.count(tok) >= n for tok, n in step.detail.get("inputs", {}).items())


class NullCost:
    """Offline cost model for tests: the prior work (knowledge.prior_work_ticks), nothing known about places."""

    def __init__(self, reserved=()):
        self.reserved = frozenset(reserved)
        self.cache = {}

    def station_near(self, block):
        return False

    def facts(self):
        return {"dimension": OVERWORLD, **body_facts(None)}

    def work(self, step, held=None):
        return prior_work_ticks(step, held or {}, TICKS_PER_S)

    def estimate(self, step, held=None, at=None):
        return self.work(step, held) + self.dig_to(step, held)

    def dig_to(self, step, held=None):
        """Ticks the digging to it takes (its work_of beyond its own work), with `held`."""
        return dig_to_ticks(self.work_of(step)[0], step, held or {}, TICKS_PER_S)

    def walk_lb(self, step):
        return 0

    def work_of(self, step):
        """A step's own work (knowledge.own_work): offline, nothing in sight to dig to."""
        return own_work(step)
