"""Rounds: where the brain's time goes, from the `round …` lines brain._round writes to detail.log.
mc.py rounds [SINCE]    median and max ms per phase since HH:MM:SS (default: the whole file), and the 3 longest gaps
(a task ended → the next task posted: the body idle between them)."""
import re
import statistics
import sys

from .. import api

NUMBER = re.compile(r"-?\d+(?:\.\d*)?")     # a field's value in a round line

def parse(line):
    """Pure: (time, {field: ms or None}) of a `round` line, or None for any other line."""
    parts = line.split()
    if len(parts) < 3 or parts[1] != "round":
        return None
    fields = {}
    for p in parts[2:]:
        k, _, v = p.partition("=")
        if v != "-" and not NUMBER.fullmatch(v):
            return None
        fields[k] = None if v == "-" else float(v)
    return parts[0], fields

def summarize(lines, since=None):
    """Pure: {"rounds": n, "phases": {phase: (median, max)}, "gaps": [(ms, time), top 3]} over the round lines at or
    after `since` (HH:MM:SS)."""
    rows = [r for r in map(parse, lines) if r is not None and (since is None or r[0] >= since)]
    phases = {}
    for _, f in rows:
        for k, v in f.items():
            if k != "gap" and v is not None:
                phases.setdefault(k, []).append(v)
    gaps = sorted(((f["gap"], t) for t, f in rows if f.get("gap") is not None), reverse=True)[:3]
    return {"rounds": len(rows), "phases": {k: (statistics.median(v), max(v)) for k, v in phases.items()},
            "gaps": gaps}

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    since = argv[0] if argv else None
    try:
        with open(api.DETAIL_FILE) as f:
            got = summarize(f, since)
    except OSError as e:
        print(f"no detail.log: {e}")
        return 1
    print(f"{got['rounds']} rounds" + (f" since {since}" if since else ""))
    for k, (med, mx) in got["phases"].items():
        print(f"  {k:8} median {med:7.0f} ms   max {mx:7.0f} ms")
    for ms, t in got["gaps"]:
        print(f"  gap {ms:7.0f} ms at {t}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
