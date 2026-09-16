"""Read-only check against the live game: build a Brain and score the candidate pool once.

Offline tests construct states and call pure functions; they never run a goal's `done` lambda against a real
inventory, and never call `candidates()` with a real world behind it. Two crash loops reached autoplay that way — a
module shadowed by a parameter name, and a goal lambda that raised on an empty slot. This catches that class before a
long run starts, and touches nothing: no tasks, no writes, one read of the world.

Returns 0 when the pool builds, or when the game is not running (nothing to check). Returns 1 on any exception.
"""
import sys
import traceback

from .. import api, retry, skills          # noqa: F401  (importing skills registers the goal actions)
from ..brain import Brain
from ..world import Snapshot


def main():
    try:
        api.get("/state")
    except api.McError:
        print("dry-run skipped: game not reachable")
        return 0
    try:
        brain = Brain()
        snap = Snapshot()
        ids = [s["id"] for s in snap.inv.slots]
        brain.sig = retry.signature(snap.feet, ids, snap.night, 0)
        brain.coarse = retry.signature(snap.feet, ids, snap.night, 0, bin_size=16)
        ctx = skills.Context(brain.mem, brain.policy(snap, snap.night), snap.dimension, brain.blacklist)
        pool, _filtered = brain.candidates(ctx, snap, snap.night)
        top = sorted(pool, key=lambda c: c.score, reverse=True)[:4]
        print("dry-run ok:", ", ".join(f"{c.name} {c.score:.4f}" for c in top))
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
