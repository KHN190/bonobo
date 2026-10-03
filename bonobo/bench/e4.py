"""E4: a step's actual seconds against its estimate, read from the price lines (dispatch.trace → prices.jsonl).
A step is in when actual / estimate ∈ STEP_BAND; a price item holds when the geometric mean of its steps' ratios is in
STEP_BAND and every kept sample in ITEM_SPREAD; a ratio outside KEEP is dropped and listed (a stuck path, a step cut
short), never fitted."""
import json
import math

from ..api import swallowed
from ..data import TICKS_PER_S

STEP_BAND = (0.5, 2.0)
ITEM_SPREAD = (0.25, 4.0)
KEEP = (0.2, 5.0)


def ratio(line):
    """Pure: actual seconds / estimated seconds of one price line, or None (no estimate, or not ok); a step run
    under a faster clock (cond.tick_rate) counted in game seconds."""
    est = float(line.get("est") or 0) / TICKS_PER_S
    if not line.get("ok") or est <= 0:
        return None
    rate = float((line.get("cond") or {}).get("tick_rate") or TICKS_PER_S)
    return float(line["actual_s"]) * rate / TICKS_PER_S / est


def inside(r, band):
    return band[0] <= r <= band[1]


def read_lines(path, row=None, since=None):
    """The price lines of `path`, those of `row` from `since` on when given (a malformed line skipped)."""
    out = []
    try:
        with open(path) as fh:
            for ln in fh:
                try:
                    line = json.loads(ln)
                except ValueError as e:
                    swallowed("e4.read_lines", e)      # a line cut by a crash mid-write
                    continue
                if (row is None or line.get("row") == row) and (since is None or line.get("t", 0) >= since):
                    out.append(line)
    except FileNotFoundError as e:
        swallowed("e4.read_lines", e)
        return []
    return out


def row_verdict(lines):
    """Pure: (holds, misses) of a bench row's ok steps — holds None when no ok step was priced; misses the steps
    outside STEP_BAND as "kind token: ratio"."""
    judged = [(ln, r) for ln in lines if (r := ratio(ln)) is not None]
    if not judged:
        return None, []
    misses = [f"{ln['kind']} {ln['token']}: {r:.2f}" for ln, r in judged if not inside(r, STEP_BAND)]
    return not misses, misses


def item_of(line):
    """Pure: the price item a line's work came from ("PRIOR_TICKS.gather_each") and its source tag."""
    item, _, tag = str(line.get("price", {}).get("work", "")).partition(":")
    return item, tag


def item_verdicts(lines):
    """Pure: {item: {"tag", "n", "gmean", "holds", "dropped"}} over the ok lines naming it."""
    by = {}
    for ln in lines:
        r = ratio(ln)
        if r is None:
            continue
        item, tag = item_of(ln)
        d = by.setdefault(item, {"tag": tag, "kept": [], "dropped": []})
        (d["kept"] if inside(r, KEEP) else d["dropped"]).append(round(r, 3))
    out = {}
    for item, d in by.items():
        kept = d["kept"]
        g = math.exp(sum(math.log(r) for r in kept) / len(kept)) if kept else None
        out[item] = {"tag": d["tag"], "n": len(kept), "gmean": g, "dropped": d["dropped"],
                     "holds": g is not None and inside(g, STEP_BAND) and all(inside(r, ITEM_SPREAD) for r in kept)}
    return out
