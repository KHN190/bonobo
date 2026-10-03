"""Every known source used up is never the end of a way — a sourced step is priced as a search (expected_find_s)
for every need kind the planner's way table offers (knowledge.sources over the producers); and a
stand that holds for no way to a target fails AT that target (nav.reach_stand: `pos`), so it is banned and the next
search finds another (the tree it found unbanned, re-found, the milestone "unavailable")."""
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
from bonobo.skillcore import Ban  # noqa: E402


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
            bl[(h["x"], h["y"], h["z"])] = Ban(FOREVER)
    for m in snap.mobs:
        bl[(m["id"], 0, 0)] = Ban(FOREVER)
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


class AStepWhoseSourcesFailedHereSeeks(unittest.TestCase):
    """brain.next_step: a sourced step cooled here (its known sources failed) gives way to the search for its kinds,
    for every sourced kind the producers offer ("no step of the plan can run from here", the milestone
    failed)."""

    def test_rows(self):
        import tempfile
        from bonobo import brain as brainmod
        from bonobo.planner import Step
        from tests.test_plan_upkeep import Queue_
        from tests.world import round_ctx
        kinds = {src[0] for _t, src in sourced_ways()} - {"fill"}
        from bonobo.planner import runnable
        bag = __import__("tests.world", fromlist=["bag"]).bag(inventory())
        ways = [way(src, t, 1)[0] for t, src in sourced_ways()]
        # each kind's first way an empty bag can start (a tool it needs is an earlier step's)
        rows = [(k, next(st for st in ways if st.kind == k and runnable(st, bag))) for k in sorted(kinds)]
        for kind, step in rows:
            with self.subTest(kind), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                snap = snapshot(state(), inventory())
                q.b.failed(brainmod.step_key(step), api.NotAvailable("check: none found here"))
                got = q.b.next_step([step], snap, round_ctx(q.b, snap))
                # must fail: None — the plan given up while the world is unexplored
                self.assertEqual((got.kind, got.token, got.detail["kinds"]),
                                 ("seek", step.token, step_kinds(step)))
        self.assertIsNone(brainmod.seek_for(Step("craft", "minecraft:stick", 4, {})))


class AStandThatHoldsForNoWayFailsAtItsTarget(unittest.TestCase):
    def test_rows(self):
        target = (13013, 91, 13006)
        task = {"type": "mine", "x": target[0], "y": target[1], "z": target[2]}
        tread = (13013, 86, 13006)
        # (situation, the way planned, its why, the cell the failure names)
        rows = [("no way planned: the cell its why names", None, nav.Why("no tread", tread), tread),
                ("must fail: no stand after every way: the target", [{"type": "goto", "x": 0, "y": 0, "z": 0}],
                 "", target)]
        for name, steps, why, want in rows:
            with self.subTest(name), mock.patch.object(nav, "feet", return_value=(13014, 87, 13006)), \
                    mock.patch.object(nav, "_read_box", return_value=None), \
                    mock.patch.object(nav, "stands_for", return_value=False), \
                    mock.patch.object(nav, "stand_candidates", return_value=[]), \
                    mock.patch.object(nav, "plan_walks", return_value=[]), \
                    mock.patch.object(nav, "inventory_now", return_value=None), \
                    mock.patch.object(nav, "plan_way", return_value=(steps, why, 0.0)), \
                    mock.patch.object(api, "run_chain", return_value=[]):
                with self.assertRaises(api.NavFailed) as got:
                    nav.reach_stand(task, nav.Policy())
                self.assertEqual(got.exception.pos, want)


class EveryStepCooledSeeks(unittest.TestCase):
    """D1/E5: when every step of the plan cools here, the round seeks the plan's first source elsewhere (a state
    change lifts the coolings), never "nothing to do; waiting" while a source is in sight."""

    def test_table(self):
        from bonobo import dispatch
        from bonobo.planner import Step
        plan = [Step("mine", "minecraft:cobblestone", 11, {"blocks": ["stone"]}), Step("craft", "minecraft:stick", 4),
                Step("gather", "log", 1)]
        rows = [("the run's plan: the cobblestone's stone sought", plan, plan[0]),
                ("crafts only: nothing to seek (the craft's own retry)", [Step("craft", "minecraft:stick", 4)], None)]
        for name, steps, want in rows:
            with self.subTest(name):
                self.assertIs(dispatch.first_sought(steps), want)


if __name__ == "__main__":
    unittest.main()
