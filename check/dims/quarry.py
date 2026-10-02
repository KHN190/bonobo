"""An optional fight the plan asks for: a queued task for a drop only a fighter gives (string: a spider, an ender
pearl: an enderman), the mob in sight, a sword carried (the `kit` dimension's: read only with it) — the round plans the hunt and offers it under the fight
line (brain.fight_line_holds → estimate.fight_line_ok). α: the live task naming one of these drops (tasks.load)."""
from bonobo import goals, tasks

NAME = "quarry"
VALUES = ("none", "spider", "enderman")
DROP = {"spider": "minecraft:string", "enderman": "minecraft:ender_pearl"}
AT = (20.5, 64.0, 0.5)              # in sight (/entities 48), beyond a spider's notice (16): no threat even by night
DEPENDS = (lambda f: f["kit"] == "sword", {"kit": "sword"})


def domain():
    return VALUES


def alpha(a):
    wanted = {need[0] for t in tasks.load() if t["state"] in tasks.LIVE for need in t["args"].get("needs", ())}
    return next((mob for mob, drop in DROP.items() if drop in wanted), "none")


def gamma(value, facts, g):
    if value == "none":
        return
    tasks.add(goals.have((DROP[value], 1)))
    x, y, z = AT
    g.entities.append({"id": 200, "type": f"minecraft:{value}", "x": x, "y": y, "z": z, "health": 20.0})
