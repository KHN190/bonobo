"""data.OVERRUN (the user's rule): a step's ways are held to OVERRUN × its as-run price (Step.est, api.step_budget) —
a way priced past what the step has left is refused before digging, one running past it is stopped at a segment
boundary, the budget spent across every try and way of the step; an overrun is api.Overrun (no ban: the round
prices again), never a NavFailed keyed to the target."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav  # noqa: E402
from bonobo.data import OVERRUN  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

TARGET = (10, 64, 0)
REGION = FakeRegion((0, 60, -4), (16, 70, 4), {})
SEGMENT = 6                       # api.run_chain's own segment size: its before_segment runs once per part


def steps_of(n):
    return [{"type": "mine", "x": i, "y": 64, "z": 0} for i in range(n)]


class StepBudget(unittest.TestCase):
    def setUp(self):
        self.clock, self.parts = [0.0], []
        clock = mock.patch.object(nav.time, "time", lambda: self.clock[0])     # the budget's clock too
        clock.start()
        self.addCleanup(clock.stop)

    def way(self, seconds, part_s=0.0):
        """One reach_stand: plan_way answers a 12-step way priced `seconds`; each run_chain segment advances the
        fake clock by `part_s` after its before_segment hook (the safety hook and the budget check) ran."""
        def run_chain(tasks, stop_on_failure=True, before_segment=None, **_kw):
            for i in range(0, len(tasks), SEGMENT):
                if before_segment:
                    before_segment(tasks[i:i + SEGMENT])
                self.parts.append(len(tasks[i:i + SEGMENT]))
                self.clock[0] += part_s
        with mock.patch.object(nav, "feet", lambda: (0, 64, 0)), \
                mock.patch.object(nav, "_read_box", lambda *a, **k: REGION), \
                mock.patch.object(nav, "ways_for", lambda *a, **k: 1), \
                mock.patch.object(nav, "stands_for", lambda *a, **k: bool(self.parts)), \
                mock.patch.object(nav, "stand_candidates", lambda *a, **k: []), \
                mock.patch.object(nav, "plan_walks", lambda *a, **k: []), \
                mock.patch.object(nav, "inventory_now", lambda: None), \
                mock.patch.object(nav, "plan_way", lambda *a, **k: (steps_of(12), None, seconds)), \
                mock.patch.object(api, "run_chain", run_chain), mock.patch.object(api, "detail", lambda *a: None):
            nav.reach_stand({"type": "mine", "x": TARGET[0], "y": TARGET[1], "z": TARGET[2]}, nav.Policy())

    def test_refused_before_digging(self):
        """accept7: a way priced 104 s against a 61 s step (> 1.5×): refused, nothing dug, not a NavFailed (no ban)."""
        with api.step_budget(61 * TICKS_PER_S), self.assertRaises(api.Overrun) as e:
            self.way(104.3)
        self.assertNotIsInstance(e.exception, api.NavFailed)      # must fail: the target banned for a price miss
        self.assertEqual(self.parts, [])

    def test_dug_within_the_budget(self):
        self.assertLessEqual(20.0, OVERRUN * 61, "fixture must fall inside the budget")
        with api.step_budget(61 * TICKS_PER_S):
            self.way(20.0)
        self.assertEqual(self.parts, [SEGMENT, SEGMENT])

    def test_stopped_at_a_segment_once_over(self):
        """Priced fine (10 s), the digging runs long: stopped before the next segment, not finished."""
        with api.step_budget(10 * TICKS_PER_S), self.assertRaises(api.Overrun):
            self.way(10.0, part_s=20.0)
        self.assertEqual(self.parts, [SEGMENT])                   # must fail: the whole way dug

    def test_one_budget_across_the_steps_ways(self):
        """Two ways of one step: the second has only what the first left (must fail: a budget per try)."""
        with api.step_budget(10 * TICKS_PER_S) as budget:
            self.way(10.0, part_s=5.0)
            self.assertEqual(budget.spent(), 10.0)
            self.parts.clear()                                    # the step's next way: a stand not yet held
            with self.assertRaises(api.Overrun):
                self.way(10.0)

    def test_a_safety_act_inside_pauses_the_steps_clock(self):
        """A 30 s soft skill (eat, flee: step_budget(None)) inside a 10 s step: the step's clock paused for it, no
        overrun after (must fail: the step's price refuted for time a safety act spent)."""
        with api.step_budget(10 * TICKS_PER_S) as budget:
            self.clock[0] += 2.0
            with api.step_budget(None):
                self.clock[0] += 30.0
            api.check_budget()
            self.assertEqual(budget.spent(), 2.0)

    def test_no_step_no_budget(self):
        """A way outside a step (a reflex, a bench walk) is never held to a price it has none of."""
        self.way(1e6, part_s=1e6)
        self.assertEqual(self.parts, [SEGMENT, SEGMENT])

    def test_the_safety_hook_runs_before_every_segment(self):
        """S1/S7: the policy's before_segment (the hazard check) still runs per segment of a budgeted way."""
        seen = []
        policy = nav.Policy(before_segment=lambda part: seen.append(len(part)))
        with api.step_budget(61 * TICKS_PER_S), mock.patch.object(nav, "Policy", lambda: policy):
            self.way(20.0)
        self.assertEqual(seen, [SEGMENT, SEGMENT])


def _refuted_scene():
    """(cost of, step, near, far, bag): two iron ores remembered — a near one buried 4 down, a far one open on the
    floor — and a stone pickaxe."""
    from bonobo import cost as costmod, world
    from bonobo.planner import Step
    from tests.world import inventory, memory, state
    ground = {(x, y, z): "stone" for x in range(-20, 21) for z in range(-6, 7) for y in range(50, 64)}
    near, far = (6, 60, 0), (-12, 64, 0)
    ground[near] = ground[far] = "iron_ore"
    region = world.Region.of((-20, 50, -6), (20, 70, 6), ground)
    mem = memory()
    for c in (near, far):
        mem.note_seen("iron_ore", c, "minecraft:overworld")

    def cost_of(bag):
        snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), bag, {}, [], region)
        return costmod.Cost(snap, mem)
    step = Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore"], "breaks": 1})
    return cost_of, step, near, far, mem, world.Inventory(inventory(("stone_pickaxe", 1)))


class RefutedPrice(unittest.TestCase):
    """A price the run refuted (Memory.refute, dispatch.execute's): Cost prices the step at its measured rest while the
    state holds, never under its own lower bound (P3)."""

    def _refute(self, mem, step, target, seconds, bag):
        from bonobo.skillcore import ban_state
        mem.refute(step, target, seconds,
                   ban_state((0.5, 64, 0.5), frozenset(s["id"] for s in bag.slots if s.get("count"))))

    def test_a_rest_below_its_bound_is_priced_at_the_bound(self):
        cost_of, step, near, _far, mem, bag = _refuted_scene()
        self._refute(mem, step, near, 0.5, bag)
        c = cost_of(bag)
        tools = c.step_state()[1]
        bound = c.walk_lb(step, tools) + c.dig_lb(step, tools)
        self.assertGreater(bound, 0.5 * TICKS_PER_S)
        # must fail: the refuted rest under the plan's own lower bound (P3: lb ≤ the chosen price)
        self.assertEqual(c.estimate(step), bound)

    def test_a_cheaper_second_source_is_picked(self):
        """The near target (buried) refuted dear: the far one (open on the floor, no dig) beats it on a plain walk."""
        cost_of, step, near, far, mem, bag = _refuted_scene()
        c0 = cost_of(bag)
        self.assertEqual(c0.site(step), near, "fixture must pick the near one first, unrefuted")
        self._refute(mem, step, near, 1000.0, bag)
        c = cost_of(bag)
        # must fail: still priced (and sited) at the dear refuted target, not G3's cheaper second source
        self.assertEqual(c.site(step), far)

    def test_a_bag_change_lifts_the_refutation(self):
        """The refutation is kept under the bag's way-relevant kinds (ban_state/knowledge.way_kinds: tools, building
        blocks): a pickaxe swapped for another tier is a different state, so the same target prices fresh again
        (G3/E5: the fact only lifts by the state change it was measured in)."""
        from bonobo.world import Inventory
        from tests.world import inventory
        cost_of, step, near, _far, mem, bag = _refuted_scene()
        self._refute(mem, step, near, 0.5, bag)
        new_bag = Inventory(inventory(("iron_pickaxe", 1)))        # a different tool tier: a different way_kinds set
        c = cost_of(new_bag)
        self.assertEqual(c.site(step), near)
        c.estimate(step)
        # must fail: the lifted refutation still priced from the old, refuted rest
        self.assertNotIn("refuted", step.parts)


class OverrunIsReplannedNotFailed(unittest.TestCase):
    """brain.outcome_of(Overrun) == "interrupted" (data.EXCEPTIONS["Overrun"]: replan, layer:plan; arbiter.RESUME_OF
    "same"): Brain.failed returns before writing a retry entry or a ban (brain.py:368's early return) -- new API
    (api.Overrun, EXCEPTIONS/RESUME_OF rows) with no base equivalent: not red by assertion on 2000ec2 (AttributeError/
    KeyError there), unlike the production-path rows (test_overrun_paths)."""

    def test_outcome_is_interrupted(self):
        from bonobo import brain
        self.assertEqual(brain.outcome_of(api.Overrun("the step ran 20s > 15s", pos=(1, 64, 1), remaining_s=20.0)),
                         ("interrupted", "layer:plan"))

    def test_failed_writes_no_retry_entry_and_no_ban(self):
        from bonobo import brain
        from tests.world import brain_fixture
        b = brain_fixture()
        outcomes, retried = [], []
        b.mem.record_outcome = lambda *a, **k: outcomes.append(a)
        b.retry.failed = lambda *a, **k: retried.append(a)
        err = api.Overrun("the step ran 20s > 15s", pos=(5, 64, 5), remaining_s=20.0)
        self.assertIsNone(b.failed("mine:minecraft:raw_iron", err))
        self.assertEqual(outcomes, [], "must fail: an interruption counted as an outcome")
        self.assertEqual(retried, [], "must fail: an interruption written as a retry entry")
        self.assertEqual(b.blacklist, {}, "must fail: an interruption banned its target")


class ReflexShelterNeverOverruns(unittest.TestCase):
    """bench/core.py's shelter path: dispatch.run_priced(..., budget=False) opens no step clock (S7's exclusion), so
    api.check_budget/afford stay no-ops however long the reflex runs. `budget=` is new API (no base signature):
    not red by assertion on 2000ec2 (TypeError there)."""

    def test_a_long_reflex_never_raises(self):
        from bonobo import dispatch
        from bonobo.planner import Step
        step = Step("skill", "shelter", 1, {}, 5 * TICKS_PER_S)

        def long_reflex():
            self.assertIsNone(api.BUDGET[0], "must fail: a budget open under budget=False")
            for _ in range(5):
                api.check_budget()               # must never raise: no budget is open
            api.afford(1e9, (0, 64, 0))           # an absurd way price: still a no-op with no budget
            return "sheltered"

        with mock.patch.object(dispatch, "trace", lambda *a, **k: None):
            out = dispatch.run_priced("minecraft:overworld", step, False, long_reflex, budget=False)
        self.assertEqual(out, "sheltered")


if __name__ == "__main__":
    unittest.main()
