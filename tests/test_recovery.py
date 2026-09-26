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
    ("enderman in the breath", "retreat_to_cover", "unrecognised"),        # not a kind: the default, never a guess
    ("something nobody enumerated", "retreat_to_cover", "unrecognised"),
    ("", "retreat_to_cover", "unrecognised"),
    (None, "retreat_to_cover", "unrecognised"),
    ("a: b: breath", "retreat_to_cover", "clouds pool"),                   # the last segment is the kind
]


class Lookup(unittest.TestCase):
    def test_every_reason_gets_exactly_this_answer(self):
        for reason, act, why in LOOKUP:
            with self.subTest(reason=reason):
                self.assertEqual(recovery.recovery_for(reason), act)
                got_act, got_why = recovery.explain(reason)
                self.assertEqual(got_act, act, "explain and recovery_for disagree")
                self.assertTrue(got_why.startswith(why), got_why)

    def test_every_row_of_the_table_is_reachable_by_its_kind(self):
        for kind, act, _ in recovery.TABLE:
            with self.subTest(kind):
                self.assertEqual(recovery.recovery_for(kind), act)
        self.assertEqual(len({k for k, _, _ in recovery.TABLE}), len(recovery.TABLE), "a kind listed twice")

    # A 2.9-block enderman does not fit in a 1×2 corridor, but it teleports and reaches into the mouth: the
    # answer is to break the aggro, never to climb into a hole beside it — a bench run died doing exactly that.
    # (kind) → is it answered by the corridor (the default)?
    COVER = [("enderman", False), ("airborne", False), ("critical_health", False), ("breath", True),
             ("hostiles", True), ("stale", True), ("unheard of", True)]

    def test_which_kinds_are_answered_with_cover(self):
        self.assertEqual(recovery.DEFAULT, "retreat_to_cover")
        for kind, cover in self.COVER:
            with self.subTest(kind):
                self.assertEqual(recovery.recovery_for(kind) == recovery.DEFAULT, cover)


# (fight action) → its abort conditions, exactly: when to give up, and what to do instead
ABORTS = [
    ("dig_tunnel", [("health below the floor", "retreat_and_eat"), ("dragon perched", "retreat_to_cover"),
                    ("breath within 6", "retreat_to_cover")]),
    ("place_bed", [("dragon perched", "retreat_to_cover"), ("bed cell occupied", "abandon")]),
    ("reinforce", [("dragon perched", "retreat_to_cover"), ("out of obsidian", "abandon")]),
    ("shoot_crystal", [("enderman in the line of aim", "abandon"), ("dragon perched", "retreat_to_cover")]),
    ("fire_window", []),          # 0.4 s is shorter than a perception round trip: atomic, nothing to abort into
    ("nonexistent", []),
]
ANSWERS = {act for _, act, _ in recovery.TABLE} | {recovery.DEFAULT, "abandon"}


class Aborts(unittest.TestCase):
    def test_every_action_gives_up_exactly_so(self):
        for name, want in ABORTS:
            with self.subTest(name):
                self.assertEqual(recovery.aborts_for(name), want)

    def test_the_table_is_all_rows(self):
        self.assertEqual(set(recovery.ABORTS), {n for n, _ in ABORTS if n != "nonexistent"})

    def test_every_abort_answers_with_something_that_is_an_answer(self):
        # The half the skill contracts were missing: "this took too long" and never "and now this".
        for name, entries in recovery.ABORTS.items():
            for condition, action in entries:
                with self.subTest(name=name, condition=condition):
                    self.assertIn(action, ANSWERS)


if __name__ == "__main__":
    unittest.main()
