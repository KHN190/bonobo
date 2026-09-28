"""Wood: fell trunks from the ground up, nearest first."""
import math

from . import knowledge as K
from . import api, nav
from .api import McError, NotAvailable, log
from .data import GROUPS
from .explore import seek_blocks
from .knowledge import CHOP_AXE_S
from .skill import skill
from .skillcore import feet, gained, settle
from .world import Inventory, find

TRUNK_REACH = 4        # logs this far above the base are in reach from beside the trunk (eye 1.62, reach 4.5)

def trunk_batch(base, overhead, want):
    """Pure: one trunk as one batch — every log in reach up to TRUNK_REACH as single mines in the batch's order
    (nav.mine_batch: the top of the column first), then one pickup."""

    x, y, z = base
    logs = [tuple(base)] + [tuple(c) for c in overhead if y < c[1] <= y + TRUNK_REACH]
    logs = logs[:max(1, want)]
    return nav.mine_batch(logs, collect=False) + [{"type": "collect", "radius": 4, "only": ["log"]}]

def felled(trunk, still):
    """Pure: none of this trunk's logs still stands (`still`: the log cells the world lists after chopping)."""
    return not any((t["x"], t["y"], t["z"]) in still for t in trunk)

def pick_trunks(logs):
    """Pure: logs grouped into trunks (within one block sideways of the group's first), in /find's nearest-first order."""

    trunks = []
    for t in logs:
        for tr in trunks:
            if abs(t["x"] - tr[0]["x"]) <= 1 and abs(t["z"] - tr[0]["z"]) <= 1:
                tr.append(t)
                break
        else:
            trunks.append([t])
    return trunks

@skill(gives=K.GIVES_GATHER, needs={}, speed={"axe": CHOP_AXE_S}, start=lambda c: Inventory().count("log"), done=lambda c: Inventory().count("log") >= c.base + c.args[1],
       budget=600, stall=90, units=lambda c: c.args[1], key=lambda c: "chop",
       provides={"item:log": lambda ctx, s: (s.count,)}, fills_bag=lambda c: GROUPS["log"])
def chop(ctx, n):
    """Fell whole trunks nearest first until `n` more logs are held."""
    target = Inventory().count("log") + n
    for _ in range(8):
        if Inventory().count("log") >= target:
            return
        yield Inventory().count("log")   # progress = logs held; walking between trees isn't progress
        logs = [t for t in find(GROUPS["log"], radius=48, limit=80) if not ctx.blocked((t["x"], t["y"], t["z"]))
                and (t["x"], t["y"], t["z"]) not in ctx.policy.protected]
        if not logs:
            try:
                seek_blocks(ctx, GROUPS["log"])
            except NotAvailable as e:
                ctx.mem.forget_seen("tree", feet(), ctx.dimension, radius=48)
                raise NotAvailable(f"no trees found nearby, even after exploring ({e})")
            continue
        # the nearest trunk the pathfinder can reach on this ground, not the nearest seen (one was walked to over a platform edge)
        trunk = None
        for seed in pick_trunks(logs)[:4]:
            base = min(seed, key=lambda t: t["y"])
            pos = (base["x"], base["y"], base["z"])
            if math.dist(feet(), pos) <= 2.5 or nav.reachable(pos, ctx.policy, 2.0, feet=feet())[0]:
                trunk = seed
                break
            for t in seed:
                ctx.ban((t["x"], t["y"], t["z"]))
        if trunk is None:
            raise api.NavFailed("no tree in sight can be walked to on this ground")
        base = min(trunk, key=lambda t: t["y"])
        if math.dist(feet(), (base["x"], base["y"], base["z"])) > 2.5:   # stand beside the trunk (3.5 m was too far)
            # get to the trunk with the navigator first (the walker can't climb or tunnel), then chop within reach
            if not nav.arrived((base["x"], base["y"], base["z"]), ctx.policy, range_=2, attempts=2):
                # the walker giving up is not "no way": the game's pathfinder decides whether digging or bridging gets there
                if nav.way_to(ctx, [(base["x"], base["y"], base["z"])], range_=2.0):
                    pass                         # a way was made and checked: chop from where we now stand
                elif not nav.reachable((base["x"], base["y"], base["z"]), ctx.policy, 2.0)[0]:
                    for t in trunk:   # genuinely no way in: never mine_many a trunk we cannot get to
                        ctx.ban((t["x"], t["y"], t["z"]))
                    # One failure ends the skill; the brain's retry policy decides (the ban changes its state).
                    raise api.NavFailed(f"no way to the tree at {(base['x'], base['y'], base['z'])}")
        before = Inventory().count("log")
        base_pos = (base["x"], base["y"], base["z"])
        # base log from outside, then stand in its cell and take the logs overhead: every face above the eye, no approach search
        overhead = sorted((t for t in trunk if t["x"] == base["x"] and t["z"] == base["z"]
                           and base["y"] < t["y"] <= base["y"] + 4), key=lambda t: t["y"])
        # the whole trunk as one submission
        tasks = trunk_batch(base_pos, [(t["x"], t["y"], t["z"]) for t in overhead], target - before)
        try:
            r = nav.run_cells("mine_many", tasks[:-1], then=tasks[-1], wait=90)
        except api.Unreachable as out:
            if not nav.way_to(ctx, out.cells or [base_pos]):
                raise
            r = nav.run_cells("mine_many", tasks[:-1], then=tasks[-1], wait=90)
        # what still stands is asked of the world, not read off a snapshot taken before chopping
        still = {(t["x"], t["y"], t["z"]) for t in find(GROUPS["log"], radius=8, limit=60)}
        for t in trunk:
            cell = (t["x"], t["y"], t["z"])
            # standing and with no way to it — the game's answer
            if cell in still and not nav.reachable(cell, ctx.policy, 2.0)[0]:
                ctx.ban(cell)
        logs_now = lambda: Inventory().count("log")   # noqa: E731
        if gained(logs_now, before) <= before:
            # this trunk gave nothing: ban it and take the next tree in the same call
            for t in trunk:
                ctx.ban((t["x"], t["y"], t["z"]))
            log(f"   trunk at {(base['x'], base['y'], base['z'])} yielded no logs ({r['message']}); next tree")
            continue
        # renewable wood: note the grove and replant once the trunk is all down (a sapling under standing logs blocked the way up)
        from . import farming
        ctx.mem.note_seen("tree", base_pos, ctx.dimension)
        if not felled(trunk, still):
            continue
        try:
            farming.replant(ctx, base_pos)
        except api.INTERRUPTIONS:
            raise              # an interruption is not a failure to shrug off here
        except McError as e:
            log(f"   replanting at {base_pos} failed: {e}")
    if settle(lambda: Inventory().count("log"), lambda n: n >= target) < target:
        raise McError("could not chop enough logs")
