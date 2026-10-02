"""C6: a carried bed gets a room (bed_room_tasks), its box lit when that pays (torch_cover, light_pays), the vanilla
gate read before it is placed (sleep_gate, 1.21.11 trySleep's order), and it is always taken back (take_bed)."""
import unittest
from unittest import mock

from bonobo import survive
from tests.world import FakeRegion, bag, inventory

FEET = (0, 64, 0)
NIGHT = 18000


def ground(over=None):
    """Grass to y 63 over a 9×9 box, air above (the feet at y 64)."""
    blocks = {(x, y, z): "grass_block" for x in range(-4, 5) for z in range(-4, 5) for y in range(60, 64)}
    blocks.update(over or {})
    return FakeRegion((-4, 60, -4), (4, 68, 4), {c: b for c, b in blocks.items() if b != "air"})


def state(**kw):
    s = {"x": 0.5, "y": 64.0, "z": 0.5, "dimension": "minecraft:overworld", "timeOfDay": NIGHT}
    s.update(kw)
    return s


class BedRoom(unittest.TestCase):
    def test_rows(self):
        flat = ground()
        trees = ground({(x, y, z): "oak_log" for x in range(-4, 5) for z in range(-4, 5) for y in (64, 65)
                        if (x, z) != (0, 0)})
        # (situation, region, protected) → (tasks?, a room found)
        rows = [("flat ground: a room as it stands, nothing dug", flat, (), 0, True),
                ("logs 2 high all round: the cheapest room is on top of them, its 2 log cells dug", trees, (), 2, True),
                ("must fail: every candidate in the home", flat,
                 {(x, y, z) for x in range(-4, 5) for z in range(-4, 5) for y in (63, 64, 65)}, None, False)]
        for name, region, protected, n, found in rows:
            with self.subTest(name):
                tasks, cells, _s, why = survive.bed_room_tasks(region, FEET, protected, [], None)
                self.assertEqual(cells is not None, found, why)
                if found:
                    self.assertEqual(len(tasks), n)
                    if not tasks:
                        self.assertTrue(survive.bed_room(region, *cells))

    def test_predicate(self):
        foot, head = (1, 64, 0), (2, 64, 0)
        rows = [("air on grass, air above", ground(), True),
                ("must fail: a log over the head (obstructed)", ground({(2, 65, 0): "oak_log"}), False),
                ("must fail: no floor under the head", ground({(2, 63, 0): "air"}), False),
                ("a slab over the head does not bury", ground({(2, 65, 0): "oak_slab"}), True)]
        for name, region, want in rows:
            with self.subTest(name):
                self.assertEqual(survive.bed_room(region, foot, head), want)


class SleepGate(unittest.TestCase):
    FOOT, HEAD = (1, 64, 0), (2, 64, 0)

    def test_rows(self):
        zombie = {"type": "minecraft:zombie", "x": 6.0, "y": 64.0, "z": 0.0}
        far = {"type": "minecraft:zombie", "x": 10.0, "y": 64.0, "z": 0.0}
        piglin = {"type": "minecraft:zombified_piglin", "x": 3.0, "y": 64.0, "z": 0.0}
        # (situation, state, region, hostiles) → the refusal's words, None when it lets us sleep
        rows = [("night, clear: sleeps", state(), ground(), [], None),
                ("must fail: a zombie 5 off behind a wall still refuses (vanilla ignores walls)", state(),
                 ground({(4, 64, 0): "stone", (4, 65, 0): "stone"}), [zombie], "zombie within the bed's box"),
                ("a zombie 9 off: outside the 8-block box", state(), ground(), [far], None),
                ("must fail: a calm zombified piglin does not refuse", state(), ground(), [piglin], None),
                ("an angry one does", state(), ground(), [dict(piglin, angry=True)], "zombified_piglin"),
                ("day: refused", state(timeOfDay=6000), ground(), [], "a bed only works at night"),
                ("the Nether: explodes", state(dimension="minecraft:the_nether"), ground(), [], "explodes"),
                ("too far", state(x=10.5), ground(), [], "too far"),
                ("obstructed", state(), ground({(1, 65, 0): "stone"}), [], "obstructed")]
        for name, s, region, hostiles, want in rows:
            with self.subTest(name):
                got = survive.sleep_gate(s, self.FOOT, self.HEAD, region, hostiles)
                if want is None:
                    self.assertIsNone(got)
                else:
                    self.assertIn(want, got or "")


class LightBox(unittest.TestCase):
    def test_cover(self):
        # (dark spots) → torches: one torch lights 14 blocks around (15 − manhattan)
        rows = [("none dark: none", [], 0), ("a 9×9 dark floor: one torch at its middle", 
                 [(x, 64, z) for x in range(-4, 5) for z in range(-4, 5)], 1),
                ("two patches 40 apart: two", [(0, 64, 0), (40, 64, 0)], 2)]
        for name, dark, n in rows:
            with self.subTest(name):
                torches = survive.torch_cover(dark)
                self.assertEqual(len(torches), n)
                self.assertTrue(all(any(15 - sum(abs(t[i] - c[i]) for i in range(3)) > 0 for t in torches)
                                    for c in dark))

    def test_pays(self):
        # (dark spots, floor spots, night left s, torch s) → pays
        rows = [("must fail: the dark open surface, no torches planned", 40, 120, 400.0, 0.5, True),
                ("must fail: a sealed pod: no dark spot, no torches", 0, 4, 400.0, 0.0, False),
                ("dawn: 2 s left, a torch costs more than the risk", 1, 120, 2.0, 0.25, False)]
        for name, dark, floor, left, torch_s, want in rows:
            with self.subTest(name):
                self.assertEqual(survive.light_pays(dark, floor, left, torch_s), want)


class TakeBed(unittest.TestCase):
    def test_the_bed_comes_back_however_the_night_went(self):
        ctx = type("C", (), {"policy": type("P", (), {"protected": set()})(),
                             "mem": type("M", (), {"slept": lambda s: None})()})()
        taken = []
        inv = bag(inventory(("minecraft:red_bed", 1)))
        for name, morning in [("slept till morning", True), ("refused all night (raised)", False)]:
            with self.subTest(name), \
                    mock.patch.object(survive, "Region", lambda lo, hi: ground()), \
                    mock.patch.object(survive, "light_box", lambda *a: []), \
                    mock.patch.object(survive, "entities", lambda *a: []), \
                    mock.patch.object(survive.api, "get", lambda p: state()), \
                    mock.patch.object(survive.api, "run_chain", lambda *a, **k: []), \
                    mock.patch.object(survive, "_morning", lambda: morning), \
                    mock.patch.object(survive, "take_bed", lambda c, foot: taken.append(foot)):
                taken.clear()
                try:
                    survive._sleep_carried(ctx, "minecraft:red_bed", dict(state(), blockX=0, blockY=64, blockZ=0), inv)
                except survive.NotAvailable:
                    pass
                self.assertEqual(len(taken), 1, "must fail: the bed left behind")


class NightLeft(unittest.TestCase):
    """night_left_s: no night outside the Overworld."""

    ROWS = [("the Overworld at midnight: the rest of the night", "minecraft:overworld", 18000, (23400 - 18000) / 20),
            ("the Overworld by day: none ahead yet", "minecraft:overworld", 6000, None),
            # must fail on the clock-only reading: the Nether reports the Overworld's midnight, and has no night
            ("the Nether at the Overworld's midnight: no night", "minecraft:the_nether", 18000, None),
            ("the End likewise", "minecraft:the_end", 18000, None)]

    def test_rows(self):
        from types import SimpleNamespace
        from bonobo import decompose
        for name, dim, t, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(decompose.night_left_s(SimpleNamespace(time=t, dimension=dim)), want)


if __name__ == "__main__":
    unittest.main()
