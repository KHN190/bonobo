"""The bench tables' one vocabulary: scene templates (→ console commands), predicates (→ checks over the world and
the bag), and the names a row's run, hooks and interrupts are written in. Rows (bench_<tier>.py) hold only data in
these words; `table.py` turns them into the runner's row dicts.

Data conventions, one each:
- a position relative to the bench ORIGIN is ("@", dx, dy, dz) — `at(dx, dy, dz)` in the old sheet;
- a callable written inside another's arguments is a tuple whose head is "!" + its kind: ("!gain", "log", 2);
  at the top of a row's `check`, `before` and `run` the head is the bare kind (the slot says what it is);
  ("&name",) is the function `name` itself, not a call of it;
- ("$data", "bench/siege.jsonl") is a file in this player's data directory (paths.data);
- "$api", "$inv", "$ctx" stand for the objects a predicate or a run is called with; ("$ctx", "policy") is an
  attribute of the context, ("$call", name, *args) a helper called at run time (its args may hold these markers).
"""
import importlib
import operator
import sys

from .core import ORIGIN, at

# -- positions ----------------------------------------------------------------------------------------------------
def pos(p):
    """("@", dx, dy, dz) → the absolute position; anything else unchanged."""
    return at(*p[1:]) if isinstance(p, tuple) and len(p) == 4 and p[0] == "@" else p


def _c(p):
    p = pos(p)
    return f"{p[0]} {p[1]} {p[2]}"


# -- scene templates: (template, *params) → console commands -----------------------------------------------------
def _tree(x, z, wood="oak", height=5):
    """One tree block by block: leaves persistent, the same crown every run."""
    return [f"fill {_c(('@', x - 2, height - 2, z - 2))} {_c(('@', x + 2, height - 1, z + 2))} "
            f"{wood}_leaves[persistent=true]",
            f"fill {_c(('@', x - 1, height, z - 1))} {_c(('@', x + 1, height, z + 1))} {wood}_leaves[persistent=true]",
            f"fill {_c(('@', x, 0, z))} {_c(('@', x, height - 1, z))} {wood}_log"]


def _pen(mob, n, half=7):
    """Four fence walls `half` out, `n` of `mob` inside (fixed spots)."""
    walls = [(-half, -half, half, -half), (-half, half, half, half), (-half, -half + 1, -half, half - 1),
             (half, -half + 1, half, half - 1)]
    spots = [(3, 2), (-3, 2), (2, -4), (-4, -3), (4, -1), (-1, 4)][:n]
    return ([f"fill {_c(('@', a, 0, b))} {_c(('@', c, 0, d))} oak_fence" for a, b, c, d in walls]
            + [f"summon {mob} {_c(('@', x, 0, z))}" for x, z in spots])


def _tank(x0, x1, z0, z1, top, water_top=None, floor_y=-4, wall="glass", open_side=None):
    """A tank: a stone floor at `floor_y`, four walls up to `top` (the `open_side` only to the water), water."""
    lo, hi = (x0 - 1, z0 - 1), (x1 + 1, z1 + 1)
    out = [f"fill {_c(('@', lo[0], floor_y, lo[1]))} {_c(('@', hi[0], floor_y, hi[1]))} stone"]
    for side, a, b in (("north", lo, (hi[0], lo[1])), ("south", (lo[0], hi[1]), hi),
                       ("west", lo, (lo[0], hi[1])), ("east", (hi[0], lo[1]), hi)):
        height = water_top if side == open_side and water_top is not None else top
        out.append(f"fill {_c(('@', a[0], floor_y + 1, a[1]))} {_c(('@', b[0], height, b[1]))} {wall}")
    if water_top is not None:
        out.append(f"fill {_c(('@', x0, floor_y + 1, z0))} {_c(('@', x1, water_top, z1))} water")
    return out


SCENE = {
    "cmd": lambda text: [text],                                                   # a command with no position
    "fill": lambda lo, hi, block: [f"fill {_c(lo)} {_c(hi)} {block}"],
    "setblock": lambda p, block: [f"setblock {_c(p)} {block}"],
    "tp": lambda p: [f"tp @p {_c(p)}"],
    "stand": lambda dx=0, dy=0, dz=0: [f"tp @p {_c(('@', dx + 0.5, dy, dz + 0.5))}"],   # the body, mid-block
    "give": lambda item, n=None: [f"give @p {item}" + ("" if n is None else f" {n}")],
    "time": lambda t: [f"time set {t}"],
    "summon": lambda mob, p, nbt=None: [f"summon {mob} {_c(p)}" + ("" if nbt is None else f" {nbt}")],
    "at": lambda template, *ps: [template.format(*[_c(p) for p in ps])],        # any other command with positions
    "floor": lambda block="stone", half=8, depth=3: [f"fill {_c(('@', -half, -depth, -half))} "
                                                     f"{_c(('@', half, -1, half))} {block}"],
    "tree": _tree,
    "grove": lambda *spots, wood="oak": ([f"fill {_c(('@', -8, -1, -8))} {_c(('@', 8, -1, 8))} grass_block"]
                                        + [c for x, z in spots for c in _tree(x, z, wood)]),
    "chest": lambda p, *items: [f"setblock {_c(p)} chest"] + [f"item replace block {_c(p)} container.{i} with {it}"
                                                             for i, it in enumerate(items)],
    "pen": _pen,
    "tank": _tank,
}


def _plain(v):
    """Scene params → values: positions absolute, containers kept."""
    if isinstance(v, tuple) and len(v) == 4 and v[0] == "@":
        return pos(v)
    if isinstance(v, (list, tuple)):
        return type(v)(_plain(x) for x in v)
    return v


def scene(items):
    """A row's scene → the setup command list, in order. ("sheet", NAME) is one of the old sheet's command lists
    (a world several rows share: the fight arena, the brain's world); ("built", "mod:fn", *params) a builder
    that draws its commands (a fight cell from its seed)."""
    out = []
    for kind, *params in items:
        if kind == "sheet":
            out += list(resolve(params[0]))
        elif kind == "built":
            out += list(resolve(params[0])(*[_plain(p) for p in params[1:]]))
        else:
            out += SCENE[kind](*params)
    return out


# -- names --------------------------------------------------------------------------------------------------------
def _scen():
    return sys.modules.get("bonobo.scenarios") or importlib.import_module("bonobo.scenarios")


def resolve(name):
    """A word → the sheet's function or value: "mod.path:attr", a sheet name (its own, or with the leading
    underscore the table drops), or a dotted module function."""
    if ":" in name:
        mod, attr = name.split(":")
        return getattr(importlib.import_module(mod), attr)
    scen = _scen()
    if hasattr(scen, name):
        return getattr(scen, name)
    if hasattr(scen, "_" + name):
        return getattr(scen, "_" + name)
    if "." in name:
        mod, attr = name.rsplit(".", 1)
        return getattr(importlib.import_module(mod), attr)
    raise KeyError(f"no word {name!r}")


# -- predicates: (kind, *args) over (api, inv) ---------------------------------------------------------------------
OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "==": operator.eq,
       "!=": operator.ne, "is": operator.is_, "is not": operator.is_not,
       "in": lambda a, b: a in b, "not in": lambda a, b: a not in b}


def cmp(value, op=None, want=None):
    """Pure: `value op want`, or the value's truth when no op is given."""
    return bool(value) if op is None else OPS[op](value, want)


def count(api, inv, token, op, want):
    """The bag's count of `token` (a group name counts its members) compared: ("count", "log", ">=", 4)."""
    return cmp(inv.count(token), op, want)


def state(api, inv, key, op=None, want=None):
    """A /state field compared (or its truth): ("state", "dimension", "==", "minecraft:the_nether")."""
    return cmp(api.get("/state")[key], op, want)


def bag(api, inv, method, args=(), op=None, want=None):
    """An Inventory reading compared: ("bag", "used_slots", [], "<", 34)."""
    return cmp(getattr(inv, method)(*args), op, want)


def call(api, inv, name, args=(), op=None, want=None, resolve=None):
    """A named world helper's answer compared (or its truth); "$api"/"$inv" in `args` are the check's own."""
    fn = resolve(name)
    return cmp(fn(*[api if a == "$api" else inv if a == "$inv" else a for a in args]), op, want)


PREDICATES = {"count": count, "state": state, "bag": bag, "call": call}
LOGIC = ("all", "any", "not", "now")      # composition: all/any of predicates, not one, one read on the bag now

# -- hooks the old sheet wrote as a lambda of several steps ---------------------------------------------------------
def hungry(ctx):
    """The eat base's start: hunger at full strength for 5 s, the bar read before eating, the eat target set."""
    import time
    from .. import api
    scen = _scen()
    scen._chat("effect give @p minecraft:hunger 5 255 true")
    time.sleep(5.5)
    scen.BASE.update(food_before=api.get("/state")["food"])
    scen._eat_target(ctx)


HOOKS = {"hungry": hungry}


# -- row templates: (template, params) → row data in these words ---------------------------------------------------
def limit():
    """The bench's hard limit per row (runner.ROW_LIMIT_S): no template asks for more."""
    from .runner import ROW_LIMIT_S
    return ROW_LIMIT_S


def nest(w):
    """A word at the top of a slot → the same word inside another's arguments."""
    return w if w[0].startswith(("!", "&")) else ("!" + w[0],) + tuple(w[1:])


def top(w):
    return (w[0][1:],) + tuple(w[1:]) if w[0].startswith("!") else w


def items(w):
    """A check word → the row's check list (an `all` is its parts)."""
    w = top(w)
    return [top(x) for x in w[1:]] if w[0] == "all" else [w]


def _progress(b):
    return {k: b[k] for k in ("progress", "effect", "target") if k in b}


BOX_EXPECT = [(("@", -10, -17, -10), ("@", 20, 9, 10), "*", 1, 10 ** 6)]      # a generated row's box signature


def base_row(name, base, cond=None, surprise=None):
    """A base changed by a condition or a surprise (the old sheet's `_row`), as data."""
    from .bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK
    from .runner import ROW_LIMIT_S
    b, c, x = BASES[base], CONDITIONS[cond] if cond else {}, SURPRISES[surprise] if surprise else {}
    scene = list(x["scene"]) if x.get("replace_setup") else b["scene"] + c.get("scene", []) + x.get("scene", [])
    run, check, hooks = x.get("run", b["run"]), x.get("check", b["check"]), [("start", name)]
    fails = x.get("fails", c.get("fails"))
    scene += c.get("scene_for", {}).get(base, [])
    hooks += [h for h in (b.get("pre"), x.get("before"), c.get("before")) if h]
    if x.get("run_n"):
        run = ("skill", "chop", x["run_n"])
    if c.get("goal_met"):
        scene += [("give", "oak_log" if t == "log" else t.split(":")[-1], n) for t, n in b["needs"]]
        run, check = ("plan_is_empty", b["needs"]), ("same_bag_and_place",)
    resume = ("!unless_done", nest(b["check"]), ("!achieve_needs", b["needs"]) if b.get("needs") else nest(b["run"]))
    kind = c.get("interrupt")
    if kind:
        act = {"mid": "inject_interrupt", "twice": "inject_interrupt", "contested": "post_foreign_task",
               "player": "take_over"}.get(kind)
        if act:
            hooks.append(("on_progress", name, _progress(b), ("&" + act,)) + ((2,) if kind == "twice" else ()))
        else:
            hooks.append(("interrupt_when", ("!gained_at_least",) + tuple(b["effect"]), None,
                          "bench: interrupt at the moment of success"))
        run = ("resume", name, nest(run), resume)
        if kind == "success" and b.get("bound"):
            check = ("gain",) + tuple(b["bound"])
        check = ("all", nest(check), ("!interrupted", 2) if kind == "twice" else ("!interrupted",))
    if c.get("hazard"):
        if c["hazard"] == "sand":
            hooks.append(("on_progress", name, _progress(b), ("&sand_on_head",)))
        run = ("after_l0", name, nest(run), resume)
        check = ("all", nest(check), ("!alive", 8), ("!call", "head_clear", []), ("!not", ("!state", "inLava")))
    if fails:
        run = ("expect_failure", name, nest(run), fails)
        effect = x.get("check") or c.get("fails_check", {}).get(base) or ("same_bag",)
        check = ("all", ("!failed_as_expected",), ("!alive",), nest(effect))
    target = TARGET_S[base] * TARGET_SLACK if not c and not x and base in TARGET_S else None
    if target:
        run = ("timed", nest(run))
    row = {"name": name, "doc": f"{b['doc']} — {x.get('doc') or c.get('doc', 'as is')}", "module": "skills",
           "scene": scene, "before": hooks, "run": run, "check": items(check),
           **({"target_s": target} if target else {}),
           "budget": min(ROW_LIMIT_S, b["budget"] * (2 if c.get("tick_rate", 20) < 20 else 1)),
           "skills": list(b["skills"]), "point": x.get("point", b.get("point", "A")),
           "tags": {"base": base, **({c["axis"]: cond} if c else {}), **({"surprise": name} if x else {})}}
    if fails:
        row["fails"] = fails
    for key in ("tick_rate", "dimension"):
        if c.get(key) or x.get(key) or b.get(key):
            row[key] = x.get(key) or c.get(key) or b.get(key)
    if b.get("entities"):
        row["expect_entities"] = list(b["entities"])
    if b.get("combat"):
        row["combat"] = True
    row["expect"] = BOX_EXPECT
    return row


# -- the fight sheet's rows (bench/fight.py): a cell of its dimensions, recorded by a sweep and judged by rules ---------
FIGHT = "bonobo.bench.fight:"
FIGHT_EXPECT = [(("@", -9, -1, -9), ("@", 12, -1, 9), "stone", 418, 418)]


def _f(name):
    return resolve(FIGHT + name)


def _sweep(name, cells, build, seconds, rows, settle, record=None):
    return ("bonobo.bench.core:_sweep", name, ("!iter", cells), build,
            record or ("!" + FIGHT + "_fought", ("&" + FIGHT + "_kinds_of",), seconds), ("$data", rows), settle)


def arena_row(name, enemy, ground, kit, blood):
    """combat_arena: one cell of (enemy, ground) × (kit, blood) per row."""
    cell = dict(_f("ARMED"), enemy=enemy, ground=ground, kit=kit, blood=blood, run=0)
    return {"name": name, "module": "threat", "raw": True, "combat": True, "dimension": "minecraft:overworld",
            "sweep": True, "variant": [sorted(cell.items())], "scene": [("sheet", FIGHT + "_FIGHT_SETUP")],
            "doc": f"combat_arena shard {enemy}/{ground}/{kit}/{blood}: each cell writes the whole decision into "
                   "bench/combat.jsonl; the rules are relations between rows.",
            "expect": FIGHT_EXPECT,
            "run": _sweep(name, [cell], ("&" + FIGHT + "_build",), _f("CELL_SECONDS"), "bench/combat.jsonl", 0.6),
            "check": [("bonobo.bench.core:_sweep_check", name,
                       ("$data", "bench/combat.jsonl"),
                       [("&" + FIGHT + r,) for r in ("_answers_are_closed", "_shapes_fit_the_enemy",
                                                     "_more_of_them_costs_more")], 1)],
            "tick_rate": 60, "budget": limit()}


def siege_row(name, wave):
    """The siege, one wave per row: from the wave's carry, cleared alive."""
    line_up, _, left = _f("WAVES")[wave - 1]
    return {"name": name, "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:overworld",
            "doc": f"Siege wave {wave} of {len(_f('WAVES'))} ({line_up}), sword, pickaxe, full iron, shield, food and "
                   "blocks: every answer the model offers is available → the wave cleared alive.",
            "scene": [("sheet", FIGHT + "_FIGHT_SETUP"), ("cmd", "effect give @p minecraft:instant_health 3 10 true"),
                      ("built", FIGHT + "_siege_kit"), ("built", FIGHT + "_carry") + tuple(left)],
            "run": _sweep(name, [{"wave": wave, "line_up": line_up}], ("&" + FIGHT + "_siege_build",), None,
                          "bench/siege.jsonl", 0.6, record=("!" + FIGHT + "_siege_record", 24.0)),
            "check": [("bonobo.bench.core:_sweep_check", name, ("$data", "bench/siege.jsonl"),
                       [("&" + FIGHT + "_answers_are_closed",), ("&" + FIGHT + "_wave_cleared",)], 1)],
            "detail": ("siege_detail", name), "budget": limit()}


def escape_row(name, enemy, ground, kit, seed=None):
    """No weapon, no armour, one enemy: away by any answer but swinging. The cell's seed lays out the ground; none
    given, one is drawn (the old sheet drew it at import)."""
    import random
    seed = random.randrange(1 << 30) if seed is None else seed
    cell = dict(_f("UNARMED"), enemy=enemy, ground=ground, kit=kit, run=0, seed=seed)
    return {"name": name, "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:overworld",
            "sweep": True,
            "doc": f"No weapon, no armour, {enemy} on {ground} ground with {kit}, {_f('ESCAPE_SECONDS'):.0f} s: the "
                   "answer has to come from somewhere other than swinging — back off, block the way, dig down, eat, "
                   "or leave a teleporter alone (bench/escape.jsonl).",
            "scene": [("cmd", "gamemode survival @p"), ("cmd", "kill @e[type=!player,type=!item,distance=..48]"),
                      ("built", FIGHT + "_build", cell)],
            "expect": FIGHT_EXPECT,
            "run": _sweep(name, [cell], ("!constant", []), _f("ESCAPE_WATCH"), "bench/escape.jsonl", 0.0),
            "check": [("bonobo.bench.core:_sweep_check", name, ("$data", "bench/escape.jsonl"),
                       [("&" + FIGHT + "_answers_are_closed",), ("&" + FIGHT + "_shapes_fit_the_enemy",)], 1)],
            "detail": ("escape_detail", name), "tick_rate": 60, "budget": limit()}


def behaviour_row(name, behaviour):
    """One fight behaviour: a cell moved off ARMED so that one answer is worth the most; chosen and working."""
    moved, _rule, why = _f("BEHAVIOURS")[behaviour]
    cell = dict(dict(_f("ARMED"), **moved), run=0, seed=0)
    return {"name": name, "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:overworld",
            "stochastic": True, "variant": sorted(cell.items()), "doc": f"Fight behaviour: {why}",
            "scene": [("sheet", FIGHT + "_FIGHT_SETUP")], "expect": FIGHT_EXPECT,
            "run": _sweep(name, [cell], ("&" + FIGHT + "_build",), None, "bench/behaviour.jsonl", 0.6,
                          record=("!" + FIGHT + "_record_with_start",
                                  ("!" + FIGHT + "_fought", ("&" + FIGHT + "_kinds_of",), _f("BEHAVIOUR_SECONDS")))),
            "check": [("behaviour", behaviour)], "tick_rate": 60, "budget": limit()}


def fight_cell_row(name, mob, n, tier, secs, hp, clear):
    """A walled arena, the iron kit, `n` of one mob: all dead (or kept off, or left alone when neutral)."""
    kinds = [f"minecraft:{mob}"]
    spots = ([(7, 0, 0)] if mob == "creeper" else [(4, 0, 0), (-3, 0, 3), (1, 0, -4)])[:n]
    nbt = "{PersistenceRequired:1b,Health:10f}" if mob == "blaze" and n > 1 else "{PersistenceRequired:1b}"
    hold = resolve("RESOLVE_HOLD_S")
    verdict = {True: "all dead", False: "left alone (neutral)", "resolved": f"dead, or kept off and not following "
                                                                          f"for {hold:.0f} s"}[clear]
    check = ([("threat_resolved", kinds), ("decision_gaps_ok",)] if clear == "resolved"
             else [("hp_kept", hp), ("gone", kinds), ("decision_gaps_ok",)] if clear
             else [("hp_kept", hp), ("call", "hostiles", [24, ("$set", kinds)])])
    return {"name": name, "module": "fight_loop", "combat": True, "point": "B", "skills": [], "tier_fixed": tier,
            "doc": f"Walled platform, iron kit: {n} {mob} → {verdict}, health ≥ {hp}, a threat decision every "
                   "≤ 1.5 × FIGHT_POLL_S while engaged",
            "tags": {"base": "fight", "enemy": mob, "count": n},
            "scene": [("sheet", "_ARENA")] + [("summon", mob, ("@", x, y, z), nbt) for x, y, z in spots],
            "expect_entities": [(f"minecraft:{mob}", n)], "before": [("start", name), ("&record_bids",)],
            "run": ("fight_until", kinds, secs - hold - 2 if clear == "resolved" else secs - 2)
            + (() if clear is not False else (False,)),
            "check": check, "budget": min(secs + 5, limit()), "expect": BOX_EXPECT}


def siege_detail(name):
    return lambda inv: _f("_siege_detail_of")(name)


def escape_detail(name):
    return lambda inv: "; ".join(f"{r['enemy']}: {r['outcome']['hp']:.0f} hp, gap {r['outcome']['gap']}"
                                 for r in (_f("SWEEP").get(name) or []))


def behaviour(name):
    """The behaviour's own rule over its recorded row (bench/fight.py BEHAVIOURS)."""
    return _f("_behaviour_check")(f"combat__{name}", _f("BEHAVIOURS")[name][1])


WORDS = {"siege_detail": siege_detail, "escape_detail": escape_detail, "behaviour": behaviour}


# -- one-skill rows, the start-cell and placing rows, the upkeep lines and the brain's rows --------------------------
def one_row(name, skills, doc, scene, run, check, budget, tick_rate=None):
    """One skill proven in the world, once (the old `_ONE`): timed when its skill has a speed target."""
    from .bench_bases import TARGET_S, TARGET_SLACK
    target = TARGET_S[skills[0]] * TARGET_SLACK if skills[0] in TARGET_S else None
    return {"name": name, "doc": doc, "module": "skills", "scene": scene, "before": [("start", name)],
            "run": ("timed", nest(run)) if target else run, "check": items(check), "budget": budget,
            "skills": list(skills), "point": "A", "tags": {"base": skills[0]},
            **({"target_s": target} if target else {}), **({"tick_rate": tick_rate} if tick_rate else {}),
            "expect": BOX_EXPECT}


REAL_KIT = [("cmd", "spreadplayers 14200 14200 0 4 false @p"), ("cmd", "clear @p"), ("give", "stone_pickaxe"),
            ("give", "torch", 8), ("give", "cobblestone", 32), ("give", "cooked_beef", 8)]


def real_row(name, skills, doc, run, check, budget, extra=(), stochastic=False):
    """On real terrain (raw), a target put in scan range: judged by what was found."""
    return {"name": name, "doc": doc, "module": "skills", "raw": True, "release": True,
            "scene": REAL_KIT + list(extra), "before": [("start", name)], "run": run, "check": items(check),
            "budget": budget, "skills": list(skills), "point": "A", "tags": {"base": skills[0], "terrain": "real"},
            **({"stochastic": True} if stochastic else {})}


def place_row(name, item, asked, want, tier):
    """Place a block asking a facing: the block reports the facing its own rule gives."""
    p = ("@", 3, 0, 0)
    return {"name": name, "doc": f"Place {item.split(':')[1]} asking facing={asked} (the jar turns the body by the "
                                 f"block's own rule) → the block reports facing={want}",
            "module": "building", "point": "A", "skills": [], "tier_fixed": tier, "tags": {"base": "place"},
            "variant": (item, asked), "scene": [("floor",), ("stand",), ("give", item.split(":")[1], 2)],
            "before": [("start", name)], "run": ("place_facing", item, p, asked),
            "check": [("placed_facing", p, want)], "budget": 20, "expect": BOX_EXPECT}


def start_row(name, what, start_scene, stand):
    """Walk 10 blocks from an awkward start cell → at the target."""
    return {"name": name, "doc": f"Walk 10 blocks starting on {what} (GotoTask's start cell: '1 positions explored' "
                                 f"reproduces here) → at the target",
            "module": "nav", "point": "A", "skills": ["goto"], "tier_fixed": "common",
            "tags": {"base": "nav", "start": what},
            "scene": [("floor",), ("fill", ("@", 8, -3, -3), ("@", 12, -1, 3), "stone")] + list(start_scene)
            + [("stand",) + tuple(stand)],
            "before": [("start", name)], "run": ("skill", "travel_to", ("@", 10, 0, 0), 2),
            "check": [("_at", ("@", 10, 0, 0), 3.5)], "budget": limit(), "expect": BOX_EXPECT}


def upkeep_row(name, line, doc, scene, hooks, done, check):
    """One upkeep line through the whole brain, nothing queued: the moment built, the answer in the world."""
    return {"name": name, "doc": f"upkeep, {doc}", "module": "reflexes", "point": "C", "skills": [],
            "tier_fixed": "brain", "combat": line == "eat", "tags": {"base": "upkeep", "line": line},
            "scene": scene, "before": [("start", name)] + list(hooks),
            "run": ("brain_rounds", 10 if line == "eat_when_full" else 22, nest(done)), "check": items(check),
            "budget": limit(), "expect": BOX_EXPECT}


def brain_row(name, doc, scene, queue, done, minutes, check, hooks=(), variant=()):
    """The whole brain on a private queue, the world built up to the decision; the slice judged too."""
    return {"name": name, "doc": doc, "module": "brain", "point": "C", "skills": [], "tier_fixed": "brain",
            "combat": name == "resume_after_combat", "tags": {"base": "brain"}, "scene": scene,
            "before": [("start", name)] + list(hooks), "queue": queue, "variant": list(variant),
            "run": ("slice", nest(done), min(minutes, 0.4), None, queue),
            "check": [top(check), ("slice_check", None)],
            "budget": limit(), "expect": BOX_EXPECT}


def dirt_row(name, doc, extra, done, check):
    """Dusk on stone, an empty bag, a dirt patch along the platform: dug in there by hand (or never walked to)."""
    return {"name": name, "doc": doc, "module": "brain", "point": "C", "skills": ["shelter:dig in"],
            "tier_fixed": "brain", "tags": {"base": "brain", "family": "night_dirt"},
            "scene": [("floor",), ("fill", ("@", 7, -3, -1), ("@", 8, -1, 1), "dirt"),
                      ("fill", ("@", 7, -4, -1), ("@", 8, -4, 1), "stone")] + list(extra) + [("stand",),
                                                                                             ("time", 12500)],
            "before": [("start", name)], "run": ("brain_rounds", 25, nest(done)), "check": items(check),
            "budget": limit(), "expect": BOX_EXPECT}


def cell_row(name, *key):
    """A cell of the brain's grid (dimensions off the base one at a time): its families' goals queued, every
    family's rule judged, the slice too."""
    scen = _scen()
    entry = scen._grid_cells()[key]
    cell, fams = entry["cell"], entry["families"]
    judged = [scen.BRAIN_FAMILIES[f][2](cell) for f in fams]
    dims = scen.BRAIN_DIMS
    return {"name": name, "doc": f"{'+'.join(fams)}: " + ", ".join(f"{d} {cell[d]}" for d in dims) + " → "
                                 + "; ".join(why for _c, why in judged),
            "module": "brain", "point": "C", "skills": [], "tier_fixed": "brain", "combat": False,
            "tags": {"base": "brain", "family": "+".join(fams), **{d: cell[d] for d in dims}},
            "scene": [("sheet", "BRAIN_WORLD"), ("brain_dims",) + key], "before": [("brain_cell_hooks", name) + key],
            "queue": list(entry["queue"]), "run": ("slice", None, 0.4, None, list(entry["queue"])),
            "check": [("brain_rule", f) + key for f in fams] + [("slice_check", None)],
            "why": [why for _c, why in judged] + ["the slice"], "budget": limit(), "expect": BOX_EXPECT}


def _cell(key):
    scen = _scen()
    return scen._grid_cells()[tuple(key)]["cell"]


def brain_rule(fam, *key):
    """A grid family's rule for one cell (scenarios.BRAIN_FAMILIES): its check."""
    return _scen().BRAIN_FAMILIES[fam][2](_cell(key))[0]


def brain_cell_hooks(name, *key):
    scen = _scen()
    cell = _cell(key)
    return scen._hooks(*scen._cell_setup_hooks(cell), scen._start(name), *scen._cell_before(cell))


WORDS.update(brain_rule=brain_rule, brain_cell_hooks=brain_cell_hooks)
SCENE["brain_dims"] = lambda *key: [c for d, v in zip(_scen().BRAIN_DIMS, key) for c in _scen().BRAIN_DIMS[d][v]]


TEMPLATES = {"base": base_row, "arena": arena_row, "siege": siege_row, "escape": escape_row,
             "behaviour": behaviour_row, "fight_cell": fight_cell_row, "one": one_row, "real": real_row,
             "place": place_row, "start": start_row, "upkeep": upkeep_row, "brain": brain_row, "dirt": dirt_row,
             "cell": cell_row}
NAMES = {"base": lambda base, cond=None, surprise=None: surprise or f"{base}__{cond or 'base'}",
         "arena": lambda i, *cell: f"combat_arena__{i}", "siege": lambda w: f"siege__w{w}",
         "escape": lambda enemy, ground, kit, seed=None: f"escape__{enemy}_{ground}_{kit}",
         "behaviour": lambda b: f"combat__{b}", "fight_cell": lambda name, *p: name,
         "upkeep": lambda line, *p: f"upkeep__{line}",
         "cell": lambda *key: _scen().grid_name(_scen()._grid_cells()[key]["families"], _cell(key)),
         **{t: (lambda name, *p: name) for t in ("one", "real", "place", "start", "brain", "dirt")}}
NAMED = {"arena", "fight_cell", "one", "real", "place", "start", "brain", "dirt"}          # templates whose first parameter is only the row's name


# -- interrupts: the progress kinds a row's interruption waits for (the old sheet's `_interrupt_when`) -------------
INTERRUPT_PROGRESS = ("gained_at_least", "spent_at_least", "placed_at_least", "walked_at_least")

__all__ = ["ORIGIN", "OPS", "PREDICATES", "LOGIC", "SCENE", "INTERRUPT_PROGRESS", "cmp", "pos", "resolve", "scene"]
