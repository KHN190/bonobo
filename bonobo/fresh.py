"""Which world this is, and dropping what belonged to the last one. Everything the agent remembers about a place — sites and their structure snapshots, the task queue, the round tape — is about ONE save. Nothing in the data directory said which, so a new world inherited the old one's memory: the agent walked five hundred blocks to repair a shelter from a world that no longer existed, because nothing said the site belonged to another save. The mod does not report a world id, so the save itself is the signature: the newest world folder under the instance's `saves`, named by its folder and when it was created. What survives a change of world is what is not about a place — the benches and the logs."""

import json
import os
import time

from . import paths

FILE = paths.data("world.json", env="MC_WORLD")

# what belongs to one save; the logs survive on purpose
WORLD_SCOPED = ("world-notes.json", "tasks.json", "intent.json", "rounds.jsonl", "tape-mem", "track.jsonl")

def saves_dir(instance=None):
    instance = instance or os.environ.get("MC_INSTANCE", "")
    return os.path.join(os.path.expanduser(instance), "saves") if instance else None

def world_id(instance=None):
    """The world being played, as `folder:created`, or None when there is no save to look at."""

    where = saves_dir(instance)
    if not where or not os.path.isdir(where):
        return None
    worlds = []
    for name in os.listdir(where):
        level = os.path.join(where, name, "level.dat")
        if os.path.exists(level):
            info = os.stat(level)
            worlds.append((info.st_mtime, name, int(getattr(info, "st_birthtime", info.st_ctime))))
    if not worlds:
        return None
    _seen, name, born = max(worlds)
    return f"{name}:{born}"

def known():
    try:
        with open(FILE) as fh:
            return json.load(fh).get("world")
    except (OSError, ValueError):
        return None

def remember(world):
    paths.ensure(FILE)
    with open(FILE, "w") as fh:
        json.dump({"world": world, "at": time.time()}, fh)

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

def check(instance=None, always=True):
    """(world, dropped): the world being played, and the place-memory dropped before it starts."""

    world = world_id(instance)
    if always:
        return world, drop()
    if world is None:
        return None, []
    was = known()
    if was == world:
        return world, []
    dropped = drop() if was is not None else []
    remember(world)
    return world, dropped

if __name__ == "__main__":
    name, dropped = check()
    print(json.dumps({"world": name, "dropped": dropped}))
