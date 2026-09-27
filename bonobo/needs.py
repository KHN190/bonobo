"""Needs: what must be PLANNED to be had — a bed or the night's parts before dark, food stock before it runs out, a tool that broke under a held plan, a water bucket before a fall, blocks where the path is blocked, the night's ore underground. Each is PROPOSED (`need` → `needs_now`), never queued: brain.need_act turns it into the next step of its plan and arbiter.PLAN_ORDER ranks it with the queue. The fixed maintenance reflexes (eat, land, the night's shelter, the bag…) are reflexes.py. Also the tool-repair skill and the one choice of how to get through a night (`overnight`). Pure `repair_pair`, `dusk_s`, `due_now`, `overnight` are offline-tested."""

import json
import math
import time

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, decompose, goals, skills
from .reflexes import BAG_FULL, BRIDGE_MIN, EAT_BELOW, _once, ground, nether_retreat  # noqa: F401  (shared thresholds)
from .api import McError, NotAvailable, log
from .cost import Cost
from .data import NIGHT_WORK, TOOL_KINDS, mid
from .knowledge import food_count, food_points
from .planner import NullCost, Planner, Unplannable
from .skill import skill
from .skillcore import lost
from .world import Inventory

LEAD = 1.5                 # how much earlier than a plan's own seconds its upkeep starts: the one margin
DAY_TICKS_END = 12000      # dusk, in timeOfDay ticks
PLAN_S_TTL = 20            # seconds a "how long would that take" answer is kept
WORKING = 3                # durability left for a tool to count as working
NEAR_BREAK = 16            # durability a tool had last round for its disappearing to mean it broke (a round of work)
BRIDGE_STOCK = 32          # the most to fetch when the path is blocked and there is less than BRIDGE_MIN

def bridge_stock(feet, target):
    """Pure: building blocks to fetch for a blocked path — one per block of the way across (horizontal distance to where the walk was going), at least BRIDGE_MIN, at most BRIDGE_STOCK."""

    across = math.ceil(math.dist((feet[0], feet[2]), (target[0], target[2])))
    return max(BRIDGE_MIN, min(BRIDGE_STOCK, across))
# Plan steps that put the body where a fall can happen — portals, strongholds, fortresses, deep ore reached by
# digging down. The jar's WaterClutch saves a fall only with a water bucket to hand, so a plan with one of these
# gets a bucket first. (kind, token) with token None = any token of that kind.
FALL_RISK = {("portal", None), ("seek", "fortress"), ("seek", "stronghold"), ("seek", "portal_room"),
             ("activate", "end_portal"), ("hunt", "minecraft:blaze_rod")}
DEEP_Y = 40                # a mine step whose ore is richest below this is reached by digging down

def bag_signature(inv):
    """What the bag holds, exactly: a change the plan did not make is an event."""
    return tuple(sorted((s["id"], s.get("count", 1)) for s in inv.slots))

def dusk_s(snap):
    """Seconds until dark: (12000 − timeOfDay) / 20, 0 once it is dark."""
    t = int(snap.time) % 24000
    return max(0.0, (DAY_TICKS_END - t) / 20.0) if t < DAY_TICKS_END else 0.0

def night_facts(soft):
    """The place facts the night's pricing reads, from the soft-ground reading (skills."""

    if soft is None or soft is False:
        return {"soft_ground": False}
    return {"soft_ground": True, "soft_walk_s": 0.0 if soft is True else float(soft)}

def overnight(inv, cost, facts=None, bed_too=True):
    """The one choice of how to get through a night, by price (`decompose."""

    bed = []

    def bed_plan():
        if not bed_too:
            raise Unplannable("a bed is the sleep row's")
        bed[:] = decompose.decompose(inv, goals.have(("bed", 1)), cost)
        return bed

    try:
        steps, way = decompose.cheapest("overnight", 1, bed_plan, inv, cost, facts=facts)
    except Unplannable as e:
        log(f"upkeep: no way through the night ({e})")
        return None, math.inf, []
    if steps is None:
        steps, way = bed, "bed"
    return way, cost.plan_s(steps), steps

def due_now(left_s, plan_s, known, at_threshold):
    """Pure: is it time to start getting something?"""

    return left_s < plan_s * LEAD if known else bool(at_threshold)

def tool_kinds(steps):
    """Pure: the tool kinds these plan steps need (a mine step with a tier needs a pickaxe)."""
    return {"pickaxe"} if any(st.kind == "mine" and st.detail.get("tier") is not None for st in steps) else set()

def food_on_its_way(pending):
    """Pure: (meals, hunger points) of the ready food a background job or machine is making ({item id: count})."""
    from .data import FOOD, NUTRITION
    ready = {mid(f): NUTRITION[f] for f in FOOD}
    meals = sum(n for item, n in pending.items() if item in ready)
    return meals, sum(n * ready[item] for item, n in pending.items() if item in ready)

def food_lasts_s(snap, pending=None):
    """Seconds of work the stomach, the meals in the bag and those cooking in the background (`pending`, memory's pending_outputs) cover (`risk."""

    from . import beliefs
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

def craftable_tier(inv, kind):
    """The best tier of `kind` this bag crafts outright, or 0 (`Planner."""

    return Planner.from_inventory(inv, NullCost()).craftable_tier(kind)

def falls(step):
    """Pure: does this plan step put the body where a fall can happen (FALL_RISK, or ore dug down to)?"""
    from .knowledge import FIND_AT
    if (step.kind, None) in FALL_RISK or (step.kind, step.token) in FALL_RISK:
        return True
    depth = FIND_AT.get(step.token)
    return step.kind == "mine" and depth is not None and depth < DEEP_Y

def needs_water_bucket(snap, plans):
    """Pure: a held plan has a step with a fall in it and the bag has no water bucket — outside the Nether, where water cannot be poured and the bucket has to be filled before going in."""

    if snap.dimension == "minecraft:the_nether" or snap.inv.count("minecraft:water_bucket"):
        return False
    return any(falls(s) for steps in plans for s in steps)

def wear(inv):
    """{tool kind: least durability left on a carried one} for TOOL_KINDS. Pure over the bag."""
    out = {}
    for kind in TOOL_KINDS:
        left = [d for _t, d, _ in inv.tools(kind)]
        if left:
            out[kind] = min(left)
    return out

def broke(before, working):
    """Pure: the tool kinds that broke between two rounds — one was nearly worn out last round (`before`: least durability left ≤ NEAR_BREAK) and none works now (`working`: working_tiers)."""

    return {kind for kind, left in before.items() if left <= NEAR_BREAK and kind not in working}

class Needs:
    """What upkeep wants got, and what it remembers between rounds: which tools worked last round (broken) and the plans' prices (kept briefly — a memo, not a state another step reads)."""

    def __init__(self, brain):
        self.brain = brain
        self.plan_s_cache = {}        # (goal json, bag signature) -> (time, seconds)
        self.working = {}             # tool kind -> tier that worked last round
        self.wear = {}                # tool kind -> least durability left last round
        self.broken = set()           # tool kinds that broke and are not replaced yet
        self.needs_now = []           # [(kind, goal, why)] this round proposes getting (need)

    def observe(self, snap):
        tiers, now_wear = working_tiers(snap.inv), wear(snap.inv)
        self.broken |= broke(self.wear, tiers)
        self.broken -= set(tiers)
        self.working, self.wear = tiers, now_wear

    def propose(self, snap, ctx, reads=None):
        """This round's needs into `needs_now` (PLAN proposals), computed from the snapshot alone."""

        b, s, inv, over = self.brain, snap.state, snap.inv, snap.dimension == "minecraft:overworld"
        enclosed, soft_ground = ground(reads)
        blocked = b.reflexes.blocked_here(b.place)
        # A bed skips the night, the fastest way through it: made from what is carried (craft only, no sun needed),
        # it comes before any shelter and before the night's work underground.
        bed_tonight = _once(reads, "bed_tonight", lambda: self.bed_tonight(snap))
        self.needs_now = []
        if bed_tonight():
            self.need("night prep", goals.have(("bed", 1)), "a bed skips the night")
        # The night's way from here (dig in, wall in, a hut): the shelter reflex runs it when its parts are in the
        # bag; otherwise its parts are this round's need.
        night_way = _once(None, "night_way", lambda: self.overnight(snap, night_facts(soft_ground()), bed_too=False))
        shelter_due = _once(None, "shelter_due", lambda: over and snap.night and not bed_tonight()
                            and not b.reflexes.sheltered(snap, enclosed))
        if shelter_due():
            way, _secs, steps = night_way()
            if way is not None and any(st.kind != "shelter" for st in steps):
                self.prepare_night(way, steps)
        # Needs: what upkeep wants got, proposed (not queued) — the arbiter ranks them with the rows.
        # A tool is the plan's need (decompose puts one in any plan whose step wants it); upkeep only replaces one
        # that broke under a held plan that still wants it — "no working pickaxe" put a pickaxe (and its tree) in
        # front of every task, a log chop included.
        wanted = tool_kinds([st for h in getattr(b, "held", {}).values() for st in h["steps"]])
        for kind in sorted(self.broken & wanted):
            self.need("broken tool", goals.have(("tool", kind, craftable_tier(inv, kind))), f"the {kind} broke")
        if needs_water_bucket(snap, [h["steps"] for h in getattr(b, "held", {}).values()]):
            self.need("water bucket", goals.have(("minecraft:water_bucket", 1)),
                      "a plan with a fall in it and no water to land in")
        if blocked is not None and inv.count("building") < BRIDGE_MIN:
            self.need("bridge stock", goals.have(("building", bridge_stock(snap.feet, blocked["pos"]))),
                      "path blocked with nothing to bridge with")
        food_goal = goals.have(("food", 8))
        # Food cooking in the background counts: its meals toward the stock, its points toward the stomach — two
        # beef in the furnace and "hunt 6× porkchop" put a 345 s hunt before the task (night_first__low).
        pending = b.mem.pending_outputs(snap.dimension) if getattr(b, "mem", None) is not None else {}
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
        """Night in the Overworld, no bed carried, a bed would work, and the cheapest way through the night is a bed whose plan needs no sun (NIGHT_WORK steps only)."""

        if not (snap.dimension == "minecraft:overworld" and snap.night and snap.inv.count("bed") == 0
                and skills.can_sleep(snap.state) is None):
            return False
        way, _secs, steps = self.overnight(snap)
        return way == "bed" and all(st.kind in NIGHT_WORK for st in steps)

    def prepare_night(self, way, steps):
        """Dark comes before the chosen way could be had: its missing parts to the front."""

        if way == "bed":
            self.need("night prep", goals.have(("bed", 1)), "dark before a bed could be made")
            return
        src = next(s for s in decompose.SOURCES["overnight"] if s["name"] == way)
        if any(st.kind != "shelter" for st in steps):
            self.need("night prep", goals.have(*src["needs"]), f"dark before {way} could be had")

    def cost(self, snap):
        return Cost(snap, self.brain.mem, self.brain.blacklist, policy=self.brain.policy_cache)

    def need(self, kind, goal, why):
        """Propose getting `goal` (kind: its place in arbiter."""

        if all(g != goal for _k, g, _w in self.needs_now):
            self.needs_now.append((kind, goal, why))

    def plan(self, goal, snap):
        """(seconds the plan for `goal` takes from this bag, whether every place it goes is known), kept briefly."""
        key = (json.dumps(goal, sort_keys=True), bag_signature(snap.inv))
        hit = self.plan_s_cache.get(key)
        if hit and time.time() - hit[0] < PLAN_S_TTL:
            return hit[1]
        cost = self.cost(snap)
        try:
            steps = decompose.decompose(snap.inv, goal, cost)
            got = (cost.plan_s(steps), all(cost.known_source(st) for st in steps))
        except Unplannable:
            got = (math.inf, False)
        self.plan_s_cache[key] = (time.time(), got)
        return got

    def overnight(self, snap, facts=None, bed_too=True):
        """`overnight` from this bag, kept briefly like `plan`: priced every round from a fresh cost model it was the round's hotspot (0."""

        key = ("overnight", json.dumps(facts, sort_keys=True, default=str), bed_too, bag_signature(snap.inv),
               snap.dimension)
        hit = self.plan_s_cache.get(key)
        if hit and time.time() - hit[0] < PLAN_S_TTL:
            return hit[1]
        got = overnight(snap.inv, self.cost(snap), facts, bed_too=bed_too)
        self.plan_s_cache[key] = (time.time(), got)
        return got

    def plan_s(self, goal, snap):
        return self.plan(goal, snap)[0]

    def known(self, steps, snap):
        cost = self.cost(snap)
        return all(cost.known_source(st) for st in steps)

# ------------------------------------------------------------------------------------------------- upkeep skills

def repair_pair(slots, kind):
    """Pure: two damaged tools of the same item (e."""

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
        combined = min(a["maxDamage"], left(a) + left(b) + a["maxDamage"] // 20)
        if combined > max(left(s) for s in stacks) and (best is None or combined > best[0]):
            best = (combined, item, a["slot"], b["slot"])
    return None if best is None else best[1:]

def _kind_of(c):
    return c.args[1] if len(c.args) > 1 else c.kwargs.get("kind", "pickaxe")

def _tools_of(kind):
    return sum(1 for s in Inventory().slots if s["id"].endswith("_" + kind))

def repair_commands(state, args):
    """`commands` for repair_tool: the one 2×2 craft of the two most worn tools of the kind (`repair_pair`)."""
    kind = args[0] if args else "pickaxe"
    pair = repair_pair(state["inv"].slots, kind)
    if pair is None:
        raise NotAvailable(f"no two {kind}s of the same kind worth combining")
    return [{"type": "craft", "pattern": [pair[0], pair[0], None, None], "count": 1}]

@skill(gives=["state:tool_combined"], remaining=_k.fewer_tools(_kind_of), needs={}, speed={}, start=lambda c: _tools_of(_kind_of(c)), verify=lambda c: _tools_of(_kind_of(c)) < c.base,
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
