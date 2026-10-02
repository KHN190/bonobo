"""Torches where work stays in the dark (a tunnel, a cave, a vein, a base underground), lit first then per segment;
never the surface, a short shaft or a sealed night hole. Darkness is upkeep, not a danger signal."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import survive  # noqa: E402
from bonobo.data import DAY_END  # noqa: E402
from bonobo.game import COVERED_SKY  # noqa: E402


def st(sky, block=0, time_of_day=DAY_END + 1):
    return {"skyLight": sky, "blockLight": block, "timeOfDay": time_of_day}


class LightDue(unittest.TestCase):
    def test_rows(self):
        under, open_sky, wide = COVERED_SKY, COVERED_SKY + 1, survive.OPEN_DARK_SPOTS
        # (where, state, sealed, dark spots near) → light
        rows = [("a dark tunnel underground", st(under), False, wide, True),
                ("must fail: the surface at night", st(open_sky), False, wide, False),
                ("a short shaft: only its own floor dark", st(under), False, wide - 1, False),
                ("a sealed night hole", st(under), True, wide, False),
                ("already lit underground", st(under, block=1), False, wide, False)]
        for name, s, sealed, spots, want in rows:
            with self.subTest(name):
                self.assertEqual(survive.light_due(s, sealed, spots), want)


if __name__ == "__main__":
    unittest.main()
