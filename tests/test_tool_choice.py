"""The one choice of what to hold (knowledge.cheapest_equal): of every candidate, the lowest tier whose time equals
the best — breaking by whole ticks (tool_for), attacking by time to kill (weapon_for). The expected pick
is computed here by the vanilla formula over the game's numbers (data), never written in."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import beliefs, data, knowledge as K, skillcore  # noqa: E402
from bonobo.world import Inventory  # noqa: E402


def bag(*items):
    return Inventory({"slots": [{"id": f"minecraft:{i}", "count": 1, "slot": n, "damage": 0, "maxDamage": 1000}
                                for n, i in enumerate(items)], "selectedSlot": 0, "equipment": {}})


def oracle_pick(timed):
    """(item, time, tier) rows → the item: the best time, the lowest tier among those tied."""
    best = min(t for _i, t, _tier in timed)
    return min((r for r in timed if r[1] == best), key=lambda r: r[2])[0]


def ticks(hardness, speed, right):
    """Minecraft Wiki, Breaking: per tick speed / hardness / (30 right for the drop, else 100); whole ticks."""
    per = speed / hardness / (30 if right else 100)
    return 0 if per >= 1 else math.ceil(1 / per)


def tier(material):
    return -1 if material is None else data.TIER_OF_MATERIAL[material]


class Chooser(unittest.TestCase):
    def test_rows(self):
        # (why, [(candidate, time, tier)]) → the oracle's pick; must-fail rows: a tie vs a one-tick gap
        rows = [("a tie: the lower tier", [("a", 5, 2), ("b", 5, 3)]),
                ("must fail: one tick apart, the faster though higher", [("a", 6, 2), ("b", 5, 3)]),
                ("the hand ties the tools: the hand", [("hand", 6, -1), ("a", 6, 0), ("b", 6, 2)])]
        for why, timed in rows:
            with self.subTest(why):
                times, tiers = {c: t for c, t, _ in timed}, {c: r for c, _, r in timed}
                self.assertEqual(K.cheapest_equal([c for c, _, _ in timed], times.get, tiers.get), oracle_pick(timed))


class Breaking(unittest.TestCase):
    # (why, block, [(item, its material, right tool kind for the block, right for the drop)])
    ROWS = [("stone: the fastest pickaxe", "stone",
             [("hand", None, False, False), ("wooden_pickaxe", "wooden", True, True),
              ("iron_pickaxe", "iron", True, True), ("diamond_pickaxe", "diamond", True, True)]),
            ("must fail: netherrack, iron and diamond one tick count — iron", "netherrack",
             [("iron_pickaxe", "iron", True, True), ("diamond_pickaxe", "diamond", True, True)]),
            ("axe on logs", "oak_log", [("hand", None, False, True), ("stone_axe", "stone", True, True),
                                         ("iron_axe", "iron", True, True), ("iron_pickaxe", "iron", False, True)]),
            ("shovel over hand on dirt", "dirt", [("hand", None, False, True), ("wooden_shovel", "wooden", True, True)]),
            ("snow: stone and iron shovels both at once — stone", "snow",
             [("hand", None, False, True), ("stone_shovel", "stone", True, True), ("iron_shovel", "iron", True, True)]),
            ("leaves: nothing faster than the hand but shears", "oak_leaves",
             [("hand", None, False, True), ("iron_pickaxe", "iron", False, True)])]

    def test_rows(self):
        for why, block, cands in self.ROWS:
            with self.subTest(why):
                h = data.HARDNESS.get(block) or K.hardness(block)
                timed = [(c if c == "hand" else f"minecraft:{c}",
                          ticks(h, data.TOOL_SPEED[m] if eff else 1.0, right), tier(m)) for c, m, eff, right in cands]
                got = K.tool_for(bag(*[c for c, _m, _e, _r in cands if c != "hand"]), block)
                self.assertEqual(got, oracle_pick(timed))

    def test_the_callers_delegate(self):
        inv = bag("iron_pickaxe", "stone_shovel")
        armed = skillcore.arm([{"type": "mine", "x": 0, "y": 64, "z": 0}, {"type": "travel", "x": 0, "y": 64, "z": 0}],
                              inv=inv, read_blocks=False)
        self.assertEqual(armed[0]["item"], K.tool_for(inv, None))
        self.assertNotIn("item", armed[1], "must fail: a walk names an item (I3: it breaks nothing)")


class Attacking(unittest.TestCase):
    ROWS = [("a weak mob: stone, iron and diamond two hits each — stone", "minecraft:ghast",
             ["stone_sword", "iron_sword", "diamond_sword"]),
            ("must fail: one hit fewer with diamond — diamond", "minecraft:zombie", ["iron_sword", "diamond_sword"]),
            ("a sword's quicker swing over an axe's harder hit", "minecraft:zombie", ["iron_axe", "iron_sword"])]

    def test_rows(self):
        for why, mob, weapons in self.ROWS:
            with self.subTest(why):
                hp = beliefs.mob(mob)["hp"]

                def kill(item):
                    material, _, kind = item.rpartition("_")
                    dmg, rate = (data.WEAPON_DAMAGE[kind][material], data.ATTACKS_PER_S[kind][material])
                    return math.ceil(hp / dmg) / rate
                timed = [(f"minecraft:{w}", kill(w), tier(w.rpartition("_")[0])) for w in weapons] + \
                        [("hand", math.ceil(hp / data.HAND_DAMAGE) / data.HAND_ATTACKS_PER_S, -1)]
                self.assertEqual(K.weapon_for(bag(*weapons), hp), oracle_pick(timed))


if __name__ == "__main__":
    unittest.main()
