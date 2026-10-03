"""gather.hunt's detail lines: the prey chosen, what was in sight, the attack's answer, the bag through the sweep."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, gather, nav  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402

COW = {"id": 7, "type": "minecraft:cow", "x": 3.5, "y": 64.0, "z": 0.5, "distance": 2.0}


class Ctx:
    def blocked(self, key):
        return False

    def ban(self, key, s):
        pass


class Bag:
    def count(self, token):
        return 0


class HuntDetail(unittest.TestCase):
    def test_a_gone_target_is_said(self):
        # must fail: "dropped no beef" with nothing saying the attack found no target (20260930-033943)
        sight, said = [[COW]], []

        def attack(task, **k):
            sight[0] = []                       # the cow gone by the time the attack looked
            return {"status": "succeeded", "message": "target not found"}
        with mock.patch.object(gather, "entities", lambda r, types: list(sight[0])), \
                mock.patch.object(gather, "Inventory", Bag), mock.patch.object(api, "run", attack), \
                mock.patch.object(nav, "walk_sweep"), mock.patch.object(gather, "gained", lambda f, before: f()), \
                mock.patch.object(api, "detail", lambda *p: said.append(" ".join(map(str, p)))):
            with self.assertRaises(NotAvailable):
                for _ in gather.hunt.__wrapped__(Ctx(), "minecraft:beef", 1, ["minecraft:cow"], False):
                    pass
        text = "\n".join(said)
        for want in ("prey 7 at (3.5, 64.0, 0.5) 2.0 off", "7@2.0", "attack succeeded target not found",
                     "after: none", "beef 0 before, 0 after the attack, 0 after the sweep"):
            self.assertIn(want, text)


class AfterApproach(unittest.TestCase):
    """gather.after_approach: a moving animal is chased to where it is now, or the nearest other taken; never banned
    for walking off (acceptance 19:44:45: travel arrived, the pig 31 off, banned 300 s)."""

    def test_table(self):
        reach = gather.HUNT_REACH
        near = dict(COW, distance=reach)
        far = dict(COW, distance=reach + 1.0)
        other = dict(COW, id=8, distance=1.0)
        rows = [("within reach: attack it", [near], ("attack", near)),
                ("must fail: walked on but in sight: chase it, not a ban", [far, other], ("chase", far)),
                ("out of sight: the next nearest", [other], ("next", None))]
        for name, seen, want in rows:
            with self.subTest(name):
                self.assertEqual(gather.after_approach(seen, COW["id"], reach), want)


if __name__ == "__main__":
    unittest.main()
