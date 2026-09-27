"""L0: what the ENVIRONMENT does to the body — lava, fire, water, falling, being buried. Hostiles are not here: they
are the fight's (fight_loop.py).

Two halves, one kind name between them:
  kind(state, ...)   pure: which hazard is on the body now, or None. The perception thread asks it every tick and,
                     when one is, preempts at SAFETY with a /stop (the running work is abandoned; the plan is void).
  handle(...)        the rescue, run by the brain at the top of its loop: the one skill that takes the body out of
                     that hazard, with the rescue itself protected from the interrupt it answers (`api.MODE`).
A hazard with no rescue of our own (a fall: the jar's WaterClutch owns the landing) only stops the work.
"""
import time

from . import api
from .beliefs import CONFIG as _CONFIG
from .api import log
from .skillcore import head_buried

# The rescue skills (find_air, unbury), lent by skills.py at import: this module detects and dispatches, and never
# imports the skill library — or everything that reads a hazard (perception, nav) would drag all of it in.
SKILLS = {}

KINDS = ("lava", "burning", "drowning", "suffocating", "falling")

TICKS_PER_S = 20.0
REFLEX_SLACK_S = 2.0      # between tasks: surface while there is still room, rather than at the last moment
BURNING_HP = 8            # a blaze fight sets you on fire every few seconds: burning only counts once it really hurt
FALL_BLOCKS = 4.0         # falling this far with nothing under us: the work under way is moot
BURIED_EVERY_S = 1.0      # suffocation needs a block read: at most once a second
_W = _CONFIG["water"]


def drowning_in(state):
    """Pure: seconds of slack before we must leave the water, or inf when not submerged. Zero or less = leave now.

    Air is a clock, not a threshold: what matters is whether the breath left covers getting out plus the time it
    takes us to notice and start. Standing on the bottom of a lake, which is where digging puts you, is not safe.
    """
    if not state.get("inWater"):
        return float("inf")
    air_s = state.get("air", 300) / TICKS_PER_S
    return round(air_s - _W["surface_s"] - _W["reaction_s"], 2)


def drowning(state):
    """Pure: leave the water now? Either clock says so — the computed one, or a hard floor under it."""
    if not state.get("inWater"):
        return False
    return drowning_in(state) <= 0.0 or state.get("air", 300) < _W["air_floor"]


def falling(state, fallen):
    """Pure: in the air, not in water or lava, and already `fallen` blocks below where the fall began."""
    if state.get("onGround") or state.get("inWater") or state.get("inLava"):
        return False
    return fallen >= FALL_BLOCKS


def kind(state, buried=False, fallen=0.0):
    """Pure: the environmental hazard on the body now (one of KINDS), or None. `buried` is whether the eyes are inside
    a solid block (a world read the caller makes); `fallen` is how far the body has dropped since leaving the ground.
    Dead or paused is the caller's to rule out (perception.danger; the brain waits for both before a round)."""
    if state.get("inLava"):
        return "lava"
    if state.get("onFire") and state.get("health", 20) <= BURNING_HP:
        return "burning"
    if drowning(state):
        return "drowning"
    if buried:
        return "suffocating"
    if falling(state, fallen):
        return "falling"
    return None


class Watch:
    """What the per-tick reading alone cannot say: how far we have fallen, and — at most once a second — whether the
    head is inside a block. Owned by the perception thread."""

    def __init__(self):
        self.fall_top = None
        self._buried_t, self._buried = 0.0, False

    def fallen(self, state):
        y = float(state.get("y", 0.0))
        if state.get("onGround") or state.get("inWater") or state.get("inLava"):
            self.fall_top = None
            return 0.0
        self.fall_top = y if self.fall_top is None else max(self.fall_top, y)
        return self.fall_top - y

    def buried(self, state, now=None):
        now = time.time() if now is None else now
        if now - self._buried_t < BURIED_EVERY_S:
            return self._buried
        self._buried_t = now
        try:
            self._buried = bool(head_buried(state))
        except Exception:
            self._buried = False
        return self._buried

    def kind(self, state):
        return kind(state, buried=self.buried(state), fallen=self.fallen(state))


def _leave_lava(ctx, s):
    api.post("/stop")
    api.run({"type": "goto", "x": s["blockX"], "y": s["blockY"] + 3, "z": s["blockZ"], "range": 3,
             "partial": True}, wait=20)


def _surface(ctx, s):
    """Drowning (`due` decides when, once): find_air, the one way out of water — to the nearest dry cell to stand
    on, a block at the surface, or through the cap. Swimming straight up six rose to the top of a shaft's column
    and sank back under."""
    api.post("/stop")
    SKILLS["find_air"](ctx)


def _unbury(ctx, s):
    SKILLS["unbury"](ctx)


def _extinguish(ctx, s):
    """On fire and hurting: put water on it — pour the bucket at the feet and take it back — else step into water
    within 8 blocks. Without either the fire burns out on its own; the rescue says so instead of standing still."""
    from .world import Inventory, find
    api.post("/stop")
    x, y, z = s["blockX"], s["blockY"], s["blockZ"]
    if Inventory().count("minecraft:water_bucket"):
        api.run({"type": "use_item", "item": "minecraft:water_bucket", "x": x + 0.5, "y": y, "z": z + 0.5}, wait=5)
        api.run({"type": "use_item", "item": "minecraft:bucket", "x": x + 0.5, "y": y, "z": z + 0.5}, wait=5)
        return
    water = find(["water"], radius=8, limit=1)
    if not water:
        raise api.NotAvailable("on fire with no water to put it out")
    w = water[0]
    api.run({"type": "goto", "x": w["x"], "y": w["y"], "z": w["z"], "range": 0.5, "partial": True}, wait=10)


RESCUE = {"lava": _leave_lava, "drowning": _surface, "suffocating": _unbury, "burning": _extinguish}
# Hazards answered by stopping the work and nothing more: a fall is over before a round could act, and the landing
# belongs to the jar's WaterClutch (and to perception's clutch, `perception.clutch_needed`).
STOP_ONLY = ("falling",)
assert set(RESCUE) | set(STOP_ONLY) == set(KINDS), "every hazard kind is rescued or declared stop-only"


def due(state, buried=None):
    """The hazard the brain must answer before anything else this round, or None. Between tasks there is more room
    than inside one: drowning counts from REFLEX_SLACK_S of slack, not zero."""
    if state.get("inWater") and drowning_in(state) <= REFLEX_SLACK_S:
        return "drowning"
    if buried is None:
        try:
            buried = head_buried(state)
        except api.McError:
            buried = False
    k = kind(state, buried=buried)
    return k if k in RESCUE else None


def handle(ctx, state, attempt, ready):
    """Run the rescue for the hazard on the body, if there is one. Returns True when it used the round: the plan
    that was running is void, and the next round decides from wherever the rescue left us.

    `attempt(name, fn)` and `ready(name)` are the brain's failure policy, so a rescue that fails is cooled like
    anything else — by its cause, at this place."""
    k = due(state)
    if k is None or not ready(f"rescue {k}"):
        return False
    log(f"L0: {k} → rescue")
    api.INTERRUPT = None
    api.MODE = "survival"      # the perception thread does not interrupt the rescue it asked for
    try:
        attempt(f"rescue {k}", lambda: RESCUE[k](ctx, state))
    finally:
        api.MODE = "normal"
    return True

