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
        from bonobo import end, world
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
        region = _Props(blocks, props)
        self.assertEqual((world.ripe_cells(region), sorted(end.frames_missing_eye(region))),
                         ([(0, 64, 0)], [(3, 64, 0), (4, 64, 0)]))


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


class AwayFrom(unittest.TestCase):
    """world.away_from: the one "straight away from a point" (end.breath_escape reads it; its old answers pinned)."""

    def test_over_the_table(self):
        from bonobo import world
        rows = [("east of it: further east", (10, 64, 0), (0, 64, 0), 10, (20.0, 64, 0.0)),
                ("on a diagonal: along it", (3, 70, 4), (0, 0, 0), 10, (9.0, 70, 12.0)),
                ("y is kept, never climbed", (0, 5, 3), (0, 99, 0), 2, (0.0, 5, 5.0)),
                ("must fail: on the point itself, nowhere to go", (0, 64, 0), (0, 64, 0), 10, (0.0, 64, 0.0))]
        for name, here, point, blocks, want in rows:
            with self.subTest(name):
                self.assertEqual(world.away_from(here, point, blocks), want)

    def test_breath_escape_answers_as_before(self):
        from bonobo import end
        cloud = lambda x, z: {"type": "minecraft:area_effect_cloud", "x": x, "y": 64, "z": z}   # noqa: E731
        # (here, what is near, run) → the spot the old inline math gave
        rows = [((10, 64, 0), [cloud(0, 0)], 10, (20, 64, 0)), ((3, 70, 4), [cloud(0, 0)], 10, (9, 70, 12)),
                ((5, 64, 5), [cloud(0, 0), cloud(4, 4)], 7, (10, 64, 10)),
                ((-7, 60, 3), [cloud(2.5, -1.5)], 12, (-18, 60, 8)),
                ((0, 64, 0), [cloud(0, 0)], 10, (0, 64, 0)),
                ((1, 64, -2), [{"type": "minecraft:zombie", "x": 0, "y": 0, "z": 0}], 10, None)]
        for here, near, run, want in rows:
            with self.subTest(here=here):
                self.assertEqual(end.breath_escape(here, near, run=run), want)


class NearestSoft(unittest.TestCase):
    """terrain.nearest_soft: ground that digs by hand, found along the ground we stand on."""
    # (situation, region, feet) → (cell, steps) or None
    ROWS = [("soft right under the feet: dig here", soft_ground(patch=(-1, 1)), (0, 64, 0), ((0, 64, 0), 0)),
            ("dirt 8 blocks along the walkway: walk there", soft_ground(patch=(8, 9)), (0, 64, 0), ((8, 64, 0), 8)),
            ("only stone: none", soft_ground(), (0, 64, 0), None),
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
            ("flowing water, level 3: not a source", region({p: "water"}, {p: {"level": "3"}}), "water", False),
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
            ("stone in the path", {(1, 1, 0): "stone"}, 0.2, False),
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
            ("no lava anywhere", None, 1, False),
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
        rows = [  # (item, aim, on_block, expected)
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
        rows = [  # (cell, expected)
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
            ("nothing set: no saves dir", None, None, "/h", None),
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
            ("empty MC_DATA is no override", "", "/x", "/h", "/x/bonobo"),
            ("nothing set: ~/.local/share", None, None, "/h", "/h/.local/share/bonobo"),
        ]
        for why, mc, xdg, home, want in rows:
            with self.subTest(why), env(MC_DATA=mc, XDG_DATA_HOME=xdg, HOME=home):
                self.assertEqual(paths.data_dir(), want)

    def test_instance_dir(self):
        rows = [  # (why, MC_INSTANCE, HOME, expected)
            ("set", "/i", "/h", "/i"),
            ("set with ~", "~/i", "/h", "/h/i"),
            ("empty", "", "/h", ""),
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
            ("empty is taken as given, not defaulted", "", ""),
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
            ("no actions", _Model([]), {}, [], []),
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
            ("stone", False),          # mined, not taken back
            ("minecraft:crafting_table", False),   # bare block names only
        ]
        for block, want in rows:
            with self.subTest(block):
                self.assertEqual(block in got, want)
        with self.subTest("sorted, each block once"):
            self.assertEqual(got, sorted(set(got)))


# ---------------------------------------------------------------- nav

class Nav(unittest.TestCase):
    def test_safe_destination(self):
        pos = (0.0, 64.0, 0.0)
        rows = [  # (why, hazards, expected)
            ("no hazards (None)", None, pos),
            ("no hazards (empty)", [], pos),
            ("far static hazard, two-element form", [((20.0, 64.0, 0.0), 1.0)], pos),
            ("far static hazard, three-element form", [((0.0, 64.0, 20.0), 2.0, (0.0, 0.0, 0.0))], pos),
            ("engulfed by a huge hazard: nowhere clear", [(pos, 1000.0)], None),
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
            ("a one-block pillar: nowhere to go (None: fight or wall in)", floor(range(0, 2), 199), None),
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
            ("a tree 100 below the platform, 20 across: no", (20, 100, 0), feet, False),
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
            ("already standing on it: nothing", {"x": 10.5, "y": 200.0, "z": 10.5, "onGround": True,
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
            ("the probe, the cell itself asked: not there", 10006.2355, 0, False),
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
                ("past the radius: None", True, [["iron_ore", 5]], [None], 1)]
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
            ("no reading at all", {}, False),
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
            ("on the bank cell but the feet still in water", {"x": 10.5, "y": 200.0, "z": 10.5, "onGround": True,
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
            ("one block gained: not enough", (1, 64, 0), False),
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
            return {"type": "mine", "x": 0, "y": y, "z": 0, "collect": False, "requireDrops": False}
        wait = {"type": "wait", "ticks": 6}
        ladder = {"type": "place", "item": "minecraft:ladder", "x": 0, "y": 65, "z": 0,
                  "against": {"x": 1, "y": 65, "z": 0}}
        rows = [  # (why, extra blocks, depth, protected, ladders, expected (tasks, safe))
            ("two blocks of stone", {}, 2, (), False, ([mine(63), wait, mine(62), wait], 2)),
            ("stops above the region's floor (air below)", {}, 5, (), False,
             ([mine(63), wait, mine(62), wait, mine(61), wait], 3)),
            ("stops beside water", {(1, 62, 0): "water"}, 3, (), False, ([mine(63), wait], 1)),
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
            ("lava right under the feet", {(0, 63, 0): "lava"}, ()),
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
            ("first reading: nothing to difference", [(20, 0.0)], 0.0),
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
            ("mark at the end: nothing new", 3, []),
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
            ("no sword: fist", [], 0),
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
            ("a cow does not", ["minecraft:cow"], False),
            ("mixed hunt: one fighter is enough", ["minecraft:cow", "minecraft:skeleton"], True),
            ("nothing hunted", [], False),
            ("bare id is not the table's key", ["zombie"], False),
        ]
        for why, types, want in rows:
            with self.subTest(why):
                self.assertEqual(planner.hunts_a_fighter(types), want)

    def test_tool_ok(self):
        rows = [  # (why, inv, kind, tier, min_left, expected)
            ("no tools() at all", object(), "pickaxe", 1, 10, False),
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
                self.assertEqual(planner.tool_ok(inv, kind, tier, min_left), want)
        with self.subTest("default min_left is 10"):
            self.assertEqual(planner.tool_ok(_Bag({"pickaxe": [(2, 9, None)]}), "pickaxe", 2), False)

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
            ("nothing carried", {}, [], "minecraft:diamond", 0),
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
            ("tier too high", "pickaxe", 3, 10, False),
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
            ("a@b", 3, "a@b@3"),
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
        rows = [  # (why, fn, budget, per_unit, verify, expected per_unit, expected verify, expected doc)
            ("per_unit defaults to budget / 3", documented, 30, None, None, 10.0, done, "First line."),
            ("per_unit given", documented, 30, 5, None, 5, done, "First line."),
            ("zero budget", bare, 0, None, None, 0.0, done, ""),
            ("verify given is kept", bare, 90, None, verify, 30.0, verify, ""),
        ]
        for why, fn, budget, per_unit, ver, want_pu, want_ver, want_doc in rows:
            with self.subTest(why):
                c = skillkit.Contract("x", fn, None, None, done, ver, budget, 10, per_unit, None, None)
                self.assertEqual(c.per_unit, want_pu)
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

    def test_ttl_by_class(self):
        from bonobo import memory
        row = {"t": 0, "kinds": {}, "looked": {"diamond_ore": 0, "sheep": 0}}
        # (kind, ticks since the look) → still covered
        rows = [("an ore looked for a day ago: still no ore", self.D, 24000, True),
                ("an ore looked for three days ago: look again", self.D, 3 * 24000, False),
                ("sheep looked for a minute ago: none", self.S, 1200, True),
                ("sheep looked for five minutes ago: they may have walked in", self.S, 6000, False),
                ("never looked for: not covered", "iron_ore", 10, False)]
        for name, kind, age, want in rows:
            with self.subTest(name):
                self.assertEqual(memory.covered(row, [kind], age), want)

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
                mock.patch.object(explore.nav, "go_to", lambda pos, *a, **k: went.append(pos)), \
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
                ("the player took the controls", api.PlayerTookControl("the player moved"))]
        for name, exc in rows:
            with self.subTest(name):
                m = self.mem()
                ctx = type("Ctx", (), {"mem": m, "dimension": "minecraft:overworld", "policy": None})()
                went = []

                def walk(pos, *a, _e=[exc], **k):
                    went.append(pos)
                    if _e:
                        raise _e.pop()
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
                self.assertEqual((b.ready("seek sheep"), b.ready("step:seek:sheep"), b.retry.entries), (True, True, {}))


class InterruptSources(unittest.TestCase):
    """Every source that can take a search's body has a declared resume rule (arbiter.RESUME_OF), the list built
    from the code's own tables: a new reflex row, hazard kind or layer without a rule fails here."""

    def sources(self):
        from bonobo import arbiter, hazard, reflexes
        return ([f"row:{n}" for n in reflexes.NAMES] + [f"hazard:{k}" for k in hazard.KINDS]
                + [f"layer:{k}" for k in arbiter.SCALES]
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
                ("user cancel", (False, None)), ("stuck", (False, "cool"))]
        for source, want in rows:
            with self.subTest(source):
                self.assertEqual(arbiter.resume_of(source), want)

    def test_an_undeclared_source_is_refused(self):
        from bonobo import arbiter
        with self.assertRaises(KeyError):
            arbiter.resume_of("row:a reflex nobody declared")

    def test_every_interruption_has_its_resume(self):
        """Generated over api.INTERRUPTIONS: each class stands for one interrupt source (brain.outcome_of) and what the
        attempt does about it is that source's rule, read from arbiter.RESUME_OF itself: a death recovers first, a
        dimension change resumes back there, our fight waits it out. A class with no row fails; a real failure and a
        bug of ours are the contrast."""
        from bonobo import api, arbiter
        from bonobo import brain as brainmod
        # (class, the source it stands for, outcome, the rule's (resumes, what first))
        rows = [(api.Interrupted, "layer:safety", "interrupted", (True, None)),
                (api.CommitmentExpired, "layer:plan", "interrupted", (True, None)),
                (api.BodyContested, "manual", "interrupted", (True, "stand_down")),
                (api.FightHolds, "layer:tactic", "interrupted", (True, "fight")),
                (api.PlayerTookControl, "player", "interrupted", (True, "handback")),
                (api.Died, "death", "interrupted", (True, "recover")),
                (api.DimensionChanged, "dimension change", "interrupted", (True, "back")),
                (api.TaskStuck, "stuck", "failed", (False, "cool")),
                (ValueError, "crash", "failed", (False, "hold"))]
        self.assertEqual(set(api.INTERRUPTIONS) - {r[0] for r in rows}, set(), "an interruption without a row")
        for cls, source, outcome, rule in rows:
            with self.subTest(cls.__name__):
                err = cls() if cls is api.PlayerTookControl else cls("x")
                self.assertEqual(brainmod.outcome_of(err), (outcome, source))
                self.assertEqual(arbiter.resume_of(source), rule)

    def test_the_attempt_does_what_the_rule_says(self):
        """brain.attempt applies the source's rule (arbiter.RESUME_OF), nowhere else: change the rule and what the
        attempt does changes with it — a rule ignored is caught."""
        import tempfile
        from bonobo import api, arbiter, retry
        from bonobo import brain as brainmod
        from bonobo.memory import Memory
        # (situation, the error, a rule changed {source: rule}, outcome, recorded as a failure, waited out a fight)
        rows = [("died: interrupted, not counted", api.Died("x"), {}, "interrupted", False, False),
                ("must fail: the death rule made a failure — counted", api.Died("x"), {"death": "cooled"}, "failed",
                 True, False),
                ("a real failure: counted", api.TaskStuck("no progress"), {}, "failed", True, False),
                ("must fail: a stuck rule that resumes — not counted", api.TaskStuck("no progress"),
                 {"stuck": "same"}, "interrupted", False, False),
                ("our fight: waited out", api.FightHolds("x"), {}, "interrupted", False, True),
                ("must fail: the fight rule made plain — not waited", api.FightHolds("x"), {"layer:tactic": "same"},
                 "interrupted", False, False)]
        for name, err, changed, outcome, counted, waited in rows:
            with self.subTest(name):
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.retry, b.place = retry.Retry(), ("here", False)
                b.mem = Memory(os.path.join(tempfile.mkdtemp(prefix="attempt"), "notes.json"))
                b.reflexes = type("R", (), {"failed": lambda self, *a: None})()
                fights, outcomes = [], []
                b.mem.record_outcome = lambda name, ok: outcomes.append(ok)
                with mock.patch.dict(arbiter.RESUME_OF, changed), mock.patch.object(api, "post"), \
                        mock.patch.object(brainmod, "log"), mock.patch.object(brainmod.time, "sleep", lambda s: None), \
                        mock.patch.object(brainmod, "wait_out_fight", lambda: fights.append(1)):
                    got = b.attempt("task t1", lambda: (_ for _ in ()).throw(err))
                self.assertEqual((got, False in outcomes, bool(fights)), (outcome, counted, waited))

