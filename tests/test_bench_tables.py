"""The bench as tables (bonobo/bench/bench_<tier>.py: families of rows made by a template from their parameters,
one-off rows in words, one-off rows in code), proven against the old sheet offline: the old sheet's 271 rows were
recorded (tests/fixtures/bench_rows.json: every plain field, the check's words, the escape rows' seeds) before
scenarios.py was deleted, and every row built now must equal its record. Names are unique, the tier rules hold,
and every word says yes and no on recorded data."""
import importlib
import json
import math
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.bench import core, runner, table, vocab  # noqa: E402
from bonobo.bench import table as sc  # noqa: E402  (the sheet: its helpers and its one SCENARIOS)
from tests import bench_words as words  # noqa: E402

CALLABLE_KEYS = ("run", "check", "before", "detail")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bench_rows.json")


def recorded():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def tables():
    return {t: table.rows(t) for t in table.TIERS}


def check_words(check):
    """A check's words, read back from its callables; None when no word reads them."""
    try:
        return words.enc(check)
    except words.NotExpressible:
        return None


def as_json(v):
    return json.loads(json.dumps(v, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else repr(o)))


def canonical(v):
    """A recorded word naming the fight helpers' old home (bench/fight.py) names the same helper in vocab now."""
    if isinstance(v, str) and v[:1] in "!&" and "bonobo.bench.fight:" in v:
        return v[0] + v.split(":", 1)[1]
    if isinstance(v, list):
        return [canonical(x) for x in v]
    if isinstance(v, dict):
        return {k: canonical(x) for k, x in v.items()}
    return v


def record_of(name, row):
    """A built row as the fixture records one: its plain fields, its check's words."""
    out = {k: as_json(v) for k, v in row.items() if k not in CALLABLE_KEYS}
    w = check_words(row["check"])
    if w is not None:
        out["check_words"] = as_json(w)
    return out


def built(tier, name, row, seed=None):
    if name.startswith("escape__"):              # the same cell, the recorded seed
        row = vocab.escape_row(name, *name[8:].split("_", 2), seed=seed)
    return table.build(row, tier)


class Equivalence(unittest.TestCase):
    """The sweep: each row built from the tables is its recorded old row."""

    def test_every_row_is_its_record(self):
        rec = recorded()
        self.assertEqual(len(rec), 276)          # + fight_enderman_provoked; + the taught door and its untaught twin; + hatch in/out, side room
        for tier, rows in tables().items():
            for name, row in rows.items():
                with self.subTest(name):
                    want = canonical(dict(rec[name]))
                    seed = want.pop("seed", None)
                    got = record_of(name, built(tier, name, row, seed))
                    if "check_words" not in want:
                        got.pop("check_words", None)     # the old check was code no word read: the fields stand
                    self.assertEqual(got, want)

    def test_every_recorded_row_is_built(self):
        self.assertEqual(sorted(set(recorded()) ^ set(sc.SCENARIOS)), [])
        for name, want in recorded().items():
            with self.subTest(name):
                self.assertEqual(sc.SCENARIOS[name]["tier"], want["tier"])

    def test_a_changed_row_is_caught(self):
        # must fail: a scene, budget, queue, tier or check off by one reads as another row
        name, row = "iron_ingots", table.rows("core")["iron_ingots"]
        want = recorded()[name]
        rows = [("scene: one block moved", dict(row, scene=[("floor", "stone", 8, 5)] + row["scene"][1:])),
                ("budget one less", dict(row, budget=row["budget"] - 1)),
                ("a queue where there was none", dict(row, queue=[{"goal": "have", "args": {"needs": [["log", 1]]}}])),
                ("another check", dict(row, check=[("count", "minecraft:iron_ingot", ">=", 2)]))]
        for why, changed in rows:
            with self.subTest(why):
                self.assertNotEqual(record_of(name, table.build(changed, "core")), want)
        with self.subTest("another tier"):
            self.assertNotEqual(record_of(name, table.build(row, "exception")), want)

    def test_a_family_entry_makes_many_rows(self):
        mod = importlib.import_module(table.TABLES["exception"])
        made = sum(len(params) for _t, params in mod.FAMILIES)
        self.assertGreater(made, len(mod.FAMILIES))
        self.assertEqual(len(table.rows("exception")), made + len(mod.ROWS) + len(mod.CODE_ROWS))


def words_of(v, out):
    """Every word a row's data names for `resolve` at run time: "&word", ("$call", word, ...), ("do", word, ...),
    ("call"/"!call", word, ...)."""
    if isinstance(v, str) and v.startswith("&"):
        out.add(v[1:])
    elif isinstance(v, tuple) and v and isinstance(v[0], str):
        if v[0] in ("$call", "do", "call", "!call") and len(v) > 1 and isinstance(v[1], str):
            out.add(v[1])
        for x in v:
            words_of(x, out)
    elif isinstance(v, (list, tuple)):
        for x in v:
            words_of(x, out)
    elif isinstance(v, dict):
        for x in v.values():
            words_of(x, out)
    return out


class Words(unittest.TestCase):
    """A word names one function: a bare `name` beside an older `_name` is refused, never silently preferred (the
    eat base's `hungry` shadowed the drain `_hungry` two rows meant)."""

    def test_every_word_the_tables_use_resolves_to_one(self):
        used = set()
        for rows in tables().values():
            for row in rows.values():
                words_of(row, used)
        self.assertGreater(len(used), 20)
        for w in sorted(used):
            with self.subTest(w):
                vocab.resolve(w)

    def test_an_ambiguous_word_is_refused(self):
        f, g = (lambda: 1), (lambda: 2)
        rows = [("bare only", {"t_w": f}, "t_w", f),
                ("underscored only, asked bare", {"_t_w": g}, "t_w", g),
                ("underscored, asked by its full name", {"t_w": f, "_t_w": g}, "_t_w", g),
                ("must fail: both defined, asked bare", {"t_w": f, "_t_w": g}, "t_w", KeyError),
                ("must fail: neither defined", {}, "t_w", KeyError)]
        for name, defs, word, want in rows:
            with self.subTest(name), mock.patch.dict(vocab.REGISTRY, defs):
                if want is KeyError:
                    with self.assertRaises(KeyError) as e:
                        vocab.resolve(word)
                    if defs:
                        self.assertIn("_t_w", str(e.exception))
                else:
                    self.assertIs(vocab.resolve(word), want)


class Coverage(unittest.TestCase):
    def test_word_rows_hold_no_code(self):
        def plain(v):
            if isinstance(v, (list, tuple)):
                return all(plain(x) for x in v)
            if isinstance(v, dict):
                return all(isinstance(k, str) and plain(x) for k, x in v.items())
            return v is None or isinstance(v, (bool, int, float, str))
        from bonobo.bench import bench_bases
        for t in table.TIERS:
            mod = importlib.import_module(table.TABLES[t])
            with self.subTest(t):
                self.assertTrue(plain(mod.FAMILIES) and plain(mod.ROWS))
        for data in (bench_bases.BASES, bench_bases.CONDITIONS, bench_bases.SURPRISES, bench_bases.KIT):
            self.assertTrue(plain(data))
        self.assertFalse(plain({"check": [lambda api, inv: True]}))      # must fail: a lambda is code

    def test_code_rows_are_few(self):
        # the rows no word earns its place for: 24 when the tables were made; may only fall
        def code(t):          # a table may build its one-off rows lazily (bench_combat: when the sheet is built)
            rows = getattr(importlib.import_module(table.TABLES[t]), "CODE_ROWS", ())
            return rows() if callable(rows) else rows
        n = sum(len(code(t)) for t in table.TIERS)
        self.assertLessEqual(n, 24)


HOSTILE = re.compile(r"\b(zombie|skeleton|creeper|blaze|ghast|spider|cave_spider|enderman|witch|slime|magma_cube|"
                     r"wither|pillager|husk|drowned|stray|phantom|hoglin|vindicator|piglin_brute)\b")


def fights(setup, row):
    """A row that fights: a hostile summoned in its setup or its words, or the dragon slain."""
    text = " ".join(map(str, setup)) + repr(row.get("before", ())) + repr(row.get("run", ()))
    summons = re.findall(r"summon[ ',]+(?:minecraft:)?(\w+)", text)
    return any(HOSTILE.fullmatch(m) for m in summons) or "slay_dragon" in repr(row.get("run", ()))


class TierRules(unittest.TestCase):
    EXEMPT = {"resume_after_combat"}      # the user's call (see test_scenario_sheet)

    def test_names_unique(self):
        names = [n for rows in tables().values() for n in rows]
        self.assertEqual(len(names), len(set(names)))
        for t in table.TIERS:
            mod = importlib.import_module(table.TABLES[t])
            listed = [r["name"] for r in mod.ROWS] + [vocab.NAMES[tm](*p) for tm, ps in mod.FAMILIES for p in ps]
            self.assertEqual(len(listed), len(set(listed)), t)

    def test_budgets_within_the_limit(self):
        for tier, rows in tables().items():
            for name, row in rows.items():
                if tier != "acceptance":
                    with self.subTest(name):
                        self.assertLessEqual(table.build(row, tier)["budget"], runner.ROW_LIMIT_S)
                        self.assertLessEqual(row["budget"], runner.ROW_LIMIT_S)
        # must fail: a row over the limit is cut to it by the interpreter, not kept
        over = dict(table.rows("core")["iron_ingots"], budget=runner.ROW_LIMIT_S + 5)
        self.assertEqual(table.build(over, "core")["budget"], runner.ROW_LIMIT_S)

    def test_fight_rows_only_in_combat(self):
        out = sorted(n for t, rows in tables().items() for n, r in rows.items()
                     if t != "combat" and n not in self.EXEMPT and fights(vocab.scene(r["scene"]) if "scene" in r else r["setup"], r))
        self.assertEqual(out, [])

    def test_the_fight_check_sees_a_fight(self):
        rows = [("a zombie in the scene", vocab.scene([("summon", "zombie", ("@", 1, 0, 0))]), {}, True),
                ("a ghast in a hook", [], {"before": [("do", "chat", ["summon ghast 1 2 3"], {})]}, True),
                ("the dragon slain", [], {"run": ("do", "bonobo.end.slay_dragon", ["$ctx"], {})}, True),
                ("must fail: cows only", vocab.scene([("pen", "cow", 3)]), {}, False)]
        for why, setup, row, want in rows:
            with self.subTest(why):
                self.assertEqual(fights(setup, row), want)


# -- words on recorded data ---------------------------------------------------------------------------------------
from bonobo.bench.vocab import TREK_RANGE  # noqa: E402
STATE = {"x": 10000.5, "y": 200.0, "z": 10000.5, "blockX": 10000, "blockY": 200, "blockZ": 10000, "health": 18.0,
         "dead": False, "food": 20, "timeOfDay": 1000, "dimension": "minecraft:overworld", "onGround": True,
         "inWater": False, "air": 300}


def bag(*items, **extra):
    return {"slots": [{"id": i if ":" in i else "minecraft:" + i, "count": n, "slot": k, **extra.get(i, {})}
                      for k, (i, n) in enumerate(items)], "equipment": {}}


class Api:
    def __init__(self, state=None, inv=None):
        self.state, self.inv = dict(STATE, **(state or {})), inv or bag()

    def get(self, path):
        return {"/state": self.state, "/inventory": self.inv}[path]


class FakeRegion:
    BLOCKS = {}

    def __init__(self, lo, hi, props=False):
        self.blocks = {p: n for p, n in self.BLOCKS.items()
                       if all(min(lo[i], hi[i]) <= p[i] <= max(lo[i], hi[i]) for i in range(3))}

    def name(self, p):
        return self.BLOCKS.get(tuple(p), "air")

    def solid(self, p):
        return self.name(p) != "air"

    def prop(self, p, key):
        return None               # no block states recorded: a door cell is shut by being solid


A0 = ("@", 0, 0, 0)
LOG2 = bag(("oak_log", 2))
# (why, word, state, bag, sheet globals {name: dict}, want). Each word has a yes row and a no row.
PRED_ROWS = [
    ("count yes", ("count", "log", ">=", 2), None, LOG2, {}, True),
    ("must fail: count no", ("count", "log", ">=", 3), None, LOG2, {}, False),
    ("state compared yes", ("state", "dimension", "==", "minecraft:overworld"), None, None, {}, True),
    ("state compared no", ("state", "health", ">=", 19), None, None, {}, False),
    ("state truth yes", ("state", "onGround"), None, None, {}, True),
    ("state truth no", ("state", "dead"), None, None, {}, False),
    ("bag reading yes", ("bag", "used_slots", [], "<", 34), None, LOG2, {}, True),
    ("bag reading no", ("bag", "used_slots", [], "<", 1), None, LOG2, {}, False),
    ("call yes: the trek ended in its range", ("call", "trek_check", ["$api"]), None, None,
     {"TREK": {"end": (10000.5, 200.0, 10000.5), "target": (10000 + TREK_RANGE, 200, 10000)}}, True),
    ("must fail: call no: the trek ended short of its range", ("call", "trek_check", ["$api"]), None, None,
     {"TREK": {"end": (10000.5, 200.0, 10000.5), "target": (10000 + TREK_RANGE + 1, 200, 10000)}}, False),
    ("all yes", ("all", ("!state", "onGround"), ("!count", "log", ">=", 1)), None, LOG2, {}, True),
    ("all no", ("all", ("!state", "onGround"), ("!count", "log", ">=", 5)), None, LOG2, {}, False),
    ("any yes", ("any", ("!state", "dead"), ("!count", "log", ">=", 1)), None, LOG2, {}, True),
    ("any no", ("any", ("!state", "dead"), ("!count", "log", ">=", 5)), None, LOG2, {}, False),
    # the fight's own record (FIGHT_LOG) and the bag's offhand: the combat rows' words
    # bids as the live wrapper records them: (when, engaged, the answer's kind)
    ("decision gaps ok yes: engaged bids 0.1 s apart", ("decision_gaps_ok",), None, None,
     {"FIGHT_LOG": {"bids": [(0.0, False, None), (3.0, True, "fight"), (3.1, True, "fight"), (3.2, True, "fight")]}},
     True),
    ("must fail: decision gaps ok no — a 1 s gap while engaged", ("decision_gaps_ok",), None, None,
     {"FIGHT_LOG": {"bids": [(0.0, True, "fight"), (1.0, True, "fight")]}}, False),
    ("must fail: decision gaps ok no — never engaged", ("decision_gaps_ok",), None, None,
     {"FIGHT_LOG": {"bids": [(0.0, False, None), (0.1, False, None)]}}, False),
    # the jar's task through the fight: (when, task running?) against engaged bids and a mob 2 blocks off
    ("no stall yes: a task running all the time", ("no_stall",), None, None,
     {"FIGHT_LOG": {"bids": [(0.0, True, "fight")], "alive": [(0.0, [(7, 20.0, 2.0)])],
                    "trace": [{"t": 0.0, "task": {"id": 1}}, {"t": 0.2, "task": {"id": 1}}, {"t": 0.4, "task": {"id": 2}}]}},
     True),
    ("must fail: no stall no — a task ended and the body stood 0.4 s before the next", ("no_stall",), None, None,
     {"FIGHT_LOG": {"bids": [(0.0, True, "fight")], "alive": [(0.0, [(7, 20.0, 2.0)])],
                    "trace": [{"t": 0.0, "task": {"id": 1}}, {"t": 0.2, "task": None}, {"t": 0.4, "task": None},
                              {"t": 0.6, "task": None}, {"t": 0.8, "task": {"id": 2}}]}}, False),
    ("kills by the fight yes", ("kills_by_the_fight", 1), None, None,
     {"FIGHT_LOG": {"bids": [(0.5, True, "fight"), (1.5, True, "fight")],
                    "alive": [(0.0, [(7, 20.0, 2.0)]), (1.0, [(7, 6.0, 2.0)]), (2.0, [])]}}, True),
    ("must fail: kills by the fight no — the mob vanished at full health 10 off", ("kills_by_the_fight", 1), None, None,
     {"FIGHT_LOG": {"bids": [(0.5, True, "fight")], "alive": [(0.0, [(7, 20.0, 10.0)]), (1.0, [])]}}, False),
    ("answered with yes", ("answered_with", "reshape", "wall_in"), None, None,
     {"FIGHT_LOG": {"bids": [(0.0, True, "reshape")]}}, True),
    ("must fail: answered with no — swung, never reshaped", ("answered_with", "reshape", "wall_in"),
     None, None, {"FIGHT_LOG": {"bids": [(0.0, True, "fight")]}}, False),
    ("shield kept yes", ("shield_kept",), None,
     {"slots": [], "equipment": {"offhand": {"id": "minecraft:shield", "count": 1}}}, {}, True),
    ("must fail: shield kept no — the offhand empty", ("shield_kept",), None, bag(), {}, False),
    ("not yes", ("not", ("!state", "dead")), None, None, {}, True),
    ("not no", ("not", ("!state", "onGround")), None, None, {}, False),
    ("constant yes", ("constant", True), None, None, {}, True),
    ("constant no", ("constant", False), None, None, {}, False),
    ("gain yes", ("gain", "log", 2), None, LOG2, {"BASE": {"inv": bag()}}, True),
    ("gain no", ("gain", "log", 2), None, LOG2, {"BASE": {"inv": bag(("oak_log", 1))}}, False),
    ("gain at most no", ("gain", "log", 1, 1), None, LOG2, {"BASE": {"inv": bag()}}, False),
    ("kept yes", ("kept", "minecraft:diamond"), None, bag(("diamond", 5)), {"BASE": {"inv": bag(("diamond", 5))}}, True),
    ("kept no", ("kept", "minecraft:diamond"), None, bag(("diamond", 4)), {"BASE": {"inv": bag(("diamond", 5))}}, False),
    ("alive yes", ("alive", 10), None, None, {}, True),
    ("alive no: dead", ("alive", 10), {"dead": True}, None, {}, False),
    ("arrived yes", ("arrived", A0, 2), None, None, {}, True),
    ("must fail: arrived no — in the air over the cell", ("arrived", A0, 2), {"onGround": False}, None, {}, False),
    ("must fail: arrived no — a range's slack short", ("arrived", ("@", 3, 0, 0), 2), None, None, {}, False),
    ("is day yes", ("is_day",), None, None, {}, True),
    ("is day no", ("is_day",), {"timeOfDay": 18000}, None, {}, False),
    ("free slots yes", ("free_slots", 2), None, LOG2, {}, True),
    ("free slots no", ("free_slots", 36), None, LOG2, {}, False),
    ("same bag yes", ("same_bag",), None, LOG2, {"BASE": {"inv": LOG2}}, True),
    ("same bag no", ("same_bag",), None, LOG2, {"BASE": {"inv": bag()}}, False),
    ("same bag and place yes", ("same_bag_and_place",), None, LOG2, {"BASE": {"inv": LOG2, "state": STATE}}, True),
    ("same bag and place no: walked", ("same_bag_and_place",), {"x": 10009.5}, LOG2,
     {"BASE": {"inv": LOG2, "state": STATE}}, False),
    ("hp kept yes", ("hp_kept", 16), None, None, {}, True),
    ("hp kept no", ("hp_kept", 20), None, None, {}, False),
    ("rose yes", ("rose", "health"), {"health": 19.0}, None, {"BASE": {"health_before": 18.0}}, True),
    ("must fail: rose no — where it stood", ("rose", "health"), {"health": 18.0}, None,
     {"BASE": {"health_before": 18.0}}, False),
    ("regen fed yes", ("&regen_fed",), {"food": 18}, bag(("bread", 3)), {}, True),
    ("must fail: regen fed no — no bread eaten", ("&regen_fed",), {"food": 18}, bag(("bread", 4)), {}, False),
    ("slot has yes", ("slot_has", "minecraft:iron_pickaxe", "efficiency"), None,
     bag(("iron_pickaxe", 1), iron_pickaxe={"enchantments": {"efficiency": 1}}), {}, True),
    ("slot has no", ("slot_has", "minecraft:iron_pickaxe", "efficiency"), None, bag(("iron_pickaxe", 1)), {}, False),
    ("breathing yes", ("breathing",), None, None, {}, True),
    ("breathing no", ("breathing",), {"air": 100}, None, {}, False),
    ("not (sheet) yes", ("_not", ("!state", "dead")), None, None, {}, True),
    ("not (sheet) no", ("_not", ("!state", "onGround")), None, None, {}, False),
    ("before in bag yes", ("before_in_bag", "bed", "log"), None, None, {"FIRST": {"bed": 1.0, "log": 2.0}}, True),
    ("before in bag no", ("before_in_bag", "bed", "log"), None, None, {"FIRST": {"bed": 3.0, "log": 2.0}}, False),
    ("interrupted yes", ("interrupted",), None, None, {"BASE": {"name": "r"}, "INTERRUPTS": {"r": 1}}, True),
    ("interrupted no", ("interrupted",), None, None, {"BASE": {"name": "r"}, "INTERRUPTS": {"r": 0}}, False),
    ("failed as expected yes", ("failed_as_expected",), None, None,
     {"BASE": {"name": "r"}, "FAILED_AS_EXPECTED": {"r": "no pickaxe"}}, True),
    ("failed as expected no", ("failed_as_expected",), None, None, {"BASE": {"name": "r"}, "FAILED_AS_EXPECTED": {}},
     False),
    ("replans at most yes", ("replans_at_most", 2), None, None, {"BRAIN_LOG": {"replans": 2}}, True),
    ("replans at most no", ("replans_at_most", 2), None, None, {"BRAIN_LOG": {"replans": 3}}, False),
    ("sweep check yes: rows, no rule broken", ("bonobo.bench.core:_sweep_check", "w", "/nonexistent", [], 1), None,
     None, {"SWEEP": {"w": [{"cell": 1}]}}, True),
    ("sweep check no: no rows", ("bonobo.bench.core:_sweep_check", "w", "/nonexistent", [], 1), None, None,
     {"SWEEP": {}}, False),
    ("thunk yes", ("thunk", ("!constant", True)), None, None, {}, True),
    ("thunk no", ("thunk", ("!constant", False)), None, None, {}, False),
    ("api only yes", ("api_only", ("!arrived", A0, 2)), None, None, {}, True),
    ("api only no", ("api_only", ("!arrived", ("@", 8, 0, 0), 2)), None, None, {}, False),
    ("no scan yes", ("no_scan",), None, None, {"FINDS": {"diamond": 0}}, True),
    ("no scan no", ("no_scan",), None, None, {"FINDS": {"diamond": 2}}, False),
]
# world readers: the recorded blocks (FakeRegion) and entities
# a 3×3×3 shell (inner x 1, z 1, y 0..1 open) with a door in its west face: unchanged's scene
DOOR = [("@", 0, 0, 1), ("@", 0, 1, 1)]
DOOR_CELLS = [(10000 + x, 200 + y, 10000 + z) for _at, x, y, z in DOOR]     # A0 is (10000, 200, 10000)
SHELL = {(10000 + x, 200 + y, 10000 + z): ("iron_door" if (x, z) == (0, 1) and y < 2 else "stone")
         for x in range(3) for y in range(3) for z in range(3) if not (x == 1 and z == 1 and y < 2)}
WORLD_ROWS = [
    ("blocks yes", ("blocks", A0, ("@", 2, 0, 0), "torch", 2), {(10000, 200, 10000): "torch", (10001, 200, 10000): "torch"},
     [], True),
    ("must fail: blocks no: too few", ("blocks", A0, ("@", 2, 0, 0), "torch", 3), {(10000, 200, 10000): "torch"}, [], False),
    ("no block suffix yes", ("no_block_suffix", A0, ("@", 2, 0, 0), "_bed"), {(10000, 200, 10000): "stone"}, [], True),
    ("no block suffix no", ("no_block_suffix", A0, ("@", 2, 0, 0), "_bed"), {(10000, 200, 10000): "red_bed"}, [],
     False),
    ("under feet yes", ("under_feet", "cobblestone"), {(10000, 199, 10000): "cobblestone"}, [], True),
    ("under feet no", ("under_feet", "cobblestone"), {(10000, 199, 10000): "dirt"}, [], False),
    ("mobs near yes", ("mobs_near", "minecraft:cow", 2), {}, [{"type": "minecraft:cow"}] * 2, True),
    ("mobs near no", ("mobs_near", "minecraft:cow", 3), {}, [{"type": "minecraft:cow"}] * 2, False),
    ("dropped nothing yes", ("dropped_nothing",), {}, [{"type": "minecraft:cow"}], True),
    ("dropped nothing no", ("dropped_nothing",), {}, [{"type": "minecraft:item"}], False),
    ("gone yes", ("gone", ["minecraft:zombie"]), {}, [{"type": "minecraft:cow"}], True),
    ("gone no", ("gone", ["minecraft:zombie"]), {}, [{"type": "minecraft:zombie", "health": 5}], False),
    ("away or walled yes: the zombie 5 off", ("away_or_walled", ["minecraft:zombie"]), {},
     [{"type": "minecraft:zombie", "x": 10005.5, "y": 200.0, "z": 10000.5, "health": 20}], True),
    ("away or walled yes: 1 off but walled in", ("away_or_walled", ["minecraft:zombie"]),
     {**{(10000 + dx, 200 + dy, 10000 + dz): "cobblestone" for dy in (0, 1) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))},
      (10000, 202, 10000): "cobblestone"},
     [{"type": "minecraft:zombie", "x": 10001.5, "y": 200.0, "z": 10000.5, "health": 20}], True),
    ("arrived yes: on the target cell", ("arrived", A0, 0.5), {}, [], True),
    ("must fail: arrived no — two short (the doorway before the room's cell)", ("arrived", ("@", 2, 0, 0), 0.5), {},
     [], False),
    ("unchanged yes: shell and door standing", ("unchanged", A0, ("@", 2, 2, 2), []), SHELL, [], True),
    ("must fail: unchanged no — a wall block dug", ("unchanged", A0, ("@", 2, 2, 2), []),
     {c: n for c, n in SHELL.items() if c != (10002, 201, 10002)}, [], False),
    ("must fail: unchanged no — the door broken", ("unchanged", A0, ("@", 2, 2, 2), []),
     {c: n for c, n in SHELL.items() if c != DOOR_CELLS[1]}, [], False),
    ("unchanged yes: a piston door's cells open", ("unchanged", A0, ("@", 2, 2, 2), DOOR),
     {c: n for c, n in SHELL.items() if c not in DOOR_CELLS}, [], True),
    ("must fail: unchanged no — a wall dug beside the open door", ("unchanged", A0, ("@", 2, 2, 2), DOOR),
     {c: n for c, n in SHELL.items() if c not in DOOR_CELLS and c != (10002, 201, 10002)}, [], False),
    ("door state shut yes: both cells solid", ("door_state", DOOR, "shut"), {c: "stone" for c in DOOR_CELLS}, [], True),
    ("must fail: door state shut no — one cell left open", ("door_state", DOOR, "shut"), {DOOR_CELLS[0]: "stone"}, [],
     False),
    ("door state open yes: both cells air", ("door_state", DOOR, "open"), {}, [], True),
    ("must fail: door state open no — pressed shut again", ("door_state", DOOR, "open"), {DOOR_CELLS[1]: "stone"}, [],
     False),
    ("must fail: away or walled no — stood beside it in the open", ("away_or_walled", ["minecraft:zombie"]), {},
     [{"type": "minecraft:zombie", "x": 10001.5, "y": 200.0, "z": 10000.5, "health": 20}], False),
]
# Words used in checks whose yes/no needs more than a state, a bag, blocks or entities recorded (the brain's
# decision log, the slice's trace, a fight's recorded rows, the memory file): each named, with what it reads.
NOT_ROW_TESTED = {
    "door_seen": "the run's watcher over the door's open state (bench words DOOR_SEEN)",
    "not_banned": "the brain's blacklist (core.BRAIN)",
    "slice_check": "the slice's trace and decision lines (SLICE, LAST_LINES) through review",
    "placed_facing": "a placed block's facing property from the world",
    "surfaced": "a hold over time of the body's height",
    "not_remembered": "the memory file",
    "threat_resolved": "a hold over time of hostiles, gap and health",
    "endermen_calm": "the server's AngerTime per enderman (its parse: test_combat_harness.Endermen)",
    "took_cover_alcove": "the runner's trace (its rule: test_combat_harness.Endermen)",
    "ghast_answered": "the server's ghast health and the watch's fireballs (its rule: test_combat_harness.GhastAnswered)",
    "deflected": "the server's health and the volley's tracked fireballs (its rule: DeflectCells)",
    "room_to_work": "free_spots_here over the live region",
    "found_near": "find() over the live world",
    "food_up": "bite_plan over the knowledge tables (fed_as_needed has its own tests)",
    "now": "a read of the live bag and /state (the api module), the inner words are row-tested",
    "&_answers_are_closed": "a fight sweep's recorded rows (its own tests: test_pure_fight)",
    "kept_health": "the body's /state against BASE (alive, RESOLVE_HP_LOSS)",
    "&_escaped": "an escape sweep's recorded outcomes (its rule: test_combat_harness.Escaped)",
    "&_shapes_fit_the_enemy": "a fight sweep's recorded rows",
    "&_more_of_them_costs_more": "a fight sweep's recorded rows",
    "&_wave_cleared": "a fight sweep's recorded rows",
    "&has_stone_pickaxe": "the live bag",
    "&in_the_patch_underground": "the live /state and whether the body is enclosed (the live region)",
    "behaviour": "a fight behaviour's rule over its recorded row (vocab.BEHAVIOURS, test_pure_fight)",
    "brain_rule": "a brain grid family's rule for the cell (vocab.BRAIN_FAMILIES): the words it makes are the old ones",
    "hostiles": "the live entities (the gone/mobs_near rows read the same)",
    "killed": "the server's kill statistic, a scoreboard reply (stat_count and the scene: test_judged)",
}


def run_word(word, state=None, inv=None, sheet=None):
    api = Api(state, inv)
    from bonobo.world import Inventory
    patches = [mock.patch.dict(getattr(sc, k) if hasattr(sc, k) else getattr(core, k), v, clear=True)
               for k, v in (sheet or {}).items()]
    for p in patches:
        p.start()
    try:
        return bool(table.make(word)(api, Inventory(api.inv)))
    finally:
        for p in reversed(patches):
            p.stop()


class Predicates(unittest.TestCase):
    def test_a_sweep_reads_only_this_runs_rows(self):
        # must fail: no rows this run, an older run's rows in the shared file — no pass on them
        import json
        import tempfile
        from unittest import mock
        from bonobo.bench import core
        path = os.path.join(tempfile.mkdtemp(), "sweep.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"cell": 1}) + "\n")
        check = core._sweep_check("w", path, [], 1)
        with mock.patch.dict(core.SWEEP, {}, clear=True):
            self.assertFalse(check(None, None))
        with mock.patch.dict(core.SWEEP, {"w": [{"cell": 1}]}, clear=True):
            self.assertTrue(check(None, None))

    def test_state_and_bag_words(self):
        for why, word, state, inv, sheet, want in PRED_ROWS:
            with self.subTest(why):
                self.assertEqual(run_word(word, state, inv, sheet), want)

    def test_world_words(self):
        from bonobo import mechanisms, world
        for why, word, blocks, ents, want in WORLD_ROWS:
            with self.subTest(why), mock.patch.object(FakeRegion, "BLOCKS", blocks), \
                    mock.patch.object(world, "Region", FakeRegion), mock.patch.object(mechanisms, "Region", FakeRegion), \
                    mock.patch.object(world, "entities", lambda r=0, kinds=None, e=ents: [
                        x for x in e if kinds is None or x["type"] in kinds]):
                self.assertEqual(run_word(word), want)

    def test_live_functions(self):
        # the sheet's own zero-argument reads, on a recorded /state: yes and no
        from bonobo import api
        for why, state, want in (("in the overworld", {}, True), ("in the nether", {"dimension": "minecraft:the_nether"},
                                                                  False)):
            with self.subTest(why), mock.patch.object(api, "get", Api(state).get):
                self.assertEqual(table.dec(("&in_overworld",))(), want)

    def test_now_reads_the_live_bag(self):
        from bonobo import api
        rows = [("yes", ("now", ("!count", "log", ">=", 2)), True),
                ("must fail: no", ("now", ("!count", "log", ">=", 3)), False),
                ("api only yes", ("now_api", ("!arrived", A0, 2)), True),
                ("api only no", ("now_api", ("!arrived", ("@", 8, 0, 0), 2)), False)]
        fake = Api(inv=LOG2)
        for why, word, want in rows:
            with self.subTest(why), mock.patch.object(api, "get", fake.get):
                self.assertEqual(table.make(word)(), want)

    def test_every_check_word_is_row_tested_or_named(self):
        tested = {w[0] for _y, w, *_ in PRED_ROWS} | {w[0] for _y, w, *_ in WORLD_ROWS} | {"now", "now_api",
                                                                                           "&in_overworld"}
        used = set()

        def walk(x, top=False):
            if isinstance(x, tuple) and x and isinstance(x[0], str) and (top or x[0][:1] in "!&"):
                used.add(x[0][1:] if x[0].startswith("!") else x[0])
                for a in x[1:]:
                    walk(a)
            elif isinstance(x, (list, tuple)):
                for a in x:
                    walk(a)
            elif isinstance(x, dict):
                for a in x.values():
                    walk(a)
        for rows in tables().values():
            for row in rows.values():
                for item in ([] if callable(row["check"]) else row["check"]):
                    walk(item, True)
        self.assertEqual(sorted(used - tested - set(NOT_ROW_TESTED)), [])


class Scene(unittest.TestCase):
    ROWS = [(("fill", ("@", -8, -2, -8), ("@", 8, -1, 8), "grass_block"), ["fill 9992 198 9992 10008 199 10008 grass_block"]),
            (("floor",), sc._floor()), (("floor", "netherrack", 6, 2), sc._floor("netherrack", 6, 2)),
            (("stand", -1, 0, 2), [sc._tp(-1, 0, 2)]), (("grove", (2, 0), (3, 3)), sc._grove((2, 0), (3, 3))),
            (("pen", "cow", 3, 5), sc._pen("cow", 3, half=5)),
            (("chest", ("@", 2, 0, 1), "iron_ingot 5", "bread 4"), sc._chest(sc.at(2, 0, 1), "iron_ingot 5", "bread 4")),
            (("tank", -5, 5, -5, 5, 8, 7, -4, "glass", "east"), sc._tank(-5, 5, -5, 5, 8, 7, -4, "glass", "east")),
            (("tp", ("@", 0.5, 0, 0.5)), ["tp @p 10000.5 200 10000.5"]),
            (("give", "cobblestone", 16), ["give @p cobblestone 16"]),
            (("summon", "zombie", ("@", 3, 0, 0), "{PersistenceRequired:1b}"),
             ["summon zombie 10003 200 10000 {PersistenceRequired:1b}"]),
            (("tree", 3, 3), ["fill 10001 203 10001 10005 204 10005 oak_leaves[persistent=true]",
                              "fill 10002 205 10002 10004 205 10004 oak_leaves[persistent=true]",
                              "fill 10003 200 10003 10003 204 10003 oak_log"]),
            (("at", "fill {0} {1} glass hollow", ("@", -9, 0, -9), ("@", 9, 4, 9)),
             ["fill 9991 200 9991 10009 204 10009 glass hollow"])]

    def test_templates_render(self):
        for item, want in self.ROWS:
            with self.subTest(item[0]):
                self.assertEqual(vocab.scene([item]), want)
        with self.subTest("must fail: a word no template defines"), self.assertRaises(KeyError):
            vocab.scene([("teleport", 1)])

    def test_setup_reads_back(self):
        for item, want in self.ROWS:
            with self.subTest(item[0]):
                self.assertEqual(vocab.scene(words.scene_of(want)), want)
        # must fail: a position off the bench is not written relative to it
        self.assertEqual(words.scene_of(["tp @p 0 64 0"]), [("cmd", "tp @p 0 64 0")])


if __name__ == "__main__":
    unittest.main()

class WordModules(unittest.TestCase):
    """vocab gathers the words from words/ (scene → checks → runs → fight → brain → door): one home each, one direction."""
    ORDER = ("scene", "checks", "runs", "fight", "brain", "door")

    def test_a_word_defined_twice_is_refused(self):
        # must fail: two modules defining one word is an error at import, never a silent pick
        with self.assertRaises(ValueError):
            vocab.merged([{"gain": 1}, {"gain": 2}], "words")
        self.assertEqual(vocab.merged([{"gain": 1}, {"kept": 2}], "words"), {"gain": 1, "kept": 2})

    def test_every_word_has_one_home(self):
        homes = {}
        for m in vocab.WORD_MODULES:
            for n in m.__all__:
                homes.setdefault(n, []).append(m.__name__)
        self.assertEqual({n: h for n, h in homes.items() if len(h) > 1}, {})

    def test_imports_run_one_way(self):
        import ast
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo", "bench", "words")
        for i, name in enumerate(self.ORDER):
            with self.subTest(name):
                tree = ast.parse(open(os.path.join(root, f"{name}.py"), encoding="utf-8").read())
                first_def = next(n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign)))
                for n in tree.body:
                    if not isinstance(n, ast.ImportFrom):
                        continue
                    mod = (n.module or "").split(".")[-1]
                    self.assertNotIn(mod, ("vocab", "table"), "a word module never imports the gatherer")
                    if mod in self.ORDER and n.lineno < first_def:       # at the top: only the words before it
                        self.assertLess(self.ORDER.index(mod), i, f"{name} imports {mod} at its top")


class ARowIsPlainData(unittest.TestCase):
    """words.fight._plain: a sweep row's priced state is plain JSON, recursively (threat_state's `alive` set crashed
    json.dumps on every sweep/behaviour row)."""

    def test_rows(self):
        from bonobo import fight_loop
        from bonobo.bench.words import fight as wf
        live = fight_loop.threat_state({"x": 0.5, "y": 64.0, "z": 0.5, "health": 20},
                                       [((3.0, 64.0, 0.0), 3.0, (0.0, 0.0, 0.0), "minecraft:zombie", 1.0, 3.0)],
                                       ids=[7])
        self.assertIsInstance(live["alive"], set)
        with self.subTest("must fail: the live state as it is: a set json cannot write"), \
                self.assertRaises(TypeError):
            json.dumps(dict(live, field=None))
        with self.subTest("plain: written, sets sorted to lists, tuples to lists"):
            back = json.loads(json.dumps(wf._plain(live)))
            self.assertEqual(back["alive"], sorted(back["alive"]))
            self.assertIsInstance(back["here"], list)
        with self.subTest("must fail: a callable is named, never written"), \
                self.assertRaisesRegex(TypeError, r"state\.footing"):
            wf._plain(dict(live, footing=lambda spot: spot))


class AFailedMineJudgesItsOwnOre(unittest.TestCase):
    """A mine_iron condition that must fail checks the ore is still there — at the cell the base puts it
    (mine_iron__tool_one_use failed as expected at 0 s, then its check read a box at x 4 that held no ore)."""

    @staticmethod
    def ore_cells():
        from bonobo.bench.bench_bases import BASES
        return {tuple(p[1:]) for kind, p, *rest in (s for s in BASES["mine_iron"]["scene"] if len(s) >= 3)
                if kind == "setblock" and rest and rest[0] == "iron_ore"}

    @staticmethod
    def boxes(word, out):
        if isinstance(word, tuple) and word and isinstance(word[0], str):
            if word[0].lstrip("!") == "blocks" and "iron_ore" in word[3]:
                out.append((tuple(word[1][1:]), tuple(word[2][1:])))
            for w in word[1:]:
                AFailedMineJudgesItsOwnOre.boxes(w, out)
        return out

    @staticmethod
    def inside(c, lo, hi):
        return all(lo[i] <= c[i] <= hi[i] for i in range(3))

    def test_rows(self):
        from bonobo.bench.bench_bases import CONDITIONS
        ores = self.ore_cells()
        self.assertEqual(ores, {(2, 0, 0)})
        for cond in ("tool_one_use", "wrong_tool"):
            with self.subTest(cond):
                boxes = self.boxes(CONDITIONS[cond]["fails_check"]["mine_iron"], [])
                self.assertTrue(boxes)
                self.assertTrue(all(any(self.inside(c, lo, hi) for c in ores) for lo, hi in boxes))
        with self.subTest("must fail: the old box at x 4 holds no ore of the base"):
            self.assertFalse(any(self.inside(c, (4, 0, 0), (4, 1, 0)) for c in ores))


class FakeBar:
    """The server's bar for the drain hook: a hunger effect steps exhaustion as the game does (saturation first, then
    food); `short` of the first effect's ticks land (a lagging server), the rest after the read."""

    def __init__(self, food, sat, exh, rule="true", short=1.0):
        self.food, self.sat, self.exh, self.rule, self.short, self.effects = food, sat, exh, rule, short, 0

    def command(self, cmd, fb):
        from bonobo.bench.words import brain as wb
        if cmd.startswith("gamerule"):
            return [f"Gamerule {cmd.split()[1]} is currently set to: {self.rule}"]
        if cmd == wb.HUNGER_ON:
            return ["Test failed"]
        key = cmd.split()[-1]
        val = {"foodLevel": self.food, "foodSaturationLevel": self.sat, "foodExhaustionLevel": self.exh}[key]
        return [f"knh190 has the following entity data: {val}"]

    def chat(self, cmd):
        from bonobo.bench.words import brain as wb
        if cmd.startswith("effect give @p minecraft:hunger"):
            secs, amp = int(cmd.split()[4]), int(cmd.split()[5])
            share = self.short if self.effects == 0 else 1.0
            self.effects += 1
            self.exh += wb.HUNGER_PER_TICK * (amp + 1) * 20 * secs * share
            while self.exh > wb.EXHAUSTION_PER_POINT:
                self.exh -= wb.EXHAUSTION_PER_POINT
                if self.sat > 0:
                    self.sat = max(0.0, self.sat - 1)
                else:
                    self.food -= 1


class DrainRegen(unittest.TestCase):
    """The drain hook on the server's reads: regeneration off while the bar drains, back to what it read after even
    on a raise; a short drain re-planned from fresh reads; each read and plan in SETUP_READOUT."""

    def test_rows(self):
        from bonobo.bench import runner
        from bonobo.bench.words import brain as wb
        from bonobo.reflexes import EAT_BELOW, STARVE
        level = (STARVE + EAT_BELOW) // 2 - 1          # the window below holds level + 1
        window = (STARVE, EAT_BELOW)
        # (situation, bar, max s) -> (raised, the rule set last, drains given)
        rows = [("drained on the server's reads: the rule back to what it was", FakeBar(20, 5.0, 1.5), wb.LOW_FOOD_MAX_S,
                 (None, "true", 1)),
                ("a rule that was off stays off", FakeBar(20, 0.0, 0.0, rule="false"), wb.LOW_FOOD_MAX_S,
                 (None, "false", 1)),
                ("short (a lagging server): re-planned from fresh reads", FakeBar(20, 5.0, 0.0, short=0.5),
                 wb.LOW_FOOD_MAX_S, (None, "true", 2)),
                ("must fail: the drain too long raises: the rule restored anyway", FakeBar(20, 20.0, 0.0), 0,
                 (core.SetupInvalid, "true", 0))]
        for name, bar, max_s, (want, last, given) in rows:
            said = []
            with self.subTest(name), mock.patch.object(wb, "_chat", lambda c, _b=bar: (said.append(c), _b.chat(c))), \
                    mock.patch.object(core, "_command", bar.command), mock.patch.object(wb.time, "sleep", lambda s: None), \
                    mock.patch.dict(wb.BASE), mock.patch.dict(runner.SETUP_READOUT, clear=True):
                hook = wb._drain_to(level, max_s=max_s, window=window)
                if want is None:
                    hook(None)
                    self.assertEqual(bar.food, level + 1)
                    self.assertEqual(runner.SETUP_READOUT["drain"][-1], {"final": float(level + 1)})
                else:
                    with self.assertRaises(want):
                        hook(None)
                rules = [c for c in said if c.startswith(f"gamerule {wb.REGEN_RULE}")]
                self.assertEqual(rules, [f"gamerule {wb.REGEN_RULE} false", f"gamerule {wb.REGEN_RULE} {last}"])
                self.assertEqual(bar.effects, given)


class DeflectCells(unittest.TestCase):
    """The deflect row's words: fireballs down a 1-wide corridor, each tagged and tracked to its end from the body's
    position at each read, judged on server health."""

    def test_shot(self):
        from bonobo.bench.words import fight as wf
        eye = wf.deflect_eye()
        ball, m = wf.shot_at()
        self.assertAlmostEqual(math.dist(ball, eye), wf.SHOT_DIST, places=6)
        self.assertAlmostEqual(math.hypot(*m), wf.FIREBALL_SPEED, places=9)
        self.assertLess(sum(m[i] * (ball[i] - eye[i]) for i in range(3)), 0, "aimed back at the eye")
        self.assertLess(wf.SHOT_DIST, wf.CORRIDOR_LEN, "it starts inside the corridor")

    def test_fireball_end(self):
        from bonobo.bench.words import fight as wf
        eye = wf.deflect_eye()
        ball, m = wf.shot_at()
        back = tuple(-c for c in m)

        def at(k):              # the ball k steps along its shot (toward the eye)
            return tuple(ball[i] + m[i] * k for i in range(3))
        moved = tuple(eye[i] + (ball[i] - eye[i]) * 2 for i in range(3))     # the body past the ball, down the corridor
        # (situation, its (pos, velocity field, the body's eye then) reads, gone) → (resolved, deflected)
        rows = [("came, then moved away: deflected", [(at(0), m, eye), (at(5), m, eye), (at(2), back, eye)], False,
                 (True, True)),
                ("a copy moving only every few reads, flying away: turned",
                 [(at(0), m, eye), (at(0), (0, 0, 0), eye), (at(5), (0, 0, 0), eye), (at(5), (0, 0, 0), eye),
                  (at(1), (0, 0, 0), eye)], False, (True, True)),
                ("must fail: a zero-velocity field said away, the ball not moving: no turn",
                 [(at(0), m, eye), (at(5), m, eye), (at(5), back, eye)], False, (False, None)),
                ("must fail: exploded without turning", [(at(0), m, eye), (at(5), m, eye)], True, (True, False)),
                ("must fail: still coming, not resolved", [(at(0), m, eye), (at(5), m, eye)], False, (False, None)),
                ("must fail: flew past a body that moved: judged from where it stands, not a turn",
                 [(at(0), m, moved), (at(5), m, moved)], False, (False, None))]
        for name, reads, gone, want in rows:
            with self.subTest(name):
                self.assertEqual(wf.fireball_end(reads, gone), want)

    def test_tags(self):
        from bonobo.bench.words import fight as wf
        # (situation, tags so far, ids seen, fired) → tags
        rows = [("the first seen is shot 0", {}, [7], 1, {7: 0}),
                ("the next new id is the next shot", {7: 0}, [7, 9], 2, {7: 0, 9: 1}),
                ("must fail: an id past the shots fired is not tagged", {7: 0}, [7, 9], 1, {7: 0})]
        for name, tags, seen, fired, want in rows:
            with self.subTest(name):
                self.assertEqual(wf.tag_shots(tags, seen, fired), want)

    def test_next_shot(self):
        from bonobo.bench.words import fight as wf
        # (situation, fired, closed shots, shots still read) → a shot goes out now
        rows = [("the first at once", 0, {}, set(), True),
                ("must fail: the next before the last resolves", 1, {}, {0}, False),
                ("must fail: the last turned but still in the air (it flies back down the corridor)", 1, {0: True},
                 {0}, False),
                ("the next once the last resolved and is gone", 1, {0: True}, set(), 1 < wf.SHOTS),
                ("must fail: never past SHOTS", wf.SHOTS, {k: True for k in range(wf.SHOTS)}, set(), False)]
        for name, fired, closed, alive, want in rows:
            with self.subTest(name):
                self.assertEqual(wf.next_shot_due(fired, closed, alive), want)

    def test_done(self):
        from bonobo.bench.words import fight as wf
        n = wf.SHOTS
        # (situation, fired, ends read) → done
        rows = [("every shot fired and read", n, n, True),
                ("must fail: the last one fired but not read yet", n, n - 1, False),
                ("not all fired", n - 1, n - 1, False)]
        for name, fired, closed, want in rows:
            with self.subTest(name):
                self.assertEqual(wf.volley_done(fired, closed), want)

    def test_verdict(self):
        from bonobo.bench.words import fight as wf
        n = wf.SHOTS
        every, one_missed = [True] * n, [True] * (n - 1) + [False]
        # (situation, start hp, end hp, fired, deflected per shot) → passed
        rows = [("every one turned away, unhurt", 20.0, 20.0, n, every, True),
                ("must fail: idle — one burst on us", 20.0, 1.0, n, one_missed, False),
                ("must fail: unhurt but one not turned", 20.0, 20.0, n, one_missed, False),
                ("must fail: one never read", 20.0, 20.0, n, every[:-1] + [None], False),
                ("must fail: not all fired", 20.0, 20.0, n - 1, every[:-1], False),
                ("must fail: no health read", 20.0, None, n, every, False)]
        for name, h0, h1, fired, ends, want in rows:
            with self.subTest(name):
                self.assertEqual(wf.volley_verdict(h0, h1, fired, ends), want)

    def test_a_read(self):
        from bonobo.bench.words import fight as wf
        state = {"control": {"active": True, "paused": False}}
        seen = {"id": 7, "hit_s": 0.1, "reach_now": True, "impact_at": (1.0, 2.0, 3.0)}
        # (situation, state, views, tags) → the read
        rows = [("driving, one tagged fireball with the jar's prediction", state, [seen], {7: 0},
                 {"t": 1.0, "active": True, "paused": False,
                  "balls": [{"shot": 0, "hit_s": 0.1, "reach_now": True, "impact_at": (1.0, 2.0, 3.0)}]}),
                ("must fail: not driving is said (the reflex runs only then)", {"control": {"active": False}}, [],
                 {}, {"t": 1.0, "active": False, "paused": None, "balls": []}),
                ("no prediction: said as none", {}, [{"id": 9}], {},
                 {"t": 1.0, "active": None, "paused": None,
                  "balls": [{"shot": None, "hit_s": None, "reach_now": None, "impact_at": None}]})]
        for name, st, views, tags, want in rows:
            with self.subTest(name):
                self.assertEqual(wf.volley_read(1.0, st, views, tags), want)

    def test_the_row_has_no_ghast(self):
        from bonobo.bench.words import fight as wf
        row = wf.deflect_row("deflect__volley")
        self.assertFalse(any("ghast" in str(step) for step in row["scene"] + row["before"]), "no ghasts at all")

    def test_health_read(self):
        from bonobo.bench.words import fight as wf
        self.assertEqual(wf.data_health(["knh190 has the following entity data: 17.5f"]), 17.5)
        self.assertIsNone(wf.data_health(["No entity was found"]), "must fail: no entity, no health")


class FreshRow(unittest.TestCase):
    """core.fresh_row: nothing a row leaves in a world-scoped store reaches the next (031434: a hatch lesson walked
    the side room)."""

    def test_no_store_outlives_its_row(self):
        import tempfile
        from bonobo import fresh, mechanisms as mech, paths
        tmp = tempfile.mkdtemp()
        notes = os.path.join(tmp, os.path.basename(core.NOTES))
        with mock.patch.dict(os.environ, {"MC_DATA": tmp}), mock.patch.object(core, "NOTES", notes), \
                mock.patch.object(core, "reset_brain") as reset:
            for name in fresh.WORLD_SCOPED:
                if "." in name:
                    open(paths.data(name), "w").write("[]")
                else:
                    os.makedirs(paths.data(name))
            lesson = paths.data("mechanisms.json")
            mech.add("minecraft:overworld", (1, 2, 3), [(4, 5, 6)], path=lesson)
            with open(notes, "w") as f:
                json.dump({"sites": [{"name": "home", "kind": "home", "pos": [0, 0, 0],
                                      "dimension": "minecraft:overworld"}]}, f)
            self.assertTrue(mech.load(lesson))          # must fail without the drop: the last row's lesson
            core.fresh_row(object())
            self.assertEqual(mech.load(lesson), [])
            self.assertEqual([n for n in fresh.WORLD_SCOPED + (os.path.basename(notes),)
                              if os.path.exists(paths.data(n))], [])
            self.assertEqual(reset.call_args[0][1].sites(), [])      # the brain's notes: no home site left


def _door_params():
    from bonobo.bench import bench_common
    return {p[0]: p for t, ps in bench_common.FAMILIES if t == "door" for p in ps}


class DoorFamily(unittest.TestCase):
    """words.door: every door row built from DOORS — its lesson, walk and checks from the same parts."""

    def rows(self):
        return {n: r for t in sc.TIERS for n, r in table.rows(t).items() if n in _door_params()}

    def test_every_door_row_is_the_family(self):
        self.assertEqual(sorted(self.rows()), sorted(_door_params()))

    def test_the_lesson_presses_what_the_scene_built(self):
        for name, row in self.rows().items():
            with self.subTest(name):
                built = {a[0]: a[1] for w, *a in row["scene"] if w == "setblock"}
                (_t, presses, cells, _close), = [h for h in row["before"] if h[0] == "teach"]
                self.assertTrue(all("button" in built[p] for p in presses))
                piston = _door_params()[name][2] == "piston"
                self.assertEqual([built[c] == "air" for c in cells], [piston] * len(cells))
                bulb = row["expect"][0][0]
                # a piston door shuts on power: lit last; an iron door opens on it: never lit
                self.assertEqual("lit=true" in [b for w, *a in row["scene"] if w == "setblock" and a[0] == bulb
                                                for b in a[1:]][-1], piston)

    def test_the_checks_follow_the_params(self):
        for name, (_n, _shape, _kind, presses, close, taught, _start, _goal, back) in _door_params().items():
            with self.subTest(name):
                row = self.rows()[name]
                (_t, taught_presses, _c, _cl), = [h for h in row["before"] if h[0] == "teach"]
                self.assertEqual(len(taught_presses), presses if taught else 0)
                state = [c for c in row["check"] if c[0] == "door_state"][0][2]
                self.assertEqual(state, "shut" if close or not taught else "open")
                self.assertEqual(row["run"][3] is not None, back)

    def test_the_watch_is_keyed_by_its_row(self):
        # must fail (the 031434 run's "KeyError: None"): no ('start', name) → every door row watched under None,
        # and the reset between rows raised in the last row's watcher thread
        import threading
        from bonobo import mechanisms as mech
        from bonobo.bench.words import checks, door as dw
        for name, row in self.rows().items():
            with self.subTest(name):
                self.assertEqual(row["before"][0], ("start", name))
        raised, gate = [], threading.Event()

        def solid(cells):
            gate.wait(2)
            return {tuple(c): True for c in cells}
        with mock.patch.dict(checks.BASE, {"name": "r"}, clear=True), mock.patch.object(mech, "solid_map", solid), \
                mock.patch.object(threading, "excepthook", lambda a: raised.append(a.exc_type)):
            before = set(threading.enumerate())
            dw.teach([], [(0, 0, 0)], False)(None)
            watcher, = set(threading.enumerate()) - before
            dw.DOOR_SEEN.clear()                  # the next row's reset
            gate.set()
            watcher.join(2)
        self.assertFalse(watcher.is_alive())
        self.assertEqual(raised, [])

    def test_a_press_the_layout_lacks_is_refused(self):
        # must fail: two buttons asked of the one-button side room — never a row with a press nothing stands at
        from bonobo.bench.words import door as dw
        with self.assertRaises(ValueError):
            dw.door_parts("wall", "piston", 2)


class BrainHookWords(unittest.TestCase):
    """The hooks the brain/upkeep rows are written in (words.brain)."""

    def test_the_interrupt_counts_once_gained(self):
        # must fail: counted (or injected) before the bag gained — a slice absorbs it unseen otherwise
        from bonobo.bench.words import brain as wb, checks as wc
        seen = {}
        with mock.patch.object(wb, "_when", lambda progress, act: seen.update(progress=progress, act=act)), \
                mock.patch.object(wb, "_inject_interrupt") as inject, \
                mock.patch.dict(wc.BASE, {"name": "r"}, clear=True), mock.patch.dict(wc.INTERRUPTS, {}, clear=True), \
                mock.patch.object(wb, "gained_at_least", lambda token, n: ("gained", token, n)):
            wb._interrupt_counted("minecraft:raw_iron", 1)
            self.assertEqual(seen["progress"], ("gained", "minecraft:raw_iron", 1))
            self.assertEqual((wc.INTERRUPTS, inject.call_count), ({}, 0))
            seen["act"]()
            self.assertEqual((wc.INTERRUPTS, inject.call_count), ({"r": 1}, 1))

    def test_state_before_feeds_rose(self):
        from bonobo import api
        from bonobo.bench.words import brain as wb, checks as wc
        with mock.patch.dict(wc.BASE, {}, clear=True):
            with mock.patch.object(api, "get", Api({"health": 12.0}).get):
                wb._state_before("health", "food")(None)
            self.assertEqual(wc.BASE, {"health_before": 12.0, "food_before": STATE["food"]})
            self.assertFalse(wb._rose("health")(Api({"health": 12.0}), None))
            self.assertTrue(wb._rose("health")(Api({"health": 13.0}), None))

