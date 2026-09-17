"""How long anything takes to reach us, estimated, and what placing blocks does to that.

Straight-line time times a terrain factor learned from what walking actually cost, kept per bucket (open ground,
underground, enclosed) by exponential smoothing. No search: this runs beside a 5 Hz loop, and a rough number that
arrives beats an exact one that does not.
"""
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

    def __init__(self, speed=4.3, bucket="open", blocks=0, terrain=None):
        self.speed = float(speed)
        self.bucket = bucket
        self.blocks = int(blocks)
        self.terrain = terrain or TERRAIN

    def slowdown(self, squeezes=False):
        """How much longer anything takes over this ground than over a straight line: what the ground itself costs
        (learned per bucket) times what the blocks we placed cost. The one definition of "slower" — `estimate`
        asks for it rather than multiplying two of its own."""
        return self.terrain.of(self.bucket) * self.delay_ratio(squeezes)

    def arrival_s(self, src, dst, squeezes=False, speed=None, now=None):
        straight = math.dist(tuple(src), tuple(dst)) / float(speed or self.speed)
        return straight * self.slowdown(squeezes)

    def delay_ratio(self, squeezes=False):
        """What the blocks we placed do to how soon something arrives.

        A wall is only a wall to what has to walk round it. What climbs, squeezes or teleports goes over the first
        block and the tenth is worth nothing more, so stacking does not compound for those: a compounding 5% put a
        four-block wall against a spider at a 21% delay, and the sweep found the model building it.
        """
        if not self.blocks:
            return 1.0
        return SQUEEZE_FACTOR if squeezes else BLOCK_FACTOR ** self.blocks

    def blocks_worth_placing(self):
        return self.bucket != "open"

    def with_block(self, cell=None):
        return Field(self.speed, self.bucket, self.blocks + 1, self.terrain)

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


def for_state(state, speed=4.3, terrain=None):
    return Field(speed=speed, bucket=bucket_of(state), terrain=terrain)
