"""Which world this is, and dropping what belonged to the last one. Everything the agent remembers about a place — sites and their structure snapshots, the task queue, the round tape — is about ONE save. Nothing in the data directory said which, so a new world inherited the old one's memory: the agent walked five hundred blocks to repair a shelter from a world that no longer existed, because nothing said the site belonged to another save. The mod does not report a world id, so the save itself is the signature: the newest world folder under the instance's `saves`, named by its folder and its seed. What survives a change of world is what is not about a place — the benches and the logs."""

import json
import os
import time

from . import paths

FILE = paths.data("world.json", env="MC_WORLD")

# what belongs to one save; the logs survive on purpose
WORLD_SCOPED = ("world-notes.json", "tasks.json", "intent.json", "rounds.jsonl", "tape-mem", "track.jsonl",
                "milestones.json", "mechanisms.json", "homes.json")

def saves_dir(instance=None):
    instance = instance or os.environ.get("MC_INSTANCE", "")
    return os.path.join(os.path.expanduser(instance), "saves") if instance else None

def world_id(instance=None):
    """The world being played, as `folder:seed` (the newest save by its level.dat), or None when there is no save to
    look at. Minecraft rewrites level.dat on every save, so its times are no identity; the seed inside never changes."""

    where = saves_dir(instance)
    if not where or not os.path.isdir(where):
        return None
    worlds = []
    for name in os.listdir(where):
        level = os.path.join(where, name, "level.dat")
        if os.path.exists(level):
            worlds.append((os.stat(level).st_mtime, name, level))
    if not worlds:
        return None
    _seen, name, level = max(worlds)
    return f"{name}:{level_seed(level)}"

_NBT_SIZE = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}          # fixed-size tags: byte, short, int, long, float, double


def level_seed(path):
    """The world seed in a level.dat (gzipped NBT: Data.WorldGenSettings.seed, older saves Data.RandomSeed), or None
    when unreadable."""
    import gzip
    import struct
    try:
        with open(path, "rb") as f:
            raw = gzip.decompress(f.read())
    except (OSError, EOFError):
        return None
    found = {}

    def payload(tag, i, name):
        if tag in _NBT_SIZE:
            if tag == 4 and name in ("seed", "RandomSeed"):
                found.setdefault(name, struct.unpack(">q", raw[i:i + 8])[0])
            return i + _NBT_SIZE[tag]
        if tag in (7, 11, 12):                                 # byte, int, long arrays
            n = struct.unpack(">i", raw[i:i + 4])[0]
            return i + 4 + n * {7: 1, 11: 4, 12: 8}[tag]
        if tag == 8:
            return i + 2 + struct.unpack(">H", raw[i:i + 2])[0]
        if tag == 9:
            inner, n = raw[i], struct.unpack(">i", raw[i + 1:i + 5])[0]
            i += 5
            for _ in range(n):
                i = payload(inner, i, None)
            return i
        if tag == 10:
            while raw[i] != 0:
                t, k = raw[i], struct.unpack(">H", raw[i + 1:i + 3])[0]
                key = raw[i + 3:i + 3 + k].decode("utf-8", "replace")
                i = payload(t, i + 3 + k, key)
            return i + 1
        raise ValueError(f"NBT tag {tag}")

    try:
        k = struct.unpack(">H", raw[1:3])[0]
        payload(raw[0], 3 + k, None)
    except (ValueError, IndexError, struct.error):
        return None
    return found.get("seed", found.get("RandomSeed"))

ID_KIND = "seed"          # what the id after the folder is; a record without it holds the old `folder:created`


def record():
    try:
        with open(FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}

def known():
    return record().get("world")

def remember(world):
    paths.ensure(FILE)
    with open(FILE, "w") as fh:
        json.dump({"world": world, "id": ID_KIND, "at": time.time()}, fh)

def drop(names=WORLD_SCOPED):
    """Delete what belonged to the last world."""

    gone = []
    for name in names:
        path = paths.data(name)
        try:
            if os.path.isdir(path):
                import shutil
                shutil.rmtree(path)
            else:
                os.remove(path)
            gone.append(name)
        except OSError:
            pass
    return gone

def check(instance=None):
    """(world, dropped): the world being played, and the place-memory dropped when it is another save's."""

    world = world_id(instance)
    if world is None:
        return None, []
    rec = record()
    was = rec.get("world")
    if was == world:
        return world, []
    if was is not None and rec.get("id") != ID_KIND and was.split(":", 1)[0] == world.split(":", 1)[0]:
        remember(world)             # the old id (level.dat's time, rewritten each save) of this folder: the same save
        return world, []
    dropped = drop() if was is not None else []
    remember(world)
    return world, dropped

if __name__ == "__main__":
    name, dropped = check()
    print(json.dumps({"world": name, "dropped": dropped}))
