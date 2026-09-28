"""Perception thread: watches /state ~5 times a second and sorts what it sees into two kinds of trouble. environment (hazard.py)    lava, fire, water, a fall, a buried head: preempt at SAFETY with a /stop, and let the brain run the rescue at the top of its next round (hazard.handle). hostiles (fight_loop.py)   critical health, dragon breath, a provoked enderman, mobs that would kill us before the planner decides again: the threat model chooses an answer and fight_loop carries it out. Hunger is not trouble here: eating belongs to the brain's upkeep. The mod still owns per-tick nets (LavaGuard, forced surfacing, WaterClutch); this is the layer between those nets and the brain. Never acts while the player holds control, and never interrupts a rescue already running (api.mode() == "survival")."""

import math
import threading
import time
import traceback
from dataclasses import dataclass, field as _dc_field
from typing import Any

from . import api, arbiter, fight_loop, hazard, lifecycle, paths, estimate, field as _field, nav, threat
from .data import memo_ttl, DAY_END, NIGHT_END, DAY_TICKS
from .beliefs import CONFIG as _CONFIG
from .hazard import REFLEX_SLACK_S, TICKS_PER_S, drowning, drowning_in  # noqa: F401  (re-exported)
from .threat import ENGAGE as _ENGAGE, seen_at, threats_seen
from .combat_model import hazards, note_hazards  # noqa: F401  (the store lives with the points it holds)
from .knowledge import food_count, sheltered, usable
from .skill import HEARTBEAT

POLL_S = 0.2
# the operator's interrupt ends the running skill (/stop only cancels the current mod task)
FLAG = paths.data("interrupt")


@dataclass
class PerceptionState(lifecycle.State):
    """What the perception thread (5 Hz) measures and reads, shared with the body's and the fight's threads."""

    # per life (lifecycle.reset_all): what was measured and read in it
    hurt_rate: float = 0.0            # health per second, measured
    hp_seen: Any = None               # (health, when) from the previous read
    seen: dict = _dc_field(default_factory=dict)       # entity id -> (pos, when): velocity differencing
    last_here: Any = None             # where we stood when the rows were read: one observation with the reading
    answered: list = _dc_field(default_factory=list)   # every look taken and what it came to (`observe`)
    grid: Any = None                  # the walkable field around us (`ground`)
    grid_at: float = 0.0
    grid_at_pos: Any = None
    ground: dict = _dc_field(default_factory=dict)     # here → (at, (grid, region)): one slot, data.memo_ttl
    region: Any = None                # the blocks the grid was read from: evade asks where a walk lands
    kit: dict = _dc_field(default_factory=dict)        # what we carry (`kit`), re-read when kit_sig changes
    kit_sig: Any = None
    # per process
    paused: bool = False              # the scenario bench sets this while it rebuilds the world
    failed: set = _dc_field(default_factory=set)       # what perceived() already logged once
    lock: Any = _dc_field(default_factory=threading.RLock, repr=False, compare=False)

    LIFE = ("hurt_rate", "hp_seen", "seen", "last_here", "answered", "grid", "grid_at", "grid_at_pos", "ground",
            "region", "kit", "kit_sig")


STATE = lifecycle.owns(__name__, PerceptionState())


def pause(on):
    """The bench rebuilds the world: no clutch on a setup fall while `on`."""
    STATE.paused = bool(on)


def note_hurt(state, now=None):
    """Differentiate the health bar. Called every perception round; decays to zero when nothing is hitting us."""
    import time as _t
    now = now if now is not None else _t.time()
    hp = float(state.get("health", 20))
    with STATE.lock:
        prev = STATE.hp_seen
        STATE.hp_seen = (hp, now)
        if prev is None:
            return STATE.hurt_rate
        dt = now - prev[1]
        if dt <= 0.01 or dt > 3.0:
            return STATE.hurt_rate
        lost = prev[0] - hp
        rate = max(0.0, lost / dt)
        # rise at once, fall slowly: one arrow is evidence, one quiet second is not
        was = STATE.hurt_rate
        STATE.hurt_rate = rate if rate > was else was * _CONFIG["risk"]["hurt_decay"]
        return STATE.hurt_rate

def hurt_rate():
    """The pressure, MEASURED: how fast the health bar has actually been falling."""

    return STATE.hurt_rate

def pressure_now(here, rows, prot=0.0, field=None, horizon=None):
    """The pressure we are actually under: the model's rate or the measured one, whichever is worse."""

    return max(estimate.pressure_hp_s(here, rows, prot, ground=field, horizon=horizon), hurt_rate())

def note_threats(near, now=None, here=None, context=None):
    """Record the threat rows and their entity ids (`context`: threat.context_of — who is after us)."""

    import time as _t
    now = now if now is not None else _t.time()
    threat.THREAT_ROWS = threat.hostile_rows(near or [], STATE.seen, now, here=here, context=context)
    threat.THREAT_IMPACTS = threat.impacts_of(near)
    threat.THREAT_IDS = threat.ids_by_row(near or [], threat.THREAT_ROWS)
    threat.THREAT_ALIVE = threat.alive_ids(near)
    threat.THREAT_HIT_S = threat.soonest_hit_s(near)
    threat.THREAT_AT = now
    return threat.THREAT_ROWS

INTERRUPT_TTD_S = float(_ENGAGE["interrupt_ttd_s"])     # floor: never look less far ahead than this

def interrupt_within_s():
    """Seconds until the planner next gets to decide — the running commitment, measured from the last segment."""

    return max(INTERRUPT_TTD_S, api.last_segment_s())
REPEAT_S = 10         # the same danger interrupts at most once per 10 s (let the rescue work)

HOSTILE = ("critical_health", "breath", "enderman", "hostiles")
DANGERS = hazard.KINDS + HOSTILE

NIGHTFALL = "night"


IN_SITE = None      # (feet, dimension) → inside a site's interior: set by every Brain built (Brain.__init__)


def nightfall(state, enclosed, in_site=lambda: False):
    """Pure given its readers: the soft boundary request for surface work at dusk or night — "night" in the
    Overworld between DAY_END and NIGHT_END unless sheltered by the night way's own judgement (knowledge.sheltered:
    under rock, walled in, inside a site); None by day or in another dimension."""
    if state.get("dimension", "minecraft:overworld") != "minecraft:overworld":
        return None
    t = int(state.get("timeOfDay", 0)) % DAY_TICKS
    if not DAY_END <= t < NIGHT_END or sheltered(state.get("skyLight", 15), enclosed, in_site):
        return None
    return NIGHTFALL


def _enclosed_now(state):
    """The walls around the feet read now (terrain.is_enclosed over the 3×4×3 box): the shelter's remainder is
    empty."""
    from .world import Region, is_enclosed
    x, y, z = state["blockX"], state["blockY"], state["blockZ"]
    return is_enclosed(Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1)), (x, y, z))


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
            here = STATE.last_here
            near = [math.dist(here, row[0]) for row in rows] if here else []
            near = [d for d in near if d <= radius]
        except (TypeError, ValueError, IndexError):      # a row short of its centre: no distance this tick
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

        try:
            near = api.get("/entities?radius=24").get("entities", []) or []
        except (api.McError, KeyError):
            return None
        note_hazards(near)
        here = STATE.last_here = (s["x"], s["y"], s["z"])
        return note_threats(near, time.time(), here=here, context=threat.context_of(s, STATE.kit))

    def _answer_threats(self, state):
        """One look at the world, one outcome recorded — an answer, or a named reason there was none."""

        now = time.time()
        if not fight_loop.wired():
            return observe(now, "unwired")
        if api.soft():
            return observe(now, "soft")
        if _eating():
            return observe(now, "eating")
        rows, _ids = threats_seen(now=now)
        if not rows:
            # no rows after our own fresh read means quiet, an answer; only a failed read is blindness
            return observe(now, "stale" if threat.THREAT_ROWS else "quiet", seen_at=seen_at())
        state = perceived(state, now)
        sstate = threat.price_state(hp=max(1, int(state.get("health", 20))), armor=int(state.get("armor", 0)))
        price = lambda dhp: threat.hp_seconds(sstate, dhp)
        chosen = fight_loop.bid(state, rows, price, ids=threat.THREAT_IDS)
        if chosen is None:
            return observe(now, "nothing_pays", rows=len(rows), seen_at=seen_at())
        option, worth = chosen
        key = f"threat:{option.kind}"
        if now - self.last.get(key, 0) < 1.0:
            return observe(now, "repeat", kind=option.kind, rows=len(rows), seen_at=seen_at())
        self.last[key] = now
        taken, refused, failure = fight_loop.offer(
            option, worth, key, now, release=lambda: fight_loop.lease_done(state, threats_seen()[0], price, threat.THREAT_IDS),
            held=fight_loop.held(), seen_at=seen_at() or now)
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
            if api.mode() == "survival" or STATE.paused:
                continue
            try:
                s = api.get("/state")
            except (api.McError, api.PlayerTookControl, ValueError):
                continue        # game restarting, network hiccup, the player's turn: the main loop handles those
            except Exception as e:  # guard: the only watcher for lava, drowning and mobs: a read failure never kills it
                api.unexpected("perception: /state", e, "this tick is skipped")
                continue
            note_hurt(s)
            # look once per tick and hand that one reading to everything below (rows were absent or seconds old)
            self._look(s)
            try:
                self._answer_threats(s)
            except Exception as e:  # guard: the only watcher for lava, drowning, mobs: a failing answer never kills it
                if time.time() - getattr(self, "_answer_logged", 0) > 30:
                    self._answer_logged = time.time()
                    api.log(f"!! threat answer failed: {type(e).__name__}: {e}")
                    api.detail("".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip())
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
                except Exception as e:  # guard: the watcher outlives a failed stop; said, and Claude may ask again
                    api.unexpected("perception: Claude's stop", e, "the task was not stopped")
                api.log(f"!! perception: interrupt requested by Claude ({why})")
                continue
            # a fight skill handles "hurt with hostiles close" itself: only life-or-death interrupts it
            if _eating():
                continue        # a bite takes ~1.6 s and is what saves us: never interrupt it
            # nightfall on the surface: once per night, a soft request honoured between tasks (api.at_boundary);
            # the brain then takes the night's way and resumes the same target (arbiter.RESUME_OF "night")
            told = getattr(self, "_night_told", False)
            running = (s.get("control") or {}).get("task")
            night = nightfall(s, lambda: running and not told and _enclosed_now(s),
                              lambda: IN_SITE is not None and IN_SITE((s["blockX"], s["blockY"], s["blockZ"]),
                                                                      s.get("dimension")))
            if night is None:
                self._night_told = False        # day, or sheltered: asked again when exposed next
            elif not told and running:
                self._night_told = True
                api.request_boundary(night)
                api.log("!! perception: night on the surface → the work stops at its next boundary")
            # a fight skill handles breath and endermen itself: only life-or-death reasons, or every window is cut
            fighting = fight_loop.active() or api.soft()
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
            if api.soft():
                api.request_interrupt(reason)     # soft skill: message only, no /stop — the skill takes cover itself
                # a soft skill takes cover itself; cancelling its task stranded the player
                api.log(f"!! perception: {reason} → handed to the running skill")
                continue
            try:
                arbiter.BODY.preempt("safety", lambda: api.post("/stop"), reason)
            except Exception as e:  # guard: the watcher outlives a failed stop; said, the danger repeats after REPEAT_S
                api.unexpected("perception: safety stop", e, "the task was not stopped")
            api.log(f"!! perception: {reason} → interrupting the current task")

ANSWERED_MAX = 500

def answering(now=None, within=2.0):
    """Did the threat layer just choose an answer? The interrupt's one permission to stop ordinary work."""
    now = time.time() if now is None else now
    return any(now - a["t"] <= within and a["outcome"] in ("answered", "refused") for a in STATE.answered[-20:])

def observe(t, outcome, **facts):
    """Record what this look at the world came to. Returns None so a caller can `return observe(...)`."""
    with STATE.lock:
        looks = STATE.answered
        looks.append({"t": round(t, 2), "outcome": outcome, **facts})
        del looks[:-ANSWERED_MAX]
    return None

def looks_taken():
    """How many looks are recorded now: a mark for `answered_since`."""
    return len(STATE.answered)

def answered_since(mark=0):
    """Every look taken after `mark` (a length read before the stretch of interest)."""
    return list(STATE.answered[mark:])

def watching():
    """Is the layer that answers threats actually running?"""

    return fight_loop.wired() and any(t.name == "perception" and t.is_alive()
                                      for t in threading.enumerate())
GRID_R = 8
GRID_TTL_S = 2.0

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
        except Exception as e:  # guard: a reading we cannot take (kit, ground, footing) never stops the answer
            if name not in STATE.failed:
                STATE.failed.add(name)
                api.log(f"!! perception: {name}: {type(e).__name__}: {e}")
                api.detail("".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip())
    return out

def ground(state, now=None, radius=GRID_R, region_of=None):
    """The walkable field around us, re-read at most every GRID_TTL_S and only when we have moved."""

    from .world import Region
    region_of = region_of or Region
    now = now if now is not None else time.time()
    here = tuple(int(math.floor(state[k])) for k in ("x", "y", "z"))
    def read():
        region = region_of(tuple(here[i] - radius for i in range(3)), tuple(here[i] + radius for i in range(3)))
        return _field.from_region(region, here, radius), region
    cache = STATE.ground              # one read: a reset may rebind it meanwhile
    try:
        grid, region = memo_ttl(cache, here, GRID_TTL_S, read, now, one=True)
    except (api.McError, api.PlayerTookControl, ValueError):
        return STATE.grid            # a failed read keeps the last field
    except Exception as e:  # guard: a field we cannot build keeps the last one (the answer runs at 5 Hz)
        return api.unexpected("perception: ground", e, "the last field is kept") or STATE.grid
    with STATE.lock:
        STATE.grid, STATE.region = grid, region
        STATE.grid_at, STATE.grid_at_pos = cache.get(here, (now,))[0], here
    return grid

def footing(state):
    """spot → where a walk toward it lands (nav.landing), for evade; None before the ground was read."""

    region, here = STATE.region, (state["x"], state["y"], state["z"])
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
    if signature == STATE.kit_sig and STATE.kit:
        return STATE.kit
    from .world import Inventory
    inv = Inventory()
    got = {"sword_tier": sword_level([t for t, d, _ in inv.tools("sword") if usable(d)]),
            "shield": inv.offhand() == "minecraft:shield",
            "food_items": food_count(inv),              # knowledge's one food table
            "blocks": inv.count("building"),
            "dig_ok": any(usable(d) for _t, d, _ in inv.tools("pickaxe")),
            "golden_apples": inv.count("minecraft:golden_apple") + inv.count("minecraft:enchanted_golden_apple"),
            "bow": inv.count("minecraft:bow") > 0 and inv.count("minecraft:arrow") > 0,
            "gold_worn": any(str((inv.equipment.get(k) or {}).get("id", "")).startswith("minecraft:golden_")
                             for k in ("head", "chest", "legs", "feet"))}     # a piglin leaves the gold-clad alone
    with STATE.lock:
        STATE.kit, STATE.kit_sig = got, signature
    return got

def start():
    w = Watcher()
    w.start()
    return w
