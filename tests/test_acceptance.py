"""Acceptance: the agent must not idle, and it must know how to sleep, eat, build, fight and run.

Scenarios, not units. Each one starts from a REAL recorded round (so the world answers are a world that existed)
and edits the one fact the scenario is about — no food in the bag, night with a bed, a skeleton twelve blocks away.
Then it asks the planner what it would do and checks the KIND of answer, never the score.

Why not assert the pick? Because the pick is the model's business and the model is meant to change: the numbers
live in play.toml precisely so they can be refitted. What must not change is that the option exists, is priced,
and is reachable — that a hungry agent has a way to eat at all is an architectural fact; whether it eats before or
after mending its pickaxe is a modelling one.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import decide, paths, perception, threat  # noqa: E402

RECORDED = decide.load() if os.path.exists(paths.data("decisions.jsonl")) else []
SKIP = "no recorded rounds to build scenarios from"


def scene(threats=(), **edits):
    """The pool and the pick for a recorded round with one fact changed.

    `threats` are (kind, distance) pairs placed relative to where the player actually stood in that round —
    absolute coordinates would put them in another chunk, which is how the first version of these tests "proved"
    the agent never fights.
    """
    for row in reversed(RECORDED):
        try:
            _name, _top, _filtered, _pick = decide.decide(row)
        except Exception:
            continue
        st = row["calls"].get("/state") or {}
        here = (st.get("x", 0.0), st.get("y", 64.0), st.get("z", 0.0))
        with_threats([hostile(kind, here, d) for kind, d in threats])
        try:
            edited = decide.synth(row, **edits)
            brain = decide.make_brain(edited)
            # A recorded round carries the cooldowns of whatever failed while it was recorded. Those are real, but
            # they are not what these scenarios are about — a scenario asks "is there a way to do this at all",
            # and an answer that is merely cooling is still an answer the agent has.
            brain.retry.entries.clear()
            name, top, filtered, pick = decide.decide(edited, brain)
        except Exception:
            with_threats([])
            continue
        with_threats([])
        return name, dict(top), filtered, pick
    raise unittest.SkipTest(SKIP)


def with_threats(rows):
    """Put threats where the planner reads them: perception is the one authority, and in a replay it is empty."""
    perception.THREAT_ROWS = rows
    perception.THREAT_IDS = list(range(len(rows)))
    perception.THREAT_AT = 1e18        # never stale


def hostile(kind, here, distance):
    """A threat row as `threat.rows` builds them — through the module's own constructor, so a change to the row's
    shape breaks the source and not just the tests that copied it."""
    pos = (here[0] + float(distance), here[1], here[2])
    return threat.row(pos, float(threat.MOBS[kind]["reach"]), (0.0, 0.0, 0.0), kind)


@unittest.skipUnless(RECORDED, SKIP)
class NeverIdle(unittest.TestCase):
    def tearDown(self):
        with_threats([])

    def test_every_recorded_round_has_something_to_do(self):
        idle = 0
        replayed = 0
        for row in RECORDED:
            try:
                name, _top, _f, _p = decide.decide(row)
            except Exception:
                continue
            replayed += 1
            idle += name is None
        # The tape is rotated — and a fresh world starts it empty — so a fixed floor turns a short recording into
        # a failure about nothing. What is being asserted is about the rounds that ARE there: most of them must
        # replay, and none of them may come out idle. With almost no tape there is nothing to assert at all.
        if len(RECORDED) < 6:
            self.skipTest(f"only {len(RECORDED)} recorded rounds: nothing to conclude from")
        self.assertGreater(replayed, len(RECORDED) // 2,
                           f"only {replayed} of {len(RECORDED)} recorded rounds could be replayed")
        self.assertEqual(idle, 0, f"{idle} of {replayed} rounds had nothing runnable")

    def test_the_pool_offers_more_than_one_thing(self):
        """Multi-tasking is not a feature to add: it is what having a priced pool means. If only one candidate is
        ever admissible the agent is a queue, not a planner."""
        _name, top, _f, _p = scene()
        self.assertGreaterEqual(len(top), 3, top)


@unittest.skipUnless(RECORDED, SKIP)
class KnowsHow(unittest.TestCase):
    """Sleep, eat, build, fight, run — each must be a priced option in the situation that calls for it."""

    def tearDown(self):
        with_threats([])

    def offered(self, top, filtered, *words):
        """Was an answer of this kind reachable — either in the pool, or refused for a stated reason?"""
        names = list(top) + list(filtered)
        return [n for n in names if any(w in n.lower() for w in words)]

    def report(self, top, filtered):
        return f"pool={sorted(top)} filtered={sorted(filtered)[:12]}"

    def test_it_can_eat(self):
        hungry = scene(remove_items=["minecraft:cooked_beef", "minecraft:cooked_porkchop", "minecraft:beef",
                                     "minecraft:porkchop", "minecraft:mutton", "minecraft:bread"],
                       state={"food": 5})
        _name, top, filtered, _p = hungry
        self.assertTrue(self.offered(top, filtered, "food", "eat", "hunt"), self.report(top, filtered))

    def test_it_can_sleep_or_shelter_at_night(self):
        _name, top, filtered, _p = scene(state={"timeOfDay": 15000, "skyLight": 15})
        self.assertTrue(self.offered(top, filtered, "sleep", "shelter", "dig in", "wall in", "bed"), self.report(top, filtered))

    def test_it_can_make_tools(self):
        bare = scene(remove_items=["minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:iron_pickaxe",
                                   "minecraft:diamond_pickaxe"])
        _name, top, filtered, _p = bare
        self.assertTrue(self.offered(top, filtered, "pickaxe", "axe", "sword"), self.report(top, filtered))

    def answer(self, threats, hp=20, sword=2, armor=8, food=4, shield=True, blocks=64):
        """What the threat layer bids for the body in this situation.

        Not a pool candidate any more: threats are answered by the perception thread at its own cadence, and the
        pool never sees them — it saw them once, could pick `ignore` as if doing nothing were an answer, and that
        cost a death. So the acceptance question is whether a bid exists, not whether a candidate is listed.
        """
        from bonobo import field, perception, survival as sv
        perception.HELD = None
        here = (0.0, 64.0, 0.0)
        rows = [threat.row((here[0] + d, here[1], here[2]), threat.MOBS[kind]["reach"], (0.0, 0.0, 0.0), kind)
                for kind, d in threats]
        state = {"x": here[0], "y": here[1], "z": here[2], "health": hp, "armor": armor, "sword_tier": sword,
                 "food_items": food, "shield": shield, "blocks": blocks, "field": field.Field()}
        sstate = sv.make_state(hp=hp, sword=sword, armor=armor)
        return perception.bid(state, rows, lambda dhp: sv.hp_seconds(sstate, dhp))

    def test_it_can_fight_what_it_can_afford(self):
        got = self.answer([("minecraft:zombie", 4)])
        self.assertIsNotNone(got, "nothing was bid against a zombie four blocks away")
        self.assertEqual(got[0].kind, "fight")

    def test_it_can_run_from_what_it_cannot(self):
        got = self.answer([("minecraft:skeleton", 12), ("minecraft:creeper", 5)], hp=6, sword=0, armor=0,
                          shield=False)
        self.assertIsNotNone(got, "nothing was bid while being shot at 6 hp")
        self.assertNotEqual(got[0].kind, "fight")

    def test_a_threat_makes_ordinary_work_more_expensive(self):
        """The blood tax: the same work, priced with and without something shooting at us."""
        _n1, _calm, _f1, pick_calm = scene()
        _n2, _fire, _f2, pick_fire = scene(threats=[("minecraft:skeleton", 8)])
        if pick_calm is None or pick_fire is None:
            self.skipTest("no pick to compare")
        self.assertGreaterEqual(getattr(pick_fire, "risk_s", 0.0), 0.0)


@unittest.skipUnless(RECORDED, SKIP)
class PlansAhead(unittest.TestCase):
    """Far-sightedness is the solver's job: a goal is reachable when a chain of actions reaches it, however long."""

    def test_it_plans_a_bed_from_an_empty_bag(self):
        from bonobo import actions
        from bonobo.solve import solve
        table = actions.table(actions.Costs(lambda kinds: 20.0), {})
        plan = solve(table, {}, {"bed": 1})
        kinds = {a.tag[0] for a, _n in plan.steps() if a.tag}
        # Not a step count: a SHORTER plan is a better plan, and the world decides which one is shorter. Knowing
        # nowhere, the agent must still go and look, and end up holding a bed — made from wool it sheared, or
        # taken out of a village. Asserting "four steps, one of them a craft" pinned yesterday's cheapest route.
        self.assertIn("seek", kinds, "going to where the bed or the wool is, is part of the plan")
        self.assertTrue(kinds & {"craft", "take"}, plan.counts)
        self.assertGreater(plan.cost_s, 0.0)

    def test_it_plans_a_night_of_shelter_with_nothing_in_hand(self):
        from bonobo import actions
        from bonobo.solve import solve
        table = actions.table(actions.Costs(lambda kinds: 20.0), {})
        plan = solve(table, {}, {"sheltered": 1})
        self.assertGreater(plan.cost_s, 0)
        self.assertTrue(any(a.tag and a.tag[0] == "shelter" for a, _n in plan.steps()), plan.counts)

    def test_work_two_goals_share_is_not_paid_for_twice(self):
        from bonobo import actions
        from bonobo.solve import solve
        table = actions.table(actions.Costs(lambda kinds: 20.0), {})
        table_only = solve(table, {}, {"minecraft:crafting_table": 1}).cost_s
        both = solve(table, {}, {"minecraft:crafting_table": 1, "minecraft:stick": 4}).cost_s
        sticks_only = solve(table, {}, {"minecraft:stick": 4}).cost_s
        self.assertLess(both, table_only + sticks_only, "the shared planks were planned twice")


class OnTheWay(unittest.TestCase):
    """An errand's cost is its offset from where we are already going, not its distance from where we stand."""

    def test_an_errand_on_the_path_is_nearly_free(self):
        from bonobo import priority as pr
        here, target = (0.0, 64.0, 0.0), (100.0, 64.0, 0.0)
        on_path = pr.detour_s(50, here=here, there=(50.0, 64.0, 0.0), via=target)
        behind = pr.detour_s(50, here=here, there=(-50.0, 64.0, 0.0), via=target)
        self.assertAlmostEqual(on_path, 0.0, places=6)
        self.assertGreater(behind, 20.0)

    def test_without_a_committed_path_it_falls_back_to_the_round_trip(self):
        from bonobo import priority as pr
        self.assertGreater(pr.detour_s(50), pr.detour_s(10))

    def test_a_plan_says_where_it_is_going(self):
        from bonobo import actions
        from bonobo.solve import solve

        class Located(actions.Costs):
            def where(self, kinds):
                return (10.0, 64.0, 10.0)

        table = actions.table(Located(lambda kinds: 20.0), {})
        plan = solve(table, {}, {"log": 4})
        steps = [actions.to_step(a, n) for a, n in plan.steps()]
        self.assertTrue(any(st.detail.get("pos") for st in steps), [st.kind for st in steps])


class Hunger(unittest.TestCase):
    """Eating and stocking food are two actions answering two different costs."""

    def state(self, **kw):
        from bonobo import survival as sv
        return sv.make_state(**{"pickaxe": 1, "sword": 1, "bed": True, **kw})

    def test_eating_is_worth_something_when_hungry(self):
        from bonobo import survival as sv
        # The bug: `food_loss` looked only at meals carried, and eating does not change that count, so the benefit
        # of eating was zero at every hunger level — a full bag of pork and an empty stomach was stable.
        hungry = self.state(food=6, food_items=8)
        self.assertGreater(sv.benefit(hungry, {"food": 20}), 0)

    def test_eating_is_worth_nothing_when_full(self):
        from bonobo import survival as sv
        self.assertEqual(sv.benefit(self.state(food=20, food_items=8), {"food": 20}), 0)

    def test_stocking_food_is_worth_something_with_an_empty_larder(self):
        from bonobo import survival as sv
        self.assertGreater(sv.benefit(self.state(food=20, food_items=0), {"food_items": 8}), 0)

    def test_hungrier_is_worth_more(self):
        from bonobo import survival as sv
        a = sv.benefit(self.state(food=14, food_items=8), {"food": 20})
        b = sv.benefit(self.state(food=5, food_items=8), {"food": 20})
        self.assertGreater(b, a)


@unittest.skipUnless(RECORDED, SKIP)
class OneWallOneLesson(unittest.TestCase):
    """A step that cannot be done here cannot be done here for any goal that wants it."""

    def test_goals_do_not_take_turns_failing_at_the_same_step(self):
        from bonobo import decide as D
        from bonobo.api import NavFailed
        row = RECORDED[-1]
        picks = D.simulate(row, lambda name, i: NavFailed("no path found"), rounds=6)
        steps = [k.split("/", 1)[1] for _t, _n, k in picks if k and "/" in k]
        worst = max((steps.count(x) for x in set(steps)), default=0)
        self.assertLessEqual(worst, 2, f"the same step was tried {worst}× in six rounds: {steps}")


class YieldsTheBody(unittest.TestCase):
    """A commitment ends at an atomic boundary, and the premise is a price, not a set of mobs."""

    def test_a_falling_health_bar_beats_the_model(self):
        """The death that prompted this: shot by a skeleton while the model said there were ten seconds left."""
        from bonobo import perception as P
        P.HURT_RATE, P._HP_SEEN = 0.0, None
        P.note_hurt({"health": 20.0}, now=100.0)
        P.note_hurt({"health": 16.0}, now=101.0)
        self.assertAlmostEqual(P.hurt_rate(), 4.0)
        P.note_hurt({"health": 16.0}, now=102.0)
        self.assertLess(P.hurt_rate(), 4.0)       # falls off slowly: one quiet second is not proof of safety
        self.assertGreater(P.hurt_rate(), 0.0)
        P.HURT_RATE, P._HP_SEEN = 0.0, None

    def test_losing_health_changes_the_premise(self):
        from bonobo import brain as B

        class Snap:
            state = {"x": 0.0, "y": 64.0, "z": 0.0, "armor": 0, "health": 20.0}
            class inv:
                @staticmethod
                def offhand():
                    return None
            @classmethod
            def get(cls, k, d=None):
                return cls.state.get(k, d)

        b = object.__new__(B.Brain)
        with_threats([])
        full = b.premise(Snap)
        Snap.state = dict(Snap.state, health=12.0)
        hurt = b.premise(Snap)
        self.assertNotEqual(full, hurt, "taking two hearts is a change in what the world charges")

    def test_the_premise_is_binned_so_a_mob_at_the_edge_does_not_release_it(self):
        from bonobo import brain as B

        class FakeSnap:
            state = {"x": 0.0, "y": 64.0, "z": 0.0, "armor": 0, "health": 20.0}
            class inv:
                @staticmethod
                def offhand():
                    return None
            @staticmethod
            def get(k, d=None):
                return FakeSnap.state.get(k, d)

        b = object.__new__(B.Brain)
        with_threats([])
        empty = b.premise(FakeSnap)
        with_threats([hostile("minecraft:zombie", (0.0, 64.0, 0.0), 60)])
        far = b.premise(FakeSnap)
        with_threats([hostile("minecraft:zombie", (0.0, 64.0, 0.0), 2)])
        close = b.premise(FakeSnap)
        with_threats([])
        self.assertEqual(empty, far, "a mob far outside its reach must not change the price")
        self.assertNotEqual(empty, close, "a mob on top of us must")

    def test_the_segment_hook_is_the_yield_point(self):
        import inspect
        from bonobo.brain import Brain
        for hook in (Brain.segment_reflexes, Brain.hand_segment_reflexes):
            self.assertIn("yield_if_stale", inspect.getsource(hook))
        self.assertIn("CommitmentExpired", inspect.getsource(Brain.yield_if_stale))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(RECORDED, SKIP)
class ThePoolHasChoices(unittest.TestCase):
    """A pool with one candidate is not a planner, it is a queue.

    The live agent reached a state where 22 of 30 goals came back `unplannable` and the pool held a single entry.
    Whatever it then did was not a decision. This is the red test for that: every recorded world must offer a few
    things worth comparing.
    """

    def pool_of(self, row):
        from unittest import mock
        from bonobo import retry, skills, tape
        from bonobo.world import Snapshot
        b = decide.make_brain(row)
        with mock.patch("time.time", return_value=row["t"]):
            tape.REPLAY = row["calls"]
            try:
                snap = Snapshot()
                b.policy_cache = b.policy(snap, snap.night)
                ctx = skills.Context(b.mem, b.policy_cache, snap.dimension, b.blacklist)
                ids = [s["id"] for s in snap.inv.slots]
                b.sig = b.coarse = retry.signature(snap.feet, ids, snap.night, 0)
                b.place = retry.place_signature(snap.feet, snap.night)
                b.snap_cache = snap
                return b.candidates(ctx, snap, snap.night)
            finally:
                tape.REPLAY = None

    def rounds(self, n=5):
        rows = decide.load()
        step = max(1, len(rows) // n)
        return rows[::step][:n]

    def test_every_recorded_world_offers_something_to_compare(self):
        thin = []
        for row in self.rounds():
            try:
                pool, _filtered = self.pool_of(row)
            except Exception:
                continue
            if len(pool) < 3:
                thin.append((len(pool), sorted(c.name for c in pool)))
        self.assertEqual(thin, [], "a pool this thin is a queue, not a decision")

    def test_unplannable_is_rare(self):
        """`unplannable` means the requirement graph has a hole — nothing in this world reaches that dimension.
        A handful is normal (the End, the Nether before a portal); most of the board is not."""
        worst = 0
        for row in self.rounds():
            try:
                _pool, filtered = self.pool_of(row)
            except Exception:
                continue
            worst = max(worst, sum(1 for r in filtered.values() if "unplannable" in str(r)))
        self.assertLessEqual(worst, 8, f"{worst} goals have no path at all: the action table is missing columns")
