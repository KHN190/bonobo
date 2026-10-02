"""fresh.check: a save's place-memory dropped only when another save is played."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import fresh, paths  # noqa: E402


def nbt_level(seed, key="seed"):
    """A level.dat's bytes (gzipped NBT) holding `seed` where Minecraft keeps it, among tags of other kinds."""
    import gzip
    import struct

    def named(tag, name, body):
        k = name.encode()
        return bytes([tag]) + struct.pack(">H", len(k)) + k + body
    seed_tag = named(4, key, struct.pack(">q", seed))
    gen = named(10, "WorldGenSettings", seed_tag + b"\0") if key == "seed" else seed_tag
    data = named(10, "Data", named(8, "LevelName", struct.pack(">H", 2) + b"hi")
                 + named(9, "ServerBrands", bytes([8]) + struct.pack(">i", 1) + struct.pack(">H", 6) + b"fabric")
                 + named(11, "Pos", struct.pack(">i", 2) + struct.pack(">ii", 1, 2)) + gen + b"\0")
    return gzip.compress(named(10, "", data + b"\0"))


def save(instance, name, seed=1, key="seed"):
    """The save written anew (Minecraft rewrites level.dat on every save: new times, same seed)."""
    os.makedirs(os.path.join(instance, "saves", name), exist_ok=True)
    level = os.path.join(instance, "saves", name, "level.dat")
    if os.path.exists(level):
        os.remove(level)
    with open(level, "wb") as f:
        f.write(nbt_level(seed, key))


class Check(unittest.TestCase):
    STORES = ("mechanisms.json", "world-notes.json", "milestones.json")

    def start(self, instance):
        """One bot start (supervise.sh): what it dropped, and the stores still there."""
        _world, dropped = fresh.check(instance)
        return dropped, [n for n in self.STORES if os.path.exists(paths.data(n))]

    def test_rows(self):
        # (why, the saves played in turn, the drop wanted at the last start)
        rows = [("first start: nothing to compare, kept", [("a", 1)], False),
                ("must fail: the same world restarted drops the home, doors and milestones", [("a", 1), ("a", 1)],
                 False),
                ("another save: dropped", [("a", 1), ("b", 2)], True),
                ("the same folder name, a new world (another seed): dropped", [("a", 1), ("a", 2)], True)]
        for why, played, dropped in rows:
            with self.subTest(why):
                tmp = tempfile.mkdtemp()
                instance = os.path.join(tmp, "instance")
                with mock.patch.dict(os.environ, {"MC_DATA": os.path.join(tmp, "data")}), \
                        mock.patch.object(fresh, "FILE", os.path.join(tmp, "data", "world.json")):
                    os.makedirs(paths.data())
                    for name, seed in played:
                        for n in self.STORES:
                            open(paths.data(n), "w").write("[]")
                        save(instance, name, seed)
                        gone, kept = self.start(instance)
                    self.assertEqual(sorted(gone), sorted(self.STORES) if dropped else [])
                    self.assertEqual(kept, [] if dropped else list(self.STORES))

    def test_the_old_id_of_this_folder_is_this_save(self):
        # must fail: the id's form changed (folder:created → folder:seed) and the first start dropped the home again
        tmp = tempfile.mkdtemp()
        instance = os.path.join(tmp, "instance")
        with mock.patch.dict(os.environ, {"MC_DATA": os.path.join(tmp, "data")}), \
                mock.patch.object(fresh, "FILE", os.path.join(tmp, "data", "world.json")):
            os.makedirs(paths.data())
            with open(fresh.FILE, "w") as f:
                json.dump({"world": "a:1790725089", "at": 0}, f)
            for n in self.STORES:
                open(paths.data(n), "w").write("[]")
            save(instance, "a", 5)
            self.assertEqual(self.start(instance), ([], list(self.STORES)))
            self.assertEqual(fresh.known(), "a:5")
            save(instance, "b", 6)
            self.assertEqual(sorted(self.start(instance)[0]), sorted(self.STORES))     # a real change still drops

    def test_the_seed_read(self):
        tmp = tempfile.mkdtemp()
        rows = [("WorldGenSettings.seed", "seed", -4242424242424242), ("an older save: RandomSeed", "RandomSeed", 7)]
        for why, key, seed in rows:
            with self.subTest(why):
                save(tmp, why, seed, key)
                self.assertEqual(fresh.level_seed(os.path.join(tmp, "saves", why, "level.dat")), seed)
        broken = os.path.join(tmp, "broken.dat")
        with open(broken, "wb") as f:
            f.write(b"not gzip")
        with self.assertRaises(OSError):            # a damaged save is said, never read as "no seed" (gzip: BadGzipFile)
            fresh.level_seed(broken)
        self.assertIsNone(fresh.level_seed(os.path.join(tmp, "none.dat")))

    def test_no_save_drops_nothing(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(fresh, "drop") as drop:
            self.assertEqual(fresh.check(tmp), (None, []))
        drop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
