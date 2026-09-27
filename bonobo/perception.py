"""Perception thread: watches /state ~5 times a second and sorts what it sees into two kinds of trouble. environment (hazard.py)    lava, fire, water, a fall, a buried head: preempt at SAFETY with a /stop, and let the brain run the rescue at the top of its next round (hazard.handle). hostiles (fight_loop.py)   critical health, dragon breath, a provoked enderman, mobs that would kill us before the planner decides again: the threat model chooses an answer and fight_loop carries it out. Hunger is not trouble here: eating belongs to the brain's upkeep. The mod still owns per-tick nets (LavaGuard, forced surfacing, WaterClutch); this is the layer between those nets and the brain. Never acts while the player holds control, and never interrupts a rescue already running (api.MODE == "survival")."""

import math
import threading
import time

from . import api, arbiter, fight_loop, hazard, paths
from .data import memo_ttl
from .beliefs import CONFIG as _CONFIG
from .hazard import REFLEX_SLACK_S, TICKS_PER_S, drowning, drowning_in  # noqa: F401  (re-exported)
from .threat import ENGAGE as _ENGAGE
from .combat_model import hazards, note_hazards  # noqa: F401  (the store lives with the points it holds)

POLL_S = 0.2
# the operator's interrupt ends the running skill (/stop only cancels the current mod task)
FLAG = paths.data("interrupt")
HURT_RATE = 0.0       # health per second, measured
_HP_SEEN = None       # (health, when) from the previous read

THREAT_ROWS, THREAT_IDS, THREAT_AT = [], [], 0.0

def note_hurt(state, now=None):
    """Differentiate the health bar. Called every perception round; decays to zero when nothing is hitting us."""
    global HURT_RATE, _HP_SEEN
    import time as _t
    now = now if now is not None else _t.time()
    hp = float(state.get("health", 20))
    prev = _HP_SEEN
    _HP_SEEN = (hp, now)
    if prev is None:
        return HURT_RATE
    dt = now - prev[1]
    if dt <= 0.01 or dt > 3.0:
        return HURT_RATE
    lost = prev[0] - hp
    rate = max(0.0, lost / dt)
    # rise at once, fall slowly: one arrow is evidence, one quiet second is not
    HURT_RATE = rate if rate > HURT_RATE else HURT_RATE * _CONFIG["risk"]["hurt_decay"]
    return HURT_RATE

def hurt_rate():
    """The pressure, MEASURED: how fast the health bar has actually been falling."""

    return HURT_RATE

def pressure_now(here, rows, prot=0.0, field=None, horizon=None):
    """The pressure we are actually under: the model's rate or the measured one, whichever is worse."""

    from . import estimate
    return max(estimate.pressure_hp_s(here, rows, prot, ground=field, horizon=horizon), hurt_rate())

def note_threats(near, now=None, here=None):
    """Record the threat rows and their entity ids."""

    global THREAT_ROWS, THREAT_IDS, THREAT_AT
    import time as _t
    from . import threat
    now = now if now is not None else _t.time()
    THREAT_ROWS = threat.hostile_rows(near or [], _SEEN, now, here=here)
    THREAT_IDS = threat.ids_by_row(near or [], THREAT_ROWS)
    THREAT_AT = now
    return THREAT_ROWS

def threats_seen(max_age_s=3.0, now=None):
    """(rows, ids) as perception last saw them, or ([], []) when it has not looked recently enough to be trusted."""
    import time as _t
    if not THREAT_ROWS or (now or _t.time()) - THREAT_AT > max_age_s:
        return [], []
    return list(THREAT_ROWS), list(THREAT_IDS)

def seen_at():
    """When the rows above were read."""

    return THREAT_AT

_SEEN = {}            # entity id -> (pos, when): velocity differencing, owned by this thread
PAUSED = False         # the scenario bench sets this while commands rebuild the world (no clutch on a setup fall)
INTERRUPT_TTD_S = float(_ENGAGE["interrupt_ttd_s"])     # floor: never look less far ahead than this

def interrupt_within_s():
    """Seconds until the planner next gets to decide — the running commitment, measured from the last segment."""

    from . import api as _api
    return max(INTERRUPT_TTD_S, _api.LAST_SEGMENT_S)
REPEAT_S = 10         # the same danger interrupts at most once per 10 s (let the rescue work)

HOSTILE = ("critical_health", "breath", "enderman", "hostiles")
DANGERS = hazard.KINDS + HOSTILE

NIGHTFALL = "night"


def nightfall(state):
    """Pure: the soft boundary request for surface work at dusk or night — "night" when the body stands in the
    Overworld between DAY_END and NIGHT_END with open sky over it; None underground or under a roof (sky light
    at most COVERED_SKY), in daylight, or in another dimension."""
    from .data import COVERED_SKY, DAY_END, NIGHT_END
    if state.get("dimension", "minecraft:overworld") != "minecraft:overworld":
        return None
    t = int(state.get("timeOfDay", 0)) % 24000
    if not DAY_END <= t < NIGHT_END or state.get("skyLight", 15) <= COVERED_SKY:
        return None
    return NIGHTFALL


def danger(state, hostiles_within=None, breath_within=None, enderman_after_us=None, time_to_die=None,
           buried=False, fallen=0.0):
    """Pure: the danger kind to interrupt for (one of DANGERS), or None."""

    if state.get("dead") or state.get("control", {}).get("paused"):
        return None
    env = hazard.kind(state, buried=buried, fallen=fallen)
    if env is not None:
        return env
    hp = state.get("health", 20)
    # a breath or head butt in the End takes 10+ hp, so the End's floor is higher
    if hp <= (12 if state.get("dimension") == "minecraft:the_end" else 4):
        return "critical_health"
    # dragon breath burns ~10 hp a second: an emergency at any health
    if breath_within is not None and state.get("dimension") == "minecraft:the_end" and breath_within(8):
        return "breath"
    # endermen are their own signal: a provoked one follows through teleports; the answer is water or cover
    if enderman_after_us is not None and enderman_after_us(12):
        return "enderman"
    # a fight expects hostiles close: while attacking only critical health interrupts
    fighting = ((state.get("control") or {}).get("task") or {}).get("type") == "attack"
    if hp <= 10 and hostiles_within is not None and not fighting:
        d = hostiles_within(8)
        if d is not None and d <= 6:
            return "hostiles"
    # time to die at the current pressure (arrows count from far): health alone missed every death by arrows
    if time_to_die is not None and not fighting:
        t = time_to_die()
        # only what would kill us before the planner next decides is worth an interrupt
        if t is not None and t <= interrupt_within_s():
            return "hostiles"
    return None

def _running_skill():
    from .skill import HEARTBEAT
    try:
        with open(HEARTBEAT) as f:
            t, name = f.read().split()[:2]
        return name if time.time() - float(t) < 5 else None
    except (OSError, ValueError):
        return None

def _eating():
    """Eating is running: nothing may cut in."""

    return _running_skill() == "eat"

class Watcher(threading.Thread):
    def __init__(self):
        super().__init__(name="perception", daemon=True)
        self.last = {}
        self._seen = {}           # entity id -> (pos, when) for the threat layer's velocity differencing
        self._ttd_t, self._ttd = 0.0, None
        self.stopped = False
        self.hazard = hazard.Watch()     # how far we have fallen, whether the head is in a block

    def _hostiles_within(self, radius):
        """How close the nearest thing that can hurt us is, from THIS tick's reading."""

        try:
            rows, _ids = threats_seen()
            here = LAST_HERE
            near = [math.dist(here, row[0]) for row in rows] if here else []
            near = [d for d in near if d <= radius]
        except Exception:
            return None
        return min(near, default=None)

    def _time_to_die(self, s):
        """Seconds to death at the current threat pressure, or None."""

        now = time.time()
        if now - self._ttd_t < 1.0:
            return self._ttd
        self._ttd_t = now
        try:
            from . import threat
            here = (s["x"], s["y"], s["z"])
            rows, _ids = threats_seen(now=now)      # this tick's own reading: nobody queries entities twice
            if not rows:
                self._ttd = None
            else:
                from . import estimate
                prot = threat.protection(s.get("armor", 0), False)
                self._ttd = estimate.time_to_die_s(
                    s.get("health", 20), pressure_now(here, rows, prot, horizon=interrupt_within_s()))
        except (api.McError, KeyError):
            self._ttd = None
        return self._ttd

    def _look(self, s):
        """One read of what is near, shared by everything that asks this tick."""

        global LAST_HERE
        try:
            near = api.get("/entities?radius=24").get("entities", []) or []
        except (api.McError, KeyError):
            return None
        note_hazards(near)
        LAST_HERE = (s["x"], s["y"], s["z"])
        return note_threats(near, time.time(), here=LAST_HERE)

    def _answer_threats(self, state):
        """One look at the world, one outcome recorded — an answer, or a named reason there was none."""

        from . import threat
        now = time.time()
        if not fight_loop.wired():
            return observe(now, "unwired")
        if api.SOFT:
            return observe(now, "soft")
        if _eating():
            return observe(now, "eating")
        rows, _ids = threats_seen(now=now)
        if not rows:
            # no rows after our own fresh read means quiet, an answer; only a failed read is blindness
            return observe(now, "stale" if THREAT_ROWS else "quiet", seen_at=seen_at())
        state = perceived(state, now)
        sstate = threat.price_state(hp=max(1, int(state.get("health", 20))), armor=int(state.get("armor", 0)))
        price = lambda dhp: threat.hp_seconds(sstate, dhp)
        chosen = fight_loop.bid(state, rows, price, ids=THREAT_IDS)
        if chosen is None:
            return observe(now, "nothing_pays", rows=len(rows), seen_at=seen_at())
        option, worth = chosen
        key = f"threat:{option.kind}"
        if now - self.last.get(key, 0) < 1.0:
            return observe(now, "repeat", kind=option.kind, rows=len(rows), seen_at=seen_at())
        self.last[key] = now
        taken, refused, failure = fight_loop.offer(
            option, worth, key, now, release=lambda: fight_loop.lease_done(state, threats_seen()[0], price, THREAT_IDS),
            held=fight_loop.HELD, seen_at=seen_at() or now)
        observe(now, "answered" if taken else "refused", kind=option.kind, worth_s=round(worth, 1),
                rows=len(rows), seen_at=seen_at(), taken=bool(taken), refused=refused, **failure)
        if taken:
            api.log(f"!! threat: {option.kind} ({option.why}) worth {worth:.0f}s")

    def _breath_within(self, radius):
        """A dragon breath cloud within `radius`."""

        now = time.time()
        if now - getattr(self, "_breath_t", 0) < 1.0:
            return getattr(self, "_breath_seen", False)
        self._breath_t = now
        try:
            near = api.get(f"/entities?radius={int(radius)}").get("entities", [])
            note_hazards(near)
        except api.McError:
            return False
        self._breath_seen = any(e["type"] == "minecraft:area_effect_cloud" for e in near)
        return self._breath_seen

    def _enderman_after_us(self, radius):
        """An enderman provoked within `radius` (mod ≥0.1.33 reports `angry`); same 1 s cache as the breath check."""

        now = time.time()
        if now - getattr(self, "_ender_t", 0) < 1.0:
            return getattr(self, "_ender_seen", False)
        self._ender_t = now
        try:
            near = api.get(f"/entities?radius={int(radius)}").get("entities", [])
            note_hazards(near)
        except api.McError:
            return False
        self._ender_seen = any(e["type"] == "minecraft:enderman" and e.get("angry") for e in near)
        return self._ender_seen

    def run(self):
        while not self.stopped:
            time.sleep(fight_loop.FIGHT_POLL_S if fight_loop.active() else POLL_S)
            if api.MODE == "survival" or PAUSED:
                continue
            try:
                s = api.get("/state")
            except Exception:   # game restarting, network hiccup: the main loop handles those
                continue
            note_hurt(s)
            # look once per tick and hand that one reading to everything below (rows were absent or seconds old)
            self._look(s)
            try:
                self._answer_threats(s)
            except Exception as e:
                # the only watcher for lava, drowning and mobs: a failing answer must never kill the thread
                if time.time() - getattr(self, "_answer_logged", 0) > 30:
                    self._answer_logged = time.time()
                    api.log(f"!! threat answer failed: {type(e).__name__}: {e}")
            import os
            if os.path.exists(FLAG):
                try:
                    with open(FLAG) as f:
                        why = f.read().strip() or "Claude asked"
                    os.remove(FLAG)
                except OSError:
                    why = "Claude asked"
                try:
                    # the message is ours to set; /stop goes through the one exit
                    arbiter.BODY.preempt("safety", lambda: api.post("/stop"), f"claude: {why}")
                except Exception:
                    pass
                api.log(f"!! perception: interrupt requested by Claude ({why})")
                continue
            # a fight skill handles "hurt with hostiles close" itself: only life-or-death interrupts it
            if _eating():
                continue        # a bite takes ~1.6 s and is what saves us: never interrupt it
            # nightfall on the surface: once per night, a soft request honoured between tasks (api.at_boundary);
            # the brain then takes the night's way and resumes the same target (arbiter.RESUME_OF "night")
            night = nightfall(s)
            if night is None:
                self._night_told = False
            elif not getattr(self, "_night_told", False) and (s.get("control") or {}).get("task"):
                self._night_told = True
                api.AT_BOUNDARY = night
                api.log("!! perception: night on the surface → the work stops at its next boundary")
            # a fight skill handles breath and endermen itself: only life-or-death reasons, or every window is cut
            fighting = fight_loop.active() or api.SOFT
            reason = danger(s, None if fighting else self._hostiles_within,
                            None if fighting else self._breath_within,
                            None if fighting else self._enderman_after_us,
                            None if fighting else (lambda: self._time_to_die(s)),
                            buried=self.hazard.buried(s), fallen=self.hazard.fallen(s))
            now = time.time()
            if reason is None or now - self.last.get(reason, 0) < REPEAT_S:
                continue
            if not (s.get("control") or {}).get("task"):
                continue        # nothing running to interrupt; the next round's survival check will see it
            if reason == "hostiles" and not answering(now):
                # stopping the body is not an answer: only the layer about to answer a threat may stop the work
                continue
            self.last[reason] = now
            if api.SOFT:
                api.INTERRUPT = reason      # soft skill: message only, no /stop — the skill takes cover itself
                # a soft skill takes cover itself; cancelling its task stranded the player
                api.log(f"!! perception: {reason} → handed to the running skill")
                continue
            try:
                arbiter.BODY.preempt("safety", lambda: api.post("/stop"), reason)
            except Exception:
                pass
            api.log(f"!! perception: {reason} → interrupting the current task")

LAST_HERE = None       # where we stood when the rows were read: the reading and the position are one observation
ANSWERED = []
ANSWERED_MAX = 500

def answering(now=None, within=2.0):
    """Did the threat layer just choose an answer? The interrupt's one permission to stop ordinary work."""
    now = time.time() if now is None else now
    return any(now - a["t"] <= within and a["outcome"] in ("answered", "refused") for a in ANSWERED[-20:])

def observe(t, outcome, **facts):
    """Record what this look at the world came to. Returns None so a caller can `return observe(...)`."""
    ANSWERED.append({"t": round(t, 2), "outcome": outcome, **facts})
    del ANSWERED[:-ANSWERED_MAX]
    return None

def answered_since(mark=0):
    """Every look taken after `mark` (a length read before the stretch of interest)."""
    return list(ANSWERED[mark:])

def watching():
    """Is the layer that answers threats actually running?"""

    return fight_loop.wired() and any(t.name == "perception" and t.is_alive()
                                      for t in threading.enumerate())
GRID, GRID_AT, GRID_AT_POS = None, 0.0, None
_GROUND = {}        # here → (at, (grid, region)): one slot, data.memo_ttl
REGION = None       # the blocks GRID was read from: evade asks it where a walk lands (nav.landing)
GRID_R = 8
GRID_TTL_S = 2.0
_KIT, _KIT_SIG = {}, None

_FAILED = set()          # what perceived() already logged once: a failing read says so, and only once

def perceived(state, now, ground_of=None, kit_of=None):
    """The state the threat model prices: kit, ground (`field`) and the footing evade walks on, each read on its own."""

    ground_of = ground_of or ground
    kit_of = kit_of or (lambda st: kit(kit_signature(st, now)))
    out = dict(state)
    for name, read in (("kit", lambda: out.update(kit_of(state))),
                       ("ground", lambda: out.update(field=ground_of(state))),
                       ("footing", lambda: out.update(footing=footing(state)))):
        try:
            read()
        except Exception as e:
            if name not in _FAILED:
                _FAILED.add(name)
                api.log(f"!! perception: {name}: {type(e).__name__}: {e}")
    return out

def ground(state, now=None, radius=GRID_R, region_of=None):
    """The walkable field around us, re-read at most every GRID_TTL_S and only when we have moved."""

    global GRID, GRID_AT, GRID_AT_POS, REGION
    from . import field as _field
    from .world import Region
    region_of = region_of or Region
    now = now if now is not None else time.time()
    here = tuple(int(math.floor(state[k])) for k in ("x", "y", "z"))
    def read():
        region = region_of(tuple(here[i] - radius for i in range(3)), tuple(here[i] + radius for i in range(3)))
        return _field.from_region(region, here, radius), region
    try:
        GRID, REGION = memo_ttl(_GROUND, here, GRID_TTL_S, read, now, one=True)
    except Exception:
        return GRID                  # a failed read keeps the last field
    GRID_AT, GRID_AT_POS = _GROUND[here][0], here
    return GRID

def footing(state):
    """spot → where a walk toward it lands (nav.landing), for evade; None before the ground was read."""

    from . import nav
    region, here = REGION, (state["x"], state["y"], state["z"])
    return None if region is None else (lambda spot: nav.landing(region, here, spot))

KIT_TTL_S = 2.0      # the bag is re-read at least this often: /state says nothing of a sword given or picked up

def kit_signature(state, now):
    """Pure: when the kit must be read again — the held slot, a screen, the armour changed, or KIT_TTL_S passed."""

    return (state.get("selectedSlot"), state.get("screen"), state.get("armor"), int(now // KIT_TTL_S))

def sword_level(tiers):
    """Pure: the dps table's sword level from working sword tiers: 0 = fist, wood/gold = 1, at most 3."""

    return min(3, max(1, max(tiers))) if tiers else 0

def kit(signature):
    """What we are carrying, re-read only when `kit_signature` changes: this runs at 5 Hz."""
    global _KIT, _KIT_SIG
    if signature == _KIT_SIG and _KIT:
        return _KIT
    from .knowledge import food_count
    from .world import Inventory
    inv = Inventory()
    _KIT = {"sword_tier": sword_level([t for t, d, _ in inv.tools("sword") if d >= 1]),
            "shield": inv.offhand() == "minecraft:shield",
            "food_items": food_count(inv),              # knowledge's one food table
            "blocks": inv.count("building"),
            "dig_ok": any(d >= 1 for _t, d, _ in inv.tools("pickaxe")),
            "golden_apples": inv.count("minecraft:golden_apple") + inv.count("minecraft:enchanted_golden_apple")}     # a hole down needs no blocks
    _KIT_SIG = signature
    return _KIT

def start():
    w = Watcher()
    w.start()
    return w
