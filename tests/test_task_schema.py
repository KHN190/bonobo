"""Every task dict the package writes carries the keys the jar's TaskFactory reads, and none it does not: a key
misspelled or missing is refused in the game ('missing item') or silently ignored (a use_item's "hand"/"hold_ms").
The jar's reader is parsed from its source (MC_MOD_SRC or the sibling checkout); skipped where it is not on disk."""
import ast
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTORY = os.path.join(os.environ.get("MC_MOD_SRC") or os.path.expanduser(
    "~/Desktop/code/minecraft-claude-bridge/anaka/src/main/java/dev/anaka"), "task", "TaskFactory.java")
READS = {"pos": ["x", "y", "z"], "itemId": ["item"], "optFacing": ["facing"], "avoidSet": ["avoid"],
         "onlySet": ["only"], "pattern": ["pattern"]}
REQUIRED = ("pos", "itemId", "integer", "pattern", "strings")


def jar_schema(source):
    """Pure: {task type: (required keys, optional keys)} from TaskFactory.create's switch."""
    body = source[source.index("return switch (type)"):source.index("default ->")]
    out = {}
    for case in re.split(r'\n\s*case "', body)[1:]:
        name, req, opt = case[:case.index('"')], set(), set()
        for m in re.finditer(r'\b(\w+)\(o(?:,\s*"(\w+)")?', case):
            fn, key = m.group(1), m.group(2)
            if fn == "has":
                continue
            keys = READS.get(fn, [key] if key else [])
            guarded = re.search(r'o\.has\("\w+"\)\s*\?\s*' + re.escape(m.group(0)), case)
            (req if fn in REQUIRED and not guarded else opt).update(keys)
        opt |= set(re.findall(r'o\.has\("(\w+)"\)', case))
        out[name] = (req, opt - req)
    return out


def mismatches(schema, tree, where=""):
    """Pure: [(where:line, type, unknown keys, missing keys)] for every dict literal naming a jar task type. A dict
    spread with ** is not judged missing anything (its keys come from elsewhere)."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Dict):
            continue
        typed = next((v.value for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant) and k.value == "type"
                      and isinstance(v, ast.Constant)), None)
        if typed not in schema:
            continue
        keys = {k.value for k in n.keys if isinstance(k, ast.Constant)}
        req, opt = schema[typed]
        unknown = sorted(keys - req - opt - {"type"})
        missing = sorted(req - keys) if None not in n.keys else []
        if unknown or missing:
            out.append((f"{where}:{n.lineno}", typed, unknown, missing))
    return out


@unittest.skipUnless(os.path.exists(FACTORY), "the jar's source is not on disk")
class TaskSchema(unittest.TestCase):
    def test_the_package(self):
        schema = jar_schema(open(FACTORY).read())
        bad = []
        for path in sorted(glob.glob(os.path.join(ROOT, "bonobo", "**", "*.py"), recursive=True)):
            bad += mismatches(schema, ast.parse(open(path).read()), os.path.relpath(path, ROOT))
        self.assertEqual(bad, [])

    def test_rows(self):
        schema = jar_schema(open(FACTORY).read())
        # (situation, a dict) → caught
        rows = [("an attack with its entity", '{"type": "attack", "entity": 5, "shield": True}', False),
                ("a sweep round the body (its centre is optional)", '{"type": "collect", "radius": 4}', False),
                ("must fail: a use_item with no item and keys the jar never reads",
                 '{"type": "use_item", "hand": "offhand", "hold_ms": 1500}', True),
                ("must fail: a pillar with no item", '{"type": "pillar"}', True),
                ("must fail: a misspelled field", '{"type": "mine", "x": 1, "y": 2, "z": 3, "requireDrop": False}', True),
                ("a spread dict is not judged missing", '{"type": "place", **extra}', False)]
        for name, src, caught in rows:
            with self.subTest(name):
                self.assertEqual(bool(mismatches(schema, ast.parse(src))), caught)


if __name__ == "__main__":
    unittest.main()
