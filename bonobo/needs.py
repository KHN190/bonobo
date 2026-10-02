"""Needs: what must be PLANNED to be had — a bed or the night's parts before dark, food stock before it runs out, a tool that broke under a held plan, a water bucket before a fall, blocks where the path is blocked, the night's ore underground. Each is PROPOSED (`need` → `needs_now`), never queued: brain.need_act turns it into the next step of its plan and arbiter.PLAN_ORDER ranks it with the queue. The fixed maintenance reflexes (eat, land, the night's shelter, the bag…) are reflexes.py. Also the tool-repair skill and the one choice of how to get through a night (`overnight`). Pure `repair_pair`, `dusk_s`, `due_now`, `overnight` are offline-tested."""

import json
import math
import time
from typing import TYPE_CHECKING

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, decompose, goals, survive, beliefs
from .reflexes import BAG_FULL, BRIDGE_MIN, EAT_BELOW, _once, ground, nether_retreat  # noqa: F401  (shared thresholds)
from .api import McError, NotAvailable, log
from . import bag
from .bag import bag_signature
from .cost import Cost
from .decompose import cooled_ways, night_facts, night_left_s, way_key  # noqa: F401
if TYPE_CHECKING:
    from .shapes import BagState, CraftTask
from .data import DAY_END, NIGHT_WORK, TOOL_KINDS, memo_ttl, mid, FOOD, NUTRITION, DAY_TICKS, TICKS_PER_S, REPAIR_BONUS_PARTS
from .knowledge import food_count, food_points, FIND_AT
from .planner import Unplannable, craftable_tier
from .skill import skill
from .skillcore import lost
from .world import Inventory

LEAD = 1.5                 # how much earlier than a plan's own seconds its upkeep starts: the one margin
PLAN_S_TTL = 20            # seconds a "how long would that take" answer is kept
WORKING = _k.TOOL_WORKING  # durability left for a tool to count as working (planning's margin; the jar holds from 2)
NEAR_BREAK = 16            # durability a tool had last round for its disappearing to mean it broke (a round of work)
BRIDGE_STOCK = 32          # the most to fetch when the path is blocked and there is less than BRIDGE_MIN

def bridge_stock(feet, target):
    """Pure: building blocks for a blocked path — one per block across, between BRIDGE_MIN and BRIDGE_STOCK."""

    across = math.ceil(math.dist((feet[0], feet[2]), (target[0], target[2])))
    return max(BRIDGE_MIN, min(BRIDGE_STOCK, across))
# steps where a fall can happen: the jar's WaterClutch needs a water bucket, so such a plan gets one first; token None = any
FALL_RISK = {("portal", None), ("seek", "fortress"), ("seek", "stronghold"), ("seek", "portal_room"),
             ("activate", "end_portal"), ("hunt", "minecraft:blaze_rod")}
DEEP_Y = 40                # a mine step whose ore is richest below this is reached by digging down

def dusk_s(snap):
    """Seconds until dusk (data.DAY_END, the one dusk: LEAD is the only margin): (DAY_END − timeOfDay) / TICKS_PER_S, 0 once
    it is dark."""
    t = int(snap.time) % DAY_TICKS
    return max(0.0, (DAY_END - t) / TICKS_PER_S) if t < DAY_END else 0.0

def cover(ctx, state):
    """The cheapest shelter that can run here now — every skill providing a "shelter:" way, priced by the one cost
    model — run: a hazard's last way (hazard.RECOVERY) when its own way out is spent."""
    from .planner import Step
    from .world import Snapshot
    from . import skill as skillkit
    # priced as the brain prices (brain.py's Cost: memory, the targets banned here, the movement policy): a banned
    # entity or block is no shelter's way (W3)
    cost = Cost(Snapshot.from_readings(state, Inventory()), getattr(ctx, "mem", None), getattr(ctx, "blacklist", None),
                policy=getattr(ctx, "policy", None))
    ways = []
    for c in skillkit.REGISTRY.values():
        for effect in c.provides:
            if effect.startswith("shelter:"):
                step = Step("shelter", effect.split(":", 1)[1], 1)
                args = c.provides[effect](ctx, step)
                if args is not None and skillkit.can_run(c.runner, ctx, *args)[0]:
                    ways.append((cost.estimate(step), c.runner, tuple(args)))
    if not ways:
        raise NotAvailable("no shelter can be made here")
    _s, runner, args = min(ways, key=lambda w: w[0])
    runner(ctx, *args)

def overnight(inv, cost, facts=None, bed_too=True):
    """How to get through a night, by price: (choice, seconds, steps); (None, inf, []) when there is none. The bed
    ways (a carried bed — the sleep row's: a room, light, the gate, taken back — or the home's bed a walk reaches,
    its open walk priced with the night's risk) against making a bed or a shelter waited in (the night ahead and a
    sheltered night's risk: night_facts): the cheapest. A shelter is never paired with a sleep."""

    bed = []

    def carried():
        if not (bed_too and inv.count("bed") > 0):
            raise Unplannable("no bed carried")
        return []

    def bed_plan():
        if not bed_too:
            raise Unplannable("a bed is the sleep row's")
        bed[:] = decompose.decompose(inv, goals.have(("bed", 1)), cost)
        return bed

    best = None
    for key, default in (("overnight bed", carried), ("overnight", bed_plan)):
        try:
            steps, way, seconds = decompose.cheapest(key, 1, default, inv, cost, facts=facts, priced=True)
        except Unplannable as e:
            log(f"upkeep: no {key} way ({e})")
            continue
        if steps is None:
            steps, way = (bed, "bed") if key == "overnight" else ([], "bed")
        if best is None or seconds < best[0]:
            best = (seconds, way, steps)
    if best is None:
        return None, math.inf, []
    return best[1], cost.plan_s(best[2]), best[2]

def due_now(left_s, plan_s, known, at_threshold):
    """Pure: is it time to start getting something?"""

    return left_s < plan_s * LEAD if known else bool(at_threshold)

def tool_kinds(steps):
    """Pure: the tool kinds these plan steps need (a mine step with a tier needs a pickaxe)."""
    return {"pickaxe"} if any(st.kind == "mine" and st.detail.get("tier") is not None for st in steps) else set()

def food_on_its_way(pending):
    """Pure: (meals, hunger points) of the ready food a background job or machine is making ({item id: count})."""
    ready = {mid(f): NUTRITION[f] for f in FOOD}
    meals = sum(n for item, n in pending.items() if item in ready)
    return meals, sum(n * ready[item] for item, n in pending.items() if item in ready)

def food_lasts_s(snap, pending=None):
    """Seconds of work the stomach, the bag's meals and those cooking cover."""

    drain = float(beliefs.value("risk.food_drain_s"))
    return (float(snap.get("food", 20)) + food_points(snap.inv) + food_on_its_way(pending or {})[1]) * drain

def working_tiers(inv):
    """{tool kind: best tier with a working one} for TOOL_KINDS. Pure over the bag."""
    out = {}
    for kind in TOOL_KINDS:
        tiers = [t for t, d, _ in inv.tools(kind) if d >= WORKING]
        if tiers:
            out[kind] = max(tiers)
    return out

def falls(step, known_y=None):
    """Pure: does this plan step put the body where a fall can happen (FALL_RISK, or ore dug down to)? The ore's
    depth is where it is known to lie (`known_y`), its band only when none is known."""
    if (step.kind, None) in FALL_RISK or (step.kind, step.token) in FALL_RISK:
        return True
    depth = known_y if known_y is not None else FIND_AT.get(step.token)
    return step.kind == "mine" and depth is not None and depth < DEEP_Y

def known_ore_y(mem, snap, step):
    """The y of the nearest remembered cell of a mine step's ore (memory only), or None."""
    blocks = step.detail.get("blocks") if step.kind == "mine" else None
    spots = [tuple(r["pos"]) for k in blocks or () for r in mem.seen(k, snap.dimension)] if mem is not None else []
    return min(spots, key=lambda p: math.dist(p, snap.feet))[1] if spots else None

def needs_water_bucket(snap, plans, known_y=lambda step: None):
    """Pure given `known_y` (a step → the y of the nearest known cell of its ore, or None): a held plan has a fall
    and no water bucket is carried (outside the Nether, where water can't be poured)."""

    if snap.dimension == "minecraft:the_nether" or snap.inv.count("minecraft:water_bucket"):
        return False
    return any(falls(s, known_y(s)) for steps in plans for s in steps)

def durability_left(inv):
    """{tool kind: least durability left on a carried one} for TOOL_KINDS. Pure over the bag."""
    out = {}
    for kind in TOOL_KINDS:
        left = [d for _t, d, _ in inv.tools(kind)]
        if left:
            out[kind] = min(left)
    return out

def broke(before, working):
    """Pure: tool kinds that broke since last round — nearly worn out then, none working now."""

    return {kind for kind, left in before.items() if left <= NEAR_BREAK and kind not in working}

class Needs:
    """What upkeep wants got, and what it remembers between rounds: last round's working tools, plan prices (a memo)."""

    def __init__(self, brain):
        self.brain = brain
        self.plan_s_cache = {}        # (goal json, bag signature) -> (time, seconds)
        self.working = {}             # tool kind -> tier that worked last round
        self.wear = {}                # tool kind -> least durability left last round
        self.broken = set()           # tool kinds that broke and are not replaced yet
        self.needs_now = []           # [(kind, goal, why)] this round proposes getting (need)

    def observe(self, snap):
        tiers, now_wear = working_tiers(snap.inv), durability_left(snap.inv)
        self.broken |= broke(self.wear, tiers)
        self.broken -= set(tiers)
        self.working, self.wear = tiers, now_wear

    def propose(self, snap, ctx, reads=None):
        """This round's needs into `needs_now` (PLAN proposals), computed from the snapshot alone."""

        b, s, inv, over = self.brain, snap.state, snap.inv, snap.dimension == "minecraft:overworld"
        enclosed, soft_ground, dig_site = ground(reads)
        blocked = b.reflexes.blocked_here(b.place)
        # a bed from what is carried skips the night: before any shelter and the night's work
        bed_tonight = _once(reads, "bed_tonight", lambda: self.bed_tonight(snap))
        self.needs_now = []
        if bed_tonight():
            self.need("night prep", goals.have(("bed", 1)), "a bed skips the night")
        # the night's way from here: the shelter reflex runs it when its parts are carried, else its parts are this round's need
        night_way = _once(None, "night_way", lambda: self.overnight(
            snap, night_facts(soft_ground(), cooled_ways(b.ready), dig_site(), night_left_s=night_left_s(snap)),
            bed_too=False))
        shelter_due = _once(None, "shelter_due", lambda: over and snap.night and not bed_tonight()
                            and not b.reflexes.sheltered(snap, enclosed))
        if shelter_due():
            way, _secs, steps = night_way()
            if way is not None and any(st.kind != "shelter" for st in steps):
                self.prepare_night(way, steps)
        # upkeep only replaces a tool that broke under a held plan still wanting it (a blanket "no pickaxe" put one before every task)
        wanted = tool_kinds([st for h in getattr(b, "held", {}).values() for st in h["steps"]])
        for kind in sorted(self.broken & wanted):
            self.need("broken tool", goals.have(("tool", kind, craftable_tier(inv, kind, bag.RESERVED))), f"the {kind} broke")
        if needs_water_bucket(snap, [h["steps"] for h in getattr(b, "held", {}).values()],
                              lambda st: known_ore_y(b.mem, snap, st)):
            self.need("water bucket", goals.have(("minecraft:water_bucket", 1)),
                      "a plan with a fall in it and no water to land in")
        if blocked is not None and inv.count("building") < BRIDGE_MIN:
            self.need("bridge stock", goals.have(("building", bridge_stock(snap.feet, blocked["pos"]))),
                      "path blocked with nothing to bridge with")
        food_goal = goals.have(("food", 8))
        # food cooking counts toward stock and stomach (else a hunt ran with beef in the furnace)
        pending = b.mem.pending_outputs(snap.dimension)
        meals, points = food_on_its_way(pending)
        if food_count(inv) + meals < 8:
            secs, known = self.plan(food_goal, snap)
            if due_now(food_lasts_s(snap, pending), secs, known, s.get("food", 20) + points < EAT_BELOW):
                self.need("food stock", food_goal, "food runs out before more could be had")
        if over and not snap.night and inv.count("bed") == 0:
            way, seconds, steps = self.overnight(snap)
            if way is not None and due_now(dusk_s(snap), seconds, self.known(steps, snap), dusk_s(snap) <= 0) \
                    and not b.reflexes.sheltered(snap, enclosed):
                self.prepare_night(way, steps)
        return self.needs_now

    def bed_tonight(self, snap):
        """Night in the Overworld, no bed carried, and a bed whose plan needs no sun is the cheapest way through."""

        if not (snap.night and snap.inv.count("bed") == 0 and survive.can_sleep(snap.state) is None):
            return False
        way, _secs, steps = self.overnight(snap)
        return way == "bed" and all(st.kind in NIGHT_WORK for st in steps)

    def prepare_night(self, way, steps):
        """Dark comes before the chosen way could be had: its missing parts to the front."""

        if way == "bed":
            self.need("night prep", goals.have(("bed", 1)), "dark before a bed could be made")
            return
        src = next(s for k in ("overnight bed", "overnight") for s in decompose.SOURCES[k] if s["name"] == way)
        if any(st.kind != "shelter" for st in steps):
            self.need("night prep", goals.have(*src["needs"]), f"dark before {way} could be had")

    def cost(self, snap):
        return Cost(snap, self.brain.mem, self.brain.blacklist, policy=self.brain.policy_cache, reserved=bag.RESERVED)

    def need(self, kind, goal, why):
        """Propose getting `goal` (kind: its place in arbiter.PLAN_ORDER)."""

        if all(g != goal for _k, g, _w in self.needs_now):
            self.needs_now.append((kind, goal, why))

    def plan(self, goal, snap):
        """(seconds the plan for `goal` takes from this bag, whether every place it goes is known), kept briefly."""
        def price():
            cost = self.cost(snap)
            try:
                steps = decompose.decompose(snap.inv, goal, cost)
                return cost.plan_s(steps), all(cost.known_source(st) for st in steps)
            except Unplannable:
                return math.inf, False
        return memo_ttl(self.plan_s_cache, (json.dumps(goal, sort_keys=True), bag_signature(snap.inv)), PLAN_S_TTL,
                        price, time.time())

    def overnight(self, snap, facts=None, bed_too=True):
        """`overnight` from this bag, memoised briefly: priced every round it was the decide's hotspot (0.7 of 1.1 s)."""

        key = ("overnight", json.dumps(facts, sort_keys=True, default=str), bed_too, bag_signature(snap.inv),
               snap.dimension)
        return memo_ttl(self.plan_s_cache, key, PLAN_S_TTL,
                        lambda: overnight(snap.inv, self.cost(snap), facts, bed_too=bed_too), time.time())

    def plan_s(self, goal, snap):
        return self.plan(goal, snap)[0]

    def known(self, steps, snap):
        cost = self.cost(snap)
        return all(cost.known_source(st) for st in steps)

# -- upkeep skills

def repair_pair(slots, kind):
    """Pure: two damaged tools of one item whose combined durability beats the best — crafting them together repairs."""

    tools = [s for s in slots if s["id"].endswith("_" + kind) and s.get("maxDamage")]
    by_item = {}
    for s in tools:
        by_item.setdefault(s["id"], []).append(s)
    best = None
    for item, stacks in by_item.items():
        if len(stacks) < 2:
            continue
        a, b = sorted(stacks, key=lambda s: s["maxDamage"] - s.get("damage", 0))[:2]
        left = lambda s: s["maxDamage"] - s.get("damage", 0)   # noqa: E731
        combined = min(a["maxDamage"], left(a) + left(b) + a["maxDamage"] // REPAIR_BONUS_PARTS)
        if combined > max(left(s) for s in stacks) and (best is None or combined > best[0]):
            best = (combined, item, a["slot"], b["slot"])
    return None if best is None else best[1:]

def _kind_of(c):
    return c.args[1] if len(c.args) > 1 else c.kwargs.get("kind", "pickaxe")

def _tools_of(kind):
    return sum(1 for s in Inventory().slots if s["id"].endswith("_" + kind))

def repair_commands(state: "BagState", args) -> "list[CraftTask]":
    """`commands` for repair_tool: the one 2×2 craft of the two most worn tools of the kind (`repair_pair`)."""
    kind = args[0] if args else "pickaxe"
    pair = repair_pair(state["inv"].slots, kind)
    if pair is None:
        raise NotAvailable(f"no two {kind}s of the same kind worth combining")
    return [{"type": "craft", "pattern": [pair[0], pair[0], None, None], "count": 1}]

@skill(gives=["state:tool_combined"], remaining=_k.fewer_tools(_kind_of), needs={}, start=lambda c: _tools_of(_kind_of(c)), verify=lambda c: _tools_of(_kind_of(c)) < c.base,
       commands=lambda state, args: repair_commands(state, args),
       budget=60, stall=30, prefer=1,
       provides={"repair": lambda ctx, s: (s.token,) if repair_pair(Inventory().slots, s.token) else None})
def repair_tool(ctx, kind="pickaxe"):
    """Combine the two most worn tools of one kind in the 2×2 grid into one repaired tool."""
    tasks = repair_commands({"inv": Inventory()}, (kind,))
    item = tasks[0]["pattern"][0]
    before = sum(1 for s in Inventory().slots if s["id"] == item)
    r = api.run_chain(tasks, stop_on_failure=True)[0]
    yield None
    if lost(lambda: sum(1 for s in Inventory().slots if s["id"] == item), before) >= before:
        raise McError(f"repairing {item.split(':')[1]} failed: {r['message']}")
    log(f"repaired a {item.split(':')[1]} by combining two")
    return item
