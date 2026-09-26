"""What a step costs, in ticks: the walk to where it happens plus how long the work takes. Shared by every solver
(decompose.py). No survival model, no prices of health: distance and measured durations, nothing else.

Durations are measured (`memory.duration`, the same keys the skill runner records under) once a key has
`skill.MIN_SAMPLES` runs; until then the priors below stand. Distances come from memory (the resource map, sightings,
stations) and from one cached `/find` per kind per round — the calls a recorded round carries, so a replay answers
the same way.
"""
import math

from .api import McError
from .data import GROUPS, bare
from .world import entities, find

TICKS_PER_S = 20
WALK_TICKS_PER_BLOCK = 1.5 / 0.12 / 1.0     # ~12.5 ticks a block, walking with detours
UNKNOWN_WALK_TICKS = 6000                   # nothing known nearby: what a search usually costs
# Work per unit when nothing has been measured yet, in ticks.
PRIOR_TICKS = {"craft": 60, "smelt_each": 200, "smelt_setup": 300, "mine_each": 60, "gather_each": 40,
               "hunt_each": 300, "fill": 20, "goto": 0, "build": 2400, "sleep": 400, "skill": 1200, "take": 200}
# Planner step kind -> (skill statistics key, units): the same keys the skill runner records under.
STAT_KEYS = {"mine": lambda s: (f"mine:{s.token}", s.count), "gather": lambda s: ("chop", s.count),
             "hunt": lambda s: (f"hunt:{s.token}", s.count), "smelt": lambda s: ("smelt", s.count),
             "craft": lambda s: ("craft", 1)}
HUNT_TYPES = ("minecraft:beef", "minecraft:porkchop", "minecraft:mutton", "minecraft:chicken")


def walk_ticks(distance):
    return int(float(distance) * WALK_TICKS_PER_BLOCK)


class Cost:
    """The cost model a planner is given. `snap` is this round's snapshot; `mem` and `blacklist` are optional (a
    test passes neither and gets the priors and straight lines)."""

    def __init__(self, snap, mem=None, blacklist=None):
        self.snap, self.mem = snap, mem
        self.blacklist = blacklist or {}
        self.cache = {}

    # -- where things are
    def _banned(self, key):
        import time
        exp = self.blacklist.get(tuple(key))
        return exp is not None and exp > time.time()

    def _known(self, kinds):
        """Distance to the nearest remembered one of these (resource map, sightings), or None."""
        if self.mem is None:
            return None
        here, dim = self.snap.feet, self.snap.dimension
        best = None
        for kind in kinds:
            spots = [r["pos"] for r in self.mem.seen(kind, dim)]
            for p in spots:
                if self._banned(p):
                    continue
                d = math.dist(tuple(p), here)
                best = d if best is None else min(best, d)
        return best

    def distance(self, blocks, radius=48):
        """Blocks to the nearest one of these: in sight now (one cached /find), else remembered, else None."""
        key = ("find", tuple(blocks), radius)
        if key not in self.cache:
            try:
                hits = [h for h in find(list(blocks), radius=radius, limit=20)
                        if not self._banned((h["x"], h["y"], h["z"]))]
            except McError:
                hits = []
            self.cache[key] = hits[0]["distance"] if hits else self._known(blocks)
        return self.cache[key]

    def _entity(self, types):
        key = ("ent", tuple(types))
        if key not in self.cache:
            try:
                es = [e for e in entities(64, list(types)) if not self._banned((e["id"], 0, 0))]
            except McError:
                es = []
            self.cache[key] = es[0]["distance"] if es else self._known(types)
        return self.cache[key]

    def _surface_trip(self):
        """Under rock, getting out is part of any surface trip, and it scales with depth."""
        from .data import COVERED_SKY
        if self.snap.get("skyLight", 15) > COVERED_SKY:
            return 0
        return 200 + 30 * max(0, 64 - int(self.snap.feet[1]))

    # -- what the planner asks
    def station_near(self, block):
        if self.mem is not None and any(math.dist(tuple(s["pos"]), self.snap.feet) <= 6
                                        for s in self.mem.stations(self.snap.dimension)
                                        if s.get("block") in (block, bare(block))):
            return True
        return self.distance([block], 6) is not None and self.distance([block], 6) <= 6

    def cheapest_food(self, options):
        from .knowledge import HUNT
        raw = {c: c.replace("cooked_", "") for c in options}
        dist = {c: self._entity(HUNT[raw[c]]) for c in options if raw[c] in HUNT}
        known = [c for c, d in dist.items() if d is not None]
        return min(known, key=lambda c: dist[c]) if known else options[0]

    def measured(self, step):
        """Ticks the skill runner has measured for this step, or None until enough runs exist."""
        if self.mem is None or step.kind not in STAT_KEYS:
            return None
        from .skill import MIN_SAMPLES
        key, units = STAT_KEYS[step.kind](step)
        per = self.mem.duration(key, min_samples=MIN_SAMPLES)
        return int(per * max(1, units) * TICKS_PER_S) if per is not None else None

    def estimate(self, step):
        """Ticks this step takes from here: measured work when there is enough of it, the prior otherwise, plus the
        walk to where it happens."""
        measured = self.measured(step)
        work = measured if measured is not None else self._prior_work(step)
        return work + self._walk(step)

    def _prior_work(self, step):
        k = step.kind
        if k == "smelt":
            return PRIOR_TICKS["smelt_each"] * step.count + PRIOR_TICKS["smelt_setup"]
        if k == "mine":
            return PRIOR_TICKS["mine_each"] * step.detail.get("breaks", step.count)
        if k == "gather":
            return PRIOR_TICKS["gather_each"] * step.count
        if k == "hunt":
            return PRIOR_TICKS["hunt_each"] * step.detail.get("kills", step.count)
        if k == "fill":
            return PRIOR_TICKS["fill"] * step.count
        return PRIOR_TICKS.get(k, 1000)

    def _walk(self, step):
        k = step.kind
        if k == "gather":
            d = self.distance(GROUPS["log"])
            return (walk_ticks(d) if d is not None else UNKNOWN_WALK_TICKS) + self._surface_trip()
        if k == "mine":
            d = self.distance(step.detail.get("blocks", ()), 32)
            return walk_ticks(d) if d is not None else UNKNOWN_WALK_TICKS
        if k == "hunt":
            d = self._entity(step.detail.get("types", ()))
            return (walk_ticks(d) if d is not None else UNKNOWN_WALK_TICKS) + self._surface_trip()
        if k == "fill":
            d = self._known(["water"])
            return walk_ticks(d) if d is not None else 1200
        if k == "goto":
            return walk_ticks(math.dist(self.snap.feet, tuple(step.detail["pos"])))
        return 0

    def plan_s(self, steps):
        """Seconds a whole plan takes: Σ Step.est."""
        return sum(s.est for s in steps) / TICKS_PER_S


class Prices:
    """{item: seconds to get one another way} for skills that ask what a thing is worth (the looter). Each price is
    what the planner would spend making one from an empty bag with the tools we hold; asked lazily and kept."""

    def __init__(self, cost, inv):
        self.cost, self.inv, self.cache = cost, inv, {}

    def __bool__(self):
        return True

    def get(self, item, default=None):
        if item not in self.cache:
            from .planner import Planner, Unplannable
            base = Planner.from_inventory(self.inv, self.cost)
            try:
                steps = Planner({}, base.inv.tools, self.cost).plan([(item, 1)])
                self.cache[item] = self.cost.plan_s(steps) if steps else None
            except (Unplannable, McError, KeyError, TypeError):
                self.cache[item] = None
        got = self.cache[item]
        return default if got is None else got
