"""The supervisor wakes on what matters in events.jsonl — death, task stuck, slow round, an error or refusal loop,
idle — and not on a single hiccup. Rows are written by the production emitters."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import events, paths  # noqa: E402
from bonobo.tools import wake  # noqa: E402
from check.round import renew_session  # noqa: E402

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
                renew_session()
                out = []
                write(out)
                why = wake.wake_reason(out, T + after, IDLE_S)
                self.assertEqual(why is not None, wakes, f"{name}: {why}")

    def test_frozen(self):
        # (detail.log silent seconds, the player holds control) → wakes; must fail: a stale log, control active, silent
        rows = [("must fail: silent past the limit, the agent driving", wake.api.FROZEN_S * 2, False, True),
                ("silent, under the limit", wake.api.FROZEN_S / 2, False, False),
                ("silent, the player holding control", wake.api.FROZEN_S * 2, True, False)]
        for name, silent, paused, wakes in rows:
            with self.subTest(name):
                self.assertEqual(wake.frozen(silent, paused) is not None, wakes)

    def test_silence_counts_from_the_start(self):
        # must fail: a log left from a much earlier run must not be taken as this run's own silence
        now = 1000.0
        rows = [("an old log at start: from the start", now - 190653, now - 5, 5.0),
                ("a log written in this run", now - 20, now - 300, 20.0)]
        for name, mtime, start, want in rows:
            with self.subTest(name):
                self.assertEqual(wake.silence(now, mtime, start), want)
                self.assertIsNone(wake.frozen(wake.silence(now, now - 190653, now - 5), False))

    def test_a_wake_never_ends_supervise(self):
        # must fail: supervise.sh exits (or breaks its watch) on a wake or the check-in — the bot left unwatched
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "supervise.sh")
        with open(path) as f:
            lines = f.read().splitlines()
        loop = lines[next(i for i, l in enumerate(lines) if l.startswith("while :")):]
        loop = loop[:next(i for i, l in enumerate(loop) if l == "done")]
        wakes = [i for i, l in enumerate(loop) if not l.lstrip().startswith("#")
                 and ("bonobo.tools.wake \"$OFFSET\"" in l or "check-in" in l)]
        self.assertEqual(len(wakes), 2)
        for i in wakes:
            block = loop[i:i + 4] if loop[i].lstrip().startswith("if ") else [loop[i]]
            self.assertFalse([l for l in block if "exit" in l or "break" in l], block)

    def test_the_watchdog_writes_every_stack_once_per_silence(self):
        # must fail: a stuck brain's silence passes with no stack written (py-spy was needed)
        import threading
        from unittest import mock
        from bonobo import api, brain
        said, stop = [], threading.Event()
        limit = 0.04

        def detail(*p):
            said.append(" ".join(map(str, p)))
            if len([s for s in said if s.startswith("!! frozen")]) >= 1:
                stop.set()
        with mock.patch.object(api, "detail", detail), mock.patch.object(api, "game_status", lambda: {"paused": False}), \
                mock.patch.object(api, "LAST_DETAIL", 0.0):
            brain.watchdog(stop, limit)
        self.assertTrue(any(s.startswith("!! frozen") for s in said))
        self.assertTrue(any("stack of MainThread" in s for s in said), said[:2])

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
