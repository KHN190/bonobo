"""One batch, every free furnace in reach: `skills.split_smelt` — (furnaces, items, fuel, items a fuel burns) →
[(furnace, items, fuel)]. Pure; the skill only walks the plan."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import skills  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402

A, B, C = (0, 64, 0), (2, 64, 0), (4, 64, 0)
FREE3 = [(A, "free"), (B, "free"), (C, "free")]

# (situation, furnaces, items, fuel, items per fuel) → the plan
ROWS = [
    ("three free furnaces, three items, coal to spare: one each", FREE3, 3, 3, 8, [(A, 1, 1), (B, 1, 1), (C, 1, 1)]),
    ("one furnace: all of it there", [(A, "free")], 3, 1, 8, [(A, 3, 1)]),
    ("one furnace busy with something else: skipped", [(A, "free"), (B, "busy"), (C, "free")], 3, 2, 8,
     [(A, 2, 1), (C, 1, 1)]),
    ("one coal lights one furnace: all three items in it", FREE3, 3, 1, 8, [(A, 3, 1)]),
    ("planks burn 1.5 items each: two planks cannot split three items, one furnace takes them", [(A, "free"),
     (B, "free")], 3, 2, 1.5, [(A, 3, 2)]),
    ("not enough fuel for all: as many as it burns", [(A, "free")], 5, 2, 1.5, [(A, 3, 2)]),
    ("more furnaces than items: only as many as items", FREE3, 2, 4, 8, [(A, 1, 1), (B, 1, 1)]),
    ("ten items over three: 4, 3, 3", FREE3, 10, 3, 8, [(A, 4, 1), (B, 3, 1), (C, 3, 1)]),
]
# (situation, furnaces, items, fuel, per) → the reason nothing is loaded
FAILS = [("no furnace at all", [], 3, 3, 8, "no free furnace within reach"),
         ("every furnace busy", [(A, "busy"), (B, "busy")], 3, 3, 8, "no free furnace within reach"),
         ("no fuel", FREE3, 3, 0, 8, "no fuel to burn"),
         ("boundary: fuel that burns less than one item", [(A, "free")], 3, 1, 0.5, "no fuel to burn")]


class SplitSmelt(unittest.TestCase):
    def test_split_over_the_table(self):
        for name, furnaces, n, fuel, per, want in ROWS:
            with self.subTest(name):
                self.assertEqual(skills.split_smelt(furnaces, n, fuel, per), want)

    def test_nothing_loaded_says_why(self):
        for name, furnaces, n, fuel, per, why in FAILS:
            with self.subTest(name):
                with self.assertRaises(NotAvailable) as caught:
                    skills.split_smelt(furnaces, n, fuel, per)
                self.assertEqual(str(caught.exception), why)



class JobsLoadedTogether(unittest.TestCase):
    """Three furnaces loaded in one second are three jobs: finishing one leaves the others (bench iron_ingots
    collected 2 of 3 — the ids were per second, so the first finish removed all of them)."""

    # (situation, jobs finished by their index in the order added, None: an id no job has) → items still pending
    ROWS = [("none finished: all three pending", [], 3),
            ("the first collected: two left", [0], 2),
            ("two collected: one left", [0, 2], 1),
            ("an unknown id: nothing removed", [None], 3),
            ("all collected: none left", [0, 1, 2], 0)]

    def test_finish_over_the_table(self):
        import os
        import tempfile
        from unittest import mock
        from bonobo import memory
        for name, finish, want in self.ROWS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(memory.time, "time", return_value=1000.0):
                m = memory.Memory(os.path.join(tmp, "notes.json"))
                jobs = [m.add_job("furnace", (i, 64, 0), "minecraft:overworld", "minecraft:iron_ingot", 1, 1014.0, [])
                        for i in range(3)]
                for k in finish:
                    m.finish_job(jobs[k]["id"] if k is not None else "furnace-1000")
                self.assertEqual(m.pending_outputs("minecraft:overworld").get("minecraft:iron_ingot", 0), want)

if __name__ == "__main__":
    unittest.main()


class JobReady(unittest.TestCase):
    """A furnace job is ready by the game's clock when it has one (the bench sprints it; a slow server lags it)."""
    T0 = 1000.0
    # (situation, job, tick now, wall now) → ready
    TABLE = [
        ("sprinted: ticks passed, the wall clock has not", {"ready_at": T0 + 15, "ready_tick": 520}, 1220, T0 + 3,
         True),
        ("lagging server: the wall says ready, the furnace has not cooked", {"ready_at": T0 + 15, "ready_tick": 520},
         400, T0 + 20, False),
        ("edge: exactly the tick", {"ready_at": T0 + 15, "ready_tick": 520}, 520, T0, True),
        ("an old jar (no gameTime): the wall clock", {"ready_at": T0 + 15, "ready_tick": 520}, None, T0 + 16, True),
        ("a job from before ticks were kept: the wall clock", {"ready_at": T0 + 15}, 99999, T0 + 1, False),
        ("must fail: neither clock reached", {"ready_at": T0 + 15, "ready_tick": 520}, 519, T0 + 14, False),
    ]

    def test_table(self):
        for why, job, tick, now, want in self.TABLE:
            with self.subTest(why):
                self.assertIs(skills.job_ready(job, tick, now), want)
