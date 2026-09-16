"""The ordinary-play threat layer, as situations: each test is a scene from the log that went wrong, and asserts the
decision the model gives now. Geometry is real (reach and dps from play.toml), not invented per test."""
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


class Pressure(unittest.TestCase):
    def test_a_skeleton_in_range_presses_now(self):
        self.assertGreater(threat.pressure(HERE, [row("minecraft:skeleton", 12, 0)]), 0.0)

    def test_a_zombie_far_away_presses_nothing_yet(self):
        # 40 blocks at 2.3 b/s is beyond the horizon in every future.
        self.assertEqual(threat.pressure(HERE, [row("minecraft:zombie", 40, 0)]), 0.0)

    def test_a_zombie_eight_blocks_out_presses_part_of_the_horizon(self):
        p = threat.pressure(HERE, [row("minecraft:zombie", 8, 0)])
        self.assertTrue(0.0 < p < threat.MOBS["minecraft:zombie"]["dps"])

    def test_protection_scales_it(self):
        full = threat.pressure(HERE, [row("minecraft:skeleton", 10, 0)])
        self.assertAlmostEqual(threat.pressure(HERE, [row("minecraft:skeleton", 10, 0)], prot=0.5), full / 2)


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

    def test_no_hostiles_means_one_answer(self):
        self.assertEqual(list(self.priced([])), ["ignore"])

    def test_every_answer_is_priced_in_health_and_seconds(self):
        for o in self.priced([row("minecraft:zombie", 4, 0)], sword=1).values():
            self.assertIsInstance(o.hp, float)
            self.assertGreaterEqual(o.hp, 0.0)
            self.assertGreaterEqual(o.seconds, 0.0)

    def test_carrying_on_is_an_option_with_a_price(self):
        # "Do nothing" must cost something, or it wins by default whenever the others look expensive.
        opts = self.priced([row("minecraft:zombie", 4, 0)], sword=1)
        self.assertGreater(opts["ignore"].hp, 0.0)

    def test_fighting_and_leaving_are_both_on_the_table(self):
        opts = self.priced([row("minecraft:zombie", 4, 0)], sword=1)
        self.assertIn("fight", opts)
        self.assertIn("evade", opts)

    def test_a_creeper_is_never_offered_as_a_fight(self):
        # The one hard exclusion: a blast is not a trade, at any weapon or health.
        self.assertNotIn("fight", self.priced([row("minecraft:creeper", 4, 0)], sword=3))

    def test_walling_in_appears_only_with_blocks_to_do_it(self):
        hz = [row("minecraft:zombie", 3, 0)]
        self.assertNotIn("wall_in", self.priced(hz))
        self.assertIn("wall_in", self.priced(hz, blocks=32))

    def test_a_weapon_makes_fighting_cheaper(self):
        hz = [row("minecraft:zombie", 4, 0)]
        self.assertLess(self.priced(hz, sword=2)["fight"].hp, self.priced(hz, sword=0)["fight"].hp)

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


if __name__ == "__main__":
    unittest.main()


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

    def test_saves_is_the_difference_between_two_prices(self):
        st = self.state()
        opts = threat.options(st)
        price, ignore = (lambda dhp: dhp), next(o for o in opts if o.kind == "ignore")
        for o in opts:
            self.assertAlmostEqual(threat.saves(o, opts, price, st["work_s"]),
                                   threat.option_cost(ignore, price, st["work_s"])
                                   - threat.option_cost(o, price, st["work_s"]))


class PricesForTheOtherPlanner(unittest.TestCase):
    """What combat hands ordinary play: a rate and a field, never an answer."""

    def rows(self, *hazards):
        return dict(here=(0, 0, 0), hp=20.0, sword=1, protection=0.0, hazards=list(hazards))

    def test_an_empty_field_taxes_nothing(self):
        self.assertEqual(threat.hp_tax_rate(self.rows()), 0.0)

    def test_the_tax_rises_as_they_close_in(self):
        far = self.rows(threat.row((12, 0, 0), 2.0, (-1.0, 0, 0), "minecraft:zombie"))
        near = self.rows(threat.row((2, 0, 0), 2.0, (-1.0, 0, 0), "minecraft:zombie"))
        self.assertGreater(threat.hp_tax_rate(near), threat.hp_tax_rate(far))

    def test_armour_lowers_the_tax(self):
        bare = self.rows(threat.row((2, 0, 0), 2.0, (-1.0, 0, 0), "minecraft:zombie"))
        self.assertLess(threat.hp_tax_rate(dict(bare, protection=0.6)), threat.hp_tax_rate(bare))

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

    def test_armed_and_healthy_bids_to_fight(self):
        got = self.bid([row("minecraft:zombie", 5, 0)])
        self.assertEqual(got[0].kind, "fight")
        self.assertGreater(got[1], 0)

    def test_bare_handed_bids_to_leave(self):
        self.assertEqual(self.bid([row("minecraft:zombie", 5, 0)], sword=0, armor=0)[0].kind, "evade")

    def test_the_bid_is_what_it_saves(self):
        rows = [row("minecraft:zombie", 5, 0)]
        ss = self.sv.make_state(hp=20, sword=2, armor=8)
        price = lambda dhp: self.sv.hp_seconds(ss, dhp)
        option, worth = self.bid(rows)
        st = dict(here=(0, 64, 0), hp=20.0, sword=2, protection=threat.protection(8, False), night=False,
                  blocks=64, hazards=rows)
        opts = threat.options(st)
        expected = threat.saves(next(o for o in opts if o.kind == option.kind), opts, price,
                                threat.horizon_for(st))
        self.assertAlmostEqual(worth, round(expected, 1), places=1)


class ColumnsThatMatterWhenHurt(unittest.TestCase):
    """At 4 hp the three old answers all assume open ground, so the model said "nothing helps" and the blood
    threshold took over. What is actually worth doing there is a column, not an if."""

    def state(self, **kw):
        st = dict(here=(0, 64, 0), hp=6.0, sword=2, protection=0.0, night=True, blocks=64,
                  hazards=[row("minecraft:zombie", 5, 0)], food_items=4, shield=True)
        st.update(kw)
        return st

    def kinds(self, st):
        return {o.kind for o in threat.options(st)}

    def test_eating_is_an_option_when_hurt_and_carrying_food(self):
        self.assertIn("eat", self.kinds(self.state()))

    def test_eating_is_not_offered_at_full_health(self):
        self.assertNotIn("eat", self.kinds(self.state(hp=20.0)))

    def test_eating_is_not_offered_with_an_empty_bag(self):
        self.assertNotIn("eat", self.kinds(self.state(food_items=0)))

    def test_raising_a_shield_is_an_option_when_one_is_carried(self):
        self.assertIn("shield", self.kinds(self.state()))
        self.assertNotIn("shield", self.kinds(self.state(shield=False)))

    def test_eating_heals_and_shielding_protects(self):
        opts = {o.kind: o for o in threat.options(self.state())}
        self.assertGreater(opts["eat"].heals, 0.0)
        self.assertGreater(opts["shield"].protects, 0.0)

    def tight(self):
        from bonobo import field as fd
        return fd.Field(speed=4.3, bucket="underground", terrain=fd.Terrain(prior=2.0))

    def test_reshaping_the_ground_is_an_option_with_a_block_in_hand(self):
        self.assertIn("reshape", self.kinds(self.state(field=self.tight())))
        self.assertNotIn("reshape", self.kinds(self.state(field=self.tight(), blocks=0)))

    def test_blocking_the_way_is_pointless_in_the_open(self):
        """A block on open ground is walked around in one step; standing on one still breaks an archer's line."""
        from bonobo import field as fd
        wheres = {o.target[0] for o in threat.options(self.state(field=fd.Field(speed=4.3, bucket="open")))
                  if o.kind == "reshape"}
        self.assertNotIn("between", wheres)

    def test_enough_of_it_stops_what_was_coming(self):
        placed = [o for o in threat.options(self.state(field=self.tight())) if o.kind == "reshape"]
        self.assertEqual(min(o.leaves for o in placed), 0.0)
        self.assertTrue(all(o.target for o in placed))


class EveryEnemy(unittest.TestCase):
    """The answers have to make sense for each kind of thing that hurts, not just a zombie in the open."""

    def price(self, hp=20, sword=2, armor=8):
        ss = sv.make_state(hp=hp, sword=sword, armor=armor)
        return lambda dhp: sv.hp_seconds(ss, dhp)

    def state(self, kind, distance=5, **kw):
        from bonobo import field as fd
        st = dict(here=(0, 64, 0), hp=20.0, sword=2, protection=threat.protection(8, False), night=True,
                  blocks=64, hazards=[row(kind, distance, 0)], food_items=4, shield=True,
                  field=fd.Field(speed=4.3))
        st.update(kw)
        return st

    def best(self, kind, **kw):
        st = self.state(kind, **kw)
        opts = threat.options(st)
        horizon = threat.horizon_for(st)
        price = self.price(hp=st["hp"], sword=st["sword"])
        ranked = sorted(opts, key=lambda o: -threat.saves(o, opts, price, horizon))
        return ranked[0].kind

    def test_a_creeper_is_never_fought(self):
        self.assertNotIn("fight", {o.kind for o in threat.options(self.state("minecraft:creeper", distance=4))})

    def test_a_skeleton_at_range_is_still_an_answer(self):
        opts = {o.kind for o in threat.options(self.state("minecraft:skeleton", distance=12))}
        self.assertTrue({"fight", "evade"} & opts, opts)

    def test_a_dragon_is_not_something_to_trade_blows_with(self):
        self.assertNotEqual(self.best("minecraft:ender_dragon", distance=6, hp=10.0), "fight")

    def corridor(self, kind):
        from bonobo import field as fd
        return self.state(kind, distance=6,
                          field=fd.Field(speed=4.3, bucket="underground", terrain=fd.Terrain(prior=2.0)))

    def test_blocking_is_pointless_against_what_squeezes_past(self):
        for kind in ("minecraft:spider", "minecraft:enderman", "minecraft:phantom"):
            wheres = {o.target[0] for o in threat.options(self.corridor(kind)) if o.kind == "reshape"}
            self.assertNotIn("between", wheres, kind)

    def test_blocking_a_corridor_works_on_what_has_to_walk_up_to_us(self):
        for kind in ("minecraft:zombie", "minecraft:husk", "minecraft:drowned"):
            wheres = {o.target[0] for o in threat.options(self.corridor(kind)) if o.kind == "reshape"}
            self.assertIn("between", wheres, kind)

    def test_blocking_does_nothing_about_a_blast(self):
        """A creeper's damage is not a rate, so delaying its walk does not reduce it: only distance does."""
        self.assertNotIn("between", {o.target[0] for o in threat.options(self.corridor("minecraft:creeper"))
                                     if o.kind == "reshape"})

    def test_blocking_does_nothing_about_an_archer(self):
        """A skeleton is already in reach from fifteen blocks: delaying its walk changes nothing, and the model
        says so rather than pretending a block is cover."""
        self.assertNotIn("between", {o.target[0] for o in threat.options(self.corridor("minecraft:skeleton"))
                                     if o.kind == "reshape"})

    def test_a_crowd_changes_the_answer(self):
        """Both a zombie and three of them "kill you if ignored" — a one-step price cannot separate those, and
        pretending it can is fake precision. What must differ is the answer: one is worth killing, a crowd is not."""
        many = self.state("minecraft:zombie", distance=5)
        many["hazards"] = [row("minecraft:zombie", 5, 0), row("minecraft:zombie", 5, 2),
                           row("minecraft:zombie", 4, -2)]
        opts = threat.options(many)
        price, horizon = self.price(), threat.horizon_for(many)
        best = max((o for o in opts if o.kind != "ignore"), key=lambda o: threat.saves(o, opts, price, horizon))
        self.assertNotEqual(best.kind, "fight")
        self.assertEqual(self.best("minecraft:zombie", distance=5), "fight")

    def test_being_hurt_brings_out_the_health_answers(self):
        st = self.state("minecraft:zombie", distance=6, hp=6.0)
        self.assertTrue({"eat", "shield"} <= {o.kind for o in threat.options(st)})


class Reshaping(unittest.TestCase):
    """Blocking the way, standing on a block and digging down are one column: the same effect (they are slower to
    reach us, or they stop seeing us) bought with the same currency (seconds placing, blood while exposed)."""

    def state(self, kind="minecraft:zombie", distance=6, bucket="underground", blocks=64, **kw):
        from bonobo import field as fd
        st = dict(here=(0, 64, 0), hp=14.0, sword=2, protection=0.0, night=True, blocks=blocks,
                  hazards=[row(kind, distance, 0)], food_items=0, shield=False,
                  field=fd.Field(speed=4.3, bucket=bucket, terrain=fd.Terrain(prior=2.0)))
        st.update(kw)
        return st

    def shaped(self, st):
        return [o for o in threat.options(st) if o.kind == "reshape"]

    def wheres(self, st):
        return {o.target[0] for o in self.shaped(st)}

    def best_where(self, st):
        opts = threat.options(st)
        horizon = threat.horizon_for(st)
        ss = sv.make_state(hp=int(st["hp"]), sword=st["sword"])
        price = lambda dhp: sv.hp_seconds(ss, dhp)
        shaped = self.shaped(st)
        return max(shaped, key=lambda o: threat.saves(o, opts, price, horizon)).target[0] if shaped else None

    def test_all_three_are_the_same_column(self):
        self.assertEqual({o.kind for o in self.shaped(self.state())}, {"reshape"})
        self.assertTrue({"between", "under", "down"} & self.wheres(self.state()))

    def test_nothing_to_place_no_column(self):
        self.assertEqual(self.shaped(self.state(blocks=0)), [])

    def test_more_work_never_leaves_more_coming(self):
        between = sorted((o for o in self.shaped(self.state()) if o.target[0] == "between"),
                         key=lambda o: o.target[1])
        leaves = [o.leaves for o in between]
        self.assertTrue(all(b <= a for a, b in zip(leaves, leaves[1:])), leaves)

    def test_more_of_it_costs_more_time(self):
        between = sorted((o for o in self.shaped(self.state()) if o.target[0] == "between"),
                         key=lambda o: o.target[1])
        self.assertGreater(between[-1].seconds, between[0].seconds)

    def test_against_an_archer_in_the_open_it_breaks_the_line_of_sight(self):
        """A skeleton is already in reach: delaying its walk is worth nothing, getting out of sight is worth
        everything, so the chosen shape must be the one that hides us."""
        self.assertIn(self.best_where(self.state(kind="minecraft:skeleton", distance=12, bucket="open")),
                      ("under", "down"))

    def test_in_a_tunnel_against_something_that_walks_it_blocks_the_way(self):
        self.assertEqual(self.best_where(self.state(kind="minecraft:zombie", distance=6)), "between")

    def test_nothing_is_worth_shaping_against_what_squeezes_past(self):
        for kind in ("minecraft:spider", "minecraft:enderman"):
            self.assertNotIn("between", self.wheres(self.state(kind=kind)), kind)
