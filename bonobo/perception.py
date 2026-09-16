"""Perception thread: watches /state ~5 times a second and interrupts whatever long task is running when life is at
risk. Reflexes used to run only between tasks and chain segments, so a 5-minute hunt or tunnel kept going while
the agent burned or was beaten down. The mod still owns per-tick nets (LavaGuard, forced surfacing); this is the
layer between those nets and the brain: stop the task, let the survival mode (brain.survival) act next round.

Only interrupts — never acts on the game itself beyond /stop, never while the player holds control, and never
while the brain is already running a survival rescue (api.MODE == "survival")."""
import threading
import time

from . import api, paths

POLL_S = 0.2
# The operator's interrupt (mc.py interrupt): end the running skill so an override directive runs next round. /stop
# alone only cancels the current mod task; a skill (a hunt exploring leg after leg) keeps going.
FLAG = paths.data("interrupt")
PAUSED = False         # the scenario bench sets this while commands rebuild the world (no clutch on a setup fall)
REPEAT_S = 10         # the same danger interrupts at most once per 10 s (let the rescue work)


def danger(state, hostiles_within=None, breath_within=None, enderman_after_us=None):
    """Pure: the reason to interrupt now, or None. `hostiles_within(r)` → nearest hostile distance or None; it is only
    called when health is low (entity queries cost more than a state read)."""
    if state.get("dead") or state.get("control", {}).get("paused"):
        return None
    hp, food = state.get("health", 20), state.get("food", 20)
    if state.get("inLava"):
        return "in lava"
    # A blaze fight sets you on fire every few seconds: interrupting at 14 hp made fight_blaze impossible (bench
    # 04:06). Burning only interrupts when it has really hurt.
    if state.get("onFire") and hp <= 8:
        return "burning"
    if state.get("inWater") and state.get("air", 300) < 120 and not state.get("onGround", False):
        return "running out of air"
    # One breath or head butt in the End takes 10+ hp, so 4 is far too late there — and 10 still leaves no room for
    # the hit that is already on its way.
    if hp <= (12 if state.get("dimension") == "minecraft:the_end" else 4):
        return "critical health"
    # Dragon breath burns the floor we stand on and takes ~10 hp a second: being in it is an emergency at any health.
    if breath_within is not None and state.get("dimension") == "minecraft:the_end" and breath_within(8):
        return "dragon breath close"
    # Endermen are their own signal, never mixed into "hostiles": a provoked one follows through teleports and the
    # answer is water or cover, not a fight.
    if enderman_after_us is not None and enderman_after_us(12):
        return "enderman after us"
    # A fight is supposed to have hostiles close: while attacking, only critical health (above) interrupts. A blaze
    # fight was stopped at 10 hp "hurt with hostiles close" (bench 05:36).
    fighting = ((state.get("control") or {}).get("task") or {}).get("type") == "attack"
    if hp <= 10 and hostiles_within is not None and not fighting:
        d = hostiles_within(8)
        if d is not None and d <= 6:
            return "hurt with hostiles close"
    if food <= 2:
        return "starving"
    return None


def clutch_needed(fallen, gap, state, has_water_bucket):
    """Pure: place water under us now? Falling (not on ground, not in water) for 5+ blocks already and the ground
    within 2–5 blocks below (placing too early wastes it, too late does nothing)."""
    if not has_water_bucket or state.get("onGround") or state.get("inWater") or state.get("inLava"):
        return False
    return fallen >= 5 and gap is not None and 2 <= gap <= 5


def ground_gap(state, max_depth=24):
    """Blocks of air below the feet down to the first solid block (None if deeper than max_depth)."""
    from .world import Region
    x, y, z = state["blockX"], state["blockY"], state["blockZ"]
    r = Region((x, y - max_depth, z), (x, y, z))
    for k in range(1, max_depth + 1):
        if r.solid((x, y - k, z)):
            return k - 1
    return None


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
        self.stopped = False
        self.fall_top = None     # highest y since leaving the ground

    def _hostiles_within(self, radius):
        try:
            from .brain import is_threat
            near = [e["distance"] for e in api.get(f"/entities?radius={radius}").get("entities", []) if is_threat(e)]
        except api.McError:
            return None
        return min(near, default=None)

    def _breath_within(self, radius):
        """A dragon breath cloud within `radius`. Only asked in the End, and at most every second: entity queries
        cost more than a state read."""
        now = time.time()
        if now - getattr(self, "_breath_t", 0) < 1.0:
            return getattr(self, "_breath_seen", False)
        self._breath_t = now
        try:
            near = api.get(f"/entities?radius={int(radius)}").get("entities", [])
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
        except api.McError:
            return False
        self._ender_seen = any(e["type"] == "minecraft:enderman" and e.get("angry") for e in near)
        return self._ender_seen

    def run(self):
        while not self.stopped:
            time.sleep(POLL_S)
            if api.MODE == "survival" or PAUSED:
                continue
            try:
                s = api.get("/state")
            except Exception:   # game restarting, network hiccup: the main loop handles those
                continue
            import os
            if os.path.exists(FLAG):
                try:
                    why = open(FLAG).read().strip() or "Claude asked"
                    os.remove(FLAG)
                except OSError:
                    why = "Claude asked"
                api.INTERRUPT = f"claude: {why}"
                try:
                    api.post("/stop")
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
                            None if fighting else self._enderman_after_us)
            now = time.time()
            if reason is None or now - self.last.get(reason, 0) < REPEAT_S:
                continue
            if not (s.get("control") or {}).get("task"):
                continue        # nothing running to interrupt; the next round's survival check will see it
            self.last[reason] = now
            api.INTERRUPT = reason
            if api.SOFT:
                # A soft skill reads the reason and takes cover itself. Cancelling its task instead cost two travels
                # mid-dig and left the player stranded on the surface ("the hole's rim is not reachable").
                api.log(f"!! perception: {reason} → handed to the running skill")
                continue
            try:
                api.post("/stop")
            except Exception:
                pass
            api.log(f"!! perception: {reason} → interrupting the current task")


def start():
    w = Watcher()
    w.start()
    return w
