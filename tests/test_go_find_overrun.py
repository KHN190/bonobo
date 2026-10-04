"""Regression test: when a fallback search (go_find) overruns the step's tight local budget,
it must not raise api.Overrun out of dispatch.execute (which would classify as an interruption
and infinite-loop without cooling). Instead, go_find should report False and dispatch.execute
must raise the original NotAvailable failure so retry cools the action at this location."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, dispatch, nav, skillcore, world  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import inventory, memory, state  # noqa: E402

DIM = "minecraft:overworld"
FEET = (13022, 70, 12935)


class GoFindOverrunTest(unittest.TestCase):
    """When a local step cannot reach its target and fallback search exceeds the step's budget,
    dispatch.execute must raise NotAvailable so the task fails and cools down."""

    def test_overrun_in_go_find_refutes_step_price_globally(self):
        mem = memory()
        bag = world.Inventory(inventory(("stone_axe", 1)))
        ctx = skillcore.Context(mem, nav.Policy(), DIM)
        step = Step("gather", "minecraft:oak_log", 1, {"blocks": ["oak_log"]}, 19 * TICKS_PER_S)

        def fake_run_step(ctx, step, night, seek=True):
            return api.NotAvailable("no tree in sight can be walked to on this ground")

        def fake_go_find(ctx, step):
            raise api.Overrun("way to (13011, 64, 12912) ~73s > the step's 28s left",
                              pos=(13011, 64, 12912), remaining_s=73.0, spent=1.0)

        kinds = frozenset(s["id"] for s in bag.slots if s.get("count"))
        with mock.patch.object(dispatch, "run_step", fake_run_step), \
                mock.patch.object(dispatch, "go_find", fake_go_find), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                mock.patch.object(api, "get", lambda *a, **k: state(x=FEET[0] + .5, y=FEET[1], z=FEET[2] + .5)), \
                mock.patch.object(api.STATE, "feet_seen", FEET), \
                mock.patch.object(api.STATE, "kinds_seen", kinds):
            # Overrun is raised to pause and replan with the refuted price
            with self.assertRaises(api.Overrun):
                dispatch.execute(ctx, step, False)

            # Refutation must be recorded under target=None so Cost finds it
            state_sig = skillcore.ban_state(FEET, kinds)
            refuted_s = mem.refuted_s(step, None, state_sig)
            self.assertIsNotNone(refuted_s)
            self.assertGreaterEqual(refuted_s, 73.0)

    def test_overrun_with_target_pos_also_refutes_target_none(self):
        mem = memory()
        bag = world.Inventory(inventory(("stone_pickaxe", 1)))
        ctx = skillcore.Context(mem, nav.Policy(), DIM)
        step = Step("mine", "minecraft:raw_iron", 2, {"blocks": ["iron_ore"], "tier": 1}, 31 * TICKS_PER_S)
        target_pos = (12995, 60, 12951)

        def fake_run_step(ctx, step, night, seek=True):
            raise api.Overrun(f"way to {target_pos} ~99s > the step's 45s left",
                              pos=target_pos, remaining_s=99.0, spent=1.0)

        kinds = frozenset(s["id"] for s in bag.slots if s.get("count"))
        with mock.patch.object(dispatch, "run_step", fake_run_step), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                mock.patch.object(api, "get", lambda *a, **k: state(x=FEET[0] + .5, y=FEET[1], z=FEET[2] + .5)), \
                mock.patch.object(api.STATE, "feet_seen", FEET), \
                mock.patch.object(api.STATE, "kinds_seen", kinds):
            with self.assertRaises(api.Overrun):
                dispatch.execute(ctx, step, False)

            state_sig = skillcore.ban_state(FEET, kinds)
            # Both the specific target pos and target=None must be refuted
            self.assertGreaterEqual(mem.refuted_s(step, target_pos, state_sig), 99.0)
            self.assertGreaterEqual(mem.refuted_s(step, None, state_sig), 99.0)



if __name__ == "__main__":
    unittest.main()
