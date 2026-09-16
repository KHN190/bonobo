"""The decision loop: reflexes → phase bookkeeping → night handling → cost-scored goal selection → one verified
step → repeat. Goals are expressed as requirements; the planner turns them into steps; only the first step of the
best plan runs per round, so every round re-plans against the real inventory (DEPS-style selector)."""
import math
import time
import traceback

import json
import os

from . import (api, arbiter, bag, blueprints, brewing, combat, directives, end, farming, fluids, intent, jobs,
               lookahead, loot, nav, nether, paths, priority, retry, route, skills, tape, ui, upkeep, world)
from . import skill as skillkit
from .api import GameUnreachable, McError, NotAvailable, PlayerTookControl, TaskStuck, log
from .data import BASE_MARKERS, GROUPS, HAND_MINEABLE_SUFFIX, bare, mid
from .knowledge import UNDERGROUND_KINDS
from .planner import tool_ok  # noqa: F401  (one definition, shared)
from .memory import Memory
from .planner import Planner, Unplannable, runnable
from .world import Inventory, Snapshot, entities, find, travel_ticks

# ---------------------------------------------------------------- goals


class Goal:
    """A target the brain wants: `done` checks the world; `needs` feeds the planner; `after` runs when needs are
    met (e.g. equip armor, fill a bucket); `value` weighs it against its planned cost. `phase` is a soft
    preference (later phases score lower), never a lock. `superseded_by` names a better goal that makes this one
    pointless whenever that goal can be planned (stone pickaxe when an iron one is craftable)."""

    def __init__(self, name, value, done, needs=(), after=None, phase=0, superseded_by=None, build=None,
                 background=False, action=None, unlocks=(), feasible=None, dimension="minecraft:overworld",
                 effect=None):
        # What the survival state looks like once this goal is done (survival.py). A goal with an effect is worth
        # exactly the seconds that effect saves; `value` then only breaks ties among the unmigrated.
        self.effect = effect
        # Where the goal's work happens; in another dimension the pool offers "use the portal" for it instead.
        self.dimension = dimension
        # feasible() → None when the goal can work here, else the reason it can't (checked before it enters the pool)
        self.feasible = feasible
        self.name, self.value, self.done, self.needs, self.after, self.phase = name, value, done, list(needs), after, phase
        self.action = action   # skill(ctx) run once the needs are met (fill a bucket, …)
        # Goals this one enables that the plans can't show (tool/utensil preconditions inside skills).
        self.unlocks = tuple(unlocks)
        self.superseded_by = superseded_by
        self.build = build   # blueprint to build near home once the needs are met
        # Background goals (stockpiles) have low value and no phase: they fill the gaps between main goals so the
        # agent is never idle, and always lose to a plannable main goal.
        self.background = background


def wearing_at_least(inv, material):
    rank = {"iron": 3, "diamond": 4, "netherite": 5}
    return all(bare(inv.worn(slot)).split("_")[0] in [m for m, r in rank.items() if r >= rank[material]]
               for slot in ("head", "chest", "legs", "feet"))


def pick_tool_plan(plans, blocked_kinds, no_mining=True):
    """Pure: from {tier: plan} the tier to make. A plan qualifies only if every step can run now: no step of a
    blocked kind (surface gathering at night) and, when replacing the pickaxe, no mining (it needs the tool we lack).
    The highest qualifying tier wins unless it costs clearly more than the cheapest. Returns (tier, plan) or None."""
    def total(plan):
        return sum(s.est for s in plan)

    ok = {t: p for t, p in plans.items()
          if p and not any(s.kind in blocked_kinds or (no_mining and s.kind == "mine" and s.detail.get("tier") is not None)
                           for s in p)}
    if not ok:
        return None
    cheapest = min(total(p) for p in ok.values())
    tier = max(t for t, p in ok.items() if total(p) <= 1.5 * cheapest + 200)
    return tier, ok[tier]


PICK_MAX = {"wooden": 59, "golden": 32, "stone": 131, "iron": 250, "diamond": 1561, "netherite": 2031}
# (base, cost). Tiny on purpose: a fallback only runs when no main goal can (torches once outscored the portal).
FALLBACK_BASE = {"light up": (0.03, 300), "strip mine": (0.06, 1500), "explore": (0.04, 2400)}
RANKING_FILE = paths.data("ranking.jsonl")
NETHER_MOBS = {"minecraft:blaze", "minecraft:wither_skeleton", "minecraft:ghast"}
PLAN_CACHE_S = 20

# Forcing cooling fallbacks back in must happen well BEFORE anyone calls the agent idle, or the two thresholds fight:
# at 15 s each, a slice declared "idle 17s" in the same moment the force rule would have produced work (the only
# runnable fallback was explore, cooling for 20 s after a successful leg).
IDLE_LIMIT = 8       # seconds without work before a fallback is forced (user rule: never idle past ~15 s)
from .data import COVERED_SKY  # noqa: E402,F401  (one definition, shared with the action table)
STALL_LIMIT = 45 * 60  # no main goal completed for this long → macro stall, escalated to Claude
TRACK_FILE = paths.data("track.jsonl")
FAR_SITE_TICKS = 1200  # a site within 60 s is always worth the trek; beyond that it's compared to building a hut
STUCK_LIMIT = 60     # checked every minute: moved < 2 blocks and no inventory change → forced unstuck


def step_key(goal, step):
    return f"{goal.name}/{step.kind}:{step.token}"


def progress_signature():
    try:
        inv = Inventory()
    except McError:
        return None
    return (tuple(sorted((s["id"], s.get("count", 1), s.get("damage", 0)) for s in inv.slots)),
            tuple(sorted((k, v.get("id")) for k, v in inv.equipment.items())))


# Which mobs count as a threat is a fact about mobs, not a decision, so it lives with the rest of them in
# threat.py. It used to live here, and perception imported the brain to ask — which put brain, and through it
# scenarios, into the dependency closure of every module below. `module_deps` is the bench's re-run key, so the
# closure collapsing to "everything" did not fail: it silently re-ran every scenario for every change.


from .route import kit_needs, nether_kit_missing  # noqa: E402  (one definition, shared with the route)


def must_retreat(snap):
    """Pure-ish: in the Nether, head home through the portal when food, health or bag room run low."""
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


def build_needs(mem, snap, name, extra=()):
    """A blueprint goal's needs: only what the started build still lacks (nothing placed counts twice), else the
    full material list."""
    bp = blueprints.REGISTRY[name]
    started = mem.data.get("builds", {}).get(name)
    if started and started.get("dimension") == snap.dimension:
        origin, turns = tuple(started["origin"]), started["turns"]
        cells = [pos for pos, *_ in blueprints.placed(bp, origin, turns)]
        lo = tuple(min(c[i] for c in cells) for i in range(3))
        hi = tuple(max(c[i] for c in cells) for i in range(3))
        try:
            region = world.Region(lo, hi)
            need = blueprints.remaining(bp, origin, turns, region.name)
        except McError:
            need = blueprints.materials(bp)
    else:
        need = blueprints.materials(bp)
    return [(token, n) for token, n in need.items()] + list(extra)


def _enchant_best_pickaxe(ctx, inv, mem):
    pick = max(inv.tools("pickaxe"), default=None)
    if pick is None:
        raise NotAvailable("no pickaxe to enchant")
    ui.enchant_item(ctx, pick[2])
    mem.data.setdefault("enchanted", {})["pickaxe"] = pick[2]
    mem.save()


from .route import food_count  # noqa: E402,F811  (one definition of "food carried" for every module)


def _needed_by(plan, better):
    """Is this goal's own product a step of the better goal's plan? Then it is that plan's cheap prefix, not its
    rival: iron ore cannot be mined without a stone pickaxe, so "superseded by iron pickaxe" removed the only way
    to ever reach an iron pickaxe, and the agent carried a wooden one for a session."""
    if not plan or not better:
        return False
    product = plan[-1].token
    return any(s.token == product for s in better)


SHELTER_RANGE = 64.0


def _shelter_near(snap, mem):
    site = mem.nearest_site(snap.feet, snap.dimension, kinds=["home", "shelter"])
    return bool(site) and math.dist(site["pos"], snap.feet) <= SHELTER_RANGE


def survival_state(snap, mem):
    """The survival model's view of now (survival.py), from the snapshot and memory only — no world reads."""
    from . import survival
    inv = snap.inv
    tier = lambda kind: max([t for _, _, t in [(k, d, tier_of(item)) for k, d, item in inv.tools(kind) if d >= 3]]
                            or [0])
    return survival.make_state(
        night=snap.night, ticks_until_dusk=snap.ticks_until_dusk, hp=snap.get("health", 20), food=snap.get("food", 20),
        # Sheltered = there is a roof to sleep under within reach, from memory alone. Hardcoding False made every
        # shelter worth nothing to the model, so the one place that ever built one was the dusk branch of safety().
        bed=inv.count("bed") > 0, sheltered=_shelter_near(snap, mem), torches=inv.usable("minecraft:torch") >= 8,
        sword=tier("sword"), pickaxe=tier("pickaxe"), food_items=food_count(inv), nights_missed=mem.nights_missed,
        # Armour POINTS, as /state reports them: the belief table prices protection per point, in one place.
        armor=snap.get("armor", 0),
        shield=inv.offhand() == "minecraft:shield" or inv.count("minecraft:shield") > 0)


def tier_of(item):
    name = bare(item).split("_")[0]
    return {"wooden": 1, "golden": 1, "stone": 1, "iron": 2, "diamond": 3, "netherite": 3}.get(name, 0)


def goals(snap, mem):
    inv = snap.inv
    home = mem.home()
    g = [
        # Phase 0 — survive and tool up (route: ~20 logs, stone tools, food, bed, light).
        Goal("stone pickaxe", 10, lambda: tool_ok(inv, "pickaxe", 1), [("tool", "pickaxe", 1)],
             superseded_by="iron pickaxe", effect={"pickaxe": 1}),
        # Phantoms come after 3 sleepless nights: the bed grows more urgent with every missed night.
        Goal("bed to carry", 9, lambda: inv.count("bed") > 0, [("bed", 1)], effect={"bed": True}),
        # Same bed through string (spiders) — the pool takes whichever source is cheaper right now.
        Goal("bed from string", 8, lambda: inv.count("bed") > 0, [("minecraft:white_bed", 1)], effect={"bed": True}),
        # The plan asks for exactly what `done` needs: planning 16 when 8 finishes it doubled the cost the pool
        # divides by, and cooking lost to everything for a whole session while raw meat was eaten instead.
        Goal("food (≥8)", 8, lambda: food_count(inv) >= 8, [("food", 8)], effect={"food_items": 8}),
        Goal("stone sword", 6, lambda: tool_ok(inv, "sword", 1), [("tool", "sword", 1)], superseded_by="iron sword",
             effect={"sword": 1}),
        Goal("torches (≥8)", 6, lambda: inv.usable("minecraft:torch") >= 8, [("minecraft:torch", 24)],
             effect={"torches": True}),
        Goal("carried crafting table", 5, lambda: inv.count("minecraft:crafting_table") > 0,
             [("minecraft:crafting_table", 1)]),
        Goal("carried furnace", 4, lambda: inv.count("minecraft:furnace") > 0, [("minecraft:furnace", 1)]),
        Goal("stone axe", 4, lambda: tool_ok(inv, "axe", 1), [("tool", "axe", 1)]),
        Goal("building blocks (≥32)", 3, lambda: inv.count("building") >= 32, [("stone", 64)]),
        Goal("ladders (≥4)", 2, lambda: inv.count("minecraft:ladder") >= 4, [("minecraft:ladder", 12)]),
        # Phase 1 — iron: what the route actually needs (bucket, pickaxe, shield, flint & steel) + armor.
        Goal("iron pickaxe", 9, lambda: tool_ok(inv, "pickaxe", 2), [("tool", "pickaxe", 2)], phase=1,
             effect={"pickaxe": 2}),
        Goal("bucket", 9, lambda: inv.count("minecraft:bucket") + inv.count("minecraft:water_bucket") > 0,
             [("minecraft:bucket", 1)], phase=1),
        Goal("shield", 7, lambda: inv.count("minecraft:shield") > 0, [("minecraft:shield", 1)],
             after=skills.equip_armor, phase=1, effect={"shield": True}),
        Goal("flint and steel", 6, lambda: inv.count("minecraft:flint_and_steel") > 0,
             [("minecraft:flint_and_steel", 1)], phase=1),
        Goal("iron sword", 5, lambda: tool_ok(inv, "sword", 2), [("tool", "sword", 2)], phase=1, effect={"sword": 2}),
        Goal("iron armor", 8, lambda: wearing_at_least(inv, "iron"),
             [(f"minecraft:iron_{p}", 1) for p in ("helmet", "chestplate", "leggings", "boots")
              if not bare(inv.worn({"helmet": "head", "chestplate": "chest", "leggings": "legs", "boots": "feet"}[p]))
              .startswith(("iron_", "diamond_", "netherite_"))],
             after=skills.equip_armor, phase=1, effect={"armor": 2}),
        # Phase 1→2 — the Nether: a water bucket casts obsidian on a lava pool, the frame is built and lit.
        Goal("water bucket", 7, lambda: inv.count("minecraft:water_bucket") > 0, [("minecraft:bucket", 1)],
             action=fluids.fill_water_bucket, phase=1, unlocks=("nether portal",),
             # Speedrun route: only with water actually known (in sight, or noted by the travel scan) — picked 17× in a
             # 20-min slice, 9 of them "no still water within 48 blocks". Normal play keeps looking as before.
             feasible=lambda: None if find(["water"], radius=48, limit=1) or (
                 route.current() and mem.resources("water", snap.dimension))
             else "no water within 48 blocks" if not route.current()
             else "no water seen yet (the travel scan notes lakes)"),
        # Renewable food: a wheat plot (a crop job harvests it) and breeding with that wheat.
        # A place to sleep and store, valued by the model (a bed in the open is interrupted; a shelter is not).
        # Its materials are needs, not a feasibility test: "missing stone and a torch" is a plan, not a refusal —
        # written as a test, the goal was refused every round of a session and no shelter was ever built.
        Goal("shelter nearby", 7, lambda: _shelter_near(snap, mem),
             [(item, n) for item, n in blueprints.materials(blueprints.SHELTER).items()],
             action=skills.build_shelter, effect={"sheltered": True}),
        Goal("wheat farm", 4, lambda: bool(mem.sites(snap.dimension, kinds=["farm"])),
             [("minecraft:stone_hoe", 1), ("minecraft:wheat_seeds", 8), ("minecraft:water_bucket", 1)],
             action=farming.plant_farm, phase=1),
        Goal("breed animals", 3, lambda: False, [], action=farming.breed, phase=1,
             feasible=lambda: None if inv.count("minecraft:wheat") >= 2 or inv.count("minecraft:carrot") >= 2
             else "no wheat or carrots to breed with"),
        # Phase 3–4 — Nether and End: blaze rods at a fortress, ender pearls, eyes of ender, the stronghold.
        Goal("nether fortress", 9, lambda: bool(mem.sites("minecraft:the_nether", kinds=["fortress"])), [],
             action=nether.find_fortress, phase=3, dimension="minecraft:the_nether",
             feasible=lambda: None if mem.machines("minecraft:overworld", "portal") else "no portal built yet"),
        Goal("blaze rods (7)", 9, lambda: inv.count("minecraft:blaze_rod") + inv.count("minecraft:blaze_powder") // 2 >= 7,
             [("minecraft:blaze_rod", 7)], phase=3, dimension="minecraft:the_nether",
             feasible=lambda: None if mem.sites("minecraft:the_nether", kinds=["fortress"]) else "no fortress found yet"),
        Goal("ender pearls (12)", 8, lambda: inv.count("minecraft:ender_pearl") + inv.count("minecraft:ender_eye") >= 12,
             [("minecraft:ender_pearl", 12)], phase=3,
             # Endermen roam the Overworld at night (and the Nether); by day there are none to hunt.
             feasible=lambda: None if snap.night or snap.dimension == "minecraft:the_nether"
             else "endermen are hunted at night"),
        Goal("eyes of ender (12)", 9, lambda: inv.count("minecraft:ender_eye") >= 12,
             [("minecraft:ender_eye", 12)], phase=4),
        Goal("stronghold located", 9, lambda: bool(mem.sites("minecraft:overworld", kinds=["stronghold"])), [],
             action=nether.locate_stronghold, phase=4,
             feasible=lambda: None if inv.count("minecraft:ender_eye") >= 2 else "need 2 eyes of ender to throw"),
        # Combat kit, loot, upkeep, potions — the route past the portal needs them (wiki / speedrun practice).
        Goal("bow", 6, lambda: inv.count("minecraft:bow") > 0, [("minecraft:bow", 1)], phase=2),
        Goal("arrows (32)", 5, lambda: inv.count("minecraft:arrow") >= 32, [("minecraft:arrow", 32)], phase=2),
        Goal("gold helmet for piglins", 5, lambda: inv.count("minecraft:golden_helmet") > 0 or
             bare(inv.worn("head")) == "golden_helmet", [("minecraft:golden_helmet", 1)], phase=3,
             feasible=lambda: None if mem.machines("minecraft:overworld", "portal") else "no portal built yet"),
        Goal("piglin barter", 7, lambda: inv.count("minecraft:ender_pearl") + inv.count("minecraft:ender_eye") >= 12,
             [], action=nether.barter_piglin, phase=3, dimension="minecraft:the_nether",
             feasible=lambda: nether.barter_ready(inv, [(inv.equipment.get(k) or {}).get("id")
                                                        for k in ("head", "chest", "legs", "feet")])),
        Goal("fire resistance potions", 5, lambda: inv.count("minecraft:potion") >= 3 and
             inv.count("minecraft:magma_cream") == 0 and inv.count("minecraft:nether_wart") == 0, [],
             action=brewing.brew_fire_resistance, phase=3,
             feasible=lambda: None if inv.count("minecraft:magma_cream") and inv.count("minecraft:nether_wart")
             and inv.count("minecraft:blaze_powder") else "need nether wart, magma cream and blaze powder"),
        Goal("loot nearby chests", 2.5, lambda: False, [], action=loot.loot_chest, phase=1,
             feasible=lambda: None if loot.unlooted_chests_cached(mem, snap) else "no unlooted chest nearby"),
        Goal("repair worn pickaxe", 3, lambda: False, [], action=lambda ctx: upkeep.repair_tool(ctx, "pickaxe"),
             feasible=lambda: None if upkeep.repair_pair(inv.slots, "pickaxe") and all(
                 d < 60 for _, d, _ in inv.tools("pickaxe")) else "no worn pair of pickaxes to combine"),
        # Worth what the gear costs to make again — which is what dying costs, by definition — and due NOW: the
        # drops despawn. Left at twelve legacy points it was worth 120 s and lost to crafting a sword.
        Goal("recover items after death", 12, lambda: False, [], action=upkeep.recover_items,
             effect={},
             feasible=lambda: None if mem.recent_death(snap.dimension) else "no recent death"),
        Goal("portal room found", 10, lambda: bool(mem.sites("minecraft:overworld", kinds=["portal_room"]))
             or snap.dimension == "minecraft:the_end", [], action=end.find_portal_room, phase=5,
             dimension="minecraft:overworld",
             feasible=lambda: None if mem.sites("minecraft:overworld", kinds=["stronghold"])
             and inv.count("minecraft:ender_eye") >= 12 else "need the stronghold estimate and 12 eyes"),
        Goal("activate end portal", 10, lambda: bool(find(["end_portal"], radius=32, limit=1)), [],
             action=end.activate_end_portal, phase=5,
             feasible=lambda: None if inv.count("minecraft:ender_eye") >= 12 and find(["end_portal_frame"], radius=32, limit=1)
             else "need 12 eyes and the portal room"),
        Goal("enter the End", 10, lambda: snap.dimension == "minecraft:the_end", [], action=end.enter_end, phase=5,
             feasible=lambda: None if find(["end_portal"], radius=32, limit=1) else "no active end portal nearby"),
        # The speedrun kill: beds explode in the End (end.slay_dragon plans it); 6 beds are made before the stronghold.
        Goal("beds for the dragon", 9, lambda: inv.count("bed") >= route.DRAGON_BEDS
             or snap.dimension == "minecraft:the_end"
             or bool(mem.data.get("dragon_defeated")), [("bed", 6)], phase=4),
        Goal("defeat the ender dragon", 20, lambda: bool(mem.data.get("dragon_defeated")), [],
             # One driver: crystals → pit → perch → one bomb → back in the pit; melee windows when the beds run out.
             action=lambda ctx: end.slay_dragon(ctx),
             phase=5, dimension="minecraft:the_end",
             # Beds first; melee when it perches once they're gone (the speedrun profile bans the bow).
             feasible=lambda: None if inv.count("bed") or inv.count("sword") or inv.count("axe")
             else "need beds or a sword"),
        # Screens (mod ≥ 0.1.23): enchant the pickaxe, trade villagers for pearls, repair at an anvil.
        Goal("enchanting table", 4, lambda: inv.count("minecraft:enchanting_table") > 0 or bool(
            find(["enchanting_table"], radius=24, limit=1)), [("minecraft:enchanting_table", 1)], phase=2),
        Goal("enchant the pickaxe", 4, lambda: bool(mem.data.get("enchanted", {}).get("pickaxe")), [],
             action=lambda ctx: _enchant_best_pickaxe(ctx, inv, mem), phase=2,
             feasible=lambda: None if snap.get("xpLevel", 0) >= 5 and inv.count("minecraft:lapis_lazuli")
             and (inv.count("minecraft:enchanting_table") or find(["enchanting_table"], radius=24, limit=1))
             else "need 5+ levels, lapis and an enchanting table"),
        Goal("villager pearls", 6, lambda: inv.count("minecraft:ender_pearl") + inv.count("minecraft:ender_eye") >= 12,
             [], action=lambda ctx: ui.trade(ctx, "minecraft:ender_pearl"), phase=3,
             feasible=lambda: None if inv.count("minecraft:emerald") >= 5 and any(
                 e["type"] == "minecraft:villager" for e in entities(24)) else "need emeralds and a villager nearby"),
        # The Nether trip's kit as one goal: its gaps become planner needs (food, blocks, gold helmet), untouched by
        # stock-goal bans.
        Goal("nether kit", 9, lambda: not nether_kit_missing(inv), kit_needs(inv), phase=2,
             feasible=lambda: None if mem.machines("minecraft:overworld", "portal") else "no portal built yet"),
        # Built once, in the Overworld: in the Nether there's no portal *machine*, and checking the current dimension
        # reopened the goal and walked the agent straight back through the portal.
        Goal("nether portal", 10, lambda: bool(mem.machines("minecraft:overworld", "portal")),
             build_needs(mem, snap, "nether_portal", [("minecraft:flint_and_steel", 1)]),
             build="nether_portal", phase=2),
        # Phase 2 — automation: machines that keep working while the agent is away (blueprints.py).
        Goal("auto smelter", 5, lambda: home is None or bool(mem.machines(snap.dimension, "smelting")),
             [("minecraft:hopper", 3), ("minecraft:chest", 3), ("minecraft:furnace", 1)],
             build="auto_smelter", phase=2),
        # Shelter kit: a door lets the agent build a hut wherever dusk finds it (blueprints.SHELTER).
        Goal("carried door", 3, lambda: inv.count("door") > 0, [("door", 1)]),
        # A chest in the bag means a full inventory can always be emptied on the spot (cache chest) instead of
        # dropping items that the next dig sweeps straight back up.
        # A cache chest placed nearby is what the carried one was for: counting it avoids the loop craft chest →
        # deposit places it → craft again.
        Goal("carried chest", 3.5, lambda: inv.count("minecraft:chest") > 0 or any(
            math.dist(s["pos"], snap.feet) <= 32 for s in mem.sites(snap.dimension, kinds=["cache", "home"])),
             [("minecraft:chest", 1)]),
        # Background stockpiles: cheap, nearby, always useful.
        Goal("stock logs", 1.5, lambda: inv.count("log") >= 32, [("log", 32)], background=True),
        # Counted over all building blocks (andesite etc. included): mining for more beyond this just fills the bag.
        Goal("stock building blocks", 1, lambda: inv.count("building") >= 64, [("stone", 64)], background=True),
        Goal("stock coal", 1.5, lambda: inv.count("coal") >= 24, [("coal", 24)], background=True),
        Goal("stock food", 2, lambda: food_count(inv) >= 24, [("food", 24)], background=True),
        # Tool safety stock: wood for sticks / a table / a door, and a spare stone pickaxe (tunnels wear picks fast).
        Goal("stock planks", 2, lambda: inv.count("planks") + 4 * inv.count("log") >= 16, [("planks", 16)],
             background=True),
        Goal("spare pickaxe", 2.5, lambda: sum(1 for _, d, _ in inv.tools("pickaxe") if d >= 20) >= 2,
             [("minecraft:stone_pickaxe", inv.count("minecraft:stone_pickaxe") + 1)], background=True),
        Goal("stock torches", 1.5, lambda: inv.usable("minecraft:torch") >= 48, [("minecraft:torch", 48)],
             background=True),
    ]
    return g


# ---------------------------------------------------------------- cost model backed by the live world


class LiveCost:
    def __init__(self, snap, blacklist=None, mem=None):
        self.snap = snap
        self.cache = {}
        self.blacklist = blacklist or {}
        self.mem = mem

    def _banned(self, key):
        exp = self.blacklist.get(key)
        return exp is not None and exp > time.time()

    def _find(self, blocks, radius):
        """Distance to the nearest target that isn't blacklisted as unreachable — in sight now, else the nearest point
        the travel scan remembered (resource map): a lake seen 120 blocks back is a cost, not "unobtainable"."""
        key = ("find", tuple(blocks), radius)
        if key not in self.cache:
            hits = [h for h in find(blocks, radius=radius, limit=20) if not self._banned((h["x"], h["y"], h["z"]))]
            dist = hits[0]["distance"] if hits else None
            if dist is None and self.mem is not None and radius >= 32:
                kind = next((k for k, ids in Brain.SCAN_BLOCKS.items() if set(ids) & {bare(b) for b in blocks}), None)
                if kind:
                    known = [p for p in self.mem.resources(kind, self.snap.dimension)
                             if not self._banned(tuple(p))]
                    if known:
                        dist = min(math.dist(p, self.snap.feet) for p in known)
            self.cache[key] = dist
        return self.cache[key]

    def _entity(self, types):
        key = ("ent", tuple(types))
        if key not in self.cache:
            es = [e for e in entities(64, types) if not self._banned((e["id"], 0, 0))]
            self.cache[key] = es[0] if es else None
        return self.cache[key]

    def _surface_trip(self):
        # Under rock, getting out is part of any surface trip, and it scales with depth (~1.5 s per block of
        # staircase): from y=-20 that's ~2 min, not the flat 30 s this used to add.
        if self.snap.get("skyLight", 15) > COVERED_SKY:
            return 0
        return 200 + 30 * max(0, 64 - int(self.snap.feet[1]))

    def station_near(self, block):
        return self._find([block], 6) is not None

    def cheapest_food(self, options):
        from .knowledge import HUNT
        raw = {c: c.replace("cooked_", "") for c in options}
        dist = {c: (self._entity(HUNT[raw[c]]) or {}).get("distance") for c in options}
        known = [c for c in options if dist[c] is not None]
        return min(known, key=lambda c: dist[c]) if known else options[0]

    # Planner step kind → (skill statistics key, units): the same keys the skill runner records under.
    def _learned_ticks(self, step):
        if self.mem is None:
            return None
        key, units = {"mine": (f"mine:{step.token}", step.count), "gather": ("chop", step.count),
                      "hunt": (f"hunt:{step.token}", step.count), "smelt": ("smelt", step.count),
                      "craft": ("craft", 1)}.get(step.kind, (None, 0))
        per = self.mem.duration(key) if key else None
        return int(per * units * 20) if per is not None else None

    def estimate(self, step):
        # Measured durations beat heuristics once there are enough samples (they include the walking involved).
        learned = self._learned_ticks(step)
        # Success rates apply once, at the candidate (priority.effective_success), not inside step costs.
        return learned if learned is not None else self._estimate(step)

    def risk_s(self, step, ticks=None):
        """Seconds this step is expected to cost in BLOOD, priced by the survival model.

        The rate and the shape both come from elsewhere: perception says what is around (one authority, no world
        read here), and the ACTION says how it is exposed — standing work takes the pressure for its duration,
        leaving takes it only until it is out of reach. A flat rate applied to everything is what made walking
        away from a zombie look as expensive as standing in front of it.
        """
        from . import perception, survival, threat
        from .solve import Action
        rows, _ids = perception.threats_seen()
        if not rows or self.snap is None:
            return 0.0
        seconds = (self.estimate(step) if ticks is None else ticks) / priority.TICKS_PER_S
        inv = self.snap.inv
        state = {"here": (self.snap.state["x"], self.snap.state["y"], self.snap.state["z"]),
                 "hp": self.snap.state.get("health", 20),
                 "protection": threat.protection(self.snap.get("armor", 0),
                                                 inv.offhand() == "minecraft:shield"),
                 "hazards": rows}
        shape = Action(step.kind, {}, max(0.1, seconds), tag=(step.kind, step.token))
        dhp = shape.exposure(state)
        if dhp <= 0:
            return 0.0
        sstate = survival_state(self.snap, self.mem) if self.mem is not None else None
        return survival.hp_seconds(sstate, dhp) if sstate else dhp

    def _estimate(self, step):
        walk = lambda d: int(d * 1.5 / 0.12)  # noqa: E731
        if step.kind == "craft":
            return 60
        if step.kind == "smelt":
            return 200 * step.count + 300
        if step.kind == "gather":
            d = self._find(GROUPS["log"], 48)
            if d is None and self.mem is not None:
                # Resource map: the nearest remembered grove that isn't depleted (or has regrown).
                groves = self.mem.resources("tree", self.snap.dimension)
                d = min((math.dist(g, self.snap.feet) for g in groves), default=None)
            return (walk(d) + 40 * step.count if d is not None else 6000) + self._surface_trip()
        if step.kind == "fill":
            # No live world query in an estimate: estimates run inside recorded decisions, and a fresh /find made
            # every golden replay miss. Ask the resource map instead, and assume a walk when it knows no water.
            d = None
            if self.mem is not None:
                lakes = self.mem.resources("water", self.snap.dimension)
                d = min((math.dist(p, self.snap.feet) for p in lakes), default=None)
            return (walk(d) if d is not None else 1200) + 20 * step.count
        if step.kind == "mine":
            d = self._find(step.detail["blocks"], 32)
            per = 60 * step.detail.get("breaks", step.count)
            return walk(d) + per if d is not None else 8000 + per
        if step.kind == "hunt":
            e = self._entity(step.detail["types"])
            if e is None:
                reach = 6000
            else:
                climb = max(0, e["y"] - self.snap.feet[1]) * 20   # a staircase up costs ~1 s per block
                reach = walk(e["distance"]) + climb + self._surface_trip()
            return reach + 300 * step.detail.get("kills", step.count)
        return 1000


# ---------------------------------------------------------------- the brain


SEG_MISSES = 3      # "nothing of this kind" answers per route segment before the goal waits for the next segment
# Planning is the expensive half, so it stops once there is enough to choose between — but "enough" is a count of
# usable candidates, not a quota of attempts: on a night when everything answers "waits for day", a quota leaves
# the pool with one entry. MAX_EXPAND is the ceiling for a world where nothing plans at all.
MIN_CHOICES = 5
# No ceiling worth the name: layered descent solves a goal in a few milliseconds, so trying every goal costs less
# than a tenth of a second, and a cap only ever meant "the pool is thin for a reason nobody can see". It stops
# early at MIN_CHOICES because that is enough to choose between, not because planning is dear.
MAX_EXPAND = 200
_EAT_REFLEX_FOOD = 4      # the floor: below this eat anything at once, without waiting for a round


# The recorder is wired from here, the top, so tape itself imports nothing above `paths`. Each entry says what
# part of a decision belongs on the tape and who owns it.
def _wire_threats(brain):
    from . import perception, skills
    def answer(option):
        snap = Snapshot()
        ctx = skills.Context(brain.mem, brain.policy(snap, snap.night), snap.dimension, brain.blacklist)
        brain.engage(option, snap.state, ctx)
    perception.wire_answer(answer)


def _wire_tape():
    from . import directives, loot, priority, route
    for mod in (priority, route, directives):
        tape.register(mod.__name__.split(".")[-1], file_path=lambda m=mod: m.FILE)
    tape.register("loot_cache", snapshot=lambda: {
        "pos": list(loot._CACHE["pos"]) if loot._CACHE["pos"] else None,
        "hits": [list(h) for h in loot._CACHE["hits"]], "age": time.time() - loot._CACHE["t"]})


_wire_tape()


class Brain:
    def __init__(self):
        self.mem = Memory()
        skillkit.STATS = self.mem     # skills record measured durations; estimates read them back
        nav.ROAD_MEM = self.mem       # travelled legs become a road network (roads.py) for later trips
        self.fail_sig = {}      # step statistics key -> world state of its last failure (success resets on change)
        self.recent_fail = None  # (candidate name, time) of the last failed pick
        self.plan_cache = {}    # goal name -> (time, bag key, plan)
        self.blacklist = {}     # unreachable targets, shared by every round's Context and the cost model
        self.last_light = 0
        self.last_offhand = 0
        self._seen = {}              # entity id -> (pos, when): the threat layer differences velocity between rounds
        self.idle_since = None
        self.history = []       # (time, feet, progress signature) per round, for "stuck in place" detection
        self.last_quick = None
        self.policy_cache = nav.Policy(before_segment=self.segment_reflexes)
        self.retry = retry.Retry()   # state-aware failure policy (retry.py)
        self.sig = None              # world state of this round (retry decisions)
        self.place = None            # coarse location for failure counting
        self.snap_cache = None       # this round's snapshot, so the segment hook can re-price without a world read
        self._prices_key, self._prices = None, ({}, {})
        # Three "found nothing" answers for the same goal inside one route segment: that resource isn't here.
        self.prep_tokens = {}        # goal → tokens that only its look-ahead preparation needs (not the goal itself)
        self.seg_name = None         # active route segment; per-segment search misses reset when it changes
        self.seg_misses = {}         # (segment, goal) → how often it found nothing of its kind here
        self.coarse = None           # coarser state (success-rate resets)
        self.committed = None        # goal name kept until its assumptions fail or it is clearly beaten
        self.committed_assumptions = []
        self.committed_pos = None    # where the committed plan is headed: errands are judged against that path
        self.last_choice = (None, 0)
        self.last_hold_log = 0
        self.escalated = {}
        self.done_seen = set()
        self.last_milestone = time.time()
        self.last_track = 0
        _wire_threats(self)

    # -- policies
    def policy(self, snap, night):
        # Without a usable pickaxe, movement walks only: a dig route would raise ToolMissing mid-trip, and a trip that
        # runs ahead of the tool goal (dusk return) would retry it forever.
        can_dig = any(d >= 3 for _, d, _ in snap.inv.tools("pickaxe"))
        return nav.Policy(allow_dig=True, hand_only=not can_dig, allow_surface=not night,
                          protected=self.mem.protected_cells(snap.dimension),
                          before_segment=self.segment_reflexes if can_dig else self.hand_segment_reflexes)

    # -- reflexes (temporary interrupts; never plan, never recurse into goals)
    def reflexes(self):
        s = api.get("/state")
        if s["control"].get("paused"):
            api.wait_for_handback()
            s = api.get("/state")
        if s["dead"]:
            # What was on us is what makes the walk back worth anything; after the respawn it is unknowable.
            self.mem.log_death((s["blockX"], s["blockY"], s["blockZ"]), s["dimension"],
                               carried=[(x["id"], x.get("count", 1)) for x in Inventory().slots])
            log("died → respawning")
            api.post("/respawn")
            time.sleep(2)
            s = api.get("/state")
        if s["screen"] == "class_433":
            api.post("/resume")
        # Self-preservation outranks everything (Mindcraft's first mode): drowning or burning → get out now.
        from . import perception as _pc
        if _pc.drowning(s) or _pc.drowning_in(s) <= _pc.REFLEX_SLACK_S:
            # The same clock perception watches, with more slack: between tasks there is room to surface early.
            log(f"reflex: {_pc.drowning_in(s):.1f}s of air slack → swimming up")
            api.post("/stop")
            api.run({"type": "goto", "x": s["blockX"], "y": s["blockY"] + 6, "z": s["blockZ"], "range": 2,
                     "partial": True, "useBoat": False}, wait=20)
            raise McError("surfaced after running low on air")
        if s["inLava"]:
            log("reflex: in lava → leaving it")
            api.post("/stop")
            api.run({"type": "goto", "x": s["blockX"], "y": s["blockY"] + 3, "z": s["blockZ"], "range": 3,
                     "partial": True}, wait=20)
            raise McError("escaped lava")
        # No threat decision here. `reflexes` runs first in every round and may not fail: a NotAvailable raised
        # from it (an evade with nowhere to go) aborted the whole round, so the agent never reached safety(), never
        # reached the pool, and therefore never fought back, never fled, never slept and never built. Answering a
        # threat is a choice with a price, and choices belong in the pool (_threat_candidates) — or, when there is
        # no time for a round, in survival() below.
        if s["dimension"] == "minecraft:the_nether":
            # Ghast fireballs: hit one that comes within reach and it flies back (a portal or bridge survives, and
            # so do we). Punching is instant; dodging needs cover we may not have.
            for fb in entities(6, ["minecraft:fireball"]):
                if fb["distance"] <= 4.5:
                    log("reflex: ghast fireball close → hitting it back")
                    try:
                        api.run({"type": "attack", "entity": fb["id"]}, wait=2)
                    except McError:
                        pass
        if s["food"] <= _EAT_REFLEX_FOOD:
            skills.eat(raw_ok=True)   # the floor only; eating well is a candidate, priced against the work
        # Invariant, not a goal step: better armor in the bag is worn at once (a goal's `after` only ran when the
        # whole goal finished, so single pieces sat unused).
        head = bare((Inventory().equipment.get("head") or {}).get("id") or "")
        if s["screen"] == "none" and s["dimension"] == "minecraft:the_nether" and head != "golden_helmet" \
                and Inventory().count("minecraft:golden_helmet"):
            nether.wear_gold_helmet()      # equipment by dimension: gold on in the Nether (piglins), iron back outside
            head = "golden_helmet"
        gold_on_in_nether = s["dimension"] == "minecraft:the_nether" and head == "golden_helmet"
        if s["screen"] == "none" and not gold_on_in_nether and skills.better_armor_carried():
            skills.equip_armor()
        # Same kind of invariant: the shield lives in the offhand (blocks skeleton arrows, creeper blasts, blazes).
        if s["screen"] == "none" and skills.shield_wanted_in_offhand() and time.time() - self.last_offhand > 30:
            self.last_offhand = time.time()
            skills.shield_to_offhand()
        # Inside a sealed pod everything dark is behind the wall: nothing to light, nothing can spawn next to us.
        if time.time() - self.last_light > 5 and not skills.enclosed():
            ctx = skills.Context(self.mem, self.policy_cache, s["dimension"], self.blacklist)
            if skills.place_torch_if_dark(ctx):
                self.last_light = time.time()

    def hand_segment_reflexes(self, tasks):
        """Segment hook for hand-digging routes (no pickaxe): bookkeeping and reflexes, no pickaxe precondition."""
        self.yield_if_stale()
        self.yield_if_stale()
        dug = [(t["x"], t["y"], t["z"]) for t in tasks if t.get("type") == "mine" and "x" in t]
        if dug:
            self.mem.mark_dirty_near(dug, api.get("/state")["dimension"])
        self.reflexes()

    def yield_if_stale(self):
        """Hand the body back if the plan's premise has changed. Called at every chain segment — which is where an
        atomic action ends, about one block apart.

        This is what makes a commitment a commitment. A skill used to keep the body until it finished, so nothing
        below "dead in four seconds" could get a word in; a zombie could beat on the agent for a whole mining task.
        The planner does not seize the body back — PLAN yields at its own boundaries, and only SAFETY and faster
        pre-empt. Seizing would leave half-walked paths and half-opened containers to clean up.
        """
        if not self.committed_assumptions or self.snap_cache is None:
            return
        for kind, value in self.committed_assumptions:
            if kind == "premise" and self.premise(self.snap_cache) != value:
                raise api.CommitmentExpired(f"{self.committed}: the world changed while working")

    def segment_reflexes(self, tasks):
        self.yield_if_stale()
        dug = [(t["x"], t["y"], t["z"]) for t in tasks if t.get("type") == "mine" and "x" in t]
        if dug:
            s = api.get("/state")
            self.mem.mark_dirty_near(dug, s["dimension"])
            # Precondition of every dig segment (tunnels too, which mine with requireDrops=False): a working pickaxe
            # unless every block is hand-mineable. Failing here re-plans, so the tool goal gets chosen next round.
            region = world.region_around(dug, pad=0)
            needs_pick = region is None or any(not bare(region.name(c)).endswith(HAND_MINEABLE_SUFFIX) for c in dug)
            if needs_pick and not any(d >= 3 for _, d, _ in Inventory().tools("pickaxe")):
                raise skills.ToolMissing("pickaxe", 0)
            # The previous segment may have broken into lava: cover it before digging on.
            skills.contain_lava(skills.Context(self.mem, self.policy_cache, s["dimension"], self.blacklist))
        self.reflexes()

    # -- failure policy
    def failed(self, name, err):
        """Failures wait for the situation to change (retry.py), not for a timer: same state → growing backstop."""
        now = time.time()
        cause = retry.cause_of(err)
        if isinstance(err, NotAvailable) and self.seg_name:
            # "No water within 48 blocks", "no sheep in sight": walking 30 blocks makes it a new state, so the
            # state-aware retry never gives up. Count it per route segment instead — a Nether-kit slice spent 8
            # minutes looking for water and sheep that were not in that biome at all.
            key = (self.seg_name, name)
            self.seg_misses[key] = self.seg_misses.get(key, 0) + 1
        n, wait, worth_logging = self.retry.failed(name, cause, str(err), self.sig, now, self.place)
        if cause == "tool" and n < 3:
            self.retry.cap(name, 0, now)   # the tool goal runs next round; retry right after it
            wait = 0
        if worth_logging:
            log(f"{'~~' if isinstance(err, NotAvailable) else '!!'} {name}: {err} "
                f"({cause}, ×{n} here; retry on change or in {wait}s)")

    def ready(self, name):
        return self.retry.ready(name, self.sig, time.time(), self.place)

    def escalate(self, kind, what):
        """A macro problem (stalled progress, every rescue exhausted), not a single failure: one `?? STALL` line per
        kind per 20 min — supervise.sh wakes Claude on it."""
        now = time.time()
        if now - self.escalated.get(kind, 0) < 1200:
            return
        self.escalated[kind] = now
        log(f"?? STALL {kind}: {what}")

    # -- execution of one plan step
    def execute(self, ctx, step, night):
        log(f"   → {step}")
        key = f"{step.kind}:{step.token}"
        try:
            self._execute(ctx, step, night)
        except GameUnreachable:
            raise
        except api.Interrupted:
            raise   # stopped for a danger: no statistics
        except (McError, skills.ToolMissing) as e:
            # A path failure isn't the skill's fault: hunts that "fail" for lack of a route mustn't look hard.
            self.mem.record_outcome(f"nav:{step.kind}" if retry.cause_of(e) == "nav" else key, False)
            self.fail_sig[key] = self.coarse
            raise
        self.mem.record_outcome(key, True)

    def _execute(self, ctx, step, night):
        if step.kind == "craft":
            skills.craft(ctx, step.token, step.detail["times"])
        elif step.kind == "smelt":
            machine = self.nearest_machine("smelting")
            if machine and step.count >= 8:
                # Drop the batch into the auto smelter and keep working; the output counts as pending.
                skills.load_smelter(ctx, machine, step.detail["input"], step.count, step.detail["fuel"],
                                    mid(step.token))
            elif step.count >= skills.ASYNC_SMELT_MIN:
                # Multitask: load a furnace and go on with other steps; collect when the estimate says it's done.
                skills.start_smelt_job(ctx, mid(step.token), step.detail["input"], step.count, step.detail["fuel"])
            else:
                skills.smelt(ctx, mid(step.token), step.detail["input"], step.count, step.detail["fuel"])
        elif step.kind == "mine":
            # Group tokens (stone, coal) are counted as groups, so any variant mined counts toward the step.
            skills.mine(ctx, step.token, step.count, step.detail["blocks"], step.detail["tier"],
                        step.detail.get("breaks"))
        elif step.kind == "room":
            {"tidy": skills.tidy_inventory, "deposit": skills.deposit}[step.token](ctx)
        elif step.kind == "seek":
            # Go to where one of these is: the nearest known, else look for one. Ore, animals, water and trees all
            # take this path — finding is one action, and not knowing where is what it costs, not a special case.
            kinds = step.detail["kinds"]
            if not self.go_find(ctx, snap_kinds=kinds):
                raise NotAvailable(f"could not find {bare(kinds[0])}")
        elif step.kind == "resume":
            # Finish what is already half done, where it is. The skill is the same one; only the place is given.
            pos = tuple(step.detail["pos"])
            if not nav.go_to(pos, self.policy_cache, range_=2, attempts=2):
                raise NotAvailable(f"can't get back to the unfinished {step.token} at {pos}")
            {"dig_in": skills.dig_in, "pod": skills.pod, "hut": skills.build_shelter}[step.token](ctx)
        elif step.kind == "gather":
            skills.chop(ctx, step.count)
        elif step.kind == "fill":
            from . import fluids
            fluids.fill_water_bucket(ctx)
        elif step.kind == "hunt" and "minecraft:blaze" in step.detail["types"]:
            # Blazes shoot fireballs and hover: bow from range, shield up, melee only when they come close.
            combat.fight_blaze(ctx, step.count)
        elif step.kind == "hunt":
            skills.hunt(ctx, step.token, step.count, step.detail["types"], night)

    def nearest_machine(self, tag, max_dist=64):
        s = api.get("/state")
        here = (s["blockX"], s["blockY"], s["blockZ"])
        options = [m for m in self.mem.machines(s["dimension"], tag) if math.dist(m["origin"], here) <= max_dist]
        return min(options, key=lambda m: math.dist(m["origin"], here), default=None)

    # -- one round
    def round(self):
        """One decision round. The intention stack is rebuilt from what acts and published at the end, whichever
        layer took the round — the screen shows the reason for the task, not only the task."""
        intent.clear()
        try:
            self._round()
        finally:
            intent.publish()

    def _round(self):
        self.reflexes()
        tape.begin()          # the world queries this round reads (decisions.jsonl, replayed offline by decide.py)
        snap = Snapshot()
        night = snap.night
        self.mem.observe_phase(night)
        self.policy_cache = self.policy(snap, night)
        ctx = skills.Context(self.mem, self.policy_cache, snap.dimension, self.blacklist)
        now = time.time()
        bans = sum(1 for exp in self.blacklist.values() if exp > now)
        self.sig = retry.signature(snap.feet, (s["id"] for s in snap.inv.slots), night, bans)
        # Success statistics reset only on a real change of scene (16-block area, new item kinds), not a few steps.
        self.coarse = retry.signature(snap.feet, (s["id"] for s in snap.inv.slots), night, 0, bin_size=16)
        # Where we are, coarsely: what "this cannot be done here" is counted against.
        self.place = retry.place_signature(snap.feet, night)
        self.snap_cache = snap

        self.track(snap)
        self.scan_resources(snap)
        # The only early return left: what kills inside one round (suffocation, drowning, a threat that arrives
        # before the next decision). Everything else about staying alive is a candidate in the pool below, priced
        # in the same seconds as mining and crafting — which is the only way "go home before dark" and "finish
        # this vein" can be compared at all.
        if self.survival(snap, ctx):
            return

        # Claude's override directives: above the pool (they can break any loop), below the life-saving layers.
        if self.follow_directive(ctx, snap):
            return

        # Everything else competes in one scored pool (priority.py): goals, maintenance, boost/background
        # directives, fallbacks.
        now = time.time()
        # The idle rule lives in the pool too: after IDLE_LIMIT, cooling fallbacks become candidates again.
        force = self.idle_since is not None and now - self.idle_since >= IDLE_LIMIT and \
            not (night and self.sheltered(snap))
        pool, filtered = self.candidates(ctx, snap, night, force=force)
        pick = priority.choose(pool, self.committed, held=self.assumptions_hold(snap, night),
                               here=(snap.state["x"], snap.state["y"], snap.state["z"]))
        tape.end(self, pick, pool, filtered, force)
        if pick is None:
            if night and self.sheltered(snap):
                # Waiting out the night sealed in / underground is the survival action itself, like sleeping.
                self.idle_since = None
                self.hold_log("night: sheltered, nothing safe to do; holding")
                intent.set("goal", "holding: sheltered for the night")
            else:
                self.idle_since = self.idle_since or now
                self.hold_log("nothing runnable; holding")
                intent.set("goal", "holding: nothing runnable")
            api.run({"type": "wait", "ticks": 100}, wait=15)
            return
        self.idle_since = None
        self.explain(pick, pool, filtered)
        intent.goal(pick, pool)
        self.committed = pick.name
        self.committed_assumptions = list(getattr(pick, "assumptions", ()))
        self.committed_pos = getattr(pick, "goes_to", None)
        bag.RESERVED = bag.RESERVED | pick.reserve   # the running plan's items too (a food-stock hunt threw its meat)
        # Failures cool down this candidate's key only (a goal's current step); everything else stays available.
        before = self.retry.entries.get(pick.key, {}).get("since")
        arbiter.BODY.submit("plan", lambda: self.attempt(pick.key, pick.run, cooldown=pick.cap), pick.name,
                            commit_s=pick.commitment_s, cost_rate=1.0, cost_s=pick.cost_s)
        try:
            arbiter.BODY.step()
        except api.CommitmentExpired as e:
            log(f"   {e}")
        after = self.retry.entries.get(pick.key, {}).get("since")
        if after is not None and after != before:
            self.recent_fail = (pick.name, time.time())

    SCAN_EVERY_S = 20
    SCAN_BLOCKS = {"tree": ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log"],
                   "water": ["water"], "lava": ["lava"], "iron": ["iron_ore", "deepslate_iron_ore"],
                   "coal": ["coal_ore", "deepslate_coal_ore"]}
    SCAN_MOBS = ("minecraft:sheep", "minecraft:cow", "minecraft:pig", "minecraft:chicken")

    def scan_resources(self, snap):
        """Map resources while travelling (80 % of a slice was travel): every 20 s note the nearest tree, water, lava,
        iron and coal in 48 blocks and the animals in sight, so goals choose known places instead of searching."""
        now = time.time()
        if now - getattr(self, "last_scan", 0) < self.SCAN_EVERY_S:
            return
        self.last_scan = now
        try:
            for kind, blocks in self.SCAN_BLOCKS.items():
                hits = find(blocks, radius=48, limit=1)
                if hits:
                    h = hits[0]
                    if kind == "lava":
                        self.mem.add_lava(h, snap.dimension)
                    else:
                        self.mem.note_resource(kind, (h["x"], h["y"], h["z"]), snap.dimension)
            for e in entities(48, list(self.SCAN_MOBS)):
                self.mem.add_sighting(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), snap.dimension)
        except McError:
            pass

    # -- the priority pool
    def candidates(self, ctx, snap, night, force=False):
        """Everything the pool may do now as priority.Candidates, and {name: why} for what was kept out. `force`
        lets cooling fallbacks in (the 15 s idle rule).

        Orchestration only. Refusing lives in pool.py (pure), scoring in priority.Candidate; each stage below
        collects one kind of candidate. Refusal texts are unchanged — the tape and the goldens compare them.
        """
        from . import pool as _pool, survival
        from . import skill as skill_kit
        pctx = self._pool_context(snap, night, force)
        out, filtered = [], {}
        self.last_refusals = []

        def offer(c):
            # Every candidate that names a skill gets that skill's own preconditions attached here, once, rather
            # than at each of the five places candidates are built. `light up` was offered with no torches
            # seventy times in twenty seconds because its check lived at one of those places and was cleared at
            # another; a rescue or a maintenance errand could have done the same and nobody would have noticed.
            if getattr(c, "precheck", None) is None and getattr(c, "runs", None):
                target, args = c.runs
                c.precheck = lambda t=target, a=args: skill_kit.can_run(t, *a)
            r = _pool.admit(c, pctx, priority.weight_for, route.allowed, retry.EXHAUSTED_AFTER)
            if r is None:
                out.append(c)
                return
            filtered[c.name] = str(r)
            self.last_refusals.append((c.name, r))

        self._rescue_candidates(ctx, snap, night, offer, filtered)
        self._maintenance_candidates(ctx, snap, night, offer, filtered)
        eligible = self._goal_candidates(ctx, snap, night, pctx, offer, filtered)
        self._directive_candidates(ctx, snap, night, offer, filtered)
        # Reservations cover every open main goal that can plan, not only the one picked: a down-weighted wheat
        # farm's seeds were thrown away three times in a minute.
        bag.RESERVED = set().union(*(bag.reserved_ids(plan, g.needs) for g, plan, _, _ in eligible
                                     if not g.background)) if eligible else set()
        self._fallback_candidates(ctx, snap, night, pctx, offer, out, filtered)
        return out, filtered

    def _pool_context(self, snap, night, force):
        from . import pool as _pool, scenarios
        recent = self.recent_fail
        staying = bool(recent and recent[0] == self.committed and time.time() - recent[1] < 90)
        segment = self.route_segment(snap) if route.current() else None
        if (segment["name"] if segment else None) != self.seg_name:
            self.seg_name = segment["name"] if segment else None
            self.seg_misses.clear()      # a new segment is a new place: everything is worth one more look
        return _pool.Context(
            segment=segment, seg_name=self.seg_name,
            seg_goals=set(segment["goals"]) if segment is not None else set(),
            bench_failing=scenarios.failing_goals() if segment is not None else set(),
            staying=staying, committed=self.committed, weights=priority.load(), ready=self.ready, force=force,
            seg_misses=self.seg_misses, seg_misses_limit=SEG_MISSES, retry=self.retry, sig=self.sig,
            night=night, night_capable=self.has_pickaxe(snap) and wearing_at_least(snap.inv, "iron"),
            snap=snap, has_pickaxe=self.has_pickaxe(snap), sightings=self.mem.sightings,
            way_into=self.way_into(snap), no_go=self.no_go(snap), sheltered=_shelter_near(snap, self.mem),
            place=self.place)

    def no_go(self, snap):
        """Circles no step may be planned into: what perception can see, at the reach it can hurt from.

        The complement of the blood tax. Walking past a skeleton costs health and that is a price the pool can pay;
        planning to stand in front of one is not a price, and no benefit should be able to buy it.
        """
        from . import perception, threat
        rows, _ids = perception.threats_seen()
        margin = float(threat.PLAYER["melee_reach"])
        return [(h[0], float(h[1]) + margin) for h in rows if h[3] in threat.MOBS]

    def way_into(self, snap):
        """Whether a route into another dimension is known, from memory alone (no world call: replays stay exact).
        The Nether can always be built into; the End needs the portal room remembered first."""
        def known(dim):
            if dim in (snap.dimension, nether.NETHER, "minecraft:overworld"):
                return None
            if dim == "minecraft:the_end" and not self.mem.sites("minecraft:overworld", kinds=["portal_room"]):
                return "no way into the End yet: the portal room comes first"
            return None
        return known

    def _maintenance_candidates(self, ctx, snap, night, offer, filtered):
        """Urgency curves decide how much each upkeep item matters right now."""
        from . import survival
        C = priority.Candidate
        # Health is the margin every future fight starts from (survival.fight_loss), so healing is worth whatever
        # that margin is worth. Regeneration needs food above 17 and a few quiet seconds; both are cheap, which is
        # why nothing ever chose to do it while the value was zero.
        hp = snap.get("health", 20)
        if hp < 20 and food_count(snap.inv):
            sstate = survival_state(snap, self.mem)
            saves = survival.benefit(sstate, {"hp": 20})
            if saves > 0:
                offer(C("heal up", 8, 20 * (20 - hp), lambda: self.heal(ctx, snap), kind="maintenance",
                        seconds=saves, cap=20, detail=f"regenerate {20 - hp:.0f} hp"))
        if not self.has_pickaxe(snap):
            offer(C("replace pickaxe", 12, 2000, lambda: self.replace_tool(ctx, "pickaxe"),
                    kind="maintenance", cap=30))     # no pickaxe: the benefit is due now, so no discount
        bag_delay = priority.bag_delay(snap.inv.used_slots())
        # What a full bag costs: the next stretch of mining produces nothing that can be kept.
        bag_worth = survival.bag_loss(survival_state(snap, self.mem))
        if bag_delay < priority.DISCOUNT_HORIZON_S:
            if not skills.store_plan(snap.inv.slots):
                filtered["deposit"] = "nothing worth storing"
            elif skills.can_store_here(ctx, local_only=night):
                _c = C("deposit", 3, 1200, lambda: skills.deposit(ctx, local_only=night), delay_s=bag_delay,
                        kind="maintenance", seconds=bag_worth)
                _c.runs = (skills.deposit, (ctx,))
                offer(_c)
            x, y, z = snap.feet
            throwable = skills.free_slots_plan(snap.inv.slots,
                                               need=max(0, snap.inv.used_slots() - (36 - skills.FREE_SLOTS_TARGET)))
            if not throwable:
                filtered["tidy"] = "nothing worth throwing away"
            elif skills.throw_direction(world.Region((x - 3, y - 1, z - 3), (x + 3, y + 2, z + 3)), snap.feet):
                _c = C("tidy", 3, 300, lambda: skills.tidy_inventory(ctx), delay_s=bag_delay,
                        kind="maintenance", cap=20, seconds=bag_worth)
                _c.runs = (skills.tidy_inventory, (ctx,))
                offer(_c)
            elif not (night and self.sheltered(snap)):   # never leave a night shelter just for the bag
                _c = C("open space", 3, 400, lambda: skills.move_to_open_space(ctx), delay_s=bag_delay,
                        kind="maintenance", cap=30, seconds=bag_worth)
                _c.runs = (skills.move_to_open_space, (ctx,))
                offer(_c)
            else:
                filtered["tidy"] = "no room to throw in this shelter; waits for day"
        if night:
            return
        dirty = [s for s in self.mem.sites(snap.dimension) if s.get("dirty")]
        if dirty:
            offer(C("repair", 2, 2000, lambda: skills.repair_site(ctx, dirty[0]), kind="maintenance"))
        ready_jobs = sorted((j for j in self.mem.jobs(snap.dimension)
                             if skills.job_ready(j) and math.dist(j["pos"], snap.feet) <= 96),
                            key=lambda j: math.dist(j["pos"], snap.feet))[:3]
        for job in ready_jobs:
            d = math.dist(job["pos"], snap.feet)
            # Being close does not make a job worth more; it makes it cheaper — and "close" means close to where
            # we are already going, not to where we stand.
            detour = priority.detour_s(d, here=snap.feet, there=tuple(job["pos"]), via=self.committed_pos)
            offer(C(f"collect {job['kind']} job", jobs.JOB_VALUE.get(job["kind"], 3),
                    int(detour * priority.TICKS_PER_S) + 200,
                    lambda job=job: jobs.collect(ctx, job),
                    key=f"job {job['id']}", kind="maintenance", cap=30,
                    detail=f"{job['kind']} at {tuple(job['pos'])}"))
        ready = [m for m in self.mem.machines(snap.dimension) if skills.pending_ready(m)]
        if ready:
            offer(C("collect machine", 4, 1200, lambda: skills.collect_machine(ctx, ready[0]), kind="maintenance"))

    def _goal_candidates(self, ctx, snap, night, pctx, offer, filtered):
        """Plan every open goal, gate its steps, refuse with a reason or offer one candidate. Returns the eligible
        (goal, plan, step, run) tuples for reservations."""
        from . import pool as _pool
        C = priority.Candidate
        open_goals = [g for g in goals(snap, self.mem) if not g.done()]
        cost_model = LiveCost(snap, self.blacklist, self.mem)
        # Two passes. Pricing every goal used to mean solving every goal — thirty layered descents a round, and
        # once "not knowing where" stopped being a dead end (seek columns) every goal became solvable, so the round
        # went from 57 s to 187 s. But `reach_cost` already prices every dimension in one sweep of the graph, and a
        # goal's cost is just its shortfall read off that table. So: price all of them from the table, then expand
        # only the few that could plausibly win. The rest never needed a plan — they were never going to be chosen.
        prices, vector = self.prices(snap, cost_model)
        ready_goals = []
        for g in open_goals:
            if not self.ready(g.name):
                filtered[g.name] = "cooling after a failure"
                continue
            # Feasibility before ranking. It is a cheap, pure check (the bag, memory), and a goal that cannot start
            # has no business taking a place in the shortlist — goals with no `needs` price at zero, so "defeat the
            # ender dragon" sat at the top of the list for free while the stone pickaxe that could actually be made
            # was pushed out of the six that get planned.
            why = g.feasible() if g.feasible else None
            if why:
                filtered[g.name] = why
                continue
            ready_goals.append(g)
        # Ranked by what they are WORTH, not by what they cost: a bed is dear and still the best thing to do. Both
        # halves come from tables (survival.benefit, reach_cost), so ranking thirty goals costs nothing; only the
        # few that could plausibly win are then solved for a plan.
        from . import survival as _sv
        from .survival import CONFIG as _PLAY
        sstate = survival_state(snap, self.mem)

        def rough_score(g):
            worth = _sv.benefit(sstate, g.effect) if g.effect else g.value * priority.SECONDS_PER_VALUE
            price = self.rough_cost(g, prices, vector)
            if not g.needs:
                # No requirement list means the price cannot be read off the graph — the work is in a skill. Rank
                # it by worth alone rather than as if it were free, or every action-shaped goal outranks every
                # goal that has to be built.
                price = 0.0 if g.dimension == snap.dimension else float(_PLAY["pool"]["portal_trip_s"])
            return float(worth) - price
        # Expand until there are enough choices, not a fixed number of them. A quota of six looked like a saving
        # until a night filtered all six ("gather waits for day") and the pool came down to one maintenance errand
        # — which is a queue, not a decision. Planning stops as soon as there is something to compare.
        # One pass: plan a goal, then immediately ask whether it can actually be used. Counting PLANS instead of
        # usable candidates was the trap — on a night when five goals plan fine and every one of them answers
        # "gather waits for day", the quota is spent and the pool comes down to a single maintenance errand.
        ranked_goals = sorted(ready_goals, key=rough_score, reverse=True)
        open_names = {g.name for g in open_goals}
        plans, eligible = {}, []
        for n, g in enumerate(ranked_goals):
            if len(eligible) >= MIN_CHOICES or n >= MAX_EXPAND:
                filtered[g.name] = "outranked before planning (there were already better things to compare)"
                continue
            try:
                plans[g.name] = self.plan_for(g, snap, cost_model)
            except Unplannable as e:
                filtered[g.name] = f"unplannable: {e}"
                continue
            plan = self.with_preparation(g, plans[g.name], snap, cost_model)
            gate = lambda s, plan=plan, g=g: _pool.gate_step(s, g, plan, pctx, UNDERGROUND_KINDS,
                                                             lookahead.escape_ready, bare)
            runnables = [s for s in plan if runnable(s, snap.inv)]
            step, reason = _pool.pick_step(plan, runnables, self.prep_tokens.get(g.name, ()), gate)
            if reason is None:
                reason = _pool.goal_reason(g, plan, pctx, nether_kit_missing, nether.NETHER, NETHER_MOBS)
            run = self.goal_runner(ctx, g, plan, step, night)
            portal_s = 0.0
            if reason is None and g.dimension != snap.dimension:
                # The work is in another dimension: going through the portal is this goal's next step, and the
                # round trip is part of what this goal costs. It used to be free, so anything on the far side
                # looked as cheap as the thing under our feet.
                run = (lambda dim=g.dimension: nether.use_portal(ctx, dim))
                step = None
                from .survival import CONFIG as _PLAY
                portal_s = float(_PLAY["pool"]["portal_trip_s"])
            if run is None:
                reason = reason or "nothing to do"
            if reason:
                filtered[g.name] = reason
                continue
            eligible.append((g, plan, step, run))
        # Supersession, decided on what is actually runnable this round. Deciding it on "the better goal has a plan"
        # kept stone tools out for a whole session while the iron ones were refused every round.
        plans_by_name = {g.name: plan for g, plan, _, _ in eligible}
        kept = []
        for item in eligible:
            g, plan = item[0], item[1]
            better = plans_by_name.get(g.superseded_by) if g.superseded_by else None
            if better is not None and not _needed_by(plan, better):
                filtered.setdefault(g.name, f"superseded by {g.superseded_by}")
            else:
                kept.append(item)
        eligible = kept
        from . import survival
        sstate = survival_state(snap, self.mem)
        # Every goal's worth in seconds first, so "what this one unlocks" can be counted in the same unit as
        # "what this one saves" instead of as a multiplier nobody could price.
        worth = {}
        for g, _plan, _step, _run in eligible:
            saves = survival.benefit(sstate, g.effect) if g.effect else None
            if g.name == "recover items after death":
                # Worth what is lying there, priced like anything else: an empty corpse is worth nothing and a
                # full one is worth the hours in it. A flat death cost said both were 240 seconds, so the pile
                # lost to a stone sword and despawned.
                from .memory import worth_of
                dead = self.mem.recent_death(snap.dimension)
                saves = worth_of(dead.get("carried") if dead else [], prices)
            worth[g.name] = float(saves if saves is not None else g.value * priority.SECONDS_PER_VALUE)

        # How many eligible goals need each piece of work: the denominator of the sharing above.
        shared = {}
        for _g, _plan, _s, _r in eligible:
            for st in _plan:
                shared[(st.kind, st.token)] = shared.get((st.kind, st.token), 0) + 1
        # What the world's TERMINAL goods are worth right now, per unit: shelter, a bed, food, light, a sword, a
        # pickaxe (survival.END_DIMS). Value is computed from these downward, so a saving on a shared intermediate
        # is counted once instead of once per link of the chain that wants it.
        ends = survival.end_worths(sstate)
        from .solve import credits
        # The cheapest route to each of those goods, once for the whole round. Every candidate's future value is
        # then a walk down routes already computed instead of a solve and a relaxation of its own — a round is a
        # tenth of a second, and a second search per candidate does not fit in it.
        table, _vec = self.action_table(snap, cost_model)
        credit = credits(table, vector, [d for d, w in ends.items() if w > 0])
        for g, plan, step, run in eligible:
            stat = f"{step.kind}:{step.token}" if step else None
            changed = stat in self.fail_sig and self.fail_sig[stat] != self.coarse
            share = priority.BACKGROUND if g.background else 1.0
            # What this goal is worth to everything after it: how much cheaper the terminal goods are once its
            # plan has run. Product, facility placed, room made, tool crafted — all of it is the same price
            # difference, so none of it has to be classified (and none can be classified wrongly, which is what
            # the two earlier functions kept doing to each other).
            unlocks_s = [(self.future_worth(plan, prices, ends, credit), 1.0)]
            unlocks_s = [(v, p) for v, p in unlocks_s if v > 0]
            # Shared work is paid for once. A step another eligible goal also needs (the same iron, the same
            # trip) is split between them, so "mine iron" does not look twice as expensive as it is just because
            # two goals want it. This is the横向 saving the pool could never see.
            own = sum(s.est / max(1, shared.get((s.kind, s.token), 1)) for s in plan)
            cand = C(g.name, g.value * share, int(own) + 200, run,
                     key=step_key(g, step) if step else g.name,
                     seconds=worth[g.name], share=share,
                     risk_s=sum(cost_model.risk_s(s, s.est) for s in plan),
                     delay_s=self.goal_delay(g, snap, sstate),
                     unlocks=unlocks_s,
                     success=priority.effective_success(self.mem.success_rate(stat), changed) if stat else 1.0,
                     detail=str(step) if step else "finish", reserve=bag.reserved_ids(plan, g.needs),
                     commitment_s=priority.step_commitment(step.est, step.count) if step else None,
                     assumptions=self.assumptions_for(g, plan, step, snap))
            cand.goes_to = next((tuple(s.detail["pos"]) for s in plan if s.detail.get("pos")), None)
            cand.dimension_s = portal_s
            cand.off_route_s = getattr(cand, "off_route_s", 0.0)
            # Only crafting left, everything in the bag: no travel, so no waiting for a later route segment.
            cand.craft_only = bool(plan) and all(s.kind == "craft" for s in plan) and \
                g.dimension in (None, snap.dimension)
            offer(cand)
        return eligible

    def _directive_candidates(self, ctx, snap, night, offer, filtered):
        """Claude's boost / background directives (override ones ran before the pool)."""
        C = priority.Candidate
        cost_model = LiveCost(snap, self.blacklist, self.mem)
        for d in directives.runnable(directives.load()):
            mode = d.get("mode", "override")
            if mode == "override" or d["kind"] != "goal":
                continue
            name = f"directive {d['id']}"
            try:
                plan = Planner.from_inventory(snap.inv, cost_model, self.mem.pending_outputs(snap.dimension)) \
                    .plan([tuple(n) for n in d.get("needs", [])])
            except Unplannable as e:
                filtered[name] = f"unplannable: {e}"
                continue
            if not plan:
                directives.save(directives.mark(directives.load(), d["id"], done=True)[0])
                continue
            step = next((s for s in plan if runnable(s, snap.inv)), None)
            if step is None:
                filtered[name] = "no runnable step"
                continue
            base = 10 * min(priority.CLAMP[1], float(d.get("x", 3))) if mode == "boost" else 1.0
            offer(C(name, base, sum(s.est for s in plan) + 200, lambda step=step: self.execute(ctx, step, night),
                    key=f"{name}/{step.kind}:{step.token}", kind="directive", detail=str(step)))

    def _fallback_candidates(self, ctx, snap, night, pctx, offer, pool_out, filtered):
        """Always-available low-value work, so the pool is rarely empty; wandering fallbacks held back while a goal
        retries on the spot come back when nothing else can run here."""
        from . import pool as _pool
        C = priority.Candidate
        from . import skill as skillkit_
        held_back = []
        for fname, fn, allowed, runs in self.fallbacks(ctx, snap, night):
            if allowed:
                base, cost = FALLBACK_BASE[fname]
                c = C(fname, base, cost, fn, kind="fallback", cap=20)
                c.runs = runs
                if runs:
                    target, args = runs
                    # A veto, not a price. Pricing an unmet precondition only makes sense when the plan will go
                    # and satisfy it — which is true of goals (the solver puts the step in the plan) and false of
                    # fallbacks, whose `run` calls one skill and nothing else. Charging `light up` thirty-three
                    # seconds for torches it would never make put it in the pool at −47.8 s, where it was chosen
                    # and failed "no torches to spare" seventy times in twenty seconds. Making the torches is
                    # `torches (≥8)`; it is in the pool on its own, and it can win on its own.
                    c.precheck = lambda t=target, a=args: skillkit_.can_run(t, *a)
                offer(c)
                if pctx.staying and fname in _pool.WANDERING and fname in filtered:
                    held_back.append(c)
        if not pool_out and held_back:
            for c in held_back:
                if priority.weight_for(c.name, pctx.weights)[1]:
                    continue        # ask the ban itself, not the filter reason (a banned fallback came back 170×)
                filtered.pop(c.name, None)
                pool_out.append(c)

    def go_find(self, ctx, snap_kinds):
        """Walk to the nearest one of these; if none is in sight, go look. Records how far it turned out to be, so
        the next estimate is measured rather than assumed (memory.search_distance)."""
        from .world import entities, find
        here = nav.feet_now()
        blocks = [k for k in snap_kinds if not k.startswith("minecraft:") or "_" in k.split(":")[-1]]
        hits = find(snap_kinds, radius=48, limit=1) or (find(blocks, radius=48, limit=1) if blocks else [])
        from . import actions as act
        note_kind = next((k for k, marker in act.LiveCosts.MAPPED.items()
                          if any(marker in x for x in snap_kinds)), None)
        if hits:
            spot = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
        else:
            mobs = entities(64, list(snap_kinds))
            if mobs:
                spot = (round(mobs[0]["x"]), round(mobs[0]["y"]), round(mobs[0]["z"]))
            else:
                # Nothing in sight where the map said there would be something: the note is wrong, and saying so
                # now is what stops the same walk being priced again next round.
                if note_kind:
                    for stale in list(self.mem.resources(note_kind, ctx.dimension)):
                        if math.dist(stale, here) <= 48:
                            self.mem.confirm(note_kind, stale, ctx.dimension, found=False)
                return self.explore(ctx) or False
        if not nav.go_to(spot, self.policy_cache, range_=3, attempts=2):
            return False      # could not get there: the note may still be true, so it stands
        if note_kind:
            self.mem.confirm(note_kind, spot, ctx.dimension, found=True)
        self.mem.note_search(snap_kinds[0], math.dist(spot, here))
        return True

    def unmet_cost(self, needs, snap, ctx):
        """Seconds to satisfy what this work requires but the world does not yet provide. Zero when it is all there.

        The same requirement graph the goals are planned with (`solve.reach_cost`), asked about a skill's `needs`.
        This is what turns "cannot run" into a price: the pool sees the torches as part of the cost of lighting up,
        compares it with everything else, and picks.
        """
        if not needs:
            return 0.0
        from . import actions as act
        from .solve import reach_cost
        table, vector = self.action_table(snap, LiveCost(snap, self.blacklist, self.mem))
        price = reach_cost(table, vector)
        total = 0.0
        for dim, want in needs.items():
            short = float(want) - float(vector.get(dim, 0))
            if short <= 0:
                continue
            per = price.get(dim)
            if per is None or per == float("inf"):
                return float("inf")       # nothing in this world provides it: the candidate prices itself out
            total += per * short
        return total

    def route_segment(self, snap):
        """The active route segment; logs splits when a segment completes and escalates one over its budget."""
        steps = route.ROUTES.get(route.current())
        if not steps:
            return None
        seg = route.active_segment(steps, snap.inv, self.mem, snap.dimension)
        state = self.mem.data.setdefault("route_state", {})
        seg = route.with_hysteresis(steps, seg, state, snap.inv)
        name = seg["name"] if seg else "finished"
        now = time.time()
        if state.get("segment") != name:
            if state.get("segment"):
                log(f"route: segment '{state['segment']}' done in {int(now - state.get('since', now)) // 60} min")
            state.update(segment=name, since=now)
            self.mem.save()
            if seg:
                log(f"route: now '{name}' (budget {seg['budget'] // 60} min; goals: {', '.join(sorted(seg['goals']))})")
        elif seg and now - state.get("since", now) > seg["budget"]:
            self.escalate("route", f"segment '{name}' over its {seg['budget'] // 60} min budget")
        return seg

    def prices(self, snap, cost_model):
        """({dimension: seconds per unit}, world vector) for this round, computed once and shared by every goal.

        One sweep of the requirement graph prices everything in it, so a goal's cost is a lookup, not a search. The
        table updates itself: its inputs are the action costs, and those already carry what has been measured
        (walk distances, learned durations, search distances).
        """
        key = (id(snap), id(cost_model))
        if getattr(self, "_prices_key", None) != key:
            from .solve import reach_cost
            table, vector = self.action_table(snap, cost_model)
            self._prices_key = key
            self._prices = (reach_cost(table, vector), vector)
        return self._prices

    def rough_cost(self, g, prices, vector):
        """Seconds to finish this goal, read off the price table. No search: this is the ordering, not the plan."""
        from . import actions as act
        total = 0.0
        for dim, want in act.target_of(g.needs).items():
            short = float(want) - float(vector.get(dim, 0))
            if short <= 0:
                continue
            per = prices.get(dim)
            if per is None or per == float("inf"):
                return float("inf")
            total += per * short
        return total

    def future_worth(self, plan, prices, ends, credit):
        """Seconds this plan saves everything after it (priority.future_value), read off the round's credit table.

        The honest question — what do the terminal goods cost once this plan has run — needs a second solve and a
        second relaxation per candidate. `credits` answers it from the route tree the round already built: for each
        end, how many of its seconds pass through something this plan makes. What the plan makes is read off its
        steps, which exist because the plan was solved to be RUN; nothing is solved in order to be priced.

        Per end the biggest single credit is taken, not the sum: the things a plan makes sit on top of one another
        along the same route (logs, then planks, then a bed), and adding them charges one saving once per link —
        the mistake that put an enchanting table at two hundred thousand seconds. The biggest is a lower bound,
        and shy is the safe direction.
        """
        if not plan or not ends:
            return 0.0
        made = {s.token for s in plan if s.token}
        after = dict(prices)
        for dim, rows in credit.items():
            saved = max([v for d, v in rows.items() if d in made] or [0.0])
            if saved:
                after[dim] = max(0.0, prices.get(dim, float("inf")) - saved)
        return priority.future_value(prices, after, ends)

    def solve_steps(self, needs, snap, cost_model):
        """`needs` → executable steps, through the one solver. Raises Unplannable when nothing reaches them."""
        from . import actions as act
        from .solve import Unsolvable, solve
        target = act.target_of(needs)
        if not target:
            return []
        table, vector = self.action_table(snap, cost_model)
        try:
            found = solve(table, vector, target)
        except Unsolvable as e:
            raise Unplannable(str(e))
        return [act.to_step(a, n) for a, n in found.steps()]

    def plan_for(self, g, snap, cost_model):
        """Plans are reused for PLAN_CACHE_S while the bag is unchanged: 27 goals × world queries every round was
        most of a round's HTTP traffic."""
        contents = tuple(sorted((s["id"], s.get("count", 1)) for s in snap.inv.slots))
        hit = self.plan_cache.get(g.name)
        if hit and hit[1] == contents and time.time() - hit[0] < PLAN_CACHE_S:
            return hit[2]
        plan = self.solve_steps(g.needs, snap, cost_model)
        self.plan_cache[g.name] = (time.time(), contents, plan)
        return plan

    def action_table(self, snap, cost_model):
        """(columns, state vector) for this round, built once. The table depends on where we are — a place the
        world does not offer has no travel column — so it is rebuilt when the round is, and not more often."""
        from . import actions as act
        key = (id(snap), id(cost_model))
        if getattr(self, "_table_key", None) != key:
            costs = act.LiveCosts(cost_model)
            vector = act.state_of(snap, self.mem)
            self._table_key = key
            self._table = (act.table(costs, vector), vector)
        return self._table

    def goal_runner(self, ctx, goal, plan, step, night):
        if plan:
            return lambda: self.execute(ctx, step, night)
        if goal.action:
            return lambda: goal.action(ctx)
        if goal.build:
            home = self.mem.home()
            near = tuple(home["pos"]) if home and goal.build != "nether_portal" else nav.feet_now()
            return lambda: skills.build_blueprint(ctx, goal.build, near)
        return goal.after

    def goal_delay(self, g, snap, sstate):
        """Seconds until this goal's benefit falls due. The discount replaces the urgency multiplier: a bed is not
        "worth more at dusk", it is worth the same and paid sooner, which is a thing the score can express."""
        if g.effect and ("bed" in g.effect or "sheltered" in g.effect):
            return priority.dusk_delay(snap.ticks_until_dusk, snap.night)
        if g.effect and "food_items" in g.effect:
            return priority.food_delay(snap.get("food", 20), food_count(snap.inv))
        if g.effect and "pickaxe" in g.effect:
            return priority.tool_delay(self.pickaxe_left(snap))
        if g.effect and ("sword" in g.effect or "armor" in g.effect or "shield" in g.effect):
            return 0.0      # a fight can happen at any moment: combat gear pays the moment it is carried
        return priority.dusk_delay(snap.ticks_until_dusk, snap.night) if g.effect else 0.0

    def assumptions_for(self, g, plan, step, snap):
        """What the world must still look like for this plan to remain the right one. Checked every round; when one
        stops holding, the commitment is released and the goal re-planned (priority.choose's `held`).

        The fight planner has had this since it existed — a plan is a function of a state, so it is only valid while
        that state holds. Ordinary play had a 60-second timer instead, which meant the ore could be gone and the sun
        could set and the agent would keep walking toward both.
        """
        out = [("day", snap.night), ("premise", self.premise(snap))]
        if step is not None:
            out.append(("step", f"{step.kind}:{step.token}"))
            if step.kind in ("mine", "gather", "hunt", "seek"):
                out.append(("segment", self.seg_name))
        return out

    def pickaxe_left(self, snap):
        """Remaining durability fraction of the best pickaxe (0 without one)."""
        best = 0.0
        for _, d, item in snap.inv.tools("pickaxe"):
            full = PICK_MAX.get(bare(item).split("_")[0], 250)
            best = max(best, d / full)
        return best

    def hp_tax_rate(self, snap):
        """Health per second the surroundings are charging us right now. The one number a plan's premise rests on."""
        from . import perception, threat
        rows, _ids = perception.threats_seen()
        if not rows and perception.hurt_rate() <= 0:
            return 0.0
        inv = snap.inv
        prot = threat.protection(snap.get("armor", 0), inv.offhand() == "minecraft:shield")
        here = (snap.state["x"], snap.state["y"], snap.state["z"])
        # The larger of what the model expects and what the health bar is actually doing. The model prices a
        # skeleton by reach and dps; a skeleton that aims well outdoes it, and the difference was a death.
        return max(threat.pressure(here, rows, prot), perception.hurt_rate())

    def premise(self, snap):
        """What the world charges us, coarsely. A plan stays valid while this holds.

        Three facts, all binned: the tax rate, how many threats can actually reach us, and how much health we have
        left. Health belongs here because taking an arrow IS a change in the price — without it a plan made at full
        health stayed "valid" all the way down to zero, and the agent was shot dead mid-task.

        Binned, and only over what can arrive. Comparing the SET of threats releases the commitment every time
        anything crosses the edge of perception, and still calls a mob two blocks closer "the same".
        """
        from . import perception, threat
        rows, _ids = perception.threats_seen()
        here = (snap.state["x"], snap.state["y"], snap.state["z"])
        from .survival import CONFIG as _PLAY
        tax_bin, health_bin = _PLAY["pool"]["tax_bin"], _PLAY["pool"]["health_bin"]
        tax = round(self.hp_tax_rate(snap) / tax_bin) * tax_bin
        coming = [h for h in rows if h[3] in threat.MOBS and threat.arrival(here, h) != float("inf")]
        health = int(float(snap.get("health", 20)) // health_bin)
        return (tax, len(coming), health)

    def assumptions_hold(self, snap, night):
        """Do the premises of the committed plan still hold? Kept here because it needs the round's snapshot, and
        checked before `choose` so a stale commitment cannot win on hysteresis.

        Facts, not timers. A goal committed to in daylight is released by nightfall; one committed to in a segment
        is released when the route moves on; one whose step has since failed in this state is released at once.
        """
        if not self.committed or not self.committed_assumptions:
            return True
        for kind, value in self.committed_assumptions:
            if kind == "day" and value != night:
                self.hold_log(f"commitment released: {'night fell' if night else 'day broke'}")
                return False
            if kind == "segment" and value != self.seg_name:
                self.hold_log(f"commitment released: route moved to {self.seg_name}")
                return False
            if kind == "step" and self.fail_sig.get(value) == self.coarse:
                self.hold_log(f"commitment released: {value} just failed here")
                return False
            if kind == "premise":
                now_premise = self.premise(snap)
                if now_premise != value:
                    self.hold_log(f"commitment released: the world charges differently now {value} → {now_premise}")
                    return False
        return True

    def stalled_seconds(self):
        """Seconds since the bag/equipment last changed (the committed task's persistence decays with it)."""
        if not self.history:
            return 0.0
        latest = self.history[-1][2]
        for t, _, sig in reversed(self.history):
            if sig != latest:
                return time.time() - t
        return time.time() - self.history[0][0]

    def explain(self, pick, pool, filtered):
        """Why this pick: the top-5 score breakdown and what was filtered, logged when the choice changes and
        appended to ranking.jsonl for the review (and for Claude's priorities.json tuning)."""
        choice = (pick.name, pick.key)
        if choice == self.last_choice[0] and time.time() - self.last_choice[1] <= 120:
            return
        self.last_choice = (choice, time.time())
        ranked = sorted(pool, key=lambda c: c.score, reverse=True)
        log(f"=== {pick.name} | {pick.kind} | next: {pick.detail or pick.key} | score {pick.score:.5f}")
        for c in ranked[:5]:
            api.detail("   rank " + c.explain())
        if filtered:
            # Say how many were cut, or the line lies: "nether kit" sat at position 11 and looked like it had
            # vanished from the pool entirely, which sent a whole debugging session down the wrong path.
            # 40, not 10: a round filtered 43 goals and the ten shown never included the one being debugged.
            shown = list(filtered.items())[:40]
            more = len(filtered) - len(shown)
            api.detail("   filtered: " + "; ".join(f"{n} ({r})" for n, r in shown)
                + (f"; … and {more} more" if more > 0 else ""))
        try:
            with open(RANKING_FILE, "a") as f:
                f.write(json.dumps({"t": int(time.time()), "pick": pick.name,
                                    "top": [[c.name, round(c.score, 6), c.base] for c in ranked[:5]],
                                    "filtered": filtered}) + "\n")
        except OSError:
            pass

    def hold_log(self, text):
        if time.time() - self.last_hold_log > 60:
            self.last_hold_log = time.time()
            log(text)

    def with_preparation(self, goal, plan, snap, cost_model):
        """Look-ahead (lookahead.py): simulate the plan and re-plan with whatever it would be missing on the way —
        carried stations, a shelter kit if it runs into the night, a spare pickaxe, food."""
        if not plan or goal.background:
            return plan
        site = self.mem.nearest_site(snap.feet, snap.dimension, kinds=["home", "shelter"])
        eta = travel_ticks(snap.feet, site["pos"]) if site else None
        extra = lookahead.prepare(plan, snap.inv, snap.time, snap.inv.count("bed") > 0,
                                  blueprints.materials(blueprints.SHELTER), eta)
        extra = [e for e in extra if e[0] not in {n[0] for n in goal.needs}]
        if not extra:
            return plan
        try:
            # The same solver the goal was planned with. There used to be two planners here — the solver for the
            # goal, the old recursive descent for its preparation — so a goal could plan and its preparation
            # could not, and the round died inside `planner.need` on a requirement the solver had introduced.
            prepared = self.solve_steps(goal.needs + extra, snap, cost_model)
        except Unplannable:
            return plan
        if prepared and prepared[0].key() != plan[0].key():
            api.detail(f"   look-ahead for {goal.name}: bring {lookahead.describe(extra)} first")
        # Remember which tokens are only here because of the look-ahead. A gated goal must not look runnable just
        # because its preparation can run: a food goal whose hunt was gated kept crafting torches instead, and the
        # kit sat at food 0/6 for a whole slice while "making progress".
        self.prep_tokens[goal.name] = {e[1] if e[0] == "tool" else e[0] for e in extra}
        return prepared

    # -- safety layer: a priority mode ahead of goals (Mindcraft/Baritone style); owns dusk and night
    def sheltered(self, snap):
        if snap.get("skyLight", 15) <= COVERED_SKY or skills.enclosed():
            return True
        feet = list(snap.feet)
        return any(feet in s.get("interior", []) for s in self.mem.sites(snap.dimension))

    def safety(self, snap, ctx):
        """Returns True when it used the round.
        Dusk: without a carried bed, head for the nearest site early enough to arrive before dark (travel estimate
        + 75 s margin). Night: sleep when a bed is at hand; underground or sealed in counts as safe (goals keep
        running underground steps); exposed → dig in, else wall in, else ask the brain (`??`)."""
        carried_bed = snap.inv.count("bed") > 0
        if snap.dimension != "minecraft:overworld":
            return False   # no day/night there, and beds explode in the Nether: no sleeping, no night shelter
        if not snap.night:
            if carried_bed:
                return False   # a carried bed makes any spot a bedroom
            if snap.get("skyLight", 15) <= COVERED_SKY:
                return False   # underground already counts as sheltered: keep working, no trek, no hut
            dusk = snap.ticks_until_dusk
            site = self.mem.nearest_site(snap.feet, snap.dimension, kinds=["home", "shelter"])
            if site is not None:
                eta = travel_ticks(snap.feet, site["pos"])
                if math.dist(snap.feet, site["pos"]) <= 24 or dusk >= eta + 1500:
                    return False
                # Trek or build? A hut takes skills.build_shelter's expected time (measured once it has been
                # built a few times); the trek is only worth it when it isn't much longer than that.
                can_build = not skills.materials_missing(blueprints.SHELTER)
                build_ticks = skillkit.expected(skills.build_shelter, ctx) * 20 if can_build else math.inf
                worth_trek = eta <= max(FAR_SITE_TICKS, 1.5 * build_ticks)
                if dusk >= eta + 200 and worth_trek and self.ready("return to shelter"):
                    intent.say("safety", f"dusk in {dusk // 20}s, {site['name']} ~{eta // 20}s away → heading back")

                    def go_back():
                        if not nav.go_to(tuple(site["pos"]), self.policy(snap, False), range_=4):
                            raise NotAvailable(f"{site['name']} not reachable")

                    self.attempt("return to shelter", go_back)
                    return True
            # No site close enough: put up a hut here while it's still light. It becomes the forward base for
            # sleeping and storage in this work area, instead of a long trek home every evening.
            if self.has_pickaxe(snap):
                return False   # with a pickaxe the night shelter is a tunnel down (see below): no hut hunting at dusk
            if dusk < 3000 and self.ready("build shelter"):
                intent.say("safety", f"dusk in {dusk // 20}s and no shelter within {FAR_SITE_TICKS // 20}s → building a forward base here")
                self.attempt("build shelter", lambda: skills.build_shelter(ctx))
                return True
            return False

        if self.ready("sleep") and (carried_bed or find(BASE_MARKERS["bed"], radius=48, limit=1)):
            try:
                skills.sleep(ctx, self.policy(snap, True))
                return True
            except McError as e:
                self.failed("sleep", e)
        if self.sheltered(snap):
            return False
        hut = self.mem.nearest_site(snap.feet, snap.dimension, kinds=["shelter"])
        if hut and hut.get("interior") and math.dist(snap.feet, hut["pos"]) <= 40 and self.ready("enter shelter"):
            cell = tuple(hut["interior"][0])

            def enter():
                if not nav.go_to(cell, self.policy(snap, True), range_=0.5, attempts=2):
                    raise NotAvailable(f"{hut['name']} not reachable")

            intent.say("safety", f"night: going into {hut['name']}")
            self.attempt("enter shelter", enter)
            return True
        # Standing on the shore with the body box overlapping a water block reads inWater too: only swimming counts
        # (that flicker made reach_land "succeed" instantly and loop three times a second).
        if skills.swimming(snap.state) and self.ready("reach land"):   # same test as reach_land's done
            # Nothing can be dug or walled in while swimming: get onto land first, shelter next round.
            log("night in the water: swimming to land first")
            self.attempt("reach land", lambda: skills.reach_land(ctx), cooldown=15)
            return True
        if self.has_pickaxe(snap) and self.ready("descend"):
            # One robust shelter: underground (sky light 0) counts as sheltered. travel digs a staircase down wherever
            # it can, where burrow/dig-in/pod each needed special terrain and often left openings.
            x, y, z = snap.feet
            # Wide search: at a shore the ground right below is wet; solid rock a few blocks inland still works.
            target = skills.underground_target(world.Region((x - 9, y - 8, z - 9), (x + 9, y + 1, z + 9)), snap.feet,
                                               radius=8)

            def descend():
                if target is None:
                    raise NotAvailable("no solid ground to tunnel into here")
                intent.say("safety", f"night: exposed → tunnelling underground to {target}")
                nav.go_to(target, self.policy(snap, True), range_=0.8, attempts=1)
                # Done = the same test that triggers sheltering (trigger and done must agree, or it re-digs all night).
                if not self.sheltered(Snapshot()):
                    # The staircase travel dug lets sky light down: dig in from here and seal overhead.
                    skills.dig_in(ctx)
                if not self.sheltered(Snapshot()):
                    raise NotAvailable(f"still exposed after tunnelling toward {target} and digging in")

            self.attempt("descend", descend, cooldown=30)
            return True
        if not self.ready("shelter"):
            return False
        # Choose the place before the method: on a peak, a pillar or open ground nothing works in place, so walk
        # to the nearest spot where burrowing, digging in or walling in can actually finish.
        x, y, z = snap.feet
        spot = skills.find_shelter_spot(world.Region((x - 10, y - 5, z - 10), (x + 10, y + 4, z + 10)), snap.feet,
                                        protected=self.policy_cache.protected)
        if spot is not None and spot[0] != snap.feet:
            intent.say("safety", f"night: sheltering by {spot[1]} at {spot[0]} (from {snap.feet})")
            nav.go_to(spot[0], self.policy(snap, True), range_=0.5, attempts=2)
        try:
            skills.dig_in(ctx)
        except McError as e:
            log(f"dig-in failed: {e} → burrowing into a hillside, else walling in")
            try:
                try:
                    skills.burrow(ctx)
                    return True
                except McError as e_burrow:
                    log(f"burrow failed: {e_burrow} → walling in instead")
                skills.pod(ctx)
            except McError as e2:
                # Retry soon: a half-built wall still leaves us exposed.
                log(f"?? exposed at night, wall-in failed: {e2}")
                self.failed("shelter", e2)
                self.retry.cap("shelter", 20, time.time())
        return True

    # -- Claude's directives (directives.py): above normal goals, below survival
    def follow_directive(self, ctx, snap):
        items = directives.load()
        # Only override directives run here; boost/background ones are candidates in the pool.
        d = next((x for x in directives.runnable(items) if x.get("mode", "override") == "override"), None)
        if d is None or not self.ready("directive " + d["id"]):
            return False
        name = "directive " + d["id"]
        try:
            if d["kind"] == "goal":
                needs = [tuple(n) for n in d.get("needs", [])]
                plan = Planner.from_inventory(snap.inv, LiveCost(snap, self.blacklist, self.mem),
                                              self.mem.pending_outputs(snap.dimension)).plan(needs)
                if not plan:
                    directives.save(directives.mark(items, d["id"], done=True)[0])
                    log(f"directive done: {directives.describe(d)}")
                    return True
                step = next((s for s in plan if runnable(s, snap.inv)), None)
                if step is None:
                    raise NotAvailable("no runnable step yet")
                log(f"=== directive {d['id']}: {directives.describe(d)} → {step}")
                self.execute(ctx, step, snap.night)
            elif d["kind"] == "goto":
                target = tuple(d["target"])
                log(f"=== directive {d['id']}: {directives.describe(d)}")
                if not nav.go_to(target, self.policy(snap, snap.night), range_=d.get("range", 2), attempts=2):
                    raise NotAvailable(f"{target} not reached")
                directives.save(directives.mark(directives.load(), d["id"], done=True)[0])
            elif d["kind"] == "skill":
                if d["name"] not in skillkit.REGISTRY:
                    raise McError(f"{d['name']} is not a registered skill (only contracted skills can be directed)")
                fn = skillkit.REGISTRY[d["name"]].runner
                log(f"=== directive {d['id']}: {directives.describe(d)}")
                fn(ctx, *d.get("args", []))
                directives.save(directives.mark(directives.load(), d["id"], done=True)[0])
            else:
                raise McError(f"unknown directive kind {d['kind']}")
        except PlayerTookControl:
            raise
        except (McError, skills.ToolMissing, AttributeError, TypeError) as e:
            items, gave_up = directives.mark(directives.load(), d["id"], failed_reason=str(e))
            directives.save(items)
            self.failed(name, e)
            if gave_up:
                log(f"?? directive gave up after {directives.MAX_FAILS} tries: {directives.describe(d)} ({e})")
            else:
                log(f"!! {name}: {e}")
        return True

    # -- survival mode: always-available rescues, ahead of safety and goals
    def track(self, snap):
        now = time.time()
        self.history = [h for h in self.history if now - h[0] <= STUCK_LIMIT + 30]
        self.history.append((now, snap.feet, progress_signature()))
        if now - self.last_track < 60:
            return
        # Macro progress, once a minute: which main goals are done, where we are (review.py reads track.jsonl).
        self.last_track = now
        done = {g.name for g in goals(snap, self.mem) if not g.background and g.done()}
        if done - self.done_seen:
            self.last_milestone = now
        self.done_seen = done
        try:
            with open(TRACK_FILE, "a") as f:
                bans = sum(1 for exp in self.blacklist.values() if exp > now)
                f.write(json.dumps({"t": int(now), "pos": list(snap.feet), "done": sorted(done), "bans": bans,
                                    "cooling": sorted(k for k, e in self.retry.entries.items() if e["until"] > now)[:12]})
                        + "\n")
        except OSError:
            pass
        if now - self.last_milestone > STALL_LIMIT:
            self.escalate("progress", f"no main goal completed in {int(now - self.last_milestone) // 60} min "
                                      f"(done: {len(done)}; at {snap.feet})")

    def stuck_in_place(self, snap):
        """Same block and no inventory change for STUCK_LIMIT seconds (sleeping / sealed in at night excluded)."""
        if snap.night and self.sheltered(snap):
            return False
        old = [h for h in self.history if time.time() - h[0] >= STUCK_LIMIT]
        if not old:
            return False
        ref = old[-1]
        return all(math.dist(h[1], ref[1]) < 2 and h[2] == ref[2] for h in self.history if h[0] >= ref[0])

    # What is lethal inside one round, and therefore cannot wait for the pool to choose. Everything else about
    # staying alive is a candidate priced in seconds like any other work — see `_rescue_candidates`.
    LETHAL_NOW = ("unbury", "find air")

    def survival(self, snap, ctx):
        """The hard floor: only what kills inside one decision round. Suffocation and drowning have no deliberation
        time — by the time a pool round has scored thirty candidates the air is gone.

        Everything else that used to live here (eat to heal, wall in, dig out, unstuck, leave the Nether) is now a
        candidate. It was never true that they must pre-empt: "walk out of the Nether" is worth a few hundred
        seconds and takes a few hundred, which is exactly the comparison the pool exists to make. As an if-chain
        above the pool their order was their priority, and nobody could say what any of them was worth.
        """
        s = snap.state
        rescues = []
        if skills.head_buried(s):
            rescues.append(("unbury", lambda: skills.unbury(ctx)))
        if s["inWater"] and s["air"] < 150 and skills.head_underwater(s):
            rescues.append(("find air", lambda: skills.find_air(ctx)))
        urgent = self.threat_now(snap)
        if urgent is not None:
            # Dead before the next round: no time to price anything.
            intent.say("threat", f"threat: {urgent.kind} — {urgent.why}")
            rescues.append((f"threat:{urgent.kind}", lambda: self.engage(urgent, s, ctx)))
        for name, fn in rescues:
            if self.ready(name):
                intent.say("survival", f"survival: {name}")
                api.INTERRUPT = None
                api.MODE = "survival"      # the perception thread doesn't interrupt the rescue itself
                try:
                    self.attempt(name, fn, cooldown=10)
                finally:
                    api.MODE = "normal"
                return True
        return False

    def _rescue_candidates(self, ctx, snap, night, offer, filtered):
        """Staying alive, priced in seconds and offered to the pool: healing, food, shelter, sleep, getting unstuck,
        leaving the Nether. They win when they are worth winning, and they say what they are worth."""
        from . import survival
        C = priority.Candidate
        s = snap.state
        sstate = survival_state(snap, self.mem)

        def worth(effect):
            return max(0.0, survival.benefit(sstate, effect))

        retreat = must_retreat(snap)
        if retreat:
            # Everything the Nether costs is ahead of us until we are out: price it as a day of the risk we carry.
            offer(C(f"retreat from the Nether ({retreat})", 0, 4000,
                    lambda: nether.use_portal(ctx, "minecraft:overworld"), kind="maintenance", cap=30,
                    seconds=survival.expected_loss(sstate), detail=retreat))
        if s["health"] < 20 and food_count(snap.inv):
            offer(C("heal up", 0, 20 * max(1, 20 - int(s["health"])), lambda: self.heal(ctx, snap),
                    kind="maintenance", cap=20, seconds=worth({"hp": 20}),
                    detail=f"regenerate {20 - s['health']:.0f} hp"))
        # Eating is worth what the hunger is costing now (survival.hunger_loss) — a different thing from having
        # meals in the bag. As a line in `reflexes` it fired at a fixed level and only ate COOKED food, so a full
        # bag of raw pork and an empty stomach was a stable state.
        if s["food"] < 20 and (food_count(snap.inv) or skills.edible_carried(snap.inv)):
            cooked = food_count(snap.inv) > 0
            eat_c = C("eat", 0, 40, lambda: skills.eat(raw_ok=not cooked), kind="maintenance", cap=5,
                      seconds=worth({"food": 20}), detail="until full")
            eat_c.runs = (skills.eat, ())
            offer(eat_c)
        if s["food"] <= 6 and not food_count(snap.inv):
            offer(C("eat anything", 0, 600, lambda: skills.eat(raw_ok=True) or self.forage(ctx, snap),
                    kind="maintenance", cap=10, seconds=worth({"food_items": 8})))
        if not night and skills.enclosed():
            # Walled in from last night: nothing else can run at all until we are out, so it is worth whatever the
            # rest of the day is worth.
            _c = C("dig out", 0, 600, lambda: skills.dig_out(ctx), kind="maintenance", cap=10,
                    seconds=float(survival.CONFIG["time"]["day_s"]))
            _c.runs = (skills.dig_out, (ctx,))
            offer(_c)
        if self.stuck_in_place(snap):
            offer(C("unstuck", 0, 600, lambda: self.unstuck(ctx, snap), kind="maintenance", cap=10,
                    seconds=float(survival.CONFIG["time"]["day_s"])))
        self._night_candidates(ctx, snap, night, offer, filtered, sstate, worth)

    def _night_candidates(self, ctx, snap, night, offer, filtered, sstate, worth):
        """Sleeping, sheltering and heading home, priced by what the night costs without them (survival.night_loss).

        This was `safety()`: an if-chain that returned before the pool, so "go home before dark" always beat "finish
        the ore you are standing on", whatever either was worth. The bed and the shelter already had a price in
        seconds; they just had no way to say it.
        """
        from . import survival
        C = priority.Candidate
        if snap.dimension != "minecraft:overworld":
            return          # no day, no night, and beds explode
        carried_bed = snap.inv.count("bed") > 0
        delay = priority.dusk_delay(snap.ticks_until_dusk, night)
        if night and (carried_bed or find(BASE_MARKERS["bed"], radius=48, limit=1)):
            offer(C("sleep", 0, 400, lambda: skills.sleep(ctx, self.policy(snap, True)), kind="maintenance",
                    cap=20, seconds=worth({"bed": True, "sheltered": True}), detail="skip the night"))
        if not self.sheltered(snap):
            site = self.mem.nearest_site(snap.feet, snap.dimension, kinds=["home", "shelter"])
            if site is not None:
                eta = travel_ticks(snap.feet, site["pos"])
                offer(C("return to shelter", 0, eta + 200,
                        lambda: self._go_to_site(site, snap), kind="maintenance", cap=60,
                        seconds=worth({"sheltered": True}), delay_s=delay,
                        detail=f"{site['name']} ~{eta // 20}s away"))
            if not skills.materials_missing(blueprints.SHELTER):
                offer(C("build shelter", 0, int(skillkit.expected(skills.build_shelter, ctx) * 20) or 2400,
                        lambda: skills.build_shelter(ctx), kind="maintenance", cap=120,
                        seconds=worth({"sheltered": True}), delay_s=delay))
            if night:
                _c = C("dig in", 0, 600, lambda: skills.dig_in(ctx), kind="maintenance", cap=30,
                        seconds=worth({"sheltered": True}))
                _c.runs = (skills.dig_in, (ctx,))
                offer(_c)
                offer(C("wall in", 0, 800, lambda: skills.pod(ctx), kind="maintenance", cap=30,
                        seconds=worth({"sheltered": True})))

    def _go_to_site(self, site, snap):
        if not nav.go_to(tuple(site["pos"]), self.policy(snap, snap.night), range_=4):
            raise NotAvailable(f"{site['name']} not reachable")

    def forage(self, ctx, snap):
        from .knowledge import HUNT
        if snap.night and not self.sheltered(snap):
            raise NotAvailable("no foraging exposed at night")
        for token in ("minecraft:beef", "minecraft:porkchop", "minecraft:mutton", "minecraft:chicken"):
            if entities(48, HUNT[token]):
                return skills.hunt(ctx, token, 2, HUNT[token], snap.night)
        raise NotAvailable("no animals to forage")

    def threat_state(self, s):
        """The threat model's view of now, from the perception authority. No world read here on purpose: the pool
        must be a pure function of the snapshot plus what perception already saw, or recorded rounds stop replaying
        and every decision costs an extra request."""
        from . import perception, threat
        hazards, ids = perception.threats_seen()
        inv = Inventory()
        sword = max((t for t, d, _ in inv.tools("sword") if d >= 1), default=0)
        blocks = sum(inv.count(b) for b in skills.GROUPS["building"] + skills.GROUPS["planks"])
        tod = s.get("timeOfDay", 0) % 24000
        return {"here": (s["x"], s["y"], s["z"]), "hp": s["health"], "sword": sword,
                "protection": threat.protection(s.get("armor", 0), inv.offhand() == "minecraft:shield"),
                "night": 13000 <= tod < 23000, "blocks": blocks, "hazards": hazards, "ids": ids}

    def threat_price(self, snap):
        """How this agent, right now, converts health into seconds — the survival model, as a function."""
        from . import survival
        sstate = survival_state(snap, self.mem)
        return lambda dhp: survival.hp_seconds(sstate, dhp)

    def threat_now(self, snap):
        """The emergency answer, or None. Emergency means: dead before the pool gets another turn — anything slower
        than that is a choice, and choices are priced against mining and crafting in the pool."""
        from . import threat
        try:
            tstate = self.threat_state(snap.state)
        except McError:
            return None
        if not tstate["hazards"]:
            return None
        press = threat.pressure(tstate["here"], tstate["hazards"], tstate["protection"])
        if threat.time_to_die(tstate["hp"], press) > threat.ENGAGE["interrupt_ttd_s"]:
            return None
        return threat.decide(tstate, self.threat_price(snap))

    def engage(self, decision, s, ctx=None):
        """Carry out one threat answer — an Option from the pool or a Decision from the emergency; both name a
        `kind` and a `target`. May fail: it is called through `attempt`, never from `reflexes`."""
        if decision.kind == "fight":
            api.run({"type": "attack", "entity": decision.target}, wait=45)
        elif decision.kind == "evade":
            if not nav.go_to(decision.target, self.policy_cache, range_=3, attempts=1, min_hp=0):
                raise NotAvailable(f"could not get away to {decision.target}")
        elif decision.kind == "wall_in":
            ctx = ctx or skills.Context(self.mem, self.policy_cache, s["dimension"], self.blacklist)
            skills.pod(ctx)
        elif decision.kind == "eat":
            skills.eat(raw_ok=True)
        elif decision.kind == "shield":
            skills.shield_to_offhand()
            api.run({"type": "use_item", "hand": "offhand", "hold_ms": 1500}, wait=5)
        elif decision.kind == "reshape":
            self.reshape(decision, s)

    def reshape(self, decision, s):
        """Change the ground: block the way, stand a block up, or dig down. One answer, three places to put it."""
        where, n = decision.target
        feet = tuple(int(math.floor(s[k])) for k in ("x", "y", "z"))
        if where == "down":
            for i in range(n):
                api.run({"type": "mine", "x": feet[0], "y": feet[1] - 1 - i, "z": feet[2]}, wait=20)
            return
        item = next((b for b in skills.GROUPS["building"] if Inventory().count(b)), None)
        if item is None:
            raise NotAvailable("nothing to shape the ground with")
        if where == "under":
            for _ in range(n):
                api.run({"type": "pillar", "item": item}, wait=20)
            return
        near = perception.threats_seen()[0]
        toward = min(near, key=lambda h: math.dist(feet, h[0]))[0] if near else (feet[0] + 1, feet[1], feet[2])
        step = [1 if toward[i] > feet[i] else (-1 if toward[i] < feet[i] else 0) for i in (0, 2)]
        for i in range(n):
            skills.place(item, (feet[0] + step[0], feet[1] + i, feet[2] + step[1]))

    def unstuck(self, ctx, snap):
        """No progress for a minute. One method per call, each skipped once it has failed in this state (retry.py):
        the nearest site, up toward the sky, 16 blocks along each axis, down. Cooldowns and blacklists are kept —
        they are what was learned. Every method failed here → macro escalation to Claude."""
        x, y, z = snap.feet
        site = self.mem.nearest_site(snap.feet, snap.dimension)
        if snap.dimension == "minecraft:the_nether":
            # In the Nether "unstuck" heads for the way home, not the nearest remembered spot (a fortress or a
            # sighting deeper in): the portal is where every retreat ends anyway.
            portals = self.mem.sites(snap.dimension, kinds=["portal"])
            if portals:
                site = min(portals, key=lambda p: math.dist(p["pos"], snap.feet))
        # A site we are already standing on cannot un-stick us: heading for one 3 blocks away "succeeded" instantly
        # and the same minute repeated forever. Only a site far enough to change the situation counts.
        if site and math.dist(site["pos"], snap.feet) < 12:
            site = None
        methods = ([("site", tuple(site["pos"]))] if site else []) + [
            ("up", (x, y + 12, z)), ("east", (x + 16, y, z)), ("west", (x - 16, y, z)),
            ("south", (x, y, z + 16)), ("north", (x, y, z - 16)), ("down", (x, y - 8, z))]
        for label, target in methods:
            name = f"unstuck:{label}"
            if not self.ready(name):
                continue
            log(f"no progress for {STUCK_LIMIT}s at {snap.feet} → unstuck by heading {label} {target}")
            self.history.clear()
            if nav.go_to(target, self.policy(snap, snap.night), range_=3, attempts=1):
                self.retry.succeeded(name)
                return
            self.failed(name, NotAvailable(f"could not get {label} to {target}"))
            raise NotAvailable(f"unstuck {label} failed")
        self.escalate("stuck", f"every unstuck method failed at {snap.feet}")
        raise NotAvailable("every unstuck method failed here")

    def fallbacks(self, ctx, snap, night):
        """Work that is always worth something, in order, when no goal is runnable — so the agent is never idle:
        spawn-proof the area, strip mine (fine at night, lit by reflexes), explore for animals and land by day."""
        surface_day = not night and snap.get("skyLight", 0) > 7
        overworld = snap.dimension == "minecraft:overworld"
        # Preconditions up front: a fallback that can't run must not be tried (and spin on ToolMissing). All three
        # are Overworld work: strip mining toward y=16 in the Nether digs into the lava sea at y≈31.
        return [
            # Each names the skill it runs, so the pool can ask that skill's own preconditions before pricing it
            # (pool.admit). Asking the world directly here cost a /blocks read per round and left 63 recorded
            # rounds unreplayable; asking the skill costs nothing and is the same answer.
            ("light up", lambda: skills.light_area(ctx), overworld, (skills.light_area, (ctx,))),
            ("strip mine", lambda: skills.strip_mine_step(ctx),
             overworld and self.has_pickaxe(snap) and lookahead.escape_ready(snap.inv),
             (skills.strip_mine_step, (ctx,))),
            ("explore", lambda: self.explore(ctx), overworld and surface_day, None),
        ]

    def has_pickaxe(self, snap):
        return any(d >= 3 for _, d, _ in snap.inv.tools("pickaxe"))

    def replace_tool(self, ctx, kind):
        """Make the best tool of `kind` the inventory allows (tier 3 → 0, `pick_tool_plan`): only a plan that can run
        to its end is started — never smelt ingots for an iron pickaxe whose sticks can't be had. Surface gathering at
        night is allowed in full iron armor with health to spare."""
        for _ in range(8):
            snap = Snapshot()
            if any(d >= 3 for _, d, _ in snap.inv.tools(kind)):
                return
            cost = LiveCost(snap, self.blacklist, self.mem)
            plans = {}
            for tier in (3, 2, 1, 0):
                try:
                    plans[tier] = Planner.from_inventory(snap.inv, cost).plan([("tool", kind, tier)])
                except Unplannable:
                    pass
            armored = wearing_at_least(snap.inv, "iron") and snap.get("health", 0) >= 14
            blocked = {"gather", "hunt"} if snap.night and not armored else set()
            picked = pick_tool_plan(plans, blocked, no_mining=kind == "pickaxe")
            if picked is None:
                if blocked and pick_tool_plan(plans, set(), no_mining=kind == "pickaxe"):
                    raise NotAvailable(f"a new {kind} needs surface materials; waiting for daylight")
                raise NotAvailable(f"no way to make a {kind} from here")
            tier, plan = picked
            step = next((s for s in plan if runnable(s, snap.inv)), None)
            if step is None:
                raise NotAvailable(f"no runnable step toward a tier-{tier} {kind}")
            log(f"   replacing {kind} (tier {tier}): {step}; plan: {', '.join(map(str, plan))}")
            self.execute(ctx, step, snap.night)
        raise McError(f"still no usable {kind}")

    def explore(self, ctx):
        """Exploring is the act of looking, not a promise that the world holds animals. It fails only when it could
        not move: counting "no animals here" as a failure cooled the one runnable fallback for 180 s, emptied the
        pool and left the agent idle until the slice gave up (10:16:56 — every route goal was waiting on a sighting,
        so explore was all there was)."""
        before = nav.feet_now()
        found = skills.explore_for(ctx, ["minecraft:sheep", "minecraft:cow", "minecraft:pig", "minecraft:chicken"],
                                   legs=4)
        if found:
            return
        if math.dist(nav.feet_now(), before) < 2:
            # Only a body that truly never left counts as stuck. At 8 blocks this fired whenever the legs aimed at an
            # unreachable spot, cooled the one runnable fallback for 180 s and emptied the pool again.
            raise NotAvailable("explore could not move from here")
        log("   explored, no animals in this stretch — new ground covered, not a failure")

    def attempt(self, name, fn, cooldown=None):
        """Run fn under the failure policy. `cooldown` caps the retry wait (survival rescues, fallbacks)."""
        try:
            return self._attempt(name, fn)
        finally:
            if cooldown is not None:
                self.retry.cap(name, cooldown, time.time())

    def _attempt(self, name, fn):
        before = progress_signature()
        try:
            fn()
            self.retry.succeeded(name)
            # Progress = the inventory/equipment changed. Quick successful steps are fine; only "succeeded" with no
            # effect twice in a row for the same goal is a spin.
            idle = progress_signature() == before
            if idle and self.last_quick == name:
                self.failed(name, McError("no progress (succeeded twice without changing anything)"))
            self.last_quick = name if idle else None
        except PlayerTookControl:
            api.wait_for_handback()
        except GameUnreachable:
            api.wait_for_game()
        except api.CommitmentExpired as e:
            log(f"   {e}")          # not a failure: the next round decides with what the world looks like now
        except api.BodyContested as e:
            log(f"?? {e}; standing down 10 s")
            time.sleep(10)
        except (McError, skills.ToolMissing) as e:
            self.failed(name, e)
            try:
                api.post("/stop")
            except McError:
                pass
        except Exception:
            log("!! crash in " + name + "\n" + traceback.format_exc())
            self.retry.hold(name, 300, time.time())


def code_version():
    """Short hash of the brain's source, logged at start so a review can tell which code is running."""
    import glob
    import hashlib
    h = hashlib.sha1()
    for path in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "*.py"))):
        with open(path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:10]


def autoplay(hours):
    import fcntl
    lock_file = paths.data("autoplay.lock")
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        log("!! another autoplay process is already running; aborting")
        return
    log(f"autoplay start: brain code {code_version()}")
    from . import perception
    perception.start()   # interrupts long tasks on danger (~5 Hz), survival mode acts next round
    brain = Brain()
    deadline = time.time() + hours * 3600
    while time.time() < deadline:
        try:
            brain.round()
        except PlayerTookControl:
            api.wait_for_handback()
        except GameUnreachable:
            api.wait_for_game()
        except McError as e:
            log(f"!! round: {e}")
            time.sleep(2)
        except Exception:
            log("!! crash in round\n" + traceback.format_exc())
            time.sleep(10)
    try:
        api.post("/release")
    except McError:
        pass
