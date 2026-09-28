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
        rows = [("must fail: nothing begun", 0, None), ("the water in, nothing sown", 2, CENTRE),
                ("half the ring sown", 10, CENTRE), ("every cell sown: finished, not begun", 18, None)]
        for name, k, want in rows:
            with self.subTest(name):
                world = grass()
                for t in full[:k]:
                    apply(world, t)
                self.assertEqual(farming.started_plot(world, (0, 64, 0)), want)


class PartialChain(unittest.TestCase):
    def test_only_out_of_reach_cells_are_banned(self):
        tasks = farming.plot_commands(CENTRE, HOE)[:4]      # mine, pour, till (-1,-1), sow (-1,-1)
        ok, far, other = {"status": "succeeded"}, {"status": "failed", "message": "cannot reach (-1, 63, -1)"}, \
            {"status": "failed", "message": "no seeds"}
        # (situation, results) → the cells banned
        rows = [("must fail: all done: none", [ok] * 4, []),
                ("the till out of reach: its cell", [ok, ok, far, ok], [(-1.0, 63.0, -1.0)]),
                ("a failure that is not reach: asked again, not banned", [ok, ok, ok, other], []),
                ("the dig out of reach: the centre", [far, ok, ok, ok], [(0, 63, 0)]),
                ("the sow out of reach: its cell (aimed under the farmland's top, still that cell)",
                 [ok, ok, ok, far], [(-1.0, 63.0, -1.0)])]
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
                self.assertTrue(all(c[1] + 0.9 < t["y"] < c[1] + 0.9375 for c, t in zip(cells, got)))


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
