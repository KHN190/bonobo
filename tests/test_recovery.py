"""Offline tests for the recovery table and the abort conditions.

The table exists because the previous arrangement — an if/elif chain inside each fight skill — failed in two ways
that both ended runs: a trigger nobody had enumerated fell through and left the agent standing still while it was
hit, and the same trigger got different answers in different skills. So the properties worth asserting are not the
individual mappings but those two failures: there is always an answer, and there is only one answer.
"""
import unittest

from bonobo import recovery


class Lookup(unittest.TestCase):
    def test_every_trigger_has_an_answer(self):
        for needle, act, _ in recovery.TABLE:
            self.assertEqual(recovery.recovery_for(f"perception: {needle}"), act)

    def test_unknown_danger_still_answers(self):
        # The failure this table was written for: no match must never mean "carry on".
        self.assertEqual(recovery.recovery_for("something nobody enumerated"), recovery.DEFAULT)

    def test_no_reason_at_all_still_answers(self):
        self.assertEqual(recovery.recovery_for(None), recovery.DEFAULT)
        self.assertEqual(recovery.recovery_for(""), recovery.DEFAULT)

    def test_default_is_cover_not_standing_still(self):
        self.assertEqual(recovery.DEFAULT, "retreat_to_cover")

    def test_matching_is_case_insensitive(self):
        self.assertEqual(recovery.recovery_for("ENDERMAN"), "shake_enderman")

    def test_first_match_wins_and_is_stable(self):
        # An interrupt can name two dangers ("enderman in the breath"). Whichever the table prefers, it must prefer
        # it every time — the old chains disagreed between skills, which is how the same danger got two answers.
        reason = "claude: enderman"
        self.assertEqual(recovery.recovery_for(reason), recovery.recovery_for(reason))
        self.assertEqual(recovery.recovery_for(reason), "shake_enderman")

    def test_explain_gives_a_reason_with_the_action(self):
        act, why = recovery.explain("breath")
        self.assertEqual(act, "retreat_to_cover")
        self.assertTrue(why)

    def test_enderman_is_never_answered_by_hiding_in_the_hole(self):
        # A 2.9-block enderman does not fit in a 1×2 corridor, but it teleports and reaches into the mouth. The
        # answer is to break the aggro, never to climb into a hole beside it — a bench run died doing exactly that.
        self.assertNotEqual(recovery.recovery_for("enderman"), "retreat_to_cover")


class Aborts(unittest.TestCase):
    def test_every_interruptible_action_declares_when_to_give_up(self):
        for name in ("dig_tunnel", "place_bed", "reinforce", "shoot_crystal"):
            self.assertTrue(recovery.aborts_for(name), f"{name} has no abort conditions")

    def test_each_abort_says_what_to_do_next(self):
        # The half the skill contracts were missing: they could say "this took too long" but never "and now this".
        for name, entries in recovery.ABORTS.items():
            for condition, action in entries:
                self.assertTrue(condition and action, f"{name}: an abort with no action is a hang")

    def test_the_window_is_atomic(self):
        # 0.4 s is shorter than one perception round trip (98 ms measured, best case), so there is no moment inside
        # it at which aborting is possible. It either starts or it does not.
        self.assertEqual(recovery.aborts_for("fire_window"), [])

    def test_unknown_action_has_no_aborts(self):
        self.assertEqual(recovery.aborts_for("nonexistent"), [])


if __name__ == "__main__":
    unittest.main()
