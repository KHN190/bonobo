"""Estimate words: a row's budget is production's price of its goal over its scene × the slack (V10).
  ("plan", needs)                       the planner's cheapest plan for `needs` from the scene's bag and blocks
  ("step", kind, token, count, detail)  the cost model's price of one step (detail positions '@'-relative)
  ("way", target)                       nav.plan_way's seconds to stand at `target` over the scene's blocks
  ("fight", mobs, n)                    the fight model's estimate (words.fight.fight_est_s)
  ("eat", food)                         the eat step at the bites survive.bite_plan makes from `food` and the bag
  ("on", scene_words, word)             `word` priced over another scene (a must-fail row: the work it was asked)
  ("sum", word, ...)                    the words one after another"""
import math
import os
import re

from ...data import TICKS_PER_S, bare
from ..core import pos
from .ways import _bag

UNIT_BLOCKS = ("air", "cave_air", "void_air")


def scene_world(setup):
    """Pure: blocks, feet, slots, mobs and clock a scene's commands leave."""
    from .scene import _placed
    blocks, slots, mobs, feet, clock = {}, [], [], None, None
    for cmd in setup:
        w = cmd.split(" run ")[-1].split()
        got = _placed(cmd)
        if got is not None:
            cells, block, mode, filt, shell = got
            for p in cells:
                if mode == "outline" and not shell(p) or mode == "keep" and blocks.get(p, "air") != "air":
                    continue
                if mode == "replace" and filt is not None and blocks.get(p, "air") != filt:
                    continue
                blocks[p] = "air" if mode == "hollow" and not shell(p) else block
        elif w[:2] == ["tp", "@p"] and len(w) >= 5:
            feet = tuple(math.floor(float(v)) for v in w[2:5])
        elif w[:2] == ["give", "@p"]:
            item = re.split(r"[\[{]", w[2])[0]
            slots.append((bare(item), int(w[3]) if len(w) > 3 else 1))
        elif w[:2] == ["clear", "@p"]:
            slots = []
        elif w[:1] == ["summon"] and len(w) >= 5:
            mobs.append((f"minecraft:{bare(w[1])}", tuple(math.floor(float(v)) for v in w[2:5])))
        elif w[:2] == ["time", "set"]:
            clock = int(w[2])
    return {"blocks": {c: b for c, b in blocks.items() if b not in UNIT_BLOCKS}, "feet": feet or pos(("@", 0, 0, 0)),
            "slots": slots, "mobs": mobs, "time": clock}


def scene_cost(world, dimension="minecraft:overworld"):
    """The production cost model over the scene."""
    from ...cost import Cost
    from ...world import Snapshot
    fx, fy, fz = world["feet"]
    state = {"x": fx + 0.5, "y": float(fy), "z": fz + 0.5, "blockX": fx, "blockY": fy, "blockZ": fz,
             "dimension": dimension, "timeOfDay": world["time"] if world["time"] is not None else 1000,
             "health": 20.0, "maxHealth": 20.0, "food": 20, "saturation": 5.0, "air": 300, "armor": 0}
    import tempfile
    from ...memory import Memory
    hits: dict = {}
    for c, b in world["blocks"].items():
        hits.setdefault(bare(b), []).append({"x": c[0], "y": c[1], "z": c[2], "block": f"minecraft:{bare(b)}",
                                            "distance": math.dist(c, world["feet"])})
    mobs = [{"type": m, "id": i, "x": p[0], "y": p[1], "z": p[2], "distance": math.dist(p, world["feet"])}
            for i, (m, p) in enumerate(world["mobs"])]
    mem = Memory(os.path.join(tempfile.mkdtemp(prefix="scene-"), "notes.json"))      # a scene remembers nothing
    # the round's ground as production reads it (survive.ROUND_GROUND's box round the feet), from the scene's blocks
    from ...survive import ROUND_GROUND
    from ...world import Region, cell_add
    lo, hi = (cell_add((fx, fy, fz), d) for d in ROUND_GROUND[0])
    ground = Region.of(lo, hi, {c: bare(b) for c, b in world["blocks"].items()})
    return Cost(Snapshot.from_readings(state, _bag(world["slots"]), hits, mobs, region=ground), mem)


def _plan_s(needs, world, dimension):
    from ... import brain  # noqa: F401  (every skill registered: the planner's producing tables)
    from ... import planner
    cost = scene_cost(world, dimension)
    steps = planner.plan_needs(cost.snap.inv, [tuple(n) for n in needs], cost)
    return sum(s.est for s in steps) / TICKS_PER_S


def _detail(detail):
    return {k: (list(pos(v)) if isinstance(v, tuple) and v[:1] == ("@",) else v) for k, v in (detail or {}).items()}


def _step_s(kind, token, count, detail, world, dimension):
    from ...planner import Step
    return scene_cost(world, dimension).estimate(Step(kind, token, count, _detail(detail))) / TICKS_PER_S


def _way_s(target, world):
    from ... import nav
    from ...world import Region
    blocks = world["blocks"]
    lo = tuple(min(c[i] for c in blocks) - 1 for i in range(3))
    hi = tuple(max(c[i] for c in blocks) + 4 for i in range(3))
    steps, why, seconds = nav.plan_way(Region.of(lo, hi, blocks), world["feet"], pos(target), "stand",
                                       _bag(world["slots"]), ())
    if steps is None:
        raise ValueError(f"no way to {target} over the scene: {why}")
    return float(seconds or 0.0)


def est_s(word, setup, dimension="minecraft:overworld"):
    """Production's seconds for the row's goal from the scene its `setup` builds."""
    from .fight import fight_est_s
    from .scene import scene
    kind, args = word[0], word[1:]
    if kind == "on":
        return est_s(args[1], scene(list(args[0])), dimension)
    if kind == "sum":
        return sum(est_s(w, setup, dimension) for w in args)
    if kind == "fight":
        return fight_est_s(list(args[0]), args[1])
    world = scene_world(setup)
    if kind == "plan":
        return _plan_s(args[0], world, dimension)
    if kind == "step":
        step_kind, token, count, detail = args
        return _step_s(step_kind, token, count, detail, world, dimension)
    if kind == "way":
        return _way_s(args[0], world)
    if kind == "eat":
        from ...survive import bite_plan
        bites = len(bite_plan(args[0], {f"minecraft:{i}": n for i, n in world["slots"]}))
        return _step_s("eat", "food", bites, {}, world, dimension)
    raise ValueError(f"no estimate word {kind!r}")
