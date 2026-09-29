"""Decor is never a threat (hello2 08:39: the bunker hall's armor stands, frames, carts), and a bait decision names
the creeper it baits."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import threat  # noqa: E402
from bonobo.memory import HOME_ENTITIES  # noqa: E402


class Decor(unittest.TestCase):
    def test_home_entities_are_no_rows(self):
        near = [{"id": i, "type": t, "x": 56.5, "y": 43.0, "z": -98.5, "health": 20.0}
                for i, t in enumerate(HOME_ENTITIES)]
        # must fail: an armor stand (a LivingEntity with health) alone yields a threat
        self.assertEqual(threat.hostile_rows(near, {}, 1.0, here=(55.5, 43.0, -107.5)), [])

    def test_bait_names_its_creeper(self):
        creeper = ((60.5, 43.0, -107.5), 3.0, (0.0, 0.0, 0.0), "minecraft:creeper", 1.0, 1.0)
        opt = threat.bait_option((55.5, 43.0, -107.5), [creeper], [4242], [0], set(), 7.5, 0.0)
        self.assertIn("creeper 4242", opt.why)


if __name__ == "__main__":
    unittest.main()
