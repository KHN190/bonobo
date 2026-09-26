"""Hostiles: fight or flight. Perception sees a threat and offers the answer it chose (`offer`); this module takes the
body for it and carries it out (`engage`). Environmental hazards are not here (hazard.py).

Moved out of the brain and the perception thread as they were: the answer still runs where perception offers it,
under a TACTIC preemption whose lease lasts until answering stops paying (`perception.lease_done`). What changes is
only who owns it — the survival brain no longer knows how to fight.
"""
import math

from . import api, arbiter, nav
from .api import NotAvailable
from .skillcore import mine_cell
from .world import Inventory

ANSWER = None          # (option) -> None: carries out one answer with the agent's memory and movement policy


def wire(mem, policy_of, blacklist, prices=None):
    """Give the fight what it needs from the agent: memory, a movement policy for the current snapshot, and the
    shared blacklist. `policy_of(snap)` is asked per answer, so a fight at night walks by the night's rules."""
    global ANSWER
    from . import skills
    from .world import Snapshot

    def answer(option):
        snap = Snapshot()
        ctx = skills.Context(mem, policy_of(snap), snap.dimension, blacklist, prices=prices)
        engage(option, snap.state, ctx)
    ANSWER = answer
    return answer


def wired():
    return ANSWER is not None


def offer(option, worth, key, now, release, held, seen_at):
    """Answer a threat now: take the body at TACTIC (stopping what runs), run the answer, and hold the lease until
    `release()` says answering has stopped paying. Returns (taken, refused, failure)."""
    failure = {}

    def run():
        try:
            ANSWER(option)
        except Exception as e:
            failure["failed"] = f"{type(e).__name__}: {e}"
            raise

    taken, refused = arbiter.BODY.preempt("tactic", run, key, worth_s=worth, now=now, clear_first=True,
                                          release=release, held=held, seen_at=seen_at)
    return taken, refused, failure


def engage(decision, s, ctx):
    """Carry out one threat answer — an Option from perception or a Decision from the emergency; both name a `kind`
    and a `target`. May fail: it runs under the arbiter, never from the brain's reflexes."""
    from . import skills
    if decision.kind == "fight":
        api.run({"type": "attack", "entity": decision.target}, wait=45)
    elif decision.kind == "evade":
        if not nav.moved(nav.go_to(decision.target, ctx.policy, range_=3, attempts=1, min_hp=0)):
            raise NotAvailable(f"could not get away to {decision.target}")
    elif decision.kind == "wall_in":
        skills.pod(ctx)
    elif decision.kind == "eat":
        skills.eat(raw_ok=True)
    elif decision.kind == "shield":
        skills.shield_to_offhand()
        api.run({"type": "use_item", "hand": "offhand", "hold_ms": 1500}, wait=5)
    elif decision.kind == "reshape":
        reshape(decision, s, ctx)


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
