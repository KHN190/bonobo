"""Wood: fell trunks from the ground up, nearest first."""
import math

from . import api, nav
from .api import McError, NotAvailable, log
from .data import GROUPS
from .explore import seek_blocks
from .skill import skill
from .skillcore import _collect_only, feet, gained, settle
from .world import Inventory, find


def trunk_batch(base, overhead, want):
    """Pure: one trunk as one batch — the base log from outside, into its cell (travel: the canopy may start one up,
    and travel breaks what is over the head), the logs overhead from below (every bottom face right over the eye),
    one pickup for all of them. Only as many overhead logs as are still wanted."""
    x, y, z = base
    mine = lambda c: {"type": "mine", "x": c[0], "y": c[1], "z": c[2], "collect": False,   # noqa: E731
                      "requireDrops": False}
    out = [mine(base)]
    up = [c for c in overhead][:max(0, want - 1)]
    if up:
        out.append({"type": "travel", "x": x, "y": y, "z": z, "range": 0.3})
        out += [mine(c) for c in up]
    out.append({"type": "collect", "radius": 4, "only": ["log"]})
    return out


def felled(trunk, still):
    """Pure: none of this trunk's logs still stands (`still`: the log cells the world lists after chopping)."""
    return not any((t["x"], t["y"], t["z"]) in still for t in trunk)


@skill(start=lambda c: Inventory().count("log"), done=lambda c: Inventory().count("log") >= c.base + c.args[1],
       budget=600, stall=90, per_unit=6, units=lambda c: c.args[1], key=lambda c: "chop",
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
        seed = logs[0]
        trunk = [t for t in logs if abs(t["x"] - seed["x"]) <= 1 and abs(t["z"] - seed["z"]) <= 1]
        base = min(trunk, key=lambda t: t["y"])
        if math.dist(feet(), (base["x"], base["y"], base["z"])) > 2.5:   # stand beside the trunk (3.5 m was too far)
            # The walker alone can't climb a mountain or tunnel to a tree 30 blocks up: get to the trunk with the
            # navigator (dig, pillar, ladder, bridge) first, then chop within reach.
            if not nav.arrived((base["x"], base["y"], base["z"]), ctx.policy, range_=2, attempts=2):
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
        base_pos = (base["x"], base["y"], base["z"])
        # Base log from outside, then stand in its cell and take the logs overhead: every bottom face is right above
        # the eye, no approach search. From outside, logs 1–2 up behind leaves failed "no path found (267 positions)"
        # after a 3 s walk (bench 05:14), and mine_many's top-down order hit the canopy first (bench 03:43).
        overhead = sorted((t for t in trunk if t["x"] == base["x"] and t["z"] == base["z"]
                           and base["y"] < t["y"] <= base["y"] + 4), key=lambda t: t["y"])
        # The whole trunk as ONE submission (trunk_batch): the jar runs it through without a round trip per log.
        tasks = trunk_batch(base_pos, [(t["x"], t["y"], t["z"]) for t in overhead], target - before)
        try:
            results = api.run_chain(tasks, stop_on_failure=False, wait=90)
        except api.Unreachable as out:
            if not nav.way_to(ctx, out.cells or [base_pos]):
                raise
            results = api.run_chain(tasks, stop_on_failure=False, wait=90)
        r = next((x for x in results if x.get("status") != "succeeded"), results[-1] if results else {"message": ""})
        # What is still standing: asked of the world rather than read off a region snapshot taken before the
        # chopping. A cell we just broke is not "left over", and one the canopy dropped into is.
        still = {(t["x"], t["y"], t["z"]) for t in find(GROUPS["log"], radius=8, limit=60)}
        for t in trunk:
            cell = (t["x"], t["y"], t["z"])
            # Standing AND with no way to it — the second half is the game's answer, not a guess from here.
            if cell in still and not nav.reachable(cell, ctx.policy, 2.0)[0]:
                ctx.ban(cell)
        logs_now = lambda: Inventory().count("log")   # noqa: E731
        if gained(logs_now, before) <= before:
            # This trunk gave nothing (canopy, drops stuck): ban it and take the next tree in the same skill call.
            for t in trunk:
                ctx.ban((t["x"], t["y"], t["z"]))
            log(f"   trunk at {(base['x'], base['y'], base['z'])} yielded no logs ({r['message']}); next tree")
            continue
        # Renewable wood: remember the grove, put a sapling back where the trunk stood — once it is all down. A
        # sapling in the base cell under logs still standing blocked the way up to them ("target unreachable").
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
