"""Test point B — L0, offline: what the environment does to the body, judged from `/state` readings.

  HAZARDS   a /state (plus the two readings the caller makes: head in a block, blocks fallen) → hazard.kind, and
            whether the brain must rescue before anything else this round (hazard.due)
  DANGERS   the same readings with hostiles about → perception.danger: environment first, hostiles never reach L0
  FALLS     a sequence of /state readings → how far `Watch` says we have fallen (the one stateful reading)

The rescues themselves (water_clutch, cross_lava_8, cave_escape, lava edge, buried by sand) are in-game rows of the
scenario sheet; here only the judgment that triggers them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempfile  # noqa: E402
import time  # noqa: E402
from unittest import mock  # noqa: E402

from bonobo import api, hazard, perception, retry, skillcore, skills, upkeep  # noqa: E402
from bonobo import brain as brainmod  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from tests.world import state  # noqa: E402

W = hazard._W
DROWN_AIR = int((W["surface_s"] + W["reaction_s"]) * hazard.TICKS_PER_S)     # the clock's own zero, in ticks

# (situation, /state changes, buried, fallen, kind, due) — `due` is what the brain answers at the top of a round.
HAZARDS = [
    ("standing on stone, all well", {}, False, 0.0, None, None),
    ("in lava", {"inLava": True, "onFire": True, "health": 18.0}, False, 0.0, "lava", "lava"),
    ("lava edge: burning, health still high", {"onFire": True, "health": 18.0}, False, 0.0, None, None),
    ("burning and hurt", {"onFire": True, "health": 6.0}, False, 0.0, "burning", "burning"),
    ("swimming, lungs full", {"inWater": True, "onGround": False, "air": 300}, False, 0.0, None, None),
    ("under water, air at the clock's zero", {"inWater": True, "onGround": False, "air": DROWN_AIR},
     False, 0.0, "drowning", "drowning"),
    ("under water, a little slack left (brain rescues between tasks)",
     {"inWater": True, "onGround": False, "air": DROWN_AIR + 30}, False, 0.0, None, "drowning"),
    ("under water, below the hard floor", {"inWater": True, "onGround": False, "air": W["air_floor"] - 1},
     False, 0.0, "drowning", "drowning"),
    ("buried by sand", {}, True, 0.0, "suffocating", "suffocating"),
    ("buried and standing in water", {"inWater": True, "air": 300}, True, 0.0, "suffocating", "suffocating"),
    ("falling off a cliff edge", {"onGround": False}, False, hazard.FALL_BLOCKS + 6, "falling", None),
    ("a hop is not a fall", {"onGround": False}, False, hazard.FALL_BLOCKS - 1, None, None),
    ("falling into water is a landing", {"onGround": False, "inWater": True}, False, 30.0, None, None),
    ("lava beats everything else on the body", {"inLava": True, "onFire": True, "health": 3.0}, True, 20.0,
     "lava", "lava"),
    ("nether, on netherrack", {"dimension": "minecraft:the_nether", "skyLight": 0}, False, 0.0, None, None),
    ("night, rain: weather is not a hazard", {"timeOfDay": 18000}, False, 0.0, None, None),
]

# Hostile situations: the environment is fine, something is coming at us. L0 must not see any of them.
HOSTILE = [
    ("critical health", {"health": 3.0}, {}, "critical_health"),
    ("in the End, hurt", {"health": 11.0, "dimension": "minecraft:the_end"}, {}, "critical_health"),
    ("dragon breath close", {"dimension": "minecraft:the_end"}, {"breath_within": lambda r: True}, "breath"),
    ("an enderman after us", {}, {"enderman_after_us": lambda r: True}, "enderman"),
    ("hurt, a zombie at 3", {"health": 9.0}, {"hostiles_within": lambda r: 3.0}, "hostiles"),
    ("hurt, a zombie at 3, but we are the ones attacking",
     {"health": 9.0, "control": {"active": True, "paused": False, "allowed": True, "task": {"type": "attack"},
                                 "queued": 0}}, {"hostiles_within": lambda r: 3.0}, None),
    ("healthy, a zombie at 3", {"health": 20.0}, {"hostiles_within": lambda r: 3.0}, None),
    ("dead: nothing to interrupt for", {"dead": True, "health": 0.0}, {}, None),
    ("the player holds control", {"health": 3.0, "control": {"paused": True}}, {}, None),
]


class Hazards(unittest.TestCase):
    def test_state_to_kind_and_due(self):
        for name, changes, buried, fallen, kind, due in HAZARDS:
            s = state(**changes)
            with self.subTest(name):
                self.assertEqual(hazard.kind(s, buried=buried, fallen=fallen), kind)
                self.assertEqual(hazard.due(s, buried=buried), due)
                if kind is not None:
                    self.assertIn(kind, hazard.KINDS)
                # What perception interrupts for is the same judgment, environment first.
                self.assertEqual(perception.danger(s, buried=buried, fallen=fallen), kind)

    def test_due_only_names_what_has_a_rescue(self):
        for name, changes, buried, fallen, _kind, due in HAZARDS:
            with self.subTest(name):
                self.assertIn(due, set(hazard.RESCUE) | {None})

    # (situation, /state changes) → seconds of slack before the water must be left (air/20 − surfacing − reaction)
    CLOCK = [("dry land has no clock", {"air": 0}, float("inf")),
             ("full lungs under water", {"inWater": True, "air": 300}, 300 / 20 - W["surface_s"] - W["reaction_s"]),
             ("half a breath", {"inWater": True, "air": 150}, 150 / 20 - W["surface_s"] - W["reaction_s"]),
             ("at the clock's zero", {"inWater": True, "air": DROWN_AIR}, 0.0),
             ("no air at all: overdue", {"inWater": True, "air": 0}, -(W["surface_s"] + W["reaction_s"]))]

    def test_the_drowning_clock(self):
        for name, changes, want in self.CLOCK:
            with self.subTest(name):
                self.assertAlmostEqual(hazard.drowning_in(state(**changes)), round(want, 2), places=2)


class HostilesAreNotL0(unittest.TestCase):
    # every danger kind perception can name, and the family that answers it
    FAMILY = {"lava": "L0", "burning": "L0", "drowning": "L0", "suffocating": "L0", "falling": "L0",
              "critical_health": "fight", "breath": "fight", "enderman": "fight", "hostiles": "fight"}

    def test_each_danger_has_exactly_one_family(self):
        self.assertEqual(set(perception.DANGERS), set(self.FAMILY), "a danger kind without a family row")
        for kind, family in self.FAMILY.items():
            with self.subTest(kind):
                self.assertEqual(("L0" if kind in hazard.KINDS else "") + ("fight" if kind in perception.HOSTILE
                                                                             else ""), family)

    def test_hostile_situations_reach_the_fight_not_the_rescue(self):
        for name, changes, callbacks, want in HOSTILE:
            s = state(**changes)
            with self.subTest(name):
                self.assertIsNone(hazard.kind(s), "a mob is not an environmental hazard")
                self.assertIsNone(hazard.due(s, buried=False))
                got = perception.danger(s, **callbacks)
                self.assertEqual(got, want)
                self.assertTrue(got is None or got in perception.HOSTILE)


# (situation, [(y, onGround, inWater, inLava)], fallen after each reading)
FALLS = [
    ("walk off a 20-block cliff", [(84, True, False, False), (84, False, False, False), (80, False, False, False),
                                   (70, False, False, False), (64, True, False, False)], [0, 0, 4, 14, 0]),
    ("jump: up before down", [(64, False, False, False), (65.2, False, False, False), (64, False, False, False)],
     [0, 0, 1.2]),
    ("fall into water resets", [(90, False, False, False), (70, False, False, False), (60, False, True, False)],
     [0, 20, 0]),
    ("fall into lava resets (lava is its own hazard)", [(90, False, False, False), (75, False, False, True)], [0, 0]),
]


class Falls(unittest.TestCase):
    def test_fallen_over_a_reading_sequence(self):
        for name, readings, want in FALLS:
            watch = hazard.Watch()
            with self.subTest(name):
                got = [round(watch.fallen(state(y=float(y), onGround=g, inWater=w, inLava=lv)), 2)
                       for y, g, w, lv in readings]
                self.assertEqual(got, want)


# (situation, /state changes, head buried?, what the rescue does, [rounds]) → per round: (used the round?, rescue
# run?, /stop posted?). Rounds run back to back through the real Brain.attempt / Brain.ready (the failure policy).
OK, FAILS, STOPPED = "ok", api.NavFailed("no path out of the lava"), api.Interrupted("perception: a new hazard")
RESCUES = [
    ("in lava: rescued, under survival mode", {"inLava": True}, False, OK, [(True, "lava", False)]),
    ("buried: rescued", {}, True, OK, [(True, "suffocating", False)]),
    ("drowning between tasks: rescued", {"inWater": True, "onGround": False, "air": 140}, False, OK,
     [(True, "drowning", False)]),
    ("burning: rescued (water on it)", {"onFire": True, "health": 5.0}, False, OK, [(True, "burning", False)]),
    ("falling: stop-only, the round is not used", {"onGround": False}, False, OK, [(False, None, False)]),
    ("nothing wrong", {}, False, OK, [(False, None, False)]),
    ("the rescue fails: /stop, and the cause cools here — the next round does not try again",
     {"inLava": True}, False, FAILS, [(True, "lava", True), (False, None, False)]),
    ("the rescue is interrupted: no /stop, no cooling, tried again next round",
     {"inLava": True}, False, STOPPED, [(True, "lava", False), (True, "lava", False)]),
]


class Rescue(unittest.TestCase):
    def test_rounds(self):
        for name, changes, buried, does, rounds in RESCUES:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.policy_cache = __import__("bonobo.nav", fromlist=["Policy"]).Policy()
                b.mem, b.retry, b.place = Memory(tmp + "/notes.json"), retry.Retry(), ("here", False)
                b.table, b.last_failure = upkeep.Upkeep(b), None
                ran, modes, posted = [], [], []

                def rescue(ctx, st, _k):
                    ran.append(_k)
                    modes.append(api.MODE)
                    if does is not OK:
                        raise does
                table = {k: (lambda ctx, st, _k=k: rescue(ctx, st, _k)) for k in hazard.RESCUE}
                with mock.patch.dict(hazard.RESCUE, table), \
                        mock.patch.object(skills, "head_buried", return_value=buried), \
                        mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append(path)), \
                        mock.patch.object(api, "api", side_effect=AssertionError("L0 read the world")):
                    for used, kind, stopped in rounds:
                        ran.clear(), posted.clear()
                        api.INTERRUPT, api.MODE = "perception: danger", "normal"
                        self.assertEqual(hazard.handle(None, state(**changes), b.attempt, b.ready), used)
                        self.assertEqual(ran[0] if ran else None, kind)
                        self.assertEqual("/stop" in posted, stopped)
                        self.assertEqual(api.MODE, "normal", "survival mode ends with the rescue")
                        if kind:
                            self.assertEqual(modes[-1], "survival", "the rescue runs protected from its own trigger")
                            self.assertIsNone(api.INTERRUPT, "the interrupt it answers is consumed")
                api.INTERRUPT, api.MODE = None, "normal"


# The burning rescue, over what the world reads (the bag, water within 8): what it posts, or why it cannot.
BURNING = [
    ("a water bucket: pour it and take it back", True, [], [("run", "use_item"), ("run", "use_item")], None),
    ("no bucket, water 5 away: step into it", False, [{"block": "minecraft:water", "x": 5, "y": 64, "z": 0,
                                                      "distance": 5.0}], [("run", "goto")], None),
    ("neither: says so, does not stand still", False, [], [], api.NotAvailable),
    ("both: the bucket first", True, [{"block": "minecraft:water", "x": 5, "y": 64, "z": 0, "distance": 5.0}],
     [("run", "use_item"), ("run", "use_item")], None),
]


class Burning(unittest.TestCase):
    def test_rescue(self):
        from bonobo import world
        from tests.world import bag, inventory
        for name, bucket, water, want, raises in BURNING:
            calls = []
            inv = bag(inventory(("water_bucket", 1)) if bucket else inventory())       # built before the patch
            with self.subTest(name), \
                    mock.patch.object(world, "Inventory", lambda data=None, _i=inv: _i), \
                    mock.patch.object(world, "find", lambda *a, _w=water, **k: list(_w)), \
                    mock.patch.object(api, "post", side_effect=lambda path, body=None: calls.append(("post", path))), \
                    mock.patch.object(api, "run", side_effect=lambda t, wait=0: calls.append(("run", t["type"])) or
                                      {"status": "succeeded"}):
                if raises:
                    with self.assertRaises(raises):
                        hazard.RESCUE["burning"](None, state(onFire=True, health=5.0))
                else:
                    hazard.RESCUE["burning"](None, state(onFire=True, health=5.0))
                self.assertEqual(calls[0], ("post", "/stop"), "the work stops first")
                self.assertEqual(calls[1:], want)

    def test_every_kind_is_rescued_or_stop_only(self):
        for kind in hazard.KINDS:
            with self.subTest(kind):
                self.assertNotEqual(kind in hazard.RESCUE, kind in hazard.STOP_ONLY)


# Hostiles go to the fight (fight_loop), never to L0. Each answer is a pure batch (fight_loop.batch) from a body state
# (`skillcore.body_state` + the threats answered). (situation, the answer, the body) → the batch, exactly.
Decision = __import__("collections").namedtuple("Decision", "kind target")


def fight_body(inv=None, region=None, threats=(), offhand=None):
    from tests.world import bag, inventory
    data = inv if inv is not None else inventory()
    if offhand:
        data = dict(data, equipment=dict(data["equipment"], offhand={"id": f"minecraft:{offhand}", "count": 1}))
    return {"state": state(), "feet": (0, 64, 0), "inv": bag(data), "protected": set(), "region": region,
            "threats": list(threats)}


def _inv(**kw):
    from tests.world import inventory
    return inventory(**kw)


def _flat():
    from tests.world import flat
    return flat()


ZOMBIE_EAST = [((5.0, 64.0, 0.0), 3.0, (0.0, 0.0, 0.0), "minecraft:zombie", 1.0, 3.0)]
BATCHES = [
    ("fight: attack that entity", Decision("fight", 42), fight_body(), [{"type": "attack", "entity": 42}]),
    ("evade with blocks to bridge", Decision("evade", (10, 64, 0)), fight_body(_inv(cobblestone=8)),
     [{"type": "travel", "x": 10, "y": 64, "z": 0, "range": 3, "break": True, "place": True, "placeBudget": 8,
       "avoid": []}]),
    ("evade with nothing to place", Decision("evade", (10, 64, 0)), fight_body(),
     [{"type": "travel", "x": 10, "y": 64, "z": 0, "range": 3, "break": True, "place": True, "placeBudget": 0,
       "avoid": []}]),
    ("eat: a cooked meal first", Decision("eat", None), fight_body(_inv(beef=2, cooked_beef=1)),
     [{"type": "eat", "item": "minecraft:cooked_beef"}]),
    ("eat: raw when that is all", Decision("eat", None), fight_body(_inv(beef=2)),
     [{"type": "eat", "item": "minecraft:beef"}]),
    ("eat: nothing to eat — no answer", Decision("eat", None), fight_body(), []),
    ("shield up", Decision("shield", None), fight_body(offhand="shield"),
     [{"type": "use_item", "hand": "offhand", "hold_ms": 1500}]),
    ("shield, but none in the offhand — no answer", Decision("shield", None), fight_body(), []),
    ("dig down two", Decision("reshape", ("down", 2)), fight_body(),
     [{"type": "mine", "x": 0, "y": 63, "z": 0}, {"type": "mine", "x": 0, "y": 62, "z": 0}]),
    ("stand two up", Decision("reshape", ("under", 2)), fight_body(_inv(cobblestone=4)),
     [{"type": "pillar", "item": "minecraft:cobblestone"}] * 2),
    ("stand up with nothing to stand on — no answer", Decision("reshape", ("under", 2)), fight_body(), []),
    ("a wall toward the zombie", Decision("reshape", ("between", 2)), fight_body(_inv(cobblestone=4), threats=ZOMBIE_EAST),
     [{"type": "place", "item": "minecraft:cobblestone", "x": 1, "y": 64, "z": 0},
      {"type": "place", "item": "minecraft:cobblestone", "x": 1, "y": 65, "z": 0}]),
    ("wall in: pod's own batch", Decision("wall_in", None), fight_body(_inv(cobblestone=16), region=_flat()), 10),
    ("wall in with no region read — no answer", Decision("wall_in", None), fight_body(_inv(cobblestone=16)), []),
    ("an answer with no batch (ignore)", Decision("ignore", None), fight_body(), []),
]


class FightBatches(unittest.TestCase):
    def test_answer_to_batch(self):
        from bonobo import fight_loop
        with mock.patch.object(api, "api", side_effect=AssertionError("a batch read the world")):
            for name, option, body_, want in BATCHES:
                with self.subTest(name):
                    got = fight_loop.batch(option, body_)
                    if isinstance(want, int):
                        self.assertEqual((len(got), {t["type"] for t in got}), (want, {"place"}))
                    else:
                        self.assertEqual([{k: v for k, v in t.items() if k in w} for t, w in zip(got, want)], want)
                        self.assertEqual(len(got), len(want))

    # (the answer, its batch non-empty?, what the game queues) → the task id watched, or the reason it is no answer
    ENGAGE = [("fight, queued", Decision("fight", 42), [{"id": 7}], 7),
              ("a two-task batch: the last is watched", Decision("reshape", ("under", 2)), [{"id": 7}, {"id": 8}], 8),
              ("no batch from here", Decision("eat", None), [{"id": 7}], api.NotAvailable),
              ("the game queues none of it", Decision("fight", 42), [], api.NotAvailable)]

    def test_engage_posts_the_batch(self):
        from bonobo import fight_loop, perception
        for name, option, queued, want in self.ENGAGE:
            posted = []
            with self.subTest(name), \
                    mock.patch.object(skillcore, "feet", lambda: (0, 64, 0)), \
                    mock.patch.object(skillcore, "body_state", lambda ctx, region=None, **k: dict(
                        fight_body(_inv(cobblestone=4)), **k)), \
                    mock.patch.object(perception, "threats_seen", lambda: ([], None)), \
                    mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append(body) or
                                      {"tasks": queued, "message": "full"}):
                if isinstance(want, type):
                    with self.assertRaises(want):
                        fight_loop.engage(option, state(), None)
                else:
                    self.assertEqual(fight_loop.engage(option, state(), None), {"id": want})
                    self.assertEqual(posted, [{"tasks": fight_loop.batch(option, fight_body(_inv(cobblestone=4)))}])


def _until(cond, s=2.0):
    t0 = time.time()
    while time.time() - t0 < s:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


# CT1: the engagement on its own thread over a real arbiter; the game side answers from a stub (it posts at once,
# reports the task `game_s` later). (situation, events, game_s) — events: ("offer", Decision), ("preempt", layer) by
# a faster layer, ("release",) answering stops paying, ("raise",) the next answer fails. Expected: answers posted in
# order, a /stop between two of them?, how it ended.
A, A2, B = Decision("fight", 1), Decision("fight", 1), Decision("fight", 2)
ENGAGEMENTS = [
    ("one answer, then answering stops paying: hands back", [("offer", A), ("release",)], 0.01, [A], False,
     "handed back"),
    ("a new answer: /stop, then the new one", [("offer", A), ("offer", B), ("release",)], 0.01, [A, B], True,
     "handed back"),
    ("the same answer again: appended, nothing re-posted", [("offer", A), ("offer", A2), ("release",)], 0.01, [A],
     False, "handed back"),
    ("the answer fails: recorded, handed back", [("raise",), ("offer", A)], 0.01, [], False, "failed"),
    ("a faster layer takes the body: the engagement ends", [("offer", A), ("preempt", "safety")], 0.01, [A], False,
     "preempted"),
    ("a slow game: perception still never waits", [("offer", A), ("offer", B), ("release",)], 0.3, [A, B], True,
     "handed back"),
]


class Engagement(unittest.TestCase):
    def test_sequences(self):
        from bonobo import arbiter, fight_loop
        round_s = fight_loop.FIGHT_POLL_S
        for name, events, game_s, answered, stopped, ending in ENGAGEMENTS:
            posted, stops, flags = [], [], {"raise": False, "paying": True}
            body = arbiter.Motion()

            def answer(option):
                if flags["raise"]:
                    raise api.NavFailed("cornered")
                posted.append((option, time.time()))
                return {"id": len(posted)}

            def game_get(path):
                time.sleep(game_s)
                return {"status": "running"}

            def post(path, body_=None):
                if path == "/stop":
                    stops.append(len(posted))
                return {}
            with self.subTest(name), mock.patch.object(fight_loop.arbiter, "BODY", body), \
                    mock.patch.object(fight_loop, "ANSWER", answer), \
                    mock.patch.object(api, "get", side_effect=game_get), mock.patch.object(api, "post", side_effect=post):
                failure = None
                for ev in events:
                    if ev[0] == "raise":
                        flags["raise"] = True
                    elif ev[0] == "offer":
                        t0 = time.time()
                        was = fight_loop.engaged()
                        taken, refused, failure = fight_loop.offer(ev[1], 10.0, "hostiles", time.time(),
                                                                   lambda: not flags["paying"], None, time.time())
                        self.assertLess(time.time() - t0, round_s, "perception gets its round back at once")
                        self.assertIsNone(refused)
                        if flags["raise"]:
                            continue
                        if was is None:
                            self.assertTrue(_until(lambda: fight_loop.engaged() is not None, round_s),
                                            "engaged within one round")
                        self.assertTrue(_until(lambda o=ev[1]: posted and fight_loop.same(posted[-1][0], o),
                                               round_s * 1.5 + game_s), "the answer carried out within 1.5 rounds")
                    elif ev[0] == "release":
                        flags["paying"] = False
                    elif ev[0] == "preempt":
                        body.preempt(ev[1], lambda: None, "lava", release=lambda: False, now=time.time())
                self.assertTrue(_until(lambda: fight_loop.engaged() is None, 1.0 + game_s), "the engagement always ends")
                self.assertEqual([o for o, _t in posted], answered)
                self.assertEqual(bool([s_ for s_ in stops if 0 < s_ < len(posted)]), stopped)
                if ending == "failed":
                    self.assertEqual(failure, {"failed": "NavFailed: cornered"})
                if ending in ("handed back", "failed"):
                    self.assertIsNone(body.lease, "the lease is released")
                if ending == "preempted":
                    self.assertEqual(body.holder().layer, "safety")
                api.INTERRUPT = None

    def test_the_decision_rhythm(self):
        """Perception decides every FIGHT_POLL_S while a fight is on, every POLL_S otherwise (fight_loop.active)."""
        from bonobo import arbiter, fight_loop
        rows = [("nothing engaged", None, False, False), ("our engagement running", "intent", False, True),
                ("a boss fight holds the body", None, True, True), ("both", "intent", True, True)]
        for name, eng, boss, want in rows:
            body = arbiter.Motion()
            body.engaged = boss
            with self.subTest(name), mock.patch.object(fight_loop.arbiter, "BODY", body), \
                    mock.patch.object(fight_loop, "engaged", lambda e=eng: e):
                self.assertEqual(fight_loop.active(), want)
        self.assertEqual(fight_loop.FIGHT_POLL_S < __import__("bonobo.perception", fromlist=["POLL_S"]).POLL_S, True)


class Disengage(unittest.TestCase):
    # (situation, whose lease stands, is it the engagement on record, stop asked, /stop fails) →
    # (/stop posted, lease left with, engagement record cleared)
    ROWS = [
        ("our lease, still running a task: /stop, handed back, forgotten", "ours", True, True, False,
         (True, None, True)),
        ("our lease, nothing running: handed back without a /stop", "ours", True, False, False, (False, None, True)),
        ("a faster layer took the body: not ours to stop or hand back", "theirs", True, True, False,
         (False, "theirs", True)),
        ("the /stop fails: still handed back", "ours", True, True, True, (True, None, True)),
        ("an older engagement ending late: the current one is not forgotten", "ours", False, False, False,
         (False, None, False)),
    ]

    def test_always_hands_back(self):
        from bonobo import arbiter, fight_loop
        for name, lease, current, stop, stop_fails, (stopped, left, cleared) in self.ROWS:
            body, posted = arbiter.Motion(), []
            ours, theirs, other = (arbiter.Intent("tactic", lambda: None, "hostiles"),
                                   arbiter.Intent("safety", lambda: None, "lava"),
                                   arbiter.Intent("tactic", lambda: None, "newer"))
            body.lease = ((ours if lease == "ours" else theirs), (lambda: False), time.time())

            def post(path, body_=None):
                posted.append(path)
                if stop_fails:
                    raise api.McError("game not reachable")
                return {}
            record = {"thread": None, "want": "x", "failure": {}, "intent": ours if current else other}
            with self.subTest(name), mock.patch.object(fight_loop.arbiter, "BODY", body), \
                    mock.patch.object(api, "post", side_effect=post), mock.patch.dict(fight_loop._ENG, record):
                fight_loop.disengage(ours, stop=stop)
                self.assertEqual("/stop" in posted, stopped)
                self.assertEqual(body.lease[0] if body.lease else None, {"theirs": theirs, None: None}[left])
                self.assertEqual(fight_loop._ENG["intent"] is None, cleared)


if __name__ == "__main__":
    unittest.main()
