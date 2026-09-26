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
from bonobo.knowledge import food_count  # noqa: E402
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
                task = {"id": "t1", "goal": goal["goal"], "args": goal.get("args", {})}
                snap_b, snap_a = snapshot(inv=inv_before), snapshot(inv=inv_after)
                held_b, why_b = brainmod.replan(task, goal, snap_b, cost(snap_b, **seen))
                held_a, why_a = brainmod.replan(task, goal, snap_a, cost(snap_a, **seen), pending=pending)
                self.assertIsNone(why_b)
                self.assertIsNone(why_a)
                self.assertEqual(held_a["sig"], upkeep.bag_signature(snap_a.inv), "the held plan is stamped with its bag")
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
                self.assertIsNone(held)
                self.assertTrue(why.startswith("unplannable"))

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


# ----------------------------------------------------------------------------------------------- held plans
# The brain's queue work (task_act / repair / after_step / finish / prepare) on an unstarted Brain: the cost model it
# builds answers from the row's look-around, the bag after a step from the row's readings; any other request fails.
TREES = {"oak_log": 6, "stone": 2}


class Queue_:
    """One brain, one task file, the row's readings."""

    def __init__(self, tmp, seen=TREES):
        self.tmp, self.seen, self.after_inv = tmp, seen, inventory()
        b = self.b = brainmod.Brain.__new__(brainmod.Brain)
        b.mem = Memory(os.path.join(tmp, "notes.json"))
        b.retry, b.blacklist, b.place, b.held = retry.Retry(), {}, PLACE, {}
        b.table, b.last_failure, b.committed, b.last_hold_log = upkeep.Upkeep(b), None, None, 0
        self.patches = [mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")),
                        mock.patch.object(brainmod, "Cost", lambda snap, mem=None, bl=None: cost(snap, mem=mem,
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
                    elif op_ == "failed":
                        for _ in range(a[0]):
                            q.b.last_failure = q.b.failed(q.act.name, api.NavFailed("no path found"))
                        q.b.after_step(q.act, "failed")
                    elif op_ == "job":
                        q.b.mem.add_job("smelt", (3, 64, 0), OVER, a[0], a[1], time.time() + 60, [])
                    elif op_ == "check":
                        a[0](self, q)

    # (bag, what idle prepares first, or None when everything is held)
    PREPARE = [(inventory(), ("tool", "pickaxe", 1)),
               (inventory(("stone_pickaxe", 1)), ("tool", "sword", 1)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1)), ("food", 8)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("cooked_beef", 8)), ("minecraft:torch", 8)),
               (inventory(("stone_pickaxe", 1), ("stone_sword", 1), ("cooked_beef", 8), ("torch", 8)), None)]

    def test_prepare(self):
        for inv, want in self.PREPARE:
            with self.subTest(want=want), tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
                act = q.b.prepare(snapshot(inv=inv))
                queued = [tuple(t["args"]["needs"][0]) for t in tasks.load()]
                self.assertEqual(queued, [want] if want else [])
                self.assertEqual(act is None, want is None)


# -------------------------------------------------------------------------------------------------------- upkeep
PLACE = retry.place_signature((0, 64, 0), False)
DAY, DUSK, NIGHT = 2000, 11500, 18000
WELL_FED = [("cooked_beef", 8), ("white_bed", 1), ("stone_pickaxe", 1)]
HERD = {"cow": 12, "sheep": 20, "oak_log": 10, "stone": 2}


class Row:
    """One upkeep situation: readings, the few world facts the table reads, and what it must conclude."""

    def __init__(self, name, row, queued=(), time_of_day=DAY, inv=WELL_FED, seen=None, enclosed=False,
                 bed_seen=False, last_round=None, blocked=None, stuck=False, died=False, cooling=(), place=None,
                 job=None, machine=None, **st):
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
    b.mem = Memory(os.path.join(tmp, "notes.json"))
    b.retry, b.blacklist = retry.Retry(), {}
    b.place = PLACE
    b.table = table = upkeep.Upkeep(b)
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
    if row.stuck:
        sig = upkeep.bag_signature(snap.inv)
        table.history = [(now - 90 + i * 10, snap.feet, sig) for i in range(10)]
    if row.blocked is not None:
        table.failed("nav", api.NavFailed("no path found", pos=row.blocked), row.place)
    c, plan_s = cost(snap, **row.seen), {}
    for goal in (goals.have(("food", 8)), goals.have(("bed", 1))):
        try:
            secs = c.plan_s(decompose.decompose(snap.inv, goal, c))
        except Unplannable:
            secs = float("inf")
        plan_s[goal["args"]["needs"][0][0]] = secs
        table.plan_s_cache[(json.dumps(goal, sort_keys=True), upkeep.bag_signature(snap.inv))] = (now + 60, secs)
    with mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")), \
            mock.patch.object(api, "api", side_effect=AssertionError("upkeep read the world beyond the row")):
        got = table.act(snap, ctx=None, reads={"enclosed": row.enclosed, "bed_near": row.bed_seen})
        queued = [tuple(tuple(n) for n in t["args"]["needs"]) for t in tasks.load() if t["state"] in tasks.LIVE]
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
        for w, lead in ((w, lead) for w in cells for lead in (1.0, upkeep.LEAD, 3.0)):
            row = Row(repr(w), None)
            row.state, row.inv, row.seen = w.game_state(), w.inventory(), RESOURCES[w.dims["resource"]]
            snap = snapshot(row.state, row.inv)
            with self.subTest(world=w, lead=lead), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(upkeep, "LEAD", lead):
                chosen, queued, plan_s = run_upkeep(row, tmp)
                if chosen is not None:
                    continue                                # a row took the round: nothing is queued this round
                over = snap.dimension == OVER
                bed = over and not snap.night and snap.inv.count("bed") == 0 \
                    and upkeep.dusk_s(snap) < plan_s["bed"] * lead
                food = food_count(snap.inv) < 8 and upkeep.food_lasts_s(snap) < plan_s["food"] * lead
                self.assertEqual(((("bed", 1),) in queued), bed, f"dusk in {upkeep.dusk_s(snap)} s, bed plan "
                                                                  f"{plan_s['bed']:.0f} s × {lead}")
                self.assertEqual(((("food", 8),) in queued), food, f"food lasts {upkeep.food_lasts_s(snap):.0f} s, "
                                                                    f"plan {plan_s['food']:.0f} s × {lead}")

    def test_lead_moves_the_verdict(self):
        """The same dusk, the same bag: a longer lead inserts the bed, a shorter one does not."""
        for lead, want in ((0.1, set()), (50.0, {(("bed", 1),)})):
            row = Row(f"lead {lead}", None, time_of_day=DUSK, inv=[("cooked_beef", 8), ("stone_pickaxe", 1)])
            with self.subTest(lead=lead), tempfile.TemporaryDirectory() as tmp, mock.patch.object(upkeep, "LEAD", lead):
                _, queued, _ = run_upkeep(row, tmp)
                self.assertEqual(set(queued) & {(("bed", 1),)}, want)

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
    ("we mined the ore ourselves: gone", [("at", 0), ("see", "coal_ore", (3, 50, 3)), ("dug", [(3, 50, 3)])],
     "coal_ore", []),
    ("we dug near a tree: to verify, not gone", [("at", 0), ("see", "tree", (10, 64, 10)), ("dug", [(12, 64, 10)])],
     "tree", [((10, 64, 10), True)]),
    ("dug far from the tree: untouched", [("at", 0), ("see", "tree", (10, 64, 10)), ("dug", [(40, 64, 10)])],
     "tree", [((10, 64, 10), False)]),
    ("an unknown kind is slow, never forever", [("at", 0), ("see", "sugar_cane", (4, 64, 4)), ("at", 10 ** 7)],
     "sugar_cane", []),
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
                    elif op_ == "forget":
                        m.forget_seen(a[0], a[1], OVER)
                    elif op_ == "confirm":
                        m.confirm(a[0], a[1], OVER, found=a[2])
                    elif op_ == "dug":
                        m.mark_dirty_near(a[0], OVER)
                self.assertEqual([(tuple(r["pos"]), bool(r.get("verify"))) for r in m.seen(kind, OVER)], want)

    def test_every_volatility_class_has_rows(self):
        from bonobo.data import VOLATILITY, seen_class
        covered = {seen_class(k) for _n, _ops, k, _w in NOTES}
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


class Withdraw(unittest.TestCase):
    def test_chest_or_make(self):
        for name, (dist, items), seen, chosen, not_chosen in WITHDRAW:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                m.note_container((dist, 64, 0), OVER, [{"id": i, "count": n} for i, n in items.items()])
                snap = snapshot()
                steps = decompose.decompose(snap.inv, goals.have(("log", 4)), cost(snap, mem=m, **seen))
                self.assertTrue(has(steps, *chosen), list(map(str, steps)))
                if not_chosen:
                    self.assertFalse(has(steps, *not_chosen), list(map(str, steps)))
                took = sum(st.count for st in steps if st.kind == "withdraw")
                self.assertLessEqual(took, sum(items.get(i, 0) for i in ("minecraft:oak_log",)))


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
P = ("here", False)
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
                for key, want in ready.items():       # task, or (task, the cause that would stop it, place)
                    task, cause, b.place = key if isinstance(key, tuple) else (key, None, HERE)
                    self.assertEqual(b.ready(task, cause=cause), want, key)
                if not counts:
                    self.assertEqual(b.retry.cooling, {}, "an interruption cooled something")
                    self.assertIsNone(b.table.blocked, "an interruption was taken for a blocked path")

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
