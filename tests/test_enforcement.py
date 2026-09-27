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
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav  # noqa: E402

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")


def interrupt_writes(src):
    """Pure: assignments of a message (anything but None) to `api.INTERRUPT` in a module's source (its AST)."""
    return sum(1 for node in ast.walk(ast.parse(src)) if isinstance(node, ast.Assign)
               for t in node.targets if isinstance(t, ast.Attribute) and t.attr == "INTERRUPT"
               and isinstance(t.value, ast.Name) and t.value.id == "api"
               and not (isinstance(node.value, ast.Constant) and node.value.value is None))


def source(module):
    """A module's text. One reader, and it closes the file — ten bare `open()` calls left ten descriptors to the
    garbage collector, which is a warning in every run and a leak in none of nobody's control."""
    with open(os.path.join(PKG, module if module.endswith(".py") else module + ".py")) as f:
        return f.read()


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
                ("the last reading is stale (3 s): at rest", ((20.0, 64.0, 0.0), 3.0), (10.0, 64.0, 0.0),
                 (0.0, 0.0, 0.0))]

    def test_threats_carry_velocity(self):
        """A hazard row with a declared (0,0,0) makes every closed-form root infinite: velocity is differenced."""
        from bonobo import threat
        now = 1000.0
        for name, prev, pos, want in self.VELOCITY:
            with self.subTest(name):
                memory = {} if prev is None else {7: (prev[0], now - prev[1])}
                e = {"id": 7, "type": "minecraft:zombie", "x": pos[0], "y": pos[1], "z": pos[2]}
                got = threat.rows([e], memory, now, {"minecraft:zombie": 3.0})
                self.assertEqual(len(got), 1)
                self.assertEqual(tuple(round(v, 6) for v in got[0][2]), want)
                self.assertEqual(memory[7], (pos, now), "this reading is the next round's baseline")

    # fixture: (module source) → how many assignments write a message (not None) into api.INTERRUPT
    WRITES = [("a message written", "api.INTERRUPT = 'stop'\n", 1),
              ("must fail: cleared with None: not a writer", "api.INTERRUPT = None\n", 0),
              ("two writes in one function", "def f():\n    api.INTERRUPT = x\n    api.INTERRUPT = 'y'\n", 2),
              ("another module's INTERRUPT is not api's", "other.INTERRUPT = 'x'\n", 0),
              ("reading it is not writing it", "x = api.INTERRUPT\n", 0)]

    def test_the_writer_count_over_the_fixture(self):
        for name, src, want in self.WRITES:
            with self.subTest(name):
                self.assertEqual(interrupt_writes(src), want)

    def test_the_interrupt_message_has_one_writer(self):
        writers = {m: n for m in sorted(f[:-3] for f in os.listdir(PKG) if f.endswith(".py"))
                   if (n := interrupt_writes(source(m)))}
        # perception may still hand a message to a soft skill without stopping it; every stop-and-tell goes
        # through the arbiter. scenarios is the bench, which plays the interrupting player.
        self.assertEqual(sorted(set(writers) - {"perception", "scenarios"}), ["arbiter"], f"writers: {writers}")
        self.assertLessEqual(writers.get("perception", 0), 1)

    # Every funnel that drives the body asks the arbiter, by name, before the game hears of it.
    # (situation, the call, does the arbiter let it drive?) → (what it asked, reached the game, what came back)
    FUNNELS = [
        ("post /task, the owner", lambda: api.post("/task", {}), True, ["api.post(/task)"], True, {"status": "sent"}),
        ("post /stop, the owner", lambda: api.post("/stop", {}), True, ["api.post(/stop)"], True, {"status": "sent"}),
        ("post /task, not the owner: refused (run_chain posts it directly)", lambda: api.post("/task", {}), False,
         ["api.post(/task)"], False, POST_REFUSED),
        ("post /stop?wait=1, not the owner: the query is not part of the name", lambda: api.post("/stop?wait=1", {}),
         False, ["api.post(/stop)"], False, POST_REFUSED),
        ("post /close: not the body's, never asks", lambda: api.post("/close", {}), False, [], True, {"status": "sent"}),
        ("post /click: likewise", lambda: api.post("/click", {}), False, [], True, {"status": "sent"}),
        ("run a mine task, not the owner: a failed result, not a crash", lambda: api.run({"type": "mine"}, awaits="the funnel test: refused or run"), False,
         ["api.run(mine)"], False, {"status": "failed", "type": "mine", "message": "body owned by the arbiter",
                                    "seconds": 0}),
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

    def test_danger_kinds_and_the_recovery_table_agree(self):
        # Perception emits kinds; the table keys on them exactly. A kind with no row falls to the default, which
        # is allowed; a row for a kind perception can never emit is a dead entry pretending to be a rule.
        from bonobo import perception, recovery
        emitted = set(perception.DANGERS) | {"airborne", "stale"}       # the two non-perception triggers
        for kind, _, _ in recovery.TABLE:
            self.assertIn(kind, emitted, f"recovery row {kind!r} can never fire")
        # Every kind danger() can say, swept: each is a declared kind (a row can key on it).
        base = {"health": 20, "food": 20, "control": {}, "air": 300, "onGround": True,
                "dimension": "minecraft:overworld"}
        near = lambda d: (lambda r: d)      # noqa: E731
        rows = [({}, {}, None), ({"inLava": True}, {}, "lava"), ({"onFire": True, "health": 6}, {}, "burning"),
                ({"inWater": True, "air": 40, "onGround": False}, {}, "drowning"),
                ({}, {"buried": True}, "suffocating"), ({"onGround": False, "y": 60}, {"fallen": 6.0}, "falling"),
                ({"health": 3}, {}, "critical_health"),
                ({"dimension": "minecraft:the_end"}, {"breath_within": near(True)}, "breath"),
                ({}, {"enderman_after_us": near(True)}, "enderman"),
                ({"health": 9}, {"hostiles_within": near(4.0)}, "hostiles"),
                ({"health": 9}, {"hostiles_within": near(9.0)}, None),
                ({"dead": True, "inLava": True}, {}, None),
                ({"inLava": True, "control": {"paused": True}}, {}, None)]       # the player holds control
        for st, kw, want in rows:
            with self.subTest(state=st, **{k: True for k in kw}):
                got = perception.danger({**base, **st}, **kw)
                self.assertEqual(got, want)
                self.assertTrue(got is None or got in perception.DANGERS)


class TheSafetyLayerStopsTheBody(unittest.TestCase):
    """The message (INTERRUPT) is perception's; the command (/stop) is the arbiter's — two direct stops were two of
    the commanders a multi-threat fight cannot afford. A recovery is the safety layer speaking too: it owns the
    body while it runs, never walks inline. Both are run here over a recording arbiter."""

    RUNNING = {"active": True, "paused": False, "allowed": True, "task": {"type": "mine"}, "queued": 0}
    # One perception tick. (situation, the state read, a soft skill running?, Claude's flag text) →
    # (preemptions (layer, why), the message left in api.INTERRUPT)
    TICKS = [("lava under a running task: a safety preemption", dict(inLava=True, control=RUNNING), False, None,
              [("safety", "lava")], None),
             ("Claude asks: preempted with the reason", dict(control=RUNNING), False, "look at this",
              [("safety", "claude: look at this")], None),
             ("lava under a soft skill: the message only, the skill takes cover itself",
              dict(inLava=True, control=RUNNING), True, None, [], "lava"),
             ("must fail: lava, nothing running: nothing to stop", dict(inLava=True), False, None, [], None),
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
                            (perception, "FLAG", {"new": path}), (perception, "PAUSED", {"new": False}),
                            (perception, "_eating", {"return_value": False}), (perception, "note_hurt", {}),
                            (api, "MODE", {"new": "normal"}), (api, "SOFT", {"new": soft}),
                            (api, "INTERRUPT", {"new": None}), (api, "log", {}),
                            (api, "get", {"return_value": state(**changes)}),
                            (api, "post", {"side_effect": lambda p, b=None: posts.append(p)}),
                            (arbiter, "BODY", {"new": body}), (w, "_look", {}), (w, "_answer_threats", {}),
                            (w, "_time_to_die", {"return_value": None}),
                            (w, "_enderman_after_us", {"return_value": False}),
                            (w, "_breath_within", {"return_value": False}),
                            (w, "hazard", {"new": types.SimpleNamespace(buried=lambda s: False, fallen=lambda s: 0.0)})]:
                        stack.enter_context(mock.patch.object(target, attr, **kw))
                    w.run()
                    self.assertEqual((body.preempted, api.INTERRUPT, posts), (want_pre, want_msg, []),
                                     "perception never posts the /stop itself")
                    if want_pre:
                        body.action()
                        self.assertEqual(posts, ["/stop"], "the preemption's action is the /stop")

    # (the interrupt reason) → the recovery carried out (recovery.TABLE, else its default)
    RECOVERIES = [("enderman", "shake_enderman"), ("claude: breath", "retreat_to_cover"),
                  ("critical_health", "retreat_and_eat"), ("airborne", "water_clutch"),
                  ("a kind nobody listed: cover, never carry on", "retreat_to_cover")]

    def test_a_recovery_is_a_safety_preemption(self):
        from unittest import mock
        from bonobo import arbiter, end
        for reason, want in self.RECOVERIES:
            ran, body = [], Recorder()
            with self.subTest(reason), mock.patch.object(arbiter, "BODY", body), mock.patch.object(end, "log"), \
                    mock.patch.object(end, "_recover_body", side_effect=lambda ctx, act: ran.append((ctx, act))):
                self.assertEqual(end._recover("ctx", reason), want)
                self.assertEqual((body.preempted, ran), ([("safety", f"{want}: {reason}")], []),
                                 "nothing drives the body inline")
                body.action()
                self.assertEqual(ran, [("ctx", want)])


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
        from bonobo import world
        for name, task, st, want in self.VET:
            with self.subTest(name), mock.patch.object(api, "get", return_value=st), \
                    mock.patch.object(world, "entities", return_value=list(ENDERMEN)):
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

    LAYERS = ("hazard", "upkeep", "queue", "prepare")
    # (which layers have something to do) → the layers asked, in order, and the one that took the round
    ROWS = [(set(), ["hazard", "upkeep", "queue", "prepare"], None),
            ({"prepare"}, ["hazard", "upkeep", "queue", "prepare"], "prepare"),
            ({"queue", "prepare"}, ["hazard", "upkeep", "queue"], "queue"),
            ({"upkeep", "queue"}, ["hazard", "upkeep"], "upkeep"),
            ({"hazard", "upkeep", "queue", "prepare"}, ["hazard"], "hazard")]

    def test_decide_asks_the_layers_in_their_fixed_order(self):
        from unittest import mock
        from bonobo import api, brain, retry, tasks
        from bonobo.world import Snapshot
        snap = Snapshot.from_readings({"dimension": "minecraft:overworld", "timeOfDay": 2000},
                                      {"slots": [], "equipment": {}})
        for busy, want_asked, want_taker in self.ROWS:
            asked = []

            def layer(name, result):
                def ask(*a, **k):
                    asked.append(name)
                    return result if name in busy else None
                return ask
            b = brain.Brain.__new__(brain.Brain)
            b.retry, b.place = retry.Retry(), None
            ask_upkeep = layer("upkeep", [(0, "u", None)])
            b.needs = type("Needs", (), {"working": {}, "needs_now": [], "round": {},
                                         "propose": lambda self, *a, **k: None})()
            b.reflexes = type("Reflexes", (), {"proposals": lambda self, *a, **k: ask_upkeep() or []})()
            ask_queue = layer("queue", brain.Act("task", "t", None))
            b.task_act = lambda *a: (ask_queue(*a), {})
            b.mem, b.blacklist, b.policy_cache = None, {}, None
            b.prepare = layer("prepare", brain.Act("idle", "p", None))
            with self.subTest(busy=sorted(busy)), mock.patch.object(api, "MODE", "normal"), \
                    mock.patch.object(brain.hazard, "due", layer("hazard", "drowning")), \
                    mock.patch.object(tasks, "load", return_value=[{"id": "t1", "state": "pending"}]), \
                    mock.patch.object(tasks, "expire", return_value=False):
                act = b.decide(snap, None)
                self.assertEqual(asked, want_asked)
                taker = None if act is None else {"L0": "hazard", "upkeep": "upkeep", "task": "queue",
                                                  "idle": "prepare"}[act.layer]
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
