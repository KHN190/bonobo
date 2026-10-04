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

    def test_overrun_in_go_find_raises_not_available(self):
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
            # Without the fix, this raises api.Overrun (interruption loop).
            # With the fix, it must raise api.NotAvailable (clean failure + cooldown).
            with self.assertRaises(api.NotAvailable):
                dispatch.execute(ctx, step, False)


if __name__ == "__main__":
    unittest.main()
