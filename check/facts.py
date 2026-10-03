"""α: the finite facts the decision code reads, each computed by the production predicate it names (imported, never
copied). A fact that comes with another agent's merge is in PENDING, with the interface it will be read through."""
from types import MappingProxyType, SimpleNamespace

from bonobo import knowledge
from bonobo.data import STATION_R
from bonobo.data import FULL_BAR, MAX_HP, POD_BLOCKS, critical_hp, home_box_of
from bonobo.reflexes import EAT_BELOW, STARVE
from bonobo.threat import aggro, context_of
from bonobo.world import Region, is_enclosed

# fact → its finite domain (the order is the band order)
DOMAINS = {
    "dimension": ("minecraft:overworld", "minecraft:the_nether"),
    "night": (False, True),                 # world.Snapshot.night (data.DAY_END..NIGHT_END)
    "hp": ("ok", "crit", "low"),            # data.critical_hp; low: above it, at most half (data.MAX_HP / 2)
    "place": ("open", "enclosed", "home"),  # data.home_box_of, world.is_enclosed, knowledge.under_rock
    "bed": ("none", "carried", "home"),     # inv.count("bed"), memory.home_part("beds")
    "pickaxe": (-1, 0, 1, 2),               # knowledge.held_tiers
    "building": (False, True),              # inv.count("building") ≥ data.POD_BLOCKS
    "food": (False, True),                  # knowledge.food_count > 0
    "tree": (False, True),                  # a log in sight (/find)
    "ore": ("none", "exposed", "buried", "deep"),   # iron ore in sight; exposed by /find exposed; deep: gather.deep_below
    "threat": (False, True),                # threat.aggro over /entities within the mob's notice radius
    "takeover": (False, True),              # /state control.paused (the player holds the body)
    "queued": ("none", "stick", "cobblestone"),     # the head live task's item (tasks.load): a craft, a mine
    "cooled": (False, True),                # every night way cooling after a failure here (decompose.cooled_ways)
    "hunger": ("full", "low", "starve"),    # /state food against reflexes.EAT_BELOW, reflexes.STARVE
    "station": ("none", "crafting_table", "furnace"),   # memory.stations within actions.STATION_R
    # the threat's kind (its first value when there is no threat); a neutral one provoked (threat.aggro)
    "mob": ("zombie", "skeleton", "creeper", "spider", "enderman", "blaze"),
}
# a fact read only while (pred, the facts that turn it on) holds: else its first value (one state, not many)
DEPENDS = {"mob": (lambda f: f["threat"], {"threat": True}),
           "night": (lambda f: f["dimension"] == "minecraft:overworld", {"dimension": "minecraft:overworld"})}
# facts the decision reads that come with another agent's merge: the interface this checker wires to, fixed now
PENDING = {
    "F1": "F1 priced candidates: the round's alternatives [(name, seconds, steps)] from the planner "
          "(R1 tool tiers, R2 detours, R4 ways, D4 within PLAN by seconds, D6 est == the steps as run; "
          "V1: gather's nearest-3 cut, V2: highest craftable tier); the old planner is not patched for it",
}


def _with_dims():
    """The dimension modules (check/dims) joined to the base: their domains after the base's, their DEPENDS."""
    from .dims import DIMS
    for d in DIMS:
        assert d.NAME not in DOMAINS, f"dimension {d.NAME} defined twice"
        DOMAINS[d.NAME] = tuple(d.domain())
        if getattr(d, "DEPENDS", None) is not None:
            DEPENDS[d.NAME] = d.DEPENDS
    return DIMS


DIMS = _with_dims()


def _region(world, feet):
    lo, hi = tuple(c - 3 for c in feet), tuple(c + 3 for c in feet)
    return Region.of(lo, hi, dict(world._cells(lo, hi)))


def gold_worn(inv):
    """A golden armour piece on (perception.kit's gold_worn: a piglin leaves the gold-clad alone)."""
    return any(str((inv.equipment.get(k) or {}).get("id", "")).startswith("minecraft:golden_")
               for k in ("head", "chest", "legs", "feet"))


def threat_entities(snap, world):
    """The mobs after us, nearest first: /entities rows threat.aggro holds hostile, within their notice radius."""
    from bonobo.beliefs import MOBS
    ctx = context_of(snap.state, {"gold_worn": gold_worn(snap.inv)})
    ents = world._entities({"radius": "48"})["entities"]
    return sorted((e for e in ents if aggro(e, ctx) and e["type"] in MOBS
                   and e["distance"] <= float(MOBS[e["type"]].get("notice_r", 16))), key=lambda e: e["distance"])


def hp_of(s):
    """crit at data.critical_hp, low up to half the bar (below a fight's comfort, above the floor), else ok."""
    hp = float(s.get("health", MAX_HP))
    return "crit" if hp <= critical_hp(s) else "low" if hp <= MAX_HP / 2 else "ok"


def hunger_of(food):
    return "starve" if food <= STARVE else "low" if food < EAT_BELOW else "full"


def _queued(items):
    """The first live task whose first item is a `queued` value (tasks.LIVE), else "none": a task another dimension
    queues (a hunt) is that dimension's fact, not this one's."""
    from bonobo import tasks
    for t in items:
        if t["state"] in tasks.LIVE:
            needs = t.get("args", {}).get("needs") or [[""]]
            item = str(needs[0][0]).removeprefix("minecraft:")
            if item in DOMAINS["queued"]:
                return item
    return "none"


def alpha(snap, mem, world, brain):
    """Facts of one state (snapshot + memory + the stub's world + the round's brain: its retry), each through its
    production predicate."""
    from bonobo import gather, tasks
    from bonobo.decompose import cooled_ways
    s, inv, feet = snap.state, snap.inv, snap.feet
    boxes = [tuple(map(tuple, b)) for h in mem.homes(snap.dimension) for b in h.get("boxes", ())]
    region = _region(world, feet)
    place = ("home" if home_box_of(boxes, feet) is not None
             else "enclosed" if knowledge.under_rock(s.get("skyLight", 15)) or is_enclosed(region, feet) else "open")
    home_bed = mem.home_part("beds", snap.dimension, feet, anywhere=True)
    iron = world._find({"blocks": "iron_ore", "radius": "48"})["blocks"]
    exposed = world._find({"blocks": "iron_ore", "radius": "48", "exposed": "true"})["blocks"]
    ore = ("none" if not iron else "exposed" if exposed
           else "deep" if gather.deep_below((iron[0]["x"], iron[0]["y"], iron[0]["z"]), feet) else "buried")
    threats = threat_entities(snap, world)
    threat = bool(threats)
    ready = brain.ready
    a = SimpleNamespace(snap=snap, mem=mem, world=world, ready=ready, region=region, threats=threats, brain=brain)
    return MappingProxyType({
        **{d.NAME: d.alpha(a) for d in DIMS},
        "dimension": snap.dimension, "night": bool(snap.night), "hp": hp_of(s),
        "place": place, "bed": "carried" if inv.count("bed") else "home" if home_bed is not None else "none",
        "pickaxe": knowledge.held_tiers(inv).get("pickaxe", -1), "building": inv.count("building") >= POD_BLOCKS,
        "food": knowledge.food_count(inv) > 0,
        "tree": bool(world._find({"blocks": "oak_log", "radius": "48"})["blocks"]), "ore": ore,
        "threat": threat, "takeover": bool(s.get("control", {}).get("paused")),
        "hunger": hunger_of(float(s.get("food", FULL_BAR))),
        "station": next((st["block"].removeprefix("minecraft:") for st in
                         mem.stations(snap.dimension, near=snap.feet, within=STATION_R)), "none"),
        "mob": threats[0]["type"].removeprefix("minecraft:") if threats else DOMAINS["mob"][0],
        "queued": _queued(tasks.load()), "cooled": set(cooled_ways(ready)) == set(night_ways()),
    })


def night_ways():
    """Every night way's name (cooled_ways with nothing ready)."""
    from bonobo.decompose import cooled_ways
    return cooled_ways(lambda _k: False)


def key(facts):
    return tuple(facts[k] for k in DOMAINS)


def of(**kw):
    """Facts from keyword values (the rest, a DEPENDS fact whose condition is off, and a dimension's value its
    `valid` refuses here: its `instead`, else the domain's first value)."""
    bad = {k: v for k, v in kw.items() if k not in DOMAINS or v not in DOMAINS[k]}
    if bad:     # a state kept against an older domain (a renamed value, a dropped fact) is no state of this one
        raise ValueError(f"facts outside their domains: {bad}")
    out = {k: kw.get(k, d[0]) for k, d in DOMAINS.items()}
    for k, (on, _witness) in DEPENDS.items():
        if not on(out):
            out[k] = DOMAINS[k][0]
    for d in DIMS:
        if hasattr(d, "valid") and not d.valid(out[d.NAME], out):
            out[d.NAME] = d.instead(out) if hasattr(d, "instead") else DOMAINS[d.NAME][0]
    return MappingProxyType(out)

