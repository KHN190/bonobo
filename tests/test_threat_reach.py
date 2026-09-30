"""A mob is a threat only if it can reach us or is at us already (hello2 08:39: creepers 12-18 blocks below behind
rock, bait "let it come" waited forever). The same rows feed bait, flee and fight."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import perception, threat  # noqa: E402

HERE = (55.5, 43.0, -107.5)
CREEPER = {"id": 11356, "type": "minecraft:creeper", "x": 51.5, "y": 31.0, "z": -117.2, "health": 20.0}   # 16 below
NEAR = dict(CREEPER, x=HERE[0] + 4, y=HERE[1], z=HERE[2])      # 4 off on our floor


def options_from(near, reaches):
    rows = threat.hostile_rows(perception.read_combat(near), {}, 1.0, here=HERE, reaches=reaches)
    ids = threat.ids_by_row(perception.read_combat(near), rows)
    st = {"here": HERE, "hp": 20, "sword": 0, "protection": 0.0, "blocks": 0, "hazards": rows, "ids": ids, "lit": set()}
    return rows, {o.kind for o in threat.options(st)} if rows else set()


class Reach(unittest.TestCase):
    def test_rows(self):
        # (situation, the entity, can it walk to us) → a threat row, bait offered
        rows = [("must fail: a creeper 16 below behind rock", CREEPER, False, (0, False)),
                ("must fail: a creeper 4 off beyond a wall", NEAR, False, (0, False)),
                ("a creeper 4 off in the same open cave", NEAR, True, (1, True)),
                ("the pathfinder cannot be asked: counted", NEAR, None, (1, True)),
                ("beyond a wall but at us already (provoked)", dict(NEAR, attacking=True), False, (1, True))]
        for name, e, walk, (n, bait) in rows:
            with self.subTest(name):
                got, kinds = options_from([e], lambda pos, reach, kind: walk)
                self.assertEqual((len(got), "bait" in kinds), (n, bait))

    def test_perception_asks_the_walk_once_per_pair(self):
        calls = []
        perception.STATE.reach.clear()
        with mock.patch.object(perception.nav, "walks_to", lambda cell, r, climber, y: calls.append(cell) or False):
            ask = perception.reaches_us(HERE, 1.0)
            self.assertIs(ask((51.5, 31.0, -117.2), 3.0, "minecraft:zombie"), False)
            self.assertIs(ask((51.5, 31.0, -117.2), 3.0, "minecraft:zombie"), False)
        self.assertEqual(calls, [(51, 31, -118)])


class Readout(unittest.TestCase):
    def test_every_threat_line_names_its_rows(self):
        row = ((51.5, 31.0, -117.2), 1.5, (0, 0, 0), "minecraft:zombie", 1.0, 1.0)
        body = tuple(int(c // 1) for c in HERE)
        with mock.patch.object(threat, "THREAT_ROWS", [row]), mock.patch.object(threat, "THREAT_IDS", [15188]), \
                mock.patch.dict(perception.STATE.reach, {(body, (51, 31, -118)): (1.0, True)}, clear=True):
            line = perception.threat_readout({"x": HERE[0], "y": HERE[1], "z": HERE[2]})
        self.assertIn("zombie 15188", line)
        self.assertIn("walk=True", line)


class ClimbsBack(unittest.TestCase):
    def test_rows(self):
        from bonobo import nav
        y = int(HERE[1])
        # (situation, the walk down our side) → can a zombie come back up it
        stairs = [{"y": y - k} for k in range(1, 13)]
        shaft = [{"y": y - 1}, {"y": y - 4}, {"y": y - 7}, {"y": y - 10}, {"y": y - 12}]
        rows = [("stairs a block at a time: yes", stairs, True),
                ("must fail: a drop of 3 our pathfinder takes (hello2 08:51: the zombie 12 below)", shaft, False),
                ("flat: yes", [{"y": y}, {"y": y}], True)]
        for name, steps, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.climbs_back(y, steps), want)

    def test_walks_to(self):
        from bonobo import nav
        y = int(HERE[1])
        shaft = {"found": True, "steps": [{"y": y - 3}, {"y": y - 6}]}
        with mock.patch.object(nav, "_plan_reply", lambda *a: shaft):
            # must fail: the zombie 16 below, reached only by drops, counted as coming to us
            self.assertFalse(nav.walks_to((51, 31, -118), 3.0, False, y))
            self.assertTrue(nav.walks_to((51, 31, -118), 3.0, True, y))          # a spider climbs


if __name__ == "__main__":
    unittest.main()
