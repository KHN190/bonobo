"""contain_lava's needs (fluids.py:258): no lava in sight must never demand a building block just to be ASKED --
only fill_with_blocks (fluids.py:208), reached when lava is actually open, may say "no blocks to cover it". An
empty bag with no lava exposed must not fail contain_lava at all, lava or not, after any dug-way segment."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import fluids, skillcore  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402
from bonobo.world import Inventory  # noqa: E402

_EMPTY = Inventory({"slots": [], "equipment": {}})


def _ctx():
    return type("Ctx", (), {"policy": type("Policy", (), {"lava_ok": False})()})()


class ContainLava(unittest.TestCase):
    """Both rows run with an empty bag throughout (skillcore.Inventory mocked to it, so `needs` on the base --
    "building": 1 -- is checked against a real, known-empty bag rather than a live /inventory read)."""

    def test_no_lava_no_blocks_runs_clean(self):
        # must fail without this fix: needs={"building": 1} raises NeedMissing even with no lava exposed
        with mock.patch.object(skillcore, "Inventory", lambda *a, **k: _EMPTY), \
             mock.patch.object(fluids, "find", lambda *a, **k: []):
            got = fluids.contain_lava(_ctx())
        self.assertEqual(got, 0)

    def test_open_lava_no_blocks_fails_with_the_real_reason(self):
        with mock.patch.object(skillcore, "Inventory", lambda *a, **k: _EMPTY), \
             mock.patch.object(fluids, "find", lambda *a, **k: [{"x": 0, "y": 64, "z": 0}]), \
             mock.patch.object(fluids, "feet", lambda: (0, 64, 0)), \
             mock.patch.object(fluids, "_lava_region", lambda here, radius: object()), \
             mock.patch.object(fluids, "_open_lava", lambda region, here: [(0, 64, 0)]), \
             mock.patch.object(fluids, "body_state", lambda ctx, region=None, **extra: {"inv": _EMPTY, "feet": (0, 64, 0), "region": region}):
            with self.assertRaises(NotAvailable) as cm:
                fluids.contain_lava(_ctx())
        self.assertIn("no blocks to cover it", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
