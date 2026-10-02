"""Reading the combat tape the mod records (jar ≥0.1.35). Python polls /state at about 5 Hz, which is far too coarse to fit dragon-breath spread, head-sweep reach or knockback, and the polling itself costs fight time. So the client keeps the last 20 s of per-tick combat frames in a ring buffer and this module pulls them into a file. Nothing here decides anything: it is the tape, not the player. `combat_model` reads the tape offline, the planner reads the model."""

import json
import os
import time

from . import api, paths

DIR = paths.data("combat-tapes")

def frames(since=-1):
    """Raw pull: every frame newer than `since` (a client tick)."""

    return api.get(f"/combat/frames?since={since}")

def available():
    """True when the running jar records combat frames (0.1.35+). Older jars have no such route."""
    try:
        frames(1 << 30)
        return True
    except (api.McError, OSError) as e:
        api.swallowed("combat_tape.available", e)
        return False

class Tape:
    """Frames accumulated across polls, overlap dropped."""

    def __init__(self, name):
        self.name = name
        self.frames = []
        self.gaps = []
        self.errors = []
        self._last = -1

    def poll(self):
        """One pull."""

        try:
            got = frames(self._last).get("frames") or []
        except (api.McError, OSError) as e:
            self.errors.append(str(e))
            return 0
        if got and self._last >= 0 and got[0]["tick"] > self._last + 1:
            self.gaps.append((self._last, got[0]["tick"]))
        for f in got:
            self.frames.append(f)
            self._last = max(self._last, f["tick"])
        return len(got)

    def last_tick(self):
        """The newest game tick this tape holds."""

        return self._last

    def record(self, seconds, every=8.0):
        """Poll until `seconds` pass; `every` stays well under the 20 s buffer so no frame ages out."""
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
    if not os.path.isdir(DIR):
        return None
    names = sorted(n for n in os.listdir(DIR) if n.endswith(".json") and (not name or n.startswith(name + "-")))
    return os.path.join(DIR, names[-1]) if names else None

def events(since=0, timeout_ms=1000):
    """Changes the game pushed, newer than `since` (an event sequence number)."""

    return api.get(f"/events?since={since}&timeout={int(timeout_ms)}")

