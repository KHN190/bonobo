"""Which world this is, and dropping what belonged to the last one.

Everything the agent remembers about a place — sites and their structure snapshots, directives, wants, routes,
priorities, the decision tape — is about ONE save. Nothing in the data directory said which, so a new world
inherited the old one's memory: the agent walked five hundred blocks to repair a shelter from a world that no
longer existed, and the pool scored that walk against mining because both were just numbers.

The mod does not report a world id, so the save itself is the signature: the newest world folder under the
instance's `saves`, named by its folder and when it was created. What survives a change of world is what is not
about a place — the model's beliefs, the benches, and the logs.
"""
import json
import os
import time

from . import paths

FILE = paths.data("world.json", env="MC_WORLD")

# What is about one save, and nothing else. A file not listed here survives on purpose: beliefs.jsonl is what the
# agent has measured about mobs and itself, the logs are the record of what happened, and neither is a place.
WORLD_SCOPED = ("world-notes.json", "directives.json", "wants.json", "priorities.json", "route.json",
                "intent.json", "readiness.json", "decisions.jsonl", "ranking.jsonl", "track.jsonl",
                "handover.json")


def saves_dir(instance=None):
    instance = instance or os.environ.get("MC_INSTANCE", "")
    return os.path.join(os.path.expanduser(instance), "saves") if instance else None


def world_id(instance=None):
    """The world being played, as `folder:created`, or None when there is no save to look at. Not `signature`:
    `retry.signature` is the coarse world state a failure is counted against, and one name for two facts is how a
    caller ends up asking the wrong module.

    Created, not modified: a world is written to constantly while it is played, so mtime says only that somebody
    is playing. Birth time is what makes "the same world as last round" a question with an answer.
    """
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
    """Delete what belonged to the last world. Deleted, not archived: a stale snapshot kept anywhere is a snapshot
    something will read."""
    gone = []
    for name in names:
        path = paths.data(name)
        try:
            os.remove(path)
            gone.append(name)
        except OSError:
            pass
    return gone


def check(instance=None, always=True):
    """(world, dropped): the world being played, and the place-memory dropped before it starts.

    A restart is a new agent (user, 2026-09-18): it keeps the logs and `beliefs.jsonl` — what it has MEASURED,
    which is about mobs and about itself, not about a place — and nothing else. Sites, directives, wants, routes
    and the decision tape are all about one save, and carrying them over is how a five-hundred-block walk to
    repair a shelter from a dead world outscored mining.

    `always=False` falls back to dropping only when the world itself changed, for a caller that wants continuity.
    """
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
