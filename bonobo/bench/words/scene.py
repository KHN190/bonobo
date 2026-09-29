"""Scene words (→ console commands), the positions they are written in, and the row frame every template fills."""
import importlib
import json
import math
import operator
import os
import random
import re
import sys
import threading as _threading
import time

from ... import estimate, paths  # noqa: F401
import importlib
import json
import math
import operator
import os
import re
import sys
import time
from .. import core, runner
from ...data import DAY_TICKS, POD_BLOCKS  # noqa: F401
from ..core import *          # noqa: F403  (the bench's primitives are this module's own vocabulary)
from ..core import (BOX, FLAG, NOTES, ORIGIN, SCENARIOS, SetupInvalid, _achieve, _c, _chat, _checked,
                         _command, _count_blocks, _drain, at, server_count, set_brain)
from ..runner import *        # noqa: F403
from ..runner import (LAST_FEEDBACK, LAST_LINES, _setup, _trace, classify, code_for, feedback_errors, load_table,
                           module_deps, record, run, save_table, setup_mismatches, silent_failure, status)
from ..bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK   # the bases' data: one home
from ..core import SWEEP, _platform  # noqa: F401

# -- arena pieces (relative to ORIGIN)
def _floor(block="stone", half=8, depth=3):
    return [f"fill {_c(at(-half, -depth, -half))} {_c(at(half, -1, half))} {block}"]

def _tp(dx: float = 0, dy: float = 0, dz: float = 0):
    return f"tp @p {_c(at(dx + 0.5, dy, dz + 0.5))}"

TREE_HEIGHT = 5            # logs in one bench tree (its trunk)


def _tree(x, z, wood="oak", height=TREE_HEIGHT):
    """One tree built block by block: the same shape every run (a generated tree's log count decided rows by chance)."""
    return [f"fill {_c(at(x - 2, height - 2, z - 2))} {_c(at(x + 2, height - 1, z + 2))} {wood}_leaves[persistent=true]",
            f"fill {_c(at(x - 1, height, z - 1))} {_c(at(x + 1, height, z + 1))} {wood}_leaves[persistent=true]",
            f"fill {_c(at(x, 0, z))} {_c(at(x, height - 1, z))} {wood}_log"]

CHOP_TREE = (2, 0)       # the chop base's one oak (x, z): rows that must leave it standing read it here

def _grove(*spots, wood="oak"):
    return [f"fill {_c(at(-8, -1, -8))} {_c(at(8, -1, 8))} grass_block"] + [c for x, z in spots for c in _tree(x, z, wood)]

def _chest(pos, *items):
    return [f"setblock {_c(pos)} chest"] + \
        [f"item replace block {_c(pos)} container.{i} with {item}" for i, item in enumerate(items)]

def _pen(mob, n, half=7):
    walls = [f"fill {_c(at(a, 0, b))} {_c(at(c, 0, d))} oak_fence" for a, b, c, d in
             ((-half, -half, half, -half), (-half, half, half, half), (-half, -half + 1, -half, half - 1),
              (half, -half + 1, half, half - 1))]
    spots = [(3, 2), (-3, 2), (2, -4), (-4, -3), (4, -1), (-1, 4)][:n]
    return walls + [f"summon {mob} {_c(at(x, 0, z))}" for x, z in spots]

def _tank(x0, x1, z0, z1, top, water_top=None, floor_y=-4, wall="glass", open_side=None):
    """A glass tank inside the box: floor at `floor_y`, four walls up to `top`, open above, water up to `water_top`."""
    lo, hi = (x0 - 1, floor_y, z0 - 1), (x1 + 1, top, z1 + 1)
    out = [f"fill {_c(at(lo[0], floor_y, lo[2]))} {_c(at(hi[0], floor_y, hi[2]))} stone"]
    for side, a, b in (("north", (lo[0], lo[2]), (hi[0], lo[2])), ("south", (lo[0], hi[2]), (hi[0], hi[2])),
                       ("west", (lo[0], lo[2]), (lo[0], hi[2])), ("east", (hi[0], lo[2]), (hi[0], hi[2]))):
        height = water_top if side == open_side and water_top is not None else top
        out.append(f"fill {_c(at(a[0], floor_y + 1, a[1]))} {_c(at(b[0], height, b[1]))} {wall}")
    if water_top is not None:
        out.append(f"fill {_c(at(x0, floor_y + 1, z0))} {_c(at(x1, water_top, z1))} water")
    return out

# -- positions ----------------------------------------------------------------------------------------------------
def pos(p):
    """("@", dx, dy, dz) → the absolute position; anything else unchanged."""
    return at(*p[1:]) if isinstance(p, tuple) and len(p) == 4 and p[0] == "@" else p

def _c(p):
    p = pos(p)
    return f"{p[0]} {p[1]} {p[2]}"

SCENE = {
    "cmd": lambda text: [text],                                                   # a command with no position
    "fill": lambda lo, hi, block: [f"fill {_c(lo)} {_c(hi)} {block}"],
    "setblock": lambda p, block: [f"setblock {_c(p)} {block}"],
    "tp": lambda p: [f"tp @p {_c(p)}"],
    "stand": lambda *d: [_tp(*d)],                                                  # the body, mid-block
    "give": lambda item, n=None: [f"give @p {item}" + ("" if n is None else f" {n}")],
    "time": lambda t: [f"time set {t}"],
    "summon": lambda mob, p, nbt=None: [f"summon {mob} {_c(p)}" + ("" if nbt is None else f" {nbt}")],
    "at": lambda template, *ps: [template.format(*[_c(p) for p in ps])],        # any other command with positions
    "floor": _floor, "tree": _tree, "grove": _grove, "pen": _pen, "tank": _tank,       # the sheet's own builders
    "chest": lambda p, *items: _chest(pos(p), *items),
}

def _scene_params(v):
    """Scene params → values: positions absolute, containers kept."""
    if isinstance(v, tuple) and len(v) == 4 and v[0] == "@":
        return pos(v)
    if isinstance(v, (list, tuple)):
        return type(v)(_scene_params(x) for x in v)
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
            out += list(resolve(params[0])(*[_scene_params(p) for p in params[1:]]))
        else:
            out += SCENE[kind](*params)
    return out

# -- row templates: (template, params) → row data in these words ---------------------------------------------------

def scene_now(items):
    """A `before` hook or run step: scene words sent now, mid-row (built from the same words as the scene)."""
    def hook(ctx=None):
        from ..core import _chat
        for cmd in scene(items):
            _chat(cmd)
    return hook

def limit():
    """The bench's hard limit per row (runner.ROW_LIMIT_S): no template asks for more."""
    from ..runner import ROW_LIMIT_S
    return ROW_LIMIT_S

def nest(w):
    """A word at the top of a slot → the same word inside another's arguments (a one-off row's code: as is)."""
    return w if callable(w) or w[0].startswith(("!", "&")) else ("!" + w[0],) + tuple(w[1:])

def top(w):
    return w if callable(w) else (w[0][1:],) + tuple(w[1:]) if w[0].startswith("!") else w

def items(w):
    """A check word → the row's check list (an `all` is its parts)."""
    w = top(w)
    return [w] if callable(w) else [top(x) for x in w[1:]] if w[0] == "all" else [w]

def _progress(b):
    return {k: b[k] for k in ("progress", "effect", "target") if k in b}

BOX_EXPECT = [(("@", -10, -17, -10), ("@", 20, 9, 10), "*", 1, 10 ** 6)]      # a generated row's box signature

SHEET_EXPECT = [(at(*BOX[0]), at(*BOX[1]), "*", 1, 10 ** 6)]                    # the same, for a one-off row

# -- one-skill rows, the start-cell and placing rows, the upkeep lines and the brain's rows --------------------------
def _row(name, doc, module, scene, run, check, point="A", budget=None, before=(), **more):
    """A row's common frame: started (`_start`), the box signature, the bench's limit unless it asks less."""
    return {"name": name, "doc": doc, "module": module, "point": point, "scene": list(scene),
            "before": [("start", name)] + list(before), "run": run, "check": check,
            "budget": budget or limit(), "expect": BOX_EXPECT, **more}

WORDS = {}          # words made by a function of their arguments (the fight and brain modules add theirs)
REGISTRY = {}       # every word by name, filled once by vocab from every word module (a name in two is an error)

def resolve(name):
    """A word → its function or value: "mod.path:attr", a registered name (its own, or with the leading underscore
    the table drops), or a dotted module function."""
    if ":" in name:
        mod, attr = name.split(":")
        return getattr(importlib.import_module(mod), attr)
    found = [n for n in (name, "_" + name) if n in REGISTRY]
    if len(found) == 2 and REGISTRY[found[0]] is not REGISTRY[found[1]]:
        raise KeyError(f"ambiguous word {name!r}: both {found[0]!r} and {found[1]!r} are defined — rename one")
    if found:
        return REGISTRY[found[0]]
    if "." in name:
        mod, attr = name.rsplit(".", 1)
        return getattr(importlib.import_module(mod), attr)
    raise KeyError(f"no word {name!r}")

__all__ = ['REGISTRY', 'WORDS', 'resolve', 'scene_now', 'TREE_HEIGHT', 'BOX_EXPECT', 'CHOP_TREE', 'SCENE', 'SHEET_EXPECT', '_c', '_chest', '_floor', '_grove', '_pen', '_progress', '_row', '_scene_params', '_tank', '_tp', '_tree', 'items', 'limit', 'nest', 'pos', 'scene', 'top']
