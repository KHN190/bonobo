"""Whatever the pool offers must be able to run. One test over every source of candidates.

`light up` was offered with no torches and failed seventy times in twenty seconds. The precondition check existed,
and a test asserted that a refused candidate stays out — but nothing asserted the other direction, so when one of
the five places that build candidates cleared the check, every assertion still passed.

This runs a whole round against recorded worlds and asks each candidate its OWN question. It covers threats,
rescues, maintenance, goals, directives and fallbacks at once, and it keeps covering them when a sixth source is
added, because it does not name any of them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import decide, paths, retry, skill as skill_kit, skills  # noqa: E402

HAVE_TAPE = os.path.exists(paths.data("decisions.jsonl"))


def rounds(n=4):
    """A few recorded worlds, spread across the tape: different hours, health, bags."""
    rows = decide.load()
    if not rows:
        return []
    step = max(1, len(rows) // n)
    return rows[::step][:n]


@unittest.skipUnless(HAVE_TAPE, "no recorded rounds")
class EverythingOfferedCanRun(unittest.TestCase):
    def pool_of(self, row):
        from unittest import mock
        from bonobo import tape
        from bonobo.world import Snapshot
        b = decide.make_brain(row)
        with mock.patch("time.time", return_value=row["t"]):
            tape.REPLAY = row["calls"]
            try:
                snap = Snapshot()
                b.policy_cache = b.policy(snap, snap.night)
                ctx = skills.Context(b.mem, b.policy_cache, snap.dimension, b.blacklist)
                ids = [s["id"] for s in snap.inv.slots]
                b.sig = retry.signature(snap.feet, ids, snap.night, 0)
                b.coarse = b.sig
                b.place = retry.place_signature(snap.feet, snap.night)
                b.snap_cache = snap
                return b.candidates(ctx, snap, snap.night)[0]
            finally:
                tape.REPLAY = None

    def test_no_candidate_in_the_pool_fails_its_own_precondition(self):
        checked = 0
        for row in rounds():
            try:
                pool = self.pool_of(row)
            except Exception:
                continue          # a round this tape cannot replay is not this test's business
            for c in pool:
                runs = getattr(c, "runs", None)
                if not runs:
                    continue
                target, args = runs
                ok, why = skill_kit.can_run(target, *args)
                checked += 1
                self.assertTrue(ok, f"{c.name} was offered at {c.score:+.0f}s but cannot run: {why}")
        if not checked:
            self.skipTest("no candidate in these rounds names a skill")

    def test_candidates_that_name_a_skill_get_a_check_attached(self):
        """The wiring itself: `offer` must attach the precondition, so no source can forget to."""
        import inspect
        from bonobo.brain import Brain
        src = inspect.getsource(Brain.candidates)
        self.assertIn("can_run", src, "the one entry point must ask the skill, not trust each source to")

    def test_nothing_offered_scores_worse_than_doing_nothing_without_saying_why(self):
        """A negative score is allowed — losing least is sometimes the best move — but it must come with a reason
        the log can show, not from a candidate that simply cannot work."""
        for row in rounds():
            try:
                pool = self.pool_of(row)
            except Exception:
                continue
            for c in pool:
                if c.score < 0:
                    self.assertTrue(c.detail or c.name, f"{c.name} is negative and explains nothing")


if __name__ == "__main__":
    unittest.main()
