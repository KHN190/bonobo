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


class AnUnreachedPreyIsBannedAlone(unittest.TestCase):
    def test_the_next_prey_is_hunted(self):
        far, near = dict(COW, id=8, x=40.5, distance=40.0), dict(COW, id=9, x=3.5, distance=3.0)
        banned, attacked = set(), []

        class Bans(Ctx):
            def blocked(self, key):
                return key in banned

            def ban(self, key, s):
                banned.add(key)

        class Held:
            def count(self, token):
                return len(attacked)
        sight = lambda r, types: [e for e in (far, near) if (e["id"], 0, 0) not in banned]     # noqa: E731
        with mock.patch.object(gather, "entities", sight), mock.patch.object(gather, "Inventory", Held), \
                mock.patch.object(nav, "arrived_near", lambda *a, **k: False), \
                mock.patch.object(gather._k, "under_rock", lambda *a: False), \
                mock.patch.object(api, "get", lambda path: {"skyLight": 15}), \
                mock.patch.object(api, "run", lambda task, **k: attacked.append(task["entity"]) or {"status": "succeeded"}), \
                mock.patch.object(nav, "walk_sweep"), mock.patch.object(gather, "gained", lambda f, before: f()), \
                mock.patch.object(gather, "feet", lambda: (0, 64, 0)), mock.patch.object(api, "detail"):
            for _ in gather.hunt.__wrapped__(Bans(), "minecraft:beef", 1, ["minecraft:cow"], False):
                pass
        # must fail: the hunt failed over the one cow it could not reach (NavFailed), the next never tried
        self.assertEqual((attacked, banned), ([9], {(8, 0, 0)}))


if __name__ == "__main__":
    unittest.main()
