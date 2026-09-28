"""Pure-function tables: fluids, fresh, kernel, knowledge, nav, paths, perception, planner, retry, skill.

One table per function (or behaviour), every row a subTest; each table carries a normal, a boundary and a
must-fail row. Nothing here talks to the game: regions are block dicts, the environment is patched.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import fluids, fresh, kernel, knowledge, nav, paths, perception, planner, retry  # noqa: E402
from bonobo import skill as skillkit  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402
from bonobo.world import Region  # noqa: E402
from tests.world import FakeRegion, flat  # noqa: E402


def region(blocks, props=None, lo=(-8, -8, -8), hi=(8, 8, 8)):
    """A real Region with its I/O skipped: blocks (and block-state props) given, nothing fetched."""
    r = Region.__new__(Region)
    r.lo, r.hi, r.blocks, r.props = lo, hi, dict(blocks), dict(props or {})
    return r


def env(**values):
    """Patch the environment: a value sets the variable, None removes it (restored on exit)."""
    class _Env:
        def __enter__(self):
            self.patch = mock.patch.dict(os.environ, {})
            self.patch.__enter__()
            for k, v in values.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            return self

        def __exit__(self, *exc):
            return self.patch.__exit__(*exc)
    return _Env()


# ---------------------------------------------------------------- fluids


def soft_ground(patch=None, gap=False):
    """A stone walkway (y 63, x -10..10, z -2..2) with the feet at (0, 64, 0); `patch`: an x range of dirt three deep
    (y 61..63); `gap`: the walkway cut at x 4..5 (a drop to nothing)."""
    b = {(x, 63, z): "stone" for x in range(-10, 11) for z in range(-2, 3)}
    for x0, x1 in [patch] if patch else []:
        b.update({(x, y, z): "dirt" for x in range(x0, x1 + 1) for y in (61, 62, 63) for z in range(-2, 3)})
        b.update({(x, 60, z): "stone" for x in range(x0, x1 + 1) for z in range(-2, 3)})     # ground under it
    if gap:
        for x in (4, 5):
            for z in range(-2, 3):
                b.pop((x, 63, z), None)
    return FakeRegion((-12, 58, -4), (12, 68, 4), b)


class _Props:
    """A region read with block states: {pos: name}, {pos: {key: value}} (None: read without states)."""

    def __init__(self, blocks, props):
        self.blocks = blocks
        if props is not None:
            self.prop = lambda p, key: props.get(p, {}).get(key)


class CellsWith(unittest.TestCase):
    """world.cells_with: the one filter over block states (ripe wheat, frames without an eye)."""

    def test_over_the_table(self):
        from bonobo import world
        blocks = {(0, 64, 0): "wheat", (1, 64, 0): "wheat", (2, 64, 0): "end_portal_frame",
                  (3, 64, 0): "end_portal_frame", (4, 64, 0): "end_portal_frame"}
        props = {(0, 64, 0): {"age": "7"}, (1, 64, 0): {"age": "3"}, (2, 64, 0): {"eye": "true"},
                 (3, 64, 0): {"eye": "false"}}
        # (situation, region, name, key, value, want) → cells
        rows = [("ripe wheat", _Props(blocks, props), "wheat", "age", "7", True, [(0, 64, 0)]),
                ("frames without an eye (a state not read counts as not true)", _Props(blocks, props),
                 "end_portal_frame", "eye", "true", False, [(3, 64, 0), (4, 64, 0)]),
                ("must fail: another block's state is not this one's", _Props(blocks, props), "wheat", "eye", "true",
                 True, []),
                ("must fail: a region read without states says nothing", _Props(blocks, None), "wheat", "age", "7",
                 False, []),
                ("nothing of that name", _Props(blocks, props), "stone", "age", "7", True, [])]
        for name, region, block, key, value, want, cells in rows:
            with self.subTest(name):
                self.assertEqual(sorted(world.cells_with(region, block, key, value, want)), cells)


class StandingCells(unittest.TestCase):
    """terrain.standing_cells: the one reading of "one could stand here" (find_open_spot, find_shelter_spot)."""

    def test_over_the_table(self):
        from bonobo import terrain
        floor = {(x, 63, 0): "stone" for x in range(5)}
        # (situation, blocks, here, radius) → cells
        rows = [("a floor, open above: every cell over it", floor, (0, 64, 0), 10, [(x, 64, 0) for x in range(5)]),
                ("out of the radius: left out", floor, (0, 64, 0), 2, [(0, 64, 0), (1, 64, 0), (2, 64, 0)]),
                ("must fail: a roof at head height", dict(floor) | {(1, 65, 0): "stone"},
                 (0, 64, 0), 1, [(0, 64, 0)]),
                ("must fail: lava is no floor", dict(floor) | {(0, 63, 0): "lava"}, (0, 64, 0), 1, [(1, 64, 0)]),
                ("must fail: a block where the feet go (standing on it instead)", dict(floor) | {(0, 64, 0): "dirt"},
                 (0, 64, 0), 1, [(0, 65, 0), (1, 64, 0)]),
                ("cave air is air", dict(floor) | {(0, 64, 0): "cave_air", (0, 65, 0): "cave_air"}, (0, 64, 0), 1,
                 [(0, 64, 0), (1, 64, 0)])]
        for name, blocks, here, radius, want in rows:
            with self.subTest(name):
                region = FakeRegion((-1, 60, -1), (6, 70, 1), blocks)
                self.assertEqual(sorted(terrain.standing_cells(region, here, radius)), want)


class NearestSoft(unittest.TestCase):
    """terrain.nearest_soft: ground that digs by hand, found along the ground we stand on."""
    # (situation, region, feet) → (cell, steps) or None
    ROWS = [("soft right under the feet: dig here", soft_ground(patch=(-1, 1)), (0, 64, 0), ((0, 64, 0), 0)),
            ("dirt 8 blocks along the walkway: walk there", soft_ground(patch=(8, 9)), (0, 64, 0), ((8, 64, 0), 8)),
            ("must fail: only stone: none", soft_ground(), (0, 64, 0), None),
            ("dirt across a drop to nothing: not on this ground", soft_ground(patch=(8, 9), gap=True), (0, 64, 0),
             None),
            ("dirt past the radius: none", soft_ground(patch=(8, 9)), (-10, 64, 0), None)]

    def test_nearest_soft(self):
        from bonobo import terrain
        for name, region, feet, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(terrain.nearest_soft(region, feet, 3, radius=16), want)


class Fluids(unittest.TestCase):
    def test_is_source(self):
        p = (0, 0, 0)
        rows = [  # (why, region, fluid, expected)
            ("still water, level '0'", region({p: "water"}, {p: {"level": "0"}}), "water", True),
            ("still water, level int 0", region({p: "water"}, {p: {"level": 0}}), "water", True),
            ("must fail: flowing water, level 3: not a source", region({p: "water"}, {p: {"level": "3"}}), "water", False),
            ("no props read: every fluid cell counts", region({p: "water"}), "water", True),
            ("region without prop(): fluid cell counts", FakeRegion((-1,) * 3, (1,) * 3, {p: "water"}), "water", True),
            ("lava asked as water: wrong fluid", region({p: "lava"}, {p: {"level": "0"}}), "water", False),
            ("air: no fluid at all", region({}), "lava", False),
        ]
        for why, r, fluid, want in rows:
            with self.subTest(why):
                self.assertEqual(fluids.is_source(r, p, fluid), want)

    def test_clear_line(self):
        eye, cell, point = (0.5, 1.5, 0.5), (3, 1, 0), (3.5, 1.5, 0.5)
        rows = [  # (why, blocks, margin, expected)
            ("nothing in between", {}, 0.2, True),
            ("must fail: stone in the path", {(1, 1, 0): "stone"}, 0.2, False),
            ("stone only in the target cell itself", {(3, 1, 0): "stone"}, 0.2, True),
            ("stone behind the target", {(4, 1, 0): "stone"}, 0.2, True),
            ("stone beside the path, outside the margin", {(1, 1, 1): "stone"}, 0.2, True),
            ("same stone, margin wide enough to graze it", {(1, 1, 1): "stone"}, 0.6, False),
            ("water in the path is not solid", {(1, 1, 0): "water"}, 0.2, True),
        ]
        for why, blocks, margin, want in rows:
            with self.subTest(why):
                self.assertEqual(fluids.clear_line(region(blocks), eye, cell, point, margin), want)

    def test_lava_within(self):
        p = (0, 0, 0)
        rows = [  # (why, lava cell, r, expected)
            ("must fail: no lava anywhere", None, 1, False),
            ("lava beside the feet", (1, 0, 0), 1, True),
            ("lava in the floor layer", (0, -1, 0), 1, True),
            ("lava at head+1 (dy 2)", (0, 2, 0), 1, True),
            ("lava two out, r 1: outside the box", (2, 0, 0), 1, False),
            ("lava two out, r 2: inside", (2, 0, 0), 2, True),
            ("lava above dy 2: not looked at", (0, 3, 0), 2, False),
            ("lava two below: not looked at", (0, -2, 0), 2, False),
        ]
        for why, lava, r, want in rows:
            with self.subTest(why):
                blocks = {lava: "lava"} if lava else {}
                self.assertEqual(fluids.lava_within(region(blocks), p, r), want)

    def test_use_task(self):
        rows = [  # (item, aim, on_block, expected) (must fail: the two-coordinate aim below raises)
            ("minecraft:water_bucket", (1.5, 64.02, 2.5), True,
             {"type": "use_item", "item": "minecraft:water_bucket", "x": 1.5, "y": 64.02, "z": 2.5, "onBlock": True}),
            ("minecraft:bucket", (0, 0, 0), False,
             {"type": "use_item", "item": "minecraft:bucket", "x": 0, "y": 0, "z": 0, "onBlock": False}),
            ("minecraft:flint_and_steel", (-3.5, -60.0, 7.25), True,
             {"type": "use_item", "item": "minecraft:flint_and_steel", "x": -3.5, "y": -60.0, "z": 7.25,
              "onBlock": True}),
            ("minecraft:lava_bucket", [2, 3, 4], False,
             {"type": "use_item", "item": "minecraft:lava_bucket", "x": 2, "y": 3, "z": 4, "onBlock": False}),
        ]
        for item, aim, on_block, want in rows:
            with self.subTest(item):
                self.assertEqual(fluids.use_task(item, aim, on_block), want)
        with self.subTest("an aim with two coordinates cannot be a point"):
            with self.assertRaises(IndexError):
                fluids.use_task("minecraft:bucket", (1, 2), True)

    def test_floor_aim(self):
        rows = [  # (cell, expected) (must fail: the top face, below, is never the aim)
            ((0, 64, 0), (0.5, 64.02, 0.5)),
            ((-1, 0, -1), (-0.5, 0.02, -0.5)),
            ((10, -60, 3), (10.5, -59.98, 3.5)),
            ((2, 100, -7), (2.5, 100.02, -6.5)),
        ]
        for cell, want in rows:
            with self.subTest(cell):
                got = fluids.floor_aim(cell)
                self.assertEqual(len(got), 3)
                for g, w in zip(got, want):
                    self.assertAlmostEqual(g, w, places=9)
        with self.subTest("the aim is never the cell's own top face (y+1)"):
            self.assertNotEqual(fluids.floor_aim((0, 64, 0))[1], 65.0)


# ---------------------------------------------------------------- fresh / paths (environment stubbed)

class Paths(unittest.TestCase):
    def test_saves_dir(self):
        rows = [  # (why, instance arg, MC_INSTANCE, HOME, expected)
            ("explicit instance", "/a", None, "/h", "/a/saves"),
            ("explicit instance with ~", "~/a", None, "/h", "/h/a/saves"),
            ("None falls back to MC_INSTANCE", None, "/e", "/h", "/e/saves"),
            ("explicit instance wins over MC_INSTANCE", "/a", "/e", "/h", "/a/saves"),
            ("must fail: nothing set: no saves dir", None, None, "/h", None),
            ("empty string and nothing set: no saves dir", "", None, "/h", None),
        ]
        for why, inst, mc, home, want in rows:
            with self.subTest(why), env(MC_INSTANCE=mc, HOME=home):
                self.assertEqual(fresh.saves_dir(inst), want)

    def test_data_dir(self):
        rows = [  # (why, MC_DATA, XDG_DATA_HOME, HOME, expected)
            ("MC_DATA overrides", "/d", "/x", "/h", "/d"),
            ("MC_DATA with ~", "~/d", None, "/h", "/h/d"),
            ("XDG when no MC_DATA", None, "/x", "/h", "/x/bonobo"),
            ("must fail: an empty value read as set — empty MC_DATA is no override", "", "/x", "/h", "/x/bonobo"),
            ("nothing set: ~/.local/share", None, None, "/h", "/h/.local/share/bonobo"),
        ]
        for why, mc, xdg, home, want in rows:
            with self.subTest(why), env(MC_DATA=mc, XDG_DATA_HOME=xdg, HOME=home):
                self.assertEqual(paths.data_dir(), want)

    def test_instance_dir(self):
        rows = [  # (why, MC_INSTANCE, HOME, expected)
            ("set", "/i", "/h", "/i"),
            ("set with ~", "~/i", "/h", "/h/i"),
            ("must fail: empty", "", "/h", ""),
            ("unset: no default guessed", None, "/h", ""),
        ]
        for why, mc, home, want in rows:
            with self.subTest(why), env(MC_INSTANCE=mc, HOME=home):
                self.assertEqual(paths.instance_dir(), want)

    def test_api_base(self):
        rows = [  # (why, MC_API, expected)
            ("unset: the default port", None, "http://127.0.0.1:27599"),
            ("set", "http://10.0.0.2:1234", "http://10.0.0.2:1234"),
            ("set to the default", "http://127.0.0.1:27599", "http://127.0.0.1:27599"),
            ("must fail: empty is taken as given, not defaulted", "", ""),
        ]
        for why, api, want in rows:
            with self.subTest(why), env(MC_API=api):
                self.assertEqual(paths.api_base(), want)


# ---------------------------------------------------------------- kernel

class _Act:
    def __init__(self, name):
        self.name = name


class _Model:
    def __init__(self, actions, refuse=(), default=None):
        self.actions, self.refuse, self.default = actions, set(refuse), default

    def admissible(self, state, action):
        return (False, f"no {action.name}") if action.name in self.refuse else (True, "")


class Kernel(unittest.TestCase):
    def test_survivors(self):
        a, b, c = _Act("a"), _Act("b"), _Act("c")
        rows = [  # (why, model, state, allowed names, rejected)
            ("all admissible", _Model([a, b]), {}, ["a", "b"], []),
            ("one refused, with its reason", _Model([a, b], refuse={"b"}), {}, ["a"], [("b", "no b")]),
            ("the default is exempt from the veto", _Model([a, c], refuse={"a", "c"}, default=c), {},
             ["c"], [("a", "no a")]),
            ("everything refused", _Model([a, b], refuse={"a", "b"}), {}, [], [("a", "no a"), ("b", "no b")]),
            ("must fail: no actions", _Model([]), {}, [], []),
            ("actions from a callable of the state", _Model(lambda s: [a, b][:s["n"]]), {"n": 1}, ["a"], []),
        ]
        for why, model, st, allowed, rejected in rows:
            with self.subTest(why):
                got_allowed, got_rejected = kernel.survivors(model, st)
                self.assertEqual([x.name for x in got_allowed], allowed)
                self.assertEqual(got_rejected, rejected)

    def test_choice_score_and_name(self):
        rows = [  # (why, action, value_s, cost_s, score, name)
            ("value beats cost", _Act("mine"), 10.0, 4.0, 6.0, "mine"),
            ("defaults: nothing gained", _Act("idle"), 0.0, 0.0, 0.0, "idle"),
            ("cost beats value: negative", _Act("walk"), 2.0, 5.0, -3.0, "walk"),
            ("action without a name", object(), 1.0, 1.0, 0.0, None),
            ("no action at all", None, 3.0, 0.5, 2.5, None),
        ]
        for why, action, value, cost, score, name in rows:
            with self.subTest(why):
                ch = kernel.Choice(action, value_s=value, cost_s=cost)
                self.assertEqual(ch.score, score)
                self.assertEqual(ch.name, name)


# ---------------------------------------------------------------- knowledge

class Knowledge(unittest.TestCase):
    def test_takeable_blocks(self):
        got = knowledge.takeable_blocks()
        rows = [  # (block, listed?)
            ("crafting_table", True),
            ("blast_furnace", True),
            ("wall_torch", True),
            ("white_bed", True),
            ("stone", False),          # mined, not taken back (must fail: stone is not taken back)
            ("minecraft:crafting_table", False),   # bare block names only
        ]
        for block, want in rows:
            with self.subTest(block):
                self.assertEqual(block in got, want)
        with self.subTest("sorted, each block once"):
            self.assertEqual(got, sorted(set(got)))


# ---------------------------------------------------------------- nav

class Nav(unittest.TestCase):
    def test_waypoints(self):
        # (situation, here, target, leg) → the points walked to, ending at the target
        rows = [("the portal trip: legs of 40, height interpolated", (-258, 65, 270), (-366, 120, 191), 40,
                 [(-285, 79, 250), (-312, 92, 230), (-339, 106, 211), (-366, 120, 191)]),
                ("boundary: within one leg, the target alone", (0, 64, 0), (10, 64, 0), 40, [(10, 64, 0)]),
                ("a straight line north", (0, 70, 0), (0, 70, -200), 40,
                 [(0, 70, -40), (0, 70, -80), (0, 70, -120), (0, 70, -160), (0, 70, -200)]),
                ("a target given as a list comes back a tuple", (5, 60, 5), [105, 40, -95], 40,
                 [(30, 55, -20), (55, 50, -45), (80, 45, -70), (105, 40, -95)]),
                ("must fail: travel's leg (nav.LEG) cuts other hops than the portal trip's", (-258, 65, 270),
                 (-366, 120, 191), 48, [(-294, 83, 244), (-330, 102, 217), (-366, 120, 191)])]
        for name, here, target, leg, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.waypoints(here, target, leg), want)

    def test_safe_destination(self):
        pos = (0.0, 64.0, 0.0)
        rows = [  # (why, hazards, expected)
            ("no hazards (None)", None, pos),
            ("no hazards (empty)", [], pos),
            ("far static hazard, two-element form", [((20.0, 64.0, 0.0), 1.0)], pos),
            ("far static hazard, three-element form", [((0.0, 64.0, 20.0), 2.0, (0.0, 0.0, 0.0))], pos),
            ("must fail: engulfed by a huge hazard: nowhere clear", [(pos, 1000.0)], None),
        ]
        for why, hz, want in rows:
            with self.subTest(why):
                self.assertEqual(nav.safe_destination(pos, hz), want)

    def test_landing(self):
        """nav.landing: evade's walk ends on connected ground — never past a drop that kills (the sky platform)."""
        lo, hi = (-18, 185, -3), (18, 205, 3)

        def floor(xs, y, name="stone"):
            return {(x, y, z): name for x in xs for z in range(-3, 4)}
        rows = [  # (why, blocks, expected landing walking from (0.5, 200, 0.5) toward (16, 200, 0))
            ("a sky platform x -3..3: to its edge, not off it", floor(range(-3, 4), 199), (3, 200, 0)),
            ("the platform, a pool below its edge: over the edge into the water",
             {**floor(range(-3, 4), 199), **floor(range(4, 18), 190, "water")}, (16, 191, 0)),
            ("must fail: a one-block pillar: nowhere to go (None: fight or wall in)", floor(range(0, 2), 199), None),
            ("flat ground: the whole way", floor(range(-18, 19), 199), (16, 200, 0)),
            ("a terrace two down: stepped down onto it", {**floor(range(-3, 4), 199), **floor(range(4, 19), 197)},
             (16, 198, 0)),
        ]
        for why, blocks, want in rows:
            with self.subTest(why):
                self.assertEqual(nav.landing(FakeRegion(lo, hi, blocks), (0.5, 200.0, 0.5), (16, 200, 0)), want)

    def test_reachable_when_the_game_cannot_say(self):
        """nav.reachable with no answer from the game (/plan unasked): maybe — unless plainly below a drop."""
        from unittest import mock
        feet = (0, 200, 0)
        rows = [  # (why, cell, feet given, expected)
            ("must fail: a tree 100 below the platform, 20 across: no", (20, 100, 0), feet, False),
            ("a tree on the same ground 30 off: maybe", (30, 200, 0), feet, True),
            ("a tree 3 below (a safe drop): maybe", (2, 197, 0), feet, True),
            ("a valley 20 down, 60 across (a slope): maybe", (60, 180, 0), feet, True),
            ("no feet given: maybe, as before", (20, 100, 0), None, True),
        ]
        policy = nav.Policy()
        for why, cell, at, want in rows:
            with self.subTest(why), mock.patch.object(nav, "route_s", return_value=(None, None)):
                self.assertIs(nav.reachable(cell, policy, 2.0, feet=at)[0], want)

    def test_climb_out_tasks(self):
        """nav.climb_out_tasks: a swimmer beside the bank faces it and holds forward+jump until standing."""
        land = (10, 200, 10)
        want = [{"type": "look", "x": 10.5, "y": 200.5, "z": 10.5},
                {"type": "input", "keys": ["forward", "jump"], "until": "onGround", "ticks": nav.CLIMB_TICKS}]
        rows = [  # (why, state, expected)
            ("in the water against the bank: face it, climb", {"x": 9.2, "y": 199.3, "z": 10.5, "onGround": False,
                                                              "inWater": True}, want),
            ("must fail: already standing on it: nothing", {"x": 10.5, "y": 200.0, "z": 10.5, "onGround": True,
                                                  "inWater": False}, []),
            ("in the water 6 off the bank: swim first, nothing", {"x": 4.5, "y": 199.3, "z": 10.5, "onGround": False,
                                                                 "inWater": True}, []),
            ("dry but beside it (a step short): not a swimmer's move", {"x": 9.5, "y": 200.0, "z": 10.5,
                                                                       "onGround": True, "inWater": False}, []),
        ]
        for why, st, expected in rows:
            with self.subTest(why):
                self.assertEqual(nav.climb_out_tasks(st, land), expected)

    def test_there_at_the_cell_itself(self):
        """nav.there with range 0 is the cell: dig_in walked to 10006.24 for the soft spot at 10007 and range 0.5
        called it there (floored to 10006, 1 ≤ 0.5 + ARRIVE_SLACK), then dug the stone column beside it."""
        spot = (10007, 200, 10000)
        rows = [  # (why, body x, range) → there
            ("the probe: one column short, range 0.5 — there (the bug)", 10006.2355, 0.5, True),
            ("must fail: the probe, the cell itself asked: not there", 10006.2355, 0, False),
            ("on the cell, off its centre: there", 10007.9, 0, True),
            ("on the cell's centre: there", 10007.5, 0, True),
            ("one column past it: not there", 10008.1, 0, False),
        ]
        for why, x, range_, want in rows:
            with self.subTest(why):
                st = {"x": x, "y": 200.0, "z": 10000.5, "onGround": True}
                self.assertIs(nav.there(st, spot, range_), want)

    def test_one_look_per_round(self):
        """world.nearest: on a jar with perBlock, one /find answers every kind of the union for the round."""
        from unittest import mock
        from bonobo import api, world
        answer = {"blocks": [{"block": "minecraft:iron_ore", "distance": 6.0},
                             {"block": "minecraft:oak_log", "distance": 3.0}]}
        rows = [("new jar: iron, then logs, then stone — one request", True, [["iron_ore"], ["oak_log"], ["stone"]],
                 [6.0, 3.0, None], 1),
                ("old jar: each group asked once", False, [["iron_ore"], ["oak_log"], ["iron_ore"]], [6.0, 3.0, 6.0], 2),
                ("new jar, moved between asks: asked again", True, [["iron_ore"], "move", ["iron_ore"]],
                 [6.0, None, 6.0], 2),
                ("must fail: past the radius: None", True, [["iron_ore", 5]], [None], 1)]
        for name, new, asks, want, requests in rows:
            with self.subTest(name):
                calls = []
                world._SIGHT.update(key=None, t=0.0, near={})
                world._PER_BLOCK[:] = [new]
                feet = [(0, 64, 0)]
                got = []
                def ask(path):
                    calls.append(path)
                    wanted = path.split("blocks=")[1].split("&")[0].split(",")
                    return {"blocks": [h for h in answer["blocks"] if h["block"] in wanted]}
                with mock.patch.object(api, "get", side_effect=ask):
                    for a in asks:
                        if a == "move":
                            feet[0] = (5, 64, 0)
                            got.append(None)
                            continue
                        kinds, radius = (a[:-1], a[-1]) if isinstance(a[-1], int) else (a, 48)
                        got.append(world.nearest(kinds, feet[0], "minecraft:overworld", radius,
                                                 union=["iron_ore", "oak_log", "stone"]))
                self.assertEqual((got, len(calls)), (want, requests))
                if new:
                    self.assertIn("perBlock=1", calls[0])
        world._PER_BLOCK[:] = []

    def test_at_rest(self):
        rows = [  # (why, state, expected)
            ("on the ground", {"onGround": True}, True),
            ("in water", {"onGround": False, "inWater": True}, True),
            ("on a ladder", {"climbing": True}, True),
            ("in the air", {"onGround": False, "inWater": False, "climbing": False}, False),
            ("must fail: no reading at all", {}, False),
        ]
        for why, st, want in rows:
            with self.subTest(why):
                self.assertEqual(nav.at_rest(st), want)

    def test_ashore(self):
        land = (10, 200, 10)
        rows = [  # (why, state, expected)
            ("in the water 1.4 off the bank (the jar called it arrived)",
             {"x": 8.6, "y": 199.4, "z": 10.5, "onGround": False, "inWater": True}, False),
            ("standing on the bank, dry", {"x": 10.5, "y": 200.0, "z": 10.5, "onGround": True, "inWater": False}, True),
            ("must fail: on the bank cell but the feet still in water", {"x": 10.5, "y": 200.0, "z": 10.5, "onGround": True,
                                                              "inWater": True}, False),
            ("on the ground a block below the bank", {"x": 10.5, "y": 199.0, "z": 10.5, "onGround": True,
                                                      "inWater": False}, False),
            ("dry ground one cell off the bank (0.3 is the cell)", {"x": 11.5, "y": 200.0, "z": 10.5,
                                                                    "onGround": True, "inWater": False}, False),
        ]
        for why, st, want in rows:
            with self.subTest(why):
                self.assertIs(nav.ashore(st, land), want)

    def test_walked_closer(self):
        start, target = (0, 64, 0), (10, 64, 0)
        rows = [  # (why, here, expected)
            ("exactly two blocks gained (boundary)", (2, 64, 0), True),
            ("five blocks gained", (5, 64, 0), True),
            ("must fail: one block gained: not enough", (1, 64, 0), False),
            ("did not move", (0, 64, 0), False),
            ("walked away", (-3, 64, 0), False),
            ("sideways, same distance", (0, 64, 10), False),
        ]
        for why, here, want in rows:
            with self.subTest(why):
                self.assertEqual(nav.walked_closer(start, here, target), want)

    def test_dig_down_tasks(self):
        feet = (0, 64, 0)

        def mine(y):
            return {"type": "mine", "x": 0, "y": y, "z": 0, "collect": False, "requireDrops": False, "down": True}
        wait = {"type": "wait", "ticks": 6}
        ladder = {"type": "place", "item": "minecraft:ladder", "x": 0, "y": 65, "z": 0,
                  "against": {"x": 1, "y": 65, "z": 0}}
        rows = [  # (why, extra blocks, depth, protected, ladders, expected (tasks, safe))
            ("two blocks of stone", {}, 2, (), False, ([mine(63), wait, mine(62), wait], 2)),
            ("stops above the region's floor (air below)", {}, 5, (), False,
             ([mine(63), wait, mine(62), wait, mine(61), wait], 3)),
            ("must fail: stops beside water", {(1, 62, 0): "water"}, 3, (), False, ([mine(63), wait], 1)),
            ("air cell is walked through, not mined", {(0, 63, 0): "air"}, 1, (), False, ([wait], 1)),
            ("ladder hung on the wall above the head", {(1, 65, 0): "stone"}, 1, (), True,
             ([mine(63), wait, ladder], 1)),
            ("ladders wanted, no wall: none placed", {}, 1, (), True, ([mine(63), wait], 1)),
        ]
        for why, extra, depth, protected, ladders, want in rows:
            with self.subTest(why):
                r = flat(lo=(-4, 60, -4), hi=(4, 70, 4), floor_y=63)
                r.blocks.update(extra)
                self.assertEqual(nav.dig_down_tasks(r, feet, depth, protected, ladders), want)
        fails = [  # (why, extra blocks, protected)
            ("must fail: lava right under the feet", {(0, 63, 0): "lava"}, ()),
            ("bedrock under the feet", {(0, 63, 0): "bedrock"}, ()),
            ("the first cell is protected", {}, ((0, 63, 0),)),
            ("water beside the first cell", {(0, 63, 1): "water"}, ()),
        ]
        for why, extra, protected in fails:
            with self.subTest(why):
                r = flat(lo=(-4, 60, -4), hi=(4, 70, 4), floor_y=63)
                r.blocks.update(extra)
                with self.assertRaises(NotAvailable):
                    nav.dig_down_tasks(r, feet, 3, protected)


# ---------------------------------------------------------------- perception (module memory reset per test)

class Perception(unittest.TestCase):
    def setUp(self):
        self._saved = (perception.HURT_RATE, perception._HP_SEEN, list(perception.ANSWERED))

    def tearDown(self):
        perception.HURT_RATE, perception._HP_SEEN = self._saved[0], self._saved[1]
        perception.ANSWERED[:] = self._saved[2]

    def test_note_hurt_then_hurt_rate(self):
        rows = [  # (why, [(health, t)], expected rate)
            ("must fail: first reading: nothing to difference", [(20, 0.0)], 0.0),
            ("two hp in one second", [(20, 0.0), (18, 1.0)], 2.0),
            ("four hp in two seconds", [(20, 0.0), (16, 2.0)], 2.0),
            ("readings too close together: ignored", [(20, 0.0), (18, 0.005)], 0.0),
            ("gap over three seconds: ignored", [(20, 0.0), (18, 4.0)], 0.0),
            ("healing is no hurt", [(10, 0.0), (14, 1.0)], 0.0),
            ("quiet second after a hit: decays by 0.7", [(20, 0.0), (18, 1.0), (18, 2.0)], 1.4),
        ]
        for why, reads, want in rows:
            with self.subTest(why):
                perception.HURT_RATE, perception._HP_SEEN = 0.0, None
                got = None
                for hp, t in reads:
                    got = perception.note_hurt({"health": hp}, now=t)
                self.assertAlmostEqual(got, want, places=9)
                self.assertAlmostEqual(perception.hurt_rate(), want, places=9)

    def test_answered_since(self):
        looks = [{"t": 1.0, "outcome": "a"}, {"t": 2.0, "outcome": "b"}, {"t": 3.0, "outcome": "c"}]
        rows = [  # (why, mark, expected outcomes)
            ("from the start", 0, ["a", "b", "c"]),
            ("after two", 2, ["c"]),
            ("must fail: mark at the end: nothing new", 3, []),
            ("mark past the end: nothing", 10, []),
        ]
        for why, mark, want in rows:
            with self.subTest(why):
                perception.ANSWERED[:] = looks
                self.assertEqual([x["outcome"] for x in perception.answered_since(mark)], want)
        with self.subTest("a copy: changing it leaves the record alone"):
            perception.ANSWERED[:] = looks
            perception.answered_since(0).clear()
            self.assertEqual(len(perception.ANSWERED), 3)

    def test_sword_level(self):
        rows = [  # (why, tiers, expected)
            ("must fail: no sword: fist", [], 0),
            ("wooden/golden (tier 0) is level 1", [0], 1),
            ("stone", [1], 1),
            ("iron", [2], 2),
            ("diamond", [3], 3),
            ("netherite capped at 3", [4], 3),
            ("best of several", [0, 2, 1], 2),
        ]
        for why, tiers, want in rows:
            with self.subTest(why):
                self.assertEqual(perception.sword_level(tiers), want)


# ---------------------------------------------------------------- planner

class _Bag:
    def __init__(self, tools):
        self._tools = tools

    def tools(self, kind):
        return self._tools.get(kind, [])


class Planner(unittest.TestCase):
    def test_hunts_a_fighter(self):
        rows = [  # (why, types, expected)
            ("a zombie fights back", ["minecraft:zombie"], True),
            ("must fail: a cow does not", ["minecraft:cow"], False),
            ("mixed hunt: one fighter is enough", ["minecraft:cow", "minecraft:skeleton"], True),
            ("nothing hunted", [], False),
            ("bare id is not the table's key", ["zombie"], False),
        ]
        for why, types, want in rows:
            with self.subTest(why):
                self.assertEqual(planner.hunts_a_fighter(types), want)

    def test_tool_ok(self):
        rows = [  # (why, inv, kind, tier, min_left, expected)
            ("must fail: no tools() at all", object(), "pickaxe", 1, 10, False),
            ("right tier, plenty left", _Bag({"pickaxe": [(1, 100, None)]}), "pickaxe", 1, 10, True),
            ("better tier counts", _Bag({"pickaxe": [(3, 100, None)]}), "pickaxe", 2, 10, True),
            ("tier too low", _Bag({"pickaxe": [(1, 100, None)]}), "pickaxe", 2, 10, False),
            ("durability at the limit (boundary)", _Bag({"pickaxe": [(2, 10, None)]}), "pickaxe", 2, 10, True),
            ("durability under the limit", _Bag({"pickaxe": [(2, 9, None)]}), "pickaxe", 2, 10, False),
            ("worn but min_left 1", _Bag({"sword": [(1, 5, None)]}), "sword", 1, 1, True),
            ("other kind carried only", _Bag({"axe": [(3, 100, None)]}), "pickaxe", 0, 10, False),
        ]
        for why, inv, kind, tier, min_left, want in rows:
            with self.subTest(why):
                self.assertEqual(knowledge.tool_ok(inv, kind, tier, min_left), want)
        with self.subTest("default min_left is 10"):
            self.assertEqual(knowledge.tool_ok(_Bag({"pickaxe": [(2, 9, None)]}), "pickaxe", 2), False)

    def test_virtual_inventory_counts(self):
        rows = [  # (why, counts, ops, token, expected available)
            ("carried", {"minecraft:stick": 3}, [], "stick", 3),
            ("consumed part", {"minecraft:stick": 3}, [("consume", "stick", 2)], "stick", 1),
            ("consumed more than carried: floors at 0", {"minecraft:stick": 3}, [("consume", "stick", 5)],
             "stick", 0),
            ("added plain item", {}, [("add", "stick", 4)], "stick", 4),
            ("added group token counts as produced", {}, [("add", "planks", 4)], "planks", 4),
            ("produced is consumed first", {"minecraft:oak_planks": 2},
             [("add", "planks", 4), ("consume", "planks", 3)], "planks", 3),
            ("must fail: nothing carried", {}, [], "minecraft:diamond", 0),
        ]
        for why, counts, ops, token, want in rows:
            with self.subTest(why):
                inv = planner.VirtualInventory(counts, [])
                for op, tok, n in ops:
                    getattr(inv, op)(tok, n)
                self.assertEqual(inv.available(token), want)

    def test_virtual_inventory_has_tool(self):
        tools = [("pickaxe", 2, 50), ("sword", 1, 3)]
        rows = [  # (why, kind, tier, min_left, expected)
            ("iron pickaxe for stone", "pickaxe", 1, 10, True),
            ("exact tier and durability (boundary)", "pickaxe", 2, 50, True),
            ("must fail: tier too high", "pickaxe", 3, 10, False),
            ("worn sword under min_left", "sword", 1, 10, False),
            ("no axe", "axe", 0, 1, False),
        ]
        inv = planner.VirtualInventory({}, tools)
        for why, kind, tier, min_left, want in rows:
            with self.subTest(why):
                self.assertEqual(inv.has_tool(kind, tier, min_left), want)


# ---------------------------------------------------------------- retry / skill

class RetryAndSkill(unittest.TestCase):
    def test_cause_key(self):
        rows = [  # (cause, place, expected)
            ("stuck", ((1, 4, -2), False), "stuck@((1, 4, -2), False)"),
            ("error", "here", "error@here"),
            ("", None, "@None"),
            ("a@b", 3, "a@b@3"),  # must fail: a separator inside the cause is kept, not split
        ]
        for cause, place, want in rows:
            with self.subTest(cause):
                self.assertEqual(retry.cause_key(cause, place), want)
        with self.subTest("different places keep causes apart"):
            self.assertNotEqual(retry.cause_key("stuck", 1), retry.cause_key("stuck", 2))

    def test_contract_defaults(self):
        def documented():
            """First line.
            Second line."""

        def bare():
            pass

        def done(c):
            return True

        def verify(c):
            return False
        rows = [  # (why, fn, verify, expected verify, expected doc)
            ("a documented skill: its doc's first line, verify defaults to done", documented, None, done, "First line."),
            ("no docstring: no doc", bare, None, done, ""),
            ("a lambda: no doc", lambda c: None, None, done, ""),
            ("must fail: a verify given is kept, not replaced by done", bare, verify, verify, ""),
        ]
        for why, fn, ver, want_ver, want_doc in rows:
            with self.subTest(why):
                c = skillkit.Contract("x", fn, skillkit.Spec(done=done, verify=ver, budget=30, stall=10))
                self.assertIs(c.verify, want_ver)
                self.assertEqual(c.doc, want_doc)
                self.assertEqual(c.units(object()), 1)
                self.assertEqual(c.key(object()), "x")
                self.assertEqual(c.provides, {})


if __name__ == "__main__":
    unittest.main()


class Frontier(unittest.TestCase):
    """memory.frontier over the section map (16×16×16): what to look over next, per kind, in its own y band."""
    HERE = (8, 70, 8)                  # section (0, 4, 0)
    D = "diamond_ore"                  # static: absent holds 2 days
    S = "minecraft:sheep"              # mobile: absent holds 2400 ticks

    def mem(self):
        import tempfile
        from bonobo.memory import Memory
        m = Memory(os.path.join(tempfile.mkdtemp(prefix="frontier"), "notes.json"))
        m.clock = 1000
        return m

    def test_choice(self):
        from bonobo import memory
        seen_all = {(dx, 4, dz): {"t": 1000, "kinds": {}, "looked": {"sheep": 1000}}
                    for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        # (situation, section map, kinds, band, the first section chosen, or [] when nothing is left)
        rows = [("nothing looked over: the nearest beside us", {}, [self.S], None, (-1, 4, 0)),
                ("the west one looked over for sheep: the next nearest", {(-1, 4, 0): seen_all[(-1, 4, 0)]}, [self.S],
                 None, (0, 4, -1)),
                ("must fail: every section near looked over lately", seen_all, [self.S], None, []),
                ("a look for sheep says nothing about diamonds", seen_all, [self.D], None, (-1, 4, 0)),
                ("seen at y 64, the band at y -58: the frontier is down there", seen_all, [self.D], -58, (0, -4, 0))]
        for name, smap, kinds, band, want in rows:
            with self.subTest(name):
                got = memory.frontier(smap, self.HERE, kinds, 1000, band=lambda k, b=band: b, radius=1)
                self.assertEqual(got[0] if got else [], want)

    def test_out_of_look_is_a_skip_not_a_look(self):
        """A band section out of look range from any reachable stand is skipped for the search, never "looked over";
        one a stand within look radius reaches is looked at and answered."""
        from bonobo import explore, memory
        band = (0, -4, 0)                                  # y -58 under a y 70 stand
        rows = [("surface stand, the band 128 below: skipped, not answered", 70, -56, "skip", False, True),
                ("a stand within look radius (a cave floor near the band): looked, answered", -40, -56, "look", True,
                 False),
                ("must fail: a skip is not a look — the section stays unanswered for a later search", 70, -56, "skip",
                 False, True),
                ("a dig-down stand counts where the search may dig", 70, -56, "dig", True, False)]
        for name, stand_y, band_y, how, answered, skipped in rows:
            with self.subTest(name):
                m = self.mem()
                reach = explore.stand_in_look_range(stand_y, band_y, 48, can_dig=how == "dig")
                self.assertEqual(reach, how != "skip")
                if reach:
                    m.see_sections("minecraft:overworld", (8, band_y, 8), 0, {}, [self.D])
                else:
                    m.skip_section("minecraft:overworld", band)
                smap = m.section_map("minecraft:overworld")
                self.assertEqual(memory.covered(smap.get(band), [self.D], m.clock), answered)
                left = [s for s, _c in m.frontier("minecraft:overworld", self.HERE, [self.D], band=lambda k: -56)]
                self.assertEqual(band not in left, answered or skipped)
                if skipped:
                    self.assertTrue(memory.out_of_look({band: m.clock}, band, [self.D], m.clock))
                    self.assertFalse(memory.out_of_look({band: m.clock}, band, [self.D],
                                                        m.clock + memory.absent_ttl(self.D) + 1))    # expires

    def test_stand_in_look_range(self):
        from bonobo import explore
        rows = [("a cave floor 16 above the band", (-40, -56, 48, False), True),
                ("boundary: exactly the radius apart", (40, -8, 48, False), True),
                ("a dig-down stand", (70, -56, 48, True), True),
                ("must fail: one past the radius", (41, -8, 48, False), False),
                ("must fail: the surface, the band 126 below, no digging", (70, -56, 48, False), False)]
        for name, args, want in rows:
            with self.subTest(name):
                self.assertIs(explore.stand_in_look_range(*args), want)

    def test_out_of_look(self):
        from bonobo import memory
        ttl = memory.absent_ttl(self.D)
        band = (0, -4, 0)
        rows = [("skipped lately", {band: 1000}, 1000 + 10, True),
                ("boundary: the skip at its ttl", {band: 1000}, 1000 + ttl, True),
                ("no clock: a skip holds", {band: 1000}, None, True),
                ("must fail: the skip expired", {band: 1000}, 1000 + ttl + 1, False),
                ("must fail: another section skipped", {(1, -4, 0): 1000}, 1010, False),
                ("must fail: no skips", None, 1010, False)]
        for name, skips, tick, want in rows:
            with self.subTest(name):
                self.assertIs(memory.out_of_look(skips, band, [self.D], tick), want)

    def test_ttl_by_class(self):
        from bonobo import memory
        row = {"t": 0, "kinds": {}, "looked": {"diamond_ore": 0, "sheep": 0}}
        # (kind, ticks since the look) → still covered
        rows = [("an ore looked for a day ago: still no ore", self.D, 24000, True),
                ("an ore looked for three days ago: look again", self.D, 3 * 24000, False),
                ("sheep looked for a minute ago: none", self.S, 1200, True),
                ("sheep looked for five minutes ago: they may have walked in", self.S, 6000, False),
                ("must fail: never looked for: not covered", "iron_ore", 10, False)]
        for name, kind, age, want in rows:
            with self.subTest(name):
                self.assertEqual(memory.covered(row, [kind], age), want)
        # (situation, the look's stamp, the tick now) → covered: an unstamped look is no answer; a stamped one expires
        for name, stamp, tick, want in [("must fail: a look with no game time is refused", None, 5000, False),
                                        ("a clocked look, just now: covered", 5000, 5000, True),
                                        ("the same look past its TTL: expired", 5000, 5000 + memory.absent_ttl(self.S) + 1,
                                         False),
                                        ("must fail: never looked for", "absent", 5000, False)]:
            with self.subTest(name):
                looked = {} if stamp == "absent" else {"sheep": stamp}
                self.assertEqual(memory.covered({"t": 0, "kinds": {}, "looked": looked}, [self.S], tick), want)

    def test_a_look_outside_a_round_reads_the_game_time(self):
        """No brain round set the clock (the bench's achieve, the CLI): see_sections stamps the tick read now."""
        from bonobo import memory
        m = self.mem()
        m.clock = None
        for name, reader, want in [("the game answers: its tick", lambda: 7777, 7777),
                                   ("must fail: no game to ask: unstamped", None, None)]:
            with self.subTest(name), mock.patch.object(memory, "TICK_READER", reader):
                sec = memory.section_of(self.HERE)
                m.see_sections("minecraft:overworld", memory.section_centre(sec), 0, {}, [self.S])
                row = m.section_map("minecraft:overworld")[sec]
                self.assertEqual(row["looked"]["sheep"], want)

    def test_resume_and_shared(self):
        # A search interrupted after one look resumes elsewhere; another task's look counts for the same kind.
        m = self.mem()
        m.see_sections("minecraft:overworld", self.HERE, 48, {}, [self.S])
        after = [s for s, _c in m.frontier("minecraft:overworld", self.HERE, [self.S])]
        self.assertNotIn((1, 4, 0), after)                        # covered by the first look: not walked again
        self.assertTrue(after)                                    # the search goes on, farther out
        other = [s for s, _c in m.frontier("minecraft:overworld", self.HERE, [self.D])]
        self.assertIn((1, 4, 0), other)                           # a sheep look answers nothing for diamonds

    def test_the_map_is_bounded(self):
        from bonobo import memory
        m = self.mem()
        with mock.patch.object(memory, "SECTION_CAP", 50):
            for x in range(0, 2000, 64):
                m.see_sections("minecraft:overworld", (x, 70, 0), 48, {}, [self.S])
        self.assertLessEqual(len(m.section_map("minecraft:overworld")), 50)

    def test_nothing_left_says_so(self):
        from bonobo import explore
        m = self.mem()
        m.see_sections("minecraft:overworld", self.HERE, 16 * 13, {}, [self.S])
        ctx = type("Ctx", (), {"mem": m, "dimension": "minecraft:overworld", "policy": None})()
        with mock.patch.object(explore, "feet", lambda: self.HERE), \
                mock.patch.object(explore, "_ground", lambda tx, tz, y: None):     # no ground to walk to either
            with self.assertRaises(NotAvailable) as e:
                list(explore._search(ctx, [self.S], lambda: [], 64, 3))
        self.assertIn("searched", str(e.exception))

    def test_a_frontier_out_of_reach_is_not_walked_again(self):
        # (situation, which candidates the walk reaches) → (walks tried, the search's answer)
        from bonobo import explore
        rows = [("the nearest reachable: one walk", {0}, 1, "walked"),
                ("the nearest walled off: the next one", {1}, 2, "walked"),
                ("only the fourth", {3}, 4, "walked"),
                ("must fail: all walled off (a sealed arena) — each tried once, then out of reach", set(), 4,
                 "out of reach")]
        for name, reachable, walks, want in rows:
            with self.subTest(name):
                m = self.mem()
                ctx = type("Ctx", (), {"mem": m, "dimension": "minecraft:overworld", "policy": None})()
                went = []

                def walk(pos, *a, **k):
                    went.append(pos)
                    return (len(went) - 1) in reachable
                with mock.patch.object(explore, "feet", lambda: self.HERE), \
                        mock.patch.object(explore, "_ground", lambda tx, tz, y: 70), \
                        mock.patch.object(explore.nav, "go_to", walk), mock.patch.object(explore, "log"):
                    try:
                        next(explore._search(ctx, [self.S], lambda: [], 48, 3))
                        got = "walked"
                    except NotAvailable as e:
                        got = "out of reach" if "out of reach" in str(e) else str(e)
                self.assertEqual((len(went), got), (walks, want))

    def test_a_cave_below_is_a_frontier_with_ground(self):
        # The band's section has no known surface; the column's ground near the band's height (a cave floor) is
        # walked to — and a candidate with no ground at all is skipped for the next.
        from bonobo import explore
        m = self.mem()
        went = []
        grounds = iter([None, -55])                                   # the first candidate: no ground; the next: a cave
        ctx = type("Ctx", (), {"mem": m, "dimension": "minecraft:overworld", "policy": None})()
        with mock.patch.object(explore, "feet", lambda: self.HERE), \
                mock.patch.object(explore, "_ground", lambda tx, tz, y: next(grounds)), \
                mock.patch.object(explore.nav, "go_to", lambda pos, *a, **k: went.append(pos) or True), \
                mock.patch.object(explore, "log"):
            gen = explore._search(ctx, [self.D], lambda: [], 48, 1)
            next(gen)
        self.assertEqual(went[0][1], -55)

    def test_resume_after_any_interrupt(self):
        """A search left for a faster layer — a fight (tactic), the eat reflex (maintain), the night's way (plan) —
        resumes for the same target from the same map: the same next section, nothing cooled, nothing banned."""
        from bonobo import api, explore, retry
        from bonobo import brain as brainmod
        # (the source, what reaches the search)
        rows = [("tactic: our own fight holds the body", api.FightHolds("nav.go_to: a fight holds the body")),
                ("another commander took the body", api.BodyContested("replaced by a task we did not post")),
                ("maintain: the eat reflex took the body", api.Interrupted("a faster layer took the body")),
                ("plan: the night's way came due", api.CommitmentExpired("the night: a new decision")),
                ("the player took the controls", api.PlayerTookControl("the player moved")),
                ("nightfall on the surface", api.NightFell("night")),
                ("must fail: a real failure (no progress) is cooled, not resumed", api.TaskStuck("no progress"))]
        for name, exc in rows:
            with self.subTest(name):
                m = self.mem()
                ctx = type("Ctx", (), {"mem": m, "dimension": "minecraft:overworld", "policy": None})()
                went = []

                def walk(pos, *a, _e=[exc], **k):
                    went.append(pos)
                    if _e:
                        raise _e.pop()
                    return True
                with mock.patch.object(explore, "feet", lambda: self.HERE), \
                        mock.patch.object(explore, "_ground", lambda tx, tz, y: 70), \
                        mock.patch.object(explore.nav, "go_to", walk), mock.patch.object(explore, "log"):
                    with self.assertRaises(type(exc)):
                        next(explore._search(ctx, [self.S], lambda: [], 48, 3))
                    next(explore._search(ctx, [self.S], lambda: [], 48, 3))      # the resume
                self.assertEqual(went[1], went[0])                                # the same next section
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.retry, b.place, b.mem = retry.Retry(), ("here", False), m
                b.reflexes = type("R", (), {"failed": lambda self, *a: None})()
                with mock.patch.object(api, "post"), mock.patch.object(brainmod, "log"), \
                        mock.patch.object(api, "wait_for_handback", lambda: None), \
                        mock.patch.object(brainmod.time, "sleep", lambda s: None):
                    b.attempt("seek sheep", lambda: (_ for _ in ()).throw(exc), also=("step:seek:sheep",))
                resumed = not name.startswith("must fail")
                self.assertEqual((b.ready("seek sheep"), b.ready("step:seek:sheep"), not b.retry.entries),
                                 (resumed, resumed, resumed))


class Nightfall(unittest.TestCase):
    """perception.nightfall: surface work at dusk or night is asked to stop at its next boundary (the night's way
    first, then the same target); underground, under a roof, by day or in another dimension, nothing."""
    OW = "minecraft:overworld"

    def test_rows(self):
        from bonobo import perception
        # (situation, /state, walled in, in a site) → the request; the same judgement as the night way's
        # (knowledge.sheltered, which reflexes.Maintain.sheltered asks)
        rows = [("surface at dusk, open sky", dict(dimension=self.OW, timeOfDay=13000, skyLight=15), False, False,
                 "night"),
                ("surface at midnight, day 5's clock", dict(dimension=self.OW, timeOfDay=5 * 24000 + 18000, skyLight=12),
                 False, False, "night"),
                ("a cave mouth: dim, not walled in → still the night's", dict(dimension=self.OW, timeOfDay=18000,
                                                                          skyLight=7), False, False, "night"),
                ("underground at night: none", dict(dimension=self.OW, timeOfDay=18000, skyLight=0), False, False, None),
                ("sheltered: walled in under open sky (a pod)", dict(dimension=self.OW, timeOfDay=18000, skyLight=15),
                 True, False, None),
                ("inside our hut's interior: none", dict(dimension=self.OW, timeOfDay=18000, skyLight=10), False, True,
                 None),
                ("the Nether has no night: none", dict(dimension="minecraft:the_nether", timeOfDay=18000, skyLight=15),
                 False, False, None),
                ("must fail: by day on the surface, nothing", dict(dimension=self.OW, timeOfDay=6000, skyLight=15), False,
                 False, None),
                ("must fail: dawn is day again", dict(dimension=self.OW, timeOfDay=23500, skyLight=15), False, False,
                 None)]
        for name, state, walled, in_site, want in rows:
            with self.subTest(name):
                self.assertEqual(perception.nightfall(state, lambda: walled, lambda: in_site), want)

    def test_one_judgement_with_the_night_way(self):
        # the night rows' Maintain.sheltered and nightfall read knowledge.sheltered: over the same readings they agree
        from types import SimpleNamespace
        from bonobo import knowledge, perception, reflexes
        for sky, walled, in_site in [(15, False, False), (7, False, False), (0, False, False), (15, True, False),
                                     (10, False, True)]:
            with self.subTest(sky=sky, walled=walled, in_site=in_site):
                m = reflexes.Maintain.__new__(reflexes.Maintain)
                m.in_site = lambda feet, dim, _v=in_site: _v
                snap = SimpleNamespace(get=lambda k, d=None, _s=sky: _s if k == "skyLight" else d, feet=(0, 64, 0),
                                       dimension=self.OW)
                state = dict(dimension=self.OW, timeOfDay=18000, skyLight=sky)
                self.assertEqual(m.sheltered(snap, lambda: walled),
                                 perception.nightfall(state, lambda: walled, lambda: in_site) is None)
                self.assertEqual(m.sheltered(snap, lambda: walled), knowledge.sheltered(sky, lambda: walled,
                                                                                        lambda: in_site))

    def test_taken_only_between_tasks(self):
        # (situation, pending, soft skill, the night's way running) → raises NightFell at a boundary?
        from bonobo import api
        rows = [("pending: the next boundary stops the work", "night", False, False, True),
                ("nothing pending: goes on", None, False, False, False),
                ("a soft skill (a fight): never cut", "night", True, False, False),
                ("must fail: the shelter being built is never cut by the night it answers", "night", False, True, False)]
        for name, pending, soft, exempt, raises in rows:
            with self.subTest(name), mock.patch.object(api, "AT_BOUNDARY", pending), \
                    mock.patch.object(api, "SOFT", soft), mock.patch.object(api, "BOUNDARY_EXEMPT", lambda: exempt):
                try:
                    api.at_boundary()
                    got = False
                except api.NightFell:
                    got = True
                self.assertEqual(got, raises)
                if raises:
                    self.assertIsNone(api.AT_BOUNDARY)            # taken once


class InterruptSources(unittest.TestCase):
    """Every source that can take a search's body has a declared resume rule (arbiter.RESUME_OF), the list built
    from the code's own tables: a new reflex row, hazard kind or layer without a rule fails here."""

    def sources(self):
        from bonobo import arbiter, hazard, perception, reflexes
        return ([f"row:{n}" for n in reflexes.NAMES] + [f"hazard:{k}" for k in hazard.KINDS]
                + [f"layer:{k}" for k in arbiter.SCALES] + [perception.NIGHTFALL]
                + ["manual", "player", "game lost", "jar reflex", "death", "dimension change", "user cancel", "stuck",
                   "crash"])

    def test_every_source_is_declared(self):
        from bonobo import arbiter
        missing = [s for s in self.sources() if s not in arbiter.RESUME_OF]
        self.assertEqual(missing, [])

    def test_the_rule_per_class(self):
        from bonobo import arbiter
        # (source) → (resumes, what first)
        rows = [("layer:tactic", (True, "fight")), ("row:eat", (True, None)), ("hazard:drowning", (True, None)),
                ("manual", (True, "stand_down")), ("row:empty the bag", (True, "recheck")), ("death", (True, "recover")),
                ("dimension change", (True, "back")), ("row:leave the Nether", (True, "back")),
                ("player", (True, "handback")), ("user cancel", (False, None)), ("stuck", (False, "cool")),  # must fail: a user cancel resumes nothing
                ("crash", (False, "hold")), ("night", (True, "night"))]
        for source, want in rows:
            with self.subTest(source):
                self.assertEqual(arbiter.resume_of(source), want)

    def test_an_undeclared_source_is_refused(self):
        from bonobo import arbiter
        with self.assertRaises(KeyError):
            arbiter.resume_of("row:a reflex nobody declared")

    def test_a_changed_rule_changes_the_outcome(self):
        """The rule is data (arbiter.RESUME_OF) and outcome_of reads it: change a source's rule and what an exception
        means changes with it — a rule ignored is caught. (What brain.attempt then does on the body — the wait, the
        /stop — is the bench's: resume_after_combat.)"""
        from bonobo import api, arbiter
        from bonobo import brain as brainmod
        # (situation, the error, a rule changed {source: rule}) → (outcome, what first)
        rows = [("died: interrupted, the items first", api.Died("x"), {}, ("interrupted", "recover")),
                ("must fail: the death rule made a failure", api.Died("x"), {"death": "cooled"}, ("failed", "cool")),
                ("a real failure: cooled", api.TaskStuck("no progress"), {}, ("failed", "cool")),
                ("must fail: a stuck rule that resumes", api.TaskStuck("no progress"), {"stuck": "same"},
                 ("interrupted", None)),
                ("our fight: waited out", api.FightHolds("x"), {}, ("interrupted", "fight")),
                ("nightfall: interrupted, the night's way first", api.NightFell("night"), {}, ("interrupted", "night")),
                ("must fail: the night rule made a failure", api.NightFell("night"), {"night": "cooled"},
                 ("failed", "cool")),
                ("a bug of ours: held", ValueError("x"), {}, ("failed", "hold"))]
        for name, err, changed, want in rows:
            with self.subTest(name), mock.patch.dict(arbiter.RESUME_OF, changed):
                outcome, source = brainmod.outcome_of(err)
                self.assertEqual((outcome, arbiter.resume_of(source)[1]), want)



class RoundLog(unittest.TestCase):
    """The round's timing line (brain.phase_ms, round_line), the api's task clock, and the summary mc.py rounds prints."""

    def test_round_line(self):
        from bonobo import brain as brainmod
        rows = [("every phase, a gap", 0.0, [("inv", .001), ("snap", .051), ("plan", .251)], .301, 120.4,
                 "round t=301 inv=1 snap=50 plan=200 act=50 gap=120"),
                ("the idle round: nothing posted after an end", 0.0, [("inv", .002)], .010, None,
                 "round t=10 inv=2 act=8 gap=-"),
                ("a phase marked twice adds up", 0.0, [("fast", .010), ("fast", .030)], .030, 0.0,
                 "round t=30 fast=30 act=0 gap=0"),
                ("must fail: a phase not reached is left out, not zero", 0.0, [], .005, None, "round t=5 act=5 gap=-")]
        for name, t0, marks, end, gap, want in rows:
            with self.subTest(name):
                self.assertEqual(brainmod.round_line(brainmod.phase_ms(t0, marks, end), gap), want)

    def test_task_clock(self):
        from bonobo import api
        # (situation, method, path, answer, stamped, ids already seen ended)
        rows = [("a watched task ended", "GET", "/task?id=4&wait=2", {"id": 4, "status": "succeeded"},
                 {"ended"}, set()),
                ("api.run's post, the task over at once", "POST", "/task?wait=0", {"id": 5, "status": "failed"},
                 {"ended", "first_post"}, set()),
                ("a chain's post: queued, none ended yet", "POST", "/task?wait=0",
                 {"tasks": [{"id": 6, "status": "running"}, {"id": 7, "status": "queued"}]}, {"first_post"}, set()),
                ("must fail: still running is no end", "GET", "/task?id=4&wait=2", {"id": 4, "status": "running"},
                 set(), set()),
                ("must fail: a read-back of a task already seen ended", "GET", "/task?id=4", {"id": 4, "status": "succeeded"},
                 set(), {4}),
                ("must fail: a state read is neither", "GET", "/state", {"id": 1, "status": "x"}, set(), set())]
        for name, method, path, out, want, seen in rows:
            with self.subTest(name), mock.patch.dict(api.CLOCK, {"ended": None, "first_post": None, "ended_id": -1}), \
                    mock.patch.object(api, "_ENDED_IDS", set(seen)), \
                    mock.patch.object(api.time, "perf_counter", return_value=7.0):
                api._clock(method, path, out)
                self.assertEqual({k for k in ("ended", "first_post") if api.CLOCK[k] is not None}, want)

    def test_a_chain_stamps_its_last_task_once(self):
        """run_chain: the end is stamped when the watch first sees the last task ended — the read-back of every
        queued task after it does not move it later (that would hide the idle time)."""
        from bonobo import api, arbiter
        clock = iter([float(k) for k in range(1, 100)])
        answers = {"/task?id=9&wait=2": {"id": 9, "status": "succeeded", "type": "wait", "message": ""},
                   "/task?id=8": {"id": 8, "status": "succeeded", "type": "wait", "message": ""},
                   "/task?id=9": {"id": 9, "status": "succeeded", "type": "wait", "message": ""},
                   "/state": {"control": {"task": None}}}

        def call(method, path, body=None, timeout=1200):
            out = answers[path] if method == "GET" else {"tasks": [{"id": 8, "status": "running"},
                                                                   {"id": 9, "status": "queued"}]}
            api._clock(method, path, out)
            return out
        with mock.patch.dict(api.CLOCK, {"ended": None, "first_post": None, "ended_id": -1}), \
                mock.patch.object(api, "_ENDED_IDS", set()), mock.patch.object(api, "api", side_effect=call), \
                mock.patch.object(api.time, "perf_counter", side_effect=lambda: next(clock)), \
                mock.patch.object(api, "DRESS", None), mock.patch.object(api, "LAST_POSTED", None), \
                mock.patch.object(api, "detail"), mock.patch.object(api, "at_boundary", lambda: None), \
                mock.patch.object(api, "_raise_if_released", lambda *a, **k: None), \
                mock.patch.object(arbiter, "BODY", arbiter.Motion()):
            api.run_chain([{"type": "wait", "ticks": 1}, {"type": "wait", "ticks": 1}])
            self.assertEqual((api.CLOCK["first_post"], api.CLOCK["ended"]), (1.0, 2.0))

    def test_summary(self):
        from bonobo.tools import rounds
        lines = ["10:00:00 round t=100 snap=40 act=60 gap=900", "10:00:01 plan for t1: gather",
                 "10:00:02 round t=300 snap=20 act=280 gap=-", "10:00:03 round t=200 snap=30 act=170 gap=50",
                 "10:00:04 round t=50 snap=10 act=40 gap=3000"]
        rows = [("everything", None, 4, {"t": (150.0, 300.0), "snap": (25.0, 40.0), "act": (115.0, 280.0)},
                 [(3000.0, "10:00:04"), (900.0, "10:00:00"), (50.0, "10:00:03")]),
                ("since 10:00:02", "10:00:02", 3, {"t": (200.0, 300.0), "snap": (20.0, 30.0), "act": (170.0, 280.0)},
                 [(3000.0, "10:00:04"), (50.0, "10:00:03")]),
                ("must fail: other lines are not rounds", "10:00:01", 3, None, None),
                ("nothing since", "11:00:00", 0, {}, [])]
        for name, since, n, phases, gaps in rows:
            with self.subTest(name):
                got = rounds.summarize(lines, since)
                self.assertEqual(got["rounds"], n)
                if phases is not None:
                    self.assertEqual((got["phases"], got["gaps"]), (phases, gaps))


class IdleWait(unittest.TestCase):
    """brain.idle_wait: the round that closed the last task waits not at all; otherwise 1 s slices until work is queued."""

    def test_over_the_table(self):
        from bonobo import api
        from bonobo import brain as brainmod
        full = brainmod.IDLE_WAIT_TICKS // brainmod.IDLE_SLICE_TICKS
        rows = [("the round that closed the last task: no wait", True, [], 0),
                ("nothing queued: the whole wait, in slices", False, [False] * full, full),
                ("work queued after the second slice: ends there", False, [False, True], 2),
                ("must fail: queued at once still waits one slice, never the whole 5 s", False, [True], 1)]
        for name, finished, queued, want in rows:
            posted = []
            b = brainmod.Brain.__new__(brainmod.Brain)
            b.just_finished = finished
            answers = iter(queued)
            with self.subTest(name), mock.patch.object(api, "run", side_effect=lambda t, **k: posted.append(t)):
                self.assertEqual(b.idle_wait(lambda: next(answers)), want)
                self.assertEqual(len(posted), want)
                self.assertFalse(b.just_finished)


class SkillLine(unittest.TestCase):
    def test_skill_line(self):
        from bonobo import skill as sk
        rows = [("a generator skill", {"pre": 40.4, "body": 900.2, "checks": 310.0, "verify": 120.0, "n": 4},
                 "skill mine pre=40 body=900 checks=310 verify=120 n=4"),
                ("a plain function: no checks", {"pre": 5.0, "body": 12.0, "checks": 0.0, "verify": 1.0, "n": 0},
                 "skill eat pre=5 body=12 checks=0 verify=1 n=0"),
                ("must fail: a phase not reached is left out", {"pre": 5.0, "body": 1.0, "checks": 0.0, "n": 1},
                 "skill eat pre=5 body=1 checks=0 n=1"),
                ("no count: n=0", {"pre": 1.0}, "skill eat pre=1 n=0")]
        for name, t, want in rows:
            with self.subTest(name):
                self.assertEqual(sk.skill_line(want.split()[1], t), want)


class Pits(unittest.TestCase):
    """nav.in_pit / pit_exit_tasks / stair_down_tasks: a body never ends in a hole it cannot leave."""

    @staticmethod
    def ground(depth, feet=(0, 64, 0), half=3):
        """Stone to y 64+1 all round, a 1-wide hole `depth` deep at `feet` (the feet at its bottom)."""
        from tests.world import FakeRegion
        x, y, z = feet
        top = y + depth - 1                       # the ground's top block level beside the hole
        blocks = {(i, j, k): "stone" for i in range(x - half, x + half + 1) for k in range(z - half, z + half + 1)
                  for j in range(y - 4, top + 1) if not (i == x and k == z and j >= y)}
        return FakeRegion((x - half, y - 5, z - half), (x + half, y + 6, z + half), blocks)

    @classmethod
    def bedrock(cls, depth):
        from tests.world import FakeRegion
        stone = cls.ground(depth)
        return FakeRegion(stone.lo, stone.hi, {c: "bedrock" for c in stone.blocks})

    def test_in_pit(self):
        from bonobo import nav
        from tests.world import FakeRegion
        rows = [("a 1-wide shaft 3 deep: a pit", self.ground(3), True),
                ("2 deep: the head-height sides solid — a pit", self.ground(2), True),
                ("must fail: a 1-deep dip — a jump clears it", self.ground(1), False),
                ("must fail: flat ground", self.ground(0), False),
                ("must fail: the sides not read", FakeRegion((0, 64, 0), (0, 64, 0), {}), False)]
        for name, region, want in rows:
            with self.subTest(name):
                self.assertIs(nav.in_pit(region, (0, 64, 0)), want)

    def test_pit_exit(self):
        from bonobo import nav
        rows = [("a block carried, the shaft open above: pillar", self.ground(3), "minecraft:cobblestone", "pillar"),
                ("no block: a step dug into a side, then walked onto", self.ground(3), None, "goto"),
                ("must fail: nothing to stand on beside (air all round): no way", self.ground(0), None, None),
                ("must fail: bedrock all round, no block: no way", self.bedrock(3), None, None)]
        for name, region, block, want in rows:
            with self.subTest(name):
                tasks = nav.pit_exit_tasks(region, (0, 64 if want else 65, 0), block)
                self.assertEqual(tasks[-1]["type"] if tasks else None, want)

    def test_stairs_not_a_shaft(self):
        from bonobo import nav
        region = self.ground(0)
        rows = [("5 below and 3 east: steps east, one down each", (3, 59, 0), (1, 0)),
                ("straight below: steps along x, never the own column", (0, 60, 0), (1, 0)),
                ("must fail: level with the feet: no stairs", (4, 64, 0), None),
                ("one below: a step, not stairs", (2, 63, 0), None)]
        for name, target, d in rows:
            with self.subTest(name):
                tasks = nav.stair_down_tasks(region, (0, 64, 0), target)
                gotos = [(t["x"], t["y"], t["z"]) for t in tasks if t["type"] == "goto"]
                if d is None:
                    self.assertEqual(tasks, [])
                else:
                    self.assertTrue(gotos)
                    self.assertTrue(all(g[1] == 64 - k and (g[0], g[2]) == (d[0] * k, d[1] * k)
                                        for k, g in enumerate(gotos, 1)))

    def test_stairs_stop_before_danger(self):
        from bonobo import nav
        from tests.world import FakeRegion
        flat = self.ground(0)
        lava = FakeRegion(flat.lo, flat.hi, {**flat.blocks, (1, 63, 0): "lava"})
        rows = [("must fail: lava in the first step", lava, (3, 59, 0), ()),
                ("must fail: the first step protected", flat, (3, 59, 0), {(1, 63, 0)}),
                ("must fail: the target above", flat, (2, 70, 0), ()),
                ("must fail: bedrock under the steps", self.bedrock(0), (3, 59, 0), ())]
        for name, region, target, protected in rows:
            with self.subTest(name):
                self.assertEqual(nav.stair_down_tasks(region, (0, 64, 0), target, protected), [])
