"""Every site's way is priced by the door's own reach over the round's read ground (cost.Cost.reach) — a chest, a place,
a mob as a mine is — never by the straight line: one the door cannot get to is refused, and a banned or unreachable
source is never offered beside the leg (Cost.enroute)."""
import math
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import api, goals, lifecycle, nav, planner, skillcore, tasks, world  # noqa: E402
from bonobo import cost as costmod  # noqa: E402
from tests.world import brain_fixture, inventory, memory, round_ctx, state  # noqa: E402

D = "minecraft:overworld"
ORE = (40, 64, 0)
KIT = (("stone_pickaxe", 1), ("stone_sword", 1), ("stone_axe", 1), ("crafting_table", 1), ("furnace", 1), ("bread", 8))
FLAT, TOP = (20, 64, 1), (20, 72, 1)       # a chest on the ground; one on an 8-high stone cliff, no block to climb by


def ground(chest, cliff=0):
    """Dirt over stone to an iron ore 40 east; a `chest`, on a stone cliff `cliff` high when one is asked."""
    blocks = {(x, y, z): "dirt" if y >= 62 else "stone" for x in range(-8, 49) for z in range(-8, 9)
              for y in range(58, 64)}
    blocks[ORE] = "iron_ore"
    for x in range(chest[0] - 2, chest[0] + 3):
        for z in range(chest[2] - 2, chest[2] + 3):
            for y in range(64, 64 + cliff):
                blocks[(x, y, z)] = "stone"
    blocks[chest] = "chest"
    return world.Region.of((-8, 58, -8), (48, 80, 8), blocks)


def proposals(chest, cliff=0, contents=None, ban=None):
    """The round's held plan for a raw iron, a `chest` beside the leg (its `contents` remembered, else unopened), no
    way block in the bag, `ban` banned. Returns the brain."""
    tmp = tempfile.mkdtemp()
    lifecycle.reset_all(caches=False)
    with mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")), \
            mock.patch.object(api, "api", side_effect=AssertionError("the round read the world")):
        b = brain_fixture()
        b.mem.clock = 0
        if ban is not None:
            b.blacklist = world.Versioned()
            b.blacklist[ban] = skillcore.Ban(time.time() + 600)
        hits = {"iron_ore": [{"x": ORE[0], "y": ORE[1], "z": ORE[2], "distance": 40.0, "block": "minecraft:iron_ore"}],
                "chest": [{"x": chest[0], "y": chest[1], "z": chest[2], "distance": float(chest[0]),
                           "block": "minecraft:chest"}]}
        if contents is not None:
            b.mem.note_container(chest, D, [{"id": i, "count": n, "slot": k} for k, (i, n) in enumerate(contents)])
        snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), world.Inventory(inventory(*KIT)), hits, [],
                                            ground(chest, cliff))
        b.round_snap = snap
        tasks.add(goals.have(("minecraft:raw_iron", 1)))
        b.plan_proposals(snap, round_ctx(b, snap))
        return b


def held_at(b, pos):
    return [st.kind for st in (b.needs_plan or {}).get("steps", ()) if st.detail.get("pos") == list(pos)]


class ACliffChestIsNotOnTheWay(unittest.TestCase):
    """An unopened chest beside the leg: on the ground it is looked into; on a cliff the bag cannot climb, never."""

    def test_rows(self):
        rows = [("on the ground: looked into", FLAT, 0, ["look"]),
                ("must fail on the base: atop the cliff, priced as the straight walk", TOP, 8, [])]
        for name, chest, cliff, want in rows:
            with self.subTest(name):
                b = proposals(chest, cliff)
                self.assertEqual(held_at(b, chest), want, b.enroute_choice)


class ABannedChestIsNotOffered(unittest.TestCase):
    """A chest's cell banned (its last try failed): the next round offers it beside the leg no more."""

    def test_rows(self):
        rows = [("unopened, its look", None), ("remembered diamonds, their withdraw", [("minecraft:diamond", 2)])]
        for name, contents in rows:
            with self.subTest(name):
                b = proposals(FLAT, contents=contents, ban=FLAT)
                offered = b.enroute_choice.candidate if b.enroute_choice is not None else ""
                # must fail on the base: the banned chest still weighed, its look even held
                self.assertNotIn(str(FLAT), offered)
                self.assertEqual(held_at(b, FLAT), [])


class AWayOverACliffIsNotTheStraightLine(unittest.TestCase):
    """A withdraw from, or a walk to, the cliff's top: priced past the straight walk, or refused (sought)."""

    def test_rows(self):
        rows = [("a withdraw from the chest atop", planner.Step("withdraw", "minecraft:diamond", 1, {"pos": list(TOP)})),
                ("a walk to stand beside it", planner.Step("goto", "here", 1, {"pos": [TOP[0], TOP[1], TOP[2] - 1]}))]
        for name, step in rows:
            with self.subTest(name):
                lifecycle.reset_all(caches=False)
                with mock.patch.object(api, "api", side_effect=AssertionError("the price read the world")):
                    snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), world.Inventory(inventory(*KIT)), {},
                                                        [], ground(TOP, 8))
                    cost = costmod.Cost(snap, memory(), world.Versioned(), policy=nav.Policy())
                    parts = cost._walk_parts(step)
                line = costmod.walk_ticks(math.dist(snap.feet, step.detail["pos"]))
                # must fail on the base: the straight walk, no seek
                self.assertTrue(parts["seek"] > 0 or parts["walk"] > line, (parts, line))


if __name__ == "__main__":
    unittest.main()
