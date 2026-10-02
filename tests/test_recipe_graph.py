"""The recipe data has no cycle: no item is, through its recipes and smelts, an ingredient of itself. The planners'
depth guards (planner.MAX_DEPTH, solve.EXPAND_MAX_DEPTH) stand in for this invariant; held here on the data instead."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.data import GROUPS, RECIPES, SMELTS  # noqa: E402


def _ids(token, groups):
    """A recipe cell's token as the item ids it stands for (a group: its members)."""
    return groups.get(token, [token])


def edges(recipes, smelts, groups):
    """Pure: {item: the items it is made from} over every recipe and smelt."""
    out = {}
    for item, (pattern, _n) in recipes.items():
        out.setdefault(item, set()).update(i for t in pattern if t for i in _ids(t, groups))
    for item, raw in smelts.items():
        out.setdefault(item, set()).update(_ids(raw, groups))
    return out


def cycle(graph):
    """Pure: one cycle [a, b, …, a] in `graph`, or None (iterative depth-first search, three colours)."""
    colour, parent = {}, {}
    for root in graph:
        if colour.get(root):
            continue
        stack = [(root, iter(graph.get(root, ())))]
        colour[root] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = 2
                stack.pop()
            elif colour.get(nxt) == 1:
                path = [nxt, node]
                while path[-1] != nxt:
                    path.append(parent[path[-1]])
                return path[::-1]
            elif not colour.get(nxt):
                colour[nxt], parent[nxt] = 1, node
                stack.append((nxt, iter(graph.get(nxt, ()))))
    return None


class RecipeGraph(unittest.TestCase):
    ROWS = [
        ("a chain: planks from logs, sticks from planks", {"stick": (["planks", None], 4), "planks": (["log"], 4)},
         {}, {"planks": ["planks"]}, False),
        ("must fail: a block back to its ingots and the ingots from the block",
         {"block": (["ingot"] * 9, 1), "ingot": (["block"], 9)}, {}, {}, True),
        ("must fail: a cycle through a smelt", {"a": (["b"], 1)}, {"b": "a"}, {}, True),
        ("must fail: a cycle through a group's member", {"x": (["grp"], 1)}, {}, {"grp": ["y", "x"]}, True),
    ]

    def test_rows(self):
        for why, recipes, smelts, groups, has in self.ROWS:
            with self.subTest(why):
                self.assertIs(cycle(edges(recipes, smelts, groups)) is not None, has)

    def test_the_game_data_has_none(self):
        self.assertIsNone(cycle(edges(RECIPES, SMELTS, GROUPS)))


if __name__ == "__main__":
    unittest.main()
