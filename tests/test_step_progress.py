""""No progress" must mean the step did nothing — not that the bag looks the same.

`seek` walks the body to where the work is. It is SUPPOSED to leave the bag untouched; arriving is the whole job.
The spin guard compared inventories, so every walk looked like a spin: on a fresh world the agent made a wooden
pickaxe and then failed `seek 1× stone` for the stone sword, the stone axe and the stone pickaxe in turn, each one
"succeeded twice without changing anything", each one cooling for a minute. It never made a stone tool at all.

Progress is any change in what the step could have changed: what is carried, or where the body stands.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402


class ProgressIncludesMoving(unittest.TestCase):
    def test_the_signature_notices_the_body_moving(self):
        from unittest import mock
        with mock.patch.object(brain, "Inventory", _bag), mock.patch.object(brain.nav, "feet_now", lambda: (0, 64, 0)):
            here = brain.progress_signature()
        with mock.patch.object(brain, "Inventory", _bag), mock.patch.object(brain.nav, "feet_now", lambda: (9, 64, 0)):
            there = brain.progress_signature()
        self.assertNotEqual(here, there, "a walk that arrived somewhere else is progress, whatever the bag holds")

    def test_standing_still_with_the_same_bag_is_still_a_spin(self):
        from unittest import mock
        with mock.patch.object(brain, "Inventory", _bag), mock.patch.object(brain.nav, "feet_now", lambda: (0, 64, 0)):
            self.assertEqual(brain.progress_signature(), brain.progress_signature())

    def test_it_survives_a_world_that_cannot_answer(self):
        from unittest import mock

        def broken():
            raise brain.McError("no game")
        with mock.patch.object(brain, "Inventory", broken):
            self.assertIsNone(brain.progress_signature())


class _bag:
    slots = [{"id": "minecraft:stone", "count": 1}]
    equipment = {}


if __name__ == "__main__":
    unittest.main()
