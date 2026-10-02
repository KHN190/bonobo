"""Way words: rows of the way planner, the tool rule and the home's night, each built from one spec (SPECS: the cells
of its scene, '@'-relative) — the scene's commands and its budget both read off the spec; the budget is the
production estimate of the run (nav.plan_way / way_s, knowledge.dig_ticks, the planner's own make price) × the
bench's slack, never typed."""
import math

from ..bench_bases import TARGET_SLACK
from .scene import _row, items, limit, nest

PICK = "stone_pickaxe"
SHOVEL_KIT = [("oak_planks", 8), ("stick", 4), ("crafting_table", 1)]       # what a wooden shovel is made from, carried


def _box(lo, hi, block):
    return {(x, y, z): block for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1)
            for z in range(lo[2], hi[2] + 1)}


# every way row's world, as data: fills (lo, hi, block) in order, then single cells; feet at (0, 0, 0)
SPECS = {
    # R1: iron 6 below, two sand on the third step's column: dug top-down, the sand never lands in a cleared cell
    "stairs_under_sand": {"fills": [((-3, -10, -3), (10, -1, 3), "stone")],
                          "cells": {(3, -2, 0): "sand", (3, -1, 0): "sand", (6, -6, 0): "iron_ore"},
                          "target": (6, -6, 0)},
    # R2: a buried iron 3 off, an exposed one 6 off on the floor: the exposed one is the cheaper way
    "exposed_over_buried": {"fills": [((-8, -4, -3), (8, -1, 3), "stone")],
                            "cells": {(3, -2, 0): "iron_ore", (-6, 0, 0): "iron_ore"},
                            "target": (-6, 0, 0), "buried": (3, -2, 0)},
    # R3: the iron behind a home wall across the whole arena, too high to walk over: no way, nothing tried
    "vein_behind_home": {"fills": [((-8, -3, -8), (12, -1, 8), "stone"), ((2, 0, -8), (4, 4, 8), "stone")],
                         "cells": {(8, 0, 0): "iron_ore"}, "target": (8, 0, 0), "home": ((2, 0, -8), (4, 4, 8))},
    # R4 / R4': a dirt patch, the shovel's parts carried
    "dirt_patch": {"fills": [((-8, -3, -8), (8, -1, 8), "dirt")], "cells": {}},
    # R6: a home room with a bed in it, the body inside at night
    "home_bed": {"fills": [((-3, -3, -4), (8, -1, 4), "stone"), ((-1, 0, -3), (7, 3, 3), "stone")],
                 "air": ((0, 0, -2), (6, 2, 2)),
                 "cells": {(4, 0, 0): "red_bed[facing=east,part=foot]", (5, 0, 0): "red_bed[facing=east,part=head]"},
                 "home": ((-1, 0, -3), (7, 3, 3)), "bed": (4, 0, 0)},
}


def spec_blocks(spec):
    """Pure: {cell: block} the spec builds ('@'-relative)."""
    blocks = {}
    for lo, hi, b in spec["fills"]:
        blocks.update(_box(lo, hi, b))
    if "air" in spec:
        lo, hi = spec["air"]
        for c in _box(lo, hi, "air"):
            blocks.pop(c, None)
    blocks.update({c: b.split("[")[0] for c, b in spec["cells"].items()})
    return blocks


def spec_scene(spec, give=(), extra=()):
    """Pure: the scene words that build the spec, the body at the origin."""
    at = lambda c: ("@",) + tuple(c)     # noqa: E731
    out = [("fill", at(lo), at(hi), b) for lo, hi, b in spec["fills"]]
    if "air" in spec:
        out.append(("fill", at(spec["air"][0]), at(spec["air"][1]), "air"))
    out += [("setblock", at(c), b) for c, b in spec["cells"].items()]
    return out + [("stand",)] + [("give", i, n) for i, n in give] + list(extra)


def _region(spec):
    from ...world import Region
    blocks = spec_blocks(spec)
    lo = tuple(min(c[i] for c in blocks) - 1 for i in range(3))
    hi = tuple(max(c[i] for c in blocks) + 4 for i in range(3))
    return Region.of(lo, hi, blocks)


def _bag(items):
    from ...actions import TOOL_USES
    from ...world import Inventory
    slots = []
    for item, n in items:
        material = item.rpartition("_")[0]
        slots.append({"id": f"minecraft:{item}", "count": n, "damage": 0, "slot": len(slots),
                      **({"maxDamage": TOOL_USES[material]} if material in TOOL_USES else {})})
    return Inventory({"slots": slots, "equipment": {}})


def budget(est_s):
    """The row's budget: the production estimate × the bench's slack, within the bench's limit."""
    return min(limit(), max(1, math.ceil(est_s * TARGET_SLACK)))


def way_est_s(spec, give):
    """Pure: seconds the planned way to the spec's target takes (nav.plan_way over the spec's blocks) plus its break."""
    from ... import nav
    from ...knowledge import dig_ticks
    region, inv = _region(spec), _bag(give)
    target = spec["target"]
    steps, _why, seconds = nav.plan_way(region, (0, 0, 0), target, "mine", inv, ())
    walk = math.dist((0, 0, 0), target) / nav.PLAYER_SPEED if steps is None else (seconds or 0.0)
    block = spec_blocks(spec)[target]
    return walk + dig_ticks([block], inv) / 20.0


def shovel_break_even():
    """Pure: the dirt count at which a wooden shovel pays (its make price over its saving per block): the planner's
    own rule (knowledge.work_s) with its own make estimate (the probe plan)."""
    from ...knowledge import work_s
    from ...planner import NullCost, Planner, TICKS_PER_S
    probe = Planner({f"minecraft:{i}": n for i, n in SHOVEL_KIT}, [], NullCost())
    probe.probing = True
    make_s = sum(s.est for s in probe.plan([("tool", "shovel", 0)])) / TICKS_PER_S
    saving = work_s(["dirt"], [], {}, TICKS_PER_S) - work_s(["dirt"], [], {"shovel": 0}, TICKS_PER_S)
    return math.ceil(make_s / saving), make_s


def dirt_counts():
    """Pure: (a count the shovel pays for, a count it does not): the break-even × and ÷ the bench's slack."""
    n, _make = shovel_break_even()
    return math.ceil(n * TARGET_SLACK), max(1, math.floor(n / TARGET_SLACK))


def way_row(name, key, doc, run, check, give=(), fails=None, before=()):
    """A row of the way planner over SPECS[key]: built from the spec, budgeted by its own estimate."""
    spec = SPECS[key]
    give = [(PICK, 1)] + list(give)
    more = {"fails": fails} if fails else {}
    if fails:
        run = ("expect_failure", name, nest(run), fails)
    return _row(name, doc, "skills", spec_scene(spec, give), run, items(check), budget=budget(way_est_s(spec, give)),
                before=list(before), skills=["mine"], tier_fixed="exception", tags={"base": "way", "spec": key},
                **more)


def dirt_tool_row(name, pays, doc):
    """R4 / R4': dig dirt by the plan (the brain's own path): a wooden shovel made first only when the count pays."""
    from ...knowledge import dig_ticks
    n = dirt_counts()[0 if pays else 1]
    _be, make_s = shovel_break_even()
    tool = _bag([("wooden_shovel", 1)] if pays else [])
    est = (make_s if pays else 0.0) + dig_ticks(["dirt"] * n, tool) / 20.0
    check = ("all", ("!gain", "minecraft:dirt", n),
             ("!count", "minecraft:wooden_shovel", ">=" if pays else "==", 1 if pays else 0))
    return _row(name, f"{doc} ({n} dirt; the shovel pays from {shovel_break_even()[0]})", "skills",
                spec_scene(SPECS["dirt_patch"], SHOVEL_KIT), ("achieve_needs", [("minecraft:dirt", n)]), items(check),
                budget=budget(est), skills=["mine"], tier_fixed="exception", tags={"base": "way", "spec": "dirt"})


def home_night_row(name, doc):
    """R6: night inside the home, its bed in the hall: slept in it, nothing of the home dug."""
    from ... import nav
    from ...survive import MORNING_S
    spec = SPECS["home_bed"]
    est = math.dist((0, 0, 0), spec["bed"]) / nav.PLAYER_SPEED + MORNING_S
    lo, hi = (("@",) + tuple(c) for c in spec["home"])
    return _row(name, doc, "reflexes", spec_scene(spec, extra=[("time", 18000)]),
                ("brain_rounds", budget(est), ("now", ("!is_day",))),
                items(("all", ("!is_day",), ("!unchanged", lo, hi, []))), point="C", budget=budget(est),
                before=[("home_box", lo, hi)], skills=[], tier_fixed="brain", tags={"base": "upkeep", "line": "home_bed"})


def sleep_est(scene_blocks, give):
    """Pure: the budget of a carried bed's night over a scene's blocks: the room the sleep skill digs
    (survive.bed_room_tasks, priced by nav.way_s), the torches its box wants (torch_cover over the dark floor),
    the morning read."""
    from ... import nav, survive
    from ...world import Region
    blocks = dict(scene_blocks)
    lo = tuple(min(c[i] for c in blocks) - 9 for i in range(3))
    hi = tuple(max(c[i] for c in blocks) + 9 for i in range(3))
    region, inv = Region.of(lo, hi, blocks), _bag(give)
    tasks, cells, seconds, _why = survive.bed_room_tasks(region, (0, 0, 0), (), [], inv)
    taken = set(cells or ()) | {(0, 0, 0)}
    floor = [(x, 0, z) for x in range(-8, 9) for z in range(-8, 9) if (x, 0, z) not in taken
             and region.name((x, 0, z)) == "air" and region.solid((x, -1, z))]
    torches = survive.torch_cover(floor)
    return budget((seconds or 0.0) + len(torches) * nav.PLACE_S + survive.MORNING_S)


def _sleep_scene(extra):
    floor = _box((-8, -3, -8), (8, -1, 8), "stone")
    floor.update(extra)
    return {c: b for c, b in floor.items() if b != "air"}


EST = {"dark_open": lambda: sleep_est(_sleep_scene({**_box((-3, 0, -3), (3, 1, 3), "oak_log"),
                                                  **_box((0, 0, 0), (0, 1, 0), "air")}), [("torch", 16)]),
       "sealed_pod": lambda: sleep_est(_sleep_scene({**_box((-4, -3, -4), (4, 3, 4), "stone"),
                                                   **_box((0, 0, 0), (0, 1, 0), "air")}),
                                       [("stone_pickaxe", 1), ("torch", 16)])}


def home_box(lo, hi):
    """before: the box lo..hi is a home of ours (memory.add_home over what stands in it)."""
    def hook(ctx):
        from ...world import Region
        r = Region(lo, hi)
        ctx.mem.add_home("bench_home", [(lo, hi)], "minecraft:overworld", dict(r.blocks))
    return hook


def place_into(pos, item):
    """run: one place of `item` into `pos` through the door (the home guard judges it first)."""
    def run(ctx):
        from ... import api
        return api.run({"type": "place", "item": item, "x": pos[0], "y": pos[1], "z": pos[2]},
                       awaits="the block placed, or refused before the jar")
    return run


DROP_HEIGHT = 15          # the drop: past nav.SAFE_DROP, so a landing without the clutch hurts
FALL_WATCH_S = 8.0


def fall(taken):
    """run: the body sent DROP_HEIGHT up into the air — the player holding it first when `taken` (the jar's /control,
    as the key does) — and watched until it stands again."""
    def run(ctx):
        import time
        from ... import api
        from ..core import _chat
        if taken:
            api.api("POST", "/control", {"paused": True})
        try:
            _chat(f"tp @p ~ ~{DROP_HEIGHT} ~")
            t0 = time.time()
            time.sleep(1.0)
            while time.time() - t0 < FALL_WATCH_S and not api.get("/state").get("onGround"):
                time.sleep(0.1)
        finally:
            if taken:
                api.api("POST", "/control", {"paused": False})
        return True
    return run


def fall_row(name, taken):
    """S6: a fall of DROP_HEIGHT with a water bucket carried (not in hand). The player holding the body: nothing of
    the agent runs — no clutch, no swap: hurt, the hand unchanged. Its twin, the agent driving: the clutch lands it
    unhurt (must fail of the first: the reflex ran under the player)."""
    from ...data import MAX_HP
    scene = [("floor",), ("stand",), ("cmd", "clear @p"), ("give", "cobblestone", 1), ("give", "water_bucket")]
    check = ([("state", "health", "<", MAX_HP), ("held", "minecraft:cobblestone")] if taken
             else [("state", "health", ">=", MAX_HP)])
    doc = (f"A {DROP_HEIGHT}-block fall, a water bucket carried, "
           + ("the player holding the body → no clutch, no swap: hurt, cobblestone still in hand" if taken
              else "the agent driving → the clutch lands it unhurt"))
    return _row(name, doc, "skills", scene, ("fall", taken), check, budget=25, skills=[], tier_fixed="exception", variant=(taken,),
                tags={"base": "takeover"})


TEMPLATES = {"way": way_row, "dirt_tool": dirt_tool_row, "home_night": home_night_row, "fall": fall_row}
NAMES = {t: (lambda name, *p: name) for t in TEMPLATES}

__all__ = ["EST", "sleep_est", "SPECS", "SHOVEL_KIT", "PICK", "budget", "dirt_counts", "dirt_tool_row", "home_box", "home_night_row", "place_into", "DROP_HEIGHT",
           "FALL_WATCH_S", "fall", "fall_row",
           "shovel_break_even", "spec_blocks", "spec_scene", "way_est_s", "way_row"]
