"""What the world looks like right now: player snapshot, inventory, block regions, searches."""
from __future__ import annotations

import functools
import math

import time
from typing import TYPE_CHECKING, Any, Mapping, cast

from . import api, lifecycle
from .data import (DAY_END, DAY_TICKS, DOOR_SUFFIX, GROUPS, HAZARD, NIGHT_END, OPEN_PROP, PARTIAL_SUFFIX, PASSABLE,
                   PASSABLE_SUFFIX, is_door,
                   PLAYER_MADE_SUFFIX, TIER_OF_MATERIAL, UNBREAKABLE, bare, living, mid)

if TYPE_CHECKING:
    from .shapes import Cell, EntityReading, Equipment, InventoryReading, Slot, StateReading

# the round's route answers ({key: (found, seconds)}), kept here so the cost model prices a route without importing movement
ROUTES = {}


def route_key(cell, range_, nodes):
    """Pure: the one key of a route price — the walk's /plan, nothing dug or built (E3, D6) — for nav and the cost."""
    return (tuple(int(v) for v in cell), float(range_), int(nodes))

NEIGHBOURS6 = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]

def to_segment(p, a, b):
    """Pure: the distance from point `p` to the segment a → b."""
    ab = [b[i] - a[i] for i in range(3)]
    n = sum(v * v for v in ab)
    t = 0.0 if n == 0 else max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / n))
    return math.dist(p, [a[i] + t * ab[i] for i in range(3)])

def line_clear(solid, a, b, step=0.25):
    """Pure: nothing solid on the straight line a → b (points; the cells at both ends not asked) — a shot's line."""
    n = max(1, int(math.dist(a, b) / step))
    ends = {tuple(int(math.floor(c)) for c in a), tuple(int(math.floor(c)) for c in b)}
    for i in range(1, n):
        cell = tuple(int(math.floor(a[k] + (b[k] - a[k]) * i / n)) for k in range(3))
        if cell not in ends and solid(cell):
            return False
    return True

def line_of_fire(a, b):
    """Is there an open line from `a` to `b` (both eye points): one read of the box they span."""
    lo = tuple(int(math.floor(min(a[i], b[i]))) for i in range(3))
    hi = tuple(int(math.floor(max(a[i], b[i]))) for i in range(3))
    try:
        return line_clear(Region(lo, hi).solid, a, b)
    except api.McError as e:
        return api.swallowed("world.line_of_fire", e)      # unread: unknown (None), the mob still counts

def add(p, d) -> Cell:
    return p[0] + d[0], p[1] + d[1], p[2] + d[2]


def is_enclosed(region, inside):
    """Pure: no 2-high opening on any side and a solid roof."""

    return not openings(region, inside)

def openings(region, inside: Cell):
    """Pure: what an enclosure around `inside` lacks — {cell: "wall"} per 2-high gap and the roof; {} when enclosed."""

    x, y, z = inside
    out = {}
    for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        if not region.solid((x + dx, y, z + dz)) and not region.solid((x + dx, y + 1, z + dz)):
            out[(x + dx, y, z + dz)] = "wall"
    if not region.solid((x, y + 2, z)):
        out[(x, y + 2, z)] = "roof"
    return out

BAG_SLOTS = 36          # the main bag: hotbar 0-8 and the 27 above it (offhand and armour are not bag)


def screen_slot(slot):
    """Pure: a bag slot's id in the inventory screen a /click names — the hotbar (0-8) sits at 36-44 there, the
    rest keep their number."""
    return 36 + slot if slot < 9 else slot

class Inventory:
    slots: list[Slot]
    equipment: Equipment

    def __init__(self, data: InventoryReading | Mapping[str, Any] | None = None):
        got: Mapping[str, Any] = data or api.get("/inventory")
        self.slots = got["slots"]
        self.equipment = got["equipment"]
        self.selected = int(got.get("selectedSlot", 0))      # the hotbar slot in the main hand

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
        return max(0, BAG_SLOTS - self.used_slots())

    def used_slots(self):
        return len(self.slots)

def ticks_until_dusk(time_of_day):
    """Ticks until the next dusk. 0 during the night; at dawn (after NIGHT_END) a whole day lies ahead."""
    t = time_of_day % DAY_TICKS
    if t < DAY_END:
        return DAY_END - t
    if t > NIGHT_END:
        return DAY_TICKS - t + DAY_END
    return 0

class Snapshot:
    """One consistent read of the player: state + inventory, built from readings the caller took."""
    state: StateReading
    inv: Inventory

    @classmethod
    def from_readings(cls, state: Mapping[str, Any], inventory: "Mapping[str, Any] | Inventory") -> "Snapshot":
        """A snapshot of recorded readings (/state dict, /inventory dict or Inventory): no world read."""
        snap = cls.__new__(cls)
        snap.state = cast("StateReading", dict(state))
        snap.inv = inventory if isinstance(inventory, Inventory) else Inventory(inventory)
        return snap

    @property
    def feet(self) -> Cell:
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

    def get(self, key, default=None):
        return self.state.get(key, default)

SIGHT_TTL_S = 3.0          # a round's look at "how far is the nearest of each": kept while the feet stay put
_SIGHT = {"key": None, "t": 0.0, "near": {}, "y": {}, "hits": {}}
SIGHT_PER_BLOCK = 4        # hits kept per block: a source is the nearest of them outside the protected cells
# the round's route answers and the last look are about the world we stood in (a new row may stand at the same feet)
lifecycle.in_place(__name__, "ROUTES", "_SIGHT")
PER_BLOCK_SINCE = (0, 1, 55)       # the jar version that answers /find?perBlock


@functools.cache
def _jar_answers_per_block():
    """Once known, per process: the running jar answers /find?perBlock (an unreachable game is asked again)."""
    import re
    return tuple(int(x) for x in re.findall(r"\d+", str(api.game_status().get("version", "0")))[:3]) >= PER_BLOCK_SINCE


def _per_block_ok():
    try:
        return _jar_answers_per_block()
    except api.McError as e:
        api.swallowed("world._per_block_ok", e)
        return False

def nearest(kinds, feet, dimension, radius=48, union=(), skip=None):
    """Blocks to the nearest of `kinds` in sight, or None — estimates never search the world themselves. `skip`:
    cells that are not there for this ask (a gather source is never a home block)."""

    names = [bare(k) for k in kinds]
    key = (tuple(feet), dimension)
    fresh = _SIGHT["key"] == key and time.time() - _SIGHT["t"] < SIGHT_TTL_S
    if not fresh:
        _SIGHT.update(key=key, t=time.time(), near={}, y={}, hits={})
    near, hits_of = _SIGHT["near"], _SIGHT.setdefault("hits", {})
    missing = [n for n in names if n not in near]
    if missing:
        ask = sorted({bare(u) for u in union} | set(missing)) if _per_block_ok() else missing
        ids = ",".join(mid(b) for b in ask)
        path = (f"/find?blocks={ids}&radius=48&limit={len(ask) * (SIGHT_PER_BLOCK + 1) + 8}"
                f"&perBlock={SIGHT_PER_BLOCK}" if _per_block_ok() else f"/find?blocks={ids}&radius=48&limit=20")
        try:
            hits = api.get(path)["blocks"]
        except api.McError:
            hits = []
        for b in ask:
            near.setdefault(b, None)
            hits_of.setdefault(b, [])
        for h in hits:
            b = bare(h["block"])
            hits_of.setdefault(b, []).append(h)
            if near.get(b) is None or h["distance"] < near[b]:
                near[b] = h["distance"]
                if "y" in h:
                    _SIGHT["y"][b] = h["y"]
    if skip is not None:          # a Protected of boxes alone is an empty set: asked, never truth-tested
        got = [h["distance"] for n in names for h in hits_of.get(n, ())
               if h["distance"] <= radius and (h["x"], h["y"], h["z"]) not in skip]
    else:
        got = [near[n] for n in names if near.get(n) is not None and near[n] <= radius]
    return min(got) if got else None

def sight_y(kinds, skip=None):
    """The y of the nearest of `kinds` the last look saw (`nearest`; `skip`: cells not there), or None: no read of its
    own."""
    if skip is not None:
        got = [(h["distance"], h["y"]) for k in kinds for h in _SIGHT.get("hits", {}).get(bare(k), ())
               if (h["x"], h["y"], h["z"]) not in skip]
        return min(got)[1] if got else None
    near, ys = _SIGHT["near"], _SIGHT["y"]
    got = [(near[n], ys[n]) for n in (bare(k) for k in kinds) if near.get(n) is not None and n in ys]
    return min(got)[1] if got else None

def find(blocks, radius=32, limit=50, exposed=False):
    """What `/find` sees."""

    ids = ",".join(mid(b) for b in blocks)
    return api.get(f"/find?blocks={ids}&radius={radius}&limit={limit}" + ("&exposed=true" if exposed else ""))["blocks"]

def entities(radius=16, types=None) -> list[EntityReading]:
    out = living(api.get(f"/entities?radius={radius}")["entities"])
    return [e for e in out if types is None or e["type"] in types]

def dark_spots(radius=4, max_light=7, limit=30):
    return api.get(f"/dark?radius={radius}&maxLight={max_light}&limit={limit}")["spots"]

def container():
    return api.get("/container")

REGION_MAX = 32 * 32 * 32       # the most cells one /blocks answers (the jar's MAX_REGION_VOLUME)

def slabs(lo, hi, most=REGION_MAX):
    """Pure: the box lo..hi cut along x into boxes of at most `most` cells — one read per slab (the jar refuses larger)."""

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
        self.blocks: dict[Cell, str] = {}
        self.props: dict[Cell, dict[str, str]] = {}
        for a, b in slabs(lo, hi):
            query = f"/blocks?from={a[0]},{a[1]},{a[2]}&to={b[0]},{b[1]},{b[2]}" + ("&props=1" if props else "")
            data = api.get(query)
            pal = [bare(p) for p in data["palette"]]
            for entry in data["blocks"]:
                x, y, z, i = entry[:4]
                self.blocks[(x, y, z)] = pal[i]
                if len(entry) > 4:
                    self.props[(x, y, z)] = entry[4]   # block state properties (mod >= 0.1.14 with props=1)

    @classmethod
    def of(cls, lo, hi, blocks):
        """A Region from known blocks ({cell: name}), no read: a scene's spec priced offline (bench estimates)."""
        r = cls.__new__(cls)
        r.lo, r.hi, r.blocks, r.props = tuple(lo), tuple(hi), dict(blocks), {}
        return r

    def prop(self, p, key):
        return self.props.get(p, {}).get(key)

    def covers(self, lo, hi):
        return all(self.lo[i] <= lo[i] and hi[i] <= self.hi[i] for i in range(3))

    def inside(self, p):
        return all(self.lo[i] <= p[i] <= self.hi[i] for i in range(3))

    def name(self, p):
        return self.blocks.get(p, "air")

    def solid(self, p):
        n = self.name(p)
        return n != "air" and n not in PASSABLE and not n.endswith(PASSABLE_SUFFIX) and n not in HAZARD

    def buries(self, p):
        """A head in this cell suffocates: solid and a full cube — no slab, stair or snow, no door, trapdoor or gate."""
        return self.solid(p) and not self.name(p).endswith(PARTIAL_SUFFIX + DOOR_SUFFIX)

    def open_door(self, p):
        """A door, trapdoor or gate standing open (read with props): passable though still there."""
        return is_door(self.name(p)) and self.prop(p, OPEN_PROP) == "true"

    def hazard(self, p):
        return self.name(p) in HAZARD

    def unbreakable(self, p):
        return self.name(p) in UNBREAKABLE

    def player_made(self, p):
        return self.name(p).endswith(PLAYER_MADE_SUFFIX)


# the last bag and box reads with the POST count then (R-a, R-b): one read serves ARM, the stand gate, HOLD and R4
# while nothing has been sent since — a send may change both
READS: dict = {}
lifecycle.in_place(__name__, "READS")


def bag():
    """The bag, read once while nothing was sent since (ARM's read serves HOLD)."""
    got = READS.get("bag")
    if got is not None and got[0] == api.STATE.posts:
        return got[1]
    inv = Inventory()
    READS["bag"] = (api.STATE.posts, inv)
    return inv


def box(lo, hi):
    """Blocks with their states over [lo, hi]: the last box read when it covers this one and nothing was sent since
    (one read per segment: ARM's names, the stand gate, R4's before; R4's after is the next segment's before)."""
    got = READS.get("box")
    if got is not None and got[0] == api.STATE.posts and got[1].covers(lo, hi):
        return got[1]
    region = Region(lo, hi, props=True)
    READS["box"] = (api.STATE.posts, region)
    return region

def region_around(points, pad=3, max_volume=32768):
    lo = [min(p[i] for p in points) - pad for i in range(3)]
    hi = [max(p[i] for p in points) + pad for i in range(3)]
    if (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1) > max_volume:
        return None
    return Region(lo, hi)

def connected(region, seed: Cell, ids) -> set[Cell]:
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
    """Pure given `tick`/`now`: is a background job done?"""

    if job.get("ready_tick") is not None and tick is not None:
        return tick >= job["ready_tick"]
    return job["ready_at"] <= (time.time() if now is None else now)

def feet() -> Cell:
    """The block the feet are in, (x, y, z): one /state read."""

    s = api.get("/state")
    return s["blockX"], s["blockY"], s["blockZ"]

def cells_with(region, name, key, value, want=True):
    """Pure: the `name` blocks of `region` whose block state `key` reads `value` (`want=False`: reads anything else)."""

    prop = getattr(region, "prop", None)
    return [p for p, n in region.blocks.items() if n == name and prop and (str(prop(p, key)) == value) == want]

def ripe_cells(region):
    """Pure: wheat blocks at full growth (age 7)."""
    return cells_with(region, "wheat", "age", "7")

def ripe_near(feet, radius=32):
    """Ripe wheat cells within `radius` of `feet` (one /find, then block states): reaped before a new plot is sown."""

    hits = find(["wheat"], radius=radius, limit=64) or []
    if not hits or feet is None:
        return []
    cells = [(h["x"], h["y"], h["z"]) for h in hits]
    lo = tuple(min(c[i] for c in cells) for i in range(3))
    hi = tuple(max(c[i] for c in cells) for i in range(3))
    return ripe_cells(Region(lo, hi, props=True))
