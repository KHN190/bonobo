"""What a step costs, in ticks: the walk to where it happens plus how long the work takes. The one cost model, shared by every solver (decompose.py): the planner asks `estimate`, the column solver (actions.table) asks the seconds methods at the bottom (`work_s`, `seek_s`, `find_p`, `where`). One walk-time estimate: `walk_ticks`. No survival model, no prices of health: distance and measured durations, nothing else. Durations are measured (`memory.duration`, the same keys the skill runner records under) once a key has `skill.MIN_SAMPLES` runs; until then the priors below stand. Distances come from memory (the resource map, sightings, stations) and from one cached `/find` per kind per round — the calls a recorded round carries, so a replay answers the same way."""

import math

from .api import McError
from .beliefs import CONFIG as _PLAY
from .data import DEEPSLATE_TOP, LEVEL_SIGHT_DEPTH, STAIR_CELLS, FIND_P, GROUPS, NAV_NODES, ROUTE_FACTOR, WALK_BLOCKS_PER_TICK, bare
from .knowledge import soil_depth, dig_ticks, held_tiers, own_work, work_s, dawn_s, FIND_AT, MIN_FIND_P, PRIOR_TICKS, prior_ticks, step_call, tool_ok, HUNT, SOURCE_BLOCKS, under_rock  # noqa: F401  (PRIOR_TICKS: re-exported)
from .skillcore import banned
from .world import ROUTES, entities, job_ready, nearest, route_key, sight_y
from .skill import MIN_SAMPLES
from .planner import Planner, Unplannable

TICKS_PER_S = 20
DOOR_ROUTE = None      # mechanisms.route_s, wired by the brain: seconds through a taught door, or None

WALK_TICKS_PER_BLOCK = ROUTE_FACTOR / WALK_BLOCKS_PER_TICK     # ~12.5 ticks a block, walking with detours
UNKNOWN_WALK_TICKS = 6000                   # nothing known nearby: what a search usually costs
# work per unit before anything is measured, in ticks, bare-handed: a held tool's declared speed is taken off (_sped_up)
# step kind → (statistics key, units): the keys the skill runner records under
STAT_KEYS = {"mine": lambda s: (f"mine:{s.token}", s.count), "gather": lambda s: ("chop", s.count),
             "hunt": lambda s: (f"hunt:{s.token}", s.count), "smelt": lambda s: ("smelt", s.count),
             "craft": lambda s: ("craft", 1)}

def walk_ticks(distance):
    """Ticks to walk `distance` straight-line blocks, detours included: the one walk-time estimate."""
    return int(float(distance) * WALK_TICKS_PER_BLOCK)

def dig_blocks(feet_y, y, ore, soil):
    """Pure: the blocks dug to reach y from the feet, expected — a buried surface kind straight down through the soil
    over it; an ore a level stand holds (LEVEL_SIGHT_DEPTH: no region read here) nothing, deeper by a staircase
    (STAIR_CELLS a step), its first `soil` steps soil (knowledge.soil_depth), the rest rock (deepslate below DEEPSLATE_TOP)."""
    if not ore:
        return ["dirt"] * max(0, feet_y - 1 - y)
    if y >= feet_y - LEVEL_SIGHT_DEPTH:
        return []
    out = []
    for k in range(1, feet_y - y):
        level = feet_y - k
        rock = "deepslate" if level < DEEPSLATE_TOP else "stone"
        out += ["dirt" if k <= soil else rock] * STAIR_CELLS
    return out

class _Gone:
    """Cells an estimate never goes to: banned now, or protected (None: none asked)."""

    def __init__(self, protected, blacklist):
        self.protected, self.blacklist = protected, blacklist

    def __contains__(self, p):
        return banned(self.blacklist, tuple(p)) or (self.protected is not None and tuple(p) in self.protected)

class Cost:
    """The cost model a planner is given."""

    def __init__(self, snap, mem=None, blacklist=None, known=None, finds=None, policy=None, ripe=None, reserved=()):
        """`known`: fn(kinds) -> distance or None, standing in for memory (offline: no snapshot, no world).
        `reserved`: item ids the held plans will consume (bag.RESERVED), kept from a better tool's material."""

        self.snap, self.mem = snap, mem
        self.reserved = frozenset(reserved)
        self.blacklist = blacklist or {}
        self.cache = {}
        self._known_fn = known
        self._finds = finds
        self.policy = policy
        self._ripe = ripe            # offline: {token: ripe cells} standing in for memory and the world

    # -- where things are
    def protected(self):
        """The cells never broken (memory.protected_cells: sites, a home's box): never a source to gather from; None
        with no memory to ask."""
        if "protected" not in self.cache:
            self.cache["protected"] = (self.mem.protected_cells(self.snap.dimension)
                                       if self.mem is not None and self.snap is not None else None)
        return self.cache["protected"]

    def not_there(self, sources=False):
        """The cells no estimate goes to: banned (no way there), and with `sources` the protected ones."""
        return _Gone(self.protected() if sources else None, self.blacklist)

    def _nearest(self, kinds, sources=False):
        """(position, distance) of the nearest remembered one of these (memory.seen, "tree" for any log), or None;
        `sources`: a protected cell is not there."""

        if self.mem is None or self.snap is None:
            return None
        kinds = list(kinds) + (["tree"] if any(bare(k).endswith("log") for k in kinds) else [])
        here, dim = self.snap.feet, self.snap.dimension
        skip = self.not_there(sources)
        spots = [tuple(r["pos"]) for k in kinds for r in self.mem.seen(k, dim) if tuple(r["pos"]) not in skip]
        best = min(spots, key=lambda p: math.dist(p, here), default=None)
        return (best, math.dist(best, here)) if best is not None else None

    def ripe(self, token):
        """Ripe crop cells known to give `token` from due crop jobs (memory only: an estimate never touches the world)."""

        if token != "minecraft:wheat":
            return 0
        if self._ripe is not None:
            return self._ripe.get(token, 0)
        key = ("ripe", token)
        if key not in self.cache:
            n = 0
            if self.mem is not None and self.snap is not None:
                n = sum(j.get("count", 0) for j in self.mem.jobs(self.snap.dimension)
                        if j["kind"] == "crop" and j.get("item") == token
                        and job_ready(j, self.snap.state.get("gameTime")))
            self.cache[key] = n
        return self.cache[key]

    def _known(self, kinds, sources=False):
        """Distance to the nearest remembered one of these, or None."""
        if self._known_fn is not None:
            return self._known_fn(list(kinds))
        hit = self._nearest(kinds, sources)
        return hit[1] if hit else None

    def distance(self, blocks, radius=48, sources=False):
        """Blocks to the nearest one of these: remembered (no world read), else in sight now (one cached /find), else
        None. `sources`: what a gather or a mine takes from — a protected cell (a home block) is not there."""

        key = ("find", tuple(blocks), radius, sources)
        if key not in self.cache and self._finds is not None:
            got = [self._finds[b] for b in blocks if b in self._finds and self._finds[b] <= radius]
            self.cache[key] = min(got) if got else self._known(blocks, sources)
        if key not in self.cache:
            known = self._known(blocks, sources)
            if known is not None and known <= radius:
                self.cache[key] = known
        if key not in self.cache:
            # in sight: the round's one look (world.nearest), never a search of its own
            seen = None
            if self.snap is not None:
                seen = nearest(list(blocks), self.snap.feet, self.snap.dimension, radius, union=SOURCE_BLOCKS,
                               skip=self.not_there(sources))
            self.cache[key] = seen if seen is not None else self._known(blocks, sources)
        return self.cache[key]

    def _entity(self, types):
        key = ("ent", tuple(types))
        if key not in self.cache and self._finds is not None:
            got = [self._finds[t] for t in types if t in self._finds]
            self.cache[key] = min(got) if got else self._known(types)
        if key not in self.cache:
            try:
                es = [e for e in entities(64, list(types)) if not banned(self.blacklist, (e["id"], 0, 0))]
            except McError:
                es = []
            self.cache[key] = es[0]["distance"] if es else self._known(types)
        return self.cache[key]

    def _surface_trip(self):
        """Under rock, getting out is part of any surface trip, and it scales with depth."""
        if not under_rock(self.snap.get("skyLight", 15)):
            return 0
        return 200 + 30 * max(0, 64 - int(self.snap.feet[1]))

    # -- what the planner asks
    def station_near(self, block):
        if self.mem is not None and any(math.dist(tuple(s["pos"]), self.snap.feet) <= 6
                                        for s in self.mem.stations(self.snap.dimension)
                                        if s.get("block") in (block, bare(block))):
            return True
        return self.distance([block], 6) is not None and self.distance([block], 6) <= 6

    def measured(self, step):
        """Ticks the skill runner has measured for this step, or None until enough runs exist."""
        if self.mem is None or step.kind not in STAT_KEYS:
            return None
        key, units = STAT_KEYS[step.kind](step)
        per = self.mem.duration(key, min_samples=MIN_SAMPLES)
        return int(per * max(1, units) * TICKS_PER_S) if per is not None else None

    def estimate(self, step):
        """Ticks this step takes from here: measured work when there is enough of it, the prior otherwise, plus the walk to where it happens."""

        return self.work_ticks(step) + self._walk(step)

    def work_ticks(self, step):
        """Ticks of the step's own work, without the walk."""
        measured = self.measured(step)
        return measured if measured is not None else max(0, self._prior_work(step) - self._sped_up(step))

    def _sped_up(self, step):
        """Ticks the carried tools save on this step's prior work (work_of; the prior is the hand's)."""
        inv = getattr(self.snap, "inv", None)
        if inv is None:
            return 0
        breaks, kills = own_work(step)          # the digging to it is priced with its tools already (_overburden_ticks)
        return round((work_s(breaks, kills, {}, TICKS_PER_S) - work_s(breaks, kills, held_tiers(inv), TICKS_PER_S))
                     * TICKS_PER_S)

    def work_of(self, step, reach=True):
        """(breaks, kills) a step is expected to make: its own work (knowledge.own_work) and (`reach`) the digging
        to the nearest in sight (dig_blocks)."""
        breaks, kills = own_work(step)
        if reach and step.kind == "mine" and self.snap is not None:
            y = sight_y(step.detail.get("blocks") or (), self.not_there(True))
            if y is not None:
                breaks = breaks + dig_blocks(int(self.snap.feet[1]), int(y), FIND_AT.get(step.token) is not None,
                                             self.soil())
        return breaks, kills

    def _prior_work(self, step):
        if step.kind == "seek":
            kinds = list(step.detail.get("kinds") or [step.token])
            return round(self.seek_s(kinds) / max(MIN_FIND_P, self.find_p(kinds)) * TICKS_PER_S)
        if step.kind == "wait":
            return round(dawn_s(getattr(self.snap, "state", None) or {}) * TICKS_PER_S)
        return prior_ticks(step)

    SOURCED = ("gather", "mine", "hunt", "trade")      # step kinds that walk to where their thing is found

    def _source(self, step):
        """Blocks to where this step's thing is (in sight or remembered), None when nowhere known."""
        k = step.kind
        if k == "gather":
            return self.distance(GROUPS["log"], sources=True)
        if k == "mine":
            return self.distance(step.detail.get("blocks", ()), 32, sources=True)
        return self._entity(step.detail.get("types", ()))

    def known_source(self, step):
        """Is where this step goes known (in sight or remembered)?"""

        if step.kind == "seek":
            return False
        return step.kind not in self.SOURCED or self._source(step) is not None

    def _walk(self, step):
        k = step.kind
        if k in self.SOURCED:
            d = self._source(step)
            return (walk_ticks(d) if d is not None else UNKNOWN_WALK_TICKS) \
                + (self._surface_trip() if k != "mine" else self._overburden_ticks(step))
        if k == "fill":
            d = self._known(["water"])
            return walk_ticks(d) if d is not None else 1200
        if k in ("goto", "withdraw", "look"):
            through = self.door_s(tuple(step.detail["pos"]))
            return round(through * TICKS_PER_S) if through is not None else \
                walk_ticks(math.dist(self.snap.feet, tuple(step.detail["pos"])))
        return 0

    def soil(self):
        """The soil under the feet (knowledge.soil_depth): no column is read here, so the prior."""
        return soil_depth(None, tuple(self.snap.feet))

    def _overburden_ticks(self, step):
        """The digging to the nearest one in sight (dig_blocks), each break by the tool the bag holds for it."""
        if self.snap is None:
            return 0
        y = sight_y(step.detail.get("blocks", ()), self.not_there(True))
        if y is None:
            return 0
        blocks = dig_blocks(int(self.snap.feet[1]), int(y), FIND_AT.get(step.token) is not None, self.soil())
        return dig_ticks(blocks, getattr(self.snap, "inv", None))

    def door_s(self, where):
        """Seconds to `where` through a taught door on the way (mechanisms, wired as DOOR_ROUTE), else None: the
        stored mechanisms and the snapshot's feet, never a world read."""
        if DOOR_ROUTE is None or self.snap is None:
            return None
        return DOOR_ROUTE(tuple(self.snap.feet), tuple(where), lambda d: walk_ticks(d) / TICKS_PER_S,
                          dimension=self.snap.dimension)

    def plan_s(self, steps):
        """Seconds a whole plan takes: Σ Step.est."""
        return sum(s.est for s in steps) / TICKS_PER_S

    # -- what the column solver asks (actions.table), in seconds
    def _route(self, where):
        """(found, seconds) of the game's walk to `where` when asked this round (nav's route cache), else (None, None)."""
        return ROUTES.get(route_key(where, 2.0, NAV_NODES), (None, None))

    def reachable(self, kinds):
        """False only when the game's route to the nearest known one was asked and not found."""
        where = self.where(kinds)
        if where is None:
            return True
        return self._route(where)[0] is not False

    def where(self, kinds):
        """The position of the nearest known one, or None: what "on the way" is judged by."""
        hit = self._nearest(kinds)
        return hit[0] if hit else None

    def seek_s(self, kinds):
        """Seconds to reach one of these: the game's route, else the known distance walked, else the prior."""

        seconds = self.route_s(kinds)
        if seconds is not None:
            return max(1.0, round(float(seconds), 1))
        known = self._known(kinds)
        if known is None:
            return float(_PLAY["plan"]["seek_prior_s"])
        return max(1.0, round(walk_ticks(known) / TICKS_PER_S + 2.0, 1))

    def route_s(self, kinds):
        """The game's own walk estimate when already asked this round (nav's route cache; read, never added to);
        through a taught door on the way, the walk to its press, the press and the walk through."""

        where = self.where(kinds)
        if where is None:
            return None
        through = self.door_s(where)
        if through is not None:
            return through
        found, seconds = self._route(where)
        return seconds if found else None

    def find_p(self, kinds):
        """The chance a look for one of these finds it: by how the game makes it (data.FIND_P), else the prior."""
        known = [FIND_P[bare(k)] for k in kinds if bare(k) in FIND_P]
        return max(known) if known else float(_PLAY["plan"]["exists_prior"])

class Prices:
    """{item: seconds to get one another way} for skills that ask what a thing is worth (the looter)."""

    def __init__(self, cost, inv):
        self.cost, self.inv, self.cache = cost, inv, {}

    def __bool__(self):
        return True

    def get(self, item, default=None):
        if item not in self.cache:
            base = Planner.from_inventory(self.inv, self.cost)
            try:
                steps = Planner({}, base.inv.tools, self.cost).plan([(item, 1)])
                self.cache[item] = self.cost.plan_s(steps) if steps else None
            except (Unplannable, McError, KeyError, TypeError):
                self.cache[item] = None
        got = self.cache[item]
        return default if got is None else got
