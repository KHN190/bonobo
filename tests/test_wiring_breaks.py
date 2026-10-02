"""Readings production computes and then never passes (findings: wiring breaks W4, W5)."""
import unittest
from unittest import mock

from bonobo import actions, needs
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


class Reachable(unittest.TestCase):
    def test_a_route_not_found_is_no_at(self):
        """W5: standing beside one whose route the game refused is not "at" it (must fail: reachable never passed)."""
        from bonobo import decompose
        c = cost(snapshot(state(), inventory()))
        seen = {}

        def state_of(snap, mem, extra=None, reachable=None):
            seen["reachable"] = reachable
            raise decompose.Unplannable("probe")
        with mock.patch.object(actions, "state_of", state_of), mock.patch.object(c, "mem", object()):
            with self.assertRaises(decompose.Unplannable):
                decompose.SOLVERS["solve"](c.snap.inv, [("minecraft:stick", 1)], c)
        self.assertIsNotNone(seen.get("reachable"))

    def test_reachable_reads_the_route_cache(self):
        from bonobo import cost as costmod
        c = cost(snapshot(state(), inventory()))
        where = (5, 64, 5)
        with mock.patch.object(costmod.Cost, "where", lambda self, kinds: where), \
                mock.patch.dict(costmod.ROUTES, {costmod.route_key(where, 2.0, costmod.NAV_NODES): (False, None)}):
            self.assertFalse(c.reachable(["coal_ore"]))
        with mock.patch.object(costmod.Cost, "where", lambda self, kinds: None):
            self.assertTrue(c.reachable(["coal_ore"]))


if __name__ == "__main__":
    unittest.main()
