"""Offline tests for the fight planner: structured states in, intents out. No game, milliseconds.

Each test names a way a fight has actually been lost — starting what could not finish, walking into the open with
no cover to return to, standing still because everything was refused, ranking on a number nobody measured — and
asserts the planner refuses or reports it, for the stated reason.
"""
import unittest

from bonobo import fight_plan as fp

CLOUD = "minecraft:area_effect_cloud"
ENDERMAN = "minecraft:enderman"


def state(*, phase=6, elapsed=0.0, hp=20.0, pos=(8.0, 65.0, 0.0), boss_hp=200.0, threats=(),
          beds=6, obsidian=0, water=True, bow=0, arrows=0,
          tunnel=True, bed_placed=True, reinforced=False, crystals=0, in_cover=True, cover=(8, 65, 0)):
    """A fight state with sane defaults: tunnel dug, in cover, healthy, mid sitting phase."""
    return fp.make_state(
        self_={"pos": pos, "hp": hp, "in_cover": in_cover, "cover": cover},
        boss={"phase": phase, "phase_elapsed_s": elapsed, "hp": boss_hp},
        threats=threats,
        resources={"beds": beds, "obsidian": obsidian, "water": water, "bow": bow, "arrows": arrows},
        terrain={"tunnel_ready": tunnel, "bed_placed": bed_placed, "reinforced": reinforced,
                 "crystals_open": crystals},
    )


def threat(kind, x, z, r=3.0, vel=(0.0, 0.0, 0.0)):
    return ((x, 65.0, z), r, vel, kind)


F = fp.Fight()


class StateShape(unittest.TestCase):
    def test_five_sections_and_nothing_else(self):
        self.assertEqual(set(state()), {"self", "boss", "threats", "resources", "terrain"})

    def test_lookup_reads_dotted_paths_and_counts_threats(self):
        s = state(beds=3, threats=[threat(ENDERMAN, 5, 0), threat(CLOUD, 4, 4)])
        self.assertEqual(fp.lookup(s, "resources.beds"), 3)
        self.assertEqual(fp.lookup(s, f"threats.{ENDERMAN}"), 1)
        self.assertEqual(fp.lookup(s, "terrain.tunnel_ready"), 1)
        self.assertEqual(fp.lookup(s, "resources.nothing"), 0)

    def test_validate_names_every_problem(self):
        bad = state()
        bad["self"]["hp"] = 99.0
        bad["boss"]["phase_elapsed_s"] = 1.8e9
        del bad["boss"]["hp"]
        bad["threats"].append(((0, 0, 0), 1.0))
        where = dict(fp.validate_state(bad))
        for key in ("self.hp", "boss.phase_elapsed_s", "boss.hp", "threats[0]"):
            self.assertIn(key, where)

    def test_a_healthy_state_validates(self):
        self.assertEqual(fp.validate_state(state()), [])

    def test_effects_are_pure(self):
        s = state(tunnel=False, in_cover=False)
        after = F.action("dig_tunnel").effect(s)
        self.assertFalse(s["terrain"]["tunnel_ready"], "the original must not change")
        self.assertTrue(after["terrain"]["tunnel_ready"])


class TimeModel(unittest.TestCase):
    def test_remaining_is_conditional_not_marginal(self):
        self.assertGreater(fp.remaining(6, 0.0), fp.remaining(6, 4.0))
        self.assertLess(fp.remaining(6, 4.0), 1.5)

    def test_expired_phase_has_nothing_left(self):
        self.assertEqual(fp.remaining(6, 10.0), 0.0)

    def test_p10_is_pessimistic_against_the_median(self):
        self.assertLessEqual(fp.remaining(0, 0.0, q=0.1), fp.remaining(0, 0.0, q=0.5))


class ActionsAreData(unittest.TestCase):
    """Resource and terrain checks come from declarations on the action, not branches in the planner."""

    def test_requires_is_checked_from_the_declaration(self):
        ok, why = F.admissible(state(beds=0), F.action("fire_window"))
        self.assertFalse(ok)
        self.assertIn("resources.beds", why)

    def test_needs_is_checked_from_the_declaration(self):
        ok, why = F.admissible(state(tunnel=False), F.action("fire_window"))
        self.assertFalse(ok)
        self.assertIn("terrain.tunnel_ready", why)

    def test_threat_counts_can_be_required(self):
        # water_bucket only makes sense against an enderman; the requirement is a threat count, and it is data.
        ok, why = F.admissible(state(phase=0), F.action("water_bucket"))
        self.assertFalse(ok)
        self.assertIn(f"threats.{ENDERMAN}", why)
        ok, _ = F.admissible(state(phase=0, threats=[threat(ENDERMAN, 30, 0)]), F.action("water_bucket"))
        self.assertTrue(ok)

    def test_no_planner_branch_names_an_action(self):
        import inspect
        src = inspect.getsource(fp.Fight.admissible)
        self.assertNotIn('action.name ==', src, "resource checks must be declarations, not name branches")

    def test_actions_are_per_fight(self):
        a, b = fp.Fight(), fp.Fight()
        self.assertIsNot(a.actions, b.actions)
        self.assertIsNot(a.action("dig_tunnel"), b.action("dig_tunnel"))

    def test_exactly_one_default(self):
        self.assertEqual([a.name for a in F.actions if a.default], ["retreat"])


class Veto(unittest.TestCase):
    def test_atomic_work_must_fit_the_phase(self):
        ok, why = F.admissible(state(elapsed=4.9), F.action("fire_window"))
        self.assertFalse(ok)
        self.assertIn("s left", why)

    def test_interruptible_work_is_judged_by_its_segment(self):
        # Digging takes 6 s and the holding pattern's p10 is 2.0 s; judged whole it would starve forever.
        s = state(phase=0, tunnel=False, in_cover=False, pos=(20.0, 64.0, 0.0))
        self.assertEqual(fp.commitment(F.action("dig_tunnel")), 0.8)
        ok, why = F.admissible(s, F.action("dig_tunnel"))
        self.assertTrue(ok, why)

    def test_phase_restricted_actions(self):
        ok, why = F.admissible(state(phase=6, bow=1, arrows=8, crystals=2), F.action("shoot_crystal"))
        self.assertFalse(ok)
        self.assertIn("phase", why)

    def test_damage_rule_only_fails_on_damage(self):
        # Hurt, nothing hitting us, no cover: digging cover must still be allowed. A version compared 0 against a
        # negative allowance and refused it — a hurt agent could not build the cover that would stop the hurt.
        s = state(phase=0, hp=8.0, tunnel=False, in_cover=False, pos=(20.0, 64.0, 0.0))
        ok, why = F.admissible(s, F.action("dig_tunnel"))
        self.assertTrue(ok, why)

    def test_damage_rule_fails_when_it_would_kill(self):
        s = state(phase=0, hp=8.0, tunnel=False, in_cover=False, pos=(0.0, 65.0, 0.0),
                  threats=[threat(CLOUD, 0, 0, r=6.0)])       # standing in breath
        ok, why = F.admissible(s, F.action("dig_tunnel"))
        self.assertFalse(ok)

    def test_dead_admits_nothing_but_says_why(self):
        ok, why = F.admissible(state(hp=0.0), F.action("dig_tunnel"))
        self.assertFalse(ok)
        self.assertIn("health", why)

    def test_retreat_is_never_vetoed(self):
        for s in (state(), state(hp=1.0, tunnel=False, beds=0, phase=0), state(phase=4, elapsed=0.8)):
            self.assertTrue(F.admissible(s, F.default)[0], "the default must always survive the veto")

    def test_the_pincer_is_refused(self):
        # Two clouds closing from either side: each alone leaves an escape, together they do not. The veto asks
        # the field (earliest arrival over the union), so it sees what a per-threat or distance test cannot.
        here = (0.0, 65.0, 0.0)
        # Two clouds converging along x are escapable by a step across in z, and the model correctly says so.
        # Closing the pincer needs the perpendicular covered too: four from the cardinal directions.
        axis = [threat(CLOUD, -4.0, 0, r=3.0, vel=(4.0, 0, 0)), threat(CLOUD, 4.0, 0, r=3.0, vel=(-4.0, 0, 0))]
        ring = axis + [threat(CLOUD, 0, -4.0, r=3.0, vel=(0, 0, 4.0)), threat(CLOUD, 0, 4.0, r=3.0, vel=(0, 0, -4.0))]
        # bed_placed=False, or place_bed's own `needs` refuses before the field is ever consulted.
        s_axis = state(phase=0, pos=here, tunnel=False, in_cover=False, threats=axis, cover=None, bed_placed=False)
        s_ring = state(phase=0, pos=here, tunnel=False, in_cover=False, threats=ring, cover=None, bed_placed=False)
        # place_bed commits 0.5 s + 1.0 s back.
        self.assertTrue(F.admissible(s_axis, F.action("place_bed"))[0], "a sidestep escapes two on one axis")
        ok, why = F.admissible(s_ring, F.action("place_bed"))
        self.assertFalse(ok, "converging from every side must veto what an open flank allows")
        self.assertIn("first threat arrives", why)


class Objective(unittest.TestCase):
    """cost = work / rate + Σ exposure + p(death) × death_cost — and every benefit is a difference of it."""

    def test_a_hurt_dragon_is_closer_to_done(self):
        self.assertLess(F.objective(state(boss_hp=40.0)), F.objective(state(boss_hp=200.0)))

    def test_cover_is_worth_seconds(self):
        self.assertLess(F.objective(state(tunnel=True, in_cover=True)),
                        F.objective(state(tunnel=False, in_cover=False)))

    def test_a_dead_dragon_costs_nothing(self):
        self.assertEqual(F.objective(state(boss_hp=0.0)), 0.0)

    def test_benefit_is_the_effect_on_the_objective(self):
        s = state(tunnel=False, in_cover=False, phase=0)
        after = F.action("dig_tunnel").effect(s)
        self.assertAlmostEqual(F.benefit(s, F.action("dig_tunnel")), F.objective(s) - F.objective(after), places=1)

    def test_the_tunnel_stops_paying_on_the_last_window(self):
        # No rule says so — with one window left there is nothing to amortise the dig over, and the seconds say it.
        many = F.benefit(state(phase=0, boss_hp=200.0, tunnel=False, in_cover=False), F.action("dig_tunnel"))
        few = F.benefit(state(phase=0, boss_hp=30.0, tunnel=False, in_cover=False), F.action("dig_tunnel"))
        self.assertGreater(many, few)

    def test_being_hit_now_is_a_cost(self):
        calm = state(pos=(30.0, 65.0, 0.0), threats=[])
        hit = state(pos=(0.0, 65.0, 0.0), threats=[threat(CLOUD, 0, 0, r=6.0)])
        self.assertGreater(F.objective(hit), F.objective(calm))

    def test_shaking_an_enderman_is_worth_the_damage_it_stops(self):
        s = state(phase=0, pos=(0.0, 65.0, 0.0), threats=[threat(ENDERMAN, 1.0, 0)])
        self.assertGreater(F.benefit(s, F.action("water_bucket")), 0.0)

    def test_a_standing_crystal_is_work(self):
        self.assertGreater(F.objective(state(crystals=3)), F.objective(state(crystals=0)))


class Planning(unittest.TestCase):
    def test_sitting_phase_fires(self):
        self.assertEqual(F.plan(state())["intent"], "fire_window")

    def test_late_in_the_window_it_refuses_to_start(self):
        p = F.plan(state(elapsed=4.8))
        self.assertNotEqual(p["intent"], "fire_window")
        self.assertIn("fire_window", dict(p["rejected"]))

    def test_circling_with_nothing_built_digs(self):
        p = F.plan(state(phase=0, tunnel=False, bed_placed=False, in_cover=False, pos=(20.0, 64.0, 0.0)))
        self.assertEqual(p["intent"], "dig_tunnel", p["rejected"])

    def test_zero_benefit_never_beats_retreating(self):
        p = F.plan(state(phase=6, tunnel=False, in_cover=False, beds=0))
        self.assertEqual(p["intent"], "retreat")

    def test_always_answers_something(self):
        p = F.plan(state(phase=4, elapsed=0.5, hp=3.0, beds=0, tunnel=False))
        self.assertEqual(p["intent"], "retreat")

    def test_deadline_matches_the_intent(self):
        p = F.plan(state(elapsed=1.0))
        self.assertGreaterEqual(p["deadline_s"], 0.0)
        self.assertEqual(p["duration_s"], F.action(p["intent"]).duration_s)

    def test_rejections_carry_reasons(self):
        for _, why in F.plan(state(beds=0, tunnel=False))["rejected"]:
            self.assertTrue(why)


class Faults(unittest.TestCase):
    def test_a_healthy_state_has_no_fault(self):
        self.assertEqual(F.plan(state())["fault"], [])

    def test_a_malformed_clock_is_a_fault(self):
        self.assertIn("boss.phase_elapsed_s", dict(F.plan(state(elapsed=1.8e9))["fault"]))

    def test_refusing_everything_is_a_fault(self):
        p = F.plan(state(phase=4, elapsed=0.8, beds=0, tunnel=False, crystals=0, obsidian=0))
        self.assertEqual(p["intent"], "retreat")
        self.assertIn("no action", dict(p["fault"]))

    def test_assumptions_are_separate_from_faults(self):
        p = F.plan(state())
        self.assertTrue(p["assumptions"], "the guessed parameters must be declared")
        self.assertEqual(p["fault"], [], "and must not masquerade as a fault in every plan")


if __name__ == "__main__":
    unittest.main()
