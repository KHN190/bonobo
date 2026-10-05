"""Milestones read the bag live (a lost tool resets its milestone) but credit what a later milestone consumed into a
product still held. Expected: nothing serialised that can overlap, no delay beyond vanilla's (Minecraft Wiki): 5 ticks
between breaks, 4 between uses or places, 200 a smelted item; a GUI action at most LOW_DELAY_TICKS (未实测).
"""
import contextlib
import math
import itertools
import os
import random
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401
from bonobo import api, arbiter, fight_loop, goals, hazard, nav, perception, reflexes, retry, skillcore, tasks, threat  # noqa: E402
from bonobo.arbiter import SCALES  # noqa: E402
from bonobo.knowledge import break_ticks, prior_work_ticks, tool_item  # noqa: E402
from bonobo.skillcore import Ban, ban_state, banned  # noqa: E402
from bonobo.craft import ITEMS_PER_FUEL, recipe_of  # noqa: E402
from collections import Counter  # noqa: E402
from bonobo.data import MAX_HP, bare, mid, place_signature  # noqa: E402
from bonobo.estimate import eat_due  # noqa: E402
from bonobo.reflexes import EAT_BELOW  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402
from bonobo.planner import NullCost, Node, Search, Step, from_bag, plan_needs  # noqa: E402
from bonobo.skill import MIN_SAMPLES  # noqa: E402
from bonobo.survive import ROUND_GROUND  # noqa: E402
from bonobo.world import Versioned, cell_add  # noqa: E402
from tests.test_decompose import TIER, held, take  # noqa: E402
from tests.test_enforcement import Recorder  # noqa: E402
from tests.world import bag, brain_fixture, cost, flat, inventory, memory, round_ctx, snapshot, state  # noqa: E402

KIT = (("iron_pickaxe", 1), ("iron_sword", 1), ("stone_axe", 1), ("crafting_table", 1), ("furnace", 1),
       ("cooked_beef", 8), ("white_bed", 1), ("torch", 24), ("cobblestone", 64), ("bucket", 1), ("shield", 1),
       ("flint_and_steel", 1), ("iron_helmet", 1), ("iron_chestplate", 1), ("iron_leggings", 1), ("iron_boots", 1),
       ("water_bucket", 1), ("golden_helmet", 1))


def kit(without=(), add=(), worn=None):
    inv = inventory(*[i for i in KIT if i[0] not in without], *add)
    for slot, item in (worn or {}).items():
        inv["equipment"][slot] = {"id": f"minecraft:{item}", "count": 1}
    return inv


def next_name(inv, mem=None, **st):
    got = brain.next_milestone(snapshot(state(**st), inv), mem if mem is not None else memory())
    return got and got["args"]["name"]


def route_index(name):
    return list(goals.MILESTONES).index(name)


class Milestones(unittest.TestCase):
    ROWS = [
        ("rods consumed into eyes held: no trip back for rods",
         kit(add=(("blaze_rod", 1), ("ender_eye", 12))), {}, "not before", "eyes of ender"),
        ("pearls consumed into eyes held: no trip back for pearls",
         kit(add=(("blaze_rod", 7), ("ender_eye", 12))), {}, "not before", "eyes of ender"),
        ("eyes set in the frame, standing in the End: the run is past the portal",
         kit(), {"dimension": "minecraft:the_end"}, "not before", "dragon beds"),
        ("iron armour worn, none carried: still had",
         kit(without=("iron_chestplate",), worn={"chest": "iron_chestplate"}), {}, "not", "iron armor"),
        ("the bucket filled with water: still had",
         kit(without=("bucket",)), {}, "not", "iron tools"),
        ("must fail: the iron pickaxe lost while eyes are held resets the tool milestones",
         kit(without=("iron_pickaxe",), add=(("blaze_rod", 1), ("ender_eye", 12))), {}, "is", "stone tools"),
        ("must fail: one rod and nothing made of rods is short of rods",
         kit(add=(("blaze_rod", 1),)), {}, "is", "blaze rods"),
    ]

    def test_rows(self):
        for name, inv, st, rel, want in self.ROWS:
            with self.subTest(name):
                got = next_name(inv, **st)
                if rel == "is":
                    self.assertEqual(got, want)
                elif rel == "not":
                    self.assertNotEqual(got, want)
                else:
                    self.assertTrue(got is None or route_index(got) >= route_index(want), got)


class TaskFile(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="tasks-"), "tasks.json")
        patcher = mock.patch.object(tasks, "FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.snap = snapshot(state(), inventory(("torch", 1)))
        self.b = brain_fixture()

    def propose(self):
        with mock.patch.object(brain, "next_milestone", lambda snap, mem: None):
            self.b.plan_proposals(self.snap, round_ctx(self.b, self.snap))

    def test_cancelled_and_expired_tasks_leave_no_held_plan(self):
        rows = [("cancelled", lambda t: tasks.cancel(t["id"])),
                ("expired", lambda t: tasks.update(t["id"], expires=1.0)),
                ("finished", lambda t: None)]
        for why, end in rows:
            with self.subTest(why):
                tasks.save([])
                t = tasks.add(goals.have(("minecraft:torch", 1)))
                self.b.held = {t["id"]: {"steps": [Step("mine", "minecraft:cobblestone", 3, {})]}}
                end(t)
                self.propose()
                self.assertNotIn(t["id"], self.b.held)

    def test_a_task_added_while_the_round_expires_others_is_kept(self):
        tasks.save([])
        old = tasks.add(goals.have(("minecraft:torch", 64)))
        tasks.update(old["id"], expires=1.0)
        real = tasks.expire

        def racing(items, now=None):
            tasks.add(goals.have(("minecraft:stick", 4)))
            return real(items, now)
        with mock.patch.object(tasks, "expire", racing):
            self.propose()
        goals_left = [t["args"] for t in tasks.load()]
        self.assertIn({"needs": [["minecraft:stick", 4]]}, goals_left)
        self.assertTrue(any(t["id"] == old["id"] for t in tasks.load()))


def plan(needs, *items, **kw):
    return plan_needs(bag(inventory(*items)), list(needs), NullCost(), **kw)


def made(steps, kind, token):
    return sum(s.count for s in steps if s.kind == kind and bare(s.token) == token)


class SameTokenTwice(unittest.TestCase):
    ROWS = [
        ("stone for the kit and the food's furnace", [("food", 8), ("stone", 32)], (("beef", 8),),
         "mine", "cobblestone"),
        ("iron for the pickaxe and the ingots", [("tool", "pickaxe", 2), ("minecraft:iron_ingot", 3)], (),
         "mine", "raw_iron"),
        ("logs for the table and the sticks", [("minecraft:crafting_table", 1), ("minecraft:stick", 4)], (),
         "gather", "log"),
        ("logs for a wooden pickaxe and sticks", [("tool", "pickaxe", 0), ("minecraft:stick", 4)], (),
         "gather", "log"),
    ]

    def test_order_does_not_change_what_is_got(self):
        for name, needs, items, kind, token in self.ROWS:
            with self.subTest(name):
                forward = made(plan(needs, *items), kind, token)
                backward = made(plan(reversed(needs), *items), kind, token)
                self.assertEqual(forward, backward)

    def test_iron_is_got_once_for_both(self):
        steps = plan([("tool", "pickaxe", 2), ("minecraft:iron_ingot", 3)])
        self.assertEqual(made(steps, "smelt", "iron_ingot"), 3 + 3)


class SmeltFuel(unittest.TestCase):
    ROWS = [
        ("six iron on coal", [("minecraft:iron_ingot", 6)], (("raw_iron", 6), ("coal", 4), ("furnace", 1))),
        ("three iron and three copper on coal: two sessions",
         [("minecraft:iron_ingot", 3), ("minecraft:copper_ingot", 3)],
         (("raw_iron", 3), ("raw_copper", 3), ("coal", 4), ("furnace", 1))),
        ("six iron on planks", [("minecraft:iron_ingot", 6)], (("raw_iron", 6), ("oak_planks", 8), ("furnace", 1))),
        ("nine iron on coal: past one coal", [("minecraft:iron_ingot", 9)],
         (("raw_iron", 9), ("coal", 4), ("furnace", 1))),
    ]

    def test_each_smelt_carries_what_its_session_burns(self):
        for name, needs, items in self.ROWS:
            with self.subTest(name):
                for st in (s for s in plan(needs, *items) if s.kind == "smelt"):
                    fuel = {bare(k): v for k, v in st.detail["inputs"].items() if bare(k) != bare(st.detail["input"])}
                    self.assertEqual(len(fuel), 1, st.detail)
                    (kind, n), = fuel.items()
                    per = ITEMS_PER_FUEL["planks" if kind.endswith("planks") else kind]
                    self.assertEqual(n, math.ceil(st.count / per), (kind, st.count))


class HeldReplay(unittest.TestCase):
    def replay(self, items, needs, held, pending=None):
        c = NullCost()
        root = Node(from_bag(bag(inventory(*items)), pending=pending, facts=c.facts()), [], [])
        return Search(c).replay(root, needs, held)

    def test_rows(self):
        fill = [Step("fill", "minecraft:water_bucket", 1, {"container": "minecraft:bucket",
                                                            "inputs": {"minecraft:bucket": 1}})]
        portal = [Step("build", "nether_portal", 1, {})]
        rows = [
            ("a fill whose call is its own input", (("bucket", 1),), [("minecraft:water_bucket", 1)], fill, True),
            ("a portal keeps the flint it only holds", (("obsidian", 10), ("flint_and_steel", 1), ("cobblestone", 4)),
             [("fact", "portal", True), ("minecraft:flint_and_steel", 1)], portal, True),
            ("must fail: a portal with no obsidian", (("flint_and_steel", 1), ("cobblestone", 4)),
             [("fact", "portal", True)], portal, False),
            ("must fail: a fill with no bucket", (), [("minecraft:water_bucket", 1)], fill, False),
        ]
        for name, items, needs, held_plan, ok in rows:
            with self.subTest(name):
                self.assertEqual(self.replay(items, needs, held_plan) is not None, ok)

    def test_a_held_plan_waiting_on_a_furnace_is_replanned_not_crashed(self):
        jobs = {"minecraft:iron_ingot": 3}
        inv = bag(inventory(("stick", 2), ("crafting_table", 1)))
        held = plan_needs(inv, [("minecraft:bucket", 1)], NullCost(), pending=jobs, jobs=jobs)
        for name, pending in (("job still running", jobs), ("job gone", None)):
            with self.subTest(name):
                try:
                    again = plan_needs(inv, [("minecraft:bucket", 1)], NullCost(), pending=pending, jobs=pending,
                                       held=held)
                except Exception as e:
                    self.fail(f"{type(e).__name__}: {e}")
                if pending is None:
                    self.assertFalse(any(s.kind == "await" for s in again))


class WalkLowerBound(unittest.TestCase):
    def test_never_above_the_price_from_any_place(self):
        for d in (3.0, 8.0, 20.0):
            c = cost(None, None, stone=d)
            step = Step("mine", "minecraft:cobblestone", 1, {"blocks": ["minecraft:stone"], "tier": 0, "breaks": 1})
            site, feet = c.site(step), c.snap.feet
            lb = c.walk_lb(step, None)
            for at in (None, (feet[0] - 10, feet[1], feet[2]), (site[0] + 3, site[1], site[2])):
                with self.subTest(distance=d, at=at):
                    priced = Step(step.kind, step.token, step.count, dict(step.detail))
                    c.estimate(priced, at=at)
                    getting = sum(priced.parts.get(k, 0) for k in ("walk", "dig", "surface", "seek", "refuted"))
                    self.assertLessEqual(lb, getting)

    def test_must_fail_a_far_site_has_a_bound(self):
        c = cost(None, None, stone=20.0)
        step = Step("mine", "minecraft:cobblestone", 1, {"blocks": ["minecraft:stone"], "tier": 0, "breaks": 1})
        self.assertGreater(c.walk_lb(step, None), 0)


class RememberedTree(unittest.TestCase):
    def test_a_walled_in_log_is_no_source_remembered_or_seen(self):
        st = state()
        f = (st["blockX"], st["blockY"], st["blockZ"])
        lo, hi = (cell_add(f, d) for d in ROUND_GROUND[0])
        log = (f[0] + 5, f[1], f[2])
        region = flat(lo, hi, floor_y=f[1] - 1)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1, 2):
                for dz in (-1, 0, 1):
                    region.blocks[(log[0] + dx, log[1] + dy, log[2] + dz)] = "bedrock"
        region.blocks[log] = "oak_log"
        rows = [("remembered", True, False), ("seen", False, True), ("both", True, True)]
        for name, remembered, seen in rows:
            with self.subTest(name):
                mem = memory()
                if remembered:
                    mem.note_seen("tree", log, st["dimension"])
                snap = snapshot(st, inventory(), region=region)
                if seen:
                    snap.hits["oak_log"] = [{"x": log[0], "y": log[1], "z": log[2], "block": "minecraft:oak_log",
                                             "distance": 5.0}]
                c = cost(snap, mem)
                self.assertIsNone(c.site(Step("gather", "log", 1, {})))

    def test_must_fail_an_open_log_is_a_source(self):
        st = state()
        mem = memory()
        f = (st["blockX"], st["blockY"], st["blockZ"])
        lo, hi = (cell_add(f, d) for d in ROUND_GROUND[0])
        log = (f[0] + 5, f[1], f[2])
        region = flat(lo, hi, floor_y=f[1] - 1)
        region.blocks[log] = "oak_log"
        mem.note_seen("tree", log, st["dimension"])
        self.assertEqual(cost(snapshot(st, inventory(), region=region), mem).site(Step("gather", "log", 1, {})), log)


class View(dict):
    def __missing__(self, key):
        return False


class NightMeal(unittest.TestCase):
    def view(self, **kw):
        return View(dict(food=6, hp=MAX_HP, meal="minecraft:cooked_beef", night=True, bed_works=True,
                         bed_carried=True, shelter_ready=True), **kw)

    def test_rows(self):
        rows = [
            ("sleep and shelter both cooling: eat", {}, lambda n: n not in ("sleep", "shelter"), True),
            ("sleep cooling, no shelter way: eat", {"shelter_ready": False}, lambda n: n != "sleep", True),
            ("must fail: sleep about to run: the meal waits for it", {}, lambda n: True, False),
            ("must fail: shelter about to run: the meal waits for it", {}, lambda n: n != "sleep", False),
        ]
        for name, changes, ready, eats in rows:
            with self.subTest(name):
                fired = [n for _i, n in reflexes.due(self.view(**changes), ready)]
                self.assertEqual("eat" in fired, eats, fired)


class Driving(unittest.TestCase):
    def test_interleaved_runs_leave_no_stale_driver(self):
        motion = arbiter.Motion()
        a_in, a_out, b_in, b_out = (threading.Event() for _ in range(4))

        def act(entered, release):
            def run():
                entered.set()
                release.wait(2)
            return run
        x = arbiter.Intent("tactic", act(a_in, a_out), "fight", key="fight")
        p = arbiter.Intent("plan", act(b_in, b_out), "mine", key="mine")
        ta = threading.Thread(target=motion._run, args=(x,))
        ta.start()
        a_in.wait(2)
        tb = threading.Thread(target=motion._run, args=(p,))
        tb.start()
        b_in.wait(2)
        a_out.set()
        ta.join(2)
        b_out.set()
        tb.join(2)
        self.assertIsNone(motion.driving)


class PlaceBins(unittest.TestCase):
    def test_rows(self):
        rows = [
            ("negative x, inside a block", (-16.3, 64.0, 5.2), (-17, 64, 5), True),
            ("negative x on the bin edge", (-0.5, 64.0, 0.5), (-1, 64, 0), True),
            ("positive", (15.9, 64.0, 0.1), (15, 64, 0), True),
            ("must fail: neighbouring bins", (-16.3, 64.0, 5.2), (-16, 64, 5), False),
        ]
        for name, float_feet, cell, same in rows:
            with self.subTest(name):
                self.assertEqual(place_signature(float_feet, False) == place_signature(cell, False), same)


def chain_runner(bad):
    def run_chain(tasks_, stop_on_failure=True, wait=None):
        out = []
        for t in tasks_:
            cell = (t["x"], t["y"], t["z"])
            if cell in bad:
                out.append({"status": "failed", "message": bad[cell]})
                break
            out.append({"status": "succeeded"})
        return out
    return run_chain


class RunCells(unittest.TestCase):
    def run_cells(self, n, bad):
        cells = [{"type": "mine", "x": i, "y": 64, "z": 0} for i in range(n)]
        with mock.patch.object(nav.api, "run_chain", chain_runner(bad)), \
                mock.patch.object(nav.api, "out_of_reach", lambda r: None), \
                mock.patch.object(nav.api, "detail", lambda *a: None):
            return nav.run_cells("mine", cells)["result"]

    def test_rows(self):
        far = nav.STEP_UNREACHABLE[0]
        rows = [
            ("unreachable, reachable, unreachable: the rest still mined",
             5, {(0, 64, 0): far, (2, 64, 0): far}, 3),
            ("one unreachable among many", 6, {(3, 64, 0): far}, 5),
            ("all reachable", 4, {}, 4),
            ("must fail: two unreachable in a row give the rest back", 5, {(0, 64, 0): far, (1, 64, 0): far}, 0),
        ]
        for name, n, bad, ok in rows:
            with self.subTest(name):
                self.assertEqual(self.run_cells(n, bad)["succeeded"], ok)


class StationsLeftStanding(unittest.TestCase):
    def test_rows(self):
        st = state()
        dim, near = st["dimension"], (st["blockX"] + 2, st["blockY"], st["blockZ"])

        def smelting(block):
            mem = memory()
            mem.add_station(f"minecraft:{block}", near, dim)
            mem.add_job("smelt", near, dim, "minecraft:iron_ingot", 6, 10 ** 9, False)
            return mem
        rows = [
            ("our furnace left smelting: the kit is had", kit(without=("furnace",)), smelting("furnace"), "not"),
            ("our table standing by: the kit is had", kit(without=("crafting_table",)),
             (lambda m: (m.add_station("minecraft:crafting_table", near, dim), m)[1])(memory()), "not"),
            ("must fail: no furnace carried or standing", kit(without=("furnace",)), memory(), "is"),
            ("must fail: no table carried or standing", kit(without=("crafting_table",)), memory(), "is"),
        ]
        for name, inv, mem, rel in rows:
            with self.subTest(name):
                got = next_name(inv, mem)
                (self.assertEqual if rel == "is" else self.assertNotEqual)(got, "station kit")


class RescueFallback(unittest.TestCase):
    def test_rows(self):
        dying, threat = state(health=2.0), ["zombie"]
        rows = [
            ("critical cooling, a threat unanswered: the threat's rescue", dying, threat,
             lambda n: n != "rescue critical", "threat"),
            ("must fail: critical ready: critical first", dying, threat, lambda n: True, "critical"),
            ("lava and critical, lava cooling: critical", state(health=2.0, inLava=True), None,
             lambda n: n != "rescue lava", "critical"),
            ("every rescue cooling: none", dying, threat, lambda n: False, None),
        ]
        for name, st, unanswered, ready, want in rows:
            with self.subTest(name):
                self.assertEqual(hazard.rescue_due(st, buried=False, unanswered=unanswered, ready=ready), want)


class Landing(unittest.TestCase):
    def test_rows(self):
        rows = [
            ("a lava floor ahead", "lava", 63, False),
            ("a magma floor ahead", "magma_block", 63, False),
            ("lava at the feet ahead", "lava", 64, False),
            ("must fail: plain stone all the way", None, 63, True),
        ]
        for name, block, y, reaches in rows:
            with self.subTest(name):
                region = flat((-10, 60, -10), (20, 70, 10), floor_y=63)
                if block:
                    for x in range(3, 8):
                        region.blocks[(x, y, 0)] = block
                got = nav.landing(region, (0.5, 64.0, 0.5), (7.5, 64.0, 0.5))
                self.assertIsNotNone(got)
                self.assertNotIn(region.name(got), ("lava", "magma_block"))
                self.assertNotIn(region.name((got[0], got[1] - 1, got[2])), ("lava", "magma_block"))
                self.assertEqual(got[0] == 7, reaches, got)


def mine_step():
    return Step("mine", "minecraft:cobblestone", 4, {"blocks": ["minecraft:stone"], "tier": 0, "breaks": 4})


def priced(c):
    st = mine_step()
    return c.estimate(st), st.parts


class MeasuredWork(unittest.TestCase):
    def test_a_run_is_priced_as_it_ran_from_where_it_ran(self):
        rows = [("near", 3.0, 3.0), ("far", 20.0, 20.0), ("measured far, priced near", 20.0, 3.0),
                ("measured near, priced far", 3.0, 20.0)]
        for name, ran_at, priced_at in rows:
            with self.subTest(name):
                ran_total, ran_parts = priced(cost(None, memory(), stone=ran_at))
                want, _ = priced(cost(None, memory(), stone=priced_at))
                mem = memory()
                for _ in range(MIN_SAMPLES):
                    mem.record_duration("mine:minecraft:cobblestone", ran_parts["work"] / TICKS_PER_S, 4)
                got, parts = priced(cost(None, mem, stone=priced_at))
                self.assertAlmostEqual(got, want, delta=max(2, want * 0.02), msg=parts)


class FastLayer(unittest.TestCase):
    def decide(self, **patches):
        b = brain_fixture(planning=False)
        snap = snapshot(state(inLava=True, onGround=False), inventory())
        with contextlib.ExitStack() as stack:
            for target, attr, value in [(b.needs, "propose", lambda *a, **k: []),
                                        (b.reflexes, "proposals", lambda *a, **k: []),
                                        (b, "light_intent", lambda *a, **k: None),
                                        *[(fight_loop, k, v) for k, v in patches.items()]]:
                stack.enter_context(mock.patch.object(target, attr, value))
            act = b._decide_round(snap, round_ctx(b, snap))
        return act and act.name

    def test_rows(self):
        def boom(*_a, **_k):
            raise KeyError("threat model")
        rows = [("the threat model raises: lava still rescued", {"unanswered_now": boom}, "rescue lava"),
                ("must fail: all well: lava rescued", {}, "rescue lava")]
        for name, patches, want in rows:
            with self.subTest(name):
                try:
                    got = self.decide(**patches)
                except Exception as e:
                    got = f"{type(e).__name__}: {e}"
                self.assertEqual(got, want)


class PerceptionTick(unittest.TestCase):
    def tick(self, mode, changes, last=None):
        w, body, posts = perception.Watcher(), Recorder(), []
        w.last.update(last or {})

        def one_tick(_s, w=w):
            w.stopped = True
        with contextlib.ExitStack() as stack:
            for target, attr, kw in [
                    (perception.time, "sleep", {"side_effect": one_tick}),
                    (perception.fight_loop, "active", {"return_value": False}),
                    (perception.STATE, "paused", {"new": False}),
                    (perception, "_eating", {"return_value": False}), (perception, "note_hurt", {}),
                    (api.STATE, "mode", {"new": mode}), (api.STATE, "soft", {"new": False}),
                    (api.STATE, "interrupt", {"new": None}), (api, "log", {}),
                    (api, "get", {"return_value": state(**changes)}),
                    (api, "post", {"side_effect": lambda p, b=None: posts.append(p)}),
                    (arbiter, "BODY", {"new": body}), (w, "_look", {}), (w, "_answer_threats", {}),
                    (w, "_time_to_die", {"return_value": None}),
                    (w, "_enderman_after_us", {"return_value": False}),
                    (w, "_breath_within", {"return_value": False}),
                    (w, "hazard", {"new": types.SimpleNamespace(buried=lambda s: False, fallen=lambda s: 0.0)})]:
                stack.enter_context(mock.patch.object(target, attr, **kw))
            w.run()
            return [layer for layer, _why in body.preempted], api.STATE.interrupt

    def test_rows(self):
        lava = dict(inLava=True, onGround=False, control={"task": {"type": "travel"}})
        rows = [
            ("our own lava rescue running: not stopped by us", "survival", lava, None, []),
            ("lava again 3 s after the last stop, no rescue running: stopped", "normal", lava,
             lambda: {"lava": perception.time.time() - 3}, ["safety"]),
            ("must fail: lava under a running task: stopped", "normal", lava, None, ["safety"]),
            ("all well while a rescue runs: nothing", "survival", dict(control={"task": {"type": "travel"}}), None,
             []),
        ]
        for name, mode, changes, last, want in rows:
            with self.subTest(name):
                layers, _told = self.tick(mode, changes, last() if last else None)
                self.assertEqual(layers, want)


SEED = 20261005
CONSUMABLES = (("blaze_rod", 7), ("blaze_rod", 1), ("ender_pearl", 12), ("ender_pearl", 3), ("ender_eye", 12),
               ("ender_eye", 4), ("blaze_powder", 6), ("white_bed", 2), ("torch", 10), ("cobblestone", 20))


def route_place(inv):
    got = next_name(inv)
    return len(goals.MILESTONES) if got is None else route_index(got)


class FuzzMilestones(unittest.TestCase):
    def test_more_in_the_bag_never_moves_the_run_back(self):
        rng = random.Random(SEED)
        pool = list(KIT) + list(CONSUMABLES)
        for case in range(60):
            base = rng.sample(pool, rng.randint(0, len(pool)))
            more = base + rng.sample(pool, rng.randint(1, 6))
            with self.subTest(case=case, base=base, more=more):
                self.assertGreaterEqual(route_place(inventory(*more)), route_place(inventory(*base)))

    def test_a_lost_tool_resets_its_milestone(self):
        rng = random.Random(SEED + 1)
        firsts = {"iron_pickaxe": "iron pickaxe", "iron_sword": "iron tools", "stone_axe": "stone tools",
                  "shield": "iron tools", "flint_and_steel": "iron tools"}
        for case in range(40):
            lost = rng.choice(sorted(firsts))
            extra = rng.sample(CONSUMABLES, rng.randint(0, 4))
            with self.subTest(case=case, lost=lost, extra=extra):
                self.assertLessEqual(route_place(kit(without=(lost,), add=extra)), route_index(firsts[lost]))


NEED_POOL = [("minecraft:stick", 4), ("minecraft:crafting_table", 1), ("tool", "pickaxe", 1), ("tool", "pickaxe", 2),
             ("minecraft:furnace", 1), ("minecraft:torch", 8), ("minecraft:iron_ingot", 3), ("stone", 16),
             ("minecraft:bucket", 1), ("tool", "sword", 1), ("tool", "axe", 1), ("minecraft:chest", 1)]
BAG_POOL = [("oak_log", 3), ("oak_planks", 6), ("stick", 4), ("cobblestone", 12), ("crafting_table", 1),
            ("furnace", 1), ("coal", 4), ("raw_iron", 3), ("iron_ingot", 2), ("wooden_pickaxe", 1),
            ("stone_pickaxe", 1)]


def replay(items, steps, needs):
    bag_, tools, bad = Counter(), [], []
    for item, n in items:
        bag_[mid(item)] += n
        material, _, kind = item.rpartition("_")
        if material in TIER:
            tools.append((kind, TIER[material]))
    for st in steps:
        if st.kind == "smelt" and held(bag_, "minecraft:furnace") < 1:
            bad.append((str(st), "furnace"))
        if st.kind == "mine" and st.detail.get("tier") is not None and \
                not any(k == "pickaxe" and t >= st.detail["tier"] for k, t in tools):
            bad.append((str(st), "pickaxe"))
        for tok, n in (st.detail.get("inputs") or {}).items():
            if n and held(bag_, tok) < n:
                bad.append((str(st), tok))
            take(bag_, tok, n)
        bag_[st.token] += st.count
        material, _, kind = bare(st.token).rpartition("_")
        if material in TIER:
            tools.append((kind, TIER[material]))
    unmet = []
    for need in needs:
        if need[0] == "tool":
            ok = any(k == need[1] and t >= need[2] for k, t in tools)
        else:
            ok = held(bag_, need[0]) >= need[1]
            take(bag_, need[0], need[1])
        if not ok:
            unmet.append(need)
    return bad, unmet


def got_raw(steps):
    out = Counter()
    for st in steps:
        if st.kind in ("gather", "mine", "hunt"):
            out[(st.kind, bare(st.token))] += st.count
    return out


class FuzzPlans(unittest.TestCase):
    def cases(self, seed, n):
        rng = random.Random(seed)
        for case in range(n):
            needs = rng.sample(NEED_POOL, rng.randint(1, 3))
            items = rng.sample(BAG_POOL, rng.randint(0, 3))
            yield case, needs, items

    def test_a_plan_replays_by_bag_arithmetic_and_holds_every_need(self):
        for case, needs, items in self.cases(SEED + 2, 30):
            with self.subTest(case=case, needs=needs, items=items):
                bad, unmet = replay(items, plan(needs, *items), needs)
                self.assertEqual((bad, unmet), ([], []))

    def test_the_order_needs_are_asked_in_costs_the_same(self):
        for case, needs, items in self.cases(SEED + 3, 30):
            with self.subTest(case=case, needs=needs, items=items):
                a, b = plan(needs, *items), plan(reversed(needs), *items)
                sa, sb = (sum(st.est for st in steps) / TICKS_PER_S for steps in (a, b))
                self.assertAlmostEqual(sa, sb, delta=max(1.0, 0.04 * min(sa, sb)), msg=(got_raw(a), got_raw(b)))


class FuzzPlaceBins(unittest.TestCase):
    def test_a_float_position_bins_with_its_block(self):
        rng = random.Random(SEED + 4)
        for case in range(500):
            feet = tuple(rng.uniform(-3000.0, 3000.0) for _ in range(3))
            cell = tuple(math.floor(c) for c in feet)
            with self.subTest(case=case, feet=feet):
                self.assertEqual(place_signature(feet, False), place_signature(cell, False))


class FuzzRunCells(unittest.TestCase):
    def test_every_cell_is_mined_or_given_back_and_lone_failures_skip_nothing(self):
        rng = random.Random(SEED + 5)
        messages = [nav.STEP_UNREACHABLE[0], nav.STEP_UNREACHABLE[1], nav.NO_STAND, "mine timed out"]
        for case in range(80):
            n = rng.randint(1, 10)
            bad = {(i, 64, 0): rng.choice(messages) for i in rng.sample(range(n), rng.randint(0, n))}
            with self.subTest(case=case, n=n, bad=sorted(bad)):
                got = RunCells.run_cells(self, n, bad)
                self.assertEqual(got["succeeded"] + len(got["failures"]), n)
                lone = not any((i, 64, 0) in bad and (i + 1, 64, 0) in bad for i in range(n))
                if lone:
                    self.assertEqual(got["succeeded"], n - len(bad))


class FuzzLanding(unittest.TestCase):
    def test_never_on_or_over_fire_and_lava(self):
        rng = random.Random(SEED + 6)
        hot = ("lava", "magma_block", "fire")
        for case in range(150):
            region = flat((-10, 55, -10), (20, 72, 10), floor_y=63)
            for x in range(1, 12):
                roll = rng.random()
                if roll < 0.2:
                    region.blocks[(x, 63, 0)] = rng.choice(hot)
                elif roll < 0.3:
                    region.blocks[(x, 64, 0)] = rng.choice(hot)
                elif roll < 0.4:
                    for y in range(58, 64):
                        region.blocks[(x, y, 0)] = "air"
                elif roll < 0.5:
                    region.blocks[(x, 64, 0)] = "stone"
                elif roll < 0.55:
                    region.blocks[(x, 63, 0)] = "water"
            got = nav.landing(region, (0.5, 64.0, 0.5), (11.5, 64.0, 0.5))
            with self.subTest(case=case, got=got):
                if got is None:
                    continue
                below = (got[0], got[1] - 1, got[2])
                self.assertFalse(region.solid(got) or region.solid((got[0], got[1] + 1, got[2])))
                self.assertNotIn(region.name(got), hot)
                self.assertNotIn(region.name(below), hot)
                self.assertTrue(region.solid(below) or region.name(below).endswith("water"))


class FuzzRescue(unittest.TestCase):
    def test_the_first_ready_hazard_is_answered_and_none_starves(self):
        rng = random.Random(SEED + 7)
        for case in range(300):
            st = state(inLava=rng.random() < 0.2, onFire=rng.random() < 0.2, inWater=rng.random() < 0.3,
                       air=rng.choice([300, 60, 0]), health=rng.choice([20.0, 8.0, 3.0, 1.0]),
                       onGround=rng.random() < 0.6, climbing=rng.random() < 0.1)
            kw = dict(buried=rng.random() < 0.15, unanswered=["zombie"] if rng.random() < 0.3 else None,
                      afloat=rng.random() < 0.2, fallen=rng.choice([0.0, 2.0, 12.0]))
            cooling = {k for k in hazard.KINDS if rng.random() < 0.4}

            def ready(name, cooling=cooling):
                return name.split(" ", 1)[1] not in cooling

            def alone(k, st=st, kw=kw):
                return hazard.rescue_due(st, ready=lambda n: n == f"rescue {k}", **kw) == k
            got = hazard.rescue_due(st, ready=ready, **kw)
            with self.subTest(case=case, got=got, cooling=sorted(cooling)):
                active = [k for k in hazard.KINDS if alone(k)]
                ready_active = [k for k in active if k not in cooling]
                self.assertEqual(got, ready_active[0] if ready_active else None)


REFLEX_KEYS = ("night", "bed_works", "bed_carried", "bed_near", "shelter_ready", "nether_bad", "enclosed",
               "sheltered", "job_ready", "machine_ready", "stuck", "in_pit", "blocked")


class FuzzNightMeal(unittest.TestCase):
    def test_a_due_meal_waits_only_for_a_cover_row_that_runs(self):
        rng = random.Random(SEED + 8)
        names = [n for n, _t, _a in reflexes.TABLE]
        for case in range(400):
            view = View({k: rng.random() < 0.5 for k in REFLEX_KEYS}, food=rng.randint(0, 20),
                        hp=rng.choice([20.0, 15.0, 9.0, 3.0]),
                        meal=rng.choice([None, "minecraft:cooked_beef"]), used_slots=0, building=0)
            cooling = {n for n in names if n != "eat" and rng.random() < 0.4}
            fired = [n for _i, n in reflexes.due(view, lambda n, c=cooling: n not in c)]
            hungry = eat_due(view["food"], view["hp"], EAT_BELOW, MAX_HP, 20) and view["meal"] is not None
            with self.subTest(case=case, fired=fired, cooling=sorted(cooling)):
                if hungry and "eat" not in fired:
                    self.assertTrue({"sleep", "shelter"} & set(fired), "the meal waits for a cover row that never runs")


class FuzzWalkLowerBound(unittest.TestCase):
    def test_never_above_the_price(self):
        rng = random.Random(SEED + 9)
        for case in range(25):
            d = rng.uniform(2.0, 25.0)
            c = cost(None, None, stone=d)
            step = mine_step()
            site, feet = c.site(step), c.snap.feet
            lb = c.walk_lb(step, None)
            at = rng.choice([None, (feet[0] + rng.randint(-15, 15), feet[1], feet[2] + rng.randint(-5, 5)),
                             (site[0] + rng.randint(-4, 4), site[1], site[2] + rng.randint(-4, 4))])
            with self.subTest(case=case, d=round(d, 1), at=at):
                st = mine_step()
                c.estimate(st, at=at)
                self.assertLessEqual(lb, sum(st.parts.get(k, 0) for k in ("walk", "dig", "surface", "seek", "refuted")))


class FuzzDriving(unittest.TestCase):
    def test_any_finish_order_leaves_no_driver(self):
        layers = [("tactic", "fight"), ("plan", "mine"), ("safety", "lava")]
        for order in itertools.permutations(range(3)):
            with self.subTest(order=order):
                motion = arbiter.Motion()
                entered = [threading.Event() for _ in layers]
                release = [threading.Event() for _ in layers]
                threads = []
                for i, (layer, why) in enumerate(layers):
                    def run(i=i):
                        entered[i].set()
                        release[i].wait(2)
                    t = threading.Thread(target=motion._run, args=(arbiter.Intent(layer, run, why, key=why),))
                    t.start()
                    entered[i].wait(2)
                    threads.append(t)
                for i in order:
                    release[i].set()
                    threads[i].join(2)
                self.assertIsNone(motion.driving)



class FuzzRouteCrafting(unittest.TestCase):
    def test_crafting_along_the_route_never_moves_the_run_back(self):
        rng = random.Random(SEED + 10)
        crafts = [("blaze_rod", 1, "blaze_powder", 2), ("blaze_powder", 1, "ender_eye", 1)]
        for case in range(80):
            counts = Counter({"blaze_rod": rng.randint(0, 9), "blaze_powder": rng.randint(0, 6),
                              "ender_pearl": rng.randint(0, 14), "ender_eye": rng.randint(0, 6)})
            before = route_place(kit(add=tuple((k, n) for k, n in counts.items() if n)))
            for _ in range(rng.randint(1, 8)):
                src, k, out, n = rng.choice(crafts)
                if counts[src] < k or (out == "ender_eye" and counts["ender_pearl"] < 1):
                    continue
                counts[src] -= k
                counts[out] += n
                if out == "ender_eye":
                    counts["ender_pearl"] -= 1
            after = route_place(kit(add=tuple((k, n) for k, n in counts.items() if n)))
            with self.subTest(case=case, counts=dict(counts)):
                self.assertGreaterEqual(after, before)

    def test_putting_on_or_off_hand_moves_nothing(self):
        rng = random.Random(SEED + 11)
        wearable = {"iron_helmet": "head", "iron_chestplate": "chest", "iron_leggings": "legs", "iron_boots": "feet",
                    "golden_helmet": "head", "shield": "offhand"}
        for case in range(60):
            moved = {item: slot for item, slot in wearable.items() if rng.random() < 0.5}
            used = set()
            worn = {}
            for item, slot in moved.items():
                if slot not in used:
                    used.add(slot)
                    worn[slot] = item
            extra = rng.sample(CONSUMABLES, rng.randint(0, 3))
            with self.subTest(case=case, worn=worn):
                self.assertEqual(route_place(kit(without=tuple(worn.values()), add=extra, worn=worn)),
                                 route_place(kit(add=extra)))


def realise(need):
    if need[0] == "tool":
        return (bare(tool_item(need[1], int(need[2]))), 1)
    token = bare(need[0])
    return {"food": ("cooked_beef", int(need[1])), "bed": ("white_bed", int(need[1])),
            "stone": ("cobblestone", int(need[1])), "building": ("cobblestone", int(need[1]))}.get(token,
                                                                                                (token, int(need[1])))


class FuzzMilestoneProgress(unittest.TestCase):
    def test_getting_what_the_next_milestone_asks_moves_past_it(self):
        rng = random.Random(SEED + 12)
        pool = list(KIT) + list(CONSUMABLES)
        for case in range(60):
            items = rng.sample(pool, rng.randint(0, len(pool)))
            for dim in ("minecraft:overworld", "minecraft:the_end"):
                inv = inventory(*items)
                got = brain.next_milestone(snapshot(state(dimension=dim), inv), memory())
                if got is None or got["args"]["name"] in goals.RUN_AFTER:
                    continue
                given = items + [realise(n) for n in goals.needs(got, bag(inv))]
                with self.subTest(case=case, dim=dim, milestone=got["args"]["name"]):
                    self.assertGreater(route_place(inventory(*given)) if dim.endswith("overworld") else
                                       route_place_in(inventory(*given), dim), route_index(got["args"]["name"]))


def route_place_in(inv, dim):
    got = brain.next_milestone(snapshot(state(dimension=dim), inv), memory())
    return len(goals.MILESTONES) if got is None else route_index(got["args"]["name"])


class FuzzTaskFile(unittest.TestCase):
    def test_no_task_is_lost_and_no_plan_outlives_its_task(self):
        rng = random.Random(SEED + 13)
        pool = [goals.have(("minecraft:torch", 1)), goals.have(("minecraft:stick", 4)),
                goals.have(("minecraft:torch", 64)), goals.have(("minecraft:chest", 1))]
        for case in range(15):
            harness = TaskFile("test_cancelled_and_expired_tasks_leave_no_held_plan")
            harness.setUp()
            tasks.save([])
            added = set()
            real = tasks.expire
            for _step in range(rng.randint(3, 10)):
                op = rng.choice(["add", "cancel", "expire", "round", "race"])
                ids = [t["id"] for t in tasks.load()]
                if op == "add":
                    added.add(tasks.add(rng.choice(pool))["id"])
                elif op == "cancel" and ids:
                    tasks.cancel(rng.choice(ids))
                elif op == "expire" and ids:
                    tasks.update(rng.choice(ids), expires=1.0)
                elif op in ("round", "race"):
                    for tid in ids:
                        harness.b.held.setdefault(tid, {"steps": [], "sig": None, "event": True, "dim": None,
                                                        "want": None})

                    def racing(items, now=None, op=op):
                        if op == "race":
                            added.add(tasks.add(rng.choice(pool))["id"])
                        return real(items, now)
                    with mock.patch.object(tasks, "expire", racing):
                        harness.propose()
                    live = {t["id"] for t in tasks.load() if t["state"] in tasks.LIVE}
                    with self.subTest(case=case, step=_step, op=op):
                        self.assertLessEqual(set(harness.b.held), live | added - {t["id"] for t in tasks.load()
                                                                                  if t["state"] not in tasks.LIVE})
            with self.subTest(case=case, end=True):
                self.assertLessEqual(added, {t["id"] for t in tasks.load()})
            harness.doCleanups()


class FuzzCostShape(unittest.TestCase):
    def test_farther_is_never_cheaper(self):
        rng = random.Random(SEED + 14)
        for case in range(25):
            d1, d2 = sorted(rng.uniform(2.0, 30.0) for _ in range(2))
            with self.subTest(case=case, d1=round(d1, 1), d2=round(d2, 1)):
                self.assertLessEqual(priced(cost(None, None, stone=d1))[0], priced(cost(None, None, stone=d2))[0])

    def test_both_lower_bounds_together_never_above_the_price(self):
        rng = random.Random(SEED + 15)
        for case in range(25):
            c = cost(None, None, stone=rng.uniform(2.0, 25.0))
            step = mine_step()
            lb = c.walk_lb(step, None) + c.dig_lb(step, None)
            st = mine_step()
            c.estimate(st)
            with self.subTest(case=case):
                self.assertLessEqual(lb, sum(st.parts.get(k, 0) for k in ("walk", "dig", "surface", "seek", "refuted")))

    def test_measured_runs_from_anywhere_price_where_they_are_asked(self):
        rng = random.Random(SEED + 16)
        for case in range(20):
            ran_at, priced_at = rng.uniform(2.0, 25.0), rng.uniform(2.0, 25.0)
            ran_total, ran_parts = priced(cost(None, memory(), stone=ran_at))
            want, _ = priced(cost(None, memory(), stone=priced_at))
            mem = memory()
            for _ in range(MIN_SAMPLES):
                mem.record_duration("mine:minecraft:cobblestone", ran_parts["work"] / TICKS_PER_S, 4)
            got, parts = priced(cost(None, mem, stone=priced_at))
            with self.subTest(case=case, ran_at=round(ran_at, 1), priced_at=round(priced_at, 1)):
                self.assertAlmostEqual(got, want, delta=max(2, want * 0.02), msg=parts)

    def test_a_banned_cell_is_never_the_site(self):
        rng = random.Random(SEED + 17)
        for case in range(40):
            dists = sorted(rng.sample(range(2, 15), rng.randint(1, 4)))
            st = state()
            snap = snapshot(st, inventory())
            snap.hits["stone"] = [{"x": st["blockX"] + d, "y": st["blockY"] - 1, "z": st["blockZ"],
                                   "block": "minecraft:stone", "distance": float(d)} for d in dists]
            cells = [(h["x"], h["y"], h["z"]) for h in snap.hits["stone"]]
            black = Versioned()
            for c in rng.sample(cells, rng.randint(0, len(cells))):
                black[c] = Ban(10 ** 12, None)
            from bonobo.cost import Cost
            site = Cost(snap, memory(), blacklist=black).site(mine_step())
            with self.subTest(case=case, banned=sorted(black), site=site):
                self.assertNotIn(site, black)
                if len(black) < len(cells):
                    self.assertIsNotNone(site)

    def test_a_walled_log_is_refused_alike_remembered_or_seen(self):
        rng = random.Random(SEED + 18)
        st = state()
        f = (st["blockX"], st["blockY"], st["blockZ"])
        lo, hi = (cell_add(f, d) for d in ROUND_GROUND[0])
        log = (f[0] + 5, f[1], f[2])
        shell = [(log[0] + dx, log[1] + dy, log[2] + dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1, 2)
                 for dz in (-1, 0, 1) if (dx, dy, dz) != (0, 0, 0)]
        for case in range(40):
            region = flat(lo, hi, floor_y=f[1] - 1)
            for c in shell:
                if rng.random() < 0.85:
                    region.blocks[c] = "bedrock"
            region.blocks[log] = "oak_log"
            mem = memory()
            mem.note_seen("tree", log, st["dimension"])
            remembered = cost(snapshot(st, inventory(), region=region), mem).site(Step("gather", "log", 1, {}))
            seen_snap = snapshot(st, inventory(), region=region)
            seen_snap.hits["oak_log"] = [{"x": log[0], "y": log[1], "z": log[2], "block": "minecraft:oak_log",
                                          "distance": 5.0}]
            seen = cost(seen_snap, memory()).site(Step("gather", "log", 1, {}))
            with self.subTest(case=case):
                self.assertEqual(remembered is None, seen is None)


class FuzzPlanShape(unittest.TestCase):
    def secs(self, steps):
        return sum(st.est for st in steps) / TICKS_PER_S

    def cases(self, seed, n):
        return FuzzPlans.cases(self, seed, n)

    def test_together_never_dearer_than_apart(self):
        for case, needs, items in self.cases(SEED + 19, 25):
            if len(needs) < 2:
                continue
            with self.subTest(case=case, needs=needs, items=items):
                together = self.secs(plan(needs, *items))
                apart = self.secs(plan(needs[:1], *items)) + self.secs(plan(needs[1:], *items))
                self.assertLessEqual(together, max(apart * 1.04 + 1.0, apart + 8.0))

    def test_more_in_the_bag_never_dearer(self):
        rng = random.Random(SEED + 20)
        for case, needs, items in self.cases(SEED + 21, 25):
            more = items + rng.sample(BAG_POOL, rng.randint(1, 3))
            with self.subTest(case=case, needs=needs, items=items, more=more):
                self.assertLessEqual(self.secs(plan(needs, *more)), self.secs(plan(needs, *items)) * 1.04 + 1.0)

    def test_what_is_held_needs_nothing(self):
        rng = random.Random(SEED + 22)
        for case in range(30):
            needs = rng.sample(NEED_POOL, rng.randint(1, 3))
            items = [realise(n) for n in needs] + [("crafting_table", 1), ("furnace", 1)]
            with self.subTest(case=case, needs=needs):
                self.assertEqual(plan(needs, *items), [])

    def test_the_exact_search_is_no_dearer_than_any_order(self):
        for case, needs, items in self.cases(SEED + 23, 15):
            exact = self.secs(plan(needs, *items, exact=True))
            for order in itertools.permutations(needs):
                with self.subTest(case=case, order=order, items=items):
                    self.assertLessEqual(exact, self.secs(plan(order, *items)) + 0.5)

    def test_the_same_question_gets_the_same_plan(self):
        for case, needs, items in self.cases(SEED + 24, 20):
            with self.subTest(case=case, needs=needs, items=items):
                self.assertEqual([str(st) for st in plan(needs, *items)], [str(st) for st in plan(needs, *items)])

    def test_a_held_plan_is_replayed_exactly_when_the_bag_still_runs_it(self):
        rng = random.Random(SEED + 25)
        for case, needs, items in self.cases(SEED + 26, 30):
            held_plan = plan(needs, *items)
            if not held_plan:
                continue
            now = list(items)
            if now and rng.random() < 0.7:
                now.pop(rng.randrange(len(now)))
            c = NullCost()
            root = Node(from_bag(bag(inventory(*now)), facts=c.facts()), [], [])
            with self.subTest(case=case, needs=needs, items=items, now=now):
                try:
                    got = Search(c).replay(root, list(needs), held_plan)
                except Exception as e:
                    self.fail(f"{type(e).__name__}: {e}")
                bad, unmet = replay(now, held_plan, needs)
                self.assertEqual(got is not None, not bad and not unmet)


class FuzzWatchFall(unittest.TestCase):
    def test_fallen_is_the_drop_since_last_support(self):
        rng = random.Random(SEED + 27)
        for case in range(100):
            w, top, y = hazard.Watch(), None, 80.0
            trace = []
            for _tick in range(40):
                support = rng.choice(["air", "air", "air", "ground", "water", "climbing"])
                y += rng.choice([-1.5, -0.8, 0.0, 0.4])
                s = dict(y=y, onGround=support == "ground", inWater=support == "water", inLava=False,
                         climbing=support == "climbing")
                got = w.fallen(s)
                if support in ("ground", "water", "climbing"):
                    top, want = None, 0.0
                else:
                    top = y if top is None else max(top, y)
                    want = top - y
                trace.append((support, round(got, 2), round(want, 2)))
            with self.subTest(case=case):
                self.assertEqual([t for t in trace if t[1] != t[2]], [], trace)


class FuzzPerception(unittest.TestCase):
    def test_a_danger_is_stopped_unless_our_own_rescue_runs(self):
        rng = random.Random(SEED + 28)
        for case in range(120):
            changes = dict(inLava=rng.random() < 0.25, onFire=rng.random() < 0.25, inWater=rng.random() < 0.25,
                           air=rng.choice([300, 40, 0]), health=rng.choice([20.0, 6.0, 2.0]),
                           onGround=rng.random() < 0.6, control={"task": {"type": "travel"}} if rng.random() < 0.6
                           else {})
            mode = rng.choice(["normal", "survival"])
            want = perception.danger(state(**changes), buried=False, fallen=0.0)
            layers, _told = PerceptionTick.tick(self, mode, changes)
            with self.subTest(case=case, mode=mode, danger=want, changes=changes):
                self.assertEqual(layers, ["safety"] if want is not None and mode != "survival" else [])

    def test_a_tick_survives_any_reading_failing(self):
        rng = random.Random(SEED + 29)
        errors = [api.McError("read failed"), api.PlayerTookControl(), ValueError("bad json"), KeyError("x"),
                  TimeoutError("slow")]
        targets = ["get", "head_buried", "note_hurt", "_look", "_answer_threats"]
        for case in range(60):
            where, err = rng.choice(targets), rng.choice(errors)
            w, body = perception.Watcher(), Recorder()

            def boom(*_a, err=err, **_k):
                raise err

            def one_tick(_s, w=w):
                w.stopped = True
            with contextlib.ExitStack() as stack:
                for target, attr, kw in [
                        (perception.time, "sleep", {"side_effect": one_tick}),
                        (perception.fight_loop, "active", {"return_value": False}),
                        (perception.STATE, "paused", {"new": False}),
                        (perception, "_eating", {"return_value": False}),
                        (perception, "note_hurt", {"side_effect": boom} if where == "note_hurt" else {}),
                        (api.STATE, "mode", {"new": "normal"}), (api.STATE, "soft", {"new": False}),
                        (api.STATE, "interrupt", {"new": None}), (api, "log", {}),
                        (api, "get", {"side_effect": boom} if where == "get" else {"return_value": state()}),
                        (api, "post", {}), (arbiter, "BODY", {"new": body}),
                        (w, "_look", {"side_effect": boom} if where == "_look" else {}),
                        (w, "_answer_threats", {"side_effect": boom} if where == "_answer_threats" else {}),
                        (w, "_time_to_die", {"return_value": None}),
                        (w, "_enderman_after_us", {"return_value": False}),
                        (w, "_breath_within", {"return_value": False}),
                        (hazard, "head_buried", {"side_effect": boom} if where == "head_buried"
                         else {"return_value": False})]:
                    stack.enter_context(mock.patch.object(target, attr, **kw))
                with self.subTest(case=case, where=where, err=type(err).__name__):
                    try:
                        w.run()
                    except BaseException as e:
                        self.fail(f"the watcher thread died on {type(e).__name__} from {where}")


class FuzzFastLayer(unittest.TestCase):
    def test_any_one_reading_failing_still_rescues_lava(self):
        rng = random.Random(SEED + 30)
        targets = [(fight_loop, "unanswered_now"), (skillcore, "head_buried_in"), (threat, "threats_seen")]
        for case in range(9):
            obj, attr = rng.choice(targets)
            err = rng.choice([KeyError("k"), ValueError("v"), api.McError("m"), AttributeError("a")])

            def boom(*_a, err=err, **_k):
                raise err
            b = brain_fixture(planning=False)
            snap = snapshot(state(inLava=True, onGround=False), inventory())
            with self.subTest(case=case, broken=f"{obj.__name__}.{attr}", err=type(err).__name__):
                with contextlib.ExitStack() as stack:
                    for target, name, value in [(b.needs, "propose", lambda *a, **k: []),
                                                (b.reflexes, "proposals", lambda *a, **k: []),
                                                (b, "light_intent", lambda *a, **k: None), (obj, attr, boom)]:
                        stack.enter_context(mock.patch.object(target, name, value))
                    try:
                        act = b._decide_round(snap, round_ctx(b, snap))
                        got = act and act.name
                    except Exception as e:
                        got = f"{type(e).__name__}: {e}"
                self.assertEqual(got, "rescue lava")


class PreemptMatrix(unittest.TestCase):
    def tearDown(self):
        api.consume_interrupt()

    def test_a_faster_layer_always_takes_the_body_and_a_slower_never(self):
        for holder, intent in itertools.product(SCALES, SCALES):
            with self.subTest(holder=holder, intent=intent):
                m, ran, t = arbiter.Motion(), [], perception.time.time()
                m.preempt(holder, lambda: None, "held", now=t, release=lambda: False)
                m.preempt(intent, lambda: ran.append(intent), "asks", now=t + 0.1)
                self.assertEqual(bool(ran), SCALES[intent] < SCALES[holder], (holder, intent))


class FuzzReflexTable(unittest.TestCase):
    def test_triggers_only_read_the_view(self):
        rng = random.Random(SEED + 31)
        for case in range(200):
            view = View({k: rng.random() < 0.5 for k in REFLEX_KEYS}, food=rng.randint(0, 20), hp=rng.choice([20.0, 4.0]),
                        meal=rng.choice([None, "minecraft:bread"]), used_slots=rng.randint(0, 36),
                        building=rng.randint(0, 64))
            before = dict(view)
            reflexes.due(view)
            with self.subTest(case=case):
                self.assertEqual(dict(view), before)


def hot(name):
    return name in ("lava", "fire", "magma_block", "soul_fire")


class FuzzWays(unittest.TestCase):
    def test_a_planned_way_stands_where_it_says_and_breaks_nothing_kept(self):
        rng = random.Random(SEED + 32)
        for case in range(80):
            region = flat((-10, 55, -10), (20, 72, 10), floor_y=63)
            for x in range(2, 12):
                roll = rng.random()
                if roll < 0.15:
                    for y in range(58, 64):
                        region.blocks[(x, y, 0)] = "air"
                elif roll < 0.25:
                    region.blocks[(x, 63, 0)] = "lava"
                elif roll < 0.35:
                    region.blocks[(x, 64, 0)] = region.blocks[(x, 65, 0)] = "stone"
                elif roll < 0.4:
                    region.blocks[(x, 64, 0)] = "stone"
            protected = {(x, y, 0) for x in range(2, 12) for y in (63, 64) if rng.random() < 0.1}
            kind = rng.choice(["stand", "mine", "use"])
            target = (rng.randint(6, 12), 64 if kind != "mine" else rng.choice([63, 61]), 0)
            blocks = rng.choice([0, 4, 20])
            inv = bag(inventory(("iron_pickaxe", 1), *((("cobblestone", blocks),) if blocks else ())))
            ground, here, steps, why, secs = nav.After(region), (0, 64, 0), [], None, 0.0
            for _segment in range(12):
                if nav.stands_for(kind, ground, here, target):
                    break
                seg, why, s_ = nav.plan_way(ground, here, target, kind, inv, frozenset(protected))
                if seg is None or not seg:
                    break
                steps += seg
                secs += s_ or 0.0
                here = nav.took(ground, seg, here)
            with self.subTest(case=case, kind=kind, target=target, blocks=blocks, why=why):
                if not steps or why is not None:
                    continue
                self.assertTrue(nav.stands_for(kind, ground, here, target), (here, steps[-4:]))
                touched = {(t["x"], t["y"], t["z"]) for t in steps if t["type"] in ("mine", "place")}
                self.assertFalse(touched & protected)
                self.assertLessEqual(sum(t["type"] == "place" for t in steps), blocks)
                for t in steps:
                    if t["type"] == "goto":
                        feet_c, below = (t["x"], t["y"], t["z"]), (t["x"], t["y"] - 1, t["z"])
                        self.assertFalse(hot(ground.name(feet_c)) or hot(ground.name(below)), t)
                self.assertGreaterEqual(secs or 0.0, 0.0)


class FuzzRetry(unittest.TestCase):
    def test_cooling_follows_failures_and_ends_with_success_or_time(self):
        rng = random.Random(SEED + 33)
        causes = ["nav", "error", "unavailable", "tool", "game", "replan", "interrupt"]
        for case in range(150):
            r, now, place = retry.Retry(), 1000.0, ((0, 4, 0), False)
            last = {}
            for _ in range(rng.randint(1, 12)):
                task = rng.choice(["a", "b"])
                now += rng.choice([0.0, 5.0, 60.0, 400.0])
                if rng.random() < 0.3:
                    r.succeeded(task)
                    last.pop(task, None)
                else:
                    v = r.failed(task, rng.choice(causes), "msg", now, place)
                    if v is not None:
                        self.assertLessEqual(v.wait, max(retry.MAX_BACKSTOP.values()))
                        last[task] = now + v.wait
            for task in ("a", "b"):
                with self.subTest(case=case, task=task):
                    if task not in last:
                        self.assertTrue(r.ready(task, now, place) or r.causes(task))
                    self.assertTrue(r.ready(task, now + max(retry.MAX_BACKSTOP.values()) + 1, place))
                    r.succeeded(task)
                    self.assertTrue(r.ready(task, now, place))


def ours(cls=Exception):
    for sub in cls.__subclasses__():
        if sub.__module__.startswith("bonobo"):
            yield sub
        yield from ours(sub)


class ErrorClasses(unittest.TestCase):
    def test_every_resume_or_replan_error_is_not_counted_as_a_failure(self):
        from bonobo import data
        for cls in sorted(set(ours()), key=lambda c: c.__name__):
            row = data.EXCEPTIONS.get(cls.__name__)
            if row is None:
                continue
            with self.subTest(cls=cls.__name__, row=row):
                self.assertEqual(row[0] in retry.NOT_FAILURES, issubclass(cls, api.INTERRUPTIONS) or row[0] == "replan")


class FuzzBans(unittest.TestCase):
    def test_a_ban_made_standing_anywhere_in_a_block_holds_in_that_block(self):
        rng = random.Random(SEED + 34)
        kinds = frozenset({"minecraft:cobblestone", "minecraft:iron_pickaxe"})
        for case in range(300):
            feet = tuple(rng.uniform(-400.0, 400.0) for _ in range(3))
            cell = tuple(math.floor(c) for c in feet)
            black = {(1, 2, 3): Ban(10 ** 12, ban_state(feet, kinds))}
            with self.subTest(case=case, feet=feet):
                self.assertTrue(banned(black, (1, 2, 3), 0.0, ban_state(cell, kinds)))



DESTROY_DELAY_TICKS, USE_DELAY_TICKS, SMELT_TICKS, LOW_DELAY_TICKS = 5, 4, 200, 10


class SmeltingRunsBeside(unittest.TestCase):
    def plan_s(self, iron, stone):
        c = cost(snapshot(state(), inventory(("raw_iron", iron), ("furnace", 1), ("coal", 4), ("iron_pickaxe", 1))),
                 None, stone=4.0)
        steps = plan_needs(c.snap.inv, [("minecraft:iron_ingot", iron), ("stone", stone)], c)
        smelt = [st for st in steps if st.kind == "smelt"]
        other = sum(st.est for st in steps if st.kind != "smelt")
        return c.plan_s(steps), smelt, other / TICKS_PER_S

    def test_a_furnace_works_while_the_body_mines(self):
        rng = random.Random(SEED + 35)
        rows = [(8, 20), (16, 40), (4, 64)] + [(rng.randint(2, 16), rng.randint(8, 64)) for _ in range(9)]
        for iron, stone in rows:
            with self.subTest(iron=iron, stone=stone):
                planned, smelt, other = self.plan_s(iron, stone)
                self.assertTrue(smelt)
                waiting = iron * SMELT_TICKS / TICKS_PER_S
                setup = LOW_DELAY_TICKS * 2 / TICKS_PER_S
                self.assertLessEqual(planned, max(waiting, other) + setup + 1.0,
                                     (planned, [str(st) for st in smelt], round(other, 1)))

    def test_the_smelt_step_itself_is_only_the_loading(self):
        for iron in (1, 8, 32):
            with self.subTest(iron=iron):
                _planned, smelt, _other = self.plan_s(iron, 8)
                self.assertLessEqual(sum(st.work if hasattr(st, "work") else st.parts.get("work", st.est)
                                         for st in smelt), LOW_DELAY_TICKS * 2)

    def test_work_that_does_not_need_the_furnace_goes_before_waiting_on_it(self):
        rng = random.Random(SEED + 37)
        others = [("stone", 20), ("minecraft:stick", 4), ("log", 4), ("minecraft:torch", 4)]
        for case in range(20):
            jobs = {"minecraft:iron_ingot": rng.randint(3, 9)}
            needs = [rng.choice([("minecraft:bucket", 1), ("tool", "sword", 2), ("tool", "pickaxe", 2)])] + \
                rng.sample(others, rng.randint(1, 2))
            rng.shuffle(needs)
            steps = plan_needs(bag(inventory(("stick", 2), ("crafting_table", 1), ("iron_pickaxe", 1), ("coal", 4),
                                             ("oak_planks", 8))), needs, NullCost(), pending=jobs, jobs=jobs)
            waits = [i for i, st in enumerate(steps) if st.kind == "await"]
            free = [i for i, st in enumerate(steps) if st.kind in ("mine", "gather")]
            with self.subTest(case=case, needs=needs, plan=[str(st) for st in steps]):
                self.assertTrue(not waits or not free or max(free) < min(waits))


class InventoryCrafts(unittest.TestCase):
    def test_a_two_by_two_recipe_never_stops_at_a_table(self):
        from bonobo.craft import recipe_needs_table
        from bonobo.knowledge import RECIPES
        c = cost()
        two = sorted(t for t in RECIPES if len(recipe_of(t)[0]) == 4)
        self.assertTrue(two)
        for token in two:
            with self.subTest(token=token):
                self.assertFalse(recipe_needs_table(token))
                self.assertFalse(c.table_back(Step("craft", token, 1, {"times": 1})))

    def test_must_fail_a_three_by_three_recipe_wants_one(self):
        from bonobo.craft import recipe_needs_table
        self.assertTrue(recipe_needs_table("minecraft:wooden_pickaxe"))


class ActionCadence(unittest.TestCase):
    def test_a_block_in_reach_costs_its_break_and_the_vanilla_delay(self):
        rows = [({"pickaxe": 0}, "minecraft:wooden_pickaxe"), ({"pickaxe": 1}, "minecraft:stone_pickaxe"),
                ({"pickaxe": 2}, "minecraft:iron_pickaxe"), ({"pickaxe": 3}, "minecraft:diamond_pickaxe"),
                ({}, "hand")]
        for n in (1, 8, 32):
            step = Step("mine", "minecraft:cobblestone", n, {"blocks": ["minecraft:stone"], "tier": 0, "breaks": n})
            for held_, item in rows:
                with self.subTest(blocks=n, tool=item):
                    per = prior_work_ticks(step, held_, TICKS_PER_S) / n
                    self.assertLessEqual(per, break_ticks("stone", item) + DESTROY_DELAY_TICKS + 1)

    def test_a_log_costs_its_break_and_the_vanilla_delay(self):
        rows = [({"axe": 0}, "minecraft:wooden_axe"), ({"axe": 2}, "minecraft:iron_axe"), ({}, "hand")]
        for n in (1, 4, 16):
            for held_, item in rows:
                with self.subTest(logs=n, tool=item):
                    per = prior_work_ticks(Step("gather", "log", n, {}), held_, TICKS_PER_S) / n
                    self.assertLessEqual(per, break_ticks("oak_log", item) + DESTROY_DELAY_TICKS + 1)

    def test_a_craft_is_a_few_clicks(self):
        c = cost()
        rows = [("minecraft:stick", 1), ("minecraft:oak_planks", 4), ("minecraft:torch", 2), ("minecraft:chest", 1),
                ("minecraft:wooden_pickaxe", 1)]
        for token, times in rows:
            with self.subTest(token=token, times=times):
                st = Step("craft", token, times, {"times": times})
                table = (USE_DELAY_TICKS + break_ticks("crafting_table", "hand") + DESTROY_DELAY_TICKS
                         if c.table_back(st) else 0)
                self.assertLessEqual(c.work(st), LOW_DELAY_TICKS + table)

    def test_a_place_costs_the_vanilla_use_delay(self):
        self.assertLessEqual(nav.PLACE_S * TICKS_PER_S, USE_DELAY_TICKS + 1)

    def test_a_batch_of_cells_is_one_chain_with_no_waits_between(self):
        rng = random.Random(SEED + 36)
        for case in range(20):
            n = rng.randint(1, 30)
            sent = []

            def run_chain(tasks_, stop_on_failure=True, wait=None):
                sent.append(list(tasks_))
                return [{"status": "succeeded"} for _ in tasks_]
            cells = [{"type": "mine", "x": i, "y": 64, "z": 0} for i in range(n)]
            with mock.patch.object(nav.api, "run_chain", run_chain), \
                    mock.patch.object(nav.api, "out_of_reach", lambda r: None), \
                    mock.patch.object(nav.api, "detail", lambda *a: None), \
                    mock.patch.object(nav.time, "sleep", side_effect=AssertionError("slept between cells")):
                nav.run_cells("mine", cells)
            with self.subTest(case=case, n=n):
                self.assertEqual(len(sent), 1)
                self.assertFalse([t for t in sent[0] if t.get("type") == "wait"])


if __name__ == "__main__":
    unittest.main()
