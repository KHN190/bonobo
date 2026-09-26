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


class Arrive(_Clean):
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
# exception → (cause, class). "interrupted" never counts, bans, stops or cools; "waits" is handled before the
# failure path (brain.attempt); "failure" is counted for (task, cause) and cools the cause at the place.
OUTCOMES = [
    (api.Interrupted("perception: lava"), "interrupt", "interrupted"),
    (api.BodyContested("another commander posted a task"), "interrupt", "interrupted"),
    (api.PlayerTookControl(), "interrupt", "interrupted"),
    (api.CommitmentExpired("a faster layer took the body"), "replan", "interrupted"),
    (api.GameUnreachable("game not reachable (connection refused)"), "game", "waits"),
    (skillcore.ToolMissing("pickaxe", 1), "tool", "failure"),
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


class Outcomes(unittest.TestCase):
    def test_every_exception_maps_to_one_cause_and_one_class(self):
        for err, cause, cls in OUTCOMES:
            with self.subTest(f"{type(err).__name__}: {err}"):
                self.assertEqual(retry.cause_of(err), cause)
                self.assertEqual(api.interrupted(err), cls == "interrupted")
                self.assertEqual(cause in retry.NOT_FAILURES, cls == "interrupted")
                verdict = retry.Retry().failed("task t1", cause, str(err), now=100.0, place=((0, 4, 0), False))
                self.assertEqual(verdict is None, cls == "interrupted", "only failures are counted")

    def test_no_exception_type_is_unclassified(self):
        covered = {type(err) for err, _, _ in OUTCOMES}
        self.assertEqual(exception_types() - covered, set(),
                         "a new exception type: add its row to OUTCOMES (cause, class)")

    def test_the_interruptions_are_exactly_the_interrupted_rows(self):
        self.assertEqual(set(api.INTERRUPTIONS), {type(e) for e, _, c in OUTCOMES if c == "interrupted"})

    def test_outcome_of(self):
        """What the attempt does about each: interruptions never fail (and some need a hand back or a wait)."""
        special = {api.PlayerTookControl: ("interrupted", "handback"), api.GameUnreachable: ("interrupted", "wait_game"),
                   api.BodyContested: ("interrupted", "stand_down")}
        rows = [(err, special.get(type(err), ("interrupted", None) if cls == "interrupted" else ("failed", "stop")))
                for err, _cause, cls in OUTCOMES]
        rows += [(None, ("ok", None)), (ValueError("a bug of ours"), ("failed", "crash"))]
        for err, want in rows:
            with self.subTest(repr(err)):
                self.assertEqual(brain.outcome_of(err), want)
        import inspect
        self.assertIn("outcome_of(", inspect.getsource(brain.Brain.attempt), "the attempt must ask this table")


# ------------------------------------------------------------------------------------------------ free spots
def shaft():
    """The body at the bottom of a 1×1 shaft three deep in solid stone."""
    r = world()
    for y in (61, 62, 63):
        r.blocks.pop((0, y, 0), None)
    return r, state(x=0.5, y=61.0, z=0.5)


def lava_floor():
    r = world()
    for (x, y, z), n in list(r.blocks.items()):
        if y == 63 and (x, z) != (0, 0):
            r.blocks[(x, y, z)] = "lava"
    return r


RING2 = {(x, 64, z) for x in range(-2, 3) for z in range(-2, 3) if max(abs(x), abs(z)) == 2}
# (situation, region, /state, keywords) → check(spots)
SPOTS = [
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
            for name, region, st, kw, check in SPOTS:
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
                runner = skillkit.skill(**kw)(fn)
                if isinstance(want, type):
                    with self.assertRaises(want):
                        runner(None)
                else:
                    self.assertEqual(runner(None), want)
                self.assertEqual(bool(calls), ran)
                self.assertEqual(stats.rows if timed not in (True, False) else bool(stats.rows), timed)

    def test_can_run_asks_the_same_preconditions(self):
        for pre, want in (([], (True, None)), ([_missing_pick], (False, "need a tier-1 pickaxe"))):
            with self.subTest(pre=pre), mock.patch.dict(skillkit.REGISTRY):
                runner = skillkit.skill(name=f"can_run_{len(pre)}", pre=pre)(lambda ctx: None)
                self.assertEqual(skillkit.can_run(runner, None), want)

    def test_step_keys_most_specific_first(self):
        from bonobo.planner import Step
        for kind, token, want in (("mine", "minecraft:coal", ["mine:minecraft:coal", "item:minecraft:coal", "mine"]),
                                  ("gather", "log", ["gather:log", "item:log", "gather"]),
                                  ("shelter", "dig in", ["shelter:dig in", "item:dig in", "shelter"])):
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
                    skillkit.skill(name=pname, provides={effect: lambda ctx, s, _g=got: _g}, prefer=prefer)(
                        lambda ctx, *a: None)
                found = skillkit.provider(None, Step("zz", "tok", 1))
                self.assertEqual(None if found is None else (found[0].contract.name, found[1]), want)
                self.assertEqual(skillkit.handles(Step("zz", "tok", 1)), True)


# ----------------------------------------------------------------------------------------------------- commands
FEET = (0, 64, 0)


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


SPOT = {"x": 1, "y": 64, "z": 1}

# skill → [(situation, state dict, expected)]; expected is an exception type or a check(batch) -> None (asserts).
COMMANDS = {
    "dig_in": [
        ("flat stone, blocks carried: three down and a lid", body(world(), inv=inventory(cobblestone=16)),
         lambda t, b: (t.assertEqual(types(b), ["mine", "wait"] * 3 + ["place"]),
                       t.assertEqual(cells(b), [(0, 63, 0)]))),
        ("no blocks: three down, no lid", body(world()), lambda t, b: t.assertEqual(types(b), ["mine", "wait"] * 3)),
        ("lava beside the second cell: stops at one", body(world(((1, 62, 0), "lava")), inv=inventory(cobblestone=4)),
         lambda t, b: (t.assertEqual(types(b), ["mine", "wait", "place"]), t.assertEqual(cells(b), [(0, 65, 0)]))),
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
        st = body(region, feet=blueprints.access_spot(bp, origin, turns), inv=carried, rules={})
        missing = {pos for pos, _, _, _ in parts} - {pos for pos, _, _, _ in done}
        check = _blueprint_check(missing, progress == "canopy", st)
        label = f"{name} turns={turns} {progress}"
        if name == "shelter":
            out["build_shelter"].append((label, dict(st, spot=(origin, turns)), check))
        out["build_blueprint"].append((label, dict(st, started={"origin": list(origin), "turns": turns},
                                                   _args=(name, origin)), check))
        if turns == 0 and progress == "bare":
            out["build_blueprint"].append((f"{name}: nothing started yet", dict(st, _args=(name, origin)),
                                           lambda t, b: t.assertEqual(b, [])))
    return out


def _blueprint_check(missing, canopy, st):
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
    return check


for _skill, _rows in blueprint_cases().items():
    COMMANDS.setdefault(_skill, []).extend(_rows)


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
        for name, c in skillkit.REGISTRY.items():
            if c.commands is None:
                with self.subTest(name):
                    self.assertIsNone(skillkit.commands_of(c.runner, {}))


if __name__ == "__main__":
    unittest.main()
