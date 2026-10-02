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
        ("S4", of(night=True), dec(layer="maintain", name="eat"), {}, False),
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
        self.assertEqual({k for k, why in got.items() if why == PENDING["F1"]}, {"D4", "D6", "R1", "R2", "R4"})
        self.assertNotIn("S5", got)                                         # judged: brain.fight_line_holds
        self.assertNotIn("S4", got)                                         # must fail: a judged one said unchecked


class KnownViolations(unittest.TestCase):
    """The baseline's known breaches (docs/refactor.md V list, scratchpad audits), each one on the production round:
    the checker must report it. A row that stops firing is a blind checker or a fixed production — never edited to
    pass."""
    ROWS = [   # (invariant, facts, what the baseline does there)
        ("S4", of(night=True, queued="stick"), "V6: night prep crafts in the open (data.NIGHT_WORK, arbiter.on_surface)"),
        ("S4", of(night=True, queued="stick", cooled=True), "V6: the queue's craft in the open at night"),
        ("S4", of(night=True, queued="cobblestone", pickaxe=0, cooled=True), "V6: a surface mine at night (17:49 stairwell)"),
        ("S4", of(night=True), "test_arbiter:295 NightUnderCover: wait for day in the open"),
    ]

    def test_reported(self):
        from check import round as rnd
        for inv, facts, why in self.ROWS:
            with self.subTest(inv=inv, why=why):
                d, _got, ctx = rnd.decide(facts)
                self.assertIn(inv, [k for k, _m in oracle.violations(facts, d, facts, ctx)], d.name)


class GammaRoundTrip(unittest.TestCase):
    def test_every_value_of_every_fact(self):
        from check import round as rnd
        for k, values in DOMAINS.items():
            for v in values:
                f = of(**{k: v}, **(DEPENDS[k][1] if k in DEPENDS else {}))
                with self.subTest(fact=k, value=v):
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
                (of(ground="hole"), dec(layer="maintain", name="leave the pit"), {}, "ground", "open")]
        for facts, d, ctx, k, want in rows:
            with self.subTest(d=d.name):
                self.assertEqual(explore.step(facts, d, ctx)[k], want)

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
                ("brain.Brain.segment_reflexes", "no entry point reaches it but through execution")]
        for fn, want in rows:
            with self.subTest(fn):
                c = name[fn]
                self.assertEqual(None if c in decision else why[c].split(" (")[0].split(":")[0], want)


if __name__ == "__main__":
    unittest.main()
