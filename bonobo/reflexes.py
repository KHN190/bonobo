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
from .knowledge import food_count
from .skill import skill
from .skillcore import gained
from .world import Inventory, find

BAG_FULL = 34              # slots used before the bag is emptied
BRIDGE_MIN = 8             # building blocks it takes to bridge a blocked path
EAT_BELOW = 14             # hunger points: eat below this, while there is something to eat (standing)
JOB_RANGE = 96
STUCK_LIMIT = 60           # seconds in the same block with the same bag → unstuck
BLOCKED_FOR_S = 120        # a path failure this recent, here, is "the path is blocked"


# (name, trigger over the round's view) — in order: the first that fires is the reflex the layer proposes first.
# (name, trigger over the round's view, action(m: Maintain, v: view)) — one row per reflex, trigger and action
# together; in order: the first that fires is the one the layer proposes first.
TABLE = [
    ("recover items", lambda v: v["died_recently"], lambda m, v: recover_items(v["ctx"])),
    ("eat", lambda v: v["food"] < EAT_BELOW and v["edible"],
     lambda m, v: skills.eat(raw_ok=not food_count(v["snap"].inv))),
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


class View(dict):
    """The round's readings, each made on first ask (`providers`: {key: zero-argument reader}), then kept."""

    def __init__(self, providers, **known):
        super().__init__(**known)
        self.providers = providers

    def __missing__(self, key):
        value = self.providers[key]()
        self[key] = value
        return value


def due(view, ready=lambda name: True):
    """[(seq, name)] of the reflexes whose trigger fires, in table order, skipping those cooling (`ready`)."""
    return [(i, name) for i, (name, trigger, _act) in enumerate(TABLE) if ready(name) and trigger(view)]


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

    def observe(self, snap):
        from .needs import bag_signature
        now = time.time()
        self.history = [h for h in self.history if now - h[0] <= STUCK_LIMIT + 30]
        self.history.append((now, snap.feet, bag_signature(snap.inv)))

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
            soft = soft_ground()
            return needs.overnight(inv, b.needs.cost(snap), {"soft_ground": soft}, bed_too=False)
        view = View({
            "died_recently": lambda: b.mem.recent_death(snap.dimension) is not None,
            "edible": lambda: skills.edible_carried(inv),
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
            blocked_at=blocked, building=inv.count("building"))
        rows = {name: act for name, _trigger, act in TABLE}
        return [(seq, name, (lambda act=rows[name]: act(self, view))) for seq, name in due(view, b.ready)]

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
                if skills.job_ready(j) and math.dist(j["pos"], snap.feet) <= JOB_RANGE]
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
        if skills.store_plan(snap.inv.slots) and skills.can_store_here(ctx, local_only=snap.night):
            return skills.deposit(ctx, local_only=snap.night)
        return skills.tidy_inventory(ctx)

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


@skill(verify=_drops_gone, budget=300, stall=90, per_unit=120)
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
