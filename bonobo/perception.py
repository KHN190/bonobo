"""Perception thread: watches /state ~5 times a second and sorts what it sees into two kinds of trouble. environment (hazard.py)    lava, fire, water, a fall, a buried head: preempt at SAFETY with a /stop, and let the brain run the rescue at the top of its next round (hazard.handle). hostiles (fight_loop.py)   critical health, dragon breath, a provoked enderman, mobs that would kill us before the planner decides again: the threat model chooses an answer and fight_loop carries it out. Hunger is not trouble here: eating belongs to the brain's upkeep. The mod still owns per-tick nets (LavaGuard, forced surfacing, WaterClutch); this is the layer between those nets and the brain. Never acts while the player holds control, and never interrupts a rescue already running (api.mode() == "survival")."""

import math
import threading
import time
import traceback
from dataclasses import dataclass, field as _dc_field
from typing import Any

from . import api, arbiter, events, fight_loop, hazard, lifecycle, paths, estimate, field as _field, nav, threat, world
from .data import memo_ttl, DAY_END, NIGHT_END, DAY_TICKS, is_night
from .game import EYE_HEIGHT
from .beliefs import COMMON_FOE_HP, CONFIG as _CONFIG
from .hazard import REFLEX_SLACK_S, TICKS_PER_S, drowning, drowning_in  # noqa: F401  (re-exported)
from .threat import ENGAGE as _ENGAGE, seen_at, threats_seen
from .combat_model import hazards, note_hazards  # noqa: F401  (the store lives with the points it holds)
from .knowledge import attack_weapon, dark_here, food_count, sheltered, usable
from .skill import HEARTBEAT

WATCH_S = 0.2     # how often perception reads the world outside a fight (5 Hz)
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
    reach: dict = _dc_field(default_factory=dict)      # (body cell, mob cell) → (at, walks to us): data.memo_ttl
    region: Any = None                # the blocks the grid was read from: evade asks where a walk lands
    kit: dict = _dc_field(default_factory=dict)        # what we carry (`kit`), re-read when kit_sig changes
    kit_sig: Any = None
    damage_at: Any = None             # gameTime of the last jar damage already said
    # per process
    paused: bool = False              # the scenario bench sets this while it rebuilds the world
    failed: set = _dc_field(default_factory=set)       # what perceived() already logged once
    lock: Any = _dc_field(default_factory=threading.RLock, repr=False, compare=False)

    LIFE = ("hurt_rate", "hp_seen", "seen", "last_here", "answered", "grid", "grid_at", "grid_at_pos", "ground",
            "reach", "region", "kit", "kit_sig", "damage_at")


STATE = lifecycle.owns(__name__, PerceptionState())
lifecycle.on_reset(events.forget_goal)      # events' one-life part (it imports no lifecycle: its closure)


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
        if lost >= 1.0:
            hit = state.get("lastDamage")
            if hit and hit.get("gameTime") != STATE.damage_at:
                # the game's own source (a fall has no mob to guess from)
                STATE.damage_at = hit.get("gameTime")
                src = events.damage_label(hit)
            else:
                rows, _ids = threats_seen(now=now)
                src = min(rows, key=lambda r: math.dist(r[0], (state.get("x", 0), state.get("y", 0),
                                                              state.get("z", 0))))[3] if rows and "x" in state else None
            events.hurt(lost, hp, src, t=now)
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

# /entities combat fields (jar): read here only, threat and fight read `read_combat`'s view
COMBAT_KEYS = ("velocity", "in_reach", "shooting", "drawing", "pull_ticks", "charging", "attacking", "ignited",
               "fuse_ticks", "tti_ticks", "impact")


def read_combat(near):
    """Pure: /entities rows as the fight reads them — the jar's combat fields turned into provoked, lit, hit_s,
    impact_at (x, y, z), vel (blocks/s), reach_now, busy (shooting/drawing/charging); the raw fields dropped."""
    out = []
    for e in near or []:
        d = {k: v for k, v in e.items() if k not in COMBAT_KEYS}
        d["provoked"] = bool(e.get("angry") or e.get("attacking"))
        d["lit"] = bool(e.get("ignited"))
        d["reach_now"] = bool(e.get("in_reach"))
        d["busy"] = bool(e.get("shooting") or e.get("drawing") or e.get("charging"))
        if e.get("tti_ticks") is not None:
            d["hit_s"] = float(e["tti_ticks"]) / TICKS_PER_S
        p = e.get("impact")
        if p is not None:
            d["impact_at"] = tuple(float(p[k]) for k in ("x", "y", "z")) if isinstance(p, dict) else tuple(map(float, p))
        v = e.get("velocity")
        if v is not None:
            d["vel"] = tuple(float(c) * TICKS_PER_S for c in v)
        out.append(d)
    return out


REACH_KEPT = 64          # (body, mob) answers kept at most


def reaches_us(here, now):
    """(pos, reach, kind) → can a mob there get at us: a ranged one by an open line of fire (world.line_of_fire,
    eye to eye), any other by a walk that comes to us (nav.walks_to at the walk's own arrive margin — its attack
    reach is no walk: within it, the walk "arrives" before it starts). Kept READ_TTL_S per pair: this runs at 5 Hz."""
    body = tuple(int(math.floor(c)) for c in here)
    eye = (here[0], here[1] + EYE_HEIGHT, here[2])

    def ask(pos, reach, kind=None):
        cell = tuple(int(math.floor(c)) for c in pos)
        if len(STATE.reach) > REACH_KEPT:
            STATE.reach.clear()          # the mobs round us change: old pairs are no answer to keep
        if threat.MOBS.get(kind, {}).get("ranged"):
            return memo_ttl(STATE.reach, (body, cell), READ_TTL_S,
                            lambda: world.line_of_fire((pos[0], pos[1] + EYE_HEIGHT, pos[2]), eye), now)
        climber = kind in threat.CLIMBERS
        return memo_ttl(STATE.reach, (body, cell), READ_TTL_S, lambda: nav.walks_to(cell, None, climber, body[1]), now)
    return ask


def threat_readout(state):
    """What the rows answered were: each row's id, kind, distance, and its walk answer (STATE.reach) — the evidence
    a threat line carries."""
    here = (state.get("x", 0), state.get("y", 0), state.get("z", 0))
    body = tuple(int(math.floor(c)) for c in here)
    out = []
    for i, r in zip(threat.THREAT_IDS, threat.THREAT_ROWS):
        cell = tuple(int(math.floor(c)) for c in r[0])
        walk = STATE.reach.get((body, cell), (None, "unasked"))[1]
        out.append(f"{r[3].split(':')[-1]} {i} {math.dist(here, r[0]):.1f} off walk={walk}")
    return "; ".join(out) or "no rows"


def note_threats(near, now=None, here=None, context=None):
    """Record the threat rows and their entity ids (`context`: threat.context_of — who is after us)."""

    import time as _t
    now = now if now is not None else _t.time()
    near = read_combat(near)
    reaches = reaches_us(here, now) if here is not None else None
    threat.THREAT_ROWS = threat.hostile_rows(near or [], STATE.seen, now, here=here, context=context, reaches=reaches)
    threat.THREAT_IMPACTS = threat.impacts_of(near)
    threat.THREAT_LIT = {e.get("id") for e in near if e.get("type") == "minecraft:creeper" and threat.fuse_lit(e)}
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


NIGHTFALL = "night"


IN_SITE = None      # (feet, dimension) → inside a site's interior: set by every Brain built (Brain.__init__)


def nightfall(state, enclosed, in_site=lambda: False):
    """Pure given its readers: the soft boundary request for surface work at dusk or night — "night" in the
    Overworld between DAY_END and NIGHT_END unless sheltered by the night way's own judgement (knowledge.sheltered:
    under rock, walled in, inside a site); None by day or in another dimension."""
    if not is_night(int(state.get("timeOfDay", 0)), state.get("dimension", "minecraft:overworld")) \
            or sheltered(state.get("skyLight", 15), enclosed, in_site):
        return None
    return NIGHTFALL


def in_site_here(s):
    """The feet inside one of our sites' interiors (IN_SITE, set by the brain); False before a brain is built."""
    return IN_SITE is not None and IN_SITE((s["blockX"], s["blockY"], s["blockZ"]), s.get("dimension"))


# the kit's readings the survival price takes as they are (threat.price_state's keys)
KIT_PRICED = ("sword", "shield", "food_items", "bed", "torches", "bag_free")


def price_inputs(state):
    """Pure given IN_SITE: the survival state health is priced in (threat.price_state), every key read from the
    perceived state (the kit merged; a kit not read leaves its keys at price_state's). Walls are not read: a body a
    mob reaches is not walled in. nights_missed has no reading anywhere: price_state's."""
    t, dim = int(state["timeOfDay"]), state.get("dimension", "minecraft:overworld")
    tier = state.get("pick_tier")
    return threat.price_state(
        night=is_night(t, dim), ticks_until_dusk=world.ticks_until_dusk(t), hp=max(1, int(state.get("health", 20))),
        food=int(state.get("food", 20)), armor=int(state.get("armor", 0)), dark=dark_here(state),
        sheltered=sheltered(state.get("skyLight", 15), lambda: False, lambda: in_site_here(state)),
        pickaxe=0 if tier is None else max(1, tier),       # threat.tool_loss: 0 none, 1 stone-class, 2+ iron
        **{k: state[k] for k in KIT_PRICED if k in state})


def _enclosed_now(state):
    """The walls around the feet read now (terrain.is_enclosed over the 3×4×3 box): the shelter's remainder is
    empty."""
    from .world import Region, is_enclosed
    x, y, z = state["blockX"], state["blockY"], state["blockZ"]
    return is_enclosed(Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1)), (x, y, z))


def danger(state, hostiles_within=None, breath_within=None, enderman_after_us=None, time_to_die=None,
           buried=False, fallen=0.0, within_s=INTERRUPT_TTD_S):
    """Pure: the danger kind to interrupt for (a hazard.KINDS one, or a hostile one: breath, enderman, hostiles), or None."""

    if state.get("dead") or state.get("control", {}).get("paused"):
        return None
    env = hazard.kind(state, buried=buried, fallen=fallen)
    if env is not None:
        return env
    hp = state.get("health", 20)       # at the floor (data.critical_hp) it is hazard.kind's "critical": SAFETY's
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
        if t is not None and t <= within_s:
            return "hostiles"
    return None

def _running_skill():
    try:
        with open(HEARTBEAT) as f:
            t, name = f.read().split()[:2]
        return name if time.time() - float(t) < 5 else None
    except (OSError, ValueError) as e:
        api.swallowed("perception._running_skill", e)
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
        except (TypeError, ValueError, IndexError) as e:      # a row short of its centre: no distance this tick
            api.swallowed("perception._hostiles_within", e)
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
            near = world.entities(24)
        except (api.McError, KeyError) as e:
            api.swallowed("perception._look", e)
            return None
        note_hazards(read_combat(near), hostile=threat.aggro)    # a calm neutral is no hazard
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
        sstate = price_inputs(state)
        price = lambda dhp: threat.hp_seconds(sstate, dhp)
        chosen = fight_loop.bid(state, rows, price, ids=threat.THREAT_IDS)
        # each look carries the bid's detail: the field's shape and read time, our height, the options with their
        # worth, why none went out, the engagement (fight_loop.LAST_LOOK)
        detail = dict(fight_loop.look_detail(), field_at=round(STATE.grid_at, 2))
        if chosen is None:
            return observe(now, "nothing_pays", rows=len(rows), seen_at=seen_at(), look=detail)
        option, worth = chosen
        key = f"threat:{option.kind}"
        if now - self.last.get(key, 0) < 1.0:
            return observe(now, "repeat", kind=option.kind, rows=len(rows), seen_at=seen_at(), look=detail)
        self.last[key] = now
        taken, refused, failure = fight_loop.offer(
            option, worth, key, now, release=lambda: fight_loop.lease_done(state, threats_seen()[0], price, threat.THREAT_IDS),
            held=fight_loop.held(), seen_at=seen_at() or now)
        observe(now, "answered" if taken else "refused", kind=option.kind, worth_s=round(worth, 1),
                rows=len(rows), seen_at=seen_at(), taken=bool(taken), refused=refused, look=detail, **failure)
        if taken:
            api.detail(f"!! threat: {option.kind} ({option.why}) worth {worth:.0f}s, feet {api.where((state.get('x'), state.get('y'), state.get('z')))} — {threat_readout(state)}")
            events.decision("fight", option.kind, worth, option.why)       # said once per change, not per bid
        elif refused:
            events.anomaly("answer refused", f"{option.kind}: {refused}")

    def _breath_within(self, radius):
        """A dragon breath cloud within `radius`."""

        now = time.time()
        if now - getattr(self, "_breath_t", 0) < 1.0:
            return getattr(self, "_breath_seen", False)
        self._breath_t = now
        try:
            near = world.entities(int(radius))
            note_hazards(read_combat(near), hostile=threat.aggro)    # a calm neutral is no hazard
        except api.McError as e:
            api.swallowed("perception._breath_within", e)
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
            near = world.entities(int(radius))
            note_hazards(read_combat(near), hostile=threat.aggro)    # a calm neutral is no hazard
        except api.McError as e:
            api.swallowed("perception._enderman_after_us", e)
            return False
        self._ender_seen = any(e["type"] == "minecraft:enderman" and e.get("angry") for e in near)
        return self._ender_seen

    def run(self):
        while not self.stopped:
            time.sleep(api.READ_EVERY_S if fight_loop.active() else WATCH_S)
            if api.mode() == "survival" or STATE.paused:
                continue
            try:
                s = api.get("/state")
            except (api.McError, api.PlayerTookControl, ValueError) as e:
                api.swallowed("perception.run", e)
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
            night = nightfall(s, lambda: running and not told and _enclosed_now(s), lambda: in_site_here(s))
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
                            buried=self.hazard.buried(s), fallen=self.hazard.fallen(s), within_s=interrupt_within_s())
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
READ_TTL_S = 2.0       # a ground or kit read is reused this long: the field and the bag move slower than 5 Hz

def dig_ok(ground, pick_tier):
    """Pure: the floor under us digs as deep as a hole must be to keep a walker off (melee_stop_blocks), with what we
    carry — the hand for dirt, sand, gravel; a pickaxe for stone (knowledge.diggable, the one rule)."""
    from .knowledge import diggable
    depth = int(float(_ENGAGE["melee_stop_blocks"]))
    floor = tuple(getattr(ground, "floor", ()) or ())[:depth]
    return len(floor) == depth and all(diggable(b, pick_tier) for b in floor)


def perceived(state, now, ground_of=None, kit_of=None):
    """The state the threat model prices: kit, ground (`field`) and the footing evade walks on, each read on its own."""

    ground_of = ground_of or field_around
    kit_of = kit_of or (lambda st: kit(kit_signature(st, now)))
    out = dict(state)
    for name, read in (("kit", lambda: out.update(kit_of(state))),
                       ("ground", lambda: out.update(field=ground_of(state))),
                       ("footing", lambda: out.update(footing=footing(state))),
                       ("dig", lambda: out.update(dig_ok=dig_ok(out.get("field"), out.get("pick_tier"))))):
        try:
            read()
        except Exception as e:  # guard: a reading we cannot take (kit, ground, footing) never stops the answer
            if name not in STATE.failed:
                STATE.failed.add(name)
                api.log(f"!! perception: {name}: {type(e).__name__}: {e}")
                api.detail("".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip())
    return out

def field_around(state, now=None, radius=GRID_R, region_of=None):
    """The walkable field around us, re-read at most every READ_TTL_S and only when we have moved."""

    from .world import Region
    region_of = region_of or Region
    now = now if now is not None else time.time()
    here = tuple(int(math.floor(state[k])) for k in ("x", "y", "z"))
    def read():
        region = region_of(tuple(here[i] - radius for i in range(3)), tuple(here[i] + radius for i in range(3)))
        return _field.from_region(region, here, radius), region
    cache = STATE.ground              # one read: a reset may rebind it meanwhile
    try:
        grid, region = memo_ttl(cache, here, READ_TTL_S, read, now, one=True)
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


def kit_signature(state, now):
    """Pure: when the kit must be read again — the held slot, a screen, the armour changed, or READ_TTL_S passed."""

    return (state.get("selectedSlot"), state.get("screen"), state.get("armor"), int(now // READ_TTL_S))


def kit(signature):
    """What we are carrying, re-read only when `kit_signature` changes: this runs at 5 Hz."""
    if signature == STATE.kit_sig and STATE.kit:
        return STATE.kit
    from .world import Inventory
    inv = Inventory()
    got = {"sword": attack_weapon(inv, COMMON_FOE_HP),       # what an attack holds (knowledge.attack_weapon), None: the hand
            "shield": inv.offhand() == "minecraft:shield",
            "food_items": food_count(inv),              # knowledge's one food table
            "bed": inv.count("bed") > 0,
            "torches": inv.count("minecraft:torch") > 0,
            "bag_free": world.BAG_SLOTS - inv.used_slots(),
            "blocks": inv.count("building"),
            "pick_tier": max((t for t, d, _ in inv.tools("pickaxe") if usable(d)), default=None),
            "golden_apples": inv.count("minecraft:golden_apple") + inv.count("minecraft:enchanted_golden_apple"),
            "bow": inv.count("minecraft:bow") > 0 and inv.count("minecraft:arrow") > 0,
            "gold_worn": any(str((inv.equipment.get(k) or {}).get("id", "")).startswith("minecraft:golden_")
                             for k in ("head", "chest", "legs", "feet"))}     # a piglin leaves the gold-clad alone
    with STATE.lock:
        STATE.kit, STATE.kit_sig = got, signature
    return got

def start_watching():
    w = Watcher()
    w.start()
    return w
