"""The shapes the package passes around, named once for the type checker: the game's readings (/state, /inventory,
/entities, /task), a block cell, the task dicts the jar's TaskFactory reads (tests/test_task_schema checks the
keys), and the interrupt vocabulary (data.EXCEPTIONS, arbiter.RESUME_OF). Types only: imports nothing of ours."""
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from .world import Inventory, Region

Cell = tuple[int, int, int]          # a block position: x, y, z
Pos = tuple[float, float, float]     # an exact position (feet, an entity)


# ------------------------------------------------------------------ readings (the jar's WorldInfo / InvUtil)
class Stack(TypedDict):
    """InvUtil.stackJson: one item stack."""
    id: str
    count: int
    damage: NotRequired[int]
    maxDamage: NotRequired[int]
    enchanted: NotRequired[bool]
    potion: NotRequired[str]


class Slot(Stack):
    """An /inventory slot: a stack and where it sits (0-35)."""
    slot: int


Equipment = dict[str, Stack]         # head, chest, legs, feet, offhand


class InventoryReading(TypedDict):
    """GET /inventory: the 36 main slots that hold something, and what is worn."""
    slots: list[Slot]
    selectedSlot: int
    equipment: Equipment


class LookingAt(TypedDict):
    kind: Literal["block", "entity", "none"]
    block: NotRequired[str]
    x: NotRequired[int]
    y: NotRequired[int]
    z: NotRequired[int]
    side: NotRequired[str]
    entity: NotRequired[int]


TaskStatus = Literal["running", "succeeded", "failed", "cancelled"]     # the jar's Task.Status, lowercased (Task.toJson)


class TaskResult(TypedDict):
    """Task.toJson: GET /task?id=, a posted chain's entries, /state's control.task."""
    id: int
    type: str
    status: TaskStatus
    message: str
    seconds: float
    doing: str
    result: Any


class Control(TypedDict):
    active: bool
    paused: bool
    allowed: bool
    task: TaskResult | None
    queued: int


class StateReading(TypedDict):
    """GET /state (WorldInfo.state)."""
    x: float
    y: float
    z: float
    blockX: int
    blockY: int
    blockZ: int
    yaw: float
    pitch: float
    dimension: str
    blockLight: int
    skyLight: int
    timeOfDay: int
    gameTime: int
    health: float
    maxHealth: float
    food: int
    saturation: float
    air: int
    armor: int
    xpLevel: int
    onGround: bool
    inWater: bool
    climbing: bool
    inPortal: bool
    inLava: bool
    onFire: bool
    dead: bool
    selectedSlot: int
    mainHand: Stack
    screen: str
    lookingAt: LookingAt
    control: Control


class EntityReading(TypedDict):
    """One of GET /entities' `entities` (WorldInfo.entities), nearest first."""
    id: int
    type: str
    x: float
    y: float
    z: float
    distance: float
    hostile: bool
    health: NotRequired[float]
    phase: NotRequired[int]       # the dragon's
    angry: NotRequired[bool]      # an enderman's
    baby: NotRequired[bool]       # a passive mob's
    item: NotRequired[Stack]      # a dropped item's
    velocity: NotRequired[list[float]]    # blocks per tick


class BagState(TypedDict):
    """The least a bag-only `*_commands` builder reads (a BodyState is one too)."""
    inv: Inventory


class BodyState(TypedDict):
    """skillcore.body_state: what a pure `*_commands` builder reads — one /state, the bag, the cells the policy
    protects, the blocks around (None when the builder reads none), and what the caller adds."""
    state: StateReading
    feet: Cell
    inv: Inventory
    protected: set[Cell]
    region: Region | None
    spots: NotRequired[list]             # survive.light_area: the dark spots to light
    entities: NotRequired[list[EntityReading]]
    cooling: NotRequired[list]           # farming.breed: pens bred lately
    threats: NotRequired[list]           # fight_loop: the threat rows and their entity ids
    threat_ids: NotRequired[list[int]]


# ------------------------------------------------------------------ task dicts (the jar's TaskFactory.create)
class AttackTask(TypedDict):
    type: Literal["attack"]
    entity: int
    footwork: NotRequired[Any]
    item: NotRequired[str]
    keepOff: NotRequired[float]
    shield: NotRequired[bool]


class BedBombTask(TypedDict):
    type: Literal["bed_bomb"]
    item: str
    x: int
    y: int
    z: int


class CollectTask(TypedDict):
    type: Literal["collect"]
    idle: NotRequired[Any]
    only: NotRequired[list[str]]
    radius: NotRequired[float]
    x: NotRequired[float]
    y: NotRequired[float]
    z: NotRequired[float]


class CraftTask(TypedDict):
    type: Literal["craft"]
    pattern: Any
    count: NotRequired[int]


class EatTask(TypedDict):
    type: Literal["eat"]
    item: NotRequired[str]


class GotoTask(TypedDict):
    type: Literal["goto"]
    x: float
    y: float
    z: float
    partial: NotRequired[bool]
    range: NotRequired[float]
    sprint: NotRequired[bool]
    useBoat: NotRequired[bool]


class InputTask(TypedDict):
    type: Literal["input"]
    keys: Any
    ticks: NotRequired[int]
    until: NotRequired[Any]
    yaw: NotRequired[float]


class InteractTask(TypedDict):
    type: Literal["interact"]
    entity: int
    item: NotRequired[str]


class LookTask(TypedDict):
    type: Literal["look"]
    pitch: NotRequired[float]
    yaw: NotRequired[float]
    x: NotRequired[float]
    y: NotRequired[float]
    z: NotRequired[float]


class MineTask(TypedDict):
    type: Literal["mine"]
    x: int
    y: int
    z: int
    avoid: NotRequired[Any]
    collect: NotRequired[bool]
    down: NotRequired[bool]
    item: NotRequired[str]
    only: NotRequired[Any]
    requireDrops: NotRequired[bool]


class PillarTask(TypedDict):
    type: Literal["pillar"]
    item: str


class PlaceTask(TypedDict):
    type: Literal["place"]
    item: str
    x: int
    y: int
    z: int
    against: NotRequired[Any]
    avoid: NotRequired[Any]
    facing: NotRequired[str]


# functional form: the jar reads a "break" key, a keyword in a class body
TravelTask = TypedDict("TravelTask", {
    "type": Literal["travel"], "x": float, "y": float, "z": float, "avoid": NotRequired[Any], "break": NotRequired[bool],
    "item": NotRequired[str], "place": NotRequired[bool], "placeBudget": NotRequired[int], "range": NotRequired[float],
    "voidBridge": NotRequired[bool], "sprint": NotRequired[bool]})


class UseTask(TypedDict):
    type: Literal["use"]
    x: int
    y: int
    z: int
    avoid: NotRequired[Any]


class UseItemTask(TypedDict):
    type: Literal["use_item"]
    item: str
    holdTicks: NotRequired[int]
    onBlock: NotRequired[bool]
    pitch: NotRequired[float]
    x: NotRequired[float]
    y: NotRequired[float]
    yaw: NotRequired[float]
    z: NotRequired[float]


class WaitTask(TypedDict):
    type: Literal["wait"]
    ticks: NotRequired[int]


Task = (AttackTask | BedBombTask | CollectTask | CraftTask | EatTask | GotoTask | InputTask | InteractTask | LookTask
        | MineTask | PillarTask | PlaceTask | TravelTask | UseTask | UseItemTask | WaitTask)

# ------------------------------------------------------------------ the interrupt vocabulary
# the cause a failure is counted and cooled under (data.EXCEPTIONS' first column, retry's BACKSTOP keys)
Cause = Literal["error", "game", "unavailable", "nav", "stuck", "tool", "replan", "interrupt"]
# an interrupt source (EXCEPTIONS' second column, every key of arbiter.RESUME_OF; tests/test_types keeps them equal)
Source = Literal[
    "layer:reflex", "layer:safety", "layer:maintain", "layer:plan", "layer:tactic",
    "hazard:lava", "hazard:burning", "hazard:drowning", "hazard:suffocating", "hazard:critical", "hazard:threat",
    "hazard:swimming", "hazard:falling",
    "row:eat", "row:dig out", "row:sleep", "row:shelter", "row:collect job", "row:collect machine",
    "row:path blocked", "row:unstuck", "row:recover items", "row:leave the pit", "row:empty the bag",
    "row:leave the Nether",
    "manual", "player", "game lost", "jar reflex", "death", "dimension change", "night", "user cancel", "stuck",
    "crash"]
# what arbiter.RESUME_RULES does about a source
Rule = Literal["same", "recheck", "recover", "dimension", "handback", "stand down", "fight", "game", "night", "none",
               "cooled", "crashed"]
Outcome = Literal["ok", "interrupted", "failed"]
