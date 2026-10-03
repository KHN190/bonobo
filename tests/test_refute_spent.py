"""dispatch.execute's refuted price (api.Overrun → Memory.refute): a re-estimate taken mid-overrun can be cheaper
than what the step already burned (the world moved on while digging), so the price written must never undercut the
seconds already spent plus the step's own price — else replanning picks the same step forever at the same low price.
Imports only what the base (before the fix) has too: red there by its assertion (recorded price == the bare
re-estimate), not by an ImportError."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, dispatch, nav, skillcore  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import memory  # noqa: E402

PRICE_S = 10.0          # the step's own estimate
SPENT_S = 16.0          # past OVERRUN(1.5)*PRICE_S=15s when check_budget fires
REMAINING_S = 8.0       # the re-estimate taken at the moment of overrun — cheaper than PRICE_S alone


class RefutedPriceFloorsAtSpentPlusPrice(unittest.TestCase):
    """A step priced 10s, run 16s over before an Overrun's re-estimate (8s) is taken: the recorded price must be at
    least 16+10=26s (spent + its own price), never the bare 8s re-estimate (must fail: a cheap re-estimate recorded,
    the same step picked again at the same low price next round)."""

    def test_recorded_price_is_at_least_spent_plus_own_price(self):
        clock = [0.0]
        target = (5, 64, 5)
        step = Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore"], "breaks": 1}, int(PRICE_S * TICKS_PER_S))
        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")

        def fake_run_step(ctx, step, night, seek=True):
            clock[0] = SPENT_S
            api.check_budget(target=target, remaining_s=REMAINING_S)

        with mock.patch.object(api.time, "time", lambda: clock[0]), \
                mock.patch.object(dispatch, "run_step", fake_run_step), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                self.assertRaises(api.Overrun):
            dispatch.execute(ctx, step, False)

        recorded, _state = ctx.mem.refuted[ctx.mem._refuted_key(step, target)]
        self.assertGreater(recorded, PRICE_S, "must fail: the bare re-estimate recorded, not floored by spend")
        self.assertGreaterEqual(recorded, SPENT_S + PRICE_S)


if __name__ == "__main__":
    unittest.main()
