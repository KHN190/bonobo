"""What the swept table cannot reach: everything here is about wiring, not about pricing.

Pricing — which column wins, what it saves, how the answer moves with distance, numbers, health, ground and kit —
is swept over the whole danger sweep in test_when/test_rate/test_act/test_saved, and the scene-by-scene ones
deleted with it. What is left is what a pure state vector never sees: rows built from live entities, the reflex
bidding through perception, the lease, the interrupt, and the few shapes whose geometry is the whole point.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import survival as sv  # noqa: E402
from bonobo import threat  # noqa: E402

HERE = (0.0, 64.0, 0.0)
STILL = (0.0, 0.0, 0.0)


def row(kind, x, z, vel=STILL, aware=1.0, dps=None):
    """Through the one constructor, like everything else: a row built by hand here is a second definition of a
    row, and it was exactly such a hand-built row that let a missing field go unnoticed."""
    return threat.row((float(x), 64.0, float(z)), threat.MOBS[kind]["reach"], vel, kind, aware=aware, dps=dps)


def decide(hazards, **kw):
    """Decide with the price the agent actually uses: health costs what the survival model says it costs.

    Passing health through as itself (the default) is only a test convenience, and it hides the behaviour that
    matters — at 9 hp a fight that costs 7 hp is nearly a death, not "7", which is why low health must leave.
    """
    state = {"here": HERE, "hp": 20, "sword": 0, "protection": 0.0, "night": False, "blocks": 0,
             "hazards": hazards, "ids": list(range(len(hazards)))}
    state.update(kw)
    sstate = sv.make_state(hp=state["hp"], sword=state["sword"], pickaxe=1, food_items=8, bed=True)
    return threat.decide(state, lambda dhp: sv.hp_seconds(sstate, dhp))


class Rows(unittest.TestCase):
    def test_velocity_is_differenced_between_rounds(self):
        mem = {}
        e = {"id": 7, "type": "minecraft:zombie", "x": 10.0, "y": 64.0, "z": 0.0}
        threat.rows([e], mem, 100.0, {"minecraft:zombie": 3.0})
        e2 = dict(e, x=8.0)
        (r,) = threat.rows([e2], mem, 101.0, {"minecraft:zombie": 3.0})
        self.assertEqual(r[2], (-2.0, 0.0, 0.0))

    def test_unknown_kinds_are_not_rows(self):
        self.assertEqual(threat.rows([{"id": 1, "type": "minecraft:cow", "x": 1, "y": 64, "z": 1}], {}, 0, {}), [])


class Answers(unittest.TestCase):
    """What the threat layer OFFERS, not which one it picks.

    Picking is the planner's job — the same objective that ranks mining and crafting — so asserting a choice here
    would pin the model to whatever the numbers happen to be this week. What must hold is the shape of the option
    set: every answer priced in health and seconds, nothing missing, and the one answer that is never allowed.
    """

    def priced(self, hazards, **kw):
        state = {"here": HERE, "hp": 20, "sword": 0, "protection": 0.0, "night": False, "blocks": 0,
                 "hazards": hazards, "ids": list(range(len(hazards)))}
        state.update(kw)
        return {o.kind: o for o in threat.options(state)}


    def test_escape_goes_away_from_the_group_not_between_them(self):
        hz = [row("minecraft:skeleton", 10, 2), row("minecraft:skeleton", 10, -2)]
        self.assertLess(threat.escape_spot(HERE, hz)[0], -8)


class Interrupt(unittest.TestCase):
    def test_perception_stops_a_task_when_arrows_would_kill_soon(self):
        from bonobo import perception
        state = {"health": 12, "food": 20, "x": 0.0, "y": 64.0, "z": 0.0, "control": {"task": {"type": "mine"}}}
        # Perception interrupts only for what is closer than one planning round; everything slower is the pool's
        # call, priced against the work it would interrupt.
        self.assertEqual(perception.danger(state, time_to_die=lambda: 2.0), "hostiles")
        self.assertIsNone(perception.danger(state, time_to_die=lambda: 30.0))


class OneComparison(unittest.TestCase):
    """The pool and the reflex must rank the same options the same way, or the agent oscillates between them."""

    def state(self, **kw):
        s = dict(here=(0, 0, 0), hp=20.0, sword=1, protection=0.0, night=True, blocks=64,
                 hazards=[threat.row((4, 0, 0), 2.0, (-1.0, 0, 0), "minecraft:zombie")], work_s=20.0)
        s.update(kw)
        return s

    def test_decide_agrees_with_what_the_pool_would_offer(self):
        st = self.state()
        opts = threat.options(st)
        price = lambda dhp: dhp
        best_by_saving = max((o for o in opts if o.kind != "ignore"),
                             key=lambda o: threat.saves(o, opts, price, st["work_s"]))
        self.assertEqual(threat.decide(st, price).kind, best_by_saving.kind)


class PricesForTheOtherPlanner(unittest.TestCase):
    """What combat hands ordinary play: a rate and a field, never an answer."""

    def rows(self, *hazards):
        return dict(here=(0, 0, 0), hp=20.0, sword=1, protection=0.0, hazards=list(hazards))


    def test_no_go_is_a_circle_wider_than_the_reach(self):
        zones = threat.no_go(self.rows(threat.row((10, 0, 0), 2.0, (0, 0, 0), "minecraft:zombie")))
        self.assertEqual(len(zones), 1)
        (centre, radius), = zones
        self.assertEqual(centre, (10, 0, 0))
        self.assertGreater(radius, 2.0)
        self.assertTrue(threat.inside_no_go((10, 0, 1), zones))
        self.assertFalse(threat.inside_no_go((0, 0, 0), zones))


class TheFastLane(unittest.TestCase):
    """Threat answers are bid for the body at perception's cadence, not queued for the next ten-second round."""

    def setUp(self):
        from bonobo import perception, survival as sv
        self.perception, self.sv = perception, sv
        perception.HELD = None      # each case is its own situation, not a continuation of the last

    def bid(self, rows, hp=20, sword=2, armor=8):
        ss = self.sv.make_state(hp=hp, sword=sword, armor=armor)
        state = {"x": 0, "y": 64, "z": 0, "health": hp, "armor": armor, "sword_tier": sword, "blocks": 64}
        return self.perception.bid(state, rows, lambda dhp: self.sv.hp_seconds(ss, dhp))

    def test_nothing_near_is_no_bid(self):
        self.assertIsNone(self.bid([]))


    def test_the_bid_is_what_its_own_answer_saves(self):
        """Closed against the state the bid itself built, not against one reassembled here: a test that rebuilds
        the state vector is a second copy of `perception.threat_state`, and the two drifted the moment the live
        one started reading the ground and the kit.
        """
        rows = [row("minecraft:zombie", 5, 0)]
        ss = self.sv.make_state(hp=20, sword=2, armor=8)
        price = lambda dhp: self.sv.hp_seconds(ss, dhp)
        state = {"x": 0, "y": 64, "z": 0, "health": 20, "armor": 8, "sword_tier": 2, "blocks": 64}
        option, worth = self.perception.bid(state, rows, price)
        st = self.perception.threat_state(state, rows)
        opts = threat.options(st)
        same = next(o for o in opts if o.kind == option.kind)
        self.assertAlmostEqual(worth, round(threat.saves(same, opts, price, threat.horizon_for(st)), 1), places=1)


class TheLeaseSurvivesBlindMoments(unittest.TestCase):
    """Perception goes blind for a moment all the time: the entity read is a second old, the thread was busy, the
    rows aged out. A lease that reads "nothing visible" as "nothing to answer" hands the body back mid-fight, and
    the planner's next mine task lands on top of the answer. Only a real price says the answer is done."""

    def setUp(self):
        from bonobo import perception, survival as sv
        self.perception, self.sv = perception, sv
        perception.HELD = None

    def release(self, rows):
        """The lease's own judgement, given what perception can see right now."""
        from bonobo import field
        state = {"x": 0, "y": 64, "z": 0, "health": 12, "armor": 0, "sword_tier": 2,
                 "food_items": 0, "shield": False, "blocks": 64, "field": field.Field()}
        ss = self.sv.make_state(hp=12, sword=2)
        price = lambda dhp: self.sv.hp_seconds(ss, dhp)
        return self.perception.lease_done(state, rows, price)

    def test_a_blind_moment_does_not_hand_the_body_back(self):
        self.assertFalse(self.release([]), "an empty perception read released the lease mid-answer")

    def test_a_threat_still_there_keeps_it(self):
        self.assertFalse(self.release([row("minecraft:zombie", 4, 0)]))

    def test_an_answer_that_stopped_paying_gives_it_back(self):
        self.assertTrue(self.release([row("minecraft:zombie", 60, 0)]),
                        "nothing worth answering, yet the body was still held")


if __name__ == "__main__":
    unittest.main()
