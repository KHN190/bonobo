"""Helpers that were written twice and now live in one place: a pure table each (normal, edge, must-fail)."""
import unittest

from bonobo import data


class CannotReach(unittest.TestCase):
    """data.cannot_reach: the cells a mod answer names as "cannot reach x, y, z" (end and skills read it)."""

    ROWS = [("one cell", "cannot reach 1, 64, -3", {(1, 64, -3)}),
            ("two cells in one answer", "2 of 3 steps failed: cannot reach 1, 2, 3; cannot reach -4, 5, -6",
             {(1, 2, 3), (-4, 5, -6)}),
            ("must fail: another refusal names no cell", "no path found (108 positions explored)", set()),
            ("must fail: no message", None, set()),
            ("must fail: a cell without the words", "stuck at 1, 2, 3", set())]

    def test_rows(self):
        for name, message, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(data.cannot_reach(message), want)

    def test_every_reader_uses_it(self):
        from bonobo import end
        failed = [{"status": "failed", "message": "cannot reach 1, 2, 3"},
                  {"status": "succeeded", "message": "cannot reach 9, 9, 9"}]
        self.assertEqual(end.unreachable(failed), {(1, 2, 3)})


class BagSlots(unittest.TestCase):
    """world.screen_slot and world.BAG_SLOTS: a bag slot's id in the screen a /click names; the bag's size behind
    every "free slots" (Inventory.free_slots)."""

    def test_screen_slot(self):
        from bonobo.world import screen_slot
        rows = [("hotbar first", 0, 36), ("hotbar last", 8, 44), ("the bag above it", 9, 9), ("the last bag slot", 35, 35),
                ("must fail: a hotbar slot is never clicked as itself", 3, 39)]
        for name, slot, want in rows:
            with self.subTest(name):
                self.assertEqual(screen_slot(slot), want)

    def test_free_slots(self):
        from tests.world import bag, inventory
        rows = [("empty", {}, 36), ("one stack", {"dirt": 1}, 35), ("two kinds", {"dirt": 1, "stone": 64}, 34),
                ("must fail: a full stack still takes one slot", {"stone": 64}, 35)]
        for name, counts, want in rows:
            with self.subTest(name):
                self.assertEqual(bag(inventory(**counts)).free_slots(), want)



class RoomClicks(unittest.TestCase):
    """skills.room_clicks: the throws that make room (crafting's result, a cache chest), the cheapest stacks first."""

    def test_rows(self):
        from bonobo.craft import room_clicks
        from bonobo.world import screen_slot
        from tests.world import bag, inventory
        slots = bag(inventory(rotten_flesh=5, cobblestone=10, diamond=3, poisonous_potato=2)).slots
        at = {s["id"].split(":")[1]: s["slot"] for s in slots}
        price = {"minecraft:rotten_flesh": 1, "minecraft:poisonous_potato": 1, "minecraft:cobblestone": 2,
                 "minecraft:diamond": 500}.get
        rows = [("one: a junk stack", 1, ["poisonous_potato"]),
                ("two: both junk stacks", 2, ["poisonous_potato", "rotten_flesh"]),
                ("none asked: nothing thrown", 0, []),
                ("must fail: never the diamond, even asked for three", 3, None)]
        for name, need, want in rows:
            with self.subTest(name):
                got = room_clicks(slots, need, price)
                self.assertLessEqual(len(got), need)
                self.assertNotIn(screen_slot(at["diamond"]), [c["slot"] for c in got])
                self.assertTrue(all(c["action"] == "THROW" for c in got))
                if want is not None:
                    self.assertEqual(sorted(c["slot"] for c in got), sorted(screen_slot(at[w]) for w in want))


class MemoTtl(unittest.TestCase):
    """data.memo_ttl: the one short-lived memo (perception's ground, needs' plan prices)."""

    def test_rows(self):
        # (situation, cache before, key, now, one) → (value returned, made again?, keys after)
        rows = [("fresh hit: kept value, nothing made", {"a": (10.0, "old")}, "a", 11.0, False, ("old", False, ["a"])),
                ("expired: made again", {"a": (10.0, "old")}, "a", 13.0, False, ("new", True, ["a"])),
                ("another key: made, both kept", {"a": (10.0, "old")}, "b", 11.0, False, ("new", True, ["a", "b"])),
                ("one slot: another key forgets the first", {"a": (10.0, "old")}, "b", 11.0, True, ("new", True, ["b"])),
                ("must fail: exactly at the ttl is stale", {"a": (10.0, "old")}, "a", 12.0, False, ("new", True, ["a"]))]
        for name, cache, key, now, one, want in rows:
            with self.subTest(name):
                made = []
                got = data.memo_ttl(cache, key, 2.0, lambda: made.append(1) or "new", now, one=one)
                self.assertEqual((got, bool(made), sorted(cache)), want)



class ReflexesReadTheRoundsLook(unittest.TestCase):
    """reflexes.in_sight: a reflex asks the round's one batched look (world.nearest), never /find while deciding."""

    def test_rows(self):
        from types import SimpleNamespace
        from unittest import mock
        from bonobo import reflexes, world
        seen = [{"block": "minecraft:red_bed", "distance": 30.0}, {"block": "minecraft:lava", "distance": 5.0},
                {"block": "minecraft:barrel", "distance": 4.0}]
        asked = []

        def get(path):
            asked.append(path)
            return {"blocks": seen}
        snap = SimpleNamespace(feet=(0, 64, 0), dimension="minecraft:overworld")
        rows = [("a bed 30 away, within 48", ["red_bed", "white_bed"], 48, True),
                ("a chest group: the barrel 4 away, within 6", ["chest", "barrel"], 6, True),
                ("must fail: lava 5 away is not within 3", ["lava"], 3, False),
                ("must fail: nothing of it seen", ["diamond_ore"], 48, False)]
        with mock.patch.object(world.api, "get", get), mock.patch.object(world, "_PER_BLOCK", [True]), \
                mock.patch.dict(world._SIGHT, {"key": None, "t": 0.0, "near": {}}):
            for name, kinds, radius, want in rows:
                with self.subTest(name):
                    self.assertEqual(reflexes.in_sight(snap, kinds, radius), want)
        self.assertEqual(len(asked), 1, "one look answers every reflex ask in the round")


if __name__ == "__main__":
    unittest.main()
