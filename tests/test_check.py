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
    ]

    def test_rows(self):
        for inv, facts, d, ctx, fires in self.ROWS:
            with self.subTest(inv=inv, d=d.name, facts={k: v for k, v in facts.items() if v != DOMAINS[k][0]}):
                got = oracle.CHECKS[inv](facts, d, facts, ctx)
                self.assertEqual(got is not None and not isinstance(got, oracle.Unchecked), fires, got)

    def test_every_invariant_has_a_function(self):
        ids = {f"{p}{n}" for p, top in (("S", 6), ("D", 7), ("E", 3), ("R", 5)) for n in range(1, top + 1)} | {"P1"}
        self.assertEqual(set(oracle.CHECKS), ids)

    def test_unchecked_ones_are_named(self):
        got = oracle.unchecked(of(), dec())
        self.assertEqual({k for k, why in got.items() if why == PENDING["F1"]}, set())   # check/inv/plan.py judges them
        self.assertTrue({"D4", "D6", "R1", "R2", "R4"} <= set(got))         # no plan in an empty ctx: said, not passed
        self.assertNotIn("S5", got)                                         # judged: brain.fight_line_holds
        self.assertNotIn("S4", got)                                         # must fail: a judged one said unchecked


class RoundReselection(unittest.TestCase):
    """D5: a failed act whose own key cools is not reselected."""

    def test_rows(self):
        import contextlib
        import io
        from check import round as rnd
        rows = [("the fight line's kit fails: its key cools, the task's stays", of(quarry="enderman", kit="sword"), False)]
        for name, f, want in rows:
            with self.subTest(name), contextlib.redirect_stdout(io.StringIO()):
                d, _got, ctx = rnd.decide(f)
                self.assertTrue((d.name or "").startswith("fight line"), d.name)
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


class GammaRoundTrip(unittest.TestCase):
    def test_every_value_of_every_fact(self):
        from check import round as rnd
        for k, values in DOMAINS.items():
            for v in values:
                f = of(**{k: v}, **(DEPENDS[k][1] if k in DEPENDS else {}))
                with self.subTest(fact=k, value=v):
                    _d, got, _ctx = rnd.decide(f, fail_then_again=False)
                    self.assertEqual(dict(got), dict(f))

    # (situation, facts) found apart: each round-trips
    FOUND = [("must fail: C14 a piglin by gold armour is no threat row from the first look (the kit read first)",
            {'dimension': 'minecraft:overworld', 'night': True, 'hp': 'ok', 'place': 'enclosed', 'bed': 'none',
             'pickaxe': 2, 'building': False, 'food': False, 'tree': False, 'ore': 'none', 'threat': True,
             'takeover': True, 'queued': 'none', 'cooled': True, 'hunger': 'full', 'station': 'crafting_table',
             'mob': 'creeper', 'armour': 'gold', 'bag': 'full', 'bystander': 'piglin', 'carried': 'meat_fuel',
             'chest': 'unopened', 'combat': 'shooting', 'death': 'none', 'range': 'mid', 'dps': 'read', 'dusk':
             False, 'failure': 'nav', 'fluid': 'lava', 'food_source': 'animals', 'ground': 'open', 'held':
             'same', 'idle': 'none', 'job': 'growing', 'kit': 'sword_shield', 'lit': False, 'noted': 'none',
             'pack': 'dying', 'past': 'latched', 'plan_held': 'none', 'portal': 'sites', 'quarry': 'spider',
             'repeat': 'once', 'retried': 'none', 'stock': 'none', 'task': 'planned', 'tools': 'axe_shovel',
             'trace': 'no_id', 'upkeep_held': 'none', 'weather': 'thunder'})]

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
        from check import round as rnd
        from check.facts import DIMS
        # a value its own `valid` refuses alone (a piglin calm only beside gold worn) needs other facts: not alone
        rows = [(d.NAME, v) for d in DIMS if getattr(d, "DEPENDS", None) is None for v in d.domain()
                if of(**{d.NAME: v})[d.NAME] == v]
        self.assertIn(("kit", "sword"), rows)             # must fail: kit gated on a threat again
        for name, v in rows:
            with self.subTest(fact=name, value=v):
                self.assertEqual(rnd.decide(of(**{name: v}), fail_then_again=False)[1][name], v)


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


if __name__ == "__main__":
    unittest.main()
