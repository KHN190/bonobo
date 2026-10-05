"""The PRODUCTION round on γ(facts): brain.decide with the transport stubbed (check/stub.py). It calls; it never
re-decides. Decision = (layer, kind, token, target, writes, reason) plus the intents the arbiter ranked."""
import contextlib
import os
import time
import shutil
import tempfile
from collections import namedtuple
from typing import Any
from unittest import mock

from . import DATA as _DIR

Decision = namedtuple("Decision", "layer kind token target writes reason name alternatives")
STUBBED = ("fight_loop.offer",)     # stood in for while the round runs: the threat answer's handover of the body


def data_dirs():
    """Where the round's runtime data is: the checker's own dir, and the dirs production resolved its files under at
    import (memory, tasks) — the same unless a bonobo module was imported before check/ set MC_DATA (a test run that
    loads another module first: the round's memory and queue then lived in the tests' dir and outlived the round)."""
    from bonobo import memory, tasks
    dirs = sorted({_DIR, os.path.dirname(memory.NOTES_FILE), os.path.dirname(tasks.FILE)})
    temp = os.path.realpath(tempfile.gettempdir())
    away = [d for d in dirs if not os.path.realpath(d).startswith(temp + os.sep)]
    if away:
        # a player's own data (bonobo imported before check/): never emptied, the round refused
        raise RuntimeError(f"the checker empties only temp dirs; production resolved {away} (import check first)")
    return dirs


def fresh_round():
    """The round's one restore point: every runtime data dir emptied, every life's state (lifecycle.reset_all) and the
    session's (renew_session) back to their start."""
    from bonobo import lifecycle
    clear_inputs()
    lifecycle.reset_all()
    renew_session()


def renew_session() -> None:
    """Every session state (paths.session) back to its factory's contents, in place: a holder keeps the same object.
    Production never renews a session (it outlives every life); the checker's round and tests do."""
    from bonobo import paths
    for obj, factory in paths._SESSION.values():
        fresh = factory()
        obj.clear()
        (obj.update if isinstance(obj, (dict, set)) else obj.extend)(fresh)


def clear_inputs():
    """Every runtime data dir emptied (memory, tasks, tape: a round's inputs), the process's caches kept."""
    for d in data_dirs():
        for name in os.listdir(d) if os.path.isdir(d) else ():
            p = os.path.join(d, name)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


def _decision(act, chosen, intents, world):
    step = getattr(act, "step", None)
    alts = [(i.layer, i.kind, getattr(i.action, "name", None)) for i in intents]
    return Decision(layer=chosen.layer if chosen else None, kind=getattr(act, "layer", None),
                    token=getattr(step, "token", None) if step is not None else None,
                    target=tuple(step.detail["pos"]) if step is not None and step.detail.get("pos") is not None else None,
                    writes=tuple(p for p, _b in world.posts), reason=getattr(chosen, "reason", None) or None,
                    name=getattr(act, "name", None), alternatives=tuple(alts))


def fact_ages(b, round_snap):
    """P1: (the /state read the decision used, the round's own), {terrain read: (its age at that read, its TTL)}."""
    from bonobo import perception, world
    from bonobo.data import FACT_TTL_S
    snap = getattr(b, "decided_on", None)
    if snap is None:
        return None, {}
    look = world._SIGHT
    ages = {"the look (world.nearest)": (snap.read_at - look["t"] if look.get("key") else 0.0, FACT_TTL_S["look"]),
            "the ground (perception)": (snap.read_at - perception.STATE.grid_at if perception.STATE.grid_at else 0.0,
                                        FACT_TTL_S["ground"]),
            "the ground (the round's)": (snap.read_at - snap.region_at, FACT_TTL_S["ground"])}
    return (snap.read_seq, round_snap.read_seq), ages


def _hazard_at(ctx, world):
    """Search.advance writing a hazard at its first call (S7): the interrupt, the body under water."""
    from bonobo import api, planner
    real = planner.Search.advance

    def advance(self, node, floor=0):
        if ctx.get("hazard_at") is None:
            ctx["hazard_at"] = planner.SPENT["steps"]
            api.request_interrupt("drowning")
            world.state.update(inWater=True, air=0)
        return real(self, node, floor)
    return advance


TARGET = (12, 64, 12)       # a failure about a target (a vein, a station): its `pos`


def step_failures(first):
    """The failures D5 tries in turn: `first` (this state's), each other cause that cools (check/dims/failure), one
    about a target (`pos`: cooled at the target, banned there), an interruption (resumed, never counted)."""
    from bonobo.api import Interrupted, McError, NotAvailable
    from bonobo.data import UNREACHABLE
    from .dims.failure import FAILING
    out = [first] + [cls("check: the step failed here") for cls in FAILING.values() if type(first) is not cls]
    at = NotAvailable("check: the target was not there")
    at.pos = TARGET
    # a bare mod failure whose text says the target is out of reach (retry.cause_of: counted as nav by its text)
    unreachable = McError(f"check: {UNREACHABLE[0]} {TARGET[0]}, {TARGET[1]}, {TARGET[2]}")
    # every other exception data.EXCEPTIONS names, once each (a replan's count, a missing need or station built
    # from what it misses): retry.Retry.failed's replan and not-a-failure arms
    from .dims.failure import _classes
    seen = {type(e) for e in out} | {NotAvailable, McError, Interrupted}
    rest = []
    for cls in _classes().values():
        if cls in seen:
            continue
        for arg in ("check: the step failed here", {"minecraft:crafting_table": 1}):
            try:
                rest.append(cls(arg))
                break
            except (TypeError, AttributeError):
                continue
    return out + [at, unreachable] + rest + [Interrupted("check: interrupted")]


def look(state):
    """Perception's look with the kit already read (reset_all clears it; gold worn decides piglins)."""
    import time
    from bonobo import perception
    perception.kit(perception.kit_signature(state, time.time()))
    w = perception.Watcher()
    w._look(state)
    return w


def pressed(st):
    """Something reaches us or can shoot us: threat.pressure or threat.burst_damage above zero on the threat state
    (the test threat.options makes before it offers anything but ignoring)."""
    from bonobo import threat
    from bonobo.beliefs import MOBS
    here, prot, grid = tuple(st["here"]), float(st.get("protection", 0.0)), st.get("field")
    hazards = [h for h in st.get("hazards", ()) if h[3] in MOBS]
    return bool(hazards) and (threat.pressure(here, hazards, prot, ground=grid) > 0.0
                              or threat.burst_damage(here, hazards, prot) > 0.0)


def decide(facts, fail_then_again=True):
    """(Decision, alpha of the γ world, ctx): the production round's choice on the concrete world of `facts`. ctx:
    the step's kind, whether its target lies in the home, the night's cheapest way (needs.overnight), and the
    decision of a second round after the first one's step failed (D5)."""
    try:
        return _decide(facts, fail_then_again)
    finally:
        fresh_round()        # the round's life leaves nothing behind for the next caller in this process


def warm_name(facts, other):
    """D8: the decision's name on `facts` with the declared caches warm from a round on `other`."""
    from bonobo import lifecycle
    try:
        _decide(other, False)
        clear_inputs()
        lifecycle.reset_all(caches=False)
        renew_session()
        return _decide(facts, False, fresh=False)[0].name
    finally:
        fresh_round()


def hazard_round(facts):
    """S7: (the chosen layer, search steps after the hazard) for a hazard mid-plan; None: nothing planned."""
    try:
        d, _got, ctx = _decide(facts, False, hazard=True)
        return None if ctx.get("hazard_at") is None else (d.layer, ctx["search_steps_after"])
    finally:
        fresh_round()


CHAIN_N = 10        # G-dev: decides a chain runs from its start


def chain(facts, n=CHAIN_N):
    """G-dev: `n` decides on one world from `facts`, the chosen step's outputs and inputs carried into the bag between
    them (planner._After); each act against the task's direct plan, priced by production (plan_round, price_as_run on
    a Cost of the round's snapshot): [{name, layer, step, forced, on_route, side_s, direct_s, after_s}]."""
    from bonobo import arbiter, tasks
    rows, kept = [], None
    try:
        for _ in range(n):
            live = [t for t in tasks.load() if t["state"] in tasks.LIVE] if kept is not None else None
            if live == []:
                break
            d, _got, ctx = _decide(facts, False, fresh=kept is None, kept=kept)
            kept = ctx["world"]
            live = [t for t in tasks.load() if t["state"] in tasks.LIVE]
            if not live:
                break
            goal, step = tasks.goal_of(live[0]), ctx.get("step")
            direct, steps, side = _direct(kept, goal, step)
            if step is not None:
                _carry(kept[0], step)
            after = _direct(kept, goal, None)[0]
            rows.append({"name": d.name, "layer": d.layer, "step": step.key() if step is not None else None,
                         "forced": d.layer is not None and arbiter.SCALES[d.layer] < arbiter.SCALES["plan"],
                         "on_route": step is not None and step.key() in {s.key() for s in steps},
                         "side_s": side, "direct_s": direct, "after_s": after})
    finally:
        fresh_round()
    return rows


def _direct(kept, goal, step):
    """(seconds of the task's plan from the world's bag now, its steps, seconds `step` takes as run from here): the
    round's planner over the goal's needs (plan_round) on a Cost of this snapshot; None where nothing plans it."""
    from bonobo import api, decompose, planner
    from bonobo.cost import Cost
    from bonobo.game import TICKS_PER_S
    from bonobo.knowledge import SOURCE_BLOCKS
    from bonobo.survive import ROUND_GROUND
    from bonobo.world import Snapshot
    world, mem = kept
    with mock.patch.object(api, "api", world.api), mock.patch.object(api, "detail", lambda *a: None), \
            mock.patch.object(api, "log", lambda *a: None), mock.patch.object(api.STATE, "feet_seen", None):
        snap = Snapshot.read(SOURCE_BLOCKS, ROUND_GROUND)
        cost = Cost(snap, mem)
        tools = list(planner.from_bag(snap.inv, reserved=cost.reserved).tools)
        side = sum(planner.price_as_run([step], tools, cost)) / TICKS_PER_S if step is not None else 0.0
        try:
            _f, steps, secs = planner.plan_round(snap.inv, [planner.Target("task", decompose.round_needs(
                goal, snap.inv, cost))], cost)
        except planner.Unplannable:
            return None, [], side
    return secs, steps, side


def _carry(world, step):
    """The stub's bag as `step` leaves it (planner._After): its inputs out, its outputs in, a tool made carried."""
    from bonobo import planner
    from bonobo.data import TOOL_KINDS
    from bonobo.world import Inventory
    inv = Inventory({"slots": world.slots, "equipment": dict(world.equipment)})
    after = planner._After(inv, [step])
    tools = [s for s in world.slots if s.get("maxDamage")]
    held = {s["id"] for s in tools}
    slots = tools + [{"id": s["id"], "count": s["count"], "damage": 0} for s in after.slots if s["id"] not in held]
    slots += [{"id": item, "count": 1, "damage": 0, "maxDamage": uses}
              for kind in TOOL_KINDS for _t, uses, item in after.tools(kind)[len(inv.tools(kind)):]]
    world.slots = [dict(s, slot=i) for i, s in enumerate(slots)]


def _decide(facts, fail_then_again, fresh=True, hazard=False, kept=None):
    from bonobo import api, arbiter, brain, fight_loop, perception, planner, tape
    from bonobo.api import NotAvailable
    from bonobo import dispatch
    from bonobo.data import home_box_of
    from bonobo.memory import Memory
    from bonobo.knowledge import SOURCE_BLOCKS
    from bonobo.survive import ROUND_GROUND
    from bonobo.world import Inventory, Snapshot
    from .facts import alpha
    from .gamma import gamma
    if fresh:
        fresh_round()
    seen = {}
    real_arbitrate = arbiter.arbitrate

    def watched(intents, facts=None):
        chosen = real_arbitrate(intents, facts=facts)
        seen["intents"], seen["chosen"] = list(intents), chosen
        return chosen
    from .facts import DIMS
    mem = Memory() if kept is None else kept[1]
    world = gamma(facts, mem) if kept is None else kept[0]
    failure = next((d.failure(facts) for d in DIMS if hasattr(d, "failure")), None) \
        or NotAvailable("check: the step failed here")
    ctx: dict[str, Any] = {"world": (world, mem)}
    from bonobo import kernel
    weighed, held_log = [], []
    real_switches, real_held = kernel.switches, kernel.Held.decide

    def switches(fresh, staying, lost, noise=0.0):
        out = real_switches(fresh, staying, lost, noise)
        weighed.append((fresh, staying, lost, noise, out))      # D4: what the kernel weighed, as it weighed it
        return out

    def held_decide(self, model, state, now, holds=None):
        before = self.choice.name if self.choice is not None and self.choice.action is not None else None
        out = real_held(self, model, state, now, holds)
        held_log.append((before, getattr(out, "name", None), self.because))
        return out
    with mock.patch.object(api, "api", world.api), mock.patch.object(arbiter, "arbitrate", watched), \
            mock.patch.object(kernel, "switches", switches), mock.patch.object(kernel.Held, "decide", held_decide), \
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
        snap = Snapshot.read(SOURCE_BLOCKS, ROUND_GROUND)
        b.place = None
        b.policy_cache = b.policy(snap, snap.night)
        bctx = b.context(snap.dimension)
        b.needs.observe(snap)                  # as Brain._round_body: a tool worn out since last round is broken
        world.posts.clear()
        offered = []

        def offer(option, worth, key, now, release, held, seen_at):
            offered.append((option, worth))
            return ("tactic", key), None, {}
        bids = []
        real_bid = fight_loop.bid

        def bid(state, rows, price, work_s=None, now=None, ids=()):
            bids.append((state, rows, ids))          # what the threat layer was shown: S1 judges its pressure
            return real_bid(state, rows, price, work_s=work_s, now=now, ids=ids)
        with mock.patch.object(fight_loop, STUBBED[0].partition(".")[2], offer), mock.patch.object(fight_loop, "bid", bid):
            w = look(snap.state)
            got = alpha(snap, mem, world, b)       # the state judged, read before anything answers it
            w._answer_threats(snap.state)          # the threat layer's answer: TACTIC preempts the plan (K3)
        # danger as production judges it (S1 = R5's threat): the threat model's own pressure and blast on the state the
        # threat layer was shown (threat.options' test, not its answer: a wrong "ignore" is not taken as no danger)
        ctx["pressed"] = any(pressed(fight_loop.threat_state(s, rows, ids=ids)) for s, rows, ids in bids)
        from bonobo import tasks as tasklist
        live_before = {t["id"] for t in tasklist.load() if t["state"] in tasklist.LIVE}     # D1: what this round finishes
        from bonobo.planner import SPENT
        began, cut = SPENT["steps"], SPENT["budget"]
        calls, transport = [], api.api

        def counted(method, path, *rest, **kw):
            calls.append((method, path.split("?")[0]))
            return transport(method, path, *rest, **kw)
        with contextlib.ExitStack() as stack:
            if hazard:
                stack.enter_context(mock.patch.object(planner.Search, "advance", _hazard_at(ctx, world)))
            stack.enter_context(mock.patch.object(api, "api", counted))
            act = b.decide(snap, bctx)
        ctx["decide_calls"] = calls                              # K10: every jar call the decision made itself
        ctx["search_steps"] = SPENT["steps"] - began            # the round's own thinking, the checker's readings apart
        ctx["paths"] = list(planner.PATHS)      # each plan the round's searches took: its price as run, (g, h) on its way
        ctx["budget_spent"] = SPENT["budget"] > cut              # a search stopped by its budget this round
        ctx["body_read"], ctx["fact_ages"] = fact_ages(b, snap)
        if ctx.get("hazard_at") is not None:
            ctx["search_steps_after"] = SPENT["steps"] - ctx["hazard_at"]
        planned = plan_ctx(b, act, snap, mem, world, ctx["budget_spent"])     # read now: later readings move what is seen
        if offered:
            option, worth = offered[-1]
            d = Decision(layer="tactic", kind="threat", token=option.kind, target=getattr(option, "target", None),
                         writes=tuple(p for p, _b in world.posts), reason=None, name=f"threat:{option.kind}",
                         alternatives=(("tactic", option.kind, worth),))
            return d, got, {"step_kind": "threat", "switches": weighed, "holds": held_log, "decide_calls": calls}
        d = _decision(act, seen.get("chosen"), seen.get("intents", ()), world)
        if act is None:
            # D1: why nothing was proposed, where production records it — on the task (brain.finish: tasks.marked),
            # or the need no step could be planned for (brain.unplannable); a task this round finished (live before
            # it, done or failed after) is itself the round's reason (brain.py just_finished: that round proposes nothing)
            why = [t.get("reason") or (f"task {t['id']} {t['state']} this round"
                                       if t["id"] in live_before and t["state"] not in tasklist.LIVE else None)
                   for t in tasklist.load()] + list(b.unplannable.values()) + ([b.idle_why] if getattr(b, "idle_why", None) else [])
            d = d._replace(reason=next((w for w in why if w), None))
        step = getattr(act, "step", None)
        boxes = [tuple(map(tuple, bx)) for h in mem.homes(snap.dimension) for bx in h.get("boxes", ())]
        ctx["step_kind"] = "seek" if (d.name and d.name.startswith("seek:")) else (step.kind if step is not None else None)
        ctx["step"] = step
        ctx["target_in_home"] = d.target is not None and home_box_of(boxes, d.target) is not None
        found = dispatch.runner_for(bctx, step) if step is not None else None
        if found is not None and found[0].contract.commands is not None:
            # E1/E3 (check/inv/effects.py): the open-loop skill's batch as the door sends it (api.walk_only), built,
            # never posted; a closed-loop skill decides its tasks while it runs — no batch to judge (Unchecked)
            # (skillcore.body_state: what a runner builds them from; one needing a region read it runs itself)
            from bonobo.skillcore import body_state
            try:
                ctx["tasks"] = [api.walk_only(t) for t in found[0].contract.commands(body_state(bctx), tuple(found[1]))]
            except (KeyError, TypeError, AttributeError, api.McError) as e:     # McError: a station the batch needs is missing
                ctx["tasks_unbuilt"] = f"{type(e).__name__}: {e}"      # E1/E3 stay Unchecked for it, named
        if found is not None:
            # S5: the step's skill offered under production's fight line (an optional fight below it: why)
            ok, why = brain.fight_line_holds(found[0].contract, (bctx,) + tuple(found[1]), snap.state, snap.inv)
            if not ok:
                ctx["fight_line"] = why
        if snap.night:
            # asked only of an unsheltered body, as the shelter row asks it
            from bonobo.reflexes import ground
            enclosed, _soft, _site = ground(None, snap)
            if not b.reflexes.sheltered(snap, enclosed):
                way, _s, steps = b.needs.overnight(snap, bed_too=False)     # the round's own table, not priced again
                ctx["night_way"], ctx["night_steps"] = way, [st.key() for st in steps]
        ctx.update(planned, switches=weighed, holds=held_log)
        chosen = seen.get("chosen")
        if fail_then_again and act is not None and chosen is not None:
            # D5: the step fails here; the arbiter's gate drops an intent whose key is cooling (arbiter.viable) —
            # an intent with no key, or one the failure does not cool, is offered again in this same state
            # every way the step can fail here: this state's failure, each other cooling cause (check/dims/failure),
            # the same at a target (brain.failed cools there), an interruption (no failure: the step resumes)
            reselected = chosen.key is None
            for err in step_failures(failure):
                b.failed(act.name, err)
                # an interruption (brain.outcome_of: resumed, never counted) cools nothing: only a failure must
                reselected = reselected or (brain.outcome_of(err)[0] == "failed" and b.ready(chosen.key)
                                            and b.ready(act.name))
            ctx["reselected"] = reselected
    return d, got, ctx


def plan_ctx(b, act, snap, mem, world, spent):
    """The plan's invariants' readings (check/inv/plan.py), taken while the stub is the transport: the task's held plan,
    the production cost model's price of a step on this state, the bag and memory, and the ways to a mine target."""
    from bonobo.cost import Cost
    task = getattr(act, "task", None)
    held = b.held.get(task["id"]) if task is not None else None
    cost = Cost(snap, mem, b.blacklist, policy=b.policy_cache)
    for st in held["steps"] if held is not None else ():
        cost.estimate(st)          # warm the cache while the stub answers
    from bonobo.game import TICKS_PER_S
    from bonobo.planner import from_bag, price_as_run, run_prices
    tools = list(from_bag(snap.inv, reserved=cost.reserved).tools)
    out = {"plan": list(held["steps"]) if held is not None else None, "price": cost.estimate, "inv": snap.inv,
           "price_run": None,
           "mem": mem, "dimension": snap.dimension, "feet": snap.feet,
           "task_goal": task.get("goal") and {"goal": task["goal"], "args": task.get("args", {})} if task else None,
           "way": None, "plan_hand_made": bool(held is not None and held.get("hand_made")), "bound": None,
           "enroute": held.get("enroute") if held is not None else None}
    goal = out["task_goal"]
    from bonobo import goals
    look = held["steps"][0] if held is not None and len(held["steps"]) == 1 and held["steps"][0].kind == "look" \
        else None
    if held is not None and goal and goal["goal"] in goals.ITEM_GOALS:
        pending = mem.pending_outputs(snap.dimension)       # what the planner credits: the jobs running for us
        if look is not None and "p" in look.detail:    # a look first: its price, and with 1 − p the make anyway
            out["bound"] = look.est + (1 - look.detail["p"]) * plan_bound(snap.inv, [look.detail["need"]], cost, pending)
        else:
            out["bound"] = plan_bound(snap.inv, goals.needs(goal, snap.inv), cost, pending)
        if not out["plan_hand_made"] and look is None:
            names: list = []
            out["exact_s"], out["exact_unknown"] = exact_s(snap.inv, held.get("want"), goals.needs(goal, snap.inv), cost,
                                                           pending, names=names)
            if spent and out["exact_s"] is not None:
                # a budget-cut round is no violation: what the cut cost is the run's distribution (check.run)
                out["p5_loss_s"] = sum(int(getattr(s, "est", 0) or 0) for s in held["steps"]) / TICKS_PER_S - out["exact_s"]
                from bonobo.planner import plan_name
                chosen_s = sum(int(getattr(s, "est", 0) or 0) for s in held["steps"]) / TICKS_PER_S
                out["p5_case"] = (f"{plan_name(held['steps'])} ({chosen_s:.1f} s)",
                                  f"{names[0] if names else '?'} ({out['exact_s']:.1f} s)")
    out["candidates"] = candidates(task, snap, mem, cost) if held is not None else None
    # the plan and the chosen candidate priced as they run, now (D6): a later reading would see another world
    priced = {tuple(map(id, steps)): run_prices(list(steps), tools, cost)
              for steps in [out["plan"] or []] + [c[2] for c in (out["candidates"] or [])[:1]]}
    out["price_run"] = lambda steps: priced.get(tuple(map(id, steps))) or run_prices(list(steps), tools, cost)
    out["plan_switch"] = getattr(b, "plan_switch", None)
    from bonobo.planner import food_left_s
    out["food_left_s"] = food_left_s(cost)
    # M1: each withdrawal, production's chance now, the world's block there
    out["withdraws"] = [(tuple(st.detail["pos"]), st.token, st.detail.get("p"),
                         next((q for p, _i, _n, q in cost.stored(st.token) if tuple(p) == tuple(st.detail["pos"])), None),
                         world.blocks.get(tuple(st.detail["pos"]), "air"))
                        for st in (held["steps"] if held is not None else ()) if st.kind == "withdraw"]
    step = getattr(act, "step", None)
    pos = step.detail.get("pos") if step is not None and step.kind == "mine" else None
    if pos is not None:
        out["way"] = ways(snap, world, tuple(pos))
    return out


EXACT_STEPS = 5_000      # search steps the unpruned reference may take in one state; past them P5 is unknown


def exact_s(inv, want, needs, cost, pending=None, limit=EXACT_STEPS, names=None):
    """(seconds of the cheapest plan with no search budget for what the round planned, None; or None and why it is
    unknown): the reference stops after `limit` search steps."""
    import copy
    import json
    from bonobo import api, decompose
    from bonobo.planner import Target, Unplannable, plan_candidates, plan_name, plan_round
    steps, asked = [0], cost.stop or (lambda: False)

    def stop():
        steps[0] += 1
        return steps[0] > limit or asked()
    capped = copy.copy(cost)
    capped.stop = stop
    try:
        if want:
            targets = [Target(name, decompose.round_needs(json.loads(goal), inv, capped), rank)
                       for rank, (name, goal, *_left) in enumerate(want)]
            _first, steps, secs = plan_round(inv, targets, capped, pending, exact=True)
            if names is not None:
                names.append(plan_name(steps))      # the reference's ways, for the loss report
            return secs, None
        name, secs, _steps = plan_candidates(inv, needs, capped, exact=True)[0]
        if names is not None:
            names.append(name)
        return secs, None
    except Unplannable:
        return None, None
    except api.Interrupted:
        if steps[0] <= limit:
            raise
        return None, f"the unbudgeted search passed {limit} steps: its best is unknown"


def plan_bound(inv, needs, cost, pending=None, jobs=None):
    """Ticks no plan for `needs` from this bag can cost less than: the planner's search's own bound at its root."""
    from bonobo.planner import Node, Search, from_bag
    root = Node(from_bag(inv, pending, jobs, cost.reserved, cost.facts()), [], [])
    root.stack = [("tool", n[1], int(n[2]), 1, 0) if n[0] == "tool" else ("need", n[0], int(n[1]), 0, False)
                  for n in reversed(list(needs)) if n[0] not in ("fact", "do")]
    return Search(cost).h(root)


def candidates(task, snap, mem, cost):
    """planner.plan_candidates for the task's needs (the chosen first), or None."""
    from bonobo import goals, tasks
    from bonobo.planner import Unplannable, plan_candidates
    try:
        return plan_candidates(snap.inv, goals.needs(tasks.goal_of(task), snap.inv), cost,
                               mem.pending_outputs(snap.dimension))
    except Unplannable:
        return None


def ways(snap, world, target):
    """(the way plan_way takes, the cheapest dug way, the cheapest walk the game finds) to stand where `target` is
    mined, in seconds (None: no such way)."""
    from bonobo import nav
    from bonobo.world import Region
    feet = tuple(snap.feet)
    lo = tuple(min(a, b) - 4 for a, b in zip(feet, target))
    hi = tuple(max(a, b) + 4 for a, b in zip(feet, target))
    region = Region.of(lo, hi, dict(world._cells(lo, hi)))
    stands = [(target[0] + dx, target[1] + dy, target[2] + dz) for dx, dy, dz in
              ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0))]
    walks = nav.Walks(stands, 0.5)
    chosen = nav.plan_way(region, feet, target, "mine", snap.inv, set(), walks=walks)[2]
    dug = nav.plan_way(region, feet, target, "mine", snap.inv, set(), walks={})[2]
    found = [float(r["seconds"]) for c in stands if (r := walks[c]) and r.get("found") and r.get("seconds") is not None]
    return chosen, dug, min(found) if found else None
