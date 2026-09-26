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
from bonobo import threat as sv  # noqa: E402  (the price of health lives in threat now)
from bonobo import threat  # noqa: E402

HERE = (0.0, 64.0, 0.0)      # fixture: where we stand
STILL = (0.0, 0.0, 0.0)      # fixture: a mob at rest


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
    sstate = sv.price_state(hp=state["hp"], sword=state["sword"], pickaxe=1, food_items=8, bed=True)
    return threat.decide(state, lambda dhp: sv.hp_seconds(sstate, dhp))


class Rows(unittest.TestCase):
    ZOMBIE = {"minecraft:zombie": 3.0}      # fixture: the kinds asked for

    # (last reading of entity 7 or None, this reading, kinds asked for) → the rows built: (centre, velocity)
    ROWS = [("first sight: at rest", None, {"type": "minecraft:zombie", "x": 10.0}, ZOMBIE,
             [((10.0, 64.0, 0.0), (0.0, 0.0, 0.0))]),
            ("two blocks nearer in a second", ((10.0, 64.0, 0.0), 100.0), {"type": "minecraft:zombie", "x": 8.0},
             ZOMBIE, [((8.0, 64.0, 0.0), (-2.0, 0.0, 0.0))]),
            ("the last reading is 3 s old: at rest, not a teleport", ((20.0, 64.0, 0.0), 98.0),
             {"type": "minecraft:zombie", "x": 8.0}, ZOMBIE, [((8.0, 64.0, 0.0), (0.0, 0.0, 0.0))]),
            ("a kind nobody asked about is not a row", None, {"type": "minecraft:cow", "x": 1.0}, ZOMBIE, []),
            ("nothing asked for, nothing built", None, {"type": "minecraft:zombie", "x": 1.0}, {}, [])]

    def test_rows_over_the_table(self):
        for name, prev, reading, kinds, want in self.ROWS:
            with self.subTest(name):
                mem = {} if prev is None else {7: prev}
                e = dict({"id": 7, "y": 64.0, "z": 0.0}, **reading)
                got = threat.rows([e], mem, 101.0 if prev else 100.0, kinds)
                self.assertEqual([(r[0], tuple(r[2])) for r in got], want)


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


    # (threats) → where to run: 16 blocks away from them, never between two of them
    ESCAPE = [("two archers flanking east", [("minecraft:skeleton", 10, 2), ("minecraft:skeleton", 10, -2)],
               (-16, 64, 0)),
              ("one zombie east", [("minecraft:zombie", 5, 0)], (-16, 64, 0)),
              ("one zombie west", [("minecraft:zombie", -5, 0)], (16, 64, 0)),
              ("one zombie south", [("minecraft:zombie", 0, 6)], (0, 64, -16)),
              ("nothing: an arbitrary way, still a spot", [], (16, 64, 0))]

    def test_escape_spot_over_the_table(self):
        for name, hz, want in self.ESCAPE:
            with self.subTest(name):
                self.assertEqual(threat.escape_spot(HERE, [row(k, x, z) for k, x, z in hz]), want)


class Interrupt(unittest.TestCase):
    # (seconds until dead at this pressure, the task under way) → the interrupt. Closer than one planning round
    # (4 s) stops the task; an attack is its own answer and is never interrupted for being hit.
    ROWS = [(2.0, "mine", "hostiles"), (4.0, "mine", "hostiles"), (4.01, "mine", None), (30.0, "mine", None),
            (None, "mine", None), (2.0, "attack", None)]

    def test_perception_stops_a_task_when_arrows_would_kill_soon(self):
        from bonobo import perception
        self.assertEqual(perception.interrupt_within_s(), 4.0)
        for ttd, task, want in self.ROWS:
            with self.subTest(ttd=ttd, task=task):
                state = {"health": 12, "food": 20, "x": 0.0, "y": 64.0, "z": 0.0, "control": {"task": {"type": task}}}
                self.assertEqual(perception.danger(state, time_to_die=lambda: ttd), want)


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


    # (the threat) → the circle not to walk into: its reach plus a margin
    NO_GO = [("a zombie, reach 2", threat.row((10, 0, 0), 2.0, (0, 0, 0), "minecraft:zombie"), [((10, 0, 0), 4.0)]),
             ("a skeleton, reach 15", threat.row((10, 0, 0), 15.0, (0, 0, 0), "minecraft:skeleton"),
              [((10, 0, 0), 17.0)]),
             ("a creeper, reach 3", threat.row((10, 0, 0), 3.0, (0, 0, 0), "minecraft:creeper"), [((10, 0, 0), 5.0)]),
             ("nothing: no circle", None, [])]

    def test_no_go_over_the_table(self):
        for name, hazard, want in self.NO_GO:
            with self.subTest(name):
                zones = threat.no_go(self.rows(*([hazard] if hazard else [])))
                self.assertEqual(zones, want)
                if not zones:
                    continue
                (centre, radius), = zones
                outside = (centre[0], centre[1], centre[2] + radius + 1)
                self.assertEqual([threat.inside_no_go(p, zones) for p in (centre, outside)], [True, False])


class TheFastLane(unittest.TestCase):
    """Threat answers are bid for the body at perception's cadence, not queued for the next ten-second round."""

    def setUp(self):
        from bonobo import fight_loop, threat as sv
        self.fight_loop, self.sv = fight_loop, sv
        fight_loop.HELD = None      # each case is its own situation, not a continuation of the last

    def bid(self, rows, hp=20, sword=2, armor=8):
        ss = self.sv.price_state(hp=hp, sword=sword, armor=armor)
        state = {"x": 0, "y": 64, "z": 0, "health": hp, "armor": armor, "sword_tier": sword, "blocks": 64}
        return self.fight_loop.bid(state, rows, lambda dhp: self.sv.hp_seconds(ss, dhp))

    # (rows in sight) → (answer, seconds it is worth), or None: no bid
    BIDS = [("nothing near", [], None),
            ("a zombie 5 away", [row("minecraft:zombie", 5, 0)], ("fight", 195.9)),
            ("a zombie 60 away: not worth the body", [row("minecraft:zombie", 60, 0)], None),
            ("a skeleton 10 away", [row("minecraft:skeleton", 10, 0)], ("fight", 173.3))]

    def test_bids_over_the_table(self):
        for name, rows, want in self.BIDS:
            with self.subTest(name):
                self.fight_loop.HELD = None
                got = self.bid(rows)
                self.assertEqual(None if got is None else (got[0].kind, got[1]), want)

    def test_the_bid_is_what_its_own_answer_saves(self):
        """Closed against the state the bid itself built, not against one reassembled here: a test that rebuilds
        the state vector is a second copy of `fight_loop.threat_state`, and the two drifted the moment the live
        one started reading the ground and the kit.
        """
        rows = [row("minecraft:zombie", 5, 0)]
        ss = self.sv.price_state(hp=20, sword=2, armor=8)
        price = lambda dhp: self.sv.hp_seconds(ss, dhp)
        state = {"x": 0, "y": 64, "z": 0, "health": 20, "armor": 8, "sword_tier": 2, "blocks": 64}
        option, worth = self.fight_loop.bid(state, rows, price)
        st = self.fight_loop.threat_state(state, rows)
        opts = threat.options(st)
        same = next(o for o in opts if o.kind == option.kind)
        self.assertAlmostEqual(worth, round(threat.saves(same, opts, price, threat.horizon_for(st)), 1), places=1)


def nav_mine(cell):
    from bonobo import nav
    return nav.mine_task(cell)


class EachAnswerIsABatch(unittest.TestCase):
    """`fight_loop.batch`: what one answer posts, from a body state. Pure; [] means it cannot be done from here."""

    class Bag:
        def __init__(self, counts=None, offhand="minecraft:air"):
            self.counts, self.hand = dict(counts or {}), offhand

        def count(self, item):
            return self.counts.get(item, 0)

        def offhand(self):
            return self.hand

    @staticmethod
    def option(kind, target=None):
        return type("Option", (), {"kind": kind, "target": target})()

    def state(self, **bag):
        return {"feet": (0, 64, 0), "inv": self.Bag(**bag), "region": None, "protected": set(),
                "threats": [row("minecraft:zombie", 5, 0)]}

    ROWS = [("fight: attack the target", ("fight", 42), {}, [{"type": "attack", "entity": 42}]),
            ("evade: travel there, bridging with what we carry", ("evade", (-16, 64, 0)),
             {"counts": {"building": 12}},
             [{"type": "travel", "x": -16, "y": 64, "z": 0, "range": 3, "break": True, "place": True,
               "placeBudget": 12, "avoid": []}]),
            ("eat the first food carried", ("eat",), {"counts": {"minecraft:cooked_beef": 3}},
             [{"type": "eat", "item": "minecraft:cooked_beef"}]),
            ("eat with nothing to eat: not an answer", ("eat",), {}, []),
            ("shield up with a shield in hand", ("shield",), {"offhand": "minecraft:shield"},
             [{"type": "use_item", "hand": "offhand", "hold_ms": 1500}]),
            ("shield up without one: not an answer", ("shield",), {}, []),
            ("dig down two", ("reshape", ("down", 2)), {},
             [nav_mine((0, 63, 0)), nav_mine((0, 62, 0))]),
            ("stand two up", ("reshape", ("under", 2)), {"counts": {"minecraft:cobblestone": 5}},
             [{"type": "pillar", "item": "minecraft:cobblestone"}] * 2),
            ("a wall toward the zombie in the east", ("reshape", ("between", 2)),
             {"counts": {"minecraft:cobblestone": 5}},
             [{"type": "place", "item": "minecraft:cobblestone", "x": 1, "y": 64 + i, "z": 0} for i in range(2)]),
            ("no blocks to stand on: not an answer", ("reshape", ("under", 1)), {}, []),
            ("walling in with no region read: not an answer", ("wall_in",), {}, []),
            ("ignoring is no batch", ("ignore",), {}, [])]

    def test_batch_over_the_table(self):
        from bonobo import fight_loop
        for name, (kind, *target), bag, want in self.ROWS:
            with self.subTest(name):
                got = fight_loop.batch(self.option(kind, target[0] if target else None), self.state(**bag))
                self.assertEqual(got, want)


class TheLeaseSurvivesBlindMoments(unittest.TestCase):
    """Perception goes blind for a moment all the time: the entity read is a second old, the thread was busy, the
    rows aged out. A lease that reads "nothing visible" as "nothing to answer" hands the body back mid-fight, and
    the planner's next mine task lands on top of the answer. Only a real price says the answer is done."""

    def setUp(self):
        from bonobo import fight_loop, threat as sv
        self.fight_loop, self.sv = fight_loop, sv
        fight_loop.HELD = None

    def release(self, rows):
        """The lease's own judgement, given what perception can see right now."""
        from bonobo import field
        state = {"x": 0, "y": 64, "z": 0, "health": 12, "armor": 0, "sword_tier": 2,
                 "food_items": 0, "shield": False, "blocks": 64, "field": field.Field()}
        ss = self.sv.price_state(hp=12, sword=2)
        price = lambda dhp: self.sv.hp_seconds(ss, dhp)
        return self.fight_loop.lease_done(state, rows, price)

    # (what perception sees now) → is the answer done (hand the body back)?
    LEASE = [("a blind moment: nothing visible", [], False),
             ("a zombie still at 4", [row("minecraft:zombie", 4, 0)], False),
             ("a zombie at 20", [row("minecraft:zombie", 20, 0)], False),
             ("a zombie at 30", [row("minecraft:zombie", 30, 0)], False),
             ("a skeleton at 10", [row("minecraft:skeleton", 10, 0)], False),
             ("the zombie is 60 away: stopped paying", [row("minecraft:zombie", 60, 0)], True)]

    def test_the_lease_over_the_table(self):
        for name, rows, done in self.LEASE:
            with self.subTest(name):
                self.assertEqual(self.release(rows), done)


if __name__ == "__main__":
    unittest.main()
