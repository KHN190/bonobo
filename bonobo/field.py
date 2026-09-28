"""How long anything takes to reach us, estimated, and what placing blocks does to that. Straight-line time times a terrain factor learned from what walking actually cost, kept per bucket (open ground, underground, enclosed) by exponential smoothing. No search: this runs beside a 5 Hz loop, and a rough number that arrives beats an exact one that does not."""

import math

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

    def __init__(self, speed=4.3, bucket="open", blocks=0, terrain=None, seal=None):
        self.speed = float(speed)
        self.bucket = bucket
        self.blocks = int(blocks)
        self.terrain = terrain or TERRAIN
        self.seal = None if seal is None else int(seal)   # blocks that seal the way we stand in; None: nothing we carry seals it

    def slowdown(self, squeezes=False):
        """How much longer anything takes over this ground than a straight line: learned per bucket times our placed blocks' cost."""

        return self.terrain.of(self.bucket) * self.delay_ratio(squeezes)

    def arrival_s(self, src, dst, squeezes=False, speed=None, now=None):
        straight = math.dist(tuple(src), tuple(dst)) / float(speed or self.speed)
        return straight * self.slowdown(squeezes)

    def delay_ratio(self, squeezes=False):
        """What the blocks we placed do to how soon something arrives."""

        if not self.blocks:
            return 1.0
        # the way sealed: a walker never arrives (a squeezer — spider, climber — still does, a little later)
        if not squeezes and self.seal is not None and self.blocks >= self.seal:
            return float("inf")
        return SQUEEZE_FACTOR if squeezes else BLOCK_FACTOR ** self.blocks

    def blocks_worth_placing(self):
        return self.bucket != "open" or self.seal is not None

    def with_block(self, cell=None):
        return Field(self.speed, self.bucket, self.blocks + 1, self.terrain, self.seal)

    def choke(self, src, dst, within=3.0):
        if math.dist(src, dst) < 2.0:
            return None
        span = math.dist(src, dst)
        t = min(1.0, max(1.0, within) / span)
        return tuple(int(math.floor(src[k] + (dst[k] - src[k]) * t + 0.5)) for k in range(3))

def bucket_of(state):
    if state.get("enclosed"):
        return "enclosed"
    if state.get("skyLight", 15) <= 4 or state.get("y", 64) < 50:
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

def from_region(region, here, radius, speed=4.3, terrain=None):
    """The Field over the blocks read around `here` (perception.ground): its bucket and seal from the blocks themselves."""
    return Field(speed=speed, bucket=bucket_at(region, here, radius), terrain=terrain, seal=seal_at(region, here))
