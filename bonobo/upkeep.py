"""Upkeep: the brain's fixed table (layer 3 of brain.py) and the upkeep skills (recover dropped items after a
death, repair a worn tool by combining two in the crafting grid).

The table: the first row that applies takes the round. Rows that only QUEUE work put a task at the front of the
queue instead (a pickaxe when none works, a tool that broke — at the best tier the bag can craft — food before it runs out, a bed before dark, blocks
when the path is blocked and there is nothing to bridge with). Pure `repair_pair`, `dusk_s`, `nether_retreat` are
offline-tested."""
import json
import math
import time

from . import api, blueprints, decompose, goals, nav, nether, skills, tape, tasks
from .api import McError, NotAvailable, log
from .cost import Cost
from .data import BASE_MARKERS, COVERED_SKY, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER
from .knowledge import food_count
from .planner import NullCost, Planner, Unplannable
from .skill import skill
from .skillcore import gained, lost
from .world import Inventory, find

LEAD = 1.5                 # how much earlier than a plan's own seconds its upkeep starts: the one margin
EAT_BELOW = 14             # hunger points: eat below this, while there is something to eat
FOOD_POINTS = 6.0          # hunger points one cooked item restores, roughly
BAG_FULL = 34              # slots used before the bag is emptied
DAY_TICKS_END = 12000      # dusk, in timeOfDay ticks
JOB_RANGE = 96
STUCK_LIMIT = 60           # seconds in the same block with the same bag → unstuck
PLAN_S_TTL = 20            # seconds a "how long would that take" answer is kept
WORKING = 3                # durability left for a tool to count as working
NEAR_BREAK = 16            # durability a tool had last round for its disappearing to mean it broke (a round of work)
BLOCKED_FOR_S = 120        # a path failure this recent, here, is "the path is blocked"
BRIDGE_MIN = 8             # building blocks worth starting a bridge with
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


def food_lasts_s(snap):
    """Seconds of work the stomach and the meals in the bag cover (`risk.food_drain_s` per hunger point)."""
    from . import beliefs
    drain = float(beliefs.value("risk.food_drain_s"))
    return (float(snap.get("food", 20)) + FOOD_POINTS * food_count(snap.inv)) * drain


def nether_retreat(snap):
    """In the Nether, head home through the portal when food, health or bag room run low. Pure."""
    if snap.dimension != "minecraft:the_nether":
        return None
    inv, s = snap.inv, snap.state
    if food_count(inv) < 4:
        return "food running out"
    if s.get("health", 20) <= 8:
        return "health low"
    if inv.used_slots() >= 35:
        return "bag full"
    return None


def working_tiers(inv):
    """{tool kind: best tier with a working one} for TOOL_KINDS. Pure over the bag."""
    out = {}
    for kind in TOOL_KINDS:
        tiers = [t for t, d, _ in inv.tools(kind) if d >= WORKING]
        if tiers:
            out[kind] = max(tiers)
    return out


def craftable_tier(inv, kind):
    """The best tier of `kind` this bag can craft outright — a plan of crafting steps only, nothing to gather, mine
    or smelt — or 0 (wood: the plan gathers the logs). Pure over the bag. A higher tier a plan needs is that plan's
    own business, not the replacement's."""
    for tier in sorted((t for t in TOOL_MATERIAL_FOR_TIER if t > 0), reverse=True):
        try:
            steps = Planner.from_inventory(inv, NullCost()).plan([("tool", kind, tier)])
        except Unplannable:
            continue
        if all(s.kind == "craft" for s in steps):
            return tier
    return 0


def _once(reads, key, read):
    """A zero-argument reader: `reads[key]` when given, else `read()` on first use, kept for the round."""
    box = {}
    if reads and key in reads:
        box["v"] = reads[key]

    def get():
        if "v" not in box:
            box["v"] = read()
        return box["v"]
    return get


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


class Upkeep:
    """The table, and what it remembers between rounds: where the body has been (stuck), which tools worked last
    round (broken), where the last path failure was going (blocked). `brain` supplies the failure policy
    (`ready`, `failed`, `retry`), the movement policy and memory."""

    def __init__(self, brain):
        self.brain = brain
        self.plan_s_cache = {}        # (goal json, bag signature) -> (time, seconds)
        self.history = []             # (time, feet, bag signature) for "stuck in place"
        self.escalated = {}
        self.working = {}             # tool kind -> tier that worked last round
        self.wear = {}                # tool kind -> least durability left last round
        self.broken = set()           # tool kinds that broke and are not replaced yet
        self.blocked = None           # {"t", "place", "pos"}: the last path failure and where it was going

    # -- what the rounds tell it
    def observe(self, snap):
        now = time.time()
        self.history = [h for h in self.history if now - h[0] <= STUCK_LIMIT + 30]
        self.history.append((now, snap.feet, bag_signature(snap.inv)))
        tiers, now_wear = working_tiers(snap.inv), wear(snap.inv)
        self.broken |= broke(self.wear, tiers)
        self.broken -= set(tiers)
        self.working, self.wear = tiers, now_wear

    def failed(self, cause, err, place):
        """A path failure is remembered with where it was going: the "path blocked" rows answer it."""
        if cause == "nav":
            self.blocked = {"t": time.time(), "place": place, "pos": getattr(err, "pos", None)}

    # -- the table
    def act(self, snap, ctx, reads=None):
        """(name, run) of the first row that applies, or None; rows that only queue work are applied on the way.
        `reads` = {"enclosed": bool, "bed_near": bool} stands in for the world reads the rows make (offline);
        whatever is missing is read from the world, once, when a row first asks."""
        b, s, inv, over = self.brain, snap.state, snap.inv, snap.dimension == "minecraft:overworld"
        enclosed = _once(reads, "enclosed", skills.enclosed)
        bed_near = _once(reads, "bed_near", lambda: bool(find(BASE_MARKERS["bed"], radius=48, limit=1)))
        blocked = self.blocked_here(b.place)
        rows = [
            ("recover items", lambda: b.mem.recent_death(snap.dimension) is not None, lambda: recover_items(ctx)),
            ("eat", lambda: s.get("food", 20) < EAT_BELOW and skills.edible_carried(inv),
             lambda: skills.eat(raw_ok=not food_count(inv))),
            ("reach land", lambda: skills.swimming(s), lambda: skills.reach_land(ctx)),
            ("leave the Nether", lambda: nether_retreat(snap) is not None,
             lambda: nether.use_portal(ctx, "minecraft:overworld")),
            ("dig out", lambda: not snap.night and enclosed(), lambda: skills.dig_out(ctx)),
            ("sleep", lambda: over and snap.night and skills.can_sleep(s) is None
             and (inv.count("bed") > 0 or bed_near()),
             lambda: skills.sleep(ctx, b.policy(snap, True))),
            ("shelter", lambda: over and snap.night and not self.sheltered(snap, enclosed),
             lambda: self.shelter(snap, ctx)),
            ("collect job", lambda: self.ready_job(snap) is not None, lambda: self.collect_job(snap, ctx)),
            ("collect machine", lambda: self.ready_machine(snap) is not None,
             lambda: skills.collect_machine(ctx, self.ready_machine(snap))),
            ("empty the bag", lambda: inv.used_slots() >= BAG_FULL, lambda: self.empty_bag(snap, ctx)),
            ("path blocked", lambda: blocked is not None and inv.count("building") >= BRIDGE_MIN,
             lambda: self.bridge(ctx, blocked)),
            ("unstuck", lambda: self.stuck_in_place(snap, enclosed), lambda: self.unstuck(snap, ctx)),
        ]
        for name, due, run in rows:
            if b.ready(name) and due():
                return name, run
        # Rows that only queue work: the queue does it, at the front.
        if "pickaxe" not in self.working:
            self.urgent(goals.have(("tool", "pickaxe", 0)), "no working pickaxe")
        for kind in sorted(self.broken):
            self.urgent(goals.have(("tool", kind, craftable_tier(inv, kind))), f"the {kind} broke")
        if needs_water_bucket(snap, [h["steps"] for h in getattr(b, "held", {}).values()]):
            self.urgent(goals.have(("minecraft:water_bucket", 1)), "a plan with a fall in it and no water to land in")
        if blocked is not None and inv.count("building") < BRIDGE_MIN:
            self.urgent(goals.have(("building", BRIDGE_STOCK)), "path blocked with nothing to bridge with")
        food_goal = goals.have(("food", 8))
        if food_count(inv) < 8 and food_lasts_s(snap) < self.plan_s(food_goal, snap) * LEAD:
            self.urgent(food_goal, "food runs out before more could be had")
        bed_goal = goals.have(("bed", 1))
        if over and not snap.night and inv.count("bed") == 0 \
                and dusk_s(snap) < self.plan_s(bed_goal, snap) * LEAD:
            self.urgent(bed_goal, "dark before a bed could be made")
        return None

    def urgent(self, goal, why):
        task = tasks.add(goal, front=True, source="upkeep", expires_s=1800)
        if task.get("created", 0) >= time.time() - 1:
            log(f"upkeep: {goals.describe(goal)} to the front ({why})")

    def plan_s(self, goal, snap):
        """Seconds the plan for `goal` would take from this bag (Σ Step.est), kept briefly."""
        key = (json.dumps(goal, sort_keys=True), bag_signature(snap.inv))
        hit = self.plan_s_cache.get(key)
        if hit and time.time() - hit[0] < PLAN_S_TTL:
            return hit[1]
        cost = Cost(snap, self.brain.mem, self.brain.blacklist, policy=self.brain.policy_cache)
        try:
            seconds = cost.plan_s(decompose.decompose(snap.inv, goal, cost))
        except Unplannable:
            seconds = math.inf
        self.plan_s_cache[key] = (time.time(), seconds)
        return seconds

    # -- path blocked
    def blocked_here(self, place):
        """The last path failure, when it is recent, happened here and says where it was going; else None."""
        bl = self.blocked
        if bl is None or bl["pos"] is None or bl["place"] != place or time.time() - bl["t"] > BLOCKED_FOR_S:
            return None
        return bl

    def bridge(self, ctx, blocked):
        """Make the way by hand (skills.bridge_toward) toward where the failed walk was going."""
        self.blocked = None
        log(f"   path to {blocked['pos']} blocked → bridging toward it")
        return skills.bridge_toward(ctx, blocked["pos"])

    # -- night
    def shelter(self, snap, ctx):
        """Night, exposed, no bed to sleep in: under the ground with a pickaxe, else a hut, else walls."""
        b = self.brain
        if any(d >= WORKING for _, d, _ in snap.inv.tools("pickaxe")):
            try:
                return skills.dig_in(b.context(snap.dimension, b.policy(snap, True)))
            except api.INTERRUPTIONS:
                raise
            except McError as e:
                log(f"   dig-in failed: {e} → a hut or walls")
        if not skills.materials_missing(blueprints.SHELTER):
            return skills.build_shelter(ctx)
        return skills.pod(ctx)

    def sheltered(self, snap, enclosed=None):
        if snap.get("skyLight", 15) <= COVERED_SKY:
            return True
        try:
            if (enclosed or skills.enclosed)():
                return True
        except (tape.ReplayMiss, McError):
            pass
        feet = list(snap.feet)
        return any(feet in s.get("interior", []) for s in self.brain.mem.sites(snap.dimension))

    # -- jobs and the bag
    def ready_job(self, snap):
        near = [j for j in self.brain.mem.jobs(snap.dimension)
                if skills.job_ready(j) and math.dist(j["pos"], snap.feet) <= JOB_RANGE]
        return min(near, key=lambda j: math.dist(j["pos"], snap.feet), default=None)

    def ready_machine(self, snap):
        """The nearest machine (auto smelter) whose loaded order is due, within JOB_RANGE."""
        near = [m for m in self.brain.mem.machines(snap.dimension)
                if skills.pending_ready(m) and math.dist(m["origin"], snap.feet) <= JOB_RANGE]
        return min(near, key=lambda m: math.dist(m["origin"], snap.feet), default=None)

    def collect_job(self, snap, ctx):
        from . import jobs
        job = self.ready_job(snap)
        if job is not None:
            jobs.collect(ctx, job)

    def empty_bag(self, snap, ctx):
        if skills.store_plan(snap.inv.slots) and skills.can_store_here(ctx, local_only=snap.night):
            return skills.deposit(ctx, local_only=snap.night)
        return skills.tidy_inventory(ctx)

    # -- stuck
    def stuck_in_place(self, snap, enclosed=None):
        """Same block and the same bag for STUCK_LIMIT seconds (sheltered at night excluded)."""
        if snap.night and self.sheltered(snap, enclosed):
            return False
        old = [h for h in self.history if time.time() - h[0] >= STUCK_LIMIT]
        if not old:
            return False
        ref = old[-1]
        return all(math.dist(h[1], ref[1]) < 2 and h[2] == ref[2] for h in self.history if h[0] >= ref[0])

    def unstuck(self, snap, ctx):
        """One way out per call — the nearest site, up, sideways, down — each skipped once it failed here."""
        b = self.brain
        x, y, z = snap.feet
        site = b.mem.nearest_site(snap.feet, snap.dimension)
        if site and math.dist(site["pos"], snap.feet) < 12:
            site = None
        methods = ([("site", tuple(site["pos"]))] if site else []) + [
            ("up", (x, y + 12, z)), ("east", (x + 16, y, z)), ("west", (x - 16, y, z)),
            ("south", (x, y, z + 16)), ("north", (x, y, z - 16)), ("down", (x, y - 8, z))]
        for label, target in methods:
            name = f"unstuck:{label}"
            if not b.ready(name):
                continue
            log(f"no progress for {STUCK_LIMIT}s at {snap.feet} → unstuck by heading {label} {target}")
            self.history.clear()
            if nav.moved(nav.go_to(target, b.policy(snap, snap.night), range_=3, attempts=1)):
                b.retry.succeeded(name)
                return
            b.failed(name, NotAvailable(f"could not get {label} to {target}"))
            raise NotAvailable(f"unstuck {label} failed")
        self.escalate("stuck", f"every unstuck method failed at {snap.feet}")
        raise NotAvailable("every unstuck method failed here")

    def escalate(self, kind, what):
        """A macro problem: one `?? STALL` line per kind per 20 min — supervise.sh wakes Claude on it."""
        now = time.time()
        if now - self.escalated.get(kind, 0) < 1200:
            return
        self.escalated[kind] = now
        log(f"?? STALL {kind}: {what}")


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


def _death_retired(c):
    """The death note this call walked to is spent: what was there is carried, what was not is not coming back."""
    death = c.args[0].mem.recent_death(api.get("/state")["dimension"])
    return death is None or tuple(death["pos"]) != c.result


def _drops_gone(c):
    """World evidence, not our own note: no dropped items left around the death spot we walked to."""
    from .world import entities
    return _death_retired(c) and not entities(10, ["minecraft:item"])


@skill(verify=_drops_gone, budget=300, stall=90, per_unit=120)
def recover_items(ctx):
    """Go back to the last death spot within 5 minutes and pick up what dropped there."""
    s = api.get("/state")
    death = ctx.mem.recent_death(s["dimension"])
    if death is None:
        raise NotAvailable("no recent death to recover from")
    pos = tuple(death["pos"])
    log(f"   recovering items at the death spot {pos}")
    if not nav.arrived(pos, ctx.policy, range_=2, attempts=1):
        raise api.NavFailed(f"death spot {pos} not reachable")
    before = Inventory().used_slots()
    nav.sweep(ctx, radius=10, wait=60)
    yield Inventory().used_slots()
    got = gained(lambda: Inventory().used_slots(), before) - before
    # Either way the note is spent: what is here is now carried, and what is not here is not coming back. A record
    # the world has already answered must be retired on arrival rather than left to expire on a timer, or the same
    # sixty-block walk is worth the same seconds again five minutes later.
    ctx.mem.forget_death(pos)
    log(f"recovered {got} stacks at {pos}" if got else f"nothing left at {pos}: the drops are gone")
    return pos
