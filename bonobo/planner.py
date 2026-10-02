"""Requirement resolution: turns needs like [('minecraft:bucket', 1), ('tool', 'pickaxe', 2)] into an ordered, merged, cost-estimated list of steps, simulating the inventory as it goes. Pure logic: all world knowledge comes through a cost model, so it can be tested offline."""

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .api import McError
from .data import GROUPS, MATERIAL_TOKEN, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER, bare, mid
from .beliefs import CONFIG, TICKS_PER_S, fights_back
from .knowledge import (prior_ticks, COOKABLE_FOOD, HUNT_YIELD, MINE_YIELD, TAKEABLE, TOOL_MIN_DURABILITY,
                        have_remainder, members, needs_rows, own_work, source, step_call, tool_kind, work_s)

MAX_DEPTH = 14

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
        return f"{self.kind} {self.count}× {bare(self.token)} (~{self.est // 20}s)"

class VirtualInventory:
    """Counts what we'd hold after the planned steps run."""

    def __init__(self, counts, tools, pending=None):
        self.counts = Counter(counts)       # item id -> count (held, and `pending` on its way)
        self.produced = Counter()           # group token -> count produced by planned steps
        self.tools = list(tools)            # [(kind, tier, durability)]
        self.pending = Counter(pending or {})   # item id -> of `counts`, what a job is still making (not in the bag)
        self.awaited = Counter()            # item id -> pending the plan uses: to be collected before it is used

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

    def has_tool(self, kind, tier, min_left):
        return any(k == kind and t >= tier and d >= min_left for k, t, d in self.tools)

def cooked_from_carried(options, available, n):
    """Pure: [(cooked item, how many)] made from raw meat already carried, most carried first, up to `n` in all."""
    raw = sorted(((c, available(c.replace("cooked_", ""))) for c in options), key=lambda x: -x[1])
    out = []
    for cooked, have in raw:
        k = min(have, n - sum(q for _c, q in out))
        if k > 0:
            out.append((cooked, k))
    return out

def material_per_tool(kind, tier):
    """Pure: how much of its tier's material one `kind` of `tier` takes (its recipe)."""
    material = TOOL_MATERIAL_FOR_TIER[tier]
    src = source(f"minecraft:{material}_{kind}")
    return src[1].count(MATERIAL_TOKEN[material]) if src else 0

def used_before(kind):
    """The tool kinds used more often than `kind` (play.toml tools.use_order's earlier groups)."""
    order = CONFIG["tools"]["use_order"]
    group = next(i for i, g in enumerate(order) if kind in g)
    return [k for g in order[:group] for k in g]

class Planner:
    def __init__(self, counts, tools, cost, pending=None, reserved=None):
        self.inv = VirtualInventory(counts, tools, pending)
        self.cost = cost
        # item ids the held plans will consume (bag.RESERVED): never a better tool's material
        self.reserved = frozenset(reserved if reserved is not None else getattr(cost, "reserved", ()))
        self.probing = False       # a craftable_tier probe: plans the tier asked, never upgrades it again
        self.steps = []

    @classmethod
    def from_inventory(cls, inv, cost, extra=None, pending=None, reserved=None):
        """`extra`: items counted as held beyond the bag (a planned source's output, a running job's output), so
        nothing is made twice. `pending`: of `extra`, what a running job is still making (a sown crop, a furnace) — a
        step that takes from it awaits it first; a planned source's output (dirt the plan will dig) never does."""

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
        tools = [(kind, t, d) for kind in TOOL_KINDS for t, d, _ in inv.tools(kind)] if hasattr(inv, "tools") else []
        return cls(counts, tools, cost, {mid(k): v for k, v in (pending or {}).items()}, reserved)

    # -- public
    def plan(self, needs):
        for need in needs:
            if need[0] == "tool":
                self.need_tool(need[1], need[2])
            else:
                self.need(need[0], need[1])
        return self.merged()

    def await_first(self):
        """What a step is about to take from a job still running (a sown crop, a furnace) is collected right before
        that step — after every step planned so far, which need nothing of it (never idle while it is on its way).
        Counted as held and used at once, bread was crafted from sown wheat ("missing 3× wheat for crafting")."""
        for item, n in sorted(self.inv.awaited.items()):
            if n > 0:
                self.steps.append(Step("await", item, n, est=self.cost.estimate(Step("await", item, n))))
        self.inv.awaited.clear()

    # -- resolution
    def need_tool(self, kind, tier, depth=0):
        if self.inv.has_tool(kind, tier, TOOL_MIN_DURABILITY):
            return
        # the best tier the bag makes outright, never below the need (a worn iron pickaxe was replaced with a wooden one)
        if not self.probing:
            tier = max(tier, self.craftable_tier(kind))
        material = TOOL_MATERIAL_FOR_TIER[tier]
        self.need(f"minecraft:{material}_{kind}", 1, depth + 1, fresh=True)
        self.inv.tools.append((kind, tier, 999))

    def craftable_tier(self, kind):
        """The best tier of `kind` this planned bag crafts with crafting steps only, or 0 — from what is left of the
        tier's material once the held plans' reservation and every more-used tool still short of that tier took
        theirs (play.toml tools.use_order)."""

        for tier in sorted((t for t in TOOL_MATERIAL_FOR_TIER if t > 0), reverse=True):
            probe = Planner(self.inv.counts, [], NullCost())
            probe.inv.produced = Counter(self.inv.produced)
            probe.probing = True
            token = MATERIAL_TOKEN[TOOL_MATERIAL_FOR_TIER[tier]]
            held = probe.inv.available(token)
            taken = held if set(members(token)) & self.reserved else sum(
                material_per_tool(k, tier) for k in used_before(kind)
                if not self.inv.has_tool(k, tier, TOOL_MIN_DURABILITY))
            probe.inv.consume(token, min(held, taken), awaits=False)
            try:
                steps = probe.plan([("tool", kind, tier)])
            except Unplannable:
                continue
            if all(s.kind == "craft" for s in steps):
                return tier
        return 0

    def before(self, step, depth):
        """Before a step is added: plan its skill's needs for the call (tools first, kept not used up), then the tools
        its work pays for."""

        needs = step_call(step)
        for dim, n in sorted(needs.items(), key=lambda kv: not kv[0].startswith("tool:")):
            if dim.startswith("tool:"):
                _, kind, tier = dim.split(":")
                self.need_tool(kind, int(tier), depth)
            elif self.inv.available(dim) < n:
                short = n - self.inv.available(dim)
                self.need(dim, short, depth + 1)
                self.inv.add(dim, short)
        self.speed_up(step, depth)

    def held(self):
        """{tool kind: the best tier planned or held, with wear left}."""
        out = {}
        for k, t, d in self.inv.tools:
            if d >= TOOL_MIN_DURABILITY and t in TOOL_MATERIAL_FOR_TIER:
                out[k] = max(out.get(k, t), t)
        return out

    def speed_up(self, step, depth):
        """Before a step's work (cost.work_of): the next tier of each tool kind it uses, made when what it saves on
        that work (knowledge.work_s) beats making it (the probe plan's est) — never from the same work (an axe needing
        the logs)."""

        if self.probing:
            return
        breaks, kills = self.cost.work_of(step)
        kinds = {k for k in map(tool_kind, breaks) if k is not None} | ({"sword"} if kills else set())
        held = self.held()
        for kind in sorted(kinds):
            tier = held.get(kind, -1) + 1
            if tier not in TOOL_MATERIAL_FOR_TIER:
                continue
            saved = work_s(breaks, kills, held, TICKS_PER_S) - work_s(breaks, kills, {**held, kind: tier}, TICKS_PER_S)
            if saved <= 0:
                continue
            probe = Planner(self.inv.counts, [], self.cost)
            probe.inv.produced = Counter(self.inv.produced)
            probe.probing = True
            try:
                steps = probe.plan([("tool", kind, tier)])
            except Unplannable:
                continue
            if any(s.kind == step.kind for s in steps):
                continue
            if saved > sum(s.est for s in steps) / TICKS_PER_S:
                self.need_tool(kind, tier, depth)

    def need_station(self, block, depth):
        """Stations are required, never consumed: once planned or held, every later step reuses them."""
        if self.inv.available(block) > 0 or self.cost.station_near(block):
            return
        self.need(block, 1, depth + 1)
        self.inv.add(block, 1)

    def need(self, token, n, depth=0, fresh=False):
        """Ensure `n` of token will be held. `fresh` ignores current stock (a worn tool doesn't satisfy a new one)."""
        if depth > MAX_DEPTH:
            raise Unplannable(f"requirement chain too deep at {token}")
        missing = self._from_stock(token, n, depth, fresh)
        if missing <= 0:
            return
        if token == "food":
            missing, token = self._food(missing, depth)
            if missing <= 0:
                return
        src = source(token)
        if src is None:
            raise Unplannable(f"no known way to obtain {token}")
        way = {"craft": self._craft, "smelt": self._smelt, "mine": self._mine, "gather": self._gather,
               "fill": self._fill, "hunt": self._hunt, "farm": self._farm, "trade": self._trade}.get(src[0])
        if way is None:
            raise Unplannable(f"{token}: its only source ({src[0]}) is not one the planner plans")
        way(src, token, missing, depth)

    def _from_stock(self, token, n, depth, fresh):
        """What is still missing of `n` after the stock is used (0 when it covers all)."""
        have = 0 if fresh else self.inv.available(token)
        if have >= n:
            self.inv.consume(token, n, awaits=depth > 0)
            self.await_first()
            return 0
        missing = n - have
        if not fresh:
            self.inv.consume(token, have, awaits=depth > 0)
            self.await_first()
        return missing

    def _food(self, missing, depth):
        """(still missing, the food to get): raw meat carried first — smelting is seconds, hunting minutes."""
        for cooked, k in cooked_from_carried(COOKABLE_FOOD, self.inv.available, missing):
            self.need(cooked, k, depth + 1)
            missing -= k
        if missing <= 0:
            return missing, "food"
        return missing, self.cheapest_food(missing, depth)

    FOODS = tuple(COOKABLE_FOOD) + ("minecraft:bread",)     # what a food need is made as: meat cooked, or bread

    def cheapest_food(self, missing, depth):
        """The food `missing` is made as, by price (M3/B2): each of FOODS planned from here on a probe of this plan,
        the one whose steps cost least (Σ est by the cost model); Unplannable, with each food's reason, when none can
        be planned."""
        import copy
        priced, why = [], []
        for food in self.FOODS:
            probe = copy.copy(self)
            probe.inv, probe.steps = copy.deepcopy(self.inv), []
            try:
                probe.need(food, missing, depth + 1)
            except Unplannable as e:
                why.append(f"{bare(food)}: {e}")
            else:
                priced.append((sum(st.est for st in probe.steps), food))
        if not priced:
            raise Unplannable("no food can be planned (" + "; ".join(why) + ")")
        return min(priced)[1]

    def _craft(self, src, token, missing, depth):
        _, pattern, out = src
        times = math.ceil(missing / out)
        for tok, per in Counter(p for p in pattern if p).items():
            self.need(tok, per * times, depth + 1)
        if len(pattern) == 9:
            self.need_station("minecraft:crafting_table", depth)
        inputs = dict(Counter(p for p in pattern if p))
        step = Step("craft", token, times * out, {"times": times, "inputs": {t: c * times for t, c in inputs.items()}})
        self.before(step, depth)
        self.add_step(step)
        self.inv.add(token, times * out)
        self.inv.consume(token, missing)

    def _smelt(self, src, token, missing, depth):
        _, inp = src
        self.need(inp, missing, depth + 1)
        fuel = "coal" if self.inv.available("coal") * 8 >= missing or not self.inv.available("planks") else "planks"
        self.need(fuel, math.ceil(missing / 8) if fuel == "coal" else math.ceil(missing / 1.5), depth + 1)
        self.need_station("minecraft:furnace", depth)
        fuel_n = math.ceil(missing / 8) if fuel == "coal" else math.ceil(missing / 1.5)
        step = Step("smelt", token, missing, {"input": inp, "fuel": fuel, "inputs": {inp: missing, fuel: fuel_n}})
        self.before(step, depth)
        self.add_step(step)

    def _mine(self, src, token, missing, depth):
        _, blocks, tier = src
        per = MINE_YIELD.get(mid(token), 1)
        step = Step("mine", token, missing, {"blocks": blocks, "tier": tier, "breaks": math.ceil(missing / per)})
        self.before(step, depth)               # the pickaxe of this tier: mine's own needs
        self.add_step(step)

    def _gather(self, src, token, missing, depth):
        step = Step("gather", token, missing, {})
        self.before(step, depth)
        self.add_step(step)

    def _fill(self, src, token, missing, depth):
        _, container = src
        step = Step("fill", token, missing, {"container": container})
        self.before(step, depth)
        self.need(container, 1, depth + 1)     # the empty bucket first (used up: it becomes the full one)
        self.add_step(step)

    def _hunt(self, src, token, missing, depth):
        _, types = src
        per = HUNT_YIELD.get(token, HUNT_YIELD.get(mid(token), 1))
        step = Step("hunt", token, missing, {"types": types, "kills": math.ceil(missing / per),
                                             "fighter": hunts_a_fighter(types)})
        self.before(step, depth)               # a mob that hits back: a sword first (hunt's own needs)
        self.add_step(step)

    def _farm(self, src, token, missing, depth):
        if getattr(self.cost, "ripe", lambda t: 0)(token) * TAKEABLE[token]["gives"][token] >= missing:
            # a crop already grown is harvested before a plot is sown (what is known first)
            step = Step("take", token, missing, {"blocks": list(TAKEABLE[token]["blocks"])})
            self.before(step, depth)
            self.add_step(step)
            return
        # a plot: a hoe, `per` seeds (given back at the harvest) and a water bucket that stays; one plot yields `per`
        _, seeds, per = src
        plots = math.ceil(missing / per)
        step = Step("farm", token, plots * per, {"plots": plots, "inputs": {"minecraft:water_bucket": plots}})
        self.before(step, depth)               # the hoe, and a start of seeds and water: plant_farm's own needs
        self.need(seeds, per, depth + 1)
        self.need("minecraft:water_bucket", plots, depth + 1)
        self.add_step(step)
        self.inv.add(seeds, per)
        self.inv.add(token, plots * per)
        self.inv.consume(token, missing)

    def _trade(self, src, token, missing, depth):
        # sold to a villager for what it buys: a villager to find
        _, types = src
        step = Step("trade", token, missing, {"types": types})
        self.before(step, depth)               # what the villager is paid in: trade's own needs
        self.add_step(step)

    def add_step(self, step):
        step.est = self.cost.estimate(step)
        self.steps.append(step)

    MERGEABLE_CRAFTS = {"planks", "minecraft:stick", "minecraft:torch", "minecraft:ladder"}

    def merged(self):
        """Same-kind steps for one token merge into the earliest: intermediates made once for everything."""

        out, index = [], {}
        for s in self.steps:
            k = s.key()
            mergeable = s.kind in ("mine", "gather", "hunt", "smelt") or (
                s.kind == "craft" and s.token in self.MERGEABLE_CRAFTS)
            if k in index and mergeable:
                first = out[index[k]]
                first.count += s.count
                for key in ("breaks", "kills", "times"):
                    if key in s.detail:
                        first.detail[key] += s.detail[key]
                for tok, c in s.detail.get("inputs", {}).items():
                    first.detail.setdefault("inputs", {})[tok] = first.detail["inputs"].get(tok, 0) + c
                first.est = self.cost.estimate(first)
            else:
                index[k] = len(out)
                out.append(s)
        return out

def hunts_a_fighter(types):
    """Does this hunt target something that fights back (beliefs.fights_back)? Animals do not."""
    return fights_back(types)

def runnable(step, inv):
    """Can this step start from the bag now?"""

    needs = step_call(step)
    if have_remainder(inv, needs_rows(needs)):
        return False
    return all(inv.count(tok) >= n for tok, n in step.detail.get("inputs", {}).items())

class NullCost:
    """Offline cost model for tests."""

    def station_near(self, block):
        return False

    def estimate(self, step):
        """The same prior the real cost model starts from (knowledge.prior_ticks): one table, not a second guess."""
        return prior_ticks(step)

    def work_of(self, step):
        """A step's own work (knowledge.own_work): offline, nothing in sight to dig to."""
        return own_work(step)
