"""Goals as data: what a task asks for, how to tell it is done, what the planner needs for it. No decisions here. A goal is `{"goal": template, "args": {...}}` — JSON, because it lives in tasks.json and is written by the cerebrum. have(needs)          hold these: [[token, n], ...] or [["tool", kind, tier], ...]   done = the bag says so craft(needs)         the same as `have`: crafting is how most things are had milestone(name)      a named set of `have`s (MILESTONES), in the order the run needs them goto(pos, range)     be there                                                        done = standing there road(a, b)           walk a → b once, so the leg becomes a known road (roads.py)      done = its plan ran build(bp, at)        a blueprint standing at `at` (or near home / here)              done = memory has the machine sleep()              skip the night in a bed                                         done = it is day skill(name, args)    run one registered skill                                        done = its plan ran effect(effect, n)    whatever skill provides `effect` (skill.providers: "breed", "light", "repair:pickaxe", "state:sheltered", …), with `detail` for its adapter                done = its plan ran Done is asked of the WORLD (bag, position, memory of what was built), never of a plan: a plan can be empty because the work is finished, because it is under way somewhere else (a furnace), or because nothing can be planned."""

import functools
import math

from .data import TIER_OF_MATERIAL, TOOL_KINDS, bare
from .data import END_PORTAL_OPEN  # noqa: F401  (the site kind memory keeps once the end portal is lit)
from .game import WAYPOINT_R
from .knowledge import (DRAGON_BEDS, blocks_remainder, have_remainder, held_count, kit_needs, reconcile,  # noqa: F401
                        tool_ok)

TEMPLATES = ("have", "craft", "milestone", "goto", "road", "build", "sleep", "skill", "effect")
ITEM_GOALS = ("have", "craft", "milestone")
# Goals ended by their own step's contract (no world state names a skill run).
RUN_ONCE = ("skill", "effect")
# Milestones whose plan goes on past holding things (decompose.THEN): done when that plan has run.
RUN_AFTER = ("end portal",)
# Milestones off the speedrun route: never the run's next target nor its route's seconds; only "used later" (en-route).
OFF_ROUTE = ("diamond tools",)

# the run as named sets of things to hold, in order
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
    # OFF_ROUTE: diamonds "used later" for en-route (brain.enroute_wanted); every tool kind at the diamond tier
    "diamond tools": [["tool", kind, TIER_OF_MATERIAL["diamond"]] for kind in TOOL_KINDS],
}
# the night's idle work under cover: ore below, first not held
NIGHT_STOCK = [[["minecraft:raw_iron", 16]], [["minecraft:diamond", 3]]]

def make(template, **args) -> dict:
    if template not in TEMPLATES:
        raise ValueError(f"unknown goal {template!r}: expected one of {', '.join(TEMPLATES)}")
    if template == "milestone" and args.get("name") not in MILESTONES:
        raise ValueError(f"unknown milestone {args.get('name')!r}: {', '.join(MILESTONES)}")
    return {"goal": template, "args": args}

def have(*needs) -> dict:
    """have(("minecraft:torch", 24), ("tool", "pickaxe", 2))"""
    return make("have", needs=[list(n) for n in needs])

def parse_need(token, n=1):
    """A need from the command line: `tool:pickaxe:2` or `minecraft:torch 24`."""
    if token.startswith("tool:"):
        _, kind, tier = token.split(":")
        return ["tool", kind, int(tier)]
    return [token, int(n)]

def needs(goal, inv) -> list:
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

def short(inv, need_rows):
    """What of these is not held yet, as text; empty when everything is."""
    out = []
    for need in need_rows:
        rest = have_remainder(inv, [need])          # the math is knowledge's; here only the text
        if need[0] == "tool":
            if rest:
                out.append(f"{need[1]} tier {need[2]}")
            continue
        token, n = need[0], int(need[1])
        if rest:
            out.append(f"{bare(token)} {n - rest[token]}/{n}")
    return ", ".join(out)

# each goal kind states its remainder as a pure fn of the world ({} met); progress is never a counter
DESIRED = {}

def desired(*templates):
    """Register goal `templates`' remainder fn(goal, snap, mem) → {missing} ({} met), or None when only the plan running can say."""

    def wrap(fn):
        for t in templates:
            DESIRED[t] = fn
        return fn
    return wrap

def remainder(goal, snap, mem):
    """The goal's remainder from the world now ({} = done), or None when only its plan running can say."""
    return DESIRED[goal["goal"]](goal, snap, mem)

# the shared remainder math is knowledge's; goals and skills' `remaining` both read it there
DURABLE_KINDS = TOOL_KINDS + ("helmet", "chestplate", "leggings", "boots", "shield", "bucket", "flint_and_steel", "bed",
                              "crafting_table", "furnace")
STATIONS = ("minecraft:crafting_table", "minecraft:furnace")


def durable(token):
    """Pure: a thing had as itself — a tool, armour, a station, a bed… (DURABLE_KINDS by name or kind), never a stock."""
    name = bare(token)
    return name in DURABLE_KINDS or name.rpartition("_")[2] in DURABLE_KINDS or mid_(token) in STATIONS


def mid_(token):
    return token if ":" in token else f"minecraft:{token}"


def credit(inv, token, placed=None):
    """Pure over the bag and the recipes (knowledge.sources): how many of `token` the bag holds counting what its
    products embody — a product P made from k of `token` with output o credits held(P) × k / o, down the recipe chain
    (a rod: 1; a powder: ½; an eye: ½). Durables embody nothing (a product is a different thing, never a spare)."""
    placed = placed or {}
    seen, out = {bare(token)}, float(held_count(inv, token) + placed.get(mid_(token), 0))

    def embodied(target, ratio, depth):
        total = 0.0
        if depth > 4:
            return total
        for made, src in _products_of(target):
            if src[0] != "craft" or bare(made) in seen or durable(made):
                continue          # a furnace carried embodies no building stone: a durable is itself, not a stock
            pattern, o = _recipe(made)
            k = sum(1 for cell in pattern if cell and bare(cell) == bare(target))
            if not k:
                continue
            seen.add(bare(made))
            r = ratio * k / o
            total += (held_count(inv, made) + placed.get(mid_(made), 0)) * r + embodied(made, r, depth + 1)
        return total
    if durable(token):
        return out
    return out + embodied(token, 1.0, 0)


def _recipe(token):
    from .data import RECIPES, mid
    from .knowledge import GROUP_RECIPES
    return GROUP_RECIPES[token] if token in GROUP_RECIPES else RECIPES[mid(token)]


@functools.lru_cache(maxsize=256)
def _products_of(token):
    """[(product, craft source)] whose recipe uses `token` (knowledge's craft tables), once per token."""
    from .knowledge import producers
    out = []
    for g in producers():
        if g.kind not in ("craft", "craft_group"):
            continue
        for made in g.keys():
            src = g.get(made)
            if src is not None and any(cell and bare(cell) == bare(token) for cell in src[1]):
                out.append((made, src))
    return out


def raw_steps(token, n, depth=0):
    """Pure over the recipes and the producing tables: [(kind, raw token, count)] making `n` of `token` from nothing —
    a craft folded through its inputs, a mined or gathered thing as it is (the planner's own tables, read here)."""
    from .knowledge import sources
    from .data import mid
    for made, src in sources(token):
        if src[0] == "craft" and depth < 4:
            pattern, out = _recipe(made)
            times = -(-n // out)
            counts = {}
            for cell in pattern:
                if cell:
                    counts[cell] = counts.get(cell, 0) + times
            return [r for cell, k in counts.items() for r in raw_steps(cell, k, depth + 1)]
        if src[0] in ("mine", "gather"):
            blocks = list(src[1]) if src[0] == "mine" and len(src) > 1 else None
            return [(src[0], mid(made) if src[0] == "mine" else made, n, blocks)]
    return [("mine", mid(token), n, None)]


def remake_ticks(block, inv):
    """Pure: ticks to make `block` again from nothing near — its raw things got with the tools held (raw_steps),
    each craft on the way — what a station left standing is weighed against (knowledge's priors, the planner's)."""
    from types import SimpleNamespace
    from .knowledge import PRIOR_TICKS, held_tiers, prior_work_ticks
    from .data import TICKS_PER_S
    ticks = PRIOR_TICKS["craft"]
    for kind, token, n, blocks in raw_steps(block, 1):
        step = SimpleNamespace(kind=kind, token=token, count=n, detail={"blocks": blocks} if blocks else {})
        ticks += prior_work_ticks(step, held_tiers(inv), TICKS_PER_S)
    return ticks


def station_had(block, snap, mem):
    """Carried, or ours standing nearer than making another (walk_ticks there < remake_ticks)."""
    from .knowledge import walk_ticks
    if snap.inv.count(block) > 0:
        return True
    if mem is None:
        return False
    limit = remake_ticks(block, snap.inv)
    return any(walk_ticks(math.dist(pos, snap.feet)) < limit for pos in mem.known_stations(block, snap.dimension))


def portal_opened(snap, mem):
    return snap.dimension == "minecraft:the_end" or bool(mem is not None and mem.sites(kinds=[END_PORTAL_OPEN]))


def _milestone_remainder(name, goal, snap, mem):
    if name in RUN_AFTER:
        return {} if portal_opened(snap, mem) else None
    placed = {"minecraft:ender_eye": 12} if portal_opened(snap, mem) else {}
    out = {}
    for need in needs(goal, snap.inv):
        if need[0] == "tool":
            if not tool_ok(snap.inv, need[1], int(need[2])):
                out[f"tool:{need[1]}"] = int(need[2])
            continue
        token, n = need[0], int(need[1])
        if token in STATIONS:
            if not station_had(token, snap, mem):
                out[token] = 1
            continue
        if token == "bed" and mem is not None and mem.home_part("beds", snap.dimension, snap.feet, anywhere=True) \
                is not None:
            continue
        if durable(token):
            have = snap.inv.count(token, include_worn=True)
            if token == "minecraft:bucket":
                have += snap.inv.count("minecraft:water_bucket") + snap.inv.count("minecraft:lava_bucket")
        else:
            have = int(credit(snap.inv, token, placed)) if token != "food" else held_count(snap.inv, token)
            if have < n and spent_into_a_later_milestone(name, token, snap, mem, placed):
                continue
        if have < n:
            out[token] = n - have
    return out


def spent_into_a_later_milestone(name, token, snap, mem, placed):
    """A stock short because a later milestone's product took it, that milestone met: nothing more of it is wanted."""
    later = list(MILESTONES)[list(MILESTONES).index(name) + 1:]
    for other in later:
        rows = MILESTONES[other]
        if not isinstance(rows, list) or other in OFF_ROUTE:
            continue
        products = [r[0] for r in rows if r[0] != "tool" and (mid_(r[0]) == mid_(token) or embodies(r[0], token))]
        if products and _milestone_remainder(other, make("milestone", name=other), snap, mem) == {}:
            return True
    return False


def embodies(product, token, depth=0):
    """Pure over the recipes: `product` is made from `token`, down the chain."""
    if depth > 4 or durable(product):
        return False
    for made, _src in _products_of(token):
        if bare(made) == bare(product) or embodies(product, made, depth + 1):
            return True
    return False


@desired(*ITEM_GOALS)
def _held_remainder(goal, snap, mem):
    if goal["goal"] == "milestone":
        return _milestone_remainder(goal.get("args", {}).get("name"), goal, snap, mem)
    return have_remainder(snap.inv, needs(goal, snap.inv))

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

@desired("road")
def _road_remainder(goal, snap, mem):
    a, b = goal["args"]["a"], goal["args"]["b"]
    walked = mem is not None and mem.road_walked(a, b, snap.dimension)
    away = math.dist(snap.feet, tuple(b)) - WAYPOINT_R
    if walked and away <= 0:
        return {}
    return {"road walked": 0 if walked else 1, "blocks away": round(max(0.0, away), 1)}

@desired(*RUN_ONCE)
def _ran(goal, snap, mem):
    return None

def _registered():
    missing = [t for t in TEMPLATES if t not in DESIRED]
    if missing:
        raise TypeError(f"goal kinds without a desired state: {', '.join(missing)}")

_registered()

def done(goal, snap, mem):
    """True / False from the world (`remainder` is empty or not); None for a goal done when its plan has run."""
    rest = remainder(goal, snap, mem)
    return None if rest is None else not rest

def describe(goal) -> str:
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
