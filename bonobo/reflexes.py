"""The maintenance reflexes: a fixed trigger in, a fixed action out — no planning, a second's work. One ordered table, the arbiter's MAINTAIN layer: faster than any plan, slower than a fight. eat on a hungry stomach with food carried · out of the water · home from the Nether when it turns bad · dug out in the morning · into a bed at night · the night's shelter from what is carried · a finished furnace or machine emptied · a full bag emptied · a way made where a walk was blocked · unstuck What must be PLANNED to be had — a bed, food stock, a tool, a bucket, blocks, the night's ore — is not here: those are PLAN proposals (upkeep's needs, `decompose`). A trigger reads only its view (`view`: readings made once per round, lazily); the action is the upkeep executor's."""

import math
import time
from typing import Any

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, building, craft, fluids, nav, nether, skillcore, store, survive, tape, world, jobs
from .api import McError, NotAvailable, log, swallowed
from .data import BASE_MARKERS, FULL_BAR, MAX_HP, WALK_BLOCKS_PER_S
from .game import OPEN_SKY
from .estimate import eat_due
from .knowledge import RAW_MEAT, food_count
from .skill import skill
from .skillcore import gained
from .world import BAG_SLOTS, Inventory, Region, nearest
from .bag import FREE_SLOTS_TARGET, bag_signature, empty_how
from .decompose import cooled_ways, night_facts, night_left_s, way_key


def in_sight(snap, kinds, radius):
    """Is one of `kinds` within `radius` of the feet — read from the round's one batched look (world.nearest over
    knowledge.SOURCE_BLOCKS, the scan the cost model shares), never a /find of its own while deciding."""
    return nearest(kinds, snap.feet, snap.dimension, radius, union=_k.SOURCE_BLOCKS) is not None


BAG_FULL = BAG_SLOTS - 2   # slots used before the bag is emptied
BRIDGE_MIN = 8             # building blocks it takes to bridge a blocked path
EAT_BELOW = 14             # hunger points: eat below this, while there is something to eat (standing)
STARVE = 6                 # hunger points: at or below this, raw meat is eaten rather than waited on
JOB_RANGE = 96
UNSTUCK_MOVED = 2           # blocks the feet must move for a way out to count
STUCK_LIMIT = 60           # seconds in the same block with the same bag → unstuck
BLOCKED_FOR_S = 120        # a path failure this recent, here, is "the path is blocked"

FUELS = ("coal", "charcoal", "planks", "log")
STATION_R = 8          # a furnace of ours this near counts as one to cook in

def can_cook(inv, furnace_near):
    """Pure: raw meat can be cooked from here — fuel, and a furnace carried, near, or eight cobblestone for one."""

    fuel = any(inv.count(f) for f in FUELS)
    furnace = inv.count("minecraft:furnace") or furnace_near or inv.count("minecraft:cobblestone") >= 8
    return bool(fuel and furnace)

def meal(food, inv, cookable):
    """Pure: what the eat row eats — None (nothing now), False (a meal: cooked food only), True (raw meat too)."""

    if food_count(inv):
        return False
    if not any(inv.count(f) for f in RAW_MEAT):
        return None
    return True if food <= STARVE or not cookable() else None

def pit_due(v):
    """The body stands in a hole open to the sky it cannot jump out of (a view without the reading: no)."""
    known = "in_pit" in v or "in_pit" in getattr(v, "providers", ())
    return bool(v["in_pit"]) if known else False

def open_night(v):
    """Pure: night (data.is_night) with no cover here (knowledge.sheltered) — a row that walks or works from here
    waits (S4)."""
    return v["night"] and not v["sheltered"]


TABLE = [
    ("eat", lambda v: eat_due(v["food"], v.get("hp", MAX_HP), EAT_BELOW, MAX_HP, FULL_BAR) and v["meal"] is not None,
     lambda m, v: survive.eat(raw_ok=v["meal"])),
    ("reach land", lambda v: v["swimming"], lambda m, v: survive.reach_land(v["ctx"])),
    ("leave the Nether", lambda v: v["nether_bad"], lambda m, v: nether.use_portal(v["ctx"], "minecraft:overworld")),
    ("dig out", lambda v: not v["night"] and v["enclosed"], lambda m, v: survive.dig_out(v["ctx"])),
    ("sleep", lambda v: v["overworld"] and v["night"] and v["bed_works"] and (v["bed_carried"] or v["bed_near"]),
     lambda m, v: survive.sleep(v["ctx"], m.brain.policy(v["snap"], True))),
    ("shelter", lambda v: v["shelter_ready"], lambda m, v: m.shelter(v["snap"], v["ctx"], v["night_way"])),
    # after the shelter, and not under the open night sky: a walk back to the drops, a step out of a pit
    ("recover items", lambda v: not open_night(v) and v["died_recently"], lambda m, v: recover_items(v["ctx"])),
    ("leave the pit", lambda v: not open_night(v) and pit_due(v), lambda m, v: m.leave_pit(v["snap"], v["ctx"])),
    ("collect job", lambda v: v["job_ready"], lambda m, v: m.collect_job(v["snap"], v["ctx"])),
    ("collect machine", lambda v: v["machine_ready"],
     lambda m, v: craft.collect_machine(v["ctx"], m.ready_machine(v["snap"]))),
    ("empty the bag", lambda v: v["used_slots"] >= BAG_FULL, lambda m, v: m.empty_bag(v["snap"], v["ctx"])),
    ("path blocked", lambda v: v["blocked"] and v["building"] >= BRIDGE_MIN,
     lambda m, v: m.bridge(v["ctx"], v["blocked_at"])),
    ("unstuck", lambda v: v["stuck"], lambda m, v: m.unstuck(v["snap"], v["ctx"])),
]
NAMES = tuple(row[0] for row in TABLE)

# hysteresis: a row with an exit keeps firing until the exit holds; out of the water only after LAND_EXIT_S on something not water (shore water flips `swimming`)
LAND_EXIT_S = 1.0
EXIT = {"reach land": lambda v: v["on_land_s"] >= LAND_EXIT_S}

# what a reflex's work should move: firing again with it unchanged is a failure, so a useless reflex cannot hold the body
PROGRESS = {"empty the bag": lambda v: v["used_slots"], "unstuck": lambda v: v["feet"],
            "reach land": lambda v: v["feet"], "eat": lambda v: v["food"],
            "shelter": lambda v: (v["feet"], len(v["night_way"][2]))}     # a part made shortens the way
NO_PROGRESS = "stuck"          # the retry cause a reflex that changed nothing fails with

def progress_of(name, view):
    """Pure: the reading a run of `name` should move."""
    return PROGRESS.get(name, lambda v: (v["feet"], v["used_slots"], v["food"]))(view)

def stalled(fires_again, before, after):
    """Pure: a reflex that ran and fires again with its progress unchanged made no progress."""
    return bool(fires_again) and before == after

def latched(fired, view):
    """Pure: the rows still inside their hysteresis after this round — fired, with an exit not yet reached."""
    return frozenset(n for n in fired if n in EXIT and not EXIT[n](view))

class View(dict):
    """The round's readings, each made on first ask (`providers`: {key: zero-argument reader}), then kept."""

    def __init__(self, providers, **known):
        super().__init__(**known)
        self.providers = providers

    def __missing__(self, key):
        value = self.providers[key]()
        self[key] = value
        return value

def due(view, ready=lambda name: True, active=frozenset()):
    """[(seq, name)] of reflexes that fire in table order, skipping cooling ones: the trigger holds, or it is active with its exit unmet."""

    return [(i, name) for i, (name, trigger, _act) in enumerate(TABLE)
            if ready(name) and (trigger(view) or (name in active and name in EXIT and not EXIT[name](view)))]

def nether_retreat(snap):
    """In the Nether, head home through the portal when food, health or bag room run low. Pure."""
    if snap.dimension != "minecraft:the_nether":
        return None
    inv, s = snap.inv, snap.state
    if food_count(inv) < 4:
        return "food running out"
    if s.get("health", 20) <= 8:
        return "health low"
    if inv.free_slots() <= 1:
        return "bag full"
    return None

def ground(reads=None):
    """The two ground readings needs and reflexes ask, each read once when first asked: enclosed, and hand-diggable ground."""

    both = _once(None, "night_ground", survive.night_ground)          # one region read answers the two below
    soft = _once(reads, "soft_ground", lambda: both()[0])
    # given readings without the dig-in site say nothing against it (a test's round reads no world)
    site = (lambda: reads.get("dig_in_site", True)) if reads is not None else (lambda: both()[1])
    return _once(reads, "enclosed", survive.enclosed), soft, site

def _once(reads, key, read):
    """A zero-argument reader: `reads[key]` when given, else `read()` on first use, kept for the round."""
    box = {}
    if reads and key in reads:
        box["v"] = reads[key]

    def get():
        if "v" not in box:
            box["v"] = read()
        return box["v"]
    return get

# a shelter step's token → the skill that makes it
STEP_RUN: Any = None     # (ctx, step, night) → one plan step run (dispatch.execute): set by every Brain built
SHELTER_RUN = {"dig_in": lambda ctx: survive.dig_in(ctx), "pod": lambda ctx: survive.pod(ctx),
               "hut": lambda ctx: building.build_shelter(ctx), "home": lambda ctx: survive.sleep_at_home(ctx)}

class Maintain:
    """The reflex table's executor, remembering where the body has been (stuck) and where the last path failed (blocked)."""

    def __init__(self, brain):
        self.brain = brain
        self.history = []             # (time, feet, bag signature) for "stuck in place"
        self.escalated = {}
        self.blocked = None           # {"t", "place", "pos"}: the last path failure and where it was going
        self.active = frozenset()     # rows inside their hysteresis (`latched`)
        self.land_since = None        # when the body last stood on something that is not water
        self.last_run: tuple[str, Any] | None = None     # (name, progress when it started): judged next round (`stalled`)

    def observe(self, snap):
        now = time.time()
        self.history = [h for h in self.history if now - h[0] <= STUCK_LIMIT + 30]
        self.history.append((now, snap.feet, bag_signature(snap.inv)))
        on_land = snap.state.get("onGround", False) and not snap.state.get("inWater")
        self.land_since = (self.land_since or now) if on_land else None

    def failed(self, cause, err, place):
        """A path failure is remembered with where it was going: the "path blocked" rows answer it."""
        if cause == "nav":
            self.blocked = {"t": time.time(), "place": place, "pos": getattr(err, "pos", None)}

    def proposals(self, snap, ctx, reads=None):
        """[(seq, name, run)] of every reflex that fires; triggers read this round's snapshot only, so asking order does not matter."""

        b, s, inv, over = self.brain, snap.state, snap.inv, snap.dimension == "minecraft:overworld"
        blocked = self.blocked_here(b.place)
        enclosed, soft_ground, dig_site = ground(reads)

        def night_way():
            bed = b.mem.home_part("beds", snap.dimension, snap.feet, anywhere=True)
            # bed_reach: a home bed with no "no way there" verdict on it (the reach verdict, brain.failed)
            reach = bed is not None and not skillcore.banned(b.blacklist, bed)
            home_s = math.dist(bed, snap.feet) / WALK_BLOCKS_PER_S if reach and bed is not None else None
            return b.needs.overnight(snap, night_facts(soft_ground(), cooled_ways(b.ready), dig_site(), home_s,
                                                       night_left_s(snap)),
                                     bed_too=False)
        view = View({
            "died_recently": lambda: worth_recovering(b, snap),
            "meal": lambda: meal(s.get("food", 20), inv, lambda: can_cook(inv, any(
                "furnace" in st["block"] for st in b.mem.stations(snap.dimension, near=snap.feet, within=STATION_R)))),
            "swimming": lambda: fluids.swimming(s),
            "nether_bad": lambda: nether_retreat(snap) is not None,
            "enclosed": enclosed,
            "bed_works": lambda: survive.can_sleep(s) is None,
            "bed_near": _once(reads, "bed_near", lambda: home_bed_near(b.mem, snap)
                              or in_sight(snap, BASE_MARKERS["bed"], survive.HOME_BED_R)),
            "night_way": night_way,
            "shelter_ready": lambda: over and snap.night and not _once(reads, "bed_tonight",
                                                                        lambda: b.needs.bed_tonight(snap))()
            and not view["sheltered"] and view["night_way"][0] is not None,
            "sheltered": lambda: self.sheltered(snap, enclosed),
            "job_ready": lambda: self.ready_job(snap) is not None,
            "machine_ready": lambda: self.ready_machine(snap) is not None,
            "stuck": lambda: self.stuck_in_place(snap, enclosed),
            # a hole open to the sky, deeper than a jump (travel's shaft, a dug pit): read only under open sky
            "in_pit": lambda: s.get("skyLight", 0) >= OPEN_SKY and not fluids.swimming(s)
            and _once(reads, "in_pit", lambda: self.in_pit(snap.feet))(),
        }, snap=snap, ctx=ctx, food=s.get("food", 20), hp=s.get("health", MAX_HP), night=snap.night, overworld=over,
            bed_carried=inv.count("bed") > 0, used_slots=inv.used_slots(), blocked=blocked is not None,
            blocked_at=blocked, building=inv.count("building"), feet=snap.feet,
            on_land_s=time.time() - self.land_since if self.land_since else 0.0)
        fired = due(view, active=self.active)
        names = [name for _seq, name in fired]
        if self.last_run is not None:
            name, before = self.last_run
            self.last_run = None
            if stalled(name in names, before, progress_of(name, view)):
                b.retry.failed(name, NO_PROGRESS, "ran and changed nothing", time.time(), b.place)
        self.active = latched(names, view)
        rows = {name: act for name, _trigger, act in TABLE}

        def run(name):
            self.last_run = (name, progress_of(name, view))
            return rows[name](self, view)
        return [(seq, name, (lambda name=name: run(name))) for seq, name in fired if b.ready(name)]

    # -- path blocked
    def blocked_here(self, place):
        """The last path failure, when it is recent, happened here and says where it was going; else None."""
        bl = self.blocked
        if bl is None or bl["pos"] is None or bl["place"] != place or time.time() - bl["t"] > BLOCKED_FOR_S:
            return None
        return bl

    def bridge(self, ctx, blocked):
        """Make the way by hand (survive.bridge_toward) toward where the failed walk was going."""
        self.blocked = None
        log(f"   path to {blocked['pos']} blocked → bridging toward it")
        return survive.bridge_toward(ctx, blocked["pos"])

    # -- night
    def shelter(self, snap, ctx, night_way):
        """Night, exposed, no bed: the way `overnight` priced cheapest here, its parts first — one step a round, the
        way re-priced each round from the bag it left — then the shelter."""

        b = self.brain
        ctx = b.context(snap.dimension, b.policy(snap, True))
        way, _secs, steps = night_way
        log(f"   the night: {way} ({' → '.join(map(str, steps))})")
        try:
            if len(steps) > 1:
                return STEP_RUN(ctx, steps[0], True)
            return SHELTER_RUN[steps[-1].token](ctx)
        except McError as e:
            if api.interrupted(e):
                raise
            # the way failed, not the night: it cools under its own key and drops out of the pricing, so the
            # next way is chosen this same night (dig in: "no lid below the ground line" → wall in)
            b.failed(way_key(way), e)
            return None

    def sheltered(self, snap, enclosed=None):
        """knowledge.sheltered over this round: under rock, walled in, or inside a site's interior."""
        def walled():
            try:
                return (enclosed or survive.enclosed)()
            except (tape.ReplayMiss, McError) as e:
                swallowed("reflexes.walled", e)
                return False
        return _k.sheltered(snap.get("skyLight", 15), walled, lambda: self.in_site(snap.feet, snap.dimension))

    def in_site(self, feet, dimension):
        """The feet stand inside one of our sites' interiors (a hut with its door open is still ours)."""
        return any(list(feet) in s.get("interior", []) for s in self.brain.mem.sites(dimension))

    # -- jobs and the bag
    def ready_job(self, snap):
        near = [j for j in self.brain.mem.jobs(snap.dimension)
                if world.job_ready(j, snap.state.get("gameTime")) and math.dist(j["pos"], snap.feet) <= JOB_RANGE]
        return min(near, key=lambda j: math.dist(j["pos"], snap.feet), default=None)

    def ready_machine(self, snap):
        """The nearest machine (auto smelter) whose loaded order is due, within JOB_RANGE."""
        near = [m for m in self.brain.mem.machines(snap.dimension)
                if craft.pending_ready(m) and math.dist(m["origin"], snap.feet) <= JOB_RANGE]
        return min(near, key=lambda m: math.dist(m["origin"], snap.feet), default=None)

    def collect_job(self, snap, ctx):
        job = self.ready_job(snap)
        if job is not None:
            jobs.collect(ctx, job)

    def empty_bag(self, snap, ctx):
        """Deposit into an existing chest when a stack is worth the walk, else drop the cheapest — never craft a chest for it."""

        need = max(1, snap.inv.used_slots() - (BAG_SLOTS - FREE_SLOTS_TARGET))
        lava = in_sight(snap, ["lava"], 3)
        how = empty_how(snap.inv.slots, need, ctx.prices().get, self.chest_seconds(snap, ctx), lava)
        return store.deposit(ctx, local_only=snap.night) if how == "deposit" else store.tidy_inventory(ctx)

    def chest_seconds(self, snap, ctx):
        """Seconds to an existing chest (in reach, or a remembered site's; at night only in reach), or None."""

        if in_sight(snap, BASE_MARKERS["chest"], 6):
            return 2.0
        if snap.night:
            return None
        sites = [s for s in ctx.mem.sites(ctx.dimension) if store.site_trek_ok(ctx, s)
                 and math.dist(s["pos"], snap.feet) <= 96]
        if not sites:
            return None
        return min(math.dist(s["pos"], snap.feet) for s in sites) / WALK_BLOCKS_PER_S

    # -- stuck
    def in_pit(self, feet):
        """nav.in_pit over the cells round the feet (one small read)."""
        x, y, z = feet
        try:
            return nav.in_pit(Region((x - 1, y, z - 1), (x + 1, y + 2, z + 1)), feet)
        except McError as e:
            swallowed("reflexes.in_pit", e)
            return False

    def leave_pit(self, snap, ctx):
        """One level up out of a pit (nav.pit_exit_tasks): a pillar with a carried block, else a step dug in the side."""
        x, y, z = snap.feet
        region = Region((x - 1, y - 1, z - 1), (x + 1, y + 3, z + 1))
        tasks = nav.pit_exit_tasks(region, snap.feet, nav.building_item())
        if not tasks:
            raise NotAvailable(f"in a pit at {snap.feet}: no block to pillar and no side to dig a step in")
        log(f"   in a pit at {snap.feet}: one level up ({tasks[0]['type']})")
        api.run_chain(tasks, stop_on_failure=True, wait=30)

    def stuck_in_place(self, snap, enclosed=None):
        """Same block and the same bag for STUCK_LIMIT seconds (sheltered at night excluded)."""
        if snap.night and self.sheltered(snap, enclosed):
            return False
        old = [h for h in self.history if time.time() - h[0] >= STUCK_LIMIT]
        if not old:
            return False
        ref = old[-1]
        return all(math.dist(h[1], ref[1]) < 2 and h[2] == ref[2] for h in self.history if h[0] >= ref[0])

    def situation(self, snap):
        """Where we are stuck (stuck_situation), from one small read round the feet; "open" when unread."""
        x, y, z = snap.feet
        try:
            region = Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1))
        except McError as e:
            swallowed("reflexes.situation", e)
            return "open"
        return stuck_situation(survive.is_enclosed(region, snap.feet), nav.in_pit(region, snap.feet),
                               on_column(region, snap.feet), _k.under_rock(snap.state.get("skyLight", 15)))

    def unstuck(self, snap, ctx):
        """One way out per call — the nearest site, then the ways in the order the situation asks (unstuck_order) —
        each skipped once it failed here."""
        b = self.brain
        site = b.mem.nearest_site(snap.feet, snap.dimension)
        if site and math.dist(site["pos"], snap.feet) < 12:
            site = None
        order = unstuck_order(self.situation(snap))
        methods = ([("site", tuple(site["pos"]))] if site else []) + [
            (label, tuple(snap.feet[i] + UNSTUCK_WAYS[label][i] for i in range(3))) for label in order]
        for label, target in methods:
            name = f"unstuck:{label}"
            if not b.ready(name):
                continue
            log(f"no progress for {STUCK_LIMIT}s at {snap.feet} → unstuck by heading {label} {target}")
            self.history.clear()
            nav.go_to(target, b.policy(snap, snap.night), range_=3, attempts=1)
            if math.dist(nav.feet(), snap.feet) >= UNSTUCK_MOVED:
                # judged by the feet: "up" on open ground re-aims at the column's own ground and reports arrived
                # without a step taken (upkeep__unstuck 20260928-082652: the way counted done, nothing moved)
                b.retry.succeeded(name)
                return
            # the next way in this same call: the stuck clock was just cleared, so a refusal here waited another
            # STUCK_LIMIT before sideways was tried (open ground: "up" resolves to the feet's own column)
            b.failed(name, NotAvailable(f"could not get {label} to {target}"))
        self.escalate("stuck", f"every unstuck method failed at {snap.feet}")
        raise NotAvailable("every unstuck method failed here")

    def escalate(self, kind, what):
        """A macro problem: one `?? STALL` line per kind per 20 min — supervise.sh wakes Claude on it."""
        now = time.time()
        if now - self.escalated.get(kind, 0) < 1200:
            return
        self.escalated[kind] = now
        log(f"?? STALL {kind}: {what}")

# each way out: its offset from the feet
UNSTUCK_WAYS = {"up": (0, 12, 0), "east": (16, 0, 0), "west": (-16, 0, 0), "south": (0, 0, 16), "north": (0, 0, -16),
                "down": (0, -8, 0)}
SIDEWAYS = ("east", "west", "south", "north")


def stuck_situation(enclosed, pit, column, under_rock):
    """Pure: where we are stuck — walled in, in a pit, high on a column, under rock, or in the open."""
    if enclosed:
        return "enclosed"
    if pit:
        return "pit"
    if column:
        return "high"
    return "underground" if under_rock else "open"


def unstuck_order(situation):
    """Pure: the ways out, first first — walled in: dig out sideways; a pit: climb; high: come down; under rock:
    toward the surface; open: sideways (up first built a pillar on open ground)."""
    return {"enclosed": SIDEWAYS + ("up", "down"), "pit": ("up",) + SIDEWAYS + ("down",),
            "high": ("down",) + SIDEWAYS + ("up",), "underground": ("up",) + SIDEWAYS + ("down",)}.get(
        situation, SIDEWAYS + ("up", "down"))


def on_column(region, feet):
    """Pure: standing on a one-wide column — every cell round the one under the feet is air."""
    x, y, z = feet
    ring = [(x + dx, y - 1, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]
    return all(region.inside(c) for c in ring) and not any(region.solid(c) for c in ring)


def home_bed_near(mem, snap):
    """A home bed within the night's reach (memory, no read)."""
    bed = mem.home_part("beds", snap.dimension, snap.feet, anywhere=True)
    return bed is not None and math.dist(bed, snap.feet) <= survive.HOME_BED_R


def recovery_worth(value_s, dist, since_s, speed, despawn_s):
    """Pure: what walking back to a death's drops is worth, in seconds: their value if reached before they despawn,
    less the walk; ≤ 0 not worth it (a spot 10k blocks off was walked to)."""
    trip = dist / speed
    reached = trip < despawn_s - since_s
    return (value_s if reached else 0.0) - trip


def worth_recovering(b, snap, now=None):
    """The last death (same dimension, drops not yet despawned) is worth the walk: its bag priced another way."""
    from .data import ITEM_DESPAWN_S      # data, not memory: the needs closure stays as it was
    death = b.mem.recent_death(snap.dimension)
    if death is None:
        return False
    prices = b.price_table(snap)
    value = sum((prices.get(item) or 0.0) * n for item, n in death.get("carried", ()))
    since = (now or time.time()) - death["t"]
    return recovery_worth(value, math.dist(snap.feet, death["pos"]), since, nav.PLAYER_SPEED, ITEM_DESPAWN_S) > 0


def _death_retired(c):
    """The death note this call walked to is spent: what was there is carried, what was not is not coming back."""
    death = c.args[0].mem.recent_death(api.get("/state")["dimension"])
    return death is None or tuple(death["pos"]) != c.result

def _drops_gone(c):
    """World evidence, not our own note: no dropped items left around the death spot we walked to."""
    from .world import entities
    return _death_retired(c) and not entities(10, ["minecraft:item"])

@skill(gives=["state:recovered"], remaining=_k.none_of("minecraft:item", within=6.0), needs={}, verify=_drops_gone, budget=300, stall=90)
def recover_items(ctx):
    """Go back to the last death spot within 5 minutes and pick up what dropped there."""
    s = api.get("/state")
    death = ctx.mem.recent_death(s["dimension"])
    if death is None:
        raise NotAvailable("no recent death to recover from")
    pos = tuple(death["pos"])
    log(f"   recovering items at the death spot {pos}")
    if not nav.arrived(pos, ctx.policy, range_=2, attempts=1):
        raise api.NavFailed(f"death spot {pos} not reachable", pos=pos)
    before = Inventory().used_slots()
    nav.sweep(ctx, radius=10, wait=60)
    yield Inventory().used_slots()
    got = gained(lambda: Inventory().used_slots(), before) - before
    # the note is spent either way: retire it on arrival, or the same walk looks worth it again
    ctx.mem.forget_death(pos)
    log(f"recovered {got} stacks at {pos}" if got else f"nothing left at {pos}: the drops are gone")
    return pos
