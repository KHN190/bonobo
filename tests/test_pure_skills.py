"""Pure-function tables for skills / solve / tape / threat / world: one table per function, every row a subTest.

Each table holds a normal row, a boundary row and a must-fail row (its reason in the row's name). Inputs are readings
(tests/world.py) or hand-built rows through the modules' own constructors; nothing here talks to the game.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import skills, solve, tape, threat  # noqa: E402
from bonobo.api import McError, NotAvailable  # noqa: E402
from bonobo.bag import pickup_whitelist  # noqa: E402
from bonobo.world import connected  # noqa: E402
from tests.world import FakeRegion, bag, inventory, state  # noqa: E402

def run_table(t, fn, table):
    """table: [(why, args, expected)]; expected is a value, or an exception class."""
    for why, args, expected in table:
        with t.subTest(why):
            if isinstance(expected, type) and issubclass(expected, BaseException):
                with t.assertRaises(expected):
                    fn(*args)
            else:
                t.assertEqual(fn(*args), expected)


# ---------------------------------------------------------------- skills

BOAT = ["planks", None, "planks", "planks", "planks", "planks", None, None, None]   # 5 planks cells


class PickTrunks(unittest.TestCase):
    L = staticmethod(lambda x, y, z: {"x": x, "y": y, "z": z})
    # (situation, logs as /find lists them) → trunks as lists of (x, y, z), in listed order
    ROWS = [("one trunk, three logs", [(0, 64, 0), (0, 65, 0), (0, 66, 0)], [[(0, 64, 0), (0, 65, 0), (0, 66, 0)]]),
            ("two trees apart: two trunks, nearest listed first", [(5, 64, 0), (0, 64, 0), (5, 65, 0)],
             [[(5, 64, 0), (5, 65, 0)], [(0, 64, 0)]]),
            ("a branch one block off joins its trunk", [(0, 64, 0), (1, 66, 1)], [[(0, 64, 0), (1, 66, 1)]]),
            ("two blocks off is another trunk", [(0, 64, 0), (2, 64, 0)], [[(0, 64, 0)], [(2, 64, 0)]]),
            ("nothing seen: no trunks", [], [])]

    def test_trunks(self):
        from bonobo import wood
        for name, logs, want in self.ROWS:
            with self.subTest(name):
                got = wood.pick_trunks([self.L(*c) for c in logs])
                self.assertEqual([[(t["x"], t["y"], t["z"]) for t in tr] for tr in got], want)


class BitesToFull(unittest.TestCase):
    B, BEEF, APPLE, CARROT, RAW = ("minecraft:bread", "minecraft:cooked_beef", "minecraft:apple", "minecraft:carrot",
                                   "minecraft:beef")
    # (situation, food points, carried, raw ok) → (item, bites)
    ROWS = [("two haunches short, bread: one bite", 16, {B: 3}, False, (B, 1)),
            ("full: nothing", 20, {B: 3}, False, (None, 0)),
            ("only raw meat, not starving: nothing", 10, {RAW: 5}, False, (None, 0)),
            ("only raw meat, starving: raw, bites to full", 0, {RAW: 5}, True, (RAW, 7)),
            ("several: the one that fills the gap exactly", 12, {B: 1, BEEF: 1, APPLE: 1}, False, (BEEF, 1)),
            ("a small gap: the biggest that does not overflow", 17, {APPLE: 1, CARROT: 1}, False, (CARROT, 1)),
            ("every item overflows: the smallest", 19, {BEEF: 1, B: 1}, False, (B, 1)),
            ("none carried (count 0): nothing", 5, {B: 0}, True, (None, 0))]

    def test_bites(self):
        for name, food, carried, raw_ok, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(skills.bites_to_full(food, carried, raw_ok), want)


class ResolvePattern(unittest.TestCase):
    TABLE = [
        ("one log for planks", (["log", None, None, None], 1, bag(inventory(oak_log=1))),
         ["minecraft:oak_log", None, None, None]),
        ("richest member picked when both suffice", (["log", None, None, None], 2,
                                                     bag(inventory(oak_log=2, birch_log=3))),
         ["minecraft:birch_log", None, None, None]),
        ("boundary: exactly five planks for a boat", (BOAT, 1, bag(inventory(spruce_planks=5))),
         ["minecraft:spruce_planks", None, "minecraft:spruce_planks", "minecraft:spruce_planks",
          "minecraft:spruce_planks", "minecraft:spruce_planks", None, None, None]),
        ("plain item token passes through as its id", (["cobblestone", None], 1, bag(inventory(cobblestone=1))),
         ["minecraft:cobblestone", None]),
        ("must fail: four planks for five cells", (BOAT, 1, bag(inventory(spruce_planks=4))), McError),
        ("must fail: members are not mixed (3 oak + 3 birch for 5)",
         (BOAT, 1, bag(inventory(oak_planks=3, birch_planks=3))), McError),
        ("must fail: times multiplies the need (1 log, times 2)", (["log", None, None, None], 2,
                                                                   bag(inventory(oak_log=1))), McError),
    ]

    def test_table(self):
        run_table(self, skills.resolve_pattern, self.TABLE)


class OutputOf(unittest.TestCase):
    TABLE = [
        ("non-group token: its own id", ("stick", ["minecraft:oak_planks"]), "minecraft:stick"),
        ("planks follow the log", ("planks", ["minecraft:birch_log", None, None, None]), "minecraft:birch_planks"),
        ("stem planks", ("planks", ["minecraft:crimson_stem", None, None, None]), "minecraft:crimson_planks"),
        ("bed follows the wool", ("bed", ["minecraft:red_wool", "minecraft:red_wool"]), "minecraft:red_bed"),
        ("boundary: boat takes the first non-empty cell", ("boat", [None, "minecraft:spruce_planks"]),
         "minecraft:spruce_boat"),
        ("door", ("door", ["minecraft:acacia_planks"]), "minecraft:acacia_door"),
        ("must fail: planks from a non-log", ("planks", ["minecraft:stone", None, None, None]), KeyError),
    ]

    def test_table(self):
        run_table(self, skills.output_of, self.TABLE)


def slots(n, item="dirt"):
    return bag(inventory(*[(item, 1)] * n))


def _hit(x, y, z):
    return {"x": x, "y": y, "z": z, "id": "minecraft:iron_ore"}


class SeekHits(unittest.TestCase):
    """Mining seeks sealed ore as readily as exposed: seeing through blocks is allowed, the approach digs to it."""
    SEALED, OPEN, OURS = _hit(10002, 200, 10000), _hit(10003, 201, 10000), _hit(10004, 200, 10000)
    # (situation, (found, radius, banned, protected), hits | None (widen) | the NotAvailable message)
    TABLE = [
        ("sealed ore in stone beside us: found", ([SEALED], 24, set(), set()), [SEALED]),
        ("exposed and sealed alike, in /find's order", ([OPEN, SEALED], 24, set(), set()), [OPEN, SEALED]),
        ("edge: nothing near yet: widen", ([], 24, set(), set()), None),
        ("must fail: nothing at the widest radius", ([], 48, set(), set()), "no iron_ore within 48 blocks"),
        ("must fail: excluded by bans and our builds, and said so",
         ([SEALED, OURS], 48, {(10002, 200, 10000)}, {(10004, 200, 10000)}),
         "no iron_ore within 48 blocks: 2 in range but 1 banned, 1 protected"),
        ("a ban leaves the rest", ([SEALED, OPEN], 48, {(10002, 200, 10000)}, set()), [OPEN]),
    ]

    def test_table(self):
        for why, (found, radius, banned, protected), want in self.TABLE:
            with self.subTest(why):
                args = (["iron_ore"], found, radius, banned.__contains__, protected)
                if isinstance(want, str):
                    with self.assertRaises(skills.NotAvailable) as got:
                        skills.seek_hits(*args)
                    self.assertEqual(str(got.exception), want)
                else:
                    self.assertEqual(skills.seek_hits(*args), want)

    # (situation, find kwargs) → the query's tail after the limit: exposure is never sent as false
    QUERY = [
        ("default: sealed blocks seen", {}, ""),
        ("explicitly not exposed: the same query", {"exposed": False}, ""),
        ("exposed asked for", {"exposed": True}, "&exposed=true"),
        ("must fail if false is sent: a jar reading the key's presence goes strict", {"exposed": 0}, ""),
    ]

    def test_find_query(self):
        from bonobo import world
        for why, kw, tail in self.QUERY:
            with self.subTest(why), mock.patch.object(world.api, "get", return_value={"blocks": []}) as get:
                world.find(["iron_ore"], radius=48, limit=60, **kw)
                self.assertEqual(get.call_args[0][0], "/find?blocks=minecraft:iron_ore&radius=48&limit=60" + tail)


class Returns(unittest.TestCase):
    GO, MINE = ("t", "going to mine stone at (9998, 199, 10000)", 9999, 199, 10000), \
        ("t", "mining stone at (9998, 199, 10000)", 9999, 199, 10000)
    # (situation, observed states in order) → returns to a state already left
    TABLE = [
        ("walking on: every state new", ([("t", "walk", 0, 64, i) for i in range(6)],), 0),
        ("mine_stone's circle: approach ↔ mine five times", ([GO, MINE] * 5,), 8),
        ("edge: one step back is one return", ([GO, MINE, GO],), 1),
        ("a batch that advances: the report changes", ([("t", f"mine_many {i}/5", 0, 64, 0) for i in range(5)],), 0),
        ("nothing seen", ([],), 0),
    ]

    def test_table(self):
        from bonobo import api
        run_table(self, api.returns, self.TABLE)

    def test_the_circle_is_stopped(self):
        from bonobo import api
        stop = lambda seen: api.returns(seen) >= api.OSCILLATION_RETURNS   # noqa: E731
        self.assertEqual((stop([self.GO, self.MINE] * 3), stop([self.GO, self.MINE, self.GO])), (True, False))


class NotedHits(unittest.TestCase):
    """skills.noted_hits: a remembered ore is gone to straight, no /find (seen_store__noted scanned every pass)."""
    TABLE = [
        ("a noted diamond: that cell", ([{"kind": "diamond_ore", "pos": [4, 60, 0]}], ["diamond_ore"], set(), set()),
         [(4, 60, 0)]),
        ("a note of another block: nothing (then /find)", ([{"kind": "iron_ore", "pos": [4, 60, 0]}], ["diamond_ore"],
                                                           set(), set()), []),
        ("the noted cell banned: nothing", ([{"kind": "diamond_ore", "pos": [4, 60, 0]}], ["diamond_ore"],
                                            {(4, 60, 0)}, set()), []),
        ("the noted cell ours: nothing", ([{"kind": "diamond_ore", "pos": [4, 60, 0]}], ["diamond_ore"], set(),
                                          {(4, 60, 0)}), []),
        ("namespaced kinds match bare", ([{"kind": "minecraft:deepslate_diamond_ore", "pos": [1, 2, 3]}],
                                         ["deepslate_diamond_ore"], set(), set()), [(1, 2, 3)]),
    ]

    def test_table(self):
        for why, (notes, blocks, banned, protected), want in self.TABLE:
            with self.subTest(why):
                got = skills.noted_hits(notes, blocks, lambda p: p in banned, protected)
                self.assertEqual([(h["x"], h["y"], h["z"]) for h in got], want)


class SealPlan(unittest.TestCase):
    """skills.seal_plan: before breaking a cell, a block into every fluid cell touching it face to face."""

    def test_table(self):
        from tests.world import FakeRegion, bag, inventory
        cell = (0, 64, 0)
        lo, hi = (-3, 60, -3), (3, 68, 3)
        stone = {(x, y, z): "stone" for x in range(-3, 4) for y in range(60, 69) for z in range(-3, 4)}
        cobble, dirt, none = inventory(("cobblestone", 8)), inventory(("dirt", 1)), inventory()
        rows = [  # (why, fluid cells, bag) → cells placed into (in face order), or the NotAvailable
            ("lava below, cobblestone: sealed", {(0, 63, 0): "lava"}, cobble, [(0, 63, 0)]),
            ("water beside, dirt: sealed", {(1, 64, 0): "water"}, dirt, [(1, 64, 0)]),
            ("lava above, cobblestone: sealed", {(0, 65, 0): "lava"}, cobble, [(0, 65, 0)]),
            ("water below and lava beside: both, below first", {(0, 63, 0): "water", (0, 64, 1): "lava"}, cobble,
             [(0, 63, 0), (0, 64, 1)]),
            ("water two away (not a face): nothing", {(2, 64, 0): "water"}, cobble, []),
            ("dry all round: nothing, even with an empty bag", {}, none, []),
            ("must fail: lava below, nothing to seal with", {(0, 63, 0): "lava"}, none, NotAvailable),
            ("must fail: two faces, one block", {(0, 63, 0): "lava", (1, 64, 0): "water"}, dirt, NotAvailable),
        ]
        for why, fluids, inv, want in rows:
            with self.subTest(why):
                region = FakeRegion(lo, hi, {**stone, cell: "iron_ore", **fluids})
                if want is NotAvailable:
                    with self.assertRaises(NotAvailable):
                        skills.seal_plan(region, [cell], bag(inv))
                    continue
                got = skills.seal_plan(region, [cell], bag(inv))
                self.assertEqual([(t["x"], t["y"], t["z"]) for t in got], want)
                self.assertTrue(all(t["type"] == "place" for t in got))


class OnlyIsItemIds(unittest.TestCase):
    """data.item_ids / api.with_item_ids: every "only" the jar gets is exact item ids (it matched "log" to nothing)."""

    def test_table(self):
        from bonobo import api, data
        logs = [data.mid(m) for m in data.GROUPS["log"]]
        rows = [("a group: its members' ids", ["log"], logs),
                ("an id: itself", ["minecraft:blaze_rod"], ["minecraft:blaze_rod"]),
                ("both, no repeats", ["minecraft:oak_log", "log"], ["minecraft:oak_log"] + [i for i in logs
                                                                                           if i != "minecraft:oak_log"]),
                ("must fail: a bare name that is no group", ["zzz"], ValueError)]
        for name, tokens, want in rows:
            with self.subTest(name):
                if want is ValueError:
                    with self.assertRaises(ValueError):
                        data.item_ids(tokens)
                else:
                    self.assertEqual(data.item_ids(tokens), want)
        batch = {"tasks": [{"type": "mine_many", "blocks": []}, {"type": "collect", "radius": 4, "only": ["log"]}]}
        self.assertEqual(api.with_item_ids(batch)["tasks"][1]["only"], logs)
        self.assertNotIn("only", api.with_item_ids(batch)["tasks"][0])


class TakeBackVerdict(unittest.TestCase):
    """skills.take_back_verdict: a station not picked up but still standing is left (a station there), not lost."""
    # (situation, (bag gained it, still standing)) → verdict
    TABLE = [
        ("picked up: taken", (True, False), "taken"),
        ("in the bag though one still shows there (another table): taken", (True, True), "taken"),
        ("the break did not happen: left standing, a station", (False, True), "left"),
        ("must fail as a loss: gone and not in the bag", (False, False), "lost"),
    ]

    def test_table(self):
        run_table(self, skills.take_back_verdict, self.TABLE)


class TakesBack(unittest.TestCase):
    # (situation, block, pickaxe held) → break it to carry on
    TABLE = [
        ("a crafting table, by hand", ("minecraft:crafting_table", False), True),
        ("a furnace with a pickaxe", ("minecraft:furnace", True), True),
        ("a chest, by hand", ("chest", False), True),
        ("must fail: a furnace by hand (17 s, no drop: smelt__base's timeout)", ("minecraft:furnace", False), False),
        ("must fail: a blast furnace by hand", ("minecraft:blast_furnace", False), False),
    ]

    def test_table(self):
        run_table(self, skills.takes_back, self.TABLE)


class CraftPlan(unittest.TestCase):
    """One sitting planned whole from the bag: single or chained, 2×2 or 3×3, the net delta verify checks."""
    P, S = "minecraft:oak_planks", "minecraft:stick"
    PICK = [P, P, P, None, S, None, None, S, None]
    # (situation, recipes, bag) → (patterns and counts, needs a table, net delta) | the McError message
    TABLE = [
        ("single 3×3 recipe: the bench's wooden pickaxe", [("minecraft:wooden_pickaxe", 1)],
         {"oak_planks": 8, "stick": 4, "crafting_table": 1},
         ([(PICK, "minecraft:wooden_pickaxe", 1)], True, {P: -3, S: -2, "minecraft:wooden_pickaxe": 1})),
        ("2-step chain, 2×2 then 3×3: planks made are the pickaxe's planks", [("planks", 1), ("minecraft:wooden_pickaxe", 1)],
         {"oak_log": 1, "stick": 2},
         ([(["minecraft:oak_log", None, None, None], P, 4), (PICK, "minecraft:wooden_pickaxe", 1)], True,
          {"minecraft:oak_log": -1, P: 1, S: -2, "minecraft:wooden_pickaxe": 1})),
        ("edge: 2×2 only, no table needed", [("minecraft:stick", 1)], {"oak_planks": 2},
         ([([P, None, P, None], S, 4)], False, {P: -2, S: 4})),
        ("a carried table is a station, never an input: it stays in the bag", [("minecraft:wooden_pickaxe", 1)],
         {"oak_planks": 3, "stick": 2, "crafting_table": 1},
         ([(PICK, "minecraft:wooden_pickaxe", 1)], True, {P: -3, S: -2, "minecraft:wooden_pickaxe": 1})),
        ("must fail: two planks, no sticks → named, before a table is placed", [("minecraft:wooden_pickaxe", 1)],
         {"oak_planks": 2, "crafting_table": 1}, "missing 3× planks for crafting"),
        ("must fail: the chain's second step short even after the first", [("planks", 1), ("minecraft:wooden_pickaxe", 1)],
         {"oak_log": 1}, "missing 2× minecraft:stick for crafting"),
    ]

    def test_table(self):
        for why, recipes, have, want in self.TABLE:
            with self.subTest(why):
                inv = bag(inventory(**have))
                if isinstance(want, str):
                    with self.assertRaises(McError) as got:
                        skills.craft_plan(recipes, inv)
                    self.assertEqual(str(got.exception), want)
                else:
                    self.assertEqual(skills.craft_plan(recipes, inv), want)


class Sittings(unittest.TestCase):
    """skills.sittings: a chain cut where the grid changes — the 2×2 part in the bag, the 3×3 part at a table.
    plan_repair_on_event asked for a table before making it, every round (StationMissing)."""
    # (situation, recipes, bag) → [(needs a table, [items])] | the McError message
    TABLE = [
        ("stick, table, stone pickaxe, no table: the bag's part first, then the table it made",
         [("minecraft:stick", 1), ("minecraft:crafting_table", 1), ("minecraft:stone_pickaxe", 1)],
         {"oak_planks": 12, "cobblestone": 3},
         [(False, ["minecraft:stick", "minecraft:crafting_table"]), (True, ["minecraft:stone_pickaxe"])]),
        ("a table carried, only a 3×3 recipe: straight to the table", [("minecraft:wooden_pickaxe", 1)],
         {"oak_planks": 3, "stick": 2, "crafting_table": 1}, [(True, ["minecraft:wooden_pickaxe"])]),
        ("2×2 only: no table at all", [("minecraft:stick", 1)], {"oak_planks": 2}, [(False, ["minecraft:stick"])]),
        ("must fail: short of cobblestone — named, before anything is placed",
         [("minecraft:stick", 1), ("minecraft:crafting_table", 1), ("minecraft:stone_pickaxe", 1)],
         {"oak_planks": 12, "cobblestone": 1}, "missing"),
    ]

    def test_table(self):
        for why, recipes, have, want in self.TABLE:
            with self.subTest(why):
                inv = bag(inventory(**have))
                if isinstance(want, str):
                    with self.assertRaises(McError) as got:
                        skills.sittings(skills.craft_plan(recipes, inv)[0])
                    self.assertIn(want, str(got.exception))
                else:
                    got = skills.sittings(skills.craft_plan(recipes, inv)[0])
                    self.assertEqual([(t, [st[1] for st in part]) for t, part in got], want)


class MineSegmentCommands(unittest.TestCase):
    TABLE = [
        ("one cell, tool tier: drops required", ({"inv": slots(0)}, ([(1, 2, 3)], "minecraft:coal", 0)),
         [{"type": "mine_many", "collect": True, "requireDrops": True, "blocks": [{"x": 1, "y": 2, "z": 3}]}]),
        ("no tier: drops not required", ({"inv": slots(3)}, ([(0, 60, 0), (-1, 59, 4)], "minecraft:dirt", None)),
         [{"type": "mine_many", "collect": True, "requireDrops": False,
           "blocks": [{"x": 0, "y": 60, "z": 0}, {"x": -1, "y": 59, "z": 4}]}]),
        ("boundary: 27 used slots, still no filter", ({"inv": slots(27)}, ([(1, 2, 3)], "minecraft:coal", 0)),
         [{"type": "mine_many", "collect": True, "requireDrops": True, "blocks": [{"x": 1, "y": 2, "z": 3}]}]),
        ("boundary: 28 used slots, filtered to the whitelist",
         ({"inv": slots(28)}, ([(1, 2, 3)], "minecraft:raw_iron", 1)),
         [{"type": "mine_many", "collect": True, "requireDrops": True,
           "only": pickup_whitelist(28, ["minecraft:raw_iron"]), "blocks": [{"x": 1, "y": 2, "z": 3}]}]),
        ("no cells: an empty batch, not no batch", ({"inv": slots(0)}, ([], "minecraft:coal", 0)),
         [{"type": "mine_many", "collect": True, "requireDrops": True, "blocks": []}]),
        ("must fail: args short of (cells, drop, tier)", ({"inv": slots(0)}, ([(1, 2, 3)], "minecraft:coal")),
         ValueError),
    ]

    def test_table(self):
        run_table(self, skills.mine_segment_commands, self.TABLE)

    def test_filtered_batch_keeps_the_drop(self):
        only = skills.mine_segment_commands({"inv": slots(36)}, ([(1, 2, 3)], "minecraft:raw_iron", 1))[0]["only"]
        self.assertIn("minecraft:raw_iron", only)
        self.assertNotIn("minecraft:dirt", only)


class DarkHere(unittest.TestCase):
    TABLE = [
        ("no light at all, midday underground", (state(blockLight=0, skyLight=0, timeOfDay=6000),), True),
        ("open sky by day", (state(blockLight=0, skyLight=15, timeOfDay=6000),), False),
        ("open sky by night", (state(blockLight=0, skyLight=15, timeOfDay=13000),), True),
        ("boundary: skyLight 7 is not open sky", (state(blockLight=0, skyLight=7, timeOfDay=6000),), True),
        ("boundary: time 12500 is night", (state(blockLight=0, skyLight=15, timeOfDay=12500),), True),
        ("boundary: time 0 is not inside (0, 12500)", (state(blockLight=0, skyLight=15, timeOfDay=0),), True),
        ("must fail: block light 1 is lit", (state(blockLight=1, skyLight=0, timeOfDay=6000),), False),
        ("must fail: no blockLight reading", ({"skyLight": 0, "timeOfDay": 6000},), False),
    ]

    def test_table(self):
        run_table(self, skills.dark_here, self.TABLE)


class EdibleCarried(unittest.TestCase):
    TABLE = [
        ("cooked food", (bag(inventory(cooked_beef=1)),), True),
        ("raw meat counts as food", (bag(inventory(beef=1)),), True),
        ("boundary: food only in the offhand", (bag(inventory(offhand="bread")),), True),
        ("must fail: nothing edible", (bag(inventory(dirt=64)),), False),
        ("must fail: empty bag", (bag(inventory()),), False),
    ]

    def test_table(self):
        run_table(self, skills.edible_carried, self.TABLE)


class CanWorkHere(unittest.TestCase):
    TABLE = [
        ("on the ground", ({"inWater": False, "onGround": True},), None),
        ("treading water", ({"inWater": True, "onGround": False},), "treading water: nothing to stand on"),
        ("boundary: in water but standing (shore block)", ({"inWater": True, "onGround": True},), None),
        ("boundary: in water, no onGround field", ({"inWater": True},), "treading water: nothing to stand on"),
        ("must fail: empty state reads as able", ({},), None),
    ]

    def test_table(self):
        run_table(self, skills.can_work_here, self.TABLE)


class PendingReady(unittest.TestCase):
    TABLE = [
        ("one output ready long ago", ({"pending": [{"ready_at": 0}]},), True),
        ("one output far in the future", ({"pending": [{"ready_at": 1e12}]},), False),
        ("one of two ready", ({"pending": [{"ready_at": 1e12}, {"ready_at": 0}]},), True),
        ("boundary: empty pending list", ({"pending": []},), False),
        ("must fail: no pending key at all", ({},), False),
    ]

    def test_table(self):
        run_table(self, skills.pending_ready, self.TABLE)


# ---------------------------------------------------------------- solve

def names(via):
    return {d: a.name for d, a in via.items()}


CHOP = solve.Action("pt_chop", {"log": 1}, 5)
CRAFT = solve.Action("pt_craft", {"log": -1, "planks": 4}, 2)
BUY = solve.Action("pt_buy", {"log": 2}, 4)
MINE = solve.Action("pt_mine", {"iron": 1}, 10, requires={"pickaxe": 1})


class ReachTree(unittest.TestCase):
    TABLE = [
        ("one column", ([CHOP], {}), ({"log": 5.0}, {"log": "pt_chop"})),
        ("a chain: planks priced through logs", ([CHOP, CRAFT], {}),
         ({"log": 5.0, "planks": 1.75}, {"log": "pt_chop", "planks": "pt_craft"})),
        ("held logs cost nothing", ([CHOP, CRAFT], {"log": 3}), ({"log": 0.0, "planks": 0.5}, {"planks": "pt_craft"})),
        ("the cheaper per unit wins", ([CHOP, BUY], {}), ({"log": 2.0}, {"log": "pt_buy"})),
        ("boundary: a held zero is not held", ([CHOP], {"log": 0}), ({"log": 5.0}, {"log": "pt_chop"})),
        ("boundary: unpriced dimensions are dropped", ([CHOP], {"food": 5}), ({"log": 5.0}, {"log": "pt_chop"})),
        ("requirement held: reachable", ([MINE], {"pickaxe": 1}),
         ({"pickaxe": 0.0, "iron": 10.0}, {"iron": "pt_mine"})),
        ("must fail: requirement missing, nothing priced", ([MINE], {}), ({}, {})),
    ]

    def test_table(self):
        for why, (cols, st), (cost, via) in self.TABLE:
            with self.subTest(why), mock.patch.dict(solve._PRICES, clear=True):
                got_cost, got_via = solve.reach_tree(cols, st)
                self.assertEqual(got_cost, cost)
                self.assertEqual(names(got_via), via)


# ---------------------------------------------------------------- tape

class Replayed(unittest.TestCase):
    RECORDED = {"/state": {"x": 1}}
    TABLE = [
        ("a recorded GET", False, ("GET", "/state"), {"x": 1}),
        ("lenient miss: a well-formed nothing", True, ("GET", "/region"), dict(tape._EMPTY)),
        ("must fail: strict miss", False, ("GET", "/region"), tape.ReplayMiss),
        ("must fail: a POST never replays", False, ("POST", "/state"), tape.ReplayMiss),
        ("must fail: lenient does not excuse acting", True, ("POST", "/task"), tape.ReplayMiss),
    ]

    def test_table(self):
        for why, lenient, args, expected in self.TABLE:
            with self.subTest(why), mock.patch.object(tape, "REPLAY", self.RECORDED), \
                    mock.patch.object(tape, "LENIENT", lenient):
                if isinstance(expected, type):
                    with self.assertRaises(expected):
                        tape.replayed(*args)
                else:
                    self.assertEqual(tape.replayed(*args), expected)


# ---------------------------------------------------------------- threat

Z = "minecraft:zombie"
HERE = (0.0, 64.0, 0.0)


def zrow(pos, vel=(0.0, 0.0, 0.0)):
    return (tuple(pos), float(threat.MOBS[Z]["reach"]), tuple(vel), Z, 1.0, float(threat.MOBS[Z]["dps"]))


class HostileRows(unittest.TestCase):
    TABLE = [
        ("a zombie, no id: at rest", ([{"type": Z, "x": 1, "y": 64, "z": 2}], {}, 10.0), [zrow((1, 64, 2))]),
        ("velocity differenced against memory", ([{"type": Z, "id": 7, "x": 2, "y": 64, "z": 0}],
                                                 {7: ((0, 64, 0), 9.0)}, 10.0), [zrow((2, 64, 0), (2.0, 0.0, 0.0))]),
        ("boundary: memory 2 s old is too stale for velocity", ([{"type": Z, "id": 7, "x": 2, "y": 64, "z": 0}],
                                                               {7: ((0, 64, 0), 8.0)}, 10.0), [zrow((2, 64, 0))]),
        ("an angry neutral counts", ([{"type": "minecraft:enderman", "angry": True, "x": 0, "y": 64, "z": 3}], {}, 0.0),
         [((0, 64, 3), float(threat.MOBS["minecraft:enderman"]["reach"]), (0.0, 0.0, 0.0), "minecraft:enderman", 1.0,
           float(threat.MOBS["minecraft:enderman"]["dps"]))]),
        ("nothing near", ([], {}, 0.0), []),
        ("must fail: a calm neutral is no row", ([{"type": "minecraft:enderman", "x": 0, "y": 64, "z": 3}], {}, 0.0),
         []),
        ("must fail: a kind the table does not know", ([{"type": "minecraft:pig", "x": 0, "y": 64, "z": 3}], {}, 0.0),
         []),
    ]

    def test_table(self):
        run_table(self, threat.hostile_rows, self.TABLE)

    MEMORY = [
        ("seen: remembered at now", {}, [{"type": Z, "id": 7, "x": 2, "y": 64, "z": 0}], 10.0, {7: ((2, 64, 0), 10.0)}),
        ("boundary: exactly 10 s old is kept", {9: ((0, 0, 0), 0.0)}, [], 10.0, {9: ((0, 0, 0), 0.0)}),
        ("older than 10 s is dropped", {9: ((0, 0, 0), 0.0)}, [], 10.5, {}),
        ("must fail: no id, nothing remembered", {}, [{"type": Z, "x": 2, "y": 64, "z": 0}], 10.0, {}),
    ]

    def test_memory(self):
        for why, memory, near, now, after in self.MEMORY:
            with self.subTest(why):
                memory = dict(memory)
                threat.hostile_rows(near, memory, now)
                self.assertEqual(memory, after)


class IdsByRow(unittest.TestCase):
    NEAR = [{"x": 1, "y": 64, "z": 2, "id": 5}, {"x": -3, "y": 64, "z": 0, "id": 6}]
    TABLE = [
        ("each row named by position", (NEAR, [zrow((-3, 64, 0)), zrow((1, 64, 2))]), [6, 5]),
        ("float position matches int reading", (NEAR, [zrow((1.0, 64.0, 2.0))]), [5]),
        ("boundary: no rows", (NEAR, []), []),
        ("boundary: nothing near", (None, [zrow((1, 64, 2))]), [None]),
        ("must fail: a row nowhere near an entity", (NEAR, [zrow((9, 64, 9))]), [None]),
    ]

    def test_table(self):
        run_table(self, threat.ids_by_row, self.TABLE)


class EvadeCost(unittest.TestCase):
    TABLE = [
        ("no threats: free", (HERE, (10.0, 64.0, 0.0), [], 0.0), 0.0),
        ("boundary: no walk, no cost", (HERE, HERE, [zrow((1.0, 64.0, 0.0))], 0.0), 0.0),
        ("a creeper's blast is not pressure", (HERE, (10.0, 64.0, 0.0),
                                               [threat.row((1.0, 64.0, 0.0), 3.0, (0, 0, 0), "minecraft:creeper")],
                                               0.0), 0.0),
        ("must fail: an unknown kind presses nothing", (HERE, (10.0, 64.0, 0.0),
                                                        [threat.row((1.0, 64.0, 0.0), 3.0, (0, 0, 0), "minecraft:pig")],
                                                        0.0), 0.0),
    ]

    def test_table(self):
        run_table(self, threat.evade_cost, self.TABLE)

    def test_linear_in_the_walk(self):
        """Pressure is read once, here; the walk multiplies it: d blocks cost d × one block (to rounding)."""
        hazards = [zrow((1.0, 64.0, 0.0))]
        one = threat.evade_cost(HERE, (-1.0, 64.0, 0.0), hazards, 0.0)
        self.assertNotEqual(one, 0.0)
        for d in (2, 4, 8, 16):
            with self.subTest(d=d):
                self.assertAlmostEqual(threat.evade_cost(HERE, (-float(d), 64.0, 0.0), hazards, 0.0), d * one,
                                       delta=0.01 * d)


class ReshapeOptions(unittest.TestCase):
    """Only the no-means gate: with nothing to place and nothing that digs there is no column (grid never read)."""
    TABLE = [
        ("nothing said", ({},), []),
        ("no blocks", ({"blocks": 0},), []),
        ("boundary: no blocks and ground that does not dig", ({"blocks": 0, "dig_ok": False},), []),
        ("boundary: dig_ok falsy None", ({"blocks": 0, "dig_ok": None},), []),
        ("must fail: dig_ok opens the column, which then needs a threat", ({"blocks": 0, "dig_ok": True},),
         ValueError),
    ]

    def test_table(self):
        run_table(self, lambda st: threat.reshape_options(st, None, [], HERE, 1.0, 0.0, 0.0, 10.0), self.TABLE)


def option(hp=0.0, seconds=0.0, leaves=0.0, blast_after=0.0):
    return threat.Option("test", None, hp, seconds, "", leaves=leaves, blast_after=blast_after)


def LINEAR(hp):
    return 2.0 * hp


def SQUARE(hp):
    return hp * hp


class TotalCost(unittest.TestCase):
    """seconds + price(health spent + health still owed), with owed = leaves × work_s + blast_after."""
    TABLE = [
        ("nothing at all", (option(), LINEAR, 10.0), 0.0),
        ("time and health", (option(hp=3.0, seconds=2.0), LINEAR, 10.0), 8.0),
        ("what it leaves, over the work", (option(leaves=1.0), LINEAR, 10.0), 20.0),
        ("a blast still owed", (option(seconds=1.0, blast_after=5.0), LINEAR, 10.0), 11.0),
        ("convex price: one price of the sum, not two", (option(hp=2.0, seconds=1.0, leaves=0.5), SQUARE, 2.0),
         10.0),
        ("boundary: no work left, what it leaves is free", (option(hp=1.0, seconds=1.0, leaves=5.0), LINEAR, 0.0),
         3.0),
    ]

    def test_table(self):
        run_table(self, threat.total_cost, self.TABLE)


# ---------------------------------------------------------------- world

def vein(*cells, name="coal_ore"):
    return FakeRegion((-10, 0, -10), (10, 80, 10), {c: name for c in cells})


COAL = ["minecraft:coal_ore"]


class Connected(unittest.TestCase):
    LINE = vein((0, 10, 0), (1, 10, 0), (2, 10, 0))
    TABLE = [
        ("a line of three", (LINE, (0, 10, 0), COAL), {(0, 10, 0), (1, 10, 0), (2, 10, 0)}),
        ("from the middle", (LINE, (1, 10, 0), COAL), {(0, 10, 0), (1, 10, 0), (2, 10, 0)}),
        ("boundary: a lone block", (vein((5, 5, 5)), (5, 5, 5), COAL), {(5, 5, 5)}),
        ("diagonal is not connected", (vein((0, 10, 0), (1, 11, 0)), (0, 10, 0), COAL), {(0, 10, 0)}),
        ("two ids, one vein", (FakeRegion((-10, 0, -10), (10, 80, 10),
                                          {(0, 10, 0): "coal_ore", (0, 9, 0): "deepslate_coal_ore"}),
                               (0, 10, 0), COAL + ["minecraft:deepslate_coal_ore"]), {(0, 10, 0), (0, 9, 0)}),
        ("must fail: seed is not the ore", (LINE, (0, 11, 0), COAL), set()),
        ("must fail: the other id is not asked for", (FakeRegion((-10, 0, -10), (10, 80, 10),
                                                                 {(0, 10, 0): "coal_ore",
                                                                  (0, 9, 0): "deepslate_coal_ore"}),
                                                      (0, 10, 0), COAL), {(0, 10, 0)}),
    ]

    def test_table(self):
        run_table(self, connected, self.TABLE)


if __name__ == "__main__":
    unittest.main()
