"""Rules must be enforced, not merely written.

Every failure in this fight so far had one shape: a rule existed as a pure function, a unit test and a sentence of
documentation — none of which requires a call site. So "the rule exists" and "the rule runs" were independent, and
five separate rules turned out to be written and never wired: the bunker geometry, the threat model, the planner
itself (in shadow mode), the recovery table, and the enderman aim check.

Unit tests cannot catch this, because the unit passes. These tests read the call graph instead: for each rule, is
there a path from the code that actually executes to the function that enforces it?
"""
import ast
import contextlib
import pathlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav  # noqa: E402
from tests.world import round_ctx  # noqa: E402
from tests.world import brain_fixture  # noqa: E402

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")


def interrupt_writes(src):
    """Pure: writes of a message (anything but None) into the interrupt in a module's source (its AST): a call of
    `api.request_interrupt(msg)`, the arbiter's `WIRE["tell"](msg)`, or an assignment to `api.STATE.interrupt`."""
    def message(v):
        return not (isinstance(v, ast.Constant) and v.value is None)
    n = 0
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "request_interrupt" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "api" and node.args and message(node.args[0]):
            n += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Subscript) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "WIRE" \
                and isinstance(node.func.slice, ast.Constant) and node.func.slice.value == "tell" \
                and node.args and message(node.args[0]):
            n += 1          # the arbiter's wire to api.request_interrupt (arbiter never imports api)
        elif isinstance(node, ast.Assign) and message(node.value):
            n += sum(1 for t in node.targets if isinstance(t, ast.Attribute) and t.attr == "interrupt"
                     and isinstance(t.value, ast.Attribute) and t.value.attr == "STATE"
                     and isinstance(t.value.value, ast.Name) and t.value.value.id == "api")
    return n


# Modules that execute: they talk to the game or decide what to do. A rule enforced only in a module that never
# runs during play is not enforced.
# `api` belongs here: it is the funnel every task passes through, and therefore the right place for guards that
# must not be opt-in. Leaving it out made this test blind to exactly the fix it asks for.
# `api` is the funnel every task passes through; `fight_plan` decides once per fight round. Both are execution,
# and leaving either out makes this check blind to the wiring it exists to guard.
POST_REFUSED = {"status": "failed", "message": "body owned by the arbiter", "tasks": []}


class Recorder:
    """An arbiter that records each preemption (layer, why) and keeps the action, to be run by the test."""

    def __init__(self):
        self.preempted, self.action = [], None

    def preempt(self, layer, action, why, **kw):
        self.preempted.append((layer, why))
        self.action = action


def _raised(call):
    """The exception class name `call` raised, or its value."""
    try:
        return call()
    except Exception as e:
        return type(e).__name__


class RulesAreWired(unittest.TestCase):
    # The wiring rules (planner, bunker, threat model, safe step, bids, interrupts, the answer loop, rows) are
    # shown by behaviour in tests/test_wiring.py.

    # (the entity's last reading (pos, seconds ago) or None, now) → the velocity its row carries
    VELOCITY = [("never seen before: at rest", None, (10.0, 64.0, 0.0), (0.0, 0.0, 0.0)),
                ("moved 2 blocks toward us in 1 s", ((12.0, 64.0, 0.0), 1.0), (10.0, 64.0, 0.0), (-2.0, 0.0, 0.0)),
                ("fell a block in half a second", ((10.0, 65.0, 0.0), 0.5), (10.0, 64.0, 0.0), (0.0, -2.0, 0.0)),
                ("must fail: the last reading is stale (3 s): at rest", ((20.0, 64.0, 0.0), 3.0), (10.0, 64.0, 0.0),
                 (0.0, 0.0, 0.0))]

    def test_threats_carry_velocity(self):
        """A hazard row with a declared (0,0,0) makes every closed-form root infinite: velocity is differenced."""
        from bonobo import threat
        now = 1000.0
        for name, prev, pos, want in self.VELOCITY:
            with self.subTest(name):
                memory = {} if prev is None else {7: (prev[0], now - prev[1])}
                e = {"id": 7, "type": "minecraft:zombie", "x": pos[0], "y": pos[1], "z": pos[2]}
                got = threat.threat_rows([e], memory, now, {"minecraft:zombie": 3.0}, (0.0, 64.0, 0.0))
                self.assertEqual(len(got), 1)
                self.assertEqual(tuple(round(v, 6) for v in got[0][2]), want)
                self.assertEqual(memory[7], (pos, now), "this reading is the next round's baseline")

    # fixture: (module source) → how many assignments write a message (not None) into api.STATE.interrupt
    WRITES = [("a message written", "api.request_interrupt('stop')\n", 1),
              ("the field written directly", "api.STATE.interrupt = 'stop'\n", 1),
              ("must fail: cleared with None: not a writer", "api.request_interrupt(None)\n", 0),
              ("must fail: the field cleared with None", "api.STATE.interrupt = None\n", 0),
              ("the arbiter's wire", "WIRE['tell'](reason)\n", 1),
              ("must fail: another wire is not the message", "WIRE['stop']()\n", 0),
              ("two writes in one function", "def f():\n    api.request_interrupt(x)\n    api.request_interrupt('y')\n", 2),
              ("another module's interrupt is not api's", "other.request_interrupt('x')\nother.STATE.interrupt = 'x'\n", 0),
              ("reading it is not writing it", "x = api.interrupt_pending()\ny = api.STATE.interrupt\n", 0)]

    def test_the_writer_count_over_the_fixture(self):
        for name, src, want in self.WRITES:
            with self.subTest(name):
                self.assertEqual(interrupt_writes(src), want)

    def test_the_interrupt_message_has_one_writer(self):
        writers = {p.stem: n for p in sorted(pathlib.Path(PKG).glob("*.py")) if (n := interrupt_writes(p.read_text()))}
        # perception may still hand a message to a soft skill without stopping it; every stop-and-tell goes
        # through the arbiter (the bench, which plays the interrupting player, lives in bonobo/bench).
        self.assertEqual(sorted(set(writers) - {"perception"}), ["arbiter"], f"writers: {writers}")
        self.assertLessEqual(writers.get("perception", 0), 1)

    # Every funnel that drives the body asks the arbiter, by name, before the game hears of it.
    # (situation, the call, does the arbiter let it drive?) → (what it asked, reached the game, what came back)
    FUNNELS = [
        ("post /task, the owner", lambda: api.post("/task", {}), True, ["api.post(/task)"], True, {"status": "sent"}),
        ("post /stop, the owner", lambda: api.post("/stop", {}), True, ["api.post(/stop)"], True, {"status": "sent"}),
        ("must fail: post /task, not the owner: refused (run_chain posts it directly)", lambda: api.post("/task", {}), False,
         ["api.post(/task)"], False, POST_REFUSED),
        ("post /stop?wait=1, not the owner: the query is not part of the name", lambda: api.post("/stop?wait=1", {}),
         False, ["api.post(/stop)"], False, POST_REFUSED),
        ("post /close: not the body's, never asks", lambda: api.post("/close", {}), False, [], True, {"status": "sent"}),
        ("post /click: likewise", lambda: api.post("/click", {}), False, [], True, {"status": "sent"}),
        ("run a mine task, not the owner: FightHolds, like run_chain and a walk (never a failed try a loop counts)",
         lambda: _raised(lambda: api.run({"type": "mine"}, awaits="the funnel test: refused or run")), False,
         ["api.run(mine)"], False, "FightHolds"),
        ("walk, not the owner: no walk, the interruption raised (never False: a False banned the target)",
         lambda: _raised(lambda: nav.go_to((5, 64, 5), None)), False, ["nav.go_to"], False, "FightHolds")]

    def test_every_funnel_asks_who_owns_the_body(self):
        from unittest import mock
        from bonobo import arbiter
        for name, call, owns, want_asked, reached, want in self.FUNNELS:
            asked = []
            with self.subTest(name), \
                    mock.patch.object(arbiter.BODY, "owns", side_effect=lambda who, o=owns: asked.append(who) or o), \
                    mock.patch.object(api, "api", return_value={"status": "sent"}) as wire:
                self.assertEqual(call(), want)
                self.assertEqual((asked, wire.called), (want_asked, reached))

class TheSafetyLayerStopsTheBody(unittest.TestCase):
    """The message (INTERRUPT) is perception's; the command (/stop) is the arbiter's — two direct stops were two of
    the commanders a multi-threat fight cannot afford. It is run here over a recording arbiter."""

    RUNNING = {"active": True, "paused": False, "allowed": True, "task": {"type": "mine"}, "queued": 0}
    # One perception tick. (situation, the state read, a soft skill running?, Claude's flag text) →
    # (preemptions (layer, why), the message left in api.STATE.interrupt)
    TICKS = [("lava under a running task: a safety preemption", dict(inLava=True, control=RUNNING), False, None,
              [("safety", "lava")], None),
             ("Claude asks: preempted with the reason", dict(control=RUNNING), False, "look at this",
              [("safety", "claude: look at this")], None),
             ("lava under a soft skill: the message only, the skill takes cover itself",
              dict(inLava=True, control=RUNNING), True, None, [], "lava"),
             # S1: no task, a plan being searched: the arbiter, the one writer, stops it too
             ("must fail: lava, nothing running: the round's planning stopped by the arbiter", dict(inLava=True), False,
              None, [("safety", "lava")], None),
             ("all well, nothing running: nothing", dict(), False, None, [], None),
             ("all well under a running task: nothing", dict(control=RUNNING), False, None, [], None)]

    def test_perception_stops_only_through_the_arbiter(self):
        import tempfile
        import types
        from unittest import mock
        from bonobo import arbiter, perception
        from tests.world import state
        for name, changes, soft, flag, want_pre, want_msg in self.TICKS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                w, body, posts = perception.Watcher(), Recorder(), []
                path = os.path.join(tmp, "interrupt")
                if flag:
                    with open(path, "w") as f:
                        f.write(flag)

                def one_tick(_s, w=w):
                    w.stopped = True
                # One ExitStack, not one `with` of twenty patches: Python caps statically nested blocks at 20.
                with contextlib.ExitStack() as stack:
                    for target, attr, kw in [
                            (perception.time, "sleep", {"side_effect": one_tick}),
                            (perception.fight_loop, "active", {"return_value": False}),
                            (perception, "FLAG", {"new": path}), (perception.STATE, "paused", {"new": False}),
                            (perception, "_eating", {"return_value": False}), (perception, "note_hurt", {}),
                            (api.STATE, "mode", {"new": "normal"}), (api.STATE, "soft", {"new": soft}),
                            (api.STATE, "interrupt", {"new": None}), (api, "log", {}),
                            (api, "get", {"return_value": state(**changes)}),
                            (api, "post", {"side_effect": lambda p, b=None: posts.append(p)}),
                            (arbiter, "BODY", {"new": body}), (w, "_look", {}), (w, "_answer_threats", {}),
                            (w, "_time_to_die", {"return_value": None}),
                            (w, "_enderman_after_us", {"return_value": False}),
                            (w, "_breath_within", {"return_value": False}),
                            (w, "hazard", {"new": types.SimpleNamespace(buried=lambda s: False, fallen=lambda s: 0.0)})]:
                        stack.enter_context(mock.patch.object(target, attr, **kw))
                    w.run()
                    self.assertEqual((body.preempted, api.STATE.interrupt, posts), (want_pre, want_msg, []),
                                     "perception never posts the /stop itself")
                    if want_pre:
                        body.action()
                        self.assertEqual(posts, ["/stop"], "the preemption's action is the /stop")

ENDERMEN = [{"type": "minecraft:enderman", "id": 7, "x": 6, "y": 64, "z": 0, "angry": True},
            {"type": "minecraft:enderman", "id": 8, "x": 3, "y": 64, "z": 0},
            {"type": "minecraft:end_crystal", "id": 9, "x": 0, "y": 80, "z": 20}]


class EveryLookIsVetted(unittest.TestCase):
    """Looking at an enderman provokes it: the task funnel warns of such an aim, the bow refuses one. Standing at
    (0, 64, 0), endermen at 3 and 6 blocks east; a rising line to (12, 69, 0) crosses the head band."""

    END = {"dimension": "minecraft:the_end", "x": 0, "y": 64, "z": 0}
    # (situation, task, the state read) → the funnel's warning
    VET = [("a look through an enderman's head, in the End", {"type": "look", "x": 12, "y": 69, "z": 0}, END,
            "aim at 12,69,0 crosses an enderman's head"),
           ("the same direction below the head", {"type": "look", "x": 12, "y": 64, "z": 0}, END, None),
           ("must fail: the same aim in the Overworld: no endermen to vet for",
            {"type": "look", "x": 12, "y": 69, "z": 0}, dict(END, dimension="minecraft:overworld"), None),
           ("a task that does not aim", {"type": "goto", "x": 12, "y": 69, "z": 0}, END, None),
           ("an aiming task with no place to aim at", {"type": "attack", "entity": 7}, END, None)]

    def test_the_funnel_warns_of_a_provoking_aim(self):
        from unittest import mock
        for name, task, st, want in self.VET:
            def got(path, st=st):
                return {"entities": list(ENDERMEN)} if path.startswith("/entities") else st
            with self.subTest(name), mock.patch.object(api, "get", side_effect=got):
                self.assertEqual(api.vet_aim(task), want)

    # (situation, the target, the entities handed in as `near`) → shot (else refused)
    SHOOT = [("must fail: through an enderman's head: refused", {"id": 1, "x": 12, "y": 69, "z": 0}, ENDERMEN, False),
             ("level, below the heads: shot", {"id": 1, "x": 12, "y": 64, "z": 0}, ENDERMEN, True),
             ("the crystal, well clear of them: shot", ENDERMEN[2], ENDERMEN, True),
             ("through a head, but nothing handed in to vet against: shot", {"id": 1, "x": 12, "y": 69, "z": 0},
              None, True)]

    def test_the_bow_refuses_a_provoking_aim(self):
        from unittest import mock
        from bonobo import combat
        for name, target, near, shot in self.SHOOT:
            with self.subTest(name), mock.patch.object(api, "get", return_value=self.END), \
                    mock.patch.object(api, "run", return_value={"status": "succeeded"}) as run:
                if shot:
                    combat.shoot(target, near=near)
                else:
                    with self.assertRaisesRegex(api.NotAvailable, "enderman stands in the line of aim"):
                        combat.shoot(target, near=near)
                self.assertEqual([c.args[0]["item"] for c in run.call_args_list], ["minecraft:bow"] if shot else [])


class OneDecisionPoint(unittest.TestCase):
    """A fixed order, no scores: the first layer that has something to do takes the round (brain.py docstring).
    Asked of `Brain.decide` itself, with each layer replaced by a recorder that says whether it has work."""

    LAYERS = ("hazard", "upkeep", "queue")
    # (which layers have something to do) → the layers asked, in order, and the one that took the round
    ROWS = [(set(), ["hazard", "upkeep", "queue"], None),  # must fail: nothing to do, no layer takes the round
            ({"queue"}, ["hazard", "upkeep", "queue"], "queue"),
            ({"upkeep", "queue"}, ["hazard", "upkeep"], "upkeep"),
            ({"hazard", "upkeep", "queue"}, ["hazard"], "hazard")]

    # a fight row's round (Brain.round(plan=False)): reflexes and safety only — the queue never asked
    # (combat__low_hp_eat mined coal for 14 s after the fight)
    NO_PLAN = [({"queue"}, ["hazard", "upkeep"], None),   # must fail: plan work would take the round
               ({"upkeep", "queue"}, ["hazard", "upkeep"], "upkeep"),
               ({"hazard", "queue"}, ["hazard"], "hazard")]

    def test_decide_asks_the_layers_in_their_fixed_order(self):
        self.over(self.ROWS, planning=True)

    def test_a_round_without_the_plan_layer(self):
        self.over(self.NO_PLAN, planning=False)

    def over(self, rows, planning):
        from unittest import mock
        from bonobo import api, brain, retry, tasks
        from tests.world import inventory, snapshot, state
        snap = snapshot(state(timeOfDay=2000), inventory())          # the round's read: its ground too
        for busy, want_asked, want_taker in rows:
            asked = []

            def layer(name, result):
                def ask(*a, **k):
                    asked.append(name)
                    return result if name in busy else None
                return ask
            b = brain_fixture()
            b.unplannable = {}
            b.abandoned = None
            b.retry, b.place, b.planning = retry.Retry(), None, planning
            ask_upkeep = layer("upkeep", [(0, "u", None)])
            b.needs = type("Needs", (), {"working": {}, "needs_now": [], "round": {},
                                         "propose": lambda self, *a, **k: None})()
            b.reflexes = type("Reflexes", (), {"afloat": False,
                                               "proposals": lambda self, *a, **k: ask_upkeep() or [],
                                               "terms": {"u": ("S8", None)}})()
            # a queue act names its step, as craft_act makes every one (brain.act_on_surface reads it)
            from bonobo.planner import Step
            ask_queue = layer("queue", brain.Act("task", "t", None, step=Step("craft", "minecraft:stick", 4, {})))
            # the round's one plan (brain.round_for) stands in; the task's act is asked of it
            b.round_for = lambda entries, snap, cost, old=None: {"steps": [], "sig": None, "event": False,
                                                                 "dim": snap.dimension, "want": ()}
            b.task_act = lambda *a: (ask_queue(*a), {})
            b.mem, b.blacklist, b.policy_cache, b.held = None, {}, None, {}
            with self.subTest(busy=sorted(busy)), mock.patch.object(api.STATE, "mode", "normal"), \
                    mock.patch.object(brain.hazard, "rescue_due", layer("hazard", "drowning")), \
                    mock.patch.object(tasks, "load", return_value=[{"id": "t1", "state": "running", "goal": "have",
                                                                    "args": {"needs": [["log", 2]]}}]), \
                    mock.patch.object(tasks, "expire", return_value=False):
                act = b.decide(snap, round_ctx(b, snap))
                self.assertEqual(asked, want_asked)
                taker = None if act is None else {"L0": "hazard", "upkeep": "upkeep", "task": "queue"}[act.layer]
                self.assertEqual(taker, want_taker)


class SafetyIsNotOptIn(unittest.TestCase):
    """A guard that each call site must remember to ask for is a guard whose coverage decays."""

    def test_guards_are_on_by_default(self):
        """A guard each caller must remember to ask for is a guard whose coverage decays: the safe value is the
        default, stated exactly."""
        import inspect
        from bonobo import api, nav, skillcore
        rows = [("every walk guards health", nav.go_to, "min_hp", nav.MIN_WALK_HP),
                ("every walk avoids hazards", nav.go_to, "avoid_hazards", True),
                ("every wait for the world yields to an interrupt", skillcore.settle, "soft", False),
                ("every interrupt check applies to hard work", api.check_interrupt, "soft", False)]
        for name, fn, param, want in rows:
            with self.subTest(name):
                self.assertEqual(inspect.signature(fn).parameters[param].default, want)


if __name__ == "__main__":
    unittest.main()
