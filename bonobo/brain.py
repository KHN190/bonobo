"""The decision loop: reflexes → phase bookkeeping → night handling → cost-scored goal selection → one verified
step → repeat. Goals are expressed as requirements; the planner turns them into steps; only the first step of the
best plan runs per round, so every round re-plans against the real inventory (DEPS-style selector)."""
import contextlib
import math
import time
import traceback

import json
import os

from . import (api, arbiter, bag, blueprints, brewing, combat, directives, end, farming, fluids, intent, jobs,
               lookahead, loot, nav, nether, paths, priority, retry, route, skills, tape, ui, upkeep, world)
from . import beliefs
from . import estimate
from . import skill as skillkit
from . import skillcore
from .skillcore import mine_cell as skillkit_mine
from .api import GameUnreachable, McError, NotAvailable, PlayerTookControl, TaskStuck, log
from .data import BASE_MARKERS, GROUPS, HAND_MINEABLE_SUFFIX, TOOL_MATERIALS, bare, mid
from . import knowledge
from .knowledge import UNDERGROUND_KINDS
from .actions import tool_dim as act_tool_dim, uses_dim as act_uses_dim  # noqa: F401
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


def tool_wear():
    """{tool kind: total damage on tools of that kind}, or None when the world cannot answer.

    Damage is how the game itself counts uses: a block mined is a point of durability. Reading it before and after
    a step is how "how often is a pickaxe actually reached for" gets measured instead of assumed.
    """
    from .data import TIER_OF_MATERIAL, bare
    try:
        inv = Inventory()
    except McError as e:
        return api.swallowed("tool_wear: what the tools look like now", e)
    out = {}
    for slot in inv.slots:
        material, _, kind = bare(slot["id"]).rpartition("_")
        if material in TIER_OF_MATERIAL:
            out[kind] = out.get(kind, 0) + int(slot.get("damage", 0))
    return out


def note_tool_wear(mem, before, after, seconds):
    """Record what the last step wore out, as uses per second of play (memory.note_tool_use)."""
    if before is None or after is None or seconds <= 0:
        return
    for kind in set(before) | set(after):
        used = max(0, after.get(kind, 0) - before.get(kind, 0))
        mem.note_tool_use(kind, seconds=seconds, uses=used)


def progress_signature():
    """What a step could have changed: what is carried, and where the body stands.

    Position belongs here. `seek` walks to where the work is and is SUPPOSED to leave the bag alone — comparing
    inventories alone called every walk a spin, so on a fresh world the agent made a wooden pickaxe and then
    failed `seek 1× stone` for the stone sword, the stone axe and the stone pickaxe in turn, each "succeeded twice
    without changing anything", each cooling for a minute. It never made a stone tool at all.
    """
    try:
        inv = Inventory()
    except McError as e:
        return api.swallowed("progress_signature: what changed this round", e)
    try:
        feet = tuple(int(x) for x in nav.feet_now())
    except Exception:
        feet = None
    return (tuple(sorted((s["id"], s.get("count", 1), s.get("damage", 0)) for s in inv.slots)),
            tuple(sorted((k, v.get("id")) for k, v in inv.equipment.items())), feet)


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
        shield=inv.offhand() == "minecraft:shield" or inv.count("minecraft:shield") > 0,
        dark=is_dark(snap))


def is_dark(snap):
    """Is it dark enough HERE for hostiles to spawn and come at us?

    Skylight only counts while the sun is up; after dusk an open field is as dark as a cave. One reading of what
    "dark" means, so the exposure price, the torch goal and the survival model cannot drift apart.
    """
    from . import survival
    light = int(snap.get("blockLight", 0))
    if not snap.night:
        light = max(light, int(snap.get("skyLight", 0)))
    return light <= int(survival.CONFIG["risk"]["dark_light"])


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
            # The position comes free with the distance: whoever prices the WAY there (`reach_s`) then needs no
            # query of its own — and a decision that makes a query nobody recorded cannot be replayed.
            self.cache[("hit",) + key[1:]] = (hits[0]["x"], hits[0]["y"], hits[0]["z"]) if hits else None
            dist = hits[0]["distance"] if hits else None
            if dist is None and self.mem is not None and radius >= 32:
                kind = next((k for k, ids in Brain.RESOURCE_KINDS.items()
                             if set(ids) & {bare(b) for b in blocks}), None)
                if kind:
                    known = [p for p in self.mem.resources(kind, self.snap.dimension)
                             if not self._banned(tuple(p))]
                    if known:
                        dist = min(math.dist(p, self.snap.feet) for p in known)
            self.cache[key] = dist
        return self.cache[key]

    def _nearest_known(self, kinds, radius):
        """Position of the nearest one of these that memory knows and that is not blacklisted, or None.

        Memory only — the resource map and remembered sightings. An estimate that makes a fresh query cannot be
        replayed: every recorded round would miss the call it never made.
        """
        if self.mem is None:
            return None
        here, dim = self.snap.feet, self.snap.dimension
        best, best_d = None, None
        for kind in kinds:
            spots = list(self.mem.resources(bare(kind), dim)) + list(self.mem.resources(kind, dim))
            spots += [s["pos"] if isinstance(s, dict) else s for s in (self.mem.sightings(kind, dim) or ())]
            for p in spots:
                p = tuple(p)
                if self._banned(p):
                    continue
                d = math.dist(p, here)
                if d <= radius and (best_d is None or d < best_d):
                    best, best_d = p, d
        return best

    def reach_s(self, kinds):
        """Seconds to get within working reach of the nearest one of these — `math.inf` when there is no route from
        here, None when it cannot be priced (nothing known nearby, or the blocks are not to hand).

        The price comes from nav's own route search, which may walk, dig, bridge, pillar or climb, and refuses to
        go through lava or water. So every reason a thing can be visible and still unworkable — a flooded pit, a
        lava sheet, a wall with no pickaxe, a drop with nothing to bridge with — arrives here as one number in
        seconds, and the planner compares it with everything else instead of discovering it by failing.

        The one world read is the box of blocks between us and it, and it is optional: on a recorded round the
        call was never made, so this answers None and the table falls back to the straight line it used before.
        """
        from . import nav, tape
        from .beliefs import CONFIG as _PLAY
        key = ("reach", tuple(kinds))
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None                                  # unknown until something better is found
        probe = float(_PLAY["nav"]["probe_r"])
        pos = self._nearest_known(list(kinds), probe)
        if pos is None:
            # Memory is empty for everything we have not walked to yet — which includes everything in plain sight.
            # Pricing arrival from memory alone is why a bed behind a lava sheet cost the same as one on flat
            # ground: nobody had ever written it down, so nobody priced the way to it. The position comes from a
            # query the round has ALREADY made (`_find` stores it); asking again would make this decision
            # unreplayable, and a decision that cannot be replayed cannot be tested.
            seen = [v for k, v in self.cache.items()
                    if k[0] == "hit" and v is not None and set(k[1]) & {mid(x) for x in kinds}]
            pos = min(seen, key=lambda p: math.dist(p, self.snap.feet)) if seen else None
        if pos is None:
            return None
        try:
            region = world.region_around([self.snap.feet, pos], pad=3)
        except (tape.ReplayMiss, McError, NotAvailable) as e:
            return api.swallowed("reach_s: the blocks between us and it", e)
        if region is None or not region.blocks:
            # A box nobody has the blocks for is not a wall: "unknown" and "no route" are different answers, and
            # only the second one may move the body somewhere else.
            return None
        # The route's means come from the bag we already have in hand, never from a fresh inventory query.
        inv = self.snap.inv
        policy = nav.Policy(hand_only=not any(d >= 3 for _t, d, _n in inv.tools("pickaxe")))
        # A QUICK price, not the route: ranking asks about every findable thing every round, and a full search
        # each time is the round's whole budget. Through the time door, which owns the arithmetic; the exact
        # search runs when the body actually moves.
        from . import gates
        priced = gates.takes_s(gates.Situation(region=region, policy=policy, route=nav.estimate_price_s,
                                              here=tuple(self.snap.feet)),
                               gates.Go(tuple(self.snap.feet), tuple(pos)))
        self.cache[key] = math.inf if priced is None else priced
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
        """Ticks this step takes here. An ENGINE of `gates.takes_s(s, Run(step))`: measured durations where this
        world has measured them (`memory.duration`), the heuristics where it has not. Everyone who wants seconds
        asks the door, which divides by the tick rate."""
        learned = self._learned_ticks(step)
        # Success rates apply once, at the candidate (`gates.p("success")`), not inside step costs.
        return learned if learned is not None else self._estimate(step)

    def risk_s(self, step, ticks=None):
        """Seconds this step is expected to cost in BLOOD, priced by the survival model.

        The rate and the shape both come from elsewhere: perception says what is around (one authority, no world
        read here), and the ACTION says how it is exposed — standing work takes the pressure for its duration,
        leaving takes it only until it is out of reach. A flat rate applied to everything is what made walking
        away from a zombie look as expensive as standing in front of it.
        """
        from . import gates, perception, threat
        from .solve import Action
        if self.snap is None:
            return 0.0
        rows, _ids = perception.threats_seen()
        from . import gates as _g
        seconds = (_g.takes_s(None, _g.Run(step, cost=self)) if ticks is None
                   else float(ticks) / priority.TICKS_PER_S)
        sstate = survival_state(self.snap, self.mem) if self.mem is not None else None
        # What the DARK costs, before anything is in sight: hostiles spawn where the light is low, and the price of
        # working there is a RATE (`gates.p("encounter")`), not a sighting. Without this an unlit cave at noon was
        # free, the threat layer answered "ignore — carrying on takes ~4.1 hp/s", and the agent mined until it died.
        ambient = gates.exposure_s(seconds, sstate, mem=self.mem,
                                   underground=self.snap.get("skyLight", 15) <= COVERED_SKY) if sstate else 0.0
        if not rows:
            return ambient
        inv = self.snap.inv
        state = {"here": (self.snap.state["x"], self.snap.state["y"], self.snap.state["z"]),
                 "hp": self.snap.state.get("health", 20),
                 "protection": threat.protection(self.snap.get("armor", 0),
                                                 inv.offhand() == "minecraft:shield"),
                 "hazards": rows}
        shape = Action(step.kind, {}, max(0.1, seconds), tag=(step.kind, step.token))
        dhp = shape.exposure(state)
        if dhp <= 0:
            return ambient
        # Health into seconds through the one scarcity door: blood is scarce the way a slot is scarce.
        return ambient + (gates.marginal("blood", sstate=sstate) * dhp if sstate else dhp)

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
        ctx = skills.Context(brain.mem, brain.policy(snap, snap.night), snap.dimension, brain.blacklist,
                             prices=brain.price_table)
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
        # How many goals it took to fill the pool last time: a capacity hint, like a growing buffer's, not a quota.
        self.expand_hint = MIN_CHOICES * 2
        self.blacklist = {}     # unreachable targets, shared by every round's Context and the cost model
        self.ban_counts = skillcore._BAN_COUNTS   # how often each cell was banned: the same counter skills use
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
        self.stood_down = None       # why this layer is not a participant this round (`stand_down`), or None
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
            ctx = skills.Context(self.mem, self.policy_cache, s["dimension"], self.blacklist, prices=self.price_table)
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
            skills.contain_lava(skills.Context(self.mem, self.policy_cache, s["dimension"], self.blacklist, prices=self.price_table))
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
        elif step.kind == "take":
            # Already made, standing in the world: walk over and break it out. Same protection as any other dig.
            skills.take(ctx, step.token, step.count, step.detail["blocks"])
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
        self.note_body_of(snap)
        self.policy_cache = self.policy(snap, night)
        ctx = skills.Context(self.mem, self.policy_cache, snap.dimension, self.blacklist, prices=self.price_table)
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
        # What this candidate was PRICED at, if it was priced by an expected yield, and what the bag was worth
        # before it ran. Together they are the measurement that outgrows the declared prior (`memory.yield_rate`).
        expected_s = self.yield_worth_s(pick.name, snap) if hasattr(snap, "inv") else 0.0
        worth_before = None
        if expected_s:
            prices, _vector = self.prices(snap, LiveCost(snap, self.blacklist, self.mem))
            worth_before = self.bag_worth_s(snap, prices)
        # Ordinary work is resumable: a walk interrupted is a walk continued, and stopping it throws nothing
        # away. What it would have to redo is one step's granularity (`commitment_s`), which is what defends it
        # against another PLAN-layer answer — and nothing at all against a faster layer.
        arbiter.BODY.submit("plan", lambda: self.attempt(pick.key, pick.run, cooldown=pick.cap), pick.name,
                            commit_s=pick.commitment_s, cost_rate=1.0, cost_s=pick.cost_s,
                            resumable=True, redo_s=pick.commitment_s)
        ran_from, ran_at = tuple(snap.feet), time.time()
        try:
            arbiter.BODY.step()
        except api.CommitmentExpired as e:
            log(f"   {e}")
        self.note_walk_of(ran_from, ran_at, pick)
        self.note_yield_of(pick.name, expected_s, worth_before)
        after = self.retry.entries.get(pick.key, {}).get("since")
        if after is not None and after != before:
            self.recent_fail = (pick.name, time.time())

    # A walk shorter than this says nothing about what a block costs: turning round, one step aside, a door.
    WALK_SAMPLE_BLOCKS = 8
    # The body moves slowly: below this, a difference of one point is rounding, not a rate.
    BODY_SAMPLE_S = 30.0

    def note_body_of(self, snap):
        """What the body actually does between rounds: how fast health comes back, how fast food goes down.

        Both are declared guesses (`play.toml [risk] regen_s_per_hp, food_drain_s`) and both are visible in every
        pair of consecutive snapshots — the agent has been walking past its own measurement all along. Only the
        stretches that say something are counted: health rising while fed is regeneration, health falling is a
        fight and says nothing about regeneration; food only ever falls.
        """
        hp, food, now = snap.get("health", 20), snap.get("food", 20), time.time()
        used = snap.inv.used_slots() if hasattr(snap, "inv") else None
        was = getattr(self, "last_body", None)
        self.last_body = (hp, food, now, used)
        if was is None:
            return None
        was_hp, was_food, was_at, was_used = was
        dt = now - was_at
        if dt < self.BODY_SAMPLE_S:
            return None
        # How fast working fills the bag — the number every "is it worth carrying" answer divides by.
        if used is not None and was_used is not None and used > was_used:
            beliefs.note("pool.slot_fill_s", dt / (used - was_used), where="round")
        floor = float(survival.CONFIG["risk"]["regen_food_floor"])
        if hp > was_hp and min(food, was_food) >= floor:
            beliefs.note("risk.regen_s_per_hp", dt / (hp - was_hp), where="round")
        if food < was_food:
            beliefs.note("risk.food_drain_s", dt / (was_food - food), where="round")
        return self.last_body

    def note_walk_of(self, was, at, pick):
        """What that leg of walking actually cost, against what the navigator quoted.

        The only number in the whole route estimate is `[nav] unit_s` — seconds per walked block — and it has been
        a declared guess since the day it was written (`play.toml [unmeasured]`). Every round that moves the body
        is a measurement of it, and they were all being thrown away. `beliefs.note` only raises the count; what
        moves the number is a fit over the history, which is not this call's business.
        """
        try:
            now = Snapshot()
        except (McError, GameUnreachable) as err:
            return api.swallowed("brain.note_walk_of", err)
        moved = sum(abs(a - b) for a, b in zip(tuple(now.feet), was))     # walked blocks, not the straight line
        if moved < self.WALK_SAMPLE_BLOCKS:
            return None
        return beliefs.note("nav.unit_s", (time.time() - at) / moved, where=f"round:{pick.name}")

    SCAN_EVERY_S = 20
    SCAN_BLOCKS = {"tree": ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log"],
                   "water": ["water"], "lava": ["lava"], "iron": ["iron_ore", "deepslate_iron_ore"],
                   "coal": ["coal_ore", "deepslate_coal_ore"]}
    # Finished goods standing in the world (a village's beds, furnaces, tables, crops), noted under the block's
    # own name — that is what `at:<block>` and the take columns ask for, and it is what makes "take that one" cost
    # a walk instead of a search. Sixty blocks, but ONE query: a scan runs every twenty seconds and sixty finds a
    # scan would be the round's whole budget.
    TAKE_BLOCKS = knowledge.takeable_blocks()
    RESOURCE_KINDS = dict(SCAN_BLOCKS, **{b: [b] for b in TAKE_BLOCKS})
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
            # One sweep for everything that is already made: each hit is remembered under its own block name.
            for h in find(self.TAKE_BLOCKS, radius=48, limit=16) or ():
                self.mem.note_resource(bare(h["block"]), (h["x"], h["y"], h["z"]), snap.dimension)
            for e in entities(48, list(self.SCAN_MOBS)):
                self.mem.add_sighting(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), snap.dimension)
        except McError as e:
            # A scan that fails is a map that does not grow — and every price read off that map is then a guess
            # about a world nobody looked at. Not fatal; not invisible either.
            api.swallowed("scan_resources: looking around", e)

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
                filtered.pop(c.name, None)      # admitted on a second pass: it is no longer being kept out
                out.append(c)
                return True
            filtered[c.name] = str(r)
            self.last_refusals.append((c.name, r))
            return False       # the caller may need to know that planning more is the only way to fill the pool

        self._rescue_candidates(ctx, snap, night, offer, filtered)
        self._maintenance_candidates(ctx, snap, night, offer, filtered)
        eligible = self._goal_candidates(ctx, snap, night, pctx, offer, filtered)
        self._directive_candidates(ctx, snap, night, offer, filtered)
        # Reservations cover every open main goal that can plan, not only the one picked: a down-weighted wheat
        # farm's seeds were thrown away three times in a minute.
        bag.RESERVED = set().union(*(bag.reserved_ids(plan, g.needs) for g, plan, _, _ in eligible
                                     if not g.background)) if eligible else set()
        self._fallback_candidates(ctx, snap, night, pctx, offer, filtered)
        if not out and pctx.staying:
            # Nothing can run while a goal retries where it stands. Holding wandering work back is a PREFERENCE —
            # stay near the thing being retried — and a preference cannot be the reason the agent does nothing. It
            # is waived by offering again through the SAME door with `staying` cleared, never by appending to the
            # pool behind the door's back: that second way in is how `light up` with no torches was chosen thirty
            # times in twenty seconds, having just been refused for exactly that.
            pctx = pctx.relaxed(staying=False)
            self._fallback_candidates(ctx, snap, night, pctx, offer, filtered)
        return out, filtered

    def stand_down(self, reason):
        """Stop being a participant: every candidate this layer would offer is refused, in the open, until
        `resume()`. The body is then whoever else asks for it — which is the arbiter's business, not ours.

        A bench cell measuring one layer needs the others quiet, and the way that used to be done was to not call
        `round()` at all. That hides the planner from the tape (no candidates, no refusals, no reason) and leaves
        anything it was committed to running. This says it instead.
        """
        self.stood_down = reason or "standing down"
        self.committed = None            # nothing is held while we are not playing
        log(f"   planner standing down: {self.stood_down}")

    def resume(self):
        """Take part again."""
        was, self.stood_down = self.stood_down, None
        if was:
            log("   planner taking part again")
        return was

    @contextlib.contextmanager
    def not_taking_part(self, reason):
        """`with brain.not_taking_part("threat bench cell"):` — the shape a bench should use, so a cell that
        raises still gives the planner back."""
        self.stand_down(reason)
        try:
            yield self
        finally:
            self.resume()

    def _pool_context(self, snap, night, force):
        from . import pool as _pool, scenarios
        recent = self.recent_fail
        staying = bool(recent and recent[0] == self.committed and time.time() - recent[1] < 90)
        segment = self.route_segment(snap) if route.current() else None
        if (segment["name"] if segment else None) != self.seg_name:
            self.seg_name = segment["name"] if segment else None
            self.seg_misses.clear()      # a new segment is a new place: everything is worth one more look
        return _pool.Context(
            stood_down=self.stood_down,
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
        from . import gates, survival
        C = priority.Candidate
        # Health is the margin every future fight starts from (survival.fight_loss), so healing is worth whatever
        # that margin is worth. Regeneration needs food above 17 and a few quiet seconds; both are cheap, which is
        # why nothing ever chose to do it while the value was zero.
        hp = snap.get("health", 20)
        if hp < 20 and food_count(snap.inv):
            saves = self.worth_of_change(snap, {"lever:hp": 20 - hp}, slots=0)
            if saves > 0:
                offer(C("heal up", 8, 20 * (20 - hp), lambda: self.heal(ctx, snap), kind="maintenance",
                        seconds=saves, cap=20, detail=f"regenerate {20 - hp:.0f} hp"))
        if not self.has_pickaxe(snap):
            offer(C("replace pickaxe", 12, 2000, lambda: self.replace_tool(ctx, "pickaxe"),
                    kind="maintenance", cap=30,      # no pickaxe: the benefit is due now, so no discount
                    seconds=self.worth_of_change(snap, {act_tool_dim("pickaxe", 1): 1,
                                                        act_uses_dim("pickaxe"): 131}, slots=1)))
        # What a full bag costs: the next stretch of mining produces nothing that can be kept.
        # What making room is worth: the same subtraction as everything else, with the bag as the lever.
        freed = max(1, snap.inv.used_slots() - (36 - skills.FREE_SLOTS_TARGET))
        bag_worth = self.worth_of_change(snap, {"lever:bag_free": freed}, slots=0)
        if True:
            if not skills.store_plan(snap.inv.slots):
                filtered["deposit"] = "nothing worth storing"
            elif skills.can_store_here(ctx, local_only=night):
                _c = C("deposit", 3, 1200, lambda: skills.deposit(ctx, local_only=night),
                        kind="maintenance", seconds=bag_worth)
                _c.runs = (skills.deposit, (ctx,))
                offer(_c)
            x, y, z = snap.feet
            throwable = skills.free_slots_plan(snap.inv.slots,
                                               need=max(0, snap.inv.used_slots() - (36 - skills.FREE_SLOTS_TARGET)))
            if not throwable:
                filtered["tidy"] = "nothing worth throwing away"
            elif skills.throw_direction(world.Region((x - 3, y - 1, z - 3), (x + 3, y + 2, z + 3)), snap.feet):
                _c = C("tidy", 3, 300, lambda: skills.tidy_inventory(ctx),
                        kind="maintenance", cap=20, seconds=bag_worth)
                _c.runs = (skills.tidy_inventory, (ctx,))
                offer(_c)
            elif not (night and self.sheltered(snap)):   # never leave a night shelter just for the bag
                _c = C("open space", 3, 400, lambda: skills.move_to_open_space(ctx),
                        kind="maintenance", cap=30, seconds=bag_worth)
                _c.runs = (skills.move_to_open_space, (ctx,))
                offer(_c)
            else:
                filtered["tidy"] = "no room to throw in this shelter; waits for day"
        if night:
            return
        dirty = [s for s in self.mem.sites(snap.dimension) if s.get("dirty")]
        dirty = [self.pick_target(dirty, snap.feet)] if dirty else []
        dirty = [d for d in dirty if d is not None]
        if dirty:
            # A shelter with a hole in it is not a shelter: what mending it is worth is what having one is worth.
            offer(C("repair", 2, 2000, lambda: skills.repair_site(ctx, dirty[0]), kind="maintenance",
                    seconds=self.worth_of_change(snap, {"sheltered": 1}, slots=0)))
        ready_jobs = sorted((j for j in self.mem.jobs(snap.dimension)
                             if skills.job_ready(j) and math.dist(j["pos"], snap.feet) <= 96),
                            key=lambda j: math.dist(j["pos"], snap.feet))[:3]
        for job in ready_jobs:
            d = math.dist(job["pos"], snap.feet)
            # Being close does not make a job worth more; it makes it cheaper — and "close" means close to where
            # we are already going, not to where we stand.
            detour = gates.marginal("detour", distance=d, here=snap.feet, there=tuple(job["pos"]),
                                    via=self.committed_pos)
            offer(C(f"collect {job['kind']} job", jobs.JOB_VALUE.get(job["kind"], 3),
                    int(detour * priority.TICKS_PER_S) + 200,
                    lambda job=job: jobs.collect(ctx, job),
                    key=f"job {job['id']}", kind="maintenance", cap=30,
                    seconds=self.items_worth_s(snap, [(job.get("item"), job.get("count", 0))]),
                    detail=f"{job['kind']} at {tuple(job['pos'])}"))
        ready = [m for m in self.mem.machines(snap.dimension) if skills.pending_ready(m)]
        ready = [m for m in [self.pick_target(ready, snap.feet, pos_of=lambda m: m["origin"])] if m is not None]
        if ready:
            waiting = [(p.get("item"), p.get("count", 0)) for p in ready[0].get("pending", ())]
            offer(C("collect machine", 4, 1200, lambda: skills.collect_machine(ctx, ready[0]), kind="maintenance",
                    seconds=self.items_worth_s(snap, waiting)))

    def _goal_candidates(self, ctx, snap, night, pctx, offer, filtered, expand_all=False):
        """Plan every open goal, gate its steps, refuse with a reason or offer one candidate. Returns the eligible
        (goal, plan, step, run) tuples for reservations.

        How many goals get planned is decided by what came of the last rounds, not by a constant: `expand_hint` is
        the capacity hint, grown when a round needed more and let down slowly when it did not — and when the pool
        comes out EMPTY, the hint is ignored and everything left is planned (`expand_all`). Three recorded rounds
        had nothing to do for exactly this reason: the five goals that planned first were all cooling or short of
        torches, and the thirty behind them were dismissed as "outranked before planning" without being tried.
        """
        from . import actions as act
        from . import gates
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
            # One number, from the one door. `goal_worth_s` already asks what this costs — it moves the clock on by
            # how long the work takes (`takes_s`, read off the same price table) and discounts the saving from when
            # it lands. Subtracting the work here as well was the second yardstick: the same seconds charged
            # twice, and the ranking then disagreed with the score it was ranking for.
            return float(self.goal_worth_s(g, sstate, snap, prices))
        # Expand until there are enough choices, not a fixed number of them. A quota of six looked like a saving
        # until a night filtered all six ("gather waits for day") and the pool came down to one maintenance errand
        # — which is a queue, not a decision. Planning stops as soon as there is something to compare.
        # One pass: plan a goal, then immediately ask whether it can actually be used. Counting PLANS instead of
        # usable candidates was the trap — on a night when five goals plan fine and every one of them answers
        # "gather waits for day", the quota is spent and the pool comes down to a single maintenance errand.
        ranked_goals = sorted(ready_goals, key=rough_score, reverse=True)
        # What every open goal still wants, added up: the ceiling on taking more than this step asks for. Extra
        # beyond it is not cheap, it is imaginary — nothing in the world is waiting for it.
        demand = {}
        for g in ready_goals:
            for dim, want in act.target_of(g.needs).items():
                short = float(want) - float(vector.get(dim, 0))
                if short > 0:
                    demand[dim] = demand.get(dim, 0.0) + short
        open_names = {g.name for g in open_goals}
        plans, eligible, planned_goals = {}, [], 0
        budget = len(ranked_goals) if expand_all else max(MIN_CHOICES, min(MAX_EXPAND, self.expand_hint))
        outranked = 0
        for n, g in enumerate(ranked_goals):
            if not expand_all and (len(eligible) >= MIN_CHOICES or n >= budget):
                outranked += 1
                filtered[g.name] = "outranked before planning (there were already better things to compare)"
                continue
            planned_goals = n + 1
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
            if step is not None:
                # How much to take is a margin: one more unit is worth what the round's own prices say it saves
                # (`prices` is the dual of the same solve), and costs the pickup plus the slot it eats. Done here,
                # after the gate and before the step is priced, so the bigger parcel is what the body is promised.
                step = act.marginal_batch(step, prices, demand, max(0, 36 - snap.inv.used_slots()))
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
        # The plan is solved by now, so how long it takes is known exactly — the ranking pass had to guess it
        # from the price table.
        worth = {g.name: self.goal_worth_s(g, sstate, snap, prices,
                                           takes_s=sum(st.est for st in plan) / priority.TICKS_PER_S)
                 for g, plan, _step, _run in eligible}

        # How many eligible goals need each piece of work: the denominator of the sharing above.
        shared = {}
        for _g, _plan, _s, _r in eligible:
            for st in _plan:
                shared[(st.kind, st.token)] = shared.get((st.kind, st.token), 0) + 1
        # What the world's TERMINAL goods are worth right now, per unit: shelter, a bed, food, light, a sword, a
        # pickaxe (survival.END_DIMS). Value is computed from these downward, so a saving on a shared intermediate
        # is counted once instead of once per link of the chain that wants it.
        admitted = 0
        for g, plan, step, run in eligible:
            stat = f"{step.kind}:{step.token}" if step else None
            changed = stat in self.fail_sig and self.fail_sig[stat] != self.coarse
            share = priority.BACKGROUND if g.background else 1.0
            # What this goal is worth to everything after it: how much cheaper the terminal goods are once its
            # plan has run. Product, facility placed, room made, tool crafted — all of it is the same price
            # difference, so none of it has to be classified (and none can be classified wrongly, which is what
            # the two earlier functions kept doing to each other).
            # What this goal is worth to everything AFTER it: the same subtraction, over what its plan leaves
            # behind. One entry, so a goal's own worth and what it unlocks cannot be computed two different ways.
            made = {}
            for st in plan:
                if st.token and st.kind in ("mine", "gather", "take", "craft", "smelt", "hunt"):
                    made[st.token] = made.get(st.token, 0.0) + float(st.count or 1)
            future = self.items_worth_s(snap, list(made.items())) if made else 0.0
            unlocks_s = [(future, 1.0)]
            unlocks_s = [(v, p) for v, p in unlocks_s if v > 0]
            # Shared work is paid for once. A step another eligible goal also needs (the same iron, the same
            # trip) is split between them, so "mine iron" does not look twice as expensive as it is just because
            # two goals want it. This is the横向 saving the pool could never see.
            own = sum(s.est / max(1, shared.get((s.kind, s.token), 1)) for s in plan)
            cand = C(g.name, g.value * share, int(own) + 200, run,
                     key=step_key(g, step) if step else g.name,
                     seconds=worth[g.name], share=share,
                     risk_s=sum(cost_model.risk_s(s, s.est) for s in plan),
                     unlocks=unlocks_s,
                     success=priority.effective_success(gates.p(None, "success", mem=self.mem, key=stat), changed)
                     if stat else 1.0,
                     detail=str(step) if step else "finish", reserve=bag.reserved_ids(plan, g.needs),
                     commitment_s=priority.step_commitment(step.est, step.count,
                                                           atomic=bool(step.detail.get("batched"))) if step else None,
                     assumptions=self.assumptions_for(g, plan, step, snap))
            cand.goes_to = next((tuple(s.detail["pos"]) for s in plan if s.detail.get("pos")), None)
            cand.dimension_s = portal_s
            cand.off_route_s = getattr(cand, "off_route_s", 0.0)
            # Only crafting left, everything in the bag: no travel, so no waiting for a later route segment.
            cand.craft_only = bool(plan) and all(s.kind == "craft" for s in plan) and \
                g.dimension in (None, snap.dimension)
            admitted += 1 if offer(cand) else 0
        # What it took this time, remembered for the next round: enough to choose between is a property of the
        # world we are in, not a number anybody can write down once. Up at once when a round needed more, down a
        # step at a time when it did not, so a quiet stretch does not leave the hint stuck at its worst case.
        if not expand_all:
            self.expand_hint = max(MIN_CHOICES, planned_goals + 2) if admitted < MIN_CHOICES \
                else max(MIN_CHOICES, min(self.expand_hint, planned_goals + 2))
        if admitted < MIN_CHOICES and outranked and not expand_all:
            # Not enough got IN — which is the only count that matters, since a candidate the pool refuses is not
            # a choice. The goals that were never tried are the only place more can come from, so the hint does
            # not get to decide whether there is anything to compare.
            return self._goal_candidates(ctx, snap, night, pctx, offer, filtered, expand_all=True)
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
            # What Claude asked for is worth what its plan produces, priced like anybody else's plan; the boost
            # is a WEIGHT on top (`base`), which is what an instruction actually is — a thumb on the scale, not a
            # different currency.
            prices, _vector = self.prices(snap, cost_model)
            worth = sum(float(prices.get(st.token, 0.0) or 0.0) * float(st.count)
                        for st in plan if prices.get(st.token) not in (None, float("inf")))
            offer(C(name, base, sum(s.est for s in plan) + 200, lambda step=step: self.execute(ctx, step, night),
                    key=f"{name}/{step.kind}:{step.token}", kind="directive", detail=str(step),
                    seconds=round(worth * max(1.0, float(base)), 2)))

    def _fallback_candidates(self, ctx, snap, night, pctx, offer, filtered):
        """Always-available low-value work, so the pool is rarely empty.

        Offers only. Wandering work held back while a goal retries on the spot comes back because `candidates`
        offers this again with `staying` cleared when the pool is empty — through the same admission, which still
        asks each skill whether it can run at all.
        """
        C = priority.Candidate
        from . import skill as skillkit_
        for fname, fn, allowed, runs in self.fallbacks(ctx, snap, night):
            if allowed:
                base, cost = FALLBACK_BASE[fname]
                worth = self.fallback_worth_s(fname, snap)
                if worth is None:
                    # No price, no offer. There is no other currency to fall back on, and a fallback nobody can
                    # price is a fallback nobody can compare — which is how "explore" used to win nights.
                    filtered[fname] = "not priced in seconds yet (play.toml [yield] / [yield_s])"
                    continue
                c = C(fname, base, cost, fn, kind="fallback", cap=20, seconds=worth)
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
                # Nothing in SIGHT is not nothing known. A dozen sheep were written down at 21:07 and at 09:14 the
                # answer was still "could not find sheep", because the only question ever asked was what is within
                # 48 blocks NOW. Walk to what is remembered; exploring is what is left when nothing is remembered.
                spot = self.remembered_spot(snap_kinds, ctx.dimension, here)
                if spot is None:
                    return self.explore(ctx) or False
                if not nav.go_to(spot, self.policy_cache, range_=4, attempts=2):
                    # Could not GET there. That is a fact about the route, not about the note: the place is banned
                    # (so the next round prices the next one) and what memory says about it still stands.
                    self.ban(spot)
                    return False
                # Standing there and looking is the only thing that can settle a note — and it settles exactly the
                # ONE note we walked to. Retiring every note within 48 blocks of a single disappointing look is
                # how "could not find stone" survived a memory holding fourteen stone points.
                found = bool(find(snap_kinds, radius=6, limit=1) or entities(8, list(snap_kinds)))
                for kind in snap_kinds:
                    self.mem.confirm(kind, spot, ctx.dimension, found=found)
                if found:
                    self.mem.note_resource(snap_kinds[0], spot, ctx.dimension)
                return found
        if not nav.go_to(spot, self.policy_cache, range_=3, attempts=2):
            return False      # could not get there: the note may still be true, so it stands
        if note_kind:
            self.mem.confirm(note_kind, spot, ctx.dimension, found=True)
        # Where we now stand, written down for whatever we came for — not only for the handful of kinds that have
        # an entry in `LiveCosts.MAPPED`. `state_of` reads `at:<what>` from these notes, and until this line existed
        # nothing ever set it: the body walked onto the stone, remembered nothing, and planned the same walk again
        # every round until every stone goal had cooled.
        self.mem.note_resource(snap_kinds[0], spot, ctx.dimension)
        self.mem.note_search(snap_kinds[0], math.dist(spot, here))
        return True

    def pick_target(self, options, here, pos_of=lambda o: o["pos"]):
        """The nearest of these that is not blacklisted, or None.

        "shelter-3 not reachable" was logged every two minutes for an hour because the candidate was cooled by
        NAME and the site was chosen by distance alone — so the same unreachable shelter came back as soon as the
        cooldown expired, while a second one stood forty blocks away. Choosing from what is not banned is what
        makes "go to a shelter" mean the dimension rather than that one building; None is what makes digging a new
        one the cheapest thing left, which is the answer nobody had to write down.
        """
        left = [o for o in options if not self.banned(pos_of(o))]
        return min(left, key=lambda o: math.dist(pos_of(o), here), default=None)

    def banned(self, pos):
        """Is this place under an unreachability ban right now? One reader, shared with the cost model."""
        exp = self.blacklist.get(tuple(pos))
        return exp is not None and exp > time.time()

    def ban(self, pos, seconds=600):
        """Write down that we could not get there. Repeats escalate, exactly as `Context.ban` does — the same
        blacklist AND the same escalation counter, so a ban a skill wrote and a ban the brain wrote mean the same
        thing (the counter lives in `skillcore`, shared by everyone, and is why a place proven unreachable twice
        waits twice as long)."""
        self.ban_counts = getattr(self, "ban_counts", None) or skillcore._BAN_COUNTS
        skillcore.Context.ban(self, pos, seconds)

    def remembered_spot(self, kinds, dimension, here):
        """The nearest place memory says one of these was, or None. Sightings and resource points are one map for
        this question: a herd is a sighting, a grove is a resource point, and "where was one of these" is the same
        question about both."""
        best, best_d = None, None
        for kind in kinds:
            spots = [tuple(p) for p in self.mem.resources(kind, dimension)]
            spots += [tuple(x["pos"]) for x in self.mem.sightings(kind, dimension)]
            for pos in spots:
                d = math.dist(pos, here)
                if d <= 1.5:
                    continue          # we are standing on it and it is not here: that is what `confirm` is for
                if best_d is None or d < best_d:
                    best, best_d = pos, d
        return best

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

    def price_table(self, snap=None):
        """This round's shadow prices, per ITEM, for whoever needs to ask what a thing is worth in seconds.

        The solver prices DIMENSIONS: group tokens ("bed", "planks", "food") and the item ids that happen to be
        dimensions of their own. A chest is full of concrete items, so asking it raw answered None for bread and
        planks (no column makes them by that name) and 0.0 for a stone pickaxe (we are holding one, so another
        saves nothing this round) — and the looter walked past food, planks and a spare pickaxe.

        Three fills, in one place, so the looter, the bag and the yield measurement all price an item the same way:

          * an item with no price of its own inherits its GROUP's (a loaf is food, oak planks are planks);
          * a TOOL is worth what having one takes off the terminal goods, not what this round's plan needs;
          * everything else keeps the dual the solve produced.
        """
        try:
            snap = snap or Snapshot()
            prices, _vector = self.prices(snap, LiveCost(snap, self.blacklist, self.mem))
        except (McError, NotAvailable):
            return {}
        out = dict(prices)
        for group, members in GROUPS.items():
            per = prices.get(group)
            if not per or per == float("inf"):
                continue
            for item in members:
                # Group members are written both ways in this codebase ("bread" and "minecraft:bread"); a chest
                # reports the second. Fill both, or half the food in a village chest has no price.
                for token in (item, mid(item)):
                    if not out.get(token):
                        out[token] = per
        for kind in ("pickaxe", "axe", "sword", "shovel", "hoe"):
            # A tool is worth what having one takes off the terminal goods, over the horizon — the value door,
            # not a table of frequencies.
            worth = self.worth_of_change(snap, {act_tool_dim(kind, 1): 1, act_uses_dim(kind): 131}, slots=1)
            for material in TOOL_MATERIALS:
                item = f"minecraft:{material}_{kind}"
                if worth > (out.get(item) or 0.0):
                    out[item] = worth
        return out

    def bag_worth_s(self, snap, prices):
        """What the bag is worth in seconds — what everything in it takes off the terminal goods.

        The measurement half of `[yield]`: what an attempt actually BROUGHT BACK is this before and after. Priced
        by the one entry, so a stack of dirt is worth what a stack of dirt is worth (nothing) even though it cost
        seconds to dig.
        """
        counts = {}
        for slot in snap.inv.slots:
            counts[slot["id"]] = counts.get(slot["id"], 0) + slot.get("count", 1)
        return self.items_worth_s(snap, list(counts.items()))

    def items_worth_s(self, snap, items):
        """Seconds a pile of items is worth: `V(now) − V(now ⊕ pile)`. One place, so a corpse, a chest and a
        furnace's output are priced the same way — and none of them by "what it cost to make", which said a thing
        this world cannot make is worth nothing at all."""
        from . import actions as act
        effect = {}
        for item, count in items or ():
            if not item or not count:
                continue
            for dim, delta in act.produce(item, float(count)).items():
                effect[dim] = effect.get(dim, 0.0) + delta
        return self.worth_of_change(snap, effect, slots=len(items or ()))

    def note_yield_of(self, name, expected_s, worth_before):
        """Record what that attempt gave against what was hoped for. One call, at the one place a candidate runs,
        so every `[yield]`-priced thing is corrected by this world: chests that keep coming back empty stop being
        worth the walk, and a rich seam raises the prior for mining instead of being forgotten."""
        if not expected_s or worth_before is None:
            return
        try:
            snap = Snapshot()
            prices, _vector = self.prices(snap, LiveCost(snap, self.blacklist, self.mem))
            self.mem.note_yield(name, max(0.0, self.bag_worth_s(snap, prices) - worth_before), expected_s)
        except (McError, NotAvailable) as e:
            # No measurement is better than a wrong one; the prior stands. But say so: a yield that never gets
            # corrected is a prior that never learns.
            api.swallowed("note_yield_of: measuring what came back", e)

    def yield_worth_s(self, name, snap):
        """Seconds one attempt of this is worth: what it brings back, through the one entry.

        `[yield]` says WHAT comes back, in items, and nothing about what that is worth; `[yield_s]` is for what
        produces no item (an enchantment, a potion, a leg of exploring), read at its cautious end while nobody has
        measured it. `memory.yield_rate` scales both by what this world actually gives back.
        """
        from .survival import CONFIG as _PLAY
        items = _PLAY.get("yield", {}).get(name)
        flat = _PLAY.get("yield_s", {}).get(name)
        total = beliefs.cautious(f"yield_s.{name}") if flat else 0.0
        if items:
            total += self.items_worth_s(snap, list(items.items()))
        from . import gates
        return round(total * gates.p(None, "yield", mem=self.mem, name=name), 2) if total else 0.0

    def progress_worth_s(self, name):
        """Seconds of the run this stage removes, or 0 when it is not a stage of it (`play.toml [progress]`)."""
        from .survival import CONFIG as _PLAY
        return float(_PLAY.get("progress", {}).get(name, 0.0))

    def fallback_worth_s(self, name, snap):
        """Seconds a fallback is worth. Lighting up removes the dark, which is a lever on the same subtraction;
        mining and exploring are worth what they bring back (`yield_worth_s`)."""
        if name != "light up":
            return self.yield_worth_s(name, snap) or None
        return self.worth_of_change(snap, {"lever:dark": -1}, slots=0)

    def situation(self, snap, cost_model=None, region=None):
        """The facts the four doors are allowed to look at, for this round.

        Built once and passed down: the columns this world offers, the body in it, what has been measured. A door
        that has to go and fetch any of this is a door that depends on the middle of the package.
        """
        from . import actions as act, gates, want
        cost_model = cost_model or LiveCost(snap, self.blacklist, self.mem)
        columns, vector = self.action_table(snap, cost_model)
        return gates.Situation(state=vector, columns=columns, region=region, policy=self.policy_cache,
                               wants=want.priced_wants(),
                               route=nav.estimate_price_s, mem=self.mem, costs=act.LiveCosts(cost_model),
                               here=tuple(snap.feet),
                               hp=snap.get("health", 20), inv_free=max(0, 36 - snap.inv.used_slots()),
                               dark=is_dark(snap), underground=snap.get("skyLight", 15) <= COVERED_SKY)

    def value_world(self, snap, cost_model=None):
        """(value_of, evolve) for `value.worth_s`: what finishing costs from a state, and where play leaves us.

        Both come from the doors: `gates.V` prices a state (terminal goods in parts, plus what being without them
        costs), and the dynamics move the levers the same model reads — hunger falls, tools wear. Nothing here
        computes seconds of its own; it decides which state to ask about.
        """
        from . import actions as act, gates
        from .survival import CONFIG as _PLAY
        cost_model = cost_model or LiveCost(snap, self.blacklist, self.mem)
        costs = act.LiveCosts(cost_model)
        stages = float(sum(v for k, v in _PLAY.get("progress", {}).items() if not self.stage_done(k, snap)))
        base = survival_state(snap, self.mem)
        # The columns this world offers, built once for the round. The door is handed them; it does not go and
        # build a table of its own, which is what made the bottom of the package depend on the middle.
        columns, _vector = self.action_table(snap, cost_model)
        cache = {}

        def value_of(state):
            key = tuple(sorted((d, round(float(v), 3)) for d, v in state.items() if v))
            if key not in cache:
                here = gates.Situation(state=state, columns=columns, costs=costs, mem=self.mem,
                                       hp=state.get("lever:hp", base.get("hp", 20)),
                                       inv_free=state.get("bag_free", base.get("bag_free", 36)))
                parts = dict(gates.V(here, parts=True, sstate=self._sstate_for(base, state)))
                parts["run"] = parts.pop("loss", 0.0) + stages
                cache[key] = parts
            return cache[key]

        drain = float(_PLAY["risk"]["food_drain_s"])
        wear = {k: gates.p(None, "tool_use", mem=self.mem, kind=k) for k in ("pickaxe", "axe", "sword", "shovel")}

        def evolve(state, t):
            out = dict(state)
            if out.get("food"):
                out["food"] = max(0.0, float(out["food"]) - float(t) / drain)
            for kind, rate in wear.items():
                dim = act.uses_dim(kind)
                if out.get(dim):
                    out[dim] = max(0.0, float(out[dim]) - rate * float(t))
            return out
        return value_of, evolve

    def _sstate_for(self, base, state):
        """This round's survival levers, with what an imagined state changes about them. The levers the solver has
        no dimension for (health, the dark, room in the bag) arrive as "lever:" deltas."""
        from . import survival
        out = dict(base)
        for key, delta in state.items():
            if str(key).startswith("lever:"):
                name = str(key).split(":", 1)[1]
                now = base.get(name, 0)
                out[name] = (float(now) + float(delta)) if isinstance(now, (int, float)) else bool(delta)
        for lever, dim in survival.END_DIMS.items():
            have = float(state.get(dim, 0) or 0)
            if lever == "food_items":
                out[lever] = have
            elif lever == "torches":
                out[lever] = have >= survival.TORCHES_MEAN
            elif lever in ("sword", "pickaxe"):
                out[lever] = max(float(base.get(lever, 0)), 1.0 if have else 0.0)
            else:
                out[lever] = bool(have) or bool(base.get(lever))
        return out

    def stage_done(self, name, snap):
        """Has this stage of the run already happened? Read off the same goals the pool offers, so the ladder and
        the goal list cannot disagree about what is left."""
        for g in goals(snap, self.mem):
            if g.name == name:
                return bool(g.done())
        return True

    def worth_of_change(self, snap, effect, slots=None, takes_s=0.0):
        """Seconds a change to the world would be worth: the ONE entry every candidate goes through.

        `effect` is a delta on the state vector — items, terminal goods, or a "lever:" (health, bag room, the
        dark) — and the answer is `value.worth_s`: what finishing costs now, minus what it costs once the change
        has happened, over the horizon, less the slots it eats. Healing, tidying, looting, mining, building and
        walking to a village all meet here, which is the only way they can be compared.
        """
        from . import value
        effect = {d: v for d, v in (effect or {}).items() if v}
        if not effect:
            return 0.0
        value_of, evolve = self.value_world(snap)
        _table, vector = self.action_table(snap, LiveCost(snap, self.blacklist, self.mem))
        carried = [d for d in effect if not str(d).startswith(("at:", "stage:", "tool:", "uses:", "lever:"))]
        return max(0.0, value.worth_s(dict(vector), effect, value_of=value_of, evolve=evolve,
                                      horizon_s=priority.DISCOUNT_HORIZON_S,
                                      bag_free=max(0, 36 - snap.inv.used_slots()),
                                      slots=float(slots if slots is not None else max(1, len(carried))),
                                      takes_s=float(takes_s or 0.0)))

    def goal_worth_s(self, g, sstate, snap, prices, takes_s=None):
        """What this goal is worth, in seconds. The ONE ladder, used both to decide which goals get planned and to
        score the ones that did — they drifted apart once, and the ranking then threw away the goal the score would
        have chosen ("recover items after death" dismissed as outranked with a full corpse on the ground).

            what it SAVES        a terminal effect, through the survival model
            what it ADVANCES     a stage of the run (`play.toml [progress]`), in seconds of the run
            what it BRINGS BACK  an expected yield, priced by the table and corrected by what this world gives
            what is LYING THERE  a corpse is worth what is in it, not a flat death cost
            what it UNLOCKS      everything else: what having its products makes cheaper

        `takes_s` is how long getting there takes. It belongs INSIDE the comparison (`value.worth_s`), because a
        thing that arrives in three hundred seconds is not the same thing as one underfoot — and without it every
        plan, near or far, long or short, was priced as if it finished instantly.
        """
        from . import actions as act, gates, survival as _sv, value
        from .survival import CONFIG as _PLAY
        if g.name == "recover items after death":
            # What is lying there, priced like anything else: the pile IS the effect.
            dead = self.mem.recent_death(snap.dimension)
            return float(self.items_worth_s(snap, dead.get("carried") if dead else []))
        # What this goal LEAVES US HOLDING that we do not hold already. The shortfall, not the target: adding the
        # whole requirement on top of a bag that already contains it prices a second copy — which is why carrying
        # the wool made the bed worth LESS than not carrying it (three more wool, no more bed, one more slot).
        _table, vector = self.action_table(snap, LiveCost(snap, self.blacklist, self.mem))
        effect = {}
        if g.effect:
            for dim, on in g.effect.items():
                dim = _sv.END_DIMS.get(dim, dim)
                want = 1.0 if on is True else float(on)
                effect[dim] = max(0.0, want - float(vector.get(dim, 0)))
        for dim, want in act.target_of(g.needs).items():
            short = max(0.0, float(want) - float(vector.get(dim, 0)))
            effect[dim] = max(effect.get(dim, 0.0), short)
        effect = {d: v for d, v in effect.items() if v > 0}
        if self.progress_worth_s(g.name):
            # A stage of the run: what it removes is not an item, it is the stage itself.
            effect[f"stage:{g.name}"] = 1
        brought = _PLAY.get("yield", {}).get(g.name) or {}
        for token, n in brought.items():
            for dim, delta in act.produce(token, float(n)).items():
                effect[dim] = effect.get(dim, 0.0) + delta
        if not effect:
            return 0.0
        value_of, evolve = self.value_world(snap)
        stage_s = self.progress_worth_s(g.name)

        def valued(state):
            # A stage is seconds of the run, not a dimension the solver knows: it belongs to the "run" part, where
            # it adds rather than competing with the terminal goods.
            parts = dict(value_of({d: v for d, v in state.items() if not str(d).startswith("stage:")}))
            if state.get(f"stage:{g.name}"):
                parts["run"] = parts.get("run", 0.0) - stage_s
            return parts
        slots = max(1.0, sum(1 for d in effect if not str(d).startswith(("at:", "stage:", "tool:", "uses:"))))
        if takes_s is None:
            # How long this will take, before a plan exists: the shortfall read off the price table, through the
            # time door.
            takes_s = gates.takes_s(None, gates.Short(act.target_of(g.needs), prices=prices,
                                                      state=self.action_table(snap,
                                                                              LiveCost(snap, self.blacklist,
                                                                                       self.mem))[1]))
        return max(0.0, value.worth_s(dict(vector), effect, value_of=valued, evolve=evolve,
                                      horizon_s=priority.DISCOUNT_HORIZON_S,
                                      bag_free=max(0, 36 - snap.inv.used_slots()), slots=slots,
                                      takes_s=takes_s))

    def rough_unlocks(self, g, prices, sstate, snap=None):
        """What a goal with no terminal effect is worth: what HAVING its products takes off the terminal goods.
        One line, through the value door — the name survives because the ranking reads it."""
        from . import actions as act
        if snap is None:
            return 0.0
        return self.worth_of_change(snap, {d: float(v) for d, v in act.target_of(g.needs).items()})

    def uses_of(self, dim):
        """How many times what this dimension names will be reached for, within the horizon.

        One for a thing used once — a bed, a bucket of water poured out. For a TOOL, how often that kind is used
        times how much of it is left (`actions.expected_uses`): a pickaxe saves its few seconds two hundred times
        a day and a bucket twice, and pricing both as a single use is why the agent made the bucket and the sword
        and never the pickaxe it needs all day.
        """
        from . import actions as act, gates
        if not dim.startswith("tool:"):
            return 1.0
        _, kind, _tier = dim.split(":")
        left = act.TOOL_USES.get("iron", 250)      # a fresh tool of the middling material
        return max(1.0, gates.p(None, "tool_left", mem=self.mem, kind=kind, left=left,
                                horizon_s=priority.DISCOUNT_HORIZON_S))

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
            vector = act.state_of(snap, self.mem, reachable=lambda kinds: costs.reach_s(kinds) != math.inf)
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
        """Health per second the surroundings are charging us right now — a RATE, and an engine of the chance
        door: `gates.marginal("blood")` turns it into seconds when anyone needs seconds. The one number a plan's
        premise rests on."""
        from . import perception, threat
        rows, _ids = perception.threats_seen()
        if not rows and perception.hurt_rate() <= 0:
            return 0.0
        prot = threat.protection(snap.get("armor", 0), snap.inv.offhand() == "minecraft:shield")
        here = (snap.state["x"], snap.state["y"], snap.state["z"])
        return perception.pressure_now(here, rows, prot)

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
        coming = [h for h in rows if h[3] in threat.MOBS
                  and estimate.arrival_s(here, h) != float("inf")]
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
        if snap.get("skyLight", 15) <= COVERED_SKY:
            return True
        try:
            if skills.enclosed():
                return True
        except (tape.ReplayMiss, McError, NotAvailable):
            # A decision must be replayable, and "am I walled in" is a world read. On a recorded round the answer
            # is not in the tape, so fall back to what memory knows — which is where the other half of this test
            # already lives. Thirty of thirty-six recorded rounds died here instead of being judged.
            pass
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
            site = self.pick_target(self.mem.sites(snap.dimension, kinds=["home", "shelter"]), snap.feet)
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
        # Threats are answered by the perception thread at its own cadence (perception.bid → arbiter lease), not
        # here: this ran once a round, could pick `ignore` as if doing nothing were a rescue, and held the body
        # while the real answer waited for a lease it could never get. One decider, not two.
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
            # The rescue layer prices what it would change exactly like every other candidate: one entry, levers
            # for what the solver has no dimension for (health, the dark, room in the bag).
            levers = {f"lever:{k}" if k in ("hp", "dark", "bag_free") else k:
                      (1 if v is True else (0 if v is False else v)) for k, v in effect.items()}
            if "lever:hp" in levers:
                levers["lever:hp"] = float(levers["lever:hp"]) - float(s.get("health", 20))
            return self.worth_of_change(snap, levers, slots=0)

        retreat = must_retreat(snap)
        if retreat:
            # Everything the Nether costs is ahead of us until we are out: price it as a day of the risk we carry.
            offer(C(f"retreat from the Nether ({retreat})", 0, 4000,
                    lambda: nether.use_portal(ctx, "minecraft:overworld"), kind="maintenance", cap=30,
                    seconds=self.worth_of_change(snap, {"lever:dimension_risk": 1}, slots=0)
                    or survival.expected_loss(sstate), detail=retreat))
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
        if night and (carried_bed or find(BASE_MARKERS["bed"], radius=48, limit=1)):
            offer(C("sleep", 0, 400, lambda: skills.sleep(ctx, self.policy(snap, True)), kind="maintenance",
                    cap=20, seconds=worth({"bed": True, "sheltered": True}), detail="skip the night"))
        if not self.sheltered(snap):
            # The nearest shelter we can actually get to: one that proved unreachable is banned, and when they all
            # are, this offer simply is not made — which is what lets "dig in" and "wall in" win the night.
            site = self.pick_target(self.mem.sites(snap.dimension, kinds=["home", "shelter"]), snap.feet)
            if site is not None:
                eta = travel_ticks(snap.feet, site["pos"])
                offer(C("return to shelter", 0, eta + 200,
                        lambda: self._go_to_site(site, snap), kind="maintenance", cap=60,
                        seconds=worth({"sheltered": True}),
                        detail=f"{site['name']} ~{eta // 20}s away"))
            if not skills.materials_missing(blueprints.SHELTER):
                offer(C("build shelter", 0, int(skillkit.expected(skills.build_shelter, ctx) * 20) or 2400,
                        lambda: skills.build_shelter(ctx), kind="maintenance", cap=120,
                        seconds=worth({"sheltered": True})))
            if night:
                _c = C("dig in", 0, 600, lambda: skills.dig_in(ctx), kind="maintenance", cap=30,
                        seconds=worth({"sheltered": True}))
                _c.runs = (skills.dig_in, (ctx,))
                offer(_c)
                offer(C("wall in", 0, 800, lambda: skills.pod(ctx), kind="maintenance", cap=30,
                        seconds=worth({"sheltered": True})))

    def _go_to_site(self, site, snap):
        if not nav.go_to(tuple(site["pos"]), self.policy(snap, snap.night), range_=4):
            # Not reachable is a fact about the PLACE. Written down, the next round prices the next shelter (or
            # digging one) instead of this same walk a minute from now.
            self.ban(tuple(site["pos"]))
            raise api.NavFailed(f"{site['name']} not reachable")

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
        """How this agent, right now, converts health into seconds — the scarcity door, as a function of hp.

        Blood is scarce the way an inventory slot is scarce: `gates.marginal("blood")` is what one point of it
        costs here, and everything that prices damage multiplies by that one number."""
        from . import gates
        sstate = survival_state(snap, self.mem)
        per_hp = gates.marginal("blood", sstate=sstate)
        return lambda dhp: per_hp * float(dhp)

    def engage(self, decision, s, ctx=None):
        """Carry out one threat answer — an Option from the pool or a Decision from the emergency; both name a
        `kind` and a `target`. May fail: it is called through `attempt`, never from `reflexes`."""
        if decision.kind == "fight":
            api.run({"type": "attack", "entity": decision.target}, wait=45)
        elif decision.kind == "evade":
            if not nav.go_to(decision.target, self.policy_cache, range_=3, attempts=1, min_hp=0):
                raise NotAvailable(f"could not get away to {decision.target}")
        elif decision.kind == "wall_in":
            ctx = ctx or skills.Context(self.mem, self.policy_cache, s["dimension"], self.blacklist, prices=self.price_table)
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
                skillkit_mine(self.policy_cache, (feet[0], feet[1] - 1 - i, feet[2]), collect=False, wait=20)
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

    def heal(self, ctx, snap=None):
        """Regenerate: eat up to a full stomach, then stand still long enough for the health to come back.

        Two candidates offered this for a long time and neither could run it — the method the pricing assumed
        simply was not there. It is nothing but the game's own rule: health regenerates while food is above
        REGEN_FOOD, and it regenerates about a point every few seconds, so the work is eating and then WAITING,
        which is the one thing the round loop otherwise never lets us do.

        It gives the body back the moment a faster layer wants it (`api.CommitmentExpired` from the arbiter) and
        the moment the reason for waiting is gone: full health, or nothing left to eat with.
        """
        from . import survival as _survival
        risk = _survival.CONFIG["risk"]
        per_hp_s, floor = float(risk["regen_s_per_hp"]), float(risk["regen_food_floor"])
        snap = snap or Snapshot()
        # How long this can take is not a constant: it is what the model already says regeneration costs, with
        # room for the bites. A budget invented here would be a second opinion about the same seconds.
        deadline = time.time() + per_hp_s * max(1.0, 20 - snap.get("health", 20)) * 2
        while time.time() < deadline:
            snap = Snapshot()
            hp, food = snap.get("health", 20), snap.get("food", 20)
            if hp >= 20:
                return True
            if food < floor:
                # Nothing to regenerate ON: eat, raw meat included — a full bag and an empty stomach is the
                # state this whole candidate exists to leave.
                if not skills.eat(raw_ok=not food_count(snap.inv)):
                    return False
                continue
            stop = api.consume_interrupt()     # a threat, a reflex, the player: waiting is the first to drop
            if stop:
                log(f"   healing stood down: {stop}")
                return False
            time.sleep(max(1.0, per_hp_s / 2))
        return snap.get("health", 20) >= 20

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
        wear_before, began = tool_wear(), time.time()
        try:
            fn()
            self.retry.succeeded(name)
            # What that step wore out, and over how long: the frequency each tool is priced by (`actions.use_rate`)
            # is measured here rather than believed forever.
            note_tool_wear(self.mem, wear_before, tool_wear(), time.time() - began)
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
