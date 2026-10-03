"""Fit the step priors (knowledge.PRIOR_TICKS) from the price lines (prices.jsonl), bounded, and say E4 per item.
Offline: reads files, never the game. A game or policy item is never fitted (a miss there is a model bug, said); an
item is changed only from MIN_N kept samples and by at least MIN_CHANGE, one fit moving it at most ×FIT_STEP and
never past ×MEASURED_BAND of its first prior (knowledge.PRIOR_ORIGIN keeps it). Usage: fit_prices [--write]"""
import os
import re
import sys

from .. import dispatch, knowledge
from ..bench import e4
from ..data import MEASURED_BAND

MIN_N = 3
MIN_CHANGE = 0.10
FIT_STEP = 2.0
KNOWLEDGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge.py")


def fitted(current, origin, gmean, n, tag):
    """Pure: the new prior of one item, or None (not fitted: game/policy, too few samples, too small a change)."""
    if tag not in ("prior", "measured") or gmean is None or n < MIN_N:
        return None
    new = current * min(max(gmean, 1.0 / FIT_STEP), FIT_STEP)
    new = min(max(new, origin / MEASURED_BAND), origin * MEASURED_BAND)
    new = int(round(new))
    return new if current > 0 and abs(new - current) / current >= MIN_CHANGE else None


def fit_plan(report, priors, origin):
    """Pure: {PRIOR_TICKS key: new ticks} from an e4.items report."""
    out = {}
    for item, d in report.items():
        table, _, key = item.partition(".")
        if table != "PRIOR_TICKS" or key not in priors:
            continue
        new = fitted(priors[key], origin.get(key, priors[key]), d["gmean"], d["n"], d["tag"])
        if new is not None:
            out[key] = new
    return out


def _block(src, start):
    """(begin, end) of the dict literal that opens at `start`'s first "{"."""
    i = src.index("{", src.index(start))
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return i, j + 1
    raise ValueError(f"unclosed {start}")


def _sub_in(src, start, key, value):
    b, e = _block(src, start)
    part, n = re.subn(rf'("{re.escape(key)}":\s*)[^,}}\n]+', lambda m: m.group(1) + value, src[b:e], count=1)
    if not n:
        raise KeyError(f"{key} not in {start}")
    return src[:b] + part + src[e:]


def fitted_source(src, changes, priors):
    """Pure: knowledge.py's source with each fitted prior written, its tag "measured", its first prior kept."""
    for key, new in changes.items():
        src = _sub_in(src, "PRIOR_TICKS = {", key, str(new))
        src = _sub_in(src, '"knowledge.PRIOR_TICKS": {', key, '"measured"')
        b, e = _block(src, "PRIOR_ORIGIN = {")
        if f'"{key}"' not in src[b:e]:
            src = src[:e - 1] + ("" if e - b == 2 else ", ") + f'"{key}": {priors[key]}' + src[e - 1:]
    return src


def main(argv):
    lines = e4.read_lines(dispatch.PRICES)
    report = e4.item_verdicts(lines)
    for item, d in sorted(report.items()):
        g = "-" if d["gmean"] is None else f"{d['gmean']:.2f}"
        print(f"{item:32} {d['tag']:9} n={d['n']:<3} gmean {g:>5} E4 {'in' if d['holds'] else 'OUT'}"
              + (f"  dropped {d['dropped']}" if d["dropped"] else "")
              + ("  (game: a model bug, not fitted)" if d["tag"] == "game" and not d["holds"] else ""))
    changes = fit_plan(report, knowledge.PRIOR_TICKS, knowledge.PRIOR_ORIGIN)
    for key, new in sorted(changes.items()):
        print(f"fit PRIOR_TICKS.{key}: {knowledge.PRIOR_TICKS[key]} → {new}")
    if changes and "--write" in argv:
        with open(KNOWLEDGE) as fh:
            src = fh.read()
        with open(KNOWLEDGE, "w") as fh:
            fh.write(fitted_source(src, changes, knowledge.PRIOR_TICKS))
        print("written: run P3, D6 and the golden plans before merging")
    return 0 if all(d["holds"] for d in report.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
