"""How many come: the one threat, a second of its kind beside it, or a second up out of melee reach (on a pillar:
estimate.melee_reachable says no — the bow's, or an evade's)."""
from typing import Any

from bonobo.beliefs import PLAYER
from bonobo.estimate import melee_reachable

NAME = "pack"
VALUES = ("one", "two", "above")
DEPENDS = (lambda f: f["threat"], {"threat": True})
UP = int(float(PLAYER["melee_reach"]) + float(PLAYER["eye_height"])) + 2      # past the melee reach upward


def domain():
    return VALUES


def alpha(a):
    if len(a.threats) < 2:
        return VALUES[0]
    s = a.snap.state
    here = (s["x"], s["y"], s["z"])
    second = a.threats[1]
    row = ((second["x"], second["y"], second["z"]), 0.0, (0.0, 0.0, 0.0), second["type"])
    return "two" if melee_reachable(here, row) else "above"


def gamma(value, f, g):
    if not f["threat"] or value == "one":
        return
    e: dict[str, Any] = dict(g.entities[0], id=2)
    if value == "two":
        e.update(z=e["z"] + 2.0)
    else:
        x, y, z = int(e["x"]), int(g.state["y"]) + UP, int(e["z"]) + 2
        g.blocks.update({(x, yy, z): "stone" for yy in range(int(g.state["y"]), y)})      # its pillar
        e.update(x=x + 0.5, y=float(y), z=z + 0.5)
    g.entities.append(e)
