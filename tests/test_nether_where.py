"""Nether-only work is never offered outside the Nether (hello2 10:34-10:40: seek fortress and collect_blaze_rods ran
in the Overworld, 5 min waiting for no blaze), and a portal step goes only to a known portal — else one is cast first."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, brain, decompose, skill as skillkit  # noqa: E402,F401

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
        """The portal a Nether hunt needs first (the hunt contract's `when`): walked to when one is known, else cast
        (lava seen, the kit carried) before it."""
        from bonobo import goals
        from tests.test_sources import CAST_KIT, kinds, world
        kit = CAST_KIT + [("iron_sword", 1)]
        for name, w, first in [("no portal known: cast one first", dict(items=kit, seen=[("lava", (6, 60, 0))]),
                                ("cast", "nether_portal")),
                               ("a portal known: walk to it", dict(items=kit, sites=[("portal", (0, 64, 0), OVER)]),
                                ("portal", NETHER))]:
            with self.subTest(name):
                inv, cost = world(**w)
                got = kinds(decompose.decompose(inv, goals.have(("minecraft:blaze_rod", 6)), cost))
                places = [k for k in got if k[0] in ("cast", "build", "portal", "seek")]
                self.assertEqual(places[0], first)


if __name__ == "__main__":
    unittest.main()
