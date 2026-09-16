"""One priority pool for everything that isn't life-saving. Reflexes, survival and night safety stay a fixed order;
goals, maintenance, Claude's boost/background directives and fallbacks are all Candidates scored by one formula:

    score = base * urgency(state) * unlock * weight * success / max(cost, COST_FLOOR)

  base     static value (goal value; background stock × BACKGROUND; later phases halve)
  urgency  a curve over state: full bag, missed nights, a worn pickaxe, hunger — how maintenance enters the pool
  unlock   how many other open goals this one feeds (from the plans): the iron pickaxe rises by itself
  weight   Claude's hot-reloaded adjustment (priorities.json): multiply / ban / pin, always with a TTL, clamped
  success  measured step success rate, floored at MIN_SUCCESS and reset to the prior once the state changed —
           failing must not mean "never chosen again, so never redeemed"

Switching needs the challenger to beat the current task by `margin`, which decays from 1.5 to 1.0 as the current
task goes without progress. Pure (time and file paths passed in): offline-testable."""
import json
import os
import time
from . import paths

FILE = paths.data("priorities.json", env="MC_PRIORITIES")
CLAMP = (0.05, 20.0)
MIN_SUCCESS = 0.3
COST_FLOOR = 600          # ticks: every decision carries ~30 s of overhead (walking, menus); crafts aren't free
BACKGROUND = 0.25
MARGIN, MARGIN_DECAY_S = 1.5, 60


class Candidate:
    def __init__(self, name, base, cost, run, key=None, urgency=1.0, unlock=1.0, weight=1.0, success=1.0,
                 kind="goal", cap=None, detail="", reserve=None):
        self.name, self.base, self.cost, self.run = name, base, cost, run
        self.reserve = reserve or set()   # item ids this candidate's plan consumes (bag.RESERVED once committed)
        self.key = key or name          # retry-policy key (a goal's current step)
        self.urgency, self.unlock, self.weight, self.success = urgency, unlock, weight, success
        self.kind, self.cap, self.detail = kind, cap, detail

    @property
    def score(self):
        return self.base * self.urgency * self.unlock * self.weight * self.success / max(self.cost, COST_FLOOR)

    def explain(self):
        return (f"{self.name}: base {self.base:g} × urg {self.urgency:g} × unlock {self.unlock:g} × w {self.weight:g}"
                f" × ok {self.success:.2f} / cost {max(self.cost, COST_FLOOR)} = {self.score:.5f}")


# -- urgency curves
def bag_urgency(used_slots):
    """0 below 28 slots (not a candidate); then a gentle, capped curve — 28 → 0.5, 32 → 1.5, 35 → 3, 36 → 4.
    Starting early lets the bag be sorted in open tunnels before it is full; the cap keeps it from turning back
    into a hard priority (×20 once made tidy 200× any goal)."""
    if used_slots < 28:
        return 0.0
    return min(4.0, 0.5 + 0.25 * (used_slots - 28) + (0.5 if used_slots >= 35 else 0.0) + (1.0 if used_slots >= 36 else 0.0))


def bed_urgency(nights_missed):
    return 1.0 + nights_missed


def pickaxe_urgency(left_fraction):
    """The best pickaxe's remaining durability fraction (0 when none)."""
    return 10.0 if left_fraction < 0.1 else 1.0


def food_urgency(food_level, food_items):
    if food_items >= 8:
        return 1.0
    return 4.0 if food_level <= 10 or food_items <= 2 else 2.0


def proximity(distance):
    """Errands on the way: a job or resource within 16 blocks is worth twice as much (merged into the route), within
    32 half again; farther ones are separate trips."""
    if distance <= 16:
        return 2.0
    return 1.5 if distance <= 32 else 1.0


# -- unlock value from the plans
def unlock_values(products, plans, per=0.5, cap=3.0):
    """products: {goal: token its plan finally makes}; plans: {goal: [steps]}. A goal whose product appears in other
    goals' plans unlocks them: 1 + per × count, capped."""
    out = {}
    for g, token in products.items():
        n = sum(1 for h, plan in plans.items() if h != g and token and any(s.token == token for s in plan))
        out[g] = min(cap, 1.0 + per * n)
    return out


# -- success with a floor and a reset on state change
def effective_success(rate, state_changed):
    return 1.0 if state_changed else max(MIN_SUCCESS, rate)


# -- Claude's weights (priorities.json), hot-reloaded every round
def load(path=None, now=None):
    """{target: {"x", "ban", "pin", "why"}} for unexpired entries. An entry expires at `until`, or `ttl` seconds
    after `set` (or after the file's modification time)."""
    path = path or FILE
    now = now or time.time()
    try:
        with open(path) as f:
            data = json.load(f)
        mtime = os.path.getmtime(path)
    except (OSError, ValueError):
        return {}
    out = {}
    for w in data.get("weights", []):
        until = w.get("until") or (w.get("set", mtime) + w.get("ttl", 0))
        if until > now and w.get("target"):
            out[w["target"]] = w
    return out


def weight_for(name, weights):
    """(multiplier, banned). Pins take the ceiling; multipliers are clamped so one tweak can't freeze the system."""
    w = weights.get(name)
    if not w:
        return 1.0, False
    if w.get("ban"):
        return 0.0, True
    if w.get("pin"):
        return CLAMP[1], False
    return min(CLAMP[1], max(CLAMP[0], float(w.get("x", 1.0)))), False


def add_weight(target, path=None, now=None, **fields):
    """Write/replace one weight entry (mc.py prio). ttl is required: adjustments must expire."""
    path = path or FILE
    now = now or time.time()
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    items = [w for w in data.get("weights", []) if w.get("target") != target and
             (w.get("until") or 0) > now]
    ttl = fields.pop("ttl")
    items.append({"target": target, "until": now + ttl, "set": now, **fields})
    data["weights"] = items
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    return items[-1]


# -- named weight profiles (mc.py prio profile NAME)
PROFILES = {
    # Speedrun: the dragon route only. Side projects are banned; the chain portal → blaze rods / pearls → eyes →
    # stronghold → End → dragon is boosted. Survival, safety and maintenance still run (they aren't goals).
    "speedrun": {
        "ban": ["wheat farm", "breed animals", "enchanting table", "enchant the pickaxe", "stock logs",
                "stock building blocks", "stock coal", "stock planks", "stock torches", "spare pickaxe",
                "carried door", "carried chest", "ladders (≥4)", "auto smelter", "repair worn pickaxe",
                "fire resistance potions", "stone axe", "iron sword", "stock food",
                # The bow is NOT optional any more: the dragon heals 2 hp/s off any crystal within 32 blocks, perched
                # included, and 2 of the 10 crystals sit in iron cages. Without arrows the only way to those is
                # towering up next to an exploding crystal — the most dangerous thing this agent can do.
                # No storage treks or cache chests in a speedrun: a full bag is tidied (junk thrown), never stored.
                "deposit",
                # Picked 17× in a 20-min slice without helping the route: no torches, no carried stations.
                "torches (≥8)", "carried furnace", "carried chest",
                # Placing torches doesn't advance a speedrun: with nothing else runnable, exploring finds what's missing
                # (the idle fix picked "light up" over "explore").
                "light up",
                # No sleeping in a 30-min run; dragon beds come from bartered string or seen sheep (beds for the dragon).
                "bed to carry", "bed from string"],
        "boost": {"gold helmet for piglins": 6, "nether fortress": 6, "blaze rods (7)": 8, "piglin barter": 6,
                  "ender pearls (12)": 6, "eyes of ender (12)": 8, "stronghold located": 8, "portal room found": 9,
                  "activate end portal": 10, "enter the End": 10, "defeat the ender dragon": 12,
                  "beds for the dragon": 8, "bow": 6, "arrows (32)": 6,
                  # Ready-made iron/food/obsidian in ruined portals, shipwrecks and village chests beats mining.
                  "loot nearby chests": 3,
                  "food (≥8)": 3},   # never boost a banned target: the later boost replaced the ban (bow ran anyway)
    },
}


def apply_profile(name, path=None, now=None, ttl=6 * 3600):
    """Write a profile's bans and boosts (each expiring after `ttl`), replacing earlier entries for those targets and
    lifting bans the profile boosts. Returns the number of entries written."""
    prof = PROFILES[name]
    n = 0
    for target in prof.get("ban", []):
        add_weight(target, path=path, now=now, ttl=ttl, ban=True, why=f"profile {name}")
        n += 1
    for target, x in prof.get("boost", {}).items():
        add_weight(target, path=path, now=now, ttl=ttl, x=x, why=f"profile {name}")
        n += 1
    return n


# -- choosing with persistence
def choose(pool, committed, stalled_seconds):
    """Best candidate, except the committed one stays while its score × margin still beats the best. The margin
    decays from MARGIN to 1.0 as the committed task goes without progress."""
    if not pool:
        return None
    ranked = sorted(pool, key=lambda c: c.score, reverse=True)
    if committed:
        margin = 1.0 + (MARGIN - 1.0) * max(0.0, 1.0 - stalled_seconds / MARGIN_DECAY_S)
        current = next((c for c in ranked if c.name == committed), None)
        if current is not None and current.score * margin >= ranked[0].score:
            return current
    return ranked[0]
