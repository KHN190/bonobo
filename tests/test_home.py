"""The player's home: a site whose box is never broken (but for our own blocks), whose beds, chests and stations are
used as they stand, whose decor is never struck, where nothing is poured or lit; a rescue at critical hp may dig."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, memory, nav  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

DIM = "minecraft:overworld"
LO, HI = (0, 64, 0), (9, 70, 9)
BLOCKS = {(1, 64, 1): "minecraft:red_bed", (2, 64, 1): "minecraft:red_bed", (5, 64, 5): "minecraft:chest",
          (7, 64, 2): "minecraft:crafting_table", (7, 64, 4): "minecraft:furnace", (0, 64, 0): "minecraft:stone_bricks",
          (3, 65, 3): "minecraft:air"}


def a_home(tmp):
    mem = memory.Memory(os.path.join(tmp, "notes.json"))
    mem.add_home("home", LO, HI, DIM, BLOCKS)
    return mem


class TheHome(unittest.TestCase):
    def test_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            parts = mem.homes(DIM)[0]["parts"]
            self.assertEqual(len(parts["beds"]), 2)
            self.assertEqual(parts["chests"], [[5, 64, 5]])
            self.assertEqual(sorted(b for b, _p in parts["stations"]), ["crafting_table", "furnace"])
            self.assertIn((7, 64, 2), {tuple(s["pos"]) for s in mem.stations(DIM)}, "its stations are memory's")
            self.assertEqual(mem.home_part("chests", DIM, (4, 64, 4)), (5, 64, 5),
                             "must fail: the home chest not offered for storing")
            self.assertEqual(mem.home_part("stations", DIM, (4, 64, 4), "crafting_table"), (7, 64, 2))
            self.assertIsNone(mem.home_part("chests", DIM, (40, 64, 40)), "outside, not asked for anywhere")

    def test_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            mem.note_placed((4, 65, 4), DIM)
            p = mem.protected_cells(DIM)
            rows = [("a home block", (0, 64, 0), True), ("air in the box: nothing may be broken there", (3, 66, 3), True),
                    ("must fail: our own block inside, ours to take back", (4, 65, 4), False),
                    ("outside the box", (20, 64, 0), False)]
            for name, cell, want in rows:
                with self.subTest(name):
                    self.assertEqual(cell in p, want)
            self.assertLess(len(set(p)), 100, "the box is asked, never listed")

    def test_refusals(self):
        homes = [{"snapshot": {"lo": list(LO), "hi": list(HI)}}]
        ours = {(4, 65, 4)}
        at = {1: ("minecraft:armor_stand", (3, 65, 3)), 2: ("minecraft:zombie", (3, 65, 3)),
              3: ("minecraft:armor_stand", (30, 65, 3))}
        # (situation, task, critical allowance) → refused
        rows = [("must fail: a home block broken (gather, unstuck)", {"type": "mine", "x": 0, "y": 64, "z": 0}, False, True),
                ("our own block taken back", {"type": "mine", "x": 4, "y": 65, "z": 4}, False, False),
                ("at critical hp a rescue may dig", {"type": "mine", "x": 0, "y": 64, "z": 0}, True, False),
                ("must fail: an armor stand in the home struck", {"type": "attack", "entity": 1}, False, True),
                ("a zombie in the home is fought", {"type": "attack", "entity": 2}, False, False),
                ("an armor stand outside is none of the home's", {"type": "attack", "entity": 3}, False, False),
                ("must fail: water poured inside", {"type": "place", "item": "minecraft:water_bucket",
                                                    "x": 3, "y": 65, "z": 3}, False, True),
                ("a block placed inside: allowed", {"type": "place", "item": "minecraft:cobblestone",
                                                    "x": 3, "y": 65, "z": 3}, False, False),
                ("opening a chest inside: allowed", {"type": "use", "x": 5, "y": 64, "z": 5}, False, False)]
        for name, task, allow, refused in rows:
            with self.subTest(name):
                why = memory.home_refusal(task, homes, ours, lambda eid: at.get(eid), allow_break=allow)
                self.assertEqual(why is not None, refused, why)

    def test_the_door_refuses(self):
        """api.post: the guard sees every task (a missed caller can't break the home)."""
        def guard(task):
            if task.get("type") == "mine":
                raise api.NotAvailable("part of the home")
        with mock.patch.object(api, "GUARD", guard), mock.patch.object(api, "api", return_value={"tasks": []}) as sent:
            with self.assertRaises(api.NotAvailable):
                api.post("/task?wait=0", {"tasks": [{"type": "mine", "x": 0, "y": 64, "z": 0}]})
            self.assertFalse(sent.called, "must fail: the break went out")


class UsedAsItStands(unittest.TestCase):
    def test_the_home_table(self):
        """Crafting in the home: its table, walked to — no new one placed, none taken back."""
        from bonobo import craft
        from tests.world import bag, inventory
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            ctx = mock.Mock(mem=mem, dimension=DIM)
            for name, home, want in [("the home's table", True, []),
                                     ("must fail: no home table: one placed and taken back", False, ["place", "mine"])]:
                with self.subTest(name):
                    sent = []
                    inv = bag(inventory(oak_planks=12, cobblestone=3))
                    with mock.patch.object(craft, "Inventory", lambda: inv), \
                            mock.patch.object(craft, "find", lambda *a, **k: []), \
                            mock.patch.object(craft, "feet", lambda: (4, 64, 4) if home else (40, 64, 40)), \
                            mock.patch.object(craft.nav, "arrived", lambda *a, **k: True), \
                            mock.patch.object(craft, "free_spots_here", lambda limit=1: [(41, 64, 40)]), \
                            mock.patch.object(craft, "close_screen", lambda: None), \
                            mock.patch.object(craft, "_standing", lambda *a: False), \
                            mock.patch.object(craft, "run_split", lambda tasks, wait: sent.extend(tasks)):
                        craft._sitting(ctx, [("minecraft:stick", 1), ("minecraft:crafting_table", 1),
                                             ("minecraft:stone_pickaxe", 1)])
                    self.assertEqual([t["type"] for t in sent if t["type"] in ("place", "mine")], want)

    def test_gathering_skips_the_home(self):
        from bonobo import gather
        p = memory.Protected((), [(LO, HI)])
        found = [{"x": 3, "y": 65, "z": 3, "block": "minecraft:oak_log"}, {"x": 30, "y": 65, "z": 3, "block": "minecraft:oak_log"}]
        hits = gather.seek_hits(["minecraft:oak_log"], found, 16, lambda p: False, p)
        self.assertEqual([h["x"] for h in hits], [30], "must fail: a log in the home offered for gathering")


class TheGuard(unittest.TestCase):
    """brain.home_guard: api's door for the home — refusals raise, a critical break is said, our placing is noted."""

    def test_rows(self):
        from types import SimpleNamespace
        from bonobo import brain, events
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            me = SimpleNamespace(mem=mem)
            said = []
            with mock.patch.object(api.STATE, "dim_seen", DIM), \
                    mock.patch.object(events, "emit", lambda kind, line, *a, **k: said.append(kind)):
                brain.Brain.home_guard(me, {"type": "place", "item": "minecraft:dirt", "x": 3, "y": 65, "z": 3})
                self.assertIn((3, 65, 3), mem.placed_in_home(DIM), "our block inside, noted")
                brain.Brain.home_guard(me, {"type": "mine", "x": 3, "y": 65, "z": 3})
                self.assertNotIn((3, 65, 3), mem.placed_in_home(DIM), "taken back: no longer ours to note")
                with self.assertRaises(api.NotAvailable):
                    brain.Brain.home_guard(me, {"type": "mine", "x": 0, "y": 64, "z": 0})
                self.assertEqual(said, [], "must fail: a refused break said as done")
                with api.home_break_allowed("buried at 3 hp"):
                    brain.Brain.home_guard(me, {"type": "mine", "x": 0, "y": 64, "z": 0})
                self.assertEqual(said, ["home_break"], "a critical break is an event")


class NoDigThroughTheHome(unittest.TestCase):
    def test_avoid_carries_the_home(self):
        p = memory.Protected((), [(LO, HI)])
        got = nav.with_avoid({"type": "mine", "x": 5, "y": 64, "z": 12}, p)
        cells = {(c["x"], c["y"], c["z"]) for c in got["avoid"]}
        self.assertIn((5, 64, 9), cells, "must fail: an approach digging the home wall")
        self.assertTrue(all(memory.in_box((LO, HI), c) for c in cells))


class Unbury(unittest.TestCase):
    def test_step_out(self):
        from bonobo import survive
        feet = (5, 65, 5)
        floor = {(x, 64, z): "stone" for x in range(4, 7) for z in range(4, 7)}
        walls = {(x, y, z): "stone" for x in range(4, 7) for z in range(4, 7) for y in (65, 66) if (x, z) != (5, 5)}
        open_side = FakeRegion((4, 64, 4), (6, 67, 6), {**floor, **{c: n for c, n in walls.items() if c[0] != 6}})
        shut = FakeRegion((4, 64, 4), (6, 67, 6), {**floor, **walls})
        self.assertEqual(survive.step_out_cell(open_side, feet), (6, 65, 5), "a free side: stepped out, nothing broken")
        self.assertIsNone(survive.step_out_cell(shut, feet), "must fail: walled in read as a way out")

    def test_a_home_block_only_at_critical(self):
        from bonobo import survive
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            ctx = mock.Mock(mem=mem, policy=mock.Mock(protected=mem.protected_cells(DIM)))
            shut = FakeRegion((4, 63, 4), (6, 67, 6), {(x, y, z): "stone" for x in range(4, 7) for z in range(4, 7)
                                                       for y in (64, 65, 66) if (x, y, z) != (5, 65, 5)})
            for name, hp, want in [("must fail: full hp, a home block broken", 20.0, api.NotAvailable),
                                   ("critical hp: broken, allowed", 3.0, None)]:
                with self.subTest(name):
                    state = {"blockX": 5, "blockY": 65, "blockZ": 5, "y": 65.0, "health": hp, "dimension": DIM}
                    sent = []
                    with mock.patch.object(survive.api, "get", return_value=state), \
                            mock.patch.object(survive, "Region", lambda lo, hi: shut), \
                            mock.patch.object(survive.api, "run", side_effect=lambda t, **k: sent.append(
                                (t, api.STATE.home_break))):
                        gen = survive.unbury.__wrapped__(ctx)
                        if want is not None:
                            with self.assertRaises(want):
                                next(gen)
                            self.assertEqual(sent, [])
                        else:
                            next(gen)
                            self.assertEqual(sent[0][0]["type"], "mine")
                            self.assertIsNotNone(sent[0][1], "the break went out under the allowance")


if __name__ == "__main__":
    unittest.main()
