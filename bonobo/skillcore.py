"""Primitives every skill module shares: the context, positions, placing, pickup filters. Kept small and
free of skill logic so a skill module's readiness hash doesn't change with unrelated skills."""
import math
import time

from . import api, beliefs
from .api import McError, NotAvailable
from .bag import pickup_whitelist
from .data import bare
from .world import Inventory, Region, add


def _collect_only(wanted):
    """{"only": [...]} for mine/collect tasks when the bag is nearly full, else {}."""
    only = pickup_whitelist(Inventory().used_slots(), wanted)
    return {"only": only} if only else {}


def mine_cell(policy, cell, wanted=(), collect=True, require_drops=False, wait=30):
    """Break one block, never one of ours.

    Site cells — the shelter's walls, the base, a built machine — are in `policy.protected`, and nav has always
    respected them. Every other way of digging went straight to the task API, so the night the agent ran out of
    things to do it mined the hut it had just built for the cobblestone. One door for breaking a single cell, and
    the protection is on this side of it.
    """
    cell = tuple(cell)
    if cell in getattr(policy, "protected", ()):  
        raise NotAvailable(f"{cell} is part of one of our own structures")
    started = time.time()
    out = api.run({"type": "mine", "x": cell[0], "y": cell[1], "z": cell[2], "collect": collect,
                   **_collect_only(list(wanted)), "requireDrops": require_drops}, wait=wait)
    _note_break(started)
    return out


def _note_break(started):
    """How long breaking ONE block actually took, against what the model believes it takes.

    `[tools] mine_time_stone` and `mine_time_no_pickaxe` are the two numbers every mining estimate is built on and
    both were declared guesses. Every block this door breaks is one measurement of one of them — which one depends
    on whether there is a pickaxe in hand, because that is the whole difference between the two.
    """
    took = time.time() - started
    if not 0.05 <= took <= 30.0:
        return None                      # a queued task, a stall, an interrupted break: not a measurement
    try:
        has = bool(Inventory().tools("pickaxe"))
    except (McError, OSError) as err:
        return api.swallowed("skillcore._note_break", err)
    return beliefs.note("tools.mine_time_stone" if has else "tools.mine_time_no_pickaxe", took, where="mine_cell")


class ToolMissing(McError):
    def __init__(self, kind, tier):
        super().__init__(f"need a tier-{tier} {kind}")
        self.kind, self.tier = kind, tier


_BAN_COUNTS = {}


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
        exp = self.blacklist.get(tuple(pos))
        return exp is not None and exp > time.time()

    def ban(self, pos, seconds=600):
        """Blacklist a cell. Repeats escalate: a place proven unreachable twice is banned twice as long, up to two
        hours. A fixed ten minutes brought the same coal block back 37 times in one session."""
        key = tuple(pos)
        count = self.ban_counts.get(key, 0) + 1
        self.ban_counts[key] = count
        self.blacklist[key] = time.time() + min(seconds * (2 ** (count - 1)), 7200)


def feet():
    s = api.get("/state")
    return s["blockX"], s["blockY"], s["blockZ"]


def close_screen():
    s = api.get("/state")
    if s["screen"] not in ("none", "class_433"):
        api.post("/close")


def free_spots(block_under=True, reach=4, avoid=(), limit=5):
    """Air cells with a solid floor within reach, clear of the body, best first: same height as us, open above
    (not a wall nook or under a roof), about two blocks away."""
    s = api.get("/state")
    fx, fy, fz = s["blockX"], s["blockY"], s["blockZ"]
    region = Region((fx - reach, fy - 3, fz - reach), (fx + reach, fy + 4, fz + reach))
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


def free_spot(block_under=True, reach=4, avoid=()):
    spots = free_spots(block_under, reach, avoid, limit=1)
    return spots[0] if spots else None


def place(item, pos):
    r = api.run({"type": "place", "item": item, "x": pos[0], "y": pos[1], "z": pos[2]}, wait=60)
    if r["status"] != "succeeded":
        raise McError(f"placing {bare(item)} failed: {r['message']}")


def snapshot(center, half=2, down=1, up=2):
    lo = (center[0] - half, center[1] - down, center[2] - half)
    hi = (center[0] + half, center[1] + up, center[2] + half)
    region = Region(lo, hi)
    return {"lo": list(lo), "hi": list(hi),
            "blocks": {f"{x},{y},{z}": n for (x, y, z), n in region.blocks.items() if region.solid((x, y, z))}}
