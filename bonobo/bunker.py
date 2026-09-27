"""The dragon bunker: geometry only, no actions. Measured from the tapes, the fight gives us a 4.95 s sitting window and 0.85 s of warning before the take-off knock. Nothing with a 200 ms control loop and an execution delay on top can dodge on those numbers, so the answer is not better reflexes but better ground: a one-wide tunnel under the island floor turns the open-field problem into three discrete states — in the tunnel, at the mouth, out — each of which can be tested and reproduced. Why a tunnel works (all of it is vanilla geometry, none of it is a trick): * an enderman is 2.9 blocks tall: it cannot enter a 1×2 corridor, and cannot reach what stands inside one * dragon breath pools at the mouth but does not flow 2–3 blocks in * the head sweep and the take-off knockback need line of sight and space; a ceiling denies both It is an extension of the bomb pit, not a separate structure: the pit's floor is the mouth, and the tunnel runs outward from it along the same side axis. The pit stays the place a bed is clicked from; the tunnel is where we wait."""

from . import nav
from .end import PIT_DEPTH, PIT_R
from .fight_plan import CONFIG as _CFG

_GEO = _CFG["geometry"]

# past the breath pooling at the mouth; deeper costs dig time for nothing
TUNNEL_LEN = _GEO["tunnel_len"]
# clicked from the mouth: the bed's top is 3.57 from the eye against a 4.5 reach (one further in leaves 0.05) — reach, not sight, so never step out
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
    """Pure: the cell the bed is placed and detonated from — inside the tunnel, within reach of the bed."""

    return tunnel(side, floor_y, centre)[at]

def retreat(side, floor_y, centre=(0, 0), at=RETREAT_AT):
    """Pure: the cell we wait in while it takes off or breathes."""

    return tunnel(side, floor_y, centre)[at]

def head_cells(cells):
    """Pure: the head-height cell above each feet cell."""

    return [(x, y + 1, z) for x, y, z in cells]

def ceiling(cells):
    """Pure: the cells that must stay solid over the corridor."""

    return [(x, y + 2, z) for x, y, z in cells]

def dig_batch(side, floor_y, solid, centre=(0, 0), length=TUNNEL_LEN):
    """Pure: the whole bunker as one batch from its rim — down the shaft, then the corridor, each cell within reach of the last."""

    mx, my, mz = mouth(side, floor_y, centre)
    out = []
    for y in range(floor_y - 1, my - 1, -1):
        if solid((mx, y, mz)):
            out.append(nav.mine_task((mx, y, mz), collect=True))
        out.append({"type": "travel", "x": mx, "y": y, "z": mz, "range": 0.5})
    out += [nav.mine_task(c, collect=True) for c in dig_plan(side, floor_y, centre, length)[2:] if solid(c)]
    return out

def dig_plan(side, floor_y, centre=(0, 0), length=TUNNEL_LEN):
    """Pure: every cell to mine, in the order to mine it — down the shaft first, then outward."""

    cells = tunnel(side, floor_y, centre, length)
    out = []
    for feet, head in zip(cells, head_cells(cells)):
        out.append(feet)
        out.append(head)
    return out

def reinforce_cells(side, floor_y, centre=(0, 0)):
    """Pure: the cells to replace with obsidian before bombing — the mouth's own ceiling and its two side walls."""

    m = mouth(side, floor_y, centre)
    dx, dz = side
    # The two cells across the corridor axis are its walls; the cell two above the feet is its roof.
    across = (dz, dx)
    return [(m[0], m[1] + 2, m[2]),
            (m[0] + across[0], m[1], m[2] + across[1]),
            (m[0] - across[0], m[1], m[2] - across[1]),
            (m[0] + across[0], m[1] + 1, m[2] + across[1]),
            (m[0] - across[0], m[1] + 1, m[2] - across[1])]
