"""E4's samples and their bounded feedback: each step as priced and as run (dispatch.price_line), a run moving its
average a bounded step (memory.record_duration), a measured price kept near its prior (cost.measured)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import dispatch  # noqa: E402
from bonobo.data import MEASURED_BAND, TICKS_PER_S  # noqa: E402
from bonobo.knowledge import prior_ticks  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import cost, snapshot  # noqa: E402


class ThePriceLine(unittest.TestCase):
    def test_rows(self):
        st = Step("withdraw", "minecraft:stick", 4, {"pos": [3, 64, 0], "p": 0.9})
        st.est = 60
        # (situation, why it ended, row) → (ok, the work's price and its source)
        rows = [("ran: a sample", None, "withdraw_chest", (True, "PRIOR_TICKS.withdraw:prior")),
                ("must fail: an interrupted step taken as a sample", "interrupted: Interrupted", None,
                 (False, "PRIOR_TICKS.withdraw:prior"))]
        for name, why, row, (ok, price) in rows:
            with self.subTest(name):
                line = dispatch.price_line(st, False, "minecraft:overworld", 4.2, why, row)
                self.assertEqual((line["ok"], line["price"]["work"]), (ok, price))
                self.assertEqual((line["est"], line["actual_s"], line["row"], line["cond"]["p"]), (60, 4.2, row, 0.9))

    def test_the_tasks_ticks_ride_along(self):
        from bonobo import api
        mine = {"type": "mine", "startTick": 100, "endTick": 120, "result": {"block": "minecraft:stone", "brokeTick": 117}}
        rows = [("must fail: a mine's break apart from its collect", mine, ["mine", 100, 117, 120, 3]),
                ("a walk breaks nothing", {"type": "goto", "startTick": 90, "endTick": 99, "result": None},
                 ["goto", 90, None, 99, 3])]
        for name, rec, want in rows:
            with self.subTest(name):
                self.assertEqual(api.task_ticks(rec, 3), want)
        line = dispatch.price_line(Step("mine", "minecraft:cobblestone", 1, {"breaks": 1}), False, "o", 1.0,
                                   phases={"ticks": [want]})
        self.assertEqual(line["ticks"], [want])          # must fail: the split left out of the price line

    def test_a_per_unit_kind_names_its_unit_price(self):
        line = dispatch.price_line(Step("mine", "minecraft:cobblestone", 3, {"breaks": 3}), True, "o", 9.0)
        self.assertEqual(line["price"]["work"], "PRIOR_TICKS.mine_each:prior")     # must fail: "skill" (the catch-all)


class TheFeedbackIsBounded(unittest.TestCase):
    def test_one_run_moves_the_average_a_bounded_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.record_duration("chop", 10.0)
            m.record_duration("chop", 1000.0)          # a run stuck on a path
            # must fail: 307 s a log after one stuck run (0.7 × 10 + 0.3 × 1000)
            self.assertAlmostEqual(m.data["durations"]["chop"]["per"], 0.7 * 10 + 0.3 * 10 * MEASURED_BAND)

    def test_a_measured_price_stays_near_its_prior(self):
        step = Step("gather", "log", 1, {})
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.data["durations"]["chop"] = {"per": 900.0, "n": 5}
            got = cost(snapshot(), mem=m).measured(step)
        # must fail: 900 s a log priced as measured, far past the band around the prior
        self.assertEqual(got, prior_ticks(step) * MEASURED_BAND)
        self.assertLess(got, 900 * TICKS_PER_S)


class TheWaitIsTheNightLeft(unittest.TestCase):
    def test_rows(self):
        from bonobo.knowledge import dawn_s
        from tests.world import state
        for t in (13000, 18000, 22500):
            with self.subTest(timeOfDay=t):
                snap = snapshot(state(timeOfDay=t))
                # must fail: a wait for day priced as a search for something never seen (seek_prior_s / exists_prior)
                self.assertEqual(cost(snap).work(Step("wait", "day", 1, {})), round(dawn_s(snap.state) * TICKS_PER_S))


class TheWorkAsTheJarRunsIt(unittest.TestCase):
    def test_a_break_is_a_task(self):
        from bonobo.game import BREAK_COOLDOWN
        from bonobo.knowledge import PRIOR_TICKS, break_ticks, work_s
        # must fail: a stone broken in the game's 12 ticks, though the next waits the game's cooldown and the task
        # its own (bench q5: start..broke = break + 6 on every mine)
        self.assertEqual(work_s(["stone"], [], {"pickaxe": 1}, TICKS_PER_S) * TICKS_PER_S,
                         break_ticks("stone", "minecraft:stone_pickaxe") + BREAK_COOLDOWN + PRIOR_TICKS["break_task"])

    def test_a_table_placed_for_a_craft_is_taken_back(self):
        from tests.world import cost
        step = Step("craft", "minecraft:wooden_pickaxe", 1, {})
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.add_station("minecraft:crafting_table", (1, 64, 1), "minecraft:overworld")
            near = cost(snapshot(), mem=m).work(step)
        # must fail: the craft priced alone though no table stands near: the one placed is broken by hand after
        # (bench craft__base: 3.75 s of its 5)
        self.assertGreater(cost(snapshot()).work(step), near)

    def test_an_ore_behind_stone_prices_the_tunnel(self):
        import json
        from bonobo import brain, planner  # noqa: F401
        from bonobo.bench.words import est
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bench_rows.json")) as fh:
            setup = json.load(fh)["ore_buried"]["setup"]
        c = est.scene_cost(est.scene_world(setup))
        steps = planner.plan_needs(c.snap.inv, [("minecraft:raw_iron", 1)], c)
        breaks = c.work_of(steps[-1])[0]
        # must fail: the ore two blocks into the stone priced as one break at arm's length (the ground the round read
        # unread by the dig to it, and the tunnel never seeing through the cells it opened)
        self.assertGreaterEqual(breaks.count("stone"), 4)
        self.assertLessEqual(breaks.count("stone"), 6)

    def test_crafts_at_one_table_break_it_once(self):
        from bonobo import planner
        from tests.world import cost
        c = cost(snapshot())
        steps = [Step("craft", t, 1, {}) for t in ("minecraft:wooden_pickaxe", "minecraft:wooden_axe",
                                                   "minecraft:wooden_sword")]
        once = c.work(steps[0]) - c.work(steps[0], table_back=False)
        # must fail: each of three crafts at the one table placed charged its break (the run sits at it: brain.craft_run)
        self.assertEqual(sum(planner.price_as_run(steps, [], c)), sum(c.work(st, table_back=False) for st in steps) + once)
        search = planner.Search(c)
        node = planner.Node(planner.from_bag(snapshot().inv, None, None, c.reserved, c.facts()), [], [])
        for st in steps:
            search.emit(node, st, 0, 0)
        self.assertLessEqual(node.g, sum(planner.price_as_run(steps, [], c)))     # the search's price the same way


if __name__ == "__main__":
    unittest.main()
