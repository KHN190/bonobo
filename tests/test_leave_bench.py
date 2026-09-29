"""A free run never begins at a bench site: where the body is decides it (tools.leave_bench.bench_site)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.bench.core import ORIGIN, SITE_B  # noqa: E402
from bonobo.bench.runner import LEFTOVER_R  # noqa: E402
from bonobo.tools import leave_bench as lb  # noqa: E402

B = tuple(o + d for o, d in zip(ORIGIN, SITE_B))


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


if __name__ == "__main__":
    unittest.main()
