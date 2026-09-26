"""What the swept fight table cannot reach: the shape of a state, the clock, and the declarations.

Pricing and vetoing — which action wins, what it saves, what is refused in which phase, with how much health and
how little time — are swept over the whole state space in test_state_price and test_saved, and the
deleted with it. What is left is what a sweep of well-formed cells never sees: malformed states, a phase clock read wrong,
actions declared in data rather than in branches, and a pincer, which is geometry rather than a cell.
"""
import unittest

from bonobo import fight_plan as fp

CLOUD = "minecraft:area_effect_cloud"
ENDERMAN = "minecraft:enderman"


def state(*, phase=6, elapsed=0.0, hp=20.0, pos=(8.0, 65.0, 0.0), boss_hp=200.0, threats=(),
          beds=6, obsidian=0, water=True, bow=0, arrows=0,
          tunnel=True, bed_placed=True, reinforced=False, crystals=0, in_cover=True, cover=(8, 65, 0)):
    """A fight state with sane defaults: tunnel dug, in cover, healthy, mid sitting phase."""
    return fp.fight_state(
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


def _hp(s, v):
    s["self"]["hp"] = v


def _clock(s, v):
    s["boss"]["phase_elapsed_s"] = v


def _no_boss_hp(s):
    del s["boss"]["hp"]


def _short_row(s):
    s["threats"].append(((0, 0, 0), 1.0))


class StateShape(unittest.TestCase):
    # (dotted path) → what lookup reads from state(beds=3, one enderman, one cloud)
    LOOKUP = [("resources.beds", 3), (f"threats.{ENDERMAN}", 1), (f"threats.{CLOUD}", 1), ("threats.minecraft:zombie", 0),
              ("terrain.tunnel_ready", 1), ("resources.nothing", 0), ("self.hp", 20.0)]

    def test_lookup_over_the_table(self):
        s = state(beds=3, threats=[threat(ENDERMAN, 5, 0), threat(CLOUD, 4, 4)])
        self.assertEqual(set(s), {"self", "boss", "threats", "resources", "terrain"})
        for path, want in self.LOOKUP:
            with self.subTest(path):
                self.assertEqual(fp.lookup(s, path), want)

    # (what is wrong with the state) → the problems validate names, exactly
    VALIDATE = [("nothing", [], []),
                ("health above the maximum", [lambda s: _hp(s, 99.0)], ["self.hp"]),
                ("negative health", [lambda s: _hp(s, -1.0)], ["self.hp"]),
                ("a clock read in epoch seconds", [lambda s: _clock(s, 1.8e9)], ["boss.phase_elapsed_s"]),
                ("no boss health", [_no_boss_hp], ["boss.hp"]),
                ("a threat row short of fields", [_short_row], ["threats[0]"]),
                ("everything at once", [lambda s: _hp(s, 99.0), lambda s: _clock(s, 1.8e9), _no_boss_hp, _short_row],
                 ["boss.hp", "self.hp", "boss.phase_elapsed_s", "threats[0]"])]

    def test_validate_over_the_table(self):
        for name, breaks, want in self.VALIDATE:
            with self.subTest(name):
                s = state()
                for b in breaks:
                    b(s)
                self.assertEqual(sorted(k for k, _ in fp.validate_state(s)), sorted(want))

    def test_effects_are_pure(self):
        for name in ("dig_tunnel", "place_bed", "reinforce"):
            with self.subTest(name):
                s = state(tunnel=False, in_cover=False, bed_placed=False)
                before = repr(s)
                F.action(name).effect(s)
                self.assertEqual(repr(s), before, "the original must not change")
        self.assertTrue(F.action("dig_tunnel").effect(state(tunnel=False))["terrain"]["tunnel_ready"])


class TimeModel(unittest.TestCase):
    # (phase, seconds into it, quantile) → seconds left: conditional on having lasted this long, pessimistic at p10
    REMAINING = [(6, 0.0, 0.5, 4.95), (6, 4.0, 0.5, 0.95), (6, 10.0, 0.5, 0.0), (6, 0.0, 0.1, 4.95),
                 (0, 0.0, 0.5, 15.3), (0, 0.0, 0.1, 2.0), (4, 0.0, 0.5, 0.85)]

    def test_remaining_over_the_table(self):
        for phase, elapsed, q, want in self.REMAINING:
            with self.subTest(phase=phase, elapsed=elapsed, q=q):
                self.assertEqual(fp.remaining(phase, elapsed, q=q), want)


class ActionsAreData(unittest.TestCase):
    """Resource and terrain checks come from declarations on the action, not branches in the planner."""

    # (state, action) → (admissible, why)
    ADMIT = [("a healthy sitting phase", {}, "fire_window", (True, "")),
             ("no beds", {"beds": 0}, "fire_window", (False, "needs resources.beds ≥ 1, have 0")),
             ("no tunnel", {"tunnel": False}, "fire_window", (False, "needs terrain.tunnel_ready = True")),
             ("the wrong phase", {"phase": 0}, "fire_window", (False, "wrong phase (0)")),
             ("dead", {"hp": 0.0}, "fire_window", (False, "no health: nothing is admissible until alive again")),
             ("a water bucket with no enderman", {"phase": 0}, "water_bucket",
              (False, f"needs threats.{ENDERMAN} ≥ 1, have 0")),
             ("a water bucket against an enderman", {"phase": 0, "threats": [threat(ENDERMAN, 30, 0)]}, "water_bucket",
              (True, ""))]

    def test_admissible_over_the_table(self):
        for name, kw, action, want in self.ADMIT:
            with self.subTest(name):
                self.assertEqual(F.admissible(state(**kw), F.action(action)), want)

    def test_the_declared_actions_are_per_fight_with_exactly_one_default(self):
        a, b = fp.Fight(), fp.Fight()
        self.assertEqual([x.name for x in a.actions],
                         ["dig_tunnel", "place_bed", "reinforce", "shoot_crystal", "water_bucket", "fire_window",
                          "retreat"])
        self.assertEqual([x.name for x in a.actions if x.default], ["retreat"])
        self.assertEqual([x is y for x, y in zip(a.actions, b.actions)], [False] * len(a.actions))


class Veto(unittest.TestCase):


    # Clouds closing on place_bed's spot (it commits 0.5 s + 1.0 s back). The veto asks the field — earliest
    # arrival over the union — so an open flank stays open and a closed ring is refused, which a per-threat or
    # distance test cannot see. (clouds as (x, z, velocity)) → (admissible, why)
    PINCER = [("no clouds", [], (True, "")),
              ("one closing from the west", [(-4.0, 0, (4.0, 0, 0))], (True, "")),
              ("two on one axis: a sidestep across it escapes", [(-4.0, 0, (4.0, 0, 0)), (4.0, 0, (-4.0, 0, 0))],
               (True, "")),
              ("three sides", [(-4.0, 0, (4.0, 0, 0)), (4.0, 0, (-4.0, 0, 0)), (0, -4.0, (0, 0, 4.0))],
               (False, "committing 1.5s, first threat arrives in 1.5s")),
              ("the ring closed", [(-4.0, 0, (4.0, 0, 0)), (4.0, 0, (-4.0, 0, 0)), (0, -4.0, (0, 0, 4.0)),
                                   (0, 4.0, (0, 0, -4.0))], (False, "committing 1.5s, first threat arrives in 1.4s")),
              ("a still ring twenty out", [(-20, 0, (0, 0, 0)), (20, 0, (0, 0, 0)), (0, -20, (0, 0, 0)),
                                           (0, 20, (0, 0, 0))], (True, ""))]

    def test_the_pincer_over_the_table(self):
        here = (0.0, 65.0, 0.0)
        for name, clouds, want in self.PINCER:
            with self.subTest(name):
                # bed_placed=False, or place_bed's own `needs` refuses before the field is ever consulted.
                s = state(phase=0, pos=here, tunnel=False, in_cover=False, cover=None, bed_placed=False,
                          threats=[threat(CLOUD, x, z, r=3.0, vel=v) for x, z, v in clouds])
                self.assertEqual(F.admissible(s, F.action("place_bed")), want)


class Faults(unittest.TestCase):
    # (state) → (intent, fault kinds); assumptions are always declared and never a fault
    PLANS = [("healthy", {}, ("fire_window", [])),
             ("a clock read in epoch seconds", {"elapsed": 1.8e9}, ("retreat", ["boss.phase_elapsed_s", "no action"])),
             ("everything refused: the default, and said so",
              {"phase": 4, "elapsed": 0.8, "beds": 0, "tunnel": False, "crystals": 0, "obsidian": 0},
              ("retreat", ["no action"])),
             ("dead: nothing but the default", {"hp": 0.0}, ("retreat", ["no action"]))]

    def test_plan_over_the_table(self):
        for name, kw, (intent, faults) in self.PLANS:
            with self.subTest(name):
                p = F.plan(state(**kw))
                self.assertEqual((p["intent"], [k for k, _ in p["fault"]]), (intent, faults))
                self.assertEqual(sorted(p["assumptions"]), sorted(fp.UNMEASURED))


if __name__ == "__main__":
    unittest.main()
