"""tools: the other tools carried — none, a pickaxe worn below knowledge.TOOL_MIN_DURABILITY (repair, a tool that
breaks under a held plan: needs.tool_kinds), an axe and a shovel (the planner's held tiers, speed_up's kinds).
Swords are the `kit` dimension's (what the fight carries)."""
NAME = "tools"
VALUES = ("none", "worn", "axe_shovel")


def domain():
    return VALUES


def alpha(a):
    from bonobo.knowledge import TOOL_MIN_DURABILITY
    inv = a.snap.inv
    if any(d < TOOL_MIN_DURABILITY for _t, d, _s in inv.tools("pickaxe")):
        return "worn"
    return "axe_shovel" if inv.tools("axe") and inv.tools("shovel") else "none"


def gamma(value, facts, g):
    if value == "worn":
        from bonobo.actions import TOOL_USES
        from bonobo.knowledge import TOOL_MIN_DURABILITY
        g.give("iron_pickaxe")
        g.slots[-1]["damage"] = TOOL_USES["iron"] - (TOOL_MIN_DURABILITY - 1)
    elif value == "axe_shovel":
        g.give("stone_axe")
        g.give("stone_shovel")
