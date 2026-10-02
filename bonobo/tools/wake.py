"""The supervisor's wake rule over events.jsonl: which event is worth waking whoever watches autoplay.

    python3 -m bonobo.tools.wake offset          → the events file's size now (where a session starts)
    python3 -m bonobo.tools.wake mtime           → detail.log's last write (what a "frozen" wake was about)
    python3 -m bonobo.tools.wake OFFSET IDLE_S [SEEN START]  → "WAKE: <why>" and the last 10 events, exit 0;
                                                   nothing, exit 1 — frozen too, once per silence (SEEN: its mtime
                                                   said), counted from START (autoplay's start, epoch s)
    python3 -m bonobo.tools.wake tail OFFSET     → the last 10 events (a wake the shell found: exit, crash, check-in)
"""
import json
import os
import sys
import time

from .. import api, events, paths

REPEATED = events.ANOMALY_AT[1]  # an error or refusal said this often is a loop
LAST_N = 10              # events shown with a wake


def read(path, offset):
    """The records written since `offset` (bytes) and the offset after them."""
    return paths.read_jsonl(path, offset=offset)


def wake_reason(records, now, idle_s):
    """Pure: why to wake over the session's events, or None — a death, a task stuck, a slow round, an error or a
    refusal repeated REPEATED times, or holding with nothing to do for `idle_s`."""
    for r in records:
        if r.get("kind") == "death":
            return r["line"]
        if r.get("kind") == "anomaly":
            what, n = r.get("what", ""), int(r.get("count", 1))
            if what in ("task stuck", "slow round"):
                return r["line"]
            if n >= REPEATED and (what.startswith(("exception", "swallowed")) or what == "answer refused"):
                return r["line"]
    goals = [r for r in records if r.get("kind") == "goal"]
    if goals and goals[-1].get("goal") == events.IDLE_GOAL and now - float(goals[-1]["t"]) >= idle_s:
        return f"idle {now - float(goals[-1]['t']):.0f}s ({goals[-1]['line']})"
    return None


def frozen(silent_s, paused, limit_s=api.FROZEN_S):
    """Pure: why to wake on a stuck brain — detail.log silent past `limit_s` while the agent drives — or None."""
    if paused or silent_s <= limit_s:
        return None
    return f"frozen: detail.log silent {silent_s:.0f}s (over {limit_s:.0f}s) while the agent drives"


def silence(now, mtime, start):
    """Pure: seconds detail.log has been silent — counted from this autoplay's start at the earliest (a log left from
    an earlier run is no silence of this one)."""
    return now - max(mtime, start)


def _detail_mtime():
    return os.path.getmtime(api.DETAIL_FILE) if os.path.exists(api.DETAIL_FILE) else None


def last_lines(records):
    return ["  " + time.strftime("%H:%M:%S", time.localtime(float(r["t"]))) + " " + r["line"]
            for r in records[-LAST_N:]]


def main(argv):
    if argv[:1] == ["offset"]:
        print(os.path.getsize(events.EVENTS_FILE) if os.path.exists(events.EVENTS_FILE) else 0)
        return 0
    if argv[:1] == ["mtime"]:
        print(_detail_mtime() or 0)
        return 0
    if argv[:1] == ["tail"]:
        print("\n".join(["  last events:"] + last_lines(read(events.EVENTS_FILE, int(argv[1]))[0])))
        return 0
    records, _end = read(events.EVENTS_FILE, int(argv[0]))
    why = wake_reason(records, time.time(), float(argv[1]))
    mtime = _detail_mtime()
    if why is None and mtime is not None and str(mtime) != (argv[2] if len(argv) > 2 else ""):
        try:
            paused = api.get("/state")["control"].get("paused")
        except (api.McError, KeyError):
            paused = None               # no game to ask: no freeze of ours to say
        if paused is not None:
            why = frozen(silence(time.time(), mtime, float(argv[3]) if len(argv) > 3 else 0.0), paused)
    if why is None:
        return 1
    print("\n".join([f"WAKE: {why}", "  last events:"] + last_lines(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
