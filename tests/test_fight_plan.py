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



# ------------------------------------------------------------------------------ the dragon on the answer loop
from bonobo import fight_loop  # noqa: E402

DRAGON = {"id": 7, "type": "minecraft:ender_dragon", "x": 0.0, "y": 66.0, "z": 3.0, "health": 150.0, "phase": 5}
CRYSTAL_NEAR = {"id": 11, "type": "minecraft:end_crystal", "x": 20.5, "y": 80.0, "z": 0.5}
CRYSTAL_FAR = {"id": 12, "type": "minecraft:end_crystal", "x": 40.5, "y": 90.0, "z": 0.5}
BOMB = ((2, 65, 0), "minecraft:red_bed", (3, 64, 0), (5, 62, 0), False)


def view(**changes):
    v = {"dead": False, "dragon": DRAGON, "crystals": [], "here": (10.0, 64.0, 0.0), "bed": None, "bed_cell": None,
         "bomb": None, "reinforce": [], "escape": None, "cover": None}
    v.update(changes)
    return v


A = fight_loop.Answer
# (situation, the phase model's intent, the round's view) → the answer the loop posts (None: the fight stops)
DRAGON_ANSWERS = [
    ("perched, a window, a bed and the pit: the bomb", {"intent": "fire_window", "deadline_s": 3.0},
     view(bed="minecraft:red_bed", bed_cell=(2, 65, 0), bomb=BOMB), A("bed_bomb", BOMB)),
    ("perched, a window, no bed: melee on the dragon", {"intent": "fire_window", "deadline_s": 3.0}, view(),
     A("fight", 7)),
    ("flying, open crystals: shoot the first in order", {"intent": "shoot_crystal", "deadline_s": 5.0},
     view(crystals=[CRYSTAL_NEAR, CRYSTAL_FAR]), A("shoot", CRYSTAL_NEAR)),
    ("breath on us: away from the cloud before the cover", {"intent": "retreat"},
     view(escape=(20, 64, 0), cover=(5, 62, 0)), A("evade", (20, 64, 0))),
    ("retreat, no breath: into the cover", {"intent": "retreat"}, view(cover=(5, 62, 0)), A("evade", (5, 62, 0))),
    ("a window with no time left: retreat, not a bomb caught in the open", {"intent": "fire_window", "deadline_s": 0.0},
     view(bomb=BOMB, cover=(5, 62, 0)), A("evade", (5, 62, 0))),
    ("shoot asked, no open crystal left: retreat (nothing to shoot)", {"intent": "shoot_crystal"}, view(),
     A("evade", (10.0, 64.0, 0.0))),
    ("the pit wanted: the dig skill, whole", {"intent": "dig_tunnel", "deadline_s": 20.0}, view(),
     A("prep", "dig_tunnel")),
    ("the dragon dead: the fight stops", {"intent": "fire_window"}, view(dead=True), None)]


class DragonAnswer(unittest.TestCase):
    def test_intent_to_answer(self):
        for name, intent, v, want in DRAGON_ANSWERS:
            with self.subTest(name):
                self.assertEqual(fight_loop.dragon_answer(intent, v), want)

    def test_every_answer_has_a_batch(self):
        """Each answer kind the dragon uses is posted as a batch (or is a prep skill): none is empty from here."""
        from bonobo import brain  # noqa: F401  (lends "shoot")
        from tests.world import bag, inventory
        st = {"state": {"x": 10.0, "y": 64.0, "z": 0.0}, "feet": (10, 64, 0),
              "inv": bag(inventory(("red_bed", 2), ("bow", 1), ("arrow", 16), ("cobblestone", 32)))}
        want = {"bed_bomb": ["travel", "bed_bomb", "travel"], "fight": ["attack"], "shoot": ["use_item"],
                "evade": ["travel"]}
        for name, intent, v, answer in DRAGON_ANSWERS:
            if answer is None or answer.kind == "prep":
                continue
            with self.subTest(name):
                self.assertEqual([t["type"] for t in fight_loop.batch(answer, st)], want[answer.kind])


class Carry(unittest.TestCase):
    """fight_loop.carry, the one answer loop: the same answer keeps its task, a new one /stops and posts."""

    # (situation, the answers wanted pass by pass, the task statuses read, again) → the calls made, in order
    ROWS = [("the same answer twice: posted once, then watched", [A("fight", 7), A("fight", 7)], ["running"], False,
             ["post fight", "watch"]),
            ("a new answer: stop, then post it", [A("fight", 7), A("shoot", "c")], [], False,
             ["post fight", "/stop", "post shoot"]),
            ("a retreat re-aimed 2 blocks off: the same walk", [A("evade", (5, 62, 0)), A("evade", (6, 63, 1))],
             ["running"], False, ["post evade", "watch"]),
            ("the task ended, the answer still wanted, again: posted again",
             [A("shoot", "c"), A("shoot", "c"), A("shoot", "c")], ["succeeded"], True,
             ["post shoot", "watch", "post shoot"]),
            ("an attack ended, the skeleton alive, again: attacked again",
             [A("fight", 7), A("fight", 7), A("fight", 7)], ["failed"], True, ["post fight", "watch", "post fight"]),
            ("nothing left to do: the loop ends at once", [None], [], False, [])]

    def test_passes(self):
        from unittest import mock
        from bonobo import api
        for name, wants, statuses, again, want in self.ROWS:
            calls, wants_, statuses_ = [], list(wants), list(statuses)

            def answer(a):
                calls.append(f"post {a.kind}")
                return {"id": len(calls)}

            def get(path):
                calls.append("watch")
                return {"status": statuses_.pop(0) if statuses_ else "running"}
            with self.subTest(name), mock.patch.object(api, "post", side_effect=lambda p, b=None: calls.append(p)), \
                    mock.patch.object(api, "get", side_effect=get), mock.patch.object(fight_loop.time, "sleep"):
                held = {"done": None, "task_id": None}
                list(fight_loop.carry(lambda: wants_.pop(0) if wants_ else None, answer, lambda: True, held,
                                      again=again))
                self.assertEqual(calls, want)

if __name__ == "__main__":
    unittest.main()
