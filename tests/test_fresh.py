"""fresh.check: a save's place-memory dropped only when another save is played."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import fresh, paths  # noqa: E402


def save(instance, name):
    os.makedirs(os.path.join(instance, "saves", name), exist_ok=True)
    open(os.path.join(instance, "saves", name, "level.dat"), "w").close()


class Check(unittest.TestCase):
    STORES = ("mechanisms.json", "world-notes.json", "milestones.json")

    def start(self, instance):
        """One bot start (supervise.sh): what it dropped, and the stores still there."""
        _world, dropped = fresh.check(instance)
        return dropped, [n for n in self.STORES if os.path.exists(paths.data(n))]

    def test_rows(self):
        # (why, the saves played in turn, the drop wanted at the last start)
        rows = [("first start: nothing to compare, kept", ["a"], False),
                ("must fail: the same world restarted drops the home, doors and milestones", ["a", "a"], False),
                ("another save: dropped", ["a", "b"], True)]
        for why, played, dropped in rows:
            with self.subTest(why):
                tmp = tempfile.mkdtemp()
                instance = os.path.join(tmp, "instance")
                with mock.patch.dict(os.environ, {"MC_DATA": os.path.join(tmp, "data")}), \
                        mock.patch.object(fresh, "FILE", os.path.join(tmp, "data", "world.json")):
                    os.makedirs(paths.data())
                    for name in played:
                        for n in self.STORES:
                            open(paths.data(n), "w").write("[]")
                        save(instance, name)
                        os.utime(os.path.join(instance, "saves", name, "level.dat"))
                        gone, kept = self.start(instance)
                    self.assertEqual(sorted(gone), sorted(self.STORES) if dropped else [])
                    self.assertEqual(kept, [] if dropped else list(self.STORES))

    def test_no_save_drops_nothing(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(fresh, "drop") as drop:
            self.assertEqual(fresh.check(tmp), (None, []))
        drop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
