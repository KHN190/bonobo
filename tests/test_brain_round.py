"""One whole `Brain.round` offline: only the game's transport (`api.api`) is stubbed, answering recorded shapes.

Everything above the wire — invariants, Snapshot, needs, the MAINTAIN reflexes, the arbiter — runs for real, so a
wiring crash in the round itself (a renamed object called like the old function) fails here, not in a bench run.
"""
import tempfile
import unittest
from unittest import mock

from bonobo import api, brain as brainmod, tape
from tests.world import inventory, state


class FakeGame:
    """Answers GETs from recorded readings (`tape`'s well-formed nothing for the rest) and notes every POST."""

    def __init__(self, st, inv):
        self.reads = {"/state": st, "/inventory": inv}
        self.posts = []

    def __call__(self, method, path, body=None, timeout=1200):
        if method == "POST":
            self.posts.append(path.split("?")[0])
            if path.startswith("/task"):
                return {"status": "succeeded", "type": (body or {}).get("type"), "message": "", "seconds": 0,
                        "result": {}}
            return {"status": "succeeded", "tasks": [], "results": []}
        key = path.split("?")[0]
        if key == "/state" and self.reads["/state"].get("dead") and "/respawn" in self.posts:
            return dict(self.reads["/state"], dead=False)
        if key == "/status":
            return {"inWorld": True, "features": []}
        return self.reads.get(key, dict(tape._EMPTY))


class Round(unittest.TestCase):
    # (situation, /state changes, bag, posted (must appear), not posted (must not appear))
    TABLE = [
        ("a quiet day, empty bag: the round runs to a decision", {}, {}, set(), {"/respawn", "/resume"}),
        ("dead: the invariants respawn before anything is decided", {"dead": True, "health": 0.0}, {},
         {"/respawn"}, {"/resume"}),
        ("the pause screen open: the invariants resume", {"screen": "class_433"}, {"dirt": 3}, {"/resume"},
         {"/respawn"}),
        ("edge: night with a pickaxe and food", {"timeOfDay": 18000}, {"stone_pickaxe": 1, "bread": 4}, set(),
         {"/respawn", "/resume"}),
        ("must fail: alive on no screen never respawns or resumes", {"food": 3}, {"bread": 2}, set(),
         {"/respawn", "/resume"}),
    ]

    def test_table(self):
        for why, changes, bag, posted, not_posted in self.TABLE:
            with self.subTest(why), tempfile.TemporaryDirectory() as tmp:
                game = FakeGame(state(**changes), inventory(**bag))
                with mock.patch.object(api, "api", game), mock.patch.object(api, "MODE", "normal"), \
                        mock.patch("bonobo.paths.instance_dir", return_value=tmp), \
                        mock.patch("time.sleep"):          # the game's clock is not ours to wait on offline
                    b = brainmod.Brain()
                    b.round()
                self.assertEqual(posted - set(game.posts), set(), game.posts)
                self.assertEqual(not_posted & set(game.posts), set(), game.posts)


if __name__ == "__main__":
    unittest.main()
