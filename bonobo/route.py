"""The speedrun route as data (Claude writes it, the cerebellum follows it): ordered segments, each with a done test,
the goals allowed while it's active, and a time budget. The priority pool keeps scoring as usual but only offers the
active segment's goals (plus always-allowed essentials). A segment over budget escalates to Claude, who changes the
method, the budget or the route. Pure: offline-testable with plain inventories and memories."""
import json
import os
from . import paths

from .data import bare
from .knowledge import ALL_FOOD

# The chosen route lives in its own file, read every round: stored in world-notes it was overwritten by the running
# agent's own memory saves minutes after `mc.py route speedrun`.
FILE = paths.data("route.json", env="MC_ROUTE")


def current(path=None):
    try:
        with open(path or FILE) as f:
            return json.load(f).get("route")
    except (OSError, ValueError):
        return None


def choose(name, path=None):
    path = path or FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"route": name}, f)

NETHER = "minecraft:the_nether"
OVERWORLD = "minecraft:overworld"


# One definition of "enough food for the Nether trip". Six, not twelve: a speedrun crosses on a handful of steaks,
# while twelve cooked items means a dozen kills plus smelting — in a landing spot with no animals within 48 blocks
# that stalled the whole route (a slice sat at food 0/12 for 8 minutes with everything else in the kit ready).
KIT_FOOD = 6
# Beds carried into the End. Human runners take 8–10 and call five the bare minimum: each perch window is worth one
# or two blasts, and a wasted bed (bad angle, destroyed by its neighbour's blast) must not end the fight.
DRAGON_BEDS = 8


def food_count(inv):
    """The one definition of 'food carried' for the route: cooked/ready food only (raw meat must be cooked first)."""
    return sum(inv.count(f) for f in ALL_FOOD)


def nether_kit_missing(inv):
    """Pure: what a Nether trip still lacks (empty = ready): cooked food, building blocks for bridges/shelter, a gold
    helmet for piglins, bag room for the loot. No bow required (first trip: shield + melee)."""
    missing = []
    if food_count(inv) < KIT_FOOD:
        missing.append(f"food {food_count(inv)}/{KIT_FOOD}")
    if inv.count("building") < 32:
        missing.append(f"blocks {inv.count('building')}/32")
    if not (inv.count("minecraft:golden_helmet") or bare(inv.worn("head") or "") == "golden_helmet"):
        missing.append("gold helmet")
    # Two free slots for the first loot. A stricter target (8, then 5) flickered with every pickup and held the trip
    # back while tidy and the kit check disagreed; the bag keeps its own target (FREE_SLOTS_TARGET) anyway.
    if 36 - inv.used_slots() < 2:
        missing.append(f"bag room {36 - inv.used_slots()}/2 free")
    return missing


def kit_needs(inv):
    """Planner needs that close the kit's gaps (not subject to stock-goal bans)."""
    needs = []
    if food_count(inv) < KIT_FOOD:
        needs.append(("food", KIT_FOOD))     # the same constant the readiness check uses
    if inv.count("building") < 32:
        needs.append(("stone", 32))
    if not (inv.count("minecraft:golden_helmet") or bare(inv.worn("head") or "") == "golden_helmet"):
        needs.append(("minecraft:golden_helmet", 1))
    return needs


# Goals any segment may run: losing a tool, a death, a missing station must never be blocked by the route.
ALWAYS = {"stone pickaxe", "iron pickaxe", "recover items after death", "carried crafting table", "carried furnace",
          "torches (≥8)", "bucket", "water bucket", "flint and steel", "bed to carry"}

# Budgets at a 30-minute pace (sum 35 min incl. margin): over budget escalates to Claude within minutes.
SPEEDRUN = [
    {"name": "portal", "budget": 6 * 60,
     "done": lambda inv, mem, dim: bool(mem.machines(OVERWORLD, "portal")),
     "goals": {"nether portal"}},
    {"name": "nether kit", "budget": 3 * 60,
     "done": lambda inv, mem, dim: dim == NETHER or not nether_kit_missing(inv),
     "goals": {"nether kit", "gold helmet for piglins", "food (≥8)"}},
    {"name": "fortress", "budget": 5 * 60,
     "done": lambda inv, mem, dim: bool(mem.sites(NETHER, kinds=["fortress"])),
     "goals": {"nether fortress", "piglin barter"}},
    {"name": "blaze rods", "budget": 5 * 60,
     "done": lambda inv, mem, dim: inv.count("minecraft:blaze_rod") + inv.count("minecraft:blaze_powder") // 2 >= 7,
     "goals": {"blaze rods (7)", "piglin barter"}},
    {"name": "pearls", "budget": 5 * 60,
     "done": lambda inv, mem, dim: inv.count("minecraft:ender_pearl") + inv.count("minecraft:ender_eye") >= 12,
     "goals": {"piglin barter", "ender pearls (12)", "villager pearls"}},
    {"name": "eyes", "budget": 1 * 60,
     "done": lambda inv, mem, dim: inv.count("minecraft:ender_eye") >= 12,
     "goals": {"eyes of ender (12)"}},
    # Beds are the kit; a bow is opportunistic. The dragon only heals while a crystal is within 32 blocks of it, and
    # the pillars stand 40+ blocks out — perched at the fountain it is connected to nothing. So bombs land clean and
    # the crystals only matter for its flying phase: shoot the open ones if we happen to carry arrows, never tower.
    {"name": "end kit", "budget": 3 * 60,
     "done": lambda inv, mem, dim: inv.count("bed") >= 6 or dim == "minecraft:the_end"
     or bool(mem.data.get("dragon_defeated")),
     "goals": {"beds for the dragon", "bow", "arrows (32)"}},
    {"name": "stronghold", "budget": 3 * 60,
     "done": lambda inv, mem, dim: bool(mem.sites(OVERWORLD, kinds=["stronghold"])),
     "goals": {"stronghold located"}},
    {"name": "portal room", "budget": 2 * 60,
     "done": lambda inv, mem, dim: bool(mem.sites(OVERWORLD, kinds=["portal_room"])) or dim == "minecraft:the_end",
     "goals": {"portal room found"}},
    {"name": "end", "budget": 6 * 60,
     "done": lambda inv, mem, dim: bool(mem.data.get("dragon_defeated")),
     "goals": {"activate end portal", "enter the End", "defeat the ender dragon"}},
]
ROUTES = {"speedrun": SPEEDRUN}


def active_segment(route, inv, mem, dim):
    """Pure: the first segment not done yet (None when the route is finished)."""
    for seg in route:
        if not seg["done"](inv, mem, dim):
            return seg
    return None


def kit_badly_missing(inv):
    """Pure: the kit has fallen far enough behind to go back and fix it (not a slot or two of noise)."""
    return food_count(inv) < 8 or inv.count("building") < 16 or not (
        inv.count("minecraft:golden_helmet") or bare(inv.worn("head") or "") == "golden_helmet")


def with_hysteresis(route, seg, state, inv):
    """Pure-ish: don't fall back to the kit segment for small wobbles once it was passed. The bag hovering at 4/5 free
    slots flipped the route between 'nether kit' and 'fortress' every round (and triggered tidy each time)."""
    names = [s["name"] for s in route]
    reached = state.get("reached", 0)
    idx = names.index(seg["name"]) if seg else len(names)
    if idx > reached:
        state["reached"] = idx
        return seg
    if seg and seg["name"] == "nether kit" and reached > idx and not kit_badly_missing(inv):
        return route[reached] if reached < len(route) else None
    return seg


def allowed(seg, goal_name):
    return seg is None or goal_name in seg["goals"] or goal_name in ALWAYS
