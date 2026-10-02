"""upkeep_held: what the brain's held plans ask of upkeep (needs.Needs.propose reads every held plan) — none; a tool
a held plan mines with broke last round (needs.broke: nearly worn out then, none working now → a repair need); a held
plan with a fall in it (needs.FALL_RISK: a blaze hunt) and no water bucket (needs.needs_water_bucket)."""
NAME = "upkeep_held"
VALUES = ("none", "broke", "fall")
OWN = "check-upkeep"         # a held plan of no queued task: upkeep reads it, the queue never runs it
DEPENDS = (lambda f: f["dimension"] == "minecraft:overworld", {})


def domain():
    return VALUES


def valid(value, facts):
    return value != "broke" or facts["pickaxe"] == -1     # broke: no pickaxe working now


def _steps(value):
    from bonobo.decompose import Step
    if value == "broke":
        return [Step("mine", "minecraft:iron_ore", 1, {"blocks": ["minecraft:iron_ore"], "tier": 1})]
    return [Step("hunt", "minecraft:blaze_rod", 1, {"types": ["minecraft:blaze"]})]


def prepare(brain, facts):
    value = facts["upkeep_held"]
    if value == "none":
        return
    from bonobo.needs import NEAR_BREAK
    brain.held[OWN] = {"steps": _steps(value), "sig": None, "event": False, "dim": facts["dimension"], "want": {}}
    if value == "broke":
        brain.needs.wear = {"pickaxe": NEAR_BREAK}       # last round: nearly worn out


def alpha(a):
    h = getattr(a.brain, "held", {}).get(OWN) if a.brain is not None else None
    if h is None:
        return "none"
    return "broke" if any(s.kind == "mine" for s in h["steps"]) else "fall"


def gamma(value, facts, g):
    pass                 # the brain's own state: set by prepare
