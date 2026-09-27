"""Requirement resolution: turns needs like [('minecraft:bucket', 1), ('tool', 'pickaxe', 2)] into an ordered,
merged, cost-estimated list of steps, simulating the inventory as it goes. Pure logic: all world knowledge comes
through a cost model, so it can be tested offline."""
import math
from collections import Counter
from dataclasses import dataclass, field

from .api import McError
from .data import GROUPS, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER, bare, mid
from .beliefs import fights_back
from .knowledge import (COOKABLE_FOOD, HUNT_YIELD, MINE_YIELD, TAKEABLE, TOOL_MIN_DURABILITY,
                        have_remainder, members, needs_rows, source, step_call)

MAX_DEPTH = 14


class Unplannable(McError):
    pass


@dataclass
class Step:
    kind: str            # craft | smelt | mine | gather | hunt
    token: str           # item id or group token produced
    count: int           # units of `token` to end up with (craft: output items)
    detail: dict = field(default_factory=dict)
    est: int = 0         # estimated ticks

    def key(self):
        return self.kind, self.token

    def __str__(self):
        return f"{self.kind} {self.count}× {bare(self.token)} (~{self.est // 20}s)"


class VirtualInventory:
    """Counts what we'd hold after the planned steps run."""

    def __init__(self, counts, tools):
        self.counts = Counter(counts)       # item id -> count
        self.produced = Counter()           # group token -> count produced by planned steps
        self.tools = list(tools)            # [(kind, tier, durability)]

    def available(self, token):
        return sum(self.counts[m] for m in members(token)) + self.produced[token]

    def consume(self, token, n):
        take = min(n, self.produced[token])
        self.produced[token] -= take
        n -= take
        for m in members(token):
            if n <= 0:
                break
            take = min(n, self.counts[m])
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


class Planner:
    def __init__(self, counts, tools, cost):
        self.inv = VirtualInventory(counts, tools)
        self.cost = cost
        self.probing = False       # a craftable_tier probe: plans the tier asked, never upgrades it again
        self.steps = []

    @classmethod
    def from_inventory(cls, inv, cost, extra=None):
        """`extra`: items on their way (e.g. smelting in a machine) that count as held but aren't usable yet —
        steps consuming them stay unrunnable until they arrive, so nothing is mined twice."""
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
        return cls(counts, tools, cost)

    # ---- public
    def plan(self, needs):
        for need in needs:
            if need[0] == "tool":
                self.need_tool(need[1], need[2])
            else:
                self.need(need[0], need[1])
        return self.merged()

    # ---- resolution
    def need_tool(self, kind, tier, depth=0):
        if self.inv.has_tool(kind, tier, TOOL_MIN_DURABILITY):
            return
        # The tool made is the best the bag makes outright (`craftable_tier`), never below what the step needs: a
        # worn-out iron pickaxe with three ingots carried was replaced with a wooden one, then a stone one.
        if not self.probing:
            tier = max(tier, self.craftable_tier(kind))
        material = TOOL_MATERIAL_FOR_TIER[tier]
        self.need(f"minecraft:{material}_{kind}", 1, depth + 1, fresh=True)
        self.inv.tools.append((kind, tier, 999))

    def craftable_tier(self, kind):
        """The best tier of `kind` this (planned) bag crafts outright — crafting steps only, nothing to gather, mine
        or smelt — or 0. The one answer for every tool goal (the planner's own needs and upkeep's replacement)."""
        for tier in sorted((t for t in TOOL_MATERIAL_FOR_TIER if t > 0), reverse=True):
            probe = Planner(self.inv.counts, [], NullCost())
            probe.inv.produced = Counter(self.inv.produced)
            probe.probing = True
            try:
                steps = probe.plan([("tool", kind, tier)])
            except Unplannable:
                continue
            if all(s.kind == "craft" for s in steps):
                return tier
        return 0

    def before(self, step, depth):
        """Every step passes here before it is added: what the skill carrying it out needs held (knowledge.step_call
        — its `needs` for this call: tools first, then items, planned and kept, never used up by it), then the tools
        it runs faster with where making one pays (`speed_up`). The one place a step's skill is asked."""
        needs, speed = step_call(step)
        for dim, n in sorted(needs.items(), key=lambda kv: not kv[0].startswith("tool:")):
            if dim.startswith("tool:"):
                _, kind, tier = dim.split(":")
                self.need_tool(kind, int(tier), depth)
            elif self.inv.available(dim) < n:
                short = n - self.inv.available(dim)
                self.need(dim, short, depth + 1)
                self.inv.add(dim, short)
        units = step.detail.get("breaks") or step.detail.get("kills") or step.count
        self.speed_up(step.kind, speed, units, depth)

    def speed_up(self, kind, speed, units, depth):
        """Before `units` of work of step `kind`: the optional tools its skill runs faster with here (`speed`: its
        `@skill(speed=...)`, seconds saved per unit — a shovel only on soft ground, step_call), made when making one
        costs less than it saves — and only from what does not need this same work (an axe that needs the logs it
        would speed up is made after them, too late)."""
        if self.probing:
            return
        for tool, saved in speed.items():
            if self.inv.has_tool(tool, 0, TOOL_MIN_DURABILITY):
                continue
            probe = Planner(self.inv.counts, [], self.cost)
            probe.inv.produced = Counter(self.inv.produced)
            probe.probing = True
            try:
                steps = probe.plan([("tool", tool, 0)])
            except Unplannable:
                continue
            if any(s.kind == kind for s in steps):
                continue
            if saved * units > sum(s.est for s in steps) / 20.0:
                self.need_tool(tool, 0, depth)

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
        have = 0 if fresh else self.inv.available(token)
        if have >= n:
            self.inv.consume(token, n)
            return
        missing = n - have
        if not fresh:
            self.inv.consume(token, have)
        if token == "food":
            # Raw meat already carried first: smelting it is seconds, hunting is minutes — a hungry agent with two
            # raw beef and a lit furnace planned "hunt 8× porkchop" instead.
            for cooked, k in cooked_from_carried(COOKABLE_FOOD, self.inv.available, missing):
                self.need(cooked, k, depth + 1)
                missing -= k
            if missing <= 0:
                return
            token = self.cost.cheapest_food(COOKABLE_FOOD)
        src = source(token)
        if src is None:
            raise Unplannable(f"no known way to obtain {token}")
        kind = src[0]
        if kind == "craft":
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
        elif kind == "smelt":
            _, inp = src
            self.need(inp, missing, depth + 1)
            fuel = "coal" if self.inv.available("coal") * 8 >= missing or not self.inv.available("planks") else "planks"
            self.need(fuel, math.ceil(missing / 8) if fuel == "coal" else math.ceil(missing / 1.5), depth + 1)
            self.need_station("minecraft:furnace", depth)
            fuel_n = math.ceil(missing / 8) if fuel == "coal" else math.ceil(missing / 1.5)
            step = Step("smelt", token, missing, {"input": inp, "fuel": fuel, "inputs": {inp: missing, fuel: fuel_n}})
            self.before(step, depth)
            self.add_step(step)
        elif kind == "mine":
            _, blocks, tier = src
            per = MINE_YIELD.get(mid(token), 1)
            step = Step("mine", token, missing, {"blocks": blocks, "tier": tier, "breaks": math.ceil(missing / per)})
            self.before(step, depth)               # the pickaxe of this tier: mine's own needs
            self.add_step(step)
        elif kind == "gather":
            step = Step("gather", token, missing, {})
            self.before(step, depth)
            self.add_step(step)
        elif kind == "fill":
            _, container = src
            step = Step("fill", token, missing, {"container": container})
            self.before(step, depth)
            self.need(container, 1, depth + 1)     # the empty bucket first (used up: it becomes the full one)
            self.add_step(step)
        elif kind == "hunt":
            _, types = src
            per = HUNT_YIELD.get(token, HUNT_YIELD.get(mid(token), 1))
            step = Step("hunt", token, missing, {"types": types, "kills": math.ceil(missing / per),
                                                 "fighter": hunts_a_fighter(types)})
            self.before(step, depth)               # a mob that hits back: a sword first (hunt's own needs)
            self.add_step(step)
        elif kind == "farm" and getattr(self.cost, "ripe", lambda t: 0)(token) * TAKEABLE[token]["gives"][token] \
                >= missing:
            # A crop already grown is harvested before a plot is sown (the ore rule: what is known first).
            step = Step("take", token, missing, {"blocks": list(TAKEABLE[token]["blocks"])})
            self.before(step, depth)
            self.add_step(step)
        elif kind == "farm":
            # A plot (farming.plant_farm): a hoe, the seeds sown (given back at the harvest) and a water bucket that
            # stays in it; one plot is `per` of the crop.
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
        elif kind == "trade":
            # Sold to a villager for what it buys (the offer decides): nothing carried to plan, a villager to find.
            _, types = src
            step = Step("trade", token, missing, {"types": types})
            self.before(step, depth)               # what the villager is paid in: trade's own needs
            self.add_step(step)
        else:
            raise Unplannable(f"{token}: its only source ({kind}) is not one the planner plans")

    def add_step(self, step):
        step.est = self.cost.estimate(step)
        self.steps.append(step)

    MERGEABLE_CRAFTS = {"planks", "minecraft:stick", "minecraft:torch", "minecraft:ladder"}

    def merged(self):
        """Same-kind steps for the same token merge into the earliest one: gather, mine, smelt and craft
        intermediates once for everything. Merging into the earliest position keeps dependencies valid because the
        steps that feed it (gather/mine) are merged into their own earliest positions, which come before."""
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
    """Can this step start from the bag now? What the skill carrying it out needs held (knowledge.step_call — the
    same needs the plan was made to satisfy: the pickaxe of a mine, the sword of a fight, the bucket of a fill, the
    emerald of a trade), and a step with `inputs` (craft, smelt, farm) those on hand."""
    needs, _speed = step_call(step)
    if have_remainder(inv, needs_rows(needs)):
        return False
    return all(inv.count(tok) >= n for tok, n in step.detail.get("inputs", {}).items())


class NullCost:
    """Offline cost model for tests."""

    def station_near(self, block):
        return False

    def cheapest_food(self, options):
        return options[0]

    def estimate(self, step):
        return {"craft": 60, "smelt": 200 * step.count, "mine": 80 * step.count, "gather": 60 * step.count,
                "hunt": 400 * step.count, "fill": 100, "farm": 6000, "trade": 600, "take": 100}[step.kind]
