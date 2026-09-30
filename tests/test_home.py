"""The player's home: a site whose box is never broken (but for our own blocks), whose beds, chests and stations are
used as they stand, whose decor is never struck, where nothing is poured or lit; a rescue at critical hp may dig."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, memory, nav  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

DIM = "minecraft:overworld"
LO, HI = (0, 64, 0), (9, 70, 9)
BLOCKS = {(1, 64, 1): "minecraft:red_bed", (2, 64, 1): "minecraft:red_bed", (5, 64, 5): "minecraft:chest",
          (7, 64, 2): "minecraft:crafting_table", (7, 64, 4): "minecraft:furnace", (0, 64, 0): "minecraft:stone_bricks",
          (3, 65, 3): "minecraft:air"}


def a_home(tmp):
    mem = memory.Memory(os.path.join(tmp, "notes.json"))
    mem.add_home("home", [(LO, HI)], DIM, BLOCKS)
    return mem


class TheHome(unittest.TestCase):
    def test_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            parts = mem.homes(DIM)[0]["parts"]
            self.assertEqual(len(parts["beds"]), 2)
            self.assertEqual(parts["chests"], [[5, 64, 5]])
            self.assertEqual(sorted(b for b, _p in parts["stations"]), ["crafting_table", "furnace"])
            self.assertIn((7, 64, 2), {tuple(s["pos"]) for s in mem.stations(DIM)}, "its stations are memory's")
            self.assertEqual(mem.home_part("chests", DIM, (4, 64, 4)), (5, 64, 5),
                             "must fail: the home chest not offered for storing")
            self.assertEqual(mem.home_part("stations", DIM, (4, 64, 4), "crafting_table"), (7, 64, 2))
            self.assertIsNone(mem.home_part("chests", DIM, (40, 64, 40)), "outside, not asked for anywhere")

    def test_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            mem.note_placed((4, 65, 4), DIM)
            p = mem.protected_cells(DIM)
            rows = [("a home block", (0, 64, 0), True), ("air in the box: nothing may be broken there", (3, 66, 3), True),
                    ("must fail: our own block inside, ours to take back", (4, 65, 4), False),
                    ("outside the box", (20, 64, 0), False)]
            for name, cell, want in rows:
                with self.subTest(name):
                    self.assertEqual(cell in p, want)
            self.assertLess(len(set(p)), 100, "the box is asked, never listed")

    def test_refusals(self):
        homes = [{"snapshot": {"lo": list(LO), "hi": list(HI)}}]
        ours = {(4, 65, 4)}
        at = {1: ("minecraft:armor_stand", (3, 65, 3)), 2: ("minecraft:zombie", (3, 65, 3)),
              3: ("minecraft:armor_stand", (30, 65, 3))}
        # (situation, task, critical allowance) → refused
        rows = [("must fail: a home block broken (gather, unstuck)", {"type": "mine", "x": 0, "y": 64, "z": 0}, False, True),
                ("our own block taken back", {"type": "mine", "x": 4, "y": 65, "z": 4}, False, False),
                ("at critical hp a rescue may dig", {"type": "mine", "x": 0, "y": 64, "z": 0}, True, False),
                ("must fail: an armor stand in the home struck", {"type": "attack", "entity": 1}, False, True),
                ("a zombie in the home is fought", {"type": "attack", "entity": 2}, False, False),
                ("an armor stand outside is none of the home's", {"type": "attack", "entity": 3}, False, False),
                ("must fail: water poured inside", {"type": "place", "item": "minecraft:water_bucket",
                                                    "x": 3, "y": 65, "z": 3}, False, True),
                ("must fail: dirt placed inside (a wall-in, a reshape in the hall)",
                 {"type": "place", "item": "minecraft:dirt", "x": 3, "y": 65, "z": 3}, False, True),
                ("must fail: a pillar inside (its cell the feet's)", {"type": "pillar", "item": "minecraft:cobblestone"},
                 False, True),
                ("a crafting table placed inside: allowed", {"type": "place", "item": "minecraft:crafting_table",
                                                             "x": 3, "y": 65, "z": 3}, False, False),
                ("a torch inside: allowed", {"type": "place", "item": "minecraft:torch", "x": 3, "y": 65, "z": 3},
                 False, False),
                ("dirt outside the home: none of the home's", {"type": "place", "item": "minecraft:dirt",
                                                               "x": 30, "y": 65, "z": 3}, False, False),
                ("opening a chest inside: allowed", {"type": "use", "x": 5, "y": 64, "z": 5}, False, False)]
        for name, task, allow, refused in rows:
            with self.subTest(name):
                why = memory.home_refusal(task, homes, ours, lambda eid: at.get(eid), allow_break=allow,
                                          feet=(3, 65, 3))
                self.assertEqual(why is not None, refused, why)

    def test_the_door_refuses(self):
        """api.post: the guard sees every task (a missed caller can't break the home)."""
        def guard(task):
            if task.get("type") == "mine":
                raise api.NotAvailable("part of the home")
        with mock.patch.object(api, "GUARD", guard), mock.patch.object(api, "api", return_value={"tasks": []}) as sent:
            with self.assertRaises(api.NotAvailable):
                api.post("/task?wait=0", {"tasks": [{"type": "mine", "x": 0, "y": 64, "z": 0}]})
            self.assertFalse(sent.called, "must fail: the break went out")


class UsedAsItStands(unittest.TestCase):
    def test_the_home_table(self):
        """Crafting in the home: its table, walked to — no new one placed, none taken back."""
        from bonobo import craft
        from tests.world import bag, inventory
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            ctx = mock.Mock(mem=mem, dimension=DIM)
            for name, home, want in [("the home's table", True, []),
                                     ("must fail: no home table: one placed and taken back", False, ["place", "mine"])]:
                with self.subTest(name):
                    sent = []
                    inv = bag(inventory(oak_planks=12, cobblestone=3))
                    with mock.patch.object(craft, "Inventory", lambda: inv), \
                            mock.patch.object(craft, "find", lambda *a, **k: []), \
                            mock.patch.object(craft, "feet", lambda: (4, 64, 4) if home else (40, 64, 40)), \
                            mock.patch.object(craft.nav, "arrived", lambda *a, **k: True), \
                            mock.patch.object(craft, "free_spots_here", lambda limit=1: [(41, 64, 40)]), \
                            mock.patch.object(craft, "close_screen", lambda: None), \
                            mock.patch.object(craft, "_standing", lambda *a: False), \
                            mock.patch.object(craft, "run_split", lambda tasks, wait: sent.extend(tasks)):
                        craft._sitting(ctx, [("minecraft:stick", 1), ("minecraft:crafting_table", 1),
                                             ("minecraft:stone_pickaxe", 1)])
                    self.assertEqual([t["type"] for t in sent if t["type"] in ("place", "mine")], want)

    def test_gathering_skips_the_home(self):
        from bonobo import gather
        p = memory.Protected((), [(LO, HI)])
        found = [{"x": 3, "y": 65, "z": 3, "block": "minecraft:oak_log"}, {"x": 30, "y": 65, "z": 3, "block": "minecraft:oak_log"}]
        hits = gather.seek_hits(["minecraft:oak_log"], found, 16, lambda p: False, p)
        self.assertEqual([h["x"] for h in hits], [30], "must fail: a log in the home offered for gathering")


class TheGuard(unittest.TestCase):
    """brain.home_guard: api's door for the home — refusals raise, a critical break is said, our placing is noted."""

    def test_rows(self):
        from types import SimpleNamespace
        from bonobo import brain, events
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            me = SimpleNamespace(mem=mem)
            said = []
            with mock.patch.object(api.STATE, "dim_seen", DIM), \
                    mock.patch.object(events, "emit", lambda kind, line, *a, **k: said.append(kind)):
                brain.Brain.home_guard(me, {"type": "place", "item": "minecraft:crafting_table", "x": 3, "y": 65,
                                            "z": 3})
                self.assertIn((3, 65, 3), mem.placed_in_home(DIM), "our block inside, noted")
                brain.Brain.home_guard(me, {"type": "mine", "x": 3, "y": 65, "z": 3})
                self.assertNotIn((3, 65, 3), mem.placed_in_home(DIM), "taken back: no longer ours to note")
                with self.assertRaises(api.NotAvailable):
                    brain.Brain.home_guard(me, {"type": "mine", "x": 0, "y": 64, "z": 0})
                self.assertEqual(said, [], "must fail: a refused break said as done")
                with api.home_break_allowed("buried at 3 hp"):
                    brain.Brain.home_guard(me, {"type": "mine", "x": 0, "y": 64, "z": 0})
                self.assertEqual(said, ["home_break"], "a critical break is an event")


class NoDigThroughTheHome(unittest.TestCase):
    """nav.home_flags: before a leg that may dig, the game's route is asked; one that stands, digs or builds in a
    home box goes with both off (09:35: the hall's west wall dug by a walk to coal)."""
    SECOND = ((12, 64, 0), (15, 70, 9))            # a home of two boxes, natural rock between them (x 10..11)

    def flags(self, dug, walk, here=(20, 64, 5), pos=(30, 64, 5)):
        boxes = [(LO, HI), self.SECOND]
        answers = {True: dug, False: walk}
        with mock.patch.object(nav, "_plan_reply", lambda cell, brk, plc, r, *a: answers[brk]), \
                mock.patch.object(nav, "HOME_DOOR", lambda *a: None):
            return nav.home_flags(pos, True, True, 1.5, boxes, here)

    @staticmethod
    def route(*steps):
        return {"found": True, "steps": [{"x": x, "y": y, "z": z, "actions": acts} for (x, y, z), acts in steps]}

    def test_rows(self):
        walk = self.route(((20, 64, 5), []))
        rows = [("must fail: a route digging a home cell posted with break on",
                 self.route(((10, 64, 5), ["MINE 9,64,5"])), walk, (False, False, False)),
                ("near, not through: it digs outside the home", self.route(((10, 64, 5), ["MINE 10,64,5"])), walk,
                 (True, True, False)),
                ("two boxes: the rock between them is mined", self.route(((11, 64, 5), ["MINE 11,65,5"])), walk,
                 (True, True, False)),
                ("a route standing in the home: both off (a re-path digs nothing)",
                 self.route(((5, 64, 5), [])), walk, (False, False, False)),
                ("a pillar in the home: both off", self.route(((20, 64, 5), ["PILLAR 13,65,5"])), walk,
                 (False, False, False))]
        for name, dug, w, want in rows:
            with self.subTest(name):
                self.assertEqual(self.flags(dug, w), want)
        # no walk that digs nothing and the body in the home: by its door
        self.assertEqual(self.flags(self.route(((8, 64, 5), ["MINE 9,64,5"])), {"found": False}, here=(5, 64, 5)),
                         (False, False, True))
        between = memory.Protected((), [(LO, HI), self.SECOND])
        self.assertNotIn((11, 65, 5), between)          # rock between the boxes: not the home's

    def test_doors_and_home_both(self):
        """A walk's avoid: a taught door's cells (mechanisms, pressed not dug); a press in the home is no break."""
        door = (5, 64, 10)
        got = nav.avoid_fields(memory.Protected((), [(LO, HI)]) | {door}, (5, 64, 12))
        cells = {(c["x"], c["y"], c["z"]) for c in got["avoid"]}
        self.assertIn(door, cells, "must fail: the door dug")
        press = {"type": "use", "x": 5, "y": 65, "z": 9}
        self.assertIsNone(memory.home_refusal(press, [{"boxes": [[list(LO), list(HI)]]}], set()),
                          "must fail: pressing a button in the home refused as a break")


class Unbury(unittest.TestCase):
    def test_step_out(self):
        from bonobo import survive
        feet = (5, 65, 5)
        floor = {(x, 64, z): "stone" for x in range(4, 7) for z in range(4, 7)}
        walls = {(x, y, z): "stone" for x in range(4, 7) for z in range(4, 7) for y in (65, 66) if (x, z) != (5, 5)}
        open_side = FakeRegion((4, 64, 4), (6, 67, 6), {**floor, **{c: n for c, n in walls.items() if c[0] != 6}})
        shut = FakeRegion((4, 64, 4), (6, 67, 6), {**floor, **walls})
        self.assertEqual(survive.step_out_cell(open_side, feet), (6, 65, 5), "a free side: stepped out, nothing broken")
        self.assertIsNone(survive.step_out_cell(shut, feet), "must fail: walled in read as a way out")

    def test_a_home_block_only_at_critical(self):
        from bonobo import survive
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            ctx = mock.Mock(mem=mem, policy=mock.Mock(protected=mem.protected_cells(DIM)))
            shut = FakeRegion((4, 63, 4), (6, 67, 6), {(x, y, z): "stone" for x in range(4, 7) for z in range(4, 7)
                                                       for y in (64, 65, 66) if (x, y, z) != (5, 65, 5)})
            for name, hp, want in [("must fail: full hp, a home block broken", 20.0, api.NotAvailable),
                                   ("critical hp: broken, allowed", 3.0, None)]:
                with self.subTest(name):
                    state = {"blockX": 5, "blockY": 65, "blockZ": 5, "y": 65.0, "health": hp, "dimension": DIM}
                    sent = []
                    with mock.patch.object(survive.api, "get", return_value=state), \
                            mock.patch.object(survive, "Region", lambda lo, hi: shut), \
                            mock.patch.object(survive.api, "run", side_effect=lambda t, **k: sent.append(
                                (t, api.STATE.home_break))):
                        gen = survive.unbury.__wrapped__(ctx)
                        if want is not None:
                            with self.assertRaises(want):
                                next(gen)
                            self.assertEqual(sent, [])
                        else:
                            next(gen)
                            self.assertEqual(sent[0][0]["type"], "mine")
                            self.assertIsNotNone(sent[0][1], "the break went out under the allowance")


class NoBuildingInsideTheHome(unittest.TestCase):
    """fight_loop.batch: an answer that places a block the home may not hold inside it is no answer there (hello2
    08:52:51: 'fight wall_in: posts place(granite), place(dirt)×5' in the bunker's hall)."""

    def test_rows(self):
        from bonobo import brain, fight_loop, threat  # noqa: F401  (brain lends wall_in)
        from tests.world import bag, inventory
        home = memory.Protected((), [(LO, HI)])
        inside, outside = (3, 65, 3), (30, 65, 3)
        rows = [("must fail: a wall-in inside the home", "wall_in", None, inside, False),
                ("must fail: a roof reshape inside", "reshape", ("roof", 1), inside, False),
                ("must fail: a pillar inside", "reshape", ("under", 2), inside, False),
                ("a wall-in outside it: planned", "wall_in", None, outside, True)]
        for name, kind, target, feet, planned in rows:
            with self.subTest(name):
                region = FakeRegion(tuple(v - 3 for v in feet), tuple(v + 3 for v in feet),
                                    {(feet[0] + dx, feet[1] - 1, feet[2] + dz): "stone"
                                     for dx in range(-3, 4) for dz in range(-3, 4)})
                state = {"feet": feet, "region": region, "inv": bag(inventory(("dirt", 16))), "protected": home,
                         "state": {"x": feet[0] + 0.5, "y": feet[1], "z": feet[2] + 0.5}}
                option = threat.Option(kind, target, 1.0, 2.0, "test")
                got = fight_loop.batch(option, state)
                self.assertEqual(bool(got), planned, got)


class ARegistrationIsNeverLost(unittest.TestCase):
    """The homes live in their own file, written only by `mc.py home`: a bot holding memory read before the home was
    added saves its notes and the home stays (hello2: an old bot's save erased 'bunker')."""

    def test_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = os.path.join(tmp, "notes.json")
            bot = memory.Memory(notes)                      # the running bot, loaded before the home existed
            cli = memory.Memory(notes)
            cli.add_home("bunker", [(LO, HI)], DIM, BLOCKS)     # mc.py home add, another process
            bot.note_placed((3, 65, 3), DIM)                # the bot saves its notes
            bot.save()
            # must fail: the bot's save wrote its home-less notes over the home
            self.assertEqual([h["name"] for h in memory.Memory(notes).homes(DIM)], ["bunker"])
            self.assertEqual([h["name"] for h in bot.homes(DIM)], ["bunker"])      # the bot sees it too, unrestarted

    def test_a_home_kept_in_old_notes_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = os.path.join(tmp, "notes.json")
            site = {"name": "old", "kind": "home", "pos": [4, 64, 4], "dimension": DIM,
                    "snapshot": {"lo": list(LO), "hi": list(HI), "blocks": {}}, "parts": {}}
            memory.write_notes(notes, {"sites": [site]})
            mem = memory.Memory(notes)
            self.assertEqual([h["name"] for h in mem.homes(DIM)], ["old"])
            mem.save()
            self.assertEqual(memory.read_notes(notes)["sites"], [])
            self.assertEqual([h["name"] for h in memory.Memory(notes).homes(DIM)], ["old"])


class TheStartSaysIt(unittest.TestCase):
    """brain.autoplay's start reads the registrations (memory's homes, mechanisms' lessons) and says them."""

    def test_a_registered_home_is_said(self):
        # must fail: a start that loads nothing, or says nothing of the home and the taught doors
        from bonobo import brain, events, mechanisms
        with tempfile.TemporaryDirectory() as tmp:
            mem = a_home(tmp)
            lessons = os.path.join(tmp, "mechanisms.json")
            mechanisms.add(DIM, (1, 65, 0), [(2, 64, 0), (2, 65, 0)], path=lessons)
            said = []
            with mock.patch.object(api, "get", return_value={"dimension": DIM}), \
                    mock.patch.object(mechanisms, "FILE", lessons), \
                    mock.patch.object(events, "emit", lambda kind, line, **k: said.append((kind, line))):
                brain.say_registrations(mock.Mock(mem=mem))
            home = mem.homes(DIM)[0]
        (kind, line), = said
        self.assertEqual(kind, "start")
        self.assertIn(f"home home: {len(home['snapshot']['blocks'])} blocks", line)
        self.assertIn(f"{len(home['parts']['beds'])} beds", line)
        self.assertIn("mechanisms: 1", line)

    def test_nothing_registered_is_said_too(self):
        from bonobo import brain
        self.assertEqual(brain.registrations([], []), "no home registered; mechanisms: 0")

    def test_autoplay_says_it(self):
        import ast
        from bonobo import brain
        with open(brain.__file__, encoding="utf-8") as f:
            src = f.read()
        body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "autoplay")
        self.assertIn("say_registrations(brain)", ast.get_source_segment(src, body))


if __name__ == "__main__":
    unittest.main()
