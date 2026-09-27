"""Reconcile: a goal's remainder is read from the world every round ({} = met) and only that is acted on. Progress
is never a counter: an interrupted run, a world changed behind our back, a repeated round all come out right."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import goals  # noqa: E402
from tests.world import inventory, snapshot, state  # noqa: E402


def drive(want, world, desired=None, interrupt_at=(), limit=50):
    """A fake world applying one action a round toward `want` ({item: n}): the remainder read each round
    (`desired(world)`, default goals.reconcile), one of the missing added. `interrupt_at`: rounds whose action is
    lost (an interrupt before it landed). Returns (actions applied, final world). Raises when the remainder never
    shrinks — a desired state that ignores the world."""
    desired = desired or (lambda w: goals.reconcile(want, w))
    world, applied = dict(world), 0
    for rnd in range(limit):
        rest = desired(world)
        if not rest:
            return applied, world
        if rnd in interrupt_at:
            continue                                  # interrupted: nothing landed, nothing counted
        item = sorted(rest)[0]
        world[item] = world.get(item, 0) + 1
        applied += 1
    raise RuntimeError("the remainder never emptied: a desired state that does not read the world")


class Reconcile(unittest.TestCase):
    def test_remainder(self):
        rows = [("nothing held", {"log": 4}, {}, {"log": 4}),
                ("part held", {"log": 4, "stone": 3}, {"log": 1, "stone": 3}, {"log": 3}),
                ("more than asked: met", {"log": 4}, {"log": 9}, {}),
                ("exactly: met", {"log": 4, "stone": 3}, {"log": 4, "stone": 3}, {}),
                ("must fail: nothing asked is {}, never negative", {}, {"log": 2}, {})]
        for name, want, have, rest in rows:
            with self.subTest(name):
                self.assertEqual(goals.reconcile(want, have), rest)

    def test_goal_remainders_read_the_world(self):
        night, day = state(timeOfDay=18000), state(timeOfDay=2000)
        rows = [("have: 2 of 4 logs → 2", goals.have(("log", 4)), snapshot(day, inventory(("oak_log", 2))),
                 {"log": 2}),
                ("have: all held → met", goals.have(("log", 4)), snapshot(day, inventory(("oak_log", 4))), {}),
                ("have a pickaxe tier 1, a wooden one held → the tool", goals.have(("tool", "pickaxe", 1)),
                 snapshot(day, inventory(("wooden_pickaxe", 1))), {"tool:pickaxe": 1}),
                ("sleep at night → the night", goals.make("sleep"), snapshot(night, inventory()), {"night": 1}),
                ("sleep by day → met", goals.make("sleep"), snapshot(day, inventory()), {}),
                ("a run-once goal: the world cannot say", goals.make("road", a=[0, 64, 0], b=[9, 64, 0]),
                 snapshot(day, inventory()), None)]
        for name, goal, snap, want in rows:
            with self.subTest(name):
                self.assertEqual(goals.remainder(goal, snap, None), want)

    def test_every_goal_kind_declares_its_desired_state(self):
        self.assertEqual(set(goals.TEMPLATES) - set(goals.DESIRED), set())
        saved = dict(goals.DESIRED)
        try:
            del goals.DESIRED["sleep"]
            with self.assertRaises(TypeError):          # must fail: a kind without one is refused
                goals._registered()
        finally:
            goals.DESIRED.clear()
            goals.DESIRED.update(saved)

    def test_idempotent(self):
        """Met is met: reconciling again, twice, does nothing."""
        want = {"log": 4, "stone": 3}
        for name, world in (("already there", {"log": 4, "stone": 3}), ("more than there", {"log": 7, "stone": 5})):
            with self.subTest(name):
                self.assertEqual(drive(want, world), (0, world))
                self.assertEqual(drive(want, drive(want, world)[1]), (0, world))

    def test_interrupted_at_every_step(self):
        """An interrupt at any round k: the same final world, and every action applied exactly once."""
        want = {"log": 4, "stone": 3}
        clean_n, clean = drive(want, {})
        for k in range(clean_n + 1):
            with self.subTest(k=k):
                n, world = drive(want, {}, interrupt_at={k})
                self.assertEqual((n, world), (clean_n, clean))

    def test_the_world_changed_behind_our_back(self):
        """Blocks (items) taken away after it was met: the remainder grows back, and only by what went."""
        want = {"log": 4}
        n, world = drive(want, {})
        world["log"] -= 3                               # someone took three
        self.assertEqual(goals.reconcile(want, world), {"log": 3})
        self.assertEqual(drive(want, world), (3, {"log": 4}))

    def test_a_desired_state_that_ignores_the_world_never_converges(self):
        """Must fail: a remainder that does not read the world is never emptied by acting on it."""
        with self.assertRaises(RuntimeError):
            drive({"log": 4}, {}, desired=lambda w: {"log": 4})


if __name__ == "__main__":
    unittest.main()
