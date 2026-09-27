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
            ("a zombie 5 away", [row("minecraft:zombie", 5, 0)], ("fight", 197.2)),
            ("a zombie 60 away: not worth the body", [row("minecraft:zombie", 60, 0)], None),
            ("a skeleton 10 away", [row("minecraft:skeleton", 10, 0)], ("fight", 173.1))]

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


class TheSkillsBatches(unittest.TestCase):
    """combat's shoot / guard / strike, as the pure batches the skills post."""

    class Bag:
        def __init__(self, items=(), offhand="minecraft:air"):
            self.items, self.hand = set(items), offhand

        def count(self, item):
            return 1 if item in self.items else 0

        def offhand(self):
            return self.hand

    # (offhand, swords carried) → the guard batch
    GUARD = [("minecraft:shield", ["minecraft:iron_sword"],
              [{"type": "use_item", "item": "minecraft:iron_sword", "yaw": 90.0, "pitch": 0, "holdTicks": 30}]),
             ("minecraft:shield", ["minecraft:stone_sword", "minecraft:diamond_sword"],
              [{"type": "use_item", "item": "minecraft:diamond_sword", "yaw": 90.0, "pitch": 0, "holdTicks": 30}]),
             ("minecraft:shield", [], []), ("minecraft:air", ["minecraft:iron_sword"], [])]

    def test_guard_batch(self):
        from bonobo import combat
        for hand, swords, want in self.GUARD:
            with self.subTest(hand=hand, swords=swords):
                self.assertEqual(combat.guard_batch(self.Bag(swords, hand), 90.0), want)

    def test_strike_batch(self):
        from bonobo import combat
        for name, e, want in [("by its id", {"id": 9, "x": 1.0, "y": 64.0, "z": 0.0}, [{"type": "attack", "entity": 9}]),
                              ("id 0 is an id", {"id": 0}, [{"type": "attack", "entity": 0}]),
                              ("position does not matter", {"id": 3, "x": 900.0}, [{"type": "attack", "entity": 3}]),
                              ("no id: refused", {"x": 1.0}, KeyError)]:
            with self.subTest(name):
                if want is KeyError:
                    with self.assertRaises(KeyError):
                        combat.strike_batch(e)
                else:
                    self.assertEqual(combat.strike_batch(e), want)

    def test_shoot_batch(self):
        from bonobo import combat
        eye = (0.0, 65.62, 0.0)
        drop = lambda d: 0.5 * combat.GRAVITY * (d / combat.ARROW_SPEED) ** 2      # noqa: E731
        # (situation, entity, hold) → where the arrow is aimed (x, y, z) and how long the draw
        rows = [("10 blocks, default height: body at 0.6, raised by the drop", {"x": 10.0, "y": 64.0, "z": 0.0},
                 22, (10.0, 64.6 + drop(10.0), 0.0)),
                ("point blank: no drop", {"x": 0.0, "y": 64.0, "z": 0.0}, 22, (0.0, 64.6, 0.0)),
                ("a tall target aims higher", {"x": 0.0, "y": 64.0, "z": 20.0, "height": 2.0}, 22,
                 (0.0, 65.2 + drop(20.0), 20.0)),
                ("a short draw", {"x": 10.0, "y": 64.0, "z": 0.0}, 10, (10.0, 64.6 + drop(10.0), 0.0))]
        for name, e, hold, (x, y, z) in rows:
            with self.subTest(name):
                (shot,) = combat.shoot_batch(e, eye, hold_ticks=hold)
                self.assertEqual((shot["type"], shot["item"], shot["holdTicks"]), ("use_item", "minecraft:bow", hold))
                self.assertEqual([round(shot[k], 6) for k in "xyz"], [round(x, 6), round(y, 6), round(z, 6)])

    def test_a_fight_is_on_while_the_body_is_engaged(self):
        from bonobo import arbiter, fight_loop
        rows = [(False, False), (True, True)]
        for engaged, want in rows:
            with self.subTest(engaged=engaged):
                body = arbiter.Motion()
                if engaged:
                    body.engage()
                saved, arbiter.BODY = arbiter.BODY, body
                try:
                    self.assertEqual(fight_loop.active(), want)
                finally:
                    arbiter.BODY = saved


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
            ("evade: travel there, never breaking or building (nav.MOVES)", ("evade", (-16, 64, 0)),
             {"counts": {"building": 12}},
             [{"type": "travel", "x": -16, "y": 64, "z": 0, "range": 3, "break": False, "place": False,
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

    def release(self, frames):
        """The lease's own judgement over a few perception frames [(seconds since the first, rows seen)]."""
        from unittest import mock
        from bonobo import field
        state = {"x": 0, "y": 64, "z": 0, "health": 12, "armor": 0, "sword_tier": 2,
                 "food_items": 0, "shield": False, "blocks": 64, "field": field.Field()}
        ss = self.sv.price_state(hp=12, sword=2)
        price = lambda dhp: self.sv.hp_seconds(ss, dhp)
        self.fight_loop._CHASE["at"] = None
        done = None
        for dt, rows in frames:
            with mock.patch.object(self.fight_loop.time, "time", return_value=1000.0 + dt):
                done = self.fight_loop.lease_done(state, rows, price)
        return done

    # (what perception sees, frame by frame) → is the answer done (hand the body back)?
    LOST = 3.5            # a little past fight_loop.LOST_S
    LEASE = [("a blind moment: nothing visible for a second", [(0, []), (1.0, [])], False),
             ("a zombie still at 4", [(0, [row("minecraft:zombie", 4, 0)]), (LOST, [row("minecraft:zombie", 4, 0)])],
              False),
             ("a zombie at 20, inside its notice radius: it follows, not over",
              [(0, [row("minecraft:zombie", 20, 0)]), (LOST, [row("minecraft:zombie", 20, 0)])], False),
             ("a skeleton at 10", [(0, [row("minecraft:skeleton", 10, 0)]), (LOST, [row("minecraft:skeleton", 10, 0)])],
              False),
             ("a zombie 60 away closing at 2 b/s: still chasing",
              [(0, [row("minecraft:zombie", 60, 0, vel=(-2.0, 0.0, 0.0))]),
               (LOST, [row("minecraft:zombie", 60, 0, vel=(-2.0, 0.0, 0.0))])], False),
             ("a zombie 60 away, standing, for LOST_S: outrun, and nothing owed",
              [(0, [row("minecraft:zombie", 60, 0)]), (LOST, [row("minecraft:zombie", 60, 0)])], True),
             ("the zombie killed: gone for LOST_S", [(0, [row("minecraft:zombie", 4, 0)]), (0.5, []), (LOST + 1, [])],
              True),
             ("gone only a moment ago", [(0, [row("minecraft:zombie", 4, 0)]), (1.0, [])], False)]

    def test_the_lease_over_the_table(self):
        for name, frames, done in self.LEASE:
            with self.subTest(name):
                self.assertEqual(self.release(frames), done)

if __name__ == "__main__":
    unittest.main()


class Losses(unittest.TestCase):
    """The day's price terms (threat.*_loss): each one's zero, its full cost and a point between, read against the
    belief table the module prices with (`_R`, `_T`, `_K`)."""
    R, T, K = threat._R, threat._T, threat._K

    def st(self, **kw):
        return threat.price_state(**kw)

    def test_bag_loss(self):
        full = self.T["day_s"] * self.K["mining_share_of_day"]
        c = self.R["bag_comfortable"]
        for name, free, want in [("empty bag: nothing lost", 36, 0.0), ("at the comfortable line: nothing", c, 0.0),
                                 ("half way down", c / 2, full / 2), ("full: the whole mining share", 0, full)]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.bag_loss(self.st(bag_free=free)), want)

    def test_larder_and_light(self):
        day, death = self.T["day_s"], self.T["death_cost_s"]
        starving = self.R["starving_slowdown"] * day
        for name, meals, want in [("a day's meals", 8, 0.0), ("some: runs out today", 2, 0.15 * day),
                                  ("one: starving", 1, starving),
                                  ("none: starving and may die", 0, starving + self.R["starving_death"] * death)]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.larder_loss(self.st(food_items=meals)), want)
        for name, torches, want in [("torches: nothing", True, 0.0),
                                    ("dark: the chance of a death", False, self.R["dark_work_death"] * death)]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.light_loss(self.st(torches=torches)), want)

    def test_hunger_loss(self):
        span = self.T["day_s"] * self.R["meal_share_of_day"]
        full, low = self.R["food_full"], self.R["food_low"]
        for name, food, want in [
                ("full bar: nothing", full, 0.0),
                ("a little down: scaled slowdown", full - 2, span * 2 / full * self.R["hunger_slowdown"]),
                ("at the low line: starving floor", low,
                 span * max((full - low) / full * self.R["hunger_slowdown"], self.R["starving_slowdown"])),
                ("empty", 0, span * max(self.R["hunger_slowdown"], self.R["starving_slowdown"]))]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.hunger_loss(self.st(food=food)), want)

    def test_food_loss_is_both_terms(self):
        for name, kw in [("fed and stocked", {}), ("hungry, stocked", {"food": 4, "food_items": 8}),
                         ("fed, empty larder", {"food_items": 0}), ("hungry and empty", {"food": 2, "food_items": 0})]:
            with self.subTest(name):
                s = self.st(**kw)
                self.assertAlmostEqual(threat.food_loss(s), threat.hunger_loss(s) + threat.larder_loss(s))

    def test_tool_loss(self):
        mining = self.K["mining_share_of_day"] * self.T["day_s"]
        iron = self.K["mine_time_iron"]
        for name, tier, want in [("iron: the reference", 2, 0.0), ("diamond: no better than the reference", 3, 0.0),
                                 ("stone", 1, mining * (self.K["mine_time_stone"] - iron)),
                                 ("no pickaxe", 0, mining * (self.K["mine_time_no_pickaxe"] - iron))]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.tool_loss(self.st(pickaxe=tier)), want)

    def test_night_loss(self):
        death, night = self.T["death_cost_s"], self.T["night_s"]
        for name, kw, want in [
                ("bed in a shelter: slept through", {"bed": True, "sheltered": True}, 0.0),
                ("bed in the open", {"bed": True}, self.R["night_bed_open"] * death),
                ("sheltered, armed", {"sheltered": True, "sword": 1}, self.R["night_sheltered"] * death),
                ("open, unarmed, 3 sleepless nights",
                 {"nights_missed": 3},
                 (self.R["night_open"] + self.R["no_sword_night"] + self.R["phantom_night_death"]) * death + night)]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.night_loss(self.st(**kw)), want)

    def test_fights_and_hurt(self):
        # (situation, state, what must hold) — the fight's price falls with gear, the deficit's with health
        base = threat.fight_loss(self.st())
        rows = [("a sword makes a day of fights cheaper", threat.fight_loss(self.st(sword=2)) < base, True),
                ("armour makes the same fight cheaper in health",
                 threat.encounter_damage(self.st(armor=4))[1] < threat.encounter_damage(self.st())[1], True),
                ("full health: no deficit to pay", threat.hurt_loss(self.st(hp=20)), 0.0),
                ("a deficit costs at least its regeneration",
                 threat.hurt_loss(self.st(hp=10)) >= 10 * self.R["regen_s_per_hp"], True),
                ("0 hp is priced like 0.1 (clamped, no division by zero)",
                 threat.hurt_loss(self.st(hp=0)) == threat.hurt_loss(self.st(hp=0.1)), True)]
        for name, got, want in rows:
            with self.subTest(name):
                self.assertEqual(got, want)

    def test_expected_loss_is_the_sum(self):
        for name, kw in [("fresh start", {}), ("night, hungry, hurt", {"night": True, "food": 3, "hp": 8}),
                         ("well kept", {"bed": True, "sheltered": True, "torches": True, "pickaxe": 2, "sword": 2,
                                        "food_items": 8}), ("full bag", {"bag_free": 0})]:
            with self.subTest(name):
                s = self.st(**kw)
                parts = (threat.night_loss(s) + threat.food_loss(s) + threat.tool_loss(s) + threat.light_loss(s)
                         + threat.fight_loss(s) + threat.hurt_loss(s) + threat.bag_loss(s))
                self.assertAlmostEqual(threat.expected_loss(s), parts)

    def test_price_state_refuses_unknown_keys(self):
        for name, kw, err in [("known key", {"hp": 5}, None), ("typo", {"hpp": 5}, "hpp"),
                              ("two unknown", {"a": 1, "b": 2}, "a"), ("empty", {}, None)]:
            with self.subTest(name):
                if err is None:
                    self.assertEqual(threat.price_state(**kw)["hp"], kw.get("hp", 20))
                else:
                    with self.assertRaisesRegex(KeyError, err):
                        threat.price_state(**kw)


class Kit(unittest.TestCase):
    """What the threat model believes we fight with: read from the bag, again whenever it may have changed."""

    # (situation, sword items carried (id, damage)) → the dps table's sword level
    SWORDS = [("an iron sword in the bag", [("minecraft:iron_sword", 0)], 2),
              ("a diamond sword in hand (a hotbar slot)", [("minecraft:diamond_sword", 10)], 3),
              ("no sword: the fist", [], 0),
              ("a stone sword", [("minecraft:stone_sword", 0)], 1),
              ("a wooden sword: level 1 (wood/stone), not the fist", [("minecraft:wooden_sword", 0)], 1),
              ("a broken-down iron sword and a stone one: the stone", [("minecraft:iron_sword", 250),
                                                                      ("minecraft:stone_sword", 3)], 1),
              ("netherite: the table's top", [("minecraft:netherite_sword", 0)], 3)]

    def test_sword_level_over_the_table(self):
        from unittest import mock
        from bonobo import perception
        from tests.world import bag, inventory, slot
        for name, swords, want in self.SWORDS:
            with self.subTest(name):
                inv = bag(inventory(*[slot(i.split(":")[1], 1, d) for i, d in swords]))
                with mock.patch("bonobo.world.Inventory", return_value=inv):
                    perception._KIT_SIG = object()                 # a fresh read
                    self.assertEqual(perception.kit(("x", name))["sword_tier"], want)

    def test_signature_changes_when_the_bag_may_have(self):
        from bonobo import perception
        base = {"selectedSlot": 0, "screen": "none", "armor": 0}
        sig = perception.kit_signature
        rows = [("the same state a moment later", base, 10.0, base, 10.5, True),
                ("the held slot moved", base, 10.0, {**base, "selectedSlot": 3}, 10.1, False),
                ("armour put on", base, 10.0, {**base, "armor": 15}, 10.1, False),
                ("a chest screen closed", {**base, "screen": "GenericContainerScreen"}, 10.0, base, 10.1, False),
                ("KIT_TTL_S passed (a sword given by command)", base, 10.0, base, 10.0 + perception.KIT_TTL_S, False)]
        for name, a, ta, b, tb, same in rows:
            with self.subTest(name):
                self.assertEqual(sig(a, ta) == sig(b, tb), same)


class EvadeOnlyPostpones(unittest.TestCase):
    """Walking away from what follows (it notices us at the spot, or shoots that far) is no answer while a fight is
    on offer: the same threat is there again. Evade is for what cannot be fought, or cover."""

    # (situation, threats, our state) → the answer
    ROWS = [("one zombie, an iron sword", [row("minecraft:zombie", 4, 0)], dict(sword=2, protection=0.5), "fight"),
            ("one skeleton 8 off, an iron sword", [row("minecraft:skeleton", 8, 0)], dict(sword=2), "fight"),
            ("one skeleton 14 off, an iron sword: it shoots that far, fight it", [row("minecraft:skeleton", 14, 0)],
             dict(sword=2, protection=0.5), "fight"),
            ("a creeper, cover 6 behind", [row("minecraft:creeper", 3, 0)],
             dict(sword=2, cover=(HERE[0] - 6, HERE[1], HERE[2])), "evade"),
            ("three zombies, 5 hp, a stone sword: cannot win, leave",
             [row("minecraft:zombie", 3, 0), row("minecraft:zombie", 0, 3), row("minecraft:zombie", -3, 0)],
             dict(hp=5, sword=1), "evade"),
            ("one zombie 40 away", [row("minecraft:zombie", 40, 0)], dict(sword=2), "ignore")]

    def test_answers_over_the_table(self):
        for name, hazards, kw, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(decide(hazards, **kw).kind, want)

    def test_follows_to(self):
        from bonobo import estimate
        cases = [("a zombie 20 from the spot: notices (35)", row("minecraft:zombie", 20, 0), True),
                 ("a zombie 50 from the spot: left behind", row("minecraft:zombie", 50, 0), False),
                 ("a skeleton 15 off: in reach", row("minecraft:skeleton", 15, 0), True),
                 ("a skeleton 30 off: neither", row("minecraft:skeleton", 30, 0), False)]
        for name, h, want in cases:
            with self.subTest(name):
                self.assertIs(estimate.follows_to(HERE, h), want)


class ShieldAndHole(unittest.TestCase):
    """Fighting behind a shield is its own answer (the swing's cooldown is spent blocking); a hole down needs ground
    that digs, not blocks carried."""

    def test_answers_over_the_table(self):
        from bonobo import field
        crowd = [row("minecraft:zombie", 4, 0), row("minecraft:zombie", 0, 4), row("minecraft:zombie", -4, 0)]
        rows = [("a zombie, a sword, a shield: fight behind it", [row("minecraft:zombie", 4, 0)],
                 dict(sword=2, shield=True), ("fight_shielded", 0)),
                ("a zombie, a sword, no shield: fight", [row("minecraft:zombie", 4, 0)], dict(sword=2), ("fight", 0)),
                ("a skeleton 10 off, a shield: fight behind it", [row("minecraft:skeleton", 10, 0)],
                 dict(sword=2, shield=True), ("fight_shielded", 0)),
                ("three zombies at night, 10 hp, no sword, ground that digs: a hole down", crowd,
                 dict(sword=0, hp=10, night=True, dig_ok=True, field=field.Field()), ("reshape", ("down", 2))),
                ("the same, ground that does not dig: leave", crowd,
                 dict(sword=0, hp=10, night=True, dig_ok=False, field=field.Field()), ("evade", (0, 64, -16)))]
        for name, hazards, kw, want in rows:
            with self.subTest(name):
                d = decide(hazards, **kw)
                self.assertEqual((d.kind, d.target), want)

    def test_the_shielded_batch(self):
        from bonobo import fight_loop
        from bonobo.threat import Option
        from tests.world import bag, inventory
        with_shield = bag(inventory(("iron_sword", 1), offhand="shield"))
        without = bag(inventory(("iron_sword", 1)))
        opt = Option("fight_shielded", 7, 1.0, 1.0, "")
        self.assertEqual(fight_loop.batch(opt, {"inv": with_shield}),
                         [{"type": "attack", "entity": 7, "shield": True}])
        self.assertEqual(fight_loop.batch(opt, {"inv": without}), [])



class EatingInAFight(unittest.TestCase):
    """Ordinary food heals by regen, later and only undisturbed; a golden apple heals now."""

    def test_eat_options_over_the_table(self):
        rows = [("mid-melee, 6 hp, bread: not an answer (regen needs quiet)", dict(hp=6, food_items=4, hunger=10), 2.0,
                 []),
                ("walled in, 6 hp, bread, hungry: eat", dict(hp=6, food_items=4, hunger=10), 0.1, [("eat", None, 6.0)]),
                ("mid-melee, 6 hp, a golden apple: eat it now", dict(hp=6, golden_apples=1), 2.0,
                 [("eat", "minecraft:golden_apple", 8.0)]),
                ("walled in, full hunger bar: cannot eat", dict(hp=6, food_items=4, hunger=20), 0.1, []),
                ("full health: nothing to heal", dict(hp=20, food_items=4, golden_apples=1, hunger=10), 0.1, [])]
        for name, st, press, want in rows:
            with self.subTest(name):
                got = threat.eat_options(st, st["hp"], press, 0.0)
                self.assertEqual([(o.kind, o.target, o.heals) for o in got], want)
