"""/entities lists a living entity while it dies (health 0): what the bot reads is the living only (data.living, one
filter for world.entities and api's own read)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, world  # noqa: E402
from bonobo.data import living  # noqa: E402

COW = "minecraft:cow"


class Living(unittest.TestCase):
    def test_a_dying_cow_is_no_prey(self):
        dying = {"id": 2632, "type": COW, "distance": 1.7, "health": 0.0}
        alive = {"id": 3001, "type": COW, "distance": 6.0, "health": 10.0}
        arrow = {"id": 7, "type": "minecraft:arrow", "distance": 2.0}         # no health: not a living entity
        with mock.patch.object(api, "get", lambda path: {"entities": [dying, alive, arrow]}):
            got = world.entities(64, [COW])
        # must fail: the cow killed by the last row's setup offered as prey (hunt 03:54:44, "target not found")
        self.assertEqual([e["id"] for e in got], [alive["id"]])
        self.assertEqual(living([arrow]), [arrow])


if __name__ == "__main__":
    unittest.main()
