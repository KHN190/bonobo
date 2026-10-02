"""γ: a concrete world (stub transport) and memory for facts, so alpha(gamma(f)) == f (round-trip tested). Values are
production constants (data, beliefs): day/night ticks, the critical hp, the pod's blocks."""
import functools
from collections import Counter

from bonobo.beliefs import MOBS
from bonobo.data import CRITICAL_HP, DAY_END, DAY_TICKS, FULL_BAR, MAX_HP, NIGHT_END, POD_BLOCKS, TOOL_MATERIAL_FOR_TIER, recipes, TOOL_USES
from bonobo.reflexes import EAT_BELOW, STARVE
from bonobo.threat import NEUTRAL_MOBS

from .stub import StubWorld


FOOD_OF = {"full": FULL_BAR, "low": EAT_BELOW - 1, "starve": STARVE}


def inputs(item):
    """A queued item's inputs for one craft (data.recipes; a group token as its oak member), none for a mined one."""
    pattern = recipes().get(f"minecraft:{item}", ((),))[0]
    return Counter(f"oak_{t}" if t == "planks" else t.removeprefix("minecraft:") for t in pattern if t)

FEET = (0, 64, 0)
HOME = ((-4, 64, -4), (4, 67, 4))          # a home room round the feet (place = home)
STATION = (2, 64, -2)                      # a placed station within reach of the feet


def _box(lo, hi, block):
    return {(x, y, z): block for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1)
            for z in range(lo[2], hi[2] + 1)}


@functools.cache
def _floor():
    """The stone under every γ world, indexed once per process (each world lays its blocks over it)."""
    x, y, z = FEET
    return StubWorld({"blockX": x, "blockY": y, "blockZ": z}, (), _box((x - 16, y - 20, z - 16),
                                                                      (x + 16, y - 1, z + 16), "stone"))


class Build:
    """What a dimension's gamma adds to (check/dims): the world's blocks, the bag (give), the entities, the /state
    readings, the memory."""

    def __init__(self, mem):
        self.blocks, self.slots, self.entities, self.state, self.mem = {}, [], [], {}, mem
        self.equipment = {}          # /inventory equipment: an offhand, armour

    def give(self, item, n=1):
        material = item.rpartition("_")[0]
        self.slots.append({"slot": len(self.slots), "id": f"minecraft:{item}", "count": n, "damage": 0,
                           **({"maxDamage": TOOL_USES[material]} if material in TOOL_USES else {})})


def gamma(f, mem):
    """(StubWorld, the home boxes registered in `mem`) for the facts `f`."""
    from .facts import DIMS
    x, y, z = FEET
    g = Build(mem)
    blocks = g.blocks
    if f["place"] in ("enclosed", "home"):
        lo, hi = HOME
        blocks.update({c: "stone" for c in _box(lo, hi, "stone") if not (lo[0] < c[0] < hi[0] and c[1] < hi[1]
                                                                         and lo[2] < c[2] < hi[2])})
    if f["tree"]:
        blocks.update(_box((8, y, 8), (8, y + 3, 8), "oak_log"))
    if f["ore"] == "exposed":
        blocks[(6, y, 0)] = "iron_ore"
    elif f["ore"] == "buried":
        blocks[(3, y - 2, 0)] = "iron_ore"
    elif f["ore"] == "deep":
        blocks[(3, y - 12, 0)] = "iron_ore"
    if f["station"] != "none":
        blocks[STATION] = f["station"]
        mem.add_station(f"minecraft:{f['station']}", STATION, f["dimension"])
    if f["bed"] == "home":
        blocks[(2, y, 2)], blocks[(3, y, 2)] = "red_bed[facing=east,part=foot]", "red_bed[facing=east,part=head]"
    slots, give = g.slots, g.give
    if f["pickaxe"] >= 0:
        give(f"{TOOL_MATERIAL_FOR_TIER[f['pickaxe']]}_pickaxe")
    if f["bed"] == "carried":
        give("white_bed")
    if f["building"]:
        give("cobblestone", POD_BLOCKS)
    if f["food"]:
        give("bread", 8)
    if f["queued"] != "none":
        from bonobo import goals, tasks
        for item, n in inputs(f["queued"]).items():
            give(item, n)
        want = f"minecraft:{f['queued']}"
        tasks.add(goals.have((want, 1 + sum(sl["count"] for sl in slots if sl["id"] == want))))   # one more than held
    entities = g.entities
    if f["threat"]:
        mob = f"minecraft:{f['mob']}"
        # a neutral one provoked (the jar's `angry`): else it is no threat (threat.aggro)
        entities.append({"id": 1, "type": mob, "x": x + 3.5, "y": y, "z": z + 0.5, "health": 20.0,
                         "notice": MOBS[mob].get("notice_r"), **({"angry": True} if mob in NEUTRAL_MOBS else {})})
    sky = 0 if f["place"] != "open" else 15
    state = {"x": x + 0.5, "y": float(y), "z": z + 0.5, "blockX": x, "blockY": y, "blockZ": z,
             "dimension": f["dimension"], "timeOfDay": (DAY_END + NIGHT_END) // 2 if f["night"] else DAY_TICKS // 4,
             "health": {"crit": float(CRITICAL_HP), "low": MAX_HP / 2}.get(f["hp"], MAX_HP), "food": FOOD_OF[f["hunger"]], "saturation": 5.0,
             "onGround": True, "inWater": False, "skyLight": sky, "blockLight": 0, "air": 300, "armor": 0,
             "gameTime": 1000, "selectedSlot": 0,
             "control": {"active": True, "paused": bool(f["takeover"]), "task": None}}
    g.state = state
    for d in DIMS:
        d.gamma(f[d.NAME], f, g)
    world = StubWorld(state, slots, blocks, entities, equipment=g.equipment, base=_floor())
    if f["place"] == "home":
        lo, hi = HOME
        mem.add_home("home", [(lo, hi)], f["dimension"], {c: n.split("[")[0] for c, n in blocks.items()
                                                          if all(lo[i] <= c[i] <= hi[i] for i in range(3))})
    elif f["bed"] == "home":
        mem.add_home("home", [((1, y, 1), (4, y + 2, 3))], f["dimension"],
                     {(2, y, 2): "red_bed", (3, y, 2): "red_bed"})
    return world
