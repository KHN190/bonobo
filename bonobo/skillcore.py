"""Primitives every skill module shares: the context, positions, placing, pickup filters. Kept small and free of skill logic so a skill module's readiness hash doesn't change with unrelated skills."""

import math
import time

from . import api, beliefs
from .api import McError, NotAvailable
from .bag import pickup_whitelist
from .data import bare
from .world import Inventory, Region, add, feet  # noqa: F401  (feet: read here by the skills)

def _collect_only(wanted):
    """{"only": [...]} for mine/collect tasks when the bag is nearly full, else {}."""
    only = pickup_whitelist(Inventory().used_slots(), wanted)
    return {"only": only} if only else {}

def mine_cell(policy, cell, wanted=(), collect=True, require_drops=False, wait=30):
    """Break one block, never one of ours."""

    cell = tuple(cell)
    if cell in getattr(policy, "protected", ()):  
        raise NotAvailable(f"{cell} is part of one of our own structures")
    started = time.time()
    out = api.run({"type": "mine", "x": cell[0], "y": cell[1], "z": cell[2], "collect": collect,
                   **_collect_only(list(wanted)), "requireDrops": require_drops}, wait=wait, awaits="one cell through the door that keeps our builds (callers chain mine_task)")
    _note_break(started)
    return out

def _note_break(started):
    """How long breaking ONE block actually took, against what the model believes it takes."""

    took = time.time() - started
    if not 0.05 <= took <= 30.0:
        return None                      # a queued task, a stall, an interrupted break: not a measurement
    try:
        has = bool(Inventory().tools("pickaxe"))
    except (McError, OSError) as err:
        return api.swallowed("skillcore._note_break", err)
    return beliefs.note("tools.mine_time_stone" if has else "tools.mine_time_no_pickaxe", took, where="mine_cell")

class StationMissing(McError):
    """A station the plan counted on is not there (taken, broken, never placed): not a failure of the step but a changed world — the plan is repaired with the station as a need again (craft and place it, or further up)."""

    def __init__(self, block):
        super().__init__(f"no {block.split(':')[-1]} nearby or carried")
        self.block = block

class ToolMissing(McError):
    def __init__(self, kind, tier):
        super().__init__(f"need a tier-{tier} {kind}")
        self.kind, self.tier = kind, tier

_BAN_COUNTS = {}
BAN_MAX_S = 600          # the longest any cell stays banned, however often it failed

def banned(blacklist, pos, now=None):
    """Pure given `now`: is `pos` (a cell, or (entity id, 0, 0)) banned in `blacklist` ({key: expiry}) — the one reading of a ban, for the skills (Context."""

    exp = blacklist.get(tuple(pos))
    return exp is not None and exp > (time.time() if now is None else now)

class Context:
    """What skills need from the brain: memory, movement policy, target blacklist."""

    def __init__(self, memory, policy, dimension, blacklist=None, prices=None):
        self.mem = memory
        self.policy = policy
        self.dimension = dimension
        # What a unit of each token would cost to get another way, this round (`solve.reach_cost`). Injected, so a
        # skill can ask "is this worth carrying" in seconds without importing the planner.
        self._prices = prices
        # position or (entity id, 0, 0) -> expiry time. Owned by the brain so bans outlive one round.
        self.blacklist = blacklist if blacklist is not None else {}
        self.ban_counts = _BAN_COUNTS      # how often each cell was banned this session, shared like the blacklist

    def prices(self):
        """{token: seconds per unit}, or {} when nobody handed any over (tests, replays)."""
        got = self._prices() if callable(self._prices) else self._prices
        return got or {}

    def blocked(self, pos):
        return banned(self.blacklist, pos)

    def ban(self, pos, seconds=600):
        """Blacklist a cell after a FAILURE there (never after an interruption: nothing was learned about the place)."""

        key = tuple(pos)
        count = self.ban_counts.get(key, 0) + 1
        self.ban_counts[key] = count
        self.blacklist[key] = time.time() + min(seconds * (2 ** (count - 1)), BAN_MAX_S)

SETTLE_POLL_S = 0.25

def settle(read, ok, timeout=3.0, stable_s=0.5, soft=False, poll=SETTLE_POLL_S, clock=time.time, sleep=time.sleep):
    """Poll `read()` until `ok(value)` has held for `stable_s`, or `timeout` runs out."""

    from . import tape
    began = clock()
    value = read()
    seq = [(0.0, value)]
    held_since = began if ok(value) else None
    try:
        while True:
            now = clock()
            if held_since is not None and now - held_since >= stable_s:
                return value
            if now - began >= timeout:
                return value
            api.check_interrupt(began, soft)
            sleep(poll)
            value = read()
            seq.append((clock() - began, value))
            if ok(value):
                held_since = held_since if held_since is not None else clock()
            else:
                held_since = None
    finally:
        tape.reading(seq, held_since is not None)     # the numbers the verdict came from, for replaying it

def gained(read, before, timeout=3.0, stable_s=0.5):
    """What `read()` says once it has settled above `before` (a count after picking up, crafting, taking out), or what it says when time runs out."""

    return settle(read, lambda v: v > before, timeout, stable_s)

def lost(read, before, timeout=3.0, stable_s=0.5):
    """The mirror of `gained`: a count that should have gone DOWN (thrown away, stored, loaded into a furnace)."""
    return settle(read, lambda v: v < before, timeout, stable_s)

def confirmed(readings, stable_s=0.5):
    """Pure: [(seconds, reading)] → did the reading (a bool, or a /state dict's `dead`) hold from the first one for `stable_s`?"""

    if not readings:
        return False
    t0 = readings[0][0]
    for t, v in readings:
        if not (v.get("dead") if isinstance(v, dict) else v):
            return False
        if t - t0 >= stable_s:
            return True
    return False

def dead(state=None, readings=None):
    """Is the body really dead?"""

    if readings is not None:
        return confirmed(readings)
    first = (state if state is not None else api.get("/state")).get("dead")
    if not first:
        return False
    return bool(settle(lambda: api.get("/state").get("dead"), bool, timeout=1.5, stable_s=0.5, soft=True))

def body_state(ctx, region=None, **extra):
    """The state dict a skill's `commands` are built from: where the body is, what it carries, what it may not touch, and the blocks around it."""

    s = api.get("/state")
    return dict({"state": s, "feet": (s["blockX"], s["blockY"], s["blockZ"]), "inv": Inventory(),
                 "protected": set(getattr(getattr(ctx, "policy", None), "protected", ()) or ()), "region": region},
                **extra)

def carried_total():
    """Every item in the bag, counted: what a pickup or a loot raises even when it only tops up existing stacks."""
    return sum(int(s.get("count", 1)) for s in Inventory().slots)

def head_underwater(s=None):
    """The eyes are in a water block (swimming at the surface with the head out doesn't count)."""
    s = s or api.get("/state")
    if not s["inWater"]:
        return False
    eye = (s["blockX"], math.floor(s["y"] + 1.62), s["blockZ"])
    return Region(eye, eye).name(eye) == "water"

def head_buried(s=None):
    """The eyes are inside a solid block (falling sand/gravel, a placed block): suffocating."""
    s = s or api.get("/state")
    eye = (s["blockX"], math.floor(s["y"] + 1.62), s["blockZ"])
    r = Region(eye, eye)
    return r.solid(eye) and not r.name(eye).endswith(("_slab", "_stairs", "snow", "_carpet"))

def close_screen():
    s = api.get("/state")
    if s["screen"] not in ("none", "class_433"):
        api.post("/close")

def spot_region(state, reach=4):
    """The box `free_spots` reads around the body in `state` (/state)."""
    fx, fy, fz = state["blockX"], state["blockY"], state["blockZ"]
    return Region((fx - reach, fy - 3, fz - reach), (fx + reach, fy + 4, fz + reach))

def free_spots(region, state, block_under=True, reach=4, avoid=(), limit=5):
    """Pure over `region` (anything with name/solid/hazard) and `state` (/state): air cells with a solid floor within reach, clear of the body, best first — same height as us, open above (not a wall nook or under a roof), about two blocks away."""

    s = state
    fx, fy, fz = s["blockX"], s["blockY"], s["blockZ"]
    scored = []
    for dx in range(-reach, reach + 1):
        for dz in range(-reach, reach + 1):
            if not 1 <= max(abs(dx), abs(dz)) <= reach:
                continue
            for dy in (0, 1, -1, 2, -2):
                p = (fx + dx, fy + dy, fz + dz)
                if p in avoid or region.name(p) != "air":
                    continue
                if block_under and (not region.solid(add(p, (0, -1, 0))) or region.hazard(add(p, (0, -1, 0)))):
                    continue
                if p[1] in (fy, fy + 1) and abs(p[0] + 0.5 - s["x"]) < 0.8 and abs(p[2] + 0.5 - s["z"]) < 0.8:
                    continue
                enclosed = region.solid(add(p, (0, 1, 0)))
                scored.append(((abs(dy), enclosed, abs(max(abs(dx), abs(dz)) - 2)), p))
    scored.sort()
    return [p for _, p in scored[:limit]]

def free_spot(region, state, block_under=True, reach=4, avoid=()):
    spots = free_spots(region, state, block_under, reach, avoid, limit=1)
    return spots[0] if spots else None

def free_spots_here(block_under=True, reach=4, avoid=(), limit=5):
    """`free_spots` around the body now: one /state read and one region read."""
    s = api.get("/state")
    return free_spots(spot_region(s, reach), s, block_under, reach, avoid, limit)

def place(item, pos):
    r = api.run({"type": "place", "item": item, "x": pos[0], "y": pos[1], "z": pos[2]}, wait=60, awaits="one block, placed or not (callers chain place tasks)")
    if r["status"] != "succeeded":
        raise McError(f"placing {bare(item)} failed: {r['message']}")

def snapshot(center, half=2, down=1, up=2):
    lo = (center[0] - half, center[1] - down, center[2] - half)
    hi = (center[0] + half, center[1] + up, center[2] + half)
    region = Region(lo, hi)
    return {"lo": list(lo), "hi": list(hi),
            "blocks": {f"{x},{y},{z}": n for (x, y, z), n in region.blocks.items() if region.solid((x, y, z))}}
