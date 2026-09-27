"""The bench as tables (bonobo/bench/bench_<tier>.py: families of rows made by a template from their parameters,
one-off rows in words, one-off rows in code), proven against the old sheet offline: the old sheet's 271 rows were
recorded (tests/fixtures/bench_rows.json: every plain field, the check's words, the escape rows' seeds) before
scenarios.py was deleted, and every row built now must equal its record. Names are unique, the tier rules hold,
and every word says yes and no on recorded data."""
import importlib
import json
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
        self.assertEqual(len(rec), 271)
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
            with self.subTest(name), mock.patch.dict(vars(vocab), defs):
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
        n = sum(len(getattr(importlib.import_module(table.TABLES[t]), "CODE_ROWS", ())) for t in table.TIERS)
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

    def __init__(self, lo, hi):
        self.blocks = {p: n for p, n in self.BLOCKS.items()
                       if all(min(lo[i], hi[i]) <= p[i] <= max(lo[i], hi[i]) for i in range(3))}

    def name(self, p):
        return self.BLOCKS.get(tuple(p), "air")


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
    ("call yes: near", ("call", "_near", ["$api", A0, 1.0]), None, None, {}, True),
    ("call no: far", ("call", "_near", ["$api", ("@", 9, 0, 0), 1.0]), None, None, {}, False),
    ("all yes", ("all", ("!state", "onGround"), ("!count", "log", ">=", 1)), None, LOG2, {}, True),
    ("all no", ("all", ("!state", "onGround"), ("!count", "log", ">=", 5)), None, LOG2, {}, False),
    ("any yes", ("any", ("!state", "dead"), ("!count", "log", ">=", 1)), None, LOG2, {}, True),
    ("any no", ("any", ("!state", "dead"), ("!count", "log", ">=", 5)), None, LOG2, {}, False),
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
    ("at yes", ("_at", A0, 2), None, None, {}, True),
    ("at no", ("_at", ("@", 8, 0, 0), 2), None, None, {}, False),
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
    ("api only yes", ("api_only", ("!_at", A0, 2)), None, None, {}, True),
    ("api only no", ("api_only", ("!_at", ("@", 8, 0, 0), 2)), None, None, {}, False),
    ("no scan yes", ("no_scan",), None, None, {"FINDS": {"diamond": 0}}, True),
    ("no scan no", ("no_scan",), None, None, {"FINDS": {"diamond": 2}}, False),
]
# world readers: the recorded blocks (FakeRegion) and entities
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
]
# Words used in checks whose yes/no needs more than a state, a bag, blocks or entities recorded (the brain's
# decision log, the slice's trace, a fight's recorded rows, the memory file): each named, with what it reads.
NOT_ROW_TESTED = {
    "slice_check": "the slice's trace and decision lines (SLICE, LAST_LINES) through review",
    "placed_facing": "a placed block's facing property from the world",
    "decision_gaps_ok": "the tape's decision gaps",
    "surfaced": "a hold over time of the body's height",
    "not_remembered": "the memory file",
    "threat_resolved": "a hold over time of hostiles, gap and health",
    "room_to_work": "free_spots_here over the live region",
    "found_near": "find() over the live world",
    "food_up": "bite_plan over the knowledge tables (fed_as_needed has its own tests)",
    "now": "a read of the live bag and /state (the api module), the inner words are row-tested",
    "&_answers_are_closed": "a fight sweep's recorded rows (its own tests: test_pure_fight)",
    "&_shapes_fit_the_enemy": "a fight sweep's recorded rows",
    "&_more_of_them_costs_more": "a fight sweep's recorded rows",
    "&_wave_cleared": "a fight sweep's recorded rows",
    "&has_stone_pickaxe": "the live bag",
    "&in_the_patch_underground": "the live /state and whether the body is enclosed (the live region)",
    "behaviour": "a fight behaviour's rule over its recorded row (vocab.BEHAVIOURS, test_pure_fight)",
    "brain_rule": "a brain grid family's rule for the cell (vocab.BRAIN_FAMILIES): the words it makes are the old ones",
    "hostiles": "the live entities (the gone/mobs_near rows read the same)",
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
    def test_state_and_bag_words(self):
        for why, word, state, inv, sheet, want in PRED_ROWS:
            with self.subTest(why):
                self.assertEqual(run_word(word, state, inv, sheet), want)

    def test_world_words(self):
        from bonobo import world
        for why, word, blocks, ents, want in WORLD_ROWS:
            with self.subTest(why), mock.patch.object(FakeRegion, "BLOCKS", blocks), \
                    mock.patch.object(world, "Region", FakeRegion), \
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
                ("api only yes", ("now_api", ("!_at", A0, 2)), True),
                ("api only no", ("now_api", ("!_at", ("@", 8, 0, 0), 2)), False)]
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
