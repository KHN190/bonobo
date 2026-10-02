"""M1b: kit that clears the fight line (brain.line_raisers)."""
import unittest

from bonobo import beliefs, brain, estimate
from bonobo.data import critical_hp
from tests.world import inventory, snapshot, state

E, S, Z = ["minecraft:enderman"], ["minecraft:spider"], ["minecraft:zombie"]


def body(health=20.0, armor=0, items=(), **worn):
    snap = snapshot(state(health=health, armor=armor), inventory(*items))
    snap.inv.equipment.update({slot: {"id": f"minecraft:{item}", "count": 1} for slot, item in worn.items()})
    return snap


def inside(kinds, snap, rows):
    """Independent check of the line with `rows` added."""
    from bonobo.data import ARMOR_POINTS, TOOL_MATERIAL_FOR_TIER
    tiers = [r[2] for r in rows if r[0] == "tool"]
    sword = f"minecraft:{TOOL_MATERIAL_FOR_TIER[max(tiers)]}_sword" if tiers else \
        brain._k.attack_weapon(snap.inv, beliefs.COMMON_FOE_HP)
    points = float(snap.state.get("armor", 0)) + sum(
        ARMOR_POINTS[r[0].removeprefix("minecraft:").split("_")[0]][r[0].rsplit("_", 1)[1]] for r in rows if r[0] != "tool")
    mean, hit = estimate.melee_loss(kinds, sword, beliefs.protection(points))
    return estimate.fight_line_ok(float(snap.state["health"]), critical_hp(snap.state), mean, hit)


class Raisers(unittest.TestCase):
    # (situation, kinds, body, some raiser expected?)
    ROWS = [
        ("a piglin, full health, an iron sword, no armour: armour raises the line", ["minecraft:piglin"],
         body(items=[("iron_sword", 1)]), True),
        ("a zombie, bare hands: a sword or armour", Z, body(), True),
        ("must fail: a spider already inside the line needs nothing raised", S, body(items=[("iron_sword", 1)]), None),
        ("must fail: no kit when health itself is the wall (2 hp)", ["minecraft:piglin"], body(health=2.0), False),
        ("must fail: an enderman (7 per 20 ticks): no sword or iron armour clears it", E, body(), False),
    ]

    def test_rows(self):
        for name, kinds, snap, want in self.ROWS:
            with self.subTest(name):
                ok_now = brain.fight_line_holds(type("C", (), {"fights": lambda self, c: kinds})(), (), snap.state,
                                                snap.inv)[0]
                got = brain.line_raisers(kinds, snap.state, snap.inv)
                if want is None:
                    self.assertTrue(ok_now)
                    continue
                self.assertFalse(ok_now)
                self.assertEqual(bool(got), want, got)
                for rows in got:
                    self.assertTrue(inside(kinds, snap, rows), rows)      # each raiser meets the line it claims

    def test_worn_armour_is_not_asked_again(self):
        snap = body(armor=6, items=[("iron_sword", 1)], chest="iron_chestplate")
        for rows in brain.line_raisers(E, snap.state, snap.inv):
            self.assertNotIn(("minecraft:iron_chestplate", 1), rows)       # must fail: the chestplate is worn


if __name__ == "__main__":
    unittest.main()
