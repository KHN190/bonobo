"""Nether-only work is never offered outside the Nether (hello2 10:34-10:40: seek fortress and collect_blaze_rods ran
in the Overworld, 5 min waiting for no blaze), and a portal step goes only to a known portal — else one is cast first."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, brain, decompose, skill as skillkit  # noqa: E402,F401
from bonobo.planner import Step  # noqa: E402

OVER, NETHER = "minecraft:overworld", "minecraft:the_nether"


class Mem:
    def __init__(self, portals=()):
        self.portals = list(portals)

    def machines(self, dim, kind=None):
        return []

    def sites(self, dim, kinds=None):
        return [{"pos": p} for p in self.portals] if kinds == ["portal"] else []


class NetherOnly(unittest.TestCase):
    def test_rows(self):
        ctx = type("C", (), {"mem": Mem()})()
        # (skill, args, dimension) → may start
        rows = [("must fail: blaze rods in the Overworld", "collect_blaze_rods", (ctx, 6), OVER, False),
                ("must fail: a fortress sought in the Overworld", "find_fortress", (ctx,), OVER, False),
                ("a fortress sought in the Nether", "find_fortress", (ctx,), NETHER, True),
                ("must fail: a portal walked to with none known", "use_portal", (ctx, NETHER), OVER, False)]
        for name, skill_name, args, dim, want in rows:
            with self.subTest(name), mock.patch.object(api, "get", lambda p, d=dim: {"dimension": d}), \
                    mock.patch("bonobo.nether.find", lambda *a, **k: []):
                runner = skillkit.REGISTRY[skill_name].runner
                ok, why = skillkit.can_run(runner, *args)
                self.assertEqual(ok or ("not in" not in str(why) and "portal" not in str(why)), want, why)

    def test_cast_before_a_portal_none_known(self):
        rods = Step("hunt", "minecraft:blaze_rod", 6, {})
        for name, portals, first in [("no portal known: cast one first", [], ("cast", "nether_portal")),
                                     ("a portal known: walk to it", [(0, 64, 0)], ("portal", NETHER))]:
            with self.subTest(name):
                cost = type("Cost", (), {"snap": type("S", (), {"dimension": OVER})(), "mem": Mem(portals)})()
                with mock.patch.object(decompose, "_action", lambda kind, token, c, **d: Step(kind, token, 1, d)):
                    steps = decompose.where_it_lives([rods], cost)
                self.assertEqual((steps[0].kind, steps[0].token), first)


if __name__ == "__main__":
    unittest.main()
