"""Test point B — L0, offline: what the environment does to the body, judged from `/state` readings.

  HAZARDS   a /state (plus the two readings the caller makes: head in a block, blocks fallen) → hazard.kind, and
            whether the brain must rescue before anything else this round (hazard.rescue_due)
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

from bonobo import world  # noqa: E402
from bonobo import api, hazard, needs, perception, reflexes, retry, skillcore, skills, threat  # noqa: E402
from bonobo import brain as brainmod  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from tests.world import state  # noqa: E402
from bonobo.data import CRITICAL_HP  # noqa: E402

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
    ("must fail: a hop is not a fall", {"onGround": False}, False, hazard.FALL_BLOCKS - 1, None, None),
    ("falling into water is a landing", {"onGround": False, "inWater": True}, False, 30.0, None, None),
    ("lava beats everything else on the body", {"inLava": True, "onFire": True, "health": 3.0}, True, 20.0,
     "lava", "lava"),
    # health at the floor is SAFETY's own danger, threat or not (S1): no MAINTAIN/PLAN work runs past it
    ("critical health, nothing about", {"health": float(CRITICAL_HP)}, False, 0.0, "critical", "critical"),
    ("in the End, hurt to its higher floor", {"health": 11.0, "dimension": "minecraft:the_end"}, False, 0.0,
     "critical", "critical"),
    ("must fail: one above the floor: not a danger", {"health": float(CRITICAL_HP + 1)}, False, 0.0, None, None),
    ("nether, on netherrack", {"dimension": "minecraft:the_nether", "skyLight": 0}, False, 0.0, None, None),
    ("night, rain: weather is not a hazard", {"timeOfDay": 18000}, False, 0.0, None, None),
]

# Hostile situations: the environment is fine, something is coming at us. L0 must not see any of them.
HOSTILE = [
    ("dragon breath close", {"dimension": "minecraft:the_end"}, {"breath_within": lambda r: True}, "breath"),
    ("an enderman after us", {}, {"enderman_after_us": lambda r: True}, "enderman"),
    ("hurt, a zombie at 3", {"health": 9.0}, {"hostiles_within": lambda r: 3.0}, "hostiles"),
    ("hurt, a zombie at 3, but we are the ones attacking",
     {"health": 9.0, "control": {"active": True, "paused": False, "allowed": True, "task": {"type": "attack"},
                                 "queued": 0}}, {"hostiles_within": lambda r: 3.0}, None),
    ("healthy, a zombie at 3", {"health": 20.0}, {"hostiles_within": lambda r: 3.0}, None),
    ("must fail: hurt, nothing hostile about", {"health": 9.0}, {"hostiles_within": lambda r: None}, None),
    ("dead: nothing to interrupt for", {"dead": True, "health": 0.0}, {}, None),
    ("the player holds control", {"health": 9.0, "control": {"paused": True}},
     {"hostiles_within": lambda r: 3.0}, None),
]


class Hazards(unittest.TestCase):
    def test_state_to_kind_and_due(self):
        for name, changes, buried, fallen, kind, due in HAZARDS:
            s = state(**changes)
            with self.subTest(name):
                self.assertEqual(hazard.kind(s, buried=buried, fallen=fallen), kind)
                self.assertEqual(hazard.rescue_due(s, buried=buried), due)
                if kind is not None:
                    self.assertIn(kind, hazard.KINDS)
                # What perception interrupts for is the same judgment, environment first.
                self.assertEqual(perception.danger(s, buried=buried, fallen=fallen), kind)

    def test_due_only_names_what_has_a_rescue(self):
        for name, changes, buried, fallen, _kind, due in HAZARDS:
            with self.subTest(name):
                self.assertIn(due, set(hazard.RECOVERY) | {None})

    # (situation, /state changes) → seconds of slack before the water must be left (air/20 − surfacing − reaction)
    CLOCK = [("must fail: dry land has no clock", {"air": 0}, float("inf")),
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
              "critical": "L0", "breath": "fight", "enderman": "fight", "hostiles": "fight"}

    def test_each_danger_has_exactly_one_family(self):
        self.assertEqual(set(hazard.KINDS) | {k for k, f in self.FAMILY.items() if f == "fight"}, set(self.FAMILY),
                         "a danger kind without a family row")
        for kind, family in self.FAMILY.items():
            with self.subTest(kind):
                self.assertEqual(("L0" if kind in hazard.KINDS else "") + ("fight" if kind not in hazard.KINDS
                                                                             else ""), family)

    def test_hostile_situations_reach_the_fight_not_the_rescue(self):
        for name, changes, callbacks, want in HOSTILE:
            s = state(**changes)
            with self.subTest(name):
                self.assertIsNone(hazard.kind(s), "a mob is not an environmental hazard")
                self.assertIsNone(hazard.rescue_due(s, buried=False))
                got = perception.danger(s, **callbacks)
                self.assertEqual(got, want)
                self.assertTrue(got is None or got not in hazard.KINDS)


# (situation, [(y, onGround, inWater, inLava)], fallen after each reading)
FALLS = [
    ("walk off a 20-block cliff", [(84, True, False, False), (84, False, False, False), (80, False, False, False),
                                   (70, False, False, False), (64, True, False, False)], [0, 0, 4, 14, 0]),
    ("jump: up before down", [(64, False, False, False), (65.2, False, False, False), (64, False, False, False)],
     [0, 0, 1.2]),
    ("must fail: fall into water resets", [(90, False, False, False), (70, False, False, False), (60, False, True, False)],
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
    ("must fail: nothing wrong", {}, False, OK, [(False, None, False)]),
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
                b.needs, b.reflexes, b.last_failure = needs.Needs(b), reflexes.Maintain(b), None
                ran, modes, posted = [], [], []

                def rescue(ctx, st, _k):
                    ran.append(_k)
                    modes.append(api.STATE.mode)
                    if does is not OK:
                        raise does
                table = {k: [lambda ctx, st, _k=k: rescue(ctx, st, _k)] for k in hazard.RECOVERY}       # one way, either situation
                with mock.patch.dict(hazard.RECOVERY, table), \
                        mock.patch.object(hazard, "head_buried", return_value=buried), \
                        mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append(path)), \
                        mock.patch.object(api, "api", side_effect=AssertionError("L0 read the world")):
                    for used, kind, stopped in rounds:
                        ran.clear(), posted.clear()
                        api.STATE.interrupt, api.STATE.mode = "perception: danger", "normal"
                        self.assertEqual(hazard.handle(None, state(**changes), b.attempt, b.ready), used)
                        self.assertEqual(ran[0] if ran else None, kind)
                        self.assertEqual("/stop" in posted, stopped)
                        self.assertEqual(api.STATE.mode, "normal", "survival mode ends with the rescue")
                        if kind:
                            self.assertEqual(modes[-1], "survival", "the rescue runs protected from its own trigger")
                            self.assertIsNone(api.STATE.interrupt, "the interrupt it answers is consumed")
                api.STATE.interrupt, api.STATE.mode = None, "normal"


# The burning rescue, over what the world reads (the bag, water within 8): what it posts, or why it cannot.
BURNING = [
    ("a water bucket: pour it and take it back", True, [], [("run", "use_item"), ("run", "use_item")], None),
    ("must fail: no bucket, water 5 away: step into it", False, [{"block": "minecraft:water", "x": 5, "y": 64, "z": 0,
                                                      "distance": 5.0}], [("run", "goto")], None),
    ("neither: says so, does not stand still", False, [], [], api.NotAvailable),
    ("both: the bucket first", True, [{"block": "minecraft:water", "x": 5, "y": 64, "z": 0, "distance": 5.0}],
     [("run", "use_item"), ("run", "use_item")], None),
]


class Recovery(unittest.TestCase):
    """hazard.recover (S1): a rescue past its bound turns to the hazard's next way; the list spent, the reasons."""

    def test_rows(self):
        for kind, situation, ways in [(k, s, hazard.ways(k, s == "threatened")) for k in hazard.RECOVERY
                                      for s in (("threatened", "calm") if isinstance(hazard.RECOVERY[k], dict) else ("",))]:
            with self.subTest(f"{kind} {situation}"):
                ran = []

                def way(i, fails):
                    def run(ctx, st):
                        ran.append(i)
                        if fails:
                            raise api.TaskStuck(f"way {i} exceeded its budget")
                    run.__name__ = f"way{i}"
                    return run
                # the first over its bound: the next runs (must fail: a reason only, nothing more tried)
                table = [way(i, i == 0) for i in range(len(ways))]
                with mock.patch.dict(hazard.RECOVERY, {kind: table}):
                    if len(ways) > 1:
                        hazard.recover(None, kind, state())
                        self.assertEqual(ran, [0, 1])
                    else:
                        with self.assertRaises(api.NotAvailable):
                            hazard.recover(None, kind, state())
                # every way spent: one NotAvailable naming each
                ran.clear()
                with mock.patch.dict(hazard.RECOVERY, {kind: [way(i, True) for i in range(len(ways))]}):
                    with self.assertRaises(api.NotAvailable) as caught:
                        hazard.recover(None, kind, state())
                self.assertEqual(ran, list(range(len(ways))))
                self.assertTrue(all(f"way{i}" in str(caught.exception) for i in range(len(ways))))

    def test_an_interruption_is_not_a_spent_way(self):
        def fight(ctx, st):
            raise api.FightHolds("a fight holds the body")
        with mock.patch.dict(hazard.RECOVERY, {"drowning": [fight, lambda ctx, st: None]}):
            with self.assertRaises(api.FightHolds):
                hazard.recover(None, "drowning", state())

    def test_critical_by_situation(self):
        # (situation, threatened) → the first way: out of reach under a threat, a meal when calm
        rows = [("critical, a mob on us: cover first", True, "_into_cover"),
                ("critical, nothing about, food carried: eat", False, "_meal")]
        for name, threatened, first in rows:
            with self.subTest(name):
                self.assertEqual(hazard.ways("critical", threatened)[0].__name__, first)
        self.assertNotEqual(hazard.ways("critical", True)[0].__name__, "_meal",
                            "must fail: eating under blows (never finished)")

    def test_every_rescued_kind_has_a_next_way_or_says_why(self):
        # the lists themselves: drowning and burning turn to cover, lava pours water (it sets the lava)
        self.assertEqual([w.__name__ for w in hazard.RECOVERY["drowning"]], ["_surface", "_into_cover"])
        self.assertEqual([w.__name__ for w in hazard.RECOVERY["lava"]], ["_leave_lava", "_extinguish"])


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
                    mock.patch.object(api, "run_chain", side_effect=lambda ts, **k: [calls.append(("run", t["type"]))
                                                                                      for t in ts] and
                                      [{"status": "succeeded"} for _ in ts]):
                if raises:
                    with self.assertRaises(raises):
                        hazard.RECOVERY["burning"][0](None, state(onFire=True, health=5.0))
                else:
                    hazard.RECOVERY["burning"][0](None, state(onFire=True, health=5.0))
                self.assertEqual(calls[0], ("post", "/stop"), "the work stops first")
                self.assertEqual(calls[1:], want)

    def test_every_kind_is_rescued_or_stop_only(self):
        for kind in hazard.KINDS:
            with self.subTest(kind):
                self.assertNotEqual(kind in hazard.RECOVERY, kind in hazard.STOP_ONLY)


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
    ("evade with blocks: a walk, nothing dug or built (I3)", Decision("evade", (10, 64, 0)), fight_body(_inv(cobblestone=8)),
     [{"type": "travel", "x": 10, "y": 64, "z": 0, "range": 3}]),
    ("evade with nothing to place", Decision("evade", (10, 64, 0)), fight_body(),
     [{"type": "travel", "x": 10, "y": 64, "z": 0, "range": 3}]),
    ("eat: a cooked meal first", Decision("eat", None), fight_body(_inv(beef=2, cooked_beef=1)),
     [{"type": "eat", "item": "minecraft:cooked_beef"}]),
    ("eat: raw when that is all", Decision("eat", None), fight_body(_inv(beef=2)),
     [{"type": "eat", "item": "minecraft:beef"}]),
    ("must fail: eat: nothing to eat — no answer", Decision("eat", None), fight_body(), []),
    ("must fail: shield is no answer (the jar's reflex) — no batch", Decision("shield", None),
     fight_body(offhand="shield"), []),
    ("dig down two", Decision("reshape", ("down", 2)), fight_body(),
     [{"type": "mine", "x": 0, "y": 63, "z": 0}, {"type": "mine", "x": 0, "y": 62, "z": 0}]),
    ("stand two up", Decision("reshape", ("under", 2)), fight_body(_inv(cobblestone=4)),
     [{"type": "pillar", "item": "minecraft:cobblestone"}] * 2),
    ("stand up with nothing to stand on — no answer", Decision("reshape", ("under", 2)), fight_body(), []),
    ("a wall toward the zombie", Decision("reshape", ("between", 2)), fight_body(_inv(cobblestone=4), threats=ZOMBIE_EAST),
     [{"type": "place", "item": "minecraft:cobblestone", "x": 1, "y": 64, "z": 0},
      {"type": "place", "item": "minecraft:cobblestone", "x": 1, "y": 65, "z": 0}]),
    ("must fail: a zombie east at its block's centre (z 0.5) — still due east, not a diagonal block",
     Decision("reshape", ("between", 2)),
     fight_body(_inv(cobblestone=4), threats=[((5.5, 64.0, 0.5),) + ZOMBIE_EAST[0][1:]]),
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
              ("must fail: no batch from here", Decision("eat", None), [{"id": 7}], api.NotAvailable),
              ("the game queues none of it", Decision("fight", 42), [], api.NotAvailable)]

    def test_engage_posts_the_batch(self):
        from bonobo import fight_loop
        for name, option, queued, want in self.ENGAGE:
            posted = []
            with self.subTest(name), \
                    mock.patch.object(world, "feet", lambda: (0, 64, 0)), \
                    mock.patch.object(skillcore, "body_state", lambda ctx, region=None, **k: dict(
                        fight_body(_inv(cobblestone=4)), **k)), \
                    mock.patch.object(threat, "threats_seen", lambda: ([], None)), \
                    mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append(body) or
                                      {"tasks": queued, "message": "full"}):
                if isinstance(want, type):
                    with self.assertRaises(want):
                        fight_loop.engage(option, state(), None)
                else:
                    self.assertEqual(fight_loop.engage(option, state(), None), {"id": want})
                    # an attack names what it holds (api.ARM): no weapon in this bag, the hand
                    batch = [dict(t, item="hand") if t.get("type") == "attack" and "item" not in t else t
                             for t in fight_loop.batch(option, fight_body(_inv(cobblestone=4)))]
                    self.assertEqual(posted, [{"tasks": batch}])


def _until(cond, s=2.0):
    t0 = time.time()
    while time.time() - t0 < s:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


class Engagement(unittest.TestCase):
    def test_the_decision_rhythm(self):
        """Perception decides every api.READ_EVERY_S while a fight is on, every WATCH_S otherwise."""
        from bonobo import arbiter, fight_loop
        rows = [("must fail: nothing engaged", None, False, False), ("our engagement running", "intent", False, True),
                ("a boss fight holds the body", None, True, True), ("both", "intent", True, True)]
        for name, eng, boss, want in rows:
            body = arbiter.Motion()
            body.engaged = boss
            with self.subTest(name), mock.patch.object(fight_loop.arbiter, "BODY", body), \
                    mock.patch.object(fight_loop, "engaged", lambda e=eng: e):
                self.assertEqual(fight_loop.active(), want)
        self.assertEqual(api.READ_EVERY_S < __import__("bonobo.perception", fromlist=["WATCH_S"]).WATCH_S, True)


class Disengage(unittest.TestCase):
    # (situation, whose lease stands, is it the engagement on record, stop asked, /stop fails) →
    # (/stop posted, lease left with, engagement record cleared)
    ROWS = [
        ("our lease, still running a task: /stop, handed back, forgotten", "ours", True, True, False,
         (True, None, True)),
        ("our lease, nothing running: handed back without a /stop", "ours", True, False, False, (False, None, True)),
        ("must fail: a faster layer took the body: not ours to stop or hand back", "theirs", True, True, False,
         (False, "theirs", True)),
        ("the /stop fails: still handed back", "ours", True, True, True, (True, None, True)),
        ("an older engagement ending late: the current one is not forgotten", "ours", False, False, False,
         (False, None, False)),
    ]

    def test_always_hands_back(self):
        from bonobo import arbiter, fight_loop
        for name, lease, current, stop, stop_fails, (stopped, left, cleared) in self.ROWS:
            body, posted = arbiter.Motion(), []
            ours, theirs, other = (arbiter.Intent("tactic", lambda: None, "hostiles", key="hostiles"),
                                   arbiter.Intent("safety", lambda: None, "lava", key="lava"),
                                   arbiter.Intent("tactic", lambda: None, "newer", key="newer"))
            body.lease = ((ours if lease == "ours" else theirs), (lambda: False), time.time())

            def post(path, body_=None):
                posted.append(path)
                if stop_fails:
                    raise api.McError("game not reachable")
                return {}
            record = {"thread": None, "want": "x", "failure": {}, "intent": ours if current else other}
            with self.subTest(name), mock.patch.object(fight_loop.arbiter, "BODY", body), \
                    mock.patch.object(api, "post", side_effect=post), mock.patch.multiple(fight_loop.STATE, **record):
                fight_loop.disengage(ours, stop=stop)
                self.assertEqual("/stop" in posted, stopped)
                self.assertEqual(body.lease[0] if body.lease else None, {"theirs": theirs, None: None}[left])
                self.assertEqual(fight_loop.STATE.intent is None, cleared)



class Extinguish(unittest.TestCase):
    """hazard.extinguish_commands: the fire put out in one chain — pour and scoop back, else into water."""
    S = {"blockX": 10, "blockY": 64, "blockZ": -3}
    WATER = {"x": 13, "y": 63, "z": -3}
    # (situation, a water bucket carried, water within reach) → the task types, or the error
    ROWS = [("a bucket carried: pour at the feet, scoop it back", True, None, ["use_item", "use_item"]),
            ("a bucket and water near: the bucket still (no walk)", True, WATER, ["use_item", "use_item"]),
            ("no bucket, water near: walk into it", False, WATER, ["goto"]),
            ("must fail: no bucket, no water", False, None, api.NotAvailable)]

    def test_rows(self):
        for name, bucket, water, want in self.ROWS:
            with self.subTest(name):
                if isinstance(want, type):
                    with self.assertRaises(want):
                        hazard.extinguish_commands(self.S, bucket, water)
                    continue
                tasks = hazard.extinguish_commands(self.S, bucket, water)
                self.assertEqual([t["type"] for t in tasks], want)
                if bucket:
                    self.assertEqual([t["item"] for t in tasks], ["minecraft:water_bucket", "minecraft:bucket"])
                    self.assertEqual({(t["x"], t["y"], t["z"]) for t in tasks}, {(10.5, 64, -2.5)})
                else:
                    self.assertEqual((tasks[0]["x"], tasks[0]["z"]), (13, -3))

if __name__ == "__main__":
    unittest.main()
