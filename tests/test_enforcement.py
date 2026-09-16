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


def call_graph():
    """{module: {called names}} across the package — attribute calls included (`combat.shoot` → `shoot`)."""
    graph = {}
    for name in sorted(os.listdir(PKG)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(PKG, name)).read())
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

# Modules that execute: they talk to the game or decide what to do. A rule enforced only in a module that never
# runs during play is not enforced.
# `api` belongs here: it is the funnel every task passes through, and therefore the right place for guards that
# must not be opt-in. Leaving it out made this test blind to exactly the fix it asks for.
# `api` is the funnel every task passes through; `fight_plan` decides once per fight round. Both are execution,
# and leaving either out makes this check blind to the wiring it exists to guard.
EXECUTING = {"end", "combat", "brain", "skills", "nav", "perception", "skillcore", "skill", "nether", "api",
             "fight_plan", "threat"}


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

    def test_threats_carry_velocity(self):
        # A hazard list with hardcoded zero velocity makes every closed-form root return infinity, so the
        # prediction reports "nothing is coming" regardless of what is coming.
        import re
        src = open(os.path.join(PKG, "threat.py")).read()
        body = src[src.index("def rows("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertNotIn("(0.0, 0.0, 0.0)) for", body, "threats must be differenced, not declared stationary")
        self.assertIn("prev", body, "velocity comes from comparing two rounds")
        self.assertIn("rows", GRAPH["end"], "the fight builds its rows with the shared differencing")

    def test_ordinary_play_asks_the_threat_layer(self):
        # "hostile within 5 → attack, hp ≤ 10 and within 6 → flee" was two literals pretending to be a policy. The
        # decision must come from threat.decide, in the round (brain) and in the watcher (perception).
        self.assertIn("brain", callers_of("decide"), "the brain answers threats without the model")
        self.assertIn("perception", callers_of("pressure") | callers_of("time_to_die"),
                      "perception interrupts on health alone: deaths by arrows are invisible to it")
        src = open(os.path.join(PKG, "brain.py")).read()
        self.assertNotIn('e["distance"] <= 5', src, "a distance literal decides a fight again")

    def test_the_body_has_one_exit(self):
        # Written and wired in the same turn, and watched from the same turn, because every other rule in this
        # suite was written first and wired later — or never.
        self.assertTrue(callers_of("submit") | callers_of("Motion"),
                        "nothing submits intents: the fight is driving the body from several places again")

    def test_perception_does_not_halt_the_body_itself(self):
        # The message (INTERRUPT) is perception's; the command (/stop) is the arbiter's. Two direct stops here were
        # two of the commanders a multi-threat fight cannot afford.
        src = open(os.path.join(PKG, "perception.py")).read()
        direct = src.replace('lambda: api.post("/stop")', "").count('api.post("/stop")')
        self.assertEqual(direct, 0, "perception must preempt through the arbiter, never call /stop directly")
        self.assertIn("preempt", GRAPH["perception"])

    def test_the_funnels_check_who_owns_the_body(self):
        for mod in ("nav", "api"):
            self.assertIn("owns", GRAPH[mod], f"{mod} drives the body without asking the arbiter who owns it")

    def test_recoveries_preempt_rather_than_walk_inline(self):
        src = open(os.path.join(PKG, "end.py")).read()
        body = src[src.index("def _recover("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertIn("preempt", body, "a recovery is the safety layer speaking; it must own the body while it runs")

    def test_the_interrupt_message_has_one_writer(self):
        import re
        writers = {}
        for name in ("perception", "end", "brain", "skill", "arbiter", "api"):
            src = open(os.path.join(PKG, name + ".py")).read()
            n = sum(1 for rhs in re.findall(r"api\.INTERRUPT\s*=\s*(\S+)", src) if rhs != "None")
            if n:
                writers[name] = n
        # perception may still hand a message to a soft skill without stopping it; every stop-and-tell goes
        # through the arbiter.
        self.assertEqual(set(writers) - {"perception"}, {"arbiter"}, f"writers: {writers}")
        self.assertLessEqual(writers.get("perception", 0), 1)

    def test_raw_posts_that_drive_the_body_are_guarded(self):
        src = open(os.path.join(PKG, "api.py")).read()
        body = src[src.index("def post("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertIn("owns", body, "run_chain posts /task directly; the gate must be on post, not only on run")

    def test_tactics_are_preempted_not_nested_in_plans(self):
        src = open(os.path.join(PKG, "end.py")).read()
        body = src[src.index("def _carry_out("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertNotIn("        _retreat(ctx, near)\n", body, "a retreat inside a plan is a plan sub-step, not a tactic")
        self.assertIn('preempt("tactic"', body)

    def test_danger_kinds_and_the_recovery_table_agree(self):
        # Perception emits kinds; the table keys on them exactly. A kind with no row falls to the default, which
        # is allowed; a row for a kind perception can never emit is a dead entry pretending to be a rule.
        from bonobo import perception, recovery
        emitted = set(perception.DANGERS) | {"airborne", "stale"}       # the two non-perception triggers
        for kind, _, _ in recovery.TABLE:
            self.assertIn(kind, emitted, f"recovery row {kind!r} can never fire")
        src = open(os.path.join(PKG, "perception.py")).read()
        import re
        body = src[src.index("def danger("):src.index("\ndef ", src.index("def danger(") + 1)]
        for lit in re.findall(r'return "([a-z_]+)"', body):
            self.assertIn(lit, perception.DANGERS, f"danger() returns {lit!r} which is not a declared kind")


class OneDecisionPoint(unittest.TestCase):
    """Staying alive is priced, not sequenced.

    `_round` used to be four layers of if: survival, then night safety, then directives, then the pool — each
    returning early, so the order of the ifs WAS the priority and nothing could say what any layer was worth. Both
    survival and safety already had a model in seconds; they just had no way to say it. The only thing that may
    still jump the queue is what kills inside one round, because a round is the deliberation time.
    """

    def test_only_what_kills_inside_a_round_pre_empts_the_pool(self):
        import inspect
        from bonobo.brain import Brain
        src = inspect.getsource(Brain._round)
        early = [ln.strip() for ln in src.splitlines() if ln.strip() == "return" or ln.strip().startswith("return ")]
        # Three, and each is named: the survival floor (dead inside a round), an operator directive (a person
        # saying what to do is authority, not a bid, so it does not get priced), and the pool's own "nothing
        # runnable" hold. Everything about staying alive that used to sit here is now a priced candidate.
        self.assertLessEqual(len(early), 3, f"_round still decides by the order of its ifs: {early}")
        self.assertIn("self.survival(snap, ctx)", src)
        self.assertNotIn("self.safety(snap, ctx)", src, "night safety must be priced in the pool, not sequenced")

    def test_the_rescues_are_offered_as_candidates(self):
        self.assertIn("_rescue_candidates", GRAPH["brain"])


class SafetyIsNotOptIn(unittest.TestCase):
    """A guard that each call site must remember to ask for is a guard whose coverage decays."""

    def test_travel_health_guard_is_not_per_call(self):
        src = open(os.path.join(PKG, "nav.py")).read()
        tree = ast.parse(src)
        go_to = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "go_to")
        default = dict(zip([a.arg for a in go_to.args.args][-len(go_to.args.defaults):],
                           go_to.args.defaults)).get("min_hp")
        self.assertFalse(isinstance(default, ast.Constant) and default.value is None,
                         "min_hp defaults to None: every walk is unguarded unless the caller remembers")


if __name__ == "__main__":
    unittest.main()
