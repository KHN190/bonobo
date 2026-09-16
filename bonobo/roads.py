"""Road network: legs that were really travelled (start, end, seconds) become edges of a waypoint graph, so a later
trip between known places (portal ↔ base ↔ fortress) follows proven legs instead of planning from scratch.

Pure functions only; memory stores the legs (`mem.data["roads"][dimension]`), nav.go_to reads and writes them.
"""
import heapq
import math

SNAP = 6            # endpoints within this many blocks are the same waypoint
WALK_S = 0.2        # seconds per block for the unknown direct part (same unit as the mod's path costs)
MAX_LEGS = 400


def _key(p):
    return tuple(int(round(c)) for c in p)


def add_leg(roads, a, b, seconds, now):
    """Pure: record a travelled leg (both directions). Keeps the fastest time seen and the newest use."""
    a, b = _key(a), _key(b)
    if math.dist(a, b) < SNAP:
        return roads
    for leg in roads:
        if (math.dist(leg["a"], a) < SNAP and math.dist(leg["b"], b) < SNAP) or \
                (math.dist(leg["a"], b) < SNAP and math.dist(leg["b"], a) < SNAP):
            leg["s"] = min(leg["s"], seconds)
            leg["used"] = now
            return roads
    roads.append({"a": list(a), "b": list(b), "s": round(seconds, 1), "used": now})
    roads.sort(key=lambda leg: -leg["used"])
    del roads[MAX_LEGS:]
    return roads


def route(roads, start, goal):
    """Pure: the fastest chain of known legs from start to goal, with unknown direct parts costed at WALK_S per block.
    Returns [waypoint, ..., goal] (without start) or [goal] when no known leg helps."""
    start, goal = _key(start), _key(goal)
    nodes = [start, goal]

    def node_of(p):
        for i, n in enumerate(nodes):
            if math.dist(n, p) < SNAP:
                return i
        nodes.append(_key(p))
        return len(nodes) - 1

    edges = {}
    for leg in roads:
        i, j = node_of(leg["a"]), node_of(leg["b"])
        for u, v in ((i, j), (j, i)):
            edges.setdefault(u, []).append((v, leg["s"]))
    # Direct (unknown) moves between any two nodes, so a known leg is used only where it saves time.
    dist = {0: 0.0}
    prev = {}
    heap = [(0.0, 0)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == 1:
            break
        if d > dist.get(u, math.inf):
            continue
        options = list(edges.get(u, []))
        options += [(v, math.dist(nodes[u], nodes[v]) * WALK_S) for v in range(len(nodes)) if v != u]
        for v, w in options:
            if d + w < dist.get(v, math.inf):
                dist[v] = d + w
                prev[v] = u
                heapq.heappush(heap, (d + w, v))
    path, u = [], 1
    while u != 0:
        path.append(nodes[u])
        u = prev[u]
    return list(reversed(path))
