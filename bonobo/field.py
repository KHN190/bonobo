"""How long anything takes to reach us, estimated, and what placing blocks does to that. Straight-line time times a terrain factor learned from what walking actually cost, kept per bucket (open ground, underground, enclosed) by exponential smoothing. No search: this runs beside a 5 Hz loop, and a rough number that arrives beats an exact one that does not."""

import math

from .game import COVERED_SKY, MELEE_INFLATE, MOB_WIDTH, PLAYER_WIDTH

BUCKETS = ("open", "underground", "enclosed")
PRIOR = 1.6
MEMORY = 0.2
BLOCK_FACTOR = 1.8
SQUEEZE_FACTOR = 1.05
MAX_FACTOR = 12.0

class Terrain:

    def __init__(self, prior=PRIOR, memory=MEMORY):
        self.factor = {b: float(prior) for b in BUCKETS}
        self.memory = float(memory)
        self.seen = {b: 0 for b in BUCKETS}

    def observed(self, bucket, straight_s, actual_s):
        if bucket not in self.factor or straight_s <= 0 or actual_s <= 0:
            return self.factor.get(bucket, PRIOR)
        ratio = min(MAX_FACTOR, max(1.0, actual_s / straight_s))
        self.factor[bucket] += self.memory * (ratio - self.factor[bucket])
        self.seen[bucket] += 1
        return self.factor[bucket]

    def of(self, bucket):
        return self.factor.get(bucket, PRIOR)

TERRAIN = Terrain()

class Field:

    def __init__(self, speed=4.3, bucket="open", blocks=0, terrain=None, seal=None, cover=None, shape_now=(),
                 floor=(), plugs=None):
        self.speed = float(speed)
        self.bucket = bucket
        self.blocks = int(blocks)
        self.terrain = terrain or TERRAIN
        self.seal = None if seal is None else int(seal)   # blocks that seal the way we stand in; None: nothing we carry seals it
        self.cover = cover     # the nearest 2-high space (a cell), or None
        self.shape_now = tuple(shape_now)     # the shapes we stand in now (shape_at): priced like a planned reshape
        self.floor = tuple(floor)             # the blocks under our feet, top first (a dig down breaks these)
        self.plugs = dict(plugs or {})        # blocks already in our passage, per side (a unit step along it → n)

    def slowdown(self, squeezes=False, side=None):
        """How much longer anything takes over this ground than a straight line: learned per bucket times the blocks
        in its way (ours, and those already on its `side` of our passage)."""

        return self.terrain.of(self.bucket) * self.delay_ratio(squeezes, side)

    def side_of(self, here, point):
        """The side of our passage `point` is on (a key of plugs), or None off a passage."""
        for side in self.plugs:
            if (point[0] - here[0]) * side[0] + (point[2] - here[2]) * side[1] > 0:
                return side
        return None

    def arrival_s(self, src, dst, squeezes=False, speed=None, now=None):
        straight = math.dist(tuple(src), tuple(dst)) / float(speed or self.speed)
        return straight * self.slowdown(squeezes)

    def delay_ratio(self, squeezes=False, side=None):
        """What the blocks in the way (ours, and those already on the mob's side) do to how soon it arrives."""

        blocks = self.blocks + (self.plugs.get(side, 0) if side is not None else 0)
        if not blocks:
            return 1.0
        # the way sealed: a walker never arrives (a squeezer — spider, climber — still does, a little later)
        if not squeezes and self.seal is not None and blocks >= self.seal:
            return float("inf")
        return SQUEEZE_FACTOR if squeezes else BLOCK_FACTOR ** blocks

    def blocks_worth_placing(self):
        return self.bucket != "open" or self.seal is not None

    def with_block(self, cell=None):
        return Field(self.speed, self.bucket, self.blocks + 1, self.terrain, self.seal, self.cover, self.shape_now,
                     self.floor, self.plugs)

    def choke(self, src, dst, within=3.0):
        if math.dist(src, dst) < 2.0:
            return None
        span = math.dist(src, dst)
        t = min(1.0, max(1.0, within) / span)
        return tuple(int(math.floor(src[k] + (dst[k] - src[k]) * t + 0.5)) for k in range(3))

def bucket_of(state):
    if state.get("enclosed"):
        return "enclosed"
    if state.get("skyLight", 15) <= COVERED_SKY or state.get("y", 64) < 50:
        return "underground"
    return "open"

def bucket_at(region, here, radius):
    """Pure: the ground bucket at `here`: "enclosed" (walled and roofed), "underground" (solid overhead within `radius`) or "open"."""

    x, y, z = (int(math.floor(v)) for v in here)
    sides = [(x + dx, yy, z + dz) for yy in (y, y + 1) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    above = [(x, y + 2 + k, z) for k in range(max(0, int(radius) - 1))]
    if region.solid((x, y + 2, z)) and all(region.solid(c) for c in sides):
        return "enclosed"
    return "underground" if any(region.inside(c) and region.solid(c) for c in above) else "open"

def seal_at(region, here):
    """Pure: 2 when `here` is a 1-wide passage — walled on both sides along x or along z, at feet and head — so two
    blocks in it (feet and head: a walker jumps one) seal the way; else None (wider ground is not sealed at a glance)."""

    x, y, z = (int(math.floor(v)) for v in here)
    for dx, dz in ((1, 0), (0, 1)):
        if all(region.solid((x + s * dx, yy, z + s * dz)) for s in (1, -1) for yy in (y, y + 1)):
            return 2
    return None

ATTACK_RANGE = MELEE_INFLATE             # the attack box = body box grown this, sideways only (game.py)
PLAYER_HALF = PLAYER_WIDTH / 2
TALL_WIDTH = MOB_WIDTH["minecraft:enderman"]     # the one tall mob


def reached_from(offset, mob_width=TALL_WIDTH):
    """Pure: a tall mob standing in the open cell at `offset` (dx, dz) hits us at our cell's centre — its centre kept
    out of our roofed cell and the cells between, its attack box grown ATTACK_RANGE sideways."""
    reach = mob_width / 2 + ATTACK_RANGE
    for d in offset:
        gap = max(0.0, abs(d) - 0.5) + mob_width / 2 if d else 0.0    # nearest its centre gets on this axis
        if gap - reach >= PLAYER_HALF:
            return False
    return True


def tall_can_stand(solid, c):
    """Pure: a floor and three clear blocks (an enderman stands there)."""
    return solid((c[0], c[1] - 1, c[2])) and not any(solid((c[0], c[1] + k, c[2])) for k in range(3))


def low_cover_at(region, here, radius=6, mob_width=TALL_WIDTH):
    """Pure: the nearest cell within `radius` with floor, feet and head clear and a block 2 up — room for us, too low
    for a tall mob — that no tall mob in an open cell around reaches (fight_enderman_provoked: hit in the mouth).
    None when there is none."""
    x, y, z = (int(math.floor(v)) for v in here)
    near = int(math.ceil(0.5 + mob_width / 2 + ATTACK_RANGE + PLAYER_HALF))     # cells a mob could hit us from
    best = None
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            c = (x + dx, y, z + dz)
            if not (region.solid((c[0], y - 1, c[2])) and not region.solid(c) and not region.solid((c[0], y + 1, c[2]))
                    and region.solid((c[0], y + 2, c[2]))):
                continue
            if any(tall_can_stand(region.solid, (c[0] + ox, y, c[2] + oz)) and reached_from((ox, oz), mob_width)
                   for ox in range(-near, near + 1) for oz in range(-near, near + 1) if ox or oz):
                continue
            d = math.hypot(dx, dz)
            if d <= radius and (best is None or d < best[0]):
                best = (d, c)
    return None if best is None else best[1]


def stand_level(solid, x, z, y, span=4):
    """Pure: the y a body stands at in column (x, z) nearest `y` (floor under, feet and head clear), or None (a wall).
    Nearest, not highest: the top of a ceiling overhead is no ground a walker reaches (escape__walker_open_blocks)."""
    for yy in sorted(range(y - span, y + span + 1), key=lambda v: (abs(v - y), v)):
        if solid((x, yy - 1, z)) and not solid((x, yy, z)) and not solid((x, yy + 1, z)):
            return yy
    return None


def shape_at(region, here):
    """Pure: the shapes we stand in now, as reshape_options names them — ("under", n) on a column n above the ground
    round it, ("down", n) in a hole walled n high, ("roof", 9) under a cover no tall mob's open cell reaches."""
    x, y, z = (int(math.floor(v)) for v in here)
    solid = region.solid
    out = []
    sides = [(x + dx, z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    levels = [stand_level(solid, sx, sz, y) for sx, sz in sides]
    if all(lv is not None and lv < y for lv in levels):
        out.append(("under", min(y - lv for lv in levels if lv is not None)))
    walls = 0
    while walls < 3 and all(solid((sx, y + walls, sz)) for sx, sz in sides):
        walls += 1
    if walls:
        out.append(("down", walls))
    if solid((x, y + 2, z)):
        near = int(math.ceil(0.5 + TALL_WIDTH / 2 + ATTACK_RANGE + PLAYER_HALF))
        if not any(tall_can_stand(solid, (x + ox, y, z + oz)) and reached_from((ox, oz))
                   for ox in range(-near, near + 1) for oz in range(-near, near + 1) if ox or oz):
            out.append(("roof", 9))
    return tuple(out)


def blocks_in_passage(region, here, reach=4):
    """Pure: blocks already filling our 1-wide passage, per side ({unit step along it: feet + head of its first
    filled cell}) — a plug behind us seals nothing in front (combat__block_gap died to that); {} elsewhere."""
    x, y, z = (int(math.floor(v)) for v in here)
    solid = region.solid
    for dx, dz in ((1, 0), (0, 1)):
        if not all(solid((x + s * dx, yy, z + s * dz)) for s in (1, -1) for yy in (y, y + 1)):
            continue
        ax, az = dz, dx          # the passage runs across the walls
        sides = {}
        for s in (1, -1):
            for k in range(1, reach + 1):
                c = (x + s * k * ax, y, z + s * k * az)
                n = int(solid(c)) + int(solid((c[0], y + 1, c[2])))
                if n:
                    sides[(s * ax, s * az)] = n
                    break
            sides.setdefault((s * ax, s * az), 0)
        return sides
    return {}

def floor_at(region, here, depth=4):
    """Pure: the block names under our feet, top first, `depth` deep (what a dig down breaks)."""
    x, y, z = (int(math.floor(v)) for v in here)
    return tuple(region.name((x, y - 1 - i, z)) for i in range(depth))


def from_region(region, here, radius, speed=4.3, terrain=None):
    """The Field over the blocks read around `here` (perception.ground): its bucket and seal from the blocks themselves."""
    return Field(speed=speed, bucket=bucket_at(region, here, radius), terrain=terrain, seal=seal_at(region, here),
                 plugs=blocks_in_passage(region, here), cover=low_cover_at(region, here),
                 shape_now=shape_at(region, here), floor=floor_at(region, here))
