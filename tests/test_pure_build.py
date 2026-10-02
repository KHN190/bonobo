"""Pure tables for the build group: one table per function, every row a subTest.

Expected values are worked out by hand from the rule each function states (geometry, arithmetic, group membership);
the config constants they read (pit geometry, slot_fill_s, UNPRICED_S) are named, not re-derived. Nothing here talks
to the game: the only stand-ins are for I/O (a FakeRegion of named blocks, an inventory that answers `usable`).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, bag, beliefs, blueprints, building  # noqa: E402
from bonobo.api import Interrupted, NotAvailable  # noqa: E402
from bonobo.blueprints import Blueprint, Part  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

SLOT_S = float(beliefs.CONFIG["plan"]["slot_fill_s"])


# ------------------------------------------------------------------------------------------------------ actions


class EmptyHow(unittest.TestCase):
    """bag.empty_how: the empty-the-bag reflex deposits into a chest that exists, else drops the cheapest; never a
    chest made for it."""
    FLESH = {"slot": 9, "id": "minecraft:rotten_flesh", "count": 10}
    STRING = {"slot": 10, "id": "minecraft:string", "count": 5}
    GEM = {"slot": 11, "id": "minecraft:diamond", "count": 3}
    PRICE = {"minecraft:rotten_flesh": 0.1, "minecraft:string": 0.5, "minecraft:diamond": 600.0}.get
    # (situation, slots, need, seconds to an existing chest, lava near) → "deposit" | "drop" | the reason it cannot
    ROWS = [("no chest anywhere: drop the cheapest", [FLESH, STRING, GEM], 1, None, False, "drop"),
            ("a chest near, the junk not worth the walk: drop", [FLESH, STRING, GEM], 1, 10.0, False, "drop"),
            ("a chest near, diamonds to free a slot: deposit", [GEM], 1, 10.0, False, "deposit"),
            ("lava near and a chest: deposit, nothing burns", [FLESH], 1, 10.0, True, "deposit"),
            ("must fail: lava near, no chest: nothing may go", [FLESH], 1, None, True, "lava is near"),
            ("the bag holds nothing that may go", [], 1, None, False, "every stack is needed")]

    def test_empty_how(self):
        for name, slots, need, chest_s, lava, want in self.ROWS:
            with self.subTest(name):
                try:
                    got = bag.empty_how(slots, need, self.PRICE, chest_s, lava)
                except NotAvailable as e:
                    got = want if want in str(e) else str(e)
                self.assertEqual(got, want)


# ---------------------------------------------------------------------------------------------------------- api

class Interrupts(unittest.TestCase):
    def setUp(self):
        self._saved = api.STATE.interrupt

    def tearDown(self):
        api.STATE.interrupt = self._saved

    def test_consume(self):
        rows = [
            # pending        returned       why
            ("lava",         "lava",        "the pending reason is handed over"),
            (None,           None,          "boundary: nothing pending"),  # must fail: nothing pending hands nothing over
            ("",             "",            "empty reason returned as is"),
            ("creeper near", "creeper near", "any text"),
        ]
        for pending, want, why in rows:
            with self.subTest(pending=pending, why=why):
                api.STATE.interrupt = pending
                self.assertEqual(api.consume_interrupt(), want)
                self.assertEqual(api.STATE.interrupt, None)          # cleared either way
                self.assertEqual(api.consume_interrupt(), None)  # and only once

    def test_take(self):
        rows = [
            # pending        raises with     why
            ("lava",         "lava",         "a reason raises Interrupted"),
            ("creeper",      "creeper",      "the message is the reason"),
            (None,           None,           "negative: nothing pending, no raise"),
            ("",             None,           "negative: an empty reason is falsy, no raise"),
        ]
        for pending, want, why in rows:
            with self.subTest(pending=pending, why=why):
                api.STATE.interrupt = pending
                if want is None:
                    self.assertEqual(api.take_interrupt(), None)
                else:
                    with self.assertRaises(Interrupted) as cm:
                        api.take_interrupt()
                    self.assertEqual(str(cm.exception), want)
                self.assertEqual(api.STATE.interrupt, None)


# ---------------------------------------------------------------------------------------------- bag / beliefs

class RegetSeconds(unittest.TestCase):
    def test_table(self):
        two = lambda item: 2.0          # noqa: E731
        none = lambda item: None        # noqa: E731
        zero = lambda item: 0           # noqa: E731
        rows = [
            # stack                                   price  expected               why
            ({"id": "minecraft:dirt", "count": 10},   None,  bag.UNPRICED_S * 10,   "no price model: unpriced"),
            ({"id": "minecraft:dirt", "count": 10},   two,   20.0,                  "price × count"),
            ({"id": "minecraft:dirt"},                two,   2.0,                   "count defaults to one"),
            ({"id": "minecraft:dirt", "count": 3},    none,  bag.UNPRICED_S * 3,    "model has no price: unpriced"),  # must fail: no price is unpriced, not free
            ({"id": "minecraft:dirt", "count": 3},    zero,  0.0,                   "boundary: a zero price is a price"),
            ({"id": "minecraft:dirt", "count": 0},    two,   0.0,                   "boundary: empty stack"),
        ]
        for stack, price, want, why in rows:
            with self.subTest(stack=stack, why=why):
                self.assertEqual(bag.reget_seconds(stack, price), want)

    def test_missing_id(self):
        with self.assertRaises(KeyError):          # negative: a stack without an id cannot be priced
            bag.reget_seconds({"count": 1}, lambda item: 1.0)


class SlotCost(unittest.TestCase):
    def test_slot_cost_s(self):
        rows = [
            # free    expected           why
            (6,       SLOT_S / 36,       "1/free²"),
            (2,       SLOT_S / 4,        "two left"),
            (1,       SLOT_S,            "boundary: last slot"),
            (0,       SLOT_S,            "negative: a full bag clamps to one, not a division by zero"),
            (-3,      SLOT_S,            "negative: below zero clamps too"),
            (0.5,     SLOT_S,            "fractions below one clamp"),
        ]
        for free, want, why in rows:
            with self.subTest(free=free, why=why):
                self.assertAlmostEqual(beliefs.slot_cost_s(free), want, places=9)


# --------------------------------------------------------------------------------------------------- blueprints

class Rotate(unittest.TestCase):
    def test_rotate_offset(self):
        rows = [
            # offset       turns  expected         why
            ((1, 0, 0),    0,     (1, 0, 0),       "no turn"),
            ((1, 0, 0),    1,     (0, 0, 1),       "east to south"),
            ((1, 5, 2),    1,     (-2, 5, 1),      "y untouched"),
            ((1, 0, 0),    2,     (-1, 0, 0),      "half turn"),
            ((1, 0, 0),    4,     (1, 0, 0),       "boundary: full turn is identity"),
            ((1, 0, 0),    -1,    (0, 0, -1),      "negative turns wrap to three"),
            ((0, 0, -2),   1,     (2, 0, 0),       "access spot north to east"),
        ]
        for off, turns, want, why in rows:
            with self.subTest(off=off, turns=turns, why=why):
                self.assertEqual(blueprints.rotate_offset(off, turns), want)

    def test_rotate_dir(self):
        rows = [
            # dir         turns  expected       why
            ("north",     1,     "east",        "clockwise"),
            ("west",      1,     "north",       "wraps around"),
            ("north",     4,     "north",       "boundary: full turn"),
            ("south",     -1,    "east",        "negative turns wrap to three"),
            ("up",        3,     "up",          "vertical never turns"),
            ("sideways",  0,     "sideways",    "negative: zero turns never looks the name up"),
        ]
        for d, turns, want, why in rows:
            with self.subTest(d=d, turns=turns, why=why):
                self.assertEqual(blueprints.rotate_dir(d, turns), want)
        with self.assertRaises(KeyError):          # negative: an unknown direction cannot be turned
            blueprints.rotate_dir("sideways", 1)


# ----------------------------------------------------------------------------------------------------- building

class BlockMatches(unittest.TestCase):
    def test_table(self):
        rows = [
            # block name                  token                   expected  why
            ("minecraft:torch",           "torch",                True,     "torch token"),
            ("wall_torch",                "minecraft:torch",      True,     "a torch on a wall is the torch"),
            ("minecraft:redstone_torch",  "torch",                False,    "negative: not a torch"),
            ("minecraft:cobblestone",     "stone",                True,     "group member"),
            ("stone",                     "stone",                False,    "negative: smooth stone is not in the stone group"),
            ("minecraft:spruce_door",     "door",                 True,     "any door"),
            ("minecraft:cooked_beef",     "food",                 True,     "food group"),
            ("obsidian",                  "minecraft:obsidian",   True,     "bare vs full id"),
            ("air",                       "minecraft:obsidian",   False,    "negative: missing part"),
        ]
        for name, token, want, why in rows:
            with self.subTest(name=name, token=token, why=why):
                self.assertEqual(building.block_matches(name, token), want)


class _Inv:
    """What `blueprint_commands` asks of an inventory, and nothing more."""

    def __init__(self, counts):
        self.counts = counts

    def usable(self, item):
        return self.counts.get(item, 0)


class BlueprintCommands(unittest.TestCase):
    O = "minecraft:obsidian"
    ORIGIN = (10, 64, 10)
    ACCESS_FEET = (10, 64, 8)        # origin + default access (0, 0, -2)

    def cmds(self, parts, blocks=None, turns=0, feet=ACCESS_FEET, protected=(), inv=None):
        bp = Blueprint("t", "", tuple(parts))
        state = {"region": FakeRegion((0, 0, 0), (20, 80, 20), blocks or {}),
                 "inv": _Inv(inv if inv is not None else {"minecraft:cobblestone": 5}),
                 "protected": set(protected), "feet": feet}
        return building.blueprint_commands(state, (bp, self.ORIGIN, turns))

    def place(self, x, y, z, item=None, **kw):
        return dict({"type": "place", "item": item or self.O, "x": x, "y": y, "z": z}, **kw)

    def test_table(self):
        leaves = {(11, 65, 10): "oak_leaves"}
        mine_leaves = {"type": "mine", "x": 11, "y": 65, "z": 10, "collect": False, "requireDrops": False}
        pillar = {"type": "pillar", "item": "minecraft:cobblestone"}
        goto = {"type": "goto", "x": 10, "y": 64, "z": 8, "range": 0.3, "partial": False}
        rows = [
            # kwargs                                                                        expected                                        why
            (dict(parts=[Part((0, 0, 0), self.O)]),                                          [self.place(10, 64, 10)],                       "one part, empty site"),
            (dict(parts=[Part((0, 0, 0), self.O)], blocks={(10, 64, 10): "obsidian"}),       [],                                             "resume: part already there"),
            (dict(parts=[Part((0, 1, 0), self.O), Part((0, 0, 0), self.O)]),                 [self.place(10, 64, 10), self.place(10, 65, 10)], "bottom-up regardless of list order"),
            (dict(parts=[Part((0, 0, 0), self.O)], blocks=leaves),                           [mine_leaves, self.place(10, 64, 10)],          "foliage cleared first"),
            (dict(parts=[Part((0, 0, 0), self.O)], blocks=leaves, protected=[(11, 65, 10)]), [self.place(10, 64, 10)],                       "negative: protected foliage kept"),
            (dict(parts=[Part((1, 0, 0), "minecraft:furnace", facing="north")], turns=1),    [self.place(10, 64, 11, "minecraft:furnace", facing="east")], "offset and facing rotate"),
            (dict(parts=[Part((0, 0, 0), "minecraft:hopper", facing="down", against=(0, -1, 0))]),
             [self.place(10, 64, 10, "minecraft:hopper", against={"x": 10, "y": 63, "z": 10})],                                               "against wins over facing"),
            (dict(parts=[Part((0, 1, 0), self.O)]),                                          [self.place(10, 65, 10)],                       "boundary: one above feet needs no pillar"),
            (dict(parts=[Part((0, 2, 0), self.O)]),                                          [pillar, self.place(10, 66, 10)],               "two above feet: pillar once"),
            (dict(parts=[Part((0, 2, 0), self.O)], feet=(12, 64, 12)),                       [goto, pillar, self.place(10, 66, 10)],         "off the access column: walk there first"),
        ]
        for kw, want, why in rows:
            with self.subTest(why=why):
                self.assertEqual(self.cmds(**kw), want)

    def test_no_building_blocks(self):
        with self.assertRaises(NotAvailable):     # negative: a pillar with nothing to pillar with is refused
            self.cmds([Part((0, 2, 0), self.O)], inv={})

if __name__ == "__main__":
    unittest.main()
