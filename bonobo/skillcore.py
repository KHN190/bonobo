"""Primitives every skill module shares: the context, positions, placing, pickup filters. Kept small and
free of skill logic so a skill module's readiness hash doesn't change with unrelated skills."""
import math
import time

from . import api, beliefs, knowledge as _know, lifecycle, tape
from .api import McError, NotAvailable
from .bag import pickup_whitelist
from .data import BAN_MAX_S, REACH, bare
from .game import EYE_HEIGHT
from .world import BAG_SLOTS, Inventory, Region, Versioned, cell_add, inventory_now, box, screen_slot
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .shapes import BodyState


def in_dimension(dimension):
    """A skill's `pre`: the body is in `dimension` — a skill that only works there is never offered elsewhere."""
    def check(c):
        if api.get("/state").get("dimension") != dimension:
            raise NotAvailable(f"not in {bare(dimension)}")
    return check

def game_tick():
    """The game's tick now (/state gameTime): what a look made outside a brain round is stamped with (brain wires
    it into memory.TICK_READER — memory stays below the skills)."""
    return api.get("/state").get("gameTime")

def game_time_or_none():
    """game_tick, or None when there is no game to ask (an offline test): memory's TICK_READER — memory stays below
    the api and catches nothing itself."""
    try:
        return game_tick()
    except McError as e:
        api.swallowed("skillcore.game_time_or_none", e)
        return None

def _collect_only(wanted):
    """{"only": [...]} for mine/collect tasks when the bag is nearly full, else {}."""
    only = pickup_whitelist(Inventory().used_slots(), wanted)
    return {"only": only} if only else {}

def mine_cell(policy, cell, wanted=(), collect=True, require_drops=False, wait=30):
    """Break one block, never one of ours (`policy.protected`): the one door for breaking a single cell."""
    cell = tuple(cell)
    if cell in getattr(policy, "protected", ()):  
        raise NotAvailable(f"{cell} is part of one of our own structures")
    return api.run({"type": "mine", "x": cell[0], "y": cell[1], "z": cell[2], "collect": collect,
                    **_collect_only(list(wanted)), "requireDrops": require_drops}, wait=wait, awaits="one cell through the door that keeps our builds (callers chain mine_task)")

class StationMissing(McError):
    """A station the plan counted on is gone: not the step's failure — the plan is repaired with the station as a need."""
    def __init__(self, block):
        super().__init__(f"no {block.split(':')[-1]} nearby or carried")
        self.block = block

class NeedMissing(McError):
    """A call whose hard needs (skill.needs_of) the bag does not hold: refused before it starts, "missing <need>"."""

    def __init__(self, missing):
        super().__init__("missing " + ", ".join(f"{k} {v}" for k, v in sorted(missing.items())))
        self.missing = dict(missing)

class ToolMissing(McError):
    def __init__(self, kind, tier):
        super().__init__(f"need a tier-{tier} {kind}")
        self.kind, self.tier = kind, tier

_BAN_COUNTS = {}
lifecycle.in_place(__name__, "_BAN_COUNTS")     # in place: every Context shares it; bans name the last life's cells

def banned(blacklist, pos, now=None):
    """Pure given `now`: is `pos` (a cell, or (entity id, 0, 0)) banned in `blacklist` ({key: expiry})?"""
    exp = blacklist.get(tuple(pos))
    return exp is not None and exp > (time.time() if now is None else now)

class Context:
    """What skills need from the brain: memory, movement policy, target blacklist."""

    def __init__(self, memory, policy, dimension, blacklist=None, prices=None):
        self.mem = memory
        self.policy = policy
        self.dimension = dimension
        # what a unit of each token costs another way this round (solve.reach_cost), injected so skills skip the planner
        self._prices = prices
        # position or (entity id, 0, 0) → expiry; the brain owns it so bans outlive a round
        self.blacklist = blacklist if blacklist is not None else Versioned()
        self.ban_counts = _BAN_COUNTS  # shared like the blacklist

    def prices(self):
        """{token: seconds per unit}, or {} when nobody handed any over (tests, replays)."""
        got = self._prices() if callable(self._prices) else self._prices
        return got or {}

    def blocked(self, pos):
        return banned(self.blacklist, pos)

    def ban(self, pos, seconds=BAN_MAX_S):
        """Ban a cell after a failure (never an interruption); repeats escalate, capped at BAN_MAX_S because the world changes."""
        key = tuple(pos)
        count = self.ban_counts.get(key, 0) + 1
        self.ban_counts[key] = count
        self.blacklist[key] = time.time() + min(seconds * (2 ** (count - 1)), BAN_MAX_S)

SETTLE_POLL_S = 0.25

def settle(read, ok, timeout=3.0, stable_s=0.5, soft=False, poll=SETTLE_POLL_S, clock=time.time, sleep=time.sleep):
    """Poll `read()` until `ok(value)` held for `stable_s` or `timeout`; returns the last value (the world catches up a tick later)."""
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
    """What `read()` says once settled above `before`, or at the timeout; the caller compares."""
    return settle(read, lambda v: v > before, timeout, stable_s)

def lost(read, before, timeout=3.0, stable_s=0.5):
    """The mirror of `gained`: a count that should have gone DOWN (thrown away, stored, loaded into a furnace)."""
    return settle(read, lambda v: v < before, timeout, stable_s)

def confirmed(readings, stable_s=0.5):
    """Pure: did the reading hold from the first one for `stable_s`? One reading never confirms."""
    if not readings:
        return False
    t0 = readings[0][0]
    for t, v in readings:
        if not (v.get("dead") if isinstance(v, dict) else v):
            return False
        if t - t0 >= stable_s:
            return True
    return False

def really_dead(state=None, readings=None):
    """Is the body really dead? Confirmed by readings that agree for a moment, never one (a chunk load or respawn lies for a frame)."""
    if readings is not None:
        return confirmed(readings)
    first = (state if state is not None else api.get("/state")).get("dead")
    if not first:
        return False
    return bool(settle(lambda: api.get("/state").get("dead"), bool, timeout=1.5, stable_s=0.5, soft=True))

def _protected(ctx):
    """The policy's protected set as it is (a memory.Protected keeps its home boxes; a set() copy dropped them)."""
    p = getattr(getattr(ctx, "policy", None), "protected", None)
    return p.copy() if p is not None else set()

def body_state(ctx, region=None, **extra) -> "BodyState":
    """The state `commands` are built from — body, bag, protected cells, blocks around; read once here."""
    s = api.get("/state")
    out = dict({"state": s, "feet": (s["blockX"], s["blockY"], s["blockZ"]), "inv": Inventory(),
                "protected": _protected(ctx), "region": region},
               **extra)
    return cast("BodyState", out)      # `extra`: a BodyState key each (the NotRequired ones)

def carried_total():
    """Every item in the bag counted (a pickup that only tops up a stack still raises it)."""
    return sum(int(s.get("count", 1)) for s in Inventory().slots)

def head_underwater(s=None):
    """The eyes are in a water block (swimming at the surface with the head out doesn't count)."""
    s = s or api.get("/state")
    if not s["inWater"]:
        return False
    eye = (s["blockX"], math.floor(s["y"] + EYE_HEIGHT), s["blockZ"])
    return Region(eye, eye).name(eye) == "water"

def head_buried(s=None):
    """The eyes are inside a solid block (falling sand/gravel, a placed block): suffocating — read now (a skill's
    check); a round asks `head_buried_in` over its own ground."""
    s = s or api.get("/state")
    eye = eye_cell(s)
    return head_buried_in(Region(eye, eye, props=True), s)


def eye_cell(s):
    return s["blockX"], math.floor(s["y"] + EYE_HEIGHT), s["blockZ"]


def head_buried_in(region, s):
    """Pure: the eyes are inside a solid block of `region` (the round's ground)."""
    return region.buries(eye_cell(s))

def hold_clicks(slots, selected, item, price=None):
    """Pure (I2): the /click bodies that put `item` in the main hand (the stack in hotbar slot `selected`): its stack
    with wear left swapped in; "hand" = the held stack swapped into an empty slot (any, main inventory too), with
    none the lowest-value non-tool stack (`price(id)`) swapped in. [] when it is held already, or not carried."""
    by_slot = {s["slot"]: s for s in slots}
    held = by_slot.get(selected)

    def swap(slot):
        return {"slot": screen_slot(slot), "button": selected, "action": "SWAP"}

    def tool(s):
        return bool(s.get("maxDamage"))
    if item == "hand":
        if held is None or not tool(held):
            return []
        empty = next((i for i in range(BAG_SLOTS) if i not in by_slot), None)
        if empty is not None:
            return [swap(empty)]
        spare = min((s for s in slots if not tool(s)), key=lambda s: (price or (lambda i: 0))(s["id"]) or 0,
                    default=None)
        return [swap(spare["slot"])] if spare else []
    if held is not None and held["id"] == item and not (tool(held) and held["maxDamage"] - held.get("damage", 0) <= 1):
        return []
    have = next((s for s in slots if s["id"] == item and not (tool(s) and s["maxDamage"] - s.get("damage", 0) <= 1)),
                None)
    return [swap(have["slot"])] if have else []

def hold(tasks, price=None):
    """api.HOLD (I2): the item `tasks` name put in the main hand before they go out — one /inventory read, the
    screen closed first (a SWAP needs the player's own), hold_clicks posted and said (what went in the hand)."""
    item = next((t.get("item") for t in tasks if t.get("type") in api.HELD_TYPES and t.get("item")), None)
    if item is None:
        return
    inv = inventory_now()                  # ARM's read when nothing was sent since (R-a)
    clicks = hold_clicks(inv.slots, inv.selected, item, price)
    if not clicks:
        return
    close_screen()
    for body in clicks:
        api.post("/click", body)
    came = next((s["id"] for s in inv.slots if screen_slot(s["slot"]) == clicks[0]["slot"]), "nothing")
    api.detail(f"   hold {item}: {came} swapped into the hand")

def opened(result):
    """Pure: did a use open the block's screen (the jar answers succeeded with screen "none" when it did not)."""
    return (result or {}).get("status") == "succeeded" and ((result or {}).get("result") or {}).get("screen") not in (
        None, "none")

def close_screen():
    s = api.get("/state")
    if s["screen"] not in ("none", "class_433"):
        api.post("/close")

def spot_region(state, reach=4):
    """The box `free_spots` reads around the body in `state` (/state)."""
    fx, fy, fz = state["blockX"], state["blockY"], state["blockZ"]
    return Region((fx - reach, fy - 3, fz - reach), (fx + reach, fy + 4, fz + reach))

def free_spots(region, state, block_under=True, reach=4, avoid=(), limit=5):
    """Pure: air cells with a solid floor in reach, clear of the body, best first (same height, open above, ~2 away)."""
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
                if block_under and (not region.solid(cell_add(p, (0, -1, 0))) or region.hazard(cell_add(p, (0, -1, 0)))):
                    continue
                if p[1] in (fy, fy + 1) and abs(p[0] + 0.5 - s["x"]) < 0.8 and abs(p[2] + 0.5 - s["z"]) < 0.8:
                    continue
                enclosed = region.solid(cell_add(p, (0, 1, 0)))
                scored.append(((abs(dy), enclosed, abs(max(abs(dx), abs(dz)) - 2)), p))
    scored.sort()
    return [p for _, p in scored[:limit]]

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


ARM_REGION_MAX = 4096       # cells one read may cover to name what a chain's mines break; larger: a read per cell

def arm(tasks, inv=None, read_blocks=True):
    """The item each mine or attack holds, named where none is (knowledge.tool_for / weapon_for): reads only when a
    task lacks one — the bag (unless `inv`, the one perception already holds, is given) and the blocks the mines
    break (unless `read_blocks` is off: a mine then names the bare-block tool). HOLD puts that item in hand (I2)."""
    mines = [t for t in tasks if t.get("type") == "mine" and "item" not in t]
    if not mines and not any(t.get("type") == "attack" and "item" not in t for t in tasks):
        return tasks
    try:
        inv = inv if inv is not None else inventory_now()
        cells = [(t["x"], t["y"], t["z"]) for t in mines] if read_blocks else []
        names = {}
        if cells:
            # the box the stand gate reads too (cells ± REACH) serves both (R-b); too large: the cells' own box
            for pad in (REACH, 0):
                lo = tuple(math.floor(min(c[i] for c in cells) - pad) for i in range(3))
                hi = tuple(math.ceil(max(c[i] for c in cells) + pad) for i in range(3))
                if (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1) <= ARM_REGION_MAX:
                    region = box(lo, hi)
                    names = {c: region.name(c) for c in cells}
                    break
            else:
                names = {c: Region(c, c).name(c) for c in cells}
    except McError as e:              # no world to read (an offline test): the tasks go as they were — said, never silent
        api.detail(f"  arm: {type(e).__name__}: {e} — {[t.get('type') for t in tasks]} sent without naming what they hold")
        return tasks
    out = []
    for t in tasks:
        kind = t.get("type")
        if "item" in t or kind not in ("mine", "attack"):
            out.append(t)
        elif kind == "mine":
            out.append({**t, "item": _know.tool_for(inv, names.get((t["x"], t["y"], t["z"])))})
        else:
            out.append({**t, "item": _know.attack_weapon(inv, beliefs.COMMON_FOE_HP) or "hand"})
    return out

