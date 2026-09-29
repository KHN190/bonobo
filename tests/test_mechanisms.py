"""Taught mechanisms (press this, those cells open): walks press a door open and never dig it; planning prices what
lies behind it as walk + press + walk, never unreachable nor dug; the door is pressed from our own side, and only
when it stands shut (read off the world)."""
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import cost as costmod, fresh, mechanisms as mech, nav  # noqa: E402
from bonobo.planner import Step  # noqa: E402

DIM = "minecraft:overworld"
WALL_X = 10                                            # the wall the door is set in: x = WALL_X
DOOR = [(WALL_X, 64, 0), (WALL_X, 65, 0)]
OUT_PRESS, IN_PRESS = (WALL_X - 1, 65, 1), (WALL_X + 1, 65, 1)
OUTSIDE, INSIDE = (WALL_X - 10, 64, 0), (WALL_X + 10, 64, 0)


def walk_s(d):
    return d / 4.0


def taught(tmp):
    path = os.path.join(tmp, "mechanisms.json")
    for press in (OUT_PRESS, IN_PRESS):
        mech.add(DIM, press, DOOR, path=path)
    return path


class Geometry(unittest.TestCase):
    def test_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            mechs = mech.load(taught(tmp))
        door = tuple(DOOR)
        # (from, to) → the press used
        rows = [("from outside: the outside button", OUTSIDE, INSIDE, OUT_PRESS),
                ("must fail: from inside, the outside button", INSIDE, OUTSIDE, IN_PRESS)]
        for name, here, there, want in rows:
            with self.subTest(name):
                self.assertEqual(mech.on_the_way(mechs, here, there), [door])
                self.assertEqual(mech.press_for(mechs, door, here), want)
                self.assertAlmostEqual(mech.door_route_s(mechs, here, there, walk_s),
                                       walk_s(math.dist(here, want)) + mech.PRESS_S + walk_s(math.dist(want, there)))
        # a way that passes nowhere near the door has none
        far = (OUTSIDE[0], OUTSIDE[1], OUTSIDE[2] + 20)
        self.assertIsNone(mech.door_route_s(mechs, OUTSIDE, far, walk_s))


class Through(unittest.TestCase):
    def test_rows(self):
        foot = min(DOOR, key=lambda c: c[1])
        # (walking toward) → the cell past the door
        rows = [("in from outside", INSIDE, (foot[0] + 1, foot[1], foot[2])),
                ("must fail: out from inside, stepped the wrong way", OUTSIDE, (foot[0] - 1, foot[1], foot[2]))]
        for name, there, want in rows:
            with self.subTest(name):
                self.assertEqual(mech.through_cell(tuple(DOOR), there), want)


class Planning(unittest.TestCase):
    """Estimates read the stored mechanisms and the snapshot, never the world."""

    def test_a_chest_behind_a_door(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = taught(tmp)
            route = lambda here, there, w, dimension=None: mech.door_route_s(      # noqa: E731
                [m for m in mech.load(path) if m["dimension"] == dimension], here, there, w)
            c = costmod.Cost(None)
            c.snap = type("Snap", (), {"feet": OUTSIDE, "dimension": DIM})()
            step = Step("withdraw", "minecraft:chest", 1, {"pos": list(INSIDE)})
            through = mech.door_route_s(mech.load(path), OUTSIDE, INSIDE,
                                        lambda d: costmod.walk_ticks(d) / costmod.TICKS_PER_S)
            with mock.patch.object(costmod, "DOOR_ROUTE", route):
                got = c._walk(step)
            # must fail: priced as dug through or as the straight walk the shut door does not allow
            self.assertEqual(got, round(through * costmod.TICKS_PER_S))
            self.assertGreater(got, costmod.walk_ticks(math.dist(OUTSIDE, INSIDE)))
            with mock.patch.object(costmod, "DOOR_ROUTE", route), mock.patch.object(c, "where", lambda k: INSIDE):
                self.assertAlmostEqual(c.route_s(["chest"]), through)

    def test_reachable_through_the_door(self):
        # must fail: the game's plan sees the shut door as solid and says no way
        with tempfile.TemporaryDirectory() as tmp:
            path = taught(tmp)
            route = lambda here, there, w, dimension=None: mech.door_route_s(mech.load(path), here, there, w)  # noqa: E731
            with mock.patch.object(nav, "DOOR_ROUTE", route), \
                    mock.patch.object(nav, "route_s", lambda *a, **k: (False, None)):
                found, seconds = nav.reachable(INSIDE, nav.Policy(), feet=OUTSIDE)
        self.assertTrue(found)
        self.assertIsNotNone(seconds)


class Press(unittest.TestCase):
    def test_rows(self):
        # (the door read as) → pressed?
        rows = [("shut: pressed", True, True),
                ("must fail: pressed when already open", False, False)]
        for name, shut, pressed in rows:
            with self.subTest(name):
                with mock.patch.object(mech, "solid_map", lambda cells: {tuple(c): shut for c in cells}), \
                        mock.patch.object(mech.api, "run") as run:
                    mech.press_mechanism.__wrapped__(None, OUT_PRESS, DOOR)
                self.assertEqual(run.called, pressed)
                if pressed:
                    task = run.call_args[0][0]
                    self.assertEqual((task["type"], (task["x"], task["y"], task["z"])), ("use", OUT_PRESS))

    def test_verify_reads_the_cells(self):
        call = type("Call", (), {"args": (None, OUT_PRESS, DOOR)})()
        for name, shut, ok in [("open", False, True), ("must fail: verified while still solid", True, False)]:
            with self.subTest(name), mock.patch.object(mech, "solid_map",
                                                       lambda cells: {tuple(c): shut for c in cells}):
                self.assertEqual(mech._opened_now(call), ok)


class OpenState(unittest.TestCase):
    def test_rows(self):
        # (solid by name, its open state) → shut
        rows = [("a wall", True, None, True), ("air", False, None, False),
                ("a door standing open: passable though still there", True, "true", False),
                ("must fail: a shut door read as open", True, "false", True)]
        for name, solid, opened, want in rows:
            with self.subTest(name):
                shut = mech.passable_now(lambda c: solid, lambda c, k: opened if k == mech.OPEN_PROP else None)
                self.assertEqual(shut((0, 0, 0)), want)


class Walk(unittest.TestCase):
    def test_a_walk_presses_and_never_digs_the_door(self):
        # must fail: the walk digs the opens cells instead of pressing
        with tempfile.TemporaryDirectory() as tmp:
            path = taught(tmp)
            posted = []
            here = {"x": OUTSIDE[0] + 0.5, "y": float(OUTSIDE[1]), "z": OUTSIDE[2] + 0.5, "blockX": OUTSIDE[0],
                    "blockY": OUTSIDE[1], "blockZ": OUTSIDE[2], "dimension": DIM, "onGround": True}

            def run(task, **k):
                posted.append(task)
                return {"status": "succeeded", "message": "arrived"}

            def door(cells):                 # shut until a use is posted
                return {tuple(c): not any(t["type"] == "use" for t in posted) for c in cells}
            with mock.patch.object(mech, "FILE", path), mock.patch.object(mech, "solid_map", door), \
                    mock.patch.object(nav, "DOORS", mech.doors_on_way), \
                    mock.patch.object(nav, "ROAD_MEM", None), \
                    mock.patch.object(nav.api, "run", run), mock.patch.object(mech.api, "run", run), \
                    mock.patch.object(nav.api, "get", lambda p: here), \
                    mock.patch.object(nav, "feet", lambda: OUTSIDE), \
                    mock.patch.object(nav, "Inventory", lambda: type("I", (), {"count": lambda s, k: 0})()), \
                    mock.patch.object(nav, "_arrived", lambda *a, **k: True), \
                    mock.patch.object(nav, "_doorways_between", lambda a, b: {}):
                nav._travel(INSIDE, nav.Policy(), 1.5, 1, None, "work", OUTSIDE, 0.0)
        kinds = [t["type"] for t in posted]
        self.assertEqual(kinds[:4], ["travel", "use", "travel", "travel"])   # beside the press, press, through, rest
        # the press from the cell before the door on our side, not from the press's reach
        self.assertEqual((posted[0]["x"], posted[0]["y"], posted[0]["z"]), mech.through_cell(tuple(DOOR), OUTSIDE))
        self.assertEqual(posted[0]["break"], False)
        cross = posted[2]
        # must fail: the crossing leg may break (press_door_to_chest 022603: the door shut, the wall beside it dug)
        self.assertEqual((cross["break"], cross["place"]), (False, False))
        self.assertEqual((cross["x"], cross["y"], cross["z"]), mech.through_cell(tuple(DOOR), INSIDE))
        avoid = {(c["x"], c["y"], c["z"]) for c in posted[3]["avoid"]}
        self.assertTrue(set(DOOR) <= avoid)                               # the door is never dug


class Cross(unittest.TestCase):
    def test_shut_before_through_is_pressed_again(self):
        # must fail: the crossing refused ("target unreachable": the pulse over) raises out of the walk
        # (press_door_to_chest 20260930-023025: None at 2 s, never through)
        posted, refused = [], []

        def run(task, **k):
            posted.append(task)
            if task["type"] == "travel" and (task["x"], task["y"], task["z"]) == mech.through_cell(tuple(DOOR), INSIDE) \
                    and not refused:
                refused.append(task)
                raise mech.api.Unreachable("travel: target unreachable; stopped at the closest reachable point", ())
            return {"status": "succeeded", "message": "ok"}
        with mock.patch.object(mech, "solid_map", lambda cells: {tuple(c): True for c in cells}), \
                mock.patch.object(mech.api, "run", run), mock.patch.object(mech.api, "detail", lambda *a: None), \
                mock.patch.object(mech, "press_mechanism", lambda ctx, p, d: posted.append({"type": "use"}) or "pressed"):
            got = mech.cross(OUT_PRESS, [list(c) for c in DOOR], mech.through_cell(tuple(DOOR), OUTSIDE),
                             mech.through_cell(tuple(DOOR), INSIDE))
        self.assertTrue(got)
        self.assertEqual([t["type"] for t in posted], ["travel", "use", "travel", "use", "travel"])


class CrossingFails(unittest.TestCase):
    def test_a_failed_crossing_fails_the_walk(self):
        # must fail: the walk falls back to an ordinary travel that digs beside the taught door (024258)
        with tempfile.TemporaryDirectory() as tmp:
            path = taught(tmp)
            posted = []
            here = {"x": OUTSIDE[0] + 0.5, "y": float(OUTSIDE[1]), "z": OUTSIDE[2] + 0.5, "blockX": OUTSIDE[0],
                    "blockY": OUTSIDE[1], "blockZ": OUTSIDE[2], "dimension": DIM, "onGround": True}

            def run(task, **k):
                posted.append(task)
                if task["type"] == "travel" and task.get("break") is False and \
                        (task["x"], task["y"], task["z"]) == mech.through_cell(tuple(DOOR), INSIDE):
                    raise mech.api.Unreachable("travel: target unreachable", ())
                return {"status": "succeeded", "message": "arrived"}
            with mock.patch.object(mech, "FILE", path), \
                    mock.patch.object(mech, "solid_map", lambda cells: {tuple(c): False for c in cells}), \
                    mock.patch.object(mech, "press_mechanism", lambda ctx, p, d: posted.append({"type": "use"}) or "open"), \
                    mock.patch.object(nav, "DOORS", mech.doors_on_way), mock.patch.object(nav, "ROAD_MEM", None), \
                    mock.patch.object(nav.api, "run", run), mock.patch.object(mech.api, "run", run), \
                    mock.patch.object(nav.api, "get", lambda p: here), mock.patch.object(mech.api, "detail", lambda *a: None), \
                    mock.patch.object(nav, "feet", lambda: OUTSIDE), \
                    mock.patch.object(nav, "Inventory", lambda: type("I", (), {"count": lambda s, k: 0})()), \
                    mock.patch.object(nav, "_doorways_between", lambda a, b: {}):
                with self.assertRaises(mech.api.NavFailed) as e:
                    nav._travel(INSIDE, nav.Policy(), 1.5, 1, None, "work", OUTSIDE, 0.0)
        self.assertIn("door crossing failed", str(e.exception))
        self.assertFalse([t for t in posted if t["type"] == "travel" and t.get("break")])     # nothing dug


class DoorwayIsNotInside(unittest.TestCase):
    def test_the_rows_reach(self):
        # the bench row's target and reach, one test for run, check and travel_to's verify (nav.there)
        from bonobo.bench import bench_common as bc
        from bonobo.bench.vocab import pos
        room = bc.DOOR_ROOM
        inside, reach = pos(room["inside"]), room["reach"]
        door = pos(room["door"][0])

        def at(cell):
            return {"x": cell[0] + 0.5, "y": float(cell[1]), "z": cell[2] + 0.5, "onGround": True}
        # must fail: standing in the doorway counted arrived (025641: x 4.3, arrived by 1.5 + slack)
        self.assertFalse(nav.there(at(door), inside, reach))
        self.assertTrue(nav.there(at(inside), inside, reach))


class Store(unittest.TestCase):
    def test_per_save_and_round_trip(self):
        self.assertIn(os.path.basename(mech.FILE), fresh.WORLD_SCOPED)
        with tempfile.TemporaryDirectory() as tmp:
            path = taught(tmp)
            self.assertEqual(len(mech.in_dimension(DIM, path)), 2)
            self.assertEqual(mech.remove(DIM, OUT_PRESS, path), 1)
            self.assertEqual([tuple(m["press"]) for m in mech.load(path)], [IN_PRESS])
            # a door re-taught: every old press of it goes (must fail: a stale press kept, 20260930-024050)
            mech.add(DIM, (0, 0, 0), DOOR, path=path)
            self.assertEqual(mech.forget_door(DIM, DOOR, path), 2)
            self.assertEqual(mech.load(path), [])


if __name__ == "__main__":
    unittest.main()
