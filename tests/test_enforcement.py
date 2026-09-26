"""Rules must be enforced, not merely written.

Every failure in this fight so far had one shape: a rule existed as a pure function, a unit test and a sentence of
documentation — none of which requires a call site. So "the rule exists" and "the rule runs" were independent, and
five separate rules turned out to be written and never wired: the bunker geometry, the threat model, the planner
itself (in shadow mode), the recovery table, and the enderman aim check.

Unit tests cannot catch this, because the unit passes. These tests read the call graph instead: for each rule, is
there a path from the code that actually executes to the function that enforces it?
"""
import ast
import os
import unittest

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")


def source(module):
    """A module's text. One reader, and it closes the file — ten bare `open()` calls left ten descriptors to the
    garbage collector, which is a warning in every run and a leak in none of nobody's control."""
    with open(os.path.join(PKG, module if module.endswith(".py") else module + ".py")) as f:
        return f.read()


def call_graph():
    """{module: {called names}} across the package — attribute calls included (`combat.shoot` → `shoot`)."""
    graph = {}
    for name in sorted(os.listdir(PKG)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(source(name))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    called.add((a.asname or a.name).split(".")[-1])
        graph[name[:-3]] = called
    return graph


GRAPH = call_graph()


def _function(module, func):
    tree = ast.parse(source(module))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)


def calls_in(module, func=None, lambdas=False):
    """[ast.Call] made directly by `module.func` (or the whole module): the call graph at one function's grain.
    Calls inside a lambda are the lambda's (handed on to be run by someone else) unless `lambdas`."""
    root = _function(module, func) if func else ast.parse(source(module))
    out, stack = [], [root]
    while stack:
        node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Lambda) and not lambdas:
                continue
            if isinstance(child, ast.Call):
                out.append(child)
            stack.append(child)
    return out


def name_of(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def first_arg(call):
    return call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else None

# Modules that execute: they talk to the game or decide what to do. A rule enforced only in a module that never
# runs during play is not enforced.
# `api` belongs here: it is the funnel every task passes through, and therefore the right place for guards that
# must not be opt-in. Leaving it out made this test blind to exactly the fix it asks for.
# `api` is the funnel every task passes through; `fight_plan` decides once per fight round. Both are execution,
# and leaving either out makes this check blind to the wiring it exists to guard.
EXECUTING = {"end", "combat", "brain", "skills", "nav", "perception", "skillcore", "skill", "nether", "api",
             "fight_plan", "threat", "hazard", "fight_loop"}


def callers_of(func, among=EXECUTING):
    return {m for m in among if func in GRAPH.get(m, ())}


class RulesAreWired(unittest.TestCase):
    def test_the_planner_is_called_by_the_fight(self):
        self.assertIn("end", callers_of("plan"), "the fight must ask the planner, not hold its own order")

    def test_the_recovery_table_is_consulted(self):
        # Not `_recover` — that is our own wrapper. The table itself must be reached.
        wired = callers_of("explain") | callers_of("recovery_for")
        self.assertTrue(wired, "the recovery table is written but nothing looks anything up in it")

    def test_the_bunker_geometry_is_used_by_the_fight(self):
        self.assertIn("end", callers_of("mouth") | callers_of("tunnel") | callers_of("dig_plan"),
                      "bunker.py is geometry nobody digs")

    def test_the_threat_model_is_used_by_something_that_runs(self):
        # combat_model computes time-to-impact and phase statistics. If only the offline report imports it, the
        # fight is not using the model the design is built on — and the documentation says otherwise.
        self.assertTrue(callers_of("threats") | callers_of("tti") | callers_of("exposure")
                        | callers_of("window_summary"),
                        "combat_model is documented as part of the fight but no executing module calls it")

    def test_every_look_is_vetted_for_endermen(self):
        # The rule says looking at an enderman's head provokes it, so aims are checked. If only the bow checks,
        # then digging, walking, placing and bombing all look wherever they like — which is what happened.
        callers = callers_of("aim_hits_enderman")
        self.assertGreater(len(callers), 1,
                           f"only {callers or 'nothing'} checks its aim; every other action looks freely")

    def test_the_safety_choice_is_made_by_the_model(self):
        # best_step / slack_at are the closed-form safety rule. They have been written, deleted and rewritten;
        # what keeps failing is not the algorithm but its call site.
        self.assertTrue(callers_of("best_step") | callers_of("slack_at") | callers_of("min_tti"),
                        "nothing on the execution path asks the model where it is safe to stand")

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
        self.assertIn("rows", GRAPH["end"], "the fight builds its rows with the shared differencing")

    def test_ordinary_play_asks_the_threat_layer(self):
        # "hostile within 5 → attack, hp ≤ 10 and within 6 → flee" was two literals pretending to be a policy. The
        # answer comes from the threat model, and from ONE place: perception, at its own cadence. The brain had a
        # second copy that ran once a round, could choose `ignore` as though doing nothing were a rescue, and held
        # the body while the real answer waited for a lease. That cost a death.
        self.assertIn("perception", callers_of("bid") | callers_of("options"),
                      "nothing bids for the body when something is hitting us")
        self.assertNotIn("brain", callers_of("decide"), "the brain answers threats again: one decider, not two")
        self.assertIn("perception", callers_of("pressure") | callers_of("time_to_die"),
                      "perception interrupts on health alone: deaths by arrows are invisible to it")

    def test_the_body_has_one_exit(self):
        # Written and wired in the same turn, and watched from the same turn, because every other rule in this
        # suite was written first and wired later — or never.
        self.assertTrue(callers_of("submit") | callers_of("Motion"),
                        "nothing submits intents: the fight is driving the body from several places again")

    def test_perception_does_not_halt_the_body_itself(self):
        # The message (INTERRUPT) is perception's; the command (/stop) is the arbiter's. Two direct stops here were
        # two of the commanders a multi-threat fight cannot afford. A /stop handed to the arbiter in a lambda is
        # the arbiter's to run.
        direct = [c.lineno for c in calls_in("perception") if name_of(c) == "post" and first_arg(c) == "/stop"]
        self.assertEqual(direct, [], "perception must preempt through the arbiter, never call /stop directly")
        self.assertIn("preempt", GRAPH["perception"])

    def test_the_funnels_check_who_owns_the_body(self):
        for mod in ("nav", "api"):
            self.assertIn("owns", GRAPH[mod], f"{mod} drives the body without asking the arbiter who owns it")

    def test_recoveries_preempt_rather_than_walk_inline(self):
        self.assertIn("preempt", {name_of(c) for c in calls_in("end", "_recover")},
                      "a recovery is the safety layer speaking; it must own the body while it runs")

    def test_the_interrupt_message_has_one_writer(self):
        writers = {}
        for name in ("perception", "end", "brain", "skill", "arbiter", "api"):
            n = sum(1 for node in ast.walk(ast.parse(source(name))) if isinstance(node, ast.Assign)
                    for t in node.targets if isinstance(t, ast.Attribute) and t.attr == "INTERRUPT"
                    and isinstance(t.value, ast.Name) and t.value.id == "api"
                    and not (isinstance(node.value, ast.Constant) and node.value.value is None))
            if n:
                writers[name] = n
        # perception may still hand a message to a soft skill without stopping it; every stop-and-tell goes
        # through the arbiter.
        self.assertEqual(set(writers) - {"perception"}, {"arbiter"}, f"writers: {writers}")
        self.assertLessEqual(writers.get("perception", 0), 1)

    # (path, does the arbiter let this caller drive?) → (reached the game, what came back)
    POSTS = [("/task", True, True, "sent"), ("/stop", True, True, "sent"),
             ("/task", False, False, "failed"), ("/stop", False, False, "failed"),
             ("/close", False, True, "sent"), ("/click", False, True, "sent")]

    def test_raw_posts_that_drive_the_body_are_guarded(self):
        """run_chain posts /task directly: the gate is on `post`, so it holds for every caller."""
        from unittest import mock
        from bonobo import api, arbiter
        for path, owns, reached, want in self.POSTS:
            with self.subTest(path=path, owns=owns), \
                    mock.patch.object(arbiter.BODY, "owns", return_value=owns), \
                    mock.patch.object(api, "api", return_value={"status": "sent"}) as wire:
                got = api.post(path, {})
                self.assertEqual(wire.called, reached)
                self.assertEqual(got["status"], want)

    def test_tactics_are_preempted_not_nested_in_plans(self):
        direct = {name_of(c) for c in calls_in("end", "_carry_out")}
        self.assertNotIn("_retreat", direct, "a retreat inside a plan is a plan sub-step, not a tactic")
        self.assertIn("tactic", {first_arg(c) for c in calls_in("end", "_carry_out") if name_of(c) == "preempt"})

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
                ({"dead": True, "inLava": True}, {}, None)]
        for st, kw, want in rows:
            with self.subTest(state=st, **{k: True for k in kw}):
                got = perception.danger({**base, **st}, **kw)
                self.assertEqual(got, want)
                self.assertTrue(got is None or got in perception.DANGERS)


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
        snap = Snapshot.from_readings({"dimension": "minecraft:overworld"}, {"slots": [], "equipment": {}})
        for busy, want_asked, want_taker in self.ROWS:
            asked = []

            def layer(name, result):
                def ask(*a, **k):
                    asked.append(name)
                    return result if name in busy else None
                return ask
            b = brain.Brain.__new__(brain.Brain)
            b.retry, b.place = retry.Retry(), None
            b.upkeep = layer("upkeep", brain.Act("upkeep", "u", None))
            b.task_act = layer("queue", brain.Act("task", "t", None))
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

    def test_travel_health_guard_is_not_per_call(self):
        src = source("nav")
        tree = ast.parse(src)
        go_to = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "go_to")
        default = dict(zip([a.arg for a in go_to.args.args][-len(go_to.args.defaults):],
                           go_to.args.defaults)).get("min_hp")
        self.assertFalse(isinstance(default, ast.Constant) and default.value is None,
                         "min_hp defaults to None: every walk is unguarded unless the caller remembers")


if __name__ == "__main__":
    unittest.main()
