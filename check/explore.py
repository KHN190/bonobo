"""The explorer: BFS over facts. Successors = the production decision's declared effect (model.step) and every world
move (one fact changed by the world: time, a threat, hp, the player). The oracle judges every edge; D7 judges the
graph's cycles."""
import re
from collections import deque

from . import oracle, round as rnd
from .facts import DIMS, DOMAINS, key, of

# a decision's effect on the facts, read from what the production step declares it gives (its kind and name)
GIVES = (("sleep", {"night": False}), ("wait for day", {"night": False}), ("dig in", {"place": "enclosed"}),
         ("pod", {"place": "enclosed"}), ("wall in", {"place": "enclosed"}), ("hut", {"place": "enclosed"}),
         ("home", {"place": "home"}), ("shelter", {"place": "enclosed"}),
         ("leave the nether", {"dimension": "minecraft:overworld"}), ("threat:", {"threat": False}), ("pickaxe tier", {"pickaxe": None}),
         ("food", {"food": True}), ("eat", {"hunger": "full"}))
# facts the world changes on its own; each move one fact
WORLD = ("night", "threat", "hp", "takeover") + tuple(d.NAME for d in DIMS if getattr(d, "WORLD", False))
PROGRESS = oracle.GIVING      # a step kind that leaves something in the world (E2 live): progress in the bag


def step(facts, d, ctx):
    """model.step: the facts after `d`'s declared effect."""
    out = dict(facts)
    name = (d.name or "").lower()
    for word, change in GIVES:
        if re.search(rf"\b{re.escape(word)}", name):
            for k, v in change.items():
                if k == "pickaxe":
                    tier = [t for t in range(4) if f"tier {t}" in name]
                    if tier:
                        out[k] = max(out[k], tier[0])
                else:
                    out[k] = v
    if ctx.get("step_kind") == "mine" and d.token and "iron" in d.token:
        out["ore"] = "none"
    for dim in DIMS:
        if hasattr(dim, "step"):
            out.update(dim.step(facts, d, ctx))
    return of(**out)


def moves(facts):
    """The world's own moves: one fact of WORLD flipped to each other value."""
    for k in WORLD:
        for v in DOMAINS[k]:
            if v != facts[k]:
                yield of(**dict(facts, **{k: v}))


def starts():
    """Every combination of the facts the world does not change on its own (bag, place, targets, dimension), the
    world's facts at their first value: the explorer's moves reach the rest."""
    import itertools
    rest = [k for k in DOMAINS if k not in WORLD]
    return [of(**dict(zip(rest, vals))) for vals in itertools.product(*(DOMAINS[k] for k in rest))]


# the facts the night/danger/ownership decision reads together (needs.overnight, reflexes' shelter and sleep rows,
# the danger layers, the takeover): their full product. The rest (the bag, the targets in sight, the queue) feed the
# plan's steps: every PAIR of their values is combined (pairwise), not every tuple.
CORE = ("dimension", "night", "hp", "place", "bed", "threat", "takeover", "cooled") \
    + tuple(d.NAME for d in DIMS if getattr(d, "CORE", False))


def pairwise(names):
    """Rows over `names` covering every pair of two facts' values (greedy, deterministic): each row starts from the
    first uncovered pair and fills the other facts one by one with the value covering most uncovered pairs."""
    import itertools
    idx = list(itertools.combinations(range(len(names)), 2))
    left = {(i, j, a, b) for i, j in idx for a in DOMAINS[names[i]] for b in DOMAINS[names[j]]}
    rows = []
    while left:
        i0, j0, a0, b0 = min(left, key=lambda p: (p[0], p[1], DOMAINS[names[p[0]]].index(p[2]),
                                                  DOMAINS[names[p[1]]].index(p[3])))
        row = {i0: a0, j0: b0}
        for k in range(len(names)):
            if k not in row:
                row[k] = max(DOMAINS[names[k]], key=lambda v: sum(
                    (min(k, m), max(k, m), *((v, row[m]) if k < m else (row[m], v))) in left for m in row))
        rows.append(row)
        left -= {(i, j, row[i], row[j]) for i, j in idx}
    return [{names[k]: v for k, v in r.items()} for r in rows]


def states():
    """The abstract states judged: CORE's full product × the other base facts pairwise, and every fact (the
    dimensions' too, check/dims) pairwise with every other. starts() × the world's moves reach the whole product
    (moves flip each WORLD fact to every value; model.step stays inside it), so this is a reduction of the reachable
    set, stated: an interaction of three non-CORE facts, or of a dimension with two others, is not combined here —
    check.fuzz searches those, and its kept states (the corpus) are judged beside these."""
    import itertools
    dims = {d.NAME for d in DIMS}
    rest = [k for k in DOMAINS if k not in CORE and k not in dims]
    out = {key(f): f for vals in itertools.product(*(DOMAINS[k] for k in CORE)) for extra in pairwise(rest)
           if (f := of(**dict(zip(CORE, vals)), **extra))}
    out.update({key(f): f for row in pairwise(list(DOMAINS)) if (f := of(**row))})
    return list(out.values())


def made_progress(f, d, ctx):
    """An edge D7 does not hold against: a step that leaves something in the world, a seek (it ends with the sought
    thing seen and noted: memory, which no fact here reads), a danger answer, or a round while the player holds the
    body — what happens next is the player's move (S6), not a decision's."""
    return ctx.get("step_kind") in PROGRESS + ("seek",) or d.layer in oracle.DANGER_LAYERS or bool(f["takeover"])


def judge(f):
    """One state: the production decision, its successor, the oracle's verdicts (picklable for the shards)."""
    d, got, ctx = rnd.decide(f)
    after = step(f, d, ctx)
    progress = made_progress(f, d, ctx)
    return key(f), key(after), d, progress, oracle.violations(f, d, after, ctx), dict(got) != dict(f), dict(got)


def explore(limit, on_edge):
    """BFS from starts() over at most `limit` states; on_edge(before, d, after, ctx, violations) per decision."""
    seen, queue, graph, roundtrip = set(), deque(starts()), {}, []
    while queue and len(seen) < limit:
        f = queue.popleft()
        k = key(f)
        if k in seen:
            continue
        seen.add(k)
        d, got, ctx = rnd.decide(f)
        if dict(got) != dict(f):
            roundtrip.append((f, got))
        after = step(f, d, ctx)
        on_edge(f, d, after, ctx, oracle.violations(f, d, after, ctx))
        graph[k] = (key(after), d, made_progress(f, d, ctx))
        queue.append(after)
        queue.extend(moves(f))
    return seen, graph, roundtrip


def cycles(graph):
    """D7: decision-only cycles (no world move) whose decisions leave nothing in the world — remaining never falls."""
    out = []
    for k, (nxt, d, progress) in graph.items():
        path, cur, ok = [k], nxt, progress
        while cur in graph and cur not in path and len(path) < 50:
            path.append(cur)
            cur, dd, pr = graph[cur]
            ok = ok or pr
        if cur in path and not ok:
            out.append((k, d.name, len(path) - path.index(cur)))
    return out
