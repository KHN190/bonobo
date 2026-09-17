"""One body, swept: whatever order the layers speak in, only one of them is ever driving, and whoever is
interrupted is always slower than whoever interrupts. Random sequences, so the sixteen layer pairs cover
themselves; the assertion is the invariant, not a scenario.
"""
import os
import random
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import arbiter  # noqa: E402

LAYERS = ("reflex", "safety", "tactic", "plan")


class Watcher:
    """Records who was driving when, and who was cut off by whom."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = []
        self.overlaps = []
        self.interruptions = []

    def action(self, body, layer):
        def run():
            with self.lock:
                if self.running:
                    self.overlaps.append((tuple(self.running), layer))
                self.running.append(layer)
            try:
                for _ in range(3):
                    with self.lock:
                        if self.running[-1] != layer:
                            self.interruptions.append((layer, self.running[-1]))
                            return
            finally:
                with self.lock:
                    if layer in self.running:
                        self.running.remove(layer)
        return run


def sweep(seed, ticks=40):
    rng = random.Random(seed)
    body, seen = arbiter.Motion(), Watcher()
    now = 0.0
    for _ in range(ticks):
        now += rng.choice((0.0, 0.05, 0.3, 1.0))
        layer = rng.choice(LAYERS)
        if layer == "plan":
            body.submit("plan", seen.action(body, "plan"), "work", cost_rate=1.0, cost_s=100.0)
            body.step(now)
        else:
            body.preempt(layer, seen.action(body, layer), f"{layer} says so", worth_s=1e6, now=now)
    return body, seen


class OnlyOneDrives(unittest.TestCase):
    SEEDS = range(300)

    def test_two_layers_never_drive_at_once(self):
        for seed in self.SEEDS:
            _body, seen = sweep(seed)
            self.assertEqual(seen.overlaps, [], f"seed {seed}: two intents drove the body together")

    def test_whoever_is_cut_off_is_slower_than_who_cut_in(self):
        for seed in self.SEEDS:
            _body, seen = sweep(seed)
            for victim, winner in seen.interruptions:
                self.assertLess(arbiter.SCALES[winner], arbiter.SCALES[victim],
                                f"seed {seed}: {winner} interrupted the faster {victim}")

    def test_a_running_answer_is_not_cut_off_by_its_own_layer(self):
        body, ran = arbiter.Motion(), []

        def slow():
            ran.append("first")
            self.assertIsNone(body.preempt("tactic", lambda: ran.append("second"), "again",
                                           worth_s=1e6, now=1.0),
                              "a tactic answer interrupted itself")

        body.preempt("tactic", slow, "first", worth_s=1e6, now=0.0)
        self.assertEqual(ran, ["first"])

    def test_a_faster_layer_may_still_cut_in(self):
        body, ran = arbiter.Motion(), []

        def slow():
            ran.append("tactic")
            self.assertIsNotNone(body.preempt("safety", lambda: ran.append("safety"), "lava",
                                              worth_s=1e6, now=1.0))

        body.preempt("tactic", slow, "position", worth_s=1e6, now=0.0)
        self.assertEqual(ran, ["tactic", "safety"])

    def test_nothing_running_means_anyone_may_speak(self):
        body, ran = arbiter.Motion(), []
        for layer in LAYERS[:-1]:
            self.assertIsNotNone(body.preempt(layer, lambda l=layer: ran.append(l), "x",
                                              worth_s=1e6, now=0.0))
        self.assertEqual(ran, list(LAYERS[:-1]))


class TheLease(unittest.TestCase):
    """A preemption holds the body until holding it stops being worth more than working — not for a guessed
    number of seconds. While it holds, nobody else's task goes out at all: not by courtesy, by refusal."""

    SEEDS = range(200)

    def test_only_the_holder_may_drive_while_a_lease_stands(self):
        for seed in self.SEEDS:
            rng = random.Random(seed)
            body, refused = arbiter.Motion(), []
            worth = [1.0]

            def answer():
                for _ in range(3):
                    worth[0] = rng.choice((1.0, 1.0, -1.0))
                    other = threading.Thread(target=lambda w=worth[0]: refused.append(
                        (w, body.owns("nav.go_to"))))
                    other.start()
                    other.join()

            body.preempt("tactic", answer, "answer", worth_s=1e6, now=0.0,
                         release=lambda: worth[0] <= 0)
            # A lease, once handed back, is not silently retaken: only the checks before the first release say
            # anything about it.
            while refused and refused[-1][0] <= 0:
                refused.pop()
            before = refused[:next((i for i, (w, _) in enumerate(refused) if w <= 0), len(refused))]
            self.assertEqual([w for w, allowed in before if allowed], [],
                             f"seed {seed}: another thread drove while answering still paid")

    def test_the_lease_ends_when_answering_stops_paying(self):
        body = arbiter.Motion()
        worth = [1.0]
        body.preempt("tactic", lambda: None, "answer", worth_s=1e6, now=0.0,
                     release=lambda: worth[0] <= 0)
        self.assertFalse(body.owns("nav.go_to"), "the lease should still stand")
        worth[0] = -1.0
        self.assertTrue(body.owns("nav.go_to"), "the lease should have been given back")

    def test_a_faster_layer_takes_the_lease_from_a_slower_one(self):
        body, ran = arbiter.Motion(), []
        body.preempt("tactic", lambda: ran.append("tactic"), "position", worth_s=1e6, now=0.0,
                     release=lambda: False)
        self.assertIsNotNone(body.preempt("safety", lambda: ran.append("safety"), "lava",
                                          worth_s=1e6, now=0.1))
        self.assertIsNone(body.preempt("plan", lambda: ran.append("plan"), "mine", worth_s=1e6, now=0.2))
        self.assertEqual(ran, ["tactic", "safety"])

    def test_a_refused_thread_is_told_to_re_plan_not_that_it_was_robbed(self):
        from bonobo import api
        body = arbiter.Motion()
        body.preempt("tactic", lambda: None, "answer", worth_s=1e6, now=0.0, release=lambda: False)
        arbiter.BODY, saved = body, arbiter.BODY
        try:
            r = api.post("/task?wait=0", {"type": "travel"})
            self.assertEqual(r["status"], "failed")
            self.assertIn("arbiter", r["message"])
        finally:
            arbiter.BODY = saved


class TwoRealThreads(unittest.TestCase):
    """The gap this file kept missing: everything here held in one thread, and the failure lived between two.

    A planner thread posts tasks in a loop, as the brain does; a watcher thread preempts at random moments, as
    perception does. The invariant is what the log kept violating — no task of anyone else's goes out while a
    lease stands. Taking the body and announcing it must be one step: while they were two, the planner woke from
    the /stop and posted again in the gap.
    """

    def test_the_lease_stands_before_the_stop_goes_out(self):
        """The exact gap: `clear_first` cancels the planner's task, which wakes it up. If the lease is attached
        after that, the planner posts again in between — legally — and the answer is replaced 0.05 s in, which is
        what the log showed for three fixes running."""
        from unittest import mock
        from bonobo import api
        body = arbiter.Motion()
        seen = []

        def stopped(path, body_=None):
            if path == "/stop":
                # A thread woken by this cancel is about to ask; the answer must already be "no".
                seen.append(body.holder() is not None)
            return {"status": "succeeded", "message": "", "tasks": []}

        with mock.patch.object(api, "post", side_effect=stopped):
            body.preempt("tactic", lambda: None, "answer", worth_s=1e6, now=0.0,
                         clear_first=True, release=lambda: False)
        self.assertEqual(seen, [True], "the body was announced taken only after the stop went out")

    def test_no_foreign_task_goes_out_during_a_lease(self):
        for seed in range(50):
            rng = random.Random(seed)
            body = arbiter.Motion()
            leaked, stop = [], threading.Event()
            held = threading.Event()

            def planner():
                while not stop.is_set():
                    if body.owns("api.post(/task)"):
                        if held.is_set():
                            leaked.append(1)
                    time.sleep(0)

            def answer():
                held.set()
                for _ in range(20):
                    time.sleep(0)
                held.clear()

            worker = threading.Thread(target=planner, daemon=True)
            worker.start()
            try:
                for _ in range(20):
                    time.sleep(rng.choice((0.0, 0.001)))
                    body.preempt("tactic", answer, "answer", worth_s=1e6, now=0.0,
                                 release=lambda: not held.is_set())
            finally:
                stop.set()
                worker.join(1.0)
            self.assertEqual(leaked, [], f"seed {seed}: a foreign task went out while the lease stood")


if __name__ == "__main__":
    unittest.main()
