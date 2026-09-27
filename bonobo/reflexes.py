"""The maintenance reflexes: a fixed trigger in, a fixed action out — no planning, a second's work. One ordered
table (the shape of `recovery.TABLE`), the arbiter's MAINTAIN layer: faster than any plan, slower than a fight.

    eat on a hungry stomach with food carried · out of the water · home from the Nether when it turns bad ·
    dug out in the morning · into a bed at night · the night's shelter from what is carried · a finished furnace
    or machine emptied · a full bag emptied · a way made where a walk was blocked · unstuck

What must be PLANNED to be had — a bed, food stock, a tool, a bucket, blocks, the night's ore — is not here: those
are PLAN proposals (upkeep's needs, `decompose`). A trigger reads only its view (`view`: readings made once per
round, lazily); the action is the upkeep executor's.
"""
import math
import time

from . import api, nav, nether, skills, tape
from .api import McError, NotAvailable, log
from .data import BASE_MARKERS, COVERED_SKY
from .knowledge import RAW_MEAT, food_count
from .skill import skill
from .skillcore import gained
from .world import Inventory, find

BAG_FULL = 34              # slots used before the bag is emptied
BRIDGE_MIN = 8             # building blocks it takes to bridge a blocked path
EAT_BELOW = 14             # hunger points: eat below this, while there is something to eat (standing)
STARVE = 6                 # hunger points: at or below this, raw meat is eaten rather than waited on
JOB_RANGE = 96
STUCK_LIMIT = 60           # seconds in the same block with the same bag → unstuck
BLOCKED_FOR_S = 120        # a path failure this recent, here, is "the path is blocked"


# (name, trigger over the round's view) — in order: the first that fires is the reflex the layer proposes first.
# (name, trigger over the round's view, action(m: Maintain, v: view)) — one row per reflex, trigger and action
# together; in order: the first that fires is the one the layer proposes first.
FUELS = ("coal", "charcoal", "planks", "log")
STATION_R = 8          # a furnace of ours this near counts as one to cook in


def can_cook(inv, furnace_near):
    """Pure: raw meat carried can be cooked from here — fuel in the bag, and a furnace carried, one near, or the
    eight cobblestone to make one."""
    fuel = any(inv.count(f) for f in FUELS)
    furnace = inv.count("minecraft:furnace") or furnace_near or inv.count("minecraft:cobblestone") >= 8
    return bool(fuel and furnace)


def meal(food, inv, cookable):
    """Pure: what the eat row eats — None (nothing now), False (a meal: cooked food only), True (raw meat too).
    Raw only when starving (food ≤ STARVE) or when it cannot be cooked (`cookable()`); otherwise raw carried is
    cooked by the plan first (night_first__low ate both raw beef at 8 and had nothing left to cook)."""
    if food_count(inv):
        return False
    if not any(inv.count(f) for f in RAW_MEAT):
        return None
    return True if food <= STARVE or not cookable() else None


TABLE = [
    ("recover items", lambda v: v["died_recently"], lambda m, v: recover_items(v["ctx"])),
    ("eat", lambda v: v["food"] < EAT_BELOW and v["meal"] is not None,
     lambda m, v: skills.eat(raw_ok=v["meal"])),
    ("reach land", lambda v: v["swimming"], lambda m, v: skills.reach_land(v["ctx"])),
    ("leave the Nether", lambda v: v["nether_bad"], lambda m, v: nether.use_portal(v["ctx"], "minecraft:overworld")),
    ("dig out", lambda v: not v["night"] and v["enclosed"], lambda m, v: skills.dig_out(v["ctx"])),
    ("sleep", lambda v: v["overworld"] and v["night"] and v["bed_works"] and (v["bed_carried"] or v["bed_near"]),
     lambda m, v: skills.sleep(v["ctx"], m.brain.policy(v["snap"], True))),
    ("shelter", lambda v: v["shelter_ready"], lambda m, v: m.shelter(v["snap"], v["ctx"], v["night_way"])),
    ("collect job", lambda v: v["job_ready"], lambda m, v: m.collect_job(v["snap"], v["ctx"])),
    ("collect machine", lambda v: v["machine_ready"],
     lambda m, v: skills.collect_machine(v["ctx"], m.ready_machine(v["snap"]))),
    ("empty the bag", lambda v: v["used_slots"] >= BAG_FULL, lambda m, v: m.empty_bag(v["snap"], v["ctx"])),
    ("path blocked", lambda v: v["blocked"] and v["building"] >= BRIDGE_MIN,
     lambda m, v: m.bridge(v["ctx"], v["blocked_at"])),
    ("unstuck", lambda v: v["stuck"], lambda m, v: m.unstuck(v["snap"], v["ctx"])),
]
NAMES = tuple(row[0] for row in TABLE)

# Hysteresis (a Schmitt trigger): a row with an exit predicate, once it fired, keeps firing until its exit holds —
# not merely until its trigger stops. Every other row exits when its trigger does. Out of the water: in on
# swimming, out only after standing on something that is not water for LAND_EXIT_S (a shore block's water flips
# `swimming` every tick).
LAND_EXIT_S = 1.0
EXIT = {"reach land": lambda v: v["on_land_s"] >= LAND_EXIT_S}

# What a reflex's work moves, per row: run once and still firing with this unchanged is a failure (it cools under
# retry's policy), so a reflex that cannot help can never hold the body for good. Default: where we stand, the
# bag's used slots and the hunger bar.
PROGRESS = {"empty the bag": lambda v: v["used_slots"], "unstuck": lambda v: v["feet"],
            "reach land": lambda v: v["feet"], "eat": lambda v: v["food"]}
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
    """[(seq, name)] of the reflexes that fire, in table order, skipping those cooling (`ready`): the trigger holds,
    or the row is `active` (it fired last round) and has an exit that does not hold yet."""
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
    if inv.used_slots() >= 35:
        return "bag full"
    return None


def ground(reads=None):
    """The two readings of the ground under the body both needs and reflexes ask (lazy, each read once when first
    asked; `reads` stands in offline): enclosed (skills.enclosed → terrain.is_enclosed) and soft ground to dig in by
    hand (skills.soft_ground_here → terrain.soft_below). One place for both askers."""
    return _once(reads, "enclosed", skills.enclosed), _once(reads, "soft_ground", skills.soft_ground_here)


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


# A shelter step's token → the skill that makes it (decompose.SOURCES["overnight"] steps).
SHELTER_RUN = {"dig_in": lambda ctx: skills.dig_in(ctx), "pod": lambda ctx: skills.pod(ctx),
               "hut": lambda ctx: skills.build_shelter(ctx)}


class Maintain:
    """The reflex table's executor, and what it remembers between rounds: where the body has been (stuck), where
    the last path failure was going (blocked). `brain` supplies the failure policy (`ready`, `failed`, `retry`),
    the movement policy and memory."""

    def __init__(self, brain):
        self.brain = brain
        self.history = []             # (time, feet, bag signature) for "stuck in place"
        self.escalated = {}
        self.blocked = None           # {"t", "place", "pos"}: the last path failure and where it was going
        self.active = frozenset()     # rows inside their hysteresis (`latched`)
        self.land_since = None        # when the body last stood on something that is not water
        self.last_run = None          # (name, progress when it started): judged next round (`stalled`)

    def observe(self, snap):
        from .needs import bag_signature
        now = time.time()
        self.history = [h for h in self.history if now - h[0] <= STUCK_LIMIT + 30]
        self.history.append((now, snap.feet, bag_signature(snap.inv)))
        on_land = snap.state.get("onGround", False) and not snap.state.get("inWater")
        self.land_since = (self.land_since or now) if on_land else None

    def failed(self, cause, err, place):
        """A path failure is remembered with where it was going: the "path blocked" rows answer it."""
        if cause == "nav":
            self.blocked = {"t": time.time(), "place": place, "pos": getattr(err, "pos", None)}

    def act(self, snap, ctx, reads=None):
        """(name, run) of the reflex the arbiter picks among those that fire (`proposals`), or None."""
        from . import arbiter
        props = self.proposals(snap, ctx, reads)
        got = arbiter.arbitrate([arbiter.Intent("maintain", (name, run), seq=seq) for seq, name, run in props])
        return got.action if got else None

    def proposals(self, snap, ctx, reads=None):
        """[(seq, name, run)] of every reflex that fires (TABLE, `seq` its place there): each trigger reads this
        round's view of the snapshot, made here — nothing another step of the round left behind, so the order in
        which needs and reflexes are asked makes no difference. `reads` = {"enclosed", "bed_near", "soft_ground"}
        stands in for world reads (offline); whatever is missing is read, once, when first asked."""
        from . import needs
        b, s, inv, over = self.brain, snap.state, snap.inv, snap.dimension == "minecraft:overworld"
        blocked = self.blocked_here(b.place)
        enclosed, soft_ground = ground(reads)

        def night_way():
            return b.needs.overnight(snap, needs.night_facts(soft_ground()), bed_too=False)
        view = View({
            "died_recently": lambda: b.mem.recent_death(snap.dimension) is not None,
            "meal": lambda: meal(s.get("food", 20), inv, lambda: can_cook(inv, any(
                "furnace" in st["block"] for st in b.mem.stations(snap.dimension, near=snap.feet, within=STATION_R)))),
            "swimming": lambda: skills.swimming(s),
            "nether_bad": lambda: nether_retreat(snap) is not None,
            "enclosed": enclosed,
            "bed_works": lambda: skills.can_sleep(s) is None,
            "bed_near": _once(reads, "bed_near", lambda: bool(find(BASE_MARKERS["bed"], radius=48, limit=1))),
            "night_way": night_way,
            "shelter_ready": lambda: over and snap.night and not _once(reads, "bed_tonight",
                                                                        lambda: b.needs.bed_tonight(snap))()
            and not self.sheltered(snap, enclosed) and view["night_way"][0] is not None
            and all(st.kind == "shelter" for st in view["night_way"][2]),
            "job_ready": lambda: self.ready_job(snap) is not None,
            "machine_ready": lambda: self.ready_machine(snap) is not None,
            "stuck": lambda: self.stuck_in_place(snap, enclosed),
        }, snap=snap, ctx=ctx, food=s.get("food", 20), night=snap.night, overworld=over,
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
        """Make the way by hand (skills.bridge_toward) toward where the failed walk was going."""
        self.blocked = None
        log(f"   path to {blocked['pos']} blocked → bridging toward it")
        return skills.bridge_toward(ctx, blocked["pos"])

    # -- night
    def shelter(self, snap, ctx, night_way):
        """Night, exposed, no bed to sleep in, the parts in the bag: the way `overnight` priced cheapest from this
        bag and this ground (dig in — with a pickaxe, or by hand in dirt or sand — wall in, a hut). Its parts, when
        missing, are the round's "night prep" need instead (`prepare_night`)."""
        b = self.brain
        ctx = b.context(snap.dimension, b.policy(snap, True))
        way, _secs, steps = night_way
        log(f"   the night: {way} ({' → '.join(map(str, steps))})")
        return SHELTER_RUN[steps[-1].token](ctx)

    def sheltered(self, snap, enclosed=None):
        if snap.get("skyLight", 15) <= COVERED_SKY:
            return True
        try:
            if (enclosed or skills.enclosed)():
                return True
        except (tape.ReplayMiss, McError):
            pass
        feet = list(snap.feet)
        return any(feet in s.get("interior", []) for s in self.brain.mem.sites(snap.dimension))

    # -- jobs and the bag
    def ready_job(self, snap):
        near = [j for j in self.brain.mem.jobs(snap.dimension)
                if skills.job_ready(j, snap.state.get("gameTime")) and math.dist(j["pos"], snap.feet) <= JOB_RANGE]
        return min(near, key=lambda j: math.dist(j["pos"], snap.feet), default=None)

    def ready_machine(self, snap):
        """The nearest machine (auto smelter) whose loaded order is due, within JOB_RANGE."""
        near = [m for m in self.brain.mem.machines(snap.dimension)
                if skills.pending_ready(m) and math.dist(m["origin"], snap.feet) <= JOB_RANGE]
        return min(near, key=lambda m: math.dist(m["origin"], snap.feet), default=None)

    def collect_job(self, snap, ctx):
        from . import jobs
        job = self.ready_job(snap)
        if job is not None:
            jobs.collect(ctx, job)

    def empty_bag(self, snap, ctx):
        """One decision (bag.empty_how over bag.let_go's pricing): deposit into a chest that already exists when a
        stack is worth the walk, else drop the cheapest — never a chest crafted for it (no chest and no planks
        cooled the row for 180 s while dirt could simply be thrown)."""
        from .bag import FREE_SLOTS_TARGET, empty_how
        need = max(1, snap.inv.used_slots() - (36 - FREE_SLOTS_TARGET))
        lava = bool(find(["lava"], radius=3, limit=1))
        how = empty_how(snap.inv.slots, need, ctx.prices().get, self.chest_seconds(snap, ctx), lava)
        return skills.deposit(ctx, local_only=snap.night) if how == "deposit" else skills.tidy_inventory(ctx)

    def chest_seconds(self, snap, ctx):
        """Seconds to a chest that already exists (one in reach, or a remembered site's; none at night beyond
        reach), or None."""
        from .data import WALK_BLOCKS_PER_TICK
        if find(["chest", "barrel"], radius=6, limit=1):
            return 2.0
        if snap.night:
            return None
        sites = [s for s in ctx.mem.sites(ctx.dimension) if skills.site_trek_ok(ctx, s)
                 and math.dist(s["pos"], snap.feet) <= 96]
        if not sites:
            return None
        return min(math.dist(s["pos"], snap.feet) for s in sites) / (WALK_BLOCKS_PER_TICK * 20)

    # -- stuck
    def stuck_in_place(self, snap, enclosed=None):
        """Same block and the same bag for STUCK_LIMIT seconds (sheltered at night excluded)."""
        if snap.night and self.sheltered(snap, enclosed):
            return False
        old = [h for h in self.history if time.time() - h[0] >= STUCK_LIMIT]
        if not old:
            return False
        ref = old[-1]
        return all(math.dist(h[1], ref[1]) < 2 and h[2] == ref[2] for h in self.history if h[0] >= ref[0])

    def unstuck(self, snap, ctx):
        """One way out per call — the nearest site, up, sideways, down — each skipped once it failed here."""
        b = self.brain
        x, y, z = snap.feet
        site = b.mem.nearest_site(snap.feet, snap.dimension)
        if site and math.dist(site["pos"], snap.feet) < 12:
            site = None
        methods = ([("site", tuple(site["pos"]))] if site else []) + [
            ("up", (x, y + 12, z)), ("east", (x + 16, y, z)), ("west", (x - 16, y, z)),
            ("south", (x, y, z + 16)), ("north", (x, y, z - 16)), ("down", (x, y - 8, z))]
        for label, target in methods:
            name = f"unstuck:{label}"
            if not b.ready(name):
                continue
            log(f"no progress for {STUCK_LIMIT}s at {snap.feet} → unstuck by heading {label} {target}")
            self.history.clear()
            if nav.moved(nav.go_to(target, b.policy(snap, snap.night), range_=3, attempts=1)):
                b.retry.succeeded(name)
                return
            b.failed(name, NotAvailable(f"could not get {label} to {target}"))
            raise NotAvailable(f"unstuck {label} failed")
        self.escalate("stuck", f"every unstuck method failed at {snap.feet}")
        raise NotAvailable("every unstuck method failed here")

    def escalate(self, kind, what):
        """A macro problem: one `?? STALL` line per kind per 20 min — supervise.sh wakes Claude on it."""
        now = time.time()
        if now - self.escalated.get(kind, 0) < 1200:
            return
        self.escalated[kind] = now
        log(f"?? STALL {kind}: {what}")



def _death_retired(c):
    """The death note this call walked to is spent: what was there is carried, what was not is not coming back."""
    death = c.args[0].mem.recent_death(api.get("/state")["dimension"])
    return death is None or tuple(death["pos"]) != c.result


def _drops_gone(c):
    """World evidence, not our own note: no dropped items left around the death spot we walked to."""
    from .world import entities
    return _death_retired(c) and not entities(10, ["minecraft:item"])


@skill(needs={}, speed={}, verify=_drops_gone, budget=300, stall=90, per_unit=120)
def recover_items(ctx):
    """Go back to the last death spot within 5 minutes and pick up what dropped there."""
    s = api.get("/state")
    death = ctx.mem.recent_death(s["dimension"])
    if death is None:
        raise NotAvailable("no recent death to recover from")
    pos = tuple(death["pos"])
    log(f"   recovering items at the death spot {pos}")
    if not nav.arrived(pos, ctx.policy, range_=2, attempts=1):
        raise api.NavFailed(f"death spot {pos} not reachable")
    before = Inventory().used_slots()
    nav.sweep(ctx, radius=10, wait=60)
    yield Inventory().used_slots()
    got = gained(lambda: Inventory().used_slots(), before) - before
    # Either way the note is spent: what is here is now carried, and what is not here is not coming back. A record
    # the world has already answered must be retired on arrival rather than left to expire on a timer, or the same
    # sixty-block walk is worth the same seconds again five minutes later.
    ctx.mem.forget_death(pos)
    log(f"recovered {got} stacks at {pos}" if got else f"nothing left at {pos}: the drops are gone")
    return pos
