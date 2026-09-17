"""Reading the combat tape the mod records (jar ≥0.1.35).

Python polls /state at about 5 Hz, which is far too coarse to fit dragon-breath spread, head-sweep reach or knockback,
and the polling itself costs fight time. So the client keeps the last 20 s of per-tick combat frames in a ring buffer
and this module pulls them into a file. Nothing here decides anything: it is the tape, not the player. `combat_model`
reads the tape offline, the planner reads the model.
"""
import json
import os
import time

from . import api, paths

DIR = paths.data("combat-tapes")


def frames(since=-1):
    """Raw pull: every frame newer than `since` (a client tick). -1 = everything the buffer still holds.

    Frames are addressed by tick, never by wall clock: the tick counter is the only clock shared with the recorder,
    and a lag spike would silently shift a wall-clock window.
    """
    return api.get(f"/combat/frames?since={since}")


def available():
    """True when the running jar records combat frames (0.1.35+). Older jars have no such route."""
    try:
        frames(1 << 30)
        return True
    except Exception:
        return False


class Tape:
    """Frames accumulated across polls, overlap dropped. The buffer holds 20 s, so polling every ~8 s loses nothing;
    a longer gap loses the middle, and `gaps` records it rather than letting the tape look continuous."""

    def __init__(self, name):
        self.name = name
        self.frames = []
        self.gaps = []
        self.errors = []
        self._last = -1

    def poll(self):
        """One pull. Returns how many new frames landed, 0 when the game is unreachable.

        A recording must outlive the thing it records: the game restarting, a scenario crashing it, the HTTP port
        going quiet for a second must not throw away the frames already collected (a 330 s run died on its last poll
        and lost the lot).
        """
        try:
            got = frames(self._last).get("frames") or []
        except Exception as e:
            self.errors.append(str(e))
            return 0
        if got and self._last >= 0 and got[0]["tick"] > self._last + 1:
            self.gaps.append((self._last, got[0]["tick"]))
        for f in got:
            self.frames.append(f)
            self._last = max(self._last, f["tick"])
        return len(got)

    def last_tick(self):
        """The newest game tick this tape holds. Callers driving `/tick sprint` wait on this rather than on wall
        clock: it is the only measure of how much game actually ran."""
        return self._last

    def record(self, seconds, every=8.0):
        """Poll until `seconds` have passed. `every` stays well under the 20 s buffer so no frame ages out unseen."""
        end = time.time() + seconds
        while time.time() < end:
            self.poll()
            time.sleep(min(every, max(0.0, end - time.time())))
        self.poll()
        return self

    def save(self):
        os.makedirs(DIR, exist_ok=True)
        path = os.path.join(DIR, f"{self.name}-{time.strftime('%Y%m%d-%H%M%S')}.json")
        with open(path, "w") as f:
            json.dump({"name": self.name, "gaps": self.gaps, "frames": self.frames}, f)
        return path


def load(path):
    """A saved tape, for offline replay: {"name", "gaps", "frames"}."""
    with open(path) as f:
        return json.load(f)


def latest(name=None):
    """The newest saved tape (optionally of one scenario), or None."""
    try:
        names = sorted(n for n in os.listdir(DIR) if n.endswith(".json") and (not name or n.startswith(name + "-")))
    except OSError:
        return None
    return os.path.join(DIR, names[-1]) if names else None


def events(since=0, timeout_ms=1000):
    """Changes the game pushed, newer than `since` (an event sequence number). Blocks until one arrives or the
    timeout expires (jar ≥0.1.38).

    Why this exists: a GET round trip measures ~98 ms, and the shortest attack window on the tapes is 0.4 s. Polling
    for "has the phase changed yet" spends a quarter of the window finding out, and still reports a change that is on
    average 100 ms old. Waiting on the change itself costs nothing until it happens.
    """
    return api.get(f"/events?since={since}&timeout={int(timeout_ms)}")


class EventStream:
    """Sequential reader over the pushed events, keeping its own place.

    Reports a gap rather than hiding one: the game holds a bounded backlog, so a reader that falls far behind has
    missed changes and must re-read the world instead of assuming nothing happened.
    """

    def __init__(self):
        self.seq = 0
        self.missed = 0

    def poll(self, timeout_ms=1000):
        """The events since the last call, oldest first. Empty on a timeout — that is a keep-alive, not an error."""
        try:
            data = events(self.seq, timeout_ms)
        except Exception:
            return []
        oldest = data.get("oldest", 0)
        if self.seq and oldest > self.seq + 1:
            self.missed += oldest - self.seq - 1
        got = data.get("events") or []
        if got:
            self.seq = max(e["seq"] for e in got)
        else:
            self.seq = max(self.seq, data.get("latest", self.seq))
        return got

