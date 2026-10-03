"""What doing something costs — an action's health and its seconds, as one number.

The third of the five quantities. `act_cost_s(seconds, hp, price)` is the whole of it; `fight_cost` and
`leaving_hp` are the two ways the health half is worked out when the action is a fight or a walk away.

Properties over the combat sweep, and over an arbitrary price of health: nothing here may depend on what the
price function IS, only on it being monotone — that is what keeps time and health comparable.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import estimate, threat  # noqa: E402
from tests.world import BLOOD_LOST, HP_PRICES, RATES, SECONDS, dangers  # noqa: E402

class OneCurrency(unittest.TestCase):
    def test_it_is_the_time_plus_the_priced_health(self):
        for name, price in HP_PRICES.items():
            for seconds in SECONDS:
                for hp in BLOOD_LOST:
                    self.assertAlmostEqual(estimate.act_cost_s(seconds, hp, price), seconds + price(hp),
                                           places=6, msg=f"{name} {seconds}s {hp}hp")

    def test_doing_nothing_costs_nothing(self):
        for price in HP_PRICES.values():
            self.assertEqual(estimate.act_cost_s(0.0, 0.0, price), 0.0)

    def test_it_grows_along_both_ladders(self):
        for price in HP_PRICES.values():
            for hp in BLOOD_LOST:
                seen = [estimate.act_cost_s(seconds, hp, price) for seconds in SECONDS]
                self.assertEqual(seen, sorted(seen), f"{hp}hp: {seen}")
            for seconds in SECONDS:
                seen = [estimate.act_cost_s(seconds, hp, price) for hp in BLOOD_LOST]
                self.assertEqual(seen, sorted(seen), f"{seconds}s: {seen}")

    def test_the_same_action_costs_more_the_less_blood_there_is(self):
        """The price is the caller's, and this is why: the same four hearts are a scratch or most of a death. Read
        along the ladder, so the claim is about the direction the dimension declares rather than two cells."""
        for cell in dangers(enemy="walker", distance="near", ground="open", blood="whole"):
            seen = [estimate.act_cost_s(SECONDS[-1], BLOOD_LOST[-2], world.price())
                    for world in cell.along("blood")]
            self.assertEqual(seen, sorted(seen), f"{cell}: {seen}")


HERE = (0, 64, 0)          # fixture: where we stand
STONE = "minecraft:stone_sword"


def mob(kind, x):
    return estimate.row((x, 64, 0), 3.0, (0.0, 0.0, 0.0), f"minecraft:{kind}")      # the row as perception builds it


class AFightExactly(unittest.TestCase):
    """`fight_cost` on hand-placed rows, to the hundredth. From the game's weapon data (data.weapon_hit): a fist hits 1
    at 4/s, a stone sword 5 at 1.6/s — whole hits; a zombie has 20 hp and does 3 dps (3 per 20 ticks), a skeleton 20 hp at 4 per 60 ticks and shoots; melee reach 3, speed 5.612 (sprint).
    per kill: see = api.READ_EVERY_S (0.1, the loop's poll); walk = (distance − 3) / 5.612; kill = ceil(20 / hit) / rate;
    pickup = (3 − pickup_r 1) / 5.612 onto the drops; lost = (see + walk) × ranged still alive + kill × all still alive
    + pickup × ranged left alive, all incoming capped at 6 hp/s (a 3-hp hit every 0.5 s of hurt immunity). Every
    term is non-zero in every row with a mob: the totals fail when any one of them is dropped (D6, hidden work)."""

    def test_the_belief_table_is_what_the_rows_assume(self):
        from bonobo.beliefs import MOBS, PLAYER
        from bonobo.api import READ_EVERY_S
        from bonobo.data import weapon_hit
        self.assertEqual((weapon_hit(None), weapon_hit(STONE), PLAYER["melee_reach"], PLAYER["speed"],
                          PLAYER["pickup_r"], READ_EVERY_S), ((1.0, 4.0), (5.0, 1.6), 3.0, 5.612, 1.0, 0.1))
        rows = [MOBS[f"minecraft:{k}"] for k in ("zombie", "skeleton")]
        self.assertEqual([(m["hp"], m["dps"]) for m in rows], [(20, 3.0), (20, 4.0 / 3)])

    # (situation, rows, sword held, protection) → (seconds, hp lost)
    ROWS = [("must fail: nothing to fight", [], STONE, 0.0, (0.0, 0.0)),
            # k = 1 − 0.22/(1 + 0.22) = 0.8195 (each zombie blow waits 0.88/4 s after our push)
            # 0.1 + 20/2 + 2/5.612 = 10.46 (2 hits/s: the target's hurt immunity); 10 s × 3k = 24.58
            ("a zombie in reach, bare hands", [mob("zombie", 2)], None, 0.0, (10.46, 24.58)),
            # must fail without the see (2.86), without the pickup walk (2.6), or the kill alone (2.5)
            ("a zombie in reach, stone sword: see + 4 hits / 1.6 s + pickup", [mob("zombie", 2)], STONE, 0.0, (2.96, 6.15)),
            # must fail without the walk in (2.96); 0.1 + 7/5.612 + 4/1.6 + 2/5.612 = 4.2; 2.5 × 3k = 6.15
            ("a zombie 10 away: the walk is free of a melee mob", [mob("zombie", 10)], STONE, 0.0, (4.2, 6.15)),
            # 2.5 × 3k + 3 × (2.6 − 0.25 arrival) + its 4 hits after 3 sweeps (17 hp) 2.5 × 3k = 19.34
            ("two zombies: the second from its arrival, swept", [mob("zombie", 2), mob("zombie", 4)], STONE,
             0.0, (5.91, 19.34)),
            # chased at 5.612 − 1.349 (its retreat): (0.1 + 7/4.263) × 4/3 + 2.5 × 4/3 = 5.66
            ("a skeleton 10 away: shot at while seen and on the walk", [mob("skeleton", 10)], STONE, 0.0, (4.6, 5.66)),
            # must fail when the pickup is charged the skeleton's arrows after it is dead, or not at all
            # (0.1×4/3 + 2.5×3k + 0.6×4/3 (arrives at 2.0) + 0.36×4/3 + (0.1 + 5/4.263)×4/3 + 2.5×4/3) × 0.5 = 6.29
            ("zombie then skeleton, half armoured off; shot at on the zombie's pickup", [mob("zombie", 2),
             mob("skeleton", 10)], STONE, 0.5, (7.09, 6.29))]

    def test_fight_cost_over_the_table(self):
        for name, rows, sword, prot, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(estimate.fight_cost(HERE, rows, sword, prot), want)


class AFight(unittest.TestCase):
    def test_it_takes_time_and_health_whenever_there_is_anything_to_kill(self):
        for cell in dangers():
            seconds, hp = estimate.fight_cost(cell.here, cell.rows(), cell.sword, cell.armour)
            if not cell.rows():
                self.assertEqual((seconds, hp), (0.0, 0.0), cell)
            else:
                self.assertGreater(seconds, 0.0, cell)
                self.assertGreaterEqual(hp, 0.0, cell)

    def test_a_better_weapon_is_faster_and_cheaper(self):
        for cell in dangers(enemy="walker", weapon="fist"):
            costs = [estimate.fight_cost(world.here, world.rows(), world.sword, 0.0)
                     for world in cell.along("weapon")]
            self.assertEqual([c[0] for c in costs], sorted((c[0] for c in costs), reverse=True), cell)
            self.assertEqual([c[1] for c in costs], sorted((c[1] for c in costs), reverse=True), cell)

    def test_more_of_them_is_longer_and_dearer(self):
        """One dimension moved, everything else the cell's own: `enemy` is the only thing that differs between
        these two worlds, and the tier comes off the world rather than being written in."""
        for cell in dangers(enemy="walker"):
            pack = cell.with_(enemy="pack")
            one = estimate.fight_cost(cell.here, cell.rows(), cell.sword, cell.armour)
            many = estimate.fight_cost(pack.here, pack.rows(), pack.sword, pack.armour)
            with self.subTest(cell=repr(cell)):
                self.assertEqual((many[0] > one[0], many[1] > one[1]), (True, True), f"{many} vs {one}")

    def test_armour_buys_health_and_not_speed(self):
        for cell in dangers(enemy="archer", distance="across", armour="skin"):
            seconds, blood = zip(*(estimate.fight_cost(world.here, world.rows(), world.sword,
                                                       world.armour) for world in cell.along("armour")))
            self.assertEqual(len(set(round(t, 6) for t in seconds)), 1, f"{cell}: armour changed the time")
            self.assertEqual(list(blood), sorted(blood, reverse=True), f"{cell}: {blood}")

    def test_walking_to_it_is_part_of_it(self):
        for cell in dangers(enemy="walker", distance="touching"):
            seen = [estimate.fight_cost(world.here, world.rows(), world.sword, world.armour)[0]
                    for world in cell.along("distance")]
            self.assertEqual(seen, sorted(seen), f"{cell}: {seen}")


class WalkingAway(unittest.TestCase):
    def test_it_costs_less_than_standing_in_it(self):
        """Pressure falls as the distance opens — that is what leaving IS. The property is the inequality, not the
        factor: charge the full rate for the whole walk and running looks as lethal as fighting."""
        for press in RATES:
            for seconds in SECONDS:
                if press == 0.0 or seconds == 0.0:
                    continue
                with self.subTest(press=press, seconds=seconds):
                    got = estimate.leaving_hp(press, seconds)
                    self.assertEqual(0.0 < got < press * seconds, True, got)

    def test_it_grows_along_both_ladders(self):
        for seconds in SECONDS[1:]:
            seen = [estimate.leaving_hp(press, seconds) for press in RATES]
            self.assertEqual(seen, sorted(seen), f"{seconds}s: {seen}")
        for press in RATES[1:]:
            seen = [estimate.leaving_hp(press, seconds) for seconds in SECONDS]
            self.assertEqual(seen, sorted(seen), f"{press}hp/s: {seen}")

    def test_nothing_to_walk_out_of_costs_nothing(self):
        for seconds in SECONDS:
            self.assertEqual(estimate.leaving_hp(0.0, seconds), 0.0)
        for press in RATES:
            self.assertEqual(estimate.leaving_hp(press, 0.0), 0.0)

    def test_the_spot_it_walks_to_is_away_from_them(self):
        for cell in dangers(distance="near", ground="open"):
            rows = cell.rows()
            if not rows:
                continue
            spot = threat.escape_spot(cell.here, rows)
            before = estimate.pressure_hp_s(cell.here, rows, 0.0)
            after = estimate.pressure_hp_s(spot, rows, 0.0)
            with self.subTest(cell=repr(cell)):
                self.assertEqual(max(after - before, 0.0) <= 1e-9, True, f"walked into it: {after} > {before}")


class EveryColumnIsPricedThroughIt(unittest.TestCase):
    """`threat.options` builds the columns; each one's `cost_s` is this quantity and nothing else. A column that
    priced itself some other way is how `saves` came to be four times the planner's own score."""

    def test_every_option_costs_its_seconds_plus_its_priced_health(self):
        for cell in dangers():
            price = cell.price()
            for option in threat.options(cell.threat_state()):
                self.assertAlmostEqual(threat.action_cost(option, price),
                                       estimate.act_cost_s(option.seconds, option.hp, price),
                                       places=6, msg=f"{cell}: {option.kind}")

    def test_carrying_on_spends_nothing(self):
        """`ignore` is a state, not an act: while its damage was also a cost, every other column looked four times
        better than it was and the planner still held `ignore`."""
        for cell in dangers():
            doing_nothing = next(o for o in threat.options(cell.threat_state()) if o.kind == "ignore")
            self.assertEqual((doing_nothing.hp, doing_nothing.seconds), (0.0, 0.0), cell)


if __name__ == "__main__":
    unittest.main()
