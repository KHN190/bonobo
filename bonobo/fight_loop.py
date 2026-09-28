"""Hostiles: fight or flight. Perception sees a threat and offers the answer it chose (`offer`); this module takes the body for it and carries it out on its OWN thread (`_engagement`), so perception keeps sensing while the fight runs — an attack used to hold the perception thread for up to 45 s. Environmental hazards are not here (hazard.py). One engagement at a time. While it runs, a new answer from perception is not a second commander: it replaces what the engagement wants (`_ENG["want"]`), and the engagement appends (same answer: the posted task keeps running) or /stops and posts the new one. It ends when the lease says answering has stopped paying, and `disengage` always runs: what it posted is stopped, the lease handed back, the plan drives again."""

import math
import threading
import time

from . import api, arbiter, nav, field as _field, kernel, threat
from .api import NotAvailable
from .skillcore import Context
from .world import Inventory, Snapshot
from .estimate import follows_to
from .beliefs import MOBS
from .knowledge import ALL_FOOD, RAW_MEAT
from .data import GROUPS

ANSWER = None          # (option) -> None | {"id": task}: carries out one answer with the agent's memory and policy
POLL_S = 0.5           # how often a running engagement looks at what perception now wants
_ENG = {"thread": None, "want": None, "failure": {}, "intent": None}
_ENG_LOCK = threading.Lock()

def wire(mem, policy_of, blacklist, prices=None):
    """Give the fight what it needs from the agent: memory, a movement policy for the current snapshot, and the shared blacklist."""

    global ANSWER

    def answer(option):
        snap = Snapshot.from_readings(api.get("/state"), Inventory())
        ctx = Context(mem, policy_of(snap), snap.dimension, blacklist, prices=prices)
        return engage(option, snap.state, ctx)
    ANSWER = answer
    return answer

def wired():
    return ANSWER is not None

def engaged():
    """The engagement running now, or None."""
    t = _ENG["thread"]
    return _ENG["intent"] if t is not None and t.is_alive() else None

def offer(option, worth, key, now, release, held, seen_at):
    """Answer a threat now."""

    with _ENG_LOCK:
        running = engaged()
        if running is not None and arbiter.BODY.holder() is running:
            _ENG["want"] = option
            return (running.layer, running.reason), None, _ENG["failure"]
    failure = {}

    def run():
        intent = arbiter.BODY.current()
        _CHASE["at"] = None                      # a new engagement: its own chase clock
        with _ENG_LOCK:
            th = threading.Thread(target=_engagement, args=(intent, failure), daemon=True, name="fight")
            _ENG.update(thread=th, want=option, failure=failure, intent=intent)
        th.start()

    taken, refused = arbiter.BODY.preempt("tactic", run, key, worth_s=worth, now=now, clear_first=True,
                                          release=release, held=held, seen_at=seen_at)
    return taken, refused, failure

SAME_R = 4.0           # two walks to points this close are one answer: the posted walk keeps going

def same(a, b):
    """Is the new answer the one already being carried out?"""

    if a is b:
        return True
    if a is None or b is None or a.kind != b.kind:
        return False
    if a.target == b.target:
        return True
    pa, pb = a.target, b.target
    return (isinstance(pa, tuple) and isinstance(pb, tuple) and len(pa) == len(pb) == 3
            and all(isinstance(v, (int, float)) for v in pa + pb) and math.dist(pa, pb) <= SAME_R)

def carry(want_of, answer, going, held, again=False):
    """The one loop carrying answers: while `going()`, the same answer keeps the posted task, a new one /stops it and posts its own."""

    while going():
        want = want_of()
        if want is None:
            return
        if not same(want, held["done"]):
            if held["task_id"] is not None:
                api.post("/stop")
            got = answer(want)
            held["task_id"] = got.get("id") if isinstance(got, dict) else None
            held["done"] = want
        elif held["task_id"] is not None:
            r = api.get(f"/task?id={held['task_id']}&wait=1")
            if r.get("status") != "running":
                held["task_id"] = None
                if again:
                    held["done"] = None
                    time.sleep(POLL_S)          # then post it again, not in a tight loop
        else:
            time.sleep(POLL_S)
        yield want.kind

def _engagement(intent, failure):
    """The fight's own thread: carry out what perception wants, re-reading it every POLL_S, until the lease ends."""
    held = {"done": None, "task_id": None}
    try:
        def loop():
            for _ in carry(lambda: _ENG["want"], ANSWER, lambda: arbiter.BODY.holder() is intent, held, again=True):
                pass
        arbiter.BODY.carry(intent, loop)
    except Exception as e:
        failure["failed"] = f"{type(e).__name__}: {e}"
    finally:
        disengage(intent, stop=held["task_id"] is not None)

def disengage(intent, stop=True):
    """Always, however the engagement ended: stop what it still has running, hand the body back, forget it."""
    with _ENG_LOCK:
        if _ENG["intent"] is intent:
            _ENG.update(thread=None, want=None, intent=None)
    try:
        if stop and arbiter.BODY.holder() is intent:
            api.post("/stop")
    except api.McError:
        pass
    arbiter.BODY.hand_back(intent)

# -- the decision

FIGHT_POLL_S = 0.1     # while a fight holds the body: a window is 0.4 s at worst, a 0.2 s poll sees half of it
HELD = None            # the threat layer's held decision (kernel.Held): kept while it pays, replaced when not

def active():
    """A fight is on: an engagement of ours is running, or a boss fight holds the body (`arbiter.BODY.engaged`)."""

    return engaged() is not None or bool(arbiter.BODY.engaged)

def threat_state(state, rows, work_s=None, ids=()):
    """The threat model's state vector, read off a player state and the rows the watcher last saw."""

    st = {"here": (state["x"], state["y"], state["z"]), "hp": float(state.get("health", 20)),
          "sword": int(state.get("sword_tier", 0)), "protection": threat.protection(state.get("armor", 0), False),
          "night": False, "blocks": int(state.get("blocks", 0)), "hazards": rows,
          "food_items": int(state.get("food_items", 0)), "shield": bool(state.get("shield")),
          "golden_apples": int(state.get("golden_apples", 0)), "hunger": float(state.get("food", 20)),
          "field": state.get("field") or _field.Field(), "ids": list(ids), "dig_ok": bool(state.get("dig_ok")),
          "footing": state.get("footing")}
    if work_s is not None:
        st["work_s"] = work_s
    return st

def bid(state, rows, price, work_s=None, now=None, ids=()):
    """(the answer, seconds it saves) the held decision stands behind now, or None when nothing pays."""
    global HELD
    if not rows:
        return None
    st = threat_state(state, rows, work_s, ids)
    field_model = threat.Field(st, price)
    if HELD is None:
        HELD = kernel.Held()
    horizon_now = threat.horizon_for(st)
    choice = HELD.decide(field_model, field_model.state(), now if now is not None else time.time(),
                         holds=lambda c, _s: still_worth(c, field_model, price, horizon_now))
    option = choice.action.option if choice.action is not None else None
    if option is None or option.kind == "ignore":
        return None
    worth = threat.saves(option, [a.option for a in field_model.opts], price, horizon_now)
    return (option, round(worth, 1)) if worth > 0 else None

LOST_S = 3.0           # nothing has chased us this long (killed, gone, outrun): the engagement may end
CLOSING = 0.3          # blocks/s toward us: a threat coming this fast is following, wherever it is
_CHASE = {"at": None}  # when a threat was last seen chasing, in this engagement

def chasing(rows, here):
    """Pure: some threat is still after us — it notices or reaches us here (`estimate.follows_to`), or closes."""
    for h in rows:
        centre, vel = h[0], h[2]
        if follows_to(here, h):
            return True
        d = math.dist(centre, here)
        if d > 0 and sum(vel[i] * (here[i] - centre[i]) / d for i in range(3)) > CLOSING:
            return True
    return False

def engagement_over(rows, here, chased_at, now):
    """Pure: (over, chased_at)."""

    if rows and chasing(rows, here):
        return False, now
    chased_at = now if chased_at is None else chased_at
    return now - chased_at >= LOST_S, chased_at

def lease_done(state, rows, price, ids=()):
    """Has answering stopped paying?"""

    here = (state["x"], state["y"], state["z"])
    over, _CHASE["at"] = engagement_over(rows, here, _CHASE["at"], time.time())
    if not over:
        return False
    if not rows:
        return True
    try:
        fresh = bid(state, rows, price, now=time.time(), ids=ids)
    except Exception:
        return False
    return fresh is None or fresh[1] <= 0

def still_worth(choice, field_model, price, horizon):
    """The assumption behind a threat answer: that it still beats carrying on."""

    options = [a.option for a in field_model.opts]
    same = next((o for o in options if o.kind == choice.name), None)
    if same is None:
        return False
    return threat.saves(same, options, price, horizon) > 0

# -- the batches

def batch(option, state):
    """Pure: the batch that carries out one answer from a body state; [] when it cannot be carried out from here."""

    make = BATCH.get(option.kind)
    return list(make(option, state)) if make else []

# between swings (jar ≥ 0.1.51): melee back out of reach, ranged strafe off the line, a creeper keepoff past its blast
FOOTWORK = {"melee": "back", "ranged": "strafe", "burst": "keepoff"}
LURE_BLOCKS = 8          # how far a creeper by our builds is led away from them before the fight

def footwork(target, state):
    """Pure: the footwork for fighting `target`, from the rows being answered; None when it is not among them."""

    rows, ids = state.get("threats") or [], list(state.get("threat_ids") or [])
    if target not in ids or ids.index(target) >= len(rows):
        return None
    mob = MOBS.get(rows[ids.index(target)][3], {})
    return FOOTWORK["burst" if mob.get("burst") else "ranged" if mob.get("ranged") else "melee"]

def lure_spot(here, creeper, protected, blast):
    """Pure: where to lead a creeper first — LURE_BLOCKS away from our builds within `blast` of it, or None when none is that near."""

    near = [c for c in protected if math.dist(c, creeper) <= blast]
    if not near:
        return None
    cx, cz = sum(c[0] for c in near) / len(near), sum(c[2] for c in near) / len(near)
    dx, dz = here[0] - cx, here[2] - cz
    n = math.hypot(dx, dz) or 1.0
    return (round(here[0] + LURE_BLOCKS * dx / n), int(here[1]), round(here[2] + LURE_BLOCKS * dz / n))

def _attack(option, state, **extra):
    task = {"type": "attack", "entity": option.target, **extra}
    step = footwork(option.target, state)
    if step is None:
        return [task]
    out = []
    if step == "keepoff":
        rows, ids = state["threats"], list(state["threat_ids"])
        row = rows[ids.index(option.target)]
        spot = lure_spot(state["feet"], row[0], state.get("protected", ()), float(MOBS[row[3]].get("keep_out", 5.0)))
        if spot is not None:
            brk, plc, void = nav.MOVES["evade"]
            out.append({"type": "travel", "x": spot[0], "y": spot[1], "z": spot[2], "range": 2, "break": brk,
                        "place": plc, "voidBridge": void, "placeBudget": int(state["inv"].count("building")),
                        "avoid": nav.avoid_cells(state.get("protected", ()), spot, state["feet"])})
    return out + [dict(task, footwork=step)]

def _fight(option, state):
    return _attack(option, state)

def _fight_shielded(option, state):
    """The attack with the shield raised between swings (jar AttackTask "shield", ≥ 0.1.45)."""
    if state["inv"].offhand() != "minecraft:shield":
        return []
    return _attack(option, state, shield=True)

def _evade(option, state):
    x, y, z = option.target
    brk, plc, void = nav.MOVES["evade"]            # digs and bridges as priced, never out over the void (nav.MOVES)
    return [{"type": "travel", "x": x, "y": y, "z": z, "range": 3, "break": brk, "place": plc, "voidBridge": void,
             "placeBudget": int(state["inv"].count("building")),
             "avoid": nav.avoid_cells(state.get("protected", ()), (x, y, z), state["feet"])}]

def _eat(option, state):
    wanted = [option.target] if option.target else list(ALL_FOOD) + list(RAW_MEAT)
    food = next((f for f in wanted if state["inv"].count(f)), None)
    return [{"type": "eat", "item": food}] if food else []

def _shield(option, state):
    if state["inv"].offhand() != "minecraft:shield":
        return []
    return [{"type": "use_item", "hand": "offhand", "hold_ms": 1500}]

def _reshape(option, state):
    """Change the ground: dig down n, stand n blocks up, or put n blocks between us and the nearest threat."""
    where, n = option.target
    x, y, z = state["feet"]
    if where == "down":
        return [nav.mine_task((x, y - 1 - i, z), down=True) for i in range(n)]
    item = next((b for b in GROUPS["building"] if state["inv"].count(b)), None)
    if item is None:
        return []
    if where == "under":
        return [{"type": "pillar", "item": item} for _ in range(n)]
    near = state.get("threats") or []
    toward = min(near, key=lambda h: math.dist((x, y, z), h[0]))[0] if near else (x + 1, y, z)
    step = [1 if toward[i] > (x, z)[j] else (-1 if toward[i] < (x, z)[j] else 0) for j, i in enumerate((0, 2))]
    return [{"type": "place", "item": item, "x": x + step[0], "y": y + i, "z": z + step[1]} for i in range(n)]

def _place(option, state):
    cells, item = option.target
    if not state["inv"].count(item):
        return []
    return [{"type": "place", "item": item, "x": x, "y": y, "z": z} for x, y, z in cells]

BATCH = {"fight": _fight, "fight_shielded": _fight_shielded, "evade": _evade, "eat": _eat, "shield": _shield, "reshape": _reshape,
         "place": _place}       # "shoot" is lent by combat (combat.shoot_batch)
# what a batch reads around the body, by kind; skills register theirs so this module never imports the skill library
REGION = {}

def lend(kind, make, region=None):
    """A skill module lends its command batch as an answer: `make(option, state)`, and the region it reads."""
    BATCH[kind] = make
    if region is not None:
        REGION[kind] = region

def engage(decision, s, ctx):
    """Carry out one threat answer: its batch is posted, not awaited."""

    from .skillcore import body_state, feet
    read = REGION.get(decision.kind)
    rows, ids = threat.threats_seen()
    state = body_state(ctx, read(feet()) if read else None, threats=rows, threat_ids=ids)
    tasks = batch(decision, state)
    if not tasks:
        raise NotAvailable(f"{decision.kind}: nothing to do it with from here")
    # the fight's bag is the one perception read for this answer; no block read (a weapon wants none)
    r = api.post("/task?wait=0", {"tasks": api.ARM(tasks, inv=state["inv"], read_blocks=False) if api.ARM else tasks})
    queued = r.get("tasks") or []
    if not queued:
        raise NotAvailable(f"{decision.kind}: the game queued none of it ({r.get('message')})")
    return {"id": queued[-1]["id"]}

Answer = __import__("collections").namedtuple("Answer", "kind target")
