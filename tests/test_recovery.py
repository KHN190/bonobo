"""Offline tests for the recovery table and the abort conditions.

The table exists because the previous arrangement — an if/elif chain inside each fight skill — failed in two ways
that both ended runs: a trigger nobody had enumerated fell through and left the agent standing still while it was
hit, and the same trigger got different answers in different skills. So what is asserted is the whole lookup over
every shape a reason arrives in, with the exact answer, and the whole abort table with its exact entries.
"""
import unittest

from bonobo import recovery

# (the interrupt reason as it arrives) → (action, why starts with)
LOOKUP = [
    ("enderman", "shake_enderman", "never trade hits"),
    ("ENDERMAN", "shake_enderman", "never trade hits"),                    # case does not matter
    ("  enderman  ", "shake_enderman", "never trade hits"),                # nor padding
    ("claude: enderman", "shake_enderman", "never trade hits"),            # a prefixed message ends with the kind
    ("perception: breath", "retreat_to_cover", "clouds pool"),
    ("airborne", "water_clutch", "flung by a take-off"),
    ("critical_health", "retreat_and_eat", "below the floor"),
    ("hostiles", "retreat_to_cover", "anything hostile"),
    ("stale", "retreat_to_cover", "perception older"),
    ("enderman in the breath", "retreat_to_cover", "unrecognised"),        # not a kind: the default, never a guess (must fail: not a kind, the default)
    ("something nobody enumerated", "retreat_to_cover", "unrecognised"),
    ("", "retreat_to_cover", "unrecognised"),
    (None, "retreat_to_cover", "unrecognised"),
    ("a: b: breath", "retreat_to_cover", "clouds pool"),                   # the last segment is the kind
]


class Lookup(unittest.TestCase):
    def test_every_reason_gets_exactly_this_answer(self):
        for reason, act, why in LOOKUP:
            with self.subTest(reason=reason):
                got_act, got_why = recovery.explain(reason)
                self.assertEqual(got_act, act)
                self.assertTrue(got_why.startswith(why), got_why)

    def test_every_row_of_the_table_is_reachable_by_its_kind(self):
        for kind, act, _ in recovery.TABLE:
            with self.subTest(kind):
                self.assertEqual(recovery.explain(kind)[0], act)
        self.assertEqual(len({k for k, _, _ in recovery.TABLE}), len(recovery.TABLE), "a kind listed twice")

    # A 2.9-block enderman does not fit in a 1×2 corridor, but it teleports and reaches into the mouth: the
    # answer is to break the aggro, never to climb into a hole beside it — a bench run died doing exactly that.
    # (kind) → is it answered by the corridor (the default)?
    COVER = [("enderman", False), ("airborne", False), ("critical_health", False), ("breath", True),  # must fail: an enderman is not answered by the corridor
             ("hostiles", True), ("stale", True), ("unheard of", True)]

    def test_which_kinds_are_answered_with_cover(self):
        self.assertEqual(recovery.DEFAULT, "retreat_to_cover")
        for kind, cover in self.COVER:
            with self.subTest(kind):
                self.assertEqual(recovery.explain(kind)[0] == recovery.DEFAULT, cover)


if __name__ == "__main__":
    unittest.main()
