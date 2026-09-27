"""Table tests for the pure functions of the fight / estimate / decompose group.

One table per function (or per behaviour), each with a normal row, a boundary row and a must-fail row whose reason is
written beside it. Expected values are worked out by hand from the function's contract; nothing here reads source.
"""
import math
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import combat_model as cm  # noqa: E402
from bonobo import combat_tape, decompose, dispatch, end, estimate, field, fight_loop, fight_plan  # noqa: E402
from bonobo.api import McError, NavFailed, NotAvailable  # noqa: E402
from bonobo.planner import Step, Unplannable  # noqa: E402

INF = float("inf")
CLOUD = "minecraft:area_effect_cloud"


def frame(tick, *, phase=None, present=True, hp=20, pos=(0, 0, 0), dragon_hp=None, breath=(), damage=None,
          endermen=()):
    f = {"tick": tick, "player": {"pos": {"x": pos[0], "y": pos[1], "z": pos[2]},
                                  "vel": {"x": 0, "y": 0, "z": 0}, "hp": hp}}
    if phase is not None or not present:
        d = {"present": present, "phase": phase}
        if dragon_hp is not None:
            d["health"] = dragon_hp
        f["dragon"] = d
    else:
        f["dragon"] = None
    if breath:
        f["breath"] = [{"type": CLOUD, "pos": {"x": b[0], "y": b[1], "z": b[2]}, "radius": b[3]} for b in breath]
    if endermen:
        f["endermen"] = list(endermen)
    if damage is not None:
        f["damage"] = damage
    return f


# ------------------------------------------------------------------------------------------------ combat_model


class PhaseSpans(unittest.TestCase):
    def test_table(self):
        rows = [
            ("empty tape", [], []),
            ("run collapses", [frame(0, phase=6), frame(1, phase=6), frame(2, phase=7)], [(6, 0, 1), (7, 2, 2)]),
            ("no dragon is phase None", [frame(0)], [(None, 0, 0)]),
            # must-fail: a phase reported while the dragon is absent is not that phase
            ("absent dragon ignores phase", [frame(0, phase=6, present=False)], [(None, 0, 0)]),
            ("same phase split by a gap is two runs",
             [frame(0, phase=6), frame(1), frame(2, phase=6)], [(6, 0, 0), (None, 1, 1), (6, 2, 2)]),
        ]
        for name, frames, want in rows:
            with self.subTest(name):
                self.assertEqual(cm.phase_spans(frames), want)


class PhaseStats(unittest.TestCase):
    def test_table(self):
        rows = [
            ("empty tape", [], {}),
            ("one run of two ticks", [frame(0, phase=6), frame(1, phase=6)],
             {6: {"n": 1, "min_s": 0.1, "max_s": 0.1, "mean_s": 0.1}}),
            ("two runs of one phase average",
             [frame(0, phase=6), frame(1, phase=7), frame(2, phase=6), frame(3, phase=6), frame(4, phase=6)],
             {6: {"n": 2, "min_s": 0.05, "max_s": 0.15, "mean_s": 0.1},
              7: {"n": 1, "min_s": 0.05, "max_s": 0.05, "mean_s": 0.05}}),
            # must-fail: an absent dragon is still timed, under None, not dropped
            ("absent dragon timed as None", [frame(0), frame(1)],
             {None: {"n": 1, "min_s": 0.1, "max_s": 0.1, "mean_s": 0.1}}),
        ]
        for name, frames, want in rows:
            with self.subTest(name):
                self.assertEqual(cm.phase_stats(frames), want)


class FitDamage(unittest.TestCase):
    def test_table(self):
        z = "minecraft:zombie"
        rows = [
            ("no frames", [], {}),
            ("one hit", [frame(0, damage=[{"amount": 4, "nearest": z}])], {z: {"n": 1, "max": 4, "mean": 4.0}}),
            ("two hits averaged", [frame(0, damage=[{"amount": 2, "nearest": z}]),
                                   frame(1, damage=[{"amount": 5, "nearest": z}])],
             {z: {"n": 2, "max": 5, "mean": 3.5}}),
            ("no source is unknown", [frame(0, damage=[{"amount": 1}])], {"unknown": {"n": 1, "max": 1, "mean": 1.0}}),
            # must-fail: a frame with no damage list contributes nothing
            ("frames without damage", [frame(0), frame(1, damage=[])], {}),
        ]
        for name, frames, want in rows:
            with self.subTest(name):
                self.assertEqual(cm.fit_damage(frames), want)


class StepOptions(unittest.TestCase):
    def test_table(self):
        # (reach, step, pos, count, first, first step east)
        rows = [
            ("default: 8 directions x 4 steps + stay", 4.0, 1.0, (10, 64, -3), 33, (10, 64, -3), (11.0, 64, -3.0)),
            ("reach 2", 2.0, 1.0, (0, 0, 0), 17, (0, 0, 0), (1.0, 0, 0.0)),
            ("longer step", 4.0, 2.0, (0, 0, 0), 17, (0, 0, 0), (2.0, 0, 0.0)),
            # must-fail: a reach shorter than a step still offers one step each way, never only standing still
            ("reach below step", 0.5, 1.0, (0, 0, 0), 9, (0, 0, 0), (1.0, 0, 0.0)),
        ]
        for name, reach, step, pos, n, first, east in rows:
            with self.subTest(name):
                f = frame(0, pos=pos)
                out = cm.step_options(f, reach=reach, step=step)
                self.assertEqual((len(out), out[0], out[1]), (n, first, east))


class Hypotheses(unittest.TestCase):
    def test_table(self):
        c, z0 = (0, 0, 0), (0.0, 0.0, 0.0)
        z = "minecraft:zombie"
        rows = [
            ("bare hazard: continue + stop", (c, 1), None, [(c, 1, z0, None), (c, 1, z0, None)]),
            ("moving mob with here: pursue added", (c, 1, (1, 0, 0), z), (10, 0, 0),
             [(c, 1, (1, 0, 0), z), (c, 1, z0, z), (c, 1, (4.3, 0.0, 0.0), z)]),
            # must-fail: a static kind never pursues
            ("static head never pursues", (c, 6, (1, 0, 0), "dragon_head"), (10, 0, 0),
             [(c, 6, (1, 0, 0), "dragon_head"), (c, 6, z0, "dragon_head")]),
            ("here on the centre: no direction", (c, 1, z0, z), c, [(c, 1, z0, z), (c, 1, z0, z)]),
        ]
        for name, hz, here, want in rows:
            with self.subTest(name):
                self.assertEqual(cm.hypotheses(hz, here), want)


class Expand(unittest.TestCase):
    def test_table(self):
        c, z0 = (0, 0, 0), (0.0, 0.0, 0.0)
        z = "minecraft:zombie"
        rows = [
            ("nothing", [], None, []),
            ("one bare hazard", [(c, 1)], None, [(c, 1, z0, None), (c, 1, z0, None)]),
            ("mob and cloud with here", [(c, 1, z0, z), (c, 3, z0, CLOUD)], (0, 0, 5),
             [(c, 1, z0, z), (c, 1, z0, z), (c, 1, (0.0, 0.0, 4.3), z), (c, 3, z0, CLOUD), (c, 3, z0, CLOUD)]),
            # must-fail: without `here` nothing can pursue
            ("mob without here", [(c, 1, z0, z)], None, [(c, 1, z0, z), (c, 1, z0, z)]),
        ]
        for name, hazards, here, want in rows:
            with self.subTest(name):
                self.assertEqual(cm.expand(hazards, here), want)


class Safest(unittest.TestCase):
    def test_table(self):
        cloud = (0, 0, 0, 3)
        calm = {"type": "minecraft:enderman", "angry": False, "pos": {"x": 1, "y": 0, "z": 0}}
        rows = [
            ("no hazards: first option", {}, [(1, 0, 0), (2, 0, 0)], ((1, 0, 0), INF)),
            ("outside the cloud beats inside", {"breath": [cloud]}, [(0, 0, 0), (10, 0, 0)], ((10, 0, 0), INF)),
            ("tie on time broken by distance", {"breath": [cloud]}, [(5, 0, 0), (10, 0, 0)], ((10, 0, 0), INF)),
            ("all inside: the least bad", {"breath": [cloud]}, [(0, 0, 0), (1, 0, 0)], ((0, 0, 0), -0.3)),
            # must-fail: a neutral enderman is not a hazard
            ("calm enderman ignored", {"endermen": [calm]}, [(1, 0, 0), (9, 0, 0)], ((1, 0, 0), INF)),
        ]
        for name, kw, options, want in rows:
            with self.subTest(name):
                self.assertEqual(cm.safest(frame(0, **kw), options=options), want)


class CouldHaveLived(unittest.TestCase):
    def test_table(self):
        drown = (0, 0, 0, 100)
        rows = [
            ("no death", [frame(0), frame(40)], []),
            ("open ground: escape where we stood", [frame(0), frame(40, hp=0)],
             [{"tick": 0, "hp": 20, "escape": (0, 0, 0), "margin": INF}]),
            ("tape starts late: nearest later frame", [frame(40, hp=0)],
             [{"tick": 40, "hp": 0, "escape": (0, 0, 0), "margin": INF}]),
            # must-fail: inside a cloud bigger than any step there is no escape
            ("covered everywhere: no escape", [frame(0, breath=[drown]), frame(40, hp=0)],
             [{"tick": 0, "hp": 20, "escape": None, "margin": -0.3}]),
        ]
        for name, frames, want in rows:
            with self.subTest(name):
                self.assertEqual(cm.could_have_lived(frames), want)


class Windows(unittest.TestCase):
    def test_table(self):
        rows = [
            ("empty", [], None, []),
            ("one window", [frame(0, phase=6, dragon_hp=100), frame(1, phase=6, hp=18, dragon_hp=90)], None,
             [{"phase": 6, "start": 0, "duration_s": 0.1, "exposure_s": 0.0, "hp_lost": 2, "dragon_hp_lost": 10}]),
            ("one exposed frame", [frame(0, phase=7, dragon_hp=50, breath=[(0, 0, 0, 3)]),
                                   frame(1, phase=7, dragon_hp=50)], None,
             [{"phase": 7, "start": 0, "duration_s": 0.1, "exposure_s": 0.05, "hp_lost": 0, "dragon_hp_lost": 0}]),
            ("single frame: no dragon delta", [frame(5, phase=6, dragon_hp=100)], None,
             [{"phase": 6, "start": 5, "duration_s": 0.05, "exposure_s": 0.0, "hp_lost": 0, "dragon_hp_lost": 0.0}]),
            # must-fail: a phase outside the window set is not a window
            ("flying phase is no window", [frame(0, phase=0), frame(1, phase=0)], None, []),
            ("custom window set", [frame(0, phase=3, dragon_hp=10)], {3},
             [{"phase": 3, "start": 0, "duration_s": 0.05, "exposure_s": 0.0, "hp_lost": 0, "dragon_hp_lost": 0.0}]),
        ]
        for name, frames, phases, want in rows:
            with self.subTest(name):
                got = cm.windows(frames) if phases is None else cm.windows(frames, phases)
                self.assertEqual(got, want)


class NoteHazards(unittest.TestCase):
    def setUp(self):
        self.saved = (cm.HAZARDS, cm.HAZARDS_AT)

    def tearDown(self):
        cm.HAZARDS, cm.HAZARDS_AT = self.saved

    def test_table(self):
        kind = next(iter(cm.HAZARD_R))
        r = cm.HAZARD_R[kind]
        rows = [
            ("nothing near", [], 5.0, []),
            ("None near", None, 6.0, []),
            ("one hazard", [{"type": kind, "x": 1, "y": 2, "z": 3}], 7.0, [((1, 2, 3), r)]),
            # must-fail: something with no keep-out radius is not a hazard
            ("harmless kind dropped", [{"type": "test:nothing", "x": 0, "y": 0, "z": 0}], 8.0, []),
        ]
        for name, near, now, want in rows:
            with self.subTest(name):
                got = cm.note_hazards(near, now=now)
                self.assertEqual((got, cm.HAZARDS, cm.HAZARDS_AT), (want, want, now))


# ------------------------------------------------------------------------------------------------ combat_tape


class Available(unittest.TestCase):
    def test_table(self):
        def ok(since=-1):
            return []

        def gone(since=-1):
            raise McError("404")

        def broken(since=-1):
            raise OSError("refused")
        rows = [
            ("route answers", ok, True),
            ("route answers empty dict", lambda since=-1: {}, True),
            # must-fail: an old jar without the route is not available
            ("route missing", gone, False),
            ("connection refused", broken, False),
        ]
        for name, fake, want in rows:
            with self.subTest(name), mock.patch.object(combat_tape, "frames", fake):
                self.assertEqual(combat_tape.available(), want)


class EventStreamPoll(unittest.TestCase):
    def test_table(self):
        # (start seq, start missed, reply or exception, events out, seq after, missed after)
        rows = [
            ("first read takes max seq", 0, 0, {"events": [{"seq": 3}, {"seq": 5}], "oldest": 1},
             [{"seq": 3}, {"seq": 5}], 5, 0),
            ("gap counted", 5, 0, {"events": [{"seq": 9}], "oldest": 8}, [{"seq": 9}], 9, 2),
            ("oldest right after seq: no gap", 5, 1, {"events": [{"seq": 6}], "oldest": 6}, [{"seq": 6}], 6, 1),
            ("timeout advances to latest", 5, 0, {"events": [], "latest": 7}, [], 7, 0),
            ("latest behind never rewinds", 5, 0, {"latest": 2}, [], 5, 0),
            # must-fail: a failed read is a keep-alive, not a reset
            ("transport error", 5, 3, OSError("down"), [], 5, 3),
        ]
        for name, seq, missed, reply, want, seq_after, missed_after in rows:
            with self.subTest(name):
                def fake(since=0, timeout_ms=1000, reply=reply):
                    if isinstance(reply, Exception):
                        raise reply
                    return reply
                s = combat_tape.EventStream()
                s.seq, s.missed = seq, missed
                with mock.patch.object(combat_tape, "events", fake):
                    got = s.poll()
                self.assertEqual((got, s.seq, s.missed), (want, seq_after, missed_after))


# ------------------------------------------------------------------------------------------------ decompose


class Inv:
    def __init__(self, **counts):
        self.counts = counts

    def count(self, token):
        return self.counts.get(token, 0)


def steps_of(steps):
    return [(s.kind, s.token, s.count, s.detail) for s in steps]


class Register(unittest.TestCase):
    def setUp(self):
        self.saved = (dict(decompose.SOLVERS), list(decompose.ORDER))

    def tearDown(self):
        decompose.SOLVERS.clear()
        decompose.SOLVERS.update(self.saved[0])
        decompose.ORDER[:] = self.saved[1]

    def test_table(self):
        def a(*_):
            return "a"

        def b(*_):
            return "b"
        # each row registers on top of the previous ones: (name, fn, ORDER tail, SOLVERS[name])
        rows = [
            ("new name appended", "t_one", a, ["t_one"], a),
            ("second name after it", "t_two", b, ["t_one", "t_two"], b),
            # must-fail: registering again replaces the solver but never duplicates it in the order
            ("re-register replaces, no duplicate", "t_one", b, ["t_one", "t_two"], b),
            ("third", "t_three", a, ["t_one", "t_two", "t_three"], a),
        ]
        base = len(self.saved[1])
        for name, key, fn, tail, want in rows:
            with self.subTest(name):
                decompose.register(key, fn)
                self.assertEqual((decompose.ORDER[base:], decompose.SOLVERS[key]), (tail, want))


class FromContainers(unittest.TestCase):
    D = "minecraft:diamond"

    def cost(self, stored, est=7):
        mem = SimpleNamespace(stored=lambda token, dim: list(stored))
        snap = SimpleNamespace(dimension="minecraft:overworld", feet=(0, 0, 0))
        return SimpleNamespace(mem=mem, snap=snap, estimate=lambda step: est, plan_s=lambda steps: 0.0)

    def test_table(self):
        D = self.D
        two = [((10, 0, 0), D, 3), ((1, 0, 0), D, 4)]
        rows = [
            ("no memory", Inv(), [(D, 5)], SimpleNamespace(), {"a": 1}, [], {"a": 1}),
            ("nearest chest first, then the next", Inv(), [(D, 5)], self.cost(two), None,
             [("withdraw", D, 4, {"pos": [1, 0, 0]}), ("withdraw", D, 1, {"pos": [10, 0, 0]})], {D: 5}),
            ("pending counts against the need", Inv(), [(D, 5)], self.cost(two), {D: 3},
             [("withdraw", D, 2, {"pos": [1, 0, 0]})], {D: 5}),
            ("already held", Inv(**{D: 5}), [(D, 5)], self.cost(two), None, [], {}),
            ("tool needs skipped", Inv(), [("tool", "pickaxe", 1)], self.cost(two), None, [], {}),
            # must-fail: a withdrawal that costs no less than making it is not taken
            ("fetching never cheaper", Inv(), [(D, 5)], self.cost(two, est=math.inf), None, [], {}),
        ]
        for name, inv, needs, cost, pending, want_steps, want_extra in rows:
            with self.subTest(name):
                steps, extra = decompose.from_containers(inv, needs, cost, solver="t_no_such_solver",
                                                         pending=pending)
                self.assertEqual((steps_of(steps), extra), (want_steps, want_extra))


class EffectDetail(unittest.TestCase):
    def test_table(self):
        rows = [
            ("hunt reads the table", ("hunt", "minecraft:blaze_rod", 1), {"types": ["minecraft:blaze"]}),
            ("mine by short name", ("mine", "coal", 2), {"blocks": ["coal_ore", "deepslate_coal_ore"], "tier": 0}),
            ("take", ("take", "minecraft:crafting_table", 1), {"blocks": ["crafting_table"]}),
            ("craft runs count times", ("craft", "minecraft:stick", 3), {"times": 3, "inputs": {}}),
            # must-fail: a hunt of something no mob drops has no detail to offer
            ("hunt of an ore", ("hunt", "minecraft:diamond", 1), {}),
            ("unknown kind", ("smelt", "minecraft:iron_ingot", 1), {}),
        ]
        for name, args, want in rows:
            with self.subTest(name):
                self.assertEqual(decompose.effect_detail(*args), want)


class FromSources(unittest.TestCase):
    def test_table(self):
        dirt = Step("mine", "minecraft:dirt", 1, {})
        pearl = Step("barter", "piglin", 1, {})
        P = "minecraft:ender_pearl"
        # (needs, inv, pending, cheapest answer, steps, extra, cheapest asked (token, short))
        rows = [
            ("group counted under what it gives", [("building", 5)], Inv(), None, ([dirt], "dig by hand"),
             [dirt], {"minecraft:dirt": 5}, [("building", 5)]),
            ("item counted as itself", [(P, 2)], Inv(), None, ([pearl], "barter"), [pearl], {P: 2}, [(P, 2)]),
            ("pending shortens the need", [(P, 3)], Inv(), {P: 1}, ([pearl], "barter"), [pearl], {P: 3}, [(P, 2)]),
            ("held: not asked", [(P, 2)], Inv(**{P: 2}), None, ([pearl], "barter"), [], {}, []),
            ("no other source", [("minecraft:stick", 4), ("tool", "pickaxe", 1)], Inv(), None,
             ([pearl], "barter"), [], {}, []),
            # must-fail: when no source is cheapest nothing is planned or counted
            ("nothing chosen", [(P, 2)], Inv(), None, (None, None), [], {}, [(P, 2)]),
        ]
        for name, needs, inv, pending, answer, want_steps, want_extra, want_asked in rows:
            with self.subTest(name):
                asked = []

                def fake(key, amount, default, *a, answer=answer, **kw):
                    asked.append((key, amount))
                    return answer
                with mock.patch.object(decompose, "cheapest", fake):
                    steps, extra = decompose.from_sources(inv, needs, SimpleNamespace(), pending=pending)
                self.assertEqual((steps, extra, asked), (want_steps, want_extra, want_asked))


# ------------------------------------------------------------------------------------------------ dispatch


class RunnerFor(unittest.TestCase):
    def test_table(self):
        def runner(ctx, *a):
            return a
        contract = SimpleNamespace(runner=runner)
        rows = [
            ("named skill with args", Step("skill", "t_skill", 1, {"args": [1, 2]}), {"t_skill": contract}, None,
             (runner, (1, 2))),
            ("named skill without args", Step("skill", "t_skill", 1, {}), {"t_skill": contract}, None, (runner, ())),
            # must-fail: an unregistered skill has no runner
            ("unregistered skill", Step("skill", "t_missing", 1, {}), {}, None, None),
            ("other kinds go to the provider", Step("mine", "minecraft:coal", 1, {}), {}, (runner, ("x",)),
             (runner, ("x",))),
            ("no provider", Step("mine", "minecraft:coal", 1, {}), {}, None, None),
        ]
        for name, step, registry, provided, want in rows:
            with self.subTest(name), mock.patch.dict(dispatch.skillkit.REGISTRY, registry), \
                    mock.patch.object(dispatch.skillkit, "provider", lambda ctx, step, p=provided: p):
                self.assertEqual(dispatch.runner_for(SimpleNamespace(), step), want)


class RunStep(unittest.TestCase):
    def test_table(self):
        na, nav_fail = NotAvailable("none in range"), NavFailed("stuck")

        def returns(ctx, *a):
            return ("done", ctx.night)

        def raiser(exc):
            def run(ctx, *a):
                raise exc
            return run
        # (kind, runner or None, seek, returns, raises)
        rows = [
            ("runner result, night set", "craft", returns, True, ("done", True), None),
            ("seeking step hands back NotAvailable", "mine", raiser(na), True, na, None),
            ("no seek: raised", "mine", raiser(na), False, None, NotAvailable),
            ("non-seeking kind: raised", "craft", raiser(na), True, None, NotAvailable),
            # must-fail: a navigation failure is never answered by looking elsewhere
            ("nav failure raised", "hunt", raiser(nav_fail), True, None, NavFailed),
            ("no skill provides it", "mine", None, True, None, McError),
        ]
        for name, kind, runner, seek, want, exc in rows:
            with self.subTest(name):
                found = None if runner is None else (runner, ())
                ctx = SimpleNamespace(night=None)
                step = Step(kind, "minecraft:coal", 1, {})
                with mock.patch.object(dispatch.skillkit, "provider", lambda c, s, f=found: f):
                    if exc is None:
                        self.assertEqual(dispatch.run_step(ctx, step, True, seek=seek), want)
                    else:
                        with self.assertRaises(exc):
                            dispatch.run_step(ctx, step, True, seek=seek)


# ------------------------------------------------------------------------------------------------ end


class Bombable(unittest.TestCase):
    def test_table(self):
        rows = [
            ("scanning", {"phase": 6}, True),
            ("attacking", {"phase": 7}, True),
            ("flaming: breath on the perch", {"phase": 5}, False),
            # must-fail: no dragon means no window
            ("no dragon", None, False),
            ("no phase", {}, False),
        ]
        for name, dragon, want in rows:
            with self.subTest(name):
                self.assertEqual(end.bombable(dragon), want)


# ------------------------------------------------------------------------------------------------ estimate


class HorizonS(unittest.TestCase):
    def test_table(self):
        rows = [
            ("given", 8, 8.0),
            ("zero is a horizon, not missing", 0, 0.0),
            ("string number", "2.5", 2.5),
            # must-fail: None is not zero, it is the configured account
            ("default", None, float(estimate.ENGAGE["work_horizon_s"])),
        ]
        for name, h, want in rows:
            with self.subTest(name):
                self.assertEqual(estimate.horizon_s(h), want)


class ReachesShare(unittest.TestCase):
    def test_table(self):
        hide = float(estimate.ENGAGE["hide_per_block"])
        stop = float(estimate.ENGAGE["melee_stop_blocks"])
        archer, walker, squeezer = {"ranged": True}, {}, {"squeezes": True}
        rows = [
            ("no shape", None, walker, 1.0),
            ("block between delays only", ("between", 5), walker, 1.0),
            ("walker, one block up", ("up", 1), walker, max(0.0, 1.0 - 1 / stop)),
            ("walker, far up: zero", ("up", 1000), walker, 0.0),
            ("archer, one down", ("down", 1), archer, max(0.0, 1.0 - hide)),
            ("archer, deep: zero", ("down", 1000), archer, 0.0),
            ("archer, up: still shoots", ("up", 3), archer, 1.0),
            ("zero blocks: no change", ("up", 0), walker, 1.0),
            # must-fail: what squeezes is unmoved by any shape
            ("squeezer", ("up", 1000), squeezer, 1.0),
        ]
        for name, shape, mob, want in rows:
            with self.subTest(name):
                self.assertAlmostEqual(estimate.reaches_share(shape, mob), want, places=9)


class FatalChance(unittest.TestCase):
    def test_table(self):
        rows = [
            ("damage equals hp", (10, 10), math.exp(-1)),
            ("small damage", (20, 2), math.exp(-10)),
            ("hp floored at 0.1", (0, 1), math.exp(-0.1)),
            ("capped", (1, 100, 0.2), 0.2),
            # must-fail: no damage is never fatal, whatever the health
            ("no damage", (1, 0), 0.0),
            ("negative damage", (1, -5), 0.0),
        ]
        for name, args, want in rows:
            with self.subTest(name):
                self.assertAlmostEqual(estimate.fatal_chance(*args), want, places=12)


class DamageOver(unittest.TestCase):
    def test_table(self):
        rows = [
            ("rate times seconds", (2, 3), 6.0),
            ("half rate", (0.5, 4), 2.0),
            ("zero seconds", (5, 0), 0.0),
            # must-fail: a negative rate or time never heals
            ("negative rate", (-1, 3), 0.0),
            ("negative time", (2, -1), 0.0),
        ]
        for name, args, want in rows:
            with self.subTest(name):
                self.assertEqual(estimate.damage_over(*args), want)


class StatePriceS(unittest.TestCase):
    def test_table(self):
        class Model:
            def price(self, state):
                return state.get("hp_lost", 0) * 3.0
        rows = [
            ("priced by the model", {"hp_lost": 2}, 6.0),
            ("free state", {}, 0.0),
            ("fractional", {"hp_lost": 0.5}, 1.5),
            # must-fail: the model's own sign is kept, never clamped
            ("negative price", {"hp_lost": -1}, -3.0),
        ]
        for name, state, want in rows:
            with self.subTest(name):
                self.assertEqual(estimate.state_price_s(Model(), state), want)


# ------------------------------------------------------------------------------------------------ field


class BucketOf(unittest.TestCase):
    def test_table(self):
        rows = [
            ("empty state is open", {}, "open"),
            ("enclosed wins", {"enclosed": True, "y": 10, "skyLight": 0}, "enclosed"),
            ("dark at 4", {"skyLight": 4}, "underground"),
            ("light 5 is open", {"skyLight": 5}, "open"),
            ("below 50", {"y": 49}, "underground"),
            # must-fail: y 50 is not underground
            ("at 50", {"y": 50}, "open"),
            ("enclosed False", {"enclosed": False}, "open"),
        ]
        for name, state, want in rows:
            with self.subTest(name):
                self.assertEqual(field.bucket_of(state), want)


# ------------------------------------------------------------------------------------------------ fight_loop


class EngagementOver(unittest.TestCase):
    def test_table(self):
        far = estimate.row((100, 0, 0), 1.0, (0, 0, 0), "test:nothing", dps=0.0)
        near = estimate.row((1, 0, 0), 1.0, (0, 0, 0), "test:nothing", dps=0.0)
        closing = estimate.row((100, 0, 0), 1.0, (-1, 0, 0), "test:nothing", dps=0.0)
        here = (0, 0, 0)
        rows = [
            ("no rows, clock starts now", [], None, 10.0, (False, 10.0)),
            ("no rows, LOST_S elapsed", [], 5.0, 8.0, (True, 5.0)),
            ("no rows, just short", [], 5.0, 7.9, (False, 5.0)),
            ("far and still: runs out", [far], 0.0, 10.0, (True, 0.0)),
            # must-fail: never over while something chases
            ("in notice range", [near], 0.0, 10.0, (False, 10.0)),
            ("closing from afar", [closing], 0.0, 10.0, (False, 10.0)),
        ]
        for name, rows_, chased_at, now, want in rows:
            with self.subTest(name):
                self.assertEqual(fight_loop.engagement_over(rows_, here, chased_at, now), want)


class StillWorth(unittest.TestCase):
    def test_table(self):
        def opt(kind, leaves=0.0, blast=0.0, seconds=0.0, hp=0.0):
            return SimpleNamespace(kind=kind, leaves=leaves, blast_after=blast, seconds=seconds, hp=hp)
        ignore = opt("ignore", leaves=1.0)

        def model(*options):
            return SimpleNamespace(opts=[SimpleNamespace(option=o) for o in options])
        price = lambda hp: hp  # noqa: E731
        rows = [
            # saved = 1*10 - 0 - (2 + 1) = 7
            ("fight still pays", "fight", model(ignore, opt("fight", seconds=2, hp=1)), True),
            # saved = 10 - 0 - (9 + 1) = 0: breaking even is not paying
            ("breaks even", "fight", model(ignore, opt("fight", seconds=9, hp=1)), False),
            ("too slow now", "fight", model(ignore, opt("fight", seconds=20, hp=1)), False),
            # must-fail: the held answer is no longer on offer
            ("answer gone", "fight", model(ignore, opt("evade", seconds=1)), False),
            ("no do-nothing column", "fight", model(opt("fight", seconds=1)), False),
        ]
        for name, choice, fm, want in rows:
            with self.subTest(name):
                self.assertEqual(fight_loop.still_worth(SimpleNamespace(name=choice), fm, price, 10.0), want)


class Lend(unittest.TestCase):
    def setUp(self):
        self.saved = (dict(fight_loop.BATCH), dict(fight_loop.REGION))

    def tearDown(self):
        fight_loop.BATCH.clear()
        fight_loop.BATCH.update(self.saved[0])
        fight_loop.REGION.clear()
        fight_loop.REGION.update(self.saved[1])

    def test_table(self):
        def make(option, state):
            return None
        missing = object()
        rows = [
            ("batch and region", "t_a", make, "r1", (make, "r1")),
            ("region replaced", "t_a", make, "r2", (make, "r2")),
            ("empty region is still a region", "t_c", make, "", (make, "")),
            # must-fail: no region given leaves REGION untouched
            ("batch only", "t_b", make, None, (make, missing)),
        ]
        for name, kind, fn, region, want in rows:
            with self.subTest(name):
                fight_loop.lend(kind, fn, region)
                self.assertEqual((fight_loop.BATCH[kind], fight_loop.REGION.get(kind, missing)), want)


# ------------------------------------------------------------------------------------------------ fight_plan


class CycleSeconds(unittest.TestCase):
    def test_table(self):
        rows = [
            ("one sample each", {4: [1], 0: [2], 2: [3], 3: [4]}, 10),
            ("median by nearest rank, odd", {4: [3, 1, 2]}, 2),
            ("median by nearest rank, even: lower", {4: [1, 3]}, 1),
            ("nothing observed", {}, 0),
            # must-fail: the sitting phases are not part of the lap
            ("window phases ignored", {6: [100], 7: [100]}, 0),
        ]
        for name, observed, want in rows:
            with self.subTest(name), mock.patch.object(fight_plan, "OBSERVED", observed):
                self.assertEqual(fight_plan.cycle_seconds(), want)


if __name__ == "__main__":
    unittest.main()


class Footwork(unittest.TestCase):
    """fight_loop.footwork: between swings, back out of a melee mob's reach; sidestep a ranged mob's line."""

    @staticmethod
    def rows(*kinds):
        from bonobo import threat
        return [threat.row((float(3 + i), 64.0, 0.0), threat.MOBS[k]["reach"], (0.0, 0.0, 0.0), k)
                for i, k in enumerate(kinds)]

    def test_over_the_table(self):
        bag = SimpleNamespace(offhand=lambda: "minecraft:shield")
        both = dict(threats=self.rows("minecraft:zombie", "minecraft:skeleton", "minecraft:creeper"),
                    threat_ids=[11, 12, 13], inv=bag)
        rows = [  # (why, target, fight kind, state) → the attack task's footwork (None: no key)
            ("a zombie: back out of its reach", 11, "fight", both, "back"),
            ("a skeleton: strafe across its line", 12, "fight", both, "strafe"),
            ("a creeper (melee): back, never standing in its blast", 13, "fight", both, "back"),
            ("behind the shield, a skeleton: strafe too", 12, "fight_shielded", both, "strafe"),
            ("a target not among the rows: no footwork", 99, "fight", both, None),
            ("no rows at all: no footwork", 11, "fight", dict(inv=bag), None),
        ]
        for why, target, kind, state, want in rows:
            with self.subTest(why):
                task = fight_loop.batch(SimpleNamespace(kind=kind, target=target), state)[0]
                self.assertEqual(task.get("footwork"), want)
                self.assertEqual(task["entity"], target)
