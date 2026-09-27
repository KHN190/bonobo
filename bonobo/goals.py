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
    effect(effect, n)    whatever skill provides `effect` (skill.providers: "breed", "light", "repair:pickaxe",
                         "state:sheltered", …), with `detail` for its adapter                done = its plan ran

Done is asked of the WORLD (bag, position, memory of what was built), never of a plan: a plan can be empty because
the work is finished, because it is under way somewhere else (a furnace), or because nothing can be planned.
"""
import math

from .data import bare
from .knowledge import DRAGON_BEDS, food_count, kit_needs
from .planner import tool_ok

TEMPLATES = ("have", "craft", "milestone", "goto", "road", "build", "sleep", "skill", "effect")
ITEM_GOALS = ("have", "craft", "milestone")
# Which solver a goal is planned by when its task names none: combined goals (a milestone is a set of things to hold
# at once) go to the column solver, which orders them together; the rest to the default. Either falls back to
# every registered solver when it cannot plan.
SOLVER_FOR = {"milestone": "solve"}
# Goals whose "done" is that their plan ran: nothing in the world says a skill was run or a road walked.
RUN_ONCE = ("road", "skill", "effect")
# Milestones whose plan goes on past holding things (decompose.THEN): done when that plan has run.
RUN_AFTER = ("end portal",)

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
    "end portal": [["minecraft:ender_eye", 12]],     # then find and light it (decompose.THEN)
    "dragon beds": [["bed", DRAGON_BEDS]],
}
# What the brain prepares when the queue is empty, first unmet first: tools, food, light.
PREPARE = [[["tool", "pickaxe", 1]], [["tool", "sword", 1]], [["food", 8]], [["minecraft:torch", 8]]]
# The night's idle work under cover (brain.plan_proposals → "night stock"): ore below, dug down to, first not held.
NIGHT_STOCK = [[["minecraft:raw_iron", 16]], [["minecraft:diamond", 3]]]


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


# Every goal kind states what it wants as a pure function of what the world shows — the remainder still to do,
# {} when met. A task's progress is never a counter or a step index: each round the remainder is read again, and
# only that is planned for (the held plan is a cache of how, redone when the remainder changes).
DESIRED = {}


def desired(*templates):
    """Register the remainder function for goal `templates`: fn(goal, snap, mem) → {what: how much is missing}
    ({} = met), or None for a goal the world cannot judge (it is done when its plan ran: RUN_ONCE)."""
    def wrap(fn):
        for t in templates:
            DESIRED[t] = fn
        return fn
    return wrap


def reconcile(want, have):
    """Pure: what of `want` ({key: amount}) `have` does not cover — {key: missing}, {} when all is there."""
    return {k: n - have.get(k, 0) for k, n in want.items() if have.get(k, 0) < n}


def remainder(goal, snap, mem):
    """The goal's remainder from the world now ({} = done), or None when only its plan running can say."""
    return DESIRED[goal["goal"]](goal, snap, mem)


@desired(*ITEM_GOALS)
def _held_remainder(goal, snap, mem):
    if goal["goal"] == "milestone" and goal.get("args", {}).get("name") in RUN_AFTER:
        return None                       # its plan ends in doing (find the stronghold, light the portal)
    rows = needs(goal, snap.inv)
    items = {r[0]: int(r[1]) for r in rows if r[0] != "tool"}
    out = reconcile(items, {t: held(snap.inv, t) for t in items})
    for r in rows:
        if r[0] == "tool" and not tool_ok(snap.inv, r[1], int(r[2])):
            out[f"tool:{r[1]}"] = int(r[2])
    return out


@desired("goto")
def _goto_remainder(goal, snap, mem):
    args = goal["args"]
    away = math.dist(snap.feet, tuple(args["pos"])) - (float(args.get("range", 2)) + 1)
    return {"blocks away": round(away, 1)} if away > 0 else {}


@desired("build")
def _build_remainder(goal, snap, mem):
    args = goal["args"]
    bp = args["bp"]
    if bp == "shelter":
        at = args.get("at")
        built = any(at is None or math.dist(s["pos"], at) <= 8 for s in mem.sites(snap.dimension, kinds=["shelter"]))
    else:
        dim = "minecraft:overworld" if bp == "nether_portal" else snap.dimension
        built = any(m["blueprint"] == bp for m in mem.machines(dim))
    return {} if built else {f"built:{bp}": 1}


@desired("sleep")
def _sleep_remainder(goal, snap, mem):
    return {"night": 1} if snap.night else {}


@desired(*RUN_ONCE)
def _ran(goal, snap, mem):
    return None                           # nothing in the world says a skill was run or a road walked


def _registered():
    missing = [t for t in TEMPLATES if t not in DESIRED]
    if missing:
        raise TypeError(f"goal kinds without a desired state: {', '.join(missing)}")


_registered()


def done(goal, snap, mem):
    """True / False from the world (`remainder` is empty or not); None for a goal done when its plan has run."""
    rest = remainder(goal, snap, mem)
    return None if rest is None else not rest


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
    if template == "effect":
        return f"effect {args['effect']}" + (f" ×{args['count']}" if args.get("count", 1) != 1 else "")
    return template
