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
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

from bonobo import api, hazard, perception, retry, skills, upkeep  # noqa: E402
from bonobo import brain as brainmod  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
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


# (situation, /state changes, head buried?, what the rescue does, [rounds]) → per round: (used the round?, rescue
# run?, /stop posted?). Rounds run back to back through the real Brain.attempt / Brain.ready (the failure policy).
OK, FAILS, STOPPED = "ok", api.NavFailed("no path out of the lava"), api.Interrupted("perception: a new hazard")
RESCUES = [
    ("in lava: rescued, under survival mode", {"inLava": True}, False, OK, [(True, "lava", False)]),
    ("buried: rescued", {}, True, OK, [(True, "suffocating", False)]),
    ("drowning between tasks: rescued", {"inWater": True, "onGround": False, "air": 140}, False, OK,
     [(True, "drowning", False)]),
    ("burning: no rescue of our own, the round is not used", {"onFire": True, "health": 5.0}, False, OK,
     [(False, None, False)]),
    ("nothing wrong", {}, False, OK, [(False, None, False)]),
    ("the rescue fails: /stop, and the cause cools here — the next round does not try again",
     {"inLava": True}, False, FAILS, [(True, "lava", True), (False, None, False)]),
    ("the rescue is interrupted: no /stop, no cooling, tried again next round",
     {"inLava": True}, False, STOPPED, [(True, "lava", False), (True, "lava", False)]),
]


class Rescue(unittest.TestCase):
    def test_rounds(self):
        for name, changes, buried, does, rounds in RESCUES:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.mem, b.retry, b.place = Memory(tmp + "/notes.json"), retry.Retry(), ("here", False)
                b.table, b.last_failure = upkeep.Upkeep(b), None
                ran, modes, posted = [], [], []

                def rescue(ctx, st, _k):
                    ran.append(_k)
                    modes.append(api.MODE)
                    if does is not OK:
                        raise does
                table = {k: (lambda ctx, st, _k=k: rescue(ctx, st, _k)) for k in hazard.RESCUE}
                with mock.patch.dict(hazard.RESCUE, table), \
                        mock.patch.object(skills, "head_buried", return_value=buried), \
                        mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append(path)), \
                        mock.patch.object(api, "api", side_effect=AssertionError("L0 read the world")):
                    for used, kind, stopped in rounds:
                        ran.clear(), posted.clear()
                        api.INTERRUPT, api.MODE = "perception: danger", "normal"
                        self.assertEqual(hazard.handle(None, state(**changes), b.attempt, b.ready), used)
                        self.assertEqual(ran[0] if ran else None, kind)
                        self.assertEqual("/stop" in posted, stopped)
                        self.assertEqual(api.MODE, "normal", "survival mode ends with the rescue")
                        if kind:
                            self.assertEqual(modes[-1], "survival", "the rescue runs protected from its own trigger")
                            self.assertIsNone(api.INTERRUPT, "the interrupt it answers is consumed")
                api.INTERRUPT, api.MODE = None, "normal"


# Hostiles go to the fight (fight_loop), never to L0. What each answer sends, recorded rather than sent: the batch of
# one decision. (kind, target, the calls it makes: api.run task types / skill names)
Decision = __import__("collections").namedtuple("Decision", "kind target")
ANSWERS = [
    ("fight", 42, [("run", "attack")]),
    ("evade", (10, 64, 0), [("go_to", (10, 64, 0))]),
    ("wall_in", None, [("skill", "pod")]),
    ("eat", None, [("skill", "eat")]),
    ("shield", None, [("skill", "shield_to_offhand"), ("run", "use_item")]),
    ("reshape", ("under", 2), [("run", "pillar"), ("run", "pillar")]),
    ("reshape", ("down", 2), [("mine_cell", (0, 63, 0)), ("mine_cell", (0, 62, 0))]),
    ("ignore", None, []),
    ("evade", "nowhere", api.NotAvailable),            # the walk got nowhere: the answer failed, and says so
]


class FightHandOff(unittest.TestCase):
    def test_answer_to_calls(self):
        from bonobo import fight_loop
        from tests.world import bag, inventory
        for kind, target, want in ANSWERS:
            calls = []
            with self.subTest(kind=kind, target=target), \
                    mock.patch.object(api, "run", side_effect=lambda t, wait=0: calls.append(("run", t["type"])) or
                                      {"status": "succeeded"}), \
                    mock.patch.object(fight_loop.nav, "go_to", side_effect=lambda pos, *a, **k: calls.append(
                        ("go_to", pos)) or pos != "nowhere"), \
                    mock.patch.object(fight_loop, "mine_cell", side_effect=lambda pol, cell, **k: calls.append(
                        ("mine_cell", cell))), \
                    mock.patch.object(fight_loop, "Inventory", lambda: bag(inventory(cobblestone=8))), \
                    mock.patch.object(skills, "pod", side_effect=lambda ctx: calls.append(("skill", "pod"))), \
                    mock.patch.object(skills, "eat", side_effect=lambda **k: calls.append(("skill", "eat"))), \
                    mock.patch.object(skills, "shield_to_offhand",
                                      side_effect=lambda: calls.append(("skill", "shield_to_offhand"))), \
                    mock.patch.object(api, "api", side_effect=AssertionError("the answer read the world")):
                ctx = type("Ctx", (), {"policy": None})()
                if isinstance(want, type):
                    with self.assertRaises(want):
                        fight_loop.engage(Decision(kind, target), state(x=0.5, y=64.0, z=0.5), ctx)
                    continue
                fight_loop.engage(Decision(kind, target), state(x=0.5, y=64.0, z=0.5), ctx)
                self.assertEqual(calls, want)

    # (the answer raises?, taken by the arbiter?) → (taken, failure recorded)
    OFFERS = [(None, True, (True, {})), (api.NavFailed("cornered"), True, (True, {"failed": "NavFailed: cornered"})),
              (None, False, (False, {})),
              (api.NavFailed("cornered"), False, (False, {}))]     # refused: the answer never ran, nothing failed

    def test_offer_takes_the_body_at_tactic(self):
        from bonobo import fight_loop
        for raises, taken, (want_taken, want_failure) in self.OFFERS:
            seen = {}

            def preempt(layer, run, key, **kw):
                seen.update(layer=layer, clear_first=kw.get("clear_first"))
                if taken:
                    try:
                        run()
                    except Exception:
                        pass
                return taken, None if taken else "held by a faster layer"

            def answer(option):
                if raises:
                    raise raises
            with self.subTest(raises=raises, taken=taken), \
                    mock.patch.object(fight_loop.arbiter.BODY, "preempt", side_effect=preempt), \
                    mock.patch.object(fight_loop, "ANSWER", answer):
                got_taken, refused, failure = fight_loop.offer("opt", 10.0, "k", 0.0, lambda: True, None, 0.0)
                self.assertEqual((got_taken, failure), (want_taken, want_failure))
                self.assertEqual(seen, {"layer": "tactic", "clear_first": True})
                self.assertEqual(refused is None, taken)

    def test_offer_over_the_real_arbiter(self):
        """No stub: a fresh arbiter takes the body at TACTIC, stops what runs, runs the answer on this thread."""
        from bonobo import arbiter, fight_loop
        ran, posted = [], []
        for raises in (None, api.NavFailed("cornered")):
            ran.clear(), posted.clear()

            def answer(option):
                ran.append(option)
                if raises:
                    raise raises
            with self.subTest(raises=raises), mock.patch.object(fight_loop.arbiter, "BODY", arbiter.Motion()), \
                    mock.patch.object(fight_loop, "ANSWER", answer), \
                    mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append(path) or {}):
                if raises:
                    with self.assertRaises(type(raises)):
                        fight_loop.offer("opt", 10.0, "k", 0.0, lambda: True, None, 0.0)
                else:
                    taken, refused, failure = fight_loop.offer("opt", 10.0, "k", 0.0, lambda: True, None, 0.0)
                    self.assertEqual((taken, refused, failure), (("tactic", "k"), None, {}))
                self.assertEqual(ran, ["opt"])
                self.assertIn("/stop", posted, "what was running is stopped first")
                api.INTERRUPT = None

    def test_wired_once_wire_is_called(self):
        from bonobo import fight_loop
        with mock.patch.object(fight_loop, "ANSWER", None):
            self.assertFalse(fight_loop.wired())
            fight_loop.wire(None, lambda snap: None, {})
            self.assertTrue(fight_loop.wired())


if __name__ == "__main__":
    unittest.main()
