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
from bonobo import api, arbiter, blueprints, brain, nav, retry, skillcore, tape  # noqa: E402,F401  (brain: every skill module)
from bonobo import skill as skillkit  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402
from bonobo.knowledge import members  # noqa: E402
from tests.world import FakeRegion, bag, flat, inventory, state  # noqa: E402

FAST = dict(timeout=0.25, stable_s=0.03, poll=0.001)      # the same rule, on a millisecond clock


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
    ("respawn reports dead for three frames, then alive", [True, True, True, False], bool, {}, False),
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
                args = dict(FAST, **kw)
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
                skillcore.settle(reader([3, 3], interrupt_at=0), GAINED, **FAST)

    def test_gained_and_lost_are_settle(self):
        for fn, seq, want in ((skillcore.gained, [3, 5], 5), (skillcore.lost, [3, 1], 1)):
            with self.subTest(fn.__name__):
                self.assertEqual(fn(reader(seq), BEFORE, timeout=0.2, stable_s=0.01), want)

    def test_one_alive_reading_is_not_a_death(self):
        self.assertFalse(skillcore.dead(state(dead=False)))


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
    (api.GameUnreachable("game not reachable"), "error", "waits"),
    (skillcore.ToolMissing("pickaxe", 1), "tool", "failure"),
    (api.NavFailed("could not get to (1, 2, 3)"), "nav", "failure"),
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

    def test_waiting_is_caught_before_failing(self):
        """GameUnreachable is an McError, so its cause reads "error": it must never reach the failure path. The order
        of `Brain.attempt`'s handlers is that guarantee."""
        import inspect
        src = inspect.getsource(brain.Brain.attempt)
        for first in ("except PlayerTookControl", "except GameUnreachable", "except api.INTERRUPTIONS"):
            self.assertLess(src.index(first), src.index("except (McError"), first)


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
    ],
    "pod": [
        ("open ground: eight walls, a cap, the roof", body(world(), inv=inventory(cobblestone=16)),
         lambda t, b: (t.assertEqual(set(types(b)), {"place"}), t.assertEqual(len(b), 10),
                       t.assertEqual(cells(b)[-1], (0, 66, 0)))),
        ("too few blocks", body(world(), inv=inventory(cobblestone=3)), NotAvailable),
        ("grass in a wall cell is broken first", body(world(((1, 64, 0), "short_grass")), inv=inventory(cobblestone=16)),
         lambda t, b: t.assertLess(types(b).index("mine"), cells(b).index((1, 64, 0)))),
        ("already walled in", body(hole(), inv=inventory(cobblestone=16)), lambda t, b: t.assertEqual(b, [])),
        ("on a pillar in deep water: supports rise from below first", body(river(), inv=inventory(cobblestone=64)),
         lambda t, b: (t.assertEqual(set(types(b)), {"place"}), t.assertGreater(len(b), 10),
                       t.assertTrue(any(y < 64 for _, y, _ in cells(b))))),
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
    "place_torch_if_dark": [
        ("dark cave, a torch, a spot in reach", dict(body(inv=inventory(torch=4)), state=dark(), spots=[SPOT]),
         lambda t, b: t.assertEqual(b, [{"type": "place", "item": "minecraft:torch", "x": 1, "y": 64, "z": 1}])),
        ("lit already", dict(body(inv=inventory(torch=4)), state=dark(blockLight=9), spots=[SPOT]),
         lambda t, b: t.assertEqual(b, [])),
        ("daylight on the surface", dict(body(inv=inventory(torch=4)), state=dark(skyLight=15), spots=[SPOT]),
         lambda t, b: t.assertEqual(b, [])),
        ("night on the surface", dict(body(inv=inventory(torch=4)), state=dark(skyLight=15, timeOfDay=18000),
                                      spots=[SPOT]), lambda t, b: t.assertEqual(len(b), 1)),
        ("the only torch is in the offhand", dict(body(inv=inventory(offhand="torch")), state=dark(), spots=[SPOT]),
         lambda t, b: t.assertEqual(b, [])),
        ("the only dark spot is where we stand", dict(body(inv=inventory(torch=4)), state=dark(),
                                                      spots=[{"x": 0, "y": 64, "z": 0}]),
         lambda t, b: t.assertEqual(b, [])),
        ("the dark spot is out of reach", dict(body(inv=inventory(torch=4)), state=dark(),
                                               spots=[{"x": 5, "y": 64, "z": 0}]), lambda t, b: t.assertEqual(b, [])),
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
