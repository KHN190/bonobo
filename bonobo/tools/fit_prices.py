"""Fit the priors from the price lines (prices.jsonl), bounded, and say E4 per item. Offline: reads files, never the
game. A game or policy item is never fitted (a miss there is a model bug, said); an item is changed only from MIN_N
kept samples and by at least MIN_CHANGE, one fit moving it at most ×FIT_STEP and never past ×MEASURED_BAND of its first
prior (knowledge.PRIOR_ORIGIN keeps it). Fitted: the step works (PRIOR_TICKS), a crop's or an animal's growth
(GROW_S), the walk (WALK_BLOCKS_PER_TICK: a speed, fitted inversely) and its route (ROUTE_FACTOR), a search's
biome-made densities (FIND_DENSITY: per chunk, fitted inversely) and the bar's drain (food_drain_s: seconds a point, fitted inversely).
Usage: fit_prices [--write]"""
import os
import re
import sys

from .. import beliefs, data, dispatch, knowledge
from ..bench import e4
from ..data import MEASURED_BAND

MIN_N = 3
MIN_CHANGE = 0.10
FIT_STEP = 2.0
FITTED_TAGS = ("prior", "mineflayer prior", "measured")     # a game or policy value is never fitted
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE, DATA, PLAY = (os.path.join(HERE, f) for f in ("knowledge.py", "data.py", "play.toml"))


def fitted(current, origin, gmean, n, tag, inverse=False):
    """Pure: the new value of one item, or None (not fitted: game/policy, too few samples, too small a change); a
    speed (`inverse`) moves against the time it took."""
    if tag not in FITTED_TAGS or gmean is None or n < MIN_N or current <= 0:
        return None
    step = min(max(gmean, 1.0 / FIT_STEP), FIT_STEP)
    new = current / step if inverse else current * step
    new = min(max(new, origin / MEASURED_BAND), origin * MEASURED_BAND)
    new = round(new) if isinstance(current, int) else round(new, 3)
    return new if abs(new - current) / current >= MIN_CHANGE else None


def current_values():
    """{item: (value now, inverse)} of every fittable item."""
    out: dict[str, tuple[float, bool]] = {f"PRIOR_TICKS.{k}": (v, False) for k, v in knowledge.PRIOR_TICKS.items()
                                          if isinstance(v, (int, float))}
    out.update({f"knowledge.GROW_S.{k}": (v, False) for k, v in knowledge.GROW_S.items()})
    out["data.WALK_BLOCKS_PER_TICK"] = (data.WALK_BLOCKS_PER_TICK, True)
    out.update({f"knowledge.FIND_DENSITY.{k}": (v, True) for k, v in knowledge.FIND_DENSITY.items()})
    out["data.ROUTE_FACTOR"] = (data.ROUTE_FACTOR, False)
    out["risk.food_drain_s"] = (float(beliefs.CONFIG["risk"]["food_drain_s"]), True)      # seconds a point: inversely
    return out


def fit_plan(report, values, origin):
    """Pure: {item: new value} from an e4.item_verdicts report over `values` ({item: (value, inverse)})."""
    out = {}
    for item, d in report.items():
        if item not in values:
            continue
        value, inverse = values[item]
        new = fitted(value, origin.get(item, value), d["gmean"], d["n"], d["tag"], inverse)
        if new is not None:
            out[item] = new
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


def _scalar(src, name, value):
    out, n = re.subn(rf"^({re.escape(name)}\s*=\s*)[^#\n]*?(?=\s*(?:#|$))", lambda m: m.group(1) + value, src, count=1, flags=re.M)
    if not n:
        raise KeyError(name)
    return out


def fitted_knowledge(src, changes, first):
    """Pure: knowledge.py's source with the fitted PRIOR_TICKS / GROW_S values, their tags (and WALK's) "measured",
    each item's first value kept in PRIOR_ORIGIN."""
    for item, new in changes.items():
        if item.startswith("PRIOR_TICKS."):
            key = item.split(".", 1)[1]
            src = _sub_in(src, "PRIOR_TICKS = {", key, str(new))
            src = _sub_in(src, '"knowledge.PRIOR_TICKS": {', key, '"measured"')
        elif item.startswith(("knowledge.GROW_S.", "knowledge.FIND_DENSITY.")):
            table, key = item.split(".")[1:]
            src = _sub_in(src, f"{table} = {{", key, str(new))
            src = _sub_in(src, f'"knowledge.{table}": {{', key, '"measured"')
        elif item in ("data.WALK_BLOCKS_PER_TICK", "data.ROUTE_FACTOR"):
            src = re.sub(rf'"{re.escape(item)}": "[^"]*"', f'"{item}": "measured"', src, count=1)
        b, e = _block(src, "PRIOR_ORIGIN = {")
        if f'"{item}"' not in src[b:e]:
            src = src[:e - 1] + ("" if e - b == 2 else ", ") + f'"{item}": {first[item]}' + src[e - 1:]
    return src


def fitted_data(src, changes):
    """Pure: data.py's source with the fitted walk speed and route factor."""
    for item in ("data.WALK_BLOCKS_PER_TICK", "data.ROUTE_FACTOR"):
        if item in changes:
            src = _scalar(src, item.split(".", 1)[1], str(changes[item]))
    return src


def fitted_play(src, changes):
    """Pure: play.toml with the fitted bar's drain, tagged "[measured]"."""
    for item in ("risk.food_drain_s",):
        if item not in changes:
            continue
        key = item.split(".", 1)[1]
        src = _scalar(src, key, str(changes[item]))
        tagged = re.sub(rf"^({key}\s*=\s*\S+\s*#\s*)\[prior\]", r"\1[measured]", src, count=1, flags=re.M)
        src = tagged if tagged != src else re.sub(rf"^({key}\s*=\s*\S+\s*#\s*)", r"\1[measured] ", src, count=1,
                                                  flags=re.M)
    return src


def main(argv):
    report = e4.item_verdicts(e4.read_lines(dispatch.PRICES))
    for item, d in sorted(report.items()):
        g = "-" if d["gmean"] is None else f"{d['gmean']:.2f}"
        print(f"{item:32} {d['tag']:9} n={d['n']:<3} gmean {g:>5} E4 {'in' if d['holds'] else 'OUT'}"
              + (f"  dropped {d['dropped']}" if d["dropped"] else "")
              + ("  (game: a model bug, not fitted)" if d["tag"] == "game" and not d["holds"] else ""))
    values = current_values()
    changes = fit_plan(report, values, knowledge.PRIOR_ORIGIN)
    for item, new in sorted(changes.items()):
        print(f"fit {item}: {values[item][0]} → {new}")
    if changes and "--write" in argv:
        first = {item: values[item][0] for item in changes}
        for path, rewrite in ((KNOWLEDGE, lambda s: fitted_knowledge(s, changes, first)),
                              (DATA, lambda s: fitted_data(s, changes)), (PLAY, lambda s: fitted_play(s, changes))):
            with open(path) as fh:
                src = fh.read()
            out = rewrite(src)
            if out != src:
                with open(path, "w") as fh:
                    fh.write(out)
        print("written: run P3, D6 and the golden plans before merging")
    return 0 if all(d["holds"] for d in report.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
