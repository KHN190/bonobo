"""Supervise start: a free run never begins in a bench arena. A body at a bench site (the sky platform round a
scenario's origin, out to the bench's own leftover reach) has the bench's leftover mobs killed there, then is sent to
its spawn with nothing of the bench's kit: the arena floats, no walk leaves it.

    python3 -m bonobo.tools.leave_bench     → what it did, one line; nothing when the body is not at a bench site
"""
import math
import sys
import time

from .. import api
from ..bench.core import BOX, ORIGIN, SITE_B, _c, _send, replies
from ..bench.runner import LEFTOVER_R

DIMENSION = "minecraft:overworld"                      # the bench builds its sites here
SITES = (ORIGIN, tuple(o + d for o, d in zip(ORIGIN, SITE_B)))
FLOOR_Y = ORIGIN[1] + BOX[0][1]                        # the lowest the bench builds: natural terrain stays below
RESPAWN_POLLS = 20


def bench_site(pos, dimension):
    """Pure: the bench site origin whose reach holds `pos`, or None."""
    if dimension != DIMENSION or pos[1] < FLOOR_Y:
        return None
    return next((o for o in SITES if math.dist((pos[0], pos[2]), (o[0], o[2])) <= LEFTOVER_R), None)


def leave_commands():
    """Pure: the leftovers killed at every site, the bench kit cleared, the body sent to its spawn."""
    run = lambda c: f"execute in {DIMENSION} run {c}"        # noqa: E731
    return ([run(f"execute positioned {_c(o)} run kill @e[type=!player,distance=..{LEFTOVER_R}]") for o in SITES]
            + [run("clear @p"), run("kill @p")])


def main():
    s = api.get("/state")
    site = bench_site((s["x"], s["y"], s["z"]), s["dimension"])
    if site is None:
        return 0
    cmds = leave_commands()
    _send(cmds, replies(cmds), 1.0)
    for _ in range(RESPAWN_POLLS):                    # the death screen comes a tick or two after the kill
        if api.get("/state").get("dead"):
            api.post("/respawn")
            break
        time.sleep(0.25)
    print(f"at the bench site {site}: its leftovers killed, the bench kit cleared, sent to spawn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
