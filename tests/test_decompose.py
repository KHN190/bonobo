"""The decomposition contract: what the planner (planner.Planner) and the column solver (solve over actions.table)
may assume about the knowledge they plan from, and what every plan they return must do when replayed.

Four properties, each a list of violations that must be empty (or exactly the gaps named below):
  1. every column and recipe declares what it does (an effect; a pattern and an output);
  2. closure: everything a column consumes or requires is made by some column, or is a bag-only variant of a group
     that is made;
  3. replay: every goal, from an empty bag, through both planners, replayed by bag arithmetic alone — inputs held
     before each step, the goal held after the last, within a step bound;
  4. a source removed makes the goal unplannable — an answer, not a hang and not a partial plan.
"""
import os
import sys
import unittest
from collections import Counter
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, data, knowledge  # noqa: E402
from bonobo.data import GROUPS, TOOL_MATERIAL_FOR_TIER, mid  # noqa: E402
from bonobo.planner import NullCost, Planner, Unplannable  # noqa: E402
from bonobo.solve import Action, Unsolvable, solve  # noqa: E402
from tests.world import places  # noqa: E402

START = {"bag_free": 20}
STEP_BOUND = 20          # fixture: planner steps from an empty bag, at most (the iron pickaxe: 11)
SOLVE_BOUND = 50         # fixture: solver columns run from an empty bag, at most (seeks included; a dispenser: 45)

# Production gaps this contract found, pinned so that fixing one — or a new one appearing — fails here.
GAPS = {
    "closure": {"minecraft:water_bucket"},             # farm:wheat consumes it; source() says fill, no column fills
    "planner": {"minecraft:bread"},                    # no wheat source for the planner; the solver farms it
}


def real_table():
    return actions.table(places(20.0), dict(START))


# ---------------------------------------------------------------------------------------------- the checks (pure)
def undeclared(columns, recipes):
    """Columns with no effect, recipes with an empty pattern or no output: by name."""
    out = [a.name for a in columns if not any(v for v in a.effect.values())]
    out += [name for name, (pattern, n) in recipes.items() if not any(pattern) or n < 1]
    return out


def variant_of_made_group(dim, made):
    """A variant (minecraft:oak_log) whose group (log) some column makes: held stock only, by design."""
    return any(g in made and dim in (mid(m) for m in members) for g, members in GROUPS.items())


def unclosed(columns):
    """Dims consumed or required that no column makes and no made group covers."""
    made = {d for a in columns for d, v in a.effect.items() if v > 0}
    used = {d for a in columns for d, v in a.effect.items() if v < 0} | {d for a in columns for d in a.requires}
    return {d for d in used - made if not variant_of_made_group(d, made)}


def ungrounded(tokens, source):
    """Tokens `source` cannot ground in a base (mine, hunt, gather, take, fill of a grounded item): a broken chain
    or a cycle with no base entry. Least fixed point, so a cycle never grounds itself."""
    def inputs(src):
        if src[0] == "craft":
            return {t for t in src[1] if t}
        if src[0] in ("smelt", "fill"):
            return {src[1]}
        return set()
    grounded, changed = set(), True
    while changed:
        changed = False
        for t in tokens:
            src = source(t)
            if t not in grounded and src is not None and inputs(src) <= grounded and \
                    (src[0] not in ("craft", "smelt", "fill") or inputs(src)):
                grounded.add(t)
                changed = True
    return set(tokens) - grounded


def all_tokens(source, roots):
    """Every token reachable from `roots` through `source`'s inputs."""
    seen, todo = set(), list(roots)
    while todo:
        t = todo.pop()
        if t in seen:
            continue
        seen.add(t)
        src = source(t)
        if src and src[0] == "craft":
            todo += [x for x in src[1] if x]
        elif src and src[0] in ("smelt", "fill"):
            todo.append(src[1])
    return seen


TIER = {m: t for t, m in TOOL_MATERIAL_FOR_TIER.items()}


def replay_steps(steps, goal):
    """Bag arithmetic over planner.Steps: [(step, missing)] where something was not held, and the end bag."""
    bag, tools, bad = Counter(), [], []
    for st in steps:
        need = dict(st.detail.get("inputs", {}))
        src = knowledge.source(st.token)
        if st.kind == "craft" and src and src[0] == "craft" and len(src[1]) == 9:
            need.setdefault("minecraft:crafting_table", 0)
            if bag["minecraft:crafting_table"] < 1:
                bad.append((str(st), "minecraft:crafting_table"))
        if st.kind == "smelt" and bag["minecraft:furnace"] < 1:
            bad.append((str(st), "minecraft:furnace"))
        if st.kind == "mine" and st.detail.get("tier") is not None and \
                not any(k == "pickaxe" and t >= st.detail["tier"] for k, t in tools):
            bad.append((str(st), f"pickaxe tier {st.detail['tier']}"))
        for tok, n in need.items():
            if n and bag[tok] < n:
                bad.append((str(st), tok))
            bag[tok] -= n
        bag[st.token] += st.count
        mat_kind = st.token.split(":")[-1].split("_", 1)
        if len(mat_kind) == 2 and mat_kind[0] in TIER:
            tools.append((mat_kind[1], TIER[mat_kind[0]]))
    if goal[0] == "tool":
        held = any(k == goal[1] and t >= goal[2] for k, t in tools)
    else:
        held = bag[goal[0]] >= goal[1]
    return bad, held


def replay_plan(plan, start, target):
    """Bag arithmetic over a solve plan: [(action, dim)] run before it held, and whether the target is held."""
    held, early = dict(start), []
    for action, times in plan.steps():
        early += [(action.name, d) for d, need in action.requires.items() if held.get(d, 0) < need]
        early += [(action.name, d) for d, v in action.effect.items() if v < 0 and held.get(d, 0) < -v * times]
        for d, v in action.effect.items():
            held[d] = held.get(d, 0) + v * times
    return early, all(held.get(d, 0) >= n for d, n in target.items())


def goals():
    """Every recipe output a plan can start from nothing (a variant is made from its group: the group's token), and
    upkeep's goals (a bed, an iron pickaxe)."""
    variants = {mid(m) for g in knowledge.GROUP_RECIPES for m in GROUPS.get(g, ())}
    outs = sorted(set(data.RECIPES) - variants) + sorted(knowledge.GROUP_RECIPES)
    return [(o, 1) for o in outs] + [("bed", 1), ("tool", "pickaxe", 2)]


# ------------------------------------------------------------------------------------------------------ the tests
class Declared(unittest.TestCase):
    def test_the_checker_names_what_is_undeclared(self):
        real = real_table()
        rows = [("the real table and recipes", real, dict(data.RECIPES), []),
                ("a column that does nothing", real + [Action("craft:nothing", {}, 1.0)], {}, ["craft:nothing"]),
                ("a column whose effect is all zero", [Action("x", {"a": 0}, 1.0)], {}, ["x"]),
                ("a recipe with no pattern", [], {"minecraft:thing": ([None] * 4, 1)}, ["minecraft:thing"]),
                ("a recipe with no output", [], {"minecraft:thing": (["log", None, None, None], 0)},
                 ["minecraft:thing"])]
        for name, columns, recipes, want in rows:
            with self.subTest(name):
                self.assertEqual(undeclared(columns, recipes), want)


class Closed(unittest.TestCase):
    def test_every_dim_used_is_made(self):
        real = real_table()
        rows = [("the real table: only the pinned gaps", real, GAPS["closure"]),
                ("a broken chain: an input nothing makes", [Action("craft:a", {"a": 1, "b": -1}, 1.0)], {"b"}),
                ("a requirement nothing makes", [Action("work", {"a": 1}, 1.0, requires={"key": 1})], {"key"}),
                ("a variant of a made group is held stock, not a gap",
                 [Action("gather:log", {"log": 1}, 1.0), Action("craft:p", {"p": 1, "minecraft:oak_log": -1}, 1.0)],
                 set())]
        for name, columns, want in rows:
            with self.subTest(name):
                self.assertEqual(unclosed(columns), want)

    def test_every_token_is_grounded_in_a_base(self):
        roots = [g[0] for g in goals() if g[0] != "tool"] + ["minecraft:iron_pickaxe"]
        cyclic = {"a": ("craft", ["b"], 1), "b": ("craft", ["a"], 1)}
        rows = [("the real knowledge", knowledge.source, roots, GAPS["planner"] & {"minecraft:bread"}),
                ("a cycle with no base entry", lambda t: cyclic.get(t), ["a"], {"a", "b"}),
                ("a craft from nothing known", lambda t: {"a": ("craft", ["zzz"], 1)}.get(t), ["a"], {"a", "zzz"}),
                ("a chain down to a mine", lambda t: {"a": ("craft", ["b"], 1), "b": ("mine", ["x"], 0)}.get(t),
                 ["a"], set())]
        for name, source, rs, want in rows:
            with self.subTest(name):
                self.assertEqual(ungrounded(all_tokens(source, rs), source) & (want | {"a", "b", "zzz"} | set(rs)),
                                 want)


class Replayed(unittest.TestCase):
    def test_the_planner_from_an_empty_bag(self):
        failed, bad = set(), []
        for goal in goals():
            need = [goal] if goal[0] == "tool" else [(goal[0], goal[1])]
            try:
                steps = Planner({}, [], NullCost()).plan(need)
            except Unplannable:
                failed.add(goal[0])
                continue
            early, held = replay_steps(steps, goal)
            bad += [(goal, e) for e in early] + ([(goal, "not held at the end")] if not held else []) + \
                ([(goal, f"{len(steps)} steps")] if len(steps) > STEP_BOUND else [])
        self.assertEqual((bad, failed), ([], GAPS["planner"]))

    def test_the_solver_from_an_empty_bag(self):
        table, bad, failed = real_table(), [], set()
        for goal in goals():
            target = {actions.tool_dim(goal[1], goal[2]): 1} if goal[0] == "tool" else {goal[0]: goal[1]}
            try:
                plan = solve(table, dict(START), target)
            except Unsolvable:
                failed.add(goal[0])
                continue
            early, held = replay_plan(plan, START, target)
            steps = plan.steps()
            bad += [(goal, e) for e in early] + ([(goal, "not held at the end")] if not held else []) + \
                ([(goal, f"{len(steps)} steps")] if len(steps) > SOLVE_BOUND else [])
        self.assertEqual((bad, failed), ([], set()))

    def test_the_replay_catches_a_step_out_of_order(self):
        """The replay itself: a plan whose steps are swapped fails it (else a green replay proves nothing)."""
        steps = Planner({}, [], NullCost()).plan([("tool", "pickaxe", 0)])
        rows = [("as planned", steps, True), ("the pickaxe before its sticks", [steps[-1]] + steps[:-1], False),
                ("the table missing", [s for s in steps if s.token != "minecraft:crafting_table"], False),
                ("the last step dropped", steps[:-1], False)]
        for name, st, ok in rows:
            with self.subTest(name):
                early, held = replay_steps(st, ("tool", "pickaxe", 0))
                self.assertEqual(not early and held, ok, early)


class SourceRemoved(unittest.TestCase):
    """A source taken away: the goal is unplannable, answered at once — no hang, no partial plan."""

    def test_the_planner(self):
        rows = [("nothing removed: planned", {}, {}, ("bed", 1), True),
                ("no wool from sheep: no bed", {"HUNT": ["wool"]}, {}, ("bed", 1), False),
                ("no stick recipe: no pickaxe", {}, {"minecraft:stick": None}, ("tool", "pickaxe", 0), False),
                ("no iron ore: no iron ingot", {"MINE": ["minecraft:raw_iron"]}, {}, ("minecraft:iron_ingot", 1),
                 False),
                ("no furnace recipe: no iron ingot", {}, {"minecraft:furnace": None}, ("minecraft:iron_ingot", 1),
                 False)]
        for name, drop_tables, drop_recipes, goal, ok in rows:
            with self.subTest(name):
                patches = [mock.patch.dict(getattr(knowledge, t)) for t in drop_tables] + \
                    [mock.patch.dict(knowledge.RECIPES)]
                for p in patches:
                    p.start()
                try:
                    for t, keys in drop_tables.items():
                        for k in keys:
                            del getattr(knowledge, t)[k]
                    for k in drop_recipes:
                        del knowledge.RECIPES[k]
                    need = [goal] if goal[0] == "tool" else [goal]
                    try:
                        steps = Planner({}, [], NullCost()).plan(need)
                        got = replay_steps(steps, goal)[1]
                    except Unplannable:
                        got = False
                    self.assertEqual(got, ok)
                finally:
                    for p in reversed(patches):
                        p.stop()

    def test_the_solver(self):
        table = real_table()

        def without(pred):
            return [a for a in table if not pred(a)]
        rows = [("nothing removed: solved", table, {"bed": 1}, True),
                ("no column makes wool, no bed to take: no bed",
                 without(lambda a: a.effect.get("wool", 0) > 0 or a.name == "take:bed"), {"bed": 1}, False),
                ("no stick column: no stone pickaxe", without(lambda a: a.name == "craft:minecraft:stick"),
                 {"minecraft:stone_pickaxe": 1}, False),
                ("no iron mined, none taken: no ingot",
                 without(lambda a: a.effect.get("minecraft:raw_iron", 0) > 0 or a.effect.get("minecraft:iron_ingot", 0) > 0
                         and not a.name.startswith("smelt:")), {"minecraft:iron_ingot": 1}, False)]
        for name, columns, target, ok in rows:
            with self.subTest(name):
                try:
                    plan = solve(columns, dict(START), target)
                    got = replay_plan(plan, START, target)[1]
                except Unsolvable:
                    got = False
                self.assertEqual(got, ok)


if __name__ == "__main__":
    unittest.main()
