"""Every note about the world is a clue, and a clue that the world contradicts is retired on the spot.

Recovery already works this way: walk to the death spot, and whether or not anything was there, the note is spent.
Nothing else does. The resource map still says there is a tree where a tree was felled an hour ago; a site still
says there is a furnace someone has since broken; a vein still says there is iron where the iron has been mined.
The planner prices all of them at face value, walks there, finds nothing, and prices them again next round.

One rule, one place: `mem.confirm(kind, pos, found)` — arriving is the moment a note is either confirmed or retired.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import memory  # noqa: E402


def a_memory():
    return memory.Memory(os.path.join(tempfile.mkdtemp(prefix="clue-"), "notes.json"))


class ArrivingSettlesIt(unittest.TestCase):
    def setUp(self):
        self.mem = a_memory()

    def test_a_resource_that_is_not_there_is_forgotten(self):
        self.mem.note_resource("tree", (10, 64, 10), "minecraft:overworld")
        self.assertTrue(self.mem.resources("tree", "minecraft:overworld"))
        self.mem.confirm("tree", (10, 64, 10), "minecraft:overworld", found=False)
        self.assertFalse(self.mem.resources("tree", "minecraft:overworld"),
                         "a note the world has answered must stop being an errand")

    def test_a_resource_that_is_there_stays(self):
        self.mem.note_resource("tree", (10, 64, 10), "minecraft:overworld")
        self.mem.confirm("tree", (10, 64, 10), "minecraft:overworld", found=True)
        self.assertTrue(self.mem.resources("tree", "minecraft:overworld"))

    def test_confirming_somewhere_else_leaves_it_alone(self):
        self.mem.note_resource("tree", (10, 64, 10), "minecraft:overworld")
        self.mem.confirm("tree", (99, 64, 99), "minecraft:overworld", found=False)
        self.assertTrue(self.mem.resources("tree", "minecraft:overworld"))

    def test_a_site_that_is_gone_is_forgotten(self):
        self.mem.add_site("shelter", (5, 64, 5), "minecraft:overworld", name="hut-1")
        self.mem.confirm("site", (5, 64, 5), "minecraft:overworld", found=False)
        self.assertFalse([s for s in self.mem.sites("minecraft:overworld") if s["name"] == "hut-1"])

    def test_confirming_what_was_never_noted_is_harmless(self):
        self.mem.confirm("tree", (1, 1, 1), "minecraft:overworld", found=False)


class TheWalkReportsBack(unittest.TestCase):
    """Whoever goes there must say what it found. Otherwise the rule exists and nothing uses it."""

    def test_seeking_confirms_what_it_finds(self):
        import inspect
        from bonobo.brain import Brain
        self.assertIn("confirm", inspect.getsource(Brain.go_find),
                      "arriving at a sought resource must confirm or retire the note")


if __name__ == "__main__":
    unittest.main()
