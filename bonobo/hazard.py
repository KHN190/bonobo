"""L0: what the ENVIRONMENT does to the body — lava, fire, water, falling, being buried. Hostiles are not here: they are the fight's (fight_loop.py). Two halves, one kind name between them: kind(state, ...)   pure: which hazard is on the body now, or None. The perception thread asks it every tick and, when one is, preempts at SAFETY with a /stop (the running work is abandoned; the plan is void). handle(...)        the rescue, run by the brain at the top of its loop: the one skill that takes the body out of that hazard, with the rescue itself protected from the interrupt it answers (`api.MODE`). A hazard with no rescue of our own (a fall: the jar's WaterClutch owns the landing) only stops the work."""

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
    """Pure: seconds of slack before we must leave the water, or inf when not submerged."""

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
    """Pure: the environmental hazard on the body now (one of KINDS), or None."""

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
    """What the per-tick reading alone cannot say: how far we have fallen, and — at most once a second — whether the head is inside a block."""

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
    # one task: nothing to chain (the climb out is the whole answer)
    api.run({"type": "goto", "x": s["blockX"], "y": s["blockY"] + 3, "z": s["blockZ"], "range": 3,
             "partial": True}, wait=20, awaits="out of the lava: the next reading decides")

def _surface(ctx, s):
    """Drowning (`due` decides when, once): find_air, the one way out of water — to the nearest dry cell to stand on, a block at the surface, or through the cap."""

    api.post("/stop")
    SKILLS["find_air"](ctx)

def _unbury(ctx, s):
    SKILLS["unbury"](ctx)

def _extinguish(ctx, s):
    """On fire and hurting: put water on it — pour the bucket at the feet and take it back — else step into water within 8 blocks."""

    from .world import Inventory, find
    api.post("/stop")
    has_bucket = bool(Inventory().count("minecraft:water_bucket"))
    water = None if has_bucket else next(iter(find(["water"], radius=8, limit=1) or ()), None)
    # The pour and the scoop back as one chain: no reply between them is read, so none is waited for.
    api.run_chain(extinguish_commands(s, has_bucket, water), stop_on_failure=True, wait=10)

def extinguish_commands(s, has_bucket, water):
    """Pure: the tasks that put a fire on the body out — the water bucket poured at the feet and taken back, else a walk into water within reach (`water`: a /find hit or None)."""

    x, y, z = s["blockX"], s["blockY"], s["blockZ"]
    if has_bucket:
        at = {"x": x + 0.5, "y": y, "z": z + 0.5}
        return [dict({"type": "use_item", "item": "minecraft:water_bucket"}, **at),
                dict({"type": "use_item", "item": "minecraft:bucket"}, **at)]
    if water is None:
        raise api.NotAvailable("on fire with no water to put it out")
    return [{"type": "goto", "x": water["x"], "y": water["y"], "z": water["z"], "range": 0.5, "partial": True}]

RESCUE = {"lava": _leave_lava, "drowning": _surface, "suffocating": _unbury, "burning": _extinguish}
# Hazards answered by stopping the work and nothing more: a fall is over before a round could act, and the landing
# belongs to the jar's WaterClutch.
STOP_ONLY = ("falling",)
assert set(RESCUE) | set(STOP_ONLY) == set(KINDS), "every hazard kind is rescued or declared stop-only"

def due(state, buried=None):
    """The hazard the brain must answer before anything else this round, or None."""

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
    """Run the rescue for the hazard on the body, if there is one."""

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

