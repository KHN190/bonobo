"""Perception thread: watches /state ~5 times a second and sorts what it sees into two kinds of trouble.

  environment (hazard.py)    lava, fire, water, a fall, a buried head: preempt at SAFETY with a /stop, and let the
                             brain run the rescue at the top of its next round (hazard.handle).
  hostiles (fight_loop.py)   critical health, dragon breath, a provoked enderman, mobs that would kill us before the
                             planner decides again: the threat model chooses an answer and fight_loop carries it out.

Hunger is not trouble here: eating belongs to the brain's upkeep. The mod still owns per-tick nets (LavaGuard,
forced surfacing, WaterClutch); this is the layer between those nets and the brain. Never acts while the player
holds control, and never interrupts a rescue already running (api.MODE == "survival")."""
import math
import threading
import time

from . import api, arbiter, fight_loop, hazard, paths
from .beliefs import CONFIG as _CONFIG
from .hazard import REFLEX_SLACK_S, TICKS_PER_S, drowning, drowning_in  # noqa: F401  (re-exported)
from .threat import ENGAGE as _ENGAGE
from .combat_model import hazards, note_hazards  # noqa: F401  (the store lives with the points it holds)

POLL_S = 0.2
# The operator's interrupt (mc.py interrupt): end the running skill so an override directive runs next round. /stop
# alone only cancels the current mod task; a skill (a hunt exploring leg after leg) keeps going.
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
    # Rise at once, fall slowly: one arrow is evidence of a shooter, one quiet second is not evidence of safety.
    HURT_RATE = rate if rate > HURT_RATE else HURT_RATE * _CONFIG["risk"]["hurt_decay"]
    return HURT_RATE


def hurt_rate():
    """The pressure, MEASURED: how fast the health bar has actually been falling. The estimated twin is
    `estimate.pressure_hp_s`; keeping both is the point — the residual between them is what `fit` reads."""
    return HURT_RATE


def pressure_now(here, rows, prot=0.0, field=None, horizon=None):
    """The pressure we are actually under: the model's rate or the measured one, whichever is worse.

    The one place the two twins meet. Both are the same quantity — one estimated from what is in reach, one read
    off the health bar — and every caller that needs "what is happening to us now" asks here rather than picking
    a side: a skeleton that aims well outdoes the model, and that difference was a death.

    `horizon` is the seconds the question is about, and the caller owns it: "what presses me while I work" is the
    work horizon, "what can kill me before the planner decides again" is the interrupt window. Asked over twenty
    seconds, two zombies seventeen blocks away came out at 8.5 hp/s and the interrupt fired every round for a
    danger that was still four seconds' walk away — while every column priced over the same seconds saved
    nothing, so the body was stopped over and over and never answered.
    """
    from . import estimate
    return max(estimate.pressure_hp_s(here, rows, prot, ground=field, horizon=horizon), hurt_rate())


def note_threats(near, now=None, here=None):
    """Record the threat rows and their entity ids. Pure apart from the clock and the differencing memory.

    `here` is where we stand: a mob that has not noticed us yet is less of a threat than one that has, and how far
    it notices from is a distance, so the rows cannot be built without knowing where we are. Without it every
    hostile counts as having noticed us — the old behaviour, and the safe direction to be wrong in.
    """
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
    """When the rows above were read. Every layer polls the same world at its own rate, so a reading has to say
    when it was taken or nothing can tell whether two decisions describe the same world."""
    return THREAT_AT


_SEEN = {}            # entity id -> (pos, when): velocity differencing, owned by this thread
PAUSED = False         # the scenario bench sets this while commands rebuild the world (no clutch on a setup fall)
INTERRUPT_TTD_S = float(_ENGAGE["interrupt_ttd_s"])     # floor: never look less far ahead than this


def interrupt_within_s():
    """Seconds until the planner next gets to decide — the running commitment, measured from the last segment.

    Not a horizon: `estimate.horizon_s` is how long the account runs, this is how soon a danger has to land for
    the reflex to be the one that answers it. Sharing the word was enough to make them look like one number.

    A constant here, beside a skill that held the body until it finished, was wrong at both ends: too short to
    catch anything while the skill ran, too long once the skill yields every block.
    """
    from . import api as _api
    return max(INTERRUPT_TTD_S, _api.LAST_SEGMENT_S)
REPEAT_S = 10         # the same danger interrupts at most once per 10 s (let the rescue work)


HOSTILE = ("critical_health", "breath", "enderman", "hostiles")
DANGERS = hazard.KINDS + HOSTILE


def danger(state, hostiles_within=None, breath_within=None, enderman_after_us=None, time_to_die=None,
           buried=False, fallen=0.0):
    """Pure: the danger kind to interrupt for (one of DANGERS), or None. The environment first (`hazard.kind`), then
    the hostile kinds. `hostiles_within(r)` → nearest hostile distance or None; it is only called when health is low
    (entity queries cost more than a state read)."""
    if state.get("dead") or state.get("control", {}).get("paused"):
        return None
    env = hazard.kind(state, buried=buried, fallen=fallen)
    if env is not None:
        return env
    hp = state.get("health", 20)
    # One breath or head butt in the End takes 10+ hp, so 4 is far too late there — and 10 still leaves no room for
    # the hit that is already on its way.
    if hp <= (12 if state.get("dimension") == "minecraft:the_end" else 4):
        return "critical_health"
    # Dragon breath burns the floor we stand on and takes ~10 hp a second: being in it is an emergency at any health.
    if breath_within is not None and state.get("dimension") == "minecraft:the_end" and breath_within(8):
        return "breath"
    # Endermen are their own signal, never mixed into "hostiles": a provoked one follows through teleports and the
    # answer is water or cover, not a fight.
    if enderman_after_us is not None and enderman_after_us(12):
        return "enderman"
    # A fight is supposed to have hostiles close: while attacking, only critical health (above) interrupts. A blaze
    # fight was stopped at 10 hp "hurt with hostiles close" (bench 05:36).
    fighting = ((state.get("control") or {}).get("task") or {}).get("type") == "attack"
    if hp <= 10 and hostiles_within is not None and not fighting:
        d = hostiles_within(8)
        if d is not None and d <= 6:
            return "hostiles"
    # The model's version of the same rule: at the current pressure (threat.pressure — a skeleton's arrows count
    # from fifteen blocks, a zombie's reach from three), how long until dead? Close enough → stop and let the
    # threat layer answer. Health alone missed every death by arrows.
    if time_to_die is not None and not fighting:
        t = time_to_die()
        # Only what would kill us before the planner next decides is worth interrupting for.
        if t is not None and t <= interrupt_within_s():
            return "hostiles"
    return None


def clutch_needed(fallen, gap, state, has_water_bucket):
    """Pure: place water under us now? Falling (not on ground, not in water) for 5+ blocks already and the ground
    within 2–5 blocks below (placing too early wastes it, too late does nothing)."""
    if not has_water_bucket or state.get("onGround") or state.get("inWater") or state.get("inLava"):
        return False
    return fallen >= 5 and gap is not None and 2 <= gap <= 5


def _running_skill():
    from .skill import HEARTBEAT
    try:
        with open(HEARTBEAT) as f:
            t, name = f.read().split()[:2]
        return name if time.time() - float(t) < 5 else None
    except (OSError, ValueError):
        return None


def _eating():
    """Eating is running: nothing may cut in. Every bite in the dragon bench was cancelled by the next task and the
    log filled with "no bite" while health went to zero."""
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
        """How close the nearest thing that can hurt us is, from THIS tick's reading.

        It used to take a reading of its own. Two reads a tick is two worlds a tick, and the danger check could
        disagree with the answer chosen a millisecond later about whether anything was there at all.
        """
        try:
            rows, _ids = threats_seen()
            here = LAST_HERE
            near = [math.dist(here, row[0]) for row in rows] if here else []
            near = [d for d in near if d <= radius]
        except Exception:
            return None
        return min(near, default=None)

    def _time_to_die(self, s):
        """Seconds to death at the current threat pressure, or None. At most once a second: entity queries cost
        more than a state read, and one second is inside the interrupt threshold anyway."""
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
        """One read of what is near, shared by everything that asks this tick.

        `note_threats` stamps the rows with when they were taken (`seen_at`), so a decision made from them can say
        which world it describes — and a decision made from rows nobody refreshed can be told apart from a
        decision that the world was quiet.
        """
        global LAST_HERE
        try:
            near = api.get("/entities?radius=24").get("entities", []) or []
        except (api.McError, KeyError):
            return None
        note_hazards(near)
        LAST_HERE = (s["x"], s["y"], s["z"])
        return note_threats(near, time.time(), here=LAST_HERE)

    def _answer_threats(self, state):
        """One look at the world, one outcome recorded — an answer, or a named reason there was none.

        Every tick writes exactly one entry (`observe`). Silence used to be indistinguishable from "nothing was
        worth answering": a bench read fourteen empty cells and could not tell a model that chose to carry on
        from a layer that never looked.
        """
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
            # Nothing there, or nothing fresh: the tick read the world itself a moment ago, so "no rows" now means
            # the world was quiet — which is an answer, not a blind spot. `stale` can only happen if the read
            # failed, and that is the one case worth counting as blindness.
            return observe(now, "stale" if THREAT_ROWS else "quiet", seen_at=seen_at())
        try:
            state = dict(state, field=ground(state), **kit(state.get("selected", "") + str(state.get("screen"))))
        except Exception:
            pass
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
        """A dragon breath cloud within `radius`. Only asked in the End, and at most every second: entity queries
        cost more than a state read."""
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
        """An enderman that is actually provoked within `radius` (mod ≥0.1.33 reports `angry`). Same 1 s cache as the
        breath check."""
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
            # Look at the world FIRST, once, and hand that one reading to everything below. The entity read used
            # to happen at most once a second inside `_time_to_die` while the answering ran at every tick, so the
            # rows were either absent or seconds old: a bench window of eight seconds took forty looks and found
            # rows in two of them. One read per tick, one timestamp, one answer.
            self._look(s)
            try:
                self._answer_threats(s)
            except Exception as e:
                # This thread is the only thing watching for lava, drowning and mobs: an answer that fails must
                # never take the watcher with it. It died once here (BodyContested) and the agent was beaten to
                # death with a perfectly good threat model and nobody reading it.
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
                    # The message (INTERRUPT) is ours to set; the command (/stop) goes through the one exit.
                    arbiter.BODY.preempt("safety", lambda: api.post("/stop"), f"claude: {why}")
                except Exception:
                    pass
                api.log(f"!! perception: interrupt requested by Claude ({why})")
                continue
            # A fight skill handles "hurt with hostiles close" itself (retreat, eat, shield): only the life-or-death
            # reasons interrupt it. Picking up or shielding inside a blaze fight got interrupted at 9 hp (bench 06:20).
            if _eating():
                continue        # a bite takes ~1.6 s and is what saves us: never interrupt it
            # A fight skill deals with breath and endermen itself (pit, water, escape line): only give it the
            # life-or-death reasons, or every bomb window is interrupted before it starts.
            # api.SOFT is set for the whole run of a soft skill; the heartbeat can still name a nested one (eat).
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
                # Stopping the body is not an answer. While this was a rule of its own, the interrupt fired on one
                # number (time to die) and the answer was chosen on another (what a column saves), the two
                # disagreed, and the agent spent whole sessions having every task cut short by a threat no layer
                # ever did anything about. What may stop the work is the layer that is about to answer it.
                continue
            self.last[reason] = now
            if api.SOFT:
                api.INTERRUPT = reason      # soft skill: message only, no /stop — the skill takes cover itself
                # A soft skill reads the reason and takes cover itself. Cancelling its task instead cost two travels
                # mid-dig and left the player stranded on the surface ("the hole's rim is not reachable").
                api.log(f"!! perception: {reason} → handed to the running skill")
                continue
            try:
                arbiter.BODY.preempt("safety", lambda: api.post("/stop"), reason)
            except Exception:
                pass
            api.log(f"!! perception: {reason} → interrupting the current task")


LAST_HERE = None       # where we stood when the rows were read: the reading and the position are one observation
OUTCOMES = ("answered", "refused", "nothing_pays", "quiet", "stale", "repeat", "eating", "soft", "unwired")
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
    """Is the layer that answers threats actually running? A bench that does not ask this measures nothing and
    says nothing: the body was never going to move."""
    return fight_loop.wired() and any(t.name == "perception" and t.is_alive()
                                      for t in threading.enumerate())
GRID, GRID_AT, GRID_AT_POS = None, 0.0, None
GRID_R = 8
GRID_TTL_S = 2.0
_KIT, _KIT_SIG = {}, None


def ground(state, now=None, radius=GRID_R):
    """The walkable field around us, re-read at most every GRID_TTL_S and only when we have moved."""
    global GRID, GRID_AT, GRID_AT_POS
    from . import field as _field
    from .world import Region
    now = now if now is not None else time.time()
    here = tuple(int(math.floor(state[k])) for k in ("x", "y", "z"))
    if GRID is not None and now - GRID_AT < GRID_TTL_S and GRID_AT_POS == here:
        return GRID
    try:
        lo = tuple(here[i] - radius for i in range(3))
        hi = tuple(here[i] + radius for i in range(3))
        region = Region(lo, hi)
    except Exception:
        return GRID
    GRID = _field.from_region(region, here, radius)
    GRID_AT, GRID_AT_POS = now, here
    return GRID


def kit(signature):
    """What we are carrying, re-read only when the inventory signature changes: this runs at 5 Hz."""
    global _KIT, _KIT_SIG
    if signature == _KIT_SIG and _KIT:
        return _KIT
    from .world import Inventory
    inv = Inventory()
    _KIT = {"sword_tier": max((t for t, d, _ in inv.tools("sword") if d >= 1), default=0),
            "shield": inv.offhand() == "minecraft:shield",
            "food_items": sum(inv.count(f) for f in ("minecraft:cooked_beef", "minecraft:cooked_porkchop",
                                                     "minecraft:bread", "minecraft:cooked_mutton")),
            "blocks": inv.count("building")}
    _KIT_SIG = signature
    return _KIT


def start():
    w = Watcher()
    w.start()
    return w
