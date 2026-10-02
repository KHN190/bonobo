"""Readings production computes and then never passes (findings: wiring breaks W4, W5)."""
import unittest
from unittest import mock

from bonobo import needs
from tests.world import cost, inventory, snapshot, state


class BedTonight(unittest.TestCase):
    def test_the_dimension_is_can_sleeps(self):
        """W4: whether a bed works off the Overworld is can_sleep's one judgement (must fail: a second check before it)."""
        n = needs.Needs(mock.Mock())
        from types import SimpleNamespace
        real = snapshot(state(dimension="minecraft:the_nether", timeOfDay=18000), inventory())
        snap = SimpleNamespace(night=True, dimension=real.dimension, inv=real.inv, state=real.state)
        with mock.patch.object(needs.survive, "can_sleep", lambda st: None), \
                mock.patch.object(needs.Needs, "overnight", lambda self, s, *a, **k: ("bed", 1.0, [])):
            self.assertTrue(n.bed_tonight(snap))


if __name__ == "__main__":
    unittest.main()
