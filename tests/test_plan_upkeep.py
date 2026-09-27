"""Test point C — the new brain's pure layer, offline: readings in, a conclusion out.

  PLANS       (goal, sweep slice) → the plan contains / omits these steps, over every world of the slice
              (tests/world.worlds: what is around × what the body is × what the bag holds)
  CHEAPER     the same goal with the alternatives placed differently → the cheaper one is planned
  REPAIRS     a held plan, then an event that changed the bag → the plan decompose makes from the new bag (what is
              held is skipped, half-done work resumes by the remaining AMOUNT); saved plans survive a restart
  UPKEEP      a /state + /inventory + the few world reads the table makes → the row that takes the round, and the
              goals it puts at the front of the queue (food / bed with LEAD lead time, broken tool by tier, path
              blocked with nothing to bridge with)
  RETRY       a sequence of attempt outcomes (exceptions) → counts, escalation, cooling, bans; an interruption never
              counts, bans or cools
  QUEUE       a sequence of queue operations → task states and the head

Plans are made by the real planner and cost model over readings (`world.cost`: what /find saw). The upkeep table is
the real `needs.Needs` and `reflexes.Maintain` run by a real (unstarted) Brain, its three world reads answered from the row
(`skills.enclosed`, a bed seen by /find); any other request fails the test.
"""
import json
import os
import sys
import tempfile
import time
import types as pytypes
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, decompose, goals, nav, needs, planner, reflexes, retry, skillcore, skills, tasks  # noqa: E402
from bonobo import brain as brainmod  # noqa: E402  (imports every skill module: `handles` needs the registry)
from bonobo import skill as skillkit  # noqa: E402
from bonobo.data import COVERED_SKY, bare  # noqa: E402
from bonobo.knowledge import food_count  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import Unplannable  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402
from tests.world import (PLANNER_DIMS, bag, cost, full_bag, inventory, places, slot, snapshot, state,  # noqa: E402
                         worlds)

OVER, NETHER = "minecraft:overworld", "minecraft:the_nether"


def has(steps, kind, token):
    return any(s.kind == kind and bare(s.token) == bare(token) for s in steps)


def pair(kind, token):
    return kind, bare(token)


def pairs(steps):
    """The plan as {(kind, token)}: what an assertIn / assertNotIn names exactly."""
    return {(s.kind, bare(s.token)) for s in steps}


def plan(goal, snap, seen=None, pending=None, c=None):
    c = c if c is not None else cost(snap, **(seen or {}))
    return decompose.decompose(snap.inv, goal, c, pending=pending)


PICK1 = goals.have(("tool", "pickaxe", 1))
IRON3 = goals.have(("minecraft:iron_ingot", 3))
FOOD8 = goals.have(("food", 8))
BED = goals.have(("bed", 1))

# (goal, slice of the sweep, contains, omits). An empty `contains` with omits=ANY means: no step at all.
ANY = "any"
PLANS = [
    (PICK1, {"stock": ["none", "full_bag"]},
     [("gather", "log"), ("craft", "minecraft:wooden_pickaxe"), ("mine", "stone"), ("craft", "minecraft:stone_pickaxe")],
     []),
    (PICK1, {"stock": "wood_tools"}, [("mine", "stone"), ("craft", "minecraft:stone_pickaxe")],
     [("gather", "log"), ("craft", "minecraft:crafting_table"), ("craft", "minecraft:wooden_pickaxe")]),
    (PICK1, {"stock": "worn_pickaxe"}, [("craft", "minecraft:stone_pickaxe")], []),    # 1 durability left
    (PICK1, {"stock": ["stone_tools", "kit"]}, [], ANY),                                # goal met: nothing
    (IRON3, {"stock": "stone_tools"}, [("mine", "minecraft:raw_iron"), ("smelt", "minecraft:iron_ingot")],
     [("craft", "minecraft:stone_pickaxe"), ("gather", "log")]),
    (IRON3, {"stock": "none"}, [("craft", "minecraft:stone_pickaxe"), ("mine", "minecraft:raw_iron"),
                                ("smelt", "minecraft:iron_ingot")], []),
    (IRON3, {"stock": "iron"}, [], ANY),
    (FOOD8, {"stock": "kit"}, [], ANY),
    (FOOD8, {"stock": "none", "resource": "herd"}, [("hunt", "minecraft:beef"), ("smelt", "minecraft:cooked_beef")],
     [("hunt", "minecraft:porkchop")]),
    (BED, {"stock": "none", "resource": "herd"}, [("hunt", "wool"), ("craft", "bed")], []),
    (goals.have(("minecraft:water_bucket", 1)), {"stock": "kit"}, [("fill", "minecraft:water_bucket")],
     [("craft", "minecraft:bucket")]),
    (goals.make("milestone", name="nether kit"), {"stock": "kit"}, [], ANY),
    (goals.make("milestone", name="stone tools"), {"stock": "none"},
     [("craft", "minecraft:stone_pickaxe"), ("craft", "minecraft:stone_sword"), ("craft", "minecraft:stone_axe")], []),
    (goals.make("build", bp="shelter"), {"stock": "none"}, [("mine", "stone"), ("build", "shelter")], []),
    (goals.make("goto", pos=[100, 64, 0]), {}, [("goto", "pos")], []),
    (goals.make("road", a=[0, 64, 0], b=[200, 64, 0]), {}, [("goto", "pos")], []),
    (goals.make("sleep"), {}, [("sleep", "bed")], []),
    (goals.make("skill", name="chop", args=[4]), {}, [("skill", "chop")], []),
]
UNPLANNABLE = [
    ("a skill nobody registered", goals.make("skill", name="fly_to_the_moon")),
    ("a template nobody knows", {"goal": "dance", "args": {}}),
    ("an item with no source", goals.have(("minecraft:command_block", 1))),
    ("bedrock", goals.have(("minecraft:bedrock", 1))),
    ("one plannable need and one not", goals.have(("log", 2), ("minecraft:spawner", 1))),
]
# (situation, goal, solver asked, skills taken out of the registry) → Unplannable from decompose
UNPLANNABLE_BY = [
    ("a step no registered skill provides", goals.have(("log", 4)), None, ("chop",)),
    ("a solver nobody registered", goals.have(("log", 4)), "zzz", ()),
    ("the column solver without a memory", goals.have(("minecraft:stick", 4)), "solve", ()),
    ("smelting with no smelting skill", goals.have(("minecraft:iron_ingot", 3)), None,
     ("smelt", "load_smelter", "start_smelt_job")),
    ("meat with no hunter", goals.have(("minecraft:beef", 2)), None, ("hunt",)),
]
# (situation, goal, the task's own solver) → the brain's replan still plans: the first solver could not, the rest can
FALLBACK = [
    ("a milestone asks solve first; without a memory it cannot, the planner can",
     goals.make("milestone", name="stone tools"), None, ("craft", "minecraft:stone_pickaxe")),
    ("an unknown solver named on the task", goals.have(("log", 4)), "zzz", ("gather", "log")),
    ("the food milestone: solve cannot without a memory, the planner hunts", goals.make("milestone", name="food"),
     None, ("hunt", "minecraft:porkchop")),
    ("a task naming solve for sticks, no memory: the planner crafts", goals.have(("minecraft:stick", 4)), "solve",
     ("craft", "minecraft:stick")),
]


class Plans(unittest.TestCase):
    def test_goal_to_steps_over_the_sweep(self):
        for goal, where, contains, omits in PLANS:
            for w in worlds(**{k: v for k, v in where.items()}) if where else worlds():
                snap = w.snapshot()
                with self.subTest(goal=goals.describe(goal), world=w):
                    steps = decompose.decompose(snap.inv, goal, w.cost())
                    for kind, token in contains:
                        self.assertIn(pair(kind, token), pairs(steps), f"no {kind} {token} in {list(map(str, steps))}")
                    if omits == ANY:
                        self.assertEqual(steps, [], "the goal is met: no action")
                        continue
                    for kind, token in omits:
                        self.assertNotIn(pair(kind, token), pairs(steps), f"{kind} {token} planned: {list(map(str, steps))}")
                    for s in steps:
                        self.assertTrue(skillkit.handles(s), f"{s}: no skill provides it")
                        self.assertGreaterEqual(s.est, 0)
                    # The plan's first runnable step exists from this very bag: the cheap check can start it.
                    if steps:
                        self.assertIsNotNone(planner.first_runnable(steps, snap.inv))

    def test_goal_to_steps_ordered(self):
        """Inputs before what consumes them: every craft/smelt step's inputs are produced earlier or held."""
        for goal, where, _c, _o in PLANS:
            w = next(iter(worlds(**where))) if where else next(iter(worlds()))
            snap = w.snapshot()
            steps = decompose.decompose(snap.inv, goal, w.cost())
            with self.subTest(goal=goals.describe(goal)):
                made, orphans = set(), []
                for s in steps:
                    orphans += [(str(s), tok) for tok in s.detail.get("inputs", {})
                                if bare(tok) not in made and snap.inv.count(tok) == 0]
                    made.add(bare(s.token))
                self.assertEqual(orphans, [], "inputs neither held nor made before the step that consumes them")

    def test_unplannable(self):
        for name, goal in UNPLANNABLE:
            with self.subTest(name), self.assertRaises((Unplannable, ValueError)):
                plan(goal, snapshot())
        for name, goal, solver, removed in UNPLANNABLE_BY:
            with self.subTest(name), mock.patch.dict(skillkit.REGISTRY), self.assertRaises(Unplannable):
                for n in removed:
                    skillkit.REGISTRY.pop(n)
                decompose.decompose(snapshot().inv, goal, cost(snapshot(), oak_log=5), solver=solver)

    def test_replan_falls_back(self):
        for name, goal, own, step in FALLBACK:
            with self.subTest(name):
                task = {"id": "t1", "goal": goal["goal"], "args": goal["args"], **({"solver": own} if own else {})}
                held, why = brainmod.replan(task, goal, snapshot(), cost(snapshot(), oak_log=5, stone=2))
                self.assertIsNone(why)
                self.assertIn(pair(*step), pairs(held["steps"]), list(map(str, held["steps"])))

    def test_the_sweep_is_the_whole_product(self):
        n = sum(1 for _ in worlds())
        from tests.world import DIMS
        expected = 1
        for d in PLANNER_DIMS:
            expected *= len(DIMS[d])
        self.assertEqual(n, expected)


def _fixed(steps):
    return lambda inv, needs, cost, pending=None: list(steps)


def _refuses(inv, needs, cost, pending=None):
    raise Unplannable("this solver cannot")


MARK = planner.Step("craft", "minecraft:stick", 4)
# (situation, registered solvers in order, solver asked for, needs) → the steps, or the exception
SOLVERS = [
    ("no needs: nothing to solve", [("a", _refuses)], None, [], []),
    ("the first that plans wins", [("a", _fixed([MARK])), ("b", _refuses)], None, [("minecraft:stick", 4)], [MARK]),
    ("one that cannot hands on to the next", [("a", _refuses), ("b", _fixed([MARK]))], None,
     [("minecraft:stick", 4)], [MARK]),
    ("a named solver is the only one asked", [("a", _fixed([MARK])), ("b", _refuses)], "b",
     [("minecraft:stick", 4)], Unplannable),
    ("an unknown name", [("a", _fixed([MARK]))], "zzz", [("minecraft:stick", 4)], Unplannable),
    ("none can", [("a", _refuses), ("b", _refuses)], None, [("minecraft:stick", 4)], Unplannable),
]


class Solvers(unittest.TestCase):
    def test_registry_order_and_fallback(self):
        for name, solvers, asked, needs, want in SOLVERS:
            with self.subTest(name), mock.patch.dict(decompose.SOLVERS, dict(solvers), clear=True), \
                    mock.patch.object(decompose, "ORDER", [n for n, _ in solvers]):
                if isinstance(want, type):
                    with self.assertRaises(want):
                        decompose.solve_needs(inventory_bag(), needs, None, asked)
                else:
                    self.assertEqual(decompose.solve_needs(inventory_bag(), needs, None, asked), want)

    # (goal, the task's own solver or None) → the solver asked first by the brain's replan
    FIRST = [(goals.make("milestone", name="stone tools"), None, "solve"),
             (goals.have(("log", 4)), None, "planner"),
             (goals.make("milestone", name="food"), "planner", "planner"),
             (goals.have(("log", 4)), "solve", "solve")]

    def test_which_solver_goes_first(self):
        for goal, own, want in self.FIRST:
            asked = []

            def rec(name):
                return lambda inv, needs, cost, pending=None: asked.append(name) or []
            task = {"id": "t1", "goal": goal["goal"], "args": goal["args"], **({"solver": own} if own else {})}
            self.assertEqual(decompose.ORDER[0], "planner", "the planner is the default for everything else")
            with self.subTest(goal=goals.describe(goal), own=own), \
                    mock.patch.dict(decompose.SOLVERS, {"planner": rec("planner"), "solve": rec("solve")}, clear=True):
                held, why = brainmod.replan(task, goal, snapshot(), cost())
                self.assertEqual(asked[:1], [want])
                self.assertIsNone(why)

    def test_the_column_solver(self):
        """solve needs a snapshot and a memory; given both, it plans with steps some skill provides."""
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            snap = snapshot(inv=inventory(("oak_log", 4)))
            with self.assertRaises(Unplannable):
                decompose.decompose(snap.inv, PICK1, cost(snap, oak_log=6), solver="solve")
            steps = decompose.decompose(snap.inv, goals.have(("minecraft:stick", 4)), cost(snap, mem=m, oak_log=6),
                                        solver="solve")
            self.assertTrue(steps)
            for st in steps:
                self.assertTrue(skillkit.handles(st), st)


def inventory_bag():
    return bag(inventory())


# (situation, goal, bag, what was seen at what distance, the step that must be chosen, the one that must not)
CHEAPER = [
    ("cows near, pigs far → beef", FOOD8, inventory(), {"cow": 10, "pig": 60}, ("hunt", "minecraft:beef"),
     ("hunt", "minecraft:porkchop")),
    ("pigs near, cows far → pork", FOOD8, inventory(), {"cow": 60, "pig": 10}, ("hunt", "minecraft:porkchop"),
     ("hunt", "minecraft:beef")),
    ("sheep only → mutton", FOOD8, inventory(), {"sheep": 30}, ("hunt", "minecraft:mutton"),
     ("hunt", "minecraft:beef")),
    ("a table in reach is used, not crafted", PICK1, inventory(("oak_log", 4)), {"crafting_table": 3.0, "oak_log": 5},
     ("craft", "minecraft:wooden_pickaxe"), ("craft", "minecraft:crafting_table")),
    ("a table across the valley is not in reach", PICK1, inventory(("oak_log", 4)),
     {"crafting_table": 30.0, "oak_log": 5}, ("craft", "minecraft:crafting_table"), None),
]


class Cheaper(unittest.TestCase):
    def test_the_cheaper_alternative_is_planned(self):
        for name, goal, inv, seen, chosen, not_chosen in CHEAPER:
            with self.subTest(name):
                steps = plan(goal, snapshot(inv=inv), seen)
                self.assertIn(pair(*chosen), pairs(steps), list(map(str, steps)))
                if not_chosen:
                    self.assertNotIn(pair(*not_chosen), pairs(steps), list(map(str, steps)))

    def test_nearer_is_never_dearer(self):
        """The same plan with the trees nearer costs no more (the walk is priced, the work is the same)."""
        for goal in (PICK1, goals.have(("log", 12)), goals.have(("log", 1)), goals.have(("log", 4))):
            with self.subTest(goals.describe(goal)):
                secs = [cost(snapshot(), oak_log=d, stone=3).plan_s(plan(goal, snapshot(), {"oak_log": d, "stone": 3}))
                        for d in (5, 20, 45)]
                self.assertEqual(secs, sorted(secs))


# ------------------------------------------------------------------------------------------- daylight in the solver
from bonobo import actions  # noqa: E402
from bonobo.solve import Unsolvable, solve  # noqa: E402

DUSK_T, NIGHT_T, LATE_T = 11800, 18000, 23200
# (situation, /state changes, bag, target) → a check on the solver's action order (names)
DAYLIGHT = [
    ("day, logs: straight to the tree, no waiting", {"timeOfDay": 2000}, [], {"log": 2},
     lambda t, n: (t.assertIn("gather:log", n), t.assertFalse({"wait:day", "sleep"} & set(n)))),
    ("dusk is still day: the tree now", {"timeOfDay": DUSK_T}, [], {"log": 2},
     lambda t, n: t.assertFalse({"wait:day", "sleep"} & set(n))),
    ("night in the open, logs: shelter, then morning, then the tree", {"timeOfDay": NIGHT_T},
     [("stone_pickaxe", 1)], {"log": 2},
     lambda t, n: (t.assertTrue(any(x.startswith("shelter:") for x in n)),
                   t.assertLess(min(n.index(x) for x in n if x in ("wait:day", "sleep")), n.index("gather:log")),
                   t.assertLess(min(n.index(x) for x in n if x.startswith("shelter:")),
                                min(n.index(x) for x in n if x in ("wait:day", "sleep"))))),
    ("night underground with a bed: sleep, then the tree", {"timeOfDay": NIGHT_T, "skyLight": 0, "y": 30.0},
     [("white_bed", 1)], {"log": 2},
     lambda t, n: t.assertLess(n.index("sleep"), n.index("gather:log"))),
    ("night underground, no bed: wait for day, then the tree", {"timeOfDay": NIGHT_T, "skyLight": 0, "y": 30.0},
     [], {"log": 2},
     lambda t, n: (t.assertNotIn("sleep", n), t.assertLess(n.index("wait:day"), n.index("gather:log")))),
    ("night underground, stone: no waiting — mining needs no sun", {"timeOfDay": NIGHT_T, "skyLight": 0, "y": 30.0},
     [("wooden_pickaxe", 1)], {"minecraft:cobblestone": 3},
     lambda t, n: (t.assertTrue(any(x.startswith("mine:") for x in n)), t.assertFalse({"wait:day", "sleep"} & set(n)))),
    ("an hour before dawn underground: waiting is priced by what is left", {"timeOfDay": LATE_T, "skyLight": 0,
                                                                           "y": 30.0}, [], {"log": 2},
     lambda t, n: t.assertIn("wait:day", n)),
]


class Daylight(unittest.TestCase):
    def test_surface_work_waits_for_the_sun(self):
        for name, changes, inv, target, check in DAYLIGHT:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                snap = snapshot(state(**changes), inventory(*inv))
                vec = actions.state_of(snap, m)
                found = solve(actions.table(places(30.0), vec), vec, target)
                check(self, [a.name for a, _n in found.steps()])

    def test_the_clock_in_the_state(self):
        """day while the sun is up; at night, how long until it is (what wait:day is priced by)."""
        for tod, day, dawn in ((2000, True, None), (DUSK_T, True, None), (NIGHT_T, False, (23400 - NIGHT_T) / 20),
                               (LATE_T, False, (23400 - LATE_T) / 20)):
            with self.subTest(timeOfDay=tod), tempfile.TemporaryDirectory() as tmp:
                vec = actions.state_of(snapshot(state(timeOfDay=tod)), Memory(os.path.join(tmp, "n.json")))
                self.assertEqual(bool(vec.get(actions.DAY_DIM)), day)
                self.assertEqual(vec.get("clock:dawn_s"), dawn)


# ------------------------------------------------------------------------------------------ where a thing lives
NETHER_T = "minecraft:the_nether"
ROD = planner.Step("hunt", "minecraft:blaze_rod", 7, {"types": ["minecraft:blaze"], "kills": 14})
LOG_STEP = planner.Step("gather", "log", 4)
# (situation, dimension we are in, fortress remembered?, the steps planned) → the steps with the way there put first
LIVES = [
    ("rods from the Overworld, no fortress known: portal, find it, collect", OVER, False, [ROD],
     [("portal", NETHER_T), ("seek", "fortress"), ("hunt", "minecraft:blaze_rod")]),
    ("rods in the Nether, no fortress known: find it, collect", NETHER_T, False, [ROD],
     [("seek", "fortress"), ("hunt", "minecraft:blaze_rod")]),
    ("rods in the Nether, a fortress remembered: collect", NETHER_T, True, [ROD], [("hunt", "minecraft:blaze_rod")]),
    ("rods from the Overworld, a fortress remembered: portal, collect", OVER, True, [ROD],
     [("portal", NETHER_T), ("hunt", "minecraft:blaze_rod")]),
    ("logs live anywhere: nothing put first", OVER, False, [LOG_STEP], [("gather", "log")]),
    ("two rod steps: the way there once", OVER, False, [ROD, ROD],
     [("portal", NETHER_T), ("seek", "fortress"), ("hunt", "minecraft:blaze_rod"), ("hunt", "minecraft:blaze_rod")]),
]


class WhereItLives(unittest.TestCase):
    def test_the_way_there_first(self):
        for name, dim, fortress, steps, want in LIVES:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                if fortress:
                    m.add_site("fortress", (200, 70, 40), NETHER_T, name="fortress")
                snap = snapshot(state(dimension=dim))
                got = decompose.where_it_lives(list(steps), cost(snap, mem=m))
                self.assertEqual([(st.kind, st.token) for st in got], want)

    # (situation, rods held, skills removed) → the plan for "have 7 blaze rods" from the Overworld, or why not
    GOALS = [("none held: portal, fortress, collect", 0, (), [("portal", NETHER_T), ("seek", "fortress"),
                                                              ("hunt", "minecraft:blaze_rod")]),
             ("seven held: nothing to do", 7, (), []),
             ("five held: still the way there, for two", 5, (), [("portal", NETHER_T), ("seek", "fortress"),
                                                                ("hunt", "minecraft:blaze_rod")]),
             ("nobody can use a portal: no plan, and says so", 0, ("use_portal",), Unplannable),
             ("nobody collects rods (nor hunts): no plan", 0, ("collect_blaze_rods", "hunt"), Unplannable)]

    def test_blaze_rods_as_a_goal(self):
        for name, held, removed, want in self.GOALS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, mock.patch.dict(skillkit.REGISTRY):
                for n in removed:
                    skillkit.REGISTRY.pop(n)
                snap = snapshot(state(), inventory(("blaze_rod", held)) if held else inventory())
                c = cost(snap, mem=Memory(os.path.join(tmp, "notes.json")))
                if isinstance(want, type):
                    with self.assertRaises(want):
                        decompose.decompose(snap.inv, goals.have(("minecraft:blaze_rod", 7)), c)
                    continue
                got = decompose.decompose(snap.inv, goals.have(("minecraft:blaze_rod", 7)), c)
                # the way there and the collecting, in order (the sword a fight needs is the planner's own business)
                self.assertEqual([(st.kind, st.token) for st in got if st.kind in ("portal", "seek", "hunt")], want)
                self.assertEqual(sum(st.count for st in got if st.kind == "hunt"), 7 - held)


# ------------------------------------------------------------------------------------------------- effect goals
class EffectGoals(unittest.TestCase):
    def test_every_provided_effect_is_a_goal(self):
        """Driven by the registry: whatever a skill provides can be queued as a task and plans to one step that the
        same skill (or another provider) carries out."""
        effects = sorted({e for c in skillkit.REGISTRY.values() for e in c.provides})
        self.assertTrue(effects)
        detail = {"goto": {"pos": [5, 64, 0]}, "withdraw": {"pos": [3, 64, 0]},       # effects that name a place
                  "explore:blocks": {"blocks": ["oak_log"]}, "explore:mobs": {"types": ["minecraft:cow"]},
                  "hunt": {"types": ["minecraft:cow"]}, "mine": {"blocks": ["stone"], "tier": 0},
                  "seek": {"kinds": ["stone"]}, "take": {"blocks": ["red_bed"]}}
        for effect in effects:
            goal = goals.make("effect", effect=effect, count=2, **({"detail": detail[effect]} if effect in detail else {}))
            with self.subTest(effect):
                steps = decompose.decompose(snapshot().inv, goal, cost())
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0].count, 2)
                self.assertIn(effect, skillkit.step_keys(steps[0]))
                self.assertTrue(skillkit.providers(effect))
                self.assertIsNone(goals.done(goal, snapshot(), None), "done when its plan ran")
                self.assertEqual(goals.describe(goal), f"effect {effect} ×2")

    def test_effects_missing_their_detail(self):
        """An effect whose provider needs an argument the goal did not give is refused, and the refusal names it."""
        for effect, key in (("hunt", "types"), ("mine", "blocks"), ("take", "blocks"), ("seek", "kinds"),
                            ("explore:mobs", "types")):
            with self.subTest(effect), self.assertRaises(Unplannable) as caught:
                decompose.decompose(snapshot().inv, goals.make("effect", effect=effect), cost())
            self.assertEqual(str(caught.exception), f"effect {effect} needs {key} in its detail")

    def test_effects_nobody_provides(self):
        for effect in ("teleport", "item:unobtainium", "dragons:breed", "mine:", "place:nothing"):
            with self.subTest(effect), self.assertRaises(Unplannable):
                decompose.decompose(snapshot().inv, goals.make("effect", effect=effect), cost())


# ---------------------------------------------------------------------------------------------- can the step start
def _needs_torches(c):
    from bonobo.world import Inventory
    raise api.NotAvailable("no torches to spare")


# (situation, the provider's preconditions, bag) → offered (valid) this round?
STARTS = [("no preconditions", (), inventory(), True),
          ("a precondition that refuses", (_needs_torches,), inventory(), False),
          ("the inputs are missing, whatever the skill says", (), None, False),
          ("the inputs are missing and the skill refuses too", (_needs_torches,), None, False),
          ("a precondition that passes", (lambda c: None,), inventory(), True)]


def _passes(check):
    try:
        check(None)
        return True
    except Exception:
        return False


class CanStart(unittest.TestCase):
    def test_valid_asks_the_skill(self):
        from bonobo import dispatch
        for name, pre, inv, want in STARTS:
            with self.subTest(name), mock.patch.dict(skillkit.REGISTRY, clear=True), tempfile.TemporaryDirectory() as tmp:
                skillkit.skill(needs={}, speed={}, gives={}, name="zz_skill", pre=pre, provides={"craft": lambda ctx, s: (s.token,)})(
                    lambda ctx, *a: None)
                step = planner.Step("craft", "minecraft:stick", 4, {"inputs": {"planks": 2}})
                bag_ = inventory(("oak_planks", 2)) if inv is not None else inventory()
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
                b.retry, b.place = retry.Retry(), ("here", False)
                ctx = type("Ctx", (), {"policy": None, "mem": None})()
                self.assertEqual(b.valid(step, snapshot(inv=bag_), ctx), want)
                self.assertEqual(dispatch.can_start(ctx, step), all(_passes(p) for p in pre))

    def test_a_failed_step_cools_for_every_goal(self):
        """A plan step that failed cools under its own key (brain.step_key), so the next goal that plans the same
        step is not offered it; an interruption cools nothing."""
        step = planner.Step("gather", "log", 3, {})
        # (situation, what ends the attempt, the goal that tries after) → the step is still valid
        rows = [("failed under the pickaxe goal: not offered for the sword", NotAvailable("no trees found nearby"),
                 "idle: have sword tier 1", False),
                ("the same goal again: not offered", NotAvailable("no trees found nearby"),
                 "idle: have pickaxe tier 1", False),
                ("interrupted: offered again (no cooling)", api.Interrupted("a faster layer took the body"),
                 "idle: have sword tier 1", True),
                ("it succeeded: offered", None, "idle: have sword tier 1", True)]
        for name, err, _goal, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.retry, b.place, b.mem = retry.Retry(), ("here", False), Memory(tmp + "/notes.json")
                b.reflexes = type("R", (), {"failed": lambda self, *a: None})()

                def run(e=err):
                    if e is not None:
                        raise e
                with mock.patch.object(api, "post"), mock.patch.object(brainmod, "log"):
                    b.attempt("idle: have pickaxe tier 1", run, also=(brainmod.step_key(step),))
                self.assertEqual(b.ready(brainmod.step_key(step)), want)

    def test_who_can_start_a_step(self):
        """dispatch.can_start over the registry as it is, by the step's kind: no provider, an unknown skill name,
        a provider whose adapter declines here, a provider that can."""
        from bonobo import dispatch
        rows = [("nobody provides this kind", {}, planner.Step("zz", "nothing", 1), False),
                ("a skill name nobody registered", {}, planner.Step("skill", "no_such_skill", 1), False),
                ("the one provider's adapter declines here", {"zz_a": ("zz", None, ())}, planner.Step("zz", "t", 1),
                 False),
                ("a provider that can, no preconditions", {"zz_a": ("zz", (1,), ())}, planner.Step("zz", "t", 1), True),
                ("a provider that can, but its precondition refuses", {"zz_a": ("zz", (1,), (_needs_torches,))},
                 planner.Step("zz", "t", 1), False),
                ("by name, a registered skill with no preconditions", {"zz_b": ("yy", (), ())},
                 planner.Step("skill", "zz_b", 1), True)]
        for name, fakes, step, want in rows:
            with self.subTest(name), mock.patch.dict(skillkit.REGISTRY, clear=True):
                for sname, (effect, args, pre) in fakes.items():
                    skillkit.skill(needs={}, speed={}, gives={}, name=sname, pre=pre, provides={effect: lambda ctx, s, _a=args: _a})(
                        lambda ctx, *a: None)
                self.assertIs(dispatch.can_start(None, step), want)


# -------------------------------------------------------------------------------------------------- the cost model
from bonobo import cost as costmod  # noqa: E402
from bonobo.planner import Step  # noqa: E402

PT, WT = costmod.PRIOR_TICKS, costmod.walk_ticks
UNDER = state(skyLight=0, y=20.0)
# (situation, step, /state, what /find saw, ticks expected: prior work + the walk to it)
ESTIMATES = [
    ("craft: work only", Step("craft", "minecraft:stick", 4), None, {}, PT["craft"]),
    ("smelt 3: each + setup", Step("smelt", "minecraft:iron_ingot", 3), None, {}, 3 * PT["smelt_each"] + PT["smelt_setup"]),
    ("mine 4 breaks, ore 10 away", Step("mine", "minecraft:raw_iron", 4, {"blocks": ["iron_ore"], "breaks": 4}), None,
     {"iron_ore": 10}, 4 * PT["mine_each"] + WT(10)),
    ("mine, nothing in sight", Step("mine", "minecraft:raw_iron", 4, {"blocks": ["iron_ore"], "breaks": 4}), None, {},
     4 * PT["mine_each"] + costmod.UNKNOWN_WALK_TICKS),
    ("gather 2, a tree 8 away", Step("gather", "log", 2), None, {"oak_log": 8}, 2 * PT["gather_each"] + WT(8)),
    ("gather underground: the climb out is part of it", Step("gather", "log", 2), UNDER, {"oak_log": 8},
     2 * PT["gather_each"] + WT(8) + 200 + 30 * (64 - 20)),
    ("hunt 2 kills, cows 12 away", Step("hunt", "minecraft:beef", 4, {"types": ["minecraft:cow"], "kills": 2}), None,
     {"cow": 12}, 2 * PT["hunt_each"] + WT(12)),
    ("goto 30 blocks", Step("goto", "pos", 1, {"pos": [30, 64, 0]}), None, {}, WT(30)),
    ("withdraw from a chest 5 away", Step("withdraw", "minecraft:oak_log", 4, {"pos": [5, 64, 0]}), None, {},
     PT["withdraw"] + WT(5)),
    ("fill with no water known", Step("fill", "minecraft:water_bucket", 1), None, {}, PT["fill"] + 1200),
    ("sleep", Step("sleep", "bed", 1), None, {}, PT["sleep"]),
]


class CostModel(unittest.TestCase):
    def test_estimates(self):
        for name, step, st, seen, want in ESTIMATES:
            with self.subTest(name):
                self.assertEqual(cost(snapshot(st), **seen).estimate(step), want)

    def test_measured_replaces_the_prior_after_enough_samples(self):
        step = Step("gather", "log", 2)
        for samples, measured in ((0, False), (skillkit.MIN_SAMPLES - 1, False), (skillkit.MIN_SAMPLES, True),
                                  (skillkit.MIN_SAMPLES + 5, True)):
            with self.subTest(samples=samples), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                for _ in range(samples):
                    m.record_duration("chop", 10.0, 1)
                got = cost(snapshot(), mem=m, oak_log=8).estimate(step)
                self.assertEqual(got, (2 * 10 * costmod.TICKS_PER_S if measured else 2 * PT["gather_each"]) + WT(8))

    # (situation, what memory has / /find saw, is a station near?)
    STATIONS = [("a table in sight 4 away", None, {"crafting_table": 4}, True),
                ("a table in sight 9 away", None, {"crafting_table": 9}, False),
                ("a table memory keeps 3 away", ((3, 64, 0),), {}, True),
                ("a table memory keeps 30 away", ((30, 64, 0),), {}, False),
                ("nothing", None, {}, False)]

    def test_station_near(self):
        for name, stations, seen, want in self.STATIONS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                for pos in stations or ():
                    m.add_station("minecraft:crafting_table", pos, OVER)
                self.assertEqual(cost(snapshot(), mem=m, **seen).station_near("minecraft:crafting_table"), want)

    # (situation, known distance or None) → seconds the column solver prices a seek at
    SEEKS = [("never seen: the declared prior", None, None), ("40 blocks away", 40.0, WT(40) / 20 + 2.0),
             ("10 blocks away", 10.0, WT(10) / 20 + 2.0), ("200 blocks away", 200.0, WT(200) / 20 + 2.0),
             ("right here", 0.0, 2.0)]

    def test_seek_from_memory(self):
        """Where memory says one is: the game's route estimate when this round already asked, else the walk; a banned
        spot is not somewhere to go; a log is found as a remembered "tree"."""
        from bonobo import nav
        pol = nav.Policy()
        key = lambda p: (p, bool(pol.allow_dig), bool(pol.allow_build), 2.0, 6000)  # noqa: E731
        prior = float(costmod._PLAY["plan"]["seek_prior_s"])
        rows = [("a route the game priced this round", ("iron_ore", (10, 64, 0)), ["iron_ore"], {key((10, 64, 0)): (True, 7.3)},
                 {}, 7.3),
                ("no route asked: the walk", ("iron_ore", (10, 64, 0)), ["iron_ore"], {}, {}, round(WT(10) / 20 + 2.0, 1)),
                ("a route the game found none for: the walk", ("iron_ore", (10, 64, 0)), ["iron_ore"],
                 {key((10, 64, 0)): (False, None)}, {}, round(WT(10) / 20 + 2.0, 1)),
                ("the only spot is banned", ("iron_ore", (10, 64, 0)), ["iron_ore"], {}, {(10, 64, 0): time.time() + 600},
                 prior),
                ("logs are found where a tree was noted", ("tree", (20, 64, 0)), ["oak_log"], {}, {},
                 round(WT(20) / 20 + 2.0, 1)),
                ("nothing known", None, ["iron_ore"], {}, {}, prior)]
        for name, note, kinds, routes, banned, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, mock.patch.dict(nav._ROUTES, routes):
                m = Memory(os.path.join(tmp, "notes.json"))
                if note:
                    m.note_seen(note[0], note[1], OVER)
                c = costmod.Cost(snapshot(), mem=m, blacklist=banned)
                self.assertAlmostEqual(c.seek_s(kinds), want, places=1)

    def test_the_route_cache_is_read_with_the_rounds_policy(self):
        """The game's route estimate counts only for the movement rules it was asked under."""
        from bonobo import nav
        walk = round(WT(10) / 20 + 2.0, 1)
        dig, walk_only = nav.Policy(allow_dig=True), nav.Policy(allow_dig=False)
        key = lambda p: ((10, 64, 0), bool(p.allow_dig), bool(p.allow_build), 2.0, 6000)  # noqa: E731
        rows = [("asked with digging, planning with digging", dig, dig, 7.3),
                ("asked walking only, planning walking only", walk_only, walk_only, 7.3),
                ("asked with digging, planning walking only: not the same route", dig, walk_only, walk),
                ("asked walking only, planning with digging", walk_only, dig, walk),
                ("no policy given: the default's", nav.Policy(), None, 7.3)]
        for name, asked, planning, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.dict(nav._ROUTES, {key(asked): (True, 7.3)}):
                m = Memory(os.path.join(tmp, "notes.json"))
                m.note_seen("iron_ore", (10, 64, 0), OVER)
                self.assertAlmostEqual(costmod.Cost(snapshot(), mem=m, policy=planning).seek_s(["iron_ore"]), want,
                                       places=1)

    def test_route_s_directly(self):
        """cost.route_s: the game's own route seconds when this round already asked it, under this policy; None
        whenever that is not so."""
        from bonobo import nav
        pol = nav.Policy()
        key = ((10, 64, 0), bool(pol.allow_dig), bool(pol.allow_build), 2.0, 6000)
        rows = [("asked, found: its seconds", True, {key: (True, 7.3)}, {}, pol, 7.3),
                ("asked, no route found", True, {key: (False, None)}, {}, pol, None),
                ("not asked this round", True, {}, {}, pol, None),
                ("nothing remembered to route to", False, {key: (True, 7.3)}, {}, pol, None),
                ("the spot is banned", True, {key: (True, 7.3)}, {(10, 64, 0): time.time() + 600}, pol, None),
                ("asked under another policy", True, {key: (True, 7.3)}, {},
                 nav.Policy(allow_dig=not pol.allow_dig), None)]
        for name, noted, routes, banned, policy, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, mock.patch.dict(nav._ROUTES, routes):
                m = Memory(os.path.join(tmp, "notes.json"))
                if noted:
                    m.note_seen("iron_ore", (10, 64, 0), OVER)
                self.assertEqual(costmod.Cost(snapshot(), mem=m, blacklist=banned, policy=policy).route_s(["iron_ore"]),
                                 want)

    def test_seek_seconds(self):
        prior = float(costmod._PLAY["plan"]["seek_prior_s"])
        for name, known, want in self.SEEKS:
            with self.subTest(name):
                c = costmod.Cost(None, known=lambda kinds, d=known: d)
                self.assertAlmostEqual(c.seek_s(["iron_ore"]), round(want if want is not None else prior, 1), places=1)
                self.assertEqual(c.find_p(["iron_ore"]), float(costmod._PLAY["plan"]["exists_prior"]))


def stone_tools(worn=0):
    return inventory(slot("stone_pickaxe", 1, worn), ("stone_sword", 1), ("stone_axe", 1), ("crafting_table", 1),
                     ("furnace", 1))


# (situation, goal, bag before, bag after the event, pending outputs, checks on the repaired plan)
REPAIRS = [
    ("half-done: 12 logs asked, 5 held", goals.have(("log", 12)), inventory(), inventory(("oak_log", 5)), None,
     lambda t, before, after: t.assertEqual([s.count for s in after if s.kind == "gather"], [7])),
    ("the bag gained logs mid-plan", goals.have(("log", 12)), inventory(), inventory(("birch_log", 9)), None,
     lambda t, before, after: t.assertEqual(sum(s.count for s in after if s.kind == "gather"), 3)),
    ("interrupted after the table and wooden pickaxe were made", PICK1, inventory(("oak_log", 3)),
     inventory(("wooden_pickaxe", 1), ("oak_planks", 4), ("stick", 4), ("crafting_table", 1)), None,
     lambda t, before, after: (t.assertIn(pair("craft", "minecraft:crafting_table"), pairs(before)),
                               t.assertNotIn(pair("craft", "minecraft:crafting_table"), pairs(after)),
                               t.assertNotIn(pair("craft", "minecraft:wooden_pickaxe"), pairs(after)))),
    ("the pickaxe wore down to 1 mid-plan", IRON3, stone_tools(), stone_tools(worn=130), None,
     lambda t, before, after: (t.assertNotIn(pair("craft", "minecraft:stone_pickaxe"), pairs(before)),
                               t.assertIn(pair("craft", "minecraft:stone_pickaxe"), pairs(after)))),
    ("the ingots are already cooking in a furnace", IRON3, stone_tools(), stone_tools(),
     {"minecraft:iron_ingot": 3}, lambda t, before, after: t.assertEqual(after, [])),
    ("half the ore already mined", IRON3, stone_tools(), inventory(*stone_tools()["slots"], ("raw_iron", 2)), None,
     lambda t, before, after: t.assertEqual([s.count for s in after if s.kind == "mine" and "iron" in s.token], [1])),
    ("goal met while the plan was held", IRON3, stone_tools(), inventory(("iron_ingot", 3)), None,
     lambda t, before, after: t.assertEqual(after, [])),
    ("died: the bag is empty again", IRON3, stone_tools(), inventory(), None,
     lambda t, before, after: (t.assertIn(pair("gather", "log"), pairs(after)),
                               t.assertGreater(len(after), len(before)))),
]


class Repairs(unittest.TestCase):
    def test_event_to_repaired_plan(self):
        seen = {"oak_log": 8, "stone": 2, "iron_ore": 12, "coal_ore": 9}
        for name, goal, inv_before, inv_after, pending, check in REPAIRS:
            with self.subTest(name):
                task = {"id": "t1", "goal": goal["goal"], "args": goal.get("args", {})}
                snap_b, snap_a = snapshot(inv=inv_before), snapshot(inv=inv_after)
                held_b, why_b = brainmod.replan(task, goal, snap_b, cost(snap_b, **seen))
                held_a, why_a = brainmod.replan(task, goal, snap_a, cost(snap_a, **seen), pending=pending)
                self.assertIsNone(why_b)
                self.assertIsNone(why_a)
                self.assertEqual(held_a["sig"], needs.bag_signature(snap_a.inv), "the held plan is stamped with its bag")
                self.assertFalse(held_a["event"])
                check(self, held_b["steps"], held_a["steps"])

    def test_nothing_can_plan_it(self):
        for name, goal in UNPLANNABLE:
            with self.subTest(name):
                task = {"id": "t1", "goal": goal["goal"], "args": goal.get("args", {})}
                try:
                    held, why = brainmod.replan(task, goal, snapshot(), cost())
                except ValueError:
                    continue
                self.assertEqual((held, why.split(":")[0]), (None, "unplannable"))

    def test_a_saved_plan_survives_a_restart(self):
        """tasks.json keeps step dicts; what comes back is the same plan (repair then runs from the bag)."""
        for goal, where, _c, _o in PLANS:
            w = next(iter(worlds(**where))) if where else next(iter(worlds()))
            steps = decompose.decompose(w.snapshot().inv, goal, w.cost())
            with self.subTest(goals.describe(goal)):
                back = [decompose.from_dict(json.loads(json.dumps(decompose.to_dict(s)))) for s in steps]
                self.assertEqual([(s.kind, s.token, s.count, s.detail, s.est) for s in back],
                                 [(s.kind, s.token, s.count, json.loads(json.dumps(s.detail)), s.est) for s in steps])

    def test_run_once_goals_are_done_by_their_plan_not_the_world(self):
        snap = snapshot()
        # (goal) → done? None: its plan decides; the must-not: an item goal is read off the bag
        rows = [(goals.make("road", a=[0, 64, 0], b=[9, 64, 0]), None),
                (goals.make("skill", name="chop", args=[1]), None),
                (goals.make("effect", effect="mine:minecraft:stone", detail={"pos": [0, 64, 0]}), None),
                (goals.have(("log", 1)), False)]
        for goal, want in rows:
            with self.subTest(goal["goal"]):
                self.assertEqual(goal["goal"] in goals.RUN_ONCE, want is None)
                self.assertIs(goals.done(goal, snap, None), want)


# ----------------------------------------------------------------------------------------------- held plans
# The brain's queue work (task_act / repair / after_step / finish / prepare) on an unstarted Brain: the cost model it
# builds answers from the row's look-around, the bag after a step from the row's readings; any other request fails.
TREES = {"oak_log": 6, "stone": 2}          # fixture: the default look-around of a held-plan row


class Queue_:
    """One brain, one task file, the row's readings."""

    def __init__(self, tmp, seen=TREES):
        self.tmp, self.seen, self.after_inv = tmp, seen, inventory()
        b = self.b = brainmod.Brain.__new__(brainmod.Brain)
        b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
        b.mem = Memory(os.path.join(tmp, "notes.json"))
        b.retry, b.blacklist, b.place, b.held = retry.Retry(), {}, PLACE, {}
        b.needs, b.reflexes = needs.Needs(b), reflexes.Maintain(b)
        b.last_failure, b.committed, b.last_hold_log = None, None, 0
        self.patches = [mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")),
                        mock.patch.object(brainmod, "Cost", lambda snap, mem=None, bl=None, **k: cost(snap, mem=mem,
                                                                                                    **self.seen)),
                        mock.patch.object(brainmod, "Inventory", lambda: bag(self.after_inv)),
                        mock.patch.object(api, "api", side_effect=AssertionError("the queue read the world"))]

    def __enter__(self):
        from bonobo import bag as bagmod
        self._reserved = bagmod.RESERVED
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        from bonobo import bag as bagmod
        for p in reversed(self.patches):
            p.stop()
        bagmod.RESERVED = self._reserved      # task_act sets the module-wide reservation

    def task(self, goal, plan=None):
        t = tasks.add(goal)
        if plan is not None:
            tasks.update(t["id"], plan=plan)
        return tasks.load()[0]

    def state(self, task_id="t1"):
        return next((t["state"], t["reason"]) for t in tasks.load() if t["id"] == task_id)


def _round(q, inv, st=None):
    snap = snapshot(st or state(), inv)
    task = next((t for t in tasks.load() if t["state"] in tasks.LIVE), None)
    return q.b.task_act(task, snap, ctx=None) if task else None


# (situation, goal, [(op, args...)]). ops: ("round", inv) → the act, kept as q.act; ("ok", bag after) / ("interrupted",)
# / ("failed", n failures of cause nav): the step's outcome; ("check", fn(test, q)).
HELD = [
    ("a fresh task: plan, hold, first step", goals.have(("log", 4)), [
        ("round", inventory()),
        ("check", lambda t, q: (t.assertEqual((q.act.step.kind, q.act.step.token), ("gather", "log")),
                                t.assertEqual(q.state(), ("running", "")),
                                t.assertTrue(tasks.load()[0]["plan"], "the plan is saved with the task")))]),
    ("the goal is met already: done, no act", goals.have(("log", 4)), [
        ("round", inventory(("oak_log", 4))),
        ("check", lambda t, q: (t.assertIsNone(q.act), t.assertEqual(q.state(), ("done", ""))))]),
    ("a step done: removed, the rest kept without replanning", PICK1, [
        ("round", inventory()), ("ok", inventory(("oak_log", 3))),
        ("check", lambda t, q: t.assertNotIn(q.first, q.b.held["t1"]["steps"])),
        ("round", inventory(("oak_log", 3))),
        ("check", lambda t, q: t.assertNotEqual((q.act.step.kind, q.act.step.token), ("gather", "log")))]),
    ("interrupted: the next round repairs from the bag", goals.have(("log", 8)), [
        ("round", inventory()), ("interrupted",),
        ("check", lambda t, q: t.assertTrue(q.b.held["t1"]["event"])),
        ("round", inventory(("oak_log", 5))),
        ("check", lambda t, q: (t.assertFalse(q.b.held["t1"]["event"]), t.assertEqual(q.act.step.count, 3)))]),
    # Resume by the remaining amount, per kind of task: interrupted at k of n, the next plan asks for n − k and
    # redoes nothing already held.
    ("have: 3 of 8 logs held when interrupted → gather 5", goals.have(("log", 8)), [
        ("round", inventory()), ("interrupted",), ("round", inventory(("oak_log", 3))),
        ("check", lambda t, q: t.assertEqual([st.count for st in q.b.held["t1"]["steps"] if st.kind == "gather"], [5]))]),
    ("have: 1 of 3 ingots smelted when interrupted → smelt 2, mine 2", IRON3, [
        ("round", stone_tools()), ("interrupted",), ("round", inventory(*stone_tools()["slots"], ("iron_ingot", 1))),
        ("check", lambda t, q: (
            t.assertEqual([st.count for st in q.b.held["t1"]["steps"] if st.kind == "smelt"], [2]),
            t.assertEqual(sum(st.count for st in q.b.held["t1"]["steps"] if st.kind == "mine"
                              and "iron" in st.token), 2)))]),
    ("craft: 4 of 8 sticks made when interrupted → craft 4 more", goals.make("craft", needs=[["minecraft:stick", 8]]), [
        ("round", inventory(("oak_planks", 8))), ("interrupted",),
        ("round", inventory(("oak_planks", 6), ("stick", 4))),
        ("check", lambda t, q: t.assertEqual([st.count for st in q.b.held["t1"]["steps"]
                                              if (st.kind, st.token) == ("craft", "minecraft:stick")], [4]))]),
    ("build: 10 of 14 stone gathered when interrupted → mine 4, then build; the door is not made again",
     goals.make("build", bp="shelter"), [
        ("round", inventory(("oak_door", 1), ("torch", 1), ("stone_pickaxe", 1))), ("interrupted",),
        ("round", inventory(("oak_door", 1), ("torch", 1), ("stone_pickaxe", 1), ("cobblestone", 10))),
        ("check", lambda t, q: (
            t.assertEqual([st.count for st in q.b.held["t1"]["steps"] if (st.kind, st.token) == ("mine", "stone")], [4]),
            t.assertEqual(q.b.held["t1"]["steps"][-1].kind, "build"),
            t.assertNotIn(pair("craft", "door"), pairs(q.b.held["t1"]["steps"]))))]),
    ("road: interrupted on the second leg → that leg only", goals.make("road", a=[0, 64, 0], b=[40, 64, 0]), [
        ("round", inventory()), ("ok", inventory()), ("round", inventory()), ("interrupted",),
        ("round", inventory()),
        ("check", lambda t, q: t.assertEqual([st.detail["pos"] for st in q.b.held["t1"]["steps"]], [[40, 64, 0]]))]),
    ("failed on the third source: the task is failed with its cause", goals.have(("log", 4)), [
        ("round", inventory()), ("failed", 3),
        ("check", lambda t, q: (t.assertEqual(q.state()[0], "failed"), t.assertTrue(q.state()[1].startswith("nav"))))]),
    ("failed once: kept, repaired next round", goals.have(("log", 4)), [
        ("round", inventory()), ("failed", 1),
        ("check", lambda t, q: (t.assertEqual(q.state()[0], "running"), t.assertTrue(q.b.held["t1"]["event"])))]),
    ("a saved plan after a restart is checked against the bag", goals.have(("log", 6)), [
        ("saved", [{"kind": "gather", "token": "log", "count": 6, "detail": {}, "est": 100}]),
        ("round", inventory(("oak_log", 4))),
        ("check", lambda t, q: t.assertEqual(q.act.step.count, 2))]),
    ("unplannable: failed, and says why", goals.make("skill", name="fly_to_the_moon"), [
        ("round", inventory()),
        ("check", lambda t, q: (t.assertIsNone(q.act), t.assertEqual(q.state()[0], "failed"),
                                t.assertIn("unplannable", q.state()[1])))]),
    ("a road walks on from where it stopped", goals.make("road", a=[0, 64, 0], b=[40, 64, 0]), [
        ("round", inventory()), ("ok", inventory()),
        ("round", inventory()),
        ("check", lambda t, q: t.assertEqual(q.act.step.detail["pos"], [40, 64, 0])),
        ("ok", inventory()), ("round", inventory()),
        ("check", lambda t, q: (t.assertIsNone(q.act), t.assertEqual(q.state(), ("done", ""))))]),
    ("no trees anywhere: the plan runs dry, the task fails as unavailable", goals.have(("log", 4)), [
        ("seen", {}), ("round", inventory()),
        ("check", lambda t, q: t.assertEqual((q.act.step.kind, q.act.step.token), ("gather", "log"))),
        ("failed_as", NotAvailable("no trees found nearby, even after exploring"), 3),
        ("check", lambda t, q: (t.assertEqual(q.state()[0], "failed"),
                                t.assertTrue(q.state()[1].startswith("unavailable"))))]),
    ("no stone anywhere: the stone pickaxe plan still starts from what can be had", PICK1, [
        ("seen", {"oak_log": 5}), ("round", inventory()),
        ("check", lambda t, q: (t.assertEqual(q.act.step.kind, "gather"),
                                t.assertIn(pair("mine", "stone"), pairs(q.b.held["t1"]["steps"]))))]),
    ("ingots cooking in a furnace: wait, don't fail", IRON3, [
        ("job", "minecraft:iron_ingot", 3), ("round", stone_tools()),
        ("check", lambda t, q: (t.assertIsNone(q.act), t.assertEqual(q.state()[0], "running")))]),
]


class HeldPlans(unittest.TestCase):
    def test_event_sequences(self):
        for name, goal, ops in HELD:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                plan = next((o[1] for o in ops if o[0] == "saved"), None)
                q.task(goal, plan)
                q.act = q.first = None
                for op_, *a in ops:
                    if op_ == "round":
                        q.act = _round(q, a[0])
                        q.first = q.act.step if q.act else None
                    elif op_ == "ok":
                        q.after_inv = a[0]
                        q.b.after_step(q.act, "ok")
                    elif op_ == "interrupted":
                        q.b.after_step(q.act, "interrupted")
                    elif op_ in ("failed", "failed_as"):
                        err, times = (api.NavFailed("no path found"), a[0]) if op_ == "failed" else (a[0], a[1])
                        for _ in range(times):
                            q.b.last_failure = q.b.failed(q.act.name, err)
                        q.b.after_step(q.act, "failed")
                    elif op_ == "seen":
                        q.seen = a[0]
                    elif op_ == "job":
                        q.b.mem.add_job("smelt", (3, 64, 0), OVER, a[0], a[1], time.time() + 60, [])
                    elif op_ == "check":
                        a[0](self, q)

    def test_a_held_plan_is_solved_again_only_on_an_event(self):
        """plan_without_events: a held plan none of whose steps could run was solved again every round from the same
        bag ("plan for t1" every 3 s, nothing run). Solved again only when the bag changed."""
        before, changed = inventory(), inventory(("oak_log", 2))
        rows = [("a step runs, the same bag: held as it is", True, before, 0),
                ("no step runs, the same bag: not solved again (it cools)", False, before, 0),
                ("no step runs, the bag changed: solved again", False, changed, 1),
                ("a step runs, the bag changed: solved again", True, changed, 1)]
        for name, runs, bag_now, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                q.task(goals.have(("log", 8)))
                _round(q, before)                           # the first plan: not counted
                q.b.valid = lambda *a, **k: runs
                with mock.patch.object(brainmod, "replan", wraps=brainmod.replan) as solved:
                    _round(q, bag_now)
                    self.assertEqual(solved.call_count, want)

    # (bag, what idle prepares first, or None when everything is held)
    PREPARE = [(inventory(), ("tool", "pickaxe", 1)),
               (inventory(("stone_pickaxe", 1)), ("tool", "sword", 1)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1)), ("food", 8)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("cooked_beef", 8)), ("minecraft:torch", 8)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("cooked_beef", 8), ("torch", 8)), None)]

    def test_prepare(self):
        """Idle stocking is a proposal toward the first missing item, never a task."""
        for inv, want in self.PREPARE:
            with self.subTest(want=want), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                act = q.b.prepare(snapshot(inv=inv), None)
                self.assertEqual(tasks.load(), [], "idle stocking queued a task")
                self.assertEqual(None if act is None else act.name,
                                 None if want is None else f"idle: {goals.describe(goals.have(want))}")

    def test_the_round_that_finishes_the_queue_proposes_nothing_more(self):
        """plan_proposals: the task met this round is finished and nothing else is offered — the next round (queue
        empty) decides on stocking. Stocking in the same round ate the bench slices' budget."""
        rows = [("the last task met now: nothing this round", [goals.have(("log", 4))], [("oak_log", 4)], []),
                ("a task met, another still live: that one", [goals.have(("log", 4)), goals.have(("stick", 4))],
                 [("oak_log", 4), ("oak_planks", 4)], ["queue"]),
                ("nothing queued at all: stocking", [], [], ["idle"]),
                ("a live task, not met: the task", [goals.have(("log", 4))], [], ["queue"])]
        for name, queued, items, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                for g in queued:
                    q.task(g)
                got = q.b.plan_proposals(snapshot(state(), inventory(*items)), None)
                self.assertEqual([i.kind for i in got], want)

    def test_idle_beside_the_queue(self):
        """plan_proposals: stocking only when the queue has nothing that can run now, and never into the queue
        (tool_tier__one_use: a queued sword took over whenever the row's own task cooled)."""
        rows = [("a task that can run: the task, no stocking", True, False, ["queue"]),
                ("nothing queued: stocking proposed", False, False, ["idle"]),
                ("the task cooling: stocking may be picked", True, True, ["idle"]),
                ("nothing queued, the bag full of what stocking wants: nothing", False, False, [])]
        for name, queued, cooling, want in rows:
            full = inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("cooked_beef", 8), ("torch", 8))
            inv = full if name.endswith("nothing") else inventory()
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                if queued:
                    q.task(goals.have(("log", 4)))
                if cooling:
                    for _ in range(3):
                        q.b.failed("task t1", api.NavFailed("no path found"))
                before = [t["id"] for t in tasks.load()]
                got = q.b.plan_proposals(snapshot(state(), inv), None)
                self.assertEqual([i.kind for i in got], want)
                self.assertEqual([t["id"] for t in tasks.load()], before, "stocking changed the queue")


# -------------------------------------------------------------------------------------------------------- upkeep
PLACE = retry.place_signature((0, 64, 0), False)
DAY, DUSK, NIGHT = 2000, 11500, 18000
WELL_FED = [("cooked_beef", 8), ("white_bed", 1), ("stone_pickaxe", 1)]      # fixture: the default bag
HERD = {"cow": 12, "sheep": 20, "oak_log": 10, "stone": 2}


class Row:
    """One upkeep situation: readings, the few world facts the table reads, and what it must conclude."""

    def __init__(self, name, row, queued=(), time_of_day=DAY, inv=WELL_FED, seen=None, enclosed=False,
                 bed_seen=False, last_round=None, blocked=None, stuck=False, died=False, cooling=(), place=None,
                 job=None, machine=None, held=(), **st):
        self.job, self.machine = job, machine        # (pos, seconds until ready): a furnace job / a smelter order
        # `queued`: the exact set of goals put in front, or a check(set) for rows whose tier is the planner's call
        self.name, self.row = name, row
        self.queued = queued if callable(queued) else {tuple(map(tuple, q)) for q in queued}
        self.place = place or PLACE
        self.state = state(timeOfDay=time_of_day, **st)
        self.inv = inventory(*inv) if isinstance(inv, list) else inv
        self.seen = HERD if seen is None else seen
        self.enclosed, self.bed_seen, self.last_round = enclosed, bed_seen, last_round
        self.blocked, self.stuck, self.died, self.cooling = blocked, stuck, died, cooling
        self.held = list(held)                          # steps of a plan the brain holds (what it still needs)


UPKEEP = [
    Row("fed, day, a bed and a pickaxe: nothing to do", None),
    Row("hungry with food carried", "eat", food=10),
    Row("hungry, nothing edible, nothing seen: food to the front", None, queued=[[("food", 8)]], food=4,
        inv=[("white_bed", 1), ("stone_pickaxe", 1)], seen={}),
    Row("eating failed here a moment ago: the next row", None, food=10, cooling=("eat",)),
    Row("treading water", "reach land", inWater=True, onGround=False),
    Row("in the Nether with two meals left", "leave the Nether", dimension=NETHER, skyLight=0,
        inv=[("cooked_beef", 2), ("stone_pickaxe", 1)]),
    Row("in the Nether at 6 hp", "leave the Nether", dimension=NETHER, skyLight=0, health=6.0),
    Row("morning in a sealed pod", "dig out", enclosed=True),
    Row("night, a bed carried", "sleep", time_of_day=NIGHT),
    Row("night, a site bed in sight", "sleep", time_of_day=NIGHT, inv=[("cooked_beef", 8), ("stone_pickaxe", 1)],
        bed_seen=True),
    Row("night, no bed, out in the open", "shelter", time_of_day=NIGHT, inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
    Row("night underground, no bed: already covered", None, time_of_day=NIGHT, skyLight=0,
        inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
    Row("night in the Nether with a bed: never sleep there", None, time_of_day=NIGHT, dimension=NETHER, skyLight=0),
    Row("the bag is full", "empty the bag", inv=full_bag()),
    Row("path blocked, blocks to bridge with", "path blocked", inv=WELL_FED + [("cobblestone", 16)],
        blocked=(40, 64, 0)),
    Row("path blocked, nothing to bridge with: blocks to the front", None, queued=[[("building", 32)]],
        blocked=(40, 64, 0)),
    Row("path failure somewhere else is not this path", None, inv=WELL_FED + [("cobblestone", 16)],
        blocked=(40, 64, 0), place=retry.place_signature((400, 64, 0), False)),
    Row("the same block and bag for 90 s", "unstuck", stuck=True),
    Row("died a minute ago", "recover items", died=True),
    Row("no working pickaxe, no plan wanting one: nothing (a plan asks for its own)", None,
        inv=[("cooked_beef", 8), ("white_bed", 1)]),
    Row("the iron pickaxe broke under a mining plan, iron to make another: the same tier back", None,
        queued=lambda q: (("tool", "pickaxe", 2),) in q, held=[planner.Step("mine", "minecraft:raw_iron", 3, {"blocks": ["iron_ore"], "tier": 1})],
        inv=[("cooked_beef", 8), ("white_bed", 1), slot("iron_pickaxe", 1, 249), ("iron_ingot", 3), ("stick", 2),
             ("crafting_table", 1)],
        last_round=[("cooked_beef", 8), ("white_bed", 1), slot("iron_pickaxe", 1, 240)]),
    Row("the iron pickaxe broke under a mining plan, nothing to make one of: a pickaxe up to its tier", None,
        queued=lambda q: any(n[0][:2] == ("tool", "pickaxe") and n[0][2] <= 2 for n in q), held=[planner.Step("mine", "minecraft:raw_iron", 3, {"blocks": ["iron_ore"], "tier": 1})],
        inv=[("cooked_beef", 8), ("white_bed", 1), slot("iron_pickaxe", 1, 249)],
        last_round=[("cooked_beef", 8), ("white_bed", 1), slot("iron_pickaxe", 1, 240)]),
    Row("the iron pickaxe broke, no plan wanting one: not replaced now", None,
        inv=[("cooked_beef", 8), ("white_bed", 1), slot("iron_pickaxe", 1, 249)],
        last_round=[("cooked_beef", 8), ("white_bed", 1), slot("iron_pickaxe", 1, 240)]),
    Row("the stone sword broke, no plan wanting one: nothing", None, inv=WELL_FED, last_round=WELL_FED + [slot("stone_sword", 1, 125)]),
    Row("a tool that never worked is not broken", None, inv=WELL_FED, last_round=WELL_FED),
    Row("the stone axe broke: nothing (no plan step needs an axe)", None, inv=WELL_FED,
        last_round=WELL_FED + [slot("stone_axe", 1, 125)]),
    Row("hungry at night with a bed: eat first, then sleep", "eat", food=10, time_of_day=NIGHT),
    Row("the bag full and the path blocked: empty the bag first", "empty the bag", inv=full_bag("cobblestone"),
        blocked=(40, 64, 0)),
    # A night without a bed: the cheapest way through it (bed | dig in | wall in | hut), its parts fetched LEAD early.
    Row("dusk in 25 s, no bed, a pickaxe: dig in at dark — no sheep hunted for a bed", None, time_of_day=DUSK,
        inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
    Row("dusk in 25 s, no bed, no pickaxe, no sheep: dig in (a pickaxe), no bed", None, time_of_day=DUSK,
        queued=[[("tool", "pickaxe", 0)]], inv=[("cooked_beef", 8)], seen={"oak_log": 10, "stone": 2}),
    Row("dusk in 25 s, wool carried, no planks: the bed (19 s × LEAD) to the front", None,
        queued=[[("bed", 1)]],
        time_of_day=DUSK, inv=[("cooked_beef", 8), ("white_wool", 3)]),
    Row("dusk in 25 s, wool and planks carried: a bed is seconds away", None, time_of_day=DUSK,
        inv=[("cooked_beef", 8), ("stone_pickaxe", 1), ("white_wool", 3), ("oak_planks", 3), ("crafting_table", 1)]),
    Row("dusk in 25 s, cobblestone carried: walled in at dark", None, time_of_day=DUSK,
        inv=[("cooked_beef", 8), ("cobblestone", 16)], seen={}),
    Row("dusk in 25 s, no bed, no pickaxe, underground already: covered, nothing", None, time_of_day=DUSK,
        skyLight=0, y=30.0, inv=[("cooked_beef", 8)], seen={"oak_log": 10, "stone": 2}),
    Row("morning, no bed, nothing carried: plenty of day left", None, time_of_day=1000, inv=[("cooked_beef", 8)]),
    # Night: a bed skips it (made from what is carried), before any shelter or work underground.
    Row("night outside, wool, planks and a table carried: the bed to the front, no shelter", None,
        time_of_day=NIGHT, queued=[[("bed", 1)]],
        inv=[("cooked_beef", 8), ("white_wool", 3), ("oak_planks", 3), ("crafting_table", 1)]),
    Row("night outside with a bed: sleep", "sleep", time_of_day=NIGHT, inv=[("cooked_beef", 8), ("white_bed", 1)]),
    Row("night underground, no bed makings: nothing here (the night's work is the brain's)", None,
        time_of_day=NIGHT, skyLight=0, y=30.0, inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
    Row("night outside, no bed makings: shelter", "shelter", time_of_day=NIGHT,
        inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
    Row("afloat at dusk, no bed, no pickaxe: land first, nothing queued", "reach land", time_of_day=DUSK,
        inWater=True, onGround=False, inv=[("cooked_beef", 8)], seen={"oak_log": 10, "stone": 2}),
    Row("hungry, four bread carried: eat (bread is food)", "eat", food=10,
        inv=[("bread", 4), ("white_bed", 1), ("stone_pickaxe", 1)]),
    Row("hungry, cooked beef carried: eat", "eat", food=10, inv=[("cooked_beef", 2), ("white_bed", 1)]),
    Row("starving, only raw beef: eat it raw", "eat", food=4, inv=[("beef", 2), ("white_bed", 1)]),
    Row("hungry, nothing edible, cows near: no eat row, food to the front", None, food=10,
        queued=[[("food", 8)]], inv=[("white_bed", 1), ("stone_pickaxe", 1)], seen={"cow": 8}),
    Row("starving slowly, cows far away: food to the front (LEAD)", None, queued=[[("food", 8)]], food=3,
        inv=[("white_bed", 1), ("stone_pickaxe", 1)], seen={"cow": 45, "oak_log": 10, "stone": 2}),
    Row("full stomach, no meals, cows near: no hurry", None, food=20, inv=[("white_bed", 1), ("stone_pickaxe", 1)]),
    Row("a furnace job is done nearby", "collect job", job=((6, 64, 0), -5)),
    Row("the furnace job is still cooking", None, job=((6, 64, 0), 60)),
    Row("a finished job too far away to go back for", None, job=((400, 64, 0), -5)),
    Row("the auto smelter's order is due", "collect machine", machine=((8, 64, 0), -5)),
    Row("the smelter's order is not due", None, machine=((8, 64, 0), 120)),
    Row("the same block for 90 s at night, sheltered underground: resting, not stuck", None, stuck=True,
        time_of_day=NIGHT, skyLight=0, inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
    Row("the same block for 90 s at night in a sealed pod: resting, not stuck", None, stuck=True, enclosed=True,
        time_of_day=NIGHT, inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
]


def run_upkeep(row, tmp):
    """The real upkeep table, one round, on a real unstarted Brain. Returns (row name, [queued needs])."""
    b = brainmod.Brain.__new__(brainmod.Brain)
    b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
    b.mem = Memory(os.path.join(tmp, "notes.json"))
    b.retry, b.blacklist = retry.Retry(), {}
    b.place = PLACE
    b.needs = table = needs.Needs(b)
    b.reflexes = rx = reflexes.Maintain(b)
    b.held = {"t1": {"steps": row.held}} if row.held else {}
    now = time.time()
    for name in row.cooling:
        b.retry.failed(name, "error", "failed here", now, PLACE)
    if row.died:
        b.mem.log_death((6, 64, 0), row.state["dimension"])
    if row.job:
        b.mem.add_job("smelt", row.job[0], row.state["dimension"], "minecraft:iron_ingot", 3, now + row.job[1], [])
    if row.machine:
        name = b.mem.add_machine("auto_smelter", row.machine[0], 0, row.state["dimension"], ["smelting"])
        b.mem.add_pending(name, "minecraft:iron_ingot", 8, now + row.machine[1])
    snap = snapshot(row.state, row.inv)
    if row.last_round is not None:
        table.observe(snapshot(row.state, inventory(*row.last_round)))
    table.observe(snap)
    rx.observe(snap)
    if row.stuck:
        sig = needs.bag_signature(snap.inv)
        rx.history = [(now - 90 + i * 10, snap.feet, sig) for i in range(10)]
    if row.blocked is not None:
        rx.failed("nav", api.NavFailed("no path found", pos=row.blocked), row.place)
    c, plan_s = cost(snap, **row.seen), {}
    table.cost = lambda _snap: c                    # the row's readings stand in for /find and /entities
    for goal in (goals.have(("food", 8)), goals.have(("bed", 1))):     # fixture: the two upkeep prices
        try:
            steps = decompose.decompose(snap.inv, goal, c)
            secs, known = c.plan_s(steps), all(c.known_source(st) for st in steps)
        except Unplannable:
            secs, known = float("inf"), False
        plan_s[goal["args"]["needs"][0][0]] = secs
        plan_s[goal["args"]["needs"][0][0] + ":known"] = known
    plan_s["overnight"] = needs.overnight(snap.inv, c)
    plan_s["overnight:known"] = all(c.known_source(st) for st in plan_s["overnight"][2])
    with mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")), \
            mock.patch.object(api, "api", side_effect=AssertionError("upkeep read the world beyond the row")):
        reads = {"enclosed": row.enclosed, "bed_near": row.bed_seen, "soft_ground": False}
        got = rx.act(snap, ctx=None, reads=dict(reads))
        table.propose(snap, None, reads=dict(reads))
        if got:                                     # MAINTAIN outranks PLAN (arbiter.SCALES): no need acted this round
            table.needs_now = []
        # What upkeep wants got is proposed, never queued (arbiter.PLAN_ORDER ranks it): read off the proposals.
        queued = [tuple(tuple(n) for n in goal["args"]["needs"]) for _kind, goal, _why in table.needs_now]
    return (got[0] if got else None), queued, plan_s


class Upkeep(unittest.TestCase):
    def test_state_to_choice(self):
        for row in UPKEEP:
            with self.subTest(row.name), tempfile.TemporaryDirectory() as tmp:
                chosen, queued, _ = run_upkeep(row, tmp)
                self.assertEqual(chosen, row.row)
                if callable(row.queued):
                    self.assertTrue(row.queued(set(queued)), f"queued {queued}")
                else:
                    self.assertEqual(set(queued), row.queued)

    def test_lead_time_over_the_sweep(self):
        """Bed and food go to the front exactly when the time left (dusk_s, food_lasts_s) is shorter than the plan
        that would get them (Σ est) × LEAD — swept over around × body × bag × clock, and for three values of LEAD so
        the table cannot be reading a constant of its own."""
        from tests.world import RESOURCES
        cells = worlds(resource=["bare", "herd", "village"], self_=["ready", "hungry", "underground", "nether"],
                       stock=["none", "logs", "stone_tools", "kit"])
        for w, lead in ((w, lead) for w in cells for lead in (1.0, needs.LEAD, 3.0)):
            row = Row(repr(w), None)
            row.state, row.inv, row.seen = w.game_state(), w.inventory(), RESOURCES[w.dims["resource"]]
            snap = snapshot(row.state, row.inv)
            with self.subTest(world=w, lead=lead), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(needs, "LEAD", lead):
                chosen, queued, plan_s = run_upkeep(row, tmp)
                if chosen is not None:
                    continue                                # a row took the round: nothing is queued this round
                over = snap.dimension == OVER
                way, secs, _ = plan_s["overnight"]
                # A plan that knows where it goes leads by × LEAD; one that guesses waits for the real threshold.
                bed_due = needs.dusk_s(snap) < secs * lead if plan_s["overnight:known"] else needs.dusk_s(snap) <= 0
                bed = over and not snap.night and snap.inv.count("bed") == 0 and way == "bed" and bed_due \
                    and snap.get("skyLight", 15) > COVERED_SKY
                food_due = needs.food_lasts_s(snap) < plan_s["food"] * lead if plan_s["food:known"] \
                    else snap.get("food", 20) < reflexes.EAT_BELOW
                food = food_count(snap.inv) < 8 and food_due
                self.assertEqual(((("bed", 1),) in queued), bed, f"dusk in {needs.dusk_s(snap)} s, {way} plan "
                                                                  f"{secs:.0f} s × {lead}")
                self.assertEqual(((("food", 8),) in queued), food, f"food lasts {needs.food_lasts_s(snap):.0f} s, "
                                                                    f"plan {plan_s['food']:.0f} s × {lead}")

    def test_lead_moves_the_verdict(self):
        """The same dusk, the same bag: a longer lead inserts the bed, a shorter one does not."""
        for lead, want in ((0.0, set()), (0.1, set()), (50.0, {(("bed", 1),)}), (1000.0, {(("bed", 1),)})):
            row = Row(f"lead {lead}", None, time_of_day=DUSK, inv=[("cooked_beef", 8), ("white_wool", 3)])
            with self.subTest(lead=lead), tempfile.TemporaryDirectory() as tmp, mock.patch.object(needs, "LEAD", lead):
                _, queued, _ = run_upkeep(row, tmp)
                self.assertEqual(set(queued) & {(("bed", 1),)}, want)

    def test_dusk_clock(self):
        for tod, secs in ((0, 600.0), (6000, 300.0), (11500, 25.0), (12000, 0.0), (18000, 0.0), (24000 + 6000, 300.0)):
            with self.subTest(timeOfDay=tod):
                self.assertEqual(needs.dusk_s(snapshot(state(timeOfDay=tod))), secs)

    def test_food_clock_grows_with_food(self):
        ladder = [needs.food_lasts_s(snapshot(state(food=f), inventory(("cooked_beef", m))))
                  for f, m in ((2, 0), (10, 0), (20, 0), (20, 4))]
        self.assertEqual(ladder, sorted(ladder))
        self.assertEqual(len(set(ladder)), len(ladder))

    def test_nether_retreat(self):
        rows = [({}, [("cooked_beef", 8)], None),
                ({"dimension": NETHER}, [("cooked_beef", 8)], None),
                ({"dimension": NETHER}, [("cooked_beef", 3)], "food running out"),
                ({"dimension": NETHER, "health": 8.0}, [("cooked_beef", 8)], "health low"),
                ({"dimension": NETHER}, "full", "bag full")]
        for st, inv, why in rows:
            with self.subTest(st=st, inv=inv):
                snap = snapshot(state(**st), full_bag("cooked_beef") if inv == "full" else inventory(*inv))
                self.assertEqual(reflexes.nether_retreat(snap), why)

    def test_beds_work_at_night_in_the_overworld_only(self):
        rows = [({"timeOfDay": 18000}, True), ({"timeOfDay": 6000}, False), ({"timeOfDay": 6000, "thundering": True}, True),
                ({"timeOfDay": 18000, "dimension": NETHER}, False),
                ({"timeOfDay": 18000, "dimension": "minecraft:the_end"}, False)]
        for st, ok in rows:
            with self.subTest(st):
                self.assertEqual(skills.can_sleep(state(**st)) is None, ok)

    def test_working_tiers(self):
        rows = [(inventory(), {}), (inventory(("stone_pickaxe", 1), ("iron_sword", 1)), {"pickaxe": 1, "sword": 2}),
                (inventory(slot("iron_pickaxe", 1, 249), ("wooden_pickaxe", 1)), {"pickaxe": 0}),
                (inventory(slot("iron_axe", 1, 247)), {"axe": 2}), (inventory(slot("iron_axe", 1, 248)), {})]
        for inv, want in rows:
            with self.subTest(want=want):
                self.assertEqual(needs.working_tiers(bag(inv)), want)


# (bag, tool kind, the tier a broken tool is replaced at: the best this bag crafts with crafting steps alone)
CRAFTABLE = [
    ("nothing at all: wood (the plan gathers)", inventory(), "pickaxe", 0),
    ("cobblestone, sticks, a table", inventory(("cobblestone", 3), ("stick", 2), ("crafting_table", 1)), "pickaxe", 1),
    ("cobblestone and planks: sticks are a craft too", inventory(("cobblestone", 3), ("oak_planks", 8)), "pickaxe", 1),
    ("iron, sticks, a table", inventory(("iron_ingot", 3), ("stick", 2), ("crafting_table", 1)), "pickaxe", 2),
    ("iron but raw: smelting is not crafting", inventory(("raw_iron", 3), ("stick", 2), ("crafting_table", 1),
                                                         ("cobblestone", 3)), "pickaxe", 1),
    ("diamonds", inventory(("diamond", 3), ("stick", 2), ("crafting_table", 1)), "pickaxe", 3),
    ("two iron make a sword, not a pickaxe", inventory(("iron_ingot", 2), ("stick", 2), ("crafting_table", 1)),
     "sword", 2),
    ("two iron make a sword, not a pickaxe (pickaxe side)", inventory(("iron_ingot", 2), ("stick", 2),
                                                                       ("crafting_table", 1)), "pickaxe", 0),
    ("logs only: planks are a craft, but wood is tier 0 anyway", inventory(("oak_log", 4)), "axe", 0),
]


class Craftable(unittest.TestCase):
    def test_bag_to_replacement_tier(self):
        for name, inv, kind, tier in CRAFTABLE:
            with self.subTest(name):
                self.assertEqual(needs.craftable_tier(bag(inv), kind), tier)

    def test_every_tool_kind_has_a_row_or_is_wood(self):
        for kind in needs.TOOL_KINDS:
            with self.subTest(kind):
                self.assertEqual(needs.craftable_tier(bag(inventory()), kind), 0)


DAY_T = 24000
# What memory keeps of what was seen (data.VOLATILITY), on the game clock. ops: ("at", tick) sets memory's clock;
# ("see", kind, pos); ("forget", kind, pos); ("confirm", kind, pos, found); ("dug", [cells]).
# expected: [(pos, to-verify?)] of `kind`, newest first.
NOTES = [
    ("an ore is static: still there a year later", [("at", 0), ("see", "iron_ore", (3, 12, 3)), ("at", 10 ** 7)],
     "iron_ore", [((3, 12, 3), False)]),
    ("a tree is slow: kept three days", [("at", 0), ("see", "tree", (10, 64, 10)), ("at", 3 * DAY_T)], "tree",
     [((10, 64, 10), False)]),
    ("a tree is slow: gone after three days", [("at", 0), ("see", "tree", (10, 64, 10)), ("at", 3 * DAY_T + 1)],
     "tree", []),
    ("seen again: refreshed, one note", [("at", 0), ("see", "tree", (10, 64, 10)), ("at", 70000),
                                         ("see", "tree", (10, 64, 10)), ("at", 3 * DAY_T + 5000)], "tree",
     [((10, 64, 10), False)]),
    ("two trees 5 apart are one grove", [("at", 0), ("see", "tree", (10, 64, 10)), ("see", "tree", (15, 64, 10))],
     "tree", [((10, 64, 10), False)]),
    ("a cow is mobile: an area, not a cell", [("at", 0), ("see", "cow", (5, 64, 5))], "cow", [((8, 64, 8), False)]),
    ("a cow is gone after 6000 ticks", [("at", 0), ("see", "cow", (5, 64, 5)), ("at", 6001)], "cow", []),
    ("a hostile is never stored", [("at", 0), ("see", "zombie", (5, 64, 5))], "zombie", []),
    ("arrived, found nothing: retired", [("at", 0), ("see", "iron_ore", (3, 12, 3)),
                                         ("confirm", "iron_ore", (3, 12, 3), False)], "iron_ore", []),
    ("arrived, found it: kept", [("at", 0), ("see", "iron_ore", (3, 12, 3)),
                                 ("confirm", "iron_ore", (3, 12, 3), True)], "iron_ore", [((3, 12, 3), False)]),
    ("an empty spot elsewhere drops only its own note", [("at", 0), ("see", "diamond_ore", (3, 12, 3)),
                                                         ("see", "diamond_ore", (90, 12, 3)),
                                                         ("forget", "diamond_ore", (90, 12, 3))],
     "diamond_ore", [((3, 12, 3), False)]),
    ("we mined the ore ourselves: gone", [("at", 0), ("see", "iron_ore", (3, 50, 3)), ("dug", [(3, 50, 3)])],
     "iron_ore", []),
    ("diamond: kept for good", [("at", 0), ("see", "diamond_ore", (3, 12, 3)), ("at", 10 ** 7)], "diamond_ore",
     [((3, 12, 3), False)]),
    ("a village: kept for good", [("at", 0), ("see", "village", (300, 64, 3)), ("at", 10 ** 7)], "village",
     [((300, 64, 3), False)]),
    ("coal: common, never noted", [("at", 0), ("see", "coal_ore", (3, 50, 3))], "coal_ore", []),
    ("a crafting table: a station, memory.stations' alone", [("at", 0), ("see", "crafting_table", (2, 64, 2))],
     "crafting_table", []),
    ("standing at stone: noted for the next plan", [("at", 0), ("here", "stone", (1, 64, 1)), ("at", 2000)],
     "stone", [((1, 64, 1), False)]),
    ("standing at stone, three minutes on: gone", [("at", 0), ("here", "stone", (1, 64, 1)), ("at", 3600)],
     "stone", []),
    ("standing at iron: kept as iron is", [("at", 0), ("here", "iron_ore", (1, 12, 1)), ("at", 10 ** 7)],
     "iron_ore", [((1, 12, 1), False)]),
    ("we dug near a tree: to verify, not gone", [("at", 0), ("see", "tree", (10, 64, 10)), ("dug", [(12, 64, 10)])],
     "tree", [((10, 64, 10), True)]),
    ("dug far from the tree: untouched", [("at", 0), ("see", "tree", (10, 64, 10)), ("dug", [(40, 64, 10)])],
     "tree", [((10, 64, 10), False)]),
    ("an unknown kind is never noted", [("at", 0), ("see", "sugar_cane", (4, 64, 4))], "sugar_cane", []),
    ("village furniture is static", [("at", 0), ("see", "red_bed", (4, 64, 4)), ("at", 10 ** 7)], "red_bed",
     [((4, 64, 4), False)]),
]


class WhereToLook(unittest.TestCase):
    def test_note_sequences(self):
        for name, ops, kind, want in NOTES:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                for op_, *a in ops:
                    if op_ == "at":
                        m.clock = a[0]
                    elif op_ == "see":
                        m.note_seen(a[0], a[1], OVER)
                    elif op_ == "here":
                        m.note_here(a[0], a[1], OVER)
                    elif op_ == "forget":
                        m.forget_seen(a[0], a[1], OVER)
                    elif op_ == "confirm":
                        m.confirm(a[0], a[1], OVER, found=a[2])
                    elif op_ == "dug":
                        m.mark_dirty_near(a[0], OVER)
                self.assertEqual([(tuple(r["pos"]), bool(r.get("verify"))) for r in m.seen(kind, OVER)], want)

    def test_a_station_is_the_stations_record_alone(self):
        """A crafting table we placed: memory.stations has it, the seen notes never do (one source)."""
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.add_station("minecraft:crafting_table", (2, 64, 2), OVER)
            m.note_seen("crafting_table", (2, 64, 2), OVER)
            self.assertEqual(([s["pos"] for s in m.stations(OVER)], m.seen("crafting_table", OVER)), ([[2, 64, 2]], []))

    def test_every_volatility_class_has_rows(self):
        from bonobo.data import VOLATILITY, seen_class
        covered = {seen_class(k) for _n, _ops, k, _w in NOTES} \
            | {"here" for _n, ops, _k, _w in NOTES if any(op[0] == "here" for op in ops)}
        self.assertEqual(covered, set(VOLATILITY))


# (situation, what a chest holds (as last seen open) and how far, what /find saw, the step chosen / not chosen)
WITHDRAW = [
    ("4 logs in a chest 3 away, trees 40 away: take them", (3, {"minecraft:oak_log": 4}), {"oak_log": 40},
     ("withdraw", "minecraft:oak_log"), ("gather", "log")),
    ("the chest is 90 away, trees 5 away: chop", (90, {"minecraft:oak_log": 4}), {"oak_log": 5},
     ("gather", "log"), ("withdraw", "minecraft:oak_log")),
    ("the chest holds 2 of 4: take 2, chop the rest", (3, {"minecraft:oak_log": 2}), {"oak_log": 40},
     ("withdraw", "minecraft:oak_log"), None),
    ("the chest holds something else", (3, {"minecraft:cobblestone": 64}), {"oak_log": 40}, ("gather", "log"),
     ("withdraw", "minecraft:oak_log")),
]


# (situation, goal, what a chest 2 blocks away holds) → is anything withdrawn? Only the goal's own needs are taken
# from containers (top level), and only when cheaper than making them.
WITHDRAW_GOALS = [
    ("sticks in the chest, a pickaxe asked: sticks are an intermediate, made not fetched", PICK1,
     {"minecraft:stick": 8}, False),
    ("sticks in the chest, sticks asked: fetched", goals.have(("minecraft:stick", 4)), {"minecraft:stick": 8}, True),
    ("a pickaxe in the chest: tools are not withdrawn", PICK1, {"minecraft:stone_pickaxe": 1}, False),
    ("logs in the chest, logs asked, trees far: fetched", goals.have(("log", 4)), {"minecraft:oak_log": 8}, True),
    ("cobblestone in the chest, a pickaxe asked: an intermediate, mined not fetched", PICK1,
     {"minecraft:cobblestone": 16}, False),
]


class Withdraw(unittest.TestCase):
    def test_top_level_only(self):
        for name, goal, items, want in WITHDRAW_GOALS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                m.note_container((2, 64, 0), OVER, [{"id": i, "count": n} for i, n in items.items()])
                snap = snapshot()
                steps = decompose.decompose(snap.inv, goal, cost(snap, mem=m, oak_log=30, stone=20))
                self.assertEqual(any(st.kind == "withdraw" for st in steps), want, list(map(str, steps)))

    def test_chest_or_make(self):
        for name, (dist, items), seen, chosen, not_chosen in WITHDRAW:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                m.note_container((dist, 64, 0), OVER, [{"id": i, "count": n} for i, n in items.items()])
                snap = snapshot()
                steps = decompose.decompose(snap.inv, goals.have(("log", 4)), cost(snap, mem=m, **seen))
                self.assertIn(pair(*chosen), pairs(steps), list(map(str, steps)))
                if not_chosen:
                    self.assertNotIn(pair(*not_chosen), pairs(steps), list(map(str, steps)))
                took = sum(st.count for st in steps if st.kind == "withdraw")
                want = min(4, items.get("minecraft:oak_log", 0)) if chosen[0] == "withdraw" else 0
                self.assertEqual(took, want, "exactly what the chest holds of the need, no more")


# --------------------------------------------------------------------------------------------------------- retry
I, C = api.Interrupted("perception: lava"), api.CommitmentExpired("a faster layer took the body")
B, P = api.BodyContested("another commander"), api.PlayerTookControl()
NAV = api.NavFailed("no path found", pos=(9, 64, 0))
GONE = api.NotAvailable("no sheep in range")
TOOL = skillcore.ToolMissing("pickaxe", 1)
HERE, THERE = PLACE, retry.place_signature((400, 64, 0), False)

# (situation, [(task, exception, place)], expected {(task, cause): n}, escalated tasks, {task: ready now?})
RETRY = [
    ("three nav failures escalate on the third", [("task t1", NAV, HERE)] * 3, {("task t1", "nav"): 3}, {"task t1"},
     {"task t1": False}),
    ("interruptions of every kind count nothing", [("task t1", e, HERE) for e in (I, C, B, P, I)], {}, set(),
     {"task t1": True}),
    ("interleaved: interruptions do not reset or add", [("task t1", NAV, HERE), ("task t1", I, HERE),
                                                        ("task t1", NAV, HERE), ("task t1", C, HERE),
                                                        ("task t1", NAV, HERE)],
     {("task t1", "nav"): 3}, {"task t1"}, {}),
    ("two causes are counted apart", [("task t1", NAV, HERE), ("task t1", GONE, HERE), ("task t1", NAV, HERE)],
     {("task t1", "nav"): 2, ("task t1", "unavailable"): 1}, set(), {}),
    ("the cause cools at the place: another task stopped by it waits here, not elsewhere",
     [("task t1", TOOL, HERE)], {("task t1", "tool"): 1}, set(),
     {"task t1": False, ("task t2", "tool", HERE): False, ("task t2", "tool", THERE): True}),
    ("a success clears the count", [("task t1", NAV, HERE), ("task t1", NAV, HERE), ("task t1", "ok", HERE),
                                    ("task t1", NAV, HERE)], {("task t1", "nav"): 1}, set(), {}),
]


# Retry as a pure ledger over time. ops: ("fail", task, cause, t) / ("hold", name, s, t) / ("cap", name, s, t) /
# ("release", name) / ("ok", task). Then, at `now`: ready?, exhausted, last failure, what is cooling.
P = ("here", False)          # fixture: the place causes cool at
NAV_S = retry.BACKSTOP["nav"]
LEDGER = [
    ("three sources failed: exhausted, with the last message", [("fail", "t", "nav", 0), ("fail", "t", "nav", 1),
                                                               ("fail", "t", "nav", 2)],
     3, {"ready": False, "exhausted": ("nav", "m2"), "last": 2, "cooling": ["nav@('here', False)"]}),
    ("two sources: not yet", [("fail", "t", "nav", 0), ("fail", "t", "nav", 1)], 2,
     {"exhausted": None, "last": 1}),
    ("the worst cause is the one reported", [("fail", "t", "tool", 0), ("fail", "t", "nav", 1), ("fail", "t", "nav", 2),
                                             ("fail", "t", "nav", 3)], 4, {"exhausted": ("nav", "m3")}),
    ("cooled out: ready again after the wait", [("fail", "t", "nav", 0)], NAV_S + 1,
     {"ready": True, "cooling": [], "exhausted": None}),
    ("a hold throttles a name without a failure", [("hold", "deposit", 60, 0)], 30,
     {"ready_of": ("deposit", False), "exhausted": None, "cooling": []}),
    ("a hold ends", [("hold", "deposit", 60, 0)], 61, {"ready_of": ("deposit", True)}),
    ("cap shortens the hold and the cooling", [("fail", "t", "nav", 0), ("hold", "t", 500, 0), ("cap", "t", 5, 0)],
     6, {"ready": True}),
    ("release forgets the counts and the hold", [("fail", "t", "nav", 0), ("fail", "t", "nav", 1),
                                                 ("fail", "t", "nav", 2), ("hold", "t", 500, 2), ("release", "t")],
     3, {"exhausted": None, "last": None}),
    ("a success forgets the counts, not the cooling at the place", [("fail", "t", "nav", 0), ("ok", "t")], 1,
     {"exhausted": None, "last": None, "cooling": ["nav@('here', False)"], "ready_of": ("u", True)}),
]


class Ledger(unittest.TestCase):
    def test_sequences(self):
        for name, ops, now, want in LEDGER:
            r, n = retry.Retry(), 0
            with self.subTest(name):
                for op_, *a in ops:
                    if op_ == "fail":
                        r.failed(a[0], a[1], f"m{n}", a[2], P)
                        n += 1
                    elif op_ == "hold":
                        r.hold(a[0], a[1], a[2])
                    elif op_ == "cap":
                        r.cap(a[0], a[1], a[2], P)
                    elif op_ == "release":
                        r.release(a[0])
                    elif op_ == "ok":
                        r.succeeded(a[0])
                if "ready" in want:
                    self.assertEqual(r.ready("t", now, P), want["ready"])
                if "ready_of" in want:
                    self.assertEqual(r.ready(want["ready_of"][0], now, P), want["ready_of"][1])
                if "exhausted" in want:
                    self.assertEqual(r.exhausted("t"), want["exhausted"])
                if "last" in want:
                    self.assertEqual(r.last_failure("t"), want["last"])
                if "cooling" in want:
                    self.assertEqual(r.cooling_now(now), want["cooling"])


def new_brain(tmp):
    b = brainmod.Brain.__new__(brainmod.Brain)
    b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
    b.mem = Memory(os.path.join(tmp, "notes.json"))
    b.retry, b.blacklist, b.place = retry.Retry(), {}, HERE
    b.needs, b.reflexes = needs.Needs(b), reflexes.Maintain(b)
    return b


class Retry(unittest.TestCase):
    def test_event_sequences(self):
        for name, events, counts, escalated, ready in RETRY:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                b, up = new_brain(tmp), set()
                for task, err, place in events:
                    b.place = place
                    if err == "ok":
                        b.retry.succeeded(task)
                        continue
                    verdict = b.failed(task, err)
                    self.assertEqual(verdict is None, api.interrupted(err))
                    if verdict is not None and verdict.escalate:
                        up.add(task)
                self.assertEqual({k: e["n"] for k, e in b.retry.entries.items()}, counts)
                self.assertEqual(up, escalated)
                for key, want in ready.items():       # task, or (task, the cause that would stop it, place)
                    task, cause, b.place = key if isinstance(key, tuple) else (key, None, HERE)
                    self.assertEqual(b.ready(task, cause=cause), want, key)
                if not counts:
                    self.assertEqual(b.retry.cooling, {}, "an interruption cooled something")
                    self.assertIsNone(b.reflexes.blocked, "an interruption was taken for a blocked path")

    def test_cooldown_doubles_up_to_its_ceiling(self):
        r, now = retry.Retry(), 1000.0
        waits = [r.failed(f"task t{i}", "nav", "no path", now, HERE).wait for i in range(8)]
        self.assertEqual(waits[0], retry.BACKSTOP["nav"])
        for a, b in zip(waits, waits[1:]):
            self.assertEqual(b, min(retry.MAX_BACKSTOP["nav"], 2 * a))

    def test_bans_escalate_and_cap(self):
        ctx = skillcore.Context(None, None, OVER, blacklist={})
        ctx.ban_counts = {}
        cell, other = (5, 64, 5), (6, 64, 5)
        spans = []
        for _ in range(6):
            t0 = time.time()
            ctx.ban(cell, seconds=60)
            spans.append(round(ctx.blacklist[cell] - t0))
        self.assertEqual(spans, sorted(spans))
        self.assertLessEqual(max(spans), skillcore.BAN_MAX_S)
        self.assertEqual(spans[:2], [60, 120])
        self.assertTrue(ctx.blocked(cell))
        self.assertFalse(ctx.blocked(other))

    # fixture: (module source) → (ban sites, the ones that follow a go_to walk in the same function)
    BANS = [("a ban after arrived: fine", "def f(ctx):\n    if not nav.arrived(c, p):\n        ctx.ban(c)\n", (1, [])),
            ("a ban after go_to: a stopped walk bans the place",
             "def f(ctx):\n    ok = nav.go_to(c, p)\n    if not ok:\n        ctx.ban(c)\n", (1, ["f"])),
            ("go_to in another function does not count",
             "def g():\n    nav.go_to(c, p)\ndef f(ctx):\n    self.ban(c)\n", (1, [])),
            ("go_to after the ban does not count", "def f(ctx):\n    ctx.ban(c)\n    nav.go_to(c, p)\n", (1, [])),
            ("no ban at all", "def f():\n    nav.go_to(c, p)\n", (0, []))]

    def test_ban_sites_over_the_fixture(self):
        for name, src, want in self.BANS:
            with self.subTest(name):
                sites = ban_sites(src)
                self.assertEqual((len(sites), [fn for fn, after_go_to in sites if after_go_to]), want)

    def test_the_failure_path_is_the_only_banning_path(self):
        """`arrived` answers False only for a walk that failed; an interruption propagates (test point A, ARRIVE). So
        a ban that follows a walk must follow `nav.arrived`, never `nav.go_to` (a Walked leg is truthy, a stopped
        walk is False). Every ban site in the package is read; there must be some, or this says nothing."""
        import glob
        sites, bad = 0, []
        for path in sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                  "bonobo", "*.py"))):
            with open(path) as f:
                found = ban_sites(f.read())
            sites += len(found)
            bad += [f"{os.path.basename(path)}:{fn}" for fn, after_go_to in found if after_go_to]
        self.assertNotEqual(sites, 0, "no ban site found: the reading of the source is broken")
        self.assertEqual(bad, [], "a ban after go_to: a walk that was merely stopped bans the place")


def ban_sites(src):
    """Pure: [(function, whether a go_to call comes before it in that function)] for every `<x>.ban(...)` call in a
    module's source (its AST, call graph within the function)."""
    import ast
    out = []
    for fn in [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
        walks = [c.lineno for c in calls if c.func.attr == "go_to"]
        for c in calls:
            if c.func.attr == "ban":
                out.append((fn.name, any(w < c.lineno for w in walks)))
    return out


# --------------------------------------------------------------------------------------------------------- queue
def op_add(goal, **kw):
    return lambda path, now: tasks.add(goal, path=path, now=now, **kw)


def op(fn, *a, **kw):
    return lambda path, now: fn(*a, path=path, **kw)


def op_expire(dt):
    def run(path, now):
        items = tasks.load(path)
        if tasks.expire(items, now=now + dt):
            tasks.save(items, path)
    return run


G1, G2, G3 = goals.have(("log", 4)), goals.have(("tool", "pickaxe", 1)), goals.make("sleep")
# (situation, operations, expected [(id, state)], expected head id)
QUEUE = [
    ("two goals queue in order", [op_add(G1), op_add(G2)], [("t1", "pending"), ("t2", "pending")], "t1"),
    ("the same live goal is not queued twice", [op_add(G1), op_add(G1)], [("t1", "pending")], "t1"),
    ("upkeep puts its goal in front", [op_add(G1), op_add(G2, front=True, source="upkeep")],
     [("t2", "pending"), ("t1", "pending")], "t2"),
    ("done is not live: the next one is the head", [op_add(G1), op_add(G2), op(tasks.mark, "t1", "done")],
     [("t1", "done"), ("t2", "pending")], "t2"),
    ("failed with a reason", [op_add(G1), op(tasks.mark, "t1", "failed", "nav: no path")], [("t1", "failed")], None),
    ("expired tasks are cancelled", [op_add(G1, expires_s=60), op_add(G2), op_expire(61)],
     [("t1", "cancelled"), ("t2", "pending")], "t2"),
    ("not yet expired", [op_add(G1, expires_s=60), op_expire(30)], [("t1", "pending")], "t1"),
    ("cancel all", [op_add(G1), op_add(G2), op(tasks.cancel)], [("t1", "cancelled"), ("t2", "cancelled")], None),
    ("clear drops what is not live", [op_add(G1), op_add(G2), op(tasks.mark, "t1", "done"), op(tasks.clear)],
     [("t2", "pending")], "t2"),
    ("a finished goal can be queued again", [op_add(G3), op(tasks.mark, "t1", "done"), op_add(G3)],
     [("t1", "done"), ("t2", "pending")], "t2"),
    ("running keeps its plan; leaving LIVE drops it",
     [op_add(G1), op(tasks.update, "t1", state="running", plan=[{"kind": "gather", "token": "log", "count": 4}])],
     [("t1", "running")], "t1"),
]


# goals as data: parse (command line) → need; (bag, needs) → what is short; goal → its one-line description.
PARSE = [(("tool:pickaxe:2",), ["tool", "pickaxe", 2]), (("minecraft:torch", "24"), ["minecraft:torch", 24]),
         (("log",), ["log", 1]), (("tool:sword:0",), ["tool", "sword", 0]),
         (("minecraft:oak_log", "64"), ["minecraft:oak_log", 64])]
SHORT = [
    ("nothing asked", inventory(), [], ""),
    ("held", inventory(("oak_log", 4)), [("log", 4)], ""),
    ("short of logs", inventory(("oak_log", 1)), [("log", 4)], "log 1/4"),
    ("a worn tool is not a tool", inventory(slot("stone_pickaxe", 1, 125)), [("tool", "pickaxe", 1)], "pickaxe tier 1"),
    ("a better tool does", inventory(("iron_pickaxe", 1)), [("tool", "pickaxe", 1)], ""),
    ("food counts cooked meals only", inventory(("beef", 8), ("cooked_beef", 2)), [("food", 8)], "food 2/8"),
    ("two short, both said", inventory(), [("log", 2), ("minecraft:torch", 3)], "log 0/2, torch 0/3"),
]
DESCRIBE = [(goals.have(("minecraft:torch", 24), ("tool", "pickaxe", 2)), "have torch×24, pickaxe tier 2"),
            (goals.make("milestone", name="food"), "milestone food"), (goals.make("goto", pos=[1, 2, 3]), "goto (1, 2, 3)"),
            (goals.make("road", a=[0, 0, 0], b=[5, 0, 0]), "road (0, 0, 0) → (5, 0, 0)"),
            (goals.make("build", bp="shelter"), "build shelter"), (goals.make("sleep"), "sleep"),
            (goals.make("skill", name="chop", args=[4]), "skill chop [4]")]


class GoalsAsData(unittest.TestCase):
    def test_parse(self):
        for args, want in PARSE:
            with self.subTest(args):
                self.assertEqual(goals.parse_need(*args), want)
        for bad in (("tool:pickaxe",), ("tool:pickaxe:iron",), ("log", "many"), ("tool:pickaxe:1:2",)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                goals.parse_need(*bad)

    def test_short(self):
        for name, inv, needs, want in SHORT:
            with self.subTest(name):
                self.assertEqual(goals.short(bag(inv), needs), want)

    def test_describe_and_round_trip(self):
        for goal, want in DESCRIBE:
            with self.subTest(want), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(goals.describe(goal), want)
                t = tasks.add(goal, path=os.path.join(tmp, "t.json"))
                self.assertEqual(tasks.goal_of(t), goal)
                self.assertEqual(tasks.describe(t), f"t1 [pending] {want}")


class Queue(unittest.TestCase):
    def test_operation_sequences(self):
        for name, ops, want, head in QUEUE:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "tasks.json")
                for fn in ops:
                    fn(path, 1000.0)
                items = tasks.load(path)
                self.assertEqual([(t["id"], t["state"]) for t in items], want)
                self.assertEqual((tasks.head(items) or {}).get("id"), head)
                for t in items:
                    if t["state"] not in tasks.LIVE:
                        self.assertIsNone(t.get("plan"), "a finished task keeps no plan")

    def test_bad_input_is_refused(self):
        for name, call in (("unknown state", lambda p: tasks.mark("t1", "paused", path=p)),
                           ("unknown template", lambda p: goals.make("teleport")),
                           ("unknown milestone", lambda p: goals.make("milestone", name="win")),
                           ("empty milestone name", lambda p: goals.make("milestone", name="")),
                           ("made-up task state", lambda p: tasks.mark("t1", "bogus", path=p))):
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "tasks.json")
                tasks.add(G1, path=path)
                with self.assertRaises(ValueError):
                    call(path)

    def test_every_template_decomposes_or_says_why(self):
        """Extensible: a new template in goals.TEMPLATES needs a decompose branch (or a clear Unplannable)."""
        args = {"have": {"needs": [["log", 1]]}, "craft": {"needs": [["minecraft:stick", 4]]},
                "milestone": {"name": "food"}, "goto": {"pos": [5, 64, 0]}, "road": {"a": [0, 64, 0], "b": [5, 64, 0]},
                "build": {"bp": "shelter"}, "sleep": {}, "skill": {"name": "chop", "args": [1]},
                "effect": {"effect": "breed"}}
        self.assertEqual(set(args), set(goals.TEMPLATES), "a template without a row here")
        for template, a in args.items():
            with self.subTest(template):
                steps = plan({"goal": template, "args": a}, snapshot(), {"oak_log": 5, "stone": 2, "cow": 9})
                self.assertIsInstance(steps, list)



# ------------------------------------------------------------------------------------------ the night's work
class OneArbiter(unittest.TestCase):
    """Every layer proposes, arbiter.arbitrate chooses: the faster layer, then arbiter.PLAN_ORDER, then the place in
    line. The day's failures, as the proposals each situation makes (brain.decide / needs.proposals)."""

    @staticmethod
    def intent(layer, kind=None, seq=0, deadline_s=None, at=None):
        from bonobo import arbiter
        return arbiter.Intent(layer, kind or layer, kind=kind, seq=seq, deadline_s=deadline_s, at=at)

    def test_decisions_over_the_table(self):
        from bonobo import arbiter, reflexes
        P = lambda kind, seq=0: self.intent("plan", kind, seq)   # noqa: E731

        def M(name):                                            # a maintenance reflex, in its table's place
            return arbiter.Intent("maintain", name, seq=reflexes.NAMES.index(name))
        rows = [("afloat at dusk, the night's shelter due: land first", [M("shelter"), M("reach land")], "reach land"),
                ("hungry, bread carried, a task queued: eat (a reflex before any plan)", [P("queue"), M("eat")], "eat"),
                ("night, a bed carried: sleep before the shelter", [M("shelter"), M("sleep")], "sleep"),
                ("a fight holds the body, the queue wants a pickaxe: the fight",
                 [P("queue"), self.intent("tactic", "fight")], "fight"),
                ("a fight vs a reflex (eat): the fight", [M("eat"), self.intent("tactic", "fight")], "fight"),
                ("drowning in a fight: the rescue", [self.intent("tactic", "fight"), self.intent("safety", "rescue")],
                 "rescue"),
                ("a rescue vs a reflex: the rescue", [M("reach land"), self.intent("safety", "rescue")], "rescue"),
                ("raw meat and a furnace, food queued: the queue's head (smelt), no hunt proposed", [P("queue")],
                 "queue"),
                ("the queue's head before the second in line", [P("queue", 1), P("queue", 0)], "queue"),
                ("a tool broke under a held plan, a task queued: the tool first (proposed, not queued)",
                 [P("queue"), P("broken tool")], "broken tool"),
                ("a fall in the plan and no bucket, a task queued: the bucket first", [P("queue"), P("water bucket")],
                 "water bucket"),
                ("dusk soon, a bed or shelter parts to get, a task queued: the night's parts first",
                 [P("queue"), P("night prep")], "night prep"),
                ("path blocked, no blocks, a task queued: bridge blocks first", [P("queue"), P("bridge stock")],
                 "bridge stock"),
                ("food running out before it could be had, a task queued: food first", [P("queue"), P("food stock")],
                 "food stock"),
                ("hungry and food running out: eat what is carried (reflex) before getting more (plan)",
                 [P("food stock"), M("eat")], "eat"),
                ("a broken tool vs the night's parts at dusk: the night first", [P("broken tool"), P("night prep")],
                 "night prep"),
                ("afloat with the night's parts due: land first", [P("night prep"), M("reach land")], "reach land"),
                ("night underground, nothing queued, a pickaxe: dig for ore before waiting",
                 [P("wait for day"), P("night stock")], "night stock"),
                ("night underground, no pickaxe: wait for day", [P("wait for day")], "wait for day"),
                ("the bag full while hungry: eat first", [M("empty the bag"), M("eat")], "eat"),
                ("a finished furnace job vs the queue: collect it (reflex)", [P("queue"), M("collect job")],
                 "collect job"),
                ("nothing to do but stock up: idle", [P("idle")], "idle"),
                ("died a minute ago: recover the items before anything else of its layer", [M("eat"),
                                                                                         M("recover items")],
                 "recover items"),
                ("an expired rescue is not run late: the plan", [self.intent("safety", "rescue", deadline_s=1.0, at=0.0),
                                                                P("queue")], "queue"),
                ("nothing proposed: nothing", [], None)]
        for name, intents, want in rows:
            with self.subTest(name):
                got = arbiter.arbitrate(intents, now=100.0)
                self.assertEqual(got.action if got else None, want)

    def test_the_layers(self):
        from bonobo import arbiter
        order = ["reflex", "safety", "tactic", "maintain", "plan"]
        self.assertEqual(sorted(arbiter.SCALES, key=arbiter.SCALES.get), order)

    def test_the_order_is_one_table(self):
        from bonobo import arbiter
        rows = [("the night's parts before a broken tool", "night prep", "broken tool"),
                ("a tool before food stock", "broken tool", "food stock"),
                ("upkeep's needs before the queue", "food stock", "queue"),
                ("the queue before the night's ore", "queue", "night stock"),
                ("the queue before idle stocking", "queue", "idle"),
                ("an unknown kind after all of them", "idle", "made up")]
        for name, first, then in rows:
            with self.subTest(name):
                self.assertLess(arbiter.plan_rank(first), arbiter.plan_rank(then))

    # (situation, night, dimension) → surface work closed
    CLOSED = [("day in the Overworld: open", False, "minecraft:overworld", False),
              ("night in the Overworld, in the open (the shelter row failed): closed", True, "minecraft:overworld",
               True),
              ("night in the Nether: no sun to wait for, open", True, "minecraft:the_nether", False),
              ("night by the clock in the End: open", True, "minecraft:the_end", False)]

    def test_surface_closed_over_the_table(self):
        for name, night, dim, want in self.CLOSED:
            with self.subTest(name):
                self.assertIs(brainmod.surface_closed(night, dim), want)

    def test_night_in_the_open_does_not_chop(self):
        """Night, exposed, empty bag, a tree in the queue: the round waits for day, it does not walk to the tree."""
        from unittest import mock
        b = brainmod.Brain.__new__(brainmod.Brain)
        b.retry, b.place = retry.Retry(), PLACE
        b.needs = mock.Mock(working={}, needs_now=[], round={}, propose=lambda snap, ctx, reads=None: [])
        b.reflexes = mock.Mock(proposals=lambda snap, ctx, reads=None: [])       # caught in the open, nothing due
        chop = brainmod.Act("task", "task t1", None, step=planner.Step("gather", "log", 2))
        b.task_act = lambda task, snap, ctx: chop
        b.prepare = lambda snap, ctx: brainmod.Act("idle", "prepare", None)
        snap = snapshot(state(timeOfDay=NIGHT), inventory())
        with mock.patch.object(api, "MODE", "normal"), mock.patch.object(brainmod.hazard, "due", return_value=None), \
                mock.patch.object(tasks, "load", return_value=[{"id": "t1", "state": "pending"}]), \
                mock.patch.object(tasks, "expire", return_value=False):
            act = b.decide(snap, None)
        self.assertEqual((act.layer, act.name), ("idle", "wait for day"))

    def test_night_stock_plans_under_cover(self):
        """The ore the night digs for plans, from a stone pickaxe in a dark hole, as work NIGHT_WORK allows first."""
        for bag_, want in (([("stone_pickaxe", 1)], ["mine"]),
                           ([("stone_pickaxe", 1), ("minecraft:raw_iron", 15)], ["mine"]),     # one short
                           ([("iron_pickaxe", 1), ("minecraft:raw_iron", 16)], ["mine"]),      # then diamonds
                           ([("diamond_pickaxe", 1)], ["mine"])):
            with self.subTest(bag=bag_), tempfile.TemporaryDirectory() as tmp:
                snap = snapshot(state(timeOfDay=NIGHT, skyLight=0, y=20.0), inventory(*bag_))
                needs = next(n for n in goals.NIGHT_STOCK if goals.short(snap.inv, [tuple(x) for x in n]))
                steps = decompose.decompose(snap.inv, goals.have(*needs),
                                            cost(snap, mem=Memory(os.path.join(tmp, "n.json"))))
                self.assertEqual([st.kind for st in steps], want)
                self.assertIn(steps[0].kind, brainmod.NIGHT_WORK)


# ------------------------------------------------------------------------------------------------ a night's way
class Overnight(unittest.TestCase):
    """The cheapest way through a night from this bag (needs.overnight → decompose.cheapest over "overnight")."""

    # (situation, bag, what is in sight) → the way chosen and its first step's kind
    ROWS = [("a pickaxe: dig in, nothing to fetch", [("stone_pickaxe", 1)], HERD, "dig in", ["shelter"]),
            ("no sheep, no pickaxe: a wooden pickaxe then dig in, not a bed", [], {"oak_log": 10, "stone": 2},
             "dig in", ["gather", "craft", "craft", "craft", "craft", "shelter"]),
            ("wool carried: the bed", [("white_wool", 3)], HERD, "bed", ["gather", "craft", "craft", "craft"]),
            ("cobblestone carried, nothing seen: walled in", [("cobblestone", 16)], {}, "wall in", ["shelter"]),
            ("a bed carried: sleep in it, nothing to make", [("white_bed", 1)], {}, "bed", [])]

    # At night, from the shelter row (no bed: the sleep row's): (situation, bag, ground digs by hand) → way, steps
    NIGHT = [("in the open on dirt, an empty bag: dig in by hand", [], True, "dig in by hand", ["shelter"]),
             ("on stone, dirt 8 blocks along the ground (2 s walk), an empty bag: walk there, dig in by hand", [], 2.0,
              "dig in by hand", ["shelter"]),
             ("on stone, cobblestone carried: walled in", [("cobblestone", 16)], False, "wall in", ["shelter"]),
             ("on stone, a pickaxe: dig in", [("stone_pickaxe", 1)], False, "dig in", ["shelter"]),
             ("on stone, an empty bag: a pickaxe first — its tree waits for day (brain.surface_closed)", [], False,
              "dig in", ["gather", "craft", "craft", "craft", "craft", "shelter"]),
             ("the ground unread (the dusk lead): no dig by hand assumed", [], None, "dig in",
              ["gather", "craft", "craft", "craft", "craft", "shelter"]),
             # The hut is priced with what it really needs (its blueprint's "stone" group): smooth stone is not it.
             ("smooth stone, a door, a torch, no pickaxe: no hut (the blueprint's stone is cobblestone)",
              [("stone", 32), ("oak_door", 1), ("torch", 2)], False, "dig in",
              ["gather", "craft", "craft", "craft", "craft", "shelter"]),
             ("cobblestone, a door, a torch: the hut is possible, walling in is cheaper",
              [("cobblestone", 14), ("oak_door", 1), ("torch", 2)], False, "wall in", ["shelter"])]

    def test_the_night_way_over_the_table(self):
        """The shelter row asks the same pricing as the dusk lead (one choice, `needs.overnight`)."""
        for name, carried, soft, way, kinds in self.NIGHT:
            with self.subTest(name):
                snap = snapshot(state(timeOfDay=NIGHT), inventory(*carried))
                facts = None if soft is None else needs.night_facts(soft)
                got, _secs, steps = needs.overnight(snap.inv, cost(snap), facts, bed_too=False)
                self.assertEqual((got, [st.kind for st in steps]), (way, kinds))

    def test_stone_ground_dirt_near_walls_in(self):
        """On stone, an empty bag, dirt 4 away: nine dirt dug by hand, then walled in (SOURCES["building"])."""
        snap = snapshot(state(timeOfDay=NIGHT), inventory())
        got, _secs, steps = needs.overnight(snap.inv, cost(snap, dirt=4), {"soft_ground": False}, bed_too=False)
        self.assertEqual((got, [(st.kind, st.token) for st in steps]),
                         ("wall in", [("mine", "minecraft:dirt"), ("shelter", "pod")]))

    def test_soft_below_over_the_table(self):
        from bonobo.terrain import soft_below
        from tests.world import FakeRegion
        rows = [("dirt three deep, stone under", {(0, 63, 0): "dirt", (0, 62, 0): "dirt", (0, 61, 0): "sand",
                                                 (0, 60, 0): "stone"}, True),
                ("dirt three deep over a cave: the dig would stop short",
                 {(0, 63, 0): "dirt", (0, 62, 0): "dirt", (0, 61, 0): "sand"}, False),
                ("stone at the third", {(0, 63, 0): "grass_block", (0, 62, 0): "dirt", (0, 61, 0): "stone"}, False),
                ("air under the feet (a ledge)", {(0, 62, 0): "dirt", (0, 61, 0): "dirt"}, False),
                ("water under the feet", {(0, 63, 0): "water", (0, 62, 0): "dirt", (0, 61, 0): "dirt"}, False)]
        for name, blocks, want in rows:
            with self.subTest(name):
                self.assertIs(soft_below(FakeRegion((0, 60, 0), (0, 64, 0), blocks), (0, 64, 0), 3), want)

    def test_way_over_the_table(self):
        for name, carried, seen, way, kinds in self.ROWS:
            with self.subTest(name):
                snap = snapshot(state(timeOfDay=DUSK), inventory(*carried))
                got, secs, steps = needs.overnight(snap.inv, cost(snap, **seen))
                self.assertEqual((got, [st.kind for st in steps]), (way, kinds))
                self.assertEqual(secs, sum(st.est for st in steps) / 20)


# ------------------------------------------------------------------------------------------------ a tool that broke
class WhatBroke(unittest.TestCase):
    """A tool is broken only when it was nearly worn out and is gone: a whole one that vanished was stored, dropped or
    cleared, and replacing it is a task nobody asked for ("the axe broke" of an axe never carried)."""

    # (situation, last round's bag, this round's bag) → the kinds that broke
    ROWS = [("nearly worn out, then gone: broke", [slot("stone_axe", 1, 125)], [], {"axe"}),
            ("whole, then gone (stored in a chest, /clear): not broken", [("stone_axe", 1)], [], set()),
            ("nearly worn out, still in hand: not yet", [slot("stone_axe", 1, 125)], [slot("stone_axe", 1, 127)],
             set()),
            ("worn out, and a better one carried now: replaced, not broken", [slot("stone_pickaxe", 1, 128)],
             [("iron_pickaxe", 1)], set()),
            ("two kinds, one nearly worn out: only that one", [slot("wooden_sword", 1, 57), ("stone_shovel", 1)], [],
             {"sword"}),
            ("never carried: nothing", [], [], set())]

    def test_broke_over_the_table(self):
        for name, before, after, want in self.ROWS:
            with self.subTest(name):
                was = needs.wear(bag(inventory(*before)))
                self.assertEqual(needs.broke(was, needs.working_tiers(bag(inventory(*after)))), want)


# -------------------------------------------------------------------------------------------- a bucket before a fall
def _plan(*steps):
    return [planner.Step(k, t, 1) for k, t in steps]


class WaterBucketBeforeAFall(unittest.TestCase):
    """The jar's WaterClutch lands a fall only with a water bucket to hand: a plan with a fall in it (needs.FALL_RISK,
    or ore dug down to) gets one first — outside the Nether, where water cannot be poured."""

    # (situation, dimension, bag, held plans) → a water bucket to the front?
    ROWS = [("a portal ahead, no bucket", "minecraft:overworld", (), [_plan(("gather", "log"), ("portal", "x"))], True),
            ("a portal ahead, a water bucket carried", "minecraft:overworld", (("water_bucket", 1),),
             [_plan(("portal", "minecraft:the_nether"))], False),
            ("an empty bucket is not a water bucket", "minecraft:overworld", (("bucket", 1),),
             [_plan(("seek", "stronghold"))], True),
            ("flat work only: logs, planks, a table", "minecraft:overworld", (),
             [_plan(("gather", "log"), ("craft", "planks"), ("craft", "minecraft:crafting_table"))], False),
            ("iron is dug down to (y 16)", "minecraft:overworld", (), [_plan(("mine", "minecraft:raw_iron"))], True),
            ("coal near the surface (y 48) is not", "minecraft:overworld", (), [_plan(("mine", "minecraft:coal"))],
             False),
            ("already in the Nether: water cannot be poured there", "minecraft:the_nether", (),
             [_plan(("seek", "fortress"), ("hunt", "minecraft:blaze_rod"))], False),
            ("nothing held", "minecraft:overworld", (), [], False)]

    def test_needs_water_bucket_over_the_table(self):
        for name, dim, items, plans, want in self.ROWS:
            with self.subTest(name):
                snap = snapshot(inv=inventory(*items))
                snap.state = dict(snap.state, dimension=dim)
                self.assertEqual(needs.needs_water_bucket(snap, plans), want)

    def test_no_iron_no_bucket_the_plan_goes_through_iron(self):
        """With nothing, the bucket's plan is the iron chain (mine, smelt, craft the bucket, fill it)."""
        snap = snapshot(inv=inventory())
        steps = decompose.decompose(snap.inv, goals.have(("minecraft:water_bucket", 1)), cost(snap))
        got = [(s.kind, s.token) for s in steps]
        # (step) → in the plan? — the iron route, in order; the must-nots: no diamond, no ready-made bucket found
        for step, want in ((("mine", "minecraft:raw_iron"), True), (("smelt", "minecraft:iron_ingot"), True),
                           (("craft", "minecraft:bucket"), True), (("fill", "minecraft:water_bucket"), True),
                           (("mine", "minecraft:diamond"), False)):
            with self.subTest(step=step):
                self.assertEqual(step in got, want)
        self.assertEqual([got.index(s) for s in (("mine", "minecraft:raw_iron"), ("smelt", "minecraft:iron_ingot"),
                                                 ("craft", "minecraft:bucket"), ("fill", "minecraft:water_bucket"))],
                         sorted(got.index(s) for s in (("mine", "minecraft:raw_iron"), ("smelt", "minecraft:iron_ingot"),
                                                       ("craft", "minecraft:bucket"), ("fill", "minecraft:water_bucket"))))



class ModFeatures(unittest.TestCase):
    """nav.mod_features: what the running jar can do, by its version; "approach_dig" (0.1.40) means a mine the
    walker cannot reach is dug to by the jar itself, so skills.mine does not tunnel for it a second time."""

    ROWS = [("0.1.14: nothing", "0.1.14+mc1.21.11", set()), ("0.1.15: pillar", "0.1.15", {"pillar"}),
            ("0.1.39: travel", "0.1.39+mc1.21.11", {"pillar", "travel"}),
            ("0.1.40: the approach digs", "0.1.40+mc1.21.11", {"pillar", "travel", "approach_dig"}),
            ("0.1.46: eats on the way", "0.1.46+mc1.21.11", {"pillar", "travel", "approach_dig", "autoeat"}),
            ("no version read: nothing assumed", "unknown", set())]

    def test_version_to_features(self):
        from bonobo import nav
        for name, version, want in self.ROWS:
            with self.subTest(name), mock.patch.object(nav, "_features", None), \
                    mock.patch.object(api, "status", return_value={"version": version}):
                self.assertEqual(nav.mod_features(), want)


class Felled(unittest.TestCase):
    """wood.felled: a sapling goes back only once the whole trunk is down (bench chop__base: a sapling under two
    logs still standing made them unreachable)."""

    TRUNK = [{"x": 3, "y": y, "z": 0} for y in (200, 201, 202)]
    BIG = [{"x": x, "y": 200, "z": z} for x in (3, 4) for z in (0, 1)]
    ROWS = [("every log of it gone", TRUNK, set(), True),
            ("the base gone, two logs overhead still standing", TRUNK, {(3, 201, 0), (3, 202, 0)}, False),
            ("a 2×2 trunk, one column left", BIG, {(4, 200, 1)}, False),
            ("only another tree's logs stand", TRUNK, {(9, 200, 9), (9, 201, 9)}, True)]

    def test_felled_over_the_table(self):
        from bonobo import wood
        for name, trunk, still, want in self.ROWS:
            with self.subTest(name):
                self.assertIs(wood.felled(trunk, still), want)


class AFightComesBeforeUpkeep(unittest.TestCase):
    """L0 > the fight's lease > upkeep > the queue: while a fight holds the body, upkeep is not even asked (a zombie
    in the arena and upkeep sent the body off for logs, over the edge)."""

    # (situation, the body's holder, api.MODE, a hazard due, upkeep has work) → the layer that takes the round
    ROWS = [("a fight holds the body, upkeep has work", "fight", "normal", None, True, "L0", []),
            ("a rescue runs (survival mode)", None, "survival", None, True, "L0", []),
            ("nobody holds it, a hazard is due", None, "normal", "drowning", True, "L0", []),
            ("nobody holds it, upkeep has work", None, "normal", None, True, "upkeep", ["upkeep"]),
            ("nobody holds it, nothing to do", None, "normal", None, False, None, ["upkeep"])]

    def test_order_over_the_table(self):
        from unittest import mock
        from bonobo import arbiter
        for name, holder, mode, due, busy, want, asked_want in self.ROWS:
            asked = []
            b = brainmod.Brain.__new__(brainmod.Brain)
            b.retry, b.place = retry.Retry(), PLACE

            def proposals(snap, ctx, reads=None, _busy=busy):
                asked.append("upkeep")
                return [(0, "u", None)] if _busy else []
            b.needs = mock.Mock(working={}, needs_now=[], round={}, propose=lambda snap, ctx, reads=None: [])
            b.reflexes = mock.Mock(proposals=proposals)
            b.task_act = lambda task, snap, ctx: None
            b.prepare = lambda snap, ctx: None
            snap = snapshot(state(), inventory())
            with self.subTest(name), mock.patch.object(api, "MODE", mode), \
                    mock.patch.object(arbiter.BODY, "holder", return_value=holder), \
                    mock.patch.object(brainmod.hazard, "due", return_value=due), \
                    mock.patch.object(tasks, "load", return_value=[]), mock.patch.object(tasks, "expire", return_value=False):
                act = b.decide(snap, None)
                self.assertEqual((act.layer if act else None, asked), (want, asked_want))


class StationGone(unittest.TestCase):
    """A station the plan counted on is gone: memory forgets it, the failure is a replan (no count, no cooling), and
    the repaired plan makes the station again from what is carried — or goes further up for it."""

    # (situation, bag, goal, station memory still holds) → the repaired plan
    ROWS = [("the table taken, 8 planks carried: make a table, then the pickaxe",
             [("oak_planks", 8), ("stick", 2)], ("minecraft:wooden_pickaxe", 1), None, {},
             [("craft", "minecraft:crafting_table"), ("craft", "minecraft:wooden_pickaxe")]),
            ("the table taken, no planks: up to a tree", [("stick", 2)], ("minecraft:wooden_pickaxe", 1), None,
             {"oak_log": 5}, [("gather", "log"), ("craft", "planks"), ("craft", "minecraft:crafting_table"),
                              ("craft", "minecraft:wooden_pickaxe")]),
            ("the furnace taken, cobblestone carried: a furnace, then smelt",
             [("raw_iron", 2), ("coal", 1), ("cobblestone", 8), ("crafting_table", 1)], ("minecraft:iron_ingot", 2),
             None, {}, [("craft", "minecraft:furnace"), ("smelt", "minecraft:iron_ingot")]),
            ("the table still there: nothing added", [("oak_planks", 8), ("stick", 2)],
             ("minecraft:wooden_pickaxe", 1), "minecraft:crafting_table", {}, [("craft", "minecraft:wooden_pickaxe")])]

    def test_repair_over_the_table(self):
        for name, carried, need, station, seen, want in self.ROWS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                if station:
                    m.add_station(station, (1, 64, 1), OVER)
                snap = snapshot(state(), inventory(*carried))
                steps = decompose.decompose(snap.inv, goals.have(need), cost(snap, mem=m, **seen))
                self.assertEqual([(st.kind, st.token) for st in steps], want)

    def test_the_station_is_forgotten_and_it_is_a_replan(self):
        from unittest import mock
        from bonobo import skillcore
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.add_station("minecraft:crafting_table", (1, 64, 1), OVER)
            ctx = skillcore.Context(m, None, OVER, {})
            with mock.patch.object(skills, "close_screen"), mock.patch.object(skills, "find", return_value=[]), \
                    mock.patch.object(skills, "Inventory", return_value=bag(inventory())), \
                    mock.patch.object(skills, "feet", return_value=(0, 64, 0)), \
                    self.assertRaises(skillcore.StationMissing) as got:
                with skills.Station(ctx, "minecraft:crafting_table"):
                    pass
            self.assertEqual((m.stations(OVER), retry.cause_of(got.exception)), ([], "replan"))


class RepairsThatDoNotHelp(unittest.TestCase):
    """retry: a replan (a station gone) is not a failure — once. The same task replanned again with nothing done in
    between is a failure that cools: plan_repair_on_event replanned the same plan every second, forever."""

    # (situation, [events: "replan" | "ok"]) → (the last verdict is a failure, the task cooling after)
    ROWS = [("one replan: not counted, not cooling", ["replan"], (False, False)),
            ("a second replan with nothing done between: cools", ["replan", "replan"], (True, True)),
            ("a replan, a success, a replan: each one the first", ["replan", "ok", "replan"], (False, False)),
            ("three in a row: still cooling", ["replan", "replan", "replan"], (True, True))]

    def test_over_the_table(self):
        from bonobo import skillcore
        for name, events, want in self.ROWS:
            with self.subTest(name):
                r, now, verdict = retry.Retry(), 1000.0, None
                for ev in events:
                    if ev == "ok":
                        r.succeeded("task t1")
                    else:
                        err = skillcore.StationMissing("minecraft:crafting_table")
                        verdict = r.failed("task t1", retry.cause_of(err), str(err), now, PLACE)
                self.assertEqual((verdict is not None, not r.ready("task t1", now + 1, PLACE)), want)


class LeadOnlyFromKnownPlans(unittest.TestCase):
    """needs.due_now: a plan that knows where it goes starts LEAD early; one priced by a guess (a seek, a source
    nowhere known) waits for the real threshold — a guess × LEAD made food urgent on a full stomach."""

    # (situation, seconds left, plan seconds, the plan's places known, the real threshold reached) → due now
    ROWS = [("full stomach, no known food: not yet", 900.0, 400.0, False, False, False),
            ("hungry below EAT_BELOW, no known food: now", 300.0, 400.0, False, True, True),
            ("cows in sight, the plan outruns the stomach × LEAD: now", 500.0, 400.0, True, False, True),
            ("cows in sight, plenty left: not yet", 900.0, 400.0, True, False, False),
            ("dusk far, the bed's sheep nowhere known: not yet", 600.0, 900.0, False, False, False)]

    def test_due_over_the_table(self):
        for name, left, plan, known, threshold, want in self.ROWS:
            with self.subTest(name):
                self.assertIs(needs.due_now(left, plan, known, threshold), want)

    def test_known_source(self):
        snap = snapshot(state(), inventory())
        steps = [(planner.Step("hunt", "minecraft:beef", 2, {"types": ["minecraft:cow"]}), {"cow": 20}, True),
                 (planner.Step("hunt", "minecraft:beef", 2, {"types": ["minecraft:cow"]}), {}, False),
                 (planner.Step("seek", "tree", 1, {}), {"oak_log": 5}, False),
                 (planner.Step("craft", "planks", 4, {}), {}, True)]
        for step, seen, want in steps:
            with self.subTest(step=step.kind, seen=seen):
                self.assertIs(cost(snap, **seen).known_source(step), want)


class ToolsAreThePlansNeed(unittest.TestCase):
    """No unconditional "no working pickaxe" row: a plan that mines carries its own pickaxe steps; a chop needs none."""

    def test_tool_kinds(self):
        rows = [("mining iron needs a pickaxe", [planner.Step("mine", "minecraft:raw_iron", 3, {"tier": 1})],
                 {"pickaxe"}),
                ("a log chop needs none", [planner.Step("gather", "log", 3, {})], set()),
                ("digging dirt by hand needs none", [planner.Step("mine", "minecraft:dirt", 9, {"tier": None})],
                 set()),
                ("nothing held", [], set())]
        for name, steps, want in rows:
            with self.subTest(name):
                self.assertEqual(needs.tool_kinds(steps), want)

    def test_a_stone_plan_brings_its_pickaxe(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = snapshot(state(), inventory(("oak_log", 3)))
            steps = decompose.decompose(snap.inv, goals.have(("minecraft:cobblestone", 3)),
                                        cost(snap, mem=Memory(os.path.join(tmp, "n.json")), stone=2))
            self.assertEqual([(st.kind, st.token) for st in steps][-2:],
                             [("craft", "minecraft:wooden_pickaxe"), ("mine", "minecraft:cobblestone")])


class FoodFromTheBag(unittest.TestCase):
    """Raw meat carried is food first: smelt it (seconds) before hunting (minutes)."""

    def test_cooked_from_carried(self):
        cooked = ["minecraft:cooked_porkchop", "minecraft:cooked_beef", "minecraft:cooked_mutton"]
        rows = [("two raw beef, want 8: the beef, the rest elsewhere", {"minecraft:beef": 2}, 8,
                 [("minecraft:cooked_beef", 2)]),
                ("nine raw beef, want 8: 8 of it", {"minecraft:beef": 9}, 8, [("minecraft:cooked_beef", 8)]),
                ("beef and mutton: the larger pile first", {"minecraft:beef": 2, "minecraft:mutton": 5}, 6,
                 [("minecraft:cooked_mutton", 5), ("minecraft:cooked_beef", 1)]),
                ("nothing raw: nothing from the bag", {}, 8, [])]
        for name, have, n, want in rows:
            with self.subTest(name):
                self.assertEqual(planner.cooked_from_carried(cooked, lambda t, h=have: h.get(t, 0), n), want)

    def test_the_plan(self):
        rows = [("2 raw beef, coal, a furnace near, want 2: smelt them", [("beef", 2), ("coal", 4)], 2, {},
                 [("smelt", "minecraft:cooked_beef", 2)]),
                ("2 raw beef, want 8, pigs 30 away: smelt the beef, hunt the rest",
                 [("beef", 2), ("coal", 4)], 8, {"pig": 30},
                 [("smelt", "minecraft:cooked_beef", 2), ("hunt", "minecraft:porkchop", 6),
                  ("smelt", "minecraft:cooked_porkchop", 6)]),
                ("nothing raw, pigs 30 away: hunt", [("coal", 4)], 2, {"pig": 30},
                 [("hunt", "minecraft:porkchop", 2), ("smelt", "minecraft:cooked_porkchop", 2)]),
                ("8 cooked carried: nothing", [("cooked_beef", 8)], 8, {}, [])]
        for name, carried, n, seen, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                m.add_station("minecraft:furnace", (1, 64, 1), OVER)
                snap = snapshot(state(food=6), inventory(*carried))
                steps = decompose.decompose(snap.inv, goals.have(("food", n)), cost(snap, mem=m, **seen))
                self.assertEqual([(st.kind, st.token, st.count) for st in steps], want)


class TheToolTheBagMakes(unittest.TestCase):
    """A plan that needs a pickaxe makes the best one the bag makes outright (Planner.craftable_tier, the one answer
    for every tool goal): a worn-out iron pickaxe and three ingots make an iron one, not a wooden one."""

    # (situation, what else is carried) → the plan for 2 coal (coal ore in sight; any pickaxe mines it)
    ROWS = [("worn iron pickaxe, 3 iron, sticks, a table: iron", [("iron_ingot", 3)],
             [("craft", "minecraft:iron_pickaxe"), ("mine", "coal")]),
            ("no iron, 3 cobblestone: stone", [("cobblestone", 3)],
             [("craft", "minecraft:stone_pickaxe"), ("mine", "coal")]),
            ("no iron, no stone, planks: wood", [("oak_planks", 3)],
             [("craft", "minecraft:wooden_pickaxe"), ("mine", "coal")]),
            ("a working stone pickaxe: none made", [slot("stone_pickaxe", 1, 0)], [("mine", "coal")])]

    def test_plan_over_the_table(self):
        for name, extra, want in self.ROWS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                snap = snapshot(state(), inventory(slot("iron_pickaxe", 1, 249), ("stick", 2), ("crafting_table", 1),
                                                   *extra))
                steps = decompose.decompose(snap.inv, goals.have(("coal", 2)),
                                            cost(snap, mem=Memory(os.path.join(tmp, "n.json")), coal_ore=2))
                self.assertEqual([(st.kind, st.token) for st in steps], want)

    def test_upkeep_and_the_planner_agree(self):
        for extra, tier in (([("iron_ingot", 3)], 2), ([("cobblestone", 3)], 1), ([("oak_planks", 3)], 0), ([], 0)):
            with self.subTest(extra=extra):
                inv = bag(inventory(("stick", 2), ("crafting_table", 1), *extra))
                self.assertEqual(needs.craftable_tier(inv, "pickaxe"), tier)


class EatOnTheWay(unittest.TestCase):
    """What the jar is told to eat while walking (nav.autoeat_policy, sent once at the session's first contact): the threshold sits where regen stops, above
    the standing row's, and the foods go best first."""

    def test_policy(self):
        p = nav.autoeat_policy()
        self.assertEqual((p["below"], p["foods"][:3]),
                         (18, ["minecraft:cooked_beef", "minecraft:cooked_porkchop", "minecraft:cooked_mutton"]))
        self.assertGreater(p["below"], reflexes.EAT_BELOW)
        self.assertNotIn("minecraft:beef", p["foods"])          # raw meat is the standing row's, when starving
        self.assertNotIn("minecraft:rotten_flesh", p["foods"])


class TrunkBatch(unittest.TestCase):
    """wood.trunk_batch: a trunk is one submission (no round trip per log)."""

    def test_batch_over_the_table(self):
        from bonobo import wood
        def many(*cells):
            return {"type": "mine_many", "collect": False, "requireDrops": False,
                    "blocks": [{"x": c[0], "y": c[1], "z": c[2]} for c in cells]}
        pick = {"type": "collect", "radius": 4, "only": ["log"]}
        base = (3, 64, 0)
        up = [(3, 65, 0), (3, 66, 0), (3, 67, 0)]
        rows = [("four wanted, three overhead: one batch of all of it, no walk in", up, 4, [many(base, *up), pick]),
                ("two wanted: the base and the log over it", up, 2, [many(base, up[0]), pick]),
                ("one wanted: the base only", up, 1, [many(base), pick]),
                ("a stump (nothing overhead)", [], 5, [many(base), pick]),
                ("a log past reach (5 up and more): not in the batch", up + [(3, 69, 0), (3, 70, 0)], 9,
                 [many(base, *up), pick])]
        for name, overhead, want, batch in rows:
            with self.subTest(name):
                self.assertEqual(wood.trunk_batch((3, 64, 0), overhead, want), batch)


class CraftInOneSitting(unittest.TestCase):
    """brain.craft_run: the crafts made in one sitting — the step and the crafts straight after it."""

    def test_run_over_the_table(self):
        S = planner.Step
        planks, sticks, table, pick = (S("craft", "planks", 8, {"times": 2}), S("craft", "minecraft:stick", 4, {}),
                                       S("craft", "minecraft:crafting_table", 1, {}),
                                       S("craft", "minecraft:wooden_pickaxe", 1, {}))
        log, mine = S("gather", "log", 3, {}), S("mine", "stone", 3, {"tier": 0})
        rows = [("a whole craft chain", [planks, sticks, table, pick], planks, [planks, sticks, table, pick]),
                ("a mine step ends the run", [planks, sticks, mine, pick], planks, [planks, sticks]),
                ("from the middle of the plan", [log, planks, sticks], planks, [planks, sticks]),
                ("not a craft: itself alone", [log, planks], log, [log]),
                ("a lone craft", [mine, pick], pick, [pick])]
        for name, steps, first, want in rows:
            with self.subTest(name):
                self.assertEqual(brainmod.craft_run(steps, first), want)


class Reflexes(unittest.TestCase):
    """reflexes.TABLE: each trigger over the round's view — fires, and does not."""

    BASE = {"died_recently": False, "food": 20, "meal": False, "swimming": False, "nether_bad": False,
            "night": False, "enclosed": False, "overworld": True, "bed_works": False, "bed_carried": False,
            "bed_near": False, "shelter_ready": False, "job_ready": False, "machine_ready": False, "used_slots": 10,
            "blocked": False, "building": 0, "stuck": False}
    # (reflex, the view's changes that fire it, the changes that do not)
    ROWS = [("recover items", {"died_recently": True}, {}),
            ("eat", {"food": 10}, {"food": 10, "meal": None}),
            ("reach land", {"swimming": True}, {}),
            ("leave the Nether", {"nether_bad": True}, {}),
            ("dig out", {"enclosed": True}, {"enclosed": True, "night": True}),
            ("sleep", {"night": True, "bed_works": True, "bed_carried": True},
             {"night": True, "bed_works": True, "overworld": False, "bed_carried": True}),
            ("shelter", {"shelter_ready": True}, {}),
            ("collect job", {"job_ready": True}, {}),
            ("collect machine", {"machine_ready": True}, {}),
            ("empty the bag", {"used_slots": 34}, {"used_slots": 33}),
            ("path blocked", {"blocked": True, "building": 8}, {"blocked": True, "building": 7}),
            ("unstuck", {"stuck": True}, {})]

    def test_triggers(self):
        from bonobo import reflexes
        self.assertEqual([r[0] for r in self.ROWS], list(reflexes.NAMES))
        for name, fires, quiet in self.ROWS:
            with self.subTest(name):
                self.assertIn(name, [n for _i, n in reflexes.due(dict(self.BASE, **fires))])
                self.assertNotIn(name, [n for _i, n in reflexes.due(dict(self.BASE, **quiet))])

    # (which reflexes are cooling) → what is due, when both eat and reach land fire
    COOLING = [("none cooling: both, in table order", set(), [(1, "eat"), (2, "reach land")]),
               ("eat cooling: skipped, reach land still due", {"eat"}, [(2, "reach land")]),
               ("reach land cooling: eat alone", {"reach land"}, [(1, "eat")]),
               ("both cooling: nothing due though both fire", {"eat", "reach land"}, [])]

    def test_cooling_reflexes_are_skipped(self):
        from bonobo import reflexes
        view = dict(self.BASE, food=10, swimming=True)
        for name, cooling, want in self.COOLING:
            with self.subTest(name):
                self.assertEqual(reflexes.due(view, ready=lambda n, c=cooling: n not in c), want)

    def test_a_view_reads_once(self):
        from bonobo import reflexes
        calls = []
        v = reflexes.View({"stuck": lambda: calls.append(1) or True})
        self.assertEqual((v["stuck"], v["stuck"], len(calls)), (True, True, 1))


class NeedsAndReflexesAreIndependent(unittest.TestCase):
    """Needs and reflexes each read the same snapshot: asked in either order, the same reflexes fire and the same
    needs are proposed (no step leaves state another reads)."""

    ROWS = [("night outside, a pickaxe: shelter fires", dict(time_of_day=NIGHT, inv=[("cooked_beef", 8),
                                                                                   ("stone_pickaxe", 1)])),
            ("night outside, bed makings: a bed needed, no shelter", dict(
                time_of_day=NIGHT, inv=[("cooked_beef", 8), ("white_wool", 3), ("oak_planks", 3),
                                        ("crafting_table", 1)])),
            ("dusk, wool and no planks: the bed's parts needed", dict(time_of_day=DUSK,
                                                                     inv=[("cooked_beef", 8), ("white_wool", 3)])),
            ("hungry with bread by day: eat, nothing needed", dict(food=10, inv=[("bread", 4), ("white_bed", 1)]))]

    def run_order(self, row, needs_first):
        with tempfile.TemporaryDirectory() as tmp:
            b = brainmod.Brain.__new__(brainmod.Brain)
            b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
            b.mem, b.retry, b.blacklist, b.place, b.held = Memory(os.path.join(tmp, "n.json")), retry.Retry(), {}, \
                PLACE, {}
            b.needs, b.reflexes = needs.Needs(b), reflexes.Maintain(b)
            snap = snapshot(row.state, row.inv)
            c = cost(snap, **row.seen)
            b.needs.cost = lambda _snap: c
            reads = {"enclosed": False, "bed_near": False, "soft_ground": False}
            with mock.patch.object(api, "api", side_effect=AssertionError("read the world beyond the row")):
                if needs_first:
                    b.needs.propose(snap, None, reads)
                    fired = [n for _s, n, _r in b.reflexes.proposals(snap, None, reads)]
                else:
                    fired = [n for _s, n, _r in b.reflexes.proposals(snap, None, reads)]
                    b.needs.propose(snap, None, reads)
            return fired, [(k, json.dumps(g, sort_keys=True)) for k, g, _w in b.needs.needs_now]

    def test_order_swapped_same_result(self):
        for name, kw in self.ROWS:
            row = Row(name, None, **kw)
            with self.subTest(name):
                self.assertEqual(self.run_order(row, True), self.run_order(row, False))

if __name__ == "__main__":
    unittest.main()


class TheNightIsPricedOncePerBag(unittest.TestCase):
    """needs.Needs.overnight: kept for PLAN_S_TTL per bag — priced every round it was the round's hotspot."""

    def test_over_the_table(self):
        rows = [("the same bag twice: priced once", [WELL_FED, WELL_FED], 1),
                ("a changed bag: priced again", [WELL_FED, WELL_FED + [("cobblestone", 4)]], 2),
                ("three rounds, one bag: once", [WELL_FED] * 3, 1),
                ("back and forth: each new bag once", [WELL_FED, [("stick", 1)], WELL_FED], 2)]
        for name, bags, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.mem = Memory(os.path.join(tmp, "notes.json"))
                table, asked = needs.Needs(b), []
                snap0 = snapshot(state(), inventory(*bags[0]))
                c = cost(snap0)
                table.cost = lambda snap: asked.append(1) or c
                for items in bags:
                    table.overnight(snapshot(state(), inventory(*items)))
                self.assertEqual(len(asked), want)


class RawOnlyWhenStarving(unittest.TestCase):
    """reflexes.meal / can_cook: raw meat is eaten only when starving or when it cannot be cooked — else the plan
    cooks it (night_first__low ate both raw beef at 8 and had nothing left to cook)."""

    def test_over_the_table(self):
        rows = [("food 8, raw beef, a furnace near and coal: not eaten raw (cooked by the plan)", 8,
                 [("beef", 2), ("coal", 2)], True, None),
                ("food 8, raw beef, cobblestone for a furnace and planks: not eaten raw", 8,
                 [("beef", 2), ("cobblestone", 8), ("oak_planks", 4)], False, None),
                ("food 5, raw beef, a furnace near: starving — eaten raw", 5, [("beef", 2), ("coal", 2)], True, True),
                ("food 8, raw beef, no fuel: cannot cook — eaten raw", 8, [("beef", 2)], True, True),
                ("food 8, raw beef, fuel but no furnace nor stone: eaten raw", 8, [("beef", 2), ("coal", 2)], False,
                 True),
                ("food 8, cooked beef: a meal", 8, [("cooked_beef", 2), ("beef", 2)], True, False),
                ("nothing edible: nothing", 8, [("coal", 2)], True, None)]
        for name, food, items, furnace_near, want in rows:
            with self.subTest(name):
                inv = bag(inventory(*items))
                self.assertIs(reflexes.meal(food, inv, lambda: reflexes.can_cook(inv, furnace_near)), want)


class EstimatesRememberFirst(unittest.TestCase):
    """cost.Cost.distance: an estimate never searches the world — the round's one look (`finds`, pre-read) and
    memory answer; with the api raising on any request, the same answers."""

    def test_over_the_table(self):
        from bonobo.cost import Cost
        rows = [("a noted diamond 10 off, nothing in sight: remembered", {"diamond_ore": 10.0}, {}, ["diamond_ore"],
                 48, 10.0),
                ("nothing noted, one in sight 7 off: in sight", {}, {"diamond_ore": 7.0}, ["diamond_ore"], 48, 7.0),
                ("in sight only past the radius: memory answers (it has no radius)", {"diamond_ore": 40.0},
                 {"diamond_ore": 40.0}, ["diamond_ore"], 32, 40.0),
                ("a log of any wood in sight: the nearest", {}, {"oak_log": 9.0, "birch_log": 5.0},
                 ["oak_log", "birch_log"], 48, 5.0),
                ("nothing anywhere: None", {}, {}, ["diamond_ore"], 48, None)]
        for name, noted, sight, blocks, radius, want in rows:
            with self.subTest(name), mock.patch.object(api, "api", side_effect=AssertionError("an estimate read the world")):
                c = Cost(snapshot(state(), inventory()), finds=sight,
                         known=lambda kinds, _n=noted: min((_n[k] for k in kinds if k in _n), default=None))
                self.assertEqual(c.distance(blocks, radius), want)

class BridgeStockSizedToTheGap(unittest.TestCase):
    """needs.bridge_stock: one block per block across, between BRIDGE_MIN and BRIDGE_STOCK."""

    def test_over_the_table(self):
        rows = [("a 9-block way across: 9", (0, 64, 0), (9, 64, 0), 9),
                ("a step across: at least BRIDGE_MIN", (0, 64, 0), (2, 64, 0), reflexes.BRIDGE_MIN),
                ("100 blocks away: at most BRIDGE_STOCK", (0, 64, 0), (100, 64, 0), needs.BRIDGE_STOCK),
                ("diagonal 12 across, height ignored", (0, 64, 0), (9, 90, 8), 13)]
        for name, feet, target, want in rows:
            with self.subTest(name):
                self.assertEqual(needs.bridge_stock(feet, target), want)


class LookingAroundAsksOnlyWhatIsUnknown(unittest.TestCase):
    """explore.unknown: the look around asks /find only for kinds memory holds no note of."""

    def test_over_the_table(self):
        from bonobo import explore
        rows = [("nothing noted: every kind asked", [], ["diamond_ore", "obsidian"], ["diamond_ore", "obsidian"]),
                ("a diamond noted: not asked again", [("diamond_ore", (4, 60, 0))], ["diamond_ore", "obsidian"],
                 ["obsidian"]),
                ("every kind noted: nothing asked", [("diamond_ore", (4, 60, 0)), ("obsidian", (5, 60, 0))],
                 ["diamond_ore", "obsidian"], []),
                ("a note in another dimension: still asked here", [("diamond_ore", (4, 60, 0), "minecraft:the_nether")],
                 ["diamond_ore"], ["diamond_ore"])]
        for name, notes, names, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                for n in notes:
                    m.note_seen(n[0], n[1], n[2] if len(n) > 2 else OVER)
                self.assertEqual(explore.unknown(m, OVER, names), want)


class ANoteIsCheckedByReadingItsCell(unittest.TestCase):
    """dispatch.still_there: the noted cell itself is read, never searched for."""

    def test_over_the_table(self):
        from bonobo import dispatch
        from tests.world import FakeRegion
        rows = [("the diamond still there", {(4, 60, 0): "diamond_ore"}, ["diamond_ore", "deepslate_diamond_ore"], True),
                ("mined: air", {}, ["diamond_ore"], False),
                ("a variant of the kind: there", {(4, 60, 0): "deepslate_diamond_ore"},
                 ["diamond_ore", "deepslate_diamond_ore"], True),
                ("something else in the cell", {(4, 60, 0): "stone"}, ["diamond_ore"], False)]
        for name, blocks, kinds, want in rows:
            with self.subTest(name), \
                    mock.patch("bonobo.world.Region", lambda lo, hi, props=False: FakeRegion(lo, hi, blocks)), \
                    mock.patch.object(api, "api", side_effect=AssertionError("a /find was asked")):
                self.assertIs(dispatch.still_there(kinds, (4, 60, 0)), want)


class AnOreNotedIsEveryFormOfIt(unittest.TestCase):
    """explore.unknown: a noted diamond_ore covers deepslate_diamond_ore — the look around asked /find for the
    deepslate form (seen_store__noted's one diamond scan, pinned by a run: brain.py:258 → explore.py:188)."""

    def test_over_the_table(self):
        from bonobo import explore
        rows = [("diamond_ore noted: neither form asked", [("diamond_ore", (4, 60, 0))],
                 ["diamond_ore", "deepslate_diamond_ore", "obsidian"], ["obsidian"]),
                ("the deepslate form noted: neither asked", [("deepslate_diamond_ore", (4, 0, 0))],
                 ["diamond_ore", "deepslate_diamond_ore"], []),
                ("nothing noted: both asked", [], ["diamond_ore", "deepslate_diamond_ore"],
                 ["diamond_ore", "deepslate_diamond_ore"]),
                ("a block with no variants: itself only", [("obsidian", (1, 60, 0))], ["obsidian", "ancient_debris"],
                 ["ancient_debris"])]
        for name, notes, names, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                for kind, pos in notes:
                    m.note_seen(kind, pos, OVER)
                self.assertEqual(explore.unknown(m, OVER, names), want)


class ToolsThatPayForThemselves(unittest.TestCase):
    """planner.speed_up: an optional tool (the skill's `speed`) is made when making it costs less than it saves,
    and never from the work it would speed up."""

    def test_over_the_table(self):
        from bonobo.planner import NullCost, Planner
        kit = {"minecraft:oak_planks": 8, "minecraft:stick": 4, "minecraft:crafting_table": 1}
        rows = [("12 logs, planks and sticks carried: an axe first", kit, [], [("log", 12)], "minecraft:wooden_axe"),
                ("2 logs: saves less than the axe costs, none", kit, [], [("log", 2)], None),
                ("12 logs, an empty bag: the axe would need the logs, none", {}, [], [("log", 12)], None),
                ("12 logs, an axe held: none made", kit, [("axe", 0, 50)], [("log", 12)], None),
                ("8 beef (4 kills), planks and sticks: a sword first", kit, [], [("minecraft:beef", 8)],
                 "minecraft:wooden_sword")]
        for name, counts, tools, needs_, want in rows:
            with self.subTest(name):
                steps = Planner(dict(counts), list(tools), NullCost()).plan(needs_)
                made = [s.token for s in steps if s.kind == "craft" and s.token.endswith(("_axe", "_sword"))]
                self.assertEqual(made[0] if made else None, want)
                if want:
                    work = next(i for i, s in enumerate(steps) if s.kind in ("gather", "hunt"))
                    self.assertLess(steps.index(next(s for s in steps if s.token == want)), work)


class FoodOnItsWay(unittest.TestCase):
    """needs.food_on_its_way / food_lasts_s: ready food a background job is making counts — meals and points."""

    def test_over_the_table(self):
        rows = [("two cooked beef in the furnace: 2 meals, 16 points", {"minecraft:cooked_beef": 2}, (2, 16)),
                ("raw iron smelting: no food", {"minecraft:iron_ingot": 3}, (0, 0)),
                ("bread and beef: both", {"minecraft:bread": 1, "minecraft:cooked_beef": 1}, (2, 13)),
                ("nothing on its way", {}, (0, 0))]
        for name, pending, want in rows:
            with self.subTest(name):
                self.assertEqual(needs.food_on_its_way(pending), want)
        snap = snapshot(state(food=10), inventory())
        self.assertGreater(needs.food_lasts_s(snap, {"minecraft:cooked_beef": 2}), needs.food_lasts_s(snap))

    def test_no_food_stock_while_it_cooks(self):
        """Food 10, nothing ready carried, 2 cooked beef on their way: no food stock need (it was a pig hunt)."""
        for pending, want in ((True, False), (False, True)):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as tmp:
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.mem = Memory(os.path.join(tmp, "notes.json"))
                b.retry, b.blacklist, b.place, b.held = retry.Retry(), {}, PLACE, {}
                b.needs, b.reflexes = needs.Needs(b), reflexes.Maintain(b)
                if pending:
                    b.mem.add_job("smelt", (3, 64, 0), OVER, "minecraft:cooked_beef", 2, time.time() + 20, [])
                snap = snapshot(state(food=10), inventory(("oak_planks", 4)))
                c = cost(snap, cow=30)
                b.needs.cost = lambda _s: c
                b.needs.propose(snap, None, reads={"enclosed": False, "bed_near": False, "soft_ground": False})
                self.assertEqual(any(k == "food stock" for k, _g, _w in b.needs.needs_now), want)


class WhatTheFurnaceHolds(unittest.TestCase):
    """skills.after_take: memory's furnace job after a take — over when nothing cooks, else what it still holds."""

    def test_over_the_table(self):
        job = {"id": "furnace-1", "item": "minecraft:cooked_beef", "count": 4, "input": "minecraft:beef",
               "input_count": 4}
        rows = [("all taken, nothing left cooking: over", 4, 0, None, None),
                ("2 taken, 2 still in the input: holds 2, ready in 25 s", 2, 2, 1000, {"count": 2, "input_count": 2,
                                                                                    "ready_at": 25.0,
                                                                                    "ready_tick": 1000 + 420}),
                ("nothing out yet, all 4 cooking", 0, 4, None, {"count": 4, "input_count": 4, "ready_at": 45.0}),
                ("more taken than listed: never below 0", 6, 1, None, {"count": 0, "input_count": 1, "ready_at": 15.0})]
        for name, got, cooking, tick, want in rows:
            with self.subTest(name):
                self.assertEqual(skills.after_take(job, got, cooking, 0.0, tick), want)
