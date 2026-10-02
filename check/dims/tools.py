"""tools: the other tools carried — none, a pickaxe worn below knowledge.TOOL_MIN_DURABILITY (repair, a tool that
breaks under a held plan: needs.tool_kinds), a sword and an axe (the planner's held tiers, speed_up's kinds)."""
NAME = "tools"
VALUES = ("none", "worn", "kit")


def domain():
    return VALUES


def alpha(a):
    from bonobo.knowledge import TOOL_MIN_DURABILITY
    inv = a.snap.inv
    if any(d < TOOL_MIN_DURABILITY for _t, d, _s in inv.tools("pickaxe")):
        return "worn"
    return "kit" if inv.tools("sword") and inv.tools("axe") else "none"


def gamma(value, facts, g):
    if value == "worn":
        from bonobo.actions import TOOL_USES
        from bonobo.knowledge import TOOL_MIN_DURABILITY
        g.give("iron_pickaxe")
        g.slots[-1]["damage"] = TOOL_USES["iron"] - (TOOL_MIN_DURABILITY - 1)
    elif value == "kit":
        g.give("stone_sword")
        g.give("stone_axe")
