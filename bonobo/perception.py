"""Perception thread: watches /state ~5 times a second and interrupts whatever long task is running when life is at
risk. Reflexes used to run only between tasks and chain segments, so a 5-minute hunt or tunnel kept going while
the agent burned or was beaten down. The mod still owns per-tick nets (LavaGuard, forced surfacing); this is the
layer between those nets and the brain: stop the task, let the survival mode (brain.survival) act next round.

Only interrupts — never acts on the game itself beyond /stop, never while the player holds control, and never
while the brain is already running a survival rescue (api.MODE == "survival")."""
import math
import threading
import time

from . import api, arbiter, paths
from .survival import CONFIG as _CONFIG
from .threat import ENGAGE as _ENGAGE

POLL_S = 0.2
FIGHT_POLL_S = 0.1     # while arbiter.BODY is engaged: a window is 0.4 s at worst, a 0.2 s poll sees half of it
# The operator's interrupt (mc.py interrupt): end the running skill so an override directive runs next round. /stop
# alone only cancels the current mod task; a skill (a hunt exploring leg after leg) keeps going.
FLAG = paths.data("interrupt")
# The hazard set, maintained here because this is the only loop that already looks at the world often enough, and
# consulted by movement (nav.go_to). One authority: before this, every call site either invented its own threat
# logic or had none, which is how a retreat walked into the dragon while escaping an enderman.
# What health is ACTUALLY draining at, from the state reads this loop already makes. The threat model predicts a
# rate from reach and dps; a skeleton that aims well beats the prediction, and the agent died to arrows while the
# model said it had ten seconds. Decisions take the larger of the two: a model may be wrong, a falling health bar
# is not.
HURT_RATE = 0.0       # health per second, measured
_HP_SEEN = None       # (health, when) from the previous read

HAZARDS = []          # [(point, radius)], newest perception round wins
HAZARDS_AT = 0.0      # when it was refreshed; stale hazards are worse than none
# The threat rows the planner reasons about, kept by the one loop that already reads entities often enough. The
# planner must NOT read the world itself: an entity query inside candidates() costs a request per round and, worse,
# makes recorded decisions unreplayable (every golden round then misses /entities). One perception authority,
# everyone else consumes it.
THREAT_ROWS, THREAT_IDS, THREAT_AT = [], [], 0.0


def note_hazards(near, now=None):
    """Record what can hurt us right now. Pure apart from the clock; called from the perception round."""
    global HAZARDS, HAZARDS_AT
    import time as _t
    from . import combat_model
    HAZARDS = combat_model.hazard_points(near or [])
    HAZARDS_AT = now if now is not None else _t.time()
    return HAZARDS


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


def pressure_now(here, rows, prot=0.0, ground=None):
    """The pressure we are actually under: the model's rate or the measured one, whichever is worse.

    The one place the two twins meet. Both are the same quantity — one estimated from what is in reach, one read
    off the health bar — and every caller that needs "what is happening to us now" asks here rather than picking
    a side: a skeleton that aims well outdoes the model, and that difference was a death.
    """
    from . import estimate
    return max(estimate.pressure_hp_s(here, rows, prot, ground=ground), hurt_rate())


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


def hazards(max_age_s=3.0, now=None):
    """The current hazard set, or empty when perception has not looked recently enough to be trusted."""
    import time as _t
    if not HAZARDS or (now or _t.time()) - HAZARDS_AT > max_age_s:
        return []
    return HAZARDS


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


DANGERS = ("lava", "burning", "drowning", "critical_health", "breath", "enderman", "hostiles", "starving")


TICKS_PER_S = 20.0
REFLEX_SLACK_S = 2.0      # between tasks: surface while there is still room, rather than at the last moment
_W = _CONFIG["water"]


def drowning_in(state):
    """Pure: seconds of slack before we must leave the water, or inf when not submerged. Zero or less = leave now.

    Air is a clock, not a threshold: what matters is whether the breath left covers getting out plus the time it
    takes us to notice and start. The old rule compared air to a constant AND required `not onGround` — so standing
    on the bottom of a lake, which is where digging puts you, read as safe all the way to zero.
    """
    if not state.get("inWater"):
        return float("inf")
    air_s = state.get("air", 300) / TICKS_PER_S
    return round(air_s - _W["surface_s"] - _W["reaction_s"], 2)


def drowning(state):
    """Pure: leave the water now? Either clock says so — the computed one, or a hard floor under it.

    OR, not AND, and on purpose. The clock is the better rule but it rests on `air` meaning what we think it means
    and on `surface_s` being roughly right; the floor costs an early surfacing when they are not. A death is not a
    thing to be clever about twice.
    """
    if not state.get("inWater"):
        return False
    return drowning_in(state) <= 0.0 or state.get("air", 300) < _W["air_floor"]


def danger(state, hostiles_within=None, breath_within=None, enderman_after_us=None, time_to_die=None):
    """Pure: the danger kind to interrupt for (one of DANGERS), or None. `hostiles_within(r)` → nearest hostile distance or None; it is only
    called when health is low (entity queries cost more than a state read)."""
    if state.get("dead") or state.get("control", {}).get("paused"):
        return None
    hp, food = state.get("health", 20), state.get("food", 20)
    if state.get("inLava"):
        return "lava"
    # A blaze fight sets you on fire every few seconds: interrupting at 14 hp made fight_blaze impossible (bench
    # 04:06). Burning only interrupts when it has really hurt.
    if state.get("onFire") and hp <= 8:
        return "burning"
    if drowning(state):
        return "drowning"
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
    if food <= 2:
        return "starving"
    return None


def clutch_needed(fallen, gap, state, has_water_bucket):
    """Pure: place water under us now? Falling (not on ground, not in water) for 5+ blocks already and the ground
    within 2–5 blocks below (placing too early wastes it, too late does nothing)."""
    if not has_water_bucket or state.get("onGround") or state.get("inWater") or state.get("inLava"):
        return False
    return fallen >= 5 and gap is not None and 2 <= gap <= 5


FIGHT_SKILLS = ("fight_blaze", "fight_dragon", "slay_dragon", "build_bed_pit", "await_perch",
                "bed_bomb_window", "break_caged_crystal", "station")


def _running_skill():
    from .skill import HEARTBEAT
    try:
        with open(HEARTBEAT) as f:
            t, name = f.read().split()[:2]
        return name if time.time() - float(t) < 5 else None
    except (OSError, ValueError):
        return None


def _fighting():
    """A fight skill is running (its heartbeat is fresh)."""
    return _running_skill() in FIGHT_SKILLS


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
        self.fall_top = None     # highest y since leaving the ground

    def _hostiles_within(self, radius):
        try:
            from .threat import is_threat
            _all = api.get(f"/entities?radius={radius}").get("entities", [])
            note_hazards(_all)          # the authority is refreshed by the loop that was already looking
            near = [e["distance"] for e in _all if is_threat(e)]
        except api.McError:
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
            near = api.get("/entities?radius=24").get("entities", [])
            note_hazards(near)
            here = (s["x"], s["y"], s["z"])
            rows = note_threats(near, now, here=here)   # the planner reads these; it never queries entities itself
            if not rows:
                self._ttd = None
            else:
                from . import estimate
                prot = threat.protection(s.get("armor", 0), False)
                self._ttd = estimate.time_to_die_s(s.get("health", 20), pressure_now(here, rows, prot))
        except (api.McError, KeyError):
            self._ttd = None
        return self._ttd

    def _answer_threats(self, state):
        from . import survival
        if ANSWER is None or api.SOFT or _eating():
            return
        rows, _ids = threats_seen()
        if not rows:
            return
        try:
            state = dict(state, field=ground(state), **kit(state.get("selected", "") + str(state.get("screen"))))
        except Exception:
            pass
        sstate = survival.make_state(hp=max(1, int(state.get("health", 20))), armor=int(state.get("armor", 0)))
        price = lambda dhp: survival.hp_seconds(sstate, dhp)
        chosen = bid(state, rows, price)
        if chosen is None:
            return
        option, worth = chosen
        now = time.time()
        key = f"threat:{option.kind}"
        if now - self.last.get(key, 0) < 1.0:
            return
        self.last[key] = now
        record = {"t": round(now, 2), "kind": option.kind, "worth_s": round(worth, 1),
                  "taken": False, "refused": None, "failed": None}

        def run():
            try:
                ANSWER(option)
            except Exception as e:
                record["failed"] = f"{type(e).__name__}: {e}"
                raise

        taken, refused = arbiter.BODY.preempt("tactic", run, key, worth_s=worth, now=now, clear_first=True,
                                              release=lambda: lease_done(state, threats_seen()[0], price))
        record["taken"], record["refused"] = bool(taken), refused
        ANSWERED.append(record)
        del ANSWERED[:-ANSWERED_MAX]
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
            time.sleep(FIGHT_POLL_S if arbiter.BODY.engaged else POLL_S)
            if api.MODE == "survival" or PAUSED:
                continue
            try:
                s = api.get("/state")
            except Exception:   # game restarting, network hiccup: the main loop handles those
                continue
            note_hurt(s)
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
            fighting = _fighting() or api.SOFT
            reason = danger(s, None if fighting else self._hostiles_within,
                            None if fighting else self._breath_within,
                            None if fighting else self._enderman_after_us,
                            None if fighting else (lambda: self._time_to_die(s)))
            now = time.time()
            if reason is None or now - self.last.get(reason, 0) < REPEAT_S:
                continue
            if not (s.get("control") or {}).get("task"):
                continue        # nothing running to interrupt; the next round's survival check will see it
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


ANSWER = None
# Every answer this layer hands to the body, in order: {t, kind, worth_s, taken, failed}. One record, written at
# the only place an answer is executed, so a bench observes what really happened instead of wrapping `ANSWER` with
# a second code path of its own — which is how fourteen cells came back with an empty log and no explanation.
ANSWERED = []
ANSWERED_MAX = 500


def answered_since(mark=0):
    """Answers handed to the body after `mark` (a length taken before the stretch of interest)."""
    return list(ANSWERED[mark:])


def watching():
    """Is the layer that answers threats actually running? A bench that does not ask this measures nothing and
    says nothing: the body was never going to move."""
    return ANSWER is not None and any(t.name == "perception" and t.is_alive()
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


def wire_answer(fn):
    global ANSWER
    ANSWER = fn


HELD = None


def threat_state(state, rows, work_s=None):
    """The threat model's state vector, read off a player state and the rows the watcher last saw.

    One builder: the live bid and the bench have to ask the same question, and a bench that assembles its own
    state vector is testing its own arithmetic.
    """
    from . import threat
    from . import field as _field
    st = {"here": (state["x"], state["y"], state["z"]), "hp": float(state.get("health", 20)),
          "sword": int(state.get("sword_tier", 0)), "protection": threat.protection(state.get("armor", 0), False),
          "night": False, "blocks": int(state.get("blocks", 0)), "hazards": rows,
          "food_items": int(state.get("food_items", 0)), "shield": bool(state.get("shield")),
          "field": state.get("field") or _field.Field(), "ids": list(THREAT_IDS)}
    if work_s is not None:
        st["work_s"] = work_s
    return st


def bid(state, rows, price, work_s=None, now=None):
    from . import threat
    if not rows:
        return None
    st = threat_state(state, rows, work_s)
    global HELD
    from . import kernel
    field_model = threat.Field(st, price)
    if HELD is None:
        HELD = kernel.Held()
    horizon_now = threat.horizon_for(st)
    choice = HELD.decide(field_model, field_model.state(), now if now is not None else time.time(),
                         holds=lambda c, _s: still_worth(c, field_model, price, horizon_now))
    option = choice.action.option if choice.action is not None else None
    if option is None or option.kind == "ignore":
        return None
    worth = threat.saves(option, [a.option for a in field_model.opts], price, horizon_now)
    return (option, round(worth, 1)) if worth > 0 else None


def lease_done(state, rows, price):
    """Has answering stopped paying? The lease's release condition, and nothing else releases it.

    Blind moments are NOT an answer: the entity read is a second old, the watcher was busy, the rows aged out. A
    lease that reads "nothing visible" as "nothing to deal with" hands the body back in the middle of a fight, and
    the planner's next mine task lands on top of the answer — which is what `BodyContested` was, all along.
    """
    if not rows:
        return False
    try:
        fresh = bid(state, rows, price, now=time.time())
    except Exception:
        return False
    return fresh is None or fresh[1] <= 0


def still_worth(choice, field_model, price, horizon):
    """The assumption behind a threat answer: that it still beats carrying on. A held answer that has stopped
    paying (the sword broke, the crowd doubled) is not a commitment, it is a mistake with a timer."""
    from . import threat as _threat
    options = [a.option for a in field_model.opts]
    same = next((o for o in options if o.kind == choice.name), None)
    if same is None:
        return False
    return _threat.saves(same, options, price, horizon) > 0


def start():
    w = Watcher()
    w.start()
    return w
