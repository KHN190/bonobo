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
        mech.learn(DIM, press, DOOR, path=path)
    return path


class Geometry(unittest.TestCase):
    def test_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            mechs = mech.read_lessons(taught(tmp))
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
                [m for m in mech.read_lessons(path) if m["dimension"] == dimension], here, there, w)
            c = costmod.Cost(None)
            c.snap = type("Snap", (), {"feet": OUTSIDE, "dimension": DIM})()
            step = Step("withdraw", "minecraft:chest", 1, {"pos": list(INSIDE)})
            through = mech.door_route_s(mech.read_lessons(path), OUTSIDE, INSIDE,
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
            route = lambda here, there, w, dimension=None: mech.door_route_s(mech.read_lessons(path), here, there, w)  # noqa: E731
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
        from bonobo.world import Region
        rows = [("a wall", "stone", None, True), ("air", "air", None, False),
                ("a door standing open: passable though still there", "oak_door", "true", False),
                ("must fail: a shut door read as open", "oak_door", "false", True),
                ("must fail: an open barrel read as a door", "barrel", "true", True)]
        for name, block, opened, want in rows:
            with self.subTest(name):
                r = Region.__new__(Region)
                r.blocks, r.props = {(0, 0, 0): block}, {(0, 0, 0): {"open": opened}} if opened else {}
                self.assertEqual(mech.passable_now(r.solid, r.open_door)((0, 0, 0)), want)


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
        self.assertFalse([t for t in posted if t["type"] == "travel" and t.get("break")])     # nothing dug (I3)


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
                    mock.patch.object(mech, "solid_map", lambda cells: {tuple(c): True for c in cells}), \
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
        from bonobo.bench.core import pos
        from bonobo.bench.words import door as dw

        def at(cell):
            return {"x": cell[0] + 0.5, "y": float(cell[1]), "z": cell[2] + 0.5, "onGround": True}
        for shape, kind in dw.DOORS:
            with self.subTest(shape=shape, kind=kind):
                p = dw.door_parts(shape, kind, 1)
                inside, door = pos(p["points"]["inside"]), pos(p["cells"][0])
                # must fail: standing in the doorway counted arrived (025641: x 4.3, arrived by 1.5 + slack)
                self.assertFalse(nav.there(at(door), inside, mech.CROSS_RANGE))
                self.assertTrue(nav.there(at(inside), inside, mech.CROSS_RANGE))


HATCH_Y = 63                                           # a 2×2 hatch in the ground layer
HATCH = [(0, HATCH_Y, 0), (1, HATCH_Y, 0), (0, HATCH_Y, 1), (1, HATCH_Y, 1)]
HATCH_OUT, HATCH_IN = (3, HATCH_Y + 1, 0), (-1, HATCH_Y - 2, 0)     # a floor button above, a wall button below


class Hatch(unittest.TestCase):
    def mechs(self, close=False):
        return [{"dimension": DIM, "press": list(p), "opens": [list(c) for c in HATCH], "close": close}
                for p in (HATCH_OUT, HATCH_IN)]

    def test_side_by_the_hatch_axis(self):
        door = tuple(HATCH)
        above_west, below_east = (-3, HATCH_Y + 1, 0), (4, HATCH_Y - 2, 0)
        self.assertEqual(mech.crossing_axis(door, above_west), 1)
        # must fail: the side picked by x/z — west of the hatch would pick the inner button below
        self.assertEqual(mech.press_for(self.mechs(), door, above_west), HATCH_OUT)
        self.assertEqual(mech.press_for(self.mechs(), door, below_east), HATCH_IN)

    def test_through_the_hatch(self):
        door = tuple(HATCH)
        down, up = mech.through_cell(door, (0, HATCH_Y - 5, 0)), mech.through_cell(door, (0, HATCH_Y + 5, 0))
        self.assertEqual(down[1], HATCH_Y - 2)            # head under the hatch layer, clear of it
        self.assertEqual(up[1], HATCH_Y + 1)              # feet on it
        self.assertIn((down[0], HATCH_Y, down[2]), HATCH)


class OpenAndClose(unittest.TestCase):
    def run_way(self, shut, close, only=(IN_PRESS,)):
        posted = []
        mechs = [{"dimension": DIM, "press": list(p), "opens": [list(c) for c in DOOR], "close": close}
                 for p in only]
        state = {"shut": shut}

        def run(task, **k):
            posted.append(task)
            if task["type"] == "use":
                state["shut"] = not state["shut"]
            return {"status": "succeeded", "message": "ok"}
        with mock.patch.object(mech, "read_lessons", lambda path=None: mechs), \
                mock.patch.object(mech, "solid_map", lambda cells: {tuple(c): state["shut"] for c in cells}), \
                mock.patch.object(mech.api, "run", run), mock.patch.object(mech.api, "detail", lambda *a: None):
            mech.doors_on_way(OUTSIDE, INSIDE, dimension=DIM)
        return posted, state

    def test_open_with_no_press_on_our_side_is_walked(self):
        # must fail: reported unreachable (the only press is inside, the door open)
        posted, _ = self.run_way(shut=False, close=False)
        self.assertEqual(posted, [])

    def test_close_behind_only_when_taught(self):
        both = (OUT_PRESS, IN_PRESS)
        posted, state = self.run_way(shut=True, close=False, only=both)
        # must fail: close flag false, the door closed behind
        self.assertEqual([t["type"] for t in posted].count("use"), 1)
        self.assertFalse(state["shut"])
        posted, state = self.run_way(shut=True, close=True, only=both)
        uses = [(t["x"], t["y"], t["z"]) for t in posted if t["type"] == "use"]
        # must fail: close flag true, the door left open
        self.assertEqual(uses, [OUT_PRESS, IN_PRESS])
        self.assertTrue(state["shut"])


class ColumnDoor(unittest.TestCase):
    def test_axis_from_its_walls(self):
        # hello2 10:40/10:44: the side room's 1×2 door (48,53-54,-73) in a wall running along x
        door = ((48, 53, -73), (48, 54, -73))
        walls = {(47, 53, -73), (49, 53, -73)}
        solid = lambda c: tuple(c) in walls                 # noqa: E731
        here, there = (55, 53, -72), (48, 53, -90)          # far along x on one side, far along z on the other
        stand, past = mech.through_cell(door, here, solid), mech.through_cell(door, there, solid)
        # must fail: the ends on different axes — the stand in the wall (49,53,-73), a ping-pong crossing
        self.assertEqual((stand, past), ((48, 53, -72), (48, 53, -74)))
        self.assertNotIn(stand, walls)


class Store(unittest.TestCase):
    def test_per_save_and_round_trip(self):
        self.assertIn(os.path.basename(mech.FILE), fresh.WORLD_SCOPED)
        with tempfile.TemporaryDirectory() as tmp:
            path = taught(tmp)
            self.assertEqual(len(mech.in_dimension(DIM, path)), 2)
            self.assertEqual(mech.remove(DIM, OUT_PRESS, path), 1)
            self.assertEqual([tuple(m["press"]) for m in mech.read_lessons(path)], [IN_PRESS])


class HomeExit(unittest.TestCase):
    """A walk out of a home goes by its taught door (09:35: coal outside, a digging walk from the hall dug its west
    wall). The home: a box under the hatch, the hatch its roof; coal far outside."""

    BOX = ((-3, HATCH_Y - 4, -3), (4, HATCH_Y, 4))
    HALL, COAL = (2, HATCH_Y - 3, 2), (30, HATCH_Y + 1, 0)

    def walk(self, taught, boxes=None):
        from bonobo import memory
        posted, state = [], {"shut": True, "feet": self.HALL}
        mechs = [{"dimension": DIM, "press": list(p), "opens": [list(c) for c in HATCH], "close": True}
                 for p in (HATCH_OUT, HATCH_IN)] if taught else []

        def run(task, **k):
            posted.append(task)
            if task["type"] == "use":
                state["shut"] = not state["shut"]
            elif task["type"] == "travel" and task.get("status") is None:
                state["feet"] = (int(task["x"]), int(task["y"]), int(task["z"]))
            return {"status": "succeeded", "message": "arrived"}
        boxes = boxes or [self.BOX]
        policy = nav.Policy(protected=memory.Protected((), boxes, homes=[boxes]))
        here = lambda: {"x": state["feet"][0] + 0.5, "y": float(state["feet"][1]), "z": state["feet"][2] + 0.5,  # noqa: E731
                        "blockX": state["feet"][0], "blockY": state["feet"][1], "blockZ": state["feet"][2],
                        "dimension": DIM, "onGround": True}
        from bonobo.data import home_box_of

        def plan(cell, brk, plc, r, *a):        # the game's route: from the hall it digs the wall; a walk has none
            if home_box_of(boxes, state["feet"]) is None:
                return {"found": True, "steps": [{"x": 20, "y": HATCH_Y + 1, "z": 0, "actions": ["MINE 20,64,0"]}]}
            return ({"found": True, "steps": [{"x": 4, "y": HATCH_Y - 3, "z": 2, "actions": ["MINE 4,60,2"]}]}
                    if brk else {"found": False})
        with mock.patch.object(mech, "read_lessons", lambda path=None: mechs), \
                mock.patch.object(mech, "solid_map", lambda cells: {tuple(c): state["shut"] for c in cells}), \
                mock.patch.object(nav, "_plan_reply", plan), mock.patch.object(nav, "HOME_DOOR", mech.home_exit), \
                mock.patch.object(mech.api, "get", lambda p: here()), \
                mock.patch.object(nav, "DOORS", mech.doors_on_way), mock.patch.object(nav, "ROAD_MEM", None), \
                mock.patch.object(nav.api, "run", run), mock.patch.object(mech.api, "run", run), \
                mock.patch.object(mech.api, "detail", lambda *a: None), \
                mock.patch.object(nav.api, "get", lambda p: here()), \
                mock.patch.object(nav, "feet", lambda: state["feet"]), \
                mock.patch.object(nav, "Inventory", lambda: type("I", (), {"count": lambda s, k: 0})()), \
                mock.patch.object(nav, "_arrived", lambda *a, **k: True), \
                mock.patch.object(nav, "_doorways_between", lambda a, b: {}):
            nav._travel(self.COAL, policy, 1.5, 1, None, "work", self.HALL, 0.0)
        return posted, state

    def test_out_by_the_hatch(self):
        from bonobo.data import home_box_of
        posted, state = self.walk(taught=True)
        legs = [t for t in posted if t["type"] == "travel"]
        # must fail: a leg that may dig while it starts or ends in the home (the wall dug)
        inside = [t for t in legs if home_box_of([self.BOX], (t["x"], t["y"], t["z"])) is not None]
        self.assertTrue(inside)
        self.assertTrue(all(not t["break"] and not t["place"] for t in inside))
        uses = [(t["x"], t["y"], t["z"]) for t in posted if t["type"] == "use"]
        self.assertEqual(uses, [HATCH_IN, HATCH_OUT])          # pressed from the hall, shut behind from above
        self.assertTrue(state["shut"])
        self.assertEqual((legs[-1]["x"], legs[-1]["y"], legs[-1]["z"]), self.COAL)
        self.assertFalse(legs[-1].get("break"))                # a walk digs nothing (I3), outside too

    def test_the_door_in_another_box_of_the_home(self):
        # must fail: the body in the hall box, the hatch in the entrance box of the same home → NavFailed (live 3 boxes)
        hall = ((-3, HATCH_Y - 8, -3), (4, HATCH_Y - 3, 4))
        entrance = ((-3, HATCH_Y - 2, -3), (4, HATCH_Y, 4))
        posted, state = self.walk(taught=True, boxes=[hall, entrance])
        self.assertEqual([(t["x"], t["y"], t["z"]) for t in posted if t["type"] == "use"], [HATCH_IN, HATCH_OUT])
        self.assertTrue(all(not t["break"] for t in posted if t["type"] == "travel" and t["y"] < HATCH_Y))

    def test_the_exit_is_the_door_with_a_side_outside(self):
        # must fail: the side room's door (both sides in the home) taken as the way out (11:01: stand == through, stuck)
        side_door = [(-10, 64, 0), (-10, 65, 0)]
        home = [((-13, 60, -3), (-7, 66, 3)), ((-3, HATCH_Y - 4, -3), (4, HATCH_Y, 4))]
        mechs = [{"dimension": DIM, "press": [-11, 65, 1], "opens": [list(c) for c in side_door]},
                 {"dimension": DIM, "press": list(HATCH_IN), "opens": [list(c) for c in HATCH]}]
        walls = lambda door: (lambda c: c[2] != door[0][2])      # noqa: E731  (walls either side along z)
        door, inside, outside = mech.home_exit_door(mechs, home, walls)
        self.assertEqual(door, tuple(HATCH))
        self.assertNotEqual(inside, outside)
        self.assertIsNone(mech.home_exit_door(mechs[:1], home, walls))     # an interior door alone: no way out

    def test_no_taught_door_no_way(self):
        # must fail: no door taught → the walk digs out through the wall
        with self.assertRaises(nav.api.NavFailed):
            self.walk(taught=False)


if __name__ == "__main__":
    unittest.main()
