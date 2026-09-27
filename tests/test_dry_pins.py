"""DRY merges pinned: each table holds what the OLD copies returned (written before the merge), so the one home
left after it must answer the same. A must-fail row per table says what the table can tell apart."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import combat_model as cm, farming, fluids, nav  # noqa: E402


class _Inv:
    def count(self, _item):
        return 1


def _use(item, x, y, z):
    return {"type": "use_item", "item": item, "x": x, "y": y, "z": z, "onBlock": True}


class Waypoints(unittest.TestCase):
    """nav.waypoints: the old nether.waypoints (leg 40), and nav.travel's hand-rolled hops (leg nav.LEG, last cut)."""
    ROWS = [  # (here, target, leg, want)
        ((-258, 65, 270), (-366, 120, 191), 40,
         [(-285, 79, 250), (-312, 92, 230), (-339, 106, 211), (-366, 120, 191)]),
        ((0, 64, 0), (10, 64, 0), 40, [(10, 64, 0)]),
        ((0, 70, 0), (0, 70, -200), 40, [(0, 70, -40), (0, 70, -80), (0, 70, -120), (0, 70, -160), (0, 70, -200)]),
        ((5, 60, 5), [105, 40, -95], 40, [(30, 55, -20), (55, 50, -45), (80, 45, -70), (105, 40, -95)]),
    ]
    # nav.travel's old loop: n = ceil(dist / LEG), hops k = 1 .. n-1 (the target itself walked by the caller)
    HOPS = [((-258, 65, 270), (-366, 120, 191), [(-294, 83, 244), (-330, 102, 217)]),
            ((0, 70, 0), (0, 70, -200), [(0, 70, -40), (0, 70, -80), (0, 70, -120), (0, 70, -160)]),
            ((5, 60, 5), [105, 40, -95], [(38, 53, -28), (72, 47, -62)])]

    def test_waypoints(self):
        for here, target, leg, want in self.ROWS:
            with self.subTest(here=here, target=target):
                self.assertEqual(nav.waypoints(here, target, leg), want)

    def test_travel_hops(self):
        self.assertEqual(nav.LEG, 48)
        for here, target, want in self.HOPS:
            with self.subTest(here=here, target=target):
                self.assertEqual(nav.waypoints(here, target, nav.LEG)[:-1], want)

    def test_the_leg_matters(self):
        # must fail: the travel hops are not the portal trip's (leg 48 vs 40)
        self.assertNotEqual(nav.waypoints((-258, 65, 270), (-366, 120, 191), nav.LEG),
                            self.ROWS[0][3])


class MineTask(unittest.TestCase):
    """nav.mine_task: the dicts farming's plot and building's pillar spelled out were exactly this."""
    ROWS = [((1, 64, 2), False, {"type": "mine", "x": 1, "y": 64, "z": 2, "collect": False, "requireDrops": False}),
            ((-3, 70, 8), False, {"type": "mine", "x": -3, "y": 70, "z": 8, "collect": False, "requireDrops": False}),
            ((0, 0, 0), True, {"type": "mine", "x": 0, "y": 0, "z": 0, "collect": True, "requireDrops": False})]

    def test_mine_task(self):
        for cell, collect, want in self.ROWS:
            with self.subTest(cell):
                self.assertEqual(nav.mine_task(cell, collect=collect), want)

    def test_plot_digs_the_centre(self):
        self.assertEqual(farming.plot_commands((1, 64, 2), "minecraft:iron_hoe")[0], self.ROWS[0][2])

    def test_end_bars_differ(self):
        # must fail: end.crystal_commands' bar dict has no requireDrops — not the same task, left as it is
        self.assertNotEqual(nav.mine_task((1, 64, 2)), {"type": "mine", "x": 1, "y": 64, "z": 2, "collect": False})


class UseOnTop(unittest.TestCase):
    """nav.use_on_top: farming's till/sow/pour, the portal's flint (fluids.light_commands, nether's relight)."""
    ROWS = [("minecraft:water_bucket", (1, 63, 2), _use("minecraft:water_bucket", 1.5, 64.0, 2.5)),
            ("minecraft:iron_hoe", (0, 64, 1), _use("minecraft:iron_hoe", 0.5, 65.0, 1.5)),
            ("minecraft:flint_and_steel", (8, 70, -5), _use("minecraft:flint_and_steel", 8.5, 71.0, -4.5)),
            ("minecraft:wheat_seeds", (-4, -60, 7), _use("minecraft:wheat_seeds", -3.5, -59.0, 7.5))]
    LIGHT = [(((0, 64, 0), 1, 0), [_use("minecraft:flint_and_steel", 0.5, 65.0, 1.5), {"type": "wait", "ticks": 10}]),
             (((10, 70, -5), 2, 1), [_use("minecraft:flint_and_steel", 8.5, 71.0, -4.5),
                                     {"type": "wait", "ticks": 10}])]
    AIM = [(((0, 64, 0), 1), (0.5, 65.0, 1.5)), (((3, 70, -2), 3), (3.5, 71.0, -2.5))]

    def test_use_on_top(self):
        for item, cell, want in self.ROWS:
            with self.subTest(item):
                self.assertEqual(nav.use_on_top(item, cell), want)

    def test_plot_pours_and_tills(self):
        got = farming.plot_commands((1, 64, 2), "minecraft:iron_hoe")[1:3]
        self.assertEqual(got, [self.ROWS[0][2], _use("minecraft:iron_hoe", 0.5, 65.0, 1.5)])

    def test_light_commands(self):
        for args, want in self.LIGHT:
            with self.subTest(args):
                self.assertEqual(fluids.light_commands({"inv": _Inv()}, args), want)

    def test_portal_light_aim(self):
        for args, want in self.AIM:
            with self.subTest(args):
                self.assertEqual(fluids.portal_light_aim(*args), want)

    def test_a_floor_click_is_not_on_top(self):
        # must fail: a bucket's floor aim (+0.02) is not the top face (+1.0)
        t = nav.use_on_top("minecraft:water_bucket", (1, 63, 2))
        self.assertNotEqual((t["x"], t["y"], t["z"]), fluids.floor_aim((1, 63, 2)))


class BestStep(unittest.TestCase):
    """combat_model.best_step: the old loop's answers, now through safest's."""
    ROWS = [((0, 64, 0), [((3, 64, 0), 1.0, (-2, 0, 0))], None, ((-2.82842712474619, 64, 2.8284271247461903), float("inf"))),
            ((0, 64, 0), [((0, 64, 0), 10.0, (0, 0, 0))], None, ((0, 64, 0), -0.3)),
            ((0, 64, 0), [((6, 64, 0), 1.0, (-8, 0, 0)), ((-6, 64, 0), 1.0, (8, 0, 0)), ((0, 64, 6), 1.0, (0, 0, -8)),
                          ((0, 64, -6), 1.0, (0, 0, 8))], None, ((1.4142135623730951, 64, 1.414213562373095), float("inf"))),
            ((1, 64, 1), [((3, 64, 1), 2.5, (0, 0, 0))], (1, 64, -3), ((-1.8284271247461898, 64, 3.8284271247461903), float("inf"))),
            ((0, 64, 0), [((2, 64, 2), 1.0, (-1, 0, -1))], (-6, 64, 0), ((-6, 64, 0), float("inf"))),
            ([1, 64, 1], [((1, 64, 4), 2.0, (0, 0, -4))], None, ((-1.8284271247461907, 64, -1.8284271247461898), float("inf")))]

    def test_best_step(self):
        for here, hazards, cover, want in self.ROWS:
            with self.subTest(here=here, hazards=hazards, cover=cover):
                self.assertEqual(cm.best_step(here, hazards, cover=cover), want)

    def test_cover_counts(self):
        # must fail: without the cover the far bunker mouth is not chosen
        here, hazards, cover, want = self.ROWS[4]
        self.assertNotEqual(cm.best_step(here, hazards), want)


if __name__ == "__main__":
    unittest.main()
