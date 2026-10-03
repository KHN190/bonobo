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
        # must fail: a cow already dying must never be offered as prey ("target not found" once it's gone)
        self.assertEqual([e["id"] for e in got], [alive["id"]])
        self.assertEqual(living([arrow]), [arrow])

    def test_a_row_with_no_id_is_priced(self):
        from bonobo.cost import Cost
        cow = {"type": COW, "distance": 6.0, "health": 10.0}      # the jar's row may carry no id (check dim trace no_id)
        from tests.world import inventory, memory, state
        snap = world.Snapshot.from_readings(state(), inventory(), {}, [cow])
        with mock.patch.object(api, "api", side_effect=AssertionError("an estimate read the world")):
            # must fail: KeyError 'id' (cost._entity read the ban by the row's id)
            self.assertEqual(Cost(snap, memory())._entity([COW]), 6.0)


if __name__ == "__main__":
    unittest.main()
