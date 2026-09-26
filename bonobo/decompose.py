"""L2: a goal and a bag in, an ordered list of steps out. The brain calls only this.

    decompose(inv, goal, cost, solver=None, pending=None) -> [Step]

Item goals (have, craft, milestone) go to a SOLVER; the rest become action steps, after whatever materials they
need (build). Solvers are registered by name: `planner` (planner.py, recursive descent over the requirement graph)
is the default; `solve` (solve.py over the action columns) is tried when it cannot plan, or chosen by name per task. Every
solver gets the same cost model (cost.Cost), so their steps are priced in the same ticks.

Pure apart from what the cost model reads (one cached /find per kind).
"""
from . import blueprints, goals
from .planner import Planner, Step, Unplannable

SOLVERS = {}          # name -> fn(inv, needs, cost, pending) -> [Step]
ORDER = []            # fallback order when no solver is named


def register(name, fn):
    """Add a solver. The first registered is the default."""
    SOLVERS[name] = fn
    if name not in ORDER:
        ORDER.append(name)


def _planner(inv, needs, cost, pending=None):
    return Planner.from_inventory(inv, cost, pending).plan(needs)


register("planner", _planner)


def _solve(inv, needs, cost, pending=None):
    """The column solver (solve.py over actions.table): slower, and it can see further — where to go for a thing,
    what to take that is already made, which half-done work to finish. Reads the bag from the cost model's snapshot,
    so it needs one (and memory, for the places it knows)."""
    from . import actions as act
    from .solve import Unsolvable, solve
    target = act.target_of(needs)
    if not target:
        return []
    if getattr(cost, "snap", None) is None or getattr(cost, "mem", None) is None:
        raise Unplannable("the column solver needs a snapshot and a memory")
    vector = act.state_of(cost.snap, cost.mem)
    try:
        found = solve(act.table(cost, vector), vector, target)
    except Unsolvable as e:
        raise Unplannable(str(e))
    return [act.to_step(a, n) for a, n in found.steps()]


register("solve", _solve)


def solve_needs(inv, needs, cost, solver=None, pending=None):
    """Steps that make `needs` held. The named solver, else each registered one in turn until one plans."""
    if not needs:
        return []
    names = [solver] if solver else list(ORDER)
    last = None
    for name in names:
        fn = SOLVERS.get(name)
        if fn is None:
            last = Unplannable(f"no solver named {name!r}")
            continue
        try:
            return fn(inv, needs, cost, pending)
        except Unplannable as e:
            last = e
    raise last or Unplannable("no solver could plan this")


def _action(kind, token, cost, **detail):
    step = Step(kind, token, 1, detail)
    step.est = cost.estimate(step)
    return step


def decompose(inv, goal, cost, solver=None, pending=None):
    """Ordered steps for `goal` from this bag. Raises Unplannable when no registered solver can reach it, or when a
    step asks for an effect no registered skill provides (skill.handles)."""
    from . import skill
    steps = _decompose(inv, goal, cost, solver, pending)
    missing = [s for s in steps if not skill.handles(s)]
    if missing:
        raise Unplannable(f"no skill provides {missing[0].kind} {missing[0].token}")
    return steps


def _decompose(inv, goal, cost, solver, pending):
    template, args = goal["goal"], goal.get("args", {})
    if template in goals.ITEM_GOALS:
        return solve_needs(inv, goals.needs(goal, inv), cost, solver, pending)
    if template == "goto":
        return [_action("goto", "pos", cost, pos=list(args["pos"]), range=float(args.get("range", 2)))]
    if template == "road":
        return [_action("goto", "pos", cost, pos=list(args["a"]), range=4.0),
                _action("goto", "pos", cost, pos=list(args["b"]), range=4.0)]
    if template == "build":
        bp = args["bp"]
        materials = blueprints.materials(blueprints.SHELTER if bp == "shelter" else blueprints.REGISTRY[bp])
        if bp == "nether_portal":
            materials = dict(materials, **{"minecraft:flint_and_steel": 1})
        steps = solve_needs(inv, [(t, n) for t, n in materials.items()], cost, solver, pending)
        return steps + [_action("build", bp, cost, at=args.get("at"))]
    if template == "sleep":
        return [_action("sleep", "bed", cost)]
    if template == "skill":
        return [_action("skill", args["name"], cost, args=list(args.get("args", [])))]
    raise Unplannable(f"no way to decompose a {template!r} goal")


def to_dict(step):
    return {"kind": step.kind, "token": step.token, "count": step.count, "detail": step.detail, "est": step.est}


def from_dict(d):
    return Step(d["kind"], d["token"], int(d["count"]), dict(d.get("detail") or {}), int(d.get("est", 0)))
