"""What the world looks like right now: player snapshot, inventory, block regions, searches."""
import math
import time

from . import api
from .data import (DAY_END, GROUPS, HAZARD, NIGHT_END, PASSABLE, PASSABLE_SUFFIX, PLAYER_MADE_SUFFIX,
                   TIER_OF_MATERIAL, UNBREAKABLE, bare, mid)

# The round's route answers from the game (`nav.route_s` writes them, `nav.forget_routes` clears): {key: (found,
# seconds)}. Kept at the bottom, beside the other world reads, so the cost model prices a route without importing
# movement.
ROUTES = {}

NEIGHBOURS6 = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]


def add(p, d):
    return p[0] + d[0], p[1] + d[1], p[2] + d[2]


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

    def tools(self, kind):
        """[(tier, durability_left, id)] best first."""
        out = []
        for s in self.slots:
            material, _, k = bare(s["id"]).rpartition("_")
            if k == kind and material in TIER_OF_MATERIAL:
                out.append((TIER_OF_MATERIAL[material], s.get("maxDamage", 0) - s.get("damage", 0), s["id"]))
        return sorted(out, reverse=True)

    def worn(self, slot):
        return self.equipment.get(slot, {}).get("id", "minecraft:air")

    def free_slots(self):
        """Main-inventory slots still empty: what "room to carry more" means everywhere."""
        return max(0, 36 - self.used_slots())

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

    @classmethod
    def from_readings(cls, state, inventory):
        """A snapshot of recorded readings (/state dict, /inventory dict or Inventory): no world read."""
        snap = cls.__new__(cls)
        snap.state = dict(state)
        snap.inv = inventory if isinstance(inventory, Inventory) else Inventory(inventory)
        return snap

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


SIGHT_TTL_S = 3.0          # a round's look at "how far is the nearest of each": kept while the feet stay put
_SIGHT = {"key": None, "t": 0.0, "near": {}}
_PER_BLOCK = []            # [bool] once known: the running jar answers /find?perBlock (≥ 0.1.55)


def _per_block_ok():
    if not _PER_BLOCK:
        import re
        try:
            v = tuple(int(x) for x in re.findall(r"\d+", str(api.status().get("version", "0")))[:3])
        except api.McError:
            return False
        _PER_BLOCK.append(v >= (0, 1, 55))
    return _PER_BLOCK[0]


def nearest(kinds, feet, dimension, radius=48, union=()):
    """Blocks to the nearest of `kinds` in sight, or None — for estimates (the cost model), which never search the
    world themselves. On a jar with /find?perBlock: ONE scan of the whole `union` per round (kept while the feet
    stay put and for SIGHT_TTL_S), every later kind answered from it — fifteen scans a cold round were 1.5 s of a
    1.95 s decide. On an older jar: one scan of `kinds`, kept the same way."""
    names = [bare(k) for k in kinds]
    key = (tuple(feet), dimension)
    fresh = _SIGHT["key"] == key and time.time() - _SIGHT["t"] < SIGHT_TTL_S
    if not fresh:
        _SIGHT.update(key=key, t=time.time(), near={})
    near = _SIGHT["near"]
    missing = [n for n in names if n not in near]
    if missing:
        ask = sorted({bare(u) for u in union} | set(missing)) if _per_block_ok() else missing
        ids = ",".join(mid(b) for b in ask)
        path = (f"/find?blocks={ids}&radius=48&limit={len(ask) * 2 + 8}&perBlock=1" if _per_block_ok()
                else f"/find?blocks={ids}&radius=48&limit=20")
        try:
            hits = api.get(path)["blocks"]
        except api.McError:
            hits = []
        for b in ask:
            near.setdefault(b, None)
        for h in hits:
            b = bare(h["block"])
            if near.get(b) is None or h["distance"] < near[b]:
                near[b] = h["distance"]
    got = [near[n] for n in names if near.get(n) is not None and near[n] <= radius]
    return min(got) if got else None


def find(blocks, radius=32, limit=50, exposed=False):
    """What `/find` sees. Seeing through blocks is allowed: sealed blocks are found unless `exposed` is asked for,
    and then the flag is sent only as true (a jar that reads the key's presence took `exposed=false` as strict)."""
    ids = ",".join(mid(b) for b in blocks)
    return api.get(f"/find?blocks={ids}&radius={radius}&limit={limit}" + ("&exposed=true" if exposed else ""))["blocks"]


def entities(radius=16, types=None):
    out = api.get(f"/entities?radius={radius}")["entities"]
    return [e for e in out if types is None or e["type"] in types]


def dark_spots(radius=4, max_light=7, limit=30):
    return api.get(f"/dark?radius={radius}&maxLight={max_light}&limit={limit}")["spots"]


def container():
    return api.get("/container")


REGION_MAX = 32 * 32 * 32       # the most cells one /blocks answers (the jar's MAX_REGION_VOLUME)


def slabs(lo, hi, most=REGION_MAX):
    """Pure: the box lo..hi cut along x into boxes of at most `most` cells, in order — one read per slab. A box
    past the jar's limit was refused ("region too large") however much the caller needed it (reach_land: 49 wide)."""
    lo, hi = [min(a, b) for a, b in zip(lo, hi)], [max(a, b) for a, b in zip(lo, hi)]
    face = (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
    if face > most:
        raise ValueError(f"a y-z face of {face} cells is past the {most}-cell read")
    width = max(1, most // face)
    return [((x, lo[1], lo[2]), (min(hi[0], x + width - 1), hi[1], hi[2])) for x in range(lo[0], hi[0] + 1, width)]


class Region:
    """Blocks in a box (from /blocks, in slabs the jar accepts); unknown cells outside the box are not inside."""

    def __init__(self, lo, hi, props=False):
        self.lo, self.hi = tuple(lo), tuple(hi)
        self.blocks, self.props = {}, {}
        for a, b in slabs(lo, hi):
            query = f"/blocks?from={a[0]},{a[1]},{a[2]}&to={b[0]},{b[1]},{b[2]}" + ("&props=1" if props else "")
            data = api.get(query)
            pal = [bare(p) for p in data["palette"]]
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


def job_ready(job, tick=None, now=None):
    """Pure given `tick`/`now`: is a background job done? By the game's clock when both the job and the reading
    have one — the furnace cooks in ticks, so a lagging server or a sprinted clock (the iron bench's /tick sprint)
    moves it; the wall clock (`ready_at`) only as a fallback (a jar without gameTime)."""
    if job.get("ready_tick") is not None and tick is not None:
        return tick >= job["ready_tick"]
    return job["ready_at"] <= (time.time() if now is None else now)


def feet():
    """The block the feet are in, (x, y, z): one /state read. The one home of this reading (nav reads it here, below
    the skills; skillcore passes it on to them)."""
    s = api.get("/state")
    return s["blockX"], s["blockY"], s["blockZ"]


def away_from(here, point, blocks):
    """Pure: the spot `blocks` from `here` straight away from `point`, on the horizontal (y kept; unrounded). On the
    point itself, nowhere to go: `here`."""
    dx, dz = here[0] - point[0], here[2] - point[2]
    n = math.hypot(dx, dz) or 1.0
    return here[0] + dx / n * blocks, here[1], here[2] + dz / n * blocks


def cells_with(region, name, key, value, want=True):
    """Pure: the `name` blocks of `region` whose block state `key` reads `value` (`want=False`: reads anything
    else). [] from a region read without block states."""
    prop = getattr(region, "prop", None)
    return [p for p, n in region.blocks.items() if n == name and prop and (str(prop(p, key)) == value) == want]


def ripe_cells(region):
    """Pure: wheat blocks at full growth (age 7)."""
    return cells_with(region, "wheat", "age", "7")


def ripe_near(feet, radius=32):
    """Ripe wheat cells within `radius` of `feet` (one /find, then the block states read): a plot already grown is
    harvested before a new one is sown (cost.ripe)."""
    hits = find(["wheat"], radius=radius, limit=64) or []
    if not hits or feet is None:
        return []
    cells = [(h["x"], h["y"], h["z"]) for h in hits]
    lo = tuple(min(c[i] for c in cells) for i in range(3))
    hi = tuple(max(c[i] for c in cells) for i in range(3))
    return ripe_cells(Region(lo, hi, props=True))
