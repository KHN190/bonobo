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
    from .skillcore import Context
    from .world import Snapshot

    def answer(option):
        snap = Snapshot()
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
    """Is the new answer the one already being carried out? Then the posted task keeps running (append). A walk
    to a point that drifted by less than SAME_R is the same walk (a retreat re-aimed from where we now stand)."""
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
    """The one loop that carries out answers, for a threat and for a boss: while `going()`, post what `want_of()`
    answers — the same answer keeps the posted task running, a different one /stops it and posts its own. `held`
    ({"done", "task_id"}) is the loop's memory, kept by the caller so a `finally` can stop what is still posted.
    `again`: a task that ended while its answer is still wanted is posted again (a crystal still standing is shot
    again; an attack that ended with the skeleton alive attacks again — bench fight_skeleton_1 stood 16 s wanting
    "fight" with nothing posted); otherwise the loop waits for a new answer. Returns when want_of() says None (nothing left to do).
    Yields once per pass, so a skill can `yield from` it."""
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


LOST_S = 3.0           # nothing has chased us this long (killed, gone, outrun): the engagement may end
CLOSING = 0.3          # blocks/s toward us: a threat coming this fast is following, wherever it is
_CHASE = {"at": None}  # when a threat was last seen chasing, in this engagement


def chasing(rows, here):
    """Pure: some threat is still after us — within its own notice radius (it follows from there), or closing."""
    from .estimate import MOBS
    for centre, _reach, vel, kind, *_ in rows:
        d = math.dist(centre, here)
        if d <= float(MOBS.get(kind, {}).get("notice_r", 16.0)):
            return True
        if d > 0 and sum(vel[i] * (here[i] - centre[i]) / d for i in range(3)) > CLOSING:
            return True
    return False


def engagement_over(rows, here, chased_at, now):
    """Pure: (over, chased_at). Never over while a threat chases (a creeper evaded for 2 s is still coming); over
    once nothing has chased for LOST_S — it died, it went, or we outran it. No rows at all counts the same way: a
    blind moment is shorter than LOST_S, a dead mob stays gone."""
    if rows and chasing(rows, here):
        return False, now
    chased_at = now if chased_at is None else chased_at
    return now - chased_at >= LOST_S, chased_at


def lease_done(state, rows, price, ids=()):
    """Has answering stopped paying? The lease's release condition, and nothing else releases it.

    Not while anything still chases (`engagement_over`): an evade that bought distance is not the end of a creeper.
    Blind moments are NOT an answer either: the entity read is a second old, the watcher was busy, the rows aged
    out — the chase clock only ends after LOST_S of nobody after us. Then: ended when answering stops paying.
    """
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


def _bed_bomb(option, state):
    """Into the bombing hole, detonate (use a bed already there, else place-and-use), back to cover: one window as
    one batch — a round trip is ~0.1 s and the shortest window 0.4 s, so nothing in it waits for Python."""
    bed, item, stand, cover, placed = option.target
    bomb = ({"type": "use", "x": bed[0], "y": bed[1], "z": bed[2]} if placed
            else {"type": "bed_bomb", "x": bed[0], "y": bed[1], "z": bed[2], "item": item})
    return [{"type": "travel", "x": stand[0], "y": stand[1], "z": stand[2], "range": 0.8}, bomb,
            {"type": "travel", "x": cover[0], "y": cover[1], "z": cover[2], "range": 0.6}]


def _place(option, state):
    cells, item = option.target
    if not state["inv"].count(item):
        return []
    return [{"type": "place", "item": item, "x": x, "y": y, "z": z} for x, y, z in cells]


BATCH = {"fight": _fight, "evade": _evade, "eat": _eat, "shield": _shield, "reshape": _reshape,
         "bed_bomb": _bed_bomb, "place": _place}       # "shoot" is lent by combat (combat.shoot_batch)
# What a batch needs read around the body, by kind: {kind: feet -> Region}. Skills that lend their batch register
# both (skills.py: "wall_in" → pod_commands, _pod_region), so this module never imports the skill library.
REGION = {}


def lend(kind, make, region=None):
    """A skill module lends its command batch as an answer: `make(option, state)`, and the region it reads."""
    BATCH[kind] = make
    if region is not None:
        REGION[kind] = region


def engage(decision, s, ctx):
    """Carry out one threat answer — an Option from perception or a Decision from the emergency; both name a `kind`
    and a `target`: its batch (`batch`) is POSTED, not awaited. Returns {"id": last task} for the engagement to
    watch; appending or re-posting is the engagement's call."""
    from . import perception
    from .skillcore import body_state, feet
    read = REGION.get(decision.kind)
    state = body_state(ctx, read(feet()) if read else None, threats=perception.threats_seen()[0])
    tasks = batch(decision, state)
    if not tasks:
        raise NotAvailable(f"{decision.kind}: nothing to do it with from here")
    r = api.post("/task?wait=0", {"tasks": tasks})
    queued = r.get("tasks") or []
    if not queued:
        raise NotAvailable(f"{decision.kind}: the game queued none of it ({r.get('message')})")
    return {"id": queued[-1]["id"]}


# ------------------------------------------------------------------------------------------------ the dragon
# The boss fight is carried out by the same loop (`carry`) and the same batches as any threat. What to do is the
# phase model's (fight_plan.Fight.plan, one intent per round); this maps its intent to an answer the loop posts.
Answer = __import__("collections").namedtuple("Answer", "kind target")


def dragon_answer(intent, view):
    """Pure: the phase model's intent → the Answer the loop posts, or None when the dragon is dead (the fight
    stops). `view`: {"dead", "dragon" (entry or None), "crystals" (open ones, in shooting order), "here", "bed" (item or None),
    "bed_cell", "bomb" ((bed, item, stand, cover, placed) when a window can be bombed from here, else None),
    "reinforce" (cells), "escape" (away from breath, or None), "cover" (retreat cell or None)}.
      perched, a window  → a bed bomb on the bed cell, or melee without a bed (fire_window)
      flying             → shoot the nearest open crystal (shoot_crystal)
      breath, or a fault → back into cover / away from the cloud (retreat, the default)
      prep while it flies → place the bed, obsidian on the mouth; dig the pit / shake an enderman are skills
                            ("prep": the loop runs them whole)
    An intent past its deadline is a retreat: starting a bomb with no window left is caught in the open."""
    if view["dead"]:
        return None
    name = intent.get("intent")
    retreat = Answer("evade", view["escape"] or view["cover"] or tuple(view["here"]))
    if name != "retreat" and (intent.get("deadline_s") if intent.get("deadline_s") is not None else 1.0) <= 0.0:
        return retreat
    if name == "shoot_crystal" and view["crystals"]:
        return Answer("shoot", view["crystals"][0])
    if name == "fire_window" and view["dragon"] is not None:
        if view.get("bomb"):
            return Answer("bed_bomb", view["bomb"])
        return Answer("fight", view["dragon"]["id"])
    if name == "place_bed" and view["bed"] and view["bed_cell"]:
        return Answer("place", ((tuple(view["bed_cell"]),), view["bed"]))
    if name == "reinforce" and view.get("reinforce"):
        return Answer("place", (tuple(map(tuple, view["reinforce"])), "minecraft:obsidian"))
    if name in ("dig_tunnel", "water_bucket"):
        return Answer("prep", name)
    return retreat
