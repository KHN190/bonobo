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
the real `upkeep.Upkeep` run by a real (unstarted) Brain, its three world reads answered from the row
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
from bonobo import api, decompose, goals, planner, retry, skillcore, skills, tasks, upkeep  # noqa: E402
from bonobo import brain as brainmod  # noqa: E402  (imports every skill module: `handles` needs the registry)
from bonobo import skill as skillkit  # noqa: E402
from bonobo.data import bare  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import Unplannable  # noqa: E402
from tests.world import (PLANNER_DIMS, bag, cost, full_bag, inventory, slot, snapshot, state,  # noqa: E402
                         worlds)

OVER, NETHER = "minecraft:overworld", "minecraft:the_nether"


def has(steps, kind, token):
    return any(s.kind == kind and bare(s.token) == bare(token) for s in steps)


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
]


class Plans(unittest.TestCase):
    def test_goal_to_steps_over_the_sweep(self):
        for goal, where, contains, omits in PLANS:
            for w in worlds(**{k: v for k, v in where.items()}) if where else worlds():
                snap = w.snapshot()
                with self.subTest(goal=goals.describe(goal), world=w):
                    steps = decompose.decompose(snap.inv, goal, w.cost())
                    for kind, token in contains:
                        self.assertTrue(has(steps, kind, token), f"no {kind} {token} in {list(map(str, steps))}")
                    if omits == ANY:
                        self.assertEqual(steps, [], "the goal is met: no action")
                        continue
                    for kind, token in omits:
                        self.assertFalse(has(steps, kind, token), f"{kind} {token} planned: {list(map(str, steps))}")
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
                made = set()
                for s in steps:
                    for tok in s.detail.get("inputs", {}):
                        self.assertTrue(bare(tok) in made or snap.inv.count(tok) > 0,
                                        f"{s}: {tok} neither held nor made before it")
                    made.add(bare(s.token))

    def test_unplannable(self):
        for name, goal in UNPLANNABLE:
            with self.subTest(name), self.assertRaises((Unplannable, ValueError)):
                plan(goal, snapshot())

    def test_the_sweep_is_the_whole_product(self):
        n = sum(1 for _ in worlds())
        from tests.world import DIMS
        expected = 1
        for d in PLANNER_DIMS:
            expected *= len(DIMS[d])
        self.assertEqual(n, expected)


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
                self.assertTrue(has(steps, *chosen), list(map(str, steps)))
                if not_chosen:
                    self.assertFalse(has(steps, *not_chosen), list(map(str, steps)))

    def test_nearer_is_never_dearer(self):
        """The same plan with the trees nearer costs no more (the walk is priced, the work is the same)."""
        for goal in (PICK1, goals.have(("log", 12))):
            with self.subTest(goals.describe(goal)):
                secs = [cost(snapshot(), oak_log=d, stone=3).plan_s(plan(goal, snapshot(), {"oak_log": d, "stone": 3}))
                        for d in (5, 20, 45)]
                self.assertEqual(secs, sorted(secs))

    def test_preferred_provider_first(self):
        """Several skills provide one effect: `prefer` orders them, for every effect the registry knows."""
        effects = {e for c in skillkit.REGISTRY.values() for e in c.provides}
        for effect in sorted(effects):
            with self.subTest(effect):
                prefs = [c.prefer for c in skillkit.providers(effect)]
                self.assertEqual(prefs, sorted(prefs, reverse=True))


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
     lambda t, before, after: (t.assertTrue(has(before, "craft", "minecraft:crafting_table")),
                               t.assertFalse(has(after, "craft", "minecraft:crafting_table")),
                               t.assertFalse(has(after, "craft", "minecraft:wooden_pickaxe")))),
    ("the pickaxe wore down to 1 mid-plan", IRON3, stone_tools(), stone_tools(worn=130), None,
     lambda t, before, after: (t.assertFalse(has(before, "craft", "minecraft:stone_pickaxe")),
                               t.assertTrue(has(after, "craft", "minecraft:stone_pickaxe")))),
    ("the ingots are already cooking in a furnace", IRON3, stone_tools(), stone_tools(),
     {"minecraft:iron_ingot": 3}, lambda t, before, after: t.assertEqual(after, [])),
    ("half the ore already mined", IRON3, stone_tools(), inventory(*stone_tools()["slots"], ("raw_iron", 2)), None,
     lambda t, before, after: t.assertEqual([s.count for s in after if s.kind == "mine" and "iron" in s.token], [1])),
    ("goal met while the plan was held", IRON3, stone_tools(), inventory(("iron_ingot", 3)), None,
     lambda t, before, after: t.assertEqual(after, [])),
    ("died: the bag is empty again", IRON3, stone_tools(), inventory(), None,
     lambda t, before, after: (t.assertTrue(has(after, "gather", "log")),
                               t.assertGreater(len(after), len(before)))),
]


class Repairs(unittest.TestCase):
    def test_event_to_repaired_plan(self):
        seen = {"oak_log": 8, "stone": 2, "iron_ore": 12, "coal_ore": 9}
        for name, goal, inv_before, inv_after, pending, check in REPAIRS:
            with self.subTest(name):
                before = plan(goal, snapshot(inv=inv_before), seen)
                after = plan(goal, snapshot(inv=inv_after), seen, pending=pending)
                check(self, before, after)

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
        for goal in (goals.make("road", a=[0, 64, 0], b=[9, 64, 0]), goals.make("skill", name="chop", args=[1])):
            with self.subTest(goal["goal"]):
                self.assertIn(goal["goal"], goals.RUN_ONCE)
                self.assertIsNone(goals.done(goal, snap, None))


# -------------------------------------------------------------------------------------------------------- upkeep
PLACE = retry.place_signature((0, 64, 0), False)
DAY, DUSK, NIGHT = 2000, 11500, 18000
WELL_FED = [("cooked_beef", 8), ("white_bed", 1), ("stone_pickaxe", 1)]
HERD = {"cow": 12, "sheep": 20, "oak_log": 10, "stone": 2}


class Row:
    """One upkeep situation: readings, the few world facts the table reads, and what it must conclude."""

    def __init__(self, name, row, queued=(), time_of_day=DAY, inv=WELL_FED, seen=None, enclosed=False,
                 bed_seen=False, last_round=None, blocked=None, stuck=False, died=False, cooling=(), place=None,
                 **st):
        # `queued`: the exact set of goals put in front, or a check(set) for rows whose tier is the planner's call
        self.name, self.row = name, row
        self.queued = queued if callable(queued) else {tuple(map(tuple, q)) for q in queued}
        self.place = place or PLACE
        self.state = state(timeOfDay=time_of_day, **st)
        self.inv = inventory(*inv) if isinstance(inv, list) else inv
        self.seen = HERD if seen is None else seen
        self.enclosed, self.bed_seen, self.last_round = enclosed, bed_seen, last_round
        self.blocked, self.stuck, self.died, self.cooling = blocked, stuck, died, cooling


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
    Row("no working pickaxe: one to the front", None, queued=[[("tool", "pickaxe", 0)]],
        inv=[("cooked_beef", 8), ("white_bed", 1)]),
    Row("the iron pickaxe broke, iron to make another: the same tier back", None,
        queued=lambda q: (("tool", "pickaxe", 2),) in q,
        inv=[("cooked_beef", 8), ("white_bed", 1), slot("iron_pickaxe", 1, 249), ("iron_ingot", 3), ("stick", 2),
             ("crafting_table", 1)],
        last_round=[("cooked_beef", 8), ("white_bed", 1), ("iron_pickaxe", 1)]),
    Row("the iron pickaxe broke, nothing to make one of: a pickaxe of any tier up to it", None,
        queued=lambda q: any(n[0][:2] == ("tool", "pickaxe") and n[0][2] <= 2 for n in q),
        inv=[("cooked_beef", 8), ("white_bed", 1), slot("iron_pickaxe", 1, 249)],
        last_round=[("cooked_beef", 8), ("white_bed", 1), ("iron_pickaxe", 1)]),
    Row("the stone sword broke: a sword back", None,
        queued=lambda q: any(n[0][:2] == ("tool", "sword") and 0 <= n[0][2] <= 1 for n in q),
        inv=WELL_FED, last_round=WELL_FED + [("stone_sword", 1)]),
    Row("a tool that never worked is not broken", None, inv=WELL_FED, last_round=WELL_FED),
    Row("dusk in 25 s, no bed, sheep 20 away: a bed to the front (LEAD)", None, queued=[[("bed", 1)]],
        time_of_day=DUSK, inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
    Row("dusk in 25 s, wool and planks carried: a bed is seconds away", None, time_of_day=DUSK,
        inv=[("cooked_beef", 8), ("stone_pickaxe", 1), ("white_wool", 3), ("oak_planks", 3), ("crafting_table", 1)]),
    Row("morning, no bed, sheep near: plenty of day left", None, time_of_day=1000,
        inv=[("cooked_beef", 8), ("stone_pickaxe", 1)]),
    Row("starving slowly, cows far away: food to the front (LEAD)", None, queued=[[("food", 8)]], food=3,
        inv=[("white_bed", 1), ("stone_pickaxe", 1)], seen={"cow": 45, "oak_log": 10, "stone": 2}),
    Row("full stomach, no meals, cows near: no hurry", None, food=20, inv=[("white_bed", 1), ("stone_pickaxe", 1)]),
]


def run_upkeep(row, tmp):
    """The real upkeep table, one round, on a real unstarted Brain. Returns (row name, [queued needs])."""
    b = brainmod.Brain.__new__(brainmod.Brain)
    b.mem = Memory(os.path.join(tmp, "notes.json"))
    b.retry, b.blacklist = retry.Retry(), {}
    b.place = PLACE
    b.table = table = upkeep.Upkeep(b)
    now = time.time()
    for name in row.cooling:
        b.retry.failed(name, "error", "failed here", now, PLACE)
    if row.died:
        b.mem.log_death((6, 64, 0), row.state["dimension"])
    snap = snapshot(row.state, row.inv)
    if row.last_round is not None:
        table.observe(snapshot(row.state, inventory(*row.last_round)))
    table.observe(snap)
    if row.stuck:
        sig = upkeep.bag_signature(snap.inv)
        table.history = [(now - 90 + i * 10, snap.feet, sig) for i in range(10)]
    if row.blocked is not None:
        table.failed("nav", api.NavFailed("no path found", pos=row.blocked), row.place)
    c = cost(snap, **row.seen)
    for goal in (goals.have(("food", 8)), goals.have(("bed", 1))):
        try:
            secs = c.plan_s(decompose.decompose(snap.inv, goal, c))
        except Unplannable:
            secs = float("inf")
        table.plan_s_cache[(json.dumps(goal, sort_keys=True), upkeep.bag_signature(snap.inv))] = (now + 60, secs)
    bed_hits = [{"block": "minecraft:red_bed", "x": 5, "y": 64, "z": 0, "distance": 5.0}] if row.bed_seen else []
    with mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")), \
            mock.patch.object(skills, "enclosed", return_value=row.enclosed), \
            mock.patch.object(upkeep, "find", return_value=bed_hits), \
            mock.patch.object(api, "api", side_effect=AssertionError("upkeep read the world beyond the row")):
        got = table.act(snap, ctx=None)
        queued = [tuple(tuple(n) for n in t["args"]["needs"]) for t in tasks.load() if t["state"] in tasks.LIVE]
    return (got[0] if got else None), queued


class Upkeep(unittest.TestCase):
    def test_state_to_choice(self):
        for row in UPKEEP:
            with self.subTest(row.name), tempfile.TemporaryDirectory() as tmp:
                chosen, queued = run_upkeep(row, tmp)
                self.assertEqual(chosen, row.row)
                if callable(row.queued):
                    self.assertTrue(row.queued(set(queued)), f"queued {queued}")
                else:
                    self.assertEqual(set(queued), row.queued)

    def test_lead_is_one_number(self):
        self.assertEqual(upkeep.LEAD, 1.5)
        self.assertGreater(upkeep.LEAD, 1.0, "upkeep starts before the plan's own time, never after")

    def test_dusk_clock(self):
        for tod, secs in ((0, 600.0), (6000, 300.0), (11500, 25.0), (12000, 0.0), (18000, 0.0), (24000 + 6000, 300.0)):
            with self.subTest(timeOfDay=tod):
                self.assertEqual(upkeep.dusk_s(snapshot(state(timeOfDay=tod))), secs)

    def test_food_clock_grows_with_food(self):
        ladder = [upkeep.food_lasts_s(snapshot(state(food=f), inventory(("cooked_beef", m))))
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
                self.assertEqual(upkeep.nether_retreat(snap), why)

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
                self.assertEqual(upkeep.working_tiers(bag(inv)), want)


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
                self.assertEqual(upkeep.craftable_tier(bag(inv), kind), tier)

    def test_every_tool_kind_has_a_row_or_is_wood(self):
        for kind in upkeep.TOOL_KINDS:
            with self.subTest(kind):
                self.assertEqual(upkeep.craftable_tier(bag(inventory()), kind), 0)


HOUR = 3600
# (situation, [(op, *args)], kind asked, expected positions). ops: see ... ages are hours before now.
NOTES = [
    ("a sheep seen yesterday is still a guess", [("see", "minecraft:sheep", (20, 64, 0), 24)], "minecraft:sheep",
     [(20, 64, 0)]),
    ("diamonds seen 5 h ago are still there", [("see", "diamond_ore", (3, 12, 3), 5)], "diamond_ore", [(3, 12, 3)]),
    ("diamonds seen 7 h ago are not trusted", [("see", "diamond_ore", (3, 12, 3), 7)], "diamond_ore", []),
    ("seen again at the same spot: one note, refreshed", [("see", "obsidian", (9, 30, 9), 7),
                                                         ("see", "obsidian", (9, 30, 9), 0)], "obsidian",
     [(9, 30, 9)]),
    ("two spots: newest first", [("see", "minecraft:cow", (5, 64, 5), 2), ("see", "minecraft:cow", (50, 64, 5), 0)],
     "minecraft:cow", [(50, 64, 5), (5, 64, 5)]),
    ("arrived and found nothing: the note is dropped", [("see", "diamond_ore", (3, 12, 3), 1),
                                                       ("confirm", "diamond_ore", (3, 12, 3), False)],
     "diamond_ore", []),
    ("arrived and found it: kept", [("see", "diamond_ore", (3, 12, 3), 1), ("confirm", "diamond_ore", (3, 12, 3), True)],
     "diamond_ore", [(3, 12, 3)]),
    ("an empty spot elsewhere drops only its own note", [("see", "diamond_ore", (3, 12, 3), 1),
                                                         ("see", "diamond_ore", (90, 12, 3), 1),
                                                         ("confirm", "diamond_ore", (90, 12, 3), False)],
     "diamond_ore", [(3, 12, 3)]),
]


class WhereToLook(unittest.TestCase):
    def test_note_sequences(self):
        for name, ops, kind, want in NOTES:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                for o in ops:
                    if o[0] == "see":
                        _, k, pos, hours = o
                        m.add_sighting(k, pos, OVER)
                        for row in m.data["sightings"][k]:
                            if tuple(row["pos"]) == tuple(pos):
                                row["at"] = time.strftime("%Y-%m-%d %H:%M",
                                                          time.localtime(time.time() - hours * HOUR))
                        m.save()
                    else:
                        _, k, pos, found = o
                        m.confirm(k, pos, OVER, found=found)
                self.assertEqual([tuple(s["pos"]) for s in m.sightings(kind, OVER)], want)


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
    ("the cause cools at the place: another task stopped by it waits", [("task t1", TOOL, HERE)],
     {("task t1", "tool"): 1}, set(), {"task t1": False}),
    ("a success clears the count", [("task t1", NAV, HERE), ("task t1", NAV, HERE), ("task t1", "ok", HERE),
                                    ("task t1", NAV, HERE)], {("task t1", "nav"): 1}, set(), {}),
]


def new_brain(tmp):
    b = brainmod.Brain.__new__(brainmod.Brain)
    b.mem = Memory(os.path.join(tmp, "notes.json"))
    b.retry, b.blacklist, b.place = retry.Retry(), {}, HERE
    b.table = upkeep.Upkeep(b)
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
                b.place = HERE
                for task, want in ready.items():
                    self.assertEqual(b.ready(task), want)
                if not counts:
                    self.assertEqual(b.retry.cooling, {}, "an interruption cooled something")
                    self.assertIsNone(b.table.blocked, "an interruption was taken for a blocked path")

    def test_a_cause_cools_by_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = new_brain(tmp)
            b.failed("task t1", TOOL)
            self.assertFalse(b.ready("task t2", cause="tool"), "the same wall stops every task that walks into it")
            b.place = THERE
            self.assertTrue(b.ready("task t2", cause="tool"), "somewhere else it is not in the way")

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

    def test_the_failure_path_is_the_only_banning_path(self):
        """`arrived` answers False only for a walk that failed; an interruption propagates (test point A, ARRIVE),
        so `if not arrived(...): ctx.ban(...)` can never ban for one. Every such ban site asks `arrived`, not
        `go_to` (a Walked leg is truthy, a False from go_to can be a stopped walk)."""
        import inspect
        import re
        from bonobo import dispatch
        for mod in (skills, dispatch):
            src = inspect.getsource(mod)
            for m in re.finditer(r"if not (nav\.\w+)\(.*\n\s+(?:ctx|self)\.ban\(", src):
                with self.subTest(module=mod.__name__, at=m.start()):
                    self.assertEqual(m.group(1), "nav.arrived")


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
                           ("unknown milestone", lambda p: goals.make("milestone", name="win"))):
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "tasks.json")
                tasks.add(G1, path=path)
                with self.assertRaises(ValueError):
                    call(path)

    def test_every_template_decomposes_or_says_why(self):
        """Extensible: a new template in goals.TEMPLATES needs a decompose branch (or a clear Unplannable)."""
        args = {"have": {"needs": [["log", 1]]}, "craft": {"needs": [["minecraft:stick", 4]]},
                "milestone": {"name": "food"}, "goto": {"pos": [5, 64, 0]}, "road": {"a": [0, 64, 0], "b": [5, 64, 0]},
                "build": {"bp": "shelter"}, "sleep": {}, "skill": {"name": "chop", "args": [1]}}
        self.assertEqual(set(args), set(goals.TEMPLATES), "a template without a row here")
        for template, a in args.items():
            with self.subTest(template):
                steps = plan({"goal": template, "args": a}, snapshot(), {"oak_log": 5, "stone": 2, "cow": 9})
                self.assertIsInstance(steps, list)


if __name__ == "__main__":
    unittest.main()
