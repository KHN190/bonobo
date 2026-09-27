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
            body.drive("plan", seen.action(body, "plan"), "work")
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
                faster = {layer for layer in LAYERS if arbiter.SCALES[layer] < arbiter.SCALES[victim]}
                self.assertIn(winner, faster, f"seed {seed}: {winner} interrupted {victim}")

    # (the layer whose answer is running, the layer that speaks from inside it) → does it cut in?
    NESTED = [(outer, inner, arbiter.SCALES[inner] < arbiter.SCALES[outer])
              for outer in LAYERS[:-1] for inner in LAYERS]

    def test_who_may_cut_into_a_running_answer(self):
        """Only a faster layer; never the answer's own layer (it would interrupt itself), never a slower one."""
        self.assertEqual(len(self.NESTED), 12)
        for outer, inner, cuts in self.NESTED:
            with self.subTest(outer=outer, inner=inner):
                body, ran = arbiter.Motion(), []

                def running(_inner=inner):
                    ran.append("outer")
                    taken, why = body.preempt(_inner, lambda: ran.append("inner"), "inner", worth_s=1e6, now=1.0)
                    ran.append(taken is not None)

                body.preempt(outer, running, "outer", worth_s=1e6, now=0.0)
                self.assertEqual(ran, ["outer", "inner", True] if cuts else ["outer", False])

    def test_nothing_running_means_anyone_may_speak(self):
        for layer in LAYERS[:-1]:
            with self.subTest(layer):
                body, ran = arbiter.Motion(), []
                taken, why = body.preempt(layer, lambda: ran.append(layer), "x", worth_s=1e6, now=0.0)
                self.assertEqual((taken, why, ran), ((layer, "x"), None, [layer]))


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

    # (the lease holder's layer, is it still paying, who asks next) → taken by the asker?
    LEASE = [("tactic", True, "safety", True), ("tactic", True, "reflex", True), ("tactic", True, "tactic", False),
             ("tactic", True, "plan", False), ("tactic", False, "tactic", True), ("tactic", False, "plan", True),
             ("safety", True, "tactic", False), ("safety", False, "tactic", True)]

    def test_the_lease_over_the_table(self):
        """It stands while answering pays; a faster layer takes it regardless; once it stops paying anyone may."""
        for holder, paying, asker, taken in self.LEASE:
            with self.subTest(holder=holder, paying=paying, asker=asker):
                body = arbiter.Motion()
                body.preempt(holder, lambda: None, "answer", worth_s=1e6, now=0.0, seen_at=0.0,
                             release=lambda p=paying: not p)
                self.assertEqual(body.owns("nav.go_to"), not paying, "the stray caller while the lease stands")
                got, why = body.preempt(asker, lambda: None, "next", worth_s=1e6, now=0.1, seen_at=0.1)
                self.assertEqual(got is not None, taken, why)

    # (the layer holding a lease, the path posted from outside it) → what the post answers, before the game is asked
    POSTS = [("tactic", "/task?wait=0", "failed"), ("tactic", "/stop", "failed"), ("safety", "/task?wait=0", "failed"),
             ("tactic", "/close", "sent"), (None, "/task?wait=0", "sent")]

    def test_a_refused_thread_is_told_so_not_robbed(self):
        from unittest import mock
        from bonobo import api
        for holder, path, want in self.POSTS:
            with self.subTest(holder=holder, path=path):
                body = arbiter.Motion()
                if holder:
                    body.preempt(holder, lambda: None, "answer", worth_s=1e6, now=0.0, release=lambda: False)
                with mock.patch.object(arbiter, "BODY", body), \
                        mock.patch.object(api, "api", return_value={"status": "sent"}):
                    r = api.post(path, {"type": "travel"})
                self.assertEqual(r["status"], want)
                if want == "failed":
                    self.assertEqual(r["message"], "body owned by the arbiter")
                api.INTERRUPT = None            # a safety preemption leaves its message: not for the next test


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
        box, seen = {}, []

        def stopped(path, body_=None):
            if path == "/stop":
                # A thread woken by this cancel is about to ask; the answer must already be "no".
                seen.append(box["body"].holder() is not None)
            return {"status": "succeeded", "message": "", "tasks": []}

        for layer in ("tactic", "safety", "reflex"):
            with self.subTest(layer), mock.patch.object(api, "post", side_effect=stopped):
                seen.clear()
                body = box["body"] = arbiter.Motion()
                body.preempt(layer, lambda: None, "answer", worth_s=1e6, now=0.0,
                             clear_first=True, release=lambda: False)
                self.assertEqual(seen, [True], "the body was announced taken only after the stop went out")
                api.INTERRUPT = None
        with mock.patch.object(api, "post", side_effect=stopped):
            seen.clear()
            box["body"] = arbiter.Motion()
            box["body"].preempt("tactic", lambda: None, "answer", worth_s=1e6, now=0.0, clear_first=False)
            self.assertEqual(seen, [], "no clear_first, no /stop")

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
