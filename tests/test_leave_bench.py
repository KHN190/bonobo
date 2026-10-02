"""A free run never begins at a bench site: where the body is decides it (tools.leave_bench.bench_site)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.bench.core import ORIGIN, SITE_B  # noqa: E402
from bonobo.bench.runner import LEFTOVER_R  # noqa: E402
from bonobo.tools import leave_bench as lb  # noqa: E402

B = tuple(o + d for o, d in zip(ORIGIN, SITE_B))



def settings_in(text):
    """Pure: the world settings a stretch of bench source changes — "gamerule <rule>", "tick rate", "difficulty" —
    with a {NAME} rule resolved over this package's constants (REGEN_RULE)."""
    import re
    out = set()
    for m in re.finditer(r"gamerule \{?([A-Za-z_]+)\}? ", text):
        out.add("gamerule " + _rule_constants().get(m.group(1), m.group(1)))
    out |= {k for k in ("tick rate", "difficulty") if re.search(rf"[\"'(]{k} \S", text)}
    return out


def _rule_constants():
    from bonobo.bench.words.brain import REGEN_RULE
    return {"REGEN_RULE": REGEN_RULE}


class BenchSite(unittest.TestCase):
    def test_rows(self):
        ow = lb.DIMENSION
        # (where, dimension) → the site it is at
        rows = [("on the arena floor", (ORIGIN[0] - 1, ORIGIN[1], ORIGIN[2]), ow, ORIGIN),
                ("at the arena's edge", (ORIGIN[0] + LEFTOVER_R, ORIGIN[1], ORIGIN[2]), ow, ORIGIN),
                ("site B", B, ow, B),
                ("must fail: past the leftover reach", (ORIGIN[0] - LEFTOVER_R - 1, ORIGIN[1], ORIGIN[2]), ow, None),
                ("must fail: natural terrain below the platform", (ORIGIN[0], lb.FLOOR_Y - 1, ORIGIN[2]), ow, None),
                ("must fail: the Nether at the same spot", ORIGIN, "minecraft:the_nether", None),
                ("must fail: the world spawn", (-537, 66, 59), ow, None)]
        for name, pos, dim, want in rows:
            with self.subTest(name):
                self.assertEqual(lb.bench_site(pos, dim), want)

    def test_commands_kill_leftovers_before_the_body(self):
        cmds = lb.leave_commands()
        kills = [i for i, c in enumerate(cmds) if "type=!player" in c]
        self.assertEqual(len(kills), len(lb.SITES))
        self.assertLess(max(kills), cmds.index(next(c for c in cmds if c.endswith("kill @p"))))


class BenchRestores(unittest.TestCase):
    """Every world setting the bench changes has a normal value to go back to (core.WORLD_NORMAL)."""

    def test_every_setting_the_bench_changes_is_restored(self):
        import glob
        from bonobo.bench import core
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = [f for f in glob.glob(os.path.join(root, "bonobo/bench/**/*.py"), recursive=True)
                 if not f.endswith("core.py")]
        changed = set(core.BENCH_WORLD).union(*(settings_in(open(f).read()) for f in files))
        self.assertIn("gamerule advance_time", changed)           # the reader sees the frozen clock
        self.assertEqual(changed - set(core.WORLD_NORMAL), set())
        restore = core.restore_commands()
        for k in changed:
            self.assertIn(f"{k} {core.WORLD_NORMAL[k]}", restore)

    def test_rows(self):
        from bonobo.bench import core
        # (a line of bench source) → every setting it changes is restored?
        rows = [("runner's clock", "f\"gamerule advance_time {x}\"", True),
                ("regen by its constant", "_chat(f\"gamerule {REGEN_RULE} false\")", True),
                ("tick rate", "_command(f\"tick rate {rate}\", feedback)", True),
                ("must fail: a rule the bench sets but restore misses", "_chat(\"gamerule keep_inventory true\")",
                 False)]
        for name, line, ok in rows:
            with self.subTest(name):
                got = settings_in(line)
                self.assertTrue(got)
                self.assertEqual(got <= set(core.WORLD_NORMAL), ok)


if __name__ == "__main__":
    unittest.main()
