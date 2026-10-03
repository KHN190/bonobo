"""data.OVERRUN (the user's rule): a step's ways are held to OVERRUN × its as-run price (Step.est, nav.step_budget) —
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
        with nav.step_budget(61 * TICKS_PER_S), self.assertRaises(api.Overrun) as e:
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
        with nav.step_budget(10 * TICKS_PER_S), self.assertRaises(api.Overrun):
            self.way(10.0, part_s=20.0)
        self.assertEqual(self.parts, [SEGMENT])                   # must fail: the whole way dug

    def test_one_budget_across_the_steps_ways(self):
        """Two ways of one step: the second has only what the first left (must fail: a budget per try)."""
        with nav.step_budget(10 * TICKS_PER_S) as budget:
            self.way(10.0, part_s=5.0)
            self.assertEqual(budget.spent(), 10.0)
            self.parts.clear()                                    # the step's next way: a stand not yet held
            with self.assertRaises(api.Overrun):
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
        """The refutation is kept under the bag's state (ban_state): a different bag is a different state, so the
        same target prices fresh again (G3/E5: the fact only lifts by the state change it was measured in)."""
        from bonobo.world import Inventory
        from tests.world import inventory
        cost_of, step, near, _far, mem, bag = _refuted_scene()
        self._refute(mem, step, near, 0.5, bag)
        new_bag = Inventory(inventory(("stone_pickaxe", 1), ("torch", 4)))
        c = cost_of(new_bag)
        self.assertEqual(c.site(step), near)
        c.estimate(step)
        # must fail: the lifted refutation still priced from the old, refuted rest
        self.assertNotIn("refuted", step.parts)


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


class HuntChaseOverrun(unittest.TestCase):
    """The production path (dispatch.run_priced -> gather.hunt -> skill._drive -> nav.check_budget), accept7-shaped:
    a prey that never closes in (nav.chase stubbed "moved" every try, a fake clock 10s a chase). Priced 10s, its
    OVERRUN budget is 15s: stopped after 2 chases, before a 3rd is ever tried (base 2000ec2: no check_budget at all,
    every one of count*3+3 tries runs, red by assertion -- McError never raised)."""

    def test_stopped_before_the_3rd_chase(self):
        from bonobo import dispatch, gather, skillcore
        from bonobo.planner import Step
        from tests.world import inventory, memory, state

        clock = [0.0]
        chases = []
        prey = {"id": 7, "type": "minecraft:cow", "health": 10.0, "distance": 10.0, "x": 5.0, "y": 64.0, "z": 0.0}
        st = state(x=.5, y=64.0, z=.5)
        inv_payload = inventory()

        def api_get(path):
            if path.startswith("/state"):
                return st
            if path.startswith("/inventory"):
                return inv_payload
            raise AssertionError(f"unexpected api.get {path}")

        def chase(*a, **k):
            chases.append(1)
            clock[0] += 10.0
            return "moved", None

        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        step = Step("hunt", "minecraft:beef", 1, {"types": ["minecraft:cow"]}, 10 * TICKS_PER_S)
        with mock.patch.object(api, "get", api_get), mock.patch.object(api, "detail", lambda *a: None), \
                mock.patch.object(gather, "feet", lambda: (0, 64, 0)), \
                mock.patch.object(gather, "entities", lambda *a, **k: [prey]), \
                mock.patch.object(gather.nav, "chase", chase), \
                mock.patch.object(nav.time, "time", lambda: clock[0]), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                self.assertRaises(api.McError):
            dispatch.run_priced("minecraft:overworld", step, False,
                                lambda: gather.hunt(ctx, "minecraft:beef", 1, ("minecraft:cow",), False))
        self.assertLessEqual(len(chases), 2, "must fail: a 3rd chase tried past the step's budget")


class SeekLegOverrun(unittest.TestCase):
    """The production path (dispatch.run_priced -> explore.seek -> explore.seek_blocks -> skill._drive ->
    nav.check_budget): nothing in sight or remembered, so seek falls through to seek_blocks's `_search`, stubbed to
    a fixed-cost generator (a leg finds nothing, a fake clock +30s each -- what's under test is the skill-driver/
    budget interplay, not _search's own frontier search). Priced 20s, budget 30s (OVERRUN_FLOOR_S-clear): stopped
    after 2 legs, never the 6 of SEARCH_LEGS (base 2000ec2: no check_budget at all, red by assertion)."""

    def test_stopped_after_two_legs(self):
        from bonobo import dispatch, explore, skillcore
        from bonobo.planner import Step
        from tests.world import memory, state

        clock = [0.0]
        legs = []
        st = state(x=.5, y=64.0, z=.5)

        def api_get(path):
            if path.startswith("/state"):
                return st
            raise AssertionError(f"unexpected api.get {path}")

        def fake_search(ctx, kinds, look, radius, legs_n):
            for _ in range(legs_n):
                legs.append(1)
                clock[0] += 30.0
                yield (0, 0)
            raise api.NotAvailable("stub: never found")

        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        step = Step("seek", "minecraft:iron_ore", 1, {"kinds": ["minecraft:iron_ore"]}, 20 * TICKS_PER_S)
        with mock.patch.object(api, "get", api_get), mock.patch.object(explore, "log", lambda *a: None), \
                mock.patch.object(explore, "feet", lambda: (0, 64, 0)), \
                mock.patch.object(explore, "find", lambda *a, **k: []), \
                mock.patch.object(explore, "entities", lambda *a, **k: []), \
                mock.patch.object(explore, "_search", fake_search), \
                mock.patch.object(nav.time, "time", lambda: clock[0]), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                self.assertRaises(api.McError):
            dispatch.run_priced("minecraft:overworld", step, False,
                                lambda: explore.seek(ctx, ["minecraft:iron_ore"]))
        self.assertLessEqual(len(legs), 2, "must fail: a 3rd leg tried past the step's budget")


class OverrunIsReplannedNotFailed(unittest.TestCase):
    """brain.outcome_of(Overrun) == "interrupted" (data.EXCEPTIONS["Overrun"]: replan, layer:plan; arbiter.RESUME_OF
    "same"): Brain.failed returns before writing a retry entry or a ban (brain.py:368's early return) -- new API
    (api.Overrun, EXCEPTIONS/RESUME_OF rows) with no base equivalent: not red by assertion on 2000ec2 (AttributeError/
    KeyError there), unlike the production-path rows above."""

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
    nav.check_budget/afford stay no-ops however long the reflex runs. `budget=` is new API (no base signature):
    not red by assertion on 2000ec2 (TypeError there)."""

    def test_a_long_reflex_never_raises(self):
        from bonobo import dispatch
        from bonobo.planner import Step
        step = Step("skill", "shelter", 1, {}, 5 * TICKS_PER_S)

        def long_reflex():
            self.assertIsNone(nav.BUDGET[0], "must fail: a budget open under budget=False")
            for _ in range(5):
                nav.check_budget()               # must never raise: no budget is open
            nav.afford(1e9, (0, 64, 0))           # an absurd way price: still a no-op with no budget
            return "sheltered"

        with mock.patch.object(dispatch, "trace", lambda *a, **k: None):
            out = dispatch.run_priced("minecraft:overworld", step, False, long_reflex, budget=False)
        self.assertEqual(out, "sheltered")


if __name__ == "__main__":
    unittest.main()
