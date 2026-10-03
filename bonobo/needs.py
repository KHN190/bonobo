"""Needs: what must be PLANNED to be had — a bed or the night's parts before dark, a tool that broke under a held plan, a water bucket before a fall, blocks where the path is blocked, the night's ore underground. Each is PROPOSED (`need` → `needs_now`), never queued: the brain plans it with the queue in the round's one plan (planner.plan_round, by seconds). The fixed maintenance reflexes (eat, land, the night's shelter, the bag…) are reflexes.py. Also the tool-repair skill and the one choice of how to get through a night (`overnight`). Pure `repair_pair`, `dusk_s`, `due_now`, `overnight` are offline-tested."""

import copy
import json
import math
import time
from typing import TYPE_CHECKING

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, decompose, goals, survive, world
from .reflexes import BAG_FULL, BRIDGE_MIN, EAT_BELOW, _once, ground, nether_retreat  # noqa: F401  (shared thresholds)
from .api import McError, NotAvailable, log
from . import bag
from .bag import bag_signature
from .cost import Cost
from .decompose import cooled_ways, night_facts, night_left_s, way_key  # noqa: F401
if TYPE_CHECKING:
    from .shapes import BagState, CraftTask
from .data import NIGHT_WORK, TOOL_KINDS, memo_ttl, TICKS_PER_S, REPAIR_BONUS_PARTS
from .knowledge import FIND_AT
from .planner import Target, Unplannable, craftable_tier, plan_round
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

def dusk_s(snap) -> float:
    """Seconds until the next dusk (world.ticks_until_dusk: data.DAY_END, the one dusk; LEAD is the only margin), 0 while
    it is dark; from dawn (NIGHT_END) a whole day ahead."""
    return world.ticks_until_dusk(int(snap.time)) / TICKS_PER_S

def cover(ctx, state):
    """The cheapest shelter that can run here now — every skill providing a "shelter:" way, priced by the one cost
    model — run: a hazard's last way (hazard.RECOVERY) when its own way out is spent."""
    from .planner import Step
    from .world import Snapshot
    from . import skill as skillkit
    # priced as the brain prices (brain.py's Cost: memory, the targets banned here, the movement policy): a banned
    # entity or block is no shelter's way (W3)
    from .perception import ground_read
    snap = Snapshot.from_readings(state, Inventory(), *world.look_around(
        (state["blockX"], state["blockY"], state["blockZ"]), state.get("dimension"), _k.SOURCE_BLOCKS))
    cost = Cost(snap, ctx.mem, getattr(ctx, "blacklist", None), policy=getattr(ctx, "policy", None),
                region=ground_read(snap), stop=api.stop_asked)
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

def night_options(inv, cost, facts=None, bed_too=True):
    """[(way, needs, extra seconds, of them before dark, own steps)]: the night's ways; own steps priced, not planned."""
    out = [("bed", [] if inv.count("bed") > 0 else [("bed", 1)], 0.0, 0.0, [])] if bed_too else []
    for key in ("overnight bed", "overnight"):
        sources, why = decompose.offered_sources(key, cost, facts)
        if not sources:
            log(f"upkeep: no {key} way ({'; '.join(why)})")
        for src, needs, own in sources:
            own_s = sum(st.est for st in own) / TICKS_PER_S
            keys = src.get("extra_s", ())
            out.append((src["name"], list(needs), own_s + decompose.extra_s(keys, facts),
                        own_s + decompose.day_extra_s(keys, facts), own))
    return out

def overnight(inv, cost, facts=None, bed_too=True) -> tuple[str | None, float, list]:
    """(way, seconds before dark, steps) of the cheapest way through the night (one-of target); (None, inf, []): none."""

    options = night_options(inv, cost, facts, bed_too)
    if not options:
        return None, math.inf, []
    chosen, dusk = {}, copy.copy(cost)
    dusk.facts = lambda: {**cost.facts(), "night": False}      # prepared before dark
    try:
        night = Target("night", [], 0, options=tuple(o[:3] for o in options))
        _first, steps, seconds = plan_round(inv, [night], dusk, chosen=chosen)
    except Unplannable as e:
        log(f"upkeep: no way through the night ({e})")
        return None, math.inf, []
    _way, _needs, _extra, day_s, own = next(o for o in options if o[0] == chosen["night"])
    return chosen["night"], seconds + day_s, steps + own

def due_now(left_s, plan_s, known, at_threshold):
    """Pure: is it time to start getting something?"""

    return left_s < plan_s * LEAD if known else bool(at_threshold)

def tool_kinds(steps):
    """Pure: the tool kinds these plan steps need (a mine step with a tier needs a pickaxe)."""
    return {"pickaxe"} if any(st.kind == "mine" and st.detail.get("tier") is not None for st in steps) else set()

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
        self._facts = None            # (snapshot, its night facts): read once a round

    def observe(self, snap):
        tiers, now_wear = working_tiers(snap.inv), durability_left(snap.inv)
        self.broken |= broke(self.wear, tiers)
        self.broken -= set(tiers)
        self.working, self.wear = tiers, now_wear

    def propose(self, snap, ctx, reads=None):
        """This round's needs into `needs_now` (PLAN proposals), computed from the snapshot alone."""

        b, inv, over = self.brain, snap.inv, snap.dimension == "minecraft:overworld"
        enclosed, _soft_ground, _dig_site = ground(reads, snap)
        blocked = b.reflexes.blocked_here(b.place)
        self.night_facts(snap, reads)
        # a bed from what is carried skips the night: before any shelter and the night's work
        bed_tonight = _once(reads, "bed_tonight", lambda: self.bed_tonight(snap))
        self.needs_now = []
        if bed_tonight():
            self.need("night prep", goals.have(("bed", 1)), "a bed skips the night")
        # the night's way from here: the shelter reflex runs it when its parts are carried, else its parts are this round's need
        night_way = _once(None, "night_way", lambda: self.overnight(snap, bed_too=False))
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
        if over and not snap.night and inv.count("bed") == 0:
            due = self.dusk_due(snap)
            if due is not None and not b.reflexes.sheltered(snap, enclosed):
                self.prepare_night(*due)
        return self.needs_now

    def dusk_due(self, snap):
        """(way, steps) of the night's way when its preparation is due by dusk, else None."""
        left, prep = dusk_s(snap), self.night_prep_s(snap)
        if prep is None:
            return None
        way, _seconds, steps = self.overnight(snap)
        if way is not None and due_now(left, prep, self.known(steps, snap), left <= 0):
            return way, steps
        return None

    def night_prep_s(self, snap):
        """Light seconds the night's way needs before dark (dusk's reading), at the margin; None: no way."""
        free = self.night_free_s(snap)
        way, seconds, steps = self.overnight(snap)
        got = [s for s in (free, self.marginal_s(seconds, steps) if way is not None else None) if s is not None]
        return min(got) if got else None

    def route_covered_s(self, snap):
        """Seconds of work under cover the unmet milestones still ask (the planner bound's covered part)."""
        from .planner import bound
        lb, inv, ticks = bound(self.cost(snap)), snap.inv, 0.0
        for name in goals.MILESTONES:
            for key, n in _k.have_remainder(inv, goals.needs(goals.make("milestone", name=name), inv)).items():
                tool = key.startswith("tool:")
                token = _k.tool_item(key.split(":")[1], n) if tool else key
                ticks += lb.covered.get(token, lb.covered.get(_k.mid(token), 0.0)) * (1 if tool else n)
        return ticks / TICKS_PER_S

    def plan_steps(self):
        """The steps the round's plans hold (the queue's and upkeep's): what is made anyway."""
        b = self.brain
        held = list(getattr(b, "held", {}).values()) + [getattr(b, "needs_plan", None)]
        return list({id(st): st for h in held if h for st in h["steps"]}.values())

    def marginal_s(self, seconds, steps):
        """Pure given the plans: `seconds` of a way's `steps` less the share of each the round's plans make anyway."""
        made = {}
        for st in self.plan_steps():
            made[st.key()] = made.get(st.key(), 0) + int(st.count)
        less = 0.0
        for st in steps:
            have = min(int(st.count), made.get(st.key(), 0))
            made[st.key()] = made.get(st.key(), 0) - have
            less += (getattr(st, "est", 0) or 0) / TICKS_PER_S * (have / max(1, int(st.count)))
        return max(0.0, seconds - less)

    def night_free_s(self, snap):
        """Light seconds of the cheapest night way needing nothing got first (the night itself out); None: none."""
        facts = self._facts[1] if self._facts is not None and self._facts[0] is snap else None
        cost = self.cost(snap)
        free = [secs + decompose.day_extra_s(extra, facts) for key in ("overnight bed", "overnight")
                for _n, secs, _st, extra in decompose.free_ways(key, cost, facts)]
        return min(free) if free else None

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
        from .perception import ground_read
        return Cost(snap, self.brain.mem, self.brain.blacklist, policy=self.brain.policy_cache, reserved=bag.RESERVED,
                    region=ground_read(snap), stop=api.stop_asked)

    def need(self, kind, goal, why):
        """Propose getting `goal` (`kind` names it)."""

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
        """`overnight`, kept PLAN_S_TTL per (facts, bag); facts default to the round's."""

        if facts is None and self._facts is not None and self._facts[0] is snap:
            facts = self._facts[1]
        key = ("night", json.dumps(facts, sort_keys=True, default=str), bed_too, bag_signature(snap.inv),
               snap.dimension)
        return memo_ttl(self.plan_s_cache, key, PLAN_S_TTL,
                        lambda: overnight(snap.inv, self.cost(snap), facts, bed_too=bed_too), time.time())

    def night_facts(self, snap, reads=None):
        """The round's night facts (decompose.night_facts), read once a snapshot."""
        if self._facts is not None and self._facts[0] is snap:
            return self._facts[1]
        from .reflexes import home_walk_s
        b = self.brain
        _enclosed, soft_ground, dig_site = ground(reads, snap)
        left = night_left_s(snap)
        left = None if left is None else math.ceil(left / PLAN_S_TTL) * PLAN_S_TTL
        facts = night_facts(soft_ground(), cooled_ways(b.ready), dig_site(), home_walk_s(b, snap), left,
                            covered_work_s=self.route_covered_s(snap))
        self._facts = (snap, facts)
        return facts

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
