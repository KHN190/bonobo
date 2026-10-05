"""K4: one gate for every side act (arbiter.side_why) — it runs when an invariant forces it (S1–S8, P2) or it saves
seconds at production prices (knowledge.side_saving), else its reason (D1). Every proposer × forced / pays / neither:
the reflexes' rows, upkeep's needs, lighting, armour and the shield."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import arbiter, brain, goals, reflexes, threat  # noqa: E402
from bonobo import knowledge as _k  # noqa: E402
from bonobo.data import TICKS_PER_S  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import bag, brain_fixture, cost, inventory, snapshot, state  # noqa: E402

GATHER = Step("gather", "log", 4, {})
GATHER.est = 2400
CHOP = Step("craft", "minecraft:oak_planks", 4, {"inputs": {"log": 1}})
CHOP.est = 60


def fixture(held=()):
    b = brain_fixture()
    b.held = {"t1": {"steps": list(held)}} if held else {}
    return b


def verdict(forced_by, saving):
    """forced / pays / neither, as the gate reads (forced_by, saving)."""
    if arbiter.side_why(forced_by, saving) is not None:
        return "neither"
    return "forced" if forced_by in arbiter.FORCING else "pays"


class TheGate(unittest.TestCase):
    def test_rows(self):
        rows = [*[(f"forced by {r}", r, None, "forced") for r in arbiter.FORCING],
                ("pays", None, 5.0, "pays"),
                ("must fail: saves nothing", None, 0.0, "neither"),
                ("must fail: unpriced", None, None, "neither"),
                ("a dearer detour", None, -3.0, "neither"),
                ("no such invariant", "S9", None, "neither")]
        for name, forced, saving, want in rows:
            with self.subTest(name):
                self.assertEqual(verdict(forced, saving), want)
        side = arbiter.Intent("maintain", object(), key="x", side=True, saving=0.0)
        self.assertFalse(arbiter.viable(side, {}))                 # must fail: an unpriced side act offered
        self.assertTrue(arbiter.viable(arbiter.Intent("maintain", object(), key="x"), {}))


class EveryReflexRowHasTerms(unittest.TestCase):
    def test_every_row_is_forced_or_priced_once(self):
        for name in reflexes.NAMES:
            with self.subTest(name):
                self.assertEqual((name in reflexes.FORCED_BY) + (name in reflexes.PRICED), 1)

    def test_rows(self):
        snap = snapshot(state(), inventory())
        job = {"item": "minecraft:iron_ingot", "count": 3, "pos": (4, 64, 0)}
        uses_iron = Step("craft", "minecraft:iron_pickaxe", 1, {"inputs": {"minecraft:iron_ingot": 3}})
        uses_iron.est = 60
        # (row, held steps, P2 refused, (recovery s, the ready job)) → forced / pays / neither
        rows = [*[(r, (), False, None, "forced") for r, f in reflexes.FORCED_BY.items() if f != "P2"],
                *[(r, (GATHER,), True, None, "forced") for r, f in reflexes.FORCED_BY.items() if f == "P2"],
                *[(r, (GATHER,), False, None, "neither") for r, f in reflexes.FORCED_BY.items() if f == "P2"],
                ("empty the bag", (GATHER,), False, None, "pays"),
                ("empty the bag", (CHOP,), False, None, "neither"),       # must fail: no drop to lose
                ("recover items", (), False, (12.0, None), "pays"),
                ("recover items", (), False, (-4.0, None), "neither"),
                ("collect job", (uses_iron,), False, (None, job), "pays"),
                ("collect job", (GATHER,), False, (None, job), "neither")]
        for name, held, refused, given, want in rows:
            with self.subTest(f"{name}: {want}"):
                b = fixture(held)
                m = reflexes.Maintain(b)
                rec, ready = given or (None, None)
                with mock.patch.object(b, "p2_refused", return_value=refused), \
                        mock.patch.object(reflexes, "recovery_s", return_value=rec), \
                        mock.patch.object(m, "ready_job", return_value=ready), \
                        mock.patch.object(b, "price_table", return_value={"minecraft:iron_ingot": 40.0}):
                    self.assertEqual(verdict(*m.terms_of(name, snap)), want)


class UpkeepNeeds(unittest.TestCase):
    def test_rows(self):
        snap = snapshot(state(), inventory(), oak_log=4)
        c = cost(snap)
        rows = [("night prep", goals.have(("bed", 1)), (), "forced"),
                ("water bucket", goals.have(("minecraft:water_bucket", 1)), (GATHER,) * 40, "pays"),
                ("water bucket", goals.have(("minecraft:water_bucket", 1)), (), "neither"),   # must fail: no plan
                ("bridge stock", goals.have(("building", 8)), (), "neither"),
                ("broken tool", goals.have(("tool", "axe", 0)), (GATHER,) * 40, "pays")]
        for kind, goal, held, want in rows:
            with self.subTest(f"{kind}: {want}"):
                self.assertEqual(verdict(*fixture(held).side_terms(kind, goal, snap, c)), want)


class LightingArmourShield(unittest.TestCase):
    def test_lighting(self):
        dark = snapshot(state(skyLight=0, blockLight=0), inventory(("torch", 8)))
        for name, held, want in [("a plan worked here", (GATHER,) * 10, "pays"),
                                 ("must fail: nothing to work here", (), "neither")]:
            with self.subTest(name):
                b = fixture(held)
                b.last_light, b.lit_place, b.place = 0.0, None, None
                with mock.patch.object(brain.reflexes, "ground", return_value=(lambda: False, None, None)):
                    got = b.light_intent(dark, None)
                self.assertEqual(verdict(got.forced_by, got.saving), want)

    def test_armour_and_shield(self):
        iron = inventory(("iron_chestplate", 1))
        plan_s = 10 * GATHER.est / TICKS_PER_S
        pays = _k.side_saving(1.0, _k.hostile_s(plan_s) * brain.armour_gain(bag(iron)), 0.0,
                              _k.PRIOR_TICKS["equip"] / TICKS_PER_S)
        rows = [("a threat in sight: forced", "S1", None, "forced"),
                ("no threat, a plan to work: armour pays", None, pays, "pays"),
                ("must fail: no threat, the shield unpriced", None, None, "neither")]
        for name, forced, saving, want in rows:
            with self.subTest(name):
                self.assertEqual(verdict(forced, saving), want)
        self.assertGreater(brain.armour_gain(bag(iron)), 0)
        self.assertEqual(brain.armour_gain(bag(inventory())), 0)
        self.assertEqual(threat.threats_seen(now=time.time() + 1e6), ([], []))


if __name__ == "__main__":
    unittest.main()
