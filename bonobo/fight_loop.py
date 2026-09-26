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
from . import skill as skillkit
from .api import NotAvailable
from .skillcore import mine_cell
from .world import Inventory

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


def engage(decision, s, ctx):
    """Carry out one threat answer — an Option from perception or a Decision from the emergency; both name a `kind`
    and a `target`. A fight is POSTED, not awaited: returns {"id": task} for the engagement to watch. Open-loop
    answers post their skill's command batch (`skill.commands_of`), the one definition the skill itself runs."""
    from . import skills
    if decision.kind == "fight":
        return api.post("/task?wait=0", {"type": "attack", "entity": decision.target})
    if decision.kind == "evade":
        if not nav.moved(nav.go_to(decision.target, ctx.policy, range_=3, attempts=1, min_hp=0)):
            raise NotAvailable(f"could not get away to {decision.target}")
    elif decision.kind == "wall_in":
        here = skills.feet()
        batch = skillkit.commands_of(skills.pod, skills.body_state(ctx, skills._pod_region(here)))
        api.run_chain(batch)
        if not skills.enclosed():
            raise NotAvailable("walling in left an opening")
    elif decision.kind == "eat":
        skills.eat(raw_ok=True)
    elif decision.kind == "shield":
        skills.shield_to_offhand()
        api.run({"type": "use_item", "hand": "offhand", "hold_ms": 1500}, wait=5)
    elif decision.kind == "reshape":
        reshape(decision, s, ctx)
    return None


def reshape(decision, s, ctx):
    """Change the ground: block the way, stand a block up, or dig down. One answer, three places to put it."""
    from . import perception, skills          # the one authority on what is around us, asked where it is used
    where, n = decision.target
    feet = tuple(int(math.floor(s[k])) for k in ("x", "y", "z"))
    if where == "down":
        for i in range(n):
            mine_cell(ctx.policy, (feet[0], feet[1] - 1 - i, feet[2]), collect=False, wait=20)
        return
    item = next((b for b in skills.GROUPS["building"] if Inventory().count(b)), None)
    if item is None:
        raise NotAvailable("nothing to shape the ground with")
    if where == "under":
        for _ in range(n):
            api.run({"type": "pillar", "item": item}, wait=20)
        return
    near = perception.threats_seen()[0]
    toward = min(near, key=lambda h: math.dist(feet, h[0]))[0] if near else (feet[0] + 1, feet[1], feet[2])
    step = [1 if toward[i] > feet[i] else (-1 if toward[i] < feet[i] else 0) for i in (0, 2)]
    for i in range(n):
        skills.place(item, (feet[0] + step[0], feet[1] + i, feet[2] + step[1]))
