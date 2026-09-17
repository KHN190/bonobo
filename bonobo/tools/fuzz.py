"""Walk the sweep, ask the four doors, write down what every column said. Nothing here judges anything.

The offline tests state relations ("one thing changed, the number may only move this way") and stop at the first
cell that breaks one. That is the right shape for a test and the wrong shape for looking: a relation holds over
five hundred cells and says nothing about WHERE the planner is nearly indifferent, which column is never once
admissible, or which quantity is still a prior everywhere. This writes the table those questions are asked of.

One line per (cell, family), into `bench/fuzz.jsonl`:

    dims        where the cell sits along every dimension (`tests/world.DIMS`, named as `bench.cells.DIMENSIONS`
                names them where the two share a dimension — one vocabulary, offline and in game)
    quantities  the four doors, on this cell, and nothing else:
                    arrival     gates.takes_s(s, Go(here, nearest threat))   Δt — when the gap is crossed
                    pressure    gates.p(s, "encounter")                      p  — hostiles per second out here
                    hp_price    gates.marginal("blood", sstate=…)            κ  — seconds one point of health costs
                    state_price gates.V(s)                                   V  — seconds from here to finished
                A door that has nothing to say in this cell writes null rather than a number: "no threat, so no
                arrival" is a fact, and a zero would be a claim.
    columns     {name: {cost_s, saves, admissible, why_not}} — every answer this cell offers, priced by the layer
                that owns it, never by a fifth estimator written here
    pick        what the existing chooser chose (`kernel.choose` for the two fight families, `priority.choose`
                for the planner's), by name
    margin      SECONDS between the best column and the runner-up. The whole point of the table: a cell with a
                margin of 0.2 s is a coin flip the tests call a decision, and those are the cells a measurement
                is worth taking in.
    assumptions what the chooser said it was assuming, plus the cell's own unmeasured dimensions

Three families, because a cell is read three ways (`tests/world.World`): `plan` is the solver's columns priced
through V, `threat` is what is coming at us, `fight` is the dragon. They are separate rows and never compared:
two models, two prices, and a margin between them would be a number about nothing.

Sampling goes through the existing gates and nothing else: cells come from `DIMS`, prices from `gates`, the pick
from the choosers the agent actually runs. There are no assertions in this file, on purpose — a fuzzer that knows
what the right answer is only finds the bugs somebody already thought of.

    python3 -m bonobo.tools.fuzz --cells 200 --seed 1
"""
import argparse
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bonobo import gates, kernel, paths, priority, threat, value  # noqa: E402
from tests.world import DEFAULTS, DIMS, HERE, World, _SHARED  # noqa: E402

ROWS = paths.data("bench/fuzz.jsonl")

TICKS_PER_S = priority.TICKS_PER_S

FAMILIES = ("plan", "threat", "fight")

MAX_COLUMNS = 24


def dim_names():
    """The name every dimension is written out under: the in-game vocabulary where the two sides share one."""
    return {name: _SHARED.get(name, name) for name in DIMS}


def _plain(x, depth=0):
    """Whatever it is, as something `json` can write. A fact that cannot be serialised is still a fact."""
    if x is None or isinstance(x, (bool, int, str)):
        return x
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else round(x, 4)
    if isinstance(x, dict) and depth < 4:
        return {str(k): _plain(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)) and depth < 4:
        return [_plain(v, depth + 1) for v in x]
    return str(x)


def _err(e):
    return f"{type(e).__name__}: {e}"


# -- the four doors, on one cell ----------------------------------------------------------------------------------


def quantities(world):
    """The four doors on this cell, each called once, each through `gates` and nothing below it."""
    out = {"arrival": None, "pressure": None, "hp_price": None, "state_price": None}
    situation = world.situation()
    rows = world.rows()
    if rows:
        nearest = min(rows, key=lambda h: math.dist(HERE, h[0]))
        out["arrival"] = _try(lambda: gates.takes_s(situation, gates.Go(HERE, nearest[0])))
    out["pressure"] = _try(lambda: gates.p(situation, "encounter"))
    out["hp_price"] = _try(lambda: gates.marginal("blood", sstate=world.fight_sstate()))
    out["state_price"] = _try(lambda: gates.V(situation))
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def _try(fn):
    """The door's answer, or nothing. An estimator that raises in a cell is a fact about the cell."""
    try:
        value_ = fn()
    except Exception:
        return None
    if value_ is None:
        return None
    value_ = float(value_)
    return None if (math.isnan(value_) or math.isinf(value_)) else value_


# -- the three families -------------------------------------------------------------------------------------------


def plan_columns(world, rng, most=MAX_COLUMNS):
    """The solver's columns, priced the way the pool prices them: `value.worth_s` over the V door.

    `saves` is seconds the column's effect is worth over the horizon, with its own duration already inside the
    comparison; `cost_s` is what running it once takes. Nothing new is estimated here — both come back from the
    same two functions the brain calls.
    """
    state = world.state()
    situation = world.situation(state)
    table = list(world.columns(state))
    total = len(table)
    if len(table) > most:
        table = rng.sample(table, most)
    value_of = lambda s: gates.V(situation.with_state(s), parts=True)   # noqa: E731
    columns = {}
    for column in table:
        ok, why = _requires_met(column, state)
        slots = max(1.0, float(sum(1 for _d, delta in column.effect.items() if delta > 0)))
        try:
            saves = value.worth_s(state, column.effect, value_of=value_of, bag_free=situation.inv_free,
                                  slots=slots, takes_s=column.cost_s)
        except Exception as e:
            columns[column.name] = {"cost_s": round(column.cost_s, 3), "saves": None,
                                    "admissible": ok, "why_not": why or "", "error": _err(e)}
            continue
        columns[column.name] = {"cost_s": round(float(column.cost_s), 3), "saves": round(float(saves), 3),
                                "admissible": ok, "why_not": why or ""}
    return columns, total


def _requires_met(column, state):
    """(allowed, why) for a solver column: what it needs to hold and does not. The column's own declaration."""
    for dim, minimum in (column.requires or {}).items():
        have = float(state.get(dim, 0) or 0)
        if have < float(minimum):
            return False, f"needs {dim} ≥ {minimum}, have {have:g}"
    return True, ""


def plan_pick(columns):
    """What the pool would run, and by how many seconds — `priority.choose`, over candidates priced above."""
    pool = []
    for name, col in columns.items():
        if not col["admissible"] or col.get("saves") is None:
            continue
        candidate = priority.Candidate(name, 0.0, float(col["cost_s"]) * TICKS_PER_S, None,
                                       seconds=float(col["saves"]))
        pool.append(candidate)
    if not pool:
        return None, None
    chosen = priority.choose(pool, committed=None, here=HERE)
    scores = sorted((c.score for c in pool), reverse=True)
    margin = (scores[0] - scores[1]) if len(scores) > 1 else None
    return (chosen.name if chosen else None), (round(margin, 3) if margin is not None else None)


def threat_family(world):
    """Every answer to what is coming at us, priced against carrying on, and the answer `kernel.choose` takes."""
    state = world.threat_state()
    price = world.price()
    opts = threat.options(state)
    horizon = threat.horizon_for(state)
    field = threat.Field(state, price)
    kstate = field.state()
    columns = {}
    for option in opts:
        ok, why = field.admissible(kstate, option)
        columns[option.kind] = {"cost_s": round(threat.action_cost(option, price), 3),
                                "saves": round(threat.saves(option, opts, price, horizon), 3),
                                "admissible": bool(ok), "why_not": why or ""}
    choice = kernel.choose(field, kstate)
    return columns, len(opts), choice


def fight_family(world):
    """The dragon's actions, each worth the fall in `Fight.objective` it makes, and what the veto let through."""
    model = world.model()
    state = world.fight_state()
    columns = {}
    for action in model.actions:
        ok, why = model.admissible(state, action)
        try:
            saves = model.benefit(state, action)
        except Exception as e:
            columns[action.name] = {"cost_s": round(float(action.cost_s), 3), "saves": None,
                                    "admissible": bool(ok), "why_not": why or "", "error": _err(e)}
            continue
        columns[action.name] = {"cost_s": round(float(action.cost_s), 3), "saves": round(float(saves), 3),
                                "admissible": bool(ok), "why_not": why or ""}
    choice = kernel.choose(model, state)
    return columns, len(model.actions), choice


def _margin_of(choice):
    """Seconds between the best admissible column and the next one, off what the chooser already considered."""
    scores = sorted((float(s) for s, _a in choice.considered), reverse=True)
    if len(scores) < 2:
        return None
    return round(scores[0] - scores[1], 3)


def _unmeasured(world):
    """What this cell admits it does not know: a dimension sitting on its unobserved end is an assumption."""
    out = []
    if world.dims.get("confidence") == "unmeasured":
        out.append("confidence: nothing observed, every rate is a prior")
    return out


# -- one row ------------------------------------------------------------------------------------------------------


def record(world, family, rng, slice_="random", most=MAX_COLUMNS):
    """One line: this cell, read as this family. Facts only — a failure is written down, never raised."""
    names = dim_names()
    row = {"family": family, "slice": slice_,
           "dims": {names[k]: v for k, v in world.dims.items()},
           "quantities": quantities(world),
           "columns": {}, "columns_total": 0, "pick": None, "margin": None,
           "assumptions": _unmeasured(world)}
    try:
        if family == "plan":
            columns, total = plan_columns(world, rng, most=most)
            pick, margin = plan_pick(columns)
            row.update(columns=columns, columns_total=total, pick=pick, margin=margin)
        elif family == "threat":
            columns, total, choice = threat_family(world)
            row.update(columns=columns, columns_total=total, pick=choice.name, margin=_margin_of(choice),
                       assumptions=row["assumptions"] + [str(a) for a in choice.assumptions])
            row["fault"] = _plain(choice.fault)
        elif family == "fight":
            columns, total, choice = fight_family(world)
            row.update(columns=columns, columns_total=total, pick=choice.name, margin=_margin_of(choice),
                       assumptions=row["assumptions"] + [str(a) for a in choice.assumptions])
            row["fault"] = _plain(choice.fault)
        else:
            raise KeyError(f"no such family: {family!r}")
    except Exception as e:
        row["error"] = _err(e)
    return _plain(row)


# -- where the cells come from --------------------------------------------------------------------------------------


def ladders():
    """One cell per value of one dimension, everything else at its default: the single-variable slices.

    A random sample says where the planner lives; a ladder says which way a number moves when one thing changes,
    and the two answer different questions. Both are in the same table so the plot can draw a curve through the
    ladder and scatter the sample around it.
    """
    for name in DIMS:
        for world in World(**DEFAULTS).along(name):
            yield world, f"ladder:{_SHARED.get(name, name)}"


def stratified(count, rng):
    """`count` cells, each dimension drawn so its values come up about equally often.

    Uniform-at-random over the product leaves a rare-but-important value (a creeper, a bag with one slot) in a
    handful of cells; stratifying each dimension separately keeps every value present at every sample size.
    """
    columns = {}
    for name, values in DIMS.items():
        order = []
        while len(order) < count:
            block = list(values)
            rng.shuffle(block)
            order += block
        columns[name] = order[:count]
    for i in range(count):
        yield World(**{name: columns[name][i] for name in DIMS}), "random"


def rows(count=200, seed=1, families=FAMILIES, with_ladders=True, most=MAX_COLUMNS):
    """Every row this run writes: the ladders first, then the sample, each cell read as each family."""
    rng = random.Random(seed)
    cells = list(ladders()) if with_ladders else []
    cells += list(stratified(count, rng))
    for world, slice_ in cells:
        for family in families:
            yield record(world, family, rng, slice_=slice_, most=most)


def write(path=ROWS, count=200, seed=1, families=FAMILIES, with_ladders=True, most=MAX_COLUMNS, append=False):
    """Write the table out, one JSON object per line, and answer how many lines that was."""
    paths.ensure(path)
    written = 0
    with open(path, "a" if append else "w") as fh:
        for row in rows(count=count, seed=seed, families=families, with_ladders=with_ladders, most=most):
            fh.write(json.dumps(row) + "\n")
            written += 1
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="sample the sweep and write what every column said")
    parser.add_argument("--cells", type=int, default=200, help="stratified random cells (ladders are extra)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default=ROWS)
    parser.add_argument("--family", action="append", choices=list(FAMILIES),
                        help="one family per flag; all three by default")
    parser.add_argument("--max-columns", type=int, default=MAX_COLUMNS,
                        help="most planner columns priced per cell (V is the expensive door)")
    parser.add_argument("--no-ladders", action="store_true")
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args(argv)
    written = write(path=args.out, count=args.cells, seed=args.seed,
                    families=tuple(args.family or FAMILIES), with_ladders=not args.no_ladders,
                    most=args.max_columns, append=args.append)
    print(f"{written} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
