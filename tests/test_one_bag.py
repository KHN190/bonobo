"""One reader and one writer for the bag: world.Inventory is the only /inventory caller, bag.py builds the /click
bodies that drop a stack or move one (THROW, QUICK_MOVE), skillcore.arm alone chooses the item a task holds, and the
bench reads the bag through core.bag_now."""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MOVES = ("THROW", "QUICK_MOVE")
SELECTORS = ("tool_for", "weapon_for")     # which item a task holds: chosen in skillcore.arm only


def violations(source, path):
    """Pure: where `source` (at `path`, relative to the scripts folder) reads or writes the bag outside its one place."""
    tree = ast.parse(source)
    out = []
    owner = {}

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
                continue
            owner[child] = fn
            walk(child, fn)
    walk(tree, None)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if (called in SELECTORS and not (path == "bonobo/skillcore.py" and owner.get(node) == "arm")
                    and not path.startswith("bonobo/knowledge")):
                out.append(f"{path}:{node.lineno} chooses the held item itself (skillcore.arm chooses it)")
            first = node.args[0] if node.args else None
            if (isinstance(f, ast.Attribute) and f.attr == "get" and isinstance(first, ast.Constant)
                    and isinstance(first.value, str) and first.value.startswith("/inventory")
                    and path != "bonobo/world.py"):
                out.append(f"{path}:{node.lineno} reads /inventory (world.Inventory reads it)")
            if (path.startswith("bonobo/bench/") and isinstance(f, ast.Name) and f.id == "Inventory"
                    and not node.args and not node.keywords
                    and not (path == "bonobo/bench/core.py" and owner.get(node) == "bag_now")):
                out.append(f"{path}:{node.lineno} reads the bag itself (core.bag_now reads it)")
        if isinstance(node, ast.Dict) and path != "bonobo/bag.py":
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "action" and isinstance(v, ast.Constant)
                        and v.value in MOVES):
                    out.append(f"{path}:{node.lineno} builds a {v.value} click (bag.py builds them)")
    return out


def package_violations():
    out = []
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "bonobo")):
        for name in files:
            if name.endswith(".py"):
                path = os.path.relpath(os.path.join(folder, name), ROOT).replace(os.sep, "/")
                with open(os.path.join(ROOT, path)) as f:
                    out += violations(f.read(), path)
    return out


class OneBag(unittest.TestCase):
    """[E1][D3]"""
    def test_rows(self):
        # (source, path) → what breaks the rule
        rows = [("world reads /inventory", 'def f():\n    return api.get("/inventory")\n', "bonobo/world.py", []),
                ("must fail: a skill reads /inventory itself", 'def f():\n    return api.get("/inventory")\n',
                 "bonobo/store.py", ["bonobo/store.py:2 reads /inventory (world.Inventory reads it)"]),
                ("must fail: a THROW click built outside bag.py",
                 'x = {"slot": 3, "button": 1, "action": "THROW"}\n', "bonobo/craft.py",
                 ["bonobo/craft.py:1 builds a THROW click (bag.py builds them)"]),
                ("a PICKUP click is not a drop or a move", 'x = {"slot": 3, "button": 0, "action": "PICKUP"}\n',
                 "bonobo/craft.py", []),
                ("must fail: a bench word reads the bag itself", "def f():\n    return Inventory()\n",
                 "bonobo/bench/words/checks.py",
                 ["bonobo/bench/words/checks.py:2 reads the bag itself (core.bag_now reads it)"]),
                ("core.bag_now reads it", "def bag_now():\n    return Inventory()\n", "bonobo/bench/core.py", []),
                ("must fail: a skill picks its own weapon", "def f(inv):\n    return K.weapon_for(inv)\n",
                 "bonobo/combat.py", ["bonobo/combat.py:2 chooses the held item itself (skillcore.arm chooses it)"]),
                ("arm picks it", "def arm(inv):\n    return _know.weapon_for(inv)\n", "bonobo/skillcore.py", []),
                ("a recorded answer wrapped is no read", "def f(d):\n    return Inventory(d)\n",
                 "bonobo/bench/words/checks.py", [])]
        for name, source, path, want in rows:
            with self.subTest(name):
                self.assertEqual(violations(source, path), want)

    def test_the_package(self):
        self.assertEqual(package_violations(), [])


if __name__ == "__main__":
    unittest.main()
