"""Test point A — the skill contract's judgment layer, offline.

Pure functions fed readings (tests/world.py), one table per rule, every row a subTest:

  SETTLE     a reading sequence after an action (counts, `dead` flags) × interrupts → the value judged, or Interrupted
  ARRIVE     what `nav.go_to` answered, leg by leg (True / Walked / False) × interrupts → arrived, NavFailed, Interrupted
  OUTCOMES   an exception → its cause and its class (failure / interrupted / waits); every exception type the
             package defines must have a row (a new one cannot go unclassified)
  COMMANDS   a state dict → the batch an open-loop skill would post; every skill that declares `commands` must have
             rows here (a new open-loop skill cannot go untested), blueprints swept over REGISTRY × turns × progress

Nothing here talks to the game. `settle` and `arrive` see a sequence of answers the game gave; that is replay of the
judgment, not a simulation of the world. The in-game half of test point A is the scenario sheet (bonobo/scenarios.py).
"""
import itertools
import time
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, arbiter, blueprints, brain, nav, retry, skillcore, skills, tape  # noqa: E402,F401  (brain: every skill module)
from bonobo import skill as skillkit  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402
from bonobo.knowledge import members  # noqa: E402
from tests.world import FakeRegion, bag, flat, inventory, state  # noqa: E402

FAST = dict(timeout=3.0, stable_s=0.5, poll=0.25)      # the real rule, on a recorded clock (Clock)


class Clock:
    """Game-free time for `settle(clock=, sleep=)`: it moves only when settle sleeps, so a reading sequence is
    judged the same way every run."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Clean(unittest.TestCase):
    def setUp(self):
        self._saved = (api.INTERRUPT, api.MODE, api.SOFT)
        api.INTERRUPT, api.MODE, api.SOFT = None, "normal", False
        tape._readings.clear()

    def tearDown(self):
        api.INTERRUPT, api.MODE, api.SOFT = self._saved
        tape._readings.clear()


def reader(seq, interrupt_at=None, message="perception: lava"):
    """`read()` over recorded readings: the i-th call answers seq[i] (the last one repeats). `interrupt_at` = the read
    after which perception's interrupt is pending (a list for several)."""
    at = set([interrupt_at] if isinstance(interrupt_at, int) else interrupt_at or ())
    i = {"n": 0}

    def read():
        n = i["n"]
        i["n"] += 1
        if n in at:
            api.INTERRUPT = message
        return seq[min(n, len(seq) - 1)]
    read.calls = i
    return read


# ------------------------------------------------------------------------------------------------------- settle
# (name, readings, ok, settle kwargs, expected value | Interrupted). Counts are bag counts before/after a pickup,
# a craft, a furnace slot; booleans are `/state.dead` as the game reported it frame by frame.
BEFORE = 3
GAINED = lambda v: v > BEFORE          # noqa: E731
SETTLE = [
    ("pickup lands on the first read", [4], GAINED, {}, 4),
    ("pickup lag: two reads before the drop is in the bag", [3, 3, 4], GAINED, {}, 4),
    ("stack tops up late, twice", [3, 3, 3, 4, 5], GAINED, {}, 5),
    ("a count that bounces is not a gain", [3, 4, 3], GAINED, {}, 3),
    ("nothing ever arrives: the last reading at timeout", [3], GAINED, {}, 3),
    ("the bag was full: a gain that never shows", [3, 3, 3, 3], GAINED, {}, 3),
    ("lost: stored or loaded, and it held", [5, 5, 2], lambda v: v < 5, {}, 2),
    ("dead for a frame while the chunk loads", [True, False], bool, {}, False),
    ("respawn reports dead for two frames, then alive", [True, True, False], bool, {}, False),
    ("dead for as long as the hold: confirmed", [True, True, True, False], bool, {}, True),
    ("dead and staying dead", [True], bool, {}, True),
    ("interrupted mid-settle", [3, 3, 3, 3], GAINED, {"interrupt_at": 1}, api.Interrupted),
    ("interrupted at the very reading that succeeds, with a hold to wait out", [3, 4], GAINED,
     {"interrupt_at": 1}, api.Interrupted),
    ("interrupted at the reading that succeeds, nothing to hold: the success stands", [3, 4], GAINED,
     {"interrupt_at": 1, "stable_s": 0}, 4),
    ("soft skill: the interrupt is left for the skill to read", [3, 3, 4], GAINED,
     {"interrupt_at": 0, "soft": True}, 4),
    ("rescue under way (MODE survival): perception's message does not stop it", [3, 4], GAINED,
     {"interrupt_at": 0, "mode": "survival"}, 4),
]


class Settle(_Clean):
    def test_reading_sequences(self):
        for name, seq, ok, kw, want in SETTLE:
            with self.subTest(name):
                kw = dict(kw)
                api.INTERRUPT, api.MODE = None, kw.pop("mode", "normal")
                read = reader(seq, kw.pop("interrupt_at", None))
                c = Clock()
                args = dict(FAST, clock=c.now, sleep=c.sleep, **kw)
                if isinstance(want, type) and issubclass(want, BaseException):
                    with self.assertRaises(want):
                        skillcore.settle(read, ok, **args)
                    self.assertIsNone(api.INTERRUPT, "the interrupt is consumed by whoever it stopped")
                    continue
                self.assertEqual(skillcore.settle(read, ok, **args), want)
                # The tape keeps what the verdict was made from: those readings, in order, and whether it held.
                rec = tape._readings[-1]
                self.assertEqual([v for _, v in rec["seq"]], [seq[min(i, len(seq) - 1)] for i in range(len(rec["seq"]))])
                self.assertEqual(rec["verdict"], bool(ok(want)))
                api.MODE = "normal"

    def test_two_interrupts_in_a_row_stop_two_waits(self):
        for _ in range(2):
            with self.subTest(attempt=_), self.assertRaises(api.Interrupted):
                c = Clock()
                skillcore.settle(reader([3, 3], interrupt_at=0), GAINED, clock=c.now, sleep=c.sleep, **FAST)

    def test_judgments_built_on_settle(self):
        """gained / lost / dead: the same wait, each with its own verdict. (dead: one alive reading is never a death,
        and asks nothing more of the world.)"""
        rows = [("gained", lambda: skillcore.gained(reader([3, 5]), BEFORE, timeout=0.2, stable_s=0.01), 5),
                ("lost", lambda: skillcore.lost(reader([3, 1]), BEFORE, timeout=0.2, stable_s=0.01), 1),
                ("dead: alive reading", lambda: skillcore.dead(state(dead=False)), False),
                ("dead: no dead field at all", lambda: skillcore.dead({k: v for k, v in state().items() if k != "dead"}),
                 False)]
        with mock.patch.object(api, "api", side_effect=AssertionError("judged an alive reading by asking again")):
            for name, call, want in rows[2:]:
                with self.subTest(name):
                    self.assertEqual(call(), want)
        for name, call, want in rows[:2]:
            with self.subTest(name):
                self.assertEqual(call(), want)


# (situation, [(seconds, reading)], confirmed?) — a death (or any flag) is judged by readings that agree for a moment.
T = lambda dead: dict(state(), dead=dead)         # noqa: E731
CONFIRMED = [
    ("no readings", [], False),
    ("one reading is never enough", [(0.0, True)], False),
    ("three frames over half a second", [(0.0, True), (0.25, True), (0.5, True)], True),
    ("a gap in the middle", [(0.0, True), (0.25, False), (0.5, True)], False),
    ("the first reading says alive", [(0.0, False), (0.5, True), (1.0, True)], False),
    ("states from /state, dead throughout", [(0.0, T(True)), (0.3, T(True)), (0.6, T(True))], True),
    ("states from /state, a respawn frame", [(0.0, T(True)), (0.3, T(False)), (0.6, T(True))], False),
    ("a /state without the field", [(0.0, state()), (0.6, state())], False),
]


class Confirmed(unittest.TestCase):
    def test_reading_sequences(self):
        with mock.patch.object(api, "api", side_effect=AssertionError("the pure judgment asked the world")):
            for name, readings, want in CONFIRMED:
                with self.subTest(name):
                    self.assertEqual(skillcore.confirmed(readings), want)
                    self.assertEqual(skillcore.dead(readings=readings), want)


# ------------------------------------------------------------------------------------------------------- arrive
W = nav.Walked
ARRIVE = [
    ("there on the first leg", [True], {}, True),
    ("three legs, each nearer, then there", [W(30), W(12), True], {}, True),
    ("no nearer at all", [False], {}, api.NavFailed),
    ("nearer, then stuck", [W(8), False], {}, api.NavFailed),
    ("start cell not standable ('1 positions explored' answers False)", [False], {}, api.NavFailed),
    ("gains every leg but never arrives", [W(1)] * nav.ARRIVE_CALLS, {}, api.NavFailed),
    ("interrupted between legs", [W(10), True], {"interrupt_at": 0}, api.Interrupted),
    ("soft skill walks on through perception's message", [W(10), True], {"interrupt_at": 0, "soft": True}, True),
]


# (situation, body x y z, target, range) → there: the one arrival test, read from where the walk left the body
THERE = [("on the platform, the target's cell", (3.5, 201.0, 3.5), (3, 201, 3), 0.6, True),
         ("cave_escape: travel stopped a step below the target (y 199.3 vs 201)", (3.5, 199.3, 3.5), (3, 201, 3), 0.6,
          False),
         ("one cell diagonally off at range 0.6 (the old range + 1 accepted it)", (4.5, 201.0, 4.5), (3, 201, 3), 0.6,
          False),
         ("one cell off at range 1.5", (4.5, 64.0, 3.5), (3, 64, 3), 1.5, True),
         ("face to face with a block, at the far edge of the cell (skills.BESIDE)", (4.95, 64.0, 3.05), (3, 64, 3),
          skills.BESIDE, True),
         ("two cells off at BESIDE: not beside", (5.5, 64.0, 3.5), (3, 64, 3), skills.BESIDE, False),
         ("a point target (an entity's position), exactly there", (10.2, 64.0, -3.7), (10.2, 64.0, -3.7), 1.0, True),
         ("a point target 2 blocks away at range 1", (12.2, 64.0, -3.7), (10.2, 64.0, -3.7), 1.0, False)]
GROUND = {"onGround": True, "inWater": False, "climbing": False}
# (situation, what holds the body up, feet y) → there, target (3, 200, 3) at range 0.4 (its cell only)
AT_REST = [("in the target cell, on the ground", GROUND, 200.0, True),
           ("in the target cell mid-jump (y 200.18, off the ground)", {**GROUND, "onGround": False}, 200.18, False),
           ("in the target cell, swimming", {**GROUND, "onGround": False, "inWater": True}, 200.3, True),
           ("in the target cell, on a ladder", {**GROUND, "onGround": False, "climbing": True}, 200.5, True),
           ("one cell below, on the ground", GROUND, 199.0, False),
           ("cave_escape: travel said arrived, body on the step one below at y 199.25", GROUND, 199.25, False),
           ("travel succeeded but the body is mid-air in the target cell", {**GROUND, "onGround": False}, 200.4,
            False)]


def xyz(*ps):
    return [{"x": x, "y": y, "z": z} for x, y, z in ps]


WALL = {(1, 64, 0), (1, 65, 0)}                       # our own wall on the straight line to the ore at (3, 64, 0)
BOXED = {(3 + dx, 64 + dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)} - {(3, 64, 0)}
# (situation, task, protected cells) → the "avoid" the jar gets (None: the task goes as it was)
AVOID = [("our wall on the straight line to the ore: it goes with the mine, the dig goes round it",
          {"type": "mine", "x": 3, "y": 64, "z": 0}, WALL, xyz((1, 64, 0), (1, 65, 0))),
         ("nothing protected: an empty list, the dig goes straight", {"type": "mine", "x": 3, "y": 64, "z": 0},
          set(), []),
         ("the ore boxed in by our build: every cell listed, the jar's travel has no route and says so",
          {"type": "mine", "x": 3, "y": 64, "z": 0}, BOXED, xyz(*sorted(BOXED))),
         ("a build 100 blocks off: not carried", {"type": "place", "x": 3, "y": 64, "z": 0, "item": "stone"},
          {(103, 64, 0)}, []),
         ("mine_many: near any of its blocks", {"type": "mine_many", "blocks": xyz((50, 64, 0), (3, 64, 0))},
          {(110, 64, 0), (-70, 64, 0)}, xyz((110, 64, 0))),
         ("a walk-only goto: no digging, nothing added", {"type": "goto", "x": 3, "y": 64, "z": 0}, WALL, None),
         ("a task that names its own avoid keeps it", {"type": "use", "x": 3, "y": 64, "z": 0, "avoid": []}, WALL,
          None)]


class Arrive(_Clean):
    def test_what_a_walk_may_do(self):
        """nav.may_alter: every walk may dig into a hill or bridge a ditch (priced by the pathfinder); only a walk
        with a known far side bridges out over the void; the round's policy caps them all."""
        P = nav.Policy
        rows = [("to work, the round allows digging: all three", "work", P(allow_dig=True), (True, True, True)),
                ("to work, no digging this round", "work", P(allow_dig=False), (False, True, True)),
                ("exploring: dig and bridge, never over the void", "explore", P(allow_dig=True), (True, True, False)),
                ("evading: dig into the hill, bridge the ditch, never over the void", "evade", P(allow_dig=True),
                 (True, True, False)),
                ("evading with building off this round", "evade", P(allow_dig=True, allow_build=False),
                 (True, False, False))]
        for name, purpose, policy, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.may_alter(purpose, policy), want)
        with self.assertRaises(KeyError):
            nav.may_alter("wander", P())

    def test_evade_avoids_our_builds(self):
        from bonobo import fight_loop
        from bonobo.threat import Option
        from tests.world import bag, inventory
        st = {"feet": (0, 64, 0), "inv": bag(inventory()), "protected": {(3, 64, 0)}}
        got = fight_loop.batch(Option("evade", (10, 64, 0), 0.0, 0.0, ""), st)
        self.assertEqual(got[0]["avoid"], [{"x": 3, "y": 64, "z": 0}])

    def test_avoid_over_the_table(self):
        for name, task, protected, want in AVOID:
            with self.subTest(name):
                got = nav.with_avoid(task, protected)
                self.assertEqual(got, task if want is None else {**task, "avoid": want})

    # (situation, the task a skill runs) → what api.run posts (brain sets api.DRESS each round)
    DRESSED = [("a mine behind our wall gets the avoid list", {"type": "mine", "x": 3, "y": 64, "z": 0},
                {"type": "mine", "x": 3, "y": 64, "z": 0, "avoid": xyz((1, 64, 0), (1, 65, 0))}),
               ("a place near it too", {"type": "place", "x": 2, "y": 64, "z": 0, "item": "minecraft:stone"},
                {"type": "place", "x": 2, "y": 64, "z": 0, "item": "minecraft:stone",
                 "avoid": xyz((1, 64, 0), (1, 65, 0))}),
               ("a look never approaches: posted as it was", {"type": "look", "x": 3, "y": 64, "z": 0},
                {"type": "look", "x": 3, "y": 64, "z": 0}),
               ("far from the wall: an empty avoid list", {"type": "mine", "x": 500, "y": 64, "z": 0},
                {"type": "mine", "x": 500, "y": 64, "z": 0, "avoid": []})]

    def test_every_post_is_dressed(self):
        for name, task, want in self.DRESSED:
            with self.subTest(name):
                posted = []
                with mock.patch.object(api, "DRESS", lambda t: nav.with_avoid(t, WALL)), \
                        mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append(body) or
                                          {"status": "succeeded", "type": task["type"], "message": "", "seconds": 0}):
                    api.run(dict(task))
                self.assertEqual(posted, [want])

    def test_there_over_the_table(self):
        for name, (x, y, z), pos, range_, want in THERE:
            with self.subTest(name):
                self.assertIs(nav.there({"x": x, "y": y, "z": z, **GROUND}, pos, range_), want)

    def test_there_only_at_rest(self):
        for name, held, y, want in AT_REST:
            with self.subTest(name):
                self.assertIs(nav.there({"x": 3.5, "y": y, "z": 3.5, **held}, (3, 200, 3), 0.4), want)
                self.assertIs(nav.there({"x": 3.5, "y": y, "z": 3.5, **held}, (3, 200, 3), 0.6), want,
                              "range 0.6 (the scenario's) accepts no step below either")

    def test_a_walked_leg_is_not_arrival(self):
        """A leg that gained ground reads False as an answer to "there?" and True to `moved`: no caller can take
        it for arrival by its truth value."""
        for gained in (100.0, 30.0, 3.0, 0.5, nav.PROGRESS_BLOCKS):
            with self.subTest(gained=gained):
                self.assertEqual((bool(W(gained)), nav.moved(W(gained))), (False, True))

    def test_what_counts_as_moving(self):
        """`nav.moved`: there, or a leg that gained ground — never a bare truth value of the answer."""
        for got, want in ((True, True), (W(3.0), True), (W(0.5), True), (False, False), (None, False), (1, False)):
            with self.subTest(got=got):
                self.assertIs(nav.moved(got), want)

    def run_legs(self, fn, legs, interrupt_at=None, soft=False):
        answers = reader(legs, interrupt_at)
        api.SOFT = soft
        with mock.patch.object(nav, "go_to", side_effect=lambda *a, **k: answers()), \
                mock.patch.object(nav, "feet_now", return_value=(0, 64, 0)):
            return fn((40, 64, 0), None, range_=2)

    def test_leg_sequences(self):
        for name, legs, kw, want in ARRIVE:
            with self.subTest(name, call="arrive"):
                api.INTERRUPT = None
                if isinstance(want, type):
                    with self.assertRaises(want):
                        self.run_legs(nav.arrive, legs, **kw)
                else:
                    self.assertIs(self.run_legs(nav.arrive, legs, **kw), want)
            with self.subTest(name, call="arrived"):
                api.INTERRUPT = None
                if want is api.Interrupted:
                    with self.assertRaises(api.Interrupted):       # not getting there is False; being stopped is not
                        self.run_legs(nav.arrived, legs, **kw)
                else:
                    self.assertIs(self.run_legs(nav.arrived, legs, **kw), want is True)


# ----------------------------------------------------------------------------------------------------- outcomes
# exception → (cause, class). "interrupted" never counts, bans, stops or cools; "repair" (the world changed under the
# plan: a station gone) stops the step and repairs the plan, never counted or cooled; "waits" is handled before the
# failure path (brain.attempt); "failure" is counted for (task, cause) and cools the cause at the place.
OUTCOMES = [
    (api.Interrupted("perception: lava"), "interrupt", "interrupted"),
    (api.BodyContested("another commander posted a task"), "interrupt", "interrupted"),
    (api.FightHolds("our fight holds the body"), "interrupt", "interrupted"),
    (api.PlayerTookControl(), "interrupt", "interrupted"),
    (api.CommitmentExpired("a faster layer took the body"), "replan", "interrupted"),
    (api.GameUnreachable("game not reachable (connection refused)"), "game", "waits"),
    (skillcore.ToolMissing("pickaxe", 1), "tool", "failure"),
    (skillcore.StationMissing("minecraft:crafting_table"), "replan", "repair"),
    (api.NavFailed("could not get to (1, 2, 3)"), "nav", "failure"),
    (api.Unreachable("the item landed where nothing can stand"), "nav", "failure"),
    (api.McError("travel: no path found"), "nav", "failure"),
    (api.McError("goto failed: 1 positions explored"), "nav", "failure"),
    (NotAvailable("cannot reach the tree"), "nav", "failure"),
    (NotAvailable("no sheep within 48 blocks"), "unavailable", "failure"),
    (api.TaskStuck("chop: no progress toward its goal for 45s"), "stuck", "failure"),
    (api.McError("chop: finished without reaching its goal"), "error", "failure"),
    (api.McError("could not open the chest"), "error", "failure"),
]


def exception_types():
    """Every exception class the package's contract layer defines — a new one needs a row above."""
    found = set()
    for mod in (api, skillcore, skillkit, nav, retry):
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, BaseException) and obj.__module__ == mod.__name__:
                found.add(obj)
    return found


class NothingQueued(unittest.TestCase):
    """api.refused: a post that queued nothing while a fight holds the body is an interruption (no count, no cooling),
    not "unavailable" cooled 180 s — resume_after_combat never went back to its chop."""

    def test_over_the_table(self):
        rows = [("the arbiter refused the post (a fight owns the body)",
                 {"status": "failed", "message": "body owned by the arbiter", "tasks": []}, False, False, "interrupt"),
                ("nothing queued, a fight engaged", {"tasks": []}, False, True, "interrupt"),
                ("nothing queued, nobody holds the body: the world declined", {"tasks": []}, False, False,
                 "unavailable"),
                ("something queued: no answer needed", {"tasks": [{"id": 1}]}, True, True, None)]
        for name, r, queued, engaged, want in rows:
            with self.subTest(name):
                body = arbiter.Motion()
                body.engaged = engaged
                with mock.patch.object(arbiter, "BODY", body):
                    try:
                        api.refused(r, queued)
                        got = None
                    except api.McError as e:
                        got = retry.cause_of(e)
                self.assertEqual(got, want)


class AWalkCutShortIsNotAFailure(unittest.TestCase):
    """nav.arrived under a fight holding the body raises the interruption instead of answering False: the caller's
    "no way there" branch (skills.mine bans the vein) is for failures only."""

    def test_over_the_table(self):
        rows = [("a fight engaged, the walk not its intent", True, api.FightHolds)]
        for name, engaged, want in rows:
            with self.subTest(name):
                body = arbiter.Motion()
                body.engaged = engaged
                with mock.patch.object(arbiter, "BODY", body):
                    with self.assertRaises(want) as got:
                        nav.arrived((5, 64, 5), nav.Policy(), range_=1.5)
                self.assertEqual(retry.cause_of(got.exception), "interrupt")

    def test_free_body_is_asked_normally(self):
        """Must-fail counterpart: with the body free, go_to goes on to read the world (here: the fake api refuses)."""
        body = arbiter.Motion()
        with mock.patch.object(arbiter, "BODY", body), \
                mock.patch.object(api, "api", side_effect=api.GameUnreachable("offline")):
            with self.assertRaises(api.GameUnreachable):
                nav.arrived((5, 64, 5), nav.Policy(), range_=1.5)


class DigInSeals(unittest.TestCase):
    """skills.dig_in_commands: the hole is always lidded — with a carried block, else with what the dig brings up
    (an empty bag dug a lidless hole: night_dig_in_dirt)."""

    def test_over_the_table(self):
        feet = (0, 64, 0)

        def ground(top, rest):
            return FakeRegion((-2, 55, -2), (2, 67, 2), {**{(x, y, z): rest for x in range(-2, 3) for z in range(-2, 3)
                                                              for y in range(56, 63)},
                                                           **{(x, 63, z): top for x in range(-2, 3) for z in range(-2, 3)}})
        full = inventory(*[("minecraft:rotten_flesh", 64)] * 36)
        rows = [("an empty bag, dirt: the dug dirt is the lid, the dig collects it", ground("dirt", "dirt"), inventory(),
                 "minecraft:dirt", True),
                ("an empty bag, grass on top: the lid is dirt (what grass drops)", ground("grass_block", "dirt"),
                 inventory(), "minecraft:dirt", True),
                ("cobblestone carried: the carried block, nothing collected for it", ground("dirt", "dirt"),
                 inventory(("cobblestone", 8)), "minecraft:cobblestone", False),
                ("must fail: a full bag, nothing to seal with", ground("dirt", "dirt"), full, NotAvailable, None)]
        for name, region, inv, want, collects in rows:
            with self.subTest(name):
                st = {"feet": feet, "region": region, "inv": bag(inv), "protected": set()}
                if want is NotAvailable:
                    with self.assertRaises(NotAvailable):
                        skills.dig_in_commands(st)
                    continue
                tasks = skills.dig_in_commands(st)
                self.assertEqual((tasks[-1]["type"], tasks[-1]["item"]), ("place", want))
                self.assertEqual({t["collect"] for t in tasks if t["type"] == "mine"}, {collects})


class WaitOutFight(unittest.TestCase):
    """brain.wait_out_fight: back as soon as our fight lets the body go, polled — never the 10 s stand-down."""

    def test_over_the_table(self):
        from bonobo import fight_loop
        rows = [("no fight: no wait", [None], 0.0),
                ("a fight for two polls: two polls", ["zombie", "zombie", None], 2 * brain.FIGHT_POLL_S),
                ("a fight that never ends: capped", ["zombie"] * 1000, brain.FIGHT_WAIT_MAX_S),
                ("one poll", ["zombie", None], brain.FIGHT_POLL_S)]
        for name, engaged, want in rows:
            with self.subTest(name):
                clock, seq = [0.0], iter(engaged + [None] * 1000)
                sleep = lambda s: clock.__setitem__(0, clock[0] + s)     # noqa: E731
                with mock.patch.object(fight_loop, "engaged", side_effect=lambda: next(seq)), \
                        mock.patch.object(arbiter, "BODY", arbiter.Motion()):
                    got = brain.wait_out_fight(sleep=sleep, now=lambda: clock[0])
                self.assertAlmostEqual(got, want)


class Outcomes(unittest.TestCase):
    def test_every_exception_maps_to_one_cause_and_one_class(self):
        for err, cause, cls in OUTCOMES:
            with self.subTest(f"{type(err).__name__}: {err}"):
                self.assertEqual(retry.cause_of(err), cause)
                self.assertEqual(api.interrupted(err), cls == "interrupted")
                self.assertEqual(cause in retry.NOT_FAILURES, cls in ("interrupted", "repair"))
                verdict = retry.Retry().failed("task t1", cause, str(err), now=100.0, place=((0, 4, 0), False))
                self.assertEqual(verdict is None, cls in ("interrupted", "repair"), "only failures are counted")

    def test_no_exception_type_is_unclassified(self):
        covered = {type(err) for err, _, _ in OUTCOMES}
        self.assertEqual(exception_types() - covered, set(),
                         "a new exception type: add its row to OUTCOMES (cause, class)")

    def test_the_interruptions_are_exactly_the_interrupted_rows(self):
        self.assertEqual(set(api.INTERRUPTIONS), {type(e) for e, _, c in OUTCOMES if c == "interrupted"})

    def test_outcome_of(self):
        """What the attempt does about each: interruptions never fail (and some need a hand back or a wait)."""
        special = {api.PlayerTookControl: ("interrupted", "handback"), api.GameUnreachable: ("interrupted", "wait_game"),
                   api.BodyContested: ("interrupted", "stand_down"), api.FightHolds: ("interrupted", "fight")}
        rows = [(err, special.get(type(err), ("interrupted", None) if cls == "interrupted" else ("failed", "stop")))
                for err, _cause, cls in OUTCOMES]
        rows += [(None, ("ok", None)), (ValueError("a bug of ours"), ("failed", "crash"))]
        for err, want in rows:
            with self.subTest(repr(err)):
                self.assertEqual(brain.outcome_of(err), want)


# ------------------------------------------------------------------------------------------ verify needs a product
class Readings:
    """What the world reads as, before and after a skill: the bag, where the feet are, the /state, what is found,
    whether the body is walled in, the free spots around. Patched where each module reads them (readings, not a
    game); the skill's own `start` and `verify` run unchanged."""

    MODULES = ("skills", "skillcore", "loot", "explore", "nether", "reflexes", "needs", "fluids", "brewing", "farming", "ui",
               "combat")

    def __init__(self, inv=None, feet=(0, 30, 0), st=None, found=(), enclosed=False, spots=(), items=(),
                 lit=False, cast=None, mobs=()):
        self.inv, self.feet, self.st, self.found = inv or inventory(), feet, st or state(), list(found)
        self.enclosed, self.spots, self.items, self.lit = enclosed, list(spots), list(items), lit
        self.cast = cast or {}          # where the portal was cast (building._CAST); `lit`: a portal block stands there
        self.mobs = list(mobs)          # what /entities answers where a module asks it by name

    def patches(self):
        import importlib
        out = [mock.patch.object(api, "get", side_effect=lambda path: self.st if path == "/state" else
                                 (_ for _ in ()).throw(AssertionError(f"verify read {path}")))]
        values = {"Inventory": lambda data=None: bag(self.inv), "feet": lambda: self.feet,
                  "enclosed": lambda: self.enclosed, "find": lambda *a, **k: list(self.found),
                  "free_spots_here": lambda *a, **k: list(self.spots),
                  "entities": lambda *a, **k: list(self.mobs)}
        for mod in self.MODULES:
            m = importlib.import_module(f"bonobo.{mod}")
            out += [mock.patch.object(m, name, fn) for name, fn in values.items() if hasattr(m, name)]
        from bonobo import building, fluids, world
        out += [mock.patch.object(world, "entities", lambda *a, **k: list(self.items)),
                mock.patch.object(fluids, "portal_lit", lambda origin: self.lit and tuple(origin) == (0, 64, 0)),
                mock.patch.dict(building._CAST, self.cast, clear=True)]
        return out


def _judge(name, args, before, after, result=None, between=None):
    """start under `before`, verify under `after`: did the contract see a product?"""
    c = skillkit.REGISTRY[name]
    call = skillkit.Call(args, {})
    for world in (before, after):          # fixture: the two readings, not a table
        ps = world.patches()
        for p in ps:
            p.start()
        try:
            if world is before:
                call.base = c.start(call) if c.start else None
                if between:
                    between(call)
            else:
                call.result = result
                return bool(c.verify(call))
        finally:
            for p in reversed(ps):
                p.stop()


R = Readings
MACHINE = lambda n: {"name": "m", "pending": [{"item": "minecraft:iron_ingot", "count": n, "ready_at": 0}]}  # noqa: E731
HIT = {"block": "minecraft:nether_bricks", "x": 10, "y": 64, "z": 0, "distance": 10.0}
# (situation, skill, args, world before, world after, the body's result, a change between) → verified?
PRODUCTS = [
    ("strip mine: nothing dug, nowhere gone", "strip_mine_step", (None, 8), R(), R(), None, None, False),
    ("strip mine: went down a level", "strip_mine_step", (None, 8), R(), R(feet=(0, 29, 0)), None, None, True),
    ("strip mine: stone in the bag", "strip_mine_step", (None, 8), R(), R(inv=inventory(cobblestone=5)), None, None,
     True),
    ("eat: the food bar did not move", "eat", (), R(st=state(food=10)), R(st=state(food=10)), True, None, False),
    ("eat: it did", "eat", (), R(st=state(food=10)), R(st=state(food=16)), True, None, True),
    ("eat: nothing eaten, and it said so — still no product", "eat", (), R(st=state(food=10)), R(st=state(food=10)),
     False, None, False),
    ("loot: the bag is as it was", "loot_chest", (None,), R(inv=inventory(dirt=5)), R(inv=inventory(dirt=5)), 0,
     None, False),
    ("loot: more carried", "loot_chest", (None,), R(inv=inventory(dirt=5)), R(inv=inventory(dirt=5, iron_ingot=3)),
     1, None, True),
    ("open space: there, but still boxed in", "move_to_open_space", (None,), R(), R(feet=(3, 30, 0), spots=[(4, 30, 0)]),
     (3, 30, 0), None, False),
    ("open space: there, room around", "move_to_open_space", (None,), R(),
     R(feet=(3, 30, 0), spots=[(4, 30, 0), (5, 30, 0)]), (3, 30, 0), None, True),
    ("dig in: lower but open to the sky", "dig_in", (None,), R(feet=(0, 64, 0)), R(feet=(0, 61, 0)), None, None, False),
    ("dig in: lower and sealed", "dig_in", (None,), R(feet=(0, 64, 0)), R(feet=(0, 61, 0), enclosed=True), None, None,
     True),
    ("dig in: sealed where we stood", "dig_in", (None,), R(feet=(0, 64, 0)), R(feet=(0, 64, 0), enclosed=True), None,
     None, False),
    ("collect machine: nothing taken", "collect_machine", (None, MACHINE(8)), R(), R(), None, None, False),
    ("collect machine: the order collected", "collect_machine", (None, MACHINE(8)), R(), R(), None,
     lambda call: call.args[1].update(pending=[]), True),
    ("fortress: no bricks in sight", "find_fortress", (None,), R(), R(), None, None, False),
    ("fortress: bricks in sight", "find_fortress", (None,), R(), R(found=[HIT]), None, None, True),
    ("search: walked, found nothing", "seek_blocks", (None, ["oak_log"]), R(), R(feet=(60, 30, 0)), None, None, False),
    ("search: found one", "seek_blocks", (None, ["oak_log"]), R(), R(feet=(60, 30, 0)), (60, 30, 2), None, True),
]


def _died_at(pos, recovered):
    """A context whose memory holds a death at `pos` (recovered or not): what recover_items walked back to."""
    import tempfile
    from bonobo.memory import Memory
    m = Memory(os.path.join(tempfile.mkdtemp(prefix="death"), "notes.json"))
    m.log_death(pos, "minecraft:overworld", carried=[("minecraft:diamond", 3)])
    if recovered:
        m.forget_death(pos)
    return type("Ctx", (), {"mem": m})()


DROP = {"id": 9, "type": "minecraft:item", "x": 6, "y": 64, "z": 0, "distance": 1.0}
PRODUCTS += [
    ("recover: the drops still lie there", "recover_items", (_died_at((6, 64, 0), True),), R(), R(items=[DROP]),
     (6, 64, 0), None, False),
    ("recover: the note says done but items remain", "recover_items", (_died_at((6, 64, 0), False),), R(), R(),
     (6, 64, 0), None, False),
    ("recover: picked up, nothing left", "recover_items", (_died_at((6, 64, 0), True),), R(), R(), (6, 64, 0), None,
     True),
    ("cast portal: nothing cast yet", "cast_portal", (), R(), R(lit=True), None, None, False),
    ("cast portal: cast, not lit", "cast_portal", (), R(), R(cast={"origin": (0, 64, 0)}), None, None, False),
    ("cast portal: lit elsewhere is not this frame", "cast_portal", (), R(),
     R(lit=True, cast={"origin": (5, 64, 0)}), None, None, False),
    ("cast portal: cast and lit", "cast_portal", (), R(), R(lit=True, cast={"origin": (0, 64, 0)}), None, None, True),
    ("search for mobs: found none", "explore_for", (None, ["minecraft:sheep"]), R(), R(feet=(80, 30, 0)), None, None,
     False),
    ("search for mobs: found", "explore_for", (None, ["minecraft:sheep"]), R(), R(), (20, 64, 3), None, True),
]


def _stronghold_at(pos, throws):
    """A context whose memory holds the stronghold estimate at `pos` (x, y, z) with the eye throws behind it."""
    import tempfile
    from bonobo.memory import Memory
    m = Memory(os.path.join(tempfile.mkdtemp(prefix="sh"), "notes.json"))
    m.add_site("stronghold", pos, "minecraft:overworld", name="stronghold")
    if throws is not None:
        m.update_site("stronghold", throws=throws)
    return type("Ctx", (), {"mem": m})()


FIRE = {"id": "minecraft:potion", "count": 1, "potion": "minecraft:fire_resistance"}          # fixture: a fire-resistance potion stack
WATER = {"id": "minecraft:potion", "count": 3, "potion": "minecraft:water"}          # fixture: water bottles
PICK = {"id": "minecraft:iron_pickaxe", "count": 1, "damage": 0, "maxDamage": 250}
CALF = {"id": 5, "type": "minecraft:cow", "x": 3, "y": 64, "z": 0, "distance": 3.0, "baby": True}
COW = {"id": 6, "type": "minecraft:cow", "x": 4, "y": 64, "z": 0, "distance": 4.0}
MEET = [[[0, 0], [1, 1]], [[100, 0], [-1, 1]]]          # fixture: two throws meeting at (50, 50)
PRODUCTS += [
    ("brew: only water bottles", "brew_fire_resistance", (None,), R(inv=inventory(WATER)), R(inv=inventory(WATER)),
     None, None, False),
    ("brew: a fire resistance potion", "brew_fire_resistance", (None,), R(inv=inventory(WATER)),
     R(inv=inventory(FIRE, dict(WATER, count=2))), None, None, True),
    ("brew: a potion of something else", "brew_fire_resistance", (None,), R(),
     R(inv=inventory(dict(FIRE, potion="minecraft:swiftness"))), None, None, False),
    ("breed: adults only", "breed", (None,), R(mobs=[COW, COW]), R(mobs=[COW, COW]), None, None, False),
    ("breed: a calf", "breed", (None,), R(mobs=[COW, COW]), R(mobs=[COW, COW, CALF]), None, None, True),
    ("breed: the calf was already there", "breed", (None,), R(mobs=[CALF]), R(mobs=[CALF]), None, None, False),
    ("enchant: the pickaxe as it was", "enchant_item", (None, "minecraft:iron_pickaxe"), R(inv=inventory(PICK)),
     R(inv=inventory(PICK)), None, None, False),
    ("enchant: enchanted", "enchant_item", (None, "minecraft:iron_pickaxe"), R(inv=inventory(PICK)),
     R(inv=inventory(dict(PICK, enchanted=True))), None, None, True),
    ("enchant: something else enchanted", "enchant_item", (None, "minecraft:iron_pickaxe"), R(inv=inventory(PICK)),
     R(inv=inventory(PICK, {"id": "minecraft:book", "count": 1, "enchanted": True})), None, None, False),
    ("stronghold: no estimate", "locate_stronghold", (_stronghold_at((50, 30, 50), MEET),), R(), R(), None, None,
     False),
    ("stronghold: an estimate the throws meet at", "locate_stronghold", (_stronghold_at((50, 30, 50), MEET),), R(),
     R(), (50, 30, 50), None, True),
    ("stronghold: one throw only", "locate_stronghold", (_stronghold_at((50, 30, 50), MEET[:1]),), R(), R(),
     (50, 30, 50), None, False),
    ("stronghold: the throws meet elsewhere", "locate_stronghold", (_stronghold_at((80, 30, 20), MEET),), R(), R(),
     (80, 30, 20), None, False),
]


PRODUCTS += [
    ("blaze rods: none picked up", "collect_blaze_rods", (None, 2), R(), R(), None, None, False),
    ("blaze rods: one of two", "collect_blaze_rods", (None, 2), R(), R(inv=inventory(blaze_rod=1)), None, None, False),
    ("blaze rods: both", "collect_blaze_rods", (None, 2), R(), R(inv=inventory(blaze_rod=2)), None, None, True),
    ("blaze rods: held before do not count", "collect_blaze_rods", (None, 2), R(inv=inventory(blaze_rod=5)),
     R(inv=inventory(blaze_rod=5)), None, None, False),
]


class VerifyNeedsAProduct(unittest.TestCase):
    def test_no_product_no_success(self):
        for name, skill_name, args, before, after, result, between, want in PRODUCTS:
            with self.subTest(name):
                self.assertEqual(_judge(skill_name, args, before, after, result, between), want)


# ------------------------------------------------------------------------------------------------ free spots
def shaft():
    """The body at the bottom of a 1×1 shaft three deep in solid stone."""
    r = world()
    for y in (61, 62, 63):                 # fixture: the shaft's three cells
        r.blocks.pop((0, y, 0), None)
    return r, state(x=0.5, y=61.0, z=0.5)


def lava_floor():
    r = world()
    for (x, y, z), n in list(r.blocks.items()):
        if y == 63 and (x, z) != (0, 0):
            r.blocks[(x, y, z)] = "lava"
    return r


RING2 = {(x, 64, z) for x in range(-2, 3) for z in range(-2, 3) if max(abs(x), abs(z)) == 2}
# (situation, region, /state, keywords) → check(spots); built when asked (the region helpers come later in the file)
def spots_rows():
    return [
            ("open ground: level, two blocks off, open above", world(), state(), {},
         lambda t, sp: (t.assertEqual(len(sp), 5), t.assertTrue(all(p in RING2 for p in sp)))),
            ("never where the body stands", world(), state(), {"reach": 1},
         lambda t, sp: t.assertNotIn((0, 64, 0), sp)),
            ("reach 1: the eight around", world(), state(), {"reach": 1, "limit": 20},
         lambda t, sp: t.assertEqual({p for p in sp if p[1] == 64},
                                     {(x, 64, z) for x in (-1, 0, 1) for z in (-1, 0, 1)} - {(0, 64, 0)})),
            ("cells to avoid are avoided", world(), state(), {"avoid": RING2},
         lambda t, sp: t.assertFalse(set(sp) & RING2)),
            ("a lava floor gives no footing", lava_floor(), state(), {},
         lambda t, sp: t.assertTrue(all(p[1] != 64 for p in sp))),
            ("no floor needed: over nothing is fine", FakeRegion((-5, 55, -5), (5, 70, 5), {}), state(),
         {"block_under": False}, lambda t, sp: t.assertEqual(len(sp), 5)),
            ("sealed in a shaft: nowhere", shaft()[0], shaft()[1], {}, lambda t, sp: t.assertEqual(sp, [])),
            ("never the head's cell either", world(), state(), {"reach": 1, "limit": 30},
         lambda t, sp: t.assertFalse({(0, 64, 0), (0, 65, 0)} & set(sp))),
            ("avoiding all but one: that one", world(), state(), {"reach": 1, "avoid": {(x, 64, z) for x in (-1, 0, 1)
                                                                                   for z in (-1, 0, 1)} - {(1, 64, 1)}},
         lambda t, sp: t.assertEqual(sp[0], (1, 64, 1))),
            ("lava beside the body: that cell's floor is no footing", world(((1, 63, 0), "lava")), state(),
         {"reach": 1, "limit": 30}, lambda t, sp: t.assertNotIn((1, 64, 0), sp)),
            ("far from the origin, negative coordinates", world(), state(x=-7.5, y=64.0, z=-11.5), {},
         lambda t, sp: (t.assertEqual(len(sp), 5),
                        t.assertTrue(all(max(abs(p[0] + 8), abs(p[2] + 12)) == 2 for p in sp)))),
    ]


class FreeSpots(unittest.TestCase):
    def test_region_to_spots(self):
        with mock.patch.object(api, "api", side_effect=AssertionError("free_spots read the world")):
            for name, region, st, kw, check in spots_rows():
                with self.subTest(name):
                    spots = skillcore.free_spots(region, st, **kw)
                    check(self, spots)
                    self.assertEqual(skillcore.free_spot(region, st, **{k: v for k, v in kw.items() if k != "limit"}),
                                     spots[0] if spots else None)


# ------------------------------------------------------------------------------------------------ the runner
class Stats:
    def __init__(self):
        self.rows = []

    def record_duration(self, key, seconds, units):
        self.rows.append((key, units))

    def duration(self, key):
        return None


def _missing_pick(c):
    raise skillcore.ToolMissing("pickaxe", 1)


# (situation, contract keywords, what the body does, expected: result or exception type, body ran?, stats recorded?)
RUNS = [
    ("a precondition fails: nothing runs", dict(pre=[_missing_pick]), lambda: 5, skillcore.ToolMissing, False, False),
    ("already done: skipped, nothing runs", dict(done=lambda c: True), lambda: 5, None, False, False),
    ("done and verified: the result, timed", dict(verify=lambda c: c.result == 5, units=lambda c: 3, key=lambda c: "k"),
     lambda: 5, 5, True, [("k", 3)]),
    ("finished without the effect: a failure, not timed", dict(verify=lambda c: False), lambda: 5, api.McError,
     True, False),
    ("verify defaults to done", dict(done=lambda c: c.result == 5 if c.result is not None else False), lambda: 5, 5,
     True, True),
    ("the body fails: that failure, not timed", dict(), lambda: (_ for _ in ()).throw(NotAvailable("none here")),
     NotAvailable, True, False),
    ("the body is interrupted: that interruption", dict(), lambda: (_ for _ in ()).throw(api.Interrupted("lava")),
     api.Interrupted, True, False),
]


class Runner(unittest.TestCase):
    def test_contract_runs(self):
        for name, kw, body, want, ran, timed in RUNS:
            calls, stats = [], Stats()

            def fn(ctx, _body=body):
                calls.append(1)
                return _body()
            fn.__name__ = f"bench_{abs(hash(name))}"
            with self.subTest(name), mock.patch.dict(skillkit.REGISTRY), mock.patch.object(skillkit, "STATS", stats), \
                    mock.patch.object(skillkit, "VERIFY_SETTLE_S", 0.01), \
                    mock.patch.object(api, "api", side_effect=AssertionError("the runner read the world")):
                runner = skillkit.skill(needs={}, speed={}, gives={}, **kw)(fn)
                if isinstance(want, type):
                    with self.assertRaises(want):
                        runner(None)
                else:
                    self.assertEqual(runner(None), want)
                self.assertEqual(bool(calls), ran)
                self.assertEqual(stats.rows if timed not in (True, False) else bool(stats.rows), timed)

    # A skill that keeps going: (situation, the markers it yields, budget s, stall s) → TaskStuck saying which limit
    LIMITS = [("progress forever, but over budget", "rising", 0.15, 5.0, "budget"),
              ("no progress: the same marker again and again", "flat", 5.0, 0.1, "no progress"),
              ("slow progress within both limits finishes", "finite", 5.0, 5.0, None),
              ("no progress and a tiny budget: the budget is hit first", "flat", 0.05, 5.0, "budget")]

    def test_budget_and_stall(self):
        for name, markers, budget, stall, want in self.LIMITS:
            def body(ctx, _m=markers):
                i = 0
                while _m != "finite" or i < 5:
                    time.sleep(0.01)
                    i += 1
                    yield i if _m in ("rising", "finite") else 0
                return "done"
            body.__name__ = f"limits_{markers}"
            with self.subTest(name), mock.patch.dict(skillkit.REGISTRY), \
                    mock.patch.object(skillkit, "world_signature", lambda: None), \
                    mock.patch.object(skillkit, "_heartbeat", lambda n: None), \
                    mock.patch.object(skillcore, "dead", lambda *a, **k: False), \
                    mock.patch.object(skillkit, "STATS", None), mock.patch.object(skillkit, "VERIFY_SETTLE_S", 0.01):
                runner = skillkit.skill(needs={}, speed={}, gives={}, budget=budget, stall=stall)(body)
                if want is None:
                    self.assertEqual(runner(None), "done")
                    continue
                with self.assertRaises(api.TaskStuck) as caught:
                    runner(None)
                self.assertIn(want, str(caught.exception))
                self.assertEqual(retry.cause_of(caught.exception), "stuck")

    def test_can_run_asks_the_same_preconditions(self):
        ok = lambda c: None      # noqa: E731  (a precondition that passes)
        for pre, want in (([], (True, None)), ([ok], (True, None)),
                          ([_missing_pick], (False, "need a tier-1 pickaxe")),
                          ([ok, _missing_pick], (False, "need a tier-1 pickaxe")),
                          ([lambda c: (_ for _ in ()).throw(RuntimeError())], (False, "RuntimeError"))):
            with self.subTest(pre=pre), mock.patch.dict(skillkit.REGISTRY):
                runner = skillkit.skill(needs={}, speed={}, gives={}, name=f"can_run_{len(pre)}", pre=pre)(lambda ctx: None)
                self.assertEqual(skillkit.can_run(runner, None), want)

    def test_step_keys_most_specific_first(self):
        from bonobo.planner import Step
        for kind, token, want in (("mine", "minecraft:coal", ["mine:minecraft:coal", "item:minecraft:coal", "mine"]),
                                  ("gather", "log", ["gather:log", "item:log", "gather"]),
                                  ("shelter", "dig in", ["shelter:dig in", "item:dig in", "shelter"]),
                                  ("craft", "minecraft:stone_pickaxe",
                                   ["craft:minecraft:stone_pickaxe", "item:minecraft:stone_pickaxe", "craft"])):
            with self.subTest(kind=kind, token=token):
                self.assertEqual(skillkit.step_keys(Step(kind, token, 1)), want)

    # (providers: (name, effect, prefer, adapter result)), the step → the chosen (name, args) or None
    PROVIDERS = [
        ("the preferred one that can serve", [("p_a", "zz", 1, None), ("p_b", "zz", 0, (7,))], ("p_b", (7,))),
        ("preference first", [("p_a", "zz", 1, (1,)), ("p_b", "zz", 0, (2,))], ("p_a", (1,))),
        ("the specific effect before the generic", [("p_a", "zz", 5, (1,)), ("p_b", "zz:tok", 0, (2,))], ("p_b", (2,))),
        ("nobody can serve here", [("p_a", "zz", 0, None)], None),
    ]

    def test_provider(self):
        from bonobo.planner import Step
        for name, provs, want in self.PROVIDERS:
            with self.subTest(name), mock.patch.dict(skillkit.REGISTRY, clear=True):
                for pname, effect, prefer, got in provs:
                    skillkit.skill(needs={}, speed={}, gives={}, name=pname, provides={effect: lambda ctx, s, _g=got: _g}, prefer=prefer)(
                        lambda ctx, *a: None)
                found = skillkit.provider(None, Step("zz", "tok", 1))
                self.assertEqual(None if found is None else (found[0].contract.name, found[1]), want)


# ----------------------------------------------------------------------------------------------------- commands
FEET = (0, 64, 0)          # fixture: where the body stands in the command rows


def body(region=None, feet=FEET, inv=None, protected=(), **extra):
    """The state dict `commands` is built from (`skillcore.body_state`), from readings."""
    x, y, z = feet
    return dict({"state": state(x=x + 0.5, y=float(y), z=z + 0.5), "feet": feet,
                 "inv": bag(inv if inv is not None else inventory()), "protected": set(protected),
                 "region": region}, **extra)


def world(*changes, floor_y=63, block="stone"):
    """`flat()` with (pos, name) changes; name "air" clears a cell."""
    r = flat(floor_y=floor_y, block=block)
    for pos, name in changes:
        if name == "air":
            r.blocks.pop(pos, None)
        else:
            r.blocks[pos] = name
    return r


def hole():
    """A 1×1 shaft two deep with a roof: already walled in."""
    return world(((0, 64, 0), "air"), ((0, 65, 0), "air"),
                 *[((dx, y, dz), "stone") for y in (64, 65) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))],
                 ((0, 66, 0), "stone"))


def river():
    """Deep water all round a one-block stone pillar the body stands on."""
    r = FakeRegion((-4, 58, -4), (4, 68, 4), {})
    for x, y, z in itertools.product(range(-4, 5), range(58, 64), range(-4, 5)):
        r.blocks[(x, y, z)] = "stone" if (x, z) == (0, 0) or y < 60 else "water"
    return r


def types(batch):
    return [t["type"] for t in batch]


def cells(batch, kind="place"):
    return [(t["x"], t["y"], t["z"]) for t in batch if t["type"] == kind]


def dark(**kw):
    return state(**dict(dict(blockLight=0, skyLight=0, timeOfDay=6000), **kw))


SPOT = {"x": 1, "y": 64, "z": 1}          # fixture: one dark spot /dark answered

# skill → [(situation, state dict, expected)]; expected is an exception type or a check(batch) -> None (asserts).
COMMANDS = {
    "dig_in": [
        ("flat stone, blocks carried: three down and a lid", body(world(), inv=inventory(cobblestone=16)),
         lambda t, b: (t.assertEqual(types(b), ["mine", "wait"] * 3 + ["place"]),
                       t.assertEqual(cells(b), [(0, 63, 0)]))),
        ("no blocks: three down, lidded with what the dig brought up", body(world()),
         lambda t, b: t.assertEqual(types(b), ["mine", "wait"] * 3 + ["place"])),
        ("lava beside the second cell: one deep, no lid below the ground line",
         body(world(((1, 62, 0), "lava")), inv=inventory(cobblestone=4)), NotAvailable),
        ("a cave right under the first cell", body(world(((0, 62, 0), "air"))), NotAvailable),
        ("the floor is our own", body(world(), protected={(0, 63, 0)}), NotAvailable),
        ("bedrock underfoot", body(world(((0, 63, 0), "bedrock"))), NotAvailable),
        ("a start at negative coordinates", body(world(), feet=(-7, 64, -11), inv=inventory(cobblestone=4)),
         lambda t, b: (t.assertEqual(types(b), ["mine", "wait"] * 3 + ["place"]),
                       t.assertEqual(cells(b, "mine"), [(-7, 63, -11), (-7, 62, -11), (-7, 61, -11)]),
                       t.assertEqual(cells(b), [(-7, 63, -11)]))),
    ],
    "pod": [
        ("open ground: eight walls, a cap, the roof", body(world(), inv=inventory(cobblestone=16)),
         lambda t, b: (t.assertEqual(set(types(b)), {"place"}), t.assertEqual(len(b), 10),
                       t.assertEqual(cells(b)[-1], (0, 66, 0)))),
        ("too few blocks", body(world(), inv=inventory(cobblestone=3)), NotAvailable),
        ("grass in a wall cell is broken first", body(world(((1, 64, 0), "short_grass")), inv=inventory(cobblestone=16)),
         lambda t, b: t.assertLess(types(b).index("mine"), next(i for i, x in enumerate(b) if x["type"] == "place"
                                                                and (x["x"], x["y"], x["z"]) == (1, 64, 0)))),
        ("already walled in", body(hole(), inv=inventory(cobblestone=16)), lambda t, b: t.assertEqual(b, [])),
        ("on a pillar in deep water: supports rise from below first", body(river(), inv=inventory(cobblestone=64)),
         lambda t, b: (t.assertEqual(set(types(b)), {"place"}), t.assertGreater(len(b), 10),
                       t.assertTrue(any(y < 64 for _, y, _ in cells(b))))),
        ("a start at negative coordinates", body(world(), feet=(-7, 64, -11), inv=inventory(cobblestone=16)),
         lambda t, b: (t.assertEqual(len(b), 10), t.assertEqual(cells(b)[-1], (-7, 66, -11)),
                       t.assertIn((-8, 64, -11), cells(b)))),
        ("planks count as walls", body(world(), inv=inventory(oak_planks=12)),
         lambda t, b: t.assertEqual({x["item"] for x in b}, {"minecraft:oak_planks"})),
    ],
    "contain_lava": [
        ("two open lava cells: nearest first", body(world(((2, 63, 0), "lava"), ((3, 63, 0), "lava")),
                                                    inv=inventory(cobblestone=8)),
         lambda t, b: t.assertEqual(cells(b), [(2, 63, 0), (3, 63, 0)])),
        ("one block for two cells: covers one", body(world(((2, 63, 0), "lava"), ((3, 63, 0), "lava")),
                                                     inv=inventory(cobblestone=1)),
         lambda t, b: t.assertEqual(cells(b), [(2, 63, 0)])),
        ("lava and nothing to cover it with", body(world(((2, 63, 0), "lava"))), NotAvailable),
        ("lava sealed inside the rock is not exposed", body(world(((0, 60, 0), "lava")), inv=inventory(cobblestone=8)),
         lambda t, b: t.assertEqual(b, [])),
        ("no region read", body(None, inv=inventory(cobblestone=8)), lambda t, b: t.assertEqual(b, [])),
    ],
    "light_area": [
        ("a torch, a dark spot in reach", dict(body(inv=inventory(torch=4)), state=dark(), spots=[SPOT]),
         lambda t, b: t.assertEqual(b, [{"type": "place", "item": "minecraft:torch", "x": 1, "y": 64, "z": 1}])),
        ("the only torch is in the offhand", dict(body(inv=inventory(offhand="torch")), state=dark(), spots=[SPOT]),
         lambda t, b: t.assertEqual(b, [])),
        ("the only dark spot is where we stand", dict(body(inv=inventory(torch=4)), state=dark(),
                                                      spots=[{"x": 0, "y": 64, "z": 0}]),
         lambda t, b: t.assertEqual(b, [])),
        ("the dark spot is out of reach", dict(body(inv=inventory(torch=4)), state=dark(),
                                               spots=[{"x": 5, "y": 64, "z": 0}]), lambda t, b: t.assertEqual(b, [])),
        ("a room: radius 10, three at most, darkest first",
         dict(body(inv=inventory(torch=8)), state=dark(), _args=(10, 3),
              spots=[{"x": x, "y": 64, "z": 0} for x in (2, 4, 6, 8, 12)]),
         lambda t, b: t.assertEqual(cells(b), [(2, 64, 0), (4, 64, 0), (6, 64, 0)])),
        ("nothing dark answered", dict(body(inv=inventory(torch=8)), state=dark(), spots=[]),
         lambda t, b: t.assertEqual(b, [])),
    ],
    "bridge_toward": [
        ("a gap with no floor: lay, step, lay, step", body(world(*[((x, 63, 0), "air") for x in (1, 2, 3)]),
                                                           inv=inventory(cobblestone=16), _args=((4, 64, 0),)),
         lambda t, b: (t.assertEqual(cells(b), [(1, 63, 0), (2, 63, 0), (3, 63, 0)]),
                       t.assertEqual(cells(b, "goto")[-1], (4, 64, 0)))),
        ("a wall in the way is dug, feet and head", body(world(((2, 64, 0), "stone"), ((2, 65, 0), "stone")),
                                                         inv=inventory(cobblestone=16), _args=((4, 64, 0),)),
         lambda t, b: t.assertEqual(cells(b, "mine"), [(2, 64, 0), (2, 65, 0)])),
        ("our own wall stops the bridge before it", body(world(((2, 64, 0), "stone")), protected={(2, 64, 0)},
                                                         inv=inventory(cobblestone=16), _args=((4, 64, 0),)),
         lambda t, b: (t.assertNotIn((2, 64, 0), cells(b, "mine")),
                       t.assertTrue(all(x < 2 for x, _, _ in cells(b, "goto"))))),
        ("the target is above: pillar first", body(world(), inv=inventory(cobblestone=16), _args=((3, 66, 0),)),
         lambda t, b: t.assertEqual(types(b)[:2], ["pillar", "pillar"])),
        ("no blocks: no bridge", body(world(((1, 63, 0), "air")), _args=((4, 64, 0),)),
         lambda t, b: t.assertEqual(b, [])),
        ("never further than its reach", body(world(*[((x, 63, 0), "air") for x in range(1, 60)]),
                                              inv=inventory(cobblestone=64), _args=((60, 64, 0),)),
         lambda t, b: t.assertLessEqual(len(cells(b, "goto")), skills.BRIDGE_REACH)),
    ],
}


def blueprint_cases():
    """build_shelter / build_blueprint rows, swept: every blueprint × four turns × {bare site, half built, under a
    canopy}. What must hold is the same for all: a place for every part not yet standing and none for a part that
    is, leaves cleared first, never a face above the eye (pillar first), items only from what is carried."""
    out = {"build_shelter": [], "build_blueprint": []}
    for (name, bp), turns, progress in itertools.product(blueprints.REGISTRY.items(), range(4),
                                                         ("bare", "half", "canopy")):
        origin = (0, 64, 4)
        parts = blueprints.placed(bp, origin, turns)
        done = parts[: len(parts) // 2] if progress == "half" else []
        region = world(*[(pos, members(part.item)[0].split(":")[1]) for pos, part, _, _ in done])
        if progress == "canopy":
            top = max(p[0][1] for p in parts)
            for pos, _, _, _ in parts[:3]:
                region.blocks[(pos[0], top + 1, pos[2])] = "oak_leaves"
        carried = inventory(*[(members(tok)[0], min(64, n + 8)) for tok, n in blueprints.materials(bp).items()],
                            cobblestone=64)
        st = body(region, feet=blueprints.access_spot(bp, origin, turns), inv=carried)
        missing = {pos for pos, _, _, _ in parts} - {pos for pos, _, _, _ in done}
        facing = {pos: f if against is None else None for pos, _, f, against in parts}
        check = _blueprint_check(missing, progress == "canopy", st, facing)
        label = f"{name} turns={turns} {progress}"
        if name == "shelter":
            out["build_shelter"].append((label, dict(st, spot=(origin, turns)), check))
        out["build_blueprint"].append((label, dict(st, started={"origin": list(origin), "turns": turns},
                                                   _args=(name, origin)), check))
        if turns == 0 and progress == "bare":
            # Nothing started: the batch is planned where the region has room — every part placed, somewhere.
            fresh = body(world(), feet=(0, 64, 0), inv=carried)
            out["build_blueprint"].append((f"{name}: nothing started, open ground", dict(fresh, _args=(name, (0, 64, 4))),
                                           lambda t, b, n=len(parts): t.assertEqual(len(cells(b)), n)))
            out["build_blueprint"].append((f"{name}: nothing started, no region read", dict(fresh, region=None,
                                                                                        _args=(name, (0, 64, 4))),
                                           lambda t, b: t.assertEqual(b, [])))
            walled = world(*[((x, y, z), "bedrock") for x in range(-12, 13) for z in range(-8, 17) for y in (64, 65, 66, 67)
                             if (x, z) != (0, 0)])
            out["build_blueprint"].append((f"{name}: nothing started, nowhere to build", dict(
                body(walled, feet=(0, 64, 0), inv=carried), _args=(name, (0, 64, 4))),
                lambda t, b: t.assertEqual(b, [])))
    return out


def _blueprint_check(missing, canopy, st, facing):
    def check(t, batch):
        t.assertEqual(set(cells(batch)), missing, "one place per missing part, none for a part already standing")
        t.assertEqual(len(cells(batch)), len(missing))
        t.assertEqual(types(batch)[0] == "mine_many", canopy, "leaves are cleared first, and only when there")
        carried = {s["id"] for s in st["inv"].slots}
        fy = st["feet"][1]
        for task in batch:
            if task["type"] == "pillar":
                fy += 1
            if task["type"] == "place":
                t.assertLess(task["y"] - fy, 2, f"{task}: the face is above the eye")
                t.assertIn(task["item"], carried)
                # The jar turns the body from the block's own rule: the batch names the facing, never a rotation.
                t.assertEqual(task.get("facing"), facing[(task["x"], task["y"], task["z"])], task)
                t.assertFalse({"yaw", "pitch"} & set(task), task)
    return check


for _skill, _rows in blueprint_cases().items():
    COMMANDS.setdefault(_skill, []).extend(_rows)


class BlueprintBatch(unittest.TestCase):
    """building._blueprint_commands_for directly: where the batch comes from, and when there is none."""

    def test_rows(self):
        from bonobo import building
        carried = inventory(("cobblestone", 64), ("obsidian", 20), ("oak_door", 2), ("torch", 4), ("chest", 4),
                            ("hopper", 2), ("furnace", 2))
        walled = world(*[((x, y, z), "bedrock") for x in range(-12, 13) for z in range(-8, 17) for y in range(64, 68)
                         if (x, z) != (0, 0)])
        portal = blueprints.REGISTRY["nether_portal"]
        n = len(blueprints.placed(portal, (0, 64, 4), 0))
        rows = [("a build already started: its own origin", dict(started={"origin": [0, 64, 4], "turns": 0}),
                 lambda t, b: t.assertEqual(set(cells(b)), {p for p, *_ in blueprints.placed(portal, (0, 64, 4), 0)})),
                ("nothing started, open ground: every part, somewhere near", {},
                 lambda t, b: t.assertEqual(len(cells(b)), n)),
                ("nothing started, no region read", dict(region=None), lambda t, b: t.assertEqual(b, [])),
                ("nothing started, nowhere to build", dict(region=walled), lambda t, b: t.assertEqual(b, [])),
                ("a blueprint nobody drew", dict(_bp="castle"), KeyError)]
        for name, extra, want in rows:
            st = dict(body(world(), inv=carried), **{k: v for k, v in extra.items() if k != "_bp"})
            args = (extra.get("_bp", "nether_portal"), (0, 64, 4))
            with self.subTest(name), mock.patch.object(api, "api", side_effect=AssertionError("read the world")):
                if isinstance(want, type):
                    with self.assertRaises(want):
                        building._blueprint_commands_for(st, args)
                else:
                    want(self, building._blueprint_commands_for(st, args))


class Commands(unittest.TestCase):
    def batch(self, name, st):
        return skillkit.commands_of(skillkit.REGISTRY[name].runner, st, *st.get("_args", ()))

    def test_batches(self):
        for name, rows in COMMANDS.items():
            for situation, st, want in rows:
                with self.subTest(skill=name, situation=situation):
                    if isinstance(want, type):
                        with self.assertRaises(want):
                            self.batch(name, st)
                    else:
                        want(self, self.batch(name, st))

    def test_commands_read_nothing(self):
        """Pure: the same state, the same batch, and no request made."""
        with mock.patch.object(api, "api", side_effect=AssertionError("commands read the world")):
            for name, rows in COMMANDS.items():
                for situation, st, want in rows:
                    if isinstance(want, type):
                        continue
                    with self.subTest(skill=name, situation=situation):
                        self.assertEqual(self.batch(name, st), self.batch(name, st))

    def test_every_open_loop_skill_has_rows(self):
        declared = {n for n, c in skillkit.REGISTRY.items() if c.commands is not None}
        self.assertEqual(declared - set(COMMANDS), set(), "an open-loop skill without COMMANDS rows")
        self.assertEqual(set(COMMANDS) - declared, set(), "rows for a skill that declares no commands")

    def test_closed_loop_skills_answer_none(self):
        closed = {n: c for n, c in skillkit.REGISTRY.items() if c.commands is None}
        self.assertEqual({n: skillkit.commands_of(c.runner, {}) for n, c in closed.items()},
                         {n: None for n in closed}, "a closed-loop skill has no batch to hand over")




class Declarations(unittest.TestCase):
    """@skill: every skill states `needs` (hard prerequisites), `speed` (tools that make it faster, seconds saved
    per unit) and `gives` (what it produces: producing tables, or states) — `{}` written out when there are none —
    or it is refused at import."""

    def test_every_registered_skill_declares_both(self):
        from bonobo import data, knowledge
        tools = set(data.TOOL_KINDS)
        bad = {n: (c.needs, c.speed) for n, c in skillkit.REGISTRY.items()
               if not isinstance(c.needs, dict) or not isinstance(c.speed, dict)
               or not set(c.speed) <= tools or any(not (v > 0) for v in c.speed.values())
               or knowledge.SKILL_SPEED.get(n) != c.speed}
        self.assertEqual(bad, {})

    def test_the_decorator_refuses_what_is_undeclared(self):
        # (situation, needs, speed, gives) → the TypeError's words, or None when it registers
        rows = [("must fail: no needs", None, {}, {}, "declares no needs"),
                ("must fail: no speed", {}, None, {}, "declares no speed"),
                ("must fail: no gives", {}, {}, None, "declares no gives"),
                ("must fail: none of them", None, None, None, "declares no needs and no speed and no gives"),
                ("all three written out empty: registers", {}, {}, {}, None),
                ("needs as a function of the call: registers", lambda a: {}, {}, {}, None)]
        for name, needs, speed, gives, want in rows:
            with self.subTest(name):
                kw = {k: v for k, v in (("needs", needs), ("speed", speed), ("gives", gives)) if v is not None}
                try:
                    skillkit.skill("_dummy_for_the_contract_test", **kw)(lambda ctx: None)
                    got = None
                except TypeError as e:
                    got = str(e)
                finally:
                    skillkit.REGISTRY.pop("_dummy_for_the_contract_test", None)
                    __import__("bonobo.knowledge", fromlist=["SKILL_SPEED"]).SKILL_SPEED.pop(
                        "_dummy_for_the_contract_test", None)
                self.assertEqual(got if want is None else (got is not None and want in got),
                                 None if want is None else True, got)

class BagRules(unittest.TestCase):
    """A gatherer's failure on a full bag names the bag (skill.bag_full_reason, one place for chop/hunt/mine/loot)."""

    # (situation, the failure, free slots) → the message raised (None: the failure stands as it is)
    ROWS = [("chop, no free slot", "could not chop enough logs", 0,
             "bag full (no free slot): could not chop enough logs"),
            ("hunt, three slots free: its own failure", "no cow found", 3, None),
            ("loot, nothing carried away, bag full", "loot_chest: finished without reaching its goal", 0,
             "bag full (no free slot): loot_chest: finished without reaching its goal"),
            ("mine, already said", "bag full: 12 stone left on the ground", 0, None),
            ("the bag could not be read", "could not hunt enough beef", None, None)]

    def test_reason_over_the_table(self):
        for name, msg, free, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(skillkit.bag_full_reason(msg, free), want)

    # (situation, bag slots, free slots, what is gathered) → room for one more
    ROOM = [("a free slot", [], 1, {"minecraft:cobblestone"}, True),
            ("full, a cobblestone stack at 40", [{"id": "minecraft:cobblestone", "count": 40}], 0,
             {"minecraft:cobblestone"}, True),
            ("full, the cobblestone stack at 64", [{"id": "minecraft:cobblestone", "count": 64}], 0,
             {"minecraft:cobblestone"}, False),
            ("full of dirt, mining stone", [{"id": "minecraft:dirt", "count": 64}], 0, {"minecraft:cobblestone"}, False)]

    def test_room_over_the_table(self):
        from bonobo import bag
        for name, slots, free, ids, want in self.ROOM:
            with self.subTest(name):
                self.assertIs(bag.has_room(slots, free, ids), want)

    def test_the_floor_is_not_mined(self):
        from bonobo import bag
        ring = lambda x, y, z: {(x + dx, y, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)}   # noqa: E731
        for feet, want in (((0, 64, 0), ring(0, 63, 0)), ((10003, 200, 9999), ring(10003, 199, 9999)),
                           ((-5, -60, 7), ring(-5, -61, 7)), ((1, 0, 1), ring(1, -1, 1))):
            with self.subTest(feet=feet):
                self.assertEqual(bag.supports(feet), want)
        self.assertNotIn((0, 62, 0), bag.supports((0, 64, 0)))       # two below is not the floor
        self.assertNotIn((2, 63, 0), bag.supports((0, 64, 0)))       # two aside is not either

    # (situation, cells asked, feet) → the cells a skill may break, in order
    MINEABLE = [
        ("the bench floor: the ring block beside the feet is not a target", [(9999, 199, 10000), (9998, 199, 10000)],
         (10000, 200, 10000), [(9998, 199, 10000)]),
        ("a wall at feet level and one above: both kept, order kept", [(2, 65, 0), (2, 64, 0)], (0, 64, 0),
         [(2, 65, 0), (2, 64, 0)]),
        ("edge: the corner of the ring is floor too", [(1, 63, 1), (2, 63, 2)], (0, 64, 0), [(2, 63, 2)]),
        ("must fail: two below the feet — the body's own column, not a target", [(0, 62, 0)], (0, 64, 0), []),
        ("must fail: the platform under the feet, any depth (upkeep__bridge_stock fell through)",
         [(10000, 198, 10000), (10000, 197, 10000)], (10000, 200, 10000), []),
        ("the next column down: allowed", [(10001, 198, 10000), (10000, 198, 10000)], (10000, 200, 10000),
         [(10001, 198, 10000)]),
        ("above the feet in the same column: allowed (a ceiling)", [(0, 66, 0)], (0, 64, 0), [(0, 66, 0)]),
        ("must fail: only the floor offered → nothing to mine", [(0, 63, 0), (-1, 63, 0), (0, 63, 1)], (0, 64, 0),
         []),
    ]

    def test_mineable_only_where_a_body_can_stand_at_it(self):
        """bag.mineable with the blocks: a cell whose every open face is over a drop is not a target (the platform's
        edge cell, its face over the gap: the walker stepped out and fell 97)."""
        from bonobo import bag
        from tests.world import FakeRegion
        lo, hi = (9990, 180, 9994), (10010, 205, 10006)
        plat = {(x, y, z): "stone" for x in range(9995, 10002) for y in (197, 198, 199) for z in range(9995, 10006)}
        below = {(x, 197, z): "stone" for x in range(10002, 10006) for z in range(9995, 10006)}
        pool = {(x, 190, z): "water" for x in range(10002, 10006) for z in range(9995, 10006)}
        edge, feet = (10001, 198, 10000), (10000, 200, 10000)
        rows = [("must fail: the edge cell, its one face over the gap", plat, [edge], []),
                ("the same cell, a floor under its face: allowed", {**plat, **below}, [edge], [edge]),
                ("the same cell over deep water below the gap: still a drop past SAFE_DROP", {**plat, **pool},
                 [edge], []),
                ("a top cell of the platform, open above: allowed", plat, [(9997, 199, 10000)], [(9997, 199, 10000)]),
                ("no blocks read (region None): the floor rule only", None, [edge], [edge]),
                ("a sealed diamond, stone all round: allowed (the approach digs to it)",
                 {**plat, **{(9997, 198, 10000): "diamond_ore"}}, [(9997, 198, 10000)], [(9997, 198, 10000)]),
                ("must fail: the edge cell read with a pad-3 region (ends 3 below): unread is not a floor",
                 ("pad3", plat), [edge], []),
                ("the edge cell, a real floor read in the same pad-3 region: allowed",
                 ("pad3", {**plat, **{(x, 196, z): "stone" for x in range(10002, 10006) for z in range(9995, 10006)}}),
                 [edge], [edge])]
        for name, blocks, cells, want in rows:
            with self.subTest(name):
                if isinstance(blocks, tuple):
                    region = FakeRegion((9997, 195, 9997), (10004, 203, 10003), blocks[1])   # region_around pad 3
                else:
                    region = None if blocks is None else FakeRegion(lo, hi, blocks)
                self.assertEqual(bag.mineable(cells, feet, region, nav.SAFE_DROP), want)

    def test_open_faced_before_buried(self):
        """bag.mineable: open-faced cells first; a buried one only when nothing open is left."""
        from bonobo import bag
        from tests.world import FakeRegion
        lo, hi = (9990, 190, 9990), (10010, 205, 10010)
        floor3 = {(x, y, z): "stone" for x in range(9994, 10007) for y in (197, 198, 199) for z in range(9994, 10007)}
        feet = (10000, 200, 10000)
        rows = [("a flat stone floor: the open top cells, not the buried ones under the ring",
                 floor3, [(10001, 198, 10000), (10003, 199, 10000), (10002, 197, 10000)], [(10003, 199, 10000)]),
                ("only buried cells asked: the buried ones then (the approach digs)", floor3,
                 [(10002, 198, 10000), (10003, 198, 10000)], [(10002, 198, 10000), (10003, 198, 10000)]),
                ("a sealed ore alone: allowed", {**floor3, (10004, 198, 10004): "diamond_ore"}, [(10004, 198, 10004)],
                 [(10004, 198, 10004)]),
                ("must fail: the ring under the feet, buried or not", floor3, [(10001, 199, 10000), (10000, 198, 10000)],
                 [])]
        for name, blocks, cells, want in rows:
            with self.subTest(name):
                self.assertEqual(bag.mineable(cells, feet, FakeRegion(lo, hi, blocks), nav.SAFE_DROP), want)

    def test_mineable_is_the_floor_rule(self):
        from bonobo import bag
        for name, cells, feet, want in self.MINEABLE:
            with self.subTest(name):
                self.assertEqual(bag.mineable(cells, feet), want)

    # (situation, cells refused now, refused before, jar digs its own approach) → (asked again, dropped)
    REFUSED = [
        ("first refusal, old jar: ask again after making a way", [(1, 2, 3)], set(), False, ({(1, 2, 3)}, set())),
        ("the same block refused twice: dropped (the mine_stone loop)", [(9999, 199, 10000)], {(9999, 199, 10000)},
         False, (set(), {(9999, 199, 10000)})),
        ("mixed batch: only the repeat is dropped", [(1, 2, 3), (4, 5, 6)], {(4, 5, 6)}, False,
         ({(1, 2, 3)}, {(4, 5, 6)})),
        ("the jar already dug its approach: dropped on the first refusal", [(1, 2, 3)], set(), True,
         (set(), {(1, 2, 3)})),
        ("edge: nothing refused", [], {(1, 2, 3)}, False, (set(), set())),
    ]

    def test_refused_targets_are_dropped(self):
        from bonobo import bag
        for name, cells, before, jar_digs, want in self.REFUSED:
            with self.subTest(name):
                self.assertEqual(bag.refused(cells, before, jar_digs), want)

    def test_a_gatherer_checks_the_bag_before_it_starts(self):
        """chop/mine/hunt/loot on a bag with no room for what they gather fail before the body does anything; with
        a stack of it not yet full they run."""
        from tests.world import bag, inventory
        full_dirt = [("dirt", 64)] * 36
        rows = [("chop, full of dirt", lambda c: ["minecraft:oak_log"], full_dirt, "no room for oak_log"),
                ("mine, full of dirt", lambda c: ["minecraft:cobblestone"], full_dirt, "no room for cobblestone"),
                ("hunt, full of dirt", lambda c: ["minecraft:beef"], full_dirt, "no room for beef"),
                ("loot, full of dirt: room for anything needed", True, full_dirt, "no room for anything"),
                ("chop, full but an oak_log stack at 10", lambda c: ["minecraft:oak_log"],
                 [("dirt", 64)] * 35 + [("oak_log", 10)], None)]
        for name, fills, carried, want in rows:
            ran = []

            @skillkit.skill(needs={}, speed={}, gives={}, name="bag_gate_probe", fills_bag=fills)
            def probe(ctx):
                ran.append(True)
            inv = bag(inventory(*carried))
            with self.subTest(name), mock.patch.object(skillcore, "Inventory", return_value=inv):
                if want is None:
                    probe(None)
                    self.assertEqual(ran, [True])
                else:
                    with self.assertRaises(api.McError) as got:
                        probe(None)
                    self.assertEqual((str(got.exception), ran),
                                     (f"bag full (no free slot): bag_gate_probe: {want}", []))
            skillkit.REGISTRY.pop("bag_gate_probe", None)

    def test_only_gatherers_say_it(self):
        """The same failure on a full bag: a gatherer's names the bag, another skill's stays its own."""
        for fills, want in ((True, "bag full (no free slot): nothing left to take"), (False, "nothing left to take")):
            @skillkit.skill(needs={}, speed={}, gives={}, name=f"bag_probe_{fills}", fills_bag=fills)
            def probe(ctx):
                raise api.NotAvailable("nothing left to take")
            with self.subTest(fills_bag=fills), mock.patch.object(skillkit, "_free_slots", return_value=0), \
                    self.assertRaises(api.McError) as got:
                probe(None)
            self.assertEqual(str(got.exception), want)
            skillkit.REGISTRY.pop(f"bag_probe_{fills}", None)

if __name__ == "__main__":
    unittest.main()


class PortalCast(unittest.TestCase):
    """fluids.cast_frame_plan / mould_to_break: the pure plan building.cast_portal executes (frame at (0,64,0),
    along z: corners stone at y64/y68, obsidian between)."""
    ORIGIN = (0, 64, 0)

    def plan(self, solid):
        from bonobo import blueprints, fluids
        return fluids.cast_frame_plan(blueprints.NETHER_PORTAL, self.ORIGIN, 1, solid)

    def test_frame_plan(self):
        rows = [
            ("open air: first cell walled on its 3 open sides + below", lambda c: False,
             0, ((0, 64, 1), [(1, 64, 1), (-1, 64, 1), (0, 64, 2), (0, 63, 1)]), 10),
            ("the corner below is part of the frame: never mould", lambda c: False,
             2, ((0, 65, 0), [(1, 65, 0), (-1, 65, 0), (0, 65, 1), (0, 65, -1)]), 10),
            ("a solid neighbour needs no mould", lambda c: c == (1, 64, 1),
             0, ((0, 64, 1), [(-1, 64, 1), (0, 64, 2), (0, 63, 1)]), 10),
            ("the cell cast before is no mould for the next", lambda c: False,
             1, ((0, 64, 2), [(1, 64, 2), (-1, 64, 2), (0, 63, 2)]), 10),
            ("all solid around: no mould at all", lambda c: True, 0, ((0, 64, 1), []), 10),
        ]
        for name, solid, i, step, n in rows:
            with self.subTest(name):
                out = self.plan(solid)
                self.assertEqual((out[i], len(out)), (step, n))
                self.assertEqual([c[1] for c, _ in out], sorted(c[1] for c, _ in out))   # bottom-up

    def frame(self, cast, lit=False, origin=ORIGIN):
        """A region holding the first `cast` obsidian cells of the frame at `origin` (turns 1), lit or not."""
        from bonobo import blueprints
        from bonobo.world import Region
        r = Region.__new__(Region)
        r.lo, r.hi, r.props = (-8, 60, -8), (8, 72, 8), {}
        obs = [p for p, part, *_ in blueprints.placed(blueprints.NETHER_PORTAL, origin, 1)
               if part.item == "minecraft:obsidian"]
        r.blocks = {p: "obsidian" for p in sorted(obs, key=lambda p: (p[1], p[2]))[:cast]}
        if lit:
            r.blocks.update({c: "nether_portal" for c in blueprints.clear_cells(blueprints.NETHER_PORTAL, origin, 1)})
        return r

    def test_resume_a_started_frame(self):
        from bonobo import blueprints, building
        bp = blueprints.NETHER_PORTAL
        # (situation, cells standing, lit) → (cells still to cast, light it?, frame picked up as started?)
        rows = [("empty: cast all, light", 0, False, (10, True, False)),
                ("a stray obsidian is no frame", 1, False, (9, True, False)),
                ("8 of 10: cast the 2, light", 8, False, (2, True, True)),
                ("all cast, unlit: light only", 10, False, (0, True, True)),
                ("lit: leave it be", 10, True, (0, False, True))]
        for name, cast, lit, want in rows:
            with self.subTest(name):
                region = self.frame(cast, lit)
                todo, unlit = building.portal_todo(bp, self.ORIGIN, 1, region.name)
                started = (self.ORIGIN, 1) in building.started_builds(bp, region, (0, 64, 3))
                self.assertEqual((len(todo), unlit, started), want)

    def test_spot_prefers_the_started_frame(self):
        from bonobo import blueprints, building, nav
        region = self.frame(8)
        cost, origin, turns, prepare = building.spot_options(blueprints.NETHER_PORTAL, (0, 64, 3), region,
                                                             nav.Policy(), radius=2)[0]
        cells = lambda o, t: sorted(p for p, *_ in blueprints.placed(blueprints.NETHER_PORTAL, o, t))  # noqa: E731
        self.assertEqual((cost, prepare, cells(origin, turns)), (0, (), cells(self.ORIGIN, 1)))   # the same frame

    def test_mould_to_break(self):
        from bonobo import blueprints, fluids
        rows = [
            ("inside the frame: the portal needs the air", [(0, 65, 1)], [(0, 65, 1)]),
            ("on a frame cell still to be cast", [(0, 64, 2)], [(0, 64, 2)]),
            ("outside the frame stays", [(1, 64, 1), (0, 63, 1)], []),
            ("mixed: only the in-frame ones", [(1, 64, 1), (0, 66, 2), (0, 68, 1)], [(0, 66, 2), (0, 68, 1)]),
            ("none placed", [], []),
        ]
        for name, placed, want in rows:
            with self.subTest(name):
                self.assertEqual(fluids.mould_to_break(blueprints.NETHER_PORTAL, self.ORIGIN, 1, placed), want)

    def test_lava_bucket(self):
        from bonobo import fluids
        from bonobo.api import NotAvailable
        rows = [
            ("already carried: nothing to do", {"minecraft:lava_bucket": 1}, None),
            ("no bucket: fails with the reason", {}, "no bucket for lava"),
            ("bucket, no lava in reach: fails with the reason", {"minecraft:bucket": 1}, "no lava source within reach"),
            ("bucket, lava only elsewhere: same", {"minecraft:bucket": 1, "minecraft:water_bucket": 1},
             "no lava source within reach"),
        ]
        for name, items, reason in rows:
            with self.subTest(name):
                inv = bag(inventory(*items.items()))
                with mock.patch.object(fluids, "Inventory", lambda data=None: inv), \
                        mock.patch.object(fluids, "find", lambda *a, **k: []):
                    if reason is None:
                        self.assertIsNone(fluids._lava_bucket(None, (0, 64, 0)))
                    else:
                        with self.assertRaisesRegex(NotAvailable, reason):
                            fluids._lava_bucket(None, (0, 64, 0))


class PureHelpers(unittest.TestCase):
    """Small pure rules with no table of their own: the avoid list, the head in water, the kit's needs, when work
    stops for an interrupt, the incoming-damage ceiling."""

    def test_avoid_cells(self):
        R = nav.AVOID_RADIUS
        rows = [("near the walk's end: kept", {(5, 64, 0)}, [(0, 64, 0)], [{"x": 5, "y": 64, "z": 0}]),
                ("exactly at the radius: kept", {(R, 64, 0)}, [(0, 64, 0)], [{"x": R, "y": 64, "z": 0}]),
                ("one past the radius: dropped", {(R + 1, 64, 0)}, [(0, 64, 0)], []),
                ("near either end counts", {(200, 64, 0)}, [(0, 64, 0), (199, 64, 0)], [{"x": 200, "y": 64, "z": 0}]),
                ("nothing protected", set(), [(0, 64, 0)], [])]
        for name, protected, near, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.avoid_cells(protected, *near), want)

    def test_with_avoid(self):
        prot = {(1, 64, 0)}
        cell = [{"x": 1, "y": 64, "z": 0}]
        rows = [("a mine task gets the avoid list", {"type": "mine", "x": 0, "y": 64, "z": 0},
                 {"type": "mine", "x": 0, "y": 64, "z": 0, "avoid": cell}),
                ("mine_many: near any of its blocks", {"type": "mine_many", "blocks": [{"x": 2, "y": 64, "z": 0}]},
                 {"type": "mine_many", "blocks": [{"x": 2, "y": 64, "z": 0}], "avoid": cell}),
                ("a task that names its own avoid is left alone", {"type": "place", "x": 0, "y": 64, "z": 0, "avoid": []},
                 {"type": "place", "x": 0, "y": 64, "z": 0, "avoid": []}),
                ("a non-approaching task is left alone", {"type": "look", "x": 0, "y": 64, "z": 0},
                 {"type": "look", "x": 0, "y": 64, "z": 0})]
        for name, task, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.with_avoid(task, prot), want)

    def test_head_underwater(self):
        # (situation, body, what is at the eyes' block) → underwater?
        rows = [("swimming, water at the eyes", {"inWater": True, "y": 64.0}, "water", True),
                ("in water, head out (air at the eyes)", {"inWater": True, "y": 64.0}, "air", False),
                ("not in water at all, whatever the eyes' block", {"inWater": False, "y": 64.0}, "water", False),
                ("eyes one block up from a half-block body", {"inWater": True, "y": 64.5}, "water", True)]
        for name, body, at_eyes, want in rows:
            with self.subTest(name):
                s = {"blockX": 0, "blockZ": 0, **body}
                eye = (0, int(body["y"] + 1.62), 0)
                region = FakeRegion((-1, 60, -1), (1, 70, 1), {eye: at_eyes})
                with mock.patch.object(skillcore, "Region", lambda lo, hi, props=False: region):
                    self.assertIs(skillcore.head_underwater(s), want)

    def test_kit_needs(self):
        from bonobo import knowledge
        food = ("food", knowledge.KIT_FOOD)
        rows = [("empty bag: all three", {}, [food, ("stone", 32), ("minecraft:golden_helmet", 1)]),
                ("complete kit: nothing", {"cooked_beef": knowledge.KIT_FOOD, "cobblestone": 32, "golden_helmet": 1}, []),
                ("one meal short: food only", {"cooked_beef": knowledge.KIT_FOOD - 1, "cobblestone": 32,
                                                "golden_helmet": 1}, [food]),
                ("31 blocks: one short", {"cooked_beef": knowledge.KIT_FOOD, "cobblestone": 31, "golden_helmet": 1},
                 [("stone", 32)])]
        for name, counts, want in rows:
            with self.subTest(name):
                self.assertEqual(knowledge.kit_needs(bag(inventory(**counts))), want)

    def test_kit_needs_counts_a_worn_helmet(self):
        from bonobo import knowledge
        for name, head, want in [("worn", "golden_helmet", []), ("iron worn: still missing", "iron_helmet",
                                                                    [("minecraft:golden_helmet", 1)]),
                                 ("nothing worn", None, [("minecraft:golden_helmet", 1)]),
                                 ("worn and carried", "golden_helmet", [])]:
            with self.subTest(name):
                counts = {"cooked_beef": knowledge.KIT_FOOD, "cobblestone": 32}
                if name == "worn and carried":
                    counts["golden_helmet"] = 1
                self.assertEqual(knowledge.kit_needs(bag(inventory(head=head, **counts))), want)

    def test_interrupt_due(self):
        # (situation, pending interrupt, mode, a preemption at, soft?, work began at) → stop?
        rows = [("nothing pending", None, "survival", 5.0, False, 1.0, False),
                ("pending, ordinary mode: stop", "hostiles", "normal", 0.0, False, 1.0, True),
                ("soft skills read it themselves", "hostiles", "normal", 0.0, True, 1.0, False),
                ("a rescue ignores perception's messages", "hostiles", "survival", 0.0, False, 1.0, False),
                ("...but not a preemption made after it began", "lava", "survival", 2.0, False, 1.0, True),
                ("a preemption before it began is old news", "lava", "survival", 0.5, False, 1.0, False)]
        for name, pending, mode, pre_at, soft, since, want in rows:
            with self.subTest(name):
                with mock.patch.object(api, "INTERRUPT", pending), mock.patch.object(api, "MODE", mode), \
                        mock.patch.object(arbiter.BODY, "preempted_at", pre_at):
                    self.assertIs(api.interrupt_due(since, soft), want)

    def test_incoming_cap(self):
        from bonobo import estimate
        imm = float(estimate.PLAYER["hurt_immunity_s"])
        for name, hit, want in [("one zombie's hit", 3.0, 3.0 / imm), ("a harder hit", 9.0, 9.0 / imm),
                                ("nothing lands: no ceiling", 0.0, float("inf")),
                                ("negative (a heal) is no hit", -1.0, float("inf"))]:
            with self.subTest(name):
                self.assertEqual(estimate.incoming_cap(hit), want)


class AttemptPolicy(unittest.TestCase):
    """brain.Brain.attempt on a real Brain: what each kind of exception out of a step does to the ledgers.
    An interruption counts nothing, bans nothing and cools nothing; a failure is counted with its reason and /stop
    is posted; a success clears the name's count; a crash of ours holds the name. Only the waits an interruption
    triggers (handback, the game coming back, standing down) and the mod's /stop are replaced — never `attempt`."""

    def run_attempt(self, br, err):
        posts = []

        def step():
            if err is not None:
                raise err
        with mock.patch.object(api, "wait_for_handback", lambda: None), \
                mock.patch.object(api, "wait_for_game", lambda: None), \
                mock.patch.object(brain.time, "sleep", lambda s: None), \
                mock.patch.object(api, "post", lambda path, body=None: posts.append(path)):
            outcome = br.attempt("chop", step)
        return outcome, posts

    def ledgers(self, br):
        return (sorted(br.retry.entries), sorted(br.retry.cooling), sorted(br.retry.holds), dict(br.blacklist))

    def test_each_exception_kind(self):
        clean = ([], [], [], {})
        # (situation, what the step raises, outcome, /stop posted?, the ledgers after, what the count says)
        rows = [
            ("an interruption by danger", api.Interrupted("lava"), "interrupted", [], clean, None),
            ("the plan grew stale", api.CommitmentExpired("replan"), "interrupted", [], clean, None),
            ("someone else drives the body", api.BodyContested("replaced"), "interrupted", [], clean, None),
            ("the player took control", api.PlayerTookControl("paused"), "interrupted", [], clean, None),
            ("the game went away", api.GameUnreachable("restarting"), "interrupted", [], clean, None),
            ("a real failure: counted with its reason", api.McError("no logs within 48 blocks"), "failed",
             ["/stop"], ([("chop", "error")], [f"error@{None}"], [], {}), "no logs within 48 blocks"),
            ("nothing here now: counted as unavailable", api.NotAvailable("no trees"), "failed", ["/stop"],
             ([("chop", "unavailable")], [f"unavailable@{None}"], [], {}), "no trees"),
            ("a crash of ours: the name is held, not counted", ValueError("bad index"), "failed", [],
             ([], [], ["chop"], {}), None),
            ("success", None, "ok", [], clean, None),
        ]
        for name, err, outcome, stops, after, message in rows:
            with self.subTest(name):
                br = brain.Brain()
                got, posts = self.run_attempt(br, err)
                ledgers = self.ledgers(br)
                ledgers = (ledgers[0], [k.split("@")[0] + "@None" for k in ledgers[1]], ledgers[2], ledgers[3])
                self.assertEqual((got, posts, ledgers), (outcome, stops, after))
                if message is not None:
                    self.assertEqual(br.retry.entries[ledgers[0][0]]["message"], message)
                    self.assertEqual(br.last_failure.n, 1)

    def test_success_clears_the_count(self):
        br = brain.Brain()
        rows = [("one failure counts 1", api.McError("x"), 1), ("a second counts 2", api.McError("x"), 2),
                ("an interruption leaves it at 2", api.Interrupted("lava"), 2), ("a success clears it", None, 0)]
        for name, err, n in rows:
            with self.subTest(name):
                self.run_attempt(br, err)
                self.assertEqual(br.retry.entries.get(("chop", "error"), {"n": 0})["n"], n)


class BagFull(unittest.TestCase):
    """The bag's pure rules on a full bag: room, what to throw, what never goes, and the reason a failure names."""
    COBBLE = "minecraft:cobblestone"

    @staticmethod
    def bag_of(*stacks):
        """stacks: (item, count, n) → n slots of `count` each, numbered in order."""
        from tests.world import slot
        out = []
        for item, count, n in stacks:
            out += [slot(item, count, i=len(out) + k) for k in range(n)]
        return out

    def test_has_room(self):
        from bonobo import bag
        rows = [("a free slot", self.bag_of(("cobblestone", 64, 35)), 1, True),
                ("full, but a stack of it has room (63)", self.bag_of(("cobblestone", 64, 35), ("cobblestone", 63, 1)), 0,
                 True),
                ("full, every stack of it at 64", self.bag_of(("cobblestone", 64, 36)), 0, False),
                ("full, only other items have room", self.bag_of(("cobblestone", 64, 35), ("dirt", 10, 1)), 0, False)]
        for name, slots, free, want in rows:
            with self.subTest(name):
                self.assertEqual(bag.has_room(slots, free, {self.COBBLE}), want)

    def test_bag_full_reason(self):
        from bonobo.skill import bag_full_reason
        rows = [("no free slot: the cause is named", "chop: finished without reaching its goal", 0,
                 "bag full (no free slot): chop: finished without reaching its goal"),
                ("room left: the failure is its own", "no trees", 3, None),
                ("already says it", "bag full: nothing picked up", 0, None),
                ("the bag could not be read", "no trees", None, None)]
        for name, message, free, want in rows:
            with self.subTest(name):
                self.assertEqual(bag_full_reason(message, free), want)

    # Seconds to get one again, as cost.Prices answers (a fixture: the planner's price for each).
    PRICE = {"minecraft:dirt": 2.0, "minecraft:diamond": 600.0, "minecraft:iron_ingot": 90.0,
             "minecraft:cobblestone": 3.0, "minecraft:rotten_flesh": None, "minecraft:oak_log": 8.0}

    def test_let_go_over_the_table(self):
        """bag.let_go: the cheapest to get again goes first; what plans, the upkeep floor or working tools need never;
        a stack worth more than the walk to a chest is stored; with lava near nothing is dropped."""
        from bonobo import bag
        price = self.PRICE.get
        dead_pick = [dict(self.bag_of(("iron_pickaxe", 1, 1))[0], damage=249, maxDamage=250, slot=9)]
        rows = [("dirt beyond the 64 kept, before diamonds", self.bag_of(("diamond", 5, 1), ("dirt", 64, 2)), 1, {},
                 [("dirt", "drop")]),
                ("junk nothing prices goes first", self.bag_of(("rotten_flesh", 10, 1), ("dirt", 64, 1)), 1, {},
                 [("rotten_flesh", "drop")]),
                ("the floor is kept: 64 blocks, 8 meals", self.bag_of(("cobblestone", 64, 1), ("cooked_beef", 8, 1),
                                                                      ("dirt", 30, 1)), 1, {}, [("dirt", "drop")]),
                ("beyond the floor: the smaller surplus stack first",
                 self.bag_of(("cobblestone", 64, 2), ("cobblestone", 10, 1)), 1, {}, [("cobblestone", "drop")]),
                ("a chest 200 s away: diamonds (3000 s to get again) stored, dirt (128 s) dropped",
                 self.bag_of(("diamond", 5, 1), ("dirt", 64, 2)), 2, {"chest_s": 200.0},
                 [("dirt", "drop"), ("diamond", "deposit")]),
                ("lava near, no chest: nothing dropped", self.bag_of(("dirt", 64, 2)), 1, {"lava_near": True},
                 "every stack is needed"),
                ("a dead pickaxe goes whatever", self.bag_of(("diamond", 5, 1)) + dead_pick, 1, {},
                 [("iron_pickaxe", "drop")]),
                ("everything needed: said so", self.bag_of(("cooked_beef", 8, 1), ("torch", 16, 1)), 1, {},
                 "every stack is needed")]
        for name, slots, need, kw, want in rows:
            with self.subTest(name):
                if isinstance(want, str):
                    with self.assertRaisesRegex(api.NotAvailable, want):
                        bag.let_go(slots, need, price, **kw)
                else:
                    got = bag.let_go(slots, need, price, **kw)
                    self.assertEqual([(st["id"].split(":")[1], how) for st, how in got], want)

    def test_plans_keep_what_they_use(self):
        from bonobo import bag
        slots = self.bag_of(("oak_planks", 60, 3), ("dirt", 64, 1))
        with mock.patch.object(bag, "RESERVED", {"minecraft:oak_planks"}):
            got = bag.let_go(slots, 3, self.PRICE.get)
        # one plank stack stays for the plan, the 64 dirt are the block floor: two stacks can go, not three
        self.assertEqual(sorted(st["id"] for st, _ in got), ["minecraft:oak_planks", "minecraft:oak_planks"])
