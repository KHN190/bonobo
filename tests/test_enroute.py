"""En-route (docs/refactor.md 顺路插入, G3/K4): once a planning round, what lies beside the leg the next step walks is
taken on the way when P(used later) × (its price later − its work now) > the detour (knowledge.side_saving) — wanted
by the held plan (P 1) or a later milestone (P 1/(1+k), k down the chain); a remembered chest by its contents × the
chance they are still there; an unopened chest within reach of the leg looked into. The plan_proposals rows go
through the production round with what the base has, so they are red there by assertion."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import api, arbiter, goals, lifecycle, tasks, world  # noqa: E402
from tests.world import brain_fixture, inventory, memory, round_ctx, state  # noqa: E402

D = "minecraft:overworld"
ORE = (40, 64, 0)
KIT = (("stone_pickaxe", 1), ("stone_sword", 1), ("stone_axe", 1), ("crafting_table", 1), ("furnace", 1), ("bread", 8))


def ground():
    """Dirt over stone from the feet to an iron ore 40 east: the leg the plan's next step walks."""
    blocks = {(x, y, z): "dirt" if y >= 62 else "stone" for x in range(-8, 49) for z in range(-8, 9)
              for y in range(58, 64)}
    blocks[ORE] = "iron_ore"
    return world.Region.of((-8, 58, -8), (48, 70, 8), blocks)


def proposals(mobs):
    """The round's plan proposals for a raw iron task, the iron ore 40 east, with `mobs` in sight."""
    tmp = tempfile.mkdtemp()
    lifecycle.reset_all(caches=False)
    with mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")), \
            mock.patch.object(api, "api", side_effect=AssertionError("the round read the world")):
        b = brain_fixture()
        hits = {"iron_ore": [{"x": ORE[0], "y": ORE[1], "z": ORE[2], "distance": 40.0, "block": "minecraft:iron_ore"}]}
        snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), world.Inventory(inventory(*KIT)), hits, mobs,
                                            ground())
        b.round_snap = snap
        tasks.add(goals.have(("minecraft:raw_iron", 1)))
        return b.plan_proposals(snap, round_ctx(b, snap))


def sheep(x, z):
    return {"id": 9, "type": "minecraft:sheep", "x": x + .5, "y": 64, "z": z + .5, "distance": float(x)}


class TheRoundTakesWhatIsOnTheWay(unittest.TestCase):

    def test_a_sheep_on_the_leg_is_taken(self):
        """Wool is a later milestone's (a bed's): a sheep 1 off the leg pays its hunt back."""
        got = proposals([sheep(20, 1)])
        chosen = arbiter.arbitrate(got)
        # must fail: walked past, the bed's wool fetched on a trip of its own later
        self.assertEqual((chosen.kind, chosen.side), ("enroute", True), [(i.kind, i.key) for i in got])

    def test_a_sheep_far_off_the_leg_is_not(self):
        got = proposals([sheep(20, 40)])
        self.assertNotIn("enroute", [i.kind for i in got])
        self.assertEqual(arbiter.arbitrate(got).kind, "queue")


def scene(notes=(), containers=(), chests=()):
    """A Cost over flat ground, the leg (0,64,0) → (45,64,0), `notes` remembered, `containers` with remembered
    contents, `chests` seen unopened."""
    from bonobo import cost as costmod
    blocks = {(x, y, z): "dirt" if y >= 62 else "stone" for x in range(-48, 49) for z in range(-12, 13)
              for y in range(55, 64)}
    mem = memory()
    mem.clock = 0
    for kind, pos in notes:
        mem.note_seen(kind, pos, D)
    for pos, items in containers:
        mem.note_container(pos, D, [{"id": i, "count": n, "slot": k} for k, (i, n) in enumerate(items)])
    hits = {"chest": [{"x": c[0], "y": c[1], "z": c[2], "distance": float(c[0])} for c in chests]}
    snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), world.Inventory(inventory(("stone_axe", 1))), hits,
                                        [], world.Region.of((-48, 55, -12), (48, 70, 12), blocks))
    return costmod.Cost(snap, mem)


PRICE = {"log": 12.0, "wool": 20.0, "minecraft:coal": 15.0}.get


class CostEnroute(unittest.TestCase):
    """Cost.enroute's choice beside one leg: the paying ones, best first; the rest skipped."""

    def taken(self, c, wanted):
        return [(st.kind, st.token, where) for _s, st, where in c.enroute((0, 64, 0), (45, 64, 0), wanted, PRICE)]

    def test_rows(self):
        rows = [("a tree beside the leg, logs wanted: gathered", scene(notes=[("tree", (15, 64, 2))]), {"log": 1.0},
                 [("gather", "log", (15, 64, 2))]),
                ("must fail: a tree 100 off the leg: its detour costs more than its logs",
                 scene(notes=[("tree", (20, 64, 100))]), {"log": 1.0}, []),
                ("a tree beside the leg, logs not wanted: skipped", scene(notes=[("tree", (15, 64, 2))]), {"wool": 1.0}, []),
                ("a remembered chest with coal beside the leg: withdrawn",
                 scene(containers=[((30, 64, 1), [("minecraft:coal", 5)])]), {"minecraft:coal": 1.0},
                 [("withdraw", "minecraft:coal", (30, 64, 1))]),
                ("an unopened chest within reach of the leg: looked into", scene(chests=[(35, 64, 1)]), {"log": 1.0},
                 [("look", "minecraft:chest", (35, 64, 1))]),
                ("an unopened chest 10 off: skipped", scene(chests=[(35, 64, 10)]), {"log": 1.0}, [])]
        for name, c, wanted, want in rows:
            with self.subTest(name):
                self.assertEqual(self.taken(c, wanted), want)

    def test_raw_tokens(self):
        """What a later milestone's needs come down to: the got tokens its recipes read."""
        c = scene()
        self.assertEqual(c.raw_tokens("minecraft:iron_pickaxe"), {"log", "minecraft:raw_iron"})
        self.assertIn("wool", c.raw_tokens("bed"))


if __name__ == "__main__":
    unittest.main()
