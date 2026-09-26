"""Goals as data: what a task asks for, how to tell it is done, what the planner needs for it. No decisions here.

A goal is `{"goal": template, "args": {...}}` — JSON, because it lives in tasks.json and is written by the cerebrum.

    have(needs)          hold these: [[token, n], ...] or [["tool", kind, tier], ...]   done = the bag says so
    craft(needs)         the same as `have`: crafting is how most things are had
    milestone(name)      a named set of `have`s (MILESTONES), in the order the run needs them
    goto(pos, range)     be there                                                        done = standing there
    road(a, b)           walk a → b once, so the leg becomes a known road (roads.py)      done = its plan ran
    build(bp, at)        a blueprint standing at `at` (or near home / here)              done = memory has the machine
    sleep()              skip the night in a bed                                         done = it is day
    skill(name, args)    run one registered skill                                        done = its plan ran

Done is asked of the WORLD (bag, position, memory of what was built), never of a plan: a plan can be empty because
the work is finished, because it is under way somewhere else (a furnace), or because nothing can be planned.
"""
import math

from .data import bare
from .knowledge import DRAGON_BEDS, food_count, kit_needs
from .planner import tool_ok

TEMPLATES = ("have", "craft", "milestone", "goto", "road", "build", "sleep", "skill")
ITEM_GOALS = ("have", "craft", "milestone")
# Goals whose "done" is that their plan ran: nothing in the world says a skill was run or a road walked.
RUN_ONCE = ("road", "skill")

# The run, as named sets of things to hold, in the order the old goal list reached for them (brain.goals).
MILESTONES = {
    "stone tools": [["tool", "pickaxe", 1], ["tool", "sword", 1], ["tool", "axe", 1]],
    "station kit": [["minecraft:crafting_table", 1], ["minecraft:furnace", 1]],
    "food": [["food", 8]],
    "bed": [["bed", 1]],
    "torches": [["minecraft:torch", 24]],
    "building blocks": [["stone", 64]],
    "iron pickaxe": [["tool", "pickaxe", 2]],
    "iron tools": [["tool", "pickaxe", 2], ["tool", "sword", 2], ["minecraft:bucket", 1], ["minecraft:shield", 1],
                   ["minecraft:flint_and_steel", 1]],
    "iron armor": [["minecraft:iron_helmet", 1], ["minecraft:iron_chestplate", 1], ["minecraft:iron_leggings", 1],
                   ["minecraft:iron_boots", 1]],
    "water bucket": [["minecraft:water_bucket", 1]],
    "nether kit": "kit",                   # computed from the bag (knowledge.kit_needs)
    "blaze rods": [["minecraft:blaze_rod", 7]],
    "ender pearls": [["minecraft:ender_pearl", 12]],
    "eyes of ender": [["minecraft:ender_eye", 12]],
    "dragon beds": [["bed", DRAGON_BEDS]],
}
# What the brain prepares when the queue is empty, first unmet first: tools, food, light.
PREPARE = [[["tool", "pickaxe", 1]], [["tool", "sword", 1]], [["food", 8]], [["minecraft:torch", 8]]]


def make(template, **args):
    if template not in TEMPLATES:
        raise ValueError(f"unknown goal {template!r}: expected one of {', '.join(TEMPLATES)}")
    if template == "milestone" and args.get("name") not in MILESTONES:
        raise ValueError(f"unknown milestone {args.get('name')!r}: {', '.join(MILESTONES)}")
    return {"goal": template, "args": args}


def have(*needs):
    """have(("minecraft:torch", 24), ("tool", "pickaxe", 2))"""
    return make("have", needs=[list(n) for n in needs])


def parse_need(token, n=1):
    """A need from the command line: `tool:pickaxe:2` or `minecraft:torch 24`."""
    if token.startswith("tool:"):
        _, kind, tier = token.split(":")
        return ["tool", kind, int(tier)]
    return [token, int(n)]


def needs(goal, inv):
    """Planner needs (tuples) for an item goal, or [] for the others."""
    template, args = goal["goal"], goal.get("args", {})
    if template in ("have", "craft"):
        rows = args.get("needs", [])
    elif template == "milestone":
        rows = MILESTONES[args["name"]]
        if rows == "kit":
            return list(kit_needs(inv))
    else:
        return []
    return [tuple(r) for r in rows]


def held(inv, token):
    """How many of `token` the bag holds, groups and "food" (cooked meals) included."""
    if token == "food":
        return food_count(inv)
    return inv.count(token)


def short(inv, need_rows):
    """What of these is not held yet, as text; empty when everything is."""
    out = []
    for need in need_rows:
        if need[0] == "tool":
            if not tool_ok(inv, need[1], int(need[2])):
                out.append(f"{need[1]} tier {need[2]}")
            continue
        token, n = need[0], int(need[1])
        have_n = held(inv, token)
        if have_n < n:
            out.append(f"{bare(token)} {have_n}/{n}")
    return ", ".join(out)


def done(goal, snap, mem):
    """True / False from the world; None for a goal that is done when its plan has run (RUN_ONCE)."""
    template, args = goal["goal"], goal.get("args", {})
    if template in ITEM_GOALS:
        return not short(snap.inv, needs(goal, snap.inv))
    if template == "goto":
        return math.dist(snap.feet, tuple(args["pos"])) <= float(args.get("range", 2)) + 1
    if template == "build":
        bp = args["bp"]
        if bp == "shelter":
            at = args.get("at")
            return any(at is None or math.dist(s["pos"], at) <= 8 for s in mem.sites(snap.dimension, kinds=["shelter"]))
        dim = "minecraft:overworld" if bp == "nether_portal" else snap.dimension
        return any(m["blueprint"] == bp for m in mem.machines(dim))
    if template == "sleep":
        return not snap.night
    return None


def describe(goal):
    template, args = goal["goal"], goal.get("args", {})
    if template in ("have", "craft"):
        parts = [f"{r[1]} tier {r[2]}" if r[0] == "tool" else f"{bare(r[0])}×{r[1]}" for r in args.get("needs", [])]
        return f"{template} " + ", ".join(parts)
    if template == "milestone":
        return f"milestone {args['name']}"
    if template == "goto":
        return f"goto {tuple(args['pos'])}"
    if template == "road":
        return f"road {tuple(args['a'])} → {tuple(args['b'])}"
    if template == "build":
        return f"build {args['bp']}" + (f" at {tuple(args['at'])}" if args.get("at") else "")
    if template == "skill":
        return f"skill {args['name']}" + (f" {args.get('args')}" if args.get("args") else "")
    return template
