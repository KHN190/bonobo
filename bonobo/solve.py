"""The planner's solver: what to do, as an integer program. Pure, exact, no dependencies.

The old planner resolved a goal by recursive descent — need(bed) → need(wool) → need(planks) → … — which is a
depth-first walk of the recipe graph, not a plan. Three consequences, all of them visible in play:

  * it could only reason about ITEMS, because "recursive descent on a requirement" has nothing to say about being
    somewhere, having dug something, or being sheltered;
  * it produced ONE decomposition, so a goal had exactly one way to be achieved and one step's failure cooled the
    whole goal (dig_in → burrow → pod was an if-chain inside a skill, invisible to the planner);
  * shared intermediates were merged AFTERWARDS (`merged()`), so the cost it reported was not the cost of the plan
    it had chosen — 16 planks for a door and a table were planned twice and merged once.

Here the world is a vector, an action is a column, and a plan is the integer combination of columns that reaches
the target for the fewest seconds:

    minimise   cᵀn                     n_j = how many times action j runs, integer ≥ 0
    subject to x₀ + A·n ≥ b            A's column j is action j's effect on the state vector

Everything follows from that. Several ways to reach the same state are several columns and the solver picks the
cheap one; a place, a dug hole or a night's shelter is another row; sharing is exact because the same column
serves every row that needs it. Solved with a two-phase simplex over exact rationals (matrices are small and their entries are recipe counts, so double precision with an explicit tolerance is exact
in practice and about fifty times faster than rationals — which matters, because the search below calls it
hundreds of times), then branch and bound for integrality.

Requirements that are needed but not consumed — a pickaxe to mine, a table to craft, standing at the vein — sit
around the matrix rather than in it (see `solve`), because as rows they destroy the linear relaxation and the search
explodes. When one cannot be met, the action that asked for it is banned and the solver finds another column to the
same state: that is what makes "dig in needs a pickaxe, wall in does not" a choice and not a dead end.
"""
EPS = 1e-9               # zero tolerance: these matrices are small integers, so anything smaller is noise

MAX_NODES = 4000         # branch-and-bound budget. Large because a node is now one float simplex (tens of
                         # microseconds); with 400 the search ran out before it found the good plan and returned
                         # whatever integer solution it had — a bed for 318 s when 78 s was available.
BIG = 64                 # indicator scale when an action has no explicit limit
KEEP_PER_DIM = 4         # cheapest producers kept per dimension; the rest cannot be in a sensible plan
MAX_DEPTH = 12           # recipe chains are shallow; deeper than this is a cycle in the action table


class Unsolvable(Exception):
    """No combination of the known actions reaches the target. Carries what was still missing."""

    def __init__(self, missing):
        super().__init__("no plan reaches: " + ", ".join(f"{d}≥{v:g}" for d, v in sorted(missing.items())))
        self.missing = missing


class Action:
    """One column of the matrix.

    effect:   {dimension: delta} — negative consumes, positive produces.
    cost_s:   seconds to run it once (the only currency).
    requires: {dimension: minimum} — must hold to run it, and is NOT consumed (a pickaxe tier, a crafting table,
              standing at the vein). Handled by the fixed point, not by the matrix.
    limit:    the most times it may run in one plan (None = unbounded); a gather that the world cannot supply
              twenty times says so here.
    """

    __slots__ = ("name", "effect", "cost_s", "requires", "limit", "tag", "_exposure")

    def exposure(self, state):
        """Seconds of damage this action's shape implies. Zero unless someone has taught it how to price that.

        The shape is the action's own business — standing work takes the pressure for its whole duration, leaving
        takes it only until it is out of reach — but the pricing belongs to whoever knows about threats. So the
        function is injected (`actions.with_exposure`) rather than imported: this module is the solver, and a
        solver that imports the threat model is no longer a fact about arithmetic.
        """
        fn = getattr(self, "_exposure", None)
        return float(fn(self, state)) if fn else 0.0

    # A round builds a hundred and thirty thousand of these (every column, every layer of every descent, every
    # goal drawn). Without slots each one is a dict of six entries; with them it is six pointers. Nothing else
    # about the class changes, and the saving is most of a round's allocation.
    __slots__ = ("name", "effect", "cost_s", "requires", "limit", "tag", "_exposure")

    def __init__(self, name, effect, cost_s, requires=None, limit=None, tag=None):
        if cost_s <= 0:
            raise ValueError(f"action {name!r} must cost time: a free action makes every plan infinite")
        self.name, self.effect, self.cost_s = name, dict(effect), float(cost_s)
        self.requires = dict(requires or {})
        self.limit, self.tag = limit, tag
        self._exposure = None

    def __repr__(self):
        return f"Action({self.name}, {self.cost_s}s)"


class Plan:
    """The solution: how many times to run each action, what it costs, and the state it leaves behind."""

    __slots__ = ("counts", "cost_s", "final", "actions", "order", "shadow")

    def __init__(self, counts, cost_s, final, actions, order=None, shadow=None):
        self.counts, self.cost_s, self.final, self.actions = counts, cost_s, final, actions
        # The descent already emits a runnable order (inputs before the column that eats them), so `steps` returns
        # it rather than re-deriving one. Kept optional: a Plan built by hand in a test has none.
        self.order = order
        # What one more unit of each dimension is worth, in seconds — the marginal cost of reaching it. The dual
        # of this program, and the reason nobody has to enumerate other goals' plans to price what this one
        # unlocks: `reach_cost` computes it for every dimension at once, on the way to solving.
        self.shadow = dict(shadow or {})

    @property
    def empty(self):
        return not self.counts

    def steps(self):
        """(action, times) in an order that can be executed: an action runs only once whatever it consumes exists.

        The matrix says WHAT to run; this says WHEN. A topological order by production suffices because the plan
        is feasible by construction — at every point some remaining action's inputs are satisfied, or the plan
        would not have reached the target.
        """
        if self.order is not None:
            return list(self.order)
        remaining = dict(self.counts)
        have = dict(self.final)
        for a in self.actions:                      # undo the plan: start from the state we began in
            n = remaining.get(a.name, 0)
            for d, delta in a.effect.items():
                have[d] = have.get(d, 0) - delta * n
        out = []
        by_name = {a.name: a for a in self.actions}
        guard = sum(remaining.values()) + 1
        while remaining and guard > 0:
            guard -= 1
            for name in list(remaining):
                a = by_name[name]
                needed = {d: -v for d, v in a.effect.items() if v < 0}
                if all(have.get(d, 0) >= v for d, v in needed.items()) \
                        and all(have.get(d, 0) >= v for d, v in a.requires.items()):
                    n = remaining.pop(name)
                    for d, delta in a.effect.items():
                        have[d] = have.get(d, 0) + delta * n
                    out.append((a, n))
                    break
            else:
                # Nothing is runnable yet: emit the rest in cost order rather than lose the plan. A cycle here is
                # a modelling error (an action that consumes what only it produces), and the caller re-plans when
                # the step fails, so this degrades instead of hanging.
                out += [(by_name[n], c) for n, c in sorted(remaining.items(), key=lambda kv: by_name[kv[0]].cost_s)]
                break
        return out


# ---------------------------------------------------------------------------------------------------- the program

_PRICES = {}


def reach_cost(cols, state):
    """Pure: {dimension: cheapest seconds to obtain one unit}, ignoring how much is needed.

    An ENGINE of `gates.V`: the relaxation belongs to the solver, the question "what is this state worth" belongs
    to the value door.

    The global relaxation, computed once per round and shared by every layer of the descent — it is what lets a
    layer treat "and then the rest of the chain" as a single number instead of unrolling it. A column's cost is its
    own seconds plus the cost of everything it consumes and requires; a dimension's cost is the cheapest column
    that produces it; iterate to a fixed point. Unreachable dimensions stay at infinity, which is how "this world
    has no way to iron" becomes a number rather than a special case.
    """
    return reach_tree(cols, state)[0]


# What makes two relaxations the same question. Not the whole state: this is a table of "cheapest way to get one
# of each dimension", and whether we HOLD something changes it, while how much food is in the bar does not. With
# the raw state as the key the cache missed on every round (food drains continuously) and the tree — a thousand
# calls a round — was rebuilt from nothing each time.
_UNPRICED = ("lever:", "food", "bag_free")


def _state_key(state):
    """Only what a price depends on: what is held, in coarse steps. Holding one plank or four changes what is
    cheapest; holding 17.3 versus 17.4 points of food does not."""
    return tuple(sorted((d, min(int(v), 64)) for d, v in state.items()
                        if v and not str(d).startswith(_UNPRICED)))


def _columns_key(cols):
    """The table's identity: its columns and what they cost.

    Carried ON the table when it is one (`actions.Table` keeps it), computed from the contents otherwise. It is
    NOT keyed by id(): a table is built per round, the old one is collected, and Python hands the same id to the
    next — which served one world's prices for another's ground, and made a room come out cheaper than open air.
    """
    got = getattr(cols, "key", None)
    if got is not None:
        return got
    return tuple(sorted((a.name, a.cost_s) for a in cols))


def reach_tree(cols, state):
    """The same relaxation, keeping the CHOICE: ({dimension: seconds}, {dimension: column that made it cheapest}).

    The choices form a tree — the cheapest route to everything — and that tree is what makes pricing the future
    affordable. Asking honestly what a plan leaves behind means a second relaxation per candidate; reading it off
    this tree is a walk down one route (see `credits`).
    """
    key = (_columns_key(cols), _state_key(state))
    if key in _PRICES:
        return _PRICES[key]
    cost = {d: 0.0 for d, v in state.items() if v > 0}
    via = {}
    for _ in range(len(cols) + 1):
        changed = False
        for a in cols:
            need = sum(cost.get(d, float("inf")) * -v for d, v in a.effect.items() if v < 0) \
                + sum(cost.get(d, float("inf")) for d in a.requires if state.get(d, 0) <= 0)
            if need == float("inf"):
                continue
            total = a.cost_s + need
            for d, v in a.effect.items():
                if v > 0 and total / v < cost.get(d, float("inf")) - 1e-9:
                    cost[d] = total / v
                    via[d] = a
                    changed = True
        if not changed:
            break
    if len(_PRICES) > MEMO_MAX:
        _PRICES.clear()
    _PRICES[key] = (cost, via)
    return cost, via




def cost_of(actions, state, target):
    """Seconds to reach `target`, or None when nothing can. The relaxation: one pass, and a true lower bound —
    what ranking needs, since comparing thirty goals must not cost thirty searches."""
    price = reach_cost(actions, state)
    total = 0.0
    for d, v in target.items():
        short = v - state.get(d, 0)
        if short <= 0:
            continue
        per = price.get(d)
        if per is None or per == float("inf"):
            return None
        total += per * short
    return round(total, 2)


_MEMO = {}            # (columns, state, target) -> Plan. Bounded; cleared when it grows past MEMO_MAX.
MEMO_MAX = 4000


def _memo_key(actions, state, target, integral):
    """What makes two solves the same question: the columns offered, what we hold, what is wanted.

    The same coarse reading of the state as `reach_tree` uses, and for the same reason: a plan does not change
    because the food bar ticked down a tenth, and keying on the raw state meant every round asked a question
    nobody had ever asked before.
    """
    cols = tuple(sorted((a.name, a.cost_s, a.limit) for a in actions))
    return (cols, _state_key(state), tuple(sorted(target.items())), integral)


def solve(actions, state, target, integral=True):
    """Pure: the cheapest plan from `state` reaching `target` ({dimension: minimum}), or raise Unsolvable.

    Layered descent, not one big program. Putting the whole supply chain in a single matrix is correct and it is
    what the first version did — but a bed dragged wool, sheep, travel, planks, logs, trees and a bench into one
    38-column, 53-row problem, and the size grew with the game rather than with the decision. Almost none of that
    is a decision: whether to cut a log is not in question once you know a bed needs planks.

    So one layer at a time. Each layer is a matrix over the few columns that produce what this layer wants, plus one
    "pay for it" column per input priced by `reach_cost` — a global relaxation computed once. The choices stay in
    the matrix (four ways to be sheltered, two ways to reach iron, how many logs per plank); the chain below is a
    number. Typical layer: about ten columns by eight rows, and constant in the size of the game.

    Sharing across layers is kept by carrying one running inventory through the descent, so planks made for a table
    are seen by the bed that comes after.
    """
    need = {d: v for d, v in target.items() if state.get(d, 0) < v}
    if not need:
        return Plan({}, 0.0, dict(state), [], shadow=reach_cost(actions, state))
    # Thirty goals in a round share most of their sub-problems — planks, logs, a bench — and each one used to
    # re-derive them from scratch. The question is fully described by (columns, what we hold, what we want), so
    # the answer can be remembered for as long as those hold.
    key = _memo_key(actions, state, target, integral)
    hit = _MEMO.get(key)
    if hit is not None:
        return hit
    price = reach_cost(actions, state)
    have = dict(state)
    ordered = _expand(actions, have, need, price, integral, depth=0)
    counts, used = {}, []
    for action, n in ordered:
        if action.name not in counts:
            used.append(action)
        counts[action.name] = counts.get(action.name, 0) + n
    cost = sum(a.cost_s * counts[a.name] for a in used)
    out = Plan(counts, round(cost, 2), have, used, order=[(a, n) for a, n in ordered], shadow=price)
    if len(_MEMO) > MEMO_MAX:
        _MEMO.clear()
    _MEMO[key] = out
    return out


def _expand(actions, have, need, price, integral, depth):
    """Depth-first: buy the inputs, then run the actions of this layer. `have` is updated as the plan proceeds."""
    if depth > MAX_DEPTH:
        raise Unsolvable(dict(need))
    outstanding = {d: v for d, v in need.items() if have.get(d, 0) < v}
    if not outstanding:
        return []
    layer = _layer(actions, have, outstanding, price, integral)
    out = []
    for dim, amount in layer["buy"]:
        out += _expand(actions, have, {dim: have.get(dim, 0) + amount}, price, integral, depth + 1)
    for action, n in layer["run"]:
        short = {d: -v * n for d, v in action.effect.items() if v < 0 and have.get(d, 0) < -v * n}
        for dim, amount in short.items():
            out += _expand(actions, have, {dim: amount}, price, integral, depth + 1)
        for dim, floor in action.requires.items():
            if have.get(dim, 0) < floor:
                out += _expand(actions, have, {dim: floor}, price, integral, depth + 1)
        out.append((action, n))
        for d, delta in action.effect.items():
            have[d] = have.get(d, 0) + delta * n
    return out


def _layer(actions, have, need, price, integral):
    """One layer: which columns produce what this layer wants, and how many times.

    Columns are the producers of the wanted dimensions (the cheapest few, by reachable cost) plus, for every input
    those producers consume or require, a synthetic column that supplies one unit at its `reach_cost`. The synthetic
    columns are what keep the matrix shallow: they stand in for the whole chain below without unrolling it.
    """
    wanted = set(need)
    producers = []
    for d in wanted:
        made = [a for a in actions if a.effect.get(d, 0) > 0]
        made.sort(key=lambda a: _priced(a, d, price, have))
        for a in made[:KEEP_PER_DIM]:
            if a not in producers:
                producers.append(a)
    if not producers:
        raise Unsolvable(dict(need))
    inputs = set()
    for a in producers:
        inputs |= {d for d, v in a.effect.items() if v < 0 and d not in wanted}
        inputs |= {d for d in a.requires if d not in wanted}
    stand_ins = []
    for d in sorted(inputs):
        p = price.get(d)
        if p is None or p == float("inf"):
            continue                      # nothing below can supply it; the column that needs it will price as inf
        stand_ins.append(Action(f"buy:{d}", {d: 1}, max(p, 0.01), tag=("buy", d)))
    cols = producers + stand_ins
    dims = sorted(wanted | {d for a in cols for d in a.effect} | {d for a in cols for d in a.requires})
    rows, b = [], []
    for d in dims:
        rows.append([float(a.effect.get(d, 0)) for a in cols])
        b.append(float(max(0, need.get(d, 0))) - float(have.get(d, 0)))
    # A requirement inside the layer is an indicator row: running this column at all needs the thing to exist.
    for j, a in enumerate(cols):
        for d, v in a.requires.items():
            if have.get(d, 0) >= v:
                continue
            scale = float(a.limit if a.limit else BIG) * float(v)
            rows.append([scale * float(o.effect.get(d, 0)) - (float(v) if k == j else 0.0)
                         for k, o in enumerate(cols)])
            b.append(-scale * float(have.get(d, 0)))
    c = [float(a.cost_s) for a in cols]
    upper = [a.limit for a in cols]
    n = _branch_and_bound(rows, b, c, upper) if integral else _relaxed(rows, b, c, upper)
    if n is None:
        raise Unsolvable(dict(need))
    run, buy = [], []
    for j, a in enumerate(cols):
        times = int(-((-n[j] + 1e-6) // 1))
        if times <= 0:
            continue
        if a.tag and a.tag[0] == "buy":
            buy.append((a.tag[1], times))
        else:
            run.append((a, times))
    return {"run": run, "buy": buy}


def _relaxed(rows, b, c, upper):
    lp = _simplex(rows, b, c, [0] * len(c), list(upper))
    return lp[1] if lp else None


def _priced(action, dim, price, have):
    """Seconds per unit of `dim` from this column, counting what it consumes and needs at relaxation prices."""
    spent = sum(price.get(d, float("inf")) * -v for d, v in action.effect.items() if v < 0 and have.get(d, 0) <= 0)
    gates = sum(price.get(d, float("inf")) for d, v in action.requires.items() if have.get(d, 0) < v)
    return (action.cost_s + spent + gates) / action.effect[dim]


def _branch_and_bound(A, b, c, upper, nodes=None):
    """Integer minimum of cᵀn subject to A·n ≥ b, 0 ≤ n ≤ upper. Depth-first on the most fractional variable."""
    nodes = [MAX_NODES] if nodes is None else nodes
    best = [None, None]      # (value, vector)

    def dive(lo, hi):
        if nodes[0] <= 0:
            return
        nodes[0] -= 1
        relaxed = _simplex(A, b, c, lo, hi)
        if relaxed is None:
            return
        value, x = relaxed
        if best[0] is not None and value >= best[0] - EPS:
            return                                   # bound: this subtree cannot beat what we have
        rounded = _round_up(A, b, c, x, hi)          # a feasible answer straight away, to bound the search with
        if rounded is not None and (best[0] is None or rounded[0] < best[0]):
            best[0], best[1] = rounded
        frac = next((j for j, v in enumerate(x) if abs(v - round(v)) > 1e-6), None)
        if frac is None:
            best[0], best[1] = value, x
            return
        floor = int(x[frac] // 1)
        for new_lo, new_hi in ((lo, _with(hi, frac, floor)), (_with(lo, frac, floor + 1), hi)):
            if new_hi[frac] is not None and new_hi[frac] < new_lo[frac]:
                continue
            dive(new_lo, new_hi)

    dive([0] * len(c), list(upper))
    return best[1]


def _round_up(A, b, c, x, hi):
    """The relaxed solution rounded up, if it is feasible: an integer plan in hand before the search starts.

    Rounding up can only add actions, so it stays feasible whenever nothing has an upper bound in the way. Cheap to
    test, and it gives branch and bound a bound from the first node instead of the thousandth — which is the
    difference between finding the good plan and running out of budget next to it.
    """
    n = [float(int(v // 1)) + (1.0 if v - int(v // 1) > 1e-6 else 0.0) for v in x]
    for j, v in enumerate(n):
        if hi[j] is not None and v > hi[j]:
            return None
    for i in range(len(A)):
        if sum(A[i][j] * n[j] for j in range(len(n))) < b[i] - 1e-6:
            return None
    return sum(c[j] * n[j] for j in range(len(n))), n


def _with(bounds, j, value):
    out = list(bounds)
    out[j] = value
    return out


def _simplex(A, b, c, lo, hi):
    """Two-phase simplex over exact rationals. Returns (objective, x) or None when infeasible.

    Variables are shifted by their lower bounds and upper bounds become extra rows, so the tableau only ever sees
    x ≥ 0 — the textbook form, at the price of a few more rows. These programs have tens of rows; exactness is
    worth far more here than speed.
    """
    m0, n = len(A), len(c)
    rows, rhs = [], []
    for i in range(m0):                               # A·n ≥ b  →  -A·(n-lo) ≤ -(b - A·lo)
        shift = sum(A[i][j] * lo[j] for j in range(n))
        rows.append([-A[i][j] for j in range(n)])
        rhs.append(-(b[i] - shift))
    for j in range(n):                                # n_j ≤ hi_j
        if hi[j] is not None:
            row = [0.0] * n
            row[j] = 1.0
            rows.append(row)
            rhs.append(float(hi[j] - lo[j]))
    obj = [float(x) for x in c]
    base = sum(c[j] * lo[j] for j in range(n))
    out = _two_phase(rows, rhs, obj)
    if out is None:
        return None
    value, x = out
    return value + base, [x[j] + lo[j] for j in range(n)]


def _two_phase(rows, rhs, obj):
    """min objᵀx subject to rows·x ≤ rhs, x ≥ 0. Exact rationals. Returns (objective, x) or None if infeasible.

    Big-M in one phase rather than two. The two-phase version needed to drive artificials out of the basis and then
    delete their columns, and the index bookkeeping around that deletion was wrong in exactly the case that matters
    here — a program with both a flipped row and an upper-bound row — so it returned solutions that violated the
    bounds it had been given. One phase has none of those seams: the artificials simply cost M, and a solution that
    still uses one is infeasible. M is derived from the data, so it is always big enough and never a magic number.
    """
    m, n = len(rows), len(obj)
    if m == 0:
        return (0.0, [0.0] * n) if all(v >= 0 for v in obj) else None
    big = (sum(abs(float(v)) for v in obj) + sum(abs(float(v)) for v in rhs)
           + sum(abs(float(v)) for r in rows for v in r) + 1) * (m + n + 1)
    T, basis, cost = [], [], [float(v) for v in obj]
    art_cols = []
    for i in range(m):
        r = float(rhs[i])
        structural = [float(v) for v in rows[i]]
        if r < 0:
            structural = [-v for v in structural]     # Σax ≤ b<0  ⇔  Σ(−a)x ≥ −b: surplus −1, plus an artificial
            r = -r
        T.append((structural, r, float(rhs[i]) < 0))
    width = n + m + sum(1 for _, _, flipped in T if flipped)
    tableau = []
    art_at = n + m
    for i, (structural, r, flipped) in enumerate(T):
        row = structural + [0.0] * (width - n) + [r]
        row[n + i] = -1.0 if flipped else 1.0
        if flipped:
            row[art_at] = 1.0
            basis.append(art_at)
            art_cols.append(art_at)
            art_at += 1
        else:
            basis.append(n + i)
        tableau.append(row)
    cost = cost + [0.0] * (width - n) + [0.0]
    for j in art_cols:
        cost[j] = big
    for i in range(m):                                # price out the starting basis
        f = cost[basis[i]]
        if abs(f) > EPS:
            for j in range(width + 1):
                cost[j] -= f * tableau[i][j]
    if not _pivot_to_optimal(tableau, basis, cost, width):
        return None
    if any(basis[i] in art_cols and abs(tableau[i][-1]) > 1e-7 for i in range(m)):
        return None                                   # an artificial survived: the constraints contradict each other
    x = [0.0] * n
    for i in range(m):
        if basis[i] < n:
            x[basis[i]] = tableau[i][-1]
    return sum(float(obj[j]) * x[j] for j in range(n)), x


def _pivot_to_optimal(T, basis, z, total, guard=600):
    """Bland's rule: the lowest index with a negative reduced cost. Slower than steepest edge, and it cannot cycle —
    these tableaux are degenerate often enough that cycling is a real risk, not a textbook one."""
    for _ in range(guard):
        j = next((k for k in range(total) if z[k] < -EPS), None)
        if j is None:
            return True
        best_i, best_ratio = None, None
        for i in range(len(T)):
            if T[i][j] > EPS:
                ratio = T[i][-1] / T[i][j]
                if best_ratio is None or ratio < best_ratio or (ratio == best_ratio and basis[i] < basis[best_i]):
                    best_i, best_ratio = i, ratio
        if best_i is None:
            return False                              # unbounded: an action that pays for itself, a modelling error
        _pivot(T, basis, best_i, j)
        f = z[j]
        if abs(f) > EPS:
            for k in range(total + 1):
                z[k] -= f * T[best_i][k]
    return False


def _pivot(T, basis, i, j):
    p = T[i][j]
    T[i] = [v / p for v in T[i]]
    for r in range(len(T)):
        if r != i and abs(T[r][j]) > EPS:
            f = T[r][j]
            T[r] = [T[r][k] - f * T[i][k] for k in range(len(T[r]))]
    basis[i] = j
