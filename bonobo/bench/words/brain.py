"""Brain words: the brain tier's world, its grid of decisions, the upkeep lines, their templates."""
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
from ...data import DAY_END, DAY_TICKS, POD_BLOCKS  # noqa: F401
from ...survive import DIG_IN_DEPTH, SLEEP_FROM_TICKS
from ..core import *          # noqa: F403  (the bench's primitives are this module's own vocabulary)
from ..core import (BOX, FLAG, NOTES, ORIGIN, SCENARIOS, SetupInvalid, _achieve, _c, _chat, _checked,
                         _command, _count_blocks, _drain, at, server_count, set_brain)
from ..runner import *        # noqa: F403
from ..runner import (LAST_FEEDBACK, LAST_LINES, _setup, _trace, classify, code_for, feedback_errors, load_table,
                           module_deps, record, run, save_table, setup_mismatches, silent_failure, status)
from ..bench_bases import BASES, CONDITIONS, SURPRISES, TARGET_S, TARGET_SLACK   # the bases' data: one home
from ..core import SWEEP, _platform  # noqa: F401
from ...api import McError
from .scene import *  # noqa: F401,F403
from .checks import *  # noqa: F401,F403
from .runs import *  # noqa: F401,F403
from .fight import *  # noqa: F401,F403

# -- the full bag: filled after the base's kit, leaving exactly `free` slots
def _fill_bag(free, item="dirt", stack=64):
    """`before` hook: fill the bag with `item` until `free` slots are left (the kit the setup gave stays)."""
    def hook(ctx):
        from ...world import Inventory
        room = bag_now().free_slots() - free
        if room > 0:
            _chat(f"give @p {item} {room * stack}")
            time.sleep(0.5)
    return hook

def _bench_machine(ctx, origin):
    """The arena's auto smelter as memory knows a built one (the dict `upkeep.ready_machine` hands the skill)."""
    name = ctx.mem.add_machine("auto_smelter", origin, 0, "minecraft:overworld", ["smelting"])
    return next(m for m in ctx.mem.machines("minecraft:overworld") if m["name"] == name)

# -- tier "brain": the whole brain on a private queue, the world set to the deciding moment, judged by the world and its log
BRAIN_LOG: dict = {"replans": 0}      # also keeps the real brain.replan (a fn): not an int table


def furnace_slots(reply):
    """Pure: {slot: (item id, count)} from the game's `data get block <pos> Items` answer."""
    import re
    text = " ".join(reply)
    out = {}
    for entry in re.findall(r"\{([^{}]*)\}", text):
        slot = re.search(r"Slot:\s*(\d+)b", entry)
        item = re.search(r'id:\s*"([^"]+)"', entry)
        count = re.search(r"count:\s*(\d+)", entry)
        if slot and item:
            out[int(slot.group(1))] = (item.group(1), int(count.group(1)) if count else 1)
    return out

def _furnace_holds(items, radius=16):
    """A furnace near holds one of `items` in input or output, read from the world."""
    from ...world import find
    for h in find(["furnace"], radius=radius, limit=8):
        slots = furnace_slots(_command(f"data get block {h['x']} {h['y']} {h['z']} Items", []))
        if any(slots.get(s, ("", 0))[0] in items for s in (0, 2)):
            return True
    return False

FIRST_WATCH = {"gen": 0}   # the row whose watcher may write FIRST: a new row's hook retires the last row's watcher

def slept_through(start_tod, tod):
    """Pure: the row began at night and the day is back — only a sleep turns it (the arena's clock stands still)."""
    return int(start_tod) % DAY_TICKS >= SLEEP_FROM_TICKS and int(tod) % DAY_TICKS < DAY_END

def first_step(gen, t0, inv, base_count, furnace_beef, now, morning=lambda: False):
    """One look of a row's watcher: stamp each token first above the row's start, on this row's clock. False (and
    nothing written) once another row's hook has started its own watcher — a stale watcher's clock is not this row's."""
    if gen != FIRST_WATCH["gen"]:
        return False
    for s_ in inv.slots:
        for tok in (s_["id"], s_["id"].rsplit("_", 1)[-1]):     # "minecraft:white_bed" → also "bed"
            if tok not in FIRST and inv.count(tok) > base_count(tok):
                FIRST[tok] = now - t0
    if "furnace_beef" not in FIRST and furnace_beef():
        FIRST["furnace_beef"] = now - t0
    if "morning" not in FIRST and morning():
        FIRST["morning"] = now - t0             # slept: the night skipped, read from the world's clock
    return True

def _first_times(ctx):
    """`before` hook: note when each token first rises above the row's start: the brain's decision order, read from the world."""
    FIRST_WATCH["gen"] += 1
    gen = FIRST_WATCH["gen"]
    t0 = time.time()

    def furnace_beef():
        try:
            return _furnace_holds(("minecraft:beef", "minecraft:cooked_beef"))    # beef in a furnace, read from the world
        except McError:
            return False

    def morning():
        try:
            from ... import api
            return slept_through(BASE["state"]["timeOfDay"], api.get("/state")["timeOfDay"])
        except McError:
            return False

    def watch():
        from ...world import Inventory
        while time.time() - t0 < 70:
            try:
                inv = bag_now()
            except McError:
                time.sleep(0.5)
                continue
            if not first_step(gen, t0, inv, _base_count, furnace_beef, time.time(), morning):
                return
            time.sleep(0.5)
    _threading.Thread(target=watch, daemon=True).start()

def slept_before(item, or_never=False):
    """The day came back (a sleep: FIRST's "morning", read from the world's clock) before `item` rose in the bag."""
    return _before_in_bag("morning", item, or_never)

def _first_seen(*orders):
    """Progress: each order (items) decided — one of its items has shown in the bag (FIRST): which came first is
    then known, the rest of the run judges nothing more."""
    return lambda: all(any(FIRST.get(i) is not None for i in items) for items in orders)

def _before_in_bag(first, then, or_never=False):
    """`first` appeared in the bag before `then` did (with `or_never`: or `then` never did)."""
    def check(api, inv):
        a, b = FIRST.get(first), FIRST.get(then)
        if b is None:
            return or_never and a is not None
        return a is not None and a < b
    return check

def _count_replans(ctx):
    """`before` hook: count the brain's plans for the row (brain.replan), the repair measure."""
    from ... import brain
    BRAIN_LOG["replans"] = 0
    real = BRAIN_LOG.setdefault("real_replan", brain.replan)
    def replan(*a, **k):
        BRAIN_LOG["replans"] += 1
        return real(*a, **k)
    brain.replan = replan

def _replans_at_most(n):
    def check(api, inv):
        from ... import brain
        brain.replan = BRAIN_LOG.get("real_replan", brain.replan)
        return 1 <= BRAIN_LOG["replans"] <= n
    return check

def _have(*needs):
    return __import__("bonobo.goals", fromlist=["goals"]).have(*needs)


def _remove_table_when_placed(ctx):
    """`before` hook: remove the plan's crafting table the moment it stands (the plan must repair one step, not restart)."""
    def watch():
        from ...world import find
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                hit = find(["crafting_table"], radius=6, limit=1)
            except McError:
                hit = []
            if hit:
                h = hit[0]
                _chat(f"setblock {h['x']} {h['y']} {h['z']} air")
                return
            time.sleep(0.2)
    _threading.Thread(target=watch, daemon=True).start()

def _not_banned(pos):
    return lambda api, inv: core.BRAIN.blacklist.get(tuple(pos), 0) <= time.time()

def _clear_bans(ctx):
    core.BRAIN.blacklist.clear()

def _seen(kind, pos):
    def before(ctx):
        core.BRAIN.mem.note_seen(kind, pos, "minecraft:overworld")
    return before

def _not_remembered(kind):
    return lambda api, inv: core.BRAIN.mem.seen(kind, "minecraft:overworld") == []

def _remembered_any(kinds):
    """The skill noted one of `kinds` (memory's sightings): false at setup once `_forget_all` ran for each."""
    return lambda api, inv: any(core.BRAIN.mem.seen(k, "minecraft:overworld") for k in kinds)

def _forget_all(kind):
    def before(ctx):
        for r in core.BRAIN.mem.seen(kind, "minecraft:overworld"):
            core.BRAIN.mem.forget_seen(kind, r["pos"], "minecraft:overworld")
    return before

_ARENA_B = [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} grass_block", "clear @p"]

IRON_ORE_FREE, IRON_ORE_CAGED = at(4, 0, 0), at(-4, 0, 0)

# -- the brain's decisions as a grid: one world holding every reachable resource, the moment set by dimensions; ore sealed nearby so three goals fit 30 s
DIAMOND_UP, DIAMOND_DOWN = at(2, 0, -2), at(4, -9, 0)

POCKET = at(0, -9, 0)

LOW_FOOD, LOW_FOOD_MAX_S = 10, 20     # food drained to ~10 before the run (a `before` hook: harness, not budget)

HUNGER_PER_TICK = 0.005      # exhaustion one level of the hunger effect adds a tick (HungerStatusEffect)
EXHAUSTION_PER_POINT = 4.0   # exhaustion that takes one saturation point, then one food point
HUNGER_MAX_AMP = 255
DRAIN_OVER = 0.05            # exhaustion past the last point's 4.0 (the game takes it only when exceeded)


def drain_plan(food, saturation, level, exhaustion=0.0):
    """Pure: (seconds, amplifier) of the one hunger effect that takes the bar from (food, saturation, the exhaustion
    already carried) to level + 1 — the server's own clock; None when it is there already."""
    points = math.ceil(max(0.0, float(saturation))) + max(0, int(food) - (level + 1))   # a part saturation point: one
    if points <= 0:
        return None
    # the game takes a point only past 4.0, never at it; what is carried counts toward the first
    need = max(DRAIN_OVER, EXHAUSTION_PER_POINT * points + DRAIN_OVER - float(exhaustion))
    per_s = HUNGER_PER_TICK * 20
    secs = max(1, math.ceil(need / (per_s * (HUNGER_MAX_AMP + 1))))
    return secs, min(HUNGER_MAX_AMP, max(0, math.ceil(need / (per_s * secs)) - 1))


REGEN_RULE = "natural_regeneration"     # regen eats exhaustion: off while the drain runs


def gamerule_value(lines):
    """Pure: a `gamerule X` reply's value ('… is currently set to: true'), or None."""
    for line in lines:
        m = re.search(r"set to: (\S+)", str(line))
        if m:
            return m.group(1)
    return None


FOOD_KEYS = ("foodLevel", "foodSaturationLevel", "foodExhaustionLevel")    # the server's bar (the client's lags)
DRAIN_TRIES = 3             # re-planned from fresh server reads while short, at most this often
DRAIN_POLL_S = 0.25         # how often the effect's end is looked for
HUNGER_ON = 'execute if entity @p[nbt={active_effects:[{id:"minecraft:hunger"}]}]'


def entity_number(lines):
    """Pure: the number in a '/data get entity … <key>' reply ('… entity data: 5.0f'), or None."""
    for line in lines:
        m = re.search(r"entity data: (-?[0-9.]+)", str(line))
        if m:
            return float(m.group(1))
    return None


def server_food():
    """The server's food level, saturation and carried exhaustion."""
    return {k: entity_number(core._command(f"data get entity @p {k}", [])) for k in FOOD_KEYS}


def _hunger_on():
    return any("Test passed" in str(line) for line in core._command(HUNGER_ON, []))


def _drain_to(level, max_s=LOW_FOOD_MAX_S, window=None):
    """`before` hook: the bar drained to `level` + 1 by planned hunger effects (drain_plan) on the server's own reads,
    each waited out on the server, re-planned while short; natural regeneration off during it, restored after. The
    reads and plans go to the report (SETUP_READOUT["drain"])."""
    def hook(ctx):
        from ..runner import SETUP_READOUT
        reads = SETUP_READOUT.setdefault("drain", [])
        # unread: the game's default
        was = gamerule_value(core._command(f"gamerule {REGEN_RULE}", [])) or core.WORLD_NORMAL[f"gamerule {REGEN_RULE}"]
        _chat(f"gamerule {REGEN_RULE} false")
        try:
            _chat("effect clear @p minecraft:saturation")      # a refill still running would undo the drain
            _chat("effect clear @p minecraft:hunger")
            for _ in range(DRAIN_TRIES):
                f = server_food()
                food_, sat_, exh_ = (f[k] for k in FOOD_KEYS)
                if food_ is None or sat_ is None or exh_ is None:
                    raise SetupInvalid(f"the server's food unread: {f}")
                plan = drain_plan(food_, sat_, level, exh_)
                reads.append({**f, "plan": plan})
                if plan is None:
                    break
                secs, amp = plan
                if secs > max_s:
                    raise SetupInvalid(f"the drain takes {secs} s, over {max_s}")
                _chat(f"effect give @p minecraft:hunger {secs} {amp} true")
                end = time.time() + secs + max_s          # the server's clock may lag the wall's
                time.sleep(secs)
                while _hunger_on() and time.time() < end:
                    time.sleep(DRAIN_POLL_S)
            last = server_food()["foodLevel"]
            reads.append({"final": last})
            if last is None:
                raise SetupInvalid("the server's food unread after the drain")
            food = int(last)
        finally:
            _chat(f"gamerule {REGEN_RULE} {was}")
        BASE["food_drained"] = food
        from ...reflexes import EAT_BELOW, STARVE
        lo, hi = window or (STARVE, EAT_BELOW)
        if not lo < food < hi:
            raise SetupInvalid(f"food {food} after the drain: wanted between {lo} and {hi}")
    return hook

THROW_START = (6, 0, 3)     # east of the grove's oak (3, 3), clear of the stone at x 5..7, z -1..1; +x: 7, 8, the edge

BRAIN_DIMS = {
    # "tight": dusk inside the night's lead (needs.due_now), so the bed comes first — its clock set by `_tight_dusk`
    # night: a bed carried — the row proves the night comes first (slept, then the task), not the bed's crafting
    "dusk": {"plenty": ["time set 1000"], "tight": [], "night": ["time set 18000", "give @p white_bed"]},
    # the effect only marks the row hungry for the body reset; the drain proper is the hook's
    "food": {"full": [], "low": ["effect give @p minecraft:hunger 1 0 true"]},
    # one_use: a crafting table stands by the start, so the table's place-and-take-back is not measured
    "tool": {"fresh": ["give @p iron_pickaxe"],
             "one_use": ["give @p iron_pickaxe[damage=249]", f"setblock {_c(at(-2, 0, -1))} crafting_table"]},
    "head": {"surface": [_tp()], "underground": [_tp(0.5, -9, 0.5)]},
    "seen": {"none": [], "noted": []},                  # a memory note, set by the `before` hook
    # filled by the hook; a full bag starts where the only open side is +x over the edge, so what is thrown lands
    # away from the tree (from the start, valuables_full threw toward it and the chop took it back: 041819)
    "bag": {"room": [], **{b: [_tp(*THROW_START)] for b in ("junk_full", "valuables_full")}},
}

BRAIN_BASE = {"dusk": "plenty", "food": "full", "tool": "fresh", "head": "surface", "seen": "none", "bag": "room"}

BAG_FILL = {"room": None, "junk_full": (0, "dirt"), "valuables_full": (0, "diamond")}

KIT_COBBLE = 16         # the brain rows' kit: a goal of cobblestone must ask for more than this, or it is met at once

COBBLE_MORE = 2         # a row proves the choice, not the job: two blocks (one_use's one-use pickaxe breaks on the second)

KIT_LOG, LOG_GOAL = 1, 2   # one log carried, two wanted: one chop shows the task done

BRAIN_WORLD = (_ARENA_B + [f"fill {_c(at(-8, -12, -8))} {_c(at(8, -3, 8))} stone"] + _grove((3, 3))
               + [f"fill {_c(POCKET)} {_c(at(0, -8, 0))} air",
                  f"fill {_c(at(1, -11, 1))} {_c(at(2, -10, 2))} iron_ore",
                  f"fill {_c(at(5, 0, -1))} {_c(at(7, 2, 1))} stone", f"fill {_c(at(1, 0, -3))} {_c(at(3, 2, -1))} stone",
                  f"setblock {_c(DIAMOND_UP)} diamond_ore",
                  f"setblock {_c(DIAMOND_DOWN)} diamond_ore", f"setblock {_c(at(-2, 0, 0))} furnace",
                  "give @p white_wool 3", "give @p oak_planks 8", "give @p crafting_table", "give @p stick 4",
                  "give @p iron_ingot 3", "give @p beef 2", "give @p coal 2", f"give @p cobblestone {KIT_COBBLE}", f"give @p oak_log {KIT_LOG}",
                  "give @p diamond_axe"])       # the best axe: logs are not the tool test here (kit rule)

def _diamond_of(cell):
    return DIAMOND_DOWN if cell["head"] == "underground" else DIAMOND_UP

# family → (grid, queue, what each cell must show): checks read the world and bag order, never log text
def _bed_then_log(cell):
    if cell["dusk"] == "plenty" and cell["food"] == "full":
        return (_all(_before_in_bag("log", "bed", or_never=True), _gain("log", LOG_GOAL - KIT_LOG),
                     lambda api, inv: inv.count("bed") == 0),
                "a day ahead: the task first, no bed made (must not)")
    if cell["food"] == "low":
        # food first, from the world: beef in a furnace or cooked beef before any log, the bar no lower at the end
        food_first = lambda api, inv: (_before_in_bag("furnace_beef", "log")(api, inv)             # noqa: E731
                                       or _before_in_bag("minecraft:cooked_beef", "log")(api, inv))
        kept = lambda api, inv: api.get("/state")["food"] >= BASE.get("food_drained", 0)        # noqa: E731
        return _all(food_first, kept), "hungry: food before the task (cooking it counts)"
    if cell["dusk"] == "night":
        return slept_before("log"), "night on the surface, a bed carried: slept before the task"
    return _before_in_bag("bed", "log", or_never=True), "dusk or night on the surface, no bed: the night first"

def _tool_rule(cell):
    if cell["tool"] == "one_use":
        return (_all(lambda api, inv: inv.count("minecraft:iron_pickaxe") >= 1,
                     lambda api, inv: inv.count("minecraft:stone_pickaxe") + inv.count("minecraft:wooden_pickaxe") == 0,
                     _gain("minecraft:cobblestone", COBBLE_MORE)), "broken: the best tier this bag crafts (iron)")
    return (_all(lambda api, inv: inv.count("minecraft:iron_ingot") == 3, _gain("minecraft:cobblestone", COBBLE_MORE)),
            "fresh: nothing crafted, the ingots kept (must not craft)")

def _night_rule(cell):
    below = lambda api, inv: api.get("/state")["blockY"] < at(0, -3, 0)[1]      # noqa: E731
    if cell["head"] == "underground" and cell["dusk"] == "night":
        return _all(_gain("minecraft:raw_iron", 1), below), "night underground: work there (ore), no climb"
    if cell["head"] == "underground" and cell["dusk"] == "tight":
        return below, "dusk underground: already under cover, no climb to the surface (boundary)"
    if cell["dusk"] == "night":
        return (slept_before("minecraft:raw_iron", or_never=True),
                "night on the surface, a bed carried: slept first")
    if cell["dusk"] != "plenty":
        return _before_in_bag("bed", "minecraft:raw_iron", or_never=True), "dusk or night on the surface: a bed first"
    return (lambda api, inv: int(api.get("/state")["timeOfDay"]) % DAY_TICKS < SLEEP_FROM_TICKS and inv.count("bed") == 0,
            "daylight: no bed made, no sleep (must not)")

def _bag_rule(cell):
    if cell["bag"] == "valuables_full":
        return _gain("minecraft:diamond", 0), "a bag of diamonds: not one thrown to make room (must not)"
    if cell["bag"] == "junk_full":
        return (_all(_gain("log", LOG_GOAL - KIT_LOG),
                     lambda api, inv: inv.count("minecraft:dirt") < _base_count("minecraft:dirt")),
                "a bag past BAG_FULL, junk: junk thrown, then the task done")
    return _gain("log", LOG_GOAL - KIT_LOG), "room for it: the task done as usual"

FINDS: dict = {"diamond": 0}          # also keeps the real api.get

def is_diamond_scan(path):
    """Pure: a /find that looks for diamond ore — not the estimates' one look per round (world.nearest's perBlock=1
    over every source block, diamond among them), which sees what is near and searches for nothing."""
    return path.startswith("/find") and "diamond" in path and "perBlock=1" not in path

def _count_finds(ctx):
    """`before` hook: count /find scans for diamond ore during the row, at api.get."""
    from ... import api
    real = FINDS.setdefault("real", api.get)
    def get(path, *a, **k):
        if is_diamond_scan(path):
            FINDS["diamond"] += 1
        return real(path, *a, **k)
    api.get = get

def _no_scan():
    def check(api_, inv):
        from ... import api
        api.get = FINDS.get("real", api.get)
        return FINDS["diamond"] == 0
    return check

def _seen_rule(cell):
    # seeing through stone is allowed: a noted ore is walked to straight, an unnoted one found by scanning
    if cell["seen"] == "noted":
        return (_all(_gain("minecraft:diamond", 1), _not_remembered("diamond_ore"), _no_scan()),
                "noted: straight there without a scan (must not scan), the note retired")
    return _gain("minecraft:diamond", 1), "not noted: found anyway, by scanning"

# a family's rule that is an order in the bag is decided once either item shows (cell → the order's items, or None:
# decided at the queue's end)
DECIDED = {"night_first": lambda c: ("bed", "log") if c["dusk"] == "tight" and c["food"] == "full" else None,
           "night_under": lambda c: ("bed", "minecraft:raw_iron") if c["dusk"] == "tight" and c["head"] == "surface"
           else None}

# one value off the base at a time: which combination wins is tested offline; a row confirms the decision is carried out
BRAIN_FAMILIES = {
    "night_first": (list(_cells(BRAIN_BASE, dims=("dusk", "food"), table=BRAIN_DIMS)), [_have(("log", LOG_GOAL))], _bed_then_log),
    "tool_tier": (list(_cells(BRAIN_BASE, dims=("tool", "head"), table=BRAIN_DIMS)),
                  # more than the kit carries, or the kit alone meets it
                  [_have(("minecraft:cobblestone", KIT_COBBLE + COBBLE_MORE))], _tool_rule),
    "night_under": (list(_cells(BRAIN_BASE, dims=("dusk", "head"), table=BRAIN_DIMS)), [], _night_rule),
    "tidy_then_task": (list(_cells(BRAIN_BASE, dims=("bag",), table=BRAIN_DIMS)), [_have(("log", LOG_GOAL))], _bag_rule),
    "seen_store": (list(_cells(BRAIN_BASE, dims=("seen", "head"), table=BRAIN_DIMS)),
                   [_have(("minecraft:diamond", 1))], _seen_rule),
}

def _cell_name(family, cell):
    moved = [cell[d] for d in BRAIN_DIMS if cell[d] != BRAIN_BASE[d]]
    return f"{family}__" + ("_".join(moved) or "base")

def _cell_before(cell):
    hooks = [_clear_bans, _forget_all("diamond_ore"), _first_times]
    if cell["seen"] == "noted":
        hooks.append(_seen("diamond_ore", _diamond_of(cell)))
    hooks.append(_count_finds)
    if cell["food"] == "low":
        hooks.append(_drain_to(LOW_FOOD))       # setup's hunger drains the bar; cleared at LOW_FOOD
    if cell["dusk"] == "tight":
        hooks.append(_tight_dusk)
    return hooks

def tight_dusk_time(plan_s):
    """Pure: the clock whose dusk (DAY_END) falls inside the lead of a `plan_s` night (needs.due_now): half of it left."""
    from ...beliefs import TICKS_PER_S
    from ...needs import LEAD
    return DAY_END - round(plan_s * LEAD / 2 * TICKS_PER_S)

def _tight_dusk(ctx):
    """`before` hook: the clock set inside the lead of the night this bag prices (the brain's own needs.overnight)."""
    from ... import api
    from ...world import Snapshot
    _way, secs, _steps = core.BRAIN.needs.overnight(Snapshot.from_readings(api.get("/state"), bag_now()))
    if not math.isfinite(secs):
        raise SetupInvalid("no way through the night priced from this bag: no dusk is tight")
    _chat(f"time set {tight_dusk_time(secs)}")

def _cell_setup_hooks(cell):
    """`before` hooks that make the row's world, run before `_start` reads the base."""
    return [_fill_bag(*BAG_FILL[cell["bag"]])] if BAG_FILL[cell["bag"]] else []

def _grid_cells():
    """Cells of every family, one row per distinct cell: a shared cell checks every family's expectation."""
    cells = {}
    for fam, (grid, queue, rule) in BRAIN_FAMILIES.items():
        for cell in grid:
            key = tuple(cell[d] for d in BRAIN_DIMS)
            entry = cells.setdefault(key, {"cell": cell, "families": [], "queue": [], "rules": []})
            entry["families"].append(fam)
            entry["queue"] += [g for g in queue if g not in entry["queue"]]
            entry["rules"].append(rule)
    return cells

def grid_name(families, cell):
    """The row of a cell: its family's name when only one family has it, else "brain"."""
    return _cell_name(families[0] if len(families) == 1 else "brain", cell)

# -- every upkeep line through the whole brain: the moment built, the answer shown in the world; (line, setup, hooks, done, check)
def _job_ready_at(pos, item, n):
    """`before` hook: memory holds a finished background furnace job at `pos` (its output already in the furnace)."""
    def hook(ctx):
        ctx.mem.add_job("furnace", pos, "minecraft:overworld", item, n, time.time() - 1, False)
    return hook

def _state_before(*fields):
    """`before` hook: each /state field as the run starts, BASE[f"{field}_before"] (what `rose` and food_up read)."""
    def hook(ctx):
        s = __import__("bonobo.api", fromlist=["get"]).get("/state")
        BASE.update({f"{f}_before": s[f] for f in fields})
    return hook

def _rose(field):
    """check: the /state field above where it stood before the run (`state_before`)."""
    return lambda api, inv: api.get("/state")[field] > BASE[f"{field}_before"]

def _command_then(text, settle_s):
    """`before` hook: one console command, then `settle_s` for it to land."""
    def hook(ctx):
        _chat(text)
        time.sleep(settle_s)
    return hook

def _summon_after(seconds, mob, pos):
    """`before` hook: a persistent `mob` summoned at `pos` `seconds` into the run."""
    def hook(ctx):
        _threading.Timer(seconds, lambda: _chat(f"summon {mob} {_c(pos)} {{PersistenceRequired:1b}}")).start()
    return hook

def _interrupt_counted(token, n):
    """`before` hook: an interrupt the moment `n` more `token` are held, counted for the row (a slice absorbs it)."""
    def act():
        row = BASE.get("name")
        INTERRUPTS[row] = INTERRUPTS.get(row, 0) + 1
        _inject_interrupt()
    return _when(gained_at_least(token, n), act)

def _blocked_toward(pos):
    """`before` hook: upkeep's memory of a walk that failed here, toward `pos` (what `Upkeep.failed` writes)."""
    def hook(ctx):
        from ... import retry
        from ... import api
        from ...world import Inventory, Snapshot
        snap = Snapshot.from_readings(api.get("/state"), bag_now())
        core.BRAIN.reflexes.blocked = {"t": time.time(), "place": retry.place_signature(snap.feet, snap.night),
                                    "pos": pos}
    return hook

def _stuck_for(seconds):
    """`before` hook: upkeep's history says we stood here, bag unchanged, for `seconds`."""
    def hook(ctx):
        from ...needs import bag_signature
        from ... import api
        from ...world import Inventory, Snapshot
        snap = Snapshot.from_readings(api.get("/state"), bag_now())
        core.BRAIN.reflexes.history = [(time.time() - seconds, snap.feet, bag_signature(snap.inv))]
    return hook

def _machine_due(origin, n):
    """`before` hook: memory holds an auto smelter at `origin` with an order of `n` ingots already due."""
    def hook(ctx):
        m = _bench_machine(ctx, origin)
        ctx.mem.add_pending(m["name"], "minecraft:iron_ingot", n, time.time() - 1)
    return hook

_st = lambda api: api.get("/state")     # noqa: E731


# the night's shelter by what the bag allows; a bed makes none of them (must not)
_NIGHT_FLOOR = [f"fill {_c(at(-8, -6, -8))} {_c(at(8, -1, 8))} stone", _tp(), "time set 18000"]

# no pickaxe in a fight: the "no pickaxe" row waits until it ends; a dirt patch on this ground is dug into by hand, one across a drop never
DIRT_PATCH = (at(7, -3, -1), at(8, -1, 1))

def _in_the_patch_underground(api, inv):
    s = api.get("/state")
    (x0, _y0, z0), (x1, _y1, z1) = DIRT_PATCH
    return (x0 <= s["blockX"] <= x1 and z0 <= s["blockZ"] <= z1 and s["blockY"] <= at(0, 0, 0)[1] - DIG_IN_DEPTH
            and _enclosed())

# eating on the move by the jar's autoeat, still walking while chewing; control: mining is not interrupted to eat
WALK = {}
from ... import lifecycle as _lifecycle  # noqa: E402
_lifecycle.in_place(__name__, "BRAIN_LOG", "FINDS", "WALK")     # a row's own records (FIRST_WATCH: a generation, kept)

BITE_S = 1.6        # one bite (32 ticks): the window before the bar rises in which the body must keep moving

def ate_on_the_way(frames):
    """Pure over trace frames: food rose during the walk, no frame ran "eat", and x grew through the bite (autoeat while walking)."""
    fed = [f for f in frames if f.get("food") is not None]
    if not fed or any((f.get("task") or {}).get("type") == "eat" for f in frames):
        return False
    rise = next((f for f in fed if f["food"] > fed[0]["food"]), None)
    if rise is None:
        return False
    window = [f for f in frames if rise["t"] - BITE_S <= f["t"] <= rise["t"] and f.get("x") is not None]
    if len(window) < 2:
        return False
    # x at each whole second of the window, and at its end: every step forward.
    at_s = lambda sec: min(window, key=lambda f: abs(f["t"] - (window[0]["t"] + sec)))["x"]   # noqa: E731
    xs = [at_s(sec) for sec in range(int(window[-1]["t"] - window[0]["t"]) + 1)] + [window[-1]["x"]]
    xs = [x for k, x in enumerate(xs) if k == 0 or x != xs[k - 1] or k < len(xs) - 1]
    return all(b > a for a, b in zip(xs, xs[1:]))

def walk_ate():
    """ate_on_the_way over the last walk's trace (`_walk_once`), read when the check runs — not when the row is built."""
    return ate_on_the_way(WALK.get("frames", []))

def fed_up(frames, level):
    """Pure: the walk's done — the bar rose during it and reached `level` (the jar's autoeat eats to it)."""
    fed = [f["food"] for f in frames if f.get("food") is not None]
    return bool(fed) and fed[-1] > fed[0] and fed[-1] >= level

def _walk_once(ctx):
    """Walk east hungry until fed (`fed_up` at the autoeat's level), the walk traced (position, food, the jar's
    task) — the row is the eating, so the walk stops once the bar is where the jar eats it to."""
    import threading
    from ... import api, nav
    frames, stop = [], threading.Event()

    def watch():
        while not stop.is_set():
            if fed_up(frames, nav.WALK_EAT_BELOW):
                api.request_interrupt("bench: fed on the walk")
                return
            stop.wait(0.1)
    threading.Thread(target=_trace, args=(stop, frames), daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    try:
        _skill("travel_to")(ctx, at(18, 0, 0), 2)
    except api.Interrupted:
        api.post("/stop")
        if not fed_up(frames, nav.WALK_EAT_BELOW):
            raise
    finally:
        stop.set()
        WALK["frames"] = list(frames)
    return True

def worked_fed(frames):
    """Pure over trace frames: eaten while working, never paused for it — no "eat" task, every bite (the BITE_S
    before a rise) inside a running work task, and the bar no lower at the end than at the start."""
    fed = [f for f in frames if f.get("food") is not None]
    if not fed or any((f.get("task") or {}).get("type") == "eat" for f in frames):
        return False
    if fed[-1]["food"] < fed[0]["food"]:
        return False
    for prev, rise in zip(fed, fed[1:]):
        if rise["food"] <= prev["food"]:
            continue
        bite = [f for f in frames if rise["t"] - BITE_S <= f["t"] <= rise["t"]]
        if any((f.get("task") or {}).get("status") != "running" for f in bite):
            return False
    return True

def mine_fed():
    """worked_fed over the last traced mine (`_mine_hungry`), read when the check runs."""
    return worked_fed(WALK.get("mine", []))

def _mine_hungry(ctx):
    """3 cobblestone mined hungry, the whole run traced (position, food, the jar's task)."""
    import threading
    frames, stop = [], threading.Event()
    threading.Thread(target=_trace, args=(stop, frames), daemon=True).start()
    try:
        return _skill("mine")(ctx, "minecraft:cobblestone", 3, ["stone"], 0)
    finally:
        stop.set()
        WALK["mine"] = frames

def _hunger_drained(ctx):
    """`before` hook: food drained to about half (hunger at full strength for 5 s), and the level remembered — no eat
    target set (that is the eat base's `hungry`, whose word a row saying "&hungry" got)."""
    WALK.clear()
    _chat("effect give @p minecraft:hunger 5 255 true")
    time.sleep(5.5)
    BASE.update(food_before=__import__("bonobo.api", fromlist=["get"]).get("/state")["food"])

# three furnaces, a chain each: interrupted after the first, the resume loads only what is still in the bag
SMELT_FURNACES = (at(2, 0, -2), at(2, 0, 2), at(-2, 0, 2))

def _interrupt_once_loaded(ctx):
    """`before` hook: the interrupt lands the moment raw iron first leaves the bag (a furnace took its share)."""
    def watch():
        t0 = time.time()
        while time.time() - t0 < 30 and BASE.get("name") == "smelt_job_interrupted":
            try:
                if _inv_now().count("minecraft:raw_iron") < _base_count("minecraft:raw_iron"):
                    _inject_interrupt()
                    return
            except McError:
                pass
            time.sleep(0.05)
    _threading.Thread(target=watch, daemon=True).start()

def _iron_in_furnaces():
    """Raw iron and iron ingots the three furnaces hold, read from the world (data get block … Items)."""
    total = 0
    for p in SMELT_FURNACES:
        slots = furnace_slots(_command(f"data get block {p[0]} {p[1]} {p[2]} Items", []))
        total += sum(n for s_, (item, n) in slots.items() if s_ in (0, 2)
                     and item in ("minecraft:raw_iron", "minecraft:iron_ingot"))
    return total


def _load_the_rest(ctx):
    """Resume: what is still in the bag, loaded — recomputed from the world, not from where the chain stopped."""
    left = _inv_now().count("minecraft:raw_iron")
    RESUMED_LEFT["smelt_job_interrupted"] = left
    if left:
        _skill("start_smelt_job")(ctx, "minecraft:iron_ingot", "minecraft:raw_iron", left, "coal")
    return True

HOME_BED, HOME_FURNACE = (at(4, 0, 2), at(5, 0, 2)), at(4, 0, -2)

def _home_is_ours(ctx):
    """`before` hook: the bed and furnace are a site of ours and the furnace a station — to be kept out of the blast."""
    blocks = {f"{c[0]},{c[1]},{c[2]}": "red_bed" for c in HOME_BED}
    blocks[f"{HOME_FURNACE[0]},{HOME_FURNACE[1]},{HOME_FURNACE[2]}"] = "furnace"
    core.BRAIN.mem.add_site("home", HOME_BED[0], "minecraft:overworld", snapshot={"blocks": blocks}, name="home")
    core.BRAIN.mem.add_station("minecraft:furnace", HOME_FURNACE, "minecraft:overworld")

# knocked off a raised platform mid-fight: the combat kit's water bucket must catch the fall
EDGE_Y = 4

SEARCH_ARENA = [f"fill {_c(at(-8, -3, -8))} {_c(at(20, -1, 8))} stone",               # the bench box's whole floor
                f"fill {_c(at(6, 0, -6))} {_c(at(8, 4, 6))} stone", _tp()]                # a hill in the way

SEARCH_ORE = at(14, -1, 3)                   # a diamond remembered past the hill, one down (dig one to it)

def _set_time(t):
    return lambda: _chat(f"time set {t}")

SEARCH_FLAGS = {}
_lifecycle.in_place(__name__, "SEARCH_FLAGS")

def upkeep_row(name, line, doc, scene, hooks, done, check):
    """One upkeep line through the whole brain, nothing queued: the moment built, the answer in the world."""
    return _row(name, f"upkeep, {doc}", "reflexes", scene,
                ("brain_rounds", 22, nest(done)), items(check), point="C",
                before=hooks, skills=[], tier_fixed="brain", combat=line == "eat",
                tags={"base": "upkeep", "line": line})

def brain_row(name, doc, scene, queue, done, minutes, check, hooks=(), variant=()):
    """The whole brain on a private queue, the world built up to the decision; the slice judged too."""
    return _row(name, doc, "brain", scene, ("slice", nest(done), min(minutes, 0.4), None, queue),
                [top(check), ("slice_check", None)], point="C", before=hooks, skills=[], tier_fixed="brain",
                combat=any(isinstance(h, tuple) and h[0] == "fight_recorded" for h in hooks), tags={"base": "brain"},
                queue=queue, variant=list(variant))

def dirt_row(name, doc, extra, done, check):
    """Dusk on stone, an empty bag, a dirt patch along the platform: dug in there by hand (or never walked to)."""
    return _row(name, doc, "brain", [("floor",), ("fill", ("@", 7, -3, -1), ("@", 8, -1, 1), "dirt"),
                                     ("fill", ("@", 7, -4, -1), ("@", 8, -4, 1), "stone")]
                + list(extra) + [("stand",), ("time", DAY_END)], ("brain_rounds", 25, nest(done)), items(check),
                point="C", skills=["shelter:dig in"], tier_fixed="brain", kit=[],
                tags={"base": "brain", "family": "night_dirt"})

def cell_row(name, *key):
    """A cell of the brain's grid: its families' goals queued, every family's rule judged, the slice too."""
    entry = _grid_cells()[key]
    cell, fams = entry["cell"], entry["families"]
    judged = [BRAIN_FAMILIES[f][2](cell) for f in fams]
    orders = [DECIDED.get(f, lambda c: None)(cell) for f in fams]
    decided = ("!first_seen", *[list(o) for o in orders]) if all(orders) else None
    row = _row(name, f"{'+'.join(fams)}: " + ", ".join(f"{d} {cell[d]}" for d in BRAIN_DIMS) + " → "
               + "; ".join(why for _c, why in judged), "brain", [("sheet", "BRAIN_WORLD"), ("brain_dims",) + key],
               ("slice", decided, 0.4, None, list(entry["queue"])),
               [("brain_rule", f) + key for f in fams] + [("slice_check", None)], point="C", skills=[],
               tier_fixed="brain", combat=False, queue=list(entry["queue"]),
               tags={"base": "brain", "family": "+".join(fams), **{d: cell[d] for d in BRAIN_DIMS}},
               why=[why for _c, why in judged] + ["the slice"])
    return dict(row, before=[("brain_cell_hooks", name) + key], **({"kit": []} if cell["tool"] != "fresh" else {}))

def _grid_cell(key):
    return _grid_cells()[tuple(key)]["cell"]

def brain_rule(fam, *key):
    """A grid family's rule for one cell (BRAIN_FAMILIES): its check."""
    return BRAIN_FAMILIES[fam][2](_grid_cell(key))[0]

def brain_cell_hooks(name, *key):
    cell = _grid_cell(key)
    return _hooks(*_cell_setup_hooks(cell), _start(name), *_cell_before(cell))

WORDS.update(brain_rule=brain_rule, brain_cell_hooks=brain_cell_hooks)

SCENE["brain_dims"] = lambda *key: [c for d, v in zip(BRAIN_DIMS, key) for c in BRAIN_DIMS[d][v]]

TEMPLATES = {t: globals()[f"{t}_row"] for t in ("upkeep", "brain", "dirt", "cell")}
NAMES = {"upkeep": lambda line, *p: f"upkeep__{line}",
         "cell": lambda *key: grid_name(_grid_cells()[key]["families"], _grid_cell(key)),
         "brain": lambda name, *p: name, "dirt": lambda name, *p: name}

__all__ = ['DECIDED', '_first_seen', 'tight_dusk_time', '_tight_dusk', '_state_before', '_rose', '_command_then', '_summon_after', '_interrupt_counted', 'BAG_FILL', 'BITE_S', 'BRAIN_BASE', 'BRAIN_DIMS', 'BRAIN_FAMILIES', 'BRAIN_LOG', 'BRAIN_WORLD', 'COBBLE_MORE', 'DIAMOND_DOWN', 'DIAMOND_UP', 'DIRT_PATCH', 'DRAIN_OVER', 'EXHAUSTION_PER_POINT', 'HUNGER_MAX_AMP', 'HUNGER_PER_TICK', 'EDGE_Y', 'FINDS', 'FIRST_WATCH', 'HOME_BED', 'HOME_FURNACE', 'IRON_ORE_CAGED', 'IRON_ORE_FREE', 'KIT_COBBLE', 'KIT_LOG', 'LOG_GOAL', 'LOW_FOOD', 'LOW_FOOD_MAX_S', 'POCKET', 'REGEN_RULE', 'SEARCH_ARENA', 'SEARCH_FLAGS', 'SEARCH_ORE', 'SMELT_FURNACES', 'THROW_START', 'WALK', '_ARENA_B', '_NIGHT_FLOOR', '_bag_rule', '_bed_then_log', '_before_in_bag', '_bench_machine', '_blocked_toward', '_cell_before', '_cell_name', '_cell_setup_hooks', '_clear_bans', '_count_finds', '_count_replans', '_diamond_of', '_drain_to', '_fill_bag', '_first_times', '_forget_all', '_furnace_holds', '_grid_cell', '_grid_cells', '_have', '_home_is_ours', '_hunger_drained', '_in_the_patch_underground', '_interrupt_once_loaded', '_iron_in_furnaces', '_job_ready_at', '_load_the_rest', '_machine_due', '_mine_hungry', '_night_rule', '_no_scan', '_not_banned', '_not_remembered', '_remembered_any', '_remove_table_when_placed', '_replans_at_most', '_seen', '_seen_rule', '_set_time', '_st', '_stuck_for', '_tool_rule', '_walk_once', 'ate_on_the_way', 'brain_cell_hooks', 'brain_row', 'brain_rule', 'cell_row', 'dirt_row', 'drain_plan', 'fed_up', 'first_step', 'furnace_slots', 'gamerule_value', 'grid_name', 'is_diamond_scan', 'mine_fed', 'slept_before', 'slept_through', 'upkeep_row', 'walk_ate', 'worked_fed']
