"""The dragon fight's pure parts: the pit's geometry from fight.toml, the fight state read off the game's answers, and
the intent fight_plan picks from it — the one table slay_dragon only dispatches."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import dragon as D  # noqa: E402
from bonobo import fight_plan  # noqa: E402

GEO = fight_plan.CONFIG["geometry"]
FLOOR = 60
PIT = D.pit_geometry((1, 0), FLOOR)
FX, FY, FZ = PIT["feet"]
IN_PIT = {"x": FX + 0.5, "y": FY, "z": FZ + 0.5, "health": 20.0}
OUT = {"x": GEO["mouth_r"] + 7.5, "y": FLOOR, "z": 0.5, "health": 20.0}
HP = fight_plan.CONFIG["combat"]["dragon_hp"]
NOW = 100.0


def dragon(phase, hp=HP):
    """Perched on the pillar, or circling far off."""
    perched = phase in D.PERCH_PHASES
    return {"type": D.DRAGON, "x": 0.0 if perched else -60.0, "y": FLOOR + 4.0 if perched else 90.0, "z": 0.0,
            "health": hp, "phase": phase}


CRYSTAL = {"type": D.CRYSTAL, "x": 40.0, "y": 100.0, "z": 0.0}
BEDS = {"bed": 6}


class Geometry(unittest.TestCase):

    def test_table(self):
        rows = [("the pit's mouth at mouth_r on the floor", PIT["mouth"], (GEO["mouth_r"], FLOOR, 0)),
                ("its feet pit_depth below", PIT["feet"], (GEO["mouth_r"], FLOOR - GEO["pit_depth"], 0)),
                ("the bed at bed_r on the floor", PIT["bed"], (GEO["bed_r"], FLOOR, 0)),
                ("the side the body is on (x)", D.choose_side((10, 0, 2)), (1, 0)),
                ("the side the body is on (z)", D.choose_side((-1, 0, -9)), (0, -1)),
                ("the pillar's top: one above its highest bedrock",
                 D.pillar_top([(0, FLOOR, 0), (0, FLOOR + 3, 0), (10, FLOOR + 9, 0)]), FLOOR + 4),
                ("must fail: bedrock only far from the centre", D.pillar_top([(10, FLOOR, 0)]), None),
                ("in the pit", D.pit_holds(PIT["feet"], PIT), True),
                ("must fail: on the rim", D.pit_holds(PIT["mouth"], PIT), False),
                ("must fail: no pit built", D.pit_holds(PIT["feet"], {}), False),
                ("the walls and the bed's block reinforced", len(D.reinforce_cells(PIT)), 5)]
        for name, got, want in rows:
            with self.subTest(name):
                self.assertEqual(got, want)


class Clock(unittest.TestCase):

    def test_table(self):
        rows = [("the phase holds: its start kept", {"phase": 6, "since": 90.0}, 6, (6, 90.0)),
                ("must fail: a new phase restarts the clock", {"phase": 0, "since": 90.0}, 6, (6, NOW)),
                ("first reading", {"phase": None, "since": 0.0}, 0, (0, NOW))]
        for name, clock, phase, want in rows:
            with self.subTest(name):
                self.assertEqual(D.phase_clock(clock, phase, NOW), want)


class Dead(unittest.TestCase):

    def test_table(self):
        part = {"type": D.DRAGON, "health": None}
        rows = [("gone and the portal lit", [], True, True),
                ("a part left (no health), the portal lit", [part], True, True),
                ("must fail: alive with the portal lit", [dragon(0)], True, False),
                ("must fail: out of sight, the portal dark", [], False, False)]
        for name, near, portal, want in rows:
            with self.subTest(name):
                self.assertEqual(D.dragon_dead(near, portal), want)


class Intent(unittest.TestCase):
    """fight_plan's intent over fight_state's reading: what slay_dragon dispatches."""

    def test_table(self):
        bow = dict(BEDS, **{"minecraft:bow": 1, "minecraft:arrow": 8})
        obsidian = dict(BEDS, **{"minecraft:obsidian": len(D.reinforce_cells(PIT))})
        sitting = fight_plan.quantile(fight_plan.OBSERVED[6], 0.5)
        rows = [("no pit yet, it circles: dig", OUT, [dragon(0)], BEDS, {}, 0.0, "dig_tunnel"),
                ("perched (6), in the pit, beds: the window", IN_PIT, [dragon(6)], BEDS, PIT, 0.0, "fire_window"),
                ("must fail: perched, no beds", IN_PIT, [dragon(6)], {}, PIT, 0.0, "retreat"),
                ("must fail: sitting and flaming (5)", IN_PIT, [dragon(5)], BEDS, PIT, 0.0, "retreat"),
                ("must fail: the sitting phase all but over", IN_PIT, [dragon(6)], BEDS, PIT, sitting, "retreat"),
                ("it circles, the pit ready: wait", IN_PIT, [dragon(0)], BEDS, PIT, 0.0, "retreat"),
                ("a crystal standing, a bow", IN_PIT, [dragon(0), CRYSTAL], bow, PIT, 0.0, "shoot_crystal"),
                ("obsidian for the pit's walls", IN_PIT, [dragon(0)], obsidian, PIT, 0.0, "reinforce")]
        for name, me, near, inv, pit, elapsed, want in rows:
            with self.subTest(name):
                state = D.fight_state(me, near, inv, pit, {"phase": None, "since": NOW - elapsed}, NOW)
                self.assertEqual(fight_plan.validate_state(state), [])
                self.assertEqual(D.FIGHT.plan(state)["intent"], want)
                self.assertIn(want, D.DISPATCH)


if __name__ == "__main__":
    unittest.main()
