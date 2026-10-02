"""Water and lava: the body swimming (fluids.swimming: the reach-land reflex), or lava open beside the feet
(fluids.lava_within, the box the round reads)."""
from bonobo import fluids

NAME = "fluid"
VALUES = ("none", "swimming", "lava")

LAVA = (-3, 63, 0)                  # in the floor beside the feet, open to the air
NEAR = 3                            # the α box's half-size (check.facts._region)


def domain():
    return VALUES


def alpha(a):
    if fluids.swimming(a.snap.state):
        return "swimming"
    return "lava" if fluids.lava_within(a.region, a.snap.feet, NEAR) else "none"


def gamma(value, facts, g):
    if value == "swimming":
        x, y, z = int(g.state["blockX"]), int(g.state["blockY"]), int(g.state["blockZ"])
        for dy in (0, 1):
            g.blocks[(x, y + dy, z)] = "water"
        g.state.update(inWater=True, onGround=False, air=g.state.get("air", 300) // 2)
    elif value == "lava":
        g.blocks[LAVA] = "lava"
