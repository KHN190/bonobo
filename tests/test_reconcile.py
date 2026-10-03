"""Reconcile: a goal's remainder is read from the world every round ({} = met) and only that is acted on. Progress
is never a counter: an interrupted run, a world changed behind our back, a repeated round all come out right."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import goals, memory  # noqa: E402
from tests.world import inventory, snapshot, state  # noqa: E402


def notes(*shelters_at):
    """A memory holding a shelter site at each of `shelters_at` (overworld)."""
    mem = memory.Memory(os.path.join(tempfile.mkdtemp(prefix="reconcile"), "notes.json"))
    for pos in shelters_at:
        mem.data["sites"].append({"name": f"shelter-{pos}", "kind": "shelter", "pos": list(pos),
                                  "dimension": "minecraft:overworld"})
    return mem


def walked(a, b):
    """A memory holding a travelled leg from `a` to `b` (overworld): the road noted."""
    mem = notes()
    mem.data.setdefault("roads", {})["minecraft:overworld"] = [{"a": list(a), "b": list(b), "s": 9.0, "used": 0}]
    return mem


DAY, NIGHT = state(timeOfDay=2000), state(timeOfDay=18000)
HERE = snapshot(DAY, inventory()).feet
# Every goal kind (goals.TEMPLATES): (goal, the world with it not done, the world with it done, the remainder when not
# done) — a world is (snapshot, memory). A run-once kind's remainder is None both ways: only its plan running can say.
# A kind missing here fails the sweep by name.
GOAL_LEFT = {
    "have": (goals.have(("log", 4)), (snapshot(DAY, inventory(("oak_log", 1))), notes()),
             (snapshot(DAY, inventory(("oak_log", 4))), notes()), {"log": 3}),
    "craft": (goals.make("craft", needs=[["minecraft:torch", 4]]), (snapshot(DAY, inventory(("torch", 1))), notes()),
              (snapshot(DAY, inventory(("torch", 6))), notes()), {"minecraft:torch": 3}),
    "milestone": (goals.make("milestone", name="stone tools"),
                  (snapshot(DAY, inventory(("stone_pickaxe", 1))), notes()),
                  (snapshot(DAY, inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("stone_axe", 1))), notes()),
                  {"tool:sword": 1, "tool:axe": 1}),
    "goto": (goals.make("goto", pos=[HERE[0] + 13, HERE[1], HERE[2]], range=2),
             (snapshot(DAY, inventory()), notes()),
             (snapshot(state(timeOfDay=2000, x=HERE[0] + 12.5, z=HERE[2] + 0.5), inventory()), notes()),
             {"blocks away": 10.0}),
    "road": (goals.make("road", a=[0, 64, 0], b=[9, 64, 0]), (snapshot(DAY, inventory()), notes()),
             (snapshot(state(timeOfDay=2000, x=9.5, z=0.5), inventory()), walked([0, 64, 0], [9, 64, 0])),
             {"road walked": 1, "blocks away": 3.0}),
    "build": (goals.make("build", bp="shelter", at=list(HERE)), (snapshot(DAY, inventory()), notes((500, 64, 500))),
              (snapshot(DAY, inventory()), notes(HERE)), {"built:shelter": 1}),
    "sleep": (goals.make("sleep"), (snapshot(NIGHT, inventory()), notes()), (snapshot(DAY, inventory()), notes()),
              {"night": 1}),
    "skill": (goals.make("skill", name="eat"), (snapshot(DAY, inventory()), notes()),
              (snapshot(DAY, inventory()), notes()), None),
    "effect": (goals.make("effect", name="fire_resistance"), (snapshot(DAY, inventory()), notes()),
               (snapshot(DAY, inventory()), notes()), None),
}


def reads_world(desired, goal, undone, done, rest):
    """The desired state `desired(goal, snap, mem)` says `rest` of the world not done and {} of the one done (a
    run-once kind: None of both, `rest` None)."""
    if rest is None:
        return desired(goal, *undone) is None and desired(goal, *done) is None
    return desired(goal, *undone) == rest and desired(goal, *done) == {}


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
                ("a road not walked: the leg and the way to its end", goals.make("road", a=[0, 64, 0], b=[9, 64, 0]),
                 snapshot(day, inventory()), {"road walked": 1, "blocks away": 3.0}),
                ("must fail: a run-once goal: the world cannot say", goals.make("skill", name="eat"),
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

    def test_every_goal_kind_reads_its_rest_off_the_world(self):
        self.assertEqual(set(goals.TEMPLATES) - set(GOAL_LEFT), set(), "goal kinds with no done/undone fixture")
        for kind in goals.TEMPLATES:
            goal, undone, done, rest = GOAL_LEFT[kind]
            with self.subTest(kind):
                self.assertEqual(goal["goal"], kind)
                self.assertTrue(reads_world(goals.remainder, goal, undone, done, rest), kind)

    def test_a_desired_state_that_ignores_the_world_is_caught(self):
        # must fail: a constant remainder (never met, always met, or "the world cannot say" for a kind it can) — the
        # sweep's own check says no; the kind's own desired state passes it
        goal, undone, done, rest = GOAL_LEFT["goto"]
        for name, fn, ok in [("must fail: always something left", lambda g, s, m: dict(rest), False),
                             ("must fail: always met", lambda g, s, m: {}, False),
                             ("must fail: a world goal said run-once", lambda g, s, m: None, False),
                             ("goto's own: reads the feet", goals.DESIRED["goto"], True)]:
            with self.subTest(name):
                self.assertEqual(reads_world(fn, goal, undone, done, rest), ok)

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


class SharedHelpers(unittest.TestCase):
    """goals.have_remainder / blocks_remainder: the one remainder math goals and skills share."""

    def test_have_remainder(self):
        from tests.world import bag
        rows = [("logs short", [["log", 4]], inventory(("oak_log", 1)), {"log": 3}),
                ("a tool short", [["tool", "pickaxe", 1]], inventory(), {"tool:pickaxe": 1}),
                ("must fail: all held", [["log", 2], ["tool", "pickaxe", 0]], inventory(("oak_log", 2), ("wooden_pickaxe", 1)), {}),
                ("food counts cooked meals only", [["food", 2]], inventory(("beef", 5), ("cooked_beef", 1)),
                 {"food": 1})]
        for name, need, inv, want in rows:
            with self.subTest(name):
                self.assertEqual(goals.have_remainder(bag(inv), need), want)

    def test_have_remainder_counts_what_is_on_its_way(self):
        # (situation, rows, bag, pending) → what is left: pending counted as held, per token, never below {}
        from tests.world import bag
        rows = [("nothing on its way", [["log", 4]], inventory(("oak_log", 1)), {}, {"log": 3}),
                ("part on its way", [["log", 4]], inventory(("oak_log", 1)), {"log": 2}, {"log": 1}),
                ("all on its way: met", [["log", 4]], inventory(), {"log": 5}, {}),
                ("must fail: another token's pending is not this one's", [["log", 4]], inventory(("oak_log", 1)),
                 {"stone": 9}, {"log": 3}),
                ("a tool is never pending", [["tool", "pickaxe", 1]], inventory(), {"tool:pickaxe": 1},
                 {"tool:pickaxe": 1})]
        for name, need, inv, pending, want in rows:
            with self.subTest(name):
                self.assertEqual(goals.have_remainder(bag(inv), need, pending), want)

    def test_blocks_remainder(self):
        want = {(0, 64, 0): "obsidian", (1, 64, 0): "obsidian", (0, 65, 0): "minecraft:obsidian"}
        rows = [("nothing built", {}, want),
                ("two placed", {(0, 64, 0): "obsidian", (1, 64, 0): "minecraft:obsidian"}, {(0, 65, 0): "minecraft:obsidian"}),
                ("all placed: met", {p: "obsidian" for p in want}, {}),
                ("must fail: the wrong block is not the block", {p: "stone" for p in want}, want),
                ("taken away behind our back: grows back", {(0, 64, 0): "obsidian"},
                 {(1, 64, 0): "obsidian", (0, 65, 0): "minecraft:obsidian"})]
        for name, world, rest in rows:
            with self.subTest(name):
                self.assertEqual(goals.blocks_remainder(want, world.get), rest)
