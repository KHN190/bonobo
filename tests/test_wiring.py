"""The fight's wiring, by behaviour: each rule is shown doing its work on controlled input through the real
modules — what the planner picks is what the fight posts, the bunker is dug where the geometry says, the threat
model's arrival times decide, the safe step is the model's, perception bids and interrupts on pressure, the one
answer loop carries a fight, threat rows are differenced in one place. (These replace a scan of who calls whom in
the source: a name being called somewhere is not the rule being kept.)"""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import (bunker, combat_model, end, fight_loop, fight_plan, nav, perception, threat)  # noqa: E402
from tests.test_fight_plan import BOMB, DRAGON, view as dragon_view, state as fight_state  # noqa: E402

INF = float("inf")


class ThePlannerDrivesTheDragon(unittest.TestCase):
    """fight_plan.Fight.plan picks the intent; fight_loop.dragon_answer turns it into what is posted."""

    ROWS = [("sitting, healthy, a bomb window from the pit: the bomb", {}, dict(bomb=BOMB), "bed_bomb"),
            ("sitting, healthy, no bed to bomb with: melee on the dragon", {}, {}, "fight"),
            ("everything refused by the planner: back into cover",
             {"phase": 4, "elapsed": 0.8, "beds": 0, "tunnel": False, "crystals": 0, "obsidian": 0},
             dict(cover=(5, 62, 0)), "evade"),
            ("the dragon dead: nothing posted", {}, dict(dead=True), None)]

    def test_plan_to_post(self):
        fight = fight_plan.Fight()
        for name, st, v, want in self.ROWS:
            with self.subTest(name):
                answer = fight_loop.dragon_answer(fight.plan(fight_state(**st)), dragon_view(**v))
                self.assertEqual(answer.kind if answer else None, want)


class TheBunkerIsDug(unittest.TestCase):
    """bunker.dig_plan: the shaft at the mouth first, then out along the side; feet then head, cell by cell."""

    def test_dig_plan_over_the_sides(self):
        for side in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            with self.subTest(side=side):
                cells = bunker.dig_plan(side, 64)
                self.assertEqual(cells[0], bunker.mouth(side, 64))
                self.assertEqual(cells[::2], bunker.tunnel(side, 64))
                self.assertEqual(cells[1::2], [(x, y + 1, z) for x, y, z in bunker.tunnel(side, 64)])

    def test_no_side_is_no_tunnel(self):
        """Must fail to be a tunnel: a side of (0, 0) digs one column over and over."""
        feet = bunker.dig_plan((0, 0), 64)[::2]
        self.assertEqual(len(set(feet)), 1)


class TheThreatModelDecides(unittest.TestCase):
    """combat_model.tti — the arrival time the fight's veto compares — and the union over threats (min_tti)."""

    ROWS = [("already inside the reach", (1, 0, 0), (0, 0, 0), 0.0),
            ("at rest outside: never", (10, 0, 0), (0, 0, 0), INF),
            ("coming at 7 b/s from 10 with a reach of 3: in 1 s", (10, 0, 0), (-7, 0, 0), 1.0),
            ("going away: never", (10, 0, 0), (7, 0, 0), INF),
            ("coming, but past the horizon (27 s): not now", (30, 0, 0), (-1, 0, 0), INF)]

    def test_tti(self):
        for name, pos, vel, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(combat_model.tti(pos, vel, (0, 0, 0), 3.0), want)

    def test_the_first_of_several_arrives(self):
        near = ((10, 0, 0), 3.0, (-7, 0, 0))
        far = ((0, 0, 20), 3.0, (0, 0, -1))
        self.assertEqual(combat_model.min_tti((0, 0, 0), [far, near]), 1.0)


class TheSafeStepIsTheModels(unittest.TestCase):
    """nav.safe_destination: every walk's target is kept out of what hurts, by combat_model.best_step."""

    def test_destinations(self):
        here = (0.0, 64.0, 0.0)
        rows = [("nothing around: go there", [], "same"),
                ("a still hazard 20 away: go there", [((20.0, 64.0, 0.0), 3.0, (0.0, 0.0, 0.0))], "same"),
                ("the target inside a small hazard: a step out of it", [(here, 1.5, (0.0, 0.0, 0.0))], "out"),
                ("everything within a step is inside: nowhere (must fail)", [(here, 10.0, (0.0, 0.0, 0.0))], None)]
        for name, hazards, want in rows:
            with self.subTest(name):
                got = nav.safe_destination(here, hazards)
                if want == "same":
                    self.assertEqual(got, here)
                elif want == "out":
                    self.assertNotEqual(got, here)
                    self.assertGreater(math.dist(got, here), 1.5)
                else:
                    self.assertIsNone(got)


def _row(kind, x):
    return threat.row((x, 64.0, 0.0), threat.MOBS[kind]["reach"], (0.0, 0.0, 0.0), kind)


class PerceptionBidsThreats(unittest.TestCase):
    """fight_loop.bid: what perception offers the body for the rows it sees (threat.options → decide)."""

    STATE = {"x": 0.0, "y": 64.0, "z": 0.0, "health": 20, "armor": 15, "sword_tier": 2, "blocks": 0,
             "food_items": 0}
    ROWS = [("nothing seen: nothing offered", [], None),
            ("a zombie 4 off, an iron sword: fight it", [_row("minecraft:zombie", 4.0)], "fight"),
            ("a zombie 40 off: nothing owed yet", [_row("minecraft:zombie", 40.0)], None),
            ("a creeper 4 off: never traded with — away", [_row("minecraft:creeper", 4.0)], "evade")]

    def test_bids(self):
        sstate = threat.price_state(hp=20, armor=15)
        price = lambda dhp: threat.hp_seconds(sstate, dhp)   # noqa: E731
        for name, rows, want in self.ROWS:
            with self.subTest(name), mock.patch.object(fight_loop, "HELD", None):
                got = fight_loop.bid(self.STATE, rows, price, ids=list(range(len(rows))))
                self.assertEqual(got[0].kind if got else None, want)


class PerceptionInterruptsOnPressure(unittest.TestCase):
    """perception.danger: the model's time to die interrupts at full health; low health alone above the floor
    does not."""

    BASE = {"health": 20, "dimension": "minecraft:overworld", "control": {}, "inWater": False, "air": 300,
            "onGround": True}
    ROWS = [("full health, dead in 0.1 s at this pressure: interrupt", {}, 0.1, "hostiles"),
            ("8 hp, nothing pressing (dead in 10 min): no interrupt (must not)", {"health": 8}, 600.0, None),
            ("3 hp: the floor, whatever presses", {"health": 3}, 600.0, "critical_health"),
            ("dead in 0.1 s but swinging already: the fight answers, no interrupt",
             {"control": {"task": {"type": "attack"}}}, 0.1, None)]

    def test_danger(self):
        for name, changes, ttd, want in self.ROWS:
            with self.subTest(name):
                got = perception.danger(dict(self.BASE, **changes), time_to_die=lambda t=ttd: t)
                self.assertEqual(got, want)


class OneLoopCarriesTheFight(unittest.TestCase):
    """fight_loop.carry posts each answer's batch (fight_loop.batch), keeps it while it holds, re-posts on change."""

    A = fight_loop.Answer
    BODY = {"feet": (0, 64, 0), "inv": type("Bag", (), {"count": lambda self, i: 0,
                                                         "offhand": lambda self: "minecraft:air"})(),
            "protected": set(), "state": {"x": 0.0, "y": 64.0, "z": 0.0}}
    ROWS = [("the same fight twice: one attack posted", [A("fight", 7), A("fight", 7)], [["attack"]]),
            ("fight, then away: attack, stopped, a walk", [A("fight", 7), A("evade", (10, 64, 0))],
             [["attack"], "/stop", ["travel"]]),
            ("a walk re-aimed a block off: the same walk", [A("evade", (10, 64, 0)), A("evade", (11, 64, 0))],
             [["travel"]]),
            ("nothing wanted: nothing posted", [], [])]

    def test_passes(self):
        for name, wants, want in self.ROWS:
            posted, left = [], list(wants)

            def answer(a):
                posted.append([t["type"] for t in fight_loop.batch(a, self.BODY)])
                return {"id": len(posted)}
            with self.subTest(name), \
                    mock.patch("bonobo.api.post", side_effect=lambda p, b=None: posted.append(p)), \
                    mock.patch("bonobo.api.get", return_value={"status": "running"}), \
                    mock.patch.object(fight_loop.time, "sleep"):
                list(fight_loop.carry(lambda: left.pop(0) if left else None, answer, lambda: True,
                                      {"done": None, "task_id": None}))
                self.assertEqual(posted, want)


class RowsAreDifferencedOnce(unittest.TestCase):
    """The dragon fight's threat rows are threat.rows's (end._threats): velocity differenced between readings."""

    def zombie(self, x):
        return {"id": 7, "type": "minecraft:zombie", "x": x, "y": 64.0, "z": 0.0}

    def test_velocity_over_readings(self):
        rows = [("first sight: at rest", [(1000.0, 10.0)], (0.0, 0.0, 0.0)),
                ("2 blocks nearer in 1 s", [(1000.0, 12.0), (1001.0, 10.0)], (-2.0, 0.0, 0.0)),
                ("the last reading 3 s old: at rest", [(1000.0, 20.0), (1003.0, 10.0)], (0.0, 0.0, 0.0)),
                ("a neutral enderman: not a row at all", None, None)]
        for name, readings, want in rows:
            with self.subTest(name), mock.patch.dict(end._LAST_SEEN, {}, clear=True):
                if readings is None:
                    got = end._threats([{"id": 9, "type": "minecraft:enderman", "x": 3.0, "y": 64.0, "z": 0.0}],
                                       None, now=1000.0)
                    self.assertEqual(got, [])
                    continue
                for now, x in readings:
                    got = end._threats([self.zombie(x)], None, now=now)
                self.assertEqual(tuple(round(v, 6) for v in got[0][2]), want)


if __name__ == "__main__":
    unittest.main()


class TheDragonFightRunsOnTheModel(unittest.TestCase):
    """A dragon fight's frames through the real planner and the one answer loop (fight_loop.carry, api faked only to
    record what is posted): the bunker dig is asked for while there is no pit, the bomb is posted in a window, and
    a threat arriving (min_tti) turns the post into a walk to cover. Change the frame → the batch changes."""

    def run_frames(self, frames):
        fight, posted, left = fight_plan.Fight(), [], list(frames)
        body = OneLoopCarriesTheFight.BODY

        def want():
            if not left:
                return None
            st, v = left.pop(0)
            return fight_loop.dragon_answer(fight.plan(fight_state(**st)), dragon_view(**v))

        def answer(a):
            posted.append(f"prep {a.target}" if a.kind == "prep" else [t["type"] for t in fight_loop.batch(a, body)])
            return None if a.kind == "prep" else {"id": len(posted)}
        with mock.patch("bonobo.api.post", side_effect=lambda p, b=None: posted.append(p)), \
                mock.patch("bonobo.api.get", return_value={"status": "succeeded"}), \
                mock.patch.object(fight_loop.time, "sleep"):
            list(fight_loop.carry(want, answer, lambda: True, {"done": None, "task_id": None}, again=True))
        return posted

    HEAD = [((8.0, 65.0, 0.0), 6.0, (0.0, 0.0, 0.0), "dragon_head")]     # the head on the bombing spot: covered now

    def test_frames_to_posts(self):
        rows = [("circling, no pit yet: the bunker dug (the prep skill digs bunker.dig_plan)",
                 [({"phase": 0, "tunnel": False, "bed_placed": False, "in_cover": False, "cover": None}, {})],
                 lambda p: p[:1] == ["prep dig_tunnel"]),
                ("sitting, pit and bed ready: the bomb batch", [({}, dict(bomb=BOMB))],
                 lambda p: p[:1] == [["travel", "bed_bomb", "travel"]]),
                ("the same window with the head already on us (min_tti 0): no bomb",
                 [({"threats": self.HEAD}, dict(bomb=BOMB, cover=(5, 62, 0)))],
                 lambda p: ["travel", "bed_bomb", "travel"] not in p),
                ("no pit to bomb from (must fail to bomb): never the bomb batch",
                 [({"tunnel": False, "in_cover": False, "cover": None}, {})],
                 lambda p: ["travel", "bed_bomb", "travel"] not in p)]
        for name, frames, check in rows:
            with self.subTest(name):
                posted = self.run_frames(frames)
                self.assertTrue(check(posted), posted)

    def test_a_threat_changes_the_batch(self):
        calm = self.run_frames([({}, dict(bomb=BOMB))])
        pressed = self.run_frames([({"threats": self.HEAD}, dict(bomb=BOMB, cover=(5, 62, 0)))])
        self.assertNotEqual(calm, pressed)


class FakeEnd:
    """The api, answered from a tiny End: end stone up to y 64, a bedrock pillar at the centre (or none), the body
    where the last walk put it, blocks gone where a mine task was posted. Only what the posts ask about is kept:
    this records the orchestration (what was posted, in order), it does not simulate the game."""

    def __init__(self, start, pillar=True):
        self.pos, self.mined, self.posted = tuple(start), set(), []
        self.bedrock = {(x, y, z) for x in (-1, 0, 1) for z in (-1, 0, 1) for y in (60, 61, 62, 63, 64)} if pillar \
            else set()

    def solid(self, c):
        return c not in self.mined and (c in self.bedrock or c[1] <= 64)

    def __call__(self, method, path, body=None, timeout=None):
        route, _, query = path.partition("?")
        q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
        if method == "POST" and route == "/task":
            self.posted.append(dict(body))
            if body["type"] == "mine":
                self.mined.add((body["x"], body["y"], body["z"]))
            elif body["type"] in ("travel", "goto"):
                self.pos = (int(body["x"]), int(body["y"]), int(body["z"]))
            return {"status": "succeeded", "type": body["type"], "message": "", "seconds": 0, "result": {}}
        if method == "POST":
            return {"status": "ok"}
        if route == "/state":
            x, y, z = self.pos
            return {"dimension": "minecraft:the_end", "x": x + 0.5, "y": float(y), "z": z + 0.5, "blockX": x,
                    "blockY": y, "blockZ": z, "health": 20.0, "food": 20, "dead": False, "onGround": True,
                    "inWater": False, "inLava": False, "air": 300, "control": {}, "screen": "none"}
        if route == "/status":
            return {"version": "0.1.47"}
        if route == "/inventory":
            return {"slots": [], "equipment": {}, "selectedSlot": 0}
        if route == "/entities":
            return {"entities": []}
        if route == "/find":
            wanted = q.get("blocks", "")
            return {"blocks": [{"x": x, "y": y, "z": z, "block": "minecraft:bedrock", "distance": 0.0}
                               for x, y, z in sorted(self.bedrock)] if "bedrock" in wanted else []}
        if route == "/blocks":
            a = [int(v) for v in q["from"].split(",")]
            b = [int(v) for v in q["to"].split(",")]
            cells = [(x, y, z) for x in range(min(a[0], b[0]), max(a[0], b[0]) + 1)
                     for y in range(min(a[1], b[1]), max(a[1], b[1]) + 1)
                     for z in range(min(a[2], b[2]), max(a[2], b[2]) + 1) if self.solid((x, y, z))]
            return {"palette": ["minecraft:end_stone", "minecraft:bedrock"],
                    "blocks": [[x, y, z, 1 if (x, y, z) in self.bedrock else 0] for x, y, z in cells]}
        raise AssertionError(f"FakeEnd: nothing answers {method} {path}")


class TheBunkerDigIsTheGeometry(unittest.TestCase):
    """The dragon fight's prep (end.build_bed_pit, which slay_dragon runs for the planner's "dig_tunnel"): the corridor
    it mines is bunker.dig_plan's for the side the body stands on and the floor it found — another side, another
    corridor; no pillar to dig beside, nothing mined."""

    def dig(self, start, pillar=True):
        import tempfile
        from bonobo import skillcore
        from bonobo.memory import Memory
        fake = FakeEnd(start, pillar)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("bonobo.api.api", side_effect=fake), \
                mock.patch.object(nav, "_features", None), mock.patch.object(nav, "ROAD_MEM", None), \
                mock.patch.object(end, "PIT", []):
            ctx = skillcore.Context(Memory(os.path.join(tmp, "n.json")), nav.Policy(), "minecraft:the_end", {})
            try:
                end.build_bed_pit(ctx)
            except Exception as e:                         # the failure row is judged by what was posted
                fake.error = e
            pit = list(end.PIT)
        mined = [(t["x"], t["y"], t["z"]) for t in fake.posted if t["type"] == "mine"]
        return fake, mined, pit

    def corridor(self, start, pit):
        return set(bunker.dig_plan(end.choose_side(start), pit[4])[2:])

    def test_the_corridor_is_dig_plans(self):
        rows = [("east of the portal", (10, 65, 0)), ("north of it", (0, 65, -10)), ("west", (-10, 65, 0)),
                ("south", (0, 65, 10))]
        for name, start in rows:
            with self.subTest(name):
                _fake, mined, pit = self.dig(start)
                self.assertTrue(pit, "no pit recorded")
                self.assertTrue(self.corridor(start, pit) <= set(mined), mined)

    def test_another_side_another_corridor(self):
        _f, east, pit_e = self.dig((10, 65, 0))
        _f, north, pit_n = self.dig((0, 65, -10))
        self.assertNotEqual(set(east), set(north))
        self.assertNotEqual(self.corridor((10, 65, 0), pit_e), self.corridor((0, 65, -10), pit_n))

    def test_no_pillar_nothing_dug(self):
        """Must fail: no exit-portal pillar to dig beside — said so, and not one block mined."""
        fake, mined, pit = self.dig((10, 65, 0), pillar=False)
        self.assertEqual((mined, pit), ([], []))
        self.assertIn("pillar", str(getattr(fake, "error", "")))
