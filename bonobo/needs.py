"""Needs: what must be PLANNED to be had — a bed or the night's parts before dark, food stock before it runs out, a
tool that broke under a held plan, a water bucket before a fall, blocks where the path is blocked, the night's ore
underground. Each is PROPOSED (`need` → `needs_now`), never queued: brain.need_act turns it into the next step of
its plan and arbiter.PLAN_ORDER ranks it with the queue. The fixed maintenance reflexes (eat, land, the night's
shelter, the bag…) are reflexes.py. Also the tool-repair skill and the one choice of how to get through a night
(`overnight`). Pure `repair_pair`, `dusk_s`, `due_now`, `overnight` are offline-tested."""
import json
import math
import time

from . import api, decompose, goals, skills
from .reflexes import BAG_FULL, BRIDGE_MIN, EAT_BELOW, _once, nether_retreat  # noqa: F401  (shared thresholds)
from .api import McError, NotAvailable, log
from .cost import Cost
from .data import NIGHT_WORK, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER
from .knowledge import food_count
from .planner import NullCost, Planner, Unplannable
from .skill import skill
from .skillcore import gained, lost
from .world import Inventory

LEAD = 1.5                 # how much earlier than a plan's own seconds its upkeep starts: the one margin
WALK_EAT_BELOW = 18        # hunger points: the jar eats on the way below this (regen stops at 18), `autoeat_policy`
FOOD_POINTS = 6.0          # hunger points one cooked item restores, roughly
DAY_TICKS_END = 12000      # dusk, in timeOfDay ticks
PLAN_S_TTL = 20            # seconds a "how long would that take" answer is kept
WORKING = 3                # durability left for a tool to count as working
NEAR_BREAK = 16            # durability a tool had last round for its disappearing to mean it broke (a round of work)
BRIDGE_STOCK = 32          # what to fetch when the path is blocked and there is less than BRIDGE_MIN
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


def overnight(inv, cost, facts=None, bed_too=True):
    """The one choice of how to get through a night, by price (`decompose.cheapest` over "overnight"): ("bed" or
    a SOURCES["overnight"] name, seconds, steps); (None, inf, []) when there is none. Asked at dusk for the lead
    (with the bed) and at night by the shelter row (`bed_too=False`: a bed is the sleep row's). `facts`: what
    was read of the place ({"soft_ground": the ground under the feet digs by hand})."""
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


def autoeat_policy():
    """Pure: what the jar eats on the way, and when (POST /autoeat, jar ≥ 0.1.46): below WALK_EAT_BELOW, the
    best food first (data.FOOD's order). Standing still and very hungry is the upkeep row's (EAT_BELOW)."""
    from .data import FOOD
    return {"below": WALK_EAT_BELOW, "foods": [f"minecraft:{f}" for f in FOOD]}


def due_now(left_s, plan_s, known, at_threshold):
    """Pure: is it time to start getting something? With a plan whose every place is known: when what is left
    (food, daylight) is shorter than that plan × LEAD. With a plan that guesses (a seek, a source nowhere known,
    priced by a prior): only at the real threshold — a guess's hundreds of seconds × LEAD made "food" urgent on a
    full stomach and "a bed" urgent in the morning."""
    return left_s < plan_s * LEAD if known else bool(at_threshold)


def tool_kinds(steps):
    """Pure: the tool kinds these plan steps need (a mine step with a tier needs a pickaxe)."""
    return {"pickaxe"} if any(st.kind == "mine" and st.detail.get("tier") is not None for st in steps) else set()


def food_lasts_s(snap):
    """Seconds of work the stomach and the meals in the bag cover (`risk.food_drain_s` per hunger point)."""
    from . import beliefs
    drain = float(beliefs.value("risk.food_drain_s"))
    return (float(snap.get("food", 20)) + FOOD_POINTS * food_count(snap.inv)) * drain


def working_tiers(inv):
    """{tool kind: best tier with a working one} for TOOL_KINDS. Pure over the bag."""
    out = {}
    for kind in TOOL_KINDS:
        tiers = [t for t, d, _ in inv.tools(kind) if d >= WORKING]
        if tiers:
            out[kind] = max(tiers)
    return out


def craftable_tier(inv, kind):
    """The best tier of `kind` this bag crafts outright, or 0 (`Planner.craftable_tier`: one answer for every tool
    goal). Pure over the bag."""
    return Planner.from_inventory(inv, NullCost()).craftable_tier(kind)


def falls(step):
    """Pure: does this plan step put the body where a fall can happen (FALL_RISK, or ore dug down to)?"""
    from .knowledge import FIND_AT
    if (step.kind, None) in FALL_RISK or (step.kind, step.token) in FALL_RISK:
        return True
    depth = FIND_AT.get(step.token)
    return step.kind == "mine" and depth is not None and depth < DEEP_Y


def needs_water_bucket(snap, plans):
    """Pure: a held plan has a step with a fall in it and the bag has no water bucket — outside the Nether, where
    water cannot be poured and the bucket has to be filled before going in."""
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
    """Pure: the tool kinds that broke between two rounds — one was nearly worn out last round (`before`: least
    durability left ≤ NEAR_BREAK) and none works now (`working`: working_tiers). A tool that vanished whole was
    stored, dropped or cleared: nothing to replace."""
    return {kind for kind, left in before.items() if left <= NEAR_BREAK and kind not in working}


class Needs:
    """What upkeep wants got, and what it remembers between rounds: which tools worked last round (broken) and the
    plans' prices (kept briefly — a memo, not a state another step reads)."""

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
        """This round's needs into `needs_now` (PLAN proposals), computed from the snapshot alone.
        `reads` = {"enclosed": bool, "bed_near": bool, "soft_ground": bool} stands in for world reads (offline);
        whatever is missing is read from the world, once, when first asked."""
        b, s, inv, over = self.brain, snap.state, snap.inv, snap.dimension == "minecraft:overworld"
        enclosed = _once(reads, "enclosed", skills.enclosed)
        blocked = b.reflexes.blocked_here(b.place)
        # A bed skips the night, the fastest way through it: made from what is carried (craft only, no sun needed),
        # it comes before any shelter and before the night's work underground.
        bed_tonight = _once(reads, "bed_tonight", lambda: self.bed_tonight(snap))
        self.needs_now = []
        if bed_tonight():
            self.need("night prep", goals.have(("bed", 1)), "a bed skips the night")
        # The night's way from here (dig in, wall in, a hut): the shelter reflex runs it when its parts are in the
        # bag; otherwise its parts are this round's need.
        night_way = _once(None, "night_way", lambda: overnight(
            inv, self.cost(snap), {"soft_ground": _once(reads, "soft_ground", skills.soft_ground_here)()},
            bed_too=False))
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
            self.need("bridge stock", goals.have(("building", BRIDGE_STOCK)), "path blocked with nothing to bridge with")
        food_goal = goals.have(("food", 8))
        if food_count(inv) < 8:
            secs, known = self.plan(food_goal, snap)
            if due_now(food_lasts_s(snap), secs, known, s.get("food", 20) < EAT_BELOW):
                self.need("food stock", food_goal, "food runs out before more could be had")
        if over and not snap.night and inv.count("bed") == 0:
            way, seconds, steps = overnight(inv, self.cost(snap))
            if way is not None and due_now(dusk_s(snap), seconds, self.known(steps, snap), dusk_s(snap) <= 0) \
                    and not b.reflexes.sheltered(snap, enclosed):
                self.prepare_night(way, steps)
        return self.needs_now

    def bed_tonight(self, snap):
        """Night in the Overworld, no bed carried, a bed would work, and the cheapest way through the night is a bed
        whose plan needs no sun (NIGHT_WORK steps only). Asked by the needs (make it) and the shelter reflex (not
        needed then), each from the snapshot."""
        if not (snap.dimension == "minecraft:overworld" and snap.night and snap.inv.count("bed") == 0
                and skills.can_sleep(snap.state) is None):
            return False
        way, _secs, steps = overnight(snap.inv, self.cost(snap))
        return way == "bed" and all(st.kind in NIGHT_WORK for st in steps)

    def prepare_night(self, way, steps):
        """Dark comes before the chosen way could be had: its missing parts to the front. The bed is a plan of its
        own; a shelter is made at night by the shelter row, so only what it needs is fetched now."""
        if way == "bed":
            self.need("night prep", goals.have(("bed", 1)), "dark before a bed could be made")
            return
        src = next(s for s in decompose.SOURCES["overnight"] if s["name"] == way)
        if any(st.kind != "shelter" for st in steps):
            self.need("night prep", goals.have(*src["needs"]), f"dark before {way} could be had")

    def cost(self, snap):
        return Cost(snap, self.brain.mem, self.brain.blacklist, policy=self.brain.policy_cache)

    def need(self, kind, goal, why):
        """Propose getting `goal` (kind: its place in arbiter.PLAN_ORDER). Proposed, never queued: the queue holds
        the player's and the cerebrum's goals only; what upkeep wants is ranked with its rows by the arbiter."""
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

    def plan_s(self, goal, snap):
        return self.plan(goal, snap)[0]

    def known(self, steps, snap):
        cost = self.cost(snap)
        return all(cost.known_source(st) for st in steps)


# ------------------------------------------------------------------------------------------------- upkeep skills


def repair_pair(slots, kind):
    """Pure: two damaged tools of the same item (e.g. two stone pickaxes) whose combined durability beats the best
    one — crafting them together repairs (vanilla grid repair, +5 %). Returns (item id, slot a, slot b) or None."""
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


@skill(start=lambda c: _tools_of(_kind_of(c)), verify=lambda c: _tools_of(_kind_of(c)) < c.base,
       budget=60, stall=30, per_unit=5, prefer=1,
       provides={"repair": lambda ctx, s: (s.token,) if repair_pair(Inventory().slots, s.token) else None})
def repair_tool(ctx, kind="pickaxe"):
    """Combine the two most worn tools of one kind in the 2×2 grid into one repaired tool."""
    pair = repair_pair(Inventory().slots, kind)
    if pair is None:
        raise NotAvailable(f"no two {kind}s of the same kind worth combining")
    item = pair[0]
    before = sum(1 for s in Inventory().slots if s["id"] == item)
    r = api.run({"type": "craft", "pattern": [item, item, None, None], "count": 1}, wait=30)
    yield None
    if lost(lambda: sum(1 for s in Inventory().slots if s["id"] == item), before) >= before:
        raise McError(f"repairing {item.split(':')[1]} failed: {r['message']}")
    log(f"repaired a {item.split(':')[1]} by combining two")
    return item
