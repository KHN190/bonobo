"""The supervisor's wake rule over events.jsonl: which event is worth waking whoever watches autoplay.

    python3 -m bonobo.tools.wake offset          → the events file's size now (where a session starts)
    python3 -m bonobo.tools.wake OFFSET IDLE_S   → "WAKE: <why>" and the last 10 events, exit 0; nothing, exit 1
    python3 -m bonobo.tools.wake tail OFFSET     → the last 10 events (a wake the shell found: exit, crash, check-in)
"""
import json
import os
import sys
import time

from .. import events

REPEATED = events.ANOMALY_AT[1]  # an error or refusal said this often is a loop
LAST_N = 10              # events shown with a wake


def read(path, offset):
    """The records written since `offset` (bytes) and the offset after them."""
    try:
        with open(path) as f:
            f.seek(offset)
            text = f.read()
            end = f.tell()
    except OSError:
        return [], offset
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass            # a line half written: read next time
    return out, end


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


def last_lines(records):
    return ["  " + time.strftime("%H:%M:%S", time.localtime(float(r["t"]))) + " " + r["line"]
            for r in records[-LAST_N:]]


def main(argv):
    if argv[:1] == ["offset"]:
        print(os.path.getsize(events.EVENTS_FILE) if os.path.exists(events.EVENTS_FILE) else 0)
        return 0
    if argv[:1] == ["tail"]:
        print("\n".join(["  last events:"] + last_lines(read(events.EVENTS_FILE, int(argv[1]))[0])))
        return 0
    records, _end = read(events.EVENTS_FILE, int(argv[0]))
    why = wake_reason(records, time.time(), float(argv[1]))
    if why is None:
        return 1
    print("\n".join([f"WAKE: {why}", "  last events:"] + last_lines(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
