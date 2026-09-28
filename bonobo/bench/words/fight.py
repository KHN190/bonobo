"""Fight words: a combat cell built, fought, recorded and judged; the fight rows' templates; the End's pieces."""
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
from typing import Any

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
from ...api import McError
from .scene import *  # noqa: F401,F403
from .checks import *  # noqa: F401,F403
from .runs import *  # noqa: F401,F403
from ..bench_combat import (ARMED, ARMOUR, BLOOD, COUNT, DIMS, DISTANCE, ENEMY, GROUND, KIT, NEEDS,  # noqa: F401
                            UNARMED, WAVES, WEAPON, CORRIDOR_END)     # combat's dimensions: data in its table

# -- fights (the combat table's machinery): a cell of bench_combat's dimensions built, fought, recorded, judged ---
import random  # noqa: E402

from ... import estimate, paths  # noqa: E402,F401

from ..core import SWEEP, _platform  # noqa: E402

def _siege_kit():
    """The armed baseline, plus what a long fight needs more of."""
    return (scene(WEAPON["iron"] + ARMOUR["iron"] + KIT["full"])
            + ["item replace entity @p armor.legs with iron_leggings",
               "item replace entity @p armor.feet with iron_boots",
               "give @p cooked_beef 16", "give @p cobblestone 128"])

def _scatter(seed):
    """A few blocks of relief on the floor: a step to stand on, a dip to drop into, something to put between us and it."""
    rng = random.Random(seed)
    out = []
    for _ in range(rng.randint(3, 6)):
        x, z = rng.randint(-ARENA_REACH + 4, ARENA_REACH - 4), rng.randint(-ARENA_REACH + 4, ARENA_REACH - 4)
        if abs(x) < 3 and abs(z) < 3:
            continue                      # not under our own feet
        if rng.random() < 0.5:
            height = rng.randint(1, 2)
            out.append(f"fill {_c(at(x, 0, z))} {_c(at(x + rng.randint(0, 2), height, z + rng.randint(0, 2)))} stone")
        else:
            out.append(f"fill {_c(at(x, -2, z))} {_c(at(x + rng.randint(1, 2), -1, z + rng.randint(1, 2)))} air")
    return out

def _roof():
    """A lid and a floor on the walled platform: a cell is a room, not a clearing."""
    lo, hi = at(-ARENA_REACH, 5, -ARENA_REACH), at(ARENA_REACH + 3, 5, ARENA_REACH)
    # glowstone in the lid: light to watch by, nothing on the floor
    lamps = [f"setblock {_c(at(x, 5, z))} glowstone" for x in range(-ARENA_REACH + 3, ARENA_REACH + 3, 6)
             for z in range(-ARENA_REACH + 3, ARENA_REACH, 6)]
    return [f"fill {_c(lo)} {_c(hi)} stone"] + lamps

def _cells(base, dims=None, repeat=1, over=None, table=None):
    """The cells a pass visits: one dimension off the baseline at a time, or the product of `over`, each repeated."""
    import itertools
    table = DIMS if table is None else table     # another sheet's dimensions (the brain tier's) walk the same way
    seen = []
    if over:
        for values in itertools.product(*(table[name] for name in over)):
            seen.append(dict(base, **dict(zip(over, values))))
    else:
        seen.append(dict(base))
    for name in (dims or ()):
        for value in table[name]:
            if value != base[name]:
                seen.append(dict(base, **{name: value}))
    for cell in seen:
        for run in range(max(1, repeat)):
            yield dict(cell, run=run)

def _seed_of(cell):
    """One seed per cell per pass lays out ground, enemies and mood, and goes into the row so it can be rebuilt exactly."""
    return cell.get("seed") if cell.get("seed") is not None else random.randrange(1 << 30)

def _build(cell):
    """A cell, realised: a sealed room, then each dimension's own commands."""
    seed = cell.setdefault("seed", _seed_of(cell))
    out = (_platform(reach=ARENA_REACH, walled=True) + _roof() + _scatter(seed) + _revive()
           # full health and food: health carried over makes `blood` two variables at once
           + ["clear @p", "effect clear @p", "effect give @p minecraft:instant_health 10 1 true",
              "effect give @p minecraft:saturation 1 10 true",
              "difficulty normal", "time set day"])
    for name in ("ground", "weapon", "armour", "kit", "blood"):
        out += scene(DIMS[name][cell[name]])
    out += FIGHT_BUCKET
    kind = ENEMY[cell["enemy"]]
    if kind is None:
        return out
    return out + _summon(((kind, COUNT[cell["count"]]),), spread=DISTANCE[cell["distance"]], seed=seed)

def _kinds_of(cell):
    return {ENEMY[cell["enemy"]]}

def _carry(hp_lost, meals, blocks):
    """The state the waves before left, in BLOOD's own form (magic damage: armour-proof)."""
    return ([f"damage @p {hp_lost} minecraft:magic"] if hp_lost else []) + \
        ([f"clear @p cooked_beef {meals}"] if meals else []) + ([f"clear @p cobblestone {blocks}"] if blocks else [])

SHAPE_COLUMNS = {"reshape", "wall_in"}

# wide enough for every answer (escape_spot walks up to 16; the first cell fell off a 9-block platform)
ARENA_REACH = 24

def _revive():
    """Put the player back on their feet before a cell is built."""
    from ... import api as _api
    try:
        if _api.get("/state").get("dead"):
            _api.post("/respawn")
            time.sleep(1.0)
    except McError:
        pass
    return ["gamemode survival @p", "effect clear @p"]

def _columns_possible(cell):
    """The columns this cell paid for: what the kit gave, minus what the situation cannot use."""
    # a siege cell ({wave, line_up}) names no kit: nothing was paid for, no column is missing
    want = set(NEEDS.get(cell.get("kit"), ()))
    if cell.get("blood") != "hurt":
        want.discard("eat")          # eating at full health is not an option anywhere
    return want

def _combat_intent(state: dict[str, Any]):
    """What the threat model wants, before anything moves: every column, its price, and the state it priced from."""
    from ... import api as _api, fight_loop, perception, threat
    near = _api.get("/entities?radius=24").get("entities", []) or []
    now = time.time()
    rows = perception.note_threats(near, now, here=(state["x"], state["y"], state["z"]))
    if not rows:
        return {"rows": 0, "held": "ignore", "worth_s": 0.0, "options": {}, "state": None}
    try:
        state = dict(state, field=perception.ground(state),
                     **perception.kit(str(state.get("selected", "")) + str(state.get("screen"))))
        state["dig_ok"] = perception.dig_ok(state["field"], state.get("pick_tier"))     # as the live answer reads it
    except McError:
        pass
    sstate = threat.price_state(hp=max(1, int(state.get("health", 20))), armor=int(state.get("armor", 0)))
    price = lambda dhp: threat.hp_seconds(sstate, dhp)
    # the entity ids with the rows, as perception bids: without them every fight option named no target, and the
    # held decision this seeds posted attack(entity=None) into the live fight (combat__dig_in 01:03:09, a 500 ×3)
    ids = threat.ids_by_row(near, rows)
    st = fight_loop.threat_state(state, rows, ids=ids)
    horizon, opts = threat.horizon_for(st), threat.options(st)
    fight_loop.STATE.held = None
    chosen = fight_loop.bid(state, rows, price, now=now, ids=ids)
    return {"rows": len(rows),
            "options": {o.kind: {"hp": round(o.hp, 2), "seconds": round(o.seconds, 2),
                                 "leaves": round(o.leaves, 3), "why": o.why,
                                 "saves": round(threat.saves(o, opts, price, horizon), 2)} for o in opts},
            "held": chosen[0].kind if chosen else "ignore",
            "worth_s": chosen[1] if chosen else 0.0,
            "horizon_s": round(horizon, 2),
            "hp_tax_s": round(estimate.pressure_hp_s(st["here"], rows, st.get("protection", 0.0),
                                                     ground=st.get("field")), 3),
            "no_go": len(threat.no_go(st)),
            "seen_at": perception.seen_at(),
            "state": st}          # the live state: measured from, then written down by `_plain`

TRACE_EVERY_S = 0.2

def reflex_last(api_):
    """The jar's last reflex act from GET /reflex (`last` / `lastAct`), or None when this jar doesn't say."""
    try:
        r = api_.get("/reflex")
    except McError:
        return None
    return r.get("last") or r.get("lastAct")


def _sampler(stop, out, began):
    """The trace, on its own thread."""
    from ... import api as _api
    from ...world import entities
    kinds = _threat_kinds()
    while not stop.is_set():
        try:
            state = _api.get("/state")
            near = [(e["type"], round(e["distance"], 2), round(e.get("health", 0.0), 1))
                    for e in entities(24) if e.get("type") in kinds]
            task = (state.get("control") or {}).get("task") or {}
            out.append({"t": round(time.time() - began, 2), "hp": state["health"],
                        "pos": [round(state[k], 2) for k in ("x", "y", "z")], "near": near,
                        "blocking": bool(state.get("blocking")),
                        # what moved the body: the running task and the jar's last reflex act (escape: off the pillar)
                        "task": [task.get("type"), task.get("status"), str(task.get("message") or "")[:60]]
                        if task else None,
                        "reflex": reflex_last(_api)})
        except McError:
            pass
        stop.wait(TRACE_EVERY_S)

def _restock(cell):
    """Put the cell's enemies back."""
    if not cell:
        return
    kind = ENEMY.get(cell.get("enemy"))
    if not kind:
        return
    for command in _summon(((kind, COUNT[cell["count"]]),), spread=DISTANCE[cell["distance"]],
                           seed=cell.get("seed")):
        _chat(command)

ENGAGED_INTENT: dict = {}     # "intent": the priced options at the first answer that went out in the window
WINDOW_PROBE: dict = {}       # perception at the window's mark and end (paused, looks, last answer, engaged)


def answered_by_time(t0):
    """The looks perception took since `t0` (their own timestamps)."""
    from ... import perception
    with perception.STATE.lock:
        return [a for a in perception.STATE.answered if a.get("t", 0.0) >= round(t0, 2) - 0.01]


def perception_probe(t0):
    """What perception was doing: paused, looks held, looks since `t0`, the last answer's time, a fight engaged."""
    from ... import fight_loop, perception
    looks = list(perception.STATE.answered)
    last = next((a["t"] for a in reversed(looks) if a.get("outcome") == "answered"), None)
    return {"paused": bool(perception.STATE.paused), "looks": len(looks),
            "since_mark": sum(1 for a in looks if a.get("t", 0.0) >= round(t0, 2) - 0.01),
            "last_answer_t": last, "engaged": fight_loop.engaged() is not None, "watching": perception.watching()}


def missing_columns(cell, start, engaged=None):
    """Pure: the columns the cell paid for that were not on offer — read once engaged (`engaged`), not at the window's
    start (a walker held 11 off offered only ignore)."""
    intent = engaged or start
    return sorted(_columns_possible(cell) - set(intent.get("options") or {})) if intent.get("rows") else []


def _combat_execute(seconds, until=None, cell=None):
    """Live in the cell with ONE layer driving, recording every look the threat layer took and a 5 Hz trace."""
    import threading
    from ... import perception
    from ... import api
    from ...world import Inventory, Snapshot
    if not perception.watching():
        raise SetupInvalid("the threat layer is not running: nothing would answer, and nothing would be measured")
    from ..runner import take_row_mark, window_opened_at
    mark = take_row_mark()
    from_window = mark is not None
    mark = perception.looks_taken() if mark is None else mark
    t_mark = window_opened_at() or time.time()
    WINDOW_PROBE.clear()
    WINDOW_PROBE.update(from_window=from_window, at_mark=perception_probe(t_mark))
    began, worst = time.time(), Snapshot.from_readings(api.get("/state"), bag_now()).state["health"]
    trace, stop = [], threading.Event()
    ENGAGED_INTENT.clear()
    watcher = threading.Thread(target=_sampler, args=(stop, trace, began), daemon=True)
    watcher.start()
    # ONE layer drives: the planner takes no round in the window (Brain.not_taking_part, which this used, went in
    # fda0116 — every cell since raised SetupInvalid 'no way to stand down' at 1 s: combat__block_gap ×3); the threat
    # layer answers from the perception thread as always
    try:
        while time.time() - began < seconds and (until is None or until()):
            state = Snapshot.from_readings(api.get("/state"), bag_now()).state
            worst = min(worst, state["health"])
            if state["health"] <= 0:
                break
            if "intent" not in ENGAGED_INTENT and any(a.get("outcome") == "answered"
                                                        for a in perception.answered_since(mark)):
                ENGAGED_INTENT["intent"] = _combat_intent(dict(state))     # what was on offer once it was engaged
            if cell and not _hostiles(radius=24, kinds={ENEMY.get(cell.get("enemy"))} - {None}):
                _restock(cell)
            time.sleep(TRACE_EVERY_S)
    finally:
        stop.set()
        watcher.join(1.0)
    worst = min([worst] + [s["hp"] for s in trace])
    WINDOW_PROBE["at_end"] = perception_probe(t_mark)
    # answers by time since the window opened (an index into the capped look list can run past its end)
    return answered_by_time(t_mark), worst, round(time.time() - began, 1), trace

def blind_s(looks, seconds):
    """Seconds of the window in which the threat layer could not see: it had no rows, or only stale ones."""
    if not looks:
        return round(float(seconds), 2)
    # quiet is an observation; blind is a tick that could not look
    blind = sum(1 for look in looks if look["outcome"] in ("stale", "unwired", "soft"))
    return round(float(seconds) * blind / len(looks), 2)

def _threat_kinds():
    from ... import threat
    return set(threat.MOBS)

BLIND_SHARE = 0.1      # a cell blind for more of its window than this measured nothing

# long enough to contain a fight (8 s caught a swing or two); noise averages across passes, by the jitter
CELL_SECONDS = 15.0

def _plain(state):
    """The priced state as JSON: the row has to carry what the prediction assumed, and a row is data."""
    if not state:
        return None
    ground = state.get("field")
    return _data(dict(state, field=None if ground is None else {"bucket": ground.bucket, "blocks": ground.blocks}),
                 "state")

def _data(v, where):
    """Pure: `v` as plain JSON data, recursively: sets sorted to lists, tuples to lists, keys to str. Anything else
    (a callable) raises, naming the field — a set in the priced state crashed every sweep row's json.dumps."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {str(k): _data(x, f"{where}.{k}") for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_data(x, f"{where}[{i}]") for i, x in enumerate(v)]
    if isinstance(v, (set, frozenset)):
        items = [_data(x, where) for x in v]
        try:
            return sorted(items)
        except TypeError:
            return sorted(items, key=repr)
    raise TypeError(f"{where}: a {type(v).__name__} is not row data")

def _fought(kinds, seconds):
    """The shared record: price the cell, live in it, report the outcome and the clock's word on the pricing's numbers."""
    def record(cell):
        from ... import api
        from ...world import Inventory, Snapshot
        before = Snapshot.from_readings(api.get("/state"), bag_now())
        intent = _combat_intent(dict(before.state))
        answered, worst, took, trace = _combat_execute(seconds, cell=cell)
        after = Snapshot.from_readings(api.get("/state"), bag_now())
        near = _hostiles(radius=24, kinds=kinds(cell))
        # a window blind too long is not evidence
        dark = blind_s(answered, took)
        priced = intent.get("state") or {}
        missing = missing_columns(cell, intent, ENGAGED_INTENT.get("intent"))
        intent = dict(intent, state=_plain(intent.get("state")), missing_column=missing,
                      carried={k: priced.get(k) for k in ("blocks", "food_items", "shield", "sword")})
        return {"intent": intent, "answered": answered, "trace": trace, "probe": dict(WINDOW_PROBE),
                "blind_s": dark, "invalid": dark > took * BLIND_SHARE,
                "outcome": {"hp": after.state["health"], "hp_before": before.state["health"], "worst_hp": worst,
                            "hp_lost": round(before.state["health"] - after.state["health"], 1),
                            "seconds": took, "moved": round(math.dist(before.pos, after.pos), 1),
                            "left": len(near),
                            "gap": round(min((e["distance"] for e in near), default=0.0), 1),
                            "blocks_spent": before.inv.count("building") - after.inv.count("building")}}
    return record

def _where(row):
    """A row's cell, named by the dimensions it carries (listing keys would copy the dimension table)."""
    return "/".join(f"{k}={row[k]}" for k in DIMS if k in row) or str(row.get("line_up", "?"))

def _answers_are_closed(rows):
    """True of every cell: alive, a held column that went out and did not raise, a no-go zone for the planner."""
    bad = []
    for r in rows:
        where = _where(r)
        held, opts = r["intent"].get("held"), r["intent"].get("options") or {}
        if r["outcome"]["hp"] <= 0:
            bad.append(f"{where}: died")
        if held != "ignore" and not r["answered"]:
            bad.append(f"{where}: held '{held}' and nothing went out")
        if held != "ignore" and opts.get(held, {}).get("saves", 0) <= 0:
            bad.append(f"{where}: held a column that saves nothing")
        missing = r["intent"].get("missing_column") or []
        if missing:
            bad.append(f"{where}: the cell paid for {missing} and no such column was offered "
                       f"(carried {r['intent'].get('carried')})")
        if r["intent"].get("rows") and not r["intent"].get("no_go"):
            bad.append(f"{where}: threats in reach but no no-go circle for the planner")
        looks = r["answered"]
        bad += [f"{where}: {a.get('kind')} failed ({a['failed']})" for a in looks if a.get("failed")]
        bad += [f"{where}: {a.get('kind')} was priced but the body refused it ({a.get('refused')})"
                for a in looks if a["outcome"] == "refused" and a.get("refused") not in ("held",)]
        hurt = r["outcome"]["worst_hp"] < r["outcome"]["hp_before"]
        if hurt and not any(a["outcome"] == "answered" for a in looks):
            silent = sorted({a["outcome"] for a in looks}) or ["never looked"]
            bad.append(f"{where}: took damage and never answered ({', '.join(silent)})")
        if not looks:
            bad.append(f"{where}: the threat layer never looked at the world")
    return bad

def escaped(rows, hp_loss=None):
    """Pure: what an escape cell is judged on — the outcome, not the method: alive at the window's end, and no more
    health lost than RESOLVE_HP_LOSS; the columns on offer stay a readout (missing_column)."""
    hp_loss = RESOLVE_HP_LOSS if hp_loss is None else hp_loss
    bad = []
    for r in rows:
        o = r["outcome"]
        if o["hp"] <= 0:
            bad.append(f"{_where(r)}: died")
        elif o["hp_before"] - o["hp"] > hp_loss:
            bad.append(f"{_where(r)}: lost {o['hp_before'] - o['hp']:.1f} hp (> {hp_loss})")
    return bad


def _escaped(rows):
    return escaped(rows)


def _shapes_fit_the_enemy(rows):
    """What a column is FOR, read off the belief table rather than off an enemy's name."""
    from ... import beliefs, field as _field
    bad = []
    for r in rows:
        where, held = _where(r), r["intent"].get("held")
        kind = ENEMY.get(r.get("enemy"))
        mob = beliefs.mob(kind) if kind in beliefs.MOBS else {}
        if held == "fight" and r.get("weapon") == "fist":
            bad.append(f"{where}: swung with nothing in hand")
        if held == "fight" and mob.get("burst"):
            bad.append(f"{where}: traded health against a blast")
        if held in SHAPE_COLUMNS and mob.get("squeezes"):
            bad.append(f"{where}: shaped the ground against something that walks over it")
        if held in SHAPE_COLUMNS and r.get("ground") == "open" \
                and not _field.Field(bucket="open").blocks_worth_placing():
            bad.append(f"{where}: placed blocks where there is nothing to place them against")
    return bad

def _more_of_them_costs_more(rows):
    """Two rows differing in exactly one dimension: more of them can never cost less to ignore, or tax less."""
    keys = [k for k in DIMS if k != "count"]
    by, bad = {}, []
    for r in rows:
        if "count" in r:
            by[tuple(r.get(k) for k in keys) + (r["count"],)] = r
    for key, one in by.items():
        if key[-1] != "one":
            continue
        three = by.get(key[:-1] + ("three",))
        if not three:
            continue
        owed = lambda row: ((row["intent"].get("options") or {}).get("ignore", {}) or {}).get("leaves")
        a, b = owed(one), owed(three)
        if a is not None and b is not None and b < a - 1e-6:
            bad.append(f"{_where(three)}: three leave less coming than one ({b} < {a})")
        if one["intent"].get("hp_tax_s", 0) > three["intent"].get("hp_tax_s", 0) + 1e-6:
            bad.append(f"{_where(three)}: three taxed the planner less than one")
    return bad

def _hostiles(radius=32, kinds=None):
    from ...world import entities
    kinds = kinds or {ENEMY[name] for _line_up, mobs, _carry_ in WAVES for name, _n in mobs}
    return [e for e in entities(radius) if e["type"] in kinds and e.get("health", 1) > 0]

def _summon(mobs, spread=4, seed=None):
    """Where the enemies appear."""
    out = []
    rng = random.Random(seed)
    for kind, n in mobs:
        turn = rng.random() * 2 * math.pi if seed is not None else 0.0
        for i in range(n):
            angle = turn + 2 * math.pi * i / max(1, n)
            # never closer than the dimension says; jitter only opens the range
            reach = spread * (rng.uniform(1.0, 1.4) if seed is not None else 1.0)
            dx, dz = round(reach * math.cos(angle)), round(reach * math.sin(angle))
            out.append(f"summon {kind} ~{dx} ~ ~{dz}")
    return out

def _siege_build(cell):
    """No reset between waves: the siege is cumulative."""
    return _summon(tuple((ENEMY[name], n) for name, n in {w[0]: w[1] for w in WAVES}[cell["line_up"]]))

def _siege_record(per_wave_s=90.0):
    def record(cell):
        from ... import api as _api
        _api.log(f"=== siege wave {cell['wave']}: {cell['line_up']}")
        row = _fought(lambda _c: None, seconds=per_wave_s)(cell)
        row["cleared"] = row["outcome"]["left"] == 0 and row["outcome"]["hp"] > 0
        _api.log(f"   wave {cell['wave']}: {'cleared' if row['cleared'] else 'NOT cleared'}"
                 f" with {row['outcome']['hp']:.0f} hp")
        return row
    return record

def _wave_cleared(rows):
    return [] if rows and all(r["cleared"] for r in rows) else [f"wave {rows[-1]['wave'] if rows else '?'} not cleared"]

def _siege_detail_of(name):
    rows = SWEEP.get(name) or []
    return (("cleared" if rows and rows[-1]["cleared"] else "not cleared")
            + (f", {rows[-1]['outcome']['hp']:.0f} hp" if rows else ""))

_FIGHT_SETUP = (["gamemode survival @p", "difficulty normal", "time set day", "clear @p"]
                + _platform(reach=ARENA_REACH, walled=True) + ["kill @e[type=!player,type=!item,distance=..48]"])

# every fight carries a water bucket: a knock off a ledge is part of fighting
FIGHT_BUCKET = ["give @p water_bucket"]

# the cell is built in setup, so the exposure starts at setup's end
ESCAPE_SECONDS = 25.0      # the row's limit is 30 s (the user's rule): the window is what is left of it

ESCAPE_WATCH = ESCAPE_SECONDS - 2.0     # setup's end → the run's first look: ~2 s of the window already spent

BEHAVIOUR_SECONDS = 20.0

# the corridor's way in (GROUND["corridor"]): the passage between us and its open end, feet and head
GAP = [at(x, y, 0) for x in range(1, CORRIDOR_END + 1) for y in (0, 1)]
MOUTH = at(CORRIDOR_END, 0, 0)            # where block_gap's walker waits: in the passage's open end

def _last(name):
    rows = SWEEP.get(name) or []
    return rows[-1] if rows else None

def _went_out(row, *kinds):
    return any(a.get("kind") in kinds for a in row["answered"] if a.get("outcome") == "answered")

def _blocked(row):
    """The shield was up at some sample (/state blocking): the jar's reflex raised it — no answer asks for it."""
    return any(s.get("blocking") for s in row["trace"])

def _first_out(row):
    return next((a.get("kind") for a in row["answered"] if a.get("outcome") == "answered"), None)

def _ys(row):
    return [s["pos"][1] for s in row["trace"]] or [row["trace_start_y"]]

def _gap_blocked(api):
    from ...world import Region
    region = Region(tuple(min(c[i] for c in GAP) for i in range(3)), tuple(max(c[i] for c in GAP) for i in range(3)))
    return sum(1 for c in GAP if region.solid(c))

def _walled(row):
    """Cobblestone (or any placed solid) on all four sides of the feet AND the head cell, where the body ended."""
    from ...world import Region
    x, y, z = (math.floor(v) for v in row["trace"][-1]["pos"])
    region = Region((x - 1, y, z - 1), (x + 1, y + 1, z + 1))
    return all(region.solid((x + dx, y + dy, z + dz))
               for dy in (0, 1) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))

def _offhand_shield():
    from ...data import bare
    from ...world import Inventory
    return bare((bag_now().equipment.get("offhand") or {}).get("id", "")) == "shield"

def _less_hurt_than(row, control):
    base = _last(f"combat__{control}")
    return base is not None and row["outcome"]["hp_lost"] < base["outcome"]["hp_lost"]

START_Y = at(0, 0, 0)[1]


def gap_open(solid_cells):
    """Pure: the corridor's gap stands open before the run (none of GAP solid) — else closing it proves nothing."""
    return not any(c in set(solid_cells) for c in GAP)


def _gap_is_open(ctx):
    """`before` hook: block_gap's scene proven — the gap is open (the build ran), else SetupInvalid."""
    from ...world import Region
    from ..core import SetupInvalid
    region = Region(tuple(min(c[i] for c in GAP) for i in range(3)), tuple(max(c[i] for c in GAP) for i in range(3)))
    solid = [c for c in GAP if region.solid(c)]
    if not gap_open(solid):
        raise SetupInvalid(f"block_gap: the corridor's gap is already closed at {solid[:3]}")


# behaviours whose scene is proven by a hook before the run, beyond the build's replies and the line-up count
# block_gap: the walker held still (NoAI) where it was summoned, across the gap, and woken as the window opens —
# left free it wandered 14 → 20 blocks off for 14 s and nothing came at the gap to be closed
# (summoned 10 off at the seed's bearing it stood outside the corridor: it is put in the passage's mouth first)
PROVEN = {"block_gap": {"scene": [("cmd", f"tp @e[type=minecraft:zombie,limit=1,sort=nearest] {MOUTH[0] + 0.5} {MOUTH[1]} "
                                          f"{MOUTH[2] + 0.5}"),
                                  ("cmd", "data merge entity @e[type=minecraft:zombie,limit=1,sort=nearest] {NoAI:1b}")],
                        "before": [("&_gap_is_open",), ("loose", "zombie")]}}

# name: (cell off ARMED, what must hold, why); a control runs before the cell compared to it
BEHAVIOURS = {
    "block_gap": (dict(ground="corridor", kit="blocks", distance="across"),
                  lambda r, api: _went_out(r, "reshape") and _gap_blocked(api) >= 1 and r["outcome"]["gap"] >= 1.5
                  and r["outcome"]["hp_lost"] <= 4,
                  "a corridor with one gap, blocks carried: the gap closed, the walker kept outside it"),
    "pillar": (dict(ground="open", kit="blocks"),
               lambda r, api: _went_out(r, "reshape") and max(_ys(r)) >= START_Y + 2,
               "open ground, blocks: stood two up out of a walker's reach"),
    "shield_arrows": (dict(enemy="archer", kit="shield", distance="across"),
                      lambda r, api: _blocked(r) and _offhand_shield() and r["outcome"]["hp_lost"] <= 4,
                      "an archer across open ground, a shield: raised against the arrows (still in the offhand)"),
    "fight_without_shield": (dict(kit="nothing"),
                             lambda r, api: _went_out(r, "fight") and not _blocked(r)
                             and r["outcome"]["hp_lost"] > 0,
                             "a walker, no shield (control): fought, never blocked, and hurt for it"),
    "fight_and_block": (dict(kit="shield"),
                        lambda r, api: _went_out(r, "fight") and _blocked(r) and r["outcome"]["left"] == 0
                        and _less_hurt_than(r, "fight_without_shield"),
                        "the same walker, sword and shield: struck and blocked in turn, the walker dead, and less "
                        "hurt than the no-shield control"),
    "wall_in": (dict(count="three", kit="blocks", blood="hurt"),
                lambda r, api: _went_out(r, "wall_in") and _walled(r) and r["outcome"]["hp"] > 0,
                "three walkers, hurt, blocks: walled in (feet and head cells closed on four sides)"),
    "surrounded_low": (dict(count="three", blood="hurt", kit="full"),
                       lambda r, api: _first_out(r) in ("evade", "wall_in", "reshape") and not _went_out(r, "fight")
                       and r["outcome"]["hp"] > 0,
                       "three walkers at 8 hp: got away or walled in, never swung; alive"),
}

def _behaviour_check(name, rule):
    """A behaviour's rule over its recorded row; a rule given as [(part, fn)] names each part (check_parts)."""
    named = rule if isinstance(rule, list) else [("rule", rule)]

    def part(word, fn):
        def ask(api, _inv):
            row = _last(name)
            return row is not None and bool(fn(row, api))
        ask.__table__ = (word,)
        return ask
    parts = [part(w, fn) for w, fn in named]

    def check(api, inv):
        return all(p(api, inv) for p in parts)
    check.parts = parts
    return check

def _record_with_start(record):
    def rec(cell):
        from ... import api
        from ...world import Inventory, Snapshot
        y = Snapshot.from_readings(api.get("/state"), bag_now()).state["y"]
        return dict(record(cell), trace_start_y=y)
    return rec

# -- CT3: fights on a walled platform, the whole agent running; judged by the world and the decision rhythm (no bid gap over 1.5 × FIGHT_POLL_S)
FIGHT_LOG: dict = {"bids": []}        # also keeps the real fight_loop.bid
from ... import lifecycle as _lifecycle  # noqa: E402
_lifecycle.in_place(__name__, "FIGHT_LOG", "ENGAGED_INTENT", "WINDOW_PROBE")     # a row's own record

def _record_bids(ctx):
    """`before` hook: time every bid the threat layer makes during this row (the fight's decision clock)."""
    from ... import fight_loop
    FIGHT_LOG["bids"] = []
    real = FIGHT_LOG.setdefault("real_bid", fight_loop.bid)
    def bid(*a, **k):
        # (when, engaged, its kind): the gaps judged are the ones while a fight holds the body, not the walk-in
        want = fight_loop.carrying()          # the answer the running engagement carries out, None when none runs
        FIGHT_LOG["bids"].append((time.time(), fight_loop.engaged() is not None, getattr(want, "kind", None)))
        return real(*a, **k)
    fight_loop.bid = bid

def _fight_until(kinds, seconds, clear=True):
    """Brain rounds without the plan layer (they yield while the fight holds the body; reflexes like eating still
    run) until the line-up is gone, or `seconds`."""
    def run(ctx):
        from ... import fight_loop
        t0 = time.time()
        FIGHT_LOG["alive"] = []
        stop = _threading.Event()

        def sample():
            # the kill's evidence at the trace's cadence, off the round loop: sampled once a round (~0.85 s), the
            # last reading of a mob could be a second before it died and blocks off (kills_while_engaged)
            while not stop.is_set():
                try:
                    alive = _hostiles(24, set(kinds))
                    FIGHT_LOG["alive"].append((time.time(), [(e.get("id"), float(e.get("health", 0)),
                                                              round(float(e.get("distance", 99)), 2)) for e in alive]))
                except McError:
                    pass
                stop.wait(TRACE_EVERY_S)
        sampler = _threading.Thread(target=sample, daemon=True, name="fight-samples")
        sampler.start()
        try:
            while time.time() - t0 < seconds:
                if clear and not _hostiles(24, set(kinds)):
                    return True
                core.BRAIN.round(plan=False)     # a fight row runs no plan work: reflexes (eat) and the fight only
                time.sleep(TRACE_EVERY_S)        # a plan-less round with nothing to do returns at once: no hot loop
            return not clear or not _hostiles(24, set(kinds))
        finally:
            stop.set()
            sampler.join(1.0)
            fight_loop.bid = FIGHT_LOG.get("real_bid", fight_loop.bid)
    return run

def engaged_gaps(bids):
    """Pure: the seconds between consecutive bids made while engaged (`bids`: [(when, engaged)]) — the fight's own
    decision rhythm; the gaps before it engaged (the walk in, HTTP reads) are not the fight's."""
    return [b[0] - a[0] for a, b in zip(bids, bids[1:]) if a[1] and b[1]]


KILL_REACH = 5.0       # a mob the body killed was last seen within this (melee reach and a step)


def kills_while_engaged(samples, bids=()):
    """Pure: the mobs the fight is proven to have killed — `samples` [(when, [(id, hp, distance)])] in order, `bids`
    the threat layer's [(when, engaged, kind)]. A mob counts when it went between two samples, was last seen hurt
    (hp below the first reading of it) and within KILL_REACH, and the fight was engaged at a bid in between. A mob
    that vanished at full health, far off, or while nothing fought is no kill (fight_skeleton_1 20260928-225429:
    'killed' 10 blocks off in 1.3 s)."""
    first, kills = {}, 0
    for a, b in zip(samples, samples[1:]):
        for mid, hp, _d in a[1]:
            first[mid] = max(first.get(mid, hp), hp)      # its most health seen: one short reading is no baseline
        gone = {m for m, _h, _d in a[1]} - {m for m, _h, _d in b[1]}
        fought = any(e and a[0] <= t <= b[0] for t, e, *_k in bids)
        for mid, hp, dist in a[1]:
            if mid in gone and fought and hp < first[mid] and dist <= KILL_REACH:
                kills += 1
    return kills


def _stall_now():
    """The running row's longest stall — over FIGHT_LOG's own trace when one is recorded there, else the runner's."""
    from ..runner import TRACE_NOW
    trace = FIGHT_LOG["trace"] if "trace" in FIGHT_LOG else TRACE_NOW
    return longest_stall(trace, FIGHT_LOG.get("bids", []), FIGHT_LOG.get("alive", []))


def last_seen(samples):
    """Pure: each mob that went, as last read — {id: [hp, distance, most hp seen]}: why a kill was or was not credited."""
    most, last, out = {}, {}, {}
    for a, b in zip(samples, samples[1:]):
        for mid, hp, dist in a[1]:
            most[mid] = max(most.get(mid, hp), hp)
            last[mid] = [hp, dist]
        for mid in {m for m, _h, _d in a[1]} - {m for m, _h, _d in b[1]}:
            out[mid] = last[mid] + [most[mid]]
    return out


def fight_readout():
    """What the fight rows' checks read, as numbers (a failed row's report names them): the fight's kills, the
    engaged bids and their widest gap, the answer kinds carried, the shield in the offhand, the mobs left."""
    bids = FIGHT_LOG.get("bids", [])
    gaps = engaged_gaps(bids)
    alive = FIGHT_LOG.get("alive", [])
    return {"kills_while_engaged": kills_while_engaged(alive, bids), "bids": len(bids),
            "engaged_bids": sum(1 for b in bids if b[1]), "engaged_gap_max": round(max(gaps, default=0.0), 3),
            "kinds_carried": sorted({b[2] for b in bids if b[2]}), "alive_samples": alive[-3:],
            "last_seen": last_seen(alive),
            "longest_stall": _stall_now(),
            "shield_kept": _offhand_shield()}


IN_REACH = 3.5          # blocks: a mob this close is one the body should be acting on, never standing idle beside
STALL_OK = 0.25         # seconds with no running task while engaged beside a mob: one trace sample, no more


def longest_stall(trace, bids, alive, reach=IN_REACH):
    """Pure: the longest time the body had NO running task while the fight was engaged with a mob in reach —
    `trace` [{t, task}] (5 Hz), `bids` [(when, engaged, kind)], `alive` [(when, [(id, hp, distance)])]. A decision
    gap may exist; the action must not wait on it (an attack ending, then 0.4 s standing until the next post)."""
    def last(rows, t):
        prev = None
        for r in rows:
            if r[0] > t:
                break
            prev = r
        return prev
    worst, since = 0.0, None
    for s in trace:
        t = s.get("t")
        if t is None:
            continue
        bid, seen = last(bids, t), last(alive, t)
        stalled = (s.get("task") is None and bid is not None and bid[1] and seen is not None
                   and any(d <= reach for _m, _h, d in seen[1]))
        if stalled:
            since = t if since is None else since
            worst = max(worst, t - since)
        else:
            since = None
    return round(worst, 2)


def _no_stall(limit=STALL_OK):
    """Check: while engaged beside a mob the body always had a task running (longest_stall ≤ `limit`)."""
    def check(api, inv):
        return _stall_now() <= limit
    return check


def _kills_by_the_fight(n):
    """Check: `n` mobs went while the fight was engaged (the fight's own kills, not a burn or the plan's swing)."""
    return lambda api, inv: kills_while_engaged(FIGHT_LOG.get("alive", []), FIGHT_LOG.get("bids", [])) >= n


def _answered_with(*kinds):
    """Check: the fight carried out one of `kinds` at some bid."""
    return lambda api, inv: any(k in kinds for _t, _e, k in FIGHT_LOG["bids"])


def _loose(mob):
    """`before` hook: the `mob` summoned still (NoAI) wakes now — the scene counted it, and the setup before this
    (a food drain) ran with nothing hitting the body."""
    def hook(ctx):
        _chat(f"data merge entity @e[type=minecraft:{mob},limit=1,sort=nearest] {{NoAI:0b}}")
    return hook


def kept_off(gaps, enclosed, gap=3.0):
    """Pure: kept off — every hostile at least `gap` away (`gaps`: their distances), or the body walled in."""
    return enclosed or all(g >= gap for g in gaps)


def _away_or_walled(kinds, gap=3.0):
    """Check: at the end every `kinds` is `gap` or more off, or the body is walled in (world.is_enclosed)."""
    def check(api, inv):
        from ...world import Region, is_enclosed
        s = api.get("/state")
        x, y, z = s["blockX"], s["blockY"], s["blockZ"]
        gaps = [math.dist((x, y, z), (e["x"], e["y"], e["z"])) for e in _hostiles(24, set(kinds))]
        return kept_off(gaps, is_enclosed(Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1)), (x, y, z)),
                              gap)
    return check


def _shield_kept():
    """Check: the shield is still in the offhand at the end (the bag the check is handed)."""
    from ...data import bare
    return lambda api, inv: bare((inv.equipment.get("offhand") or {}).get("id", "") or "") == "shield"


def _decision_gaps_ok(factor=1.5):
    def check(api, inv):
        from ... import fight_loop
        bids = FIGHT_LOG["bids"]
        return any(b[1] for b in bids) and \
            max(engaged_gaps(bids), default=0.0) <= fight_loop.FIGHT_POLL_S * factor
    return check

def _gone(kinds):
    return lambda api, inv: not _hostiles(24, set(kinds))

def angers(lines):
    """Pure: each enderman's anger_end_time (1.21.11 Angerable writes this long, not AngerTime) from
    '/data get entity @s anger_end_time' replies ('… entity data: 2506145L'); a calm one has none."""
    return [int(m.group(1)) for line in lines for m in [re.search(r"entity data: (-?\d+)L?", line)] if m]


def game_time(lines):
    """Pure: the world's game time from '/time query gametime' ('The time is 2519838'), or None."""
    for line in lines:
        m = re.search(r"The time is (\d+)", line)
        if m:
            return int(m.group(1))
    return None


def provoked(ends, now):
    """Pure: the endermen still angry — an anger_end_time past `now`."""
    return [e for e in ends if now is None or e > now]


ENDERMEN: dict = {}     # the last calm check's reading: anger ends, the time, places (a failed row's readout)
_lifecycle.in_place(__name__, "ENDERMEN")

def positions(lines):
    """Pure: [x, y, z] per '/data get entity @s Pos' reply ('… entity data: [1.5d, 64.0d, 2.5d]')."""
    out = []
    for line in lines:
        m = re.search(r"entity data: \[([-0-9.]+)d, ([-0-9.]+)d, ([-0-9.]+)d\]", line)
        if m:
            out.append([round(float(v), 1) for v in m.groups()])
    return out


def _endermen_calm():
    """Check (the server): no enderman's anger_end_time is past the world's time — none provoked (a calm one has no
    such tag); each one's anger end, the time and places kept for the readout."""
    def check(api, inv):
        from ..core import _command
        sel = "execute as @e[type=minecraft:enderman,distance=..48] run data get entity @s"
        ends = angers(_command(f"{sel} anger_end_time", []))
        now = game_time(_command("time query gametime", []))
        pos = positions(_command(f"{sel} Pos", []))
        ENDERMEN.clear()
        ENDERMEN.update(anger_end=ends, now=now, pos=pos, provoked=provoked(ends, now))
        return bool(pos) and not provoked(ends, now)
    return check


def covered_in_time(trace, cells, within_s):
    """Pure: the body stood in one of `cells` within `within_s` of the trace's start, and lost no health after."""
    if not trace:
        return False
    t0 = trace[0]["t"]
    cells = {tuple(c) for c in cells}
    for i, s in enumerate(trace):
        if s.get("x") is None:
            continue
        if (math.floor(s["x"]), math.floor(s["y"]), math.floor(s["z"])) in cells and s["t"] - t0 <= within_s:
            hp = s.get("health") or 0.0
            return all((later.get("health") or 0.0) >= hp for later in trace[i:] if later.get("health") is not None)
    return False


def endermen_off_path(start, end, n):
    """Builder: `n` endermen spread along start → end, alternating sides, each far enough off the line that a body
    walking it doesn't bump one (half its width + ours, then a block)."""
    from ...field import PLAYER_HALF, TALL_WIDTH
    side = math.ceil(TALL_WIDTH / 2 + PLAYER_HALF) + 1
    out = []
    for i in range(n):
        f = (i + 1) / (n + 1)
        x = start[0] + (end[0] - start[0]) * f
        z = start[2] + side * (1 if i % 2 == 0 else -1)
        out.append(f"summon minecraft:enderman {int(math.floor(x)) + 0.5} {start[1]} {int(z) + 0.5} "
                   "{PersistenceRequired:1b}")
    return out


def alcove(x0, depth):
    """Builder: a 1-wide alcove `depth` deep from x0 (z 0), roofed 2 over the feet, walled round."""
    return [f"fill {_c(at(x0, 0, -1))} {_c(at(x0 + depth, 2, 1))} stone",
            f"fill {_c(at(x0, 0, 0))} {_c(at(x0 + depth - 1, 1, 0))} air"]


def alcove_cover(x0, depth):
    """Pure: the alcove's cells a tall mob at its mouth can't reach (field.reached_from)."""
    from ...field import reached_from
    return [at(x0 + k, 0, 0) for k in range(depth) if not reached_from((k + 1, 0))]


def _took_cover_alcove(x0, depth, within_s):
    """Check: under the alcove's out-of-reach cells within `within_s`, no hit after."""
    return _took_cover(alcove_cover(x0, depth), within_s)


def trapped_room(half):
    """Builder: a closed room `half` blocks out each way, 2 high (no pillar fits), a dirt floor 3 deep (diggable),
    stone walls and roof: nowhere to outrun a walker."""
    return [f"fill {_c(at(-half - 1, -4, -half - 1))} {_c(at(half + 1, 2, half + 1))} stone",
            f"fill {_c(at(-half, -3, -half))} {_c(at(half, -1, half))} dirt",
            f"fill {_c(at(-half, 0, -half))} {_c(at(half, 1, half))} air"]


def _kept_health():
    """Check: alive, and no more health lost since the row's start than RESOLVE_HP_LOSS (whatever the bot chose)."""
    def check(api, inv):
        s = api.get("/state")
        return not s["dead"] and s["health"] >= BASE["state"]["health"] - RESOLVE_HP_LOSS
    return check


def _took_cover(cells, within_s):
    """Check: under the cover (`cells`) within `within_s`, no hit after (the runner's own trace)."""
    def check(api, inv):
        from ..runner import TRACE_NOW
        return covered_in_time(TRACE_NOW, [tuple(c) for c in cells], within_s)
    return check

def _hp_kept(least):
    return lambda api, inv: api.get("/state")["health"] >= least and not api.get("/state")["dead"]

def kill_stat(kind):
    """Pure: the scoreboard objective that holds the server's kill statistic for `kind` (minecraft:zombie → bk_zombie)."""
    return "bk_" + kind.split(":")[-1]

def kill_stat_scene(kind):
    """Scene: the kill statistic for `kind` as an objective, zeroed for this row (adding one that exists only says so)."""
    obj = kill_stat(kind)
    return [("cmd", f"scoreboard objectives add {obj} minecraft.killed:{kind.replace(':', '.')}"), ("cmd", f"scoreboard players set @p {obj} 0")]

def stat_count(lines):
    """Pure: N from '/scoreboard players get' feedback ('<player> has N [obj]'); 0 when no score is set."""
    for line in lines:
        m = re.search(r"has (\d+) \[", line)
        if m:
            return int(m.group(1))
    return 0

def _killed(kinds, n):
    """Check: the server credits the player with at least `n` kills of `kinds` (its kill statistic, zeroed by the
    scene: kill_stat_scene) — a mob that only left sight, burned or fell is no kill (gone is not killed)."""
    def check(api, inv):
        from ..core import _command
        return sum(stat_count(_command(f"scoreboard players get @p {kill_stat(k)}", [])) for k in kinds) >= n
    return check

# glass walls (visible), a stone roof: undead under the sky burned before the row began
_ARENA = [f"fill {_c(at(-9, -2, -9))} {_c(at(9, -1, 9))} stone", f"fill {_c(at(-9, 0, -9))} {_c(at(9, 4, 9))} glass hollow",
          f"fill {_c(at(-9, 4, -9))} {_c(at(9, 4, 9))} stone",
          f"fill {_c(at(-8, 0, -8))} {_c(at(8, 3, 8))} air", f"fill {_c(at(-9, -1, -9))} {_c(at(9, -1, 9))} stone",
          *[f"setblock {_c(at(x, 0, z))} torch" for x in (-8, 8) for z in (-8, 0, 8)],     # light to watch by
          _tp(),
          "give @p iron_sword", "give @p stone_pickaxe",       # a pickaxe: upkeep's "no pickaxe" row stays quiet
          "item replace entity @p armor.chest with iron_chestplate",
          "item replace entity @p armor.head with iron_helmet", "give @p cooked_beef 16", "give @p cobblestone 64",
          "item replace entity @p weapon.offhand with shield"]

# the _ARENA as built: its floor and roof stone, its four glass walls (4 high), and the kit it gives
ARENA_EXPECT = [(("@", -9, -1, -9), ("@", 9, -1, 9), "stone", 361, 361),
                (("@", -9, 4, -9), ("@", 9, 4, 9), "stone", 361, 361),
                (("@", -9, 0, -9), ("@", 9, 3, 9), "glass", 288, 288)]
ARENA_GEAR = {"items": [["minecraft:iron_sword", 1]], "offhand": "minecraft:shield"}

# (name, mob, count, tier, seconds, health kept at least, cleared?) — cleared False: a neutral mob left alone
RESOLVE_GAP, RESOLVE_HOLD_S, RESOLVE_HP_LOSS = 6.0, 5.0, 4.0

def _threat_resolved(kinds, gap=RESOLVE_GAP, hold_s=RESOLVE_HOLD_S, hp_loss=RESOLVE_HP_LOSS):
    """The threat is over: every `kinds` dead or `gap` off and not closing for `hold_s`, health within `hp_loss`."""
    def check(api, inv):
        from ...world import feet
        start_hp = BASE["state"]["health"]

        def gaps():
            here = feet()
            return [math.dist(here, (e["x"], e["y"], e["z"])) for e in _hostiles(32, set(kinds))]
        first = gaps()
        last = first
        if first:
            time.sleep(hold_s)
            last = gaps()
        kills = _killed(kinds, 1)(api, inv) and 1 or 0
        return resolved(start_hp, api.get("/state")["health"], first, last, kills, gap, hp_loss)
    return check

def resolved(start_hp, hp_now, first, last, kills, gap=RESOLVE_GAP, hp_loss=RESOLVE_HP_LOSS):
    """Pure: the threat is over — health within `hp_loss`, and every mob either killed by us (the server's count:
    one that is merely gone may have blown up — an exploded creeper is no answer) or `gap` off and not closing."""
    if hp_now < start_hp - hp_loss:
        return False
    if not first:
        return kills >= 1
    return (not last and kills >= 1) or (bool(last) and min(first) >= gap and min(last) >= gap
                                         and min(last) >= min(first) - 1.0)

# tidying in the Nether with lava on one side: junk is thrown the other way
NETHER_LAVA = [f"fill {_c(at(1, 0, -1))} {_c(at(4, 0, 1))} lava"]

# -- the fight rows: a cell of bench_combat's dimensions, recorded by a sweep and judged by rules --------------------
FIGHT_EXPECT = [(("@", -9, -1, -9), ("@", 12, -1, 9), "stone", 418, 418)]

RULES = ("&_answers_are_closed",), ("&_shapes_fit_the_enemy",), ("&_more_of_them_costs_more",), ("&_wave_cleared",)

def _fight_row(name, doc, scene, cells, build, record, jsonl, settle, rules, **more):
    """A swept fight row: its cells built and fought one by one, each recorded, the rules judged over the rows."""
    return {"name": name, "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:overworld",
            "doc": doc, "scene": scene, "run": ("bonobo.bench.core:_sweep", name, ("!iter", cells), build, record,
                                                ("$data", jsonl), settle),
            "check": [("bonobo.bench.core:_sweep_check", name, ("$data", jsonl), list(rules), 1)] if rules else [],
            "budget": limit(), **more}

def _fought_for(seconds):
    return ("!_fought", ("&_kinds_of",), seconds)

def arena_row(name, enemy, ground, kit, blood):
    """combat_arena: one cell of (enemy, ground) × (kit, blood) per row."""
    cell = dict(ARMED, enemy=enemy, ground=ground, kit=kit, blood=blood, run=0)
    return _fight_row(name, f"combat_arena shard {enemy}/{ground}/{kit}/{blood}: each cell writes the whole decision "
                            "into bench/combat.jsonl; the rules are relations between rows.",
                      [("sheet", "_FIGHT_SETUP")], [cell], ("&_build",), _fought_for(CELL_SECONDS),
                      "bench/combat.jsonl", 0.6, RULES[:3], module="threat", sweep=True,
                      variant=[sorted(cell.items())], expect=FIGHT_EXPECT, tick_rate=60)

def siege_row(name, wave):
    """The siege, one wave per row: from the wave's carry, cleared alive."""
    line_up, _, left = WAVES[wave - 1]
    return _fight_row(name, f"Siege wave {wave} of {len(WAVES)} ({line_up}), sword, pickaxe, full iron, shield, food "
                            "and blocks: every answer the model offers is available → the wave cleared alive.",
                      [("sheet", "_FIGHT_SETUP"), ("cmd", "effect give @p minecraft:instant_health 3 10 true"),
                       ("built", "_siege_kit"), ("built", "_carry") + tuple(left)],
                      [{"wave": wave, "line_up": line_up}], ("&_siege_build",), ("!_siege_record", 24.0),
                      "bench/siege.jsonl", 0.6, (RULES[0], RULES[3]), detail=("siege_detail", name))

def escape_row(name, enemy, ground, kit, seed=None):
    """No weapon, no armour, one enemy: away by any answer but swinging; no seed given, one is drawn (as at import)."""
    seed = random.randrange(1 << 30) if seed is None else seed
    cell = dict(UNARMED, enemy=enemy, ground=ground, kit=kit, run=0, seed=seed)
    kind = ENEMY[enemy]
    # the enemy held still (NoAI) where it was summoned and woken as the window opens: free, a walker 11 off reached
    # the body during setup and the window began under its hits (escape__walker_open_blocks: 20 → 17 hp at 0.2 s)
    hold = [("cmd", f"data merge entity @e[type={kind},limit=1,sort=nearest] {{NoAI:1b}}")] if kind else []
    wake = {"before": [("loose", kind.split(":")[-1])]} if kind else {}
    return _fight_row(name, f"No weapon, no armour, {enemy} on {ground} ground with {kit}, {ESCAPE_SECONDS:.0f} s: the "
                            "answer has to come from somewhere other than swinging — back off, block the way, dig "
                            "down, eat, or leave a teleporter alone (bench/escape.jsonl).",
                      [("cmd", "gamemode survival @p"), ("cmd", "kill @e[type=!player,type=!item,distance=..48]"),
                       ("built", "_build", cell)] + hold, [cell], ("!constant", []), _fought_for(ESCAPE_WATCH),
                      "bench/escape.jsonl", 0.0, (("&_escaped",),), sweep=True, expect=FIGHT_EXPECT,
                      detail=("escape_detail", name), tick_rate=60, **wake)

def behaviour_row(name, behaviour):
    """One fight behaviour: a cell moved off ARMED so that one answer is worth the most; chosen and working. The cell
    is built in setup (each command's reply checked), so the run is the fight's window alone (≤ 25 s)."""
    moved, _rule, why = BEHAVIOURS[behaviour]
    cell: dict[str, Any] = dict(dict(ARMED, **moved), run=0, seed=0)
    kind = ENEMY[cell["enemy"]]
    proof = PROVEN.get(behaviour, {})
    row = _fight_row(name, f"Fight behaviour: {why}",
                     [("sheet", "_FIGHT_SETUP"), ("built", "_build", cell)] + list(proof.get("scene", ())), [cell],
                     ("!constant", []), ("!_record_with_start", _fought_for(BEHAVIOUR_SECONDS)),
                     "bench/behaviour.jsonl", 0.6, (), stochastic=True, variant=sorted(cell.items()),
                     expect=FIGHT_EXPECT, tick_rate=60,
                     **({"expect_entities": [(kind, COUNT[cell["count"]], COUNT[cell["count"]])]} if kind else {}),
                     **({"before": proof["before"]} if "before" in proof else {}))
    return dict(row, check=[("behaviour", behaviour)])

def fight_cell_row(name, mob, n, tier, secs, hp, clear):
    """A walled arena, the iron kit, `n` of one mob: all dead (or kept off, or left alone when neutral)."""
    kinds = [f"minecraft:{mob}"]
    # an archer 10 blocks off (7, 7: 9.9) — at 4 it was a melee fight; a creeper 7; the rest 4-5 off
    spots = ([(7, 0, 0)] if mob == "creeper" else [(7, 0, 7)] if mob == "skeleton"
             else [(4, 0, 0), (-3, 0, 3), (1, 0, -4)])[:n]
    nbt = "{PersistenceRequired:1b,Health:10f}" if mob == "blaze" and n > 1 else "{PersistenceRequired:1b}"
    verdict = {True: "all dead", False: "left alone (neutral)",
               "resolved": f"dead, or kept off and not following for {RESOLVE_HOLD_S:.0f} s"}[clear]
    # judged by the world only: health, the mobs gone and the server's kill count; the fight's own record (its
    # kills, stalls, answers carried, decision rhythm) is a readout (fight_readout), never the pass (bench.judged)
    check = ([("threat_resolved", kinds)] if clear == "resolved"
             else [("hp_kept", hp), ("gone", kinds), ("killed", kinds, n)] if clear
             else [("hp_kept", hp), ("call", "hostiles", [24, ("$set", kinds)])])
    if mob == "skeleton":
        check += [("shield_kept",)]           # arrows: the shield still in the offhand
    return _row(name, f"Walled platform, iron kit: {n} {mob} → {verdict}, health ≥ {hp}"
                      + (f", {n} kills credited by the server" if clear is True else ""), "fight_loop",
                [("sheet", "_ARENA")] + [("summon", mob, ("@", x, y, z), nbt) for x, y, z in spots]
                + (kill_stat_scene(f"minecraft:{mob}") if clear in (True, "resolved") else []),
                ("fight_until", kinds, secs - RESOLVE_HOLD_S - 2 if clear == "resolved" else secs - 2)
                + (() if clear is not False else (False,)), check, point="B", budget=min(secs + 5, limit()),
                before=[("&record_bids",)], combat=True, skills=[], tier_fixed=tier,
                tags={"base": "fight", "enemy": mob, "count": n},
                # the scene proven before the run: exactly this line-up, the arena standing, the kit in hand
                expect_entities=[(f"minecraft:{mob}", n, n)], expect=ARENA_EXPECT, expect_gear=ARENA_GEAR)

def siege_detail(name):
    return lambda inv: _siege_detail_of(name)

def escape_detail(name):
    return lambda inv: "; ".join(f"{r['enemy']}: {r['outcome']['hp']:.0f} hp, gap {r['outcome']['gap']}"
                                 for r in (SWEEP.get(name) or []))

def behaviour(name):
    """The behaviour's own rule over its recorded row (BEHAVIOURS)."""
    return _behaviour_check(f"combat__{name}", BEHAVIOURS[name][1])

WORDS.update(siege_detail=siege_detail, escape_detail=escape_detail, behaviour=behaviour)

from ..bench_combat import (ARMED, ARMOUR, BLOOD, COUNT, DIMS, DISTANCE, ENEMY, GROUND, KIT, NEEDS,  # noqa: E402
                           UNARMED, WAVES, WEAPON)     # combat's dimensions, its data; last: its rows use these words

# -- the deflect row: fireballs straight down a 1-wide corridor at the player, one after another; the reflex punches each
DEFLECT = {}            # the running volley: start hp, fired, each shot's outcome (deflected?) in shot order, done
EYE_Y = 1.62
FIREBALL = "minecraft:fireball"
FIREBALL_SPEED = 0.1    # a ghast's shot leaves at acceleration_power along its aim
GHAST_HP = 10.0         # a ghast's max health
SHOTS = 2               # fired one after another: the next while the last one resolves
SHOT_DIST = 12          # blocks down the corridor a shot starts from
CORRIDOR = (1, 0, 0)    # the corridor runs from the player along this
CORRIDOR_LEN = SHOT_DIST + 2    # past the shot's start: it spawns inside the corridor
VOLLEY_WATCH_S = 20     # longest the volley is tracked


def data_health(lines):
    """Pure: the Health from '/data get entity … Health' feedback ('… entity data: 7.5f'), or None (no entity)."""
    for line in lines:
        m = re.search(r"entity data: ([0-9.]+)f?", line)
        if m:
            return float(m.group(1))
    return None


def deflect_eye():
    o = at(0, 0, 0)
    return (o[0] + 0.5, o[1] + EYE_Y, o[2] + 0.5)


def corridor(length=CORRIDOR_LEN):
    """Pure: the commands for a 1-wide, 3-high stone corridor from the player along CORRIDOR, closed behind him."""
    dx, _dy, dz = CORRIDOR
    side = (dz, 0, dx)          # across the corridor
    def cell(i, up, s):
        return at(dx * i + side[0] * s, up, dz * i + side[2] * s)
    out = [f"fill {_c(cell(-2, -2, -2))} {_c(cell(length + 1, 5, 2))} air",
           f"fill {_c(cell(-1, -1, -1))} {_c(cell(length, -1, 1))} stone",       # floor
           f"fill {_c(cell(-1, 3, -1))} {_c(cell(length, 3, 1))} stone"]         # roof
    for s in (-1, 1):
        out.append(f"fill {_c(cell(-1, 0, s))} {_c(cell(length, 2, s))} stone")  # the two walls
    out += [f"fill {_c(cell(-1, 0, 0))} {_c(cell(-1, 2, 0))} stone",              # closed behind
            f"tp @p {_c(at(0, 0, 0))}", "kill @e[type=!player,distance=..40]"]
    return out


def shot_at(dist=SHOT_DIST):
    """Pure: (fireball centre, its motion) for a shot `dist` down the corridor, aimed back at the eye."""
    n = math.sqrt(sum(c_ * c_ for c_ in CORRIDOR))
    u = tuple(c_ / n for c_ in CORRIDOR)
    eye = deflect_eye()
    ball = tuple(eye[i] + u[i] * dist for i in range(3))
    return ball, tuple(-c_ * FIREBALL_SPEED for c_ in u)


def heading(pos, vel, eye):
    """Pure: >0 moving away from the eye, <0 toward it (velocity · offset from the eye)."""
    return sum(vel[i] * (pos[i] - eye[i]) for i in range(3))


def fireball_end(reads, gone):
    """Pure: (resolved, deflected) of one fireball from its (pos, velocity, the body's eye then) reads: its velocity
    turned away from the body after coming at it = deflected; gone (exploded) without turning = not."""
    signs = [heading(p, v, eye) for p, v, eye in reads]
    came = next((i for i, h in enumerate(signs) if h < 0), None)
    if came is not None and any(h > 0 for h in signs[came + 1:]):
        return True, True
    if gone and reads:
        return True, False
    return False, None


def tag_shots(tags, seen, fired):
    """Pure: each fireball id tagged with its shot index in the order first seen, never past the shots fired."""
    out = dict(tags)
    for i in seen:
        if i not in out and len(out) < fired:
            out[i] = len(out)
    return out


def next_shot_due(fired, closed):
    """Pure: the next shot goes out while the last one resolves (its end read), never before the first."""
    return fired == 0 or (fired < SHOTS and (fired - 1) in closed)


def volley_done(fired, closed):
    """Pure: every shot fired and each one's end read (a shot fired but not yet seen is not done)."""
    return fired == SHOTS and closed >= fired


def volley_verdict(start_hp, end_hp, fired, ends):
    """Pure: every shot fired and each one turned away from the body (ends: per shot, in shot order), unhurt."""
    if start_hp is None or end_hp is None or end_hp < start_hp:
        return False
    return fired == SHOTS and len(ends) == SHOTS and all(e is True for e in ends)


def _server_hp():
    from ..core import _command
    return data_health(_command("data get entity @p Health", []))


def _deflect_volley(ctx):
    """`before` hook: the shots down the corridor, the next while the last resolves, each tracked to its end from
    the body's position at each read (idle too: no shot waits for a deflect)."""
    from ..core import _command
    from ... import api
    from ...world import entities
    from ...perception import read_combat
    DEFLECT.clear()
    DEFLECT.update(start=_server_hp(), fired=0, ends=[], done=False)

    def fire():
        ball, m = shot_at()
        _command(f"summon {FIREBALL} {ball[0]:.3f} {ball[1] - 0.5:.3f} {ball[2]:.3f} "
                 f"{{Motion:[{m[0]:.4f}d,{m[1]:.4f}d,{m[2]:.4f}d],acceleration_power:0.1d,ExplosionPower:1b}}", [])
        DEFLECT["fired"] += 1

    def volley():
        t0, series, tags, closed = time.time(), {}, {}, {}
        while time.time() - t0 < VOLLEY_WATCH_S and not DEFLECT["done"]:
            if next_shot_due(DEFLECT["fired"], closed):
                fire()
            try:
                s = api.get("/state")
                eye = (s["x"], s["y"] + EYE_Y, s["z"])
                # the one reader: vel (blocks/s) from the jar's velocity
                near = {e["id"]: ((e["x"], e["y"] + 0.5, e["z"]), e["vel"])
                        for e in read_combat(entities(64, [FIREBALL])) if "vel" in e}
            except McError:
                time.sleep(0.05)
                continue
            tags = tag_shots(tags, near, DEFLECT["fired"])
            for i, (p, v) in near.items():
                series.setdefault(i, []).append((p, v, eye))
            for i, reads in series.items():
                k = tags.get(i)
                if k is None or k in closed:
                    continue
                done, turned = fireball_end(reads, i not in near)
                if done:
                    closed[k] = turned
            DEFLECT["ends"] = [closed.get(k) for k in range(DEFLECT["fired"])]
            DEFLECT["done"] = volley_done(DEFLECT["fired"], len(closed))
            time.sleep(0.05)
        DEFLECT["done"] = True
    _threading.Thread(target=volley, daemon=True, name="deflect-volley").start()


def _deflect_watch():
    """The reflex policy on, the agent driving, the whole stack live (the threat layer must hold, the jar deflects);
    ends as soon as the last fireball resolves (≤ VOLLEY_WATCH_S)."""
    def run(ctx):
        from ... import api
        api.post("/reflex", {"deflect": True, "shield": True, "counter": False})
        api.post("/takeover", {})           # the reflex runs only while the agent drives
        t0 = time.time()
        while not DEFLECT.get("done") and time.time() - t0 < VOLLEY_WATCH_S:
            time.sleep(0.05)
        time.sleep(0.5)                      # the last blast lands
        return True
    return run


def _deflected():
    """Check (the server's health read): every shot turned away from the body, the body unhurt (volley_verdict)."""
    def check(api_, inv):
        from ..runner import CHECK_READOUT
        hp = _server_hp()
        CHECK_READOUT["deflect"] = {"start_hp": DEFLECT.get("start"), "end_hp": hp, "fired": DEFLECT.get("fired", 0),
                                    "shots": [{"shot": k, "deflected": e}
                                              for k, e in enumerate(DEFLECT.get("ends", []))]}
        return volley_verdict(DEFLECT.get("start"), hp, DEFLECT.get("fired", 0), DEFLECT.get("ends", []))
    return check


def deflect_row(name):
    """A 1-wide corridor, a sword: SHOTS fireballs straight down it at the player, the next while the last resolves
    → each punched back, unhurt. The normal state: the whole stack answers."""
    scene = [("cmd", c) for c in corridor()] + [
        ("stand",), ("cmd", "clear @p"), ("cmd", "effect clear @p"), ("give", "diamond_sword")]
    return _row(name, f"{SHOTS} fireballs down a 1-wide corridor from {SHOT_DIST} out, the next while the last "
                      "resolves, a sword: each punched back by the reflex, unhurt", "fight_loop",
                scene, ("deflect_watch",), [("deflected",)], budget=25, before=[("&deflect_volley",)], combat=True,
                tier_fixed="exception", tags={"base": "deflect"})


from ... import lifecycle as _deflect_lifecycle  # noqa: E402
_deflect_lifecycle.in_place(__name__, "DEFLECT")     # a row's own record


TEMPLATES = {t: globals()[f"{t}_row"] for t in ("arena", "siege", "escape", "behaviour", "fight_cell", "deflect")}
NAMES = {"arena": lambda i, *cell: f"combat_arena__{i}", "siege": lambda w: f"siege__w{w}",
         "escape": lambda enemy, ground, kit, seed=None: f"escape__{enemy}_{ground}_{kit}",
         "behaviour": lambda b: f"combat__{b}", "fight_cell": lambda name, *p: name,
         "deflect": lambda name, *p: name}

__all__ = ['IN_REACH', 'STALL_OK', 'longest_stall', '_no_stall', '_stall_now', 'PROVEN', '_gap_is_open', 'gap_open', '_loose', '_away_or_walled', 'kept_off', 'ARENA_EXPECT', 'ARENA_GEAR', '_answered_with', '_kills_by_the_fight', '_shield_kept', 'engaged_gaps', 'kills_while_engaged', 'last_seen', 'ARENA_REACH', 'ARMED', 'ARMOUR', 'BEHAVIOURS', 'BEHAVIOUR_SECONDS', 'BLIND_SHARE', 'BLOOD', 'CELL_SECONDS', 'COUNT', 'DIMS', 'DISTANCE', 'ENEMY', 'ESCAPE_SECONDS', 'ESCAPE_WATCH', 'FIGHT_BUCKET', 'FIGHT_EXPECT', 'FIGHT_LOG', 'GAP', 'MOUTH', 'GROUND', 'KIT', 'NEEDS', 'NETHER_LAVA', 'RESOLVE_GAP', 'RESOLVE_HOLD_S', 'RESOLVE_HP_LOSS', 'RULES', 'SHAPE_COLUMNS', 'START_Y', 'SWEEP', 'TRACE_EVERY_S', 'UNARMED', 'WAVES', 'WEAPON', '_ARENA', '_FIGHT_SETUP', '_answers_are_closed', '_behaviour_check', '_build', '_carry', '_cells', '_columns_possible', '_combat_execute', 'ENGAGED_INTENT', 'WINDOW_PROBE', 'answered_by_time', 'perception_probe', 'missing_columns', '_combat_intent', '_decision_gaps_ok', '_fight_row', '_fight_until', '_first_out', '_fought', '_fought_for', '_gap_blocked', '_gone', '_hostiles', '_hp_kept', '_killed', 'kill_stat', 'kill_stat_scene', 'stat_count', '_kinds_of', '_last', '_less_hurt_than', '_more_of_them_costs_more', '_offhand_shield', '_plain', '_platform', '_record_bids', '_record_with_start', '_restock', '_revive', '_roof', '_sampler', 'reflex_last', '_scatter', '_seed_of', '_shapes_fit_the_enemy', 'escaped', '_escaped', '_siege_build', '_siege_detail_of', '_siege_kit', '_siege_record', '_summon', '_threat_kinds', '_threat_resolved', 'resolved', 'angers', 'game_time', 'provoked', '_endermen_calm', 'ENDERMEN', 'positions', 'covered_in_time', '_took_cover', 'trapped_room', '_kept_health', 'endermen_off_path', 'alcove', 'alcove_cover', '_took_cover_alcove', '_walled', '_wave_cleared', '_went_out', '_blocked', 'DEFLECT', 'EYE_Y', 'FIREBALL_SPEED', 'GHAST_HP', 'heading', 'FIREBALL', 'SHOTS', 'SHOT_DIST', 'CORRIDOR', 'CORRIDOR_LEN', 'VOLLEY_WATCH_S', 'corridor', 'shot_at', 'tag_shots', 'next_shot_due', '_deflect_volley', '_deflect_watch', '_deflected', '_server_hp', 'data_health', 'deflect_eye', 'deflect_row', 'fireball_end', 'volley_done', 'volley_verdict', '_where', '_ys', 'arena_row', 'behaviour', 'behaviour_row', 'blind_s', 'escape_detail', 'escape_row', 'estimate', 'fight_cell_row', 'paths', 'random', 'siege_detail', 'siege_row']
