"""The decision loop: reflexes → phase bookkeeping → night handling → cost-scored goal selection → one verified
step → repeat. Goals are expressed as requirements; the planner turns them into steps; only the first step of the
best plan runs per round, so every round re-plans against the real inventory (DEPS-style selector)."""
import math
import time
import traceback

import json
import os

from . import (api, bag, blueprints, brewing, combat, directives, end, farming, fluids, jobs, lookahead, loot,
               nav, nether, paths, priority, retry, route, skills, tape, ui, upkeep, world)
from . import skill as skillkit
from .api import GameUnreachable, McError, NotAvailable, PlayerTookControl, TaskStuck, log
from .data import BASE_MARKERS, GROUPS, HAND_MINEABLE_SUFFIX, bare, mid
from .knowledge import UNDERGROUND_KINDS
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
                 background=False, action=None, unlocks=(), feasible=None, dimension="minecraft:overworld"):
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


def tool_ok(inv, kind, tier, min_left=10):
    return any(t >= tier and d >= min_left for t, d, _ in inv.tools(kind))


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
COVERED_SKY = 4      # sky light at or below this = under rock (a cave with a distant opening reads 1–3)
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


NEUTRAL_MOBS = {"minecraft:zombified_piglin", "minecraft:piglin", "minecraft:enderman", "minecraft:wolf",
                "minecraft:bee", "minecraft:iron_golem", "minecraft:polar_bear", "minecraft:llama", "minecraft:panda",
                "minecraft:dolphin", "minecraft:spider", "minecraft:cave_spider"}


def is_threat(e):
    """Hostile and worth fighting on sight. The mod flags every Monster subclass hostile, but hitting a zombified
    piglin brings the whole group, a piglin ruins bartering and an enderman chases you — neutral mobs are left
    alone (spiders are neutral in daylight but ignoring them costs little)."""
    return bool(e.get("hostile")) and e.get("type") not in NEUTRAL_MOBS


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


def goals(snap, mem):
    inv = snap.inv
    home = mem.home()
    g = [
        # Phase 0 — survive and tool up (route: ~20 logs, stone tools, food, bed, light).
        Goal("stone pickaxe", 10, lambda: tool_ok(inv, "pickaxe", 1), [("tool", "pickaxe", 1)],
             superseded_by="iron pickaxe"),
        # Phantoms come after 3 sleepless nights: the bed grows more urgent with every missed night.
        Goal("bed to carry", 9, lambda: inv.count("bed") > 0, [("bed", 1)]),   # urgency grows per missed night
        # Same bed through string (spiders) — the pool takes whichever source is cheaper right now.
        Goal("bed from string", 8, lambda: inv.count("bed") > 0, [("minecraft:white_bed", 1)]),
        Goal("food (≥8)", 8, lambda: food_count(inv) >= 8, [("food", 16)]),
        Goal("stone sword", 6, lambda: tool_ok(inv, "sword", 1), [("tool", "sword", 1)], superseded_by="iron sword"),
        Goal("torches (≥8)", 6, lambda: inv.usable("minecraft:torch") >= 8, [("minecraft:torch", 24)]),
        Goal("carried crafting table", 5, lambda: inv.count("minecraft:crafting_table") > 0,
             [("minecraft:crafting_table", 1)]),
        Goal("carried furnace", 4, lambda: inv.count("minecraft:furnace") > 0, [("minecraft:furnace", 1)]),
        Goal("stone axe", 4, lambda: tool_ok(inv, "axe", 1), [("tool", "axe", 1)]),
        Goal("building blocks (≥32)", 3, lambda: inv.count("building") >= 32, [("stone", 64)]),
        Goal("ladders (≥4)", 2, lambda: inv.count("minecraft:ladder") >= 4, [("minecraft:ladder", 12)]),
        # Phase 1 — iron: what the route actually needs (bucket, pickaxe, shield, flint & steel) + armor.
        Goal("iron pickaxe", 9, lambda: tool_ok(inv, "pickaxe", 2), [("tool", "pickaxe", 2)], phase=1),
        Goal("bucket", 9, lambda: inv.count("minecraft:bucket") + inv.count("minecraft:water_bucket") > 0,
             [("minecraft:bucket", 1)], phase=1),
        Goal("shield", 7, lambda: inv.count("minecraft:shield") > 0, [("minecraft:shield", 1)],
             after=skills.equip_armor, phase=1),
        Goal("flint and steel", 6, lambda: inv.count("minecraft:flint_and_steel") > 0,
             [("minecraft:flint_and_steel", 1)], phase=1),
        Goal("iron sword", 5, lambda: tool_ok(inv, "sword", 2), [("tool", "sword", 2)], phase=1),
        Goal("iron armor", 8, lambda: wearing_at_least(inv, "iron"),
             [(f"minecraft:iron_{p}", 1) for p in ("helmet", "chestplate", "leggings", "boots")
              if not bare(inv.worn({"helmet": "head", "chestplate": "chest", "leggings": "legs", "boots": "feet"}[p]))
              .startswith(("iron_", "diamond_", "netherite_"))],
             after=skills.equip_armor, phase=1),
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
        Goal("recover items after death", 12, lambda: False, [], action=upkeep.recover_items,
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
        self.idle_since = None
        self.history = []       # (time, feet, progress signature) per round, for "stuck in place" detection
        self.last_quick = None
        self.policy_cache = nav.Policy(before_segment=self.segment_reflexes)
        self.retry = retry.Retry()   # state-aware failure policy (retry.py)
        self.sig = None              # world state of this round (retry decisions)
        # Three "found nothing" answers for the same goal inside one route segment: that resource isn't here.
        self.prep_tokens = {}        # goal → tokens that only its look-ahead preparation needs (not the goal itself)
        self.seg_name = None         # active route segment; per-segment search misses reset when it changes
        self.seg_misses = {}         # (segment, goal) → how often it found nothing of its kind here
        self.coarse = None           # coarser state (success-rate resets)
        self.committed = None        # goal name kept until done, failed or clearly beaten
        self.last_choice = (None, 0)
        self.last_hold_log = 0
        self.escalated = {}
        self.done_seen = set()
        self.last_milestone = time.time()
        self.last_track = 0

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
            self.mem.log_death((s["blockX"], s["blockY"], s["blockZ"]), s["dimension"])
            log("died → respawning")
            api.post("/respawn")
            time.sleep(2)
            s = api.get("/state")
        if s["screen"] == "class_433":
            api.post("/resume")
        # Self-preservation outranks everything (Mindcraft's first mode): drowning or burning → get out now.
        if s["inWater"] and s["air"] < 150:
            log(f"reflex: low air ({s['air']}) → swimming up")
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
        threats = [e for e in entities(8) if is_threat(e) and e["distance"] <= 5]
        if threats and s["health"] > 10:   # hurt: survival mode flees instead of trading hits
            log(f"reflex: fighting {bare(threats[0]['type'])}")
            api.run({"type": "attack", "entity": threats[0]["id"]}, wait=45)
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
        if s["food"] <= 14:
            skills.eat()
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
        dug = [(t["x"], t["y"], t["z"]) for t in tasks if t.get("type") == "mine" and "x" in t]
        if dug:
            self.mem.mark_dirty_near(dug, api.get("/state")["dimension"])
        self.reflexes()

    def segment_reflexes(self, tasks):
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
        n, wait, worth_logging = self.retry.failed(name, cause, str(err), self.sig, now)
        if cause == "tool" and n < 3:
            self.retry.cap(name, 0, now)   # the tool goal runs next round; retry right after it
            wait = 0
        if worth_logging:
            log(f"{'~~' if isinstance(err, NotAvailable) else '!!'} {name}: {err} "
                f"({cause}, ×{n} here; retry on change or in {wait}s)")

    def ready(self, name):
        return self.retry.ready(name, self.sig, time.time())

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

        self.track(snap)
        self.scan_resources(snap)
        if self.survival(snap, ctx):
            return
        if self.safety(snap, ctx):
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
        pick = priority.choose(pool, self.committed, self.stalled_seconds())
        tape.end(self, pick, pool, filtered, force)
        if pick is None:
            if night and self.sheltered(snap):
                # Waiting out the night sealed in / underground is the survival action itself, like sleeping.
                self.idle_since = None
                self.hold_log("night: sheltered, nothing safe to do; holding")
            else:
                self.idle_since = self.idle_since or now
                self.hold_log("nothing runnable; holding")
            api.run({"type": "wait", "ticks": 100}, wait=15)
            return
        self.idle_since = None
        self.explain(pick, pool, filtered)
        self.committed = pick.name
        bag.RESERVED = bag.RESERVED | pick.reserve   # the running plan's items too (a food-stock hunt threw its meat)
        # Failures cool down this candidate's key only (a goal's current step); everything else stays available.
        before = self.retry.entries.get(pick.key, {}).get("since")
        self.attempt(pick.key, pick.run, cooldown=pick.cap)
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
        """Everything the pool may do now as priority.Candidates — only what can actually succeed here (cheap
        feasibility checks first) — and {name: why} for what was filtered out. `force` lets cooling fallbacks in
        (the 15 s idle rule)."""
        weights = priority.load()
        pool, filtered = [], {}

        # The committed goal just failed and is in its retry window: errands that walk away (storing, open space,
        # exploring, strip mining) wait, so the retry happens on the spot instead of after a 20–50 s round trip.
        recent = self.recent_fail
        staying = recent and recent[0] == self.committed and time.time() - recent[1] < 90
        WANDERING = {"deposit", "open space", "explore", "strip mine", "light up"}

        segment = self.route_segment(snap) if route.current() else None
        if (segment["name"] if segment else None) != self.seg_name:
            self.seg_name = segment["name"] if segment else None
            self.seg_misses.clear()      # a new segment is a new place: everything is worth one more look
        # The route only relies on skills the bench proved: a goal whose scenario fails for this code waits while
        # other goals of the segment can run (it stays offered when it is the segment's only way forward).
        from . import scenarios
        bench_failing = scenarios.failing_goals() if segment is not None else set()
        seg_goals = set(segment["goals"]) if segment is not None else set()

        def offer(c):
            w, banned = priority.weight_for(c.name, weights)
            # Craft-only goals skip the segment filter: a gold helmet from 5 carried ingots is a click, not a detour
            # (it waited a whole slice for its segment).
            if segment is not None and c.kind == "goal" and not route.allowed(segment, c.name) \
                    and not getattr(c, "craft_only", False):
                filtered[c.name] = f"route segment '{segment['name']}' doesn't need it"
            elif c.name in bench_failing and seg_goals - bench_failing - {c.name}:
                filtered[c.name] = "its bench scenario fails for this code; another segment goal first"
            elif staying and c.name in WANDERING:
                filtered[c.name] = f"staying at {self.committed} while it retries"
            elif banned:
                filtered[c.name] = "banned by Claude"
            elif not self.ready(c.key) and not (force and c.kind == "fallback"):
                filtered[c.name] = "cooling after a failure"
            elif c.kind == "goal" and "/" in c.key and not self.ready("step:" + c.key.split("/", 1)[1]):
                filtered[c.name] = f"its step {c.key.split('/', 1)[1]} just failed for another goal"
            elif c.kind == "goal" and self.seg_misses.get((self.seg_name, c.name), 0) >= SEG_MISSES:
                filtered[c.name] = f"nothing of this kind found in segment '{self.seg_name}' ({SEG_MISSES}×)"
            elif c.kind != "fallback" and self.retry.exhausted(c.key, self.sig):
                # Failed EXHAUSTED_AFTER times in this very state: a longer wait is not a new method. The simulation
                # picked "food (≥8)/hunt:porkchop" 60 times in a row while every try failed. Something else runs
                # (another source, another goal) until the state changes; Claude hears about it once.
                filtered[c.name] = f"exhausted here ({c.key}); needs another method or a changed state"
                self.escalate(f"exhausted:{c.key}", f"{c.key} failed {retry.EXHAUSTED_AFTER}× in the same state")
            else:
                c.weight = w
                pool.append(c)

        # Maintenance: urgency curves decide how much it matters right now.
        C = priority.Candidate
        if not self.has_pickaxe(snap):
            offer(C("replace pickaxe", 12, 2000, lambda: self.replace_tool(ctx, "pickaxe"), urgency=10,
                    kind="maintenance", cap=30))
        bag_urg = priority.bag_urgency(snap.inv.used_slots())
        if bag_urg:
            if not skills.store_plan(snap.inv.slots):
                filtered["deposit"] = "nothing worth storing"
            elif skills.can_store_here(ctx, local_only=night):
                offer(C("deposit", 3, 1200, lambda: skills.deposit(ctx, local_only=night), urgency=bag_urg,
                        kind="maintenance"))
            # Feasibility first: throwing needs open room here; in a shaft the candidate is moving to room instead
            # (tidy used to be picked, fail "no open side", cool down and be picked again — 23 times in 30 min).
            x, y, z = snap.feet
            throwable = skills.free_slots_plan(snap.inv.slots,
                                               need=max(0, snap.inv.used_slots() - (36 - skills.FREE_SLOTS_TARGET)))
            if not throwable:
                filtered["tidy"] = "nothing worth throwing away"
            elif skills.throw_direction(world.Region((x - 3, y - 1, z - 3), (x + 3, y + 2, z + 3)), snap.feet):
                offer(C("tidy", 3, 300, lambda: skills.tidy_inventory(ctx), urgency=bag_urg, kind="maintenance", cap=20))
            elif not (night and self.sheltered(snap)):   # never leave a night shelter just for the bag
                offer(C("open space", 3, 400, lambda: skills.move_to_open_space(ctx), urgency=bag_urg,
                        kind="maintenance", cap=30))
            else:
                filtered["tidy"] = "no room to throw in this shelter; waits for day"
        if not night:
            dirty = [s for s in self.mem.sites(snap.dimension) if s.get("dirty")]
            if dirty:
                offer(C("repair", 2, 2000, lambda: skills.repair_site(ctx, dirty[0]), kind="maintenance"))
            # Every background job (furnace, crop, sapling, breeding cooldown) is one candidate; nearby ones get a
            # proximity bonus so errands are merged into the route instead of separate trips.
            ready_jobs = sorted((j for j in self.mem.jobs(snap.dimension)
                                 if skills.job_ready(j) and math.dist(j["pos"], snap.feet) <= 96),
                                key=lambda j: math.dist(j["pos"], snap.feet))[:3]
            for job in ready_jobs:
                d = math.dist(job["pos"], snap.feet)
                offer(C(f"collect {job['kind']} job", jobs.JOB_VALUE.get(job["kind"], 3),
                        travel_ticks(snap.feet, job["pos"]) + 200, lambda job=job: jobs.collect(ctx, job),
                        key=f"job {job['id']}", urgency=priority.proximity(d), kind="maintenance", cap=30,
                        detail=f"{job['kind']} at {tuple(job['pos'])}"))
            ready = [m for m in self.mem.machines(snap.dimension) if skills.pending_ready(m)]
            if ready:
                offer(C("collect machine", 4, 1200, lambda: skills.collect_machine(ctx, ready[0]), kind="maintenance"))

        # Goals.
        open_goals = [g for g in goals(snap, self.mem) if not g.done()]
        cost_model = LiveCost(snap, self.blacklist, self.mem)
        # With a pickaxe (tunnel down for the night) and iron armor, dark surface work is an acceptable risk.
        night_capable = self.has_pickaxe(snap) and wearing_at_least(snap.inv, "iron")
        plans = {}
        for g in open_goals:
            if not self.ready(g.name):
                filtered[g.name] = "cooling after a failure"
                continue
            try:
                plans[g.name] = self.plan_for(g, snap, cost_model)
            except Unplannable as e:
                filtered[g.name] = f"unplannable: {e}"
        unlocks = priority.unlock_values({n: (p[-1].token if p else None) for n, p in plans.items()}, plans)
        open_names = {g.name for g in open_goals}
        eligible = []
        for g in open_goals:
            if g.name not in plans:
                # No goal may vanish without a reason. "nether kit" was neither in the pool nor in `filtered` for a
                # whole 8-minute slice, so its absence could only be guessed at; a missing plan now says so itself.
                filtered.setdefault(g.name, "no plan built (and no reason recorded)")
                continue
            if g.superseded_by and g.superseded_by in plans:
                filtered.setdefault(g.name, f"superseded by {g.superseded_by}")
                continue
            plan = self.with_preparation(g, plans[g.name], snap, cost_model)

            def gate(s, plan=plan, g=g):
                """Why this STEP can't run now, or None. A gate rejects the step, never the whole goal: "nether kit"
                was dropped entire because its first runnable step was a hunt ("no pig seen yet"), so the 32 blocks
                of stone it also needed were never mined in an 8-minute slice."""
                if night and s.kind not in UNDERGROUND_KINDS and not night_capable:
                    return f"{s.kind} waits for day"
                if g.background and s.kind == "mine" and not lookahead.escape_ready(snap.inv):
                    return "no escape kit for digging"
                if not night and s.kind in ("gather", "hunt") and not snap.inv.count("bed") \
                        and not self.has_pickaxe(snap) and sum(x.est for x in plan) > snap.ticks_until_dusk + 3000:
                    return "surface trip would run into the night without a pickaxe to dig in"
                if s.kind == "hunt" and snap.dimension == "minecraft:overworld" and segment is not None:
                    # Speedrun route only: no searching for animals nobody has seen ("no sheep found" ×10). Memory
                    # only (scan_resources notes animals every 20 s) — an entity query here made every recorded round
                    # unreplayable and cost a request per goal per round.
                    kinds = s.detail.get("types", [])
                    if kinds and not any(self.mem.sightings(k, snap.dimension) for k in kinds):
                        return f"no {bare(kinds[0])} seen yet"
                return None

            # The first runnable step that also passes the gates; jumping past a *failed* step is still forbidden,
            # but a gated step must not hide the other work the same goal can do right now.
            runnables = [s for s in plan if runnable(s, snap.inv)]
            # Only steps that move the goal itself count. The look-ahead's extras (torches, a spare pickaxe) may run,
            # but they are not progress: a food goal whose hunt was gated kept crafting torches while the kit sat at
            # food 0/6 for a whole slice.
            prep = self.prep_tokens.get(g.name, ())
            own = [s for s in runnables if s.token not in prep]
            step, reason = None, None
            for cand_step in own:
                why = gate(cand_step)
                if why is None:
                    step, reason = cand_step, None
                    break
                if step is None:
                    step, reason = cand_step, why      # keep the first one's reason for the log
            if plan and not runnables:
                reason = "no runnable step"
            elif runnables and not own:
                step, reason = runnables[0], "only look-ahead preparation can run"
            if reason is None and g.feasible:
                reason = g.feasible()
            if reason is None and g.dimension == "minecraft:the_nether" and snap.dimension != g.dimension:
                kit = nether_kit_missing(snap.inv)
                if kit:
                    reason = "Nether kit not ready: " + ", ".join(kit)
            if reason is None and snap.dimension != nether.NETHER and g.dimension != nether.NETHER and any(
                    s.kind == "hunt" and set(s.detail.get("types", [])) & NETHER_MOBS for s in plan):
                # Dependency chain: eyes of ender need blaze powder, i.e. blazes — in the Nether, behind the portal.
                reason = "its plan needs Nether mobs (blaze rods): the blaze-rod goal comes first"
            run = self.goal_runner(ctx, g, plan, step, night)
            if reason is None and g.dimension != snap.dimension:
                # The work is in another dimension: going through the portal is this goal's next step.
                run = (lambda dim=g.dimension: nether.use_portal(ctx, dim))
                step = None
                plan = [] if not plan else plan
            if run is None:
                reason = reason or "nothing to do"
            if reason:
                filtered[g.name] = reason
                continue
            eligible.append((g, plan, step, run))
        for g, plan, step, run in eligible:
            stat = f"{step.kind}:{step.token}" if step else None
            changed = stat in self.fail_sig and self.fail_sig[stat] != self.coarse
            unlock = unlocks.get(g.name, 1.0) + 0.5 * sum(1 for u in g.unlocks if u in open_names)
            # No phase multiplier any more: value, unlock and cost carry progression. A ×0.25 phase factor kept the
            # portal (value 10) below cheap phase-0 crafts even after every phase-0 goal that mattered was done.
            cand = C(g.name, g.value * (priority.BACKGROUND if g.background else 1.0),
                     sum(s.est for s in plan) + 200, run, key=step_key(g, step) if step else g.name,
                     urgency=self.goal_urgency(g, snap), unlock=min(3.0, unlock),
                     success=priority.effective_success(self.mem.success_rate(stat), changed) if stat else 1.0,
                     detail=str(step) if step else "finish", reserve=bag.reserved_ids(plan, g.needs))
            # Everything it needs is in the bag and only crafting is left: no travel, so no reason to wait for a later
            # route segment (5 gold ingots carried, the helmet never made).
            cand.craft_only = bool(plan) and all(s.kind == "craft" for s in plan) and \
                g.dimension in (None, snap.dimension)
            offer(cand)

        # Claude's boost / background directives (override ones ran before the pool).
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

        # Reservations cover every open main goal that can plan, not only the one picked: a down-weighted wheat farm's
        # seeds were thrown away three times in a minute.
        bag.RESERVED = set().union(*(bag.reserved_ids(plan, g.needs) for g, plan, _, _ in eligible
                                     if not g.background)) if eligible else set()

        # Fallbacks: always-available low-value work, so the pool is rarely empty.
        held_back = []
        for fname, fn, allowed in self.fallbacks(ctx, snap, night):
            if allowed:
                base, cost = FALLBACK_BASE[fname]
                c = C(fname, base, cost, fn, kind="fallback", cap=20)
                offer(c)
                if staying and fname in WANDERING and fname in filtered:
                    held_back.append(c)
        if not pool and held_back:
            # "Stay while it retries" only makes sense when something else can run here. With the portal cooling,
            # food waiting for a seen pig and the bed for seen sheep, it left nothing and the agent idled; exploring
            # is how pigs, sheep and water get seen (recorded 07:32).
            for c in held_back:
                if priority.weight_for(c.name, weights)[1]:
                    # Ask the ban itself, not the filter reason: these were filtered by the "staying while it
                    # retries" branch, which runs before the ban branch, so a banned "light up" read as merely
                    # held back and was resurrected 170× in one 8-minute slice ("no torches to spare").
                    continue
                filtered.pop(c.name, None)
                pool.append(c)
        return pool, filtered

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

    def plan_for(self, g, snap, cost_model):
        """Plans are reused for PLAN_CACHE_S while the bag is unchanged: 27 goals × world queries every round was
        most of a round's HTTP traffic."""
        contents = tuple(sorted((s["id"], s.get("count", 1)) for s in snap.inv.slots))
        hit = self.plan_cache.get(g.name)
        if hit and hit[1] == contents and time.time() - hit[0] < PLAN_CACHE_S:
            return hit[2]
        plan = Planner.from_inventory(snap.inv, cost_model, self.mem.pending_outputs(snap.dimension)).plan(g.needs)
        self.plan_cache[g.name] = (time.time(), contents, plan)
        return plan

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

    def goal_urgency(self, g, snap):
        if g.name == "bed to carry":
            return priority.bed_urgency(self.mem.nights_missed)
        if g.name in ("food (≥8)", "stock food"):
            return priority.food_urgency(snap.get("food", 20), food_count(snap.inv))
        if "pickaxe" in g.name:
            return priority.pickaxe_urgency(self.pickaxe_left(snap))
        return 1.0

    def pickaxe_left(self, snap):
        """Remaining durability fraction of the best pickaxe (0 without one)."""
        best = 0.0
        for _, d, item in snap.inv.tools("pickaxe"):
            full = PICK_MAX.get(bare(item).split("_")[0], 250)
            best = max(best, d / full)
        return best

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
            log("   rank " + c.explain())
        if filtered:
            # Say how many were cut, or the line lies: "nether kit" sat at position 11 and looked like it had
            # vanished from the pool entirely, which sent a whole debugging session down the wrong path.
            # 40, not 10: a round filtered 43 goals and the ten shown never included the one being debugged.
            shown = list(filtered.items())[:40]
            more = len(filtered) - len(shown)
            log("   filtered: " + "; ".join(f"{n} ({r})" for n, r in shown)
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
            prepared = Planner.from_inventory(snap.inv, cost_model, self.mem.pending_outputs(snap.dimension)) \
                .plan(goal.needs + extra)
        except Unplannable:
            return plan
        if prepared and prepared[0].key() != plan[0].key():
            log(f"   look-ahead for {goal.name}: bring {lookahead.describe(extra)} first")
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
                    log(f"dusk in {dusk // 20}s, {site['name']} ~{eta // 20}s away → heading back")

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
                log(f"dusk in {dusk // 20}s and no shelter within {FAR_SITE_TICKS // 20}s → building a forward base here")
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

            log(f"night: going into {hut['name']}")
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
                log(f"night: exposed → tunnelling underground to {target}")
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
            log(f"night: sheltering by {spot[1]} at {spot[0]} (from {snap.feet})")
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

    def survival(self, snap, ctx):
        """Survival mode. Emergencies map to fixed rescues, most urgent first; each rescue is a contracted skill and
        retries within 10 s. Lava and burning are handled faster by the mod (LavaGuard)."""
        s = snap.state
        rescues = []
        if skills.head_buried(s):
            # Sand/gravel fell on us or we walked into a wall: suffocation outranks everything.
            rescues.append(("unbury", lambda: skills.unbury(ctx)))
        # Really under water (eyes in a water block) and running low — not just bobbing at the surface.
        if s["inWater"] and s["air"] < 150 and skills.head_underwater(s):
            rescues.append(("find air", lambda: skills.find_air(ctx)))
        threats = [e for e in entities(12) if is_threat(e)]
        if s["health"] <= 10 and any(e["distance"] <= 6 for e in threats):
            rescues.append(("flee", lambda: self.flee(ctx, snap, threats)))
        # Leaving the Nether outranks eating there: the offline scheduling check found "eat to heal" picked first at
        # 6 hp in the Nether (standing still in lava and ghast country); reflexes still eat on the way to the portal.
        retreat = must_retreat(snap)
        if retreat:
            rescues.append((f"retreat from the Nether ({retreat})",
                            lambda: nether.use_portal(ctx, "minecraft:overworld")))
        if s["health"] <= 6:
            if s["food"] < 20:
                rescues.append(("eat to heal", skills.eat))
            if any(is_threat(e) for e in entities(10)):
                rescues.append(("wall in to heal", lambda: skills.pod(ctx)))
        if not snap.night and skills.enclosed():
            # Walled in from last night: nothing else can run until we're out (hand-digging works without a pick).
            rescues.append(("dig out", lambda: skills.dig_out(ctx)))
        if s["food"] <= 6 and not food_count(snap.inv):
            rescues.append(("eat anything", lambda: skills.eat(raw_ok=True) or self.forage(ctx, snap)))
        if self.stuck_in_place(snap):
            rescues.append(("unstuck", lambda: self.unstuck(ctx, snap)))
        for name, fn in rescues:
            if self.ready(name):
                log(f"survival: {name}")
                api.INTERRUPT = None
                api.MODE = "survival"      # the perception thread doesn't interrupt the rescue itself
                try:
                    self.attempt(name, fn, cooldown=10)
                finally:
                    api.MODE = "normal"
                return True
        return False

    def forage(self, ctx, snap):
        from .knowledge import HUNT
        if snap.night and not self.sheltered(snap):
            raise NotAvailable("no foraging exposed at night")
        for token in ("minecraft:beef", "minecraft:porkchop", "minecraft:mutton", "minecraft:chicken"):
            if entities(48, HUNT[token]):
                return skills.hunt(ctx, token, 2, HUNT[token], snap.night)
        raise NotAvailable("no animals to forage")

    def flee(self, ctx, snap, threats):
        """Hurt and outmatched: run 16 blocks directly away from the hostiles (digging/bridging if needed)."""
        x, y, z = snap.feet
        cx = sum(e["x"] for e in threats) / len(threats)
        cz = sum(e["z"] for e in threats) / len(threats)
        dx, dz = x - cx, z - cz
        norm = math.hypot(dx, dz) or 1.0
        target = (round(x + 16 * dx / norm), y, round(z + 16 * dz / norm))
        if not nav.go_to(target, self.policy(snap, True), range_=4, attempts=1):
            raise NotAvailable("could not get away")

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
            ("light up", lambda: skills.light_area(ctx), overworld and not skills.enclosed()),
            ("strip mine", lambda: skills.strip_mine_step(ctx),
             overworld and self.has_pickaxe(snap) and lookahead.escape_ready(snap.inv)),
            ("explore", lambda: self.explore(ctx), overworld and surface_day),
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
        except (McError, skills.ToolMissing) as e:
            self.failed(name, e)
            if "/" in name:
                # The step itself failed, whichever goal ran it: iron pickaxe, torches, water bucket, bucket and
                # shield took turns mining the same unreachable vein (recorded 06:39). Share the cooldown by step.
                self.failed("step:" + name.split("/", 1)[1], e)
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
