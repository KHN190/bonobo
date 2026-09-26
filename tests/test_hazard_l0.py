"""Test point B — L0, offline: what the environment does to the body, judged from `/state` readings.

  HAZARDS   a /state (plus the two readings the caller makes: head in a block, blocks fallen) → hazard.kind, and
            whether the brain must rescue before anything else this round (hazard.due)
  DANGERS   the same readings with hostiles about → perception.danger: environment first, hostiles never reach L0
  FALLS     a sequence of /state readings → how far `Watch` says we have fallen (the one stateful reading)

The rescues themselves (water_clutch, cross_lava_8, cave_escape, lava edge, buried by sand) are in-game rows of the
scenario sheet; here only the judgment that triggers them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import hazard, perception  # noqa: E402
from tests.world import state  # noqa: E402

W = hazard._W
DROWN_AIR = int((W["surface_s"] + W["reaction_s"]) * hazard.TICKS_PER_S)     # the clock's own zero, in ticks

# (situation, /state changes, buried, fallen, kind, due) — `due` is what the brain answers at the top of a round.
HAZARDS = [
    ("standing on stone, all well", {}, False, 0.0, None, None),
    ("in lava", {"inLava": True, "onFire": True, "health": 18.0}, False, 0.0, "lava", "lava"),
    ("lava edge: burning, health still high", {"onFire": True, "health": 18.0}, False, 0.0, None, None),
    ("burning and hurt", {"onFire": True, "health": 6.0}, False, 0.0, "burning", None),
    ("swimming, lungs full", {"inWater": True, "onGround": False, "air": 300}, False, 0.0, None, None),
    ("under water, air at the clock's zero", {"inWater": True, "onGround": False, "air": DROWN_AIR},
     False, 0.0, "drowning", "drowning"),
    ("under water, a little slack left (brain rescues between tasks)",
     {"inWater": True, "onGround": False, "air": DROWN_AIR + 30}, False, 0.0, None, "drowning"),
    ("under water, below the hard floor", {"inWater": True, "onGround": False, "air": W["air_floor"] - 1},
     False, 0.0, "drowning", "drowning"),
    ("buried by sand", {}, True, 0.0, "suffocating", "suffocating"),
    ("buried and standing in water", {"inWater": True, "air": 300}, True, 0.0, "suffocating", "suffocating"),
    ("falling off a cliff edge", {"onGround": False}, False, hazard.FALL_BLOCKS + 6, "falling", None),
    ("a hop is not a fall", {"onGround": False}, False, hazard.FALL_BLOCKS - 1, None, None),
    ("falling into water is a landing", {"onGround": False, "inWater": True}, False, 30.0, None, None),
    ("lava beats everything else on the body", {"inLava": True, "onFire": True, "health": 3.0}, True, 20.0,
     "lava", "lava"),
    ("nether, on netherrack", {"dimension": "minecraft:the_nether", "skyLight": 0}, False, 0.0, None, None),
    ("night, rain: weather is not a hazard", {"timeOfDay": 18000}, False, 0.0, None, None),
]

# Hostile situations: the environment is fine, something is coming at us. L0 must not see any of them.
HOSTILE = [
    ("critical health", {"health": 3.0}, {}, "critical_health"),
    ("in the End, hurt", {"health": 11.0, "dimension": "minecraft:the_end"}, {}, "critical_health"),
    ("dragon breath close", {"dimension": "minecraft:the_end"}, {"breath_within": lambda r: True}, "breath"),
    ("an enderman after us", {}, {"enderman_after_us": lambda r: True}, "enderman"),
    ("hurt, a zombie at 3", {"health": 9.0}, {"hostiles_within": lambda r: 3.0}, "hostiles"),
    ("hurt, a zombie at 3, but we are the ones attacking",
     {"health": 9.0, "control": {"active": True, "paused": False, "allowed": True, "task": {"type": "attack"},
                                 "queued": 0}}, {"hostiles_within": lambda r: 3.0}, None),
    ("healthy, a zombie at 3", {"health": 20.0}, {"hostiles_within": lambda r: 3.0}, None),
    ("dead: nothing to interrupt for", {"dead": True, "health": 0.0}, {}, None),
    ("the player holds control", {"health": 3.0, "control": {"paused": True}}, {}, None),
]


class Hazards(unittest.TestCase):
    def test_state_to_kind_and_due(self):
        for name, changes, buried, fallen, kind, due in HAZARDS:
            s = state(**changes)
            with self.subTest(name):
                self.assertEqual(hazard.kind(s, buried=buried, fallen=fallen), kind)
                self.assertEqual(hazard.due(s, buried=buried), due)
                if kind is not None:
                    self.assertIn(kind, hazard.KINDS)
                # What perception interrupts for is the same judgment, environment first.
                self.assertEqual(perception.danger(s, buried=buried, fallen=fallen), kind)

    def test_due_only_names_what_has_a_rescue(self):
        for name, changes, buried, fallen, _kind, due in HAZARDS:
            with self.subTest(name):
                self.assertTrue(due is None or due in hazard.RESCUE)

    def test_less_air_is_never_less_danger(self):
        ladder = [hazard.drowning_in(state(inWater=True, air=a)) for a in range(300, -1, -20)]
        self.assertEqual(ladder, sorted(ladder, reverse=True))
        self.assertEqual(hazard.drowning_in(state(air=0)), float("inf"), "dry land has no clock")


class HostilesAreNotL0(unittest.TestCase):
    def test_the_two_families_do_not_overlap(self):
        self.assertFalse(set(hazard.KINDS) & set(perception.HOSTILE))
        self.assertEqual(set(perception.DANGERS), set(hazard.KINDS) | set(perception.HOSTILE))

    def test_hostile_situations_reach_the_fight_not_the_rescue(self):
        for name, changes, callbacks, want in HOSTILE:
            s = state(**changes)
            with self.subTest(name):
                self.assertIsNone(hazard.kind(s), "a mob is not an environmental hazard")
                self.assertIsNone(hazard.due(s, buried=False))
                got = perception.danger(s, **callbacks)
                self.assertEqual(got, want)
                self.assertTrue(got is None or got in perception.HOSTILE)


# (situation, [(y, onGround, inWater, inLava)], fallen after each reading)
FALLS = [
    ("walk off a 20-block cliff", [(84, True, False, False), (84, False, False, False), (80, False, False, False),
                                   (70, False, False, False), (64, True, False, False)], [0, 0, 4, 14, 0]),
    ("jump: up before down", [(64, False, False, False), (65.2, False, False, False), (64, False, False, False)],
     [0, 0, 1.2]),
    ("fall into water resets", [(90, False, False, False), (70, False, False, False), (60, False, True, False)],
     [0, 20, 0]),
    ("fall into lava resets (lava is its own hazard)", [(90, False, False, False), (75, False, False, True)], [0, 0]),
]


class Falls(unittest.TestCase):
    def test_fallen_over_a_reading_sequence(self):
        for name, readings, want in FALLS:
            watch = hazard.Watch()
            with self.subTest(name):
                got = [round(watch.fallen(state(y=float(y), onGround=g, inWater=w, inLava=lv)), 2)
                       for y, g, w, lv in readings]
                self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()
