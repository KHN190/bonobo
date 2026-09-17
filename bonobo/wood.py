"""Wood: fell trunks from the ground up, nearest first."""
import math

from . import api, nav
from .api import McError, NotAvailable, log
from .data import GROUPS
from .explore import seek_blocks
from .skill import skill
from .skillcore import _collect_only, feet
from .world import Inventory, find


@skill(start=lambda c: Inventory().count("log"), done=lambda c: Inventory().count("log") >= c.base + c.args[1],
       budget=600, stall=90, per_unit=6, units=lambda c: c.args[1], key=lambda c: "chop")
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
            if not seek_blocks(ctx, GROUPS["log"]):
                ctx.mem.note_resource("tree", feet(), ctx.dimension, depleted=True)
                raise NotAvailable("no trees found nearby, even after exploring")
            continue
        seed = logs[0]
        trunk = [t for t in logs if abs(t["x"] - seed["x"]) <= 1 and abs(t["z"] - seed["z"]) <= 1]
        base = min(trunk, key=lambda t: t["y"])
        if math.dist(feet(), (base["x"], base["y"], base["z"])) > 2.5:   # stand beside the trunk (3.5 m was too far)
            # The walker alone can't climb a mountain or tunnel to a tree 30 blocks up: get to the trunk with the
            # navigator (dig, pillar, ladder, bridge) first, then chop within reach.
            if not nav.go_to((base["x"], base["y"], base["z"]), ctx.policy, range_=2, attempts=2):
                # The walker gave up; that is not the same as there being no way. The game plans with the same
                # pathfinder it moves with, so it is the one that knows whether digging or bridging gets there.
                if nav.way_to(ctx, [(base["x"], base["y"], base["z"])], range_=2.0):
                    pass                         # a way was made and checked: chop from where we now stand
                elif not nav.reachable((base["x"], base["y"], base["z"]), ctx.policy, 2.0)[0]:
                    for t in trunk:   # genuinely no way in: never mine_many a trunk we cannot get to
                        ctx.ban((t["x"], t["y"], t["z"]))
                    # One failure ends the skill; the brain's retry policy decides (the ban changes its state).
                    raise api.NavFailed(f"no way to the tree at {(base['x'], base['y'], base['z'])}")
        before = Inventory().count("log")
        # Base log from outside, then stand in its cell and take the logs overhead: every bottom face is right above
        # the eye, no approach search. From outside, logs 1–2 up behind leaves failed "no path found (267 positions)"
        # after a 3 s walk (bench 05:14), and mine_many's top-down order hit the canopy first (bench 03:43).
        try:
            r = api.run({"type": "mine", "x": base["x"], "y": base["y"], "z": base["z"], "collect": False,
                         "requireDrops": False}, wait=60)
        except api.Unreachable as out:
            if not nav.way_to(ctx, out.cells or [(base["x"], base["y"], base["z"])]):
                raise
            r = api.run({"type": "mine", "x": base["x"], "y": base["y"], "z": base["z"], "collect": False,
                         "requireDrops": False}, wait=60)
        overhead = sorted((t for t in trunk if t["x"] == base["x"] and t["z"] == base["z"]
                           and base["y"] < t["y"] <= base["y"] + 4), key=lambda t: t["y"])
        if overhead and Inventory().count("log") + 1 < target:
            # travel, not goto: with the canopy starting one block up the trunk cell is only 1 high, and goto found
            # "no path" (bench 05:26). travel breaks the log over the head on the way in — that log is harvest too.
            nav.go_to((base["x"], base["y"], base["z"]), ctx.policy, range_=0.3, attempts=1)
            for t in overhead:
                if Inventory().count("log") + 1 >= target:
                    break
                try:
                    r = api.run({"type": "mine", "x": t["x"], "y": t["y"], "z": t["z"], "collect": False,
                                 "requireDrops": False}, wait=30)
                except api.Unreachable:
                    break                  # the canopy closed over that column: the next tree

                if r["status"] != "succeeded":
                    break
        # One pickup sweep for the whole trunk (the drops fall to the base cell).
        nav.sweep(ctx, radius=4, only=["log"], wait=15)
        # What is still standing: asked of the world rather than read off a region snapshot taken before the
        # chopping. A cell we just broke is not "left over", and one the canopy dropped into is.
        still = {(t["x"], t["y"], t["z"]) for t in find(GROUPS["log"], radius=8, limit=60)}
        for t in trunk:
            cell = (t["x"], t["y"], t["z"])
            # Standing AND with no way to it — the second half is the game's answer, not a guess from here.
            if cell in still and not nav.reachable(cell, ctx.policy, 2.0)[0]:
                ctx.ban(cell)
        if Inventory().count("log") <= before:
            # This trunk gave nothing (canopy, drops stuck): ban it and take the next tree in the same skill call.
            for t in trunk:
                ctx.ban((t["x"], t["y"], t["z"]))
            log(f"   trunk at {(base['x'], base['y'], base['z'])} yielded no logs ({r['message']}); next tree")
            continue
        # Renewable wood: remember the grove, put a sapling back where the trunk stood.
        from . import farming
        base_pos = (base["x"], base["y"], base["z"])
        ctx.mem.note_resource("tree", base_pos, ctx.dimension)
        try:
            farming.replant(ctx, base_pos)
        except McError as e:
            log(f"   replanting at {base_pos} failed: {e}")
    if Inventory().count("log") < target:
        raise McError("could not chop enough logs")
