"""nav.walk_sweep/sweep_lying: a drop the collect radius couldn't take is chased to ITS OWN cell (the same
reach_stand/plan_way the gate uses, via way_to), never just retried blind from the feet (accept7 00:33:56-58's
"1 drop(s) left lying; sweeping again" -> "the drops lie out of reach": no stand check ever named the item's own
position, so the retry repeated the identical failed radius from the identical spot)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav  # noqa: E402


def _ctx():
    return type("Ctx", (), {"policy": object()})()


class WalkSweepNear(unittest.TestCase):
    """Unit: walk_sweep's own `near` fallback -- when the jar's Unreachable names no cells, it still tries the
    ones given (a caller's own /entities read), not the feet (a no-op: the same spot, the same failed radius).
    New in this fix: `near` doesn't exist on 6929151's walk_sweep, so this row can't even be collected there --
    the regression test below is the one that fails on the base by assertion, not by signature."""

    def test_near_cells_used_when_the_jar_names_none(self):
        calls, seen = {"n": 0}, []

        def fake_run(task, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise api.Unreachable("collect: 1 items unreachable", cells=())
            return {"type": "collect", "status": "succeeded", "message": "", "seconds": 1.0}

        def fake_way_to(ctx, cells, kind="mine"):
            seen.append(tuple(cells))
            return True

        with mock.patch.object(api, "run", fake_run), mock.patch.object(nav, "way_to", fake_way_to), \
             mock.patch.object(nav, "feet", lambda: (0, 64, 0)):
            got = nav.walk_sweep(_ctx(), radius=6, only=["minecraft:cobblestone"], near=[(5, 64, 9)])
        self.assertEqual(seen, [((5, 64, 9),)])
        self.assertIsNotNone(got)

    def test_the_jars_own_cells_still_win_over_near(self):
        """Pure precedence: a cell the jar itself names is trusted first; `near` is only the fallback."""
        seen = []

        def fake_run(task, **kw):
            if len(seen) == 0:
                raise api.Unreachable("collect: 1 items unreachable", cells=[(7, 64, 7)])
            return {"type": "collect", "status": "succeeded", "message": "", "seconds": 1.0}

        def fake_way_to(ctx, cells, kind="mine"):
            seen.append(tuple(cells))
            return True

        with mock.patch.object(api, "run", fake_run), mock.patch.object(nav, "way_to", fake_way_to), \
             mock.patch.object(nav, "feet", lambda: (0, 64, 0)):
            nav.walk_sweep(_ctx(), radius=6, only=["minecraft:cobblestone"], near=[(5, 64, 9)])
        self.assertEqual(seen, [((7, 64, 7),)])


class SweepReachesTheDropsOwnCell(unittest.TestCase):
    """Regression, through the production path (sweep_lying -> walk_sweep -> way_to -> reach_stand): a drop the
    batch's own sweep left lying is reached by its real /entities position. Must fail on 6929151 by assertion (not
    collection): there, sweep_lying never reads the drop's position into the retry at all, so `reach_stand` there
    is asked for the FEET's own cell (0, 64, 0) -- the same spot the first, failed collect already stood at --
    instead of the drop's actual (12960, 67, 12928); this test asserts the latter, which only this fix produces."""

    def test_production_path_reaches_the_drop(self):
        drop = {"type": "minecraft:item", "item": {"id": "minecraft:cobblestone", "count": 1},
                "x": 12960.4, "y": 67.0, "z": 12928.3}
        attempts, seen = {"n": 0}, []

        def fake_run(task, **kw):
            attempts["n"] += 1
            if task.get("type") == "collect" and attempts["n"] == 1:
                raise api.Unreachable("collect: 1 items unreachable", cells=())
            return {"type": "collect", "status": "succeeded", "message": "", "seconds": 1.0}

        def fake_reach_stand(task, policy, faces=None, at=None):
            seen.append((task["x"], task["y"], task["z"]))

        with mock.patch.object(api, "run", fake_run), mock.patch.object(nav, "reach_stand", fake_reach_stand), \
             mock.patch.object(nav, "feet", lambda: (0, 64, 0)), \
             mock.patch("bonobo.world.entities", lambda radius, types: [drop]):
            got = nav.sweep_lying(_ctx(), ["minecraft:cobblestone"], lambda: 1, radius=6)
        self.assertEqual(seen, [(12960, 67, 12928)])    # must fail on 6929151: there it's [(0, 64, 0)]
        self.assertEqual(got, 1)


if __name__ == "__main__":
    unittest.main()
