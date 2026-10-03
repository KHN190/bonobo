"""The event log records changes only: a repeated bid is one line, an identical line is counted, an anomaly is said
at its 1st, 10th, 100th time."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import events, paths  # noqa: E402
from check.round import renew_session  # noqa: E402


class Events(unittest.TestCase):
    def setUp(self):
        renew_session()

    def test_decisions_only_when_they_change(self):
        out = []
        for pick in ("fight", "fight", "fight", "evade", "evade", "fight"):
            events.decision("fight", pick, 10.0, "why", t=1.0, sink=out)
        # must fail: the same bid written every round
        self.assertEqual([r["pick"] for r in out if r["kind"] == "decision"], ["fight", "evade", "fight"])

    def test_identical_lines_counted(self):
        out = []
        for _ in range(4):
            events.emit("task", "mine: ok (1.0s)", t=1.0, sink=out)
        events.emit("goal", "goal: iron", t=2.0, sink=out)
        rows = [(r["kind"], r.get("count")) for r in out]
        self.assertEqual(rows, [("task", None), ("repeat", 4), ("goal", None)])

    def test_anomalies_at_1_10_100(self):
        out = []
        for _ in range(100):
            events.anomaly("slow round", "6.0s", t=1.0, sink=out)
        self.assertEqual([r["count"] for r in out if r["kind"] == "anomaly"], [1, 10, 100])

    def test_resume(self):
        out = []
        events.task("mine", "interrupted", 3.0, "layer:tactic", t=1.0, sink=out)
        events.task_start("mine", t=5.0, sink=out)
        events.task_start("mine", t=9.0, sink=out)          # must fail: a second start said as a resume
        events.task_start("chop", t=9.0, sink=out)          # never interrupted: nothing said
        self.assertEqual([r["kind"] for r in out], ["task", "resume"])

    def test_a_failure_says_its_cause(self):
        from bonobo import api, retry
        # (situation, the error) → the line
        rows = [("could not reach the cow: nav", api.NavFailed("could not get to the cow"),
                 "hunt: failed (nav) (30.2s)"),
                ("none found: unavailable", api.NotAvailable("no cow found"), "hunt: failed (unavailable) (30.2s)"),
                ("must fail: said by its source ('stuck' for every failure)", api.NavFailed("x"), None)]
        for name, err, want in rows:
            with self.subTest(name):
                out = []
                events.task("hunt", "failed", 30.2, retry.source_of(err), t=1.0, sink=out, cause=retry.cause_of(err))
                if want is None:
                    self.assertNotIn("by stuck", out[0]["line"])
                else:
                    self.assertEqual(out[0]["line"], want)

    def test_goal_progress(self):
        out = []
        events.goal("gather logs", {"minecraft:oak_log": 1}, t=0.0, sink=out)
        events.goal("mine iron", {"minecraft:oak_log": 5, "minecraft:dirt": 1}, t=30.0, sink=out)
        prog = [r for r in out if r["kind"] == "progress"]
        self.assertEqual(prog[0]["gained"], {"minecraft:dirt": 1, "minecraft:oak_log": 4})
        # must fail: a goal that gained nothing still says progress
        events.goal("craft", {"minecraft:oak_log": 5, "minecraft:dirt": 1}, t=40.0, sink=out)
        self.assertEqual(len([r for r in out if r["kind"] == "progress"]), 1)

    def test_slow_round_is_deciding_not_running(self):
        # (round s, of it running a task s) → a slow-round anomaly?
        slow = events.SLOW_ROUND_S
        rows = [("slow deciding", slow + 1, 0.0, 1),
                ("must fail: a long task read as a slow round", slow * 10, slow * 10 - 0.7, 0),
                ("slow deciding around a task", slow * 10, slow * 10 - slow - 1, 1)]
        for name, total, ran, n in rows:
            with self.subTest(name):
                renew_session()
                out = []
                events.round_time(total, ran, t=1.0, sink=out)
                self.assertEqual(len([r for r in out if r["kind"] == "anomaly"]), n)

    def test_a_hurt_is_named_by_the_games_source(self):
        from unittest import mock
        from bonobo import perception
        # (situation, /state lastDamage) → the source said
        rows = [("a fall: the game's own source", {"source": "fall", "nearest": "none", "gameTime": 5}, "fall"),
                ("a zombie: its attacker", {"source": "mob", "nearest": "minecraft:zombie", "gameTime": 6},
                 "minecraft:zombie"),
                ("must fail: no jar damage: the nearest threat guessed (none seen)", None, None)]
        for name, hit, want in rows:
            with self.subTest(name), mock.patch.object(events, "hurt") as hurt, \
                    mock.patch.object(perception, "threats_seen", return_value=([], set())):
                perception.STATE.hp_seen, perception.STATE.damage_at = (20.0, 0.0), None
                state = {"health": 19.0, "x": 0, "y": 64, "z": 0, **({"lastDamage": hit} if hit else {})}
                perception.note_hurt(state, now=1.0)
                self.assertEqual(hurt.call_args.args[2], want)

    def test_no_goal_spans_a_life(self):
        # must fail: the row's last goal closed by the next row's first, its bag diff over the boundary (043641)
        from bonobo import lifecycle, perception  # noqa: F401  (perception registers events' reset)
        out = []
        with mock.patch.dict(events.STATE, {}, clear=True):
            events.goal("upkeep: sleep", {"minecraft:white_bed": 1}, t=1.0, sink=out)
            lifecycle.reset_all()
            events.goal("upkeep: empty the bag", {"minecraft:white_bed": 1, "minecraft:diamond": 1536}, t=2.0, sink=out)
        self.assertEqual([r["kind"] for r in out], ["goal", "goal"])

    def test_a_milestone_is_said_once_per_world(self):
        import tempfile
        from bonobo import fresh
        path = os.path.join(tempfile.mkdtemp(), "milestones.json")
        out = []
        events.milestones({"minecraft:stone_pickaxe": 1}, t=1.0, sink=out, path=path)
        events.STATE.clear()                   # a restart: this process's memory gone
        events.milestones({"minecraft:stone_pickaxe": 1}, t=2.0, sink=out, path=path)
        self.assertEqual([r["line"] for r in out], ["first stone_pickaxe"],
                         "must fail: said again after a restart")
        self.assertIn(os.path.basename(events.MILESTONES_FILE), fresh.WORLD_SCOPED, "per save: a new world starts over")

    def test_damage_label(self):
        rows = [("attacker first", {"source": "mob", "nearest": "minecraft:skeleton"}, "minecraft:skeleton"),
                ("no attacker: the source", {"source": "onFire", "nearest": "none"}, "onFire"),
                ("must fail: nothing read", None, None)]
        for name, last, want in rows:
            with self.subTest(name):
                self.assertEqual(events.damage_label(last), want)

    def test_death_named_by_last_hurt(self):
        import json, tempfile
        out = []
        events.hurt(3.0, 2.0, "minecraft:zombie", t=1.0, sink=out)
        events.death(None, (0, 64, 0), t=2.0, sink=out)
        self.assertEqual(out[-1]["cause"], "minecraft:zombie")       # must fail: "?"
        events.death(None, (0, 64, 0), t=3.0, sink=out)
        # a new process that starts dead reads the file; a death since closes the last life's harm
        rows = [("hurt on file", ["hurt"], "minecraft:zombie"),
                ("must fail: the last life's harm", ["hurt", "death", "respawn"], None)]
        for name, kinds, want in rows:
            with self.subTest(name):
                renew_session()
                with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
                    for k in kinds:
                        f.write(json.dumps({"t": 1.0, "kind": k, "line": k, "source": "minecraft:zombie"}) + "\n")
                self.assertEqual(events.last_hurt_by(f.name), want)
                os.unlink(f.name)

    def test_goal_steps_said_as_steps(self):
        out = []
        head = "upkeep: idle: have pickaxe tier 1"
        events.goal(head + events.STEP + "craft 12× planks", {}, t=1.0, sink=out)
        events.goal(head + events.STEP + "craft 4× stick", {"minecraft:oak_planks": 12}, t=2.0, sink=out)
        events.goal("other" + events.STEP + "craft 4× stick", {"minecraft:oak_planks": 12}, t=3.0, sink=out)
        lines = [r["line"] for r in out]
        self.assertEqual(lines[0], "goal: " + head + events.STEP + "craft 12× planks")
        self.assertNotIn(head, lines[1] + lines[2])                  # must fail: the goal repeated on every step
        self.assertTrue(lines[2].strip().startswith(events.STEP.strip() + " craft 4× stick"))
        self.assertTrue(lines[3].startswith("goal: other"))          # a new goal said whole

    def test_rows(self):
        out = []
        rows = [("a slow round is an anomaly", lambda: events.round_time(events.SLOW_ROUND_S + 1, t=1.0, sink=out), 1),
                ("must fail: a quick round said", lambda: events.round_time(0.1, t=1.0, sink=out), 0),
                ("a first diamond is a milestone", lambda: events.milestones({"minecraft:diamond": 1}, t=1.0, sink=out), 1),
                ("the second time it is not", lambda: events.milestones({"minecraft:diamond": 3}, t=1.0, sink=out), 0),
                ("a goal said once", lambda: (events.goal("iron", t=1.0, sink=out), events.goal("iron", t=2.0, sink=out)),
                 1)]
        for name, act, n in rows:
            with self.subTest(name):
                before = len(out)
                act()
                self.assertEqual(len(out) - before, n)


if __name__ == "__main__":
    unittest.main()
