"""What a step costs, in ticks: the walk to where it happens plus how long the work takes. The one cost model the planner prices with (`estimate`, `work`, `dig_to`, `walk_lb`, `site`, `facts`); the seconds to find a kind at the bottom (`seek_s`, `find_ticks`, `where`). One walk-time estimate: `walk_ticks`. No survival model, no prices of health: distance and measured durations, nothing else. Durations are measured (`memory.duration`, the same keys the skill runner records under) once a key has `skill.MIN_SAMPLES` runs; until then the priors below stand. Distances come from memory (the resource map, sightings, stations) and from one cached `/find` per kind per round — the calls a recorded round carries, so a replay answers the same way."""

import math
import time

from . import api
from .api import Interrupted, McError
from .data import SEARCH_LOOK_R
from .data import MEASURED_BAND, MACHINE_PROVIDES, STATION_R, TOOL_KINDS, DEEPSLATE_TOP, GROUPS, NAV_NODES, bare, mid
from .knowledge import SURFACE_Y, sources, step_station, work_s, food_count, soil_depth, dawn_s, body_facts, expected_find_s, step_kinds, WALK_TICKS_PER_BLOCK, dig_to_ticks, members, held_tiers, own_work, prior_work_ticks, FIND_AT, PRIOR_TICKS, prior_ticks, step_call, tool_ok, HUNT, SOURCE_BLOCKS, under_rock  # noqa: F401  (PRIOR_TICKS: re-exported)
from .skillcore import banned
from .world import Region, Versioned, job_ready, route_key, seen_hit
from .skill import MIN_SAMPLES
from .planner import Unplannable, plan_needs, way

from .game import TICKS_PER_S

DOOR_ROUTE = None      # (taught, here, there, walk_s) → seconds through a door, or None: mechanisms.door_route_s,
#                        a pure function wired by the brain (no import: the cost prices, the mechanisms module acts)
TABLE = "minecraft:crafting_table"   # nothing known nearby: what a search usually costs
# work per unit before anything is measured, in ticks, bare-handed: a held tool's declared speed is taken off (_sped_up)
# step kind → (statistics key, units): the keys the skill runner records under
STAT_KEYS = {"mine": lambda s: (f"mine:{s.token}", s.count), "gather": lambda s: ("chop", s.count),
             "hunt": lambda s: (f"hunt:{s.token}", s.count), "smelt": lambda s: ("smelt", s.count),
             "craft": lambda s: ("craft", 1)}

def walk_ticks(distance):
    """Ticks to walk `distance` straight-line blocks, detours included: the one walk-time estimate."""
    return int(float(distance) * WALK_TICKS_PER_BLOCK)

class _Ground(Region):
    """The ground as expected where nothing is read:"""

    def __init__(self, feet, target, block, soil, ore, read=None):
        pad = 3
        self.read = read                # the round's ground (Snapshot.region): what it read, it says
        self.lo = tuple(min(feet[i], target[i]) - pad for i in range(3))
        self.hi = tuple(max(feet[i], target[i]) + pad for i in range(3))
        self.blocks, self.props = {}, {}
        self.feet_y, self.target, self.block, self.soil, self.ore = feet[1], tuple(target), bare(block), soil, ore

    def name(self, p):
        if tuple(p) == self.target:
            return self.block
        if p in self.blocks:
            return self.blocks[p]           # what the way being planned changed
        if self.read is not None and self.read.inside(p):
            return self.read.name(p)
        k = self.feet_y - p[1]
        if k <= 0:
            return "air"
        if k <= self.soil or not self.ore:
            return "dirt"
        return "deepslate" if p[1] < DEEPSLATE_TOP else "stone"


def dug_way(feet, target, block, soil, ore, inv, protected=(), read=None):
    """The block names the way to stand where `target` can be mined breaks — nav.plan_way's own choice (a level way…"""
    from . import nav
    region, here, out = _Ground(feet, target, block, soil, ore, read), tuple(feet), []
    for _segment in range(abs(feet[1] - target[1]) + 2):
        steps, _why, _secs = nav.plan_way(region, here, tuple(target), "mine", inv, protected or ())
        if steps is None:
            return out or None
        out += [region.name((t["x"], t["y"], t["z"])) for t in steps
                if t["type"] == "mine" and (t["x"], t["y"], t["z"]) != tuple(target)]
        ends = [(t["x"], t["y"], t["z"]) for t in steps if t["type"] == "goto"]
        if not steps or not ends or ends[-1] == here:
            return out                    # standing where it is held, or no farther: a staircase is planned a segment at a time
        here = ends[-1]
    return out

def planned_bag(inv, held):
    """Pure: the bag with the plan's tools ({kind: tier}, fresh) in place of its own."""
    from .data import TOOL_MATERIAL_FOR_TIER, TOOL_USES
    from .knowledge import tool_item
    from .world import Inventory
    slots = [s for s in getattr(inv, "slots", ()) if bare(s["id"]).rpartition("_")[2] not in TOOL_KINDS]
    for kind, tier in held.items():
        slots.append({"id": tool_item(kind, tier), "count": 1, "damage": 0,
                      "maxDamage": TOOL_USES[TOOL_MATERIAL_FOR_TIER[tier]], "slot": len(slots)})
    return Inventory({"slots": slots, "equipment": dict(getattr(inv, "equipment", {}) or {})})

class _Gone:
    """Cells an estimate never goes to:"""

    def __init__(self, protected, blacklist, routes, now=None):
        self.protected, self.blacklist = protected, blacklist
        now = time.time() if now is None else now
        self.cells = frozenset({tuple(p) for p in blacklist if banned(blacklist, p, now)}
                               | {k[0] for k, (found, _s) in routes.items() if found is False
                                  and k[1:] == (2.0, NAV_NODES)})

    def __contains__(self, p):
        p = tuple(p)
        return p in self.cells or (self.protected is not None and p in self.protected)


def route_refused(routes, where):
    """Pure: the game's route to `where` was asked and not found (`routes`: the answers the snapshot was read with)."""
    return routes.get(route_key(where, 2.0, NAV_NODES), (None, None))[0] is False

class Cost:
    """The cost model a planner is given."""

    def __init__(self, snap, mem, blacklist=None, policy=None, reserved=(), stop=None):
        """Over the snapshot (its look and its ground included) and memory only: no world read. `reserved`: item ids
        the held plans consume (bag.RESERVED); `stop`: () → True ends a search."""

        self.snap, self.mem, self.region, self.stop = snap, mem, snap.region, stop
        self.reserved = frozenset(reserved)
        self.blacklist = blacklist if blacklist is not None else Versioned()
        self.cache = {}
        self.policy = policy

    def plans(self):
        """The round's plan memo, shared by every cost model built on this snapshot with the same readings (memory,…"""
        shared = self.snap.__dict__.setdefault("_plans", {})
        return shared.setdefault((id(self.mem), tuple(sorted(self.blacklist)), self.reserved, id(self.region)), {})

    # -- where things are
    def protected(self):
        """The cells never broken (memory.protected_cells: sites, a home's box): never a source to gather from; None
        with no memory to ask."""
        if "protected" not in self.cache:
            self.cache["protected"] = self.mem.protected_cells(self.snap.dimension)
        return self.cache["protected"]

    def not_there(self, sources=False):
        """Cells no estimate goes to (banned; with `sources`, protected), rebuilt on a write."""
        version = getattr(self.blacklist, "version", None)
        got = self.cache.get(("gone", sources))
        if got is None or version is None or got[0] != version:
            got = self.cache[("gone", sources)] = (version, _Gone(self.protected() if sources else None, self.blacklist,
                                                                  self.snap.routes))
        return got[1]

    def _nearest(self, kinds, sources=False):
        """(position, distance) of the nearest remembered one of these (memory.seen, "tree" for any log), or None;
        `sources`: a protected cell is not there."""

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
        key = ("ripe", token)
        if key not in self.cache:
            self.cache[key] = sum(j.get("count", 0) for j in self.mem.jobs(self.snap.dimension)
                                  if j["kind"] == "crop" and j.get("item") == token
                                  and job_ready(j, self.snap.state.get("gameTime")))
        return self.cache[key]

    def _known(self, kinds, sources=False):
        """Distance to the nearest remembered one of these, or None."""
        hit = self._nearest(kinds, sources)
        return hit[1] if hit else None

    def distance(self, blocks, radius: float = SEARCH_LOOK_R, sources=False):
        """Blocks to the nearest one of these: remembered (no world read), else in sight now (one cached /find), else
        None. `sources`: what a gather or a mine takes from — a protected cell (a home block) is not there."""

        key = ("find", tuple(blocks), radius, sources)
        if key not in self.cache:
            known = self._known(blocks, sources)
            if known is not None and known <= radius:
                self.cache[key] = known
        if key not in self.cache:
            seen = seen_hit(self.snap.hits, blocks, self.not_there(sources), radius)
            self.cache[key] = seen[0] if seen is not None else self._known(blocks, sources)
        return self.cache[key]

    def _entity(self, types):
        key = ("ent", tuple(types))
        if key not in self.cache:
            ids = {mid(t) for t in types}
            near = [e["distance"] for e in self.snap.mobs if mid(e["type"]) in ids
                    and (e.get("id") is None or not banned(self.blacklist, (e["id"], 0, 0)))]
            self.cache[key] = min(near) if near else self._known(types)
        return self.cache[key]

    def _surface_trip(self):
        """Under rock, getting out is part of any surface trip, and it scales with depth."""
        if not under_rock(self.snap.get("skyLight", 15)):
            return 0
        return PRIOR_TICKS["surface"] + PRIOR_TICKS["surface_per_block"] * max(0, SURFACE_Y - int(self.snap.feet[1]))

    # -- what the planner asks
    def facts(self):
        """What is true of the world for a plan, read once from the snapshot and memory (the contracts' `when` read
        these): the body (knowledge.body_facts), the dimension, a portal known here, the sites found, lava to pour."""
        dim, mem = self.snap.dimension, self.mem
        sites = (lambda kind: bool(mem.sites(None, kinds=[kind])))
        lava_bucket = self.snap.inv.count("minecraft:lava_bucket")
        return {**body_facts(self.snap.state),
                "dimension": dim, "portal": portal_known(mem, dim),
                "state:fortress_found": sites("fortress"), "state:stronghold_known": sites("stronghold"),
                "state:portal_room_found": sites("portal_room"),
                "lava": bool(lava_bucket) or bool(mem.seen("lava", dim))}

    def fight_line(self, step, held=None):
        """(ok, why): an optional fight a step makes is planned only above the fight line (S5) — the brain's one judge
        (knowledge.FIGHT_LINE ← brain.fight_line_holds) over this snapshot; (True, None) with no judge or body."""
        from . import knowledge, skill
        if knowledge.FIGHT_LINE is None or not getattr(skill.provider_of(step), "fights", None):
            return True, None              # no optional fight in it: its call's args are never built here
        found = skill.step_contract(step)
        if found is None:
            return True, None
        inv = self.snap.inv if held is None else planned_bag(self.snap.inv, held)
        try:
            return knowledge.FIGHT_LINE(found[0], found[1], self.snap.state, inv)
        except (IndexError, KeyError, TypeError):
            return True, None              # args it cannot read: the runner refuses them

    def line_kit(self, step, held=None):
        """[needs rows] of kit that each put the step's fight over the line (knowledge.LINE_KIT), from the bag as planned."""
        from . import knowledge, skill
        if knowledge.LINE_KIT is None:
            return []
        found = skill.step_contract(step)
        if found is None:
            return []
        inv = self.snap.inv if held is None else planned_bag(self.snap.inv, held)
        try:
            return knowledge.LINE_KIT(found[0], found[1], self.snap.state, inv)
        except (IndexError, KeyError, TypeError) as e:
            api.swallowed("cost.line_kit", e)       # args it cannot read: no kit, the fight stays refused
            return []

    def stored(self, token):
        """[(pos, item, count, the chance it still holds it)] of `token` in the containers seen here (memory), the
        nearest first."""
        mem, snap = self.mem, self.snap
        rate = mem.container_change_rate()
        ids = set(members(token))
        out = []
        for pos, item, have in sorted(mem.stored(token, snap.dimension), key=lambda r: math.dist(r[0], snap.feet)):
            rec = mem.container_record(pos)
            p = container_p(rec, ids, time.time() - rec.get("at", time.time()), rate)
            out.append((pos, item, have, p))
        return out

    def feet(self):
        """Where the body stands: where every route starts."""
        return tuple(self.snap.feet)

    def places(self):
        """Every place a step can be priced at here (site's answers: remembered spots, containers, the round's look)."""
        if "places" not in self.cache:
            dim = self.snap.dimension
            out = set(self._points())
            out |= {tuple(c["pos"]) for c in self.mem.data.get("containers", {}).values()
                    if c.get("dimension") == dim and c.get("pos")}
            out |= {(h["x"], h["y"], h["z"]) for hits in (self.snap.hits or {}).values() for h in hits}
            self.cache["places"] = frozenset(out)
        return self.cache["places"]

    @staticmethod
    def walk_ticks(distance):
        """walk_ticks, asked of the cost model (the planner's bound walks as the model prices a walk)."""
        return walk_ticks(distance)

    def station_near(self, block):
        """A station of `block` within STATION_R: one of ours (memory: stations, machines that provide it) or one in
        sight — used where it stands, never made again."""
        feet, dim = self.snap.feet, self.snap.dimension
        if self.mem.known_stations(block, dim, near=feet, within=STATION_R):
            return True
        if any(MACHINE_PROVIDES.get(tag) == mid(block) for m in self.mem.machines(dim)
               if math.dist(m["origin"], feet) <= STATION_R for tag in m.get("tags", ())):
            return True
        near = self.distance([block], STATION_R)
        return near is not None and near <= STATION_R

    def measured(self, step):
        """Ticks the skill runner has measured for this step, or None until enough runs exist."""
        if step.kind not in STAT_KEYS:
            return None
        key, units = STAT_KEYS[step.kind](step)
        per = self.mem.duration(key, min_samples=MIN_SAMPLES)
        if per is None:
            return None
        prior = prior_ticks(step)          # a measurement is trusted within MEASURED_BAND of the prior it replaces
        return int(min(max(per * max(1, units) * TICKS_PER_S, prior / MEASURED_BAND), prior * MEASURED_BAND))

    def estimate(self, step, held=None, at=None, table_back=True):
        """Ticks this step takes: its work (`work`) plus the walk to where it happens — from `at` (the plan's place
        before it) when that and the step's site are known, else from here; a withdrawal by the chance its container
        still holds the thing (a miss costs the walk)."""
        work = self.work(step, held, table_back)
        parts = {"work": work, **self._walk_parts(step, at, held)}
        if step.kind in ("seek", "wait"):
            parts[step.kind], parts["work"] = work, 0
        ticks = sum(parts.values())
        total = round(ticks / step.detail["p"]) if step.kind == "withdraw" and step.detail.get("p") else ticks
        step.parts = {**parts, "chance": total - ticks}      # E4: each part measured and fitted apart
        return total

    def work(self, step, held=None, table_back=True):
        """Ticks of the step's own work: measured when there is enough of it, else the prior less what the tools
        held save (`held`: {tool kind: tier}, the bag's when not given)."""
        measured = self.measured(step)
        if measured is not None:
            return measured
        if step.kind == "seek":
            kinds = step_kinds(step)
            return round(self.seek_s(kinds) * TICKS_PER_S)
        if step.kind == "wait":
            return round(dawn_s(self.snap.state) * TICKS_PER_S)
        if held is None:
            held = held_tiers(self.snap.inv)
        ticks = prior_work_ticks(step, held, TICKS_PER_S)
        if table_back and self.table_back(step):
            # the table placed for it is broken and carried on after (craft.takes_back): its break by what is held
            ticks += round(work_s([bare(TABLE)], [], held, TICKS_PER_S) * TICKS_PER_S)
        return ticks

    def table_back(self, step):
        """Does `step` place a crafting table it then breaks and carries on (a craft at one, none standing near)?
        Once a plan: the crafts after it at the table placed sit at it (brain.keeps_table, forward's one price)."""
        return step.kind == "craft" and self.made_at(step) == TABLE and not self.station_near(TABLE)

    def made_at(self, step):
        """The station a craft of `step` works at by its recipe (planner.way's), or the contract's; None for none."""
        key = ("made_at", step.token)
        if key not in self.cache:
            ways = [way(src, made, 1) for made, src in sources(step.token)]
            self.cache[key] = next((w[2] for w in ways if w[0].kind == step.kind and w[2]), None) or step_station(step)
        return self.cache[key]

    def soil(self):
        """The soil under the feet (knowledge.soil_depth) in `region`, the blocks perception read (never read here);…"""
        return soil_depth(self.region, tuple(self.snap.feet))

    def work_of(self, step, reach=True):
        """(breaks, kills) a step is expected to make: its own work (knowledge.own_work) and (`reach`) the digging
        to the nearest in sight (dug_way)."""
        breaks, kills = own_work(step)
        if reach and step.kind == "mine":
            breaks = breaks + self._dug(step)
        return breaks, kills

    def _dug(self, step):
        """The blocks the way to the nearest in sight breaks (dug_way), [] when none is in sight or no way is found."""
        blocks = step.detail.get("blocks") or ()
        feet = tuple(int(c) for c in self.snap.feet)
        hit = seen_hit(self.snap.hits, blocks, self.not_there(True))
        if hit is None:
            return []
        target = hit[1]
        key = ("dug", feet, tuple(target), tuple(blocks))
        if key not in self.cache:
            got = dug_way(feet, target, blocks[0] if blocks else "stone", self.soil(),
                          FIND_AT.get(step.token) is not None, self.snap.inv, self.protected(), self.region)
            self.cache[key] = got or []
        return list(self.cache[key])

    SOURCED = ("gather", "mine", "take", "hunt", "trade")      # step kinds that walk to where their thing is found

    def _source(self, step):
        """Blocks to where this step's thing is (in sight or remembered), None when nowhere known."""
        k = step.kind
        if k == "gather":
            return self.distance(GROUPS["log"], sources=True)
        if k in ("mine", "take"):
            return self.distance(step.detail.get("blocks", ()), sources=True)
        return self._entity(step.detail.get("types", ()))

    def known_source(self, step):
        """Is where this step goes known (in sight or remembered)?"""

        if step.kind in ("seek", "wait"):
            return False
        return step.kind not in self.SOURCED or self._source(step) is not None

    def site(self, step):
        """Where a step's work happens, when known: the position it names, else the nearest remembered (memory) or
        seen (the round's look) one of its source; None for work done where the body stands."""
        k = step.kind
        if k in ("goto", "withdraw", "look"):
            return tuple(step.detail["pos"])
        kinds = self._kinds_of(step)
        if not kinds:
            return None
        sources = k in ("gather", "mine", "take")
        key = ("site", k == "hunt", sources, tuple(kinds), self.not_there(sources), self.not_there(True))
        if key not in self.cache:
            hit = self._nearest(kinds, sources=sources)
            seen = seen_hit(self.snap.hits, kinds, self.not_there(True)) if hit is None and k != "hunt" else None
            self.cache[key] = hit[0] if hit is not None else (seen[1] if seen is not None else None)
        return self.cache[key]

    def _kinds_of(self, step):
        """A site's kinds: a search has none, its place is what it finds."""
        return [] if step.kind == "seek" else step_kinds(step)

    def walk_lb(self, step, held):
        """Ticks no walk to this step's site can beat (the digging to it aside: `dig_to`): from the nearest place a
        plan may stand before it (the feet, a remembered spot, a container, the round's look); the walk from here
        when the site is unknown, its search with the tools `held` ({tool kind: tier}) then."""
        site = self.site(step)
        if site is None:
            return self._walk(step, held=held, dig=False)
        key = ("walk_lb", tuple(site), step.kind, tuple(self._kinds_of(step) or ()))
        if key not in self.cache:
            # from every place a plan may stand before it: remembered, a container, the look's (site's own answers)
            near = min((math.dist(p, site) for p in self.places() if tuple(p) != tuple(site)), default=math.inf)
            # and as priced from here, first in the plan: the nearest source, which need not be the site's
            parts = self._walk_parts(step, None, None, dig=False) if step.kind in self.SOURCED else {"seek": 1}
            here = parts["walk"] if not parts["seek"] else math.inf
            self.cache[key] = min(walk_ticks(min(near, math.dist(self.snap.feet, site))), here)
        return self.cache[key]

    def _points(self):
        """Every remembered spot in this dimension (memory: notes, stations, sites): where a plan can stand."""
        dim = self.snap.dimension
        out = [tuple(r["pos"]) for r in self.mem.data.get("seen", []) if r.get("dimension") == dim]
        out += [tuple(s["pos"]) for s in self.mem.stations(dim)]
        return out + [tuple(s["pos"]) for s in self.mem.sites(dim)]

    def _walk(self, step, at=None, held=None, dig=True):
        return sum(self._walk_parts(step, at, held, dig).values())

    def _walk_parts(self, step, at=None, held=None, dig=True):
        """{"walk", "dig", "surface", "seek"} ticks of getting to where the step happens (seek: the walk to a thing
        nowhere known, priced by the prior)."""
        k = step.kind
        out = {"walk": 0, "dig": 0, "surface": 0, "seek": 0}
        site = self.site(step) if at is not None else None
        if at is not None and site is not None:
            out["walk"] = walk_ticks(math.dist(at, site))
            if k in ("goto", "withdraw", "look"):
                through = self.door_s(site, at)
                out["walk"] = round(through * TICKS_PER_S) if through is not None else out["walk"]
            out["dig"] = self.dig_to(step, held) if k == "mine" and dig else 0
        elif k in self.SOURCED:
            d = self._source(step)
            out["walk" if d is not None else "seek"] = walk_ticks(d) if d is not None else self.find_ticks(step_kinds(step), held)
            if k != "mine":
                out["surface"] = self._surface_trip()
            elif dig:
                out["dig"] = self.dig_to(step, held)
        elif k == "fill":
            d = self._known(["water"])
            out["walk" if d is not None else "seek"] = walk_ticks(d) if d is not None else self.find_ticks(["water"])
        elif k in ("goto", "withdraw", "look"):
            through = self.door_s(tuple(step.detail["pos"]))
            out["walk"] = round(through * TICKS_PER_S) if through is not None else \
                walk_ticks(math.dist(self.snap.feet, tuple(step.detail["pos"])))
        return out

    def dig_to(self, step, held=None):
        """Ticks the digging to the nearest one in sight takes (work_of's breaks beyond the step's own), each break
        with the best of `held` ({tool kind: tier}; the bag's when None)."""
        if held is None:
            held = held_tiers(self.snap.inv)
        return dig_to_ticks(self.work_of(step)[0], step, held, TICKS_PER_S)

    def door_s(self, where, at=None):
        """Seconds to `where` from `at` (the feet when None) through a taught door on the way, else None: the
        mechanisms memory holds (Memory.taught), never a file or the world."""
        taught = self.mem.taught(self.snap.dimension)
        if not taught or DOOR_ROUTE is None:
            return None
        return DOOR_ROUTE(taught, tuple(self.snap.feet if at is None else at), tuple(where),
                          lambda d: walk_ticks(d) / TICKS_PER_S)

    def plan_s(self, steps):
        """Seconds a whole plan takes: Σ Step.est."""
        return sum(s.est for s in steps) / TICKS_PER_S

    # -- seconds to a kind: where it is, the game's route to it, the chance a search finds one
    def hunger_rate(self):
        """The share of each step's seconds hunger adds until something is eaten (threat.hunger_slowed), 0 with food…"""
        state, inv = self.snap.state, self.snap.inv
        if "food" not in state or food_count(inv) > 0:
            return 0.0
        from . import threat
        return threat.hunger_slowed(state["food"])

    def reachable(self, kinds):
        """False only when the game's route to the nearest remembered one was asked and not found."""
        spots = [tuple(r["pos"]) for k in kinds for r in self.mem.seen(k, self.snap.dimension)]
        return not spots or not route_refused(self.snap.routes, min(spots, key=lambda p: math.dist(p, self.snap.feet)))

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
            return self.find_ticks(kinds) / TICKS_PER_S
        return max(1.0, round(walk_ticks(known) / TICKS_PER_S + 2.0, 1))

    def route_s(self, kinds):
        """The game's own walk estimate when it was asked before the snapshot was read (snap.routes); through a taught
        door on the way, the walk to its press, the press and the walk through."""

        where = self.where(kinds)
        if where is None:
            return None
        through = self.door_s(where)
        if through is not None:
            return through
        found, seconds = self.snap.routes.get(route_key(where, 2.0, NAV_NODES), (None, None))
        return seconds if found else None

    def find_ticks(self, kinds, held=None):
        """Ticks to find one of `kinds` never seen (knowledge.expected_find_s): the soonest of them, from these feet
        with the tools `held` ({tool kind: tier}) when the search runs, the bag's when None."""
        facts = {"y": self.snap.feet[1], "held": held_tiers(self.snap.inv) if held is None else held,
                 "feet": self.snap.feet, "biomes": self.snap.biomes}
        return round(min(expected_find_s(k, facts) for k in (kinds or ["other"])) * TICKS_PER_S)

def portal_known(mem, dimension):
    """Pure over memory: a portal remembered in `dimension` (a built one or a site)."""
    return bool(mem.machines(dimension, "portal") or mem.sites(dimension, kinds=["portal"]))


def container_p(record, ids, age_s, rate):
    """Pure: the chance a container holds one of `ids`, from its record: held then, discounted by the change rate
    over the record's age; not held then, the chance it changed since."""
    held = any(record["items"].get(i, 0) > 0 for i in ids)
    return math.exp(-rate * age_s) if held else 1.0 - math.exp(-rate * age_s)


class Prices:
    """{item: seconds to get one another way} for skills that ask what a thing is worth (the looter)."""

    def __init__(self, cost, inv):
        self.cost, self.inv, self.cache = cost, inv, {}

    def __bool__(self):
        return True

    def get(self, item, default=None):
        if item not in self.cache:
            from .world import Inventory
            tools = Inventory({"slots": [s for s in self.inv.slots if bare(s["id"]).rpartition("_")[2] in TOOL_KINDS],
                               "equipment": {}})       # what one more takes from nothing but the tools held
            try:
                steps = plan_needs(tools, [(item, 1)], self.cost)
                self.cache[item] = self.cost.plan_s(steps) if steps else None
            except Interrupted:
                raise                     # S7
            except (Unplannable, McError, KeyError, TypeError):
                self.cache[item] = None
        got = self.cache[item]
        return default if got is None else got
