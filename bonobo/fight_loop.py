"""Hostiles: fight or flight. Perception sees a threat and offers the answer it chose (`offer`); this module takes the
body for it and carries it out on its OWN thread (`_engagement`), so perception keeps sensing while the fight runs —
an attack used to hold the perception thread for up to 45 s. Environmental hazards are not here (hazard.py).

One engagement at a time. While it runs, a new answer from perception is not a second commander: it replaces what
the engagement wants (`_ENG["want"]`), and the engagement appends (same answer: the posted task keeps running) or
/stops and posts the new one. It ends when the lease says answering has stopped paying, and `disengage` always runs:
what it posted is stopped, the lease handed back, the plan drives again.
"""
import math
import threading
import time

from . import api, arbiter, nav
from .api import NotAvailable

ANSWER = None          # (option) -> None | {"id": task}: carries out one answer with the agent's memory and policy
POLL_S = 0.5           # how often a running engagement looks at what perception now wants
_ENG = {"thread": None, "want": None, "failure": {}, "intent": None}
_ENG_LOCK = threading.Lock()


def wire(mem, policy_of, blacklist, prices=None):
    """Give the fight what it needs from the agent: memory, a movement policy for the current snapshot, and the
    shared blacklist. `policy_of(snap)` is asked per answer, so a fight at night walks by the night's rules."""
    global ANSWER
    from . import skills
    from .world import Snapshot

    def answer(option):
        snap = Snapshot()
        ctx = skills.Context(mem, policy_of(snap), snap.dimension, blacklist, prices=prices)
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
    """Answer a threat now. While an engagement holds the body this only changes what it wants (append or reissue);
    otherwise take the body at TACTIC (stopping what runs) and start one. Returns (taken, refused, failure) at once:
    nothing here waits for the answer."""
    with _ENG_LOCK:
        running = engaged()
        if running is not None and arbiter.BODY.holder() is running:
            _ENG["want"] = option
            return (running.layer, running.reason), None, _ENG["failure"]
    failure = {}

    def run():
        intent = arbiter.BODY.current()
        with _ENG_LOCK:
            th = threading.Thread(target=_engagement, args=(intent, failure), daemon=True, name="fight")
            _ENG.update(thread=th, want=option, failure=failure, intent=intent)
        th.start()

    taken, refused = arbiter.BODY.preempt("tactic", run, key, worth_s=worth, now=now, clear_first=True,
                                          release=release, held=held, seen_at=seen_at)
    return taken, refused, failure


def same(a, b):
    """Is the new answer the one already being carried out? Then the posted task keeps running (append)."""
    return a is b or (a is not None and b is not None and a.kind == b.kind and a.target == b.target)


def _engagement(intent, failure):
    """The fight's own thread: carry out what perception wants, re-reading it every POLL_S, until the lease ends."""
    done, task_id = None, None
    try:
        def loop():
            nonlocal done, task_id
            while arbiter.BODY.holder() is intent:
                want = _ENG["want"]
                if not same(want, done):
                    if task_id is not None:
                        api.post("/stop")          # a different answer: stop the posted one, post the new one
                    got = ANSWER(want)
                    task_id = got.get("id") if isinstance(got, dict) else None
                    done = want
                elif task_id is not None:
                    r = api.get(f"/task?id={task_id}&wait=1")
                    if r.get("status") != "running":
                        task_id = None                  # the posted task ended: wait for the next answer
                else:
                    time.sleep(POLL_S)
        arbiter.BODY.carry(intent, loop)
    except Exception as e:
        failure["failed"] = f"{type(e).__name__}: {e}"
    finally:
        disengage(intent, stop=task_id is not None)


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


# ------------------------------------------------------------------------------------------------ the decision

FIGHT_POLL_S = 0.1     # while a fight holds the body: a window is 0.4 s at worst, a 0.2 s poll sees half of it
HELD = None            # the threat layer's held decision (kernel.Held): kept while it pays, replaced when not


def active():
    """A fight is on: an engagement of ours is running, or a boss fight holds the body (`arbiter.BODY.engaged`).
    Perception reads this to give a fight only the life-or-death interrupts."""
    return engaged() is not None or bool(arbiter.BODY.engaged)


def threat_state(state, rows, work_s=None, ids=()):
    """The threat model's state vector, read off a player state and the rows the watcher last saw.

    One builder: the live bid and the bench have to ask the same question, and a bench that assembles its own
    state vector is testing its own arithmetic.
    """
    from . import field as _field
    from . import threat
    st = {"here": (state["x"], state["y"], state["z"]), "hp": float(state.get("health", 20)),
          "sword": int(state.get("sword_tier", 0)), "protection": threat.protection(state.get("armor", 0), False),
          "night": False, "blocks": int(state.get("blocks", 0)), "hazards": rows,
          "food_items": int(state.get("food_items", 0)), "shield": bool(state.get("shield")),
          "field": state.get("field") or _field.Field(), "ids": list(ids)}
    if work_s is not None:
        st["work_s"] = work_s
    return st


def bid(state, rows, price, work_s=None, now=None, ids=()):
    """(the answer, seconds it saves) the held decision stands behind now, or None when nothing pays."""
    global HELD
    from . import kernel, threat
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


def lease_done(state, rows, price, ids=()):
    """Has answering stopped paying? The lease's release condition, and nothing else releases it.

    Blind moments are NOT an answer: the entity read is a second old, the watcher was busy, the rows aged out. A
    lease that reads "nothing visible" as "nothing to deal with" hands the body back in the middle of a fight, and
    the planner's next mine task lands on top of the answer — which is what `BodyContested` was, all along.
    """
    if not rows:
        return False
    try:
        fresh = bid(state, rows, price, now=time.time(), ids=ids)
    except Exception:
        return False
    return fresh is None or fresh[1] <= 0


def still_worth(choice, field_model, price, horizon):
    """The assumption behind a threat answer: that it still beats carrying on. A held answer that has stopped
    paying (the sword broke, the crowd doubled) is not a commitment, it is a mistake with a timer."""
    from . import threat as _threat
    options = [a.option for a in field_model.opts]
    same = next((o for o in options if o.kind == choice.name), None)
    if same is None:
        return False
    return _threat.saves(same, options, price, horizon) > 0


# ------------------------------------------------------------------------------------------------ the batches

RAW_OK = ("minecraft:beef", "minecraft:porkchop", "minecraft:mutton", "minecraft:rabbit", "minecraft:chicken")


def batch(option, state):
    """Pure: the command batch that carries out one answer, from a body state (`skillcore.body_state` plus
    `threats`, the rows being answered). [] when the answer cannot be carried out from here — then it is not an
    answer at all. One function per kind, the same batch the skill it borrows from would post."""
    make = BATCH.get(option.kind)
    return list(make(option, state)) if make else []


def _fight(option, state):
    return [{"type": "attack", "entity": option.target}]


def _evade(option, state):
    x, y, z = option.target
    return [{"type": "travel", "x": x, "y": y, "z": z, "range": 3, "break": True, "place": True,
             "placeBudget": int(state["inv"].count("building")), "avoid": []}]


def _wall_in(option, state):
    from .skills import pod_commands
    return pod_commands(state) if state.get("region") is not None else []


def _eat(option, state):
    from .knowledge import ALL_FOOD
    food = next((f for f in list(ALL_FOOD) + list(RAW_OK) if state["inv"].count(f)), None)
    return [{"type": "eat", "item": food}] if food else []


def _shield(option, state):
    if state["inv"].offhand() != "minecraft:shield":
        return []
    return [{"type": "use_item", "hand": "offhand", "hold_ms": 1500}]


def _reshape(option, state):
    """Change the ground: dig down n, stand n blocks up, or put n blocks between us and the nearest threat."""
    from .data import GROUPS
    where, n = option.target
    x, y, z = state["feet"]
    if where == "down":
        return [nav.mine_task((x, y - 1 - i, z)) for i in range(n)]
    item = next((b for b in GROUPS["building"] if state["inv"].count(b)), None)
    if item is None:
        return []
    if where == "under":
        return [{"type": "pillar", "item": item} for _ in range(n)]
    near = state.get("threats") or []
    toward = min(near, key=lambda h: math.dist((x, y, z), h[0]))[0] if near else (x + 1, y, z)
    step = [1 if toward[i] > (x, z)[j] else (-1 if toward[i] < (x, z)[j] else 0) for j, i in enumerate((0, 2))]
    return [{"type": "place", "item": item, "x": x + step[0], "y": y + i, "z": z + step[1]} for i in range(n)]


BATCH = {"fight": _fight, "evade": _evade, "wall_in": _wall_in, "eat": _eat, "shield": _shield,
         "reshape": _reshape}


def engage(decision, s, ctx):
    """Carry out one threat answer — an Option from perception or a Decision from the emergency; both name a `kind`
    and a `target`: its batch (`batch`) is POSTED, not awaited. Returns {"id": last task} for the engagement to
    watch; appending or re-posting is the engagement's call."""
    from . import perception, skills
    here = skills.feet()
    region = skills._pod_region(here) if decision.kind == "wall_in" else None
    state = skills.body_state(ctx, region, threats=perception.threats_seen()[0])
    tasks = batch(decision, state)
    if not tasks:
        raise NotAvailable(f"{decision.kind}: nothing to do it with from here")
    r = api.post("/task?wait=0", {"tasks": tasks})
    queued = r.get("tasks") or []
    if not queued:
        raise NotAvailable(f"{decision.kind}: the game queued none of it ({r.get('message')})")
    return {"id": queued[-1]["id"]}
