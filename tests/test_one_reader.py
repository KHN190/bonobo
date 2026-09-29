"""The jar's /entities combat fields are read in perception only (read_combat); the rest reads its view."""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bonobo import perception  # noqa: E402

READER = "bonobo/perception.py"


def indexes(source, path):
    """Pure: where `source` reads a combat field ('x["ignited"]', 'x.get("ignited")') outside the reader."""
    if path == READER:
        return []
    out = []
    for node in ast.walk(ast.parse(source)):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
              and node.args and isinstance(node.args[0], ast.Constant)):
            key = node.args[0].value
        if key in perception.COMBAT_KEYS:
            out.append(f"{path}:{node.lineno} reads {key!r}")
    return out


class OneReader(unittest.TestCase):
    def test_rows(self):
        rows = [("perception reads them", 'x = e.get("ignited")\n', READER, []),
                ("must fail: threat reads ignited", 'x = e.get("ignited")\n', "bonobo/threat.py",
                 ["bonobo/threat.py:1 reads 'ignited'"]),
                ("must fail: a subscript", 'x = e["tti_ticks"]\n', "bonobo/fight_loop.py",
                 ["bonobo/fight_loop.py:1 reads 'tti_ticks'"]),
                ("the view's own keys are fine", 'x = e.get("lit") or e["hit_s"]\n', "bonobo/threat.py", [])]
        for name, src, path, want in rows:
            with self.subTest(name):
                self.assertEqual(indexes(src, path), want)

    def test_the_package(self):
        bad = []
        for folder, _d, files in os.walk(os.path.join(ROOT, "bonobo")):
            for n in files:
                if n.endswith(".py"):
                    path = os.path.relpath(os.path.join(folder, n), ROOT).replace(os.sep, "/")
                    with open(os.path.join(ROOT, path)) as f:
                        bad += indexes(f.read(), path)
        self.assertEqual(bad, [])

    def test_read_combat(self):
        rows = [("a lit creeper", {"id": 1, "ignited": True, "fuse_ticks": 12}, {"id": 1, "provoked": False, "lit": True,
                                                                               "reach_now": False, "busy": False}),
                ("a ghast shooting, hit in 1 s at a point", {"id": 2, "shooting": True, "tti_ticks": 20,
                                                             "impact": {"x": 1, "y": 2, "z": 3}},
                 {"id": 2, "provoked": False, "lit": False, "reach_now": False, "busy": True, "hit_s": 1.0,
                  "impact_at": (1.0, 2.0, 3.0)}),
                ("must fail: attacking is provoked", {"id": 3, "attacking": True, "velocity": [0.1, 0, 0]},
                 {"id": 3, "provoked": True, "lit": False, "reach_now": False, "busy": False, "vel": (2.0, 0.0, 0.0)}),
                ("an old jar's row", {"id": 4, "angry": True}, {"id": 4, "angry": True, "provoked": True, "lit": False,
                                                               "reach_now": False, "busy": False})]
        for name, e, want in rows:
            with self.subTest(name):
                self.assertEqual(perception.read_combat([e]), [want])


if __name__ == "__main__":
    unittest.main()
