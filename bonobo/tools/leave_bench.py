"""Supervise start: a free run never begins in a bench arena, nor in its world. In a test world every setting the
bench changes goes back to the game's normal (core.restore_commands: the clock runs, weather, mobs spawn). A body at a
bench site (the sky platform round a scenario's origin, out to the bench's own leftover reach) has the bench's
leftover mobs killed, then is sent to its spawn with nothing of the bench's kit: the arena floats, no walk leaves it.

    python3 -m bonobo.tools.leave_bench     → what it did, a line each; nothing outside a test world
"""
import math
import os
import sys
import time

from .. import api
from ..bench.core import BOX, FLAG, ORIGIN, SITE_B, _c, _send, replies, restore_commands
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
    return ([f"execute positioned {_c(o)} run kill @e[type=!player,distance=..{LEFTOVER_R}]" for o in SITES]
            + ["clear @p", "kill @p"])


def run(cmds):
    cmds = [f"execute in {DIMENSION} run {c}" for c in cmds]
    _send(cmds, replies(cmds), 1.0)


def main():
    if not os.path.exists(FLAG):
        return 0                    # the bench runs only in a test world: a real world's settings are the player's
    api.take_control()              # a script's start takes the body: a paused toggle refused every post
    run(restore_commands())
    print("world settings back to the game's normal: " + ", ".join(restore_commands()))
    s = api.get("/state")
    site = bench_site((s["x"], s["y"], s["z"]), s["dimension"])
    if site is None:
        return 0
    run(leave_commands())
    for _ in range(RESPAWN_POLLS):                    # the death screen comes a tick or two after the kill
        if api.get("/state").get("dead"):
            api.post("/respawn")
            break
        time.sleep(0.25)
    print(f"at the bench site {site}: its leftovers killed, the bench kit cleared, sent to spawn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
