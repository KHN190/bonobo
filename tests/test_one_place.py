"""Helpers that were written twice and now live in one place: a pure table each (normal, edge, must-fail)."""
import unittest

from bonobo import data


class CannotReach(unittest.TestCase):
    """data.cannot_reach: the cells a mod answer names as "cannot reach x, y, z" (end and skills read it)."""

    ROWS = [("one cell", "cannot reach 1, 64, -3", {(1, 64, -3)}),
            ("two cells in one answer", "2 of 3 steps failed: cannot reach 1, 2, 3; cannot reach -4, 5, -6",
             {(1, 2, 3), (-4, 5, -6)}),
            ("must fail: another refusal names no cell", "no path found (108 positions explored)", set()),
            ("must fail: no message", None, set()),
            ("must fail: a cell without the words", "stuck at 1, 2, 3", set())]

    def test_rows(self):
        for name, message, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(data.cannot_reach(message), want)

    def test_every_reader_uses_it(self):
        from bonobo import end
        failed = [{"status": "failed", "message": "cannot reach 1, 2, 3"},
                  {"status": "succeeded", "message": "cannot reach 9, 9, 9"}]
        self.assertEqual(end.unreachable(failed), {(1, 2, 3)})


class BagSlots(unittest.TestCase):
    """world.screen_slot and world.BAG_SLOTS: a bag slot's id in the screen a /click names; the bag's size behind
    every "free slots" (Inventory.free_slots)."""

    def test_screen_slot(self):
        from bonobo.world import screen_slot
        rows = [("hotbar first", 0, 36), ("hotbar last", 8, 44), ("the bag above it", 9, 9), ("the last bag slot", 35, 35),
                ("must fail: a hotbar slot is never clicked as itself", 3, 39)]
        for name, slot, want in rows:
            with self.subTest(name):
                self.assertEqual(screen_slot(slot), want)

    def test_free_slots(self):
        from tests.world import bag, inventory
        rows = [("empty", {}, 36), ("one stack", {"dirt": 1}, 35), ("two kinds", {"dirt": 1, "stone": 64}, 34),
                ("must fail: a full stack still takes one slot", {"stone": 64}, 35)]
        for name, counts, want in rows:
            with self.subTest(name):
                self.assertEqual(bag(inventory(**counts)).free_slots(), want)



class RoomClicks(unittest.TestCase):
    """skills.room_clicks: the throws that make room (crafting's result, a cache chest), the cheapest stacks first."""

    def test_rows(self):
        from bonobo.craft import room_clicks
        from bonobo.world import screen_slot
        from tests.world import bag, inventory
        slots = bag(inventory(rotten_flesh=5, cobblestone=10, diamond=3, poisonous_potato=2)).slots
        at = {s["id"].split(":")[1]: s["slot"] for s in slots}
        price = {"minecraft:rotten_flesh": 1, "minecraft:poisonous_potato": 1, "minecraft:cobblestone": 2,
                 "minecraft:diamond": 500}.get
        rows = [("one: a junk stack", 1, ["poisonous_potato"]),
                ("two: both junk stacks", 2, ["poisonous_potato", "rotten_flesh"]),
                ("none asked: nothing thrown", 0, []),
                ("must fail: never the diamond, even asked for three", 3, None)]
        for name, need, want in rows:
            with self.subTest(name):
                got = room_clicks(slots, need, price)
                self.assertLessEqual(len(got), need)
                self.assertNotIn(screen_slot(at["diamond"]), [c["slot"] for c in got])
                self.assertTrue(all(c["action"] == "THROW" for c in got))
                if want is not None:
                    self.assertEqual(sorted(c["slot"] for c in got), sorted(screen_slot(at[w]) for w in want))


class MemoTtl(unittest.TestCase):
    """data.memo_ttl: the one short-lived memo (perception's ground, needs' plan prices)."""

    def test_rows(self):
        # (situation, cache before, key, now, one) → (value returned, made again?, keys after)
        rows = [("fresh hit: kept value, nothing made", {"a": (10.0, "old")}, "a", 11.0, False, ("old", False, ["a"])),
                ("expired: made again", {"a": (10.0, "old")}, "a", 13.0, False, ("new", True, ["a"])),
                ("another key: made, both kept", {"a": (10.0, "old")}, "b", 11.0, False, ("new", True, ["a", "b"])),
                ("one slot: another key forgets the first", {"a": (10.0, "old")}, "b", 11.0, True, ("new", True, ["b"])),
                ("must fail: exactly at the ttl is stale", {"a": (10.0, "old")}, "a", 12.0, False, ("new", True, ["a"]))]
        for name, cache, key, now, one, want in rows:
            with self.subTest(name):
                made = []
                got = data.memo_ttl(cache, key, 2.0, lambda: made.append(1) or "new", now, one=one)
                self.assertEqual((got, bool(made), sorted(cache)), want)



class ReflexesReadTheRoundsLook(unittest.TestCase):
    """reflexes.in_sight: a reflex asks the round's one batched look (world.nearest), never /find while deciding."""

    def test_rows(self):
        from types import SimpleNamespace
        from unittest import mock
        from bonobo import reflexes, world
        seen = [{"block": "minecraft:red_bed", "distance": 30.0}, {"block": "minecraft:lava", "distance": 5.0},
                {"block": "minecraft:barrel", "distance": 4.0}]
        asked = []

        def get(path):
            asked.append(path)
            return {"blocks": seen}
        snap = SimpleNamespace(feet=(0, 64, 0), dimension="minecraft:overworld")
        rows = [("a bed 30 away, within 48", ["red_bed", "white_bed"], 48, True),
                ("a chest group: the barrel 4 away, within 6", ["chest", "barrel"], 6, True),
                ("must fail: lava 5 away is not within 3", ["lava"], 3, False),
                ("must fail: nothing of it seen", ["diamond_ore"], 48, False)]
        with mock.patch.object(world.api, "get", get), mock.patch.object(world, "_PER_BLOCK", [True]), \
                mock.patch.dict(world._SIGHT, {"key": None, "t": 0.0, "near": {}}):
            for name, kinds, radius, want in rows:
                with self.subTest(name):
                    self.assertEqual(reflexes.in_sight(snap, kinds, radius), want)
        self.assertEqual(len(asked), 1, "one look answers every reflex ask in the round")


if __name__ == "__main__":
    unittest.main()


def duplicate_defs(source):
    """Top-level functions and classes a module defines more than once (the later one silently shadows the first)."""
    import ast
    import collections
    names = collections.Counter(n.name for n in ast.parse(source).body
                                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    return sorted(k for k, v in names.items() if v > 1)


class OneNameOneDefinition(unittest.TestCase):
    """No module defines a top-level name twice: skills.make_room (a station spot dug in the world) was shadowed by a
    second make_room (room in the bag), and every one-argument call became a TypeError."""

    ROWS = [("two different names", "def a(x):\n    pass\ndef b(x):\n    pass\n", []),
            ("must fail: the same function twice", "def a(x):\n    pass\ndef a(x, y):\n    pass\n", ["a"]),
            ("must fail: a class and a function of one name", "class a:\n    pass\ndef a():\n    pass\n", ["a"]),
            ("a nested def of the same name is not top level",
             "def a():\n    def a():\n        pass\n    return a\n", [])]

    def test_rows(self):
        for name, source, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(duplicate_defs(source), want)

    def test_the_package(self):
        import glob
        import os
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")
        for path in sorted(glob.glob(os.path.join(root, "**", "*.py"), recursive=True)):
            with self.subTest(os.path.relpath(path, root)), open(path, encoding="utf-8") as f:
                self.assertEqual(duplicate_defs(f.read()), [])


def misplaced_skills(source):
    """Pure: the functions wearing @skill that are not skills — a skill's first parameter is the context (`ctx`);
    a helper slipped between a decorator and its def (skills.spent_cells above `def mine`, bbe599d) took the
    registration, and "mine" vanished from REGISTRY. Also any explicit name= that differs from the def's name."""
    import ast
    out = []
    for n in ast.parse(source).body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in n.decorator_list:
            call = d if isinstance(d, ast.Call) else None
            fn = call.func if call else d
            if not (isinstance(fn, ast.Name) and fn.id == "skill"):
                continue
            first = n.args.args[0].arg if n.args.args else None
            named = next((k.value.value for k in (call.keywords if call else [])
                          if k.arg == "name" and isinstance(k.value, ast.Constant)), None)
            if first != "ctx" or (named is not None and named != n.name):
                out.append(n.name)
    return out


class SkillsWearTheirOwnDecorator(unittest.TestCase):
    ROWS = [("a skill", "@skill(needs={})\ndef mine(ctx, token):\n    pass\n", []),
            ("a helper above the decorator: fine", "def spent(a):\n    pass\n@skill(needs={})\ndef mine(ctx):\n    pass\n",
             []),
            ("must fail: a helper between the decorator and its skill",
             "@skill(needs={})\ndef spent(sent, name_at):\n    pass\ndef mine(ctx):\n    pass\n", ["spent"]),
            ("must fail: a name= that is not the def's", "@skill(name='chop')\ndef mine(ctx):\n    pass\n", ["mine"])]

    def test_rows(self):
        for name, source, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(misplaced_skills(source), want)

    def test_the_package(self):
        import glob
        import os
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")
        for path in sorted(glob.glob(os.path.join(root, "**", "*.py"), recursive=True)):
            with self.subTest(os.path.relpath(path, root)), open(path, encoding="utf-8") as f:
                self.assertEqual(misplaced_skills(f.read()), [])


class WhatToHold(unittest.TestCase):
    """knowledge.tool_for / weapon_for: the one choice of the item a task holds (the jar picks none)."""

    def test_tool_for(self):
        from bonobo import knowledge
        from tests.world import bag as _bag, inventory
        bag = lambda *items: _bag(inventory(*items))     # noqa: E731
        rows = [("stone: the best pickaxe carried", bag(("stone_pickaxe", 1), ("diamond_pickaxe", 1)), "stone", None,
                 "minecraft:diamond_pickaxe"),
                ("dirt: the shovel, not the pickaxe", bag(("iron_pickaxe", 1), ("stone_shovel", 1)), "dirt", None,
                 "minecraft:stone_shovel"),
                ("a log: the axe", bag(("iron_axe", 1), ("iron_pickaxe", 1)), "oak_log", None, "minecraft:iron_axe"),
                ("leaves: the hand, whatever is carried", bag(("iron_axe", 1)), "oak_leaves", None, "hand"),
                ("must fail: stone, no pickaxe carried: the bare hand, named", bag(("iron_axe", 1)), "stone", None,
                 "hand"),
                ("must fail: diamond ore asks tier 2, only stone carried: the hand", bag(("stone_pickaxe", 1)),
                 "diamond_ore", 2, "hand")]
        for name, inv, block, tier, want in rows:
            with self.subTest(name):
                self.assertEqual(knowledge.tool_for(inv, block, tier), want)

    def test_weapon_for(self):
        from bonobo import knowledge
        from tests.world import bag as _bag, inventory
        bag = lambda *items: _bag(inventory(*items))     # noqa: E731
        rows = [("a sword over an axe", bag(("iron_axe", 1), ("stone_sword", 1)), "minecraft:stone_sword"),
                ("the strongest sword", bag(("wooden_sword", 1), ("diamond_sword", 1)), "minecraft:diamond_sword"),
                ("an axe when no sword", bag(("iron_axe", 1)), "minecraft:iron_axe"),
                ("must fail: nothing to fight with: the bare hand, named", bag(("cobblestone", 8)), "hand")]
        for name, inv, want in rows:
            with self.subTest(name):
                self.assertEqual(knowledge.weapon_for(inv), want)


class ToolWear(unittest.TestCase):
    """knowledge.usable (the jar's rule: remaining > 1) and knowledge.working (planning's replace-soon margin)."""

    def test_rows(self):
        from bonobo import knowledge
        # (situation, uses left) → (usable, working)
        rows = [("new", 250, (True, True)), ("at the margin: working", 3, (True, True)),
                ("two left: held, not working", 2, (True, False)),
                ("must fail: one left — the jar never holds it", 1, (False, False)),
                ("must fail: broken", 0, (False, False))]
        for name, left, want in rows:
            with self.subTest(name):
                self.assertEqual((knowledge.usable(left), knowledge.working(left)), want)
        self.assertLess(knowledge.TOOL_USABLE, knowledge.TOOL_WORKING)
