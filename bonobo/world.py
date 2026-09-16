"""What the world looks like right now: player snapshot, inventory, block regions, searches."""
import math

from . import api
from .data import (DAY_END, FALLING, GROUPS, HAZARD, NIGHT_END, PASSABLE, PASSABLE_SUFFIX, PLAYER_MADE_SUFFIX,
                   ROUTE_FACTOR, TIER_OF_MATERIAL, UNBREAKABLE, WALK_BLOCKS_PER_TICK, bare, mid)

NEIGHBOURS6 = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]


def add(p, d):
    return p[0] + d[0], p[1] + d[1], p[2] + d[2]


def horizontal_dist(a, b):
    return math.dist((a[0], a[2]), (b[0], b[2]))


def travel_ticks(a, b):
    return int(math.dist(a, b) * ROUTE_FACTOR / WALK_BLOCKS_PER_TICK)


class Inventory:
    def __init__(self, data=None):
        data = data or api.get("/inventory")
        self.slots = data["slots"]
        self.equipment = data["equipment"]

    def _stacks(self, include_worn):
        yield from self.slots
        offhand = self.equipment.get("offhand")
        if offhand and offhand.get("count"):
            yield offhand
        if include_worn:
            for slot in ("head", "chest", "legs", "feet"):
                s = self.equipment.get(slot)
                if s and s.get("count"):
                    yield s

    def count(self, item_or_group, include_worn=False):
        ids = GROUPS.get(item_or_group, [mid(item_or_group)])
        return sum(s["count"] for s in self._stacks(include_worn) if s["id"] in ids)

    def usable(self, item_or_group):
        """Count in the 36 main slots only: what tasks can put in the hand (place, craft). The offhand isn't."""
        ids = GROUPS.get(item_or_group, [mid(item_or_group)])
        return sum(s["count"] for s in self.slots if s["id"] in ids)

    def offhand(self):
        return self.equipment.get("offhand", {}).get("id", "minecraft:air")

    def richest(self, group):
        totals = {i: self.count(i) for i in GROUPS[group]}
        best = max(totals, key=totals.get)
        return best, totals[best]

    def tools(self, kind):
        """[(tier, durability_left, id)] best first."""
        out = []
        for s in self.slots:
            material, _, k = bare(s["id"]).rpartition("_")
            if k == kind and material in TIER_OF_MATERIAL:
                out.append((TIER_OF_MATERIAL[material], s.get("maxDamage", 0) - s.get("damage", 0), s["id"]))
        return sorted(out, reverse=True)

    def best_tool(self, kind, min_left=1):
        return next((t for t in self.tools(kind) if t[1] >= min_left), None)

    def worn(self, slot):
        return self.equipment.get(slot, {}).get("id", "minecraft:air")

    def used_slots(self):
        return len(self.slots)


def ticks_until_dusk(time_of_day):
    """Ticks until the next dusk. 0 during the night; at dawn (after NIGHT_END) a whole day lies ahead."""
    t = time_of_day % 24000
    if t < DAY_END:
        return DAY_END - t
    if t > NIGHT_END:
        return 24000 - t + DAY_END
    return 0


class Snapshot:
    """One consistent read of the player: state + inventory."""

    def __init__(self):
        self.state = api.get("/state")
        self.inv = Inventory()

    @property
    def feet(self):
        s = self.state
        return s["blockX"], s["blockY"], s["blockZ"]

    @property
    def pos(self):
        s = self.state
        return s["x"], s["y"], s["z"]

    @property
    def dimension(self):
        return self.state["dimension"]

    @property
    def time(self):
        return self.state["timeOfDay"]

    @property
    def night(self):
        return DAY_END <= self.time <= NIGHT_END

    @property
    def ticks_until_dusk(self):
        return ticks_until_dusk(self.time)

    @property
    def dark_here(self):
        s = self.state
        if "blockLight" not in s:
            return False
        underground_or_night = s["skyLight"] <= 7 or self.night
        return underground_or_night and s["blockLight"] < 8

    def get(self, key, default=None):
        return self.state.get(key, default)


def find(blocks, radius=32, limit=50, exposed=False):
    ids = ",".join(mid(b) for b in blocks)
    return api.get(f"/find?blocks={ids}&radius={radius}&limit={limit}&exposed={str(exposed).lower()}")["blocks"]


def entities(radius=16, types=None):
    out = api.get(f"/entities?radius={radius}")["entities"]
    return [e for e in out if types is None or e["type"] in types]


def dark_spots(radius=4, max_light=7, limit=30):
    return api.get(f"/dark?radius={radius}&maxLight={max_light}&limit={limit}")["spots"]


def container():
    return api.get("/container")


class Region:
    """Blocks in a box (from /blocks); unknown cells outside the box are treated as not inside."""

    def __init__(self, lo, hi, props=False):
        self.lo, self.hi = tuple(lo), tuple(hi)
        query = f"/blocks?from={lo[0]},{lo[1]},{lo[2]}&to={hi[0]},{hi[1]},{hi[2]}" + ("&props=1" if props else "")
        data = api.get(query)
        pal = [bare(p) for p in data["palette"]]
        self.blocks, self.props = {}, {}
        for entry in data["blocks"]:
            x, y, z, i = entry[:4]
            self.blocks[(x, y, z)] = pal[i]
            if len(entry) > 4:
                self.props[(x, y, z)] = entry[4]   # block state properties (mod >= 0.1.14 with props=1)

    def prop(self, p, key):
        return self.props.get(p, {}).get(key)

    def inside(self, p):
        return all(self.lo[i] <= p[i] <= self.hi[i] for i in range(3))

    def name(self, p):
        return self.blocks.get(p, "air")

    def solid(self, p):
        n = self.name(p)
        return n != "air" and n not in PASSABLE and not n.endswith(PASSABLE_SUFFIX) and n not in HAZARD

    def hazard(self, p):
        return self.name(p) in HAZARD

    def falling(self, p):
        return self.name(p) in FALLING

    def unbreakable(self, p):
        return self.name(p) in UNBREAKABLE

    def player_made(self, p):
        return self.name(p).endswith(PLAYER_MADE_SUFFIX)


def region_around(points, pad=3, max_volume=32768):
    lo = [min(p[i] for p in points) - pad for i in range(3)]
    hi = [max(p[i] for p in points) + pad for i in range(3)]
    if (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1) > max_volume:
        return None
    return Region(lo, hi)


def connected(region, seed, ids):
    """Flood-fill a vein of the given block ids from a seed position."""
    names = {bare(i) for i in ids}
    out, todo = set(), [seed]
    while todo:
        p = todo.pop()
        if p in out or region.name(p) not in names:
            continue
        out.add(p)
        todo.extend(add(p, d) for d in NEIGHBOURS6)
    return out
