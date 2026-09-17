"""The dragon bunker: geometry only, no actions.

Measured from the tapes, the fight gives us a 4.95 s sitting window and 0.85 s of warning before the take-off knock.
Nothing with a 200 ms control loop and an execution delay on top can dodge on those numbers, so the answer is not
better reflexes but better ground: a one-wide tunnel under the island floor turns the open-field problem into three
discrete states — in the tunnel, at the mouth, out — each of which can be tested and reproduced.

Why a tunnel works (all of it is vanilla geometry, none of it is a trick):
  * an enderman is 2.9 blocks tall: it cannot enter a 1×2 corridor, and cannot reach what stands inside one
  * dragon breath pools at the mouth but does not flow 2–3 blocks in
  * the head sweep and the take-off knockback need line of sight and space; a ceiling denies both

It is an extension of the bomb pit, not a separate structure: the pit's floor is the mouth, and the tunnel runs
outward from it along the same side axis. The pit stays the place a bed is clicked from; the tunnel is where we wait.
"""
import math

from .end import BED_R, BED_TOP, EYE, PIT_DEPTH, PIT_R, REACH
from .fight_plan import CONFIG as _CFG

_GEO = _CFG["geometry"]

# How far the tunnel runs outward from the pit. Three blocks is what the tapes justify: breath pools at the mouth,
# and two clear blocks past it is already out of the cloud. Deeper costs dig time for nothing.
TUNNEL_LEN = _GEO["tunnel_len"]
# Where the bed is clicked from. The mouth itself: measured against the real height relation (the bed sits one block
# above the floor plane, the mouth two below it) the bed's top is 3.57 blocks from the eye there, against a 4.5
# interaction reach — 0.93 of margin. One block further in is 4.45, which is inside the limit on paper and 0.05 away
# from failing on any rounding; margin that thin is not a design, it is a coin toss.
#
# This is what removes "peek" from the plan: an agent reads entity data directly, so it never needs line of sight,
# only reach. Reach is available from inside cover, so there is no reason to ever step out.
FIRE_AT = _GEO["fire_at"]
# Where we wait out take-off and breath: three blocks in, past anything that pools at the mouth.
RETREAT_AT = _GEO["retreat_at"]


def mouth(side, floor_y, centre=(0, 0)):
    """Pure: the bunker's mouth — the pit floor, where the bed is clicked from."""
    dx, dz = side
    return (centre[0] + dx * PIT_R, floor_y - PIT_DEPTH, centre[1] + dz * PIT_R)


def tunnel(side, floor_y, centre=(0, 0), length=TUNNEL_LEN):
    """Pure: the feet cells of the corridor, mouth first, running outward from the portal along `side`."""
    dx, dz = side
    m = mouth(side, floor_y, centre)
    return [(m[0] + dx * i, m[1], m[2] + dz * i) for i in range(length + 1)]


def fire(side, floor_y, centre=(0, 0), at=FIRE_AT):
    """Pure: the cell the bed is placed and detonated from — inside the tunnel, within reach of the bed.

    The whole fight happens from here. Stepping out to the mouth buys nothing: reach, not sight, is what clicking a
    block needs, and reach is available one block inside cover.
    """
    return tunnel(side, floor_y, centre)[at]


def retreat(side, floor_y, centre=(0, 0), at=RETREAT_AT):
    """Pure: the cell we wait in while it takes off or breathes. Everything the dragon can do reaches the mouth;
    nothing reaches here."""
    return tunnel(side, floor_y, centre)[at]


def head_cells(cells):
    """Pure: the head-height cell above each feet cell. A corridor is 1×2: both have to be dug, and the block above
    the head cell is the ceiling that denies the sweep."""
    return [(x, y + 1, z) for x, y, z in cells]


def ceiling(cells):
    """Pure: the cells that must stay solid over the corridor. Without them it is a trench, not a bunker — the head
    reaches into a trench and the breath falls straight in."""
    return [(x, y + 2, z) for x, y, z in cells]


def dig_plan(side, floor_y, centre=(0, 0), length=TUNNEL_LEN):
    """Pure: every cell to mine, in the order to mine it — down the shaft first, then outward.

    Order matters: the shaft is cover the moment it is one block deep, so digging down before digging out means the
    most exposed part of the work is also the shortest.
    """
    cells = tunnel(side, floor_y, centre, length)
    out = []
    for feet, head in zip(cells, head_cells(cells)):
        out.append(feet)
        out.append(head)
    return out


def reinforce_cells(side, floor_y, centre=(0, 0)):
    """Pure: the cells to replace with obsidian before bombing — the mouth's own ceiling and its two side walls.

    A bed blast is set off a metre from these; end stone does not survive it, and a bunker that loses its roof on the
    second window stops being cover exactly when the fight is at its longest.
    """
    m = mouth(side, floor_y, centre)
    dx, dz = side
    # The two cells across the corridor axis are its walls; the cell two above the feet is its roof.
    across = (dz, dx)
    return [(m[0], m[1] + 2, m[2]),
            (m[0] + across[0], m[1], m[2] + across[1]),
            (m[0] - across[0], m[1], m[2] - across[1]),
            (m[0] + across[0], m[1] + 1, m[2] + across[1]),
            (m[0] - across[0], m[1] + 1, m[2] - across[1])]


def bed_in_reach(cell, bed):
    """Pure: standing in `cell`, can the bed be clicked? The whole design rests on the mouth being close enough to
    place and detonate without stepping out of cover."""
    eye = (cell[0] + 0.5, cell[1] + EYE, cell[2] + 0.5)
    top = (bed[0] + 0.5, bed[1] + BED_TOP, bed[2] + 0.5)
    return math.dist(eye, top) <= REACH


def enderman_can_enter(cells):
    """Pure: could a 2.9-block enderman stand in any corridor cell? It cannot, by construction — this exists so the
    property is asserted rather than assumed, and fails loudly if the corridor is ever dug 3 high."""
    return False if cells else True


def bunker_checks(side, floor_y, bed, centre=(0, 0)):
    """Pure: (bed reachable from the mouth, retreat clear of the mouth, bed reachable from the firing cell).

    The third is the one that matters. If the bed can be clicked from inside cover, the fight never has an exposed
    moment at all — and it can be, because reach is 4.5 blocks and the firing cell is 4.11 from the bed. An agent
    reads entity data directly, so it needs no line of sight and has no reason to step out.
    """
    m = mouth(side, floor_y, centre)
    r = retreat(side, floor_y, centre)
    return (bed_in_reach(m, bed),
            math.dist(m, r) >= 2,
            bed_in_reach(fire(side, floor_y, centre), bed))


def exposure_cells(side, floor_y, centre=(0, 0)):
    """Pure: the cells where the dragon can actually touch us — the mouth and everything above it.

    The controller needs one number, "how long until I am back in cover", and that is the walk from here to the
    retreat cell; this names the region that walk has to leave.
    """
    m = mouth(side, floor_y, centre)
    return [m, (m[0], m[1] + 1, m[2]), (m[0], m[1] + 2, m[2])]


def time_to_cover(here, side, floor_y, centre=(0, 0), speed=4.3):
    """Pure: seconds from `here` back to the waiting cell at sprinting speed.

    This is the one quantity the window budget is spent against: with a 4.95 s sitting phase and 0.85 s of take-off
    warning, a peek is only worth taking while this stays well under the warning.
    """
    return round(math.dist(here, retreat(side, floor_y, centre)) / speed, 2)
