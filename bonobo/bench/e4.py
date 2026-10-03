"""E4: a step's actual seconds against its estimate, read from the price lines (dispatch.trace → prices.jsonl).
A step is in when actual / estimate ∈ STEP_BAND; a price item holds when the geometric mean of its steps' ratios is in
STEP_BAND and every kept sample in ITEM_SPREAD; a ratio outside KEEP is dropped and listed (a stuck path, a step cut
short), never fitted."""
import json
import math

from ..api import swallowed
from .. import beliefs
from ..data import ROUTE_FACTOR, TICKS_PER_S, WALK_BLOCKS_PER_TICK

STEP_BAND = (0.5, 2.0)
ITEM_SPREAD = (0.25, 4.0)
KEEP = (0.2, 5.0)


def _scale(line):
    """Pure: game seconds per wall second of the line's run (cond.tick_rate: a bench row's fast clock)."""
    return float((line.get("cond") or {}).get("tick_rate") or TICKS_PER_S) / TICKS_PER_S


def ratio(line):
    """Pure: actual seconds / estimated seconds of one price line, or None (no estimate, or not ok); in game seconds
    (the game's own clock when the line read it, else the wall clock × the row's tick rate)."""
    est = float(line.get("est") or 0) / TICKS_PER_S
    if not line.get("ok") or est <= 0:
        return None
    actual = line["game_s"] if line.get("game_s") is not None else float(line["actual_s"]) * _scale(line)
    return float(actual) / est


def part_ratios(line):
    """Pure: {price item: actual / estimate} by part — the work (and its digging) against the phase after the
    walking, the walk or the seek against the walking — when the line was measured by phase; else the whole step
    against its work's item."""
    whole = ratio(line)
    if whole is None:
        return {}
    price = {p: s.partition(":")[0] for p, s in (line.get("price") or {}).items()}
    est, act = line.get("est_parts") or {}, line.get("actual_parts")
    if not act or not est or line.get("game_s") is not None:
        return {price.get("work", ""): whole}
    out, k = {}, _scale(line)
    work = (est.get("work", 0) + est.get("dig", 0)) / TICKS_PER_S
    if work > 0:
        out[price["work"]] = act["work"] * k / work
    walk, seek = (est.get("walk", 0) + est.get("surface", 0)) / TICKS_PER_S, est.get("seek", 0) / TICKS_PER_S
    moved = (act["walk"] + act["seek"]) * k
    if walk > 0 and not seek and "walk" in price:
        out.update(walk_split(line, act["walk"] * k) or {price["walk"]: moved / walk})
    elif seek > 0 and not walk and "seek" in price:
        out[price["seek"]] = moved / seek
    return out


STRAIGHT_MIN = 4.0      # blocks: a shorter move tells nothing of its route's length


def walk_split(line, walk_s):
    """Pure: the walk's two prices apart, from the path the /state reads traced — {ROUTE_FACTOR: path / (straight ×
    the factor), WALK_BLOCKS_PER_TICK: walk seconds / (path at the priced speed)}; {} when the path was not traced."""
    path, straight = line.get("path_m"), line.get("straight_m")
    if not path or not straight or straight < STRAIGHT_MIN or walk_s <= 0:
        return {}
    return {"data.ROUTE_FACTOR": path / (straight * ROUTE_FACTOR),
            "data.WALK_BLOCKS_PER_TICK": walk_s / (path / (WALK_BLOCKS_PER_TICK * TICKS_PER_S))}


def drain_ratio(lines):
    """Pure: (the bar's drain priced (risk.food_drain_s) against the measured one — Σ drop × the price / Σ seconds,
    > 1 draining faster — and the lines it read), over the ok lines that read the bar; (None, 0) without one."""
    read = [ln for ln in lines if ln.get("ok") and ln.get("bar_drop") is not None and ln.get("actual_s")]
    seconds = sum(float(ln["actual_s"]) * _scale(ln) for ln in read)
    if not read or seconds <= 0:
        return None, 0
    return sum(float(ln["bar_drop"]) for ln in read) * float(beliefs.value("risk.food_drain_s")) / seconds, len(read)


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


def item_verdicts(lines):
    """Pure: {item: {"tag", "n", "gmean", "holds", "dropped"}} over the ok lines' part ratios naming it."""
    by = {}
    for ln in lines:
        tags = {s.partition(":")[0]: s.partition(":")[2] for s in (ln.get("price") or {}).values()}
        for item, r in part_ratios(ln).items():
            d = by.setdefault(item, {"tag": tags.get(item, "prior"), "kept": [], "dropped": []})
            (d["kept"] if inside(r, KEEP) else d["dropped"]).append(round(r, 3))
    out = {}
    for item, d in by.items():
        kept = d["kept"]
        g = math.exp(sum(math.log(r) for r in kept) / len(kept)) if kept else None
        out[item] = {"tag": d["tag"], "n": len(kept), "gmean": g, "dropped": d["dropped"],
                     "holds": g is not None and inside(g, STEP_BAND) and all(inside(r, ITEM_SPREAD) for r in kept)}
    g, n = drain_ratio(lines)
    if g is not None:
        out["risk.food_drain_s"] = {"tag": "prior", "n": n, "gmean": g, "dropped": [], "holds": inside(g, STEP_BAND)}
    return out
