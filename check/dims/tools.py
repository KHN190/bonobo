"""tools: the other tools carried — none, a pickaxe worn below knowledge.TOOL_WORKING (repair, a tool that
breaks under a held plan: needs.tool_kinds), an axe and a shovel (the planner's held tiers, speed_up's kinds).
Swords are the `kit` dimension's (what the fight carries)."""
from bonobo.data import TOOL_USES
NAME = "tools"
VALUES = ("none", "worn", "axe_shovel")


def domain():
    return VALUES


def alpha(a):
    from bonobo.knowledge import TOOL_WORKING
    inv = a.snap.inv
    if any(d < TOOL_WORKING for _t, d, _s in inv.tools("pickaxe")):
        return "worn"
    return "axe_shovel" if inv.tools("axe") and inv.tools("shovel") else "none"


def gamma(value, facts, g):
    if value == "worn":
        from bonobo.data import TOOL_USES
        from bonobo.knowledge import TOOL_WORKING
        g.give("iron_pickaxe")
        g.slots[-1]["damage"] = TOOL_USES["iron"] - (TOOL_WORKING - 1)
    elif value == "axe_shovel":
        g.give("stone_axe")
        g.give("stone_shovel")
