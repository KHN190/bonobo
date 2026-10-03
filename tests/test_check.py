"""The offline checker (check/): each oracle function on a holding row and a must-fail row; γ round-trips through α
for every value of every fact; model.step and the cycle finder."""
import unittest

from check import explore, oracle
from check.facts import DEPENDS, DOMAINS, PENDING, of
from check.round import Decision


def dec(layer="plan", kind="idle", name="idle: have pickaxe tier 1", token=None, target=None, writes=()):
    return Decision(layer, kind, token, target, writes, None, name, ())


NOTHING = Decision(None, None, None, None, (), None, None, ())


class Oracle(unittest.TestCase):
    # (id, facts, decision, ctx, fires?)
    ROWS = [
        ("S1", of(threat=True), dec(layer="tactic", name="threat:evade"), {}, False),
        ("S1", of(threat=True), dec(), {}, True),                         # must fail: a threat answered by the plan
        ("S1", of(threat=True), dec(), {"pressed": True}, True),          # must fail: a pursuer that reaches us
        ("S1", of(threat=True, ground="hole"), dec(), {"pressed": False}, False),   # walled off in a pit: no danger
        ("S1", of(threat=True, range="far"), dec(), {"pressed": False}, False),     # far: nothing reaches us yet
        ("S1", of(hp="crit"), dec(), {}, True),
        ("S2", of(place="home"), dec(), {"step_kind": "mine", "target_in_home": False}, False),
        ("S2", of(place="home"), dec(), {"step_kind": "mine", "target_in_home": True}, True),
        ("S2", of(place="home", hp="crit"), dec(layer="safety"), {"step_kind": "mine", "target_in_home": True}, False),
        ("S3", of(place="home"), dec(name="shelter: pod"), {"step_kind": "shelter"}, True),
        ("S3", of(place="open"), dec(name="shelter: pod"), {"step_kind": "shelter"}, False),
        ("S4", of(night=True), dec(layer="maintain", name="sleep"), {}, False),
        ("S4", of(night=True), dec(name="wait for day"), {}, True),        # must fail: waiting in the open
        ("S4", of(night=True), dec(), {"step_kind": "gather"}, True),
        ("S4", of(night=True, place="enclosed"), dec(), {"step_kind": "gather"}, False),
        ("S4", of(night=True), dec(), {"step_kind": "craft"}, True),       # must fail: the sky is the cover, not the kind
        ("S4", of(night=True, place="enclosed"), dec(), {"step_kind": "craft"}, False),
        ("S4", of(night=True), dec(layer="maintain", name="eat"), {}, True),     # must fail: a row is not exempt by its name
        ("S4", of(night=True), dec(layer="safety", name="rescue drowning"), {}, False),   # the body's layers come first
        ("S4", of(night=True), dec(layer="maintain", name="shelter", token="minecraft:wooden_pickaxe"),
         {"step_kind": "craft", "night_steps": [("craft", "minecraft:wooden_pickaxe"), ("shelter", "dig_in")]}, False),
        ("S4", of(night=True), dec(layer="maintain", name="shelter", token="minecraft:torch"),       # must fail: not its way's
         {"step_kind": "craft", "night_steps": [("craft", "minecraft:wooden_pickaxe")]}, True),
        ("S4", of(night=True), dec(name="night prep: have pickaxe tier 0", token="minecraft:wooden_pickaxe"),  # must fail:
         {"step_kind": "craft", "night_steps": [("craft", "minecraft:wooden_pickaxe")]}, True),   # the plan's, not the row's
        ("S4", of(night=True), dec(name="shelter: dig in"), {"step_kind": "shelter"}, False),
        ("S4", of(night=True), dec(name="food stock: have food×8"), {"step_kind": "hunt"}, True),
        ("S6", of(takeover=True), NOTHING, {}, False),
        ("S6", of(takeover=True), dec(), {}, False),                       # the jar refuses work while paused
        ("S6", of(takeover=True), dec(writes=("/run",)), {}, True),
        ("D1", of(), NOTHING, {}, True),
        ("D1", of(), dec(), {}, False),
        ("D3", of(), dec(writes=("/reflex",)), {}, True),
        ("D3", of(), dec(), {}, False),
        ("D5", of(), dec(), {"reselected": True}, True),                    # must fail: the failed step again
        ("D5", of(), dec(), {"reselected": False}, False),
        ("R3", of(night=True, bed="carried"), dec(name="wait for day"), {}, True),
        ("R3", of(night=True, bed="carried"), dec(layer="maintain", name="sleep"), {}, False),
        ("R3", of(night=True), dec(name="shelter: dig in"), {"night_way": "dig in", "step_kind": "shelter"}, False),
        ("R3", of(night=True), dec(layer="maintain", name="shelter"), {"night_way": "dig in"}, False),
        ("R3", of(night=True), dec(name="wait for day"), {"night_way": "dig in"}, True),
        ("R3", of(night=True, bed="carried"), dec(layer="maintain", name="eat"), {}, True),   # must fail: no name exempt
        ("R3", of(night=True, bed="carried"), dec(layer="safety", name="rescue drowning"), {}, False),
        ("S5", of(hp="crit"), dec(name="food stock"), {"step_kind": "hunt",
                                                        "fight_line": "health 8 under the line"}, True),
        ("S5", of(), dec(name="food stock"), {"step_kind": "hunt"}, False),     # the line holds (or no fight)
        ("R5", of(), dec(layer="tactic", token="fight"), {}, True),
        ("R5", of(threat=True), dec(layer="tactic", token="fight"), {}, False),
        ("K10", of(), dec(), {"decide_calls": [("GET", "/blocks")]}, True),      # must fail: the ground read in decide
        ("K10", of(), dec(), {"decide_calls": []}, False),
    ]

    def test_rows(self):
        for inv, facts, d, ctx, fires in self.ROWS:
            with self.subTest(inv=inv, d=d.name, facts={k: v for k, v in facts.items() if v != DOMAINS[k][0]}):
                got = oracle.CHECKS[inv](facts, d, facts, ctx)
                self.assertEqual(got is not None and not isinstance(got, oracle.Unchecked), fires, got)

    def test_every_invariant_has_a_function(self):
        ids = {f"{p}{n}" for p, top in (("S", 8), ("D", 8), ("E", 3), ("R", 5), ("P", 5)) for n in range(1, top + 1)} | {"M1", "K10", "GDEV"}
        self.assertEqual(set(oracle.CHECKS), ids)

    def test_unchecked_ones_are_named(self):
        got = oracle.unchecked(of(), dec())
        self.assertEqual({k for k, why in got.items() if why == PENDING["F1"]}, set())   # check/inv/plan.py judges them
        self.assertTrue({"D4", "D6", "P2", "P3", "P4", "P5", "R1", "R2", "R4"} <= set(got))         # no plan in an empty ctx: said, not passed
        self.assertNotIn("S5", got)                                         # judged: brain.fight_line_holds
        self.assertNotIn("S4", got)                                         # must fail: a judged one said unchecked


class RoundReselection(unittest.TestCase):
    """D5: a failed act whose own key cools is not reselected."""

    def test_rows(self):
        import contextlib
        import io
        from unittest import mock
        from bonobo import brain
        from check import round as rnd
        rows = [("the fight line's kit fails: its key cools, the task's stays", of(quarry="enderman", kit="sword"), False)]
        # the kit is given: whether the game's numbers leave one for an enderman is test_fight_line_raise's question
        kit = [[("minecraft:iron_chestplate", 1)]]
        for name, f, want in rows:
            with self.subTest(name), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(brain, "line_raisers", lambda *a, **k: kit):
                d, _got, ctx = rnd.decide(f)
                plan = [(s.kind, s.token) for s in ctx.get("plan") or []]
                self.assertEqual((d.layer, d.name), ("plan", "task t1"))         # the kit is the task plan's own steps
                self.assertIn(("craft", "minecraft:iron_chestplate"), plan, plan)
                self.assertEqual(ctx["reselected"], want)            # must fail when only the intent key is asked


class NightIsTheOverworlds(unittest.TestCase):
    def test_no_night_fact_off_the_overworld(self):
        """No night outside the Overworld."""
        self.assertFalse(of(dimension="minecraft:the_nether", night=True)["night"])
        self.assertTrue(of(night=True)["night"])

class OneRestorePoint(unittest.TestCase):
    """C12: a round starts from nothing wherever production resolved its files — a test run that imported bonobo
    before check/ set MC_DATA keeps memory and the queue in its own dir, and a home or a task left there by an earlier
    round leaked into every later one (GammaRoundTrip after test_sources: place open → home, 187 mismatches)."""

    def test_an_earlier_rounds_home_does_not_leak(self):
        import contextlib
        import io
        import os
        import tempfile
        from unittest import mock
        from bonobo import memory, tasks
        from check import round as rnd
        elsewhere = tempfile.mkdtemp(prefix="imported-first-")
        with mock.patch.object(memory, "NOTES_FILE", os.path.join(elsewhere, "world-notes.json")), \
                mock.patch.object(tasks, "FILE", os.path.join(elsewhere, "tasks.json")), \
                contextlib.redirect_stdout(io.StringIO()):
            rnd.decide(of(place="home", queued="stick"), fail_then_again=False)     # leaves a home and a task there
            _d, got, _ctx = rnd.decide(of(), fail_then_again=False)
        self.assertEqual((got["place"], got["queued"]), ("open", "none"))          # must fail: the home leaked

    def test_a_players_own_data_is_never_emptied(self):
        import os
        import shutil
        import tempfile
        from unittest import mock
        from bonobo import memory
        from check import round as rnd
        own = tempfile.mkdtemp(prefix="bonobo-own-", dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            kept = os.path.join(own, "detail.log")
            with open(kept, "w") as fh:
                fh.write("a player's log")
            with mock.patch.object(memory, "NOTES_FILE", os.path.join(own, "world-notes.json")):
                with self.assertRaises(RuntimeError):                 # must fail: a round that empties it
                    rnd.decide(of(), fail_then_again=False)
            self.assertTrue(os.path.exists(kept))
        finally:
            shutil.rmtree(own)

class KnownViolations(unittest.TestCase):
    """The baseline's known breaches (docs/refactor.md V list), each built as the decision itself and asked of the
    oracle: the checker must report it whatever production now chooses there."""
    # (invariant, facts, the breaching decision, the round's readings, what the baseline did there)
    ROWS = [
        ("S4", of(night=True, place="open", queued="stick", pickaxe=1),
         dec(kind="queue", name="task t1", token="minecraft:stick"), {"step_kind": "craft"},
         "V6: an ordinary task crafts in the open at night (no night way's step: data.NIGHT_WORK, arbiter.on_surface)"),
        ("S4", of(night=True, place="open", queued="stick"),
         dec(kind="queue", name="task t1", token="minecraft:stick"), {"step_kind": "craft"},
         "V6: the queue's craft in the open at night"),
        ("S4", of(night=True, place="open", queued="cobblestone", pickaxe=0),
         dec(kind="queue", name="task t1", token="minecraft:cobblestone"), {"step_kind": "mine"},
         "V6: a surface mine at night (17:49 stairwell)"),
    ]

    def test_reported(self):
        for inv, facts, d, ctx, why in self.ROWS:
            with self.subTest(inv=inv, why=why):
                self.assertIn(inv, [k for k, _m in oracle.violations(facts, d, facts, ctx)], d.name)


class ACrashIsAStatesOwn(unittest.TestCase):
    """explore.judged: a state whose round raises is a CRASH violation with its trace's ends; the run goes on."""

    def test_rows(self):
        from unittest import mock
        from check import round as rnd

        def boom(f, fail_then_again=True):
            raise ValueError("too many values to unpack")
        with mock.patch.object(rnd, "decide", boom):
            k, after, d, _progress, found, _mismatch, _got, _loss, _secs = explore.judged(of())
        self.assertEqual((after, d.layer, [inv for inv, _why in found]), (None, "crash", ["CRASH"]))
        self.assertIn("ValueError", found[0][1])
        # must fail: judge itself still raises (only judged isolates)
        with mock.patch.object(rnd, "decide", boom), self.assertRaises(ValueError):
            explore.judge(of())

    def test_a_hang_is_a_timeout(self):
        import time
        from unittest import mock
        from check import round as rnd

        def hang(f, fail_then_again=True):
            while True:
                try:
                    time.sleep(0.05)
                except Exception:  # guard: a production-style catch-all must not swallow the alarm
                    pass
        with mock.patch.object(rnd, "decide", hang):
            _k, after, _d, _p, found, _m, _got, _loss, secs = explore.judged(of(), timeout=1)
        self.assertEqual((after, [inv for inv, _why in found]), (None, ["TIMEOUT"]))
        self.assertIn("'night'", found[0][1])                         # the state's facts are named
        self.assertLess(secs, 5)


class TheExactSearchIsCapped(unittest.TestCase):
    """round.exact_s: the unbudgeted reference stops after its step cap and says its best is unknown."""

    def test_rows(self):
        from check import round as rnd
        from tests.world import cost, inventory, snapshot
        needs = [("tool", "pickaxe", 1)]
        # (situation, step cap) → (seconds known, why unknown)
        rows = [("a cap the search stays under: its best", 10 ** 9, (True, False)),
                ("must fail: a cap of one step: unknown, said why", 1, (False, True))]
        for name, limit, want in rows:
            with self.subTest(name):
                snap = snapshot(None, inventory())          # a snapshot each: the plan memo lives on it
                secs, why = rnd.exact_s(snap.inv, None, needs, cost(snap, oak_log=30, stone=20), limit=limit)
                self.assertEqual((secs is not None, why is not None), want, why)

    def test_a_mispruned_plan_is_reported(self):
        """P5 against the unpruned reference: a plan dearer than it on a round its budget never cut is a violation (a
        cut that dropped the cheaper way); a budget-cut round is no violation, its loss goes to the run's distribution."""
        from bonobo.game import TICKS_PER_S
        from bonobo.planner import Step
        from check.inv.plan import P5, TOL_S
        mined = Step("mine", "minecraft:cobblestone", 3, {"blocks": ["minecraft:stone"], "tier": 0}, est=0)
        ref_s = 10.0
        mined.est = int((ref_s + 2 * TOL_S) * TICKS_PER_S)
        f = of()
        d = Decision("plan", "task", "cobblestone", None, (), None, "task t1", ())
        rows = [("must fail: dearer than the reference, budget never cut", False, True),
                ("budget cut: no violation", True, False)]
        for name, spent, violated in rows:
            with self.subTest(name):
                ctx = {"plan": [mined], "exact_s": ref_s, "budget_spent": spent}
                got = P5(f, d, f, ctx)
                self.assertEqual(got is not None and not isinstance(got, oracle.Unchecked), violated, got)


class HeldPlanPricedAsTheRound(unittest.TestCase):
    """plan_held's fixture prices its steps as the planner does, over the round's own look: a craft run's mine after
    the pickaxe it crafts is priced with that pickaxe (price_as_run), not bare-handed with no look (Cost(snap, mem))."""

    def test_rows(self):
        from check import round as rnd
        f = of(plan_held="craft_run", task="tool", carried="logs", station="crafting_table")
        d, _got, ctx = rnd.decide(f, fail_then_again=False)
        # must fail: 'mine 8× cobblestone planned at 1212 ticks, the model prices it 108 now'
        self.assertEqual([v for v in oracle.violations(f, d, f, ctx) if v[0] == "D6"], [])


class FinishedRound(unittest.TestCase):
    """D1 on the production round: the round that finishes the queue's last task proposes nothing on purpose (brain
    just_finished) — the task it finished is the reason; a queue empty before the round, nothing proposed, no reason
    stated, is a violation."""

    def test_rows(self):
        from check import oracle, round as rnd
        from unittest import mock
        from bonobo import brain
        # (facts, the round's own decision kept?, D1 fires?)
        rows = [("the last task finished this round: its finishing is the reason", of(task="tool", pickaxe=2), True,
                 False),
                ("must fail: nothing queued, nothing proposed, no reason", of(), False, True)]
        for why, f, real, fires in rows:
            with self.subTest(why):
                if real:
                    d, _got, ctx = rnd.decide(f, fail_then_again=False)
                else:
                    with mock.patch.object(brain.Brain, "decide", lambda self, snap, ctx: None):
                        d, _got, ctx = rnd.decide(f, fail_then_again=False)
                self.assertIsNone(d.kind)
                self.assertEqual(oracle.D1(f, d, f, ctx) is not None, fires, d.reason)


class NightWayOnlyUnsheltered(unittest.TestCase):
    """R3's night way is asked only of an unsheltered body."""

    def test_rows(self):
        from check import round as rnd
        rows = [("must fail: walled in, the night is spent here: no way asked", of(night=True, place="enclosed",
                                                                                    pickaxe=1), False),
                ("at home: no way asked", of(night=True, place="home", pickaxe=1), False),
                ("in the open: the way is asked", of(night=True, place="open", pickaxe=1), True)]
        for why, f, asked in rows:
            with self.subTest(why):
                _d, _got, ctx = rnd.decide(f, fail_then_again=False)
                self.assertEqual("night_way" in ctx, asked, ctx.get("night_way"))
                if asked:
                    self.assertTrue(ctx["night_steps"])


def alone(k, v):
    """The fact `k` at `v` with its condition on (DEPENDS), the rest at their first values."""
    return of(**{k: v}, **(DEPENDS[k][1] if k in DEPENDS else {}))


def every_value():
    """The fewest states with each fact's every value at least once: the i-th value of every fact together, then
    alone each value those leave out. The whole corpus is check.run's."""
    m = max(len(v) for v in DOMAINS.values())
    out = [of(**{k: v[i % len(v)] for k, v in DOMAINS.items()}) for i in range(m)]
    seen = {(k, f[k]) for f in out for k in f}
    return out + [alone(k, v) for k, vs in DOMAINS.items() for v in vs if (k, v) not in seen]


def one_type(values):
    """Pure: the values of one fact are of one type (check.run sorts the corpus keys: a bool beside a str raises)."""
    return len({type(v) for v in values}) <= 1


class FactValuesOfOneType(unittest.TestCase):
    def test_every_fact(self):
        self.assertTrue(one_type((False, True)))
        self.assertFalse(one_type((False, True, "dawn")))               # must fail: what broke check.run's sort
        for k, vs in DOMAINS.items():
            with self.subTest(k):
                self.assertTrue(one_type(vs), f"{k}: {vs}")
                sorted(vs)


class ASideActPaysItsWay(unittest.TestCase):
    """GDEV (check.round.chain): an act off the task's direct plan costs at most what it saves (side + after ≤ direct)
    unless a layer above the plan forced it."""

    def test_rows(self):
        from check.inv.plan import GDEV, TOL_S
        direct, side = 100.0, 10.0
        f = of()
        # (forced, on the direct plan, seconds left after it) → flagged; a loss of side + after − direct
        rows = [("must fail: a side act that loses", False, False, direct - side + 2 * TOL_S, True),
                ("a side act that pays its way", False, False, direct - side - 2 * TOL_S, False),
                ("must fail: an on-route act flagged", False, True, direct - side + 2 * TOL_S, False),
                ("forced by a layer above the plan", True, False, direct - side + 2 * TOL_S, False)]
        for name, forced, on_route, after, flagged in rows:
            with self.subTest(name):
                chain = [{"name": "act", "layer": "plan", "step": ("hunt", "porkchop"), "forced": forced,
                          "on_route": on_route, "side_s": side, "direct_s": direct, "after_s": after}]
                got = GDEV(f, None, f, {"chain": chain})
                self.assertEqual(got is not None and not isinstance(got, oracle.Unchecked), flagged, got)

    def test_a_chain_carries_its_bag(self):
        from check import round as rnd
        rows = rnd.chain(of(task="tool"), 3)
        self.assertEqual(len(rows), 3)
        for before, after in zip(rows, rows[1:]):
            # must fail: the bag not carried (each decide priced from the start's bag again)
            self.assertAlmostEqual(before["after_s"], after["direct_s"])
            self.assertLess(after["direct_s"], before["direct_s"])
        self.assertTrue(all(r["on_route"] for r in rows))


class TheWayThereIsNotOverpriced(unittest.TestCase):
    """P3 on every node of the way to each plan the round's searches took (planner.PATHS: g + h ≤ the plan as run),
    not only the root's bound: a g priced high midway shows there, with no exact reference run."""

    def test_rows(self):
        from check.inv.plan import P3
        f = of()
        rows = [("the way under its price", [(100, [(0.0, 60.0), (40.0, 50.0), (100.0, 0.0)])], False),
                ("must fail: a node midway above the price", [(100, [(0.0, 60.0), (90.0, 40.0), (100.0, 0.0)])], True)]
        for name, paths, fires in rows:
            with self.subTest(name):
                got = P3(f, None, f, {"paths": paths})
                self.assertEqual(got is not None and not isinstance(got, oracle.Unchecked), fires, got)

    def test_a_round_whose_g_runs_high(self):
        from unittest import mock
        from bonobo import planner
        from check import round as rnd
        from check.inv.plan import P3
        real = planner.Search.emit

        def high(self, node, *a, **k):
            out = real(self, node, *a, **k)
            node.g += 10 ** 5           # a step priced far above what it takes, midway
            return out
        f = of(task="tool")
        d, _got, ctx = rnd.decide(f, fail_then_again=False)
        self.assertIsNone(P3(f, d, f, ctx))
        with mock.patch.object(planner.Search, "emit", high):
            d, _got, ctx = rnd.decide(f, fail_then_again=False)
        self.assertIsInstance(P3(f, d, f, ctx), str)           # must fail: the inflated g unseen


class UnplannableIsThisRounds(unittest.TestCase):
    """review-brain 12: `unplannable` (D1's reason when nothing is proposed) was never cleared — a round that planned
    still read the last failed round's reason."""

    def test_rows(self):
        from unittest import mock
        from bonobo import api, brain
        from tests.world import brain_fixture, round_ctx, snapshot
        b = brain_fixture()
        b.unplannable["round"] = "unplannable: a round long gone"
        snap = snapshot()
        with mock.patch.object(b, "plan_proposals", return_value=[]), \
                mock.patch.object(b.reflexes, "proposals", return_value=[]), mock.patch.object(b.needs, "propose"), \
                mock.patch.object(brain.hazard, "rescue_due", return_value=None), \
                mock.patch.object(api.STATE, "mode", "normal"):
            b.decide(snap, round_ctx(b, snap))
        self.assertEqual(b.unplannable, {})       # must fail: the stale reason read as this round's


class AGrowingCropIsNotWaitedOn(unittest.TestCase):
    """y-check's D7: task farm, a crop growing — the round chose `await` (stand by the plot) round after round. An
    await runs only when its job is due by its clock within AWAIT_MAX_S (farming.awaitable); else the plan's other
    work goes first."""

    def test_rows(self):
        import time
        from bonobo import farming
        from tests.world import memory
        now = time.time()
        rows = [("must fail: a crop ripe in an hour", now + 3600, False),
                ("due within the wait", now + farming.AWAIT_MAX_S / 2, True),
                ("due already", now - 1, True)]
        for name, ready_at, want in rows:
            with self.subTest(name):
                mem = memory()
                mem.add_job("crop", (0, 64, 0), "minecraft:overworld", "minecraft:wheat", 3, ready_at, False)
                self.assertIs(farming.awaitable(mem, "minecraft:overworld", "minecraft:wheat", now), want)

    def test_the_round_works_meanwhile(self):
        from check import explore, round as rnd
        f = of(task="farm", job="growing")
        d, _got, ctx = rnd.decide(f, fail_then_again=False)
        self.assertNotEqual(ctx.get("step_kind"), "await")
        self.assertTrue(explore.made_progress(f, d, ctx))


class StatesInTheirDomains(unittest.TestCase):
    """facts.of refuses a value outside its fact's domain or a fact no dimension defines: a state kept against an
    older domain (dusk's bools before its names; cooking and meat folded into job and carried) — r5's run crashed
    sorting a bool beside a str."""

    def test_rows(self):
        rows = [("a value of the domain", {"dusk": "dusk"}, False),
                ("must fail: dusk's old bool", {"dusk": True}, True),
                ("must fail: a removed fact", {"cooking": "iron"}, True)]
        for name, kw, refused in rows:
            with self.subTest(name):
                if refused:
                    self.assertRaises(ValueError, of, **kw)
                else:
                    self.assertEqual(of(**kw)["dusk"], kw["dusk"])

    def test_inside_a_site_is_not_the_open_sky(self):
        # must fail: y-check's S4 (night, place open, site inside: an interior with no walls round it)
        self.assertEqual(of(site="inside", place="open")["site"], "near")
        self.assertEqual(of(site="inside", place="enclosed")["site"], "inside")

    def test_the_corpus_loads(self):
        from check import fuzz
        self.assertTrue(fuzz.corpus())          # must fail: a kept file outside today's domains


class GammaRoundTrip(unittest.TestCase):
    def test_every_value_of_every_fact(self):
        from check import round as rnd
        states = every_value()
        reached = {(k, alone(k, v)[k]) for k, vs in DOMAINS.items() for v in vs}
        self.assertEqual(reached - {(k, f[k]) for f in states for k in f}, set())    # must fail: a value dropped
        for f in states:
            with self.subTest(facts={k: f[k] for k in DOMAINS}):
                _d, got, _ctx = rnd.decide(f, fail_then_again=False)
                self.assertEqual(dict(got), dict(f))

    # (situation, facts) found apart: each round-trips
    FOUND = [("must fail: C14 a piglin by gold armour is no threat row from the first look (the kit read first)",
            {'dimension': 'minecraft:overworld', 'night': True, 'hp': 'ok', 'place': 'enclosed', 'bed': 'none',
             'pickaxe': 2, 'building': False, 'food': False, 'tree': False, 'ore': 'none', 'threat': True,
             'takeover': True, 'queued': 'none', 'cooled': True, 'hunger': 'full', 'station': 'crafting_table',
             'mob': 'creeper', 'armour': 'gold', 'bag': 'full', 'bystander': 'piglin', 'carried': 'meat_fuel',
             'chest': 'unopened', 'combat': 'shooting', 'death': 'none', 'range': 'mid', 'dps': 'read', 'dusk':
             'day', 'failure': 'nav', 'fluid': 'lava', 'food_source': 'animals', 'ground': 'open', 'held':
             'same', 'idle': 'none', 'job': 'growing', 'kit': 'sword_shield', 'lit': False, 'noted': 'none',
             'pack': 'dying', 'past': 'latched', 'plan_held': 'none', 'portal': 'sites', 'quarry': 'spider',
             'repeat': 'once', 'retried': 'none', 'stock': 'none', 'task': 'tool', 'tools': 'axe_shovel',
             'trace': 'no_id', 'upkeep_held': 'none', 'weather': 'thunder'}),
             ("must fail: r5 every night way cooling, starving with no food: no way at all, so no dusk (asked True)",
              {'dimension': 'minecraft:overworld', 'night': False, 'hp': 'ok', 'place': 'home', 'bed': 'none',
               'pickaxe': 0, 'building': True, 'food': False, 'tree': False, 'ore': 'buried', 'threat': False,
               'takeover': False, 'queued': 'none', 'cooled': True, 'hunger': 'starve', 'station': 'crafting_table',
               'carried': 'raw_meat', 'chest': 'unopened', 'dusk': 'dusk', 'failure': 'nav', 'fluid': 'lava',
               'food_source': 'crops', 'ground': 'hole'})]

    def test_found(self):
        from check import round as rnd
        for name, facts in self.FOUND:
            with self.subTest(name):
                f = of(**facts)
                _d, got, _ctx = rnd.decide(f, fail_then_again=False)
                self.assertEqual(dict(got), dict(f))


class Model(unittest.TestCase):
    def test_step(self):
        rows = [(of(night=True), dec(layer="maintain", name="sleep"), {}, "night", False),
                (of(), dec(name="idle: have pickaxe tier 1"), {}, "pickaxe", 1),
                (of(threat=True), dec(layer="tactic", name="threat:fight"), {}, "threat", False),
                (of(night=True), dec(name="shelter: dig in"), {}, "place", "enclosed"),
                (of(), dec(name="idle: have food"), {}, "night", False),   # must fail would be: food sets night
                # a dimension's declared effect (check/dims/ground.step): must fail — the pit left, still in it (D7)
                (of(ground="hole"), dec(layer="maintain", name="leave the pit"), {}, "ground", "open"),
                (of(job="due"), dec(layer="maintain", name="collect job"), {}, "job", "none"),
                (of(death="near"), dec(layer="maintain", name="recover items"), {}, "death", "none"),
                (of(bag="full"), dec(layer="maintain", name="empty the bag"), {}, "bag", "room")]
        for facts, d, ctx, k, want in rows:
            with self.subTest(d=d.name):
                self.assertEqual(explore.step(facts, d, ctx)[k], want)

    def test_progress(self):
        rows = [("a gather leaves logs", of(), dec(), {"step_kind": "gather"}, True),
                ("must fail: a reach changes nothing", of(), dec(), {"step_kind": "reach"}, False),
                ("a seek ends with the thing seen", of(), dec(), {"step_kind": "seek"}, True),
                ("must fail: waiting for a day that does not come", of(dimension="minecraft:the_nether"), dec(),
                 {"step_kind": "wait"}, False),
                ("the player holds the body: the next move is theirs", of(takeover=True), dec(),
                 {"step_kind": "reach"}, True)]
        for name, facts, d, ctx, want in rows:
            with self.subTest(name):
                self.assertEqual(explore.made_progress(facts, d, ctx), want)

    def test_cycles(self):
        wait = dec(name="wait")
        graph = {"a": ("a", wait, False), "b": ("c", wait, False), "c": ("b", wait, True), "d": ("e", wait, False)}
        loops = {k for k, _n, _l in explore.cycles(graph)}
        self.assertEqual(loops, {"a"})                                      # must fail: b-c makes progress


class Dimensions(unittest.TestCase):
    """check/dims: a dimension is a file; joined to the base facts once, never over one already there."""

    def test_joined(self):
        import types
        from unittest import mock
        from check import dims, facts
        fresh = types.SimpleNamespace(NAME="check_probe", domain=lambda: (0, 1),
                                      DEPENDS=(lambda f: f["threat"], {"threat": True}))
        rows = [("a new fact: its domain and its condition joined", fresh, None),
                ("must fail: a base fact defined again", types.SimpleNamespace(NAME="night", domain=lambda: (0,)),
                 AssertionError)]
        for name, dim, raises in rows:
            with self.subTest(name), mock.patch.object(dims, "DIMS", [dim]), \
                    mock.patch.dict(facts.DOMAINS), mock.patch.dict(facts.DEPENDS):
                if raises:
                    self.assertRaises(raises, facts._with_dims)
                else:
                    facts._with_dims()
                    self.assertEqual(facts.DOMAINS[dim.NAME], dim.domain())
                    self.assertEqual(facts.of(threat=False)[dim.NAME], 0)   # its condition off: its first value


class Ungated(unittest.TestCase):
    """A dimension without DEPENDS is read in every state: each of its values stands alone (no threat, nothing cooled)
    and comes back — γ builds it whenever α reads it (kit: a sword carried with no threat about)."""

    def test_rows(self):
        from check.facts import DIMS
        # a value its own `valid` refuses alone (a piglin calm only beside gold worn) needs other facts: not alone
        rows = [(d.NAME, v) for d in DIMS if getattr(d, "DEPENDS", None) is None for v in d.domain()
                if of(**{d.NAME: v})[d.NAME] == v]
        self.assertIn(("kit", "sword"), rows)             # must fail: kit gated on a threat again
        # each round-trips in GammaRoundTrip's states (one decide per state there, none twice here)
        self.assertEqual(set(rows) - {(k, f[k]) for f in every_value() for k in f}, set())


class Queued(unittest.TestCase):
    def test_rows(self):
        from check.facts import _queued

        def task(item, state="pending"):
            return {"state": state, "args": {"needs": [[f"minecraft:{item}", 1]]}}
        # (situation, the queue) → the queued fact
        rows = [("nothing queued", [], "none"),
                ("a craft queued", [task("stick")], "stick"),
                ("must fail: a hunt (another dimension's task) read as queued", [task("beef")], "none"),
                ("the hunt first, then a craft: the craft", [task("beef"), task("stick")], "stick"),
                ("a done craft is not live", [task("stick", "done")], "none")]
        for name, items, want in rows:
            with self.subTest(name):
                self.assertEqual(_queued(items), want)


class Denominator(unittest.TestCase):
    """check/coverage.decision_code: the brain's decision code by structure — reached from the round's entry points,
    execution excluded with its reason."""

    def test_rows(self):
        from check import coverage
        decision, why = coverage.decision_code()
        name = {f"{c.co_filename.rsplit('/', 1)[-1][:-3]}.{c.co_qualname}": c for c in list(decision) + list(why)}
        # (function, None: in the denominator | the reason it is excluded)
        rows = [("brain.Brain.decide", None), ("needs.Needs.overnight", None), ("threat.options", None),
                ("gather.mine", "a skill's body"),                          # must fail: a skill body counted
                ("fight_loop._engagement", "a thread's body"),
                ("perception.Watcher.run", "a thread's body"),
                ("fight_loop.offer", "the round stands in for it"),
                ("brain.Brain.attempt", "sends to the jar"),
                # a hook handed to the door (Policy.before_segment): run as tasks are sent
                ("brain.Brain.segment_reflexes", "no entry point reaches it but through execution"),
                # a maintain row's act: run when the arbiter hands it the body (the shelter it builds, the pit left)
                ("reflexes.Maintain.shelter", "a maintain row's act"),       # must fail: counted as decision code
                ("reflexes.Maintain.leave_pit", "a maintain row's act"),
                ("reflexes.Maintain.ready_machine", None),     # also the view's (machine_ready): decision code stays
                ("reflexes.Maintain.proposals", None)]
        for fn, want in rows:
            with self.subTest(fn):
                c = name[fn]
                self.assertEqual(None if c in decision else why[c].split(" (")[0].split(":")[0], want)


class TheRoundObservesTheBag(unittest.TestCase):
    """K9: check.round runs Needs.observe as Brain._round_body does — a held plan's pickaxe worn out since last round
    is a broken tool this round."""

    def test_rows(self):
        from bonobo import needs
        from check import round as rnd
        seen = []
        real = needs.Needs.propose

        def propose(self, *a, **k):
            seen.append(set(self.broken))
            return real(self, *a, **k)
        for value, want in (("broke", {"pickaxe"}), ("none", set())):     # must fail: broke never reached needs.broken
            with self.subTest(value):
                seen.clear()
                from unittest import mock
                with mock.patch.object(needs.Needs, "propose", propose):
                    rnd.decide(of(upkeep_held=value, pickaxe=-1), fail_then_again=False)
                self.assertEqual(seen[0], want)


class InlinedGuards(unittest.TestCase):
    """check/coverage.inlined_guards: the compiler's builtin guard (any/all/tuple over a generator) by structure."""

    def test_rows(self):
        from check.coverage import inlined_guards
        rows = [(lambda xs: any(x for x in xs), 1),      # must fail: the rebound-builtin arm left in the denominator
                (lambda xs: all(x for x in xs), 1),
                (lambda xs: tuple(x for x in xs), 1),
                (lambda xs: sum(x for x in xs), 0),      # must fail: a plain call taken for a guard
                (lambda xs: [x for x in xs if x], 0)]
        for i, (fn, want) in enumerate(rows):
            with self.subTest(i):
                self.assertEqual(len(inlined_guards(fn.__code__)), want)

    def test_the_gate_counts_only_the_live_arm(self):
        from bonobo import needs
        from check.coverage import Gate, inlined_guards
        gate, c = Gate(), needs.tool_kinds.__code__
        guards = inlined_guards(c)
        self.assertTrue(guards)
        for src, left, right in c.co_branches():
            if src in guards:
                self.assertIn((c, src, left), gate.arms)
                self.assertNotIn((c, src, right), gate.arms)


class GatesInOneProcess(unittest.TestCase):
    """A second Gate in the same process records its arms (an arm DISABLEd under the first is restarted)."""

    def test_both_record(self):
        from bonobo import needs
        from check.coverage import Gate
        c = needs.tool_kinds.__code__
        for _ in range(2):                               # must fail: the second records nothing
            gate = Gate()
            with gate:
                needs.tool_kinds([])
            self.assertTrue(any(ok for (code, _s, _d), ok in gate.arms.items() if code is c))


if __name__ == "__main__":
    unittest.main()
