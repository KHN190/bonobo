"""A cause cooled at a target (brain.failed: `pos`) must be read back from that target, not only from the body's
place (brain.wait_s) — else a target-bound cooling never holds (reselect every round, retry at once).

Maintain.shelter: a night way that fails must not look like a false "ok" (brain.attempt: fn() returned, no
exception) — the next untried way is tried in the same call; only every way failing is a real failure."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, reflexes, retry  # noqa: E402
from bonobo.api import McError, NotAvailable  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import brain_fixture, inventory, round_ctx, snapshot, state  # noqa: E402

HERE = (0.0, 64.0, 0.0)


class TargetCooldownIsReadBackAtTheTarget(unittest.TestCase):
    """(situation, the task asked about) → ready now? `fail_task` cools at ("target", pos), never at the body's
    own place (brain.failed); `ready` must check both, or a target-bound cooling never holds."""
    ROWS = [
        ("must fail: the failed task, at its target: not ready", "dig in", False),
        ("a different task, never failed: still ready (the cooling is this task's alone)", "wall in", True),
    ]

    def test_rows(self):
        for name, task, want in self.ROWS:
            with self.subTest(name), tempfile.TemporaryDirectory():
                b = brain_fixture()
                b.retry, b.place = retry.Retry(), retry.place_signature(HERE, False)
                b.round_snap = snapshot(state(), inventory())
                err = api.NotAvailable("not safe here", pos=(5, 64, 5))  # "unavailable" (EXCEPTIONS), BY_PLACE
                b.failed("dig in", err)
                self.assertEqual(b.ready(task), want)


def _way(name, token, secs=1.0):
    step = Step("shelter", token, 1, {})
    step.est = 0
    return name, secs, [step]


class ShelterTriesTheNextWayInsteadOfAFalseOk(unittest.TestCase):
    def test_a_failed_way_is_replaced_by_the_next_cheapest_in_the_same_call(self):
        """dig in: "not safe here"; wall in: offered and works — the same shelter() call returns it, not None."""
        with tempfile.TemporaryDirectory():
            b = brain_fixture()
            b.retry, b.place = retry.Retry(), retry.place_signature(HERE, False)
            snap = snapshot(state(), inventory())
            b.round_snap = snap
            b.needs = mock.Mock()
            b.needs.night_facts.return_value = {}
            b.needs.overnight.return_value = _way("wall in", "pod")
            runs = {"dig_in": mock.Mock(side_effect=McError("not safe to dig here")),
                    "pod": mock.Mock(return_value="walled")}
            with mock.patch.dict(reflexes.SHELTER_RUN, runs, clear=True), \
                    mock.patch.object(reflexes, "PRICED_RUN", None):
                got = b.reflexes.shelter(snap, round_ctx(b, snap), _way("dig in", "dig_in"))
            self.assertEqual(got, "walled")
            runs["pod"].assert_called_once()

    def test_every_way_failing_raises_not_available_with_each_reason(self):
        """Nothing left to try: a real failure (NotAvailable), not a silent None — and it names every way's reason."""
        with tempfile.TemporaryDirectory():
            b = brain_fixture()
            b.retry, b.place = retry.Retry(), retry.place_signature(HERE, False)
            snap = snapshot(state(), inventory())
            b.round_snap = snap
            b.needs = mock.Mock()
            b.needs.night_facts.return_value = {}
            b.needs.overnight.return_value = (None, float("inf"), [])
            runs = {"dig_in": mock.Mock(side_effect=McError("no lid below the ground line"))}
            with mock.patch.dict(reflexes.SHELTER_RUN, runs, clear=True), \
                    mock.patch.object(reflexes, "PRICED_RUN", None):
                with self.assertRaises(NotAvailable) as caught:
                    b.reflexes.shelter(snap, round_ctx(b, snap), _way("dig in", "dig_in"))
            self.assertIn("no lid below the ground line", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
