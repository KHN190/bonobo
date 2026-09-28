"""Batched skills (G): the chains farming builds, resumed after an interrupt by what the world still lacks, and a
partial chain's failures sorted into what is banned (out of reach) and what is simply asked again."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import farming  # noqa: E402
from tests.world import flat  # noqa: E402

CENTRE, HOE = (0, 63, 0), "minecraft:stone_hoe"


def grass():
    return flat(lo=(-6, 60, -6), hi=(6, 70, 6), block="grass_block")


def apply(region, task):
    """The world after one plot task (fake): a dug centre, the water in, a cell tilled, a cell sown."""
    x, y, z = task["x"], task["y"], task["z"]
    if task["type"] == "mine":
        region.blocks[(x, y, z)] = "air"
        return
    cell = (int(x - 0.5), int(round(y - 1.0)), int(z - 0.5))
    item = task["item"]
    if item == "minecraft:water_bucket":
        region.blocks[(cell[0], cell[1] + 1, cell[2])] = "water"
    elif item.endswith("_hoe"):
        region.blocks[cell] = "farmland"
    elif item == "minecraft:wheat_seeds":
        region.blocks[(cell[0], cell[1] + 1, cell[2])] = "wheat"


class PlotResume(unittest.TestCase):
    def test_resume_after_an_interrupt_at_step_k(self):
        """Interrupted after k tasks (a segment boundary or mid-segment): the chain recomputed from the world is the
        rest of the full one — nothing done twice, nothing skipped."""
        full = farming.plot_commands(CENTRE, HOE)
        for k in (0, 2, 3, 6, 12, 17, 18):
            with self.subTest(k=k):
                world = grass()
                for t in full[:k]:
                    apply(world, t)
                self.assertEqual(farming.plot_commands(CENTRE, HOE, world), full[k:])

    def test_the_started_plot_is_found_again(self):
        # (situation, tasks already done) → the centre the resume picks (None: a fresh plot is chosen elsewhere)
        full = farming.plot_commands(CENTRE, HOE)
        rows = [("must fail: nothing begun", 0, None), ("dug, one cell tilled", 2, CENTRE),
                ("half the ring sown", 10, CENTRE), ("every cell sown, the water not yet in: still begun", 17, CENTRE),
                ("every cell sown and the water in: finished, not begun", 18, None)]
        for name, k, want in rows:
            with self.subTest(name):
                world = grass()
                for t in full[:k]:
                    apply(world, t)
                self.assertEqual(farming.started_plot(world, (0, 64, 0)), want)


class WaterLast(unittest.TestCase):
    """plot_commands: the water goes in last, into the centre hole, and the ring's blocks hold it; with a stand, each
    ring cell's clicks are preceded by a walk back onto it."""

    def test_order_and_stand(self):
        """Dig, then every till and sow in one run (no walk between: all within reach of the stand), water last."""
        full = farming.plot_commands(CENTRE, HOE, stand=(-2, 64, 0))
        kinds = [t["type"] if t["type"] != "use_item" else t["item"].split(":")[-1] for t in full]
        self.assertEqual((kinds[0], kinds[-1], kinds.count("goto"), len(kinds)), ("mine", "water_bucket", 0, 18))
        self.assertEqual(kinds[1:-1], ["stone_hoe", "wheat_seeds"] * 8)

    def test_the_pour_aims_where_the_eye_reaches(self):
        """water_task: from a stand beside the plot the rim hides the hole's floor; the far inner wall's face is aimed."""
        from bonobo import nav
        from tests.world import FakeRegion
        c = (10000, 199, 10000)
        blocks = {(x, y, z): "farmland" if y == 199 else "grass_block"
                  for x in range(9995, 10006) for y in (197, 198, 199) for z in range(9995, 10006) if (x, y, z) != c}
        region = FakeRegion((9990, 190, 9990), (10010, 210, 10010), blocks)
        aim = lambda t: (t["x"], t["y"], t["z"])       # noqa: E731
        rows = [("stand west: the east block's west face", (9998, 200, 10000), (10001.0, 199.5, 10000.5)),
                ("stand south: the north block's south face", (10000, 200, 9998), (10000.5, 199.5, 10001.0)),
                ("no stand (a resume computed offline): the floor's top", None, (10000.5, 199.0, 10000.5))]
        for name, stand, want in rows:
            with self.subTest(name):
                self.assertEqual(aim(farming.water_task(c, stand, region)), want)
        with self.subTest("must fail: the floor aimed from the west stand meets the rim first"):
            self.assertEqual(nav.first_solid(region, (9998.5, 201.62, 10000.5), (10000.5, 199.0, 10000.5)),
                             (9999, 199, 10000))

    def test_the_pour_from_the_stand(self):
        """pour_commands: one send — the centre dug again if something filled it since its dig, then the pour;
        plot_cells: every walk while the plot is made keeps off the centre and the ring."""
        stand = (-2, 64, 0)
        rows = [("the centre open: the pour alone", "air", ["use_item"]),
                ("must fail: the centre refilled (a walk's floor block): dug again, then the pour", "grass_block",
                 ["mine", "use_item"]),
                ("water already in: the pour is sent anyway (the check after reads it)", "water", ["use_item"])]
        for name, now, want in rows:
            with self.subTest(name):
                got = farming.pour_commands(CENTRE, now, stand)
                self.assertEqual([t["type"] for t in got], want)
                if got[0]["type"] == "mine":
                    self.assertEqual((got[0]["x"], got[0]["y"], got[0]["z"]), CENTRE)
        cells = farming.plot_cells(CENTRE)
        with self.subTest("a walk off the stand keeps off the plot: the centre, the cell under it, the ring"):
            self.assertTrue({CENTRE, (0, 62, 0), (1, 63, 1), (-1, 64, 0)} <= cells)
            self.assertNotIn(stand, cells)

    def test_contained(self):
        from tests.world import FakeRegion
        ground = {(x, y, z): "grass_block" for x in range(-3, 4) for y in (61, 62, 63) for z in range(-3, 4)}
        hole = {k: v for k, v in ground.items() if k != (0, 63, 0)}
        rows = [("in the centre hole, the ring round it: held", hole, (0, 63, 0), True),
                ("must fail: poured onto the ground (a level up): runs over the ring", ground, (0, 64, 0), False),
                ("must fail: a side of the hole open", {k: v for k, v in hole.items() if k != (1, 63, 0)}, (0, 63, 0),
                 False),
                ("must fail: nothing under it", {k: v for k, v in hole.items() if k != (0, 62, 0)}, (0, 63, 0), False)]
        for name, blocks, cell, want in rows:
            with self.subTest(name):
                self.assertIs(farming.water_contained(FakeRegion((-4, 58, -4), (4, 68, 4), blocks), cell), want)


class PartialChain(unittest.TestCase):
    def test_only_out_of_reach_cells_are_banned(self):
        tasks = farming.plot_commands(CENTRE, HOE)[:4]      # mine, till (-1,-1), sow (-1,-1), till (-1,0)
        ok, far, other = {"status": "succeeded"}, {"status": "failed", "message": "cannot reach (-1, 63, -1)"}, \
            {"status": "failed", "message": "no seeds"}
        # (situation, results) → the cells banned
        rows = [("must fail: all done: none", [ok] * 4, []),
                ("the till out of reach: its cell", [ok, far, ok, ok], [(-1.0, 63.0, -1.0)]),
                ("a failure that is not reach: asked again, not banned", [ok, ok, other, ok], []),
                ("the dig out of reach: the centre", [far, ok, ok, ok], [(0, 63, 0)]),
                ("the sow out of reach: its cell (aimed under the farmland's top, still that cell)",
                 [ok, ok, far, ok], [(-1.0, 63.0, -1.0)])]
        for name, results, want in rows:
            with self.subTest(name):
                self.assertEqual(farming.unreachable_cells(tasks, results), want)


class Sow(unittest.TestCase):
    def test_sow_commands(self):
        rows = [("three cells", [(0, 63, 0), (1, 63, 0), (2, 63, 0)], 3),
                ("one cell", [(0, 63, 0)], 1),
                ("must fail: none", [], 0),
                ("the same cell twice is sown twice (the caller dedups)", [(0, 63, 0)] * 2, 2)]
        for name, cells, n in rows:
            with self.subTest(name):
                got = farming.sow_commands(cells)
                self.assertEqual((len(got), {t["item"] for t in got} or {"minecraft:wheat_seeds"}),
                                 (n, {"minecraft:wheat_seeds"}))
                # aimed at the farmland's own top (15/16), not the air above a full block's top
                self.assertTrue(all(c[1] + 0.75 < t["y"] < c[1] + 0.9375 for c, t in zip(cells, got)))


if __name__ == "__main__":
    unittest.main()


class ByLayer(unittest.TestCase):
    """building.by_layer: a build's chain cut into one chunk per placed height, bottom-up; what walks or pillars up to
    a layer opens that layer's chunk."""
    P = staticmethod(lambda y: {"type": "place", "y": y})
    G, U, M = {"type": "goto", "y": 0}, {"type": "pillar"}, {"type": "mine_many"}
    # (situation, tasks) → the chunks, as short names
    ROWS = [("clear, two layers with a pillar between", [M, P(0), P(0), G, U, P(1), P(1)],
             [["m", "p0", "p0"], ["g", "p", "p1", "p1"]]),
            ("one block: one chunk", [P(0)], [["p0"]]),
            ("three heights: three chunks", [P(0), P(1), P(2)], [["p0"], ["p1"], ["p2"]]),
            ("nothing to build: no chunk (must fail to send anything)", [], []),
            ("a task after the last place stays with it", [G, P(0), M], [["g", "p0", "m"]])]

    def test_rows(self):
        from bonobo.building import by_layer
        name = lambda t: t["type"][0] + (str(t["y"]) if t["type"] == "place" else "")   # noqa: E731
        for situation, tasks, want in self.ROWS:
            with self.subTest(situation):
                self.assertEqual([[name(t) for t in c] for c in by_layer(tasks)], want)
