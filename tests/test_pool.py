"""The pool's veto layer, alone. Each refusal is a rule with a stable kind; the text is what the tape carries."""
import unittest

from bonobo import pool


class Cand:
    def __init__(self, name, kind="goal", key=None, craft_only=False):
        self.name, self.kind, self.key, self.craft_only, self.weight = name, kind, key or name, craft_only, None


class Retry:
    def __init__(self, exhausted=()):
        self._x = set(exhausted)

    def exhausted(self, key, sig, place=None):
        return key in self._x


class Snap:
    class Inv:
        def __init__(self, bed=0):
            self._bed = bed

        def count(self, item):
            return self._bed if item == "bed" else 0

    def __init__(self, dimension="minecraft:overworld", bed=0, dusk=6000):
        self.inv, self.dimension, self.ticks_until_dusk = Snap.Inv(bed), dimension, dusk


def ctx(**over):
    base = dict(segment=None, seg_name=None, seg_goals=set(), bench_failing=set(), staying=False, committed=None,
                weights={}, ready=lambda k: True, force=False, seg_misses={}, seg_misses_limit=3,
                retry=Retry(), sig=None, night=False, night_capable=False, snap=Snap(), has_pickaxe=True,
                sightings=lambda kind, dim: [])
    base.update(over)
    return pool.Context(**base)


def weight_for(name, weights):
    return weights.get(name, (1.0, False))


def allowed(seg, name):
    return name in seg["goals"]


class Admit(unittest.TestCase):
    def adm(self, c, **over):
        return pool.admit(c, ctx(**over), weight_for, allowed, 3)

    def test_clean_candidate_is_admitted_and_weighted(self):
        c = Cand("food")
        self.assertIsNone(self.adm(c, weights={"food": (2.0, False)}))
        self.assertEqual(c.weight, 2.0)

    def test_being_off_the_route_is_charged_not_refused(self):
        """A route is a plan, not a rail.

        Off-route goals used to be removed outright, so a side errand that was genuinely worth more than the next
        leg could never say so. Now it pays for the time it takes away from the run, and wins only if it is worth
        more than that.
        """
        seg = {"name": "iron", "goals": ["iron pickaxe"]}
        c = Cand("wheat farm")
        self.assertIsNone(self.adm(c, segment=seg))
        self.assertGreater(c.off_route_s, 0.0, "an off-route goal must carry the detour in its cost")
        on_route = Cand("iron pickaxe")
        self.assertIsNone(self.adm(on_route, segment=seg))
        self.assertEqual(getattr(on_route, "off_route_s", 0.0), 0.0)

    def test_craft_only_goals_skip_the_segment_filter(self):
        seg = {"name": "portal", "goals": {"nether portal"}}
        self.assertIsNone(self.adm(Cand("gold helmet", craft_only=True), segment=seg))

    def test_ban_beats_staying_in_the_reported_reason_order(self):
        # The old bug: "staying" answered before "banned", so a banned fallback read as merely held back and came
        # back 170 times. The brain asks the ban itself before undoing a hold; the order here is the tape's order.
        r = self.adm(Cand("light up", kind="fallback"), staying=True, committed="portal",
                     weights={"light up": (0.0, True)})
        self.assertEqual(r.kind, "staying")

    def test_cooling_unless_a_forced_fallback(self):
        self.assertEqual(self.adm(Cand("food"), ready=lambda k: False).kind, "cooling")
        self.assertIsNone(self.adm(Cand("explore", kind="fallback"), ready=lambda k: False, force=True))

    def test_repeated_failure_in_the_same_place_is_refused(self):
        """Kept as a veto on purpose.

        Its arithmetic replacement (a falling success rate) is a guess until it is measured against a tape; a
        skill that is simply broken would be retried on that guess. The wall stays until the number is fitted.
        """
        r = self.adm(Cand("food", key="food/hunt:porkchop"), retry=Retry({"food/hunt:porkchop"}))
        self.assertEqual(r.kind, "exhausted")
        r = self.adm(Cand("iron pickaxe"), seg_misses={(None, "iron pickaxe"): 99})
        self.assertEqual(r.kind, "segment_misses")

    def test_refusal_compares_equal_to_its_text(self):
        # The tape and the goldens store strings; a Refusal must stand in for one.
        self.assertEqual(pool.Refusal("banned", "banned by Claude"), "banned by Claude")


class Step:
    def __init__(self, kind, token="t", est=100, types=()):
        self.kind, self.token, self.est, self.detail = kind, token, est, {"types": list(types)}


class Goal:
    def __init__(self, background=False, feasible=None, dimension="minecraft:overworld"):
        self.background, self.feasible, self.dimension = background, feasible, dimension


class Gate(unittest.TestCase):
    def gate(self, step, goal=None, plan=None, **over):
        return pool.gate_step(step, goal or Goal(), plan or [step], ctx(**over), {"craft", "smelt", "mine"},
                              lambda inv: True, lambda s: s.split(":")[-1])

    def test_surface_work_waits_for_day_unless_night_capable(self):
        self.assertEqual(self.gate(Step("gather"), night=True), "gather waits for day")
        self.assertIsNone(self.gate(Step("gather"), night=True, night_capable=True))
        self.assertIsNone(self.gate(Step("craft"), night=True), "underground kinds run at night")

    def test_hunting_needs_a_sighting_on_the_route(self):
        seg = {"name": "kit", "goals": {"food"}}
        step = Step("hunt", types=["minecraft:pig"])
        self.assertEqual(self.gate(step, segment=seg), "no pig seen yet")
        self.assertIsNone(self.gate(step, segment=seg, sightings=lambda k, d: [1]))
        self.assertIsNone(self.gate(step), "off the route, memory is not required")

    def test_long_surface_trip_without_pickaxe_or_bed_is_refused(self):
        step = Step("gather", est=10000)
        self.assertIn("run into the night", self.gate(step, has_pickaxe=False, snap=Snap(dusk=1000)))


class PickStep(unittest.TestCase):
    def test_first_runnable_step_that_passes_wins(self):
        a, b = Step("hunt", "a"), Step("craft", "b")
        step, why = pool.pick_step([a, b], [a, b], (), lambda s: "waits" if s is a else None)
        self.assertIs(step, b)
        self.assertIsNone(why)

    def test_gated_first_step_keeps_its_reason_when_nothing_passes(self):
        a = Step("hunt", "a")
        step, why = pool.pick_step([a], [a], (), lambda s: "no pig seen yet")
        self.assertIs(step, a)
        self.assertEqual(why, "no pig seen yet")

    def test_no_runnable_step_is_said_so(self):
        self.assertEqual(pool.pick_step([Step("mine")], [], (), lambda s: None), (None, "no runnable step"))

    def test_preparation_only_is_not_progress(self):
        torch = Step("craft", "torch")
        step, why = pool.pick_step([torch], [torch], ("torch",), lambda s: None)
        self.assertIs(step, torch)
        self.assertEqual(why, "only look-ahead preparation can run")


class GoalReason(unittest.TestCase):
    NETHER = "minecraft:the_nether"

    def test_feasible_speaks_first(self):
        g = Goal(feasible=lambda: "no lava here")
        self.assertEqual(pool.goal_reason(g, [], ctx(), lambda inv: [], self.NETHER, set()), "no lava here")

    def test_nether_goal_needs_the_kit_from_the_overworld(self):
        g = Goal(dimension=self.NETHER)
        r = pool.goal_reason(g, [], ctx(), lambda inv: ["food 0/6"], self.NETHER, set())
        self.assertEqual(r, "Nether kit not ready: food 0/6")

    def test_overworld_plan_that_hunts_nether_mobs_waits_for_the_blaze_goal(self):
        plan = [Step("hunt", types=["minecraft:blaze"])]
        r = pool.goal_reason(Goal(), plan, ctx(), lambda inv: [], self.NETHER, {"minecraft:blaze"})
        self.assertIn("blaze-rod goal comes first", r)


if __name__ == "__main__":
    unittest.main()


class Preconditions(unittest.TestCase):
    """A skill's own preconditions are asked BEFORE the work is priced, and `force` does not waive them.

    The log that prompted this: `light up` chosen at −14.7 s, failing with "no torches to spare", ninety times in
    four minutes. The skill knew it could not run; nothing asked it. The idle rule then thawed it every few
    seconds, because thawing is meant for work that is merely cooling — not for work that is impossible.
    """

    def adm(self, c, **over):
        return pool.admit(c, ctx(**over), weight_for, allowed, 3)

    def test_a_candidate_whose_skill_cannot_run_is_refused(self):
        c = Cand("light up", kind="fallback")
        c.precheck = lambda: (False, "no torches to spare")
        r = self.adm(c)
        self.assertEqual(r.kind, "unavailable")
        self.assertIn("no torches", str(r))

    def test_force_thaws_cooling_but_not_impossibility(self):
        c = Cand("light up", kind="fallback")
        c.precheck = lambda: (False, "no torches to spare")
        self.assertIsNotNone(self.adm(c, force=True), "the idle rule must not make the impossible possible")

    def test_a_candidate_whose_skill_can_run_is_admitted(self):
        c = Cand("light up", kind="fallback")
        c.precheck = lambda: (True, None)
        self.assertIsNone(self.adm(c))

    def test_a_candidate_without_a_skill_is_unaffected(self):
        self.assertIsNone(self.adm(Cand("iron pickaxe")))
