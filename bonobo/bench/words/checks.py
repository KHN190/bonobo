"""Check words: what the world and the bag must show afterwards, and the row's start snapshot they compare with."""
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
from ..core import bag_now
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
                         _command, _count_blocks, _drain, _inv_has, _near, at, server_count, set_brain)
from ..runner import *        # noqa: F403
from ..runner import (LAST_FEEDBACK, LAST_LINES, _setup, _trace, classify, code_for, feedback_errors, load_table,
                           module_deps, record, run, save_table, setup_mismatches, silent_failure, status)
from ..bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK   # the bases' data: one home
from ..core import SWEEP, _platform  # noqa: F401
from .scene import *  # noqa: F401,F403

# == the generated sheet (points A–D): BASES × CONDITIONS, each row judged by the world (bag delta, blocks, where the body stands), never by the skill's return
import threading as _threading

BASE = {}                 # the bag, the /state and the time at the start of the run (`_start`)

FAILED_AS_EXPECTED = {}   # scenario → the failure message that matched its `fails` pattern

INTERRUPTS = {}           # scenario → interruptions the run absorbed (injected or not)

ACCEPTANCE_D = "accept_fresh_iron_pickaxe"

def _skill(name):
    """The registered runner of a skill (every skill module is imported by the brain)."""
    from ... import brain  # noqa: F401
    from ...skill import REGISTRY
    return REGISTRY[name].runner

def _inv_now():
    return bag_now()

def _base_bag():
    """The bag the run started from (BASE["inv"]): as read by bag_now, or a recorded /inventory answer."""
    from ...world import Inventory
    got = BASE["inv"]
    return Inventory(got) if isinstance(got, dict) else got

def _start(name):
    """`before` hook head: forget the last run's verdicts and remember the bag and body this run starts from."""
    def hook(ctx):
        from ... import api
        FAILED_AS_EXPECTED.pop(name, None)
        INTERRUPTS[name] = 0
        RESUMED_LEFT.pop(name, None)
        BASE.clear()
        BASE.update(name=name, inv=bag_now(), state=api.get("/state"), t=time.time())
    return hook

def _base_count(token):
    from ...world import Inventory
    return _base_bag().count(token) if BASE.get("inv") else 0

# -- checks: what the world must show afterwards
def _gain(token, n, at_most=None):
    """The bag holds at least `n` more `token` than at the start (and, with `at_most`, not more than that)."""
    def check(api, inv):
        got = inv.count(token) - _base_count(token)
        return got >= n and (at_most is None or got <= at_most)
    return check

def _same_bag_and_place(r=1.5):
    """Goal already met: nothing taken, nothing spent, the body did not wander off."""
    def check(api, inv):
        from ...world import Inventory
        before = sorted((s["id"], s["count"]) for s in _base_bag().slots)
        after = sorted((s["id"], s["count"]) for s in inv.slots)
        s0, s1 = BASE["state"], api.get("/state")
        return before == after and math.dist((s0["x"], s0["y"], s0["z"]), (s1["x"], s1["y"], s1["z"])) <= r
    return check

def _alive(min_hp=1.0):
    return lambda api, inv: not api.get("/state")["dead"] and api.get("/state")["health"] >= min_hp

def _at(pos, r):
    return lambda api, inv: _near(api, pos, r)

def _blocks(lo, hi, name, least, most=None):
    """`name` blocks (or any of a tuple of names) in the box: at least `least`, at most `most`."""
    names = (name,) if isinstance(name, str) else tuple(name)
    def check(api, inv):
        n = sum(_count_blocks(api, lo, hi, nm) for nm in names)
        return n >= least and (most is None or n <= most)
    return check

def _same_bag():
    """Nothing taken, nothing spent: the bag reads as it did at the start."""
    def check(api, inv):
        from ...world import Inventory
        before = sorted((s_["id"], s_["count"]) for s_ in _base_bag().slots)
        return before == sorted((s_["id"], s_["count"]) for s_ in inv.slots)
    return check

def _not(check):
    return lambda api, inv: not check(api, inv)

def _slot_has(item, *words):
    """A carried `item` whose slot mentions all of `words` (enchantments, potion contents), as the mod reports it."""
    def check(api, inv):
        return any(s_["id"] == item and all(w in json.dumps(s_) for w in words) for s_ in inv.slots)
    return check

def _mobs_near(kind, least, r=16):
    def check(api, inv):
        from ...world import entities
        return len(entities(r, [kind])) >= least
    return check

def _under_feet(*names):
    def check(api, inv):
        from ...world import Region
        s_ = api.get("/state")
        p = (s_["blockX"], s_["blockY"] - 1, s_["blockZ"])
        return Region(p, p).name(p) in names
    return check

def _room_to_work():
    def check(api, inv):
        from ...skillcore import free_spots_here
        return bool(free_spots_here())
    return check

def _dropped_nothing():
    def check(api, inv):
        from ...world import entities
        return not entities(8, ["minecraft:item"])
    return check

def _no_block_suffix(lo, hi, suffix):
    def check(api, inv):
        from ...world import Region
        return not any(n.endswith(suffix) for n in Region(lo, hi).blocks.values())
    return check

def fed_as_needed(food_before, carried_before, food_after, carried_after):
    """Pure: the eating filled the bar — every bite called for eaten, and the bar within the last bite of FULL_BAR."""
    from ...data import FULL_BAR, NUTRITION
    from ...survive import bite_plan
    plan = bite_plan(food_before, carried_before)
    eaten = sum(carried_before.get(k, 0) - carried_after.get(k, 0) for k in carried_before)
    if not plan:
        return eaten == 0
    return eaten == len(plan) and food_after >= FULL_BAR - (NUTRITION[plan[-1].split(":")[-1]] - 1)

def _food_up():
    """The row's eating filled the bar as needed (`fed_as_needed`), from where it stood when the eating began."""
    def check(api, inv):
        from ...knowledge import ALL_FOOD
        from ...world import Inventory
        inv = inv if inv is not None else bag_now()
        before = _base_bag()
        return fed_as_needed(BASE.get("food_before", BASE["state"]["food"]), {f: before.count(f) for f in ALL_FOOD},
                             api.get("/state")["food"], {f: inv.count(f) for f in ALL_FOOD})
    return check

def _is_day():
    return lambda api, inv: int(api.get("/state")["timeOfDay"]) % DAY_TICKS < 12500

def _free_slots(n):
    return lambda api, inv: inv.free_slots() >= n

def _interrupted(least=1):
    return lambda api, inv: INTERRUPTS.get(BASE.get("name"), 0) >= least

def _failed_as_expected():
    return lambda api, inv: BASE.get("name") in FAILED_AS_EXPECTED

def _all(*checks):
    check = lambda api, inv: all(c(api, inv) for c in checks)      # noqa: E731
    check.parts = checks            # read back part by part when the whole says no (runner.check_parts)
    return check

def _named_all(named):
    """`_all` over (check, why) pairs that logs which part failed, once."""
    def check(api, inv):
        for c, why in named:
            if not c(api, inv):
                __import__("bonobo.api", fromlist=["log"]).log(f"check: False — {why} (first seen: {dict(FIRST)})")
                return False
        return True
    return check

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
    assert resolve is not None, "vocab binds `resolve` when it builds a call check"
    fn = resolve(name)
    return cmp(fn(*[api if a == "$api" else inv if a == "$inv" else a for a in args]), op, want)

PREDICATES = {"count": count, "state": state, "bag": bag, "call": call}

LOGIC = ("all", "any", "not", "now")      # composition: all/any of predicates, not one, one read on the bag now

FIRST = {}      # token → the run second it first showed in the bag (a watcher thread, `_first_times`)
RESUMED_LEFT = {}          # row → what the resume found still to do (an interrupt after the last load left nothing)
from ... import lifecycle as _lifecycle  # noqa: E402
_lifecycle.in_place(__name__, "BASE", "FAILED_AS_EXPECTED", "INTERRUPTS", "FIRST", "RESUMED_LEFT")     # a row's own

__all__ = ['FIRST', 'RESUMED_LEFT', 'ACCEPTANCE_D', 'BASE', 'FAILED_AS_EXPECTED', 'INTERRUPTS', 'LOGIC', 'OPS', 'PREDICATES', '_alive', '_all', '_at', '_base_count', '_blocks', '_dropped_nothing', '_failed_as_expected', '_food_up', '_free_slots', '_gain', '_interrupted', '_inv_now', '_is_day', '_mobs_near', '_named_all', '_no_block_suffix', '_not', '_room_to_work', '_same_bag', '_same_bag_and_place', '_skill', '_slot_has', '_start', '_threading', '_under_feet', 'bag', 'call', 'cmp', 'count', 'fed_as_needed', 'state']
