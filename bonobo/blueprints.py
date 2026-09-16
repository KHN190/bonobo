"""Machines as data: what a build is for, its parts (relative cells, items, intended orientation, role) and the
orientation rules needed to place them. Pure data + geometry; the build/use skills live in skills.py.

Orientation (vanilla Java):
  - Hoppers output into the block that was clicked (`against`); clicking a top/bottom face outputs down.
  - Furnaces, chests, pistons, observers, dispensers, repeaters take their `facing` from the body orientation at
    the click. The default rule assumes the block faces the player (so the player looks the opposite way); builds
    verify the resulting `facing` property and the build skill learns a per-item correction when that's wrong.

Adding a machine = one Blueprint entry. Materials come from `materials()`, so the planner resolves them."""
from dataclasses import dataclass, field

DIRS = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0), "up": (0, 1, 0),
        "down": (0, -1, 0)}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east", "up": "down", "down": "up"}
YAW = {"south": 0.0, "west": 90.0, "north": 180.0, "east": -90.0}
CLOCKWISE = {"north": "east", "east": "south", "south": "west", "west": "north", "up": "up", "down": "down"}


@dataclass(frozen=True)
class Part:
    offset: tuple                 # (dx, dy, dz) from the origin
    item: str                     # item id, or a group token ("stone", "door") resolved to a held member
    facing: str = None            # intended `facing` block-state property
    against: tuple = None         # relative cell to click (hoppers output into it, torches hang on it)
    role: str = None              # "input" / "fuel" / "output" containers etc.
    either_way: bool = False      # the opposite facing works as well (doors)


@dataclass(frozen=True)
class Blueprint:
    name: str
    doc: str
    parts: tuple
    access: tuple = (0, 0, -2)    # relative standing spot from which every part is in reach
    tags: tuple = field(default=())
    clear: tuple = ()             # relative cells that must be free (interior, door top); y=0 ones need a floor


AUTO_SMELTER = Blueprint(
    name="auto_smelter",
    doc="Hopper-fed furnace: raw items into the top chest, fuel into the side chest, results collect in the bottom "
        "chest. Lets the agent drop a stack of ore and go work while it smelts (10 s/item).",
    parts=(
        Part((0, 0, 0), "minecraft:chest", role="output"),
        Part((0, 1, 0), "minecraft:hopper", facing="down", against=(0, 0, 0)),
        Part((0, 2, 0), "minecraft:furnace", role="furnace"),
        Part((0, 3, 0), "minecraft:hopper", facing="down", against=(0, 2, 0)),
        Part((0, 4, 0), "minecraft:chest", role="input"),
        Part((1, 2, 0), "minecraft:hopper", facing="west", against=(0, 2, 0)),
        Part((1, 3, 0), "minecraft:chest", role="fuel"),
    ),
    tags=("smelting",),
)

S = "stone"
SHELTER = Blueprint(
    name="shelter",
    doc="Two-cell hut, room for a bed: stone walls, a door, a torch inside, a roof. 14 stone + door + torch. "
        "Mob-proof; built where night would otherwise catch the agent far from any site.",
    parts=(
        Part((-1, 0, 0), S), Part((2, 0, 0), S), Part((0, 0, 1), S), Part((1, 0, 1), S), Part((1, 0, -1), S),
        Part((0, 0, -1), "door", facing="south", either_way=True),
        Part((-1, 1, 0), S), Part((2, 1, 0), S), Part((0, 1, 1), S), Part((1, 1, 1), S), Part((1, 1, -1), S),
        Part((0, 1, 0), "minecraft:torch", against=(0, 1, 1)),
        Part((-1, 2, 0), S), Part((2, 2, 0), S), Part((0, 2, 0), S), Part((1, 2, 0), S),
    ),
    access=(0, 0, -2),
    clear=((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 1, -1)),
    tags=("shelter",),
)

O = "minecraft:obsidian"
NETHER_PORTAL = Blueprint(
    name="nether_portal",
    doc="Minimal 4×5 Nether portal frame in the x-y plane: 10 obsidian, stone corners (they only give the next "
        "block something to be placed against). Lit with flint and steel on the inner bottom block.",
    parts=(
        Part((0, 0, 0), S), Part((3, 0, 0), S), Part((1, 0, 0), O), Part((2, 0, 0), O),
        Part((0, 1, 0), O), Part((3, 1, 0), O),
        Part((0, 2, 0), O), Part((3, 2, 0), O),
        Part((0, 3, 0), O), Part((3, 3, 0), O),
        Part((0, 4, 0), S), Part((3, 4, 0), S), Part((1, 4, 0), O), Part((2, 4, 0), O),
    ),
    access=(1, 0, -2),
    clear=((1, 1, 0), (2, 1, 0), (1, 2, 0), (2, 2, 0), (1, 3, 0), (2, 3, 0)),
    tags=("portal",),
)

REGISTRY = {bp.name: bp for bp in (AUTO_SMELTER, SHELTER, NETHER_PORTAL)}


def clear_cells(bp, origin, turns=0):
    out = []
    for c in bp.clear:
        d = rotate_offset(c, turns)
        out.append((origin[0] + d[0], origin[1] + d[1], origin[2] + d[2]))
    return out


def materials(bp):
    out = {}
    for part in bp.parts:
        out[part.item] = out.get(part.item, 0) + 1
    return out


def footprint(bp):
    """All cells the machine occupies, relative."""
    return [p.offset for p in bp.parts]


def rotate_offset(offset, turns):
    x, y, z = offset
    for _ in range(turns % 4):
        x, z = -z, x
    return x, y, z


def rotate_dir(direction, turns):
    for _ in range(turns % 4):
        direction = CLOCKWISE[direction]
    return direction


def placed(bp, origin, turns=0):
    """The blueprint's parts in world coordinates: [(pos, part, facing, against_pos)]."""
    out = []
    for part in bp.parts:
        d = rotate_offset(part.offset, turns)
        pos = (origin[0] + d[0], origin[1] + d[1], origin[2] + d[2])
        against = None
        if part.against is not None:
            a = rotate_offset(part.against, turns)
            against = (origin[0] + a[0], origin[1] + a[1], origin[2] + a[2])
        facing = rotate_dir(part.facing, turns) if part.facing else None
        out.append((pos, part, facing, against))
    return out


def remaining(bp, origin, turns, name_at):
    """Pure: materials still missing from a started build — {item token: count} for parts whose cell doesn't hold
    the part yet. `name_at(pos)` → block name there. The goal must ask for these, not the full list: it once asked
    for all 10 obsidian while 5 stood in the frame, and mined its own frame to get them."""
    need = {}
    for pos, part, *_ in placed(bp, origin, turns):
        name = name_at(pos).split(":")[-1]
        token = part.item
        ok = name == token.split(":")[-1] or (token == "stone" and name in ("cobblestone", "stone", "cobbled_deepslate"))
        if not ok:
            need[token] = need.get(token, 0) + 1
    return need


def access_spot(bp, origin, turns=0):
    d = rotate_offset(bp.access, turns)
    return origin[0] + d[0], origin[1] + d[1], origin[2] + d[2]


def look_for(facing, rule):
    """Body orientation (yaw, pitch) that should give `facing` under a rule: "toward_player" (the block faces the
    player, so look the opposite way) or "away_from_player" (the block faces where the player looks)."""
    look = OPPOSITE[facing] if rule == "toward_player" else facing
    if look == "up":
        return None, -90.0
    if look == "down":
        return None, 90.0
    return YAW[look], 0.0
