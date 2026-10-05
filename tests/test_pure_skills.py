"""Pure-function tables for skills / tape / threat / world: one table per function, every row a subTest.

Each table holds a normal row, a boundary row and a must-fail row (its reason in the row's name). Inputs are readings
(tests/world.py) or hand-built rows through the modules' own constructors; nothing here talks to the game.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, beliefs, craft, estimate, fluids as fluids_mod, gather, knowledge, survive, tape, threat  # noqa: E402
from bonobo.api import McError, NotAvailable  # noqa: E402
from bonobo.bag import pickup_whitelist  # noqa: E402
from bonobo.data import DAY_TICKS, NIGHT_END, is_night  # noqa: E402
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
            ("must fail: nothing seen: no trunks", [], [])]

    def test_trunks(self):
        from bonobo import wood
        for name, logs, want in self.ROWS:
            with self.subTest(name):
                got = wood.pick_trunks([self.L(*c) for c in logs])
                self.assertEqual([[(t["x"], t["y"], t["z"]) for t in tr] for tr in got], want)


class CheapestTrunk(unittest.TestCase):
    """wood.cheapest_trunk: by the walk's price in nav.least_way_s order, no nearest-N cut."""

    def test_rows(self):
        from bonobo import wood
        trunks = [[{"x": d, "y": 64, "z": 0}] for d in (3, 5, 7, 9, 11, 30)]
        # (situation, walk seconds by trunk x (absent: no way)) → (trunk x chosen, trunk xs refused)
        rows = [("must fail: the five nearest have no way: the sixth", {30: 20.0}, (30, [3, 5, 7, 9, 11])),
                ("a nearer trunk round a long way: the farther one", {3: 40.0, 5: 1.0}, (5, [])),
                ("must fail: none has a way", {}, (None, [3, 5, 7, 9, 11, 30])),
                ("the nearest walked to straight: none farther asked", {3: 0.5, 5: 0.1}, (3, []))]
        for name, walks, (chosen, refused) in rows:
            with self.subTest(name):
                got, out = wood.cheapest_trunk(trunks, (0, 64, 0), lambda c: walks.get(c[0]))
                self.assertEqual((got[0]["x"] if got else None, [t[0]["x"] for t in out]), (chosen, refused))


class BitesToFull(unittest.TestCase):
    B, BEEF, APPLE, CARROT, RAW = ("minecraft:bread", "minecraft:cooked_beef", "minecraft:apple", "minecraft:carrot",
                                   "minecraft:beef")
    # (situation, food points, carried, raw ok) → (item, bites)
    ROWS = [("two haunches short, bread: one bite", 16, {B: 3}, False, (B, 1)),
            ("must fail: full: nothing", 20, {B: 3}, False, (None, 0)),
            ("only raw meat, not starving: nothing", 10, {RAW: 5}, False, (None, 0)),
            ("only raw meat, starving: raw, bites to full", 0, {RAW: 5}, True, (RAW, 7)),
            ("several: the one that fills the gap exactly", 12, {B: 1, BEEF: 1, APPLE: 1}, False, (BEEF, 1)),
            ("a small gap: the biggest that does not overflow", 17, {APPLE: 1, CARROT: 1}, False, (CARROT, 1)),
            ("every item overflows: the smallest", 19, {BEEF: 1, B: 1}, False, (B, 1)),
            ("none carried (count 0): nothing", 5, {B: 0}, True, (None, 0))]

    def test_bites(self):
        for name, food, carried, raw_ok, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(survive.bites_to_full(food, carried, raw_ok), want)


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
        run_table(self, craft.resolve_pattern, self.TABLE)


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
        run_table(self, craft.output_of, self.TABLE)


def slots(n, item="dirt"):
    return bag(inventory(*[(item, 1)] * n))


def _hit(x, y, z):
    return {"x": x, "y": y, "z": z, "id": "minecraft:iron_ore"}


class SeekHits(unittest.TestCase):
    """Mining seeks sealed ore as readily as exposed: seeing through blocks is allowed, the approach digs to it."""
    SEALED, OPEN, OURS = _hit(10002, 200, 10000), _hit(10003, 201, 10000), _hit(10004, 200, 10000)
    # (situation, (found, banned, protected), (hits, why none)); the miss's next ring or seek is the caller's (K8)
    TABLE = [
        ("sealed ore in stone beside us: found", ([SEALED], set(), set()), ([SEALED], "")),
        ("exposed and sealed alike, in /find's order", ([OPEN, SEALED], set(), set()), ([OPEN, SEALED], "")),
        ("must fail: nothing in range, and said so", ([], set(), set()), ([], "none in range")),
        ("must fail: excluded by bans and our builds, and said so",
         ([SEALED, OURS], {(10002, 200, 10000)}, {(10004, 200, 10000)}),
         ([], "2 in range but 1 banned, 1 protected")),
        ("a ban leaves the rest", ([SEALED, OPEN], {(10002, 200, 10000)}, set()), ([OPEN], "")),
    ]

    def test_table(self):
        for why, (found, banned, protected), want in self.TABLE:
            with self.subTest(why):
                self.assertEqual(gather.seek_hits(["iron_ore"], found, banned.__contains__, protected), want)

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
        ("must fail: nothing seen", ([],), 0),
    ]

    def test_table(self):
        from bonobo import api
        run_table(self, api.returns, self.TABLE)

    def test_the_circle_is_stopped(self):
        from bonobo import api
        stop = lambda seen: api.returns(seen) >= api.OSCILLATION_RETURNS   # noqa: E731
        self.assertEqual((stop([self.GO, self.MINE] * 3), stop([self.GO, self.MINE, self.GO])), (True, False))


class NotedHits(unittest.TestCase):
    """gather.noted_hits: a remembered ore is gone to straight, no /find (seen_store__noted scanned every pass)."""
    TABLE = [
        ("a noted diamond: that cell", ([{"kind": "diamond_ore", "pos": [4, 60, 0]}], ["diamond_ore"], set(), set()),
         [(4, 60, 0)]),
        ("must fail: a note of another block: nothing (then /find)", ([{"kind": "iron_ore", "pos": [4, 60, 0]}], ["diamond_ore"],
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
                got = gather.noted_hits(notes, blocks, lambda p: p in banned, protected)
                self.assertEqual([(h["x"], h["y"], h["z"]) for h in got], want)


class NotedIsACandidate(unittest.TestCase):
    """Merged from test_fix7.py (design-f1 V1): a remembered ore stays a candidate beside what the look sees."""

    def test_rows(self):
        class Stop(Exception):
            pass
        ore = (4, 64, 0)
        # (noted?) → (/find calls, the noted cell among the candidates)
        for why, noted, finds, among in [("noted: a candidate beside what the look sees", True, 2, True),
                                         ("nothing noted: the look alone", False, 2, False)]:
            with self.subTest(why):
                calls, asked = [], []
                ctx = type("C", (), {"mem": type("M", (), {"seen": lambda s, b, d: []})(), "dimension": "o",
                                     "blocked": lambda s, p: False,
                                     "policy": type("P", (), {"protected": set()})()})()
                hit = {"x": ore[0], "y": ore[1], "z": ore[2], "block": "minecraft:diamond_ore", "noted": True}

                def find(*a, **k):
                    calls.append(k.get("exposed", False))
                    return []               # the look sees none: the noted vein is the only candidate

                def stop(blocks, fresh, *a, **k):
                    asked.extend((h["x"], h["y"], h["z"], h.get("noted")) for h in fresh)
                    raise Stop()
                with mock.patch.object(gather, "noted_hits", lambda *a: [hit] if noted else []), \
                        mock.patch.object(gather, "find", find), mock.patch.object(gather, "seek_hits", stop), \
                        mock.patch.object(gather, "require_pickaxe", lambda t: None), \
                        mock.patch.object(gather, "Inventory", lambda: type("I", (), {"count": lambda s, t: 0})()), \
                        mock.patch.object(gather.nav, "mod_features", lambda: {"travel"}):
                    gen = gather.mine.__wrapped__(ctx, "minecraft:diamond", 1, ["diamond_ore"], 2)
                    with self.assertRaises(Stop):
                        for _ in gen:
                            pass
                self.assertEqual(len(calls), finds)
                # must fail: the noted vein dropped because the look did not see it
                self.assertEqual((*ore, True) in asked, among)


class SealPlan(unittest.TestCase):
    """fluids_mod.seal_plan: before breaking a cell, a block into every fluid cell touching it face to face."""

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
                        fluids_mod.seal_plan(region, [cell], bag(inv))
                    continue
                got = fluids_mod.seal_plan(region, [cell], bag(inv))
                self.assertEqual([(t["x"], t["y"], t["z"]) for t in got], want)
                self.assertTrue(all(t["type"] == "place" for t in got))


class TunnelAroundCaves(unittest.TestCase):
    """gather.plan_tunnel / tunnel_run: the night's tunnel is planned through the walls — never into a cave it could
    avoid, away from hostiles seen through the rock; boxed in by caves, the opening is sealed first (seal_plan)."""

    @staticmethod
    def rock(air=()):
        from tests.world import FakeRegion
        blocks = {(x, y, z): "stone" for x in range(-6, 7) for y in range(63, 67) for z in range(-6, 7)}
        for c in [(0, 64, 0), (0, 65, 0), *air]:
            blocks.pop(c, None)
        return FakeRegion((-7, 62, -7), (7, 67, 7), blocks)

    def test_the_way_chosen(self):
        feet, east = (0, 64, 0), (1, 0)
        boxed = [(1, 64, 1), (-1, 64, -1)]          # one pocket beside each first step
        # (situation, cave air, hostiles through the walls) → (direction, steps, cells to seal first)
        rows = [("solid all round: the facing, the whole length", [], [], ((1, 0), 4, None)),
                ("a cave two steps east: another way, the whole length", [(2, 64, 1)], [], ((0, 1), 4, None)),
                ("a zombie behind the east wall: the way ending farthest from it", [], [(5, 64, 0)], ((-1, 0), 4, None)),
                ("boxed in by caves: the facing's first step, sealed first", boxed, [],
                 ((1, 0), 0, [(1, 65, 0), (1, 64, 0)]))]
        for name, air, hostiles, want in rows:
            with self.subTest(name):
                self.assertEqual(gather.plan_tunnel(self.rock(air), feet, 4, hostiles=hostiles, facing=east), want)
        with self.subTest("must fail: the cave the facing would break into is not dug into"):
            d, end, cave = gather.plan_tunnel(self.rock([(2, 64, 1)]), feet, 4, facing=east)
            self.assertNotEqual(d, east)

    def test_tunnel_run(self):
        feet, east = (0, 64, 0), (1, 0)
        rows = [("solid: the whole length", self.rock(), (), 4, (4, None)),
                ("a cave beside the second step: one step, its cells", self.rock([(2, 64, 1)]), (), 4,
                 (1, [(2, 65, 0), (2, 64, 0)])),
                ("boundary: no length", self.rock(), (), 0, (0, None)),
                ("must fail: the first step protected", self.rock(), {(1, 64, 0)}, 4, (0, None)),
                ("must fail: no floor under the first step", self.rock([(1, 63, 0)]), (), 4, (0, None))]
        for name, region, protected, length, want in rows:
            with self.subTest(name):
                self.assertEqual(gather.tunnel_run(region, feet, east, length, protected), want)

    def test_the_opening_sealed_first(self):
        region = self.rock([(1, 64, 1), (-1, 64, -1)])
        own, cave = {(0, 64, 0), (0, 65, 0)}, [(1, 65, 0), (1, 64, 0)]
        # (situation, bag) → cells placed into, or NotAvailable
        rows = [("cobblestone: the pocket beside the step sealed", inventory(("cobblestone", 8)), [(1, 64, 1)]),
                ("dirt will do", inventory(("dirt", 2)), [(1, 64, 1)]),
                ("fluids only (the old rule): the pocket is not a fluid", None, []),
                ("must fail: nothing to seal with", inventory(), NotAvailable)]
        for name, inv, want in rows:
            with self.subTest(name):
                if inv is None:
                    got = fluids_mod.seal_plan(region, cave, bag(inventory(("cobblestone", 8))), own=own)
                    self.assertEqual(got, [])
                    continue
                if want is NotAvailable:
                    with self.assertRaises(NotAvailable):
                        fluids_mod.seal_plan(region, cave, bag(inv), own=own, cave=True)
                    continue
                got = fluids_mod.seal_plan(region, cave, bag(inv), own=own, cave=True)
                self.assertEqual([(t["x"], t["y"], t["z"]) for t in got], want)


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


class FurnaceTakes(unittest.TestCase):
    """craft.furnace_takes: an open furnace takes this batch — its input empty or the same, its output too."""

    def test_table(self):
        inp, out = ["minecraft:raw_iron"], "minecraft:iron_ingot"
        rows = [("empty", {}, True), ("the same input already in", {0: "minecraft:raw_iron"}, True),
                ("our ingots waiting in the output", {2: "minecraft:iron_ingot"}, True),
                ("must fail: beef cooking in it", {0: "minecraft:beef"}, False),
                ("must fail: cooked beef in the output", {2: "minecraft:cooked_beef"}, False)]
        for name, slots, want in rows:
            with self.subTest(name):
                self.assertIs(craft.furnace_takes(slots, inp, out), want)


class TakeBackVerdict(unittest.TestCase):
    """craft.take_back_verdict: a station not picked up but still standing is left (a station there), not lost."""
    # (situation, (bag gained it, still standing)) → verdict
    TABLE = [
        ("picked up: taken", (True, False), "taken"),
        ("in the bag though one still shows there (another table): taken", (True, True), "taken"),
        ("the break did not happen: left standing, a station", (False, True), "left"),
        ("must fail as a loss: gone and not in the bag", (False, False), "lost"),
    ]

    def test_table(self):
        run_table(self, craft.take_back_verdict, self.TABLE)


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
        run_table(self, craft.takes_back, self.TABLE)


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
                        craft.craft_plan(recipes, inv)
                    self.assertEqual(str(got.exception), want)
                else:
                    self.assertEqual(craft.craft_plan(recipes, inv), want)

    def test_mixed_wood_craft_plan(self):
        """Mixed log types (e.g. 3 oak + 1 birch) must successfully craft planks across batches."""
        inv = bag(inventory(oak_log=3, birch_log=1))
        steps, table, delta = craft.craft_plan([("planks", 4)], inv)
        self.assertFalse(table)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0], (["minecraft:oak_log", None, None, None], "minecraft:oak_planks", 12))
        self.assertEqual(steps[1], (["minecraft:birch_log", None, None, None], "minecraft:birch_planks", 4))
        self.assertEqual(delta, {"minecraft:oak_log": -3, "minecraft:oak_planks": 12,
                                 "minecraft:birch_log": -1, "minecraft:birch_planks": 4})


class CraftCommands(unittest.TestCase):
    """craft.craft_commands: a crafting session as one chain — 2×2 in the bag, the table opened once, a placed
    table taken back; recomputed from the bag after an interrupt, nothing made twice or skipped."""
    PICK = [("minecraft:stick", 1), ("minecraft:crafting_table", 1), ("minecraft:stone_pickaxe", 1)]

    @staticmethod
    def shape(tasks):
        return [t["type"] if t["type"] != "craft" else ("craft", len(t["pattern"])) for t in tasks]

    def test_table(self):
        from tests.world import bag, inventory
        from bonobo.skillcore import StationMissing
        spot, near = (1, 64, 0), (2, 64, 0)
        pick, stick, stone = self.PICK, [("minecraft:stick", 1)], [("minecraft:stone_pickaxe", 1)]
        rows = [
            ("no table anywhere: the bag's crafts, the table placed, opened, the pickaxe, closed, taken back", pick,
             {"oak_planks": 12, "cobblestone": 3}, None, spot,
             ["_close", ("craft", 4), ("craft", 4), "place", "use", ("craft", 9), "_close", "mine"]),
            ("a table near: opened, not placed nor taken", pick, {"oak_planks": 12, "cobblestone": 3}, near, None,
             ["_close", ("craft", 4), ("craft", 4), "use", ("craft", 9), "_close"]),
            ("2×2 only: no table at all", stick, {"oak_planks": 2}, None, None, ["_close", ("craft", 4)]),
            ("must fail: a 3×3 recipe, no table carried, none near, none made", stone,
             {"cobblestone": 3, "stick": 2}, None, spot, StationMissing),
        ]
        for name, recipes, have, table, sp, want in rows:
            with self.subTest(name):
                st = {"inv": bag(inventory(**have)), "table": table, "spot": sp}
                if isinstance(want, type):
                    with self.assertRaises(want):
                        craft.craft_commands(st, (recipes,))
                    continue
                self.assertEqual(self.shape(craft.craft_commands(st, (recipes,))), want)

    def test_a_table_is_always_taken(self):
        """Crafting table is always taken back after use, never left standing."""
        from tests.world import bag, inventory
        st = {"inv": bag(inventory(oak_planks=12, cobblestone=3)), "table": None, "spot": (1, 64, 0)}
        for name, next_use in [("next use close", (1, 64, 0)), ("next use far", (1, 64, 300))]:
            with self.subTest(name):
                got = self.shape(craft.craft_commands(dict(st, next_use=next_use), (self.PICK,)))
                self.assertTrue("mine" in got)

    def test_resumed_from_the_bag(self):
        """Interrupted after the bag's crafts: the chain rebuilt from the bag then holds only the pickaxe."""
        from tests.world import bag, inventory
        st = {"inv": bag(inventory(oak_planks=6, stick=4, crafting_table=1, cobblestone=3)), "table": None,
              "spot": (1, 64, 0)}
        got = self.shape(craft.craft_commands(st, ([("minecraft:stone_pickaxe", 1)],)))
        self.assertEqual(got, ["place", "use", ("craft", 9), "_close", "mine"])
        self.assertEqual(got.count(("craft", 4)), 0, "nothing of the first sitting made twice")


class Sittings(unittest.TestCase):
    """craft.sittings: a chain cut where the grid changes — the 2×2 part in the bag, the 3×3 part at a table.
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
                        craft.sittings(craft.craft_plan(recipes, inv)[0])
                    self.assertIn(want, str(got.exception))
                else:
                    got = craft.sittings(craft.craft_plan(recipes, inv)[0])
                    self.assertEqual([(t, [st[1] for st in part]) for t, part in got], want)


class MineSegmentCommands(unittest.TestCase):
    @staticmethod
    def mines(cells, rd):
        return [{"type": "mine", "x": c[0], "y": c[1], "z": c[2], "collect": False, "requireDrops": rd} for c in cells]

    from bonobo import nav as _nav
    TABLE = [
        ("one cell, tool tier: drops required", ({"inv": slots(0)}, ([(1, 2, 3)], "minecraft:coal", 0)),
         mines.__func__([(1, 2, 3)], True) + [_nav.batch_sweep([(1, 2, 3)])]),
        ("no tier: drops not required, in the batch's order", ({"inv": slots(3)},
                                                                 ([(0, 60, 0), (-1, 59, 4)], "minecraft:dirt", None)),
         mines.__func__([(0, 60, 0), (-1, 59, 4)], False) + [_nav.batch_sweep([(0, 60, 0), (-1, 59, 4)])]),
        ("boundary: 27 used slots, still no filter", ({"inv": slots(27)}, ([(1, 2, 3)], "minecraft:coal", 0)),
         mines.__func__([(1, 2, 3)], True) + [_nav.batch_sweep([(1, 2, 3)])]),
        ("boundary: 28 used slots, the sweep filtered to the whitelist",
         ({"inv": slots(28)}, ([(1, 2, 3)], "minecraft:raw_iron", 1)),
         mines.__func__([(1, 2, 3)], True) + [_nav.batch_sweep([(1, 2, 3)], pickup_whitelist(28, ["minecraft:raw_iron"]))]),
        ("no cells: the sweep alone", ({"inv": slots(0)}, ([], "minecraft:coal", 0)), [_nav.batch_sweep([])]),
        ("must fail: args short of (cells, drop, tier)", ({"inv": slots(0)}, ([(1, 2, 3)], "minecraft:coal")),
         ValueError),
    ]

    def test_table(self):
        run_table(self, gather.mine_segment_commands, self.TABLE)

    def test_filtered_batch_keeps_the_drop(self):
        only = gather.mine_segment_commands({"inv": slots(36)}, ([(1, 2, 3)], "minecraft:raw_iron", 1))[-1]["only"]
        self.assertIn("minecraft:raw_iron", only)
        self.assertNotIn("minecraft:dirt", only)


class DeepBelow(unittest.TestCase):
    TABLE = [
        ("four below: a staircase", ((0, 60, 0), (0, 64, 0)), True),
        ("boundary: three below", ((0, 61, 0), (0, 64, 0)), True),
        ("must fail: two below is a walk", ((0, 62, 0), (0, 64, 0)), False),
        ("must fail: above the feet", ((0, 66, 0), (0, 64, 0)), False),
    ]

    def test_table(self):
        run_table(self, gather.deep_below, self.TABLE)


class StairLegEnd(unittest.TestCase):
    TABLE = [
        ("along x, the longer axis", ((0, 64, 0), (10, 50, 2)), (8, 56, 0)),
        ("along -z, the longer axis", ((0, 64, 0), (1, 50, -9)), (0, 56, -8)),
        ("boundary: straight below steps along +x", ((0, 64, 0), (0, 40, 0)), (8, 56, 0)),
        ("boundary: a tie goes along x", ((0, 64, 0), (-3, 40, 3)), (-8, 56, 0)),
        ("must fail: a target without its z", ((0, 64, 0), (3, 40)), IndexError),
    ]

    def test_table(self):
        run_table(self, gather.stair_leg_end, self.TABLE)


class ReachCells(unittest.TestCase):
    TABLE = [
        ("within reach, nearest first", ({(3, 64, 0), (1, 64, 0), (5, 64, 0)}, (0, 64, 0)), [(1, 64, 0), (3, 64, 0)]),
        ("boundary: 4.47 off is in reach", ({(4, 64, 2)}, (0, 64, 0)), [(4, 64, 2)]),
        ("must fail: 5 off is out of reach", ({(4, 64, 3)}, (0, 64, 0)), []),
        ("must fail: no vein", (set(), (0, 64, 0)), []),
    ]

    def test_table(self):
        run_table(self, gather.reach_cells, self.TABLE)


class SightBox(unittest.TestCase):
    TABLE = [
        ("around the origin", ((0, 64, 0),), ((-5, 60, -5), (5, 70, 5))),
        ("around a far stand", ((100, 12, -40),), ((95, 8, -45), (105, 18, -35))),
        ("boundary: below zero", ((0, -60, 0),), ((-5, -64, -5), (5, -54, 5))),
        ("must fail: a stand without its z", ((0, 64),), ValueError),
    ]

    def test_table(self):
        run_table(self, gather.sight_box, self.TABLE)


def _ores(*cells):
    from tests.world import flat
    ground = flat()
    return FakeRegion(ground.lo, ground.hi, {**ground.blocks, **{c: "iron_ore" for c in cells}})


class HeldCells(unittest.TestCase):
    """gather.held_cells: what the jar can break from the stand (nav.holds), the vein's own cells not in the way."""

    def test_table(self):
        wall = [(2, 64, 0), (3, 64, 0), (2, 65, 0), (3, 65, 0)]
        rows = [("an ore on the floor in sight", _ores((2, 64, 0)), [(2, 64, 0)], {(2, 64, 0)}, {(2, 64, 0)}),
                ("behind the vein's own cells: they break first", _ores(*wall), [(3, 64, 0)], set(wall), {(3, 64, 0)}),
                ("must fail: behind ore that is not the vein's", _ores(*wall), [(3, 64, 0)], {(3, 64, 0)}, set()),
                ("must fail: buried under the floor", _ores((2, 62, 0)), [(2, 62, 0)], {(2, 62, 0)}, set()),
                ("must fail: the block under the feet", _ores((0, 63, 0)), [(0, 63, 0)], {(0, 63, 0)}, set()),
                ("must fail: out of reach", _ores((6, 64, 0)), [(6, 64, 0)], {(6, 64, 0)}, set())]
        for why, region, cells, vein, want in rows:
            with self.subTest(why):
                self.assertEqual(gather.held_cells(region, (0, 64, 0), cells, vein), want)


class OpenFacedCells(unittest.TestCase):
    def test_table(self):
        two = [(1, 64, 0), (2, 64, 0)]
        region = _ores(*two)
        rows = [("mineable and held", two, set(two), two),
                ("must fail: nothing held", two, set(), []),
                ("must fail: the floor under the feet", [(0, 63, 0)], {(0, 63, 0)}, [])]
        for why, cells, held, want in rows:
            with self.subTest(why):
                self.assertEqual(gather.open_faced_cells(cells, (0, 64, 0), region, held), want)


class OpenerPairs(unittest.TestCase):
    def test_table(self):
        rows = [("buried: the side face nearest the eye first", _ores((2, 62, 0)), (), False,
                 [((2, 62, 0), (1, 62, 0))]),
                ("must fail: that face is protected", _ores((2, 62, 0)), {(1, 62, 0)}, False, []),
                ("must fail: an open-faced cell needs none", _ores((2, 64, 0)), (), False, []),
                ("must fail: no region read", None, (), True, [])]
        for why, region, protected, forced, want in rows:
            with self.subTest(why):
                cells = [(2, 62, 0)] if region is None else [c for c in region.blocks if region.name(c) == "iron_ore"]
                self.assertEqual(gather.opener_pairs(region, cells, (0, 64, 0), protected, forced=forced), want)


class SealOrWet(unittest.TestCase):
    """gather.seal_or_wet: seal_plan's blocks, or with nothing to seal with the wet cells and why."""

    def test_table(self):
        from tests.world import bag, inventory
        lo, hi = (-3, 60, -3), (3, 68, 3)
        stone = {(x, y, z): "stone" for x in range(-3, 4) for y in range(60, 69) for z in range(-3, 4)}
        a, b = (0, 64, 0), (2, 64, 0)
        cobble, none = inventory(("cobblestone", 8)), inventory()
        rows = [("lava below, cobblestone: sealed", {(0, 63, 0): "lava"}, cobble, ([(0, 63, 0)], set(), None)),
                ("dry: nothing to seal", {}, none, ([], set(), None)),
                ("must fail: lava below, nothing to seal with", {(0, 63, 0): "lava"}, none, ([], {a}, NotAvailable)),
                ("must fail: only the wet cell is dropped", {(2, 63, 0): "water"}, none, ([], {b}, NotAvailable))]
        for why, fluid, inv, want in rows:
            with self.subTest(why):
                region = FakeRegion(lo, hi, {**stone, a: "iron_ore", b: "iron_ore", **fluid})
                seal, wet, e = gather.seal_or_wet(region, {a, b}, bag(inv))
                self.assertEqual(([(t["x"], t["y"], t["z"]) for t in seal], wet, e and type(e)), want)


class StandRefused(unittest.TestCase):
    TABLE = [
        ("no stand held: every cell not opened yet", ([(1, 2, 3), (4, 5, 6)], set(), "cannot hold a stand spot"),
         [(1, 2, 3), (4, 5, 6)]),
        ("boundary: a cell opened once is not opened again", ([(1, 2, 3), (4, 5, 6)], {(1, 2, 3)},
                                                              "x: cannot hold a stand spot for 2"), [(4, 5, 6)]),
        ("must fail: another refusal", ([(1, 2, 3)], set(), "cannot reach 1, 2, 3"), []),
        ("must fail: no cells named", ([], set(), "cannot hold a stand spot"), []),
    ]

    def test_table(self):
        run_table(self, gather.stand_refused, self.TABLE)


class PartialRefusal(unittest.TestCase):
    TABLE = [
        ("one of three failed: the cell it names", ("1 of 3 steps failed: cannot reach 1, 2, -3",), {(1, 2, -3)}),
        ("boundary: partial, no cell named", ("2 of 3 steps failed",), set()),
        ("must fail: every step failed is not partial", ("3 of 3 steps failed: cannot reach 1, 2, 3",), None),
        ("must fail: no message", (None,), None),
    ]

    def test_table(self):
        run_table(self, gather.partial_refusal, self.TABLE)


class ReachBudget(unittest.TestCase):
    TABLE = [
        ("none spent", (0, ["iron_ore"]), None),
        ("boundary: one short of the budget", (2, ["iron_ore"]), None),
        ("must fail: the budget spent", (3, ["iron_ore"], "no way"), api.NavFailed),
        ("must fail: over the budget", (5, ["iron_ore"]), api.NavFailed),
    ]

    def test_table(self):
        run_table(self, gather._reach_budget, self.TABLE)


class DarkHere(unittest.TestCase):
    TABLE = [
        ("no light at all, midday underground", (state(blockLight=0, skyLight=0, timeOfDay=6000),), True),
        ("open sky by day", (state(blockLight=0, skyLight=15, timeOfDay=6000),), False),
        ("open sky by night", (state(blockLight=0, skyLight=15, timeOfDay=13000),), True),
        ("boundary: skyLight 7 is not open sky", (state(blockLight=0, skyLight=7, timeOfDay=6000),), True),
        ("boundary: time 12500 is night", (state(blockLight=0, skyLight=15, timeOfDay=12500),), is_night(12500)),
        # tick 0 is sunrise's end, the day's start (Minecraft Wiki, Daylight cycle): no night spawning under open sky
        ("boundary: time 0 is day", (state(blockLight=0, skyLight=15, timeOfDay=0),), is_night(0)),
        ("dawn after NIGHT_END is day", (state(blockLight=0, skyLight=15, timeOfDay=NIGHT_END + 100),),
         is_night(NIGHT_END + 100)),
        ("must fail: day 3 noon (absolute clock) under open sky is not dark",
         (state(blockLight=0, skyLight=15, timeOfDay=2 * DAY_TICKS + 6000),), False),
        ("must fail: block light 1 is lit", (state(blockLight=1, skyLight=0, timeOfDay=6000),), False),
        ("must fail: no blockLight reading", ({"skyLight": 0, "timeOfDay": 6000},), False),
    ]

    def test_table(self):
        run_table(self, knowledge.dark_here, self.TABLE)


class PendingReady(unittest.TestCase):
    TABLE = [
        ("one output ready long ago", ({"pending": [{"ready_at": 0}]},), True),
        ("one output far in the future", ({"pending": [{"ready_at": 1e12}]},), False),
        ("one of two ready", ({"pending": [{"ready_at": 1e12}, {"ready_at": 0}]},), True),
        ("boundary: empty pending list", ({"pending": []},), False),
        ("must fail: no pending key at all", ({},), False),
    ]

    def test_table(self):
        run_table(self, craft.pending_ready, self.TABLE)


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
    return (tuple(pos), float(beliefs.MOBS[Z]["reach"]), tuple(vel), Z, 1.0, float(beliefs.MOBS[Z]["dps"]))


class HostileRows(unittest.TestCase):
    TABLE = [
        ("a zombie, no id: at rest", ([{"type": Z, "x": 1, "y": 64, "z": 2}], {}, 10.0), [zrow((1, 64, 2))]),
        ("velocity differenced against memory", ([{"type": Z, "id": 7, "x": 2, "y": 64, "z": 0}],
                                                 {7: ((0, 64, 0), 9.0)}, 10.0), [zrow((2, 64, 0), (2.0, 0.0, 0.0))]),
        ("boundary: memory 2 s old is too stale for velocity", ([{"type": Z, "id": 7, "x": 2, "y": 64, "z": 0}],
                                                               {7: ((0, 64, 0), 8.0)}, 10.0), [zrow((2, 64, 0))]),
        ("an angry neutral counts", ([{"type": "minecraft:enderman", "angry": True, "x": 0, "y": 64, "z": 3}], {}, 0.0),
         [((0, 64, 3), float(beliefs.MOBS["minecraft:enderman"]["reach"]), (0.0, 0.0, 0.0), "minecraft:enderman", 1.0,
           float(beliefs.MOBS["minecraft:enderman"]["dps"]))]),
        ("nothing near", ([], {}, 0.0), []),
        ("must fail: a calm neutral is no row", ([{"type": "minecraft:enderman", "x": 0, "y": 64, "z": 3}], {}, 0.0),
         []),
        ("must fail: a kind the table does not know", ([{"type": "minecraft:pig", "x": 0, "y": 64, "z": 3}], {}, 0.0),
         []),
    ]

    def test_table(self):
        run_table(self, lambda near, memory, now: threat.hostile_rows(near, memory, now, HERE), self.TABLE)

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
                threat.hostile_rows(near, memory, now, HERE)
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
                                               [estimate.row((1.0, 64.0, 0.0), 3.0, (0, 0, 0), "minecraft:creeper")],
                                               0.0), 0.0),
        ("must fail: an unknown kind presses nothing", (HERE, (10.0, 64.0, 0.0),
                                                        [estimate.row((1.0, 64.0, 0.0), 3.0, (0, 0, 0), "minecraft:pig")],
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


class LyingDrops(unittest.TestCase):
    """nav.lying_drops: the drops a batch's sweep left lying, by item id (night_first__low 175033: the log written off
    by the jar's sweep, the trunk banned, the task failed) — what sweep_lying sweeps once more."""

    def test_table(self):
        from bonobo import nav
        log = {"type": "minecraft:item", "item": {"id": "minecraft:oak_log", "count": 1}}
        dirt = {"type": "minecraft:item", "item": {"id": "minecraft:dirt", "count": 1}}
        rows = [("must fail: a log left lying is found", [log, dirt], ["minecraft:oak_log"], [log]),
                ("another item only: none", [dirt], ["minecraft:oak_log"], []),
                ("nothing lying: none", [], ["minecraft:oak_log"], [])]
        for name, items, only, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.lying_drops(items, only), want)


if __name__ == "__main__":
    unittest.main()
