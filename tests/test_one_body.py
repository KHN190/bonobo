"""One body, swept: whatever order the layers speak in, only one of them is ever driving, and whoever is
interrupted is always slower than whoever interrupts. Random sequences, so the sixteen layer pairs cover
themselves; the assertion is the invariant, not a scenario.
"""
import os
import random
import sys
import threading
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


if __name__ == "__main__":
    unittest.main()
