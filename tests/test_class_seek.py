"""K3: every known source used up is never the end of a way — a sourced step is priced as a search (Q2's
expected_find_s) for every need kind the planner's way table offers (knowledge.sources over the producers); and a
stand that holds for no way to a target fails AT that target (nav.reach_stand: `pos`), so it is banned and the next
search finds another (accept 20:29: the tree it found unbanned, re-found, the milestone "unavailable")."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered: the producing tables)
from bonobo import api, nav  # noqa: E402
from bonobo.cost import Cost  # noqa: E402
from bonobo.data import bare  # noqa: E402
from bonobo.knowledge import producers, sources, step_kinds  # noqa: E402
from bonobo.planner import way  # noqa: E402
from bonobo.world import Versioned  # noqa: E402
from tests.world import inventory, memory, snapshot, state  # noqa: E402

FOREVER = 1e18


def sourced_ways():
    """[(token, its source)] of every way the producers offer whose step walks to where its thing is."""
    out = []
    for g in producers():
        if g.kind not in Cost.SOURCED + ("fill",):
            continue
        for token, _row in g.rows():
            out += [(made, src) for made, src in sources(token) if src[0] == g.kind and (made, src) not in out]
    return out


def banned_everywhere(snap):
    bl = Versioned()
    for hits in snap.hits.values():
        for h in hits:
            bl[(h["x"], h["y"], h["z"])] = FOREVER
    for m in snap.mobs:
        bl[(m["id"], 0, 0)] = FOREVER
    return bl


class EveryKnownSourceBannedIsASearch(unittest.TestCase):
    def test_rows(self):
        ways = sourced_ways()
        self.assertGreater(len({src[0] for _t, src in ways}), 4)
        for token, src in ways:
            step = way(src, token, 1)[0]
            kinds = step_kinds(step)
            with self.subTest(f"{src[0]} {token}"):
                snap = snapshot(state(), inventory(), **({bare(kinds[0]): 4} if kinds else {}))
                cost = Cost(snap, memory(), banned_everywhere(snap))
                cost.estimate(step)
                # must fail: the banned source priced as a walk, or no way at all
                self.assertEqual(step.parts["walk"], 0)
                self.assertTrue(0 < step.parts["seek"] < math.inf, step.parts)


class AStandThatHoldsForNoWayFailsAtItsTarget(unittest.TestCase):
    def test_rows(self):
        target = (13013, 91, 13006)
        task = {"type": "mine", "x": target[0], "y": target[1], "z": target[2]}
        rows = [("no way planned", None),
                ("must fail: no stand after every way", [{"type": "goto", "x": 0, "y": 0, "z": 0}])]
        for name, steps in rows:
            with self.subTest(name), mock.patch.object(nav, "feet", return_value=(13014, 87, 13006)), \
                    mock.patch.object(nav, "_read_box", return_value=None), \
                    mock.patch.object(nav, "stands_for", return_value=False), \
                    mock.patch.object(nav, "stand_candidates", return_value=[]), \
                    mock.patch.object(nav, "plan_walks", return_value=[]), \
                    mock.patch.object(nav, "inventory_now", return_value=None), \
                    mock.patch.object(nav, "plan_way", return_value=(steps, "no tread", 0.0)), \
                    mock.patch.object(api, "run_chain", return_value=[]):
                with self.assertRaises(api.NavFailed) as got:
                    nav.reach_stand(task, nav.Policy())
                self.assertEqual(got.exception.pos, target)


if __name__ == "__main__":
    unittest.main()
