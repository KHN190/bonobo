"""Skill shells (a name, a signature, an intent; the body raises) and the bag's spent-tool rule."""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import bag, combat, craft, end, farming, nav, skill  # noqa: E402

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")
# name → (module, args): each shell in its domain module
SHELLS = {"throw_ender_pearl": (end, (None, (0, 64, 0))), "shoot_bow": (combat, (None, 7)),
          "equip": (craft, (None, "minecraft:shield", "offhand")), "ride_boat": (nav, (None, (0, 64, 0))),
          "fish": (farming, (None, 30))}


class Shells(unittest.TestCase):
    def test_each_raises(self):
        for name, (mod, args) in SHELLS.items():
            with self.subTest(name), self.assertRaises(NotImplementedError):
                getattr(mod, name)(*args)

    def test_never_planned_nor_offered(self):
        """Not a registered skill (the planner and dispatch pick only those), and no module but its own names it."""
        for name, (mod, _a) in SHELLS.items():
            with self.subTest(name):
                self.assertNotIn(name, skill.REGISTRY, "must fail: a shell registered is a shell planned")
                own = os.path.basename(mod.__file__)
                callers = []
                for root, _d, files in os.walk(PKG):
                    for f in files:
                        if not f.endswith(".py") or (root == PKG and f == own):
                            continue
                        with open(os.path.join(root, f), encoding="utf-8") as fh:
                            tree = ast.parse(fh.read())
                        if any(getattr(n, "id", getattr(n, "attr", None)) == name for n in ast.walk(tree)
                               if isinstance(n, (ast.Name, ast.Attribute))):
                            callers.append(f)
                self.assertEqual(callers, [], "a shell is called from nowhere")


class SpentTools(unittest.TestCase):
    """bag: a diamond or netherite tool at 1 durability is kept (an anvil repairs it); others are thrown first."""

    @staticmethod
    def tool(item, left):
        return {"id": f"minecraft:{item}", "count": 1, "maxDamage": 100, "damage": 100 - left}

    def test_rows(self):
        # (situation, the stack) → (kept, thrown first by let_go)
        rows = [("must fail: a spent diamond pickaxe thrown", self.tool("diamond_pickaxe", 1), (True, False)),
                ("a spent netherite sword kept", self.tool("netherite_sword", 1), (True, False)),
                ("a spent iron pickaxe thrown first", self.tool("iron_pickaxe", 1), (False, True)),
                ("a spent diamond chestplate: armour, not a tool: thrown", self.tool("diamond_chestplate", 1),
                 (False, True)),
                ("a working stone axe kept", self.tool("stone_axe", 50), (True, False))]
        filler = {"id": "minecraft:dirt", "count": 64}
        for name, st, (keep, first) in rows:
            with self.subTest(name):
                self.assertEqual(st in bag.kept([st, filler]), keep)
                out = bag.let_go([st, filler], 1)
                self.assertEqual(bool(out) and out[0][0] is st, first)
