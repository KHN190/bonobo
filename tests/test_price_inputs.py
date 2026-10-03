"""K13, an estimate priced with the wrong inputs: every price that reads tools or a place reads the step's own (the
plan's place before it and the tools it holds then: Cost.step_state), never the round's snapshot, for a step later in a
plan (K9: one price)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo.planner import Step  # noqa: E402
from tests.world import cost, inventory, snapshot, state  # noqa: E402

IRON = Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore", "deepslate_iron_ore"], "breaks": 1})
LOG = Step("gather", "log", 2)
SEEK = Step("seek", "iron_ore", 1, {"kinds": ["iron_ore"]})
PICK = {"pickaxe": 1}


class StepState(unittest.TestCase):
    """price function × input source: (the step's own, the snapshot's) — the step's must move the price."""

    def test_table(self):
        c = cost(snapshot(state(y=70.0), inventory()))         # on the surface, an empty bag
        deep = (0, 20, 0)
        rows = [
            ("must fail: a search priced with the empty bag's hand, a pickaxe held at the step",
             lambda held, at: c.find_ticks(["iron_ore"], held, at), (PICK, None), (None, None)),
            ("must fail: a search priced from the snapshot's y, not the step's place",
             lambda held, at: c.find_ticks(["iron_ore"], held, at), (PICK, (0, 16, 0)), (PICK, None)),
            ("must fail: a surface trip priced from the snapshot's sky, the step underground",
             lambda held, at: c._surface_trip(at), (None, deep), (None, None)),
            ("must fail: a step's estimate with the bag's tools, not the plan's",
             lambda held, at: c.estimate(IRON, held, at), (PICK, None), (None, None)),
            ("must fail: walk_lb's search with the bag's tools",
             lambda held, at: c.walk_lb(IRON, held if held is not None else {}), (PICK, None), (None, None)),
            ("must fail: a seek step's work with the bag's tools",
             lambda held, at: c.work(SEEK, held), (PICK, None), (None, None)),
        ]
        for name, price, own, snap in rows:
            with self.subTest(name):
                self.assertNotEqual(price(*own), price(*snap))

    def test_the_dig_to_a_seen_ore_starts_at_the_step(self):
        import json
        from bonobo.bench.words import est
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bench_rows.json")) as fh:
            setup = json.load(fh)["ore_buried"]["setup"]
        c = est.scene_cost(est.scene_world(setup))           # the ore behind stone, 4 off
        ore = c.snap.hits["iron_ore"][0]
        beside = (ore["x"] - 1, ore["y"], ore["z"])
        # must fail: the dig to the ore in sight planned from the snapshot's feet, the step standing beside it
        self.assertLess(len(c.work_of(IRON, at=beside)[0]), len(c.work_of(IRON)[0]))

    def test_a_first_step_reads_the_snapshot(self):
        c = cost(snapshot(state(y=70.0), inventory(("stone_pickaxe", 1))))
        self.assertEqual(c.step_state(), (c.snap.feet, {"pickaxe": 1}))
        self.assertEqual(c.find_ticks(["iron_ore"]), c.find_ticks(["iron_ore"], {"pickaxe": 1}, c.snap.feet))


def _accept7():
    """(cost, ore step's target, its read ground, bag) of accept7's raw_iron leg: the ore remembered 67 off and 10
    down, the read ground the run's box (unread: _Ground's expected ground)."""
    import json
    from bonobo import world
    from bonobo.data import bare
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "accept7_ground.json")) as fh:
        a7 = json.load(fh)
    lo, hi = a7["lo"], a7["hi"]
    cells = {(x, y, z): "stone" for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1)
             for z in range(lo[2], hi[2] + 1)}
    cells.update({(x, y, z): n for x, y, z, n in a7["cells"]})
    region = world.Region.of(lo, hi, {c: n for c, n in cells.items() if n != "air"})
    inv = world.Inventory(inventory(*[(bare(i), n) for i, n in a7["inventory"]], ("stone_pickaxe", 1)))
    return tuple(a7["feet"]), tuple(a7["target"]), region, inv


class OneTargetPerStep(unittest.TestCase):
    """A mine step's walk and its dig price the way to one target — the cell site() picks, remembered or in the
    round's look (accept7 00:34: the walk to the remembered ore, the dig to none in sight: ~61 s priced, ~138 s run)."""

    def _cost(self, feet, target, region, inv, remembered, scanned, block="iron_ore"):
        from bonobo import cost as costmod, world
        from tests.world import memory
        mem = memory()
        if remembered:
            mem.note_seen(block, target, "minecraft:overworld")
        hits = {block: [{"x": target[0], "y": target[1], "z": target[2], "block": f"minecraft:{block}",
                         "distance": float(min(32.0, sum(abs(target[i] - feet[i]) for i in range(3))))}]} if scanned else {}
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), inv, hits, [], region)
        return costmod.Cost(snap, mem)

    def test_accept7_priced_no_less_than_its_dug_way(self):
        from bonobo import cost as costmod
        from bonobo.game import TICKS_PER_S
        from bonobo.knowledge import held_tiers, work_s
        feet, target, region, inv = _accept7()
        way = costmod.dug_way(feet, target, "iron_ore", 3, True, inv, (), region)
        floor = work_s(way, [], held_tiers(inv), TICKS_PER_S) * TICKS_PER_S
        self.assertGreater(len(way), 100)                   # a long tunnel: the run dug ~138 s of it
        for remembered, scanned in ((True, False), (True, True)):
            with self.subTest(remembered=remembered, scanned=scanned):
                c = self._cost(feet, target, region, inv, remembered, scanned)
                self.assertEqual(c.site(IRON), target)
                c.estimate(IRON)
                # must fail (accept7): the dig priced 0, the remembered ore not in the round's look
                self.assertGreaterEqual(IRON.parts["dig"], floor * 0.95)
                # must fail: a seek priced as the walk only, no route known to it
                self.assertGreaterEqual(c.seek_s(["iron_ore"]), floor / TICKS_PER_S * 0.95)

    def test_remembered_or_scanned_by_walkable_or_buried(self):
        from bonobo import world
        feet = (0, 64, 0)
        ground = {(x, y, z): "stone" for x in range(-12, 13) for z in range(-6, 7) for y in range(50, 64)}
        rows = [("walkable", (8, 64, 0)), ("buried", (8, 58, 0))]
        for geo, target in rows:
            blocks = dict(ground)
            blocks[target] = "iron_ore"
            region = world.Region.of((-12, 50, -6), (12, 70, 6), blocks)
            digs = []
            for remembered, scanned in ((True, False), (False, True), (True, True)):
                with self.subTest(geo=geo, remembered=remembered, scanned=scanned):
                    c = self._cost(feet, target, region, world.Inventory(inventory(("stone_pickaxe", 1))),
                                   remembered, scanned)
                    self.assertEqual(c.site(IRON), target)
                    c.estimate(IRON)
                    digs.append(IRON.parts["dig"])
                    # must fail: a buried ore priced as a walk (no dig), an open one charged a tunnel
                    self.assertEqual(IRON.parts["dig"] > 0, geo == "buried")
            # one target: the same dig whichever way the ore is known
            self.assertEqual(len(set(digs)), 1, (geo, digs))


if __name__ == "__main__":
    unittest.main()
