"""What a step costs, in ticks: the walk to where it happens plus how long the work takes. The one cost model the planner prices with (`estimate`, `work`, `dig_to`, `walk_lb`, `site`, `facts`); the seconds to find a kind at the bottom (`seek_s`, `find_ticks`, `where`). One walk-time estimate: `walk_ticks`. No survival model, no prices of health: distance and measured durations, nothing else. Durations are measured (`memory.duration`, the same keys the skill runner records under) once a key has `skill.MIN_SAMPLES` runs; until then the priors below stand. Distances come from memory (the resource map, sightings, stations) and from one cached `/find` per kind per round — the calls a recorded round carries, so a replay answers the same way."""

import math
import time

from . import api
from .api import Interrupted, McError
from .data import SEARCH_LOOK_R
from .data import MEASURED_BAND, MACHINE_PROVIDES, STATION_R, TOOL_KINDS, DEEPSLATE_TOP, GROUPS, HARDNESS, HAZARD, NAV_NODES, bare, mid
from .knowledge import SURFACE_Y, find_class, sources, step_station, work_s, food_count, soil_depth, dawn_s, body_facts, expected_find_s, step_kinds, walk_ticks, dig_to_ticks, members, held_tiers, own_work, prior_work_ticks, FIND_AT, PRIOR_TICKS, prior_ticks, step_call, tool_ok, HUNT, SOURCE_BLOCKS, under_rock  # noqa: F401  (PRIOR_TICKS, walk_ticks: re-exported)
from .skillcore import ban_state, banned
from .world import Region, Versioned, job_ready, route_key
from .skill import MIN_SAMPLES
from .planner import Step, Unplannable, plan_needs, way

from .game import TICKS_PER_S

DOOR_ROUTE = None      # (taught, here, there, walk_s) → seconds through a door, or None: mechanisms.door_route_s,
#                        a pure function wired by the brain (no import: the cost prices, the mechanisms module acts)
TABLE = "minecraft:crafting_table"   # nothing known nearby: what a search usually costs
# work per unit before anything is measured, in ticks, bare-handed: a held tool's declared speed is taken off (_sped_up)
# step kind → (statistics key, units): the keys the skill runner records under
STAT_KEYS = {"mine": lambda s: (f"mine:{s.token}", s.count), "gather": lambda s: ("chop", s.count),
             "hunt": lambda s: (f"hunt:{s.token}", s.count), "smelt": lambda s: ("smelt", s.count),
             "craft": lambda s: ("craft", 1)}

class _Ground(Region):
    """The ground as expected where nothing is read:"""

    def __init__(self, feet, target, block, soil, ore, read=None, void=()):
        pad = 3
        self.read = read                # the round's ground (Snapshot.region): what it read, it says
        self.void = frozenset(void)     # unread cells a failure named (banned): never expected ground
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
        if tuple(p) in self.void:
            return "air"
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

    def __init__(self, protected, blacklist, routes, now=None, state=None):
        self.protected, self.blacklist = protected, blacklist
        now = time.time() if now is None else now
        self.cells = frozenset({tuple(p) for p in blacklist if banned(blacklist, p, now, state)}
                               | {k[0] for k, (found, _s) in routes.items() if found is False
                                  and k[1:] == (2.0, NAV_NODES)})

    def __contains__(self, p):
        p = tuple(p)
        return p in self.cells or (self.protected is not None and p in self.protected)


def stand_kind(kinds):
    """Pure: the door's stand kind for working one of `kinds` (nav.stands_for): a fluid is clicked (use_item: a
    bucket), a block mined; None for what is no block (a mob, a pseudo-kind): its way is the walker's."""
    names = [bare(k) for k in kinds]
    if not names or any(n not in HARDNESS for n in names):
        return None
    return "use_item" if any(n in HAZARD for n in names) else "mine"


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
            state = ban_state(self.snap.feet, frozenset(s["id"] for s in self.snap.inv.slots if s.get("count")))
            got = self.cache[("gone", sources)] = (version, _Gone(self.protected() if sources else None, self.blacklist,
                                                                  self.snap.routes, state=state))
        return got[1]

    def _nearest(self, kinds, sources=False, kind=None):
        """(position, distance) of the nearest remembered one of these (memory.seen, "tree" for any log), or None;
        `sources`: a protected cell is not there."""

        kinds = list(kinds) + (["tree"] if any(bare(k).endswith("log") for k in kinds) else [])
        here, dim = self.snap.feet, self.snap.dimension
        skip = self.not_there(sources)
        spots = {tuple(r["pos"]) for k in kinds for r in self.mem.seen(k, dim) if tuple(r["pos"]) not in skip}
        got = self.workable([(math.dist(p, here), p) for p in spots], kind or stand_kind(kinds), fixable=sources)
        return (got[1], got[0]) if got else None

    def seen(self, kinds, skip, radius=math.inf, kind=None, fixable=False):
        """(distance, cell) of the nearest of `kinds` in the round's look, none in `skip`, within `radius`, that a way
        to `kind` (default stand_kind: worked as a source) is not known to fail to (workable); or None."""
        got = [(h["distance"], (h["x"], h["y"], h["z"])) for k in kinds for h in self.snap.hits.get(bare(k), ())
               if h["distance"] <= radius and (h["x"], h["y"], h["z"]) not in skip]
        return self.workable(got, kind or stand_kind(kinds), fixable)

    def workable(self, cands, kind, fixable=False):
        """The nearest of `cands` ((distance, cell)) no known way refusal bars (refused): asked lazily, nearest first,
        stopping at the first that is not refused; or None. `fixable`: a source a step takes from (a gather, a mine, a
        take), whose refusal for want of way blocks the plan answers by getting them first (unfixable, planner.tasks)
        — a station used where it stands is held to refused."""
        best = None             # (ticks, (distance, cell)): a fixable one costs its walk and its blocks got (G3)
        for d, c in sorted(cands):
            if best is not None and walk_ticks(d) >= best[0]:
                break
            if kind is None or self.refused(c, kind) is None:
                return (d, c) if best is None or walk_ticks(d) < best[0] else best[1]
            short = self.way_blocks_short(c, kind) if fixable else 0
            if short:
                ticks = walk_ticks(d) + round(work_s(["dirt"] * short, [], held_tiers(self.snap.inv), TICKS_PER_S)
                                              * TICKS_PER_S)
                if best is None or ticks < best[0]:
                    best = (ticks, (d, c))
        return None if best is None else best[1]

    def way_blocks_short(self, cell, kind, at=None, spent=0):
        """Way blocks the bag its plan leaves (step_bag) lacks for the door's way to `kind` at `cell`: refused only
        for a tread (nav's "no tread"), reached with a leg's worth carried — the stock that lays its treads
        (nav.stock_for) less what is held; 0 for a way, an unknown or a refusal blocks do not fix. The plan gets
        them first (planner.tasks: a "building" need, G3 picking the cheapest kind)."""
        from . import nav
        reached = self.reach(cell, kind, at, spent)
        if reached is None or reached.stand is not None or not str(reached.why).startswith("no tread"):
            return 0
        rich = self.reach(cell, kind, at, spent, extra=nav.BLOCK_RESERVE + nav.LEG)
        if rich is None or rich.stand is None:
            return 0
        return max(1, nav.stock_for(rich.spent) - self.step_bag(spent).count("building"))

    def step_blocks_short(self, step):
        """way_blocks_short at the step's site (a sourced step's one target), 0 with none."""
        kind, site = self.way_kind(step), self.site(step)
        return self.way_blocks_short(site, kind) if kind is not None and site is not None else 0

    def unfixable(self, cell, kind, at=None, spent=0):
        """refused's why, unless carried blocks fix it (way_blocks_short: the plan gets them first); else None."""
        why = self.refused(cell, kind, at, spent)
        return why if why is not None and not self.way_blocks_short(cell, kind, at, spent) else None

    def step_bag(self, spent=0):
        """The bag a step is priced with: the snapshot's, less the way blocks the plan's steps before it spent (D6:
        spent along the plan, nav.less_way_blocks)."""
        from . import nav
        return nav.less_way_blocks(self.snap.inv, spent)

    def reach(self, cell, kind, at=None, spent=0, extra=0):
        """nav.reach — the door's own predicate — for `kind` at `cell` from the step's place (`at`, step_state) with
        the bag its plan leaves (step_bag), over the round's read ground (a banned unread cell: no ground,
        _Ground.void); None off the read. Snapshot only (K10), once a round per (cell, kind, place, way blocks held,
        bans) (D8: the kept answer is the fresh one; P4)."""
        if self.region is None or not self.region.inside(cell):
            return None
        from . import nav
        gone = self.not_there(False)
        feet = tuple(int(c) for c in self.step_state(at)[0])
        bag = self.step_bag(spent)
        if extra:
            # `extra` dirt more (way_blocks_short's probe: would carried blocks make the way)
            bag = type(bag)({"slots": list(bag.slots) + [{"id": "minecraft:dirt", "count": extra, "slot": len(bag.slots)}],
                             "equipment": dict(bag.equipment)})
        # way_bag only answers None for a bag it was never given; step_bag's is always read, never that bag
        ways = nav.way_bag(bag) or bag
        key = ("reach", tuple(cell), kind, feet, ways.count("building"), nav.building_of(ways), gone)
        if key not in self.cache:
            ground = _Ground(feet, cell, self.region.name(cell), self.soil(), False, self.region, gone.cells)
            # a banned source (a place a step is priced at) is not there, never in the way; any other banned cell is a
            # way's failed cause: no way goes through it again (E5)
            causes = gone.cells - self.places()
            self.cache[key] = nav.reach(ground, feet, tuple(cell), kind, bag, set(self.protected() or ()) | causes)
        return self.cache[key]

    def refused(self, cell, kind, at=None, spent=0):
        """The why no way to `kind` (nav.stands_for) at `cell` exists (reach) whose cell was read or banned, or None
        (a way, or unknown past the read): what a plan refuses a source by, as the door fails by it."""
        reached = self.reach(cell, kind, at, spent)
        if reached is None or reached.stand is not None:
            return None
        named = getattr(reached.why, "cell", None)
        if named is None or not (self.region.inside(named) or named in self.not_there(False).cells):
            return None
        return reached.why

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
            seen = self.seen(blocks, self.not_there(sources), radius, fixable=sources)
            self.cache[key] = seen[0] if seen is not None else self._known(blocks, sources)
        return self.cache[key]

    def _entity(self, types):
        """Blocks to the nearest of `types` in the round's look the door has a way to (refused "attack": a way to a
        stand in reach of it as it stands now, the gate's own), not banned; else the nearest remembered."""
        key = ("ent", tuple(types))
        if key not in self.cache:
            near = [d for d, _c in self._mobs(types)]
            self.cache[key] = min(near) if near else self._known(types)
        return self.cache[key]

    def _mobs(self, types):
        """[(distance, cell or None)] of `types` in the round's look, not banned, none the door refuses to attack."""
        ids = {mid(t) for t in types}
        out = []
        for e in self.snap.mobs or ():
            if mid(e["type"]) not in ids or (e.get("id") is not None and banned(self.blacklist, (e["id"], 0, 0))):
                continue
            cell = tuple(math.floor(e[k]) for k in ("x", "y", "z")) if "x" in e else None
            if cell is None or self.refused(cell, "attack") is None:
                out.append((e["distance"], cell))
        return out

    def _surface_trip(self, at=None):
        """Under rock, getting out is part of any surface trip, and it scales with depth — from the step's own place
        (`at`: below SURFACE_Y is under rock), the snapshot's sky for a step that runs first."""
        feet, _tools = self.step_state(at)
        covered = under_rock(self.snap.get("skyLight", 15)) if at is None else feet[1] < SURFACE_Y
        if not covered:
            return 0
        return PRIOR_TICKS["surface"] + PRIOR_TICKS["surface_per_block"] * max(0, SURFACE_Y - int(feet[1]))

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
        """(ok, why): an optional fight a step makes is planned only above the fight line (fight line) — the brain's one judge
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
            if tuple(pos) in self.not_there(False) or self.refused(pos, "use") is not None:
                continue                    # a banned container, or one no way reaches: taken from nothing
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
        sight — used where it stands, never made again; one a way to use is refused at (refused: the door's "use") is
        not near."""
        feet, dim = self.snap.feet, self.snap.dimension
        if any(self.refused(c, "use") is None for c in self.mem.known_stations(block, dim, near=feet, within=STATION_R)):
            return True
        if any(MACHINE_PROVIDES.get(tag) == mid(block) for m in self.mem.machines(dim)
               if math.dist(m["origin"], feet) <= STATION_R for tag in m.get("tags", ())):
            return True
        known = self._nearest([block], kind="use")
        return (known is not None and known[1] <= STATION_R) or self.seen([block], self.not_there(), STATION_R, "use") is not None

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

    def estimate(self, step, held=None, at=None, table_back=True, spent=0):
        """Ticks this step takes: its work (`work`) plus the walk to where it happens — from `at` (the plan's place
        before it) when that and the step's site are known, else from here, with the way blocks the plan spent before
        it gone (`spent`, D6); a withdrawal by the chance its container still holds the thing (a miss costs the
        walk)."""
        refuted = self.refuted_ticks(step, self.site(step), at)
        if refuted is not None:
            # a price the run refuted (Overrun): its measured rest while the state holds, never under its own lower
            # bound (P3: walk_lb + dig_lb ≤ the price by construction)
            tools = self.step_state(None, held)[1]
            refuted = max(refuted, self.walk_lb(step, tools) + self.dig_lb(step, tools))
            step.parts = {"refuted": refuted}
            return refuted
        work = self.work(step, held, table_back)
        parts = {"work": work, **self._walk_parts(step, at, held, spent=spent)}
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
            return round(self.seek_s(kinds, held) * TICKS_PER_S)
        if step.kind == "wait":
            return round(dawn_s(self.snap.state) * TICKS_PER_S)
        held = self.step_state(None, held)[1]
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

    def work_of(self, step, reach=True, at=None):
        """(breaks, kills) a step is expected to make: its own work (knowledge.own_work) and (`reach`) the digging
        to the nearest in sight (dug_way) from the step's own place (`at`, step_state)."""
        breaks, kills = own_work(step)
        if reach and step.kind == "mine":
            breaks = breaks + self._dug(step, at)
        return breaks, kills

    def _dug(self, step, at=None):
        """The blocks the way to the step's one target breaks — the cell site() picks, remembered or in sight — from
        its place (`at`): dug_way over the read ground, unread cells as _Ground expects them; [] with no target."""
        blocks = step.detail.get("blocks") or ()
        target = self.site(step)
        if target is None:
            return []
        return self._way_breaks(self.step_state(at)[0], target, blocks[0] if blocks else "stone",
                                FIND_AT.get(step.token) is not None)

    def _way_breaks(self, feet, target, block, ore):
        """The block names dug_way breaks from `feet` to `target` (`ore`: below the soil it is stone), once a round."""
        feet = tuple(int(c) for c in feet)
        key = ("dug", feet, tuple(target), block, ore)
        if key not in self.cache:
            self.cache[key] = dug_way(feet, target, block, self.soil(), ore, self.snap.inv, self.protected(),
                                      self.region) or []
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
            seen = self.seen(kinds, self.not_there(True), fixable=sources) if k != "hunt" else None
            if hit is not None and seen is not None:
                first = seen[1] if seen[0] < hit[1] else hit[0]
            elif hit is not None:
                first = hit[0]
            elif seen is not None:
                first = seen[1]
            else:
                first = None
            self.cache[key] = first
        first = self.cache[key]
        refuted = self.refuted_ticks(step, first) if sources and first is not None else None
        return first if refuted is None else self._cheaper_source(step, kinds, first, refuted)

    def refuted_ticks(self, step, target, at=None):
        """Ticks a run measured left of `step` at `target` past its price (Memory.refuted: api.Overrun's rest, the one
        writer dispatch.execute's), while the state it was measured in holds (ban_state from the step's place, the
        bag's kinds) — None when there is none or it lifted."""
        read = getattr(self.mem, "refuted_s", None)
        if read is None:
            return None                     # a memory with no refutations (a test's stand-in)
        state = ban_state(self.step_state(at)[0], frozenset(s["id"] for s in self.snap.inv.slots if s.get("count")))
        for t in (target, None):
            got = read(step, t, state)
            if got is not None:
                return round(got * TICKS_PER_S)
        return None

    def _cheaper_source(self, step, kinds, first, refuted):
        """G3 over a refuted source: `first` at its refuted ticks against the others remembered or in sight, each by
        its walk and (a mine) its dig (dug_way), nearest first until a walk alone cannot beat the best; the cheapest."""
        key = ("cheaper", step.kind, step.token, tuple(first), refuted)
        if key not in self.cache:
            feet, skip = tuple(self.snap.feet), self.not_there(True)
            dim = self.snap.dimension
            cells = {tuple(r["pos"]) for k in kinds for r in self.mem.seen(k, dim)}
            cells |= {(h["x"], h["y"], h["z"]) for k in kinds for h in self.snap.hits.get(bare(k), ())}
            best = (refuted, tuple(first))
            blocks = step.detail.get("blocks") or ()
            for c in sorted((c for c in cells if c not in skip and c != tuple(first)), key=lambda c: math.dist(c, feet)):
                walk = walk_ticks(math.dist(c, feet))
                if walk >= best[0]:
                    break
                dig = 0
                if step.kind == "mine":
                    dug = self._way_breaks(feet, c, blocks[0] if blocks else "stone", FIND_AT.get(step.token) is not None)
                    dig = round(work_s(dug, [], held_tiers(self.snap.inv), TICKS_PER_S) * TICKS_PER_S) if dug else 0
                if walk + dig < best[0]:
                    best = (walk + dig, c)
            self.cache[key] = best[1]
        return self.cache[key]

    GOT = ("mine", "gather", "hunt", "take")      # step kinds that get a thing where it is (no input to make it from)

    def raw_needs(self, token, n, seen=()):
        """Pure over the recipes (knowledge.sources, planner.way): {token a gather, mine, hunt or take gets: how many}
        making `n` of `token` reads, along its first way (the tables' own order) — what a later milestone's needs come
        down to (enroute)."""
        if token in seen:
            return {}
        for made, src in sources(token):
            if src[0] in self.GOT:
                return {made: n}
            if src[0] in ("craft", "smelt"):
                out = {}
                for t, k in way(src, made, n)[1]:
                    for raw, m in self.raw_needs(t, k, seen + (token,)).items():
                        out[raw] = out.get(raw, 0) + m
                return out
        return {}

    def got_step(self, item, kind, n):
        """The step that gets `n` of `item` from a `kind` (a block, a mob, "tree") where it stands, or None."""
        name = bare(kind)
        for made, src in sources(item):
            if src[0] not in self.GOT:
                continue
            where = [bare(b) for b in (src[1] if len(src) > 1 and isinstance(src[1], list) else ())]
            if (src[0] == "gather" and name == "tree") or name in where:
                return way(src, made, max(1, round(n)))[0]
        return None

    def enroute(self, here, there, wanted, hidden, free_s=0.0):
        """[(bound s, step, where, item, n, P)] for what lies beside the leg `here` → `there`, best first by a cheap
        bound (knowledge.side_saving over the straight detour; the planner prices the real one, brain.enroute_plan):
        each thing seen or remembered (bag.note_yields) yielding an item `wanted` ({item: (P used later, how many
        still lacked)}); a container's remembered contents by the chance they are still there (container_p); an
        unopened chest looked into, the best wanted item by the chance a chest holds it (planner.p_unknown over the
        chests seen). Its later price bag.item_value at `hidden(step, where)`, the prices with that source hidden;
        its work and detour cost nothing within `free_s` (seconds waited anyway); only s > 0."""
        from .bag import item_value, note_yields
        from .knowledge import side_saving
        from .planner import p_unknown
        if not wanted or here is None or there is None:
            return []
        here, there, dim = tuple(here), tuple(there), self.snap.dimension
        leg = walk_ticks(math.dist(here, there)) / TICKS_PER_S

        def detour(c):
            return (walk_ticks(math.dist(here, c)) + walk_ticks(math.dist(c, there))) / TICKS_PER_S - leg

        def bound(p, later, step, c):
            return side_saving(p, later, 0.0, max(0.0, self.work(step) / TICKS_PER_S + detour(c) - free_s))

        def want(item):
            return next((pn for w, pn in wanted.items() if w == item or mid(item) in members(w)), (0.0, 0))

        def offered(step, c):
            # what the plan's own sources are held to: not banned, a way the door can make
            if tuple(c) in self.not_there(False):
                return False
            kind = "attack" if step.kind == "hunt" else self.way_kind(step)
            return kind is None or self.unfixable(c, kind) is None
        found = {}
        for r in self.mem.data.get("seen", []):
            if r.get("dimension") == dim:
                found.setdefault(tuple(r["pos"]), r["kind"])
        for kind, hits in (self.snap.hits or {}).items():
            for h in hits:
                found.setdefault((h["x"], h["y"], h["z"]), kind)
        for e in self.snap.mobs or ():
            if e.get("id") is not None and banned(self.blacklist, (e["id"], 0, 0)):
                continue
            found.setdefault((math.floor(e["x"]), math.floor(e["y"]), math.floor(e["z"])), bare(e["type"]))
        out = []
        for c, kind in found.items():
            for item, n in note_yields(kind).items():
                p, need = want(item)
                step = self.got_step(item, kind, min(n, need)) if p > 0 else None
                step = step if step is not None and offered(step, c) else None
                later = item_value(item, min(n, need), hidden(step, c)) if step is not None else None
                if later is not None:
                    out.append((bound(p, later, step, c), step, c, item, min(n, need), p))
        rate = self.mem.container_change_rate()
        records = [r for r in self.mem.data.get("containers", {}).values() if r.get("dimension") == dim]
        for rec in records:
            c = tuple(rec["pos"])
            for item, have in rec["items"].items():
                p, need = want(item)
                have = min(have, need)            # what a milestone still lacks, never the whole chest
                if p <= 0 or have <= 0:
                    continue
                chance = container_p(rec, {item}, time.time() - rec.get("at", time.time()), rate)
                step = Step("withdraw", item, have, {"pos": list(c), "p": chance})
                if not offered(step, c):
                    continue
                later = item_value(item, have, hidden(step, c))
                if later is not None:
                    out.append((bound(p * chance, later, step, c), step, c, item, have, p * chance))
        for h in (self.snap.hits or {}).get("chest", ()):
            c = (h["x"], h["y"], h["z"])
            if self.mem.container_record(c) is not None:
                continue
            look = Step("look", "container", 1, {"pos": list(c)})
            if not offered(look, c):
                continue
            for item, (p, n) in wanted.items():
                held = sum(1 for r in records if any(mid(i) in members(item) or i == item for i in r["items"]))
                later = item_value(item, n, hidden(look, c))
                if later is not None:
                    chance = p * p_unknown(held, len(records))
                    out.append((bound(chance, later, look, c), look, c, item, n, chance))
        return sorted((r for r in out if r[0] > 0), key=lambda r: -r[0])

    def _kinds_of(self, step):
        """A site's kinds: a search has none, its place is what it finds."""
        return [] if step.kind == "seek" else step_kinds(step)

    def walk_lb(self, step, held):
        """Ticks no walk to this step's site can beat (the digging to it aside: `dig_to`): from the nearest place a
        plan may stand before it (the feet, a remembered spot, a container, the round's look); the walk from here
        when the site is unknown, its search with the tools `held` ({tool kind: tier}) then."""
        site = self.site(step)
        if site is None:
            # the least over every place it may start from (step_state's `at`): the run prices it from one of them
            key = ("walk_lb_unknown", step.kind, tuple(step_kinds(step)), tuple(sorted((held or {}).items())))
            if key not in self.cache:
                self.cache[key] = min(self._walk(step, at=p, held=held, dig=False)
                                      for p in [None, *sorted(self.places())])
            return self.cache[key]
        key = ("walk_lb", tuple(site), step.kind, tuple(self._kinds_of(step) or ()))
        if key not in self.cache:
            # from every place a plan may stand before it: remembered, a container, the look's (site's own answers)
            near = min((math.dist(p, site) for p in self.places() if tuple(p) != tuple(site)), default=math.inf)
            # and as priced from here, first in the plan: the nearest source, which need not be the site's
            parts = self._walk_parts(step, None, held, dig=False) if (step.kind in self.SOURCED or step.kind in ("goto", "withdraw", "look")) else {"seek": 1}
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

    def way_kind(self, step):
        """The door's act at a step's site (stand_kind for a source; a container opened, a place stood on), None for
        a step that walks to no site."""
        if step.kind in ("withdraw", "look"):
            return "use"
        if step.kind == "goto":
            return "stand"
        return stand_kind(step_kinds(step)) if step.kind in ("gather", "mine", "take") else None

    def way_spent(self, step, at=None, spent=0):
        """Way blocks the door's way to the step's site spends (reach from `at` with `spent` gone): what the next
        step's bag is short of (D6); 0 with no site, off the read or refused."""
        kind, site = self.way_kind(step), self.site(step)
        reached = self.reach(site, kind, at, spent) if kind is not None and site is not None else None
        return reached.spent if reached is not None and reached.stand is not None else 0

    def _walk_parts(self, step, at=None, held=None, dig=True, spent=0):
        """{"walk", "dig", "surface", "seek"} ticks of getting to where the step happens (seek: the walk to a thing
        nowhere known, priced by the prior; also a site the door refuses from `at` with `spent` way blocks gone, D6)."""
        k = step.kind
        out = {"walk": 0, "dig": 0, "surface": 0, "seek": 0}
        site = self.site(step) if at is not None else None
        kind = self.way_kind(step)
        mobs = self._mobs(step.detail.get("types", ())) if k == "hunt" else ()
        mob = min(mobs, key=lambda m: m[0], default=(None, None))[1]
        # from the feet too: a named site (a chest, a place) is never swapped for a reachable other
        target = self.site(step) if kind is not None else None
        if target is not None and self.unfixable(target, kind, at, spent) is not None:
            out["seek"] = self.find_ticks(step_kinds(step), held, at)
        elif mob is not None and (ways := self.reach_ticks(mob, "attack", at, spent)) is not None:
            out["walk"] = ways          # where it stands now (K7), not where it was noted
            out["surface"] = self._surface_trip(at) if at is None else 0
        elif at is not None and site is not None:
            ways = self.reach_ticks(site, kind, at, spent) if kind is not None else None
            out["walk"] = ways if ways is not None else walk_ticks(math.dist(at, site))
            if k in ("goto", "withdraw", "look"):
                through = self.door_s(site, at)
                out["walk"] = round(through * TICKS_PER_S) if through is not None else out["walk"]
            # the way's own digs are in its seconds: dig_to only where no way was read
            out["dig"] = self.dig_to(step, held, at) if k == "mine" and dig and ways is None else 0
        elif k in self.SOURCED:
            # one target: the walk to the site the dig prices (a refuted nearest's cheaper other, _cheaper_source)
            mine_site = self.site(step) if k in ("gather", "mine", "take") else None
            ways = self.reach_ticks(mine_site, kind, None, spent) if mine_site is not None and kind is not None else None
            d = math.dist(self.snap.feet, mine_site) if mine_site is not None else self._source(step)
            out["walk" if d is not None else "seek"] = ways if ways is not None else walk_ticks(d) if d is not None \
                else self.find_ticks(step_kinds(step), held, at)
            if k != "mine":
                out["surface"] = self._surface_trip(at)
            elif dig and ways is None:
                out["dig"] = self.dig_to(step, held, at)
        elif k == "fill":
            hit = self._nearest(["water"])
            ways = self.reach_ticks(hit[0], stand_kind(["water"]), at, spent) if hit is not None else None
            out["walk" if hit is not None else "seek"] = ways if ways is not None else walk_ticks(hit[1]) \
                if hit is not None else self.find_ticks(["water"], held, at)
        elif k in ("goto", "withdraw", "look"):
            pos = tuple(step.detail["pos"])
            through = self.door_s(pos)
            ways = self.reach_ticks(pos, kind, None, spent) if through is None else None
            out["walk"] = round(through * TICKS_PER_S) if through is not None else ways if ways is not None else \
                walk_ticks(math.dist(self.snap.feet, pos))
        return out

    def reach_ticks(self, cell, kind, at=None, spent=0):
        """Ticks of the door's way to `kind` at `cell` (reach: its walk, digs and treads), with the way blocks the plan
        gets first when the bag is short; None off the read or refused."""
        got = self.reach(cell, kind, at, spent)
        if got is not None and got.stand is None:
            short = self.way_blocks_short(cell, kind, at, spent)
            got = self.reach(cell, kind, at, spent, extra=short) if short else None
        return round(got.seconds * TICKS_PER_S) if got is not None and got.stand is not None else None

    def dig_lb(self, step, held=None):
        """Ticks no dig to this step's ore in sight can beat: the least of dig_to over every place it may start from
        (step_state's `at`), as walk_lb is the least walk."""
        key = ("dig_lb", step.kind, tuple(step_kinds(step)), tuple(sorted((held or {}).items())))
        if key not in self.cache:
            # branch and bound over the step's own places (places_for), nearest its site first: no dig is under 0, so
            # the first place that needs none (the site's own cell) ends it — never one dug_way per note in memory
            site = self.site(step)
            order = sorted(self.places_for(step), key=lambda p: math.dist(p, site) if site is not None else 0.0)
            best = math.inf
            for p in [*order, None]:
                best = min(best, self.dig_to(step, held, p))
                if best <= 0:
                    break
            self.cache[key] = best
        return self.cache[key]

    def places_for(self, step):
        """The places a step's own price reads: the remembered and seen cells of its source's kinds (step_kinds) and
        the stations it works at — a subset of places() holding the step's site, so a least over it is the least over
        all where the site's own cell prices 0 (dig_lb)."""
        kinds = list(self._kinds_of(step))
        station = self.made_at(step) if step.kind == "craft" else None
        names = {bare(k) for k in kinds} | ({bare(station)} if station else set())
        dim = self.snap.dimension
        out = {tuple(r["pos"]) for r in self.mem.data.get("seen", [])
               if r.get("dimension") == dim and bare(r.get("kind", "")) in names}
        out |= {tuple(s["pos"]) for s in self.mem.stations(dim) if bare(s.get("block") or "") in names}
        out |= {(h["x"], h["y"], h["z"]) for k in names for h in (self.snap.hits or {}).get(k, ())}
        site = self.site(step)
        return frozenset(out | ({tuple(site)} if site is not None else set()))

    def dig_to(self, step, held=None, at=None):
        """Ticks the digging to the nearest one in sight takes (work_of's breaks beyond the step's own), each break
        with the best of `held` ({tool kind: tier}; the bag's when None)."""
        held = self.step_state(None, held)[1]
        return dig_to_ticks(self.work_of(step, at=at)[0], step, held, TICKS_PER_S)

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
        """The position of the nearest known one — remembered, else in the round's look, as site() picks — or None:
        what "on the way" is judged by."""
        hit = self._nearest(kinds)
        if hit is not None:
            return hit[0]
        seen = self.seen(kinds, self.not_there(True))
        return seen[1] if seen is not None else None

    def seek_s(self, kinds, held=None, at=None):
        """Seconds to reach one of these: the game's route, else to the known one (where) the way dug_way digs — its
        walk and its breaks with the tools `held` (no walk-only price for a target with no known walk) — else the
        prior."""

        seconds = self.route_s(kinds)
        if seconds is not None:
            return max(1.0, round(float(seconds), 1))
        target = self.where(kinds)
        if target is None:
            return self.find_ticks(kinds, held, at) / TICKS_PER_S
        feet = self.step_state(at)[0]
        ore = any(find_class(k)[0] == "ore" for k in kinds)
        dug = self._way_breaks(feet, target, kinds[0], ore)
        dig = work_s(dug, [], self.step_state(None, held)[1], TICKS_PER_S) if dug else 0.0
        return max(1.0, round(walk_ticks(math.dist(feet, target)) / TICKS_PER_S + dig + 2.0, 1))

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

    def step_state(self, at=None, held=None):
        """(feet, {tool kind: tier}) a step is priced from: the plan's place before it (`at`) and the tools it holds
        then (`held`) — the snapshot's feet and bag only for a step that runs first (K9: one price, the step's own)."""
        return (tuple(self.snap.feet) if at is None else tuple(at)), (held_tiers(self.snap.inv) if held is None else held)

    def find_ticks(self, kinds, held=None, at=None):
        """Ticks to find one of `kinds` never seen (knowledge.expected_find_s): the soonest of them, from the step's
        own place and tools (step_state)."""
        feet, tools = self.step_state(at, held)
        facts = {"y": feet[1], "held": tools, "feet": feet, "biomes": self.snap.biomes}
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
