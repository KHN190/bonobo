"""nav.plan_way and its parts: a way is planned steps — cells opened top down with what falls on them, a missing tread
placed, stopped before a fluid or the home — priced by the breaks each held tool makes (knowledge.dig_ticks)."""
import unittest

from bonobo import nav
from bonobo.beliefs import TICKS_PER_S
from bonobo.data import STAIR_CELLS
from bonobo.knowledge import break_ticks
from tests.world import FakeRegion, bag, inventory

FEET = (0, 64, 0)
TARGET = (4, 59, 0)               # 5 down, 4 east: a staircase east
PICK = {"id": "minecraft:diamond_pickaxe", "count": 1, "damage": 0, "maxDamage": 1561}


def ground(over=None, top=64):
    """Stone up to y top-1 (the feet at y 64 stand on y 63); `over`: cells set to other blocks."""
    blocks = {(x, y, z): "stone" for x in range(-1, 8) for z in range(-2, 3) for y in range(50, top)}
    blocks.update(over or {})
    return FakeRegion((-1, 50, -2), (8, 72, 2), {c: b for c, b in blocks.items() if b != "air"})


def mined(steps):
    return [(t["x"], t["y"], t["z"]) for t in steps if t["type"] == "mine"]


class StairSteps(unittest.TestCase):
    def test_rows(self):
        sand_col = {(1, 66, 0): "sand", (1, 67, 0): "sand"}          # two sand over the first step's head room
        # (situation, region, protected, places) → (first step's mined cells in order, why)
        rows = [("stone: the step's cells top down", ground(), (), (),
                 [(1, 63, 0)], None),
                ("sand on the head room: the sand first, top down", ground(sand_col, top=66), (), (),
                 [(1, 67, 0), (1, 66, 0), (1, 65, 0), (1, 64, 0), (1, 63, 0)], None),
                ("must fail: water beside the first step", ground({(1, 63, 1): "water"}), (), (), [], "fluid at"),
                ("must fail: the first step in the home", ground(), {(1, 63, 0)}, (), [], "home at"),
                ("no tread and no block: stopped", ground({(1, 62, 0): "air"}), (), (), [], "no tread at"),
                ("no tread, a block carried: placed", ground({(1, 62, 0): "air"}), (), ["minecraft:cobblestone"],
                 [(1, 63, 0)], None)]
        for name, region, protected, places, first, why in rows:
            with self.subTest(name):
                steps, got_why, _end = nav.stair_steps(region, FEET, TARGET, protected, places)
                goto = next((i for i, t in enumerate(steps) if t["type"] == "goto"), len(steps))
                self.assertEqual(mined(steps[:goto]), first)
                if why is None:
                    self.assertTrue(steps)
                else:
                    self.assertIn(why, got_why or "")
        # must fail: bottom up (the sand falls into the dug cell: "nothing to mine (air)")
        steps, _, _end = nav.stair_steps(ground(sand_col, top=66), FEET, TARGET)
        ys = [c[1] for c in mined(steps) if (c[0], c[2]) == (1, 0)]
        self.assertEqual(ys, sorted(ys, reverse=True))

    def test_tread_placed_before_the_walk(self):
        steps, _, _end = nav.stair_steps(ground({(1, 62, 0): "air"}), FEET, TARGET, (), ["minecraft:dirt"])
        kinds = [t["type"] for t in steps[:3]]
        self.assertEqual(kinds, ["mine", "place", "goto"])          # must fail: the walk onto air


class BridgeAndClimb(unittest.TestCase):
    """plan_way's ways over a gap or a fluid (the level way's treads placed: a bridge) and up out of a pit (a
    staircase up): every cell named, nothing left to the walk (V5, E3)."""
    COBBLE = {"id": "minecraft:cobblestone", "count": 32}

    def way(self, region, feet, target):
        inv = bag(inventory(PICK, self.COBBLE))
        steps, why, _s = nav.plan_way(region, feet, target, "stand", inv, set())
        return steps, why

    def test_bridge(self):
        lava = {(x, 63, 0): "lava" for x in range(1, 4)}
        # (situation, region over the strip) → (cells placed, why)
        rows = [("a lava strip: each tread placed, then walked", ground(lava), [(1, 63, 0), (2, 63, 0), (3, 63, 0)], None),
                ("a gap: the same", ground({(x, 63, 0): "air" for x in range(1, 4)}),
                 [(1, 63, 0), (2, 63, 0), (3, 63, 0)], None),
                ("must fail: lava at the walk's own level beside it: the way stops before it",
                 ground({**lava, (2, 64, 1): "lava"}), [(1, 63, 0)], "short")]
        for name, region, placed, why in rows:
            with self.subTest(name):
                steps, got = self.way(region, FEET, (4, 64, 0))
                if why is not None:
                    self.assertEqual([(t["x"], t["y"], t["z"]) for t in steps if t["type"] == "place"], placed)
                    self.assertNotEqual([t for t in steps if t["type"] == "goto"][-1]["x"], 4)
                    continue
                self.assertEqual([(t["x"], t["y"], t["z"]) for t in steps if t["type"] == "place"], placed)
                gotos = [(t["x"], t["y"], t["z"]) for t in steps if t["type"] == "goto"]
                self.assertEqual(gotos[-1], (4, 64, 0))

    def test_climb(self):
        pit = ground({(0, 60, 0): "air", (0, 61, 0): "air"})      # a 1×2 pocket 4 under the floor
        steps, why = self.way(pit, (0, 60, 0), (4, 64, 0))
        self.assertIsNone(why)
        gotos = [(t["x"], t["y"], t["z"]) for t in steps if t["type"] == "goto"]
        self.assertEqual([g[1] for g in gotos], [61, 62, 63, 64])  # must fail: a way that stays at the pit's level
        self.assertEqual(gotos[-1], (4, 64, 0))
        dug = mined(steps)
        self.assertIn((0, 62, 0), dug, "the head room over the step it leaves, for the jump")


class StairShape(unittest.TestCase):
    """From test_pure_world: the steps go one over and one down along the target's longer axis, never the own column;
    nothing for a target level with the feet or above, nor over bedrock."""

    def test_rows(self):
        flat = ground()
        bedrock = FakeRegion(flat.lo, flat.hi, {c: "bedrock" for c in flat.blocks})
        # (situation, region, target) → the walk direction of the steps, None: no stairs
        rows = [("5 below and 3 east: steps east, one down each", flat, (3, 59, 0), (1, 0)),
                ("straight below: steps along x, never the own column", flat, (0, 58, 0), (1, 0)),
                ("must fail: level with the feet: no stairs", flat, (4, 64, 0), None),
                ("must fail: the target above", flat, (2, 70, 0), None),
                ("must fail: bedrock under the steps", bedrock, (3, 59, 0), None)]
        for name, region, target, d in rows:
            with self.subTest(name):
                tasks, _why, _end = nav.stair_steps(region, FEET, target)
                gotos = [(t["x"], t["y"], t["z"]) for t in tasks if t["type"] == "goto"]
                if d is None:
                    self.assertEqual(tasks, [])
                else:
                    self.assertTrue(gotos)
                    self.assertTrue(all(g[1] == 64 - k and (g[0], g[2]) == (d[0] * k, d[1] * k)
                                        for k, g in enumerate(gotos, 1)))


class WayEndsAtTheTarget(unittest.TestCase):
    """plan_way, run as the door runs it (steps applied, the region read again, asked again until it plans nothing):
    the feet end where `kind` of the target can be done — not one step above it and 11 along (a46: the staircase
    stopped at target y + 1 wherever its direction had taken it)."""

    def run_door(self, kind, target, region, feet=FEET, calls=8):
        blocks = dict(region.blocks)
        for _ in range(calls):
            now = FakeRegion(region.lo, region.hi, blocks)
            steps, why, _s = nav.plan_way(now, feet, target, kind, bag(inventory(PICK)), ())
            self.assertIsNotNone(steps, why)
            if not steps:
                return feet, FakeRegion(region.lo, region.hi, blocks)
            for t in steps:
                c = (t["x"], t["y"], t["z"])
                if t["type"] == "mine":
                    blocks.pop(c, None)
                elif t["type"] == "place":
                    blocks[c] = t["item"]
                else:
                    feet = c
        self.fail(f"no end after {calls} calls")

    def test_rows(self):
        deep = FakeRegion((-14, 40, -3), (14, 70, 3),
                          {(x, y, z): "stone" for x in range(-14, 15) for z in range(-3, 4) for y in range(40, 64)})
        # (situation, kind, target) → the end holds it (mine) / stands on it (stand)
        rows = [("must fail: 12 straight below, to mine", "mine", (0, 52, 0)),
                ("12 below and 4 west, to mine", "mine", (-4, 52, 0)),
                ("6 below, to stand on", "stand", (3, 58, 0))]
        for name, kind, target in rows:
            with self.subTest(name):
                feet, now = self.run_door(kind, target, deep)
                self.assertTrue(nav.stands_at(kind, now, feet, target), f"ended at {feet}")


class WayPrice(unittest.TestCase):
    def test_rows(self):
        region = ground()
        steps, _, _end = nav.stair_steps(region, FEET, TARGET)
        cells = mined(steps)
        walk = sum(1 for t in steps if t["type"] == "goto") * (2 ** 0.5) / nav.PLAYER_SPEED
        # (held) → seconds: each break by the tool held for it, the walk down the steps
        rows = [("bare hands", bag(inventory()), len(cells) * break_ticks("stone", "hand") / TICKS_PER_S + walk),
                ("a diamond pickaxe", bag(inventory(PICK)),
                 len(cells) * break_ticks("stone", "minecraft:diamond_pickaxe") / TICKS_PER_S + walk)]
        for name, inv, want in rows:
            with self.subTest(name):
                self.assertAlmostEqual(nav.way_s(region, FEET, steps, inv), want, places=6)
        self.assertLessEqual(len(cells), 5 * STAIR_CELLS)
        # must fail: the pickaxe priced like the hand
        self.assertLess(nav.way_s(region, FEET, steps, bag(inventory(PICK))), nav.way_s(region, FEET, steps, bag(inventory())))


class PlanWay(unittest.TestCase):
    def test_rows(self):
        region, inv = ground(), bag(inventory(PICK))
        face = (3, 60, 0)
        found = {"found": True, "seconds": 2.0}
        # (situation, walks, protected) → (first step type, why)
        rows = [("a walk found: walked, nothing dug", {face: found}, (), "goto", None),
                ("no walk: the staircase", {face: {"found": False}}, (), "mine", None),
                ("a slow walk: the staircase is cheaper", {face: {"found": True, "seconds": 500.0}}, (), "mine", None),
                ("must fail: the way through the home, no walk", {}, {(1, 63, 0), (1, 64, 0), (1, 65, 0)}, None,
                 "home at")]
        for name, walks, protected, first, why in rows:
            with self.subTest(name):
                steps, got_why, seconds = nav.plan_way(region, FEET, TARGET, "mine", inv, protected, walks)
                self.assertEqual(steps[0]["type"] if steps else None, first)
                if why:
                    self.assertIn(why, got_why)
                    self.assertIsNone(seconds)


class WalkOrStair(unittest.TestCase):
    """plan_way chooses the level way or the staircase by seconds, at any depth (no threshold of its own)."""

    def test_rows(self):
        inv = bag(inventory(PICK))
        wall = {(x, y, z): "stone" for x in range(6, 8) for y in range(64, 70) for z in range(-2, 3)}
        # (situation, region, target) → (any block dug, why)
        rows = [("must fail: a block one up in a wall ahead, no tread to climb: walked to on the level, nothing dug",
                 ground(wall), (6, 65, 0), False, None),
                ("5 down: the staircase", ground(), TARGET, True, None)]
        for name, region, target, dug, why in rows:
            with self.subTest(name):
                steps, got_why, _s = nav.plan_way(region, FEET, target, "mine", inv, (), {})
                self.assertIsNotNone(steps, got_why)
                self.assertEqual(bool(mined(steps)), dug)
                self.assertEqual(got_why, why)


class LevelReach(unittest.TestCase):
    """data.STAIR_BELOW (the prior where no region is read: cost, gather) is holds' own geometry: an open block beside
    a level stand is held down to it, not one deeper (the rim hides it)."""

    def test_rows(self):
        from bonobo.data import STAIR_BELOW

        def shaft(d):
            blocks = {(x, y, z): "stone" for x in range(-3, 4) for z in range(-3, 4) for y in range(50, 64)}
            for y in range(64 - d + 1, 64):
                blocks.pop((1, y, 0))
            return FakeRegion((-3, 50, -3), (3, 70, 3), blocks)
        # (situation, depth below the feet) → held from the level stand
        rows = [("STAIR_BELOW down: held", STAIR_BELOW, True),
                ("must fail: one deeper: the rim hides it", STAIR_BELOW + 1, False)]
        for name, d, want in rows:
            with self.subTest(name):
                self.assertIs(nav.holds(shaft(d), FEET, (1, FEET[1] - d, 0)), want)


class WalksAsked(unittest.TestCase):
    """plan_way reads the walks least first (least_way_s) and asks none that cannot beat its best; every stand
    candidate is offered (no cap)."""

    class Asked(dict):
        def __init__(self, replies):
            super().__init__(replies)
            self.read = []

        def __getitem__(self, cell):
            self.read.append(cell)
            return super().__getitem__(cell)

    def test_rows(self):
        inv = bag(inventory(PICK))
        dug_s = nav.plan_way(ground(), FEET, TARGET, "mine", inv, (), {})[2]
        near, far, beyond = (1, 64, 0), (4, 64, 0), (0, 64, 2 + int(dug_s * nav.PLAYER_SPEED))
        quick = {"found": True, "seconds": nav.least_way_s(near, FEET)}
        # (situation, replies) → the cells asked, in order
        rows = [("must fail: a quick walk to the nearest: none farther asked",
                 {far: {"found": True, "seconds": 1.0}, near: quick}, [near]),
                ("no walk to the nearest: the next asked", {far: {"found": False}, near: {"found": False}}, [near, far]),
                ("must fail: one farther than the staircase takes: never asked", {beyond: {"found": True}}, [])]
        for name, replies, want in rows:
            with self.subTest(name):
                walks = self.Asked(replies)
                nav.plan_way(ground(), FEET, TARGET, "mine", inv, (), walks)
                self.assertEqual(walks.read, want)

    def test_every_candidate(self):
        region, target = ground(), (4, 63, 0)
        # must fail: capped at three (a cheaper fourth never priced)
        self.assertGreater(len(nav.stand_candidates(region, target, "mine")), 3)


class SafeDepth(unittest.TestCase):
    """nav.safe_depth: the cells safe to dig straight down, a number — never an exception (dig_in_site is pure)."""

    def test_rows(self):
        lava = ground({(0, 61, 0): "lava"})
        # (situation, region, protected) → safe cells of 3
        rows = [("deep stone", ground(), (), 3),
                ("must fail: lava in the third cell: the second beside it, one", lava, (), 1),
                ("must fail: the cell under the feet protected: none, no raise", ground(), {(0, 63, 0)}, 0)]
        for name, region, protected, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.safe_depth(region, FEET, 3, protected), want)


class PickSeed(unittest.TestCase):
    """gather.pick_seed: the vein whose way is cheapest, not the nearest (§12 A4)."""

    def test_rows(self):
        from bonobo import gather
        near, far = (1, 50, 0), (6, 60, 0)
        # (situation, [(cell, way seconds, distance)]) → the chosen
        rows = [("must fail: a buried near vein (40 s of digging) over an open one 6 off (5 s walk)",
                 [(near, 40.0, 3.0), (far, 5.0, 6.0)], far),
                ("the near one is also the cheapest", [(near, 2.0, 3.0), (far, 5.0, 6.0)], near),
                ("an unpriced one (too far to read) after every priced one", [(near, None, 3.0), (far, 50.0, 6.0)], far),
                ("none priced: the nearest", [(far, None, 6.0), (near, None, 3.0)], near),
                ("none", [], None)]
        for name, priced, want in rows:
            with self.subTest(name):
                self.assertEqual(gather.pick_seed(priced), want)


if __name__ == "__main__":
    unittest.main()
