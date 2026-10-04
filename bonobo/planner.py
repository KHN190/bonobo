"""The one planner:"""

import heapq
import itertools
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import api, lifecycle
from .api import McError
from .data import GROUPS, NIGHT_WORK, OVERWORLD, TIER_OF_MATERIAL, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER, TOOL_USES, bare, mid
from .beliefs import CONFIG, TICKS_PER_S, fights_back
from .knowledge import (ALL_FOOD, CONTRACT_FACTS, body_facts, dig_to_ticks, have_remainder, members, needs_rows, own_work, prior_work_ticks, sources, step_call, step_station, tool_item, tool_kind, spare_uses, work_s, working)
from .data import HUNT_YIELD, MINE_YIELD, TAKEABLE

DIVE_NODES = 600      # nodes the dive weighs DIVE_WIDTH ways a choice (~0.07 s measured); then the first that can be had
MAX_NODES = 60       # A* expansions past the incumbent (~0.01 s measured): spent, the best complete plan found
                      # stands — the incumbent at least (D1: an answer, never a hang)
MERGEABLE = {"mine", "gather", "hunt", "smelt", "withdraw"}     # withdraw: from one container (its key holds the place)
STATIONS = frozenset(("minecraft:crafting_table", "minecraft:furnace"))     # what way() works at, never used up
FOOD_IDS = frozenset(mid(f) for f in ALL_FOOD)          # what the eat reflex eats
MAKES_FOOD = ("smelt", "craft", "take", "withdraw", "trade", "await")   # steps that put food in the bag
MERGEABLE_CRAFTS = {"planks", "minecraft:stick", "minecraft:torch", "minecraft:ladder"}
ORDER_MAX = 4         # a level's targets every order of which is weighed (4! plans)
DIVE_WIDTH = 2        # options a nested choice of the incumbent dive weighs (the least-bound first)
REORDER_MAX = 10      # steps with a known place an order is searched over exactly (2^n states)


class Unplannable(McError):
    pass


class Dearer(Unplannable):
    """No plan under the cap the caller already has a way at."""


class Cut(Dearer):
    """Not weighed: the round's steps were spent before this way was (budget_spent), not found dearer."""


@dataclass
class Step:
    kind: str            # craft | smelt | mine | gather | hunt | ... — or any effect a skill provides (skill.providers)
    token: str           # item id or group token produced
    count: int           # units of `token` to end up with (craft: output items)
    detail: dict[str, Any] = field(default_factory=dict)    # the call's arguments: pos, types, blocks, args, ...
    est: int = 0         # estimated ticks
    parts: dict[str, int] = field(default_factory=dict, compare=False, repr=False)    # est by part (Cost.estimate: E4)

    def key(self):
        return (self.kind, self.token, tuple(self.detail.get("pos", ()))) if self.kind == "withdraw" else (self.kind, self.token)

    def __str__(self):
        return f"{self.kind} {self.count}× {bare(self.token)} (~{self.est // TICKS_PER_S}s)"


_MEMBERS: dict = {}     # token → the item ids it counts (knowledge.members), read once: the tables are fixed
lifecycle.in_place(__name__, "_MEMBERS", cache=True)


class VirtualInventory:
    """Counts what we'd hold after the planned steps run."""

    _TABLES = ("counts", "produced", "pending", "awaited", "facts", "tools")

    def __init__(self, counts, tools, pending=None, facts=None):
        self.facts = dict(facts or {})      # what is true of the world for the plan (dimension, a portal here, …)
        self.counts = {k: v for k, v in Counter(counts).items()}   # item id -> count (held, and `pending` on its way)
        self.produced: dict = {}            # group token -> count produced by planned steps
        self.tools = [tuple(t) for t in tools]   # (kind, tier, uses left)
        self.pending = dict(Counter(pending or {}))   # item id -> of `counts`, what a job is still making (not in the bag)
        self.awaited: dict = {}             # item id -> pending the plan uses: to be collected before it is used
        self._shared: set = set()           # tables still shared with a clone: copied before the first change
        self._sig = None

    def clone(self):
        out = VirtualInventory.__new__(VirtualInventory)
        out.counts, out.produced, out.pending, out.awaited = self.counts, self.produced, self.pending, self.awaited
        out.facts, out.tools = self.facts, self.tools
        out._shared = set(self._TABLES)
        self._shared = set(self._TABLES)
        out._sig = self._sig
        return out

    def _own(self, name):
        """The table `name` for writing: this inventory's own copy."""
        self._sig = None
        if name in self._shared:
            self._shared.discard(name)
            setattr(self, name, list(getattr(self, name)) if name == "tools" else dict(getattr(self, name)))
        return getattr(self, name)

    def available(self, token):
        ids = _MEMBERS.get(token)
        if ids is None:
            ids = _MEMBERS[token] = tuple(members(token))
        counts = self.counts
        return sum(counts.get(m, 0) for m in ids) + self.produced.get(token, 0)

    def consume(self, token, n, awaits=True):
        """Use `n` of token:"""
        take = min(n, self.produced.get(token, 0))
        if take:
            produced = self._own("produced")
            produced[token] -= take
            n -= take
        for m in members(token):
            if n <= 0:
                break
            take = min(n, self.counts.get(m, 0))
            if take <= 0:
                continue
            held = self.counts.get(m, 0) - self.pending.get(m, 0)
            if take > held:                  # the rest comes from a job not yet collected
                late = take - max(0, held)
                pending = self._own("pending")
                pending[m] = pending.get(m, 0) - late
                if awaits:
                    awaited = self._own("awaited")
                    awaited[m] = awaited.get(m, 0) + late
            counts = self._own("counts")
            counts[m] -= take
            n -= take

    def add(self, token, n):
        if token in GROUPS:
            produced = self._own("produced")
            produced[token] = produced.get(token, 0) + n
        else:
            counts = self._own("counts")
            counts[mid(token)] = counts.get(mid(token), 0) + n

    def set_fact(self, fact, value):
        self._own("facts")[fact] = value

    def add_tool(self, kind, tier, uses):
        self._own("tools").append((kind, tier, uses))

    def clear_awaited(self):
        if self.awaited:
            self._own("awaited").clear()

    def signature(self):
        if self._sig is None:
            self._sig = (tuple(sorted((k, v) for k, v in self.counts.items() if v)),
                         tuple(sorted((k, v) for k, v in self.produced.items() if v)),
                         tuple(sorted(self.tools)),
                         tuple(sorted((k, v) for k, v in self.pending.items() if v)),
                         tuple(sorted((k, v) for k, v in self.awaited.items() if v)),
                         tuple(sorted(self.facts.items())))
        return self._sig

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
        fit = [i for i, t in enumerate(self.tools) if t[0] == kind and t[1] >= tier and t[2] >= n]
        if fit:
            i = max(fit, key=lambda j: (self.tools[j][1], -self.tools[j][2]))
            tools = self._own("tools")
            k, t, u = tools[i]
            tools[i] = (k, t, u - n)


def signature(tasks, seen=None):
    """The tasks as a key:"""
    def part(x):
        if isinstance(x, Step):
            return (x.kind, x.token, x.count, repr(sorted(x.detail.items())))
        return x
    if seen is None:
        return tuple(tuple(part(x) for x in t) for t in tasks)
    out = []
    for t in tasks:
        hit = seen.get(id(t))
        if hit is None or hit[0] is not t:
            hit = seen[id(t)] = (t, tuple(part(x) for x in t))
        out.append(hit[1])
    return tuple(out)


def from_bag(inv, extra=None, pending=None, reserved=(), facts=None) -> VirtualInventory:
    """A virtual inventory from the bag:"""
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
    tools = [(kind, t, spare_uses(d)) for kind in TOOL_KINDS for t, d, _ in inv.tools(kind) if working(d)]
    return VirtualInventory(counts, tools, {mid(k): v for k, v in (pending or {}).items()}, facts)


def hunts_a_fighter(types):
    """Does this hunt target something that fights back (beliefs.fights_back)? Animals do not."""
    return fights_back(types)


def use_rank(kind):
    """A tool kind's place in play.toml tools.use_order (most used first): the tie between equal plans."""
    order = CONFIG["tools"]["use_order"]
    return next((i for i, g in enumerate(order) if kind in g), len(order))


# -- the ways one source makes `n` of a token: (step, the needs before it, what its run adds back)

def way(src, token, n, ripe=0) -> tuple[Step, list, str | None, list]:
    """(step, [(input token, amount)] in the order they are needed, station or None, [(token, amount)] added after)…"""
    kind = src[0]
    if kind == "craft":
        _, pattern, out = src
        times = math.ceil(n / out)
        inputs = Counter(p for p in pattern if p)
        station = "minecraft:crafting_table" if len(pattern) == 9 else None
        step = Step("craft", token, times * out, {"times": times, "inputs": {t: c * times for t, c in inputs.items()},
                                                  **({"station": station} if station else {})})
        return step, [(t, c * times) for t, c in inputs.items()], station, [(token, times * out)]
    if kind == "smelt":
        _, inp = src
        return Step("smelt", token, n, {"input": inp, "inputs": {inp: n}, "station": "minecraft:furnace"}), [(inp, n)], \
            "minecraft:furnace", []
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
    _, types, per_ingot = src          # barter
    ingots = math.ceil(n / per_ingot)
    return Step("barter", bare(types[0]), n, {"ingots": ingots, "types": list(types),
                                              "inputs": {"minecraft:gold_ingot": ingots}}), \
        [("minecraft:gold_ingot", ingots)], None, []


FUELS = (("coal", 8), ("planks", 1.5))      # what a furnace burns: items smelted per unit (Minecraft Wiki, Fuel)


def uncovered(token, srcs):
    """Pure: `srcs` less each member's way the group's own way makes alike (kind, count, station, an input of the
    group's per input): the group's way takes any member, so the member's is the same plan searched again."""
    own = [way(src, made, 1) for made, src in srcs if made == token]

    def alike(g, w):
        left = list(g[1])
        for t, c in w[1]:
            hit = next((x for x in left if x[1] == c and (x[0] == t or mid(t) in members(x[0]))), None)
            if hit is None:
                return False
            left.remove(hit)
        where = all(set(w[0].detail.get(k) or ()) <= set(g[0].detail.get(k) or ()) for k in ("blocks", "types"))
        return (g[0].kind == w[0].kind and g[0].count == w[0].count and g[2] == w[2] and not left and where
                and w[0].detail.get("tier") == g[0].detail.get("tier"))

    def covered(made, src):
        w = way(src, made, 1) if made != token else None
        return w is not None and any(alike(g, w) for g in own)
    return [(made, src) for made, src in srcs if not covered(made, src)]


def fuel_need(fuel, n):
    per = dict(FUELS)[fuel]
    return math.ceil(n / per)


# -- the bound: what one unit of a token costs at the least, from the tables alone

BEST_TOOLS = {k: max(TOOL_MATERIAL_FOR_TIER) for k in TOOL_KINDS}


def contract_facts():
    """Every fact a registered contract makes true (its `state:` gives, its `sets`): a link each a chain may take."""
    return set(CONTRACT_FACTS)


def least_prices(ways):
    """Pure:"""
    def node(t):
        return t if t in ways else mid(t) if mid(t) in ways else None
    users: dict = {}
    for asked, ways_ in ways.items():
        for _per, ins, _n in ways_:
            for t in ins:
                if node(t) is not None:
                    users.setdefault(node(t), set()).add(asked)
    price: dict = {}

    def best(asked):
        return min((per + sum(price.get(node(t), math.inf) * c for t, c in ins.items()) for per, ins, _n in ways[asked]),
                   default=math.inf)
    heap = [(best(a), a) for a in ways]
    heapq.heapify(heap)
    while heap:
        value, asked = heapq.heappop(heap)
        if value == math.inf or value >= price.get(asked, math.inf) - 1e-9:
            continue
        price[asked] = value
        for user in users.get(asked, ()):
            got = best(user)
            if got < price.get(user, math.inf) - 1e-9:
                heapq.heappush(heap, (got, user))
    return price


def covered_prices(ways, kinds):
    """Pure:"""
    return least_prices({a: [w for w, k in zip(ws, kinds[a]) if k in NIGHT_WORK] for a, ws in ways.items()})


class Bound:
    """What a token costs at the least, from the tables:"""

    def __init__(self, cost):
        from .knowledge import producers
        self.ways = {}      # asked token → [(ticks per unit, {input: per unit}, {tool kind: tier})]
        self.kinds = {}     # each way's step kind
        self.shapes = {}    # asked token → [(its unit step, the station it works at or None, {input: per unit})]
        tokens = {t for g in producers() for t in g.keys()} | set(GROUPS) | {"food"}
        for asked in sorted(tokens):
            for token, src in sources(asked) if asked != "food" else [(f, s) for f in ALL_FOOD for _m, s in
                                                                         sources(f)]:
                step, inputs, _station, _adds = way(src, token, 1)
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
                per = self.per_run(cost, step) / out
                if step.kind == "smelt":      # a smelt burns its fuel: one way per fuel, its share a unit
                    for fuel, burns in FUELS:
                        self.ways.setdefault(asked, []).append((per, {**ins, fuel: ins.get(fuel, 0) + 1 / burns}, need))
                        self.kinds.setdefault(asked, []).append(step.kind)
                else:
                    self.ways.setdefault(asked, []).append((per, ins, need))
                    self.kinds.setdefault(asked, []).append(step.kind)
                self.shapes.setdefault(asked, []).append((step, _station, ins))
        self.scratch = least_prices(self.ways)
        self.covered = covered_prices(self.ways, self.kinds)
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
                if got != tools.get(asked, {}):
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
        self.depth = self.longest_chain() + len(contract_facts()) + 2

    def longest_chain(self):
        """The longest requirement chain the tables hold, a link per input, tool and station, a cycle cut where it c…"""
        memo: dict = {}

        def deep(token, path):
            if token in path:
                return 0
            if token in memo:
                return memo[token]
            path = path | {token}
            got = 0
            for step, station, ins in self.shapes.get(token, self.shapes.get(mid(token), ())):
                links = list(ins) + ([station] if station else []) + [tool_item(k, t) for k, t in
                                                                       self.tools_of(self.tools, token).items()]
                got = max([got] + [1 + deep(t, path) for t in links])
            memo[token] = got
            return got
        return max((deep(t, frozenset()) for t in self.shapes), default=0)

    @staticmethod
    def per_run(cost, step):
        """Ticks one more run of `step` adds to its work (its own work for two runs less for one, the best tools):"""
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


_BOUNDS = {}      # Bound by (producers, hooks, measured durations): reused across rounds
BOUNDS_KEPT = 4   # the offline one, the live one and a change or two in between
lifecycle.in_place(__name__, "_BOUNDS", cache=True)


def bound(cost):
    """The tables' bound (`Bound`), kept across rounds while what it is built from holds:"""
    from . import knowledge
    stats = tuple(sorted((k, d.get("per"), d.get("n")) for k, d in cost.mem.data.get("durations", {}).items())) \
        if cost.mem is not None else ()
    key = (knowledge.TABLES_VERSION[0], tuple(id(p) for p in knowledge.PRODUCERS), stats,      # ids of tables it holds: alive
           type(cost).__name__ if isinstance(cost, NullCost) else "cost")
    if key not in _BOUNDS:
        if len(_BOUNDS) >= BOUNDS_KEPT:
            _BOUNDS.clear()
        _BOUNDS[key] = Bound(cost)
    return _BOUNDS[key]


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
    open: tuple = ()            # what is being made right now, outermost first: asked again inside, a cycle
    path: tuple = ()            # (g, h) after each step emitted: what the plan was weighed at on its way (P3's)

    def child(self):
        return Node(self.inv.clone(), list(self.stack), list(self.steps), self.g, self.tie, self.horizon, self.asked,
                    self.open, self.path)


def _delta(before, after):
    """Pure: what a sub-plan did to the bag — each table's count changes, the facts it set, the tools it left."""
    out = {}
    for name in ("counts", "produced", "pending", "awaited"):
        a, b = getattr(before, name), getattr(after, name)
        out[name] = {k: b.get(k, 0) - a.get(k, 0) for k in set(a) | set(b) if b.get(k, 0) != a.get(k, 0)}
    out["facts"] = {k: v for k, v in after.facts.items() if before.facts.get(k, _UNSET) != v}
    out["gone"] = [k for k in before.facts if k not in after.facts]
    lost, got = Counter(before.tools), Counter(after.tools)
    out["tools"] = (lost - got, got - lost)
    return out


def _applied(inv, delta):
    """Pure: `inv` with a sub-plan's `delta` applied (`_delta`)."""
    out = inv.clone()
    for name in ("counts", "produced", "pending", "awaited"):
        if delta[name]:
            table = out._own(name)
            for k, d in delta[name].items():
                table[k] = table.get(k, 0) + d
    if delta["facts"] or delta["gone"]:
        facts = out._own("facts")
        facts.update(delta["facts"])
        for k in delta["gone"]:
            facts.pop(k, None)
    lost, got = delta["tools"]
    if lost or got:
        tools = Counter(out._own("tools"))
        tools.subtract(lost)
        tools.update(got)
        out.tools[:] = sorted(t for t, n in tools.items() for _ in range(max(0, n)))
    return out


_UNSET = object()


def _scaled(step, n):
    """Pure: `step` at `n` units (its breaks, kills and times with it): what a merged run of it does."""
    detail = dict(step.detail)
    for k in ("breaks", "kills"):
        if k in detail:
            detail[k] = n * detail[k] // max(1, step.count)
    return Step(step.kind, step.token, n, detail)


def step_value(step):
    """Pure: what a step is, by value — the key its contracts' answers are kept under."""
    return step.kind, step.token, step.count, repr(sorted(step.detail.items())) if step.detail else ""


def _hook_memo(memo, name, fn, facts=False):
    """`fn(step[, facts])`, kept in `memo` by the step's value (and the facts it is asked against)."""
    def ask(step, *rest) -> Any:
        key = (name, step_value(step)) + ((tuple(sorted(rest[0].items())),) if facts else ())
        if key not in memo:
            memo[key] = fn(step, *rest)
        got = memo[key]
        return dict(got) if isinstance(got, dict) else list(got) if isinstance(got, list) else got
    return ask


class Search:
    def __init__(self, cost, kinds=None, exact=False):
        self.exact = exact              # the checker's reference: no budget, no pruning (dive width, tool payback, transpositions, orders, alike group ways)
        self.hungry = cost.hunger_rate()
        # what one search learns holds for every search of the round on the same readings (Cost.plans)
        plans = cost.plans()
        shared = plans.setdefault(("search", tuple(sorted(kinds)) if kinds else None, exact), {})
        self.walks = shared.setdefault("walks", {})       # (token, way) → its own walk at the least (own_walk)
        self.spent = 0                  # nodes advanced (the dive's budget: DIVE_NODES)
        self.stored_c, self.near_c, self.reach_c, self.least_c = (shared.setdefault(k, {}) for k in
                                                                   ("stored", "near", "reach", "least"))
        self.task_keys: dict = {}       # id(task) → (task, its key): signature, once a task
        self.saves_c: dict = {}         # (kind, tier had, tier) → {token: ticks a unit's making saves at the most}
        self._kept = None
        self.start_tools: list = []
        self.h_memo: dict = {}          # (what is left, the bag) → its bound
        self.greedy = False             # settle: every option weighed, or (the dive) the few least-bound ways inside a choice
        self.stop = cost.stop or (lambda: False)
        self.cost = cost
        self.kinds = kinds              # step kinds allowed (None: all)
        self.lb = bound(cost)
        self.counter = itertools.count()
        self.reasons = []
        self.memo = shared.setdefault("memo", {})
        self._sources = shared.setdefault("sources", {})
        self.considered = []
        self.paths: dict = {}           # id(a finished plan's steps) → the (g, h) its node was weighed at, step by step        # every complete plan priced: (name, seconds, steps) — the round's alternatives
        from . import knowledge
        knowledge.producers()                 # the skills registered: their hooks below are wired
        # the contracts' answers about a step, asked once a search per step (the registry does not change within one)
        hooks = shared.setdefault("hooks", {})
        self.call = _hook_memo(hooks, "call", knowledge.STEP_CALL or (lambda step: {}))
        self.when = _hook_memo(hooks, "when", knowledge.STEP_WHEN or (lambda step, facts: []), facts=True)
        self.sets = _hook_memo(hooks, "sets", knowledge.STEP_SETS or (lambda step: {}))
        self.used = _hook_memo(hooks, "used", knowledge.STEP_USES or (lambda step: {}))
        self.station_of = _hook_memo(hooks, "station", step_station)
        fact_steps = knowledge.FACT_STEPS or (lambda fact, value: [])
        self.fact_steps = lambda fact, value: hooks.setdefault(("facts", fact, repr(value)), fact_steps(fact, value))

    def sources(self, token):
        """knowledge.sources, asked once a plan (the registry does not change within one)."""
        if token not in self._sources:
            self._sources[token] = sources(token) if self.exact else uncovered(token, sources(token))
        return self._sources[token]

    # -- what is left: its bound
    def h(self, node, floor=0):
        """`bound` of the node, once a search per what is left, the bag it is asked from and the places walked to."""
        key = (signature(node.stack[floor:], self.task_keys), node.inv.signature(), self.walked(node))
        got = self.h_memo.get(key)
        if got is None:
            got = self.h_memo[key] = self.bound_of(node, floor)
        return got

    def bound_of(self, node, floor=0):
        """The least what is left (above `floor`) can cost:"""
        units, tool = 0.0, 0.0
        inv = node.inv
        held = self.holding(inv)
        memo: dict = {"held": {k for k, v in inv.counts.items() if v > 0} | {k for k, v in inv.produced.items() if v > 0}
                      | set(self.kept())}
        asked = {mid(t[1]) for t in node.stack[floor:] if t[0] == "need"}

        def fixed(item):          # a tool or station still to be had: its least, unless asked itself or stored
            return 0.0 if mid(item) in asked or self.stored(item) else self.lb.least(item, 1, held, memo)
        for task in node.stack[floor:]:
            op = task[0]
            if op == "emit" and task[1].kind != "await":
                units += self.least_of(node, task[1], BEST_TOOLS, walk=False)  # a step chosen, still to run (best tools: it may run after any)
            if op == "need":
                _op, token, n, _depth, fresh = task
                # a container holding it is a way the tables do not price (a withdraw): nothing is known below it
                units += self.lb.least(token, n + (held(token) if fresh else 0), held, memo)
                if not self.lb.reach.get(token, set()) & memo["held"]:     # nothing of its making held: a tool it
                    for kind, tier in self.lb.needed(token).items():       # must have is still to be had
                        if not inv.has_tool(kind, tier):
                            tool = max(tool, fixed(tool_item(kind, tier)))
            elif op == "tool":
                _op, kind, tier, uses, _depth = task
                if not inv.has_tool(kind, tier, uses):
                    tool = max(tool, fixed(tool_item(kind, tier)))
        trips = {f[5:] for f, v in inv.facts.items() if v and f.startswith("trip ")}

        station, walk = 0.0, 0.0
        for task in node.stack[floor:]:
            if task[0] != "need" or held(task[1]) >= task[2]:
                continue
            for s in self.lb.tools_of(self.lb.stations, task[1]) or ():
                if held(s) <= 0 and not self.near(s):
                    station = max(station, fixed(s))
            walk = max(walk, self.walk_to(task[1], held, memo["held"], frozenset(trips)))
        walk = max(walk, self.legs(node, floor, held, memo["held"], frozenset(trips)))
        return units + walk + max(tool, station)     # a tool or station made is work no unit's least counts

    def least_of(self, node, step, held, walk=True):
        """Ticks `step` takes at the least, run after what `node` has emitted (its g's share, and the bound's for a step
        already chosen and still to come): a repeat of a merged step only the work its units add (forward joins it
        to the first, a craft's work once); else its work and the least walk to it, none to a place walked to."""
        ticks = self.cost.work(step, held, table_back=not node.inv.facts.get("table back"))
        if (step.kind in MERGEABLE or (step.kind == "craft" and step.token in MERGEABLE_CRAFTS)) \
                and node.inv.facts.get("made " + repr(step.key())):
            return max(0, ticks - self.cost.work(_scaled(step, 0), held))
        if not walk:                    # its walk is the bound's legs' (each place walked into once)
            return ticks
        # from the nearest place it can be walked to from: forward may run it after any step
        walk = 0 if self.cost.site(step) in self.walked(node) else self.cost.walk_lb(step, held)
        if step.kind == "withdraw" and step.detail.get("p"):
            return round((ticks + walk) / step.detail["p"])
        return ticks + self.cost.dig_lb(step, held) + walk

    def walked(self, node):
        """The places the node's steps work at (forward's route passes each)."""
        return frozenset(p for p in (self.cost.site(st) for st, _h, _s in node.steps) if p is not None)

    def legs(self, node, floor, held, heldset, trips):
        """Ticks the walks still to come take at the least: every place what is left must be made at (whichever way
        it is made) is walked into once, from the nearest other place the route can come from (the feet, a place
        walked to, another such place) — distinct legs of any route, so their sum bounds it."""
        feet = self.cost.feet()
        if feet is None:
            return 0.0
        req: set = set()
        ways: list = []                 # each need: the walks of each of its ways
        for task in node.stack[floor:]:
            if task[0] == "emit" and task[1].kind != "await" and not (
                    (task[1].kind in MERGEABLE or (task[1].kind == "craft" and task[1].token in MERGEABLE_CRAFTS))
                    and node.inv.facts.get("made " + repr(task[1].key()))):
                site = self.cost.site(task[1])
                if site is not None:
                    req.add(("at", tuple(site), float(self.cost.walk_lb(task[1], BEST_TOOLS))))
            token = task[1] if task[0] == "need" and held(task[1]) < task[2] else tool_item(task[1], task[2]) \
                if task[0] == "tool" and not node.inv.has_tool(task[1], task[2], task[3]) else None
            if token is not None:
                req |= self.required(token, held, heldset, trips) or frozenset()
                ways.append(self.alternatives(token, held, heldset, trips))
        if not req and not ways:
            return 0.0
        price = self.walk_of(node, feet)
        # each need made one of its ways: the cheapest way's walks with what every way of the rest takes
        return max([price(req)] + [min(price(req | w) for w in alts) for alts in ways if alts])

    def walk_of(self, node, feet):
        """{walks} → their ticks at the least, each place walked into once from the nearest place a route comes from."""
        def price(req):
            at: dict = {}
            for r in req:
                if r[0] == "at":
                    at[r[1]] = min(at.get(r[1], math.inf), r[2])
            # the place before each: any a step can work at (planned or not: the route may pass one this bound skips)
            points = set(at) | base
            # a source nowhere known is a search of its own (the model prices each, a repeat joining its first);
            # each no more than the model's least walk to it (walk_lb: its nearest source may not be the place)
            return float(sum(min(self.cost.walk_ticks(min(math.dist(r, p) for p in points if p != r)), lb)
                             for r, lb in at.items())
                         + sum(r[2] for r in req if r[0] == "search"))
        base = self.walked(node) | {tuple(feet)} | self.cost.places() | {
            tuple(t[1].detail["pos"]) for t in node.stack if t[0] == "emit" and t[1].detail.get("pos")}
        return price

    def alternatives(self, token, held, heldset, trips):
        """[the walks one way of `token` takes] for each of its ways (its own, and what all ways of its inputs take):
        one of them is taken, so the cheapest of their walks bounds it."""
        if held(token) > 0 or self.stored(token):
            return []
        out = []
        for step, _station, ins in self.lb.shapes.get(token, self.lb.shapes.get(mid(token), ())):
            parts = [self.required(t, held, heldset, trips, frozenset({token})) for t in ins]
            if any(p is None for p in parts):
                continue
            own: frozenset = frozenset()
            if step.kind in ("gather", "mine", "hunt", "take") and token not in trips and mid(token) not in trips:
                site = self.cost.site(step)
                own = frozenset([("at", tuple(site), float(self.cost.walk_lb(step, BEST_TOOLS)))]) if site is not None else \
                    frozenset([("search", step.key(), float(self.cost.walk_lb(step, BEST_TOOLS)))])
            out.append(own.union(*(p for p in parts if p is not None)))
        return out

    def required(self, token, held, heldset, trips, seen=frozenset()):
        """The walks any making of `token` from here takes (each way's, intersected over its ways): ("at", a place)
        or ("search", the step, its walk) for a source nowhere known; none when held, stored, or a way needs none;
        None when no way makes it (a way through such a thing is no way)."""
        if token in seen:
            return None                 # made from itself: no way of a finite plan
        if held(token) > 0 or self.stored(token):
            return frozenset()
        relevant = self.lb.reach.get(token, set()) | {token, mid(token)}
        key = ("required", token, frozenset(relevant & heldset), frozenset(relevant & trips), frozenset(seen & relevant))
        if key in self.reach_c:
            return self.reach_c[key]
        out = None
        for step, _station, ins in self.lb.shapes.get(token, self.lb.shapes.get(mid(token), ())):
            own: frozenset = frozenset()
            if step.kind in ("gather", "mine", "hunt", "take") and token not in trips and mid(token) not in trips:
                site = self.cost.site(step)
                own = frozenset([("at", tuple(site), float(self.cost.walk_lb(step, BEST_TOOLS)))]) if site is not None else \
                    frozenset([("search", step.key(), float(self.cost.walk_lb(step, BEST_TOOLS)))])
            parts = [self.required(t, held, heldset, trips, seen | {token}) for t in ins]
            if any(p is None for p in parts):
                continue                # a way through what cannot be made: not one a plan takes
            need = own.union(*(p for p in parts if p is not None))
            out = need if out is None else out & need
            if not out:
                break
        self.reach_c[key] = out         # None: no way at all
        return out

    def least(self, token, n, inv):
        """Bound.least of `n` token from `inv`, once a search per what of its derivation the bag holds."""
        relevant = self.lb.reach[token] | {token, mid(token)}
        had = (tuple(sorted((k, v) for k, v in inv.counts.items() if v > 0 and k in relevant)),
               tuple(sorted((k, v) for k, v in inv.produced.items() if v > 0 and k in relevant)))
        key = (token, n, had)
        if key not in self.least_c:
            held = {k for k, _v in had[0]} | {k for k, _v in had[1]} | set(self.kept())
            self.least_c[key] = self.lb.least(token, n, self.holding(inv), {"held": held})
        return self.least_c[key]

    def holding(self, inv):
        """token → count had without making it: the bag's, and what the remembered containers hold (a withdraw is
        unpriced at the least)."""
        kept = self.kept()
        return lambda token: inv.available(token) + sum(kept.get(m, 0) for m in _MEMBERS[token])

    def kept(self):
        """{item id: count} the remembered containers here hold (memory), once a search."""
        if self._kept is None:
            mem, snap = self.cost.mem, self.cost.snap
            self._kept = {}
            if mem is not None:
                for c in mem.data.get("containers", {}).values():
                    if c.get("dimension") == snap.dimension:
                        for item, n in c.get("items", {}).items():
                            self._kept[mid(item)] = self._kept.get(mid(item), 0) + n
        return self._kept

    def stored(self, token):
        """Whether a remembered container holds `token` (cost.stored), once a search."""
        if token not in self.stored_c:
            self.stored_c[token] = bool(self.cost.stored(token))
        return self.stored_c[token]

    def near(self, block):
        """A station of `block` standing near (cost.station_near), once a search."""
        if block not in self.near_c:
            self.near_c[block] = bool(self.cost.station_near(block))
        return self.near_c[block]

    def walk_to(self, token, held, heldset, trips=frozenset()):
        """reach_lb, once per token and what of its derivation is held (`heldset`: the ids and groups had) or walked
        to already (`trips`)."""
        relevant = self.lb.reach.get(token, set()) | {token, mid(token)}
        key = (token, frozenset(relevant & heldset), frozenset(relevant & trips))
        if key not in self.reach_c:
            self.reach_c[key] = self.reach_lb(token, held, set(), trips)
        return self.reach_c[key]

    def own_walk(self, token, i, step):
        """The walk a way's own work is priced with at the least (cost.walk_lb:"""
        key = (token, i)
        if key not in self.walks:
            sourced = step.kind in ("mine", "gather", "hunt", "take")
            self.walks[key] = float(self.cost.walk_lb(step, BEST_TOOLS)) if sourced else 0.0
        return self.walks[key]

    def replay(self, root, needs, steps):
        """The held plan priced on today's world as the incumbent — (ticks, tie, steps) when it still runs from this…"""
        if any(n[0] == "do" for n in needs):
            return None
        inv = root.inv.clone()
        entries = []
        facts = dict(inv.facts)
        for st in steps:
            st = Step(st.kind, st.token, st.count, dict(st.detail))
            for tok, c in (st.detail.get("inputs") or self.recipe_inputs(st)).items():
                if inv.available(tok) < c:
                    return None
                inv.consume(tok, c, awaits=False)
            station = self.station_of(st)
            if station and inv.available(station) <= 0 and not self.near(station):
                return None             # its station neither carried, made before it, nor standing near
            for dim, n in self.call(st).items():
                if dim.startswith("tool:"):
                    if not inv.has_tool(dim.split(":")[1], int(dim.split(":")[2]), 0):
                        return None
                else:
                    if inv.available(dim) < n:
                        return None
                    inv.consume(dim, n, awaits=False)
            # the hard gates the search's own steps pass (prep's `when`, emit's fight line): none skipped on a replay
            when = self.when(st, facts)
            if isinstance(when, str) or any(facts.get(f) != v for f, v in when):
                return None
            if not self.cost.fight_line(st, inv.held())[0] and not any(
                    all(inv.has_tool(r[1], int(r[2]), 0) if r[0] == "tool" else inv.available(r[0]) >= int(r[1])
                        for r in rows) for rows in self.cost.line_kit(st, inv.held())):
                return None             # under the line, and no kit that clears it made before it (emit's rule)
            entries.append((st, inv.held(), len(entries)))
            facts.update(self.sets(st))
            material, _, kind = bare(st.token).rpartition("_")
            if st.kind == "craft" and kind in TOOL_KINDS and material in TOOL_USES:
                tier = TIER_OF_MATERIAL[material]
                inv.add_tool(kind, tier, spare_uses(TOOL_USES[material]))
            else:
                inv.add(st.token, st.count)
        for n in needs:
            if n[0] == "fact":
                if facts.get(n[1]) != n[2]:
                    return None             # a fact asked that its steps do not make
            elif n[0] == "tool" and not inv.has_tool(n[1], int(n[2]), 0) or n[0] != "tool" and inv.available(n[0]) < int(n[1]):
                return None
        out, ticks = forward(entries, self.cost, self.start_tools)
        return ticks, (), out

    def recipe_inputs(self, step):
        """{token: count} a craft or smelt of `step` takes by its recipe (a held step may not name them)."""
        for made, src in self.sources(step.token):
            if src[0] == step.kind:
                return {t: c for t, c in way(src, made, step.count)[1]}
        return {}

    def no_bound(self, root):
        """Why the bound says no plan exists: the needs nothing in the tables makes from this bag."""
        held = root.inv.available
        lost = [t[1] for t in root.stack if t[0] == "need" and self.lb.least(t[1], t[2], held, {}) == math.inf]
        lost += [tool_item(t[1], t[2]) for t in root.stack if t[0] == "tool"
                 and self.lb.least(tool_item(t[1], t[2]), 1, held, {}) == math.inf]
        return "no way to obtain " + ", ".join(dict.fromkeys(lost)) if lost else "no way found"

    def reach_lb(self, token, held, seen, trips=frozenset()):
        """Ticks no plan for `token` can walk less than (`trips`: tokens whose making walked already, its own walk
        free; what it is made from still walked to):"""
        if token in seen or held(token) > 0 or self.stored(token):
            return 0.0
        seen = seen | {token}
        best = math.inf
        walked = token in trips or mid(token) in trips
        for i, (step, _station, ins) in enumerate(self.lb.shapes.get(token, self.lb.shapes.get(mid(token), ()))):
            own = 0.0 if walked else self.own_walk(token, i, step)
            best = min(best, max([own] + [self.reach_lb(t, held, seen, trips) for t in ins]))
            if best <= 0:
                return 0.0
        return 0.0 if best == math.inf else best

    # -- one node to its next choice: resolved in place; returns children, [] when it died, None when complete
    def advance(self, node, floor=0):
        if self.stop():
            raise api.Interrupted("a hazard while planning")      # S7
        self.spent += 1
        SPENT["steps"] += 1
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
                node.inv.set_fact(task[1], node.inv.facts.get(task[1], 0) + task[2])
                got = None
            elif op == "close":
                at = len(node.open) - 1 - node.open[::-1].index(task[1])
                node.open = node.open[:at] + node.open[at + 1:]
                got = None
            elif op == "addtool":
                node.inv.add_tool(task[1], task[2], task[3])
                got = None
            else:
                raise AssertionError(op)
            if got is not None:
                return got
        return None

    @staticmethod
    def cut():
        """A search the round's budget stopped short: said (budget_spent), P5 and the G3 checks read it."""
        SPENT["budget"] += 1

    def dead(self, why):
        self.reasons.append(why)
        return []

    def options(self, node, opts, horizon=None, asked=""):
        """Children for each option ([(tie, [tasks in run order])]); one option runs in place (no branch)."""
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
        """What a step is about to take from a job still running (a sown crop, a furnace) is collected right before…"""
        for item, n in sorted(node.inv.awaited.items()):
            step = Step("await", item, n)
            step.est = self.cost.estimate(step)
            node.steps.append((step, node.inv.held(), len(node.steps)))
            node.g += step.est
        node.inv.clear_awaited()

    def need(self, node, token, n, depth, fresh):
        if depth > self.lb.depth:
            return self.dead(f"requirement chain too deep at {token}")
        have = 0 if fresh else node.inv.available(token)
        if not fresh:
            node.inv.consume(token, min(have, n), awaits=depth > 0)
            self.await_first(node)
        missing = n - have if not fresh else n
        if missing <= 0:
            return None
        # the same thing asked again further down: one way chosen for all of it, the rest left in the bag for it
        extra = 0 if fresh else sum(t[2] for t in node.stack if t[0] == "need" and t[1] == token)
        if token in node.open:
            return self.dead(f"{token} asked again while it is being made (a cycle)")
        opts = self.ways(node, token, missing + extra, depth)
        if extra:
            new_opts = []
            for tie, tasks in opts:
                if any(t[0] == "use" and t[1] == token for t in tasks):
                    new_tasks = [("use", token, missing) if t == ("use", token, missing + extra) else t for t in tasks]
                else:
                    emit_task = next((t for t in tasks if t[0] == "emit"), None)
                    if emit_task and emit_task[1].kind in ("mine", "smelt"):
                        emit_idx = tasks.index(emit_task)
                        new_tasks = tasks[:emit_idx + 1] + [("add", token, extra)] + tasks[emit_idx + 1:]
                    else:
                        new_tasks = tasks
                new_opts.append((tie, new_tasks))
            opts = new_opts
        if not opts:
            return self.dead(self.no_way(token))
        node.open = node.open + (token,)
        return self.options(node, [(tie, self.closing(token, tasks)) for tie, tasks in opts], asked=token)

    @staticmethod
    def closing(key, tasks):
        """An option's tasks with `key` closed once its own making is done — before a re-ask of the rest of it."""
        at = next((i for i, t in enumerate(tasks) if t[0] in ("need", "fact") and t[1] == key), len(tasks))
        return tasks[:at] + [("close", key)] + tasks[at:]

    @staticmethod
    def no_way(token):
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
                ripe_n = self.cost.ripe(made) * TAKEABLE[made]["gives"][made]
                for r in ((1,) if ripe_n >= n else ()) + (0,):
                    out.append(((0, 0, i, r), self.tasks(token, n, depth, len(node.steps), *way(src, made, n, ripe=r))))
                continue
            got = way(src, made, n)
            if any(node.inv.available(t) < c and not self.sources(t) for t, c in got[1]):
                continue                # an input nothing makes and the bag lacks: not a way from here
            step = got[0]
            if src[0] == "take" and self.cost.site(step) is None:
                continue                # a thing standing in the world is taken only where one is known
            out.append(((0, 0, i), self.tasks(token, n, depth, len(node.steps), *got)))
            carried = self.from_carried(node, src, made, n)
            part = way(src, made, carried) if carried else None
            if part is not None:
                # the action keyed by its inputs' source: what the bag's inputs make is its own step, run when it pays
                out.append(((0, 0, i, 1), self.tasks(token, carried, depth, len(node.steps), *part)
                            + [("need", token, n - carried, depth, False)]))
        if self.kinds is None:
            out += self.withdrawals(node, token, n, depth)
        return out

    @staticmethod
    def from_carried(node, src, made, n):
        """The most of `n` a craft or smelt makes from inputs the bag already holds (0 < k < n), else 0:"""
        if src[0] not in ("craft", "smelt") or n < 2:
            return 0
        whole = way(src, made, n)
        if all(node.inv.available(t) >= c for t, c in whole[1]):
            return 0
        for k in range(n - 1, 0, -1):
            got = way(src, made, k)
            if got[0].count < n and all(node.inv.available(t) >= c for t, c in got[1]):
                return got[0].count
        return 0

    def withdrawals(self, node, token, n, depth):
        """Taking it from a container that holds it (memory.stored, each weighed by the chance it still does):"""
        out = []
        for j, (pos, item, have, p) in enumerate(self.cost.stored(token)):
            taken = f"taken:{tuple(pos)}:{item}"          # what this plan already takes out of that container
            have -= node.inv.facts.get(taken, 0)
            if p <= 0 or have <= 0:
                continue
            k = min(n, have)
            step = Step("withdraw", item, k, {"pos": list(pos), "p": p})
            tasks: list[tuple] = [("emit", step, depth, len(node.steps)), ("taken", taken, k)]
            if k < n:
                tasks.append(("need", token, n - k, depth, False))
            out.append(((0, -1, j), tasks))       # taken ahead of made at an equal bound: the dive tries it first
        return out

    def tasks(self, token, n, depth, start, step, inputs, station, adds):
        """The run-order tasks of one way: its inputs, its station, its call's own needs, the step, what it adds."""
        station = station or self.station_of(step)     # the contract's own station where the recipe names none
        out: list[tuple] = [("need", t, c, depth + 1, False) for t, c in inputs]
        short = getattr(self.cost, "step_blocks_short", lambda s: 0)(step)
        if short:
            # its site reached only over treads the bag lacks: way blocks first, the cheapest kind by seconds (G3)
            out.append(("need", "building", short, depth + 1, False))
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
        assert children is not None, "two FUELS: the fuel always branches"
        picks = [tasks[-1] for _t, tasks in opts]
        for c, (_op, _s, fuel, k) in zip(children, picks):
            self.set_fuel(c, step, fuel, k)
        return children

    def set_fuel(self, node, step, fuel, k):
        """The fuel this node's copy of the smelt step burns (the step is copied: siblings choose their own)."""
        new = Step(step.kind, step.token, step.count, {**step.detail, "fuel": fuel,
                                                        "inputs": {**step.detail["inputs"], fuel: k}})
        node.stack = [(t[0], new) + t[2:] if t[1] is step else t for t in node.stack]

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
            if t < tier:
                continue
            new = TOOL_USES[TOOL_MATERIAL_FOR_TIER[t]]
            if t != tier and (self.kinds is not None or making):
                continue                # a tool made to make one of its own kind: the tier asked, no better
            opts.append(((t, use_rank(kind), 0),
                         [("need", tool_item(kind, t), 1, depth + 1, True), ("addtool", kind, t, spare_uses(new))]))
        return self.options(node, opts)

    def saves_at_most(self, node, kind, have, tier, step):
        """Ticks a `kind` tool of `tier` (over `have`) saves at the most on all left after `step`: every step on the stack
        and every need's making through its ways at their dearest in that work (an upper bound); inf when a task
        is not in the tables."""
        memo = self.saves_c.setdefault((kind, have, tier), {})

        def own(st):
            b, k = own_work(st)
            b = [x for x in b if tool_kind(x) == kind] if kind != "sword" else []
            k = k if kind == "sword" else []
            return (work_s(b, k, {kind: have} if have >= 0 else {}, TICKS_PER_S)
                    - work_s(b, k, {kind: tier}, TICKS_PER_S)) * TICKS_PER_S

        def unit(token, path=()):
            if token in memo:
                return memo[token]
            if token in path:
                return None             # a cycle: no finite plan takes it
            best = 0.0
            for made, src in self.sources(token):
                step, inputs, station, _adds = way(src, made, 1)
                parts = [unit(t, path + (token,)) for t, _c in inputs] + [unit(station, path + (token,)) if station else 0.0]
                if any(p is None for p in parts):
                    continue
                best = max(best, (own(step) + sum(c * p for (_t, c), p in zip(inputs, parts))) / max(1, step.count)
                           + (parts[-1] or 0.0))
            memo[token] = best
            return best

        def made_fact(fact, value, depth=0):
            """The most a fact's making (each way FACT_STEPS names: its own work, its call's needs, the facts its
            `when` asks) saves; inf where no table says (a fact no step makes, or a chain past FACT_DEPTH)."""
            if depth > FACT_DEPTH:
                return math.inf
            worst = None
            for kind, token in self.fact_steps(fact, value):
                st = Step(kind, token, 1, {})
                got = own(st)
                for dim, n in self.call(st).items():
                    if not dim.startswith("tool:"):
                        got += n * (unit(dim) or 0.0)
                when = self.when(st, {})
                if isinstance(when, str):
                    continue
                got += sum(made_fact(f, v, depth + 1) for f, v in when)
                worst = got if worst is None else max(worst, got)
            return math.inf if worst is None else worst

        total = 0.0
        emitted = {id(t[1]) for t in node.stack if t[0] == "emit"}
        for task in node.stack:
            op = task[0]
            if op == "need":
                # what a remembered container holds is taken, not made: no work of this kind for it
                kept = sum(self.kept().get(m, 0) for m in _MEMBERS.get(task[1]) or members(task[1]))
                total += max(0, task[2] - kept) * (unit(task[1]) or 0.0)
            elif op == "tool":
                total += unit(tool_item(task[1], task[2])) or 0.0
            elif op == "station":
                total += unit(task[1]) or 0.0
            elif op == "emit" and task[1] is not step:
                total += own(task[1])
            elif op == "prep" and task[1] is not step and id(task[1]) not in emitted:
                total += own(task[1])           # a step whose emit is not queued yet: its work is still to come
            elif op == "fuel":
                total += task[1].count * (unit("coal") or 0.0)
            elif op == "fact":
                total += made_fact(task[1], task[2])
            elif op == "do":
                return math.inf
        return total

    def tool_price(self, node, kind, tier):
        """Ticks a `kind` tool of `tier` costs here at the least: its making's least, its craft, the tools made for it
        alone (the faster tool's payback rule, one price)."""
        item = tool_item(kind, tier)
        return self.least(item, 1, node.inv) + self.first_run(item) + self.only_for(node, item)

    def first_run(self, item):
        """Ticks the step that makes one `item` takes at its least (its craft's own work, once: the least prices
        count a run's marginal work, a craft's none past the first)."""
        return min((self.cost.work(way(src, made, 1)[0], BEST_TOOLS) for made, src in self.sources(item)), default=0)

    def only_for(self, node, item):
        """The least of the tools making `item` takes that the bag lacks and nothing else left to plan asks for:
        made for this tool alone, so its price is the tool's too."""
        asked = [t for t in node.stack if t[0] in ("tool", "need")]

        def wanted(kind, tier):
            return any(t[1] == kind and t[2] >= tier if t[0] == "tool" else self.lb.needed(t[1]).get(kind, -1) >= tier
                       for t in asked)
        return max((self.least(tool_item(k, tr), 1, node.inv) for k, tr in self.lb.needed(item).items()
                    if not node.inv.has_tool(k, tr) and not wanted(k, tr)), default=0.0)

    def uses(self, step, kind):
        """Uses of a `kind` tool the step's work takes (a break or a hit each)."""
        breaks, kills = own_work(step)
        if kills:
            return len(kills)
        return max(1, sum(1 for b in breaks if tool_kind(b) == kind)) if breaks else 1

    def prep(self, node, step, depth):
        """Before a step:"""
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
        if depth > self.lb.depth:
            return self.dead(f"requirement chain too deep at {fact}")
        key = f"{fact}={value}"
        if key in node.open:
            return self.dead(f"{fact} {value} asked again while it is being made (a cycle)")
        opts = []
        for i, (kind, token) in enumerate(self.fact_steps(fact, value)):
            step = Step(kind, token, 1, {})
            opts.append(((0, 0, i), [("prep", step, depth), ("emit", step, depth, len(node.steps))]))
        if not opts:
            return self.dead(f"no way to make {fact} {value}")
        node.open = node.open + (key,)
        return self.options(node, [(tie, self.closing(key, tasks)) for tie, tasks in opts], asked=f"{fact} {value}")

    def speed(self, node, step, depth):
        """The tools a step's work pays for:"""
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
                if t <= held.get(kind, -1):
                    continue
                saved = (base - work_s(breaks, kills, {**held, kind: t}, TICKS_PER_S)) * TICKS_PER_S
                if saved <= 0:
                    continue
                # what it can save over all that is left (each need's ways at their most) cannot pay its least
                if not self.exact and saved + self.saves_at_most(node, kind, held.get(kind, -1), t, step) \
                        <= self.tool_price(node, kind, t):
                    continue
                opts.append(((t + 1, use_rank(kind), 1), [("tool", kind, t, self.uses(step, kind), depth)]))
        return self.options(node, opts, self.after(node, step))

    def emit(self, node, step, depth, start, raised=False):
        held = node.inv.held()
        # S5: an optional fight only above the line, with the weapon the plan holds by then (brain's one judge)
        ok, why = (True, None) if raised else self.cost.fight_line(step, held)
        if not ok:
            # under the line: each kit that clears it (brain.line_raisers) made first, priced like any need
            kits = self.cost.line_kit(step, held)
            opts = [((k + 1, 0, 1), [("tool", r[1], int(r[2]), self.uses(step, r[1]), depth) if r[0] == "tool"
                                     else ("need", r[0], int(r[1]), depth + 1, False) for r in rows]
                     + [("emit", step, depth, start, True)]) for k, rows in enumerate(kits)]
            if not opts:
                return self.dead(f"{step.kind}: {why}")
            self.reasons.append(f"{step.kind}: {why}")
            return self.options(node, opts)
        step = Step(step.kind, step.token, step.count, dict(step.detail))
        breaks, kills = own_work(step)
        for kind in {k for k in map(tool_kind, breaks) if k is not None} | ({"sword"} if kills else set()):
            if kind in held:
                node.inv.wear(kind, 0, self.uses(step, kind))
        ticks = self.least_of(node, step, held)
        if self.cost.table_back(step):
            node.inv.set_fact("table back", True)       # its break paid: the crafts after it sit at the same table
        mergeable = step.kind in MERGEABLE or (step.kind == "craft" and step.token in MERGEABLE_CRAFTS)
        made = "made " + repr(step.key())       # in the bag's facts: what a sub-plan is remembered by sees it too
        if mergeable and not node.inv.facts.get(made):
            node.inv.set_fact("trip " + step.token, True)
        if mergeable:
            node.inv.set_fact(made, True)
        if self.hungry and not node.inv.facts.get("fed"):
            ticks += round(ticks * self.hungry)         # F1l: hunger's seconds until a step makes food
            node.inv.set_fact("fed", mid(step.token) in FOOD_IDS and step.kind in MAKES_FOOD)
        node.g += ticks
        for fact, value in self.sets(step).items():
            node.inv.set_fact(fact, value)
        node.steps.append((step, held, start))      # start: where the steps it needs begin
        node.path = node.path + ((node.g, self.h(node)),)
        return None

    # -- the whole search
    def finish(self, node):
        steps, ticks = forward(node.steps, self.cost, self.start_tools)
        self.considered.append((plan_name(steps), ticks / TICKS_PER_S, steps))
        self.paths[id(steps)] = node.path
        return ticks, node.tie, steps

    def settle(self, node, floor=0, cap=math.inf):
        """`node` run down to `floor`, each choice on the way settled by what its options' own runs cost (each run t…"""
        while len(node.stack) > floor:
            if cap < math.inf and round_spent() and not self.exact:
                Search.cut()
                return None                   # the round's steps spent and a way already had: this one is not weighed
            if cap < math.inf and node.g + self.h(node, floor) > cap:
                return None                   # no cap yet: nothing to prune against, the bound not asked
            got = self.advance(node, floor)
            if got is None:
                return node
            best, best_f, before = None, math.inf, len(self.reasons)
            # a container's goods tried first in the dive (only the order: the bound still weighs every way)
            for k, c in enumerate(sorted(got, key=lambda c: (not (self.greedy and c.tie and c.tie[-1][:2] == (0, -1)),
                                                             c.g + self.h(c, c.horizon), c.tie))):
                width = DIVE_WIDTH if self.spent <= DIVE_NODES and not round_spent() else 1   # spent: the first way that can be had
                if width == 1 and k == 1 and not self.exact:
                    SPENT["budget"] += 1
                if best is not None and self.greedy and not self.exact and (floor > 0 or round_spent()) and k >= width:
                    break                     # inside a choice, the few least-bound ways; A* weighs the rest
                done = self.settled(c, cap if best is None else min(cap, best_f))
                # weighed with what is left after it: a trip one way makes, the rest of the plan takes free
                if done is not None and (best is None or (done.g + self.h(done), done.tie) < (best_f, best.tie)):
                    best, best_f = done, done.g + self.h(done)
            if best is None:
                if cap == math.inf and len(self.reasons) > before:      # every way died, not merely dearer
                    self.dead(f"no way to {got[0].asked}: " + "; ".join(dict.fromkeys(self.reasons[before:])))
                return None
            node = best
        return node

    def touched(self, node):
        """The item ids what is asked above `node`'s horizon can read or change: each token's derivation (Bound.reach),
        a step's inputs and call, the fuels and stations — the part of the bag a sub-plan's price and steps depend on."""
        out = set(STATIONS) | {mid(f) for f, _per in FUELS}
        for t in node.stack[node.horizon:]:
            tokens = []
            if t[0] in ("need", "station", "use", "add"):
                tokens.append(t[1])
            elif t[0] == "tool":
                tokens.append(tool_item(t[1], t[2]))
            elif t[0] in ("emit", "prep", "speed", "fuel") and isinstance(t[1], Step):
                tokens += [t[1].token, *t[1].detail.get("inputs", {}), *(d for d in self.call(t[1]) if ":" not in d)]
            for tok in tokens:
                out |= {tok, mid(tok)} | self.lb.reach.get(tok, set()) | self.lb.reach.get(mid(tok), set())
        return {m for tok in out for m in (_MEMBERS.get(tok) or members(tok))} | out

    def settled(self, node, cap):
        """`settle` of a node down to its horizon, memoised on what is asked, the part of the bag it reads (`touched`)
        and the places walked to (the prefix's: a step at one walks nothing more): the same price under any other
        items, its change to the bag applied to the asker's."""
        near = self.touched(node)
        inv = node.inv

        def part(table):
            return tuple(sorted((k, v) for k, v in table.items() if v and k in near))
        key = (signature(node.stack[node.horizon:], self.task_keys), part(inv.counts), part(inv.produced),
               tuple(sorted(inv.tools)), part(inv.pending), part(inv.awaited), tuple(sorted(inv.facts.items())),
               node.open, self.walked(node))
        hit = self.memo.get(key)
        if hit is not None:
            ok, delta, steps, g, tie, path = hit
            if not ok or node.g + g > cap:
                return None
            out = node.child()
            out.inv, out.stack = _applied(node.inv, delta), out.stack[:node.horizon]
            out.steps, out.g, out.tie = node.steps + list(steps), node.g + g, node.tie + tie
            out.path = node.path + tuple((node.g + dg, h) for dg, h in path)
            return out
        start, g0, t0, p0, before = len(node.steps), node.g, len(node.tie), len(node.path), node.inv.clone()
        done = self.settle(node, node.horizon, cap)
        if done is None:
            if cap == math.inf:
                self.memo[key] = (False, None, (), 0.0, (), ())
            return None
        self.memo[key] = (True, _delta(before, done.inv), tuple(done.steps[start:]), done.g - g0, done.tie[t0:],
                          tuple((g - g0, h) for g, h in done.path[p0:]))
        return done

    def dive(self, root, cap=math.inf):
        """The incumbent: every choice settled by its options' own cost (`settle`)."""
        self.greedy = True
        try:
            node = self.settle(root, 0, cap)
        finally:
            self.greedy = False
        return None if node is None else self.finish(node)

    def plan(self, root, needs, incumbent=None, cap=math.inf):
        held, after, run = [], [], []       # run order: what is had, then counted back as held, then what is done
        for need in needs:
            if need[0] == "tool":
                run.append(("tool", need[1], int(need[2]), 1, 0))
            elif need[0] == "fact":
                after.append(("fact", need[1], need[2], 0))
            elif need[0] == "do":
                station = self.station_of(need[1])
                after += ([("station", station, 0)] if station else []) + [("prep", need[1], 0), ("emit", need[1], 0, 0)]
            elif mid(need[0]) in STATIONS:
                # a station the goal asks for stands from when it is had: every later step reuses it, none makes another
                run += [("need", need[0], int(need[1]), 0, False), ("add", need[0], int(need[1]))]
            else:
                # a goal's items are to be held: had, then counted back for what the plan does after them
                run.append(("need", need[0], int(need[1]), 0, False))
                held.append(("add", need[0], int(need[1])))
        root.stack.extend(reversed(run + held + after))
        self.start_tools = list(root.inv.tools)   # what the plan's steps are priced as run from (price_as_run)
        if self.h(root) == math.inf:
            raise Unplannable(self.no_bound(root))      # nothing in the tables makes it from here: no search at all
        replayed = self.replay(root, needs, incumbent) if incumbent else None
        if replayed is not None and replayed[0] < self.h(root):
            replayed = None                 # dearer at the least than it claims: a held plan that does not run from here
        best = replayed if replayed is not None else self.dive(root.child(), cap)   # the held plan, priced today: the bar
        first_reason = self.reasons[-1] if self.reasons else None
        heap: list[tuple[float, tuple, int]] = []
        open_ = {}                          # seq → (node, or None for a complete plan, its steps)

        def push(f, tie, node, steps=None):
            n = next(self.counter)
            open_[n] = (node, steps)
            heapq.heappush(heap, (f, tie, n))

        push(self.h(root), (), root)
        nodes = 0
        visited: dict = {}                  # the transposition table: (what is left, the bag, what is open) → least g
        allowance = search_allowance()
        while heap and (self.exact or nodes <= allowance and not round_spent()):
            f, tie, n = heapq.heappop(heap)
            if (f, tie) >= ((best[0], best[1]) if best is not None else (cap, ())):
                break
            node, steps = open_.pop(n)
            if steps is not None:
                best = (f, tie, steps)
                break
            seen = (signature(node.stack, self.task_keys), node.inv.signature(), node.open)
            if not self.exact and visited.get(seen, math.inf) <= node.g:
                continue                      # the same state reached as cheaply before: nothing new below it
            visited[seen] = node.g
            nodes += 1
            got = self.advance(node)
            if got is None:
                ticks, tie, steps = self.finish(node)
                if best is None or (ticks, tie) < (best[0], best[1]):
                    best = (ticks, tie, steps)    # a whole plan cheaper than the best: the best now, budget or not
                    nodes = 0                 # a better plan found: the budget counts the expansions since one
                continue
            for c in got:
                fc = c.g + self.h(c)
                if fc < cap and (best is None or (fc, c.tie) < (best[0], best[1])):
                    push(fc, c.tie, c)
        if heap and (nodes > allowance or round_spent()) and not self.exact and (best is None or heap[0][0] < best[0]):
            SPENT["budget"] += 1              # stopped with cheaper possible: P5 may be missed, said (budget_spent)
        if best is None and cap < math.inf:
            if round_spent():
                raise Cut("not weighed: the round's search steps were spent")
            raise Dearer(f"no plan under {cap:.0f} ticks")
        if best is None:
            raise Unplannable(first_reason or (self.reasons[0] if self.reasons else "no way found"))
        if not self.exact and id(best[2]) in self.paths:
            PATHS.append((best[0], self.paths[id(best[2])]))      # the plan taken, and its way there (P3 reads it)
        return best[2]


def forward(entries, cost, tools=None):
    """The plan as it will run:"""
    steps, held = [], []
    index, where = {}, []        # key → its first step's place; each entry's place among the merged steps
    for i, (step, h, start) in enumerate(entries):
        k = step.key()
        mergeable = step.kind in MERGEABLE or (step.kind == "craft" and step.token in MERGEABLE_CRAFTS)
        # merged only where everything it needs already comes before the step it joins: none of the steps after that
        if k in index and mergeable and all(where[j] < index[k] for j in range(start, i)) \
                and not any(_ids_made(steps[p]) & _ids_used(step) for p in range(index[k] + 1, len(steps))):
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
    out = _cluster_crafts([steps[i] for i in order])
    ests = price_as_run(out, tools, cost) if tools is not None else price_as_run(out, None, cost)
    for step, est in zip(out, ests):
        step.est = est
    return out, sum(ests)


def _cluster_crafts(steps):
    """Pure: group ready crafts next to preceding craft steps so craft_run batches them in one table sitting."""
    out = list(steps)
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            if out[i].kind != "craft":
                continue
            needed_ids = _ids_used(out[i])
            earliest = 0
            for k in range(i):
                if _ids_made(out[k]) & needed_ids:
                    earliest = k + 1
            for j in range(earliest, i):
                if out[j].kind == "craft" and (j + 1 < i and out[j + 1].kind != "craft"):
                    craft_step = out.pop(i)
                    out.insert(j + 1, craft_step)
                    changed = True
                    break
            if changed:
                break
    return out


def price_as_run(steps, tools, cost, held=None) -> list:
    """Pure given the cost:"""
    out, at = [], None
    have: list[tuple] = list(tools or ())
    hungry = cost.hunger_rate()      # F1l: hunger's seconds until a step makes food
    table = False                    # a table placed and carried on already: its break is paid once
    spent, way_spent = 0, getattr(cost, "way_spent", None)     # way blocks the steps before spent (D6)
    for i, step in enumerate(steps):
        tiers = held[i] if held is not None else _tiers(have)
        est = cost.estimate(step, tiers, at, table_back=not table, **({"spent": spent} if spent else {}))
        spent += way_spent(step, at, spent) if way_spent is not None else 0
        table = table or cost.table_back(step)
        if hungry:
            est += round(est * hungry)
            hungry = 0.0 if mid(step.token) in FOOD_IDS and step.kind in MAKES_FOOD else hungry
        site = cost.site(step)
        at = site if site is not None else at
        material, _, kind = bare(step.token).rpartition("_")
        if kind in TOOL_KINDS and material in TOOL_USES and step.kind not in ("use", "await"):
            # a tool had from here on, however it came: made, taken from a chest, traded
            have.append((kind, TIER_OF_MATERIAL[material], 1))
        out.append(est)
    return out


def _tiers(tools):
    """Pure: {tool kind: the best tier with a use left} of (kind, tier, uses) tools."""
    out = {}
    for k, t, u in tools:
        if u > 0 and t in TOOL_MATERIAL_FOR_TIER:
            out[k] = max(out.get(k, t), t)
    return out


def _ids(token):
    return {mid(m) for m in (token, *members(token))} if token else set()


def _ids_made(step):
    """Pure: the item ids a step puts in the bag (a group read through its members)."""
    return _ids(step.token)


def _ids_used(step):
    """Pure: the item ids a step takes or must have: its inputs, its container, its input, its station (the recipe's
    or its contract's)."""
    return set().union(*(_ids(t) for t in list(step.detail.get("inputs", {})) + [step.detail.get("container"),
                                                                                    step.detail.get("input"),
                                                                                    step.detail.get("station"),
                                                                                    step_station(step)]))


def step_needs(steps):
    """Pure:"""
    makes = [_ids_made(s) for s in steps]
    uses = [_ids_used(s) for s in steps]
    # a tool or station is had from the step that brings it, however (craft, take, withdraw): every later step may need it
    kept = [any(bare(i).rpartition("_")[2] in TOOL_KINDS or i in STATIONS for i in m) for m in makes]
    need: list[set] = []
    for j in range(len(steps)):
        direct = {i for i in range(j) if makes[i] & uses[j] or kept[i]}
        need.append(direct.union(*(need[i] for i in direct)))
    return need


def walk_order(steps, cost):
    """Indexes of `steps` in run order:"""
    if cost.snap is None:
        return list(range(len(steps)))
    sites = {i: cost.site(s) for i, s in enumerate(steps)}
    placed = [i for i, p in sites.items() if p is not None]
    if len(placed) < 2 or len(placed) > REORDER_MAX:
        return list(range(len(steps)))
    feet = tuple(cost.snap.feet)
    need = step_needs(steps)
    before = {j: {i for i in placed if i in need[j]} for j in placed}
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
    route = min(full + [planned])[1]
    if route == tuple(placed):
        return list(range(len(steps)))
    # each placed step takes the slot of the placed step its route turn falls on; every step waits for what it needs
    rank = {i: i for i in range(len(steps))}
    rank.update({j: placed[k] for k, j in enumerate(route)})
    out, done, left = [], set(), set(range(len(steps)))
    while left:
        i = min((i for i in left if need[i] <= done), key=lambda i: (rank[i], i))
        out.append(i)
        done.add(i)
        left.discard(i)
    return out


def plan_name(steps) -> str:
    """A complete plan's name: the ways it takes, in order, each once."""
    return " → ".join(dict.fromkeys(f"{s.kind} {bare(s.token)}" for s in steps)) or "nothing to do"


SPENT: dict = {"steps": 0, "budget": 0, "round": None}   # steps advanced, searches a budget stopped, where a round began
PATHS: list = []        # each plan a round's searches took: (its price as run, (g, h) at every step on its way there)
ROUND_STEPS = 1500      # the user's cap on a round's search steps, against a runaway search (P4)
FACT_DEPTH = 3          # facts asked by the facts a step needs, followed this deep by the faster tool's payback bound
DIVE_RESERVE = 200      # of it kept from A* for the first plans the round's later searches still owe


def search_allowance():
    """Expansions one search may make: inside a round, half of what the round has left (the searches after it keep
    the rest); outside one (a test, a bench estimate), MAX_NODES since its last better plan."""
    if SPENT["round"] is None:
        return MAX_NODES
    return max(0, ROUND_STEPS - DIVE_RESERVE - (SPENT["steps"] - SPENT["round"])) // 2


def round_spent():
    """Pure given SPENT: a round is open (brain.decide marks where it began) and has advanced its cap of steps."""
    return SPENT["round"] is not None and SPENT["steps"] - SPENT["round"] >= ROUND_STEPS
lifecycle.in_place(__name__, "SPENT")
lifecycle.in_place(__name__, "PATHS")


def plan_candidates(inv, needs, cost, pending=None, jobs=None, kinds=None, exact=False, held=None,
                    cap=math.inf) -> list:
    """The plans the search priced for `needs` from this bag, cheapest first:"""
    if not needs:
        return [(plan_name([]), 0.0, [])]
    root = Node(from_bag(inv, pending, jobs, cost.reserved, cost.facts()), [], [])
    # one plan per question a round asks (the same needs from the same bag: needs, upkeep, queue, night)
    cache = cost.plans()
    key = ("plan", tuple(repr(tuple(n)) for n in needs), root.inv.signature(), tuple(sorted(kinds)) if kinds else None,
           exact, tuple((s.kind, s.token, s.count) for s in held) if held else None)
    if key in cache:
        if isinstance(cache[key], str):
            raise Unplannable(cache[key])           # the same question failed this round already
        return [(name, seconds, [Step(s.kind, s.token, s.count, dict(s.detail), s.est) for s in steps])
                for name, seconds, steps in cache[key]]
    search = Search(cost, kinds, exact)
    try:
        chosen = search.plan(root, list(needs), None if exact else held, cap)
    except Unplannable as e:
        if not isinstance(e, Dearer):
            cache[key] = str(e)
        raise
    out = {plan_name(chosen): (plan_name(chosen), sum(s.est for s in chosen) / TICKS_PER_S, chosen)}
    for name, seconds, steps in sorted(search.considered, key=lambda c: c[1]):
        out.setdefault(name, (name, seconds, steps))
    got = list(out.values())
    cache[key] = [(name, seconds, [Step(s.kind, s.token, s.count, dict(s.detail), s.est) for s in steps])
                  for name, seconds, steps in got]
    return got


def plan_needs(inv, needs, cost, pending=None, jobs=None, kinds=None, held=None, exact=False, cap=math.inf):
    """The cheapest steps that make `needs` held from this bag (`plan_candidates`' first); `held`:"""
    return plan_candidates(inv, needs, cost, pending, jobs, kinds, exact, held, cap)[0][2]


@dataclass
class Target:
    """One goal of a round's plan:"""
    name: str
    needs: list
    rank: int = 0
    options: tuple = ()     # one of: ((way, its needs, seconds it adds beyond its steps), …) — the cheapest whole plan's

def food_left_s(cost) -> float | None:
    """Seconds the body's bar lasts with nothing eaten (beliefs risk.food_drain_s a point), None when food is carrie…"""
    from . import beliefs
    from .knowledge import food_count
    if cost.snap is None:
        return None
    state, inv = cost.snap.state, cost.snap.inv
    if "food" not in state or food_count(inv) > 0:
        return None
    return float(state["food"]) * float(beliefs.value("risk.food_drain_s"))


def fed_in_time(steps, left_s) -> bool:
    """Pure: the plan puts food in the bag before the bar runs out (`left_s`), or never needs to."""
    if left_s is None:
        return True
    clock = 0.0
    for st in steps:
        clock += st.est / TICKS_PER_S
        if mid(st.token) in FOOD_IDS and st.kind in MAKES_FOOD:
            return clock <= left_s
        if clock > left_s:
            return False
    return True


class _After:
    """The bag as a plan's steps leave it: their outputs in, their inputs out, the tools they make carried."""

    def __init__(self, inv, steps, eaten=()):
        self.equipment = getattr(inv, "equipment", {})
        counts = Counter()
        for slot in inv.slots:
            counts[slot["id"]] += slot["count"]
        self._tools = {k: list(inv.tools(k)) for k in TOOL_KINDS}
        for st in steps:
            for tok, c in st.detail.get("inputs", {}).items():
                for m in members(tok):
                    take = min(int(math.ceil(c)), counts[mid(m)])
                    counts[mid(m)] -= take
                    c -= take
            item = mid(GROUPS[st.token][0]) if st.token in GROUPS else mid(st.token)
            material, _, kind = bare(item).rpartition("_")
            if st.kind == "craft" and kind in TOOL_KINDS and material in TOOL_USES:
                tier = TIER_OF_MATERIAL[material]
                self._tools[kind].append((tier, TOOL_USES[material], item))
            elif st.kind not in ("seek", "look", "reach", "portal", "enter", "activate", "slay", "sleep", "wait"):
                counts[item] += int(st.count)
        for item in eaten:
            counts[item] -= 1
        self.slots = [{"id": i, "count": n} for i, n in counts.items() if n > 0]

    def tools(self, kind):
        return list(self._tools.get(kind, []))

    def count(self, item):
        return sum(s["count"] for s in self.slots if s["id"] == mid(item))


def _orders(group):
    """The orders of `group` worth planning: targets whose makings share nothing (no item, tool or station on the
    way) plan the same in any order (forward runs them as it routes), so only those that share are permuted."""
    seen: dict = {}

    def near(tok):                  # the item, every input on its ways, their stations and the tools they mine with
        if tok in seen:
            return seen[tok]
        out = seen[tok] = {tok, mid(tok)}
        for made, src in sources(tok):
            step, inputs, station, _adds = way(src, made, 1)
            if station:
                out |= near(station)
            if step.detail.get("tier") is not None:
                out |= near(tool_item("pickaxe", int(step.detail["tier"])))
            for t, _c in inputs:
                out |= near(t)
        return out

    sets = []
    for t in group:
        if any(n[0] in ("fact", "do") for n in t.needs):
            return itertools.permutations(group)        # a fact or an act: what it touches is not in the tables
        sets.append(set().union(*[near(tool_item(n[1], int(n[2])) if n[0] == "tool" else n[0]) for n in t.needs]))
    parts: list[list[int]] = []
    for i in range(len(group)):
        joined = [p for p in parts if any(sets[i] & sets[j] for j in p)]
        parts = [p for p in parts if p not in joined] + [sum(joined, []) + [i]]
    parts.sort(key=min)
    return (sum((list(o) for o in combo), []) for combo in
            itertools.product(*[itertools.permutations([group[i] for i in sorted(p)]) for p in parts]))


def _cheapest_order(inv, group, cost, pending, jobs, held=None, exact=False, cap=math.inf):
    """One level's steps in the order of its targets whose whole plan takes fewest seconds (forward's price:"""
    group = sorted(group, key=lambda t: t.rank)
    # the reference (exact) weighs every order: the partial-order cut and the cap are what it checks
    orders = itertools.permutations(group) if exact else _orders(group) if len(group) <= ORDER_MAX else [tuple(group)]
    best, dearer = None, None
    for order in orders:
        needs = [n for t in order for n in t.needs]
        try:
            bar = cap if best is None else min(cap, best[0][0] + 1)
            steps = plan_needs(inv, needs, cost, pending, jobs, held=held, exact=exact, cap=bar) if needs else []
        except Dearer as e:
            dearer = e
            continue
        key = (sum(s.est for s in steps), tuple(t.rank for t in order))
        if best is None or key < best[0]:
            best = (key, steps)
    if best is None:
        assert dearer is not None, "every order either planned or was dearer than the cap"
        raise dearer
    return best[1]


def _one_of(inv, targets, cost, pending, jobs, held, chosen, exact, settled):
    """The steps of the targets with every one-of target settled to its cheapest way (ways that cannot be had left o…"""
    choices = [t for t in targets if t.options]
    fixed = [t for t in targets if not t.options]
    best, why = None, []
    combos = list(itertools.product(*[t.options for t in choices]))
    if len(combos) > 1:              # the least each could cost first: one that cannot beat the best is never planned
        search = Search(cost)
        floor = {}
        for combo in combos:
            root = Node(from_bag(inv, pending, jobs, cost.reserved, cost.facts()), [], [])
            needs = [n for t in fixed for n in t.needs] + [n for opt in combo for n in opt[1]]
            root.stack = [("tool", n[1], int(n[2]), 1, 0) if n[0] == "tool" else ("need", n[0], int(n[1]), 0, False)
                          for n in reversed(needs) if n[0] not in ("fact", "do")]
            floor[id(combo)] = search.h(root) / TICKS_PER_S + sum(opt[2] for opt in combo)
        # what needs no search priced first: its seconds cap every search after it
        combos.sort(key=lambda c: (any(opt[1] for opt in c) or bool(fixed), floor[id(c)]))
    for combo in combos:
        if best is not None and not exact and round_spent():
            Search.cut()                    # the round's steps spent: the ways not yet weighed are not (budget_spent)
            break
        if best is not None and floor[id(combo)] >= best[0][0]:
            why.append(" + ".join(opt[0] for opt in combo) + ": dearer at the least than the way taken")
            continue
        picked = [Target(t.name, list(opt[1]), t.rank) for t, opt in zip(choices, combo)]
        try:
            cap = math.inf if best is None or exact else (best[0][0] - sum(opt[2] for opt in combo)) * TICKS_PER_S + 1
            steps = _cheapest_order(_After(inv, []), fixed + picked, cost, pending, jobs, held, exact, cap)
        except Unplannable as e:
            why.append(" + ".join(opt[0] for opt in combo) + f": {e}")
            continue
        key = (sum(s.est for s in steps) / TICKS_PER_S + sum(opt[2] for opt in combo), [o[0] for o in combo])
        if best is None or key < best[0]:
            best = (key, steps, {t.name: opt[0] for t, opt in zip(choices, combo)}, fixed + picked)
    if best is None:
        raise Unplannable("; ".join(why))
    if chosen is not None:
        chosen.update(best[2])
    settled[:] = best[3]            # the targets with each one-of settled to its way
    return best[1]


def plan_round(inv, targets, cost, pending=None, jobs=None, held=None, chosen=None,
               exact=False) -> tuple[Step | None, list, float]:
    """The round's one plan over every target (the queue's goals and upkeep's):"""
    jobs = dict(pending or {}) if jobs is None else jobs
    settled: list = []
    steps = _one_of(inv, targets, cost, pending, jobs, held, chosen, exact, settled)
    left = food_left_s(cost)
    if not fed_in_time(steps, left):
        fed = plan_needs(inv, [("food", 1)], cost, pending, jobs, exact=exact)
        shared = set().union(*map(_ids_used, fed)) & set().union(*map(_ids_used, steps), *map(_ids_made, steps))
        if exact or shared:
            # the rest from what the meal leaves: the ways settled, the plan just made its first bar
            meal = [mid(st.token) for st in fed if mid(st.token) in FOOD_IDS and st.kind in MAKES_FOOD][:1]
            steps = fed + _cheapest_order(_After(inv, fed, eaten=meal), settled, cost, pending, jobs, steps, exact)
        else:
            steps = fed + steps         # the meal takes nothing the plan takes or makes: the plan stands after it
        if not fed_in_time(steps, left):
            raise Unplannable(f"the bar runs out in {left:.0f} s before any food the plan can make")
    tools = list(from_bag(inv, pending, jobs, cost.reserved, cost.facts()).tools)
    steps = [Step(s.kind, s.token, s.count, dict(s.detail)) for s in steps]
    for st, est in zip(steps, price_as_run(steps, tools, cost)):
        st.est = est                 # priced as the whole round runs: the meal planned apart (D6)
    return (steps[0] if steps else None), steps, sum(s.est for s in steps) / TICKS_PER_S


def p_unknown(k, n):
    """Pure:"""
    return (k + 1) / (n + 2)


def look_first(inv, needs, cost, pending=None):
    """[the look into an unopened home container] when its expected seconds beat making what is short — the look, th…"""
    mem, snap = cost.mem, cost.snap
    if mem is None:
        return []
    unopened = [c for c in mem.home_containers(snap.dimension) if mem.container_record(c) is None]
    if not unopened:
        return []
    opened = [c for c in mem.home_containers(snap.dimension) if mem.container_record(c) is not None]
    for need in needs:
        if need[0] in ("tool", "fact", "do"):
            continue
        token, n = need[0], int(need[1])
        held = {token: sum(have for _p, _i, have, _pr in cost.stored(token))}
        short = have_remainder(inv, [[token, n]], {**(pending or {}), **held}).get(token, 0)
        if short <= 0:
            continue
        try:
            make = sum(s.est for s in plan_needs(inv, [(token, short)], cost, pending))
        except Unplannable:
            make = math.inf
        ids = set(members(token))
        p = p_unknown(sum(1 for c in opened if any(mem.container_record(c)["items"].get(i, 0) > 0 for i in ids)),
                      len(opened))
        best = None
        for c in unopened:
            look = Step("look", "container", 1, {"pos": list(c)})
            look.est = price_as_run([look], [], cost)[0]
            take = Step("withdraw", mid(token), short, {"pos": list(c)})
            expected = look.est + p * cost.estimate(take, at=tuple(c)) + (1 - p) * make
            if make - expected > 0 and (best is None or make - expected > best[0]):
                look.detail.update(p=p, expected=round(expected), need=[token, short])     # what the look saves on average
                best = (make - expected, look)
        if best is not None:
            return [best[1]]
    return []


def craftable_tier(inv, kind, reserved=()):
    """The best tier of `kind` this bag crafts with crafting steps only (`reserved`, what the held plans will consum…"""
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
    """Offline cost model: the prior work (knowledge.prior_work_ticks), no body, no memory, nothing known about places."""

    def __init__(self, reserved=()):
        self.reserved = frozenset(reserved)
        self.cache = {}
        self.snap = self.mem = self.stop = None

    def plans(self):
        return self.cache

    def hunger_rate(self):
        return 0.0

    def stored(self, token):
        return []

    def ripe(self, token):
        return 0

    def site(self, step):
        return None

    def feet(self):
        return None

    def places(self):
        return frozenset()

    def walk_ticks(self, distance):
        return 0

    def fight_line(self, step, held=None):
        return True, None

    def line_kit(self, step, held=None):
        return []

    def station_near(self, block):
        return False

    def facts(self):
        return {"dimension": OVERWORLD, **body_facts(None)}

    def work(self, step, held=None, table_back=True):
        return prior_work_ticks(step, held or {}, TICKS_PER_S)

    def estimate(self, step, held=None, at=None, table_back=True):
        return self.work(step, held) + self.dig_to(step, held)

    def table_back(self, step):
        return False

    def dig_to(self, step, held=None):
        """Ticks the digging to it takes (its work_of beyond its own work), with `held`."""
        return dig_to_ticks(self.work_of(step)[0], step, held or {}, TICKS_PER_S)

    def dig_lb(self, step, held=None):
        return self.dig_to(step, held)

    def walk_lb(self, step, held):
        return 0

    def work_of(self, step):
        """A step's own work (knowledge.own_work): offline, nothing in sight to dig to."""
        return own_work(step)
