"""The PRODUCTION round on γ(facts): brain.decide with the transport stubbed (check/stub.py). It calls; it never
re-decides. Decision = (layer, kind, token, target, writes, reason) plus the intents the arbiter ranked."""
import os
import shutil
from collections import namedtuple
from unittest import mock

from . import DATA as _DIR

Decision = namedtuple("Decision", "layer kind token target writes reason name alternatives")
STUBBED = ("fight_loop.offer",)     # stood in for while the round runs: the threat answer's handover of the body


def _fresh_dir():
    for name in os.listdir(_DIR):
        p = os.path.join(_DIR, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


def _decision(act, chosen, intents, world):
    step = getattr(act, "step", None)
    alts = [(i.layer, i.kind, getattr(i.action, "name", None)) for i in intents]
    return Decision(layer=chosen.layer if chosen else None, kind=getattr(act, "layer", None),
                    token=getattr(step, "token", None) if step is not None else None,
                    target=tuple(step.detail["pos"]) if step is not None and "pos" in step.detail else None,
                    writes=tuple(p for p, _b in world.posts), reason=None if act is not None else None,
                    name=getattr(act, "name", None), alternatives=tuple(alts))


def decide(facts, fail_then_again=True):
    """(Decision, alpha of the γ world, ctx): the production round's choice on the concrete world of `facts`. ctx:
    the step's kind, whether its target lies in the home, the night's cheapest way (needs.overnight), and the
    decision of a second round after the first one's step failed (D5)."""
    from bonobo import api, arbiter, brain, fight_loop, lifecycle, perception, tape
    from bonobo.api import NotAvailable
    from bonobo import dispatch
    from bonobo.data import home_box_of
    from bonobo.memory import Memory
    from bonobo.world import Inventory, Snapshot
    from .facts import alpha
    from .gamma import gamma
    _fresh_dir()
    lifecycle.reset_all()
    seen = {}
    real_arbitrate = arbiter.arbitrate

    def watched(intents, now=None, facts=None):
        chosen = real_arbitrate(intents, now=now, facts=facts)
        seen["intents"], seen["chosen"] = list(intents), chosen
        return chosen
    from .facts import DIMS
    mem = Memory()
    world = gamma(facts, mem)
    failure = next((d.failure(facts) for d in DIMS if hasattr(d, "failure")), None) \
        or NotAvailable("check: the step failed here")
    ctx = {}
    with mock.patch.object(api, "api", world.api), mock.patch.object(arbiter, "arbitrate", watched), \
            mock.patch.object(api, "detail", lambda *a: None), mock.patch.object(api, "log", lambda *a: None), \
            mock.patch.object(tape, "REPLAY", None), \
            mock.patch.object(api.STATE, "feet_seen", None):     # the stub's /state stays in the round, not the process
        b = brain.Brain()
        b.mem = mem
        if facts["cooled"]:
            from bonobo.decompose import way_key
            from .facts import night_ways
            for way in night_ways():
                b.failed(way_key(way), failure, quiet=True)
        for dim in DIMS:
            if hasattr(dim, "prepare"):
                dim.prepare(b, facts)
        snap = Snapshot.from_readings(api.get("/state"), Inventory())
        b.place = None
        b.policy_cache = b.policy(snap, snap.night)
        bctx = b.context(snap.dimension)
        world.posts.clear()
        offered = []

        def offer(option, worth, key, now, release, held, seen_at):
            offered.append((option, worth))
            return ("tactic", key), None, {}
        with mock.patch.object(fight_loop, STUBBED[0].partition(".")[2], offer):
            w = perception.Watcher()
            w._look(snap.state)
            w._answer_threats(snap.state)          # the threat layer's answer: TACTIC preempts the plan (K3)
        act = b.decide(snap, bctx)
        got = alpha(snap, mem, world, b.ready)
        if offered:
            option, worth = offered[-1]
            d = Decision(layer="tactic", kind="threat", token=option.kind, target=getattr(option, "target", None),
                         writes=tuple(p for p, _b in world.posts), reason=None, name=f"threat:{option.kind}",
                         alternatives=(("tactic", option.kind, worth),))
            return d, got, {"step_kind": "threat"}
        d = _decision(act, seen.get("chosen"), seen.get("intents", ()), world)
        step = getattr(act, "step", None)
        boxes = [tuple(map(tuple, bx)) for h in mem.homes(snap.dimension) for bx in h.get("boxes", ())]
        ctx["step_kind"] = step.kind if step is not None else None
        ctx["target_in_home"] = d.target is not None and home_box_of(boxes, d.target) is not None
        found = dispatch.runner_for(bctx, step) if step is not None else None
        if found is not None:
            # S5: the step's skill offered under production's fight line (an optional fight below it: why)
            ok, why = brain.fight_line_holds(found[0].contract, (bctx,) + tuple(found[1]), snap.state, snap.inv)
            if not ok:
                ctx["fight_line"] = why
        if snap.night:
            ctx["night_way"] = b.needs.overnight(snap)[0]
        chosen = seen.get("chosen")
        if fail_then_again and act is not None and chosen is not None:
            # D5: the step fails here; the arbiter's gate drops an intent whose key is cooling (arbiter.viable) —
            # an intent with no key, or one the failure does not cool, is offered again in this same state
            b.failed(act.name, failure)
            ctx["reselected"] = chosen.key is None or b.ready(chosen.key)
    return d, got, ctx
