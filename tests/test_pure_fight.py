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
from bonobo import beliefs, combat_tape, decompose, dispatch, estimate, field, fight_loop, fight_plan  # noqa: E402
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
            ("must fail: empty tape", [], []),
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


class FitDamage(unittest.TestCase):
    def test_table(self):
        z = "minecraft:zombie"
        rows = [
            ("must fail: no frames", [], {}),
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
            ("must fail: nothing", [], None, []),
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


class BestStep(unittest.TestCase):
    def test_table(self):
        # (situation, here, hazards [(centre, radius, velocity)], cover) → (spot, slack), rounded
        rows = [("a hazard coming: the step away from its path", (0, 64, 0), [((3, 64, 0), 1.0, (-2, 0, 0))], None,
                 ((-2.828427, 64, 2.828427), INF)),
                ("boxed in by four: the gap between them", (0, 64, 0),
                 [((6, 64, 0), 1.0, (-8, 0, 0)), ((-6, 64, 0), 1.0, (8, 0, 0)), ((0, 64, 6), 1.0, (0, 0, -8)),
                  ((0, 64, -6), 1.0, (0, 0, 8))], None, ((1.414214, 64, 1.414214), INF)),
                ("standing in it, nowhere out: stay, the least bad", (0, 64, 0), [((0, 64, 0), 10.0, (0, 0, 0))],
                 None, ((0, 64, 0), -0.3)),
                ("a cover further than a step: taken", (0, 64, 0), [((2, 64, 2), 1.0, (-1, 0, -1))], (-6, 64, 0),
                 ((-6, 64, 0), INF)),
                ("must fail: no cover offered, a sidestep instead", (0, 64, 0), [((2, 64, 2), 1.0, (-1, 0, -1))],
                 None, ((-4.0, 64, 0.0), INF))]
        for name, here, hazards, cover, want in rows:
            with self.subTest(name):
                spot, slack = cm.best_step(here, hazards, cover=cover)
                self.assertEqual((tuple(round(c, 6) + 0.0 for c in spot), slack), want)


class Windows(unittest.TestCase):
    def test_table(self):
        rows = [
            ("must fail: empty", [], None, []),
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
            ("must fail: nothing near", [], 5.0, []),
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
            ("must fail: route missing", gone, False),
            ("connection refused", broken, False),
        ]
        for name, fake, want in rows:
            with self.subTest(name), mock.patch.object(combat_tape, "frames", fake):
                self.assertEqual(combat_tape.available(), want)


# ------------------------------------------------------------------------------------------------ decompose


class Inv:
    def __init__(self, **counts):
        self.counts = counts

    def count(self, token):
        return self.counts.get(token, 0)


def steps_of(steps):
    return [(s.kind, s.token, s.count, s.detail) for s in steps]


class EffectDetail(unittest.TestCase):
    def test_table(self):
        rows = [
            ("hunt reads the table", ("hunt", "minecraft:blaze_rod", 1), {"types": ["minecraft:blaze"]}),
            ("mine by short name", ("mine", "coal", 2), {"blocks": ["coal_ore", "deepslate_coal_ore"], "tier": 0}),
            ("take", ("take", "minecraft:crafting_table", 1), {"blocks": ["crafting_table"]}),
            ("craft runs count times", ("craft", "minecraft:stick", 3), {"times": 3, "inputs": {}}),
            # must-fail: a hunt of something no mob drops has no detail to offer
            ("hunt of an ore", ("hunt", "minecraft:diamond", 1), {}),
            ("must fail: unknown kind", ("smelt", "minecraft:iron_ingot", 1), {}),
        ]
        for name, args, want in rows:
            with self.subTest(name):
                self.assertEqual(decompose.effect_detail(*args), want)


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
            ("must fail: no provider", Step("mine", "minecraft:coal", 1, {}), {}, None, None),
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
            ("must fail: no seek: raised", "mine", raiser(na), False, None, NotAvailable),
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
            ("block between delays only", ("between", 5), walker, 1.0),
            # a step at melee_stop_blocks: one block up or down the boxes still meet and every hit lands
            ("must fail: walker, one block up: still hit, all of it", ("up", 1), walker, 1.0),
            ("must fail: walker, one block down (a 1-deep hole): still hit", ("down", 1), walker, 1.0),
            ("walker, melee_stop_blocks down: out of reach", ("down", int(stop)), walker, 0.0),
            ("walker, melee_stop_blocks up: out of reach", ("up", int(stop)), walker, 0.0),
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
        # (situation, rows, chased since, now, the jar's soonest hit in s) → (over, chased since)
        rows = [
            ("no rows, clock starts now", [], None, 10.0, None, (False, 10.0)),
            ("no rows, LOST_S elapsed", [], 5.0, 8.0, None, (True, 5.0)),
            ("no rows, just short", [], 5.0, 7.9, None, (False, 5.0)),
            ("far and still: runs out", [far], 0.0, 10.0, None, (True, 0.0)),
            # must-fail: never over while something chases
            ("in notice range", [near], 0.0, 10.0, None, (False, 10.0)),
            ("far, but the jar predicts its hit in 1 s", [far], 0.0, 10.0, 1.0, (False, 10.0)),
            ("an arrow the jar sees coming, no mob row", [], 0.0, 10.0, 0.5, (False, 10.0)),
            ("must fail: closing from afar is not differenced here any more: over", [closing], 0.0, 10.0, None,
             (True, 0.0)),
        ]
        for name, rows_, chased_at, now, hit_s, want in rows:
            with self.subTest(name):
                self.assertEqual(fight_loop.engagement_over(rows_, here, chased_at, now, hit_s), want)


class ReflexPolicyEveryLife(unittest.TestCase):
    """The jar's reflex policy is posted again at every reset (row setup, death, dimension), not only at wire."""

    def test_rows(self):
        from unittest import mock
        from bonobo import api, lifecycle
        policy = dict(counter=False, **fight_loop.ALWAYS)
        # (the fight wired, how the jar answers) → the posts made by a reset
        rows = [("wired: posted again", object(), None, [("/reflex", policy)]),
                ("must fail: never wired (no brain) — nothing posted", None, None, []),
                ("a jar without /reflex: tried, said, no crash", object(), api.McError("404"), [("/reflex", policy)])]
        for name, answer, err, want in rows:
            with self.subTest(name):
                posts = []

                def post(path, body=None):
                    posts.append((path, body))
                    if err is not None:
                        raise err
                with mock.patch.object(fight_loop, "ANSWER", answer), mock.patch.object(api, "post", post), \
                        mock.patch.object(api, "swallowed", lambda *a: None):
                    lifecycle.reset_all()
                self.assertEqual([p for p in posts if p[0] == "/reflex"], want)


class HazardsAndStandableSpots(unittest.TestCase):
    """A calm neutral is no hazard; a hazard's safe spot is one a body stands on, never inside a wall."""

    def test_hazard_points(self):
        from bonobo import threat
        e = lambda kind, **kw: dict({"type": kind, "x": 0.0, "y": 64.0, "z": 0.0}, **kw)  # noqa: E731
        rows = [("a zombie", [e("minecraft:zombie")], 1), ("a calm enderman: none", [e("minecraft:enderman")], 0),
                ("must fail: a provoked enderman is one", [e("minecraft:enderman", provoked=True)], 1),
                ("a fireball", [e("minecraft:fireball")], 1)]
        for name, near, want in rows:
            with self.subTest(name):
                self.assertEqual(len(cm.hazard_points(near, hostile=threat.aggro)), want)

    def test_safe_spot_is_standable(self):
        """fight_enderman_1 05:07:32, as logged: four endermen as hazards moved the target into the glass wall."""
        from bonobo import nav
        solid = lambda c: c[1] == 199 or c[0] >= 10009  # noqa: E731  (floor, and the wall from x 10009)
        hz = [((9997.5, 200.0, 10003.5), 3.0), ((10000.5, 200.0, 9997.5), 3.0), ((10003.5, 200.0, 10003.5), 3.0),
              ((10005.5, 200.0, 9998.5), 3.0)]
        target = (10007, 200, 10000)
        rows = [("must fail: without the ground, the spot lands in the wall", None, True),
                ("with it, never in the wall", lambda p: cm.standable_at(solid, p), False)]
        for name, standable, in_wall in rows:
            with self.subTest(name):
                spot = nav.safe_destination(target, hz, standable=standable)
                self.assertEqual(spot is not None and solid((int(spot[0] // 1), 200, 0)), in_wall)

    def test_the_plans_retreat_cell_is_standable(self):
        """P2/K1: fight_plan.admissible's safe cell passes the run's own stand test (combat_model.standable_at, as
        nav.safe_destination walks it) over the ground the fight's state carries — a pocket walled on three sides, the
        threat at its mouth: the only clear steps are in the walls, so committing is refused; on open ground the
        same commit stands."""
        import types
        from bonobo import fight_plan
        fight = fight_plan.Fight()
        here = (10007.5, 200.0, 10000.5)
        pocket = lambda c: c[1] == 199 or c[0] >= 10009 or c[2] >= 10001 or c[2] <= 9999  # noqa: E731
        open_ground = lambda c: c[1] == 199  # noqa: E731
        hz = [((here[0] - 5.0, 200.0, here[2]), 3.0, (0.0, 0.0, 0.0), "minecraft:zombie")]

        def state(solid):
            return {"self": {"pos": here, "hp": 20.0, "in_cover": False, "cover": None,
                             "hp_floor": fight_plan.CONFIG["combat"]["hp_floor"],
                             "speed": fight_plan.CONFIG["combat"]["sprint_speed"]},
                    "boss": {"phase": 0, "phase_elapsed_s": 0.0, "hp": 200.0}, "threats": hz,
                    "ground": types.SimpleNamespace(solid=solid),
                    "resources": {"beds": 0, "obsidian": 0, "water": 0, "bow": 0, "arrows": 0},
                    "terrain": {"tunnel_ready": False, "bed_placed": False, "reinforced": False, "crystals_open": 0}}
        rows = [("must fail: the safe cell inside the pocket's wall, the commit allowed", pocket, False),
                ("open ground: unchanged, allowed", open_ground, True)]
        for name, solid, allowed in rows:
            with self.subTest(name):
                self.assertEqual(fight.admissible(state(solid), fight.action("dig_tunnel"))[0], allowed)

    def test_the_dug_pit_stays_the_escape(self):
        """S1: the cell a dig made is the escape the walls leave — the pit dug (its column read as air), its feet the
        fight's cover: a commit in the walled pocket still allowed (must fail: the cover filtered as a wall)."""
        import types
        from bonobo import fight_plan
        fight = fight_plan.Fight()
        here = (10007.5, 200.0, 10000.5)
        pit = {(10007, y, 10000) for y in range(196, 200)}
        solid = lambda c: tuple(c) not in pit and (c[1] <= 199 or c[0] >= 10009 or c[2] >= 10001 or c[2] <= 9999)  # noqa: E731
        state = {"self": {"pos": here, "hp": 20.0, "in_cover": False, "cover": (10007, 196, 10000),
                          "hp_floor": fight_plan.CONFIG["combat"]["hp_floor"],
                          "speed": fight_plan.CONFIG["combat"]["sprint_speed"]},
                 "boss": {"phase": 0, "phase_elapsed_s": 0.0, "hp": 200.0},
                 "threats": [((here[0] - 5.0, 200.0, here[2]), 3.0, (0.0, 0.0, 0.0), "minecraft:zombie")],
                 "ground": types.SimpleNamespace(solid=solid),
                 "resources": {"beds": 1, "obsidian": 0, "water": 0, "bow": 0, "arrows": 0},
                 "terrain": {"tunnel_ready": True, "bed_placed": False, "reinforced": False, "crystals_open": 0}}
        self.assertEqual(fight.admissible(state, fight.action("place_bed")), (True, ""))


class StillWorth(unittest.TestCase):
    def test_table(self):
        def opt(kind, leaves=0.0, blast=0.0, seconds=0.0, hp=0.0, target=None):
            return SimpleNamespace(kind=kind, target=target, leaves=leaves, blast_after=blast, seconds=seconds, hp=hp)
        ignore = opt("ignore", leaves=1.0)

        def model(*options):
            return SimpleNamespace(opts=[SimpleNamespace(option=o) for o in options])
        price = lambda hp: hp  # noqa: E731
        rows = [
            # saved = 1*10 - 0 - (2 + 1) = 7
            ("fight still pays", "fight", model(ignore, opt("fight", seconds=2, hp=1)), True),
            # saved = 10 - 0 - (9 + 1) = 0: breaking even is not paying
            ("breaks even", "fight", model(ignore, opt("fight", seconds=9, hp=1)), False),
            ("must fail: too slow now", "fight", model(ignore, opt("fight", seconds=20, hp=1)), False),
            # must-fail: the held answer is no longer on offer
            ("answer gone", "fight", model(ignore, opt("evade", seconds=1)), False),
        ]
        # the held answer's target against the entities the reading lists alive: an attack's mob gone → decide again;
        # still listed (x-ray: behind a wall too) → kept, though another is nearest; a position target is no id
        targets = [("must fail: the held mob is gone from the reading (dead, despawned)", "fight", 41, {42}, False),
                   ("the held mob still listed, another nearest now: kept", "fight", 41, {42, 41}, True),
                   ("behind a wall: out of the rows but listed alive — kept", "fight", 41, {41}, True),
                   ("a wall's position target is never read as a gone id", "reshape", ("between", 2), set(), True),
                   ("an evade spot neither", "evade", (3, 64, 0), set(), True)]
        for name, kind, target, alive, want in targets:
            with self.subTest(name):
                held = SimpleNamespace(name=kind, action=SimpleNamespace(option=opt(kind, target=target)))
                fm = model(ignore, opt(kind, seconds=2, hp=1, target=target))
                fm.field = {"alive": alive}
                self.assertEqual(fight_loop.still_worth(held, fm, price, 10.0), want)
        for name, choice, fm, want in rows:
            with self.subTest(name):
                # the held choice as kernel.Held keeps it: its name and the action carrying the option it chose (an
                # attack names its mob, 41, and this reading still has it)
                held = SimpleNamespace(name=choice, action=SimpleNamespace(option=opt(choice, target=41)))
                fm.field = {"alive": {41}}
                self.assertEqual(fight_loop.still_worth(held, fm, price, 10.0), want)
        with self.subTest("must fail: a held fight naming no mob is never kept (attack(entity=None) cannot post)"):
            held = SimpleNamespace(name="fight", action=SimpleNamespace(option=opt("fight")))
            fm = model(ignore, opt("fight", seconds=2, hp=1))
            fm.field = {"ids": [41]}
            self.assertFalse(fight_loop.still_worth(held, fm, price, 10.0))


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
            ("must fail: nothing observed", {}, 0),
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
        return [estimate.row((float(3 + i), 64.0, 0.0), beliefs.MOBS[k]["reach"], (0.0, 0.0, 0.0), k)
                for i, k in enumerate(kinds)]

    def test_over_the_table(self):
        bag = SimpleNamespace(offhand=lambda: "minecraft:shield", count=lambda _: 0)
        both = dict(threats=self.rows("minecraft:zombie", "minecraft:skeleton", "minecraft:creeper"),
                    threat_ids=[11, 12, 13], inv=bag, feet=(0, 64, 0), protected=set())
        rows = [  # (why, target, fight kind, state) → the attack task's footwork (None: no key)
            ("a zombie: back out of its reach", 11, "fight", both, "back"),
            ("a skeleton: strafe across its line", 12, "fight", both, "strafe"),
            ("a creeper: keep off — hit, out past its blast, in again", 13, "fight", both, "keepoff"),
            ("must fail: a target not among the rows: no footwork", 99, "fight", both, None),
            ("no rows at all: no footwork", 11, "fight", dict(inv=bag), None),
        ]
        for why, target, kind, state, want in rows:
            with self.subTest(why):
                task = fight_loop.batch(SimpleNamespace(kind=kind, target=target), state)[-1]
                self.assertEqual(task.get("footwork"), want)
                self.assertEqual(task["entity"], target)
                # a creeper's keep-off distance goes to the jar (keepOff: past where its fuse stops)
                self.assertEqual(task.get("keepOff"), 7.5 if want == "keepoff" else None)

    def test_a_creeper_by_our_builds_is_led_away_first(self):
        """fight_loop: a creeper whose blast would reach a protected cell is led LURE_BLOCKS away from it, then fought."""
        bag = SimpleNamespace(offhand=lambda: None, count=lambda _: 0)
        creeper = self.rows("minecraft:creeper")          # at (3, 64, 0)
        rows = [  # (why, protected cells) → the batch's task types, and the lure's x when there is one
            ("must fail: nothing of ours near: fight where we stand", set(), ["attack"], None),
            ("a wall 2 from it: led west, away from the wall, first", {(5, 64, 0)}, ["travel", "attack"], -8),
            ("our builds 20 off: out of its blast, fight here", {(23, 64, 0)}, ["attack"], None),
            ("builds on the other side: led east", {(-1, 64, 0)}, ["travel", "attack"], 8),
        ]
        for why, protected, kinds, x in rows:
            with self.subTest(why):
                state = dict(threats=creeper, threat_ids=[7], inv=bag, feet=(0, 64, 0), protected=protected)
                got = fight_loop.batch(SimpleNamespace(kind="fight", target=7), state)
                self.assertEqual([t["type"] for t in got], kinds)
                self.assertEqual(got[-1]["footwork"], "keepoff")
                if x is not None:
                    self.assertEqual(got[0]["x"], x)
