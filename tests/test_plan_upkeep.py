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
(`survive.enclosed`, a bed seen by /find); any other request fails the test.
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
from bonobo import api, arbiter, craft, decompose, goals, lifecycle, nav, needs, planner, reflexes, retry, skillcore, survive, tasks  # noqa: E402
from bonobo.data import DAY_END, TOOL_USES  # noqa: E402
from bonobo.knowledge import TOOL_WORKING  # noqa: E402
from bonobo import brain as brainmod  # noqa: E402  (imports every skill module: `handles` needs the registry)
from bonobo import skill as skillkit  # noqa: E402
from bonobo.data import bare  # noqa: E402
from bonobo.knowledge import under_rock  # noqa: E402
from bonobo.knowledge import members  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import Unplannable  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402
from tests.world import (PLANNER_DIMS, bag, brain_fixture, cost, full_bag, handles, inventory, memory, round_ctx,  # noqa: E402
                         slot, snapshot, state, worlds)

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
     [("gather", "log"), ("craft", "minecraft:wooden_pickaxe"), ("mine", "minecraft:cobblestone"), ("craft", "minecraft:stone_pickaxe")],
     []),
    (PICK1, {"stock": "wood_tools"}, [("mine", "minecraft:cobblestone"), ("craft", "minecraft:stone_pickaxe")],
     [("gather", "log"), ("craft", "minecraft:crafting_table"), ("craft", "minecraft:wooden_pickaxe")]),
    (PICK1, {"stock": "worn_pickaxe"}, [("craft", "minecraft:stone_pickaxe")], []),    # 1 durability left
    (PICK1, {"stock": ["stone_tools", "kit"]}, [], ANY),                                # goal met: nothing (must fail: goal met, no step at all)
    (IRON3, {"stock": "stone_tools"}, [("mine", "minecraft:raw_iron"), ("smelt", "minecraft:iron_ingot")],
     [("craft", "minecraft:stone_pickaxe")]),            # the fuel (coal or planks from logs) by price
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
    (goals.make("build", bp="shelter"), {"stock": "none"}, [("mine", "minecraft:cobblestone"), ("build", "shelter")], []),
    (goals.make("goto", pos=[100, 64, 0]), {}, [("goto", "pos")], []),
    (goals.make("road", a=[0, 64, 0], b=[200, 64, 0]), {}, [("goto", "pos")], []),
    (goals.make("sleep"), {}, [("sleep", "bed")], []),
    (goals.make("skill", name="chop", args=[4]), {}, [("skill", "chop")], []),
]
UNPLANNABLE = [
    ("a skill nobody registered", goals.make("skill", name="fly_to_the_moon")),
    ("a template nobody knows", {"goal": "dance", "args": {}}),
    ("an item with no source", goals.have(("minecraft:command_block", 1))),
    ("must fail: bedrock", goals.have(("minecraft:bedrock", 1))),
    ("one plannable need and one not", goals.have(("log", 2), ("minecraft:spawner", 1))),
]
# (situation, goal, skills taken out of the registry) → Unplannable from decompose
UNPLANNABLE_BY = [
    ("must fail: a step no registered skill provides", goals.have(("log", 4)), ("chop",)),
    ("smelting with no smelting skill", goals.have(("minecraft:iron_ingot", 3)), ("smelt", "load_smelter", "start_smelt_job")),
    ("meat with no hunter", goals.have(("minecraft:beef", 2)), ("hunt",)),
]
# (situation, goal) → the brain's replan: the step it plans, or None: unplannable, and says why
REPLAN = [
    ("a milestone: the one planner", goals.make("milestone", name="stone tools"), ("craft", "minecraft:stone_pickaxe")),
    ("logs", goals.have(("log", 4)), ("gather", "log")),
    ("sticks", goals.have(("minecraft:stick", 4)), ("craft", "minecraft:stick")),
    ("must fail: bedrock: unplannable, and says why", goals.have(("minecraft:bedrock", 1)), None),
]


def NOTHING_LEFT(state, call):
    """A test skill's `remaining`: what is left of it is nothing (the dummies here produce no world state)."""
    return {}


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
                        self.assertTrue(handles(s), f"{s}: no skill carries it out")
                        self.assertGreaterEqual(s.est, 0)

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
                                if not made & {bare(m) for m in members(tok)} | {bare(tok)}
                                and snap.inv.count(tok) == 0]
                    made.add(bare(s.token))
                self.assertEqual(orphans, [], "inputs neither held nor made before the step that consumes them")

    def test_unplannable(self):
        for name, goal in UNPLANNABLE:
            with self.subTest(name), self.assertRaises((Unplannable, ValueError)):
                plan(goal, snapshot())
        for name, goal, removed in UNPLANNABLE_BY:
            with self.subTest(name), mock.patch.dict(skillkit.REGISTRY), self.assertRaises(Unplannable):
                for n in removed:
                    skillkit.REGISTRY.pop(n)
                decompose.decompose(snapshot().inv, goal, cost(snapshot(), oak_log=5))

    def test_replan(self):
        for name, goal, step in REPLAN:
            with self.subTest(name):
                held, why = brainmod.replan([("task t1", goal, 0)], snapshot(), cost(snapshot(), oak_log=5, stone=2))
                if step is None:
                    self.assertEqual((held, why.startswith("unplannable")), (None, True))
                    continue
                self.assertIsNone(why)
                self.assertIn(pair(*step), pairs(held["steps"]), list(map(str, held["steps"])))

    def test_the_sweep_is_the_whole_product(self):
        n = sum(1 for _ in worlds())
        from tests.world import DIMS
        expected = 1
        for d in PLANNER_DIMS:
            expected *= len(DIMS[d])
        self.assertEqual(n, expected)


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
    ("must fail: a table across the valley is not in reach", PICK1, inventory(("oak_log", 4)),
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


# ------------------------------------------------------------------------------------------ where a thing lives
NETHER_T = "minecraft:the_nether"
PLACE_STEPS = ("cast", "build", "portal", "seek", "hunt")
# (situation, dimension we are in, fortress remembered?, a portal remembered?) → the place steps before the rods
LIVES = [
    ("rods from the Overworld, nothing known: a portal built, through it, find the fortress, collect", OVER, False,
     False, [("build", "nether_portal"), ("portal", NETHER_T), ("seek", "fortress"), ("hunt", "minecraft:blaze_rod")]),
    ("rods in the Nether, no fortress known: find it, collect", NETHER_T, False, False,
     [("seek", "fortress"), ("hunt", "minecraft:blaze_rod")]),
    ("must fail: rods in the Nether, a fortress remembered: collect, nothing put first", NETHER_T, True, False,
     [("hunt", "minecraft:blaze_rod")]),
    ("rods from the Overworld, a portal and a fortress remembered: through it, collect", OVER, True, True,
     [("portal", NETHER_T), ("hunt", "minecraft:blaze_rod")]),
]


class WhereItLives(unittest.TestCase):
    def test_the_way_there_first(self):
        for name, dim, fortress, portal, want in LIVES:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                if fortress:
                    m.add_site("fortress", (200, 70, 40), NETHER_T, name="fortress")
                if portal:
                    m.add_site("portal", (5, 64, 0), dim, name="portal")
                snap = snapshot(state(dimension=dim), inventory(("iron_sword", 1)))
                got = decompose.decompose(snap.inv, goals.have(("minecraft:blaze_rod", 7)), cost(snap, mem=m))
                self.assertEqual([(st.kind, st.token) for st in got if st.kind in PLACE_STEPS], want)

    # (situation, rods held, skills removed) → the plan for "have 7 blaze rods" from the Overworld, or why not
    GOALS = [("none held: portal, fortress, collect", 0, (), [("portal", NETHER_T), ("seek", "fortress"),
                                                              ("hunt", "minecraft:blaze_rod")]),
             ("must fail: seven held: nothing to do", 7, (), []),
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
        same skill (or another provider) carries out — after the steps that get its needs held (the pickaxe of a
        mine, the bucket of a fill), each one some skill's."""
        effects = sorted({e for c in skillkit.REGISTRY.values() for e in c.provides})
        self.assertTrue(effects)
        detail = {"goto": {"pos": [5, 64, 0]}, "withdraw": {"pos": [3, 64, 0]},       # effects that name a place
                  "look": {"pos": [3, 64, 0]},
                  "explore:blocks": {"blocks": ["oak_log"]}, "explore:mobs": {"types": ["minecraft:cow"]},
                  "hunt": {"types": ["minecraft:cow"]}, "mine": {"blocks": ["stone"], "tier": 0},
                  "seek": {"kinds": ["stone"]}, "take": {"blocks": ["red_bed"]}}
        for effect in effects:
            goal = goals.make("effect", effect=effect, count=2, **({"detail": detail[effect]} if effect in detail else {}))
            with self.subTest(effect):
                lava = snapshot(inv=inventory(("lava_bucket", 1)))      # what a cast in place pours (its `when`)
                steps = decompose.decompose(lava.inv, goal, cost(lava))
                self.assertEqual(steps[-1].count, 2)
                self.assertIn(effect, skillkit.step_keys(steps[-1]))
                self.assertEqual([s for s in steps[:-1] if effect in skillkit.step_keys(s) and s.count == 2], [])
                self.assertTrue(all(handles(s) for s in steps))
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
          ("must fail: a precondition that refuses", (_needs_torches,), inventory(), False),
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
                skillkit.skill(needs={}, gives={}, remaining=NOTHING_LEFT, name="zz_skill", pre=pre, provides={"craft": lambda ctx, s: (s.token,)})(
                    lambda ctx, *a: None)
                step = planner.Step("craft", "minecraft:stick", 4, {"inputs": {"planks": 2}})
                bag_ = inventory(("oak_planks", 2)) if inv is not None else inventory()
                b = brain_fixture()
                b.unplannable = {}
                b.abandoned = None
                b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
                b.retry, b.place = retry.Retry(), ("here", False)
                ctx = type("Ctx", (), {"policy": None, "mem": None})()
                self.assertEqual(b.valid(step, snapshot(inv=bag_), ctx), want)
                self.assertEqual(dispatch.can_start(ctx, step, bag_), all(_passes(p) for p in pre))

    def test_a_failed_step_cools_for_every_goal(self):
        """A plan step that failed cools under its own key (brain.step_key), so the next goal that plans the same
        step is not offered it; an interruption cools nothing."""
        step = planner.Step("gather", "log", 3, {})
        # (situation, what ends the attempt, the goal that tries after) → the step is still valid
        rows = [("must fail: failed under the pickaxe goal: not offered for the sword", NotAvailable("no trees found nearby"),
                 "idle: have sword tier 1", False),
                ("the same goal again: not offered", NotAvailable("no trees found nearby"),
                 "idle: have pickaxe tier 1", False),
                ("interrupted: offered again (no cooling)", api.Interrupted("a faster layer took the body"),
                 "idle: have sword tier 1", True),
                ("it succeeded: offered", None, "idle: have sword tier 1", True)]
        for name, err, _goal, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                b = brain_fixture()
                b.unplannable = {}
                b.abandoned = None
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
        rows = [("must fail: nobody provides this kind", {}, planner.Step("zz", "nothing", 1), False),
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
                    skillkit.skill(needs={}, gives={}, remaining=NOTHING_LEFT, name=sname, pre=pre, provides={effect: lambda ctx, s, _a=args: _a})(
                        lambda ctx, *a: None)
                self.assertIs(dispatch.can_start(None, step, inventory()), want)


# -------------------------------------------------------------------------------------------------- the cost model
from bonobo import cost as costmod  # noqa: E402
from bonobo.planner import Step  # noqa: E402

PT, WT = costmod.PRIOR_TICKS, costmod.walk_ticks


def PW(step):
    """A step's prior work by the empty bag's hand: the table's, never below the game's own time for it."""
    from bonobo.knowledge import prior_work_ticks
    return prior_work_ticks(step, {}, costmod.TICKS_PER_S)


def FIND(kind, st=None):
    """A search's ticks for `kind` from the test body (knowledge.expected_find_s over its feet and tools)."""
    from bonobo.knowledge import expected_find_s, held_tiers
    s = snapshot(st)
    return round(expected_find_s(kind, {"y": s.feet[1], "held": held_tiers(s.inv)}) * costmod.TICKS_PER_S)


IRON4 = Step("mine", "minecraft:raw_iron", 4, {"blocks": ["iron_ore"], "breaks": 4})
UNDER = state(skyLight=0, y=20.0)
# (situation, step, /state, what /find saw, ticks expected: prior work + the walk to it)
ESTIMATES = [
    ("craft: work only", Step("craft", "minecraft:stick", 4), None, {}, PT["craft"]),
    ("smelt 3: each + setup", Step("smelt", "minecraft:iron_ingot", 3), None, {}, 3 * PT["smelt_each"] + PT["smelt_setup"]),
    ("mine 4 breaks, ore 10 away", IRON4, None, {"iron_ore": 10}, PW(IRON4) + WT(10)),
    ("must fail: a walk of 0 under-prices — mine, nothing in sight", IRON4, None, {},
     PW(IRON4) + FIND("iron_ore")),
    ("gather 2, a tree 8 away", Step("gather", "log", 2), None, {"oak_log": 8}, PW(Step("gather", "log", 2)) + WT(8)),
    ("gather underground: the climb out is part of it", Step("gather", "log", 2), UNDER, {"oak_log": 8},
     PW(Step("gather", "log", 2)) + WT(8) + PT["surface"] + PT["surface_per_block"] * (64 - 20)),
    ("hunt 2 kills, cows 12 away", Step("hunt", "minecraft:beef", 4, {"types": ["minecraft:cow"], "kills": 2}), None,
     {"cow": 12}, 2 * PT["hunt_each"] + WT(12)),
    ("goto 30 blocks", Step("goto", "pos", 1, {"pos": [30, 64, 0]}), None, {}, WT(30)),
    ("withdraw from a chest 5 away", Step("withdraw", "minecraft:oak_log", 4, {"pos": [5, 64, 0]}), None, {},
     PT["withdraw"] + WT(5)),
    ("fill with no water known: the search by its density", Step("fill", "minecraft:water_bucket", 1), None, {},
     PT["fill"] + FIND("water")),
    ("sleep", Step("sleep", "bed", 1), None, {}, PT["sleep"]),
]


class AwaitWhatIsOnItsWay(unittest.TestCase):
    """What a plan takes from a running job (a sown crop, a furnace) is collected first: an await step before its
    consumer — counted as held, the bread was crafted before the wheat existed (bread_from_a_farm)."""
    # (situation, carried, on its way, the plan's steps as (kind, token, count))
    ROWS = [("the crop's wheat on its way: await it, then bake", [("crafting_table", 1)], {"minecraft:wheat": 9},
             [("await", "minecraft:wheat", 3), ("craft", "minecraft:bread", 1)]),
            ("one carried, two awaited", [("crafting_table", 1), ("wheat", 1)], {"minecraft:wheat": 9},
             [("await", "minecraft:wheat", 2), ("craft", "minecraft:bread", 1)]),
            ("must fail: all carried: no await", [("crafting_table", 1), ("wheat", 3)], {"minecraft:wheat": 9},
             [("craft", "minecraft:bread", 1)]),
            ("must fail: nothing on its way: no await step", [("crafting_table", 1), ("wheat", 3)], {},
             [("craft", "minecraft:bread", 1)]),
            ("must fail: a planned source's output (the cobblestone a mine brings) is no job: no await", [], None, None)]

    def test_rows(self):
        for name, carried, pending, want in self.ROWS:
            with self.subTest(name):
                if pending is None:           # the planned-source row: counted as held, not a job's output
                    snap = snapshot(inv=inventory(("crafting_table", 1)))
                    steps = planner.plan_needs(snap.inv, [("minecraft:furnace", 1)], cost(snap),
                                               pending={"minecraft:cobblestone": 8})   # what a planned mine brings
                    self.assertEqual([s.kind for s in steps], ["craft"])
                    continue
                snap = snapshot(inv=inventory(*carried))
                steps = decompose.decompose(snap.inv, goals.have(("minecraft:bread", 1)), cost(snap), pending=pending)
                self.assertEqual([(s.kind, s.token, s.count) for s in steps], want)

    def test_a_furnace_job_awaited_before_its_consumer(self):
        """A furnace's ingots on their way: the plan's other steps first, the await right before the craft."""
        snap = snapshot(inv=inventory(("crafting_table", 1), ("stick", 2)))
        steps = planner.plan_needs(snap.inv, [("minecraft:iron_pickaxe", 1)], cost(snap),
                                   pending={"minecraft:iron_ingot": 3}, jobs={"minecraft:iron_ingot": 3})
        kinds = [(s.kind, s.token) for s in steps]
        self.assertEqual(kinds[-2:], [("await", "minecraft:iron_ingot"), ("craft", "minecraft:iron_pickaxe")])


class CostModel(unittest.TestCase):
    # (situation, carried, chest blocks away, tree blocks away): 4 logs, 4 in a remembered chest — the step planned
    # first is the cheaper of the take and the chop as the model prices them (G3), whichever it is
    CHEST_OR_TREE = [
        ("bare hands, chest by the body", (), 1, 2.5),
        ("a diamond axe held: 4 logs chopped in 8 ticks each (break_ticks)", (("diamond_axe", 1),), 1, 2.5),
        ("a wooden axe held", (("wooden_axe", 1),), 1, 2.5),
        ("must fail: the chest 40 away, the tree by the body", (("diamond_axe", 1),), 40, 2.5),
    ]

    def test_chest_or_tree(self):
        for name, carried, chest, tree in self.CHEST_OR_TREE:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                snap = snapshot(state(), inventory(*carried))
                x, y, z = snap.feet
                m.note_container((x + chest, y, z), snap.dimension, [{"id": "minecraft:oak_log", "count": 4}])
                c = cost(snap, mem=m, oak_log=tree)
                take = c.estimate(Step("withdraw", "minecraft:oak_log", 4, {"pos": [x + chest, y, z]}))
                chop = c.estimate(Step("gather", "log", 4, {}))
                steps = decompose.decompose(snap.inv, goals.have(("log", 4)), c)
                self.assertEqual(steps[0].kind, "withdraw" if take < chop else "gather", (take, chop))

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
                self.assertEqual(got, (2 * 10 * costmod.TICKS_PER_S if measured else PW(step)) + WT(8))

    # (situation, what memory has / /find saw, is a station near?)
    STATIONS = [("a table in sight 4 away", None, {"crafting_table": 4}, True),
                ("a table in sight 9 away", None, {"crafting_table": 9}, False),
                ("a table memory keeps 3 away", ((3, 64, 0),), {}, True),
                ("a table memory keeps 30 away", ((30, 64, 0),), {}, False),
                ("must fail: nothing", None, {}, False)]

    def test_station_near(self):
        for name, stations, seen, want in self.STATIONS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                for pos in stations or ():
                    m.add_station("minecraft:crafting_table", pos, OVER)
                self.assertEqual(cost(snapshot(), mem=m, **seen).station_near("minecraft:crafting_table"), want)

    # (situation, known distance or None) → seconds the column solver prices a seek at
    SEEKS = [("must fail: never seen: the declared prior", None, None), ("40 blocks away", 40.0, WT(40) / 20 + 2.0),
             ("10 blocks away", 10.0, WT(10) / 20 + 2.0), ("200 blocks away", 200.0, WT(200) / 20 + 2.0),
             ("right here", 0.0, 2.0)]

    def test_seek_from_memory(self):
        """Where memory says one is: the game's route estimate when this round already asked, else the walk; a banned
        spot is not somewhere to go; a log is found as a remembered "tree"."""
        from bonobo import nav, world
        key = lambda p: world.route_key(p, 2.0, 6000)  # noqa: E731
        prior = FIND("iron_ore") / costmod.TICKS_PER_S
        rows = [("a route the game priced this round", ("iron_ore", (10, 64, 0)), ["iron_ore"], {key((10, 64, 0)): (True, 7.3)},
                 {}, 7.3),
                ("no route asked: the walk", ("iron_ore", (10, 64, 0)), ["iron_ore"], {}, {}, round(WT(10) / 20 + 2.0, 1)),
                ("must fail: a route the game found none for: not there (cost._Gone), the prior",
                 ("iron_ore", (10, 64, 0)), ["iron_ore"], {key((10, 64, 0)): (False, None)}, {}, prior),
                ("must fail: the only spot is banned", ("iron_ore", (10, 64, 0)), ["iron_ore"], {}, {(10, 64, 0): time.time() + 600},
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

    def test_a_route_price_is_the_walks(self):
        """V5 (E3, D6): a route is priced as the walk that runs it — nothing dug or built — whatever the round's
        policy allows; a price asked with digging (the old key) is never read."""
        from bonobo import nav, world
        walk = round(WT(10) / 20 + 2.0, 1)
        dig, walk_only = nav.Policy(allow_dig=True), nav.Policy(allow_dig=False)
        walked = world.route_key((10, 64, 0), 2.0, 6000)
        dug = ((10, 64, 0), True, True, 2.0, 6000)
        rows = [("the walk's price, planning with digging allowed", walked, dig, 7.3),
                ("the walk's price, planning walking only", walked, walk_only, 7.3),
                ("must fail: a price asked with digging is read (the walk never digs)", dug, dig, walk),
                ("no policy given: the walk's price", walked, None, 7.3)]
        for name, key, planning, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.dict(nav._ROUTES, {key: (True, 7.3)}):
                m = Memory(os.path.join(tmp, "notes.json"))
                m.note_seen("iron_ore", (10, 64, 0), OVER)
                self.assertAlmostEqual(costmod.Cost(snapshot(), mem=m, policy=planning).seek_s(["iron_ore"]), want,
                                       places=1)

    def test_route_s_directly(self):
        """cost.route_s: the game's own route seconds when this round already asked it, under this policy; None
        whenever that is not so."""
        from bonobo import nav, world
        pol = nav.Policy()
        key = world.route_key((10, 64, 0), 2.0, 6000)
        rows = [("asked, found: its seconds", True, {key: (True, 7.3)}, {}, pol, 7.3),
                ("must fail: asked, no route found", True, {key: (False, None)}, {}, pol, None),
                ("not asked this round", True, {}, {}, pol, None),
                ("nothing remembered to route to", False, {key: (True, 7.3)}, {}, pol, None),
                ("the spot is banned", True, {key: (True, 7.3)}, {(10, 64, 0): time.time() + 600}, pol, None),
                ("another policy: the same walk's price", True, {key: (True, 7.3)}, {},
                 nav.Policy(allow_dig=not pol.allow_dig), 7.3)]
        for name, noted, routes, banned, policy, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, mock.patch.dict(nav._ROUTES, routes):
                m = Memory(os.path.join(tmp, "notes.json"))
                if noted:
                    m.note_seen("iron_ore", (10, 64, 0), OVER)
                self.assertEqual(costmod.Cost(snapshot(), mem=m, blacklist=banned, policy=policy).route_s(["iron_ore"]),
                                 want)

    def test_seek_seconds(self):
        prior = FIND("iron_ore") / costmod.TICKS_PER_S
        for name, known, want in self.SEEKS:
            with self.subTest(name):
                m = memory()
                if known is not None:
                    m.note_seen("iron_ore", (int(known), 64, 0), OVER)       # `known` blocks from the feet
                c = costmod.Cost(snapshot(), m)
                self.assertAlmostEqual(c.seek_s(["iron_ore"]), round(want if want is not None else prior, 1), places=1)

    def test_find_seconds_by_kind(self):
        """Unseen kinds priced by how the game places them (knowledge.expected_find_s), the soonest of a step's kinds."""
        from bonobo import data
        c = costmod.Cost(snapshot(), memory())
        weight = data.PASSIVE_WEIGHT
        common, rare = max(weight, key=weight.get), min(weight, key=weight.get)
        bed = f"{data.COLORS[0]}_bed"
        rows = [("the commonest animal", ["minecraft:" + common], FIND("minecraft:" + common)),
                ("an ore: its band's tunnel", ["iron_ore"], FIND("iron_ore")),
                ("the soonest of two kinds", ["minecraft:" + rare, "minecraft:" + common], FIND("minecraft:" + common))]
        for name, kinds, want in rows:
            with self.subTest(name):
                self.assertEqual(c.find_ticks(kinds), want)
        # must fail: a bed priced as a sheep (free run 23:46: seek white_bed ~600s beat wool from sheep)
        self.assertGreater(c.seek_s([bed]), c.seek_s(["minecraft:" + common]))
        # must fail: a rare animal priced as the commonest
        self.assertGreater(c.seek_s(["minecraft:" + rare]), c.seek_s(["minecraft:" + common]))


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
    ("must fail: goal met while the plan was held", IRON3, stone_tools(), inventory(("iron_ingot", 3)), None,
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
                entries = [("task t1", goal, 0)]
                snap_b, snap_a = snapshot(inv=inv_before), snapshot(inv=inv_after)
                held_b, why_b = brainmod.replan(entries, snap_b, cost(snap_b, **seen))
                held_a, why_a = brainmod.replan(entries, snap_a, cost(snap_a, **seen), pending=pending)
                self.assertIsNone(why_b)
                self.assertIsNone(why_a)
                self.assertEqual(held_a["sig"], needs.bag_signature(snap_a.inv), "the held plan is stamped with its bag")
                self.assertFalse(held_a["event"])
                check(self, held_b["steps"], held_a["steps"])

    def test_nothing_can_plan_it(self):
        for name, goal in UNPLANNABLE:
            with self.subTest(name):
                try:
                    held, why = brainmod.replan([("task t1", goal, 0)], snapshot(), cost())
                except ValueError:
                    continue
                self.assertEqual((held, why.split(":")[0]), (None, "unplannable"))

    def test_done_is_read_off_the_world(self):
        here, there = snapshot(), snapshot(state(x=40.5, z=0.5))
        walked = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
        walked.data.setdefault("roads", {})[OVER] = [{"a": [0, 64, 0], "b": [40, 64, 0], "s": 9.0, "used": 0}]
        road = goals.make("road", a=[0, 64, 0], b=[40, 64, 0])
        # (situation, goal, snapshot, memory) → done? None: the goal's own step, accepted by its contract, ends it
        rows = [("a road walked, the body at its end", road, there, walked, True),
                ("must fail: a road walked, the body back at its start", road, here, walked, False),
                ("must fail: at the road's end, the road never walked", road, there, None, False),
                ("a skill", goals.make("skill", name="chop", args=[1]), here, None, None),
                ("an effect", goals.make("effect", effect="mine:minecraft:stone", detail={"pos": [0, 64, 0]}), here,
                 None, None),
                ("must fail: an item goal not held", goals.have(("log", 1)), here, None, False)]
        for name, goal, snap, mem, want in rows:
            with self.subTest(name):
                self.assertEqual(goal["goal"] in goals.RUN_ONCE, want is None)
                self.assertIs(goals.done(goal, snap, mem), want)


# ----------------------------------------------------------------------------------------------- held plans
# The brain's queue work (task_act / repair / after_step / finish / prepare) on an unstarted Brain: the cost model it
# builds answers from the row's look-around, the bag after a step from the row's readings; any other request fails.
TREES = {"oak_log": 6, "stone": 2}          # fixture: the default look-around of a held-plan row


class Notes(Memory):
    """Memory held in this process only: what the brain notes stays here, no file is written."""

    def __init__(self):
        super().__init__(os.path.join(os.sep, "nonexistent", "notes.json"))

    def save(self):
        pass


class Held:
    """The queue's decision (brain.task_act / after_step) on an unstarted Brain, IO outside as the brain's own caller
    does it: the task is a dict here, each decision's task writes applied to it right after; the cost model and the
    bag after a step are the row's readings."""

    def __init__(self, goal, plan=None, seen=TREES):
        lifecycle.reset_all(caches=False)          # a fresh life: no reservation of an earlier row
        self.seen, self.after_inv, self.act, self.first = seen, inventory(), None, None
        b = self.b = brain_fixture()
        b.unplannable = {}
        b.abandoned = None
        b.policy_cache = nav.Policy()
        b.mem = Notes()
        b.retry, b.blacklist, b.place, b.held = retry.Retry(), {}, PLACE, {}
        b.needs, b.reflexes = needs.Needs(b), reflexes.Maintain(b)
        b.last_failure, b.committed, b.last_hold_log, b.task_writes = None, None, 0, None
        self.task = {"id": "t1", "goal": goal["goal"], "args": goal.get("args", {}), "state": "pending", "reason": "",
                     **({"plan": plan} if plan is not None else {})}

    def round(self, inv, st=None):
        snap = snapshot(st or state(), inv)
        if self.task["state"] not in tasks.LIVE:
            return None
        c, tid = cost(snap, mem=self.b.mem, **self.seen), self.task["id"]
        goal = tasks.goal_of(self.task)
        # the round's plan for this task alone, as plan_proposals makes it before the queue's decision (task_act)
        done = goals.remainder(goal, snap, self.b.mem) == {}
        held = self.b.held.get(tid) if done else self.b.round_for([(f"task {tid}", goal, 0)], snap, c,
                                                                  self.b.held.get(tid))
        if held is None and not done:
            self.task.update(self.b._collecting(
                lambda: self.b.fail_task(self.task, self.b.unplannable.get("round", "unplannable")))[1])
            return None
        if not done:
            self.b.held[tid] = held
            if self.task.get("state") != "running":
                self.task["state"] = "running"
        act, update = self.b.task_act(self.task, snap, round_ctx(self.b, snap), c, held)
        self.task.update(update)
        return act

    def after(self, outcome):
        self.task.update(self.b.after_step(self.act, outcome))

    def state(self):
        return self.task["state"], self.task["reason"]


class Queue_:
    """One brain, one task file, the row's readings: plan_proposals, the queue's IO-owning caller."""

    def __init__(self, tmp, seen=TREES):
        self.tmp, self.seen, self.after_inv = tmp, seen, inventory()
        b = self.b = brain_fixture()
        b.unplannable = {}
        b.abandoned = None
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
        lifecycle.reset_all(caches=False)
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()

    def task(self, goal):
        tasks.add(goal)
        return tasks.load()[0]

    def state(self, task_id="t1"):
        return next((t["state"], t["reason"]) for t in tasks.load() if t["id"] == task_id)


# (situation, goal, [(op, args...)]). ops: ("round", inv[, state]) → the act, kept as q.act; ("ok", bag after) /
# ("interrupted",) / ("failed", n failures of cause nav): the step's outcome; ("walked", a, b): a road leg noted;
# ("check", fn(test, q)).
ROAD = goals.make("road", a=[0, 64, 0], b=[40, 64, 0])
HELD = [
    ("a fresh task: plan, hold, first step", goals.have(("log", 4)), [
        ("round", inventory()),
        ("check", lambda t, q: (t.assertEqual((q.act.step.kind, q.act.step.token), ("gather", "log")),
                                t.assertEqual(q.state(), ("running", "")),
                                t.assertNotIn("plan", q.task, "K4: no plan stored with the task")))]),
    ("the goal is met already: done, no act", goals.have(("log", 4)), [
        ("round", inventory(("oak_log", 4))),
        ("check", lambda t, q: (t.assertIsNone(q.act), t.assertEqual(q.state(), ("done", ""))))]),
    ("a step done: no step count kept, the next round plans from the world, the done step not redone", PICK1, [
        ("round", inventory()), ("ok", inventory(("oak_log", 3))),
        ("check", lambda t, q: t.assertIn(q.first, q.b.held["t1"]["steps"])),
        ("round", inventory(("oak_log", 3))),
        ("check", lambda t, q: t.assertNotEqual((q.act.step.kind, q.act.step.token), ("gather", "log")))]),
    ("must fail: a step's work undone by the world is done again", goals.have(("log", 4)), [
        ("round", inventory()), ("ok", inventory(("oak_log", 4))),
        ("round", inventory(("oak_log", 1))),
        ("check", lambda t, q: (t.assertEqual((q.act.step.kind, q.act.step.token), ("gather", "log")),
                                t.assertEqual(q.act.step.count, 3)))]),
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
            t.assertEqual([st.count for st in q.b.held["t1"]["steps"] if (st.kind, st.token) == ("mine", "minecraft:cobblestone")], [4]),
            t.assertEqual(q.b.held["t1"]["steps"][-1].kind, "build"),
            t.assertNotIn(pair("craft", "door"), pairs(q.b.held["t1"]["steps"]))))]),
    ("road: the body at its start: that leg met by the world, the second walked", ROAD, [
        ("round", inventory()),
        ("check", lambda t, q: t.assertEqual(q.act.step.detail["pos"], [40, 64, 0]))]),
    ("must fail: road: the body moved off its start: the first leg walked again", ROAD, [
        ("round", inventory()), ("interrupted",), ("round", inventory(), state(x=20.5, z=0.5)),
        ("check", lambda t, q: t.assertEqual(q.act.step.detail["pos"], [0, 64, 0]))]),
    ("failed on the third source: the task is failed with its cause", goals.have(("log", 4)), [
        ("round", inventory()), ("failed", 3),
        ("check", lambda t, q: (t.assertEqual(q.state()[0], "failed"), t.assertTrue(q.state()[1].startswith("nav"))))]),
    ("failed once: kept, repaired next round", goals.have(("log", 4)), [
        ("round", inventory()), ("failed", 1),
        ("check", lambda t, q: (t.assertEqual(q.state()[0], "running"), t.assertTrue(q.b.held["t1"]["event"])))]),
    ("must fail: a plan left in an old task file is never read (no stored progress)", goals.have(("log", 6)), [
        ("saved", [{"kind": "craft", "token": "minecraft:stick", "count": 4, "detail": {}, "est": 100}]),
        ("round", inventory(("oak_log", 4))),
        ("check", lambda t, q: t.assertEqual((q.act.step.kind, q.act.step.count), ("gather", 2)))]),
    ("must fail: unplannable: failed, and says why", goals.make("skill", name="fly_to_the_moon"), [
        ("round", inventory()),
        ("check", lambda t, q: (t.assertIsNone(q.act), t.assertEqual(q.state()[0], "failed"),
                                t.assertIn("unplannable", q.state()[1])))]),
    ("a road walked, the body at its end: done from the world", ROAD, [
        ("round", inventory()), ("ok", inventory()), ("walked", [0, 64, 0], [40, 64, 0]),
        ("round", inventory(), state(x=40.5, z=0.5)),
        ("check", lambda t, q: (t.assertIsNone(q.act), t.assertEqual(q.state(), ("done", ""))))]),
    ("must fail: at the road's end, never walked: not done", ROAD, [
        ("round", inventory(), state(x=40.5, z=0.5)),
        ("check", lambda t, q: (t.assertIsNotNone(q.act), t.assertEqual(q.state()[0], "running")))]),
    ("a skill's own step accepted by its contract: done; a step before it: the task goes on",
     goals.make("skill", name="chop", args=[2]), [
        ("round", inventory()), ("ok", inventory(("oak_log", 2))),
        ("check", lambda t, q: t.assertEqual(q.state()[0], "done" if q.first.kind == "skill" else "running"))]),
    ("must fail: a skill's step interrupted: not done", goals.make("skill", name="chop", args=[2]), [
        ("round", inventory()), ("interrupted",),
        ("check", lambda t, q: t.assertEqual(q.state()[0], "running"))]),
    ("no trees anywhere: the plan runs dry, the task fails as unavailable", goals.have(("log", 4)), [
        ("seen", {}), ("round", inventory()),
        ("check", lambda t, q: t.assertEqual((q.act.step.kind, q.act.step.token), ("gather", "log"))),
        ("failed_as", NotAvailable("no trees found nearby, even after exploring"), 3),
        ("check", lambda t, q: (t.assertEqual(q.state()[0], "failed"),
                                t.assertTrue(q.state()[1].startswith("unavailable"))))]),
    ("no stone anywhere: the stone pickaxe plan still starts from what can be had", PICK1, [
        ("seen", {"oak_log": 5}), ("round", inventory()),
        ("check", lambda t, q: (t.assertEqual(q.act.step.kind, "gather"),
                                t.assertIn(pair("mine", "minecraft:cobblestone"), pairs(q.b.held["t1"]["steps"]))))]),
    ("ingots cooking in a furnace: wait, don't fail", IRON3, [
        ("job", "minecraft:iron_ingot", 3), ("round", stone_tools()),
        ("check", lambda t, q: (t.assertIsNone(q.act), t.assertEqual(q.state()[0], "running")))]),
]


class HeldPlans(unittest.TestCase):
    def test_event_sequences(self):
        for name, goal, ops in HELD:
            q = Held(goal, next((o[1] for o in ops if o[0] == "saved"), None))
            with self.subTest(name):
                for op_, *a in ops:
                    if op_ == "round":
                        q.act = q.round(*a)
                        q.first = q.act.step if q.act else None
                    elif op_ == "ok":
                        q.after_inv = a[0]
                        q.after("ok")
                    elif op_ == "interrupted":
                        q.after("interrupted")
                    elif op_ in ("failed", "failed_as"):
                        err, times = (api.NavFailed("no path found"), a[0]) if op_ == "failed" else (a[0], a[1])
                        for _ in range(times):
                            q.b.last_failure = q.b.failed(q.act.name, err)
                        q.after("failed")
                    elif op_ == "seen":
                        q.seen = a[0]
                    elif op_ == "walked":
                        q.b.mem.data.setdefault("roads", {}).setdefault(OVER, []).append(
                            {"a": a[0], "b": a[1], "s": 9.0, "used": time.time()})
                    elif op_ == "job":
                        q.b.mem.add_job("smelt", (3, 64, 0), OVER, a[0], a[1], time.time() + 60, [])
                    elif op_ == "check":
                        a[0](self, q)

    def test_a_held_plan_is_solved_again_only_on_an_event(self):
        """plan_without_events: a held plan none of whose steps could run was solved again every round from the same
        bag ("plan for t1" every 3 s, nothing run). Solved again only when the bag changed."""
        before, changed = inventory(), inventory(("oak_log", 2))
        rows = [("a step runs, the same bag: held as it is", True, before, 0),
                ("must fail: no step runs, the same bag: not solved again (it cools)", False, before, 0),
                ("no step runs, the bag changed: solved again", False, changed, 1),
                ("a step runs, the bag changed: solved again", True, changed, 1)]
        for name, runs, bag_now, want in rows:
            q = Held(goals.have(("log", 8)))
            with self.subTest(name):
                q.round(before)                             # the first plan: not counted
                q.b.valid = lambda *a, **k: runs
                with mock.patch.object(brainmod, "replan", wraps=brainmod.replan) as solved:    # a spy: counts
                    q.round(bag_now)
                self.assertEqual(solved.call_count, want)

    def test_a_new_plan_replaces_the_held_one_only_when_it_pays(self):
        """D4: a re-solved plan is taken only when it pays the switch."""
        from bonobo.beliefs import TICKS_PER_S
        # (situation, the new plan's seconds, seconds thrown away) → the new plan taken
        rows = [("much cheaper: taken", 1.0, 0.0, True),
                ("must fail: cheaper by less than it throws away: the held kept", 1.0, 1e6, False),
                ("must fail: dearer: the held kept", 1e6, 0.0, False)]
        for name, new_s, lost, taken in rows:
            q = Held(goals.have(("log", 8)))
            with self.subTest(name):
                q.round(inventory())
                held = q.b.held["t1"]
                held["event"] = True
                other = planner.Step("take", "log", 8, {"blocks": ["oak_log"]})
                other.est = int(new_s * TICKS_PER_S)
                fresh = {"steps": [other], "sig": None, "event": False, "dim": held["dim"]}
                with mock.patch.object(brainmod, "replan", return_value=(fresh, None)), \
                        mock.patch.object(brainmod, "thrown_s", return_value=lost):
                    q.round(inventory())
                self.assertEqual(q.b.held["t1"] is fresh, taken)
                held_s, chosen_s, lost_s, switched = q.b.plan_switch
                self.assertEqual((switched, chosen_s + lost_s < held_s), (taken, taken))

    def test_a_hazard_seen_while_planning_is_answered_this_round(self):
        """S1: a stop mid-search ends it; the round answers the hazard, not the plan."""
        from bonobo import hazard
        cost_ = planner.NullCost()
        cost_.stop = lambda: True
        with self.assertRaises(api.Interrupted):           # must fail: the search runs on through the stop
            planner.plan_needs(bag(inventory()), [("log", 4)], cost_)
        self.assertTrue(planner.plan_needs(bag(inventory()), [("log", 4)], planner.NullCost()))   # no stop: never stops
        q = Held(goals.have(("log", 4)))
        b = q.b
        reads = [snapshot(state(inWater=True), inventory())]       # the round read again once planning stops

        def planning(snap, ctx):
            api.request_interrupt("drowning")
            raise api.Interrupted("a hazard while planning")
        try:
            with mock.patch.object(b, "plan_proposals", side_effect=planning), \
                    mock.patch.object(b.reflexes, "proposals", return_value=[]), \
                    mock.patch.object(b.needs, "propose"), mock.patch.object(b.needs, "needs_now", [], create=True), \
                    mock.patch.object(brainmod.Snapshot, "read", lambda kinds, ground: reads.pop(0)), \
                    mock.patch.object(api, "mode", return_value="normal"), \
                    mock.patch.object(brainmod.arbiter.BODY, "holder", return_value=None), \
                    mock.patch.object(hazard, "rescue_due",
                                      side_effect=lambda s, **k: "drowning" if s.get("inWater") else None):
                snap = snapshot(state(), inventory())
                act = b.decide(snap, round_ctx(b, snap))
            self.assertIsNotNone(act)                       # must fail: the plan's round, the hazard left a round
            self.assertEqual(act.name, "rescue drowning")
            self.assertIsNone(api.interrupt_pending())
        finally:
            api.clear_requests()

    # (bag, what idle prepares first, or None when everything is held)
    PREPARE = [(inventory(), ("tool", "pickaxe", 1)),
               (inventory(("stone_pickaxe", 1)), ("tool", "sword", 1)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1)), ("food", 8)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("cooked_beef", 8)), ("minecraft:torch", 8)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("cooked_beef", 8), ("torch", 8)), "milestone")]

    def test_prepare(self):
        """Idle stocking is a proposal toward the first missing item, never a task; stocked, the run's next milestone
        (must fail: everything held, holding idle)."""
        for inv, want in self.PREPARE:
            with self.subTest(want=want), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                snap = snapshot(inv=inv)
                act = q.b.prepare(snap, round_ctx(q.b, snap))
                self.assertEqual(tasks.load(), [], "idle stocking queued a task")
                if want == "milestone":
                    first = next(n for n in goals.MILESTONES
                                 if goals.remainder(goals.make("milestone", name=n), snapshot(inv=inv), q.b.mem) != {})
                    self.assertIsNotNone(act, "must fail: PREPARE met, the queue empty: holding idle")
                    self.assertEqual(act.name, f"milestone: {goals.describe(goals.make('milestone', name=first))}")
                    continue
                self.assertEqual(act.name, f"idle: {goals.describe(goals.have(want))}")

    def test_the_round_that_finishes_the_queue_proposes_nothing_more(self):
        """plan_proposals: the task met this round is finished and nothing else is offered — the next round (queue
        empty) decides on stocking. Stocking in the same round ate the bench slices' budget."""
        rows = [("must fail: the last task met now: nothing this round", [goals.have(("log", 4))], [("oak_log", 4)], []),
                ("a task met, another still live: that one", [goals.have(("log", 4)), goals.have(("stick", 4))],
                 [("oak_log", 4), ("oak_planks", 4)], ["queue"]),
                ("nothing queued at all: stocking", [], [], ["idle"]),
                ("a live task, not met: the task", [goals.have(("log", 4))], [], ["queue"])]
        for name, queued, items, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                for g in queued:
                    q.task(g)
                snap = snapshot(state(), inventory(*items))
                got = q.b.plan_proposals(snap, round_ctx(q.b, snap))
                self.assertEqual([i.kind for i in got], want)

    def test_a_failure_cools_where_it_happened(self):
        """A step that walked away from where its round began is cooled at both places (the feet at the failure: the
        last /state read, no read of its own)."""
        from unittest import mock
        start, far = (0.0, 64.0, 0.0), (70.0, 64.0, 0.0)
        # (situation, the feet last read at the failure) → (ready at the start, ready where it failed)
        rows = [("failed 70 blocks away: cooled at both", far, (False, False)),
                ("must fail: the failure's place unread: retried where it failed", None, (False, True))]
        for name, seen, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q, \
                    mock.patch.object(api.STATE, "feet_seen", seen):
                q.b.place = retry.place_signature(start, False)
                q.b.failed("seek bed", api.NotAvailable("could not find white_bed"))
                got = []
                for at in (start, far):
                    q.b.place = retry.place_signature(at, False)
                    got.append(q.b.ready("seek bed"))
                self.assertEqual(tuple(got), want)

    def test_idle_beside_the_queue(self):
        """plan_proposals: stocking only when the queue has nothing that can run now, and never into the queue
        (tool_tier__one_use: a queued sword took over whenever the row's own task cooled)."""
        rows = [("a task that can run: the task, no stocking", True, False, ["queue"]),
                ("nothing queued: stocking proposed", False, False, ["idle"]),
                ("the task cooling: stocking may be picked", True, True, ["idle"]),
                ("nothing queued, stocked: the next milestone (must fail: holding, nothing)", False, False, ["idle"])]
        for name, queued, cooling, want in rows:
            full = inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("cooked_beef", 8), ("torch", 8))
            inv = full if "stocked" in name else inventory()
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                if queued:
                    q.task(goals.have(("log", 4)))
                if cooling:
                    for _ in range(3):
                        q.b.failed("task t1", api.NavFailed("no path found"))
                before = [t["id"] for t in tasks.load()]
                snap = snapshot(state(), inv)
                got = q.b.plan_proposals(snap, round_ctx(q.b, snap))
                self.assertEqual([i.kind for i in got], want)
                self.assertEqual([t["id"] for t in tasks.load()], before, "stocking changed the queue")


# -------------------------------------------------------------------------------------------------------- upkeep
PLACE = retry.place_signature((0, 64, 0), False)
DAY, DUSK, NIGHT = 2000, DAY_END - 25 * 20, 18000     # DUSK: 25 s before the one dusk (data.DAY_END)
WELL_FED = [("cooked_beef", 8), ("white_bed", 1), ("stone_pickaxe", 1)]      # fixture: the default bag
HERD = {"cow": 12, "sheep": 20, "oak_log": 10, "stone": 2}


class UnstuckBySituation(unittest.TestCase):
    def test_rows(self):
        from bonobo import reflexes
        # (situation, enclosed, pit, on a column, under rock) → the first way out
        rows = [("walled in: dig out sideways", True, False, False, True, "east"),
                ("in a pit: climb out", False, True, False, False, "up"),
                ("high on a column: come down", False, False, True, False, "down"),
                ("under rock: toward the surface", False, False, False, True, "up"),
                ("must fail: open ground: never up first (it built a pillar)", False, False, False, False, "east")]
        for name, enclosed, pit, column, rock, first in rows:
            with self.subTest(name):
                order = reflexes.unstuck_order(reflexes.stuck_situation(enclosed, pit, column, rock))
                self.assertEqual(order[0], first)
                self.assertEqual(sorted(order), sorted(reflexes.UNSTUCK_WAYS), "every way still tried")

    def test_on_column(self):
        from bonobo import reflexes
        from tests.world import FakeRegion
        feet = (0, 65, 0)
        pillar = FakeRegion((-1, 64, -1), (1, 67, 1), {(0, 64, 0): "minecraft:dirt"})
        floor = FakeRegion((-1, 64, -1), (1, 67, 1),
                           {(x, 64, z): "minecraft:dirt" for x in (-1, 0, 1) for z in (-1, 0, 1)})
        self.assertTrue(reflexes.on_column(pillar, feet))
        self.assertFalse(reflexes.on_column(floor, feet), "must fail: a floor read as a column")


class RecoveryWorth(unittest.TestCase):
    def test_rows(self):
        from bonobo import nav, reflexes
        from bonobo.data import ITEM_DESPAWN_S
        speed = nav.PLAYER_SPEED
        # (situation, value s, blocks away, seconds since death) → worth the walk
        rows = [("near, soon after: worth it", 100.0, 20.0, 60.0, True),
                ("must fail: a spot 10k blocks off (it despawns long before)", 100.0, 10_000.0, 60.0, False),
                ("nothing of value there", 0.0, 20.0, 60.0, False),
                ("must fail: reachable but despawned by arrival", 100.0, speed * 30, ITEM_DESPAWN_S - 20, False),
                ("the walk costs more than it brings", 10.0, speed * 20, 0.0, False)]
        for name, value, dist, since, want in rows:
            with self.subTest(name):
                self.assertEqual(reflexes.recovery_worth(value, dist, since, speed, ITEM_DESPAWN_S) > 0, want)


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
    Row("hungry, nothing edible, nothing seen: no stock need (the round's plan keeps the bar above empty)", None,
        food=4, inv=[("white_bed", 1), ("stone_pickaxe", 1)], seen={}),
    Row("eating failed here a moment ago: the next row", None, food=10, cooling=("eat",)),
    Row("in the Nether with two meals left", "leave the Nether", dimension=NETHER, skyLight=0,
        inv=[("cooked_beef", 2), ("stone_pickaxe", 1)]),
    Row("the Nether at the Overworld's midnight, a bed carried: no sleep (data.is_night, can_sleep)", None,
        dimension=NETHER, skyLight=0, time_of_day=NIGHT, inv=[("white_bed", 1), ("cooked_beef", 8), ("stone_pickaxe", 1)]),
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
    Row("died a minute ago, the bag dropped there", "recover items", died=[("minecraft:iron_ingot", 3)]),
    Row("must fail: died with nothing carried: no walk back for nothing", None, died=True),
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
    Row("hungry at night with a bed: sleep first, the meal after (S4: no meal under the open night sky)", "sleep",
        food=10, time_of_day=NIGHT),
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
    Row("hungry, four bread carried: eat (bread is food)", "eat", food=10,
        inv=[("bread", 4), ("white_bed", 1), ("stone_pickaxe", 1)]),
    Row("hungry, cooked beef carried: eat", "eat", food=10, inv=[("cooked_beef", 2), ("white_bed", 1)]),
    Row("starving, only raw beef: eat it raw", "eat", food=4, inv=[("beef", 2), ("white_bed", 1)]),
    Row("must fail: hungry, nothing edible, cows near: no eat row and no food count rule", None, food=10,
        inv=[("white_bed", 1), ("stone_pickaxe", 1)], seen={"cow": 8}),
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
    lifecycle.reset_all(caches=False)
    b = brain_fixture()
    b.unplannable = {}
    b.abandoned = None
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
        b.mem.log_death((6, 64, 0), row.state["dimension"], carried=row.died if isinstance(row.died, list) else ())
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
    for goal in (goals.have(("bed", 1)),):     # fixture: the upkeep price
        try:
            steps = decompose.decompose(snap.inv, goal, c)
            secs, known = c.plan_s(steps), all(c.known_source(st) for st in steps)
        except Unplannable:
            secs, known = float("inf"), False
        plan_s[goal["args"]["needs"][0][0]] = secs
        plan_s[goal["args"]["needs"][0][0] + ":known"] = known
    # the row's readings only: no world read, no task file
    reads = {"enclosed": row.enclosed, "bed_near": row.bed_seen, "soft_ground": False}
    picked = arbiter.arbitrate([arbiter.Intent("maintain", (name, run), seq=seq, key=name)
                                for seq, name, run in rx.proposals(snap, None, dict(reads))])
    got = picked.action if picked else None
    table.propose(snap, None, reads=dict(reads))
    plan_s["overnight"] = table.overnight(snap)        # under the round's night facts, as propose read them
    plan_s["overnight:known"] = table.known(plan_s["overnight"][2], snap)
    plan_s["night prep"] = table.night_prep_s(snap)
    if got:                                     # MAINTAIN outranks PLAN (arbiter.SCALES): no need acted this round
        table.needs_now = []
    # What upkeep wants got is proposed, never queued (planned with the queue in the round's one plan).
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
        """The bed goes to the front exactly when the time left (dusk_s) is shorter than the plan
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
                prep = plan_s["night prep"]
                bed_due = prep is not None and (needs.dusk_s(snap) < prep * lead if plan_s["overnight:known"]
                                                else needs.dusk_s(snap) <= 0)
                bed = over and not snap.night and snap.inv.count("bed") == 0 and way == "bed" and bed_due \
                    and not under_rock(snap.get("skyLight", 15))
                self.assertEqual(((("bed", 1),) in queued), bed, f"dusk in {needs.dusk_s(snap)} s, {way} plan "
                                                                  f"{secs:.0f} s × {lead}")

    def test_lead_moves_the_verdict(self):
        """The same dusk, the same bag: a longer lead inserts the bed, a shorter one does not."""
        for lead, want in ((0.0, set()), (0.1, set()), (50.0, {(("bed", 1),)}), (1000.0, {(("bed", 1),)})):
            row = Row(f"lead {lead}", None, time_of_day=DUSK, inv=[("cooked_beef", 8), ("white_wool", 3)])
            with self.subTest(lead=lead), tempfile.TemporaryDirectory() as tmp, mock.patch.object(needs, "LEAD", lead):
                _, queued, _ = run_upkeep(row, tmp)
                self.assertEqual(set(queued) & {(("bed", 1),)}, want)

    def test_dusk_clock(self):
        from bonobo.data import DAY_END, DAY_TICKS, NIGHT_END
        dawn = NIGHT_END + 100
        # derived from the one dusk (data.DAY_END): seconds = ticks to the next one / 20; must fail: at dusk and after,
        # none; from dawn (NIGHT_END) the next day's dusk, never 0
        for tod, secs in ((0, DAY_END / 20), (6000, (DAY_END - 6000) / 20), (11500, (DAY_END - 11500) / 20),
                          (DAY_END, 0.0), (18000, 0.0), (24000 + 6000, (DAY_END - 6000) / 20),
                          (dawn, (DAY_TICKS - dawn + DAY_END) / 20)):
            with self.subTest(timeOfDay=tod):
                self.assertEqual(needs.dusk_s(snapshot(state(timeOfDay=tod))), secs)
        prep = (DAY_END - 11500) / 20 / needs.LEAD + 1.0        # the night's prep, its lead past the light left at 11500
        self.assertTrue(needs.due_now(needs.dusk_s(snapshot(state(timeOfDay=11500))), prep, True, False))
        # must fail: dawn is no dusk (dusk_s 0 there queued the night's prep at daybreak)
        self.assertFalse(needs.due_now(needs.dusk_s(snapshot(state(timeOfDay=dawn))), prep, True, False))

    def test_nether_retreat(self):
        rows = [({}, [("cooked_beef", 8)], None),  # must fail: not in the Nether, no retreat
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
                ({"timeOfDay": 18000, "dimension": NETHER}, False),  # must fail: a bed in the Nether
                ({"timeOfDay": 18000, "dimension": "minecraft:the_end"}, False)]
        for st, ok in rows:
            with self.subTest(st):
                self.assertEqual(survive.can_sleep(state(**st)) is None, ok)

    def test_working_tiers(self):
        rows = [(inventory(), {}), (inventory(("stone_pickaxe", 1), ("iron_sword", 1)), {"pickaxe": 1, "sword": 2}),
                (inventory(slot("iron_pickaxe", 1, 249), ("wooden_pickaxe", 1)), {"pickaxe": 0}),
                (inventory(slot("iron_axe", 1, 247)), {"axe": 2}), (inventory(slot("iron_axe", 1, 248)), {})]  # must fail: one use left is not a working tier
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
    # what crafting alone makes (G5: use_order is a tie between equal plans, never this query's filter)
    ("two iron, no iron pickaxe: the bag makes an iron sword", inventory(("iron_ingot", 2), ("stick", 2),
                                                                   ("crafting_table", 1)), "sword", 2),
    ("two iron, an iron pickaxe held: an iron sword", inventory(("iron_ingot", 2), ("stick", 2), ("crafting_table", 1),
                                                               ("iron_pickaxe", 1)), "sword", 2),
    ("must fail: two iron make a sword, not a pickaxe (pickaxe side)", inventory(("iron_ingot", 2), ("stick", 2),
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
    ("must fail: a tree is slow: gone after three days", [("at", 0), ("see", "tree", (10, 64, 10)), ("at", 3 * DAY_T + 1)],
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
    ("must fail: the chest holds something else", (3, {"minecraft:cobblestone": 64}), {"oak_log": 40}, ("gather", "log"),
     ("withdraw", "minecraft:oak_log")),
]


# (situation, how far the chest holding 2 of 4 logs is; the trees 40 away) → the way is the cheaper as run (G3):
# 2 taken and 2 chopped, or all 4 chopped, each priced by the production model (planner.price_as_run)
WITHDRAW_SOME = [("must fail: the chest on the way to the trees: 2 taken, 2 chopped", 3),
                 ("must fail: the chest beyond the trees: the trip there costs more than chopping 2: all 4 chopped", 90)]


# (situation, goal, what a chest holds, how far) → is anything withdrawn? Taking is a way like any other, at any depth
# of the plan, priced against making it (V1: no rule for tools or intermediates).
WITHDRAW_GOALS = [
    # None: by seconds — the take merges into one trip and the planks are made anyway: whichever the model prices less
    ("must fail: sticks in a chest 2 away, a pickaxe asked: fetched or made, whichever takes fewer seconds", PICK1,
     {"minecraft:stick": 8}, 2, None),
    ("sticks in the chest, sticks asked: fetched", goals.have(("minecraft:stick", 4)), {"minecraft:stick": 8}, 2, True),
    ("a pickaxe in a chest 2 away: fetched, cheaper than making one", PICK1, {"minecraft:stone_pickaxe": 1}, 2, True),
    ("logs in the chest, logs asked, trees far: fetched", goals.have(("log", 4)), {"minecraft:oak_log": 8}, 2, True),
    ("must fail: sticks in a chest 400 away: made, not fetched", goals.have(("minecraft:stick", 4)),
     {"minecraft:stick": 8}, 400, False),
    # M3/B1: an intermediate the chest holds is taken when taking costs less than mining it
    ("cobblestone in the chest, a pickaxe asked: an intermediate, fetched", PICK1,
     {"minecraft:cobblestone": 16}, 2, True),
]


class Withdraw(unittest.TestCase):
    def test_taken_where_cheaper(self):
        for name, goal, items, far, want in WITHDRAW_GOALS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                m.note_container((far, 64, 0), OVER, [{"id": i, "count": n} for i, n in items.items()])
                snap = snapshot()
                steps = decompose.decompose(snap.inv, goal, cost(snap, mem=m, oak_log=30, stone=20))
                if want is None:
                    best = planner.plan_candidates(snap.inv, goals.needs(goal, snap.inv),
                                                   cost(snap, mem=m, oak_log=30, stone=20), exact=True)[0][2]
                    want = any(st.kind == "withdraw" for st in best)
                    self.assertLessEqual(sum(st.est for st in steps), sum(st.est for st in best))
                self.assertEqual(any(st.kind == "withdraw" for st in steps), want, list(map(str, steps)))

    def test_a_repeat_is_one_trip(self):
        """The search prices a gather it repeats as forward runs it, merged into the first: one walk to the trees."""
        name, goal, items, far, _want = WITHDRAW_GOALS[0]
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.note_container((far, 64, 0), OVER, [{"id": i, "count": n} for i, n in items.items()])
            snap = snapshot()
            found = planner.plan_candidates(snap.inv, goals.needs(goal, snap.inv),
                                            cost(snap, mem=m, oak_log=30, stone=20), exact=True)
        # must fail: a dearer plan taken while one the search priced was cheaper as run (the sticks fetched at 67.7 s
        # over made at 52.5 s, each gather of a log charged its own walk)
        self.assertEqual(found[0][1], min(s for _n, s, _st in found), [(n[:40], s) for n, s, _st in found])

    def test_part_taken_where_cheaper(self):
        for name, far in WITHDRAW_SOME:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                m.note_container((far, 64, 0), OVER, [{"id": "minecraft:oak_log", "count": 2}])
                snap = snapshot()
                steps = decompose.decompose(snap.inv, goals.have(("log", 4)), cost(snap, mem=m, oak_log=40))
                chop = planner.plan_needs(snap.inv, [("log", 4)], cost(snap, mem=m, oak_log=40), kinds={"gather"})
                gather = next(st for st in chop if st.kind == "gather")
                mix = [planner.Step("withdraw", "minecraft:oak_log", 2, {"pos": [far, 64, 0]}),
                       planner.Step("gather", gather.token, 2, dict(gather.detail))]
                chop_s = sum(st.est for st in chop)
                mix_s = sum(planner.price_as_run(mix, None, cost(snap, mem=m, oak_log=40)))
                self.assertEqual(any(st.kind == "withdraw" for st in steps), mix_s < chop_s, list(map(str, steps)))
                self.assertLessEqual(sum(st.est for st in steps), min(mix_s, chop_s), list(map(str, steps)))

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
STUCK = api.TaskStuck("no progress for 10s in mine")
HERE, THERE = PLACE, retry.place_signature((400, 64, 0), False)

# (situation, [(task, exception, place)], expected {(task, cause): n}, escalated tasks, {task: ready now?})
RETRY = [
    ("three nav failures escalate on the third; the target cools, not the place: the task may try another target",
     [("task t1", NAV, HERE)] * 3, {("task t1", "nav"): 3}, {"task t1"}, {"task t1": True}),
    ("interruptions of every kind count nothing", [("task t1", e, HERE) for e in (I, C, B, P, I)], {}, set(),
     {"task t1": True}),
    ("must fail: interleaved: interruptions do not reset or add", [("task t1", NAV, HERE), ("task t1", I, HERE),
                                                        ("task t1", NAV, HERE), ("task t1", C, HERE),
                                                        ("task t1", NAV, HERE)],
     {("task t1", "nav"): 3}, {"task t1"}, {}),
    ("two causes are counted apart", [("task t1", NAV, HERE), ("task t1", GONE, HERE), ("task t1", NAV, HERE)],
     {("task t1", "nav"): 2, ("task t1", "unavailable"): 1}, set(), {}),
    ("must fail: a missing tool is about the bag, not the place: another task stopped by it waits everywhere",
     [("task t1", TOOL, HERE)], {("task t1", "tool"): 1}, set(),
     {"task t1": False, ("task t2", "tool", HERE): False, ("task t2", "tool", THERE): False}),
    ("a place cause (stuck here) cools here, not elsewhere",
     [("task t1", STUCK, HERE)], {("task t1", "stuck"): 1}, set(),
     {"task t1": False, ("task t2", "stuck", HERE): False, ("task t2", "stuck", THERE): True}),
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
    ("must fail: two sources: not yet", [("fail", "t", "nav", 0), ("fail", "t", "nav", 1)], 2,
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
    b = brain_fixture()
    b.unplannable = {}
    b.abandoned = None
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

    def test_one_reading_of_a_ban(self):
        # (situation, blacklist, key asked, now) → banned: skillcore.banned, read by Context.blocked and the cost model
        rows = [("a cell banned till later", {(5, 64, 5): 200.0}, (5, 64, 5), 100.0, True),
                ("must fail: the ban ran out", {(5, 64, 5): 50.0}, (5, 64, 5), 100.0, False),
                ("must fail: expiring this instant is over", {(5, 64, 5): 100.0}, (5, 64, 5), 100.0, False),
                ("an entity key, asked as a list", {(42, 0, 0): 200.0}, [42, 0, 0], 100.0, True),
                ("must fail: another cell", {(5, 64, 5): 200.0}, (6, 64, 5), 100.0, False)]
        for name, blacklist, key, now, want in rows:
            with self.subTest(name):
                self.assertEqual(skillcore.banned(blacklist, key, now), want)

    # fixture: (module source) → (ban sites, the ones that follow a go_to walk in the same function)
    BANS = [("a ban after arrived: fine", "def f(ctx):\n    if not nav.arrived(c, p):\n        ctx.ban(c)\n", (1, [])),
            ("a ban after go_to: a stopped walk bans the place",
             "def f(ctx):\n    ok = nav.go_to(c, p)\n    if not ok:\n        ctx.ban(c)\n", (1, ["f"])),
            ("go_to in another function does not count",
             "def g():\n    nav.go_to(c, p)\ndef f(ctx):\n    self.ban(c)\n", (1, [])),
            ("must fail: go_to after the ban does not count", "def f(ctx):\n    ctx.ban(c)\n    nav.go_to(c, p)\n", (1, [])),
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
    ("must fail: failed with a reason", [op_add(G1), op(tasks.mark, "t1", "failed", "nav: no path")], [("t1", "failed")], None),
    ("expired tasks are cancelled", [op_add(G1, expires_s=60), op_add(G2), op_expire(61)],
     [("t1", "cancelled"), ("t2", "pending")], "t2"),
    ("not yet expired", [op_add(G1, expires_s=60), op_expire(30)], [("t1", "pending")], "t1"),
    ("cancel all", [op_add(G1), op_add(G2), op(tasks.cancel)], [("t1", "cancelled"), ("t2", "cancelled")], None),
    ("clear drops what is not live", [op_add(G1), op_add(G2), op(tasks.mark, "t1", "done"), op(tasks.drop_done)],
     [("t2", "pending")], "t2"),
    ("a finished goal can be queued again", [op_add(G3), op(tasks.mark, "t1", "done"), op_add(G3)],
     [("t1", "done"), ("t2", "pending")], "t2"),
    ("running is live (no plan kept with it: K4)",
     [op_add(G1), op(tasks.update, "t1", state="running")],
     [("t1", "running")], "t1"),
]


# goals as data: parse (command line) → need; (bag, needs) → what is short; goal → its one-line description.
PARSE = [(("tool:pickaxe:2",), ["tool", "pickaxe", 2]), (("minecraft:torch", "24"), ["minecraft:torch", 24]),  # must fail: the malformed needs raise, below
         (("log",), ["log", 1]), (("tool:sword:0",), ["tool", "sword", 0]),
         (("minecraft:oak_log", "64"), ["minecraft:oak_log", 64])]
SHORT = [
    ("must fail: nothing asked", inventory(), [], ""),
    ("held", inventory(("oak_log", 4)), [("log", 4)], ""),
    ("short of logs", inventory(("oak_log", 1)), [("log", 4)], "log 1/4"),
    ("a worn tool is not a tool", inventory(slot("stone_pickaxe", 1, TOOL_USES["stone"] - (TOOL_WORKING - 1))),
     [("tool", "pickaxe", 1)], "pickaxe tier 1"),
    ("a better tool does", inventory(("iron_pickaxe", 1)), [("tool", "pickaxe", 1)], ""),
    ("food counts cooked meals only", inventory(("beef", 8), ("cooked_beef", 2)), [("food", 8)], "food 2/8"),
    ("two short, both said", inventory(), [("log", 2), ("minecraft:torch", 3)], "log 0/2, torch 0/3"),
]
DESCRIBE = [(goals.have(("minecraft:torch", 24), ("tool", "pickaxe", 2)), "have torch×24, pickaxe tier 2"),
            (goals.make("milestone", name="food"), "milestone food"), (goals.make("goto", pos=[1, 2, 3]), "goto (1, 2, 3)"),
            (goals.make("road", a=[0, 0, 0], b=[5, 0, 0]), "road (0, 0, 0) → (5, 0, 0)"),
            (goals.make("build", bp="shelter"), "build shelter"), (goals.make("sleep"), "sleep"),
            (goals.make("skill", name="chop", args=[4]), "skill chop [4]"),
            ({"goal": "dance", "args": {}}, "dance")]         # must fail: a template nobody knows reads as its name


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
                self.assertEqual(tasks.describe_task(t), f"t1 [pending] {want}")


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

    # Every template: (args, the plan's last step (kind, token, detail it must carry)) — the chain ends at the goal
    ENDS = {"have": ({"needs": [["log", 1]]}, ("gather", "log", {})),
            "craft": ({"needs": [["minecraft:stick", 4]]}, ("craft", "minecraft:stick", {})),
            "milestone": ({"name": "food"}, ("smelt", "food", {})),
            "goto": ({"pos": [5, 64, 0]}, ("goto", "pos", {"pos": [5, 64, 0]})),
            "road": ({"a": [0, 64, 0], "b": [5, 64, 0]}, ("goto", "pos", {"pos": [5, 64, 0]})),
            "build": ({"bp": "shelter"}, ("build", "shelter", {})),
            "sleep": ({}, ("sleep", "bed", {})),
            "skill": ({"name": "chop", "args": [1]}, ("skill", "chop", {"args": [1]})),
            "effect": ({"effect": "breed"}, ("breed", "breed", {}))}

    @staticmethod
    def complete(steps, last):
        """The chain is whole: not empty, every step carried out by some registered skill (handles), and its
        last step the goal's own — its kind, its token (or a member of the group token: "food" ← cooked beef), and
        the detail the goal fixed."""
        from bonobo.knowledge import members
        kind, token, detail = last
        if not steps or not all(handles(s) for s in steps):
            return False
        end = steps[-1]
        tokens = {bare(token)} | {bare(m) for m in members(token)}
        return (end.kind == kind and bare(end.token) in tokens
                and all(end.detail.get(k) == v for k, v in detail.items()))

    def test_every_template_decomposes_to_a_complete_chain(self):
        """Extensible: a new template in goals.TEMPLATES needs a decompose branch and a row here."""
        self.assertEqual(set(self.ENDS), set(goals.TEMPLATES), "a template without a row here")
        for template, (a, last) in self.ENDS.items():
            with self.subTest(template):
                steps = plan({"goal": template, "args": a}, snapshot(), {"oak_log": 5, "stone": 2, "cow": 9})
                self.assertTrue(self.complete(steps, last), [str(s) for s in steps])

    def test_an_incomplete_chain_is_caught(self):
        # must fail: the chain cut short of its goal, a step no skill carries out, nothing at all — the same check
        steps = plan(goals.make("craft", needs=[["minecraft:stick", 4]]), snapshot(), {"oak_log": 5})
        last = self.ENDS["craft"][1]
        rows = [("the whole chain", steps, True),
                ("must fail: stops before the goal", steps[:-1], False),
                ("must fail: a step no skill provides", steps[:1] + [planner.Step("teleport", "far", 1)] + steps[1:], False),
                ("must fail: no steps", [], False),
                ("must fail: ends at another goal", steps + [planner.Step("gather", "log", 1)], False)]
        for name, chain, ok in rows:
            with self.subTest(name):
                self.assertEqual(self.complete(chain, last), ok)

    def test_what_cannot_be_decomposed_says_why(self):
        # must fail: a skill or an effect no registered skill provides, an effect missing its detail, a blueprint
        # nobody drew — Unplannable (never a KeyError), naming what is missing
        rows = [("an unknown skill", {"goal": "skill", "args": {"name": "fly"}}, "fly"),
                ("an effect nobody provides", {"goal": "effect", "args": {"effect": "teleport"}}, "teleport"),
                ("an effect without its detail", {"goal": "effect", "args": {"effect": "goto"}}, "pos"),
                ("an unknown template", {"goal": "teleport", "args": {}}, "teleport"),
                ("an unknown blueprint", {"goal": "build", "args": {"bp": "castle"}}, "castle")]
        for name, goal, says in rows:
            with self.subTest(name):
                with self.assertRaises(Unplannable) as e:
                    plan(goal, snapshot(), {})
                self.assertIn(says, str(e.exception))

    def test_the_skill_template_over_every_registered_skill(self):
        # any registered skill asked for by name: its needs got first (the bed of a sleep), then one step that skill
        # carries out, its args kept
        self.assertGreaterEqual(len(skillkit.REGISTRY), 4)
        for name in sorted(skillkit.REGISTRY):
            with self.subTest(name):
                steps = plan(goals.make("skill", name=name, args=[2]), snapshot(inv=inventory(("lava_bucket", 1))), {})
                self.assertEqual([(s.kind, s.token, s.detail) for s in steps if s.kind == "skill"],
                                 [("skill", name, {"args": [2]})])
                self.assertTrue(self.complete(steps, ("skill", name, {"args": [2]})))


# ------------------------------------------------------------------------------------------ the night's work
class OneArbiter(unittest.TestCase):
    """Every layer proposes, arbiter.arbitrate chooses: the faster layer, then the place in line (within PLAN the
    round's one plan chose: TheRoundsPick). The day's failures, as the proposals each situation makes."""

    @staticmethod
    def intent(layer, kind=None, seq=0, at=None):
        from bonobo import arbiter
        return arbiter.Intent(layer, kind or layer, kind=kind, seq=seq, at=at, key=kind or layer)

    def test_decisions_over_the_table(self):
        from bonobo import arbiter, reflexes
        P = lambda kind, seq=0: self.intent("plan", kind, seq)   # noqa: E731

        def M(name):                                            # a maintenance reflex, in its table's place
            return arbiter.Intent("maintain", name, seq=reflexes.NAMES.index(name), key=name)
        land = self.intent("safety", "rescue swimming")         # afloat: SAFETY's (hazard "swimming")
        rows = [("afloat at dusk, the night's shelter due: land first", [M("shelter"), land], "rescue swimming"),
                ("hungry, bread carried, a task queued: eat (a reflex before any plan)", [P("queue"), M("eat")], "eat"),
                ("night, a bed carried: sleep before the shelter", [M("shelter"), M("sleep")], "sleep"),
                ("a fight holds the body, the queue wants a pickaxe: the fight",
                 [P("queue"), self.intent("tactic", "fight")], "fight"),
                ("a fight vs a reflex (eat): the fight", [M("eat"), self.intent("tactic", "fight")], "fight"),
                ("drowning in a fight: the rescue", [self.intent("tactic", "fight"), self.intent("safety", "rescue")],
                 "rescue"),
                ("a rescue vs a reflex: the rescue", [M("eat"), self.intent("safety", "rescue")], "rescue"),
                ("raw meat and a furnace, food queued: the queue's head (smelt), no hunt proposed", [P("queue")],
                 "queue"),
                ("the queue's head before the second in line", [P("queue", 1), P("queue", 0)], "queue"),
                ("hungry: eat what is carried (reflex) before the round's plan", [P("round"), M("eat")], "eat"),
                ("afloat with the night's parts due: land first", [P("night prep"), land], "rescue swimming"),
                ("night underground, nothing queued, a pickaxe: dig for ore before waiting",
                 [P("wait for day"), P("night stock")], "night stock"),
                ("night underground, no pickaxe: wait for day", [P("wait for day")], "wait for day"),
                ("the bag full while hungry: eat first", [M("empty the bag"), M("eat")], "eat"),
                ("a finished furnace job vs the queue: collect it (reflex)", [P("queue"), M("collect job")],
                 "collect job"),
                ("nothing to do but stock up: idle", [P("idle")], "idle"),
                # recover items after sleep (R3), after eat (test_eating_first_keeps_the_drops)
                ("must fail: a sheltered night, a bed carried, died: sleep before the walk back (R3)",
                 [M("recover items"), M("sleep")], "sleep"),
                ("died a minute ago, hungry: eat before the walk back to the drops", [M("eat"), M("recover items")],
                 "eat"),
                ("must fail: nothing proposed: nothing", [], None)]
        for name, intents, want in rows:
            with self.subTest(name):
                got = arbiter.arbitrate(intents)
                self.assertEqual(got.action if got else None, want)

    def test_eating_first_keeps_the_drops(self):
        """A bite before the walk loses no drops while the walk beats the despawn."""
        from bonobo import nav, reflexes
        from bonobo.data import ITEM_DESPAWN_S
        from bonobo.threat import ENGAGE
        bite, speed = float(ENGAGE["eat_s"]), nav.PLAYER_SPEED
        walk = 40.0 / speed
        # (situation, drops' value s, distance blocks, died s ago) → the bite costs nothing
        rows = [("a minute ago, 40 blocks off", 120.0, 40.0, 60.0, True),
                ("must fail: the walk beats the despawn by less than a bite: eating loses them", 120.0, 40.0,
                 ITEM_DESPAWN_S - walk - bite / 2, False)]
        for name, value, dist, since, want in rows:
            with self.subTest(name):
                now = reflexes.recovery_worth(value, dist, since, speed, ITEM_DESPAWN_S)
                later = reflexes.recovery_worth(value, dist, since + bite, speed, ITEM_DESPAWN_S)
                self.assertIs(later == now, want)

    def test_the_layers(self):
        from bonobo import arbiter
        order = ["reflex", "safety", "tactic", "maintain", "plan"]
        self.assertEqual(sorted(arbiter.SCALES, key=arbiter.SCALES.get), order)

    # (situation, night, dimension) → surface work closed
    # the surface is closed exactly when it is night (data.is_night: the Overworld's only), sheltered or not
    CLOSED = [("day in the Overworld: open", 6000, "minecraft:overworld", False),
              ("must fail: night in the Overworld, in the open (the shelter row failed): closed", 18000,
               "minecraft:overworld", True),
              ("night in the Nether: no sun to wait for, open", 18000, "minecraft:the_nether", False),
              ("night by the clock in the End: open", 18000, "minecraft:the_end", False)]

    def test_surface_closed_over_the_table(self):
        from bonobo.data import is_night
        for name, t, dim, want in self.CLOSED:
            with self.subTest(name):
                self.assertIs(is_night(t, dim), want)

    def test_night_in_the_open_does_not_chop(self):
        """Night, exposed, empty bag, a tree in the queue: the round waits for day, it does not walk to the tree."""
        from unittest import mock
        b = brain_fixture()
        b.unplannable = {}
        b.abandoned = None
        b.retry, b.place = retry.Retry(), PLACE
        b.needs = mock.Mock(working={}, needs_now=[], round={}, propose=lambda snap, ctx, reads=None: [])
        b.reflexes = mock.Mock(proposals=lambda snap, ctx, reads=None: [])       # caught in the open, nothing due
        chop = brainmod.Act("task", "task t1", None, step=planner.Step("gather", "log", 2))
        # the round's one plan is the chop (brain.round_for), the task's act its step
        b.round_for = lambda entries, snap, cost, old=None: {"steps": [chop.step], "sig": None, "event": False,
                                                             "dim": snap.dimension, "want": ()}
        b.task_act = lambda task, snap, ctx, cost, held=None: (chop, {})
        b.mem, b.blacklist, b.policy_cache, b.held = None, {}, None, {}
        b.prepare = lambda snap, ctx: brainmod.Act("idle", "prepare", None)
        snap = snapshot(state(timeOfDay=NIGHT), inventory())
        task = {"id": "t1", "state": "running", **goals.have(("log", 2))}
        with mock.patch.object(api.STATE, "mode", "normal"), mock.patch.object(brainmod.hazard, "rescue_due", return_value=None), \
                mock.patch.object(tasks, "load", return_value=[task]), \
                mock.patch.object(tasks, "expire", return_value=False):
            act = b.decide(snap, round_ctx(b, snap))
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
            ("must fail: a bed carried: sleep in it, nothing to make", [("white_bed", 1)], {}, "bed", [])]

    # At night, from the shelter row (no bed: the sleep row's): (situation, bag, ground digs by hand) → way, steps
    NIGHT = [("in the open on dirt, an empty bag: dig in by hand", [], True, "dig in by hand", ["shelter"]),
             ("on stone, dirt 8 blocks along the ground (2 s walk), an empty bag: walk there, dig in by hand", [], 2.0,
              "dig in by hand", ["shelter"]),
             ("on stone, cobblestone carried: walled in", [("cobblestone", 16)], False, "wall in", ["shelter"]),
             ("on stone, a pickaxe: dig in", [("stone_pickaxe", 1)], False, "dig in", ["shelter"]),
             ("on stone, an empty bag: a pickaxe first — its tree waits for day (snap.night closes the surface)", [], False,
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

    def test_the_shelter_row_makes_the_parts(self):
        """The shelter row runs the night way's first part, then the shelter."""
        rows = [("must fail: an empty bag on stone: dig in, its parts first", [("cooked_beef", 8)]),
                ("a pickaxe: dig in at once", [("cooked_beef", 8), ("stone_pickaxe", 1)])]
        for name, carried in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                b = brain_fixture()
                b.unplannable = {}
                b.abandoned = None
                b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
                b.mem, b.retry, b.blacklist, b.place, b.held = Memory(os.path.join(tmp, "n.json")), retry.Retry(), \
                    {}, PLACE, {}
                b.needs, b.reflexes = needs.Needs(b), reflexes.Maintain(b)
                b.context = lambda dim, policy: None
                b.policy = lambda snap, night: None
                snap = snapshot(state(timeOfDay=NIGHT), inventory(*carried))
                c = cost(snap)
                b.needs.cost = lambda _snap: c
                way, _secs, steps = needs.overnight(snap.inv, c, needs.night_facts(False), bed_too=False)
                reads = {"enclosed": False, "bed_near": False, "soft_ground": False, "in_pit": False}
                ran = []
                with mock.patch.object(api, "api", side_effect=AssertionError("read the world beyond the row")), \
                        mock.patch.object(reflexes, "STEP_RUN", lambda ctx, st, night: ran.append(st)), \
                        mock.patch.dict(reflexes.SHELTER_RUN, {steps[-1].token: lambda ctx: ran.append("shelter")}):
                    fired = {n: r for _s, n, r in b.reflexes.proposals(snap, None, reads)}
                    self.assertIn("shelter", fired)
                    fired["shelter"]()
                self.assertEqual(ran, [steps[0] if len(steps) > 1 else "shelter"])

    def test_every_way_cooled_still_covers_the_night(self):
        """Every night way cooled here: the shelter row still takes the cheapest (S1 over D5), never the open night."""
        from bonobo import decompose
        with tempfile.TemporaryDirectory() as tmp:
            b = brain_fixture()
            b.unplannable = {}
            b.abandoned = None
            b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
            b.mem, b.retry, b.blacklist, b.place, b.held = Memory(os.path.join(tmp, "n.json")), retry.Retry(), \
                {}, PLACE, {}
            b.needs, b.reflexes = needs.Needs(b), reflexes.Maintain(b)
            now = time.time()
            for w in [s["name"] for k in ("overnight bed", "overnight") for s in decompose.SOURCES[k]]:
                b.retry.failed(needs.way_key(w), "error", "failed here", now, PLACE)
            snap = snapshot(state(timeOfDay=NIGHT), inventory(("cooked_beef", 8), ("stone_pickaxe", 1)))
            c = cost(snap)
            b.needs.cost = lambda _snap: c
            reads = {"enclosed": False, "bed_near": False, "soft_ground": False, "in_pit": False}
            with mock.patch.object(api, "api", side_effect=AssertionError("read the world beyond the row")):
                fired = [n for _s, n, _r in b.reflexes.proposals(snap, None, reads)]
            self.assertIn("shelter", fired)        # must fail: every way cooled left the body in the open

    def test_a_way_that_failed_here_gives_way_to_the_next(self):
        """A night way that failed here (cooling under needs.way_key) drops out of the pricing: the next way is
        chosen the same night (search_night_resume: dig in refused 'no lid below the ground line', then idling)."""
        from bonobo import decompose
        carried = [("stone_pickaxe", 1), ("cobblestone", 16)]            # both dig in and wall in can be had
        # (situation, the ways cooling) → the way chosen
        rows = [("dig in cooled: walled in", ["dig in"], "wall in"),
                ("wall in cooled: dug in", ["wall in"], "dig in"),
                ("a way this bag cannot take cooled: no change", ["hut", "dig in by hand"], None),
                # the hut too: its parts can be planned from this bag (a door, torches), so it is a way
                ("must fail: every way cooled: none tonight", [s["name"] for s in decompose.SOURCES["overnight"]],
                 "none")]
        snap = snapshot(state(timeOfDay=NIGHT), inventory(*carried))
        free, _s, _st = needs.overnight(snap.inv, cost(snap), needs.night_facts(False), bed_too=False)
        for name, cooled, want in rows:
            with self.subTest(name):
                got, _secs, steps = needs.overnight(snap.inv, cost(snap), needs.night_facts(False, cooled),
                                                    bed_too=False)
                self.assertEqual(got, free if want is None else (None if want == "none" else want))
        ways = [s["name"] for k in ("overnight bed", "overnight") for s in decompose.SOURCES[k]]
        cooled = [("one way cooling", lambda key: key != needs.way_key("dig in"), ["dig in"]),
                  ("two ways cooling, in the sources' order",
                   lambda key: key not in {needs.way_key("dig in"), needs.way_key("wall in")},
                   [w for w in ways if w in ("dig in", "wall in")]),
                  ("boundary: every way cooling", lambda key: False, ways),
                  ("must fail: nothing cooling", lambda key: True, [])]
        for name, ready, want in cooled:
            with self.subTest(name):
                self.assertEqual(needs.cooled_ways(ready), want)

    def test_only_a_dig_in_that_can_finish_is_offered(self):
        """survive.dig_in_site over the ground under the feet, and the pricing that reads it (needs.night_facts):
        a floor too thin to lid below the ground line is not offered — wall in is (search_night_resume)."""
        from bonobo import survive
        from tests.world import FakeRegion

        def ground(bottom, dug=(), under=None):
            blocks = {(x, y, z): "stone" for x in (-1, 0, 1) for z in (-1, 0, 1) for y in range(bottom, 64)}
            for c in dug:
                blocks.pop(c, None)
            if under:
                blocks[under[0]] = under[1]
            return FakeRegion((-2, 55, -2), (2, 67, 2), blocks)
        # (situation, the ground, the feet) → can a dig-in finish here
        rows = [("deep stone", ground(57), (0, 64, 0), True),
                ("four thick: the lid's floor stands", ground(60), (0, 64, 0), True),
                ("resumed after a fall, two dug, deep stone", ground(57, dug=[(0, 63, 0), (0, 62, 0)]), (0, 62, 0),
                 True),
                ("must fail: three thick over air (the bench arena)", ground(61), (0, 64, 0), False),
                ("must fail: lava in the third cell", ground(57, under=((0, 61, 0), "lava")), (0, 64, 0), False),
                ("must fail: bedrock in the second cell", ground(57, under=((0, 62, 0), "bedrock")), (0, 64, 0), False)]
        for name, region, feet, want in rows:
            with self.subTest(name):
                self.assertIs(survive.dig_in_site(region, feet), want)
        with self.subTest("must fail: the cell under the feet protected"):
            self.assertIs(survive.dig_in_site(ground(57), (0, 64, 0), {(0, 63, 0)}), False)
        snap = snapshot(state(timeOfDay=NIGHT), inventory(("stone_pickaxe", 1), ("cobblestone", 16)))
        for name, site, want in [("must fail: no dig-in site: walled in", False, "wall in")]:
            with self.subTest(name):
                got, _s, _st = needs.overnight(snap.inv, cost(snap), needs.night_facts(False, (), site), bed_too=False)
                self.assertEqual(got, want)
        with self.subTest("a dig-in site: dig in stays on offer"):
            got, _s, _st = needs.overnight(snap.inv, cost(snap), needs.night_facts(False, (), True), bed_too=False)
            self.assertIn(got, ("dig in", "wall in"))
            without = needs.night_facts(False, (), False)
            self.assertEqual(without.get("no_dig_site"), True)

    def test_a_bed_that_exists_comes_first(self):
        """C6 (A): a carried bed, or the home's bed a walk reaches, ends the night before any shelter is priced;
        a shelter is only for a night no bed can end, and no sleep is paired with it."""
        pick = [("stone_pickaxe", 1)]
        # (situation, bag, night facts, bed_too) → the way and its steps' kinds
        rows = [("17:30: a bed carried and a pickaxe — the bed (its room is the sleep's: test_night_plan BedRoom)",
                 pick + [("white_bed", 1)], {"soft_ground": False}, True, ("bed", [])),
                ("17:49: a home bed 12 s away — the home's bed, not dig in",
                 pick, needs.night_facts(False, (), True, 12.0), True, ("home", ["shelter"])),
                ("must fail: the home bed 900 s of open night walk away — not the walk: a bed made before dark (3 sheep, a
                 # table) is ~111 s against the dig-in's 25 s and the night waited out in it",
                 pick, needs.night_facts(False, (), True, 900.0), True, ("bed", ["hunt", "gather", "craft", "craft", "craft"])),
                ("no bed carried, the home bed with no way to it (no home_bed fact): dig in",
                 pick, {"soft_ground": False}, True, ("dig in", ["shelter"])),
                ("the shelter row (bed_too off) with a bed carried that was refused: a shelter to wait in",
                 pick + [("white_bed", 1)], {"soft_ground": False}, False, ("dig in", ["shelter"]))]
        for name, carried, facts, bed_too, want in rows:
            with self.subTest(name):
                snap = snapshot(state(timeOfDay=NIGHT), inventory(*carried))
                got, _secs, steps = needs.overnight(snap.inv, cost(snap), facts, bed_too=bed_too)
                self.assertEqual((got, [st.kind for st in steps]), want)

    def test_stone_ground_dirt_near_walls_in(self):
        """On stone, an empty bag, dirt 4 away, no tree seen: the cheaper of nine dirt dug by hand and walled in, or a
        wooden pickaxe from a tree found by the prior density (knowledge.FIND_DENSITY) and dug in — priced, not pinned."""
        snap = snapshot(state(timeOfDay=NIGHT), inventory())
        c = cost(snap, dirt=4)
        got, secs, steps = needs.overnight(snap.inv, c, {"soft_ground": False}, bed_too=False)
        pod = {o[0]: o[2] for o in needs.night_options(snap.inv, c, {"soft_ground": False}, False)}["wall in"]
        dirt = c.estimate(Step("mine", "minecraft:dirt", 9, {"blocks": ["dirt", "grass_block"], "breaks": 9})) / 20
        self.assertEqual(got, "dig in" if secs < dirt + pod else "wall in")
        self.assertNotIn(got, (None, "hut"))     # must fail: no way, or the dearest one

    def test_soft_below_over_the_table(self):
        from bonobo.terrain import soft_below
        from tests.world import FakeRegion
        rows = [("dirt three deep, stone under", {(0, 63, 0): "dirt", (0, 62, 0): "dirt", (0, 61, 0): "sand",
                                                 (0, 60, 0): "stone"}, True),
                ("must fail: dirt three deep over a cave: the dig would stop short",
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
            ("must fail: whole, then gone (stored in a chest, /clear): not broken", [("stone_axe", 1)], [], set()),
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
                was = needs.durability_left(bag(inventory(*before)))
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
            ("must fail: coal near the surface (y 48) is not", "minecraft:overworld", (), [_plan(("mine", "minecraft:coal"))],
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

    def test_the_ore_where_it_is_known(self):
        # search_night_resume 042911: a remembered diamond one block down read as dug to by its band (y −58): a
        # 309 s bucket plan took the body from the queued task
        snap = snapshot()
        plans = [_plan(("mine", "minecraft:diamond"))]
        self.assertTrue(needs.needs_water_bucket(snap, plans))                           # unknown: its band
        deep = needs.DEEP_Y - 1
        self.assertTrue(needs.needs_water_bucket(snap, plans, lambda st: deep))          # known deep
        # must fail: a diamond known near the surface still needs a water bucket
        self.assertFalse(needs.needs_water_bucket(snap, plans, lambda st: snap.feet[1] - 1))
        from bonobo.planner import Step
        mem = type("M", (), {"seen": lambda s, k, d: [{"pos": [5, snap.feet[1] - 1, 0]}] if k == "diamond_ore" else []})()
        step = Step("mine", "minecraft:diamond", 1, {"blocks": ["diamond_ore", "deepslate_diamond_ore"]})
        self.assertEqual(needs.known_ore_y(mem, snap, step), snap.feet[1] - 1)
        self.assertIsNone(needs.known_ore_y(mem, snap, Step("mine", "minecraft:coal", 1, {"blocks": ["coal_ore"]})))

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
    """nav.mod_features: what the running jar can do, by its version (its approach never digs: no such feature)."""

    ROWS = [("must fail: 0.1.14: nothing", "0.1.14+mc1.21.11", set()), ("0.1.15: pillar", "0.1.15", {"pillar"}),
            ("0.1.39: travel", "0.1.39+mc1.21.11", {"pillar", "travel"}),
            ("must fail: 0.1.40: no approach that digs (E1: Python names every way)", "0.1.40+mc1.21.11",
             {"pillar", "travel"}),
            ("0.1.46: eats on the way", "0.1.46+mc1.21.11", {"pillar", "travel", "autoeat"}),
            ("no version read: nothing assumed", "unknown", set())]

    def test_version_to_features(self):
        from bonobo import nav
        for name, version, want in self.ROWS:
            with self.subTest(name), mock.patch.object(nav, "_features", None), \
                    mock.patch.object(api, "game_status", return_value={"version": version}):
                self.assertEqual(nav.mod_features(), want)


class Felled(unittest.TestCase):
    """wood.felled: a sapling goes back only once the whole trunk is down (bench chop__base: a sapling under two
    logs still standing made them unreachable)."""

    TRUNK = [{"x": 3, "y": y, "z": 0} for y in (200, 201, 202)]
    BIG = [{"x": x, "y": 200, "z": z} for x in (3, 4) for z in (0, 1)]
    ROWS = [("every log of it gone", TRUNK, set(), True),
            ("must fail: the base gone, two logs overhead still standing", TRUNK, {(3, 201, 0), (3, 202, 0)}, False),
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

    # (situation, the body's holder, api.STATE.mode, a hazard due, upkeep has work) → the layer that takes the round
    ROWS = [("a fight holds the body, upkeep has work", "fight", "normal", None, True, "L0", []),
            ("a rescue runs (survival mode)", None, "survival", None, True, "L0", []),
            ("must fail: nobody holds it, a hazard is due", None, "normal", "drowning", True, "L0", []),
            ("nobody holds it, upkeep has work", None, "normal", None, True, "upkeep", ["upkeep"]),
            ("nobody holds it, nothing to do", None, "normal", None, False, None, ["upkeep"])]

    def test_order_over_the_table(self):
        from unittest import mock
        from bonobo import arbiter
        for name, holder, mode, due, busy, want, asked_want in self.ROWS:
            asked = []
            b = brain_fixture()
            b.unplannable = {}
            b.abandoned = None
            b.retry, b.place = retry.Retry(), PLACE

            def proposals(snap, ctx, reads=None, _busy=busy):
                asked.append("upkeep")
                return [(0, "u", None)] if _busy else []
            b.needs = mock.Mock(working={}, needs_now=[], round={}, propose=lambda snap, ctx, reads=None: [])
            b.reflexes = mock.Mock(proposals=proposals)
            b.task_act = lambda task, snap, ctx: None
            b.prepare = lambda snap, ctx: None
            snap = snapshot(state(), inventory())
            with self.subTest(name), mock.patch.object(api.STATE, "mode", mode), \
                    mock.patch.object(arbiter.BODY, "holder", return_value=holder), \
                    mock.patch.object(brainmod.hazard, "rescue_due", return_value=due), \
                    mock.patch.object(tasks, "load", return_value=[]), mock.patch.object(tasks, "expire", return_value=False):
                act = b.decide(snap, round_ctx(b, snap))
                self.assertEqual((act.layer if act else None, asked), (want, asked_want))


class GivenUpThenANextStep(unittest.TestCase):
    """E5: work given up is followed by what its cause asks (brain.abandon_after) — into cover the next
    round, once; "replan" leaves the round to plan again. Nothing hangs."""

    def test_rows(self):
        from unittest import mock
        # (situation, what the skill declared, the next two rounds' first act names)
        rows = [("given up, cover declared: into cover, then the round goes on", "cover",
                 ["abandoned: cover", "prepare"]),
                ("given up, replan declared: the round plans again", "replan", ["prepare", "prepare"]),
                ("must fail: nothing given up: no cover", None, ["prepare", "prepare"])]
        for name, then, want in rows:
            b = brain_fixture()
            b.unplannable, b.abandoned = {}, None
            b.retry, b.place, b.held = retry.Retry(), PLACE, {}
            b.needs = mock.Mock(working={}, needs_now=[], round={}, propose=lambda snap, ctx, reads=None: [])
            b.reflexes = mock.Mock(proposals=lambda snap, ctx, reads=None: [])
            b.prepare = lambda snap, ctx: brainmod.Act("idle", "prepare", None)
            b.abandoned = then          # brain.attempt sets it from the TaskStuck the runner raised (then=)
            snap = snapshot(state(), inventory())
            with self.subTest(name), mock.patch.object(api.STATE, "mode", "normal"), \
                    mock.patch.object(brainmod.hazard, "rescue_due", return_value=None), \
                    mock.patch.object(tasks, "load", return_value=[]), mock.patch.object(tasks, "expire", return_value=False):
                got = [b.decide(snap, round_ctx(b, snap)).name for _ in range(2)]
                self.assertEqual(got, want)

    def test_what_follows_by_cause(self):
        # E5/G3 (brain.abandon_after): a danger that stopped it → cover; not found or stuck → replan (no walk to
        # cover); a skill's own `abandon` overriding the cause
        rows = [("not found: replan", api.NotAvailable("no trees found nearby"), None, "replan"),
                ("a jar task stuck: replan", api.TaskStuck("goto: no progress"), "stuck", "replan"),
                ("must fail: lava stopped it: cover", api.Interrupted("lava"), "hazard:lava", "cover"),
                ("a threat took the body: cover", api.Interrupted("threat"), "layer:tactic", "cover"),
                ("a fight's soft skill stuck: its own cover", api.TaskStuck("slay: no progress", then="cover"),
                 "stuck", "cover")]
        for name, err, source, want in rows:
            with self.subTest(name):
                self.assertEqual(brainmod.abandon_after(err, source), want)

    def test_a_skill_with_no_next_step_is_refused(self):
        # must fail: an abandon outside skill.ABANDON_WAYS is refused where it is declared
        with self.assertRaises(TypeError):
            skillkit.skill(name="e5_no_next_step", needs={}, gives=[], budget=10, abandon="wander")(lambda ctx: None)


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
            ("must fail: the table still there: nothing added", [("oak_planks", 8), ("stick", 2)],
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
            with mock.patch.object(craft, "close_screen"), mock.patch.object(craft, "find", return_value=[]), \
                    mock.patch.object(craft, "Inventory", return_value=bag(inventory())), \
                    mock.patch.object(craft, "feet", return_value=(0, 64, 0)), \
                    self.assertRaises(skillcore.StationMissing) as got:
                with craft.Station(ctx, "minecraft:crafting_table"):
                    pass
            self.assertEqual((m.stations(OVER), retry.cause_of(got.exception)), ([], "replan"))


class RepairsThatDoNotHelp(unittest.TestCase):
    """retry: a replan (a station gone) is not a failure — once. The same task replanned again with nothing done in
    between is a failure that cools: plan_repair_on_event replanned the same plan every second, forever."""

    # (situation, [events: "replan" | "ok"]) → (the last verdict is a failure, the task cooling after)
    ROWS = [("must fail: one replan: not counted, not cooling", ["replan"], (False, False)),
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
    ROWS = [("must fail: full stomach, no known food: not yet", 900.0, 400.0, False, False, False),
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
                 (planner.Step("hunt", "minecraft:beef", 2, {"types": ["minecraft:cow"]}), {}, False),  # must fail: no cows seen, not a known source
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
                ("must fail: a log chop needs none", [planner.Step("gather", "log", 3, {})], set()),
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
    """Raw meat carried is food first: smelt it (seconds) before hunting (minutes) — by price."""

    def test_the_plan(self):
        rows = [("2 raw beef, coal, a furnace near, want 2: smelt them", [("beef", 2), ("coal", 4)], 2, {},
                 [("smelt", "minecraft:cooked_beef", 2)]),
                ("2 raw beef, want 8, pigs 30 away: smelt the beef, hunt the rest",
                 [("beef", 2), ("coal", 4)], 8, {"pig": 30},
                 [("smelt", "minecraft:cooked_beef", 2), ("hunt", "minecraft:porkchop", 6),
                  ("smelt", "minecraft:cooked_porkchop", 6)]),
                ("must fail: 2 raw beef, want 8, cows 30 away: the carried beef smelted before the hunt, not merged "
                 "into one smelt the hunt must feed (night_first__low 055858)",
                 [("beef", 2), ("coal", 4)], 8, {"cow": 30},
                 [("smelt", "minecraft:cooked_beef", 2), ("hunt", "minecraft:beef", 6),
                  ("smelt", "minecraft:cooked_beef", 6)]),
                ("nothing raw, pigs 30 away: hunt", [("coal", 4)], 2, {"pig": 30},
                 [("hunt", "minecraft:porkchop", 2), ("smelt", "minecraft:cooked_porkchop", 2)]),
                ("must fail: 8 cooked carried: nothing", [("cooked_beef", 8)], 8, {}, [])]
        for name, carried, n, seen, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                m.add_station("minecraft:furnace", (1, 64, 1), OVER)
                snap = snapshot(state(food=6), inventory(*carried))
                steps = decompose.decompose(snap.inv, goals.have(("food", n)), cost(snap, mem=m, **seen))
                self.assertEqual([(st.kind, st.token, st.count) for st in steps], want)


class TheToolTheBagMakes(unittest.TestCase):
    """A plan that needs a pickaxe makes the one that finishes soonest (each tier an option, priced): a worn-out iron
    pickaxe and three ingots make an iron one (one craft), not a wooden one (logs to fetch)."""

    # (situation, what else is carried) → the plan for 2 coal (coal ore in sight; any pickaxe mines it)
    ROWS = [("worn iron pickaxe, 3 iron, sticks, a table: iron", [("iron_ingot", 3)],
             [("craft", "minecraft:iron_pickaxe"), ("mine", "coal")]),
            ("no iron, 3 cobblestone: stone", [("cobblestone", 3)],
             [("craft", "minecraft:stone_pickaxe"), ("mine", "coal")]),
            ("no iron, no stone, planks: wood", [("oak_planks", 3)],
             [("craft", "minecraft:wooden_pickaxe"), ("mine", "coal")]),
            ("must fail: a working stone pickaxe: none made", [slot("stone_pickaxe", 1, 0)], [("mine", "coal")])]

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
                self.assertEqual(planner.craftable_tier(inv, "pickaxe"), tier)


class ScarceToTheMostUsedTool(unittest.TestCase):
    """R1/G5: the tool that finishes soonest; at equal seconds the lowest tier (then play.toml tools.use_order); what
    the held plans reserve (bag.RESERVED) is never a tool's material."""

    def plan(self, goal, *carried, reserved=()):
        from bonobo.cost import Cost
        with tempfile.TemporaryDirectory() as tmp:
            snap = snapshot(state(), inventory(("stick", 4), ("crafting_table", 1), *carried))
            c = Cost(snap, Memory(os.path.join(tmp, "n.json")), reserved=reserved)
            return [st.token for st in decompose.decompose(snap.inv, goal, c) if st.kind == "craft"]

    def test_rows(self):
        from bonobo import goals as g
        iron = "minecraft:iron_ingot"
        rows = [("must fail: 3 diamonds, no diamond pickaxe: the tier-1 sword is not diamond",
                 g.have(("tool", "sword", 1)), [("diamond", 3), ("cobblestone", 8)], (), "minecraft:stone_sword"),
                ("iron pickaxe held, 3 iron: an iron sword, the diamonds kept for the pickaxe",
                 g.have(("tool", "sword", 1)), [("diamond", 3), ("iron_ingot", 3), slot("iron_pickaxe")], (),
                 "minecraft:iron_sword"),
                ("the iron pickaxe broken, 3 iron and cobble, nothing reserved: one craft either way, the lowest tier "
                 "(stone); the iron kept (tool_tier__one_use)",
                 g.have(("tool", "pickaxe", 1)), [slot("iron_pickaxe", 1, 250), ("iron_ingot", 3), ("cobblestone", 8)],
                 (), "minecraft:stone_pickaxe"),
                ("must fail: iron reserved for a bucket: the pickaxe stone",
                 g.have(("tool", "pickaxe", 1)), [slot("iron_pickaxe", 1, 250), ("iron_ingot", 3), ("cobblestone", 8)],
                 {iron}, "minecraft:stone_pickaxe")]
        for name, goal, carried, reserved, want in rows:
            with self.subTest(name):
                self.assertEqual(self.plan(goal, *carried, reserved=reserved)[-1:], [want])


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
            # single mines, the top of the column first (nav.mine_order)
            return [{"type": "mine", "x": c[0], "y": c[1], "z": c[2], "collect": False, "requireDrops": False}
                    for c in sorted(cells, key=lambda c: -c[1])]
        from bonobo import nav

        def batch(*cells):
            # the closing pickup every batch has (nav.batch_sweep: its idle SWEEP_IDLE); must fail: the jar's 20
            return many(*cells) + [nav.batch_sweep(nav.mine_order(cells), ["log"])]
        base = (3, 64, 0)
        up = [(3, 65, 0), (3, 66, 0), (3, 67, 0)]
        rows = [("four wanted, three overhead: one batch of all of it, no walk in", up, 4, batch(base, *up)),
                ("two wanted: the base and the log over it", up, 2, batch(base, up[0])),
                ("one wanted: the base only", up, 1, batch(base)),
                ("a stump (nothing overhead)", [], 5, batch(base)),
                ("must fail: a log past reach (5 up and more): not in the batch", up + [(3, 69, 0), (3, 70, 0)], 9,
                 batch(base, *up))]
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
                ("must fail: not a craft: itself alone", [log, planks], log, [log]),
                ("a lone craft", [mine, pick], pick, [pick])]
        for name, steps, first, want in rows:
            with self.subTest(name):
                self.assertEqual(brainmod.craft_run(steps, first), want)

    def test_a_craft_waiting_on_a_later_step_stays_out(self):
        # hello2 10:34:41: "craft 4× planks" batched a torch whose coal a later mine step brings — "missing 6× coal"
        S = planner.Step
        planks = S("craft", "planks", 4, {"times": 1, "inputs": {"log": 1}})
        sticks = S("craft", "minecraft:stick", 4, {"times": 1, "inputs": {"planks": 2}})
        torch = S("craft", "minecraft:torch", 24, {"times": 6, "inputs": {"minecraft:coal": 6, "minecraft:stick": 6}})
        mine = S("mine", "minecraft:coal", 6, {"tier": 0})
        held = bag(inventory(("oak_log", 1), ("stick", 2)))
        # must fail: the torch joins the planks' sitting before its coal is mined
        self.assertEqual(brainmod.craft_run([planks, sticks, torch, mine], planks, held), [planks, sticks])
        coal = bag(inventory(("oak_log", 1), ("stick", 2), ("coal", 6)))
        self.assertEqual(brainmod.craft_run([planks, sticks, torch, mine], planks, coal), [planks, sticks, torch])

    def test_the_table_stays_for_a_later_table_craft(self):
        S = planner.Step
        planks, table, pick = (S("craft", "planks", 8, {"times": 2}), S("craft", "minecraft:crafting_table", 1, {}),
                               S("craft", "minecraft:wooden_pickaxe", 1, {}))
        mine, stone_pick = S("mine", "stone", 3, {"tier": 0}), S("craft", "minecraft:stone_pickaxe", 1, {})
        sticks = S("craft", "minecraft:stick", 4, {})
        # (situation, plan, the run) → the table left standing
        rows = [("a stone pickaxe after the mine: kept", [planks, table, pick, mine, stone_pick], [planks, table, pick],
                 True),
                ("must fail: nothing at a table after: taken back", [planks, table, pick, mine],
                 [planks, table, pick], False),
                ("a 2×2 craft after is no reason", [planks, table, pick, mine, sticks], [planks, table, pick], False)]
        for name, steps, run, want in rows:
            with self.subTest(name):
                self.assertEqual(brainmod.keeps_table(steps, run), want)

    def test_upkeep_crafts_at_one_sitting(self):
        """need_act goes through craft_act too: the crafts in a row are one act (they were one per round)."""
        S = planner.Step
        planks, sticks, table, pick = (S("craft", "planks", 8, {"times": 2}), S("craft", "minecraft:stick", 4, {}),
                                       S("craft", "minecraft:crafting_table", 1, {}),
                                       S("craft", "minecraft:wooden_pickaxe", 1, {}))
        act = brainmod.craft_act("upkeep", "idle: x", None, [planks, sticks, table, pick], planks, False)
        self.assertEqual(act.steps, [planks, sticks, table, pick], "must fail: one craft a round")


class Reflexes(unittest.TestCase):
    """reflexes.TABLE: each trigger over the round's view — fires, and does not."""

    BASE = {"died_recently": False, "food": 20, "meal": False, "nether_bad": False,
            "night": False, "enclosed": False, "sheltered": False, "bed_works": False, "bed_carried": False,
            "bed_near": False, "shelter_ready": False, "job_ready": False, "machine_ready": False, "used_slots": 10,
            "blocked": False, "building": 0, "stuck": False, "in_pit": False}
    # (reflex, the view's changes that fire it, the changes that do not) (must fail: the third column never fires)
    ROWS = [("eat", {"food": 10}, {"food": 10, "meal": None}),
            ("leave the Nether", {"nether_bad": True}, {}),
            ("dig out", {"enclosed": True}, {"enclosed": True, "night": True}),
            ("sleep", {"night": True, "bed_works": True, "bed_carried": True},
             {"night": True, "bed_works": False, "bed_carried": True}),
            ("shelter", {"shelter_ready": True}, {}),
            # must fail: under the open night sky they wait (the shelter first, S4)
            ("recover items", {"died_recently": True}, {"died_recently": True, "night": True}),
            ("leave the pit", {"in_pit": True}, {"in_pit": True, "night": True}),
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

    # (which reflexes are cooling) → what is due, when both eat and leave the Nether fire
    EAT, OUT = (__import__("bonobo.reflexes", fromlist=["NAMES"]).NAMES.index(n) for n in ("eat", "leave the Nether"))
    COOLING = [("none cooling: both, in table order", set(), [(EAT, "eat"), (OUT, "leave the Nether")]),
               ("eat cooling: skipped, leave the Nether still due", {"eat"}, [(OUT, "leave the Nether")]),
               ("leave the Nether cooling: eat alone", {"leave the Nether"}, [(EAT, "eat")]),
               ("must fail: both cooling: nothing due though both fire", {"eat", "leave the Nether"}, [])]

    # (situation, the view's changes) → eat fires (hungry, a meal carried); the night's shelter due first
    EAT_AT_NIGHT = [("hungry by day: eat", {}, True),
                    ("must fail: hungry, the night's shelter due: cover first, the meal after", {"night": True,
                                                                                               "shelter_ready": True},
                     False),
                    ("must fail: hungry, a bed carried that works tonight: sleep first", {"night": True,
                                                                                         "bed_works": True,
                                                                                         "bed_carried": True}, False),
                    ("hungry at night under cover (no shelter due): eat", {"night": True, "sheltered": True}, True)]

    def test_a_meal_waits_for_the_shelter(self):
        from bonobo import reflexes
        for name, changes, want in self.EAT_AT_NIGHT:
            with self.subTest(name):
                due = [n for _i, n in reflexes.due(dict(self.BASE, food=reflexes.EAT_BELOW - 1, **changes))]
                self.assertEqual("eat" in due, want)

    # (situation, the view's changes) → recover items and leave the pit fire (the night gate: open_night, S4)
    NIGHT_GATE = [("day: both", {}, True),
                  ("night under the open sky: both wait", {"night": True}, False),
                  ("must fail: night under rock or walled in: a step from here is under cover, both",
                   {"night": True, "sheltered": True}, True)]

    def test_the_night_gate_reads_the_sky(self):
        from bonobo import reflexes
        for name, changes, want in self.NIGHT_GATE:
            with self.subTest(name):
                due = [n for _i, n in reflexes.due(dict(self.BASE, died_recently=True, in_pit=True, **changes))]
                self.assertEqual(("recover items" in due, "leave the pit" in due), (want, want))

    def test_cooling_reflexes_are_skipped(self):
        from bonobo import reflexes
        view = dict(self.BASE, food=10, nether_bad=True)
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
            ("must fail: night outside, bed makings: a bed needed, no shelter", dict(
                time_of_day=NIGHT, inv=[("cooked_beef", 8), ("white_wool", 3), ("oak_planks", 3),
                                        ("crafting_table", 1)])),
            ("dusk, wool and no planks: the bed's parts needed", dict(time_of_day=DUSK,
                                                                     inv=[("cooked_beef", 8), ("white_wool", 3)])),
            ("hungry with bread by day: eat, nothing needed", dict(food=10, inv=[("bread", 4), ("white_bed", 1)]))]

    def run_order(self, row, needs_first):
        with tempfile.TemporaryDirectory() as tmp:
            b = brain_fixture()
            b.unplannable = {}
            b.abandoned = None
            b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
            b.mem, b.retry, b.blacklist, b.place, b.held = Memory(os.path.join(tmp, "n.json")), retry.Retry(), {}, \
                PLACE, {}
            b.needs, b.reflexes = needs.Needs(b), reflexes.Maintain(b)
            snap = snapshot(row.state, row.inv)
            c = cost(snap, **row.seen)
            b.needs.cost = lambda _snap: c
            reads = {"enclosed": False, "bed_near": False, "soft_ground": False, "in_pit": False}
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
                ("must fail: pricing every round — three rounds, one bag: once", [WELL_FED] * 3, 1),
                ("back and forth: each new bag once", [WELL_FED, [("stick", 1)], WELL_FED], 2)]
        for name, bags, want in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                b = brain_fixture()
                b.unplannable = {}
                b.abandoned = None
                b.mem = Memory(os.path.join(tmp, "notes.json"))
                table, asked = needs.Needs(b), []
                snap0 = snapshot(state(), inventory(*bags[0]))
                c = cost(snap0)
                table.cost = lambda snap: asked.append(1) or c
                for items in bags:
                    table.overnight(snapshot(state(), inventory(*items)))
                self.assertEqual(len(asked), want)

    def test_the_night_is_one_of_in_one_plan(self):
        """The night's ways are one one-of target of a single plan_round (planner.Target.options), the way taken the
        cheapest whole plan (must fail: the ways priced one plan each)."""
        calls = []
        real = needs.plan_round

        def spy(*a, **k):
            calls.append(1)
            return real(*a, **k)
        rows = [("a pickaxe: dig in", [("stone_pickaxe", 1)], {"cow": 8}, "dig in"),
                ("a bed carried: the bed", [("white_bed", 1)], {}, "bed"),
                ("wool, planks, a table: the bed made", [("white_wool", 3), ("oak_planks", 3), ("crafting_table", 1)],
                 {}, "bed")]
        for name, items, seen, want in rows:
            with self.subTest(name), mock.patch.object(needs, "plan_round", spy):
                calls.clear()
                snap = snapshot(state(), inventory(*items))
                way, _secs, _steps = needs.overnight(snap.inv, cost(snap, **seen))
                self.assertEqual((way, len(calls)), (want, 1))


class RawOnlyWhenStarving(unittest.TestCase):
    """reflexes.meal / can_cook: raw meat is eaten only when starving or when it cannot be cooked — else the plan
    cooks it (night_first__low ate both raw beef at 8 and had nothing left to cook)."""

    def test_over_the_table(self):
        rows = [("must fail: food 8, raw beef, a furnace near and coal: not eaten raw (cooked by the plan)", 8,
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
                ("must fail: nothing anywhere: None", {}, {}, ["diamond_ore"], 48, None)]
        for name, noted, sight, blocks, radius, want in rows:
            with self.subTest(name), mock.patch.object(api, "api", side_effect=AssertionError("an estimate read the world")):
                snap, m = snapshot(state(gameTime=1000), inventory(), **sight), memory()
                m.clock = snap.state["gameTime"]            # the round's clock, as brain sets it from the snapshot
                for k, d in noted.items():
                    m.note_seen(k, (int(d), 64, 0), OVER)
                c = Cost(snap, m)
                self.assertEqual(c.distance(blocks, radius), want)

class BridgeStockSizedToTheGap(unittest.TestCase):
    """needs.bridge_stock: one block per block across, between BRIDGE_MIN and BRIDGE_STOCK."""

    def test_over_the_table(self):
        rows = [("a 9-block way across: 9", (0, 64, 0), (9, 64, 0), 9),
                ("must fail: the boundary a plain count gets wrong — a step across: at least BRIDGE_MIN", (0, 64, 0), (2, 64, 0), reflexes.BRIDGE_MIN),
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
                ("must fail: every kind noted: nothing asked", [("diamond_ore", (4, 60, 0)), ("obsidian", (5, 60, 0))],
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
                ("must fail: mined: air", {}, ["diamond_ore"], False),
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
                ("must fail: the deepslate form noted: neither asked", [("deepslate_diamond_ore", (4, 0, 0))],
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
    """A tool the work uses is an option of the plan, made when the whole plan is cheaper with it — its saving on that
    work (knowledge.work_s over break_ticks / kill_s) against making it; the same work may fetch its materials."""

    KIT = [("oak_planks", 8), ("stick", 4), ("crafting_table", 1)]

    def test_over_the_table(self):
        from bonobo.planner import NullCost, plan_needs
        rows = [("12 logs, planks and sticks carried: an axe first", self.KIT, [("log", 12)], "minecraft:wooden_axe"),
                ("must fail: 2 logs: saves less than the axe costs, none", self.KIT, [("log", 2)], None),
                ("12 logs, an axe held: none made", self.KIT + [slot("wooden_axe", 1, 9)], [("log", 12)], None),
                ("must fail: 8 beef (4 kills): a sword saves less than making it", self.KIT, [("minecraft:beef", 8)],
                 None),
                ("40 beef (20 kills): a sword saves more than making it: first", self.KIT, [("minecraft:beef", 40)],
                 "minecraft:wooden_sword")]
        for name, carried, needs_, want in rows:
            with self.subTest(name):
                steps = plan_needs(bag(inventory(*carried)), needs_, NullCost())
                made = [s.token for s in steps if s.kind == "craft" and s.token.endswith(("_axe", "_sword"))]
                kind = (lambda t: t.rpartition("_")[2] if t else None)
                self.assertEqual(kind(made[0] if made else None), kind(want), [str(s) for s in steps])  # its tier by price
                if want:
                    work = next(i for i, s in enumerate(steps) if s.kind in ("gather", "hunt"))
                    self.assertLess(steps.index(next(s for s in steps if s.token == made[0])), work)

    def test_the_same_work_may_fetch_the_tools_wood(self):
        """No rule against an axe whose logs the gather fetches: the plan takes it exactly when it is cheaper —
        never dearer than the plan with the axe forced, nor than the logs by hand (each priced by the same model)."""
        from bonobo.planner import NullCost, plan_needs
        empty = bag(inventory())
        chosen = sum(s.est for s in plan_needs(empty, [("log", 12)], NullCost()))
        forced = sum(s.est for s in plan_needs(empty, [("tool", "axe", 0), ("log", 12)], NullCost()))
        self.assertLessEqual(chosen, forced)


class TheRoundsPick(unittest.TestCase):
    """brain.round_for / round_act: the queue's goals and upkeep's needs in the round's one plan (planner.plan_round):
    the order of least total seconds, the queue's place at equal totals; a plan whose clock runs the bar out is not
    the one taken."""

    def first(self, entries, snap, seen):
        q = Held(goals.have(("log", 1)), seen=seen)
        held = q.b.round_for(entries, snap, cost(snap, mem=q.b.mem, **seen))
        act = q.b.round_act(held["steps"], snap, round_ctx(q.b, snap)) if held is not None else None
        return None if act is None else (act.step.kind, act.step.token)

    def test_rows(self):
        carried = snapshot(state(), inventory(("oak_log", 1), ("oak_planks", 2)))
        sticks, planks, logs = goals.have(("minecraft:stick", 4)), goals.have(("planks", 4)), goals.have(("log", 6))
        hungry = snapshot(state(food=3), inventory())
        seen = {"cow": 8, "oak_log": 6, "stone": 2}
        # (situation, snapshot, [(name, goal, queue place)], the round plan's first step)
        rows = [("equal seconds: the queue's first place", carried, [("a", sticks, 0), ("b", planks, 1)],
                 ("craft", "minecraft:stick")),
                ("equal seconds, the places swapped: the other", carried, [("a", sticks, 1), ("b", planks, 0)],
                 ("craft", "planks")),
                ("unequal seconds, the same total either way (G3): the queue's place, not the cheaper first", carried,
                 [("a", logs, 0), ("b", sticks, 1)], ("gather", "log")),
                ("must fail: a plan that starves on the way: food first", hungry,
                 [("a", goals.have(("tool", "pickaxe", 2)), 0)], ("hunt", "minecraft:beef"))]
        for name, snap, entries, want in rows:
            with self.subTest(name):
                self.assertEqual(self.first(entries, snap, seen), want)

    def test_one_plan_a_round(self):
        """The round plans once, however many targets (planner.plan_round), and not at all again while nothing it
        was made from changed (must fail: a plan per target; the same world planned again)."""
        from bonobo import decompose as dec
        counted = []

        def spy(fn):
            def wrapped(*a, **k):
                counted.append(fn.__name__)
                return fn(*a, **k)
            return wrapped
        goals_ = [goals.have((item, 1)) for item in ("log", "minecraft:stick", "planks", "minecraft:crafting_table",
                                                       "minecraft:torch", "minecraft:chest")]

        def calls(n):
            with tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q, \
                    mock.patch.object(planner, "plan_round", spy(planner.plan_round)), \
                    mock.patch.object(dec, "solve_needs", spy(dec.solve_needs)):
                for g in goals_[:n]:
                    q.task(g)
                snap = snapshot(state(), inventory())
                counted.clear()
                q.b.plan_proposals(snap, round_ctx(q.b, snap))
                first = len(counted)
                counted.clear()
                q.b.plan_proposals(snap, round_ctx(q.b, snap))
                return first, len(counted)
        self.assertEqual(calls(2), (1, 0))
        self.assertEqual(calls(6), (1, 0))

    def test_the_order_is_the_least_total(self):
        """G3: the round's plan costs no more than its targets in any other place order; equal totals keep the
        queue's place (must fail: an order dearer in total than another taken)."""
        import itertools
        from bonobo.planner import NullCost, Target, plan_round
        carried = bag(inventory(("oak_log", 1), ("oak_planks", 2)))
        needs_ = [("logs", [("log", 6)]), ("sticks", [("minecraft:stick", 4)]), ("planks", [("planks", 4)])]
        chosen = plan_round(carried, [Target(n, x, i) for i, (n, x) in enumerate(needs_)], NullCost())[2]
        for order in itertools.permutations(needs_):
            with self.subTest([n for n, _x in order]):
                other = plan_round(carried, [Target(n, x, i) for i, (n, x) in enumerate(order)], NullCost())[2]
                self.assertLessEqual(chosen, other + 1e-9)


class WhatTheFurnaceHolds(unittest.TestCase):
    """craft.after_take: memory's furnace job after a take — over when nothing cooks, else what it still holds."""

    def test_over_the_table(self):
        job = {"id": "furnace-1", "item": "minecraft:cooked_beef", "count": 4, "input": "minecraft:beef",
               "input_count": 4}
        rows = [("must fail: all taken, nothing left cooking: over", 4, 0, None, None),
                ("2 taken, 2 still in the input: holds 2, ready in 25 s", 2, 2, 1000, {"count": 2, "input_count": 2,
                                                                                    "ready_at": 25.0,
                                                                                    "ready_tick": 1000 + 420}),
                ("nothing out yet, all 4 cooking", 0, 4, None, {"count": 4, "input_count": 4, "ready_at": 45.0}),
                ("more taken than listed: never below 0", 6, 1, None, {"count": 0, "input_count": 1, "ready_at": 15.0})]
        for name, got, cooking, tick, want in rows:
            with self.subTest(name):
                self.assertEqual(craft.after_take(job, got, cooking, 0.0, tick), want)
