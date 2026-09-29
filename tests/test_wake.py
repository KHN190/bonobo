"""The supervisor wakes on what matters in events.jsonl — death, task stuck, slow round, an error or refusal loop,
idle — and not on a single hiccup. Rows are written by the production emitters."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import events  # noqa: E402
from bonobo.tools import wake  # noqa: E402

IDLE_S = 30.0
T = 1000.0


def times(fn, n):
    def run(out):
        for _ in range(n):
            fn(out)
    return run


# (name, write the session, seconds after T it is judged, wakes)
ROWS = [
    ("death", lambda o: events.death("zombie", "0 64 0", t=T, sink=o), 0, True),
    ("task stuck", lambda o: events.anomaly("task stuck", "mine: no progress", t=T, sink=o), 0, True),
    ("slow round", lambda o: events.round_time(events.SLOW_ROUND_S * 2, t=T, sink=o), 0, True),
    ("quick round", lambda o: events.round_time(events.SLOW_ROUND_S / 2, t=T, sink=o), 0, False),  # must fail
    ("error loop", times(lambda o: events.anomaly("exception round", "boom", t=T, sink=o), wake.REPEATED), 0, True),
    ("one error", lambda o: events.anomaly("exception round", "boom", t=T, sink=o), 0, False),       # must fail
    ("refusal loop", times(lambda o: events.anomaly("answer refused", "fight: x", t=T, sink=o), wake.REPEATED),
     0, True),
    ("one refusal", lambda o: events.anomaly("answer refused", "fight: x", t=T, sink=o), 0, False),  # must fail
    ("idle", lambda o: events.goal(events.IDLE_GOAL, t=T, sink=o), IDLE_S, True),
    ("idle, not yet", lambda o: events.goal(events.IDLE_GOAL, t=T, sink=o), IDLE_S / 2, False),     # must fail
    ("busy long", lambda o: events.goal("iron", t=T, sink=o), IDLE_S * 10, False),                   # must fail
    ("idle then work", lambda o: (events.goal(events.IDLE_GOAL, t=T, sink=o),
                                  events.goal("iron", t=T + 1, sink=o)), IDLE_S * 10, False),        # must fail
    ("hurt, ate", lambda o: (events.hurt(4.0, 16.0, "zombie", t=T, sink=o),
                             events.ate("bread", t=T, sink=o)), 0, False),                           # must fail
]


class Wake(unittest.TestCase):
    def test_rows(self):
        for name, write, after, wakes in ROWS:
            with self.subTest(name):
                events.reset_state()
                out = []
                write(out)
                why = wake.wake_reason(out, T + after, IDLE_S)
                self.assertEqual(why is not None, wakes, f"{name}: {why}")

    def test_read_from_offset_skips_half_line(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"t": T, "kind": "death", "line": "old"}) + "\n")
            start = f.tell()
            f.write(json.dumps({"t": T, "kind": "goal", "line": "goal: iron", "goal": "iron"}) + "\n")
            f.write('{"t": 1, "kind": "dea')                       # half written
        try:
            recs, _ = wake.read(f.name, start)
            self.assertEqual([r["line"] for r in recs], ["goal: iron"])    # must fail: the earlier session's death
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
