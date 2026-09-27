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
