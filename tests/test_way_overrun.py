"""data.OVERRUN (the user's rule): a step's ways are held to OVERRUN × its as-run price (Step.est, nav.step_budget) —
a way priced past what the step has left is refused before digging, one running past it is stopped at a segment
boundary, the budget spent across every try and way of the step; an overrun is nav.Overrun (no ban: the round
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
        with nav.step_budget(61 * TICKS_PER_S), self.assertRaises(nav.Overrun) as e:
            self.way(104.3)
        self.assertNotIsInstance(e.exception, api.NavFailed)      # must fail: the target banned for a price miss
        self.assertEqual(self.parts, [])

    def test_dug_within_the_budget(self):
        self.assertLessEqual(20.0, OVERRUN * 61, "fixture must fall inside the budget")
        with nav.step_budget(61 * TICKS_PER_S):
            self.way(20.0)
        self.assertEqual(self.parts, [SEGMENT, SEGMENT])

    def test_stopped_at_a_segment_once_over(self):
        """Priced fine (10 s), the digging runs long: stopped before the next segment, not finished."""
        with nav.step_budget(10 * TICKS_PER_S), self.assertRaises(nav.Overrun):
            self.way(10.0, part_s=20.0)
        self.assertEqual(self.parts, [SEGMENT])                   # must fail: the whole way dug

    def test_one_budget_across_the_steps_ways(self):
        """Two ways of one step: the second has only what the first left (must fail: a budget per try)."""
        with nav.step_budget(10 * TICKS_PER_S) as budget:
            self.way(10.0, part_s=5.0)
            self.assertEqual(budget.spent(), 10.0)
            self.parts.clear()                                    # the step's next way: a stand not yet held
            with self.assertRaises(nav.Overrun):
                self.way(10.0)

    def test_a_safety_act_inside_pauses_the_steps_clock(self):
        """A 30 s soft skill (eat, flee: step_budget(None)) inside a 10 s step: the step's clock paused for it, no
        overrun after (must fail: the step's price refuted for time a safety act spent)."""
        with nav.step_budget(10 * TICKS_PER_S) as budget:
            self.clock[0] += 2.0
            with nav.step_budget(None):
                self.clock[0] += 30.0
            nav.check_budget()
            self.assertEqual(budget.spent(), 2.0)

    def test_no_step_no_budget(self):
        """A way outside a step (a reflex, a bench walk) is never held to a price it has none of."""
        self.way(1e6, part_s=1e6)
        self.assertEqual(self.parts, [SEGMENT, SEGMENT])

    def test_the_safety_hook_runs_before_every_segment(self):
        """S1/S7: the policy's before_segment (the hazard check) still runs per segment of a budgeted way."""
        seen = []
        policy = nav.Policy(before_segment=lambda part: seen.append(len(part)))
        with nav.step_budget(61 * TICKS_PER_S), mock.patch.object(nav, "Policy", lambda: policy):
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
        from bonobo import cost as costmod
        from bonobo.skillcore import ban_state
        mem.refute(costmod.refuted_key(step, target), seconds,
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


class Accept7MineStep(unittest.TestCase):
    """The production path (dispatch.run_priced → gather._go_way → nav), accept7's shape: an ore 67 off and 10 down
    through stone, the step priced 61 s. Its way (~200 s of digging) is past 1.5× the price: refused before a block is
    dug, a McError for the round (must fail: the whole way sent, accept7's ~138 s tunnel)."""

    def test_the_way_past_the_price_is_never_dug(self):
        from bonobo import dispatch, gather, skillcore, world
        from bonobo.planner import Step
        from tests.world import inventory, memory
        feet, target = (12960, 67, 12928), (12995, 57, 12995)
        lo, hi = (12955, 50, 12923), (13000, 72, 13000)
        blocks = {(x, y, z): "stone" for x in range(lo[0], hi[0] + 1) for z in range(lo[2], hi[2] + 1)
                  for y in range(lo[1], feet[1])}
        blocks[target] = "iron_ore"
        region = world.Region.of(lo, hi, blocks)
        bag = world.Inventory(inventory(("stone_pickaxe", 1)))
        step = Step("mine", "minecraft:raw_iron", 3, {"blocks": ["iron_ore"], "tier": 1, "breaks": 3},
                    61 * TICKS_PER_S)
        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        sent = []

        def run_chain(tasks, stop_on_failure=False, before_segment=None, **_kw):
            for i in range(0, len(tasks), SEGMENT):
                if before_segment:
                    before_segment(tasks[i:i + SEGMENT])
                sent.extend(tasks[i:i + SEGMENT])
            return []
        with mock.patch.object(api, "run_chain", run_chain), mock.patch.object(api, "detail", lambda *a: None), \
                mock.patch.object(gather, "Inventory", lambda *a: bag), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                self.assertRaises(api.McError):
            dispatch.run_priced("minecraft:overworld", step, False,
                                lambda: gather._go_way(ctx, region, feet, target, [], "minecraft:raw_iron"))
        self.assertEqual([t for t in sent if t["type"] == "mine"], [])


if __name__ == "__main__":
    unittest.main()
