"""Food the world offers in sight: animals to hunt (cost._entity over data.ANIMALS) or ripe crops to harvest.
α: /entities of an ANIMALS kind, /find of a crop."""
from bonobo.data import ANIMALS

NAME = "food_source"
VALUES = ("none", "animals", "crops")

HERD = ((6.5, 64.0, 6.5), (7.5, 64.0, 6.5), (6.5, 64.0, 7.5))
FIELD = ((-6, 64, 6), (-5, 64, 6), (-4, 64, 6))
COW, CROP = "minecraft:cow", "wheat"


def domain():
    return VALUES


def alpha(a):
    ents = a.world._entities({"radius": "48"})["entities"]
    if any(e["type"] in ANIMALS for e in ents):
        return "animals"
    return "crops" if a.world._find({"blocks": CROP, "radius": "48"})["blocks"] else "none"


def gamma(value, facts, g):
    if value == "animals":
        for i, (x, y, z) in enumerate(HERD):
            g.entities.append({"id": 100 + i, "type": COW, "x": x, "y": y, "z": z, "health": 10.0})
    elif value == "crops":
        for x, y, z in FIELD:
            g.blocks[(x, y - 1, z)] = "farmland"
            g.blocks[(x, y, z)] = f"{CROP}[age=7]"
