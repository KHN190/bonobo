"""The one door every task passes (design A): what is held (I2: hold_clicks, segments), where it is sent from (I4:
stands_for, task_stands, gate), what changed unasked (R4: unplanned_cells), one read per segment (R-a, R-b), a walk
that finds no route (one way, judged at the asked cell), the weapon held at engage (B3), the fight trail."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, fight_loop, nav, skillcore, world  # noqa: E402
from tests.world import FakeRegion, slot  # noqa: E402
from tests.test_reach import ground  # noqa: E402

PICK, SHOVEL, DIRT = "minecraft:iron_pickaxe", "minecraft:stone_shovel", "minecraft:dirt"


def swap(bag_slot, selected=0):
    return {"slot": world.screen_slot(bag_slot), "button": selected, "action": "SWAP"}


class HoldClicks(unittest.TestCase):
    """skillcore.hold_clicks: the item named put in the main hand by one SWAP, or nothing sent."""

    def test_rows(self):
        worn = slot("iron_pickaxe", worn=249, i=0)               # one use left
        full_tools = [slot("iron_pickaxe", i=i) for i in range(world.BAG_SLOTS)]
        full_mixed = [slot("iron_pickaxe", i=0)] + [slot("diamond", 9, i=i) for i in range(1, 20)] + \
            [slot("dirt", 64, i=i) for i in range(20, world.BAG_SLOTS)]
        price = {"minecraft:diamond": 100.0, DIRT: 0.1}.get
        rows = [("held already: nothing sent", [slot("iron_pickaxe", i=0)], PICK, []),
                ("in the hotbar: one swap from its screen slot", [slot("dirt", i=0), slot("iron_pickaxe", i=3)], PICK,
                 [swap(3)]),
                ("in the main bag: swapped from there", [slot("dirt", i=0), slot("iron_pickaxe", i=20)], PICK,
                 [swap(20)]),
                ("must fail: the held one has one use left: the fresh one swapped in",
                 [worn, slot("iron_pickaxe", i=5)], PICK, [swap(5)]),
                ("not carried: nothing", [slot("dirt", i=0)], SHOVEL, []),
                ("hand, a tool held: swapped into an empty slot", [slot("iron_pickaxe", i=0)], "hand", [swap(1)]),
                ("hand, a block held: mines as a bare hand, nothing sent", [slot("dirt", i=0)], "hand", []),
                ("hand, the bag full: the lowest-value non-tool stack (blocks too)", full_mixed, "hand", [swap(20)]),
                ("hand, the bag full of tools: nothing to swap", full_tools, "hand", [])]
        for name, slots, item, want in rows:
            with self.subTest(name):
                self.assertEqual(skillcore.hold_clicks(slots, 0, item, price), want)


class Segments(unittest.TestCase):
    def test_rows(self):
        def mine(x, item=None):
            return {"type": "mine", "x": x, "y": 64, "z": 0, **({"item": item} if item else {})}
        walk = {"type": "goto", "x": 0, "y": 64, "z": 0}
        rows = [("one item: one segment", [mine(1, PICK), mine(2, PICK)], 6, [[mine(1, PICK), mine(2, PICK)]]),
                ("must fail: a change of item cut", [mine(1, PICK), mine(2, SHOVEL)], 6,
                 [[mine(1, PICK)], [mine(2, SHOVEL)]]),
                ("a walk names nothing: it rides along", [mine(1, PICK), walk, mine(2, PICK)], 6,
                 [[mine(1, PICK), walk, mine(2, PICK)]]),
                ("at most `size` each", [mine(1, PICK), mine(2, PICK), mine(3, PICK)], 2,
                 [[mine(1, PICK), mine(2, PICK)], [mine(3, PICK)]])]
        for name, tasks, size, want in rows:
            with self.subTest(name):
                self.assertEqual(api.segments(tasks, size), want)


class Stands(unittest.TestCase):
    def test_task_stands(self):
        mine = {"type": "mine", "x": 6, "y": 64, "z": 0}
        go = {"type": "goto", "x": 5, "y": 64, "z": 0}
        rows = [("sent from the feet", [mine], (0, 64, 0), [(mine, (0, 64, 0))]),
                ("must fail: after a goto, from its cell", [go, mine], (0, 64, 0), [(mine, (5, 64, 0))]),
                ("a walk alone stands for nothing", [go], (0, 64, 0), [])]
        for name, tasks, feet, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.task_stands(tasks, feet), want)

    def test_stands_for(self):
        ore, wall = ground([((2, 64, 0), "iron_ore")]), ground([((3, 64, 0), "chest"), ((2, 64, 0), "stone"),
                                                                ((2, 65, 0), "stone"), ((2, 66, 0), "stone")])
        rows = [("mine in sight 2 off", "mine", ore, (0, 64, 0), (2, 64, 0), True),
                ("must fail: mine behind a wall", "mine", wall, (0, 64, 0), (3, 64, 0), False),
                ("place on the ground ahead: a face in sight", "place", ground(), (0, 64, 0), (2, 64, 0), True),
                ("must fail: place into the body's own cell", "place", ground(), (0, 64, 0), (0, 64, 0), False),
                ("use a block in sight", "use", ore, (0, 64, 0), (2, 64, 0), True),
                ("must fail: use behind a wall", "use", wall, (0, 64, 0), (3, 64, 0), False),
                ("stand: on it", "stand", ground(), (1, 64, 0), (1, 64, 0), True),
                ("stand: elsewhere", "stand", ground(), (0, 64, 0), (1, 64, 0), False)]
        for name, kind, region, feet, cell, want in rows:
            with self.subTest(name):
                self.assertIs(nav.stands_for(kind, region, feet, cell), want)


class Gate(unittest.TestCase):
    """nav.gate (I4): every mine/place/use from a stand that holds; a failing one gets its way first, once per try;
    a way's own step that fails is NavFailed, never a way inside a way."""
    ORE = ground([((2, 64, 0), "iron_ore")])
    MINE_NEAR = {"type": "mine", "x": 2, "y": 64, "z": 0}
    MINE_FAR = {"type": "mine", "x": 5, "y": 64, "z": 0}

    def run_gate(self, tasks, region, moves_to=None, in_way=0):
        here, ways = [(0, 64, 0)], []

        def reach(task, policy, faces=None, at=None):
            ways.append((task["x"], task["y"], task["z"]))
            if moves_to is not None:
                here[0] = moves_to
        with mock.patch.object(nav, "feet", lambda: here[0]), mock.patch.object(nav, "_read_box", lambda *a, **k: region), \
                mock.patch.object(nav, "reach_stand", reach), mock.patch.object(nav, "_IN_WAY", [in_way]):
            return nav.gate(tasks, nav.Policy()), ways

    def test_rows(self):
        far = ground([((5, 64, 0), "iron_ore")])
        after, ways = self.run_gate([self.MINE_NEAR], self.ORE)
        self.assertTrue(callable(after))
        self.assertEqual(ways, [], "a stand that holds: nothing walked")
        _after, ways = self.run_gate([self.MINE_FAR], far, moves_to=(3, 64, 0))
        self.assertEqual(ways, [(5, 64, 0)], "must fail: sent from a stand that cannot reach it")
        with self.assertRaises(api.NavFailed):
            self.run_gate([self.MINE_FAR], far)                    # the way never reaches a stand: NavFailed
        _after, ways = None, []
        with self.assertRaises(api.NavFailed):
            _after, ways = self.run_gate([self.MINE_FAR], far, in_way=1)
        self.assertEqual(ways, [], "inside a way: no way asked for its own step (no recursion)")
        self.assertEqual(self.run_gate([{"type": "goto", "x": 1, "y": 64, "z": 0}], self.ORE), (None, []))


class Unplanned(unittest.TestCase):
    """nav.unplanned_cells (R4): a cell that turned air or solid with no task naming it."""

    def test_rows(self):
        lo, hi = (-3, 60, -3), (3, 68, 3)

        def box(**cells):
            base = {(x, 63, z): "stone" for x in range(-3, 4) for z in range(-3, 4)}
            base.update({tuple(map(int, k.split("_"))): v for k, v in cells.items()})
            return FakeRegion(lo, hi, base)
        stone_wall = {"2_64_0": "stone"}
        mine = [{"type": "mine", "x": 2, "y": 64, "z": 0}]
        rows = [("the named cell mined: nothing unplanned", box(**stone_wall), box(), mine, []),
                ("must fail: a cell no task named broken", box(**stone_wall, **{"1_64_1": "dirt"}), box(), mine,
                 [((1, 64, 1), "dirt", "air")]),
                ("sand that fell: on its own", box(**{"1_65_1": "sand"}), box(**{"1_64_1": "sand"}), [], []),
                ("a pillar's column under the body", box(), box(**{"0_64_0": "dirt"}), [{"type": "pillar"}], []),
                ("must fail: a block placed unasked", box(), box(**{"1_64_1": "cobblestone"}), [],
                 [((1, 64, 1), "air", "cobblestone")])]
        for name, before, after, tasks, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.unplanned_cells(before, after, tasks, (0, 64, 0)), want)


class OneReadPerSegment(unittest.TestCase):
    """world.bag / world.box (R-a, R-b): a read serves every step until something is sent."""

    def test_bag(self):
        reads = []
        with mock.patch.object(world, "Inventory", lambda *a: reads.append(1) or object()), \
                mock.patch.object(api.STATE, "posts", 0), mock.patch.dict(world.READS, clear=True):
            first = world.inventory_now()
            self.assertIs(world.inventory_now(), first)
            api.STATE.posts += 1
            self.assertIsNot(world.inventory_now(), first)
        self.assertEqual(len(reads), 2, "must fail: the bag read again with nothing sent between")

    def test_box(self):
        reads = []

        def region(lo, hi, props=False):
            reads.append((lo, hi))
            return FakeRegion(lo, hi, {})
        FakeRegion.covers = world.Region.covers        # the read box's own test
        try:
            with mock.patch.object(world, "Region", region), mock.patch.object(api.STATE, "posts", 0), \
                    mock.patch.dict(world.READS, clear=True):
                world.box((0, 60, 0), (9, 70, 9))
                world.box((2, 62, 2), (5, 66, 5))            # inside it: reused
                world.box((0, 60, 0), (12, 70, 9))           # wider: read
                api.STATE.posts += 1
                world.box((0, 60, 0), (12, 70, 9))           # a send between: read
        finally:
            del FakeRegion.covers
        self.assertEqual(len(reads), 3, f"must fail: a covered box read again {reads}")


class NoRoute(unittest.TestCase):
    """nav._travel: a leg with no route gets one way (plan_way's, NavFailed when none) and is judged at the asked
    cell — never LEGS more asks; the ground retry only for a guessed y."""

    def travel(self, way_raises=None, y_guess=False, walk=False):
        ways, legs = [], []

        def reach(task, policy, faces=None, at=None, est=None):
            ways.append(task)
            if way_raises:
                raise way_raises
        column = FakeRegion((10, 32, 0), (10, 96, 0), {(10, y, 0): "stone" for y in range(32, 60)})
        empty = nav.Inventory({"slots": [], "equipment": {}})       # a bag read as the walk reads it (way_bag: its slots)

        with mock.patch.object(nav, "feet", lambda: (0, 64, 0)), \
                mock.patch.object(nav, "Inventory", lambda *a: empty), \
                mock.patch.object(nav, "_read_box", lambda *a, **k: column), \
                mock.patch.object(nav, "Region", lambda *a, **k: column), \
                mock.patch.object(nav, "DOORS", None), mock.patch.object(nav, "_doorways_between", lambda *a: {}), \
                mock.patch.object(nav, "_plan_reply", lambda *a, **k: {"found": walk}), \
                mock.patch.object(nav, "reach_stand", reach), \
                mock.patch.object(nav, "_leg", lambda t, aw: legs.append(t) or {"status": "failed",
                                                                                 "message": "no route"}), \
                mock.patch.object(nav, "_arrived", lambda f, p, b, ok, closer=False: ok), \
                mock.patch.object(api, "get", lambda path: {"x": 0.5, "y": 64, "z": 0.5}), \
                mock.patch.object(api, "at_boundary", lambda: None), mock.patch.object(api, "detail", lambda *a: None):
            got = nav._travel((10, 64, 0), nav.Policy(), 1.5, 3, None, "work", (0, 64, 0), 0.0, y_guess)
        return got, ways, legs

    def test_rows(self):
        got, ways, legs = self.travel()
        self.assertEqual((got, len(ways), legs), (False, 1, []), "must fail: the way asked again every leg")
        with self.assertRaises(api.NavFailed):
            self.travel(way_raises=api.NavFailed("no way to stand (10, 64, 0): fluid at (9, 64, 0)"))

    def test_ground_retry_only_for_a_guessed_y(self):
        _got, _ways, legs = self.travel(walk=True)
        self.assertEqual({t["y"] for t in legs}, {64}, "must fail: a known y (an ore's) rewritten to the ground")
        _got, _ways, legs = self.travel(walk=True, y_guess=True)
        self.assertIn(60, {t["y"] for t in legs}, "a guessed y: retried once on the column's ground")


class WayTo(unittest.TestCase):
    """nav.way_to (V4/V5): a cell the jar's walk could not reach gets a planned way (reach_stand: plan_way's named
    steps), never a walk that digs; no way → False, said."""

    def test_rows(self):
        rows = [("a way: there", None, True),
                ("must fail: no way read as there", api.NavFailed("no way to mine (5, 64, 0): fluid at (4, 64, 0)"),
                 False)]
        for name, raises, want in rows:
            asked = []

            def reach(task, policy, _r=raises):
                asked.append((task["type"], task["x"]))
                if _r:
                    raise _r
            with self.subTest(name), mock.patch.object(nav, "reach_stand", reach), \
                    mock.patch.object(nav, "feet", lambda: (0, 64, 0)), mock.patch.object(api, "detail", lambda *a: None):
                got = nav.way_to(mock.Mock(policy=nav.Policy()), [(9, 64, 0), (5, 64, 0)])
            self.assertEqual((got, asked), (want, [("mine", 5)]))       # the nearest cell, as a mine's stand

    def test_the_gaze_reflex_is_set_by_python(self):
        self.assertIs(fight_loop.ALWAYS.get("gaze"), True, "must fail: the jar's gaze left to its own default")


class WeaponAtEngage(unittest.TestCase):
    """fight_loop._engagement (B3): the weapon named by ARM is put in hand before the first answer."""

    def test_rows(self):
        held, armed = [], []
        option = mock.Mock(target=42, kind="fight")
        with mock.patch.object(api, "ARM", lambda ts: armed.append(ts) or [{**ts[0], "item": "minecraft:iron_sword"}]), \
                mock.patch.object(api, "HOLD", held.append), mock.patch.object(fight_loop.STATE, "want", option), \
                mock.patch.object(fight_loop.arbiter.BODY, "carry", lambda intent, loop: None), \
                mock.patch.object(fight_loop, "disengage", lambda *a, **k: None):
            fight_loop._engagement(object(), {})
        self.assertEqual(armed, [[{"type": "attack", "entity": 42}]])
        self.assertEqual(held, [[{"type": "attack", "entity": 42, "item": "minecraft:iron_sword"}]],
                         "must fail: the weapon first named by the attack task (its switch resets the cooldown)")


class TargetOfAFailure(unittest.TestCase):
    """api.McError(pos=…): every skill failure may name the cell it was about (brain bans and cools it there)."""

    def test_rows(self):
        rows = [("a chest that would not open", api.McError("no screen", pos=[5, 64, 5]), (5, 64, 5)),
                ("must fail: NotAvailable inherits it", api.NotAvailable("no prey", pos=(1, 2, 3)), (1, 2, 3)),
                ("NavFailed as before", api.NavFailed("no way", pos=(0, 64, 0)), (0, 64, 0)),
                ("none named", api.McError("x"), None)]
        for name, err, want in rows:
            with self.subTest(name):
                self.assertEqual(err.pos, want)
                self.assertEqual(str(err), err.args[0])


class FightTrail(unittest.TestCase):
    """api.trail_line: why an attack ended — its target last seen, how long ago, died or vanished."""

    def test_rows(self):
        def seen(t, hp, dist, pos=(3.0, 64.0, 0.0)):
            return {"t": t, "entity": 20994, "body": (0.5, 64.0, 0.5), "seen": True, "pos": pos, "hp": hp, "dist": dist}

        def gone(t):
            return {"t": t, "entity": 20994, "body": (0.5, 64.0, 0.5), "seen": False, "pos": None, "hp": None,
                    "dist": None}
        rows = [("killed: hp 0 last seen", [seen(0, 6.0, 2.1), seen(1, 0.0, 2.0)], 1.5, "died (hp 0)"),
                ("must fail: gone near at full hp: vanished, not died", [seen(0, 20.0, 2.0), gone(1)], 1.5,
                 "vanished (unloaded or gone)"),
                ("walked out past the read", [seen(0, 20.0, 63.0), gone(1)], 1.5, "vanished (out of range)"),
                ("still there", [seen(0, 20.0, 2.0)], 0.5, "still there")]
        for name, samples, now, verdict in rows:
            with self.subTest(name):
                line = api.trail_line(7, samples, now)
                self.assertTrue(line.endswith(verdict), line)
        line = api.trail_line(7, [seen(0, 20.0, 2.0), gone(1)], 1.5)
        self.assertIn("last seen (3.0, 64.0, 0.0) hp 20.0 2.0 off, 1.5s ago", line)
        self.assertIn("never seen", api.trail_line(7, [gone(0)], 1.0))


if __name__ == "__main__":
    unittest.main()
