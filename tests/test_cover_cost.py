"""W3: the shelter a hazard's last way runs (needs.cover) is priced by the brain's cost model on the skill context —
its memory, its blacklist and its movement policy — never a cost model that cannot see what is banned here."""
import unittest
from unittest import mock

from bonobo import needs
from bonobo.api import NotAvailable


class Ctx:
    mem, blacklist, policy = object(), {(1, 2, 3): 9e18}, object()


class CoverCost(unittest.TestCase):
    def test_the_contexts_readings_reach_the_cost(self):
        seen = {}

        def recorder(snap, mem=None, blacklist=None, known=None, finds=None, policy=None, ripe=None, reserved=(),
                     region=None, stop=None):
            seen.update(mem=mem, blacklist=blacklist, policy=policy)
            raise NotAvailable("recorded")
        from tests.world import inventory, state
        with mock.patch.object(needs, "Cost", recorder), \
                mock.patch.object(needs, "Inventory", lambda: __import__("bonobo.world", fromlist=["Inventory"]).Inventory(inventory())):
            with self.assertRaises(NotAvailable):
                needs.cover(Ctx(), state())
        # must fail on the old construction: no blacklist (None), no policy
        self.assertEqual((seen["mem"], seen["blacklist"], seen["policy"]), (Ctx.mem, Ctx.blacklist, Ctx.policy))


if __name__ == "__main__":
    unittest.main()
