"""How many come: the one threat; a second of its kind beside it; a second up out of melee reach (on a pillar:
estimate.melee_reachable says no — the bow's, or an evade's); a second of another kind (a ranged one beside a melee
one, or the other way); a second dying (health 0: listed through its death, no row — threat.rows)."""
from typing import Any

from bonobo.beliefs import MOBS, PLAYER
from bonobo.estimate import melee_reachable

NAME = "pack"
VALUES = ("one", "two", "above", "mixed", "dying")
DEPENDS = (lambda f: f["threat"], {"threat": True})
UP = int(float(PLAYER["melee_reach"]) + float(PLAYER["eye_height"])) + 2      # past the melee reach upward
OTHER = {False: "minecraft:skeleton", True: "minecraft:zombie"}             # by whether the first is ranged


def domain():
    return VALUES


def valid(value, f):
    """A second of another kind stands where the first does: past its notice radius (range far) it is no threat."""
    return not (value == "mixed" and f["range"] == "far")


def alpha(a):
    if len(a.threats) < 2:
        return VALUES[0]
    first, second = a.threats[0], a.threats[1]
    if second.get("health") is not None and float(second["health"]) <= 0:
        return "dying"
    if second["type"] != first["type"]:
        return "mixed"
    s = a.snap.state
    row = ((second["x"], second["y"], second["z"]), 0.0, (0.0, 0.0, 0.0), second["type"])
    return "two" if melee_reachable((s["x"], s["y"], s["z"]), row) else "above"


def gamma(value, f, g):
    if not f["threat"] or value == "one":
        return
    e: dict[str, Any] = dict(g.entities[0], id=2)
    e.update(z=e["z"] + 2.0)
    if value == "above":
        x, y, z = int(e["x"]), int(g.state["y"]) + UP, int(e["z"])
        g.blocks.update({(x, yy, z): "stone" for yy in range(int(g.state["y"]), y)})      # its pillar
        e.update(x=x + 0.5, y=float(y), z=z + 0.5)
    elif value == "mixed":
        e["type"] = OTHER[bool(MOBS[e["type"]].get("ranged"))]
        e.pop("angry", None)
    elif value == "dying":
        e["health"] = 0.0
    g.entities.append(e)
