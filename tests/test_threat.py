"""What the swept table cannot reach: everything here is about wiring, not about pricing.

Pricing — which column wins, what it saves, how the answer moves with distance, numbers, health, ground and kit —
is swept over the whole danger sweep in test_when/test_rate/test_act/test_saved, and the scene-by-scene ones
deleted with it. What is left is what a pure state vector never sees: rows built from live entities, the reflex
bidding through perception, the lease, the interrupt, and the few shapes whose geometry is the whole point.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import threat as sv  # noqa: E402  (the price of health lives in threat now)
from bonobo import threat  # noqa: E402

HERE = (0.0, 64.0, 0.0)      # fixture: where we stand
STILL = (0.0, 0.0, 0.0)      # fixture: a mob at rest


def row(kind, x, z, vel=STILL, aware=1.0, dps=None):
    """Through the one constructor, like everything else: a row built by hand here is a second definition of a
    row, and it was exactly such a hand-built row that let a missing field go unnoticed."""
    return threat.row((float(x), 64.0, float(z)), threat.MOBS[kind]["reach"], vel, kind, aware=aware, dps=dps)


def decide(hazards, **kw):
    """Decide with the price the agent actually uses: health costs what the survival model says it costs.

    Passing health through as itself (the default) is only a test convenience, and it hides the behaviour that
    matters — at 9 hp a fight that costs 7 hp is nearly a death, not "7", which is why low health must leave.
    """
    state = {"here": HERE, "hp": 20, "sword": 0, "protection": 0.0, "night": False, "blocks": 0,
             "hazards": hazards, "ids": list(range(len(hazards)))}
    state.update(kw)
    sstate = sv.price_state(hp=state["hp"], sword=state["sword"], pickaxe=1, food_items=8, bed=True)
    return threat.decide(state, lambda dhp: sv.hp_seconds(sstate, dhp))


class Rows(unittest.TestCase):
    ZOMBIE = {"minecraft:zombie": 3.0}      # fixture: the kinds asked for

    # (last reading of entity 7 or None, this reading, kinds asked for) → the rows built: (centre, velocity)
    ROWS = [("first sight: at rest", None, {"type": "minecraft:zombie", "x": 10.0}, ZOMBIE,
             [((10.0, 64.0, 0.0), (0.0, 0.0, 0.0))]),
            ("two blocks nearer in a second", ((10.0, 64.0, 0.0), 100.0), {"type": "minecraft:zombie", "x": 8.0},
             ZOMBIE, [((8.0, 64.0, 0.0), (-2.0, 0.0, 0.0))]),
            ("the last reading is 3 s old: at rest, not a teleport", ((20.0, 64.0, 0.0), 98.0),
             {"type": "minecraft:zombie", "x": 8.0}, ZOMBIE, [((8.0, 64.0, 0.0), (0.0, 0.0, 0.0))]),
            ("must fail: a kind nobody asked about is not a row", None, {"type": "minecraft:cow", "x": 1.0}, ZOMBIE, []),
            ("must fail: a dying zombie (health 0) is no row", None, {"type": "minecraft:zombie", "x": 1.0,
                                                                     "health": 0.0}, ZOMBIE, []),
            ("a hurt zombie (health 3) is", None, {"type": "minecraft:zombie", "x": 1.0, "health": 3.0}, ZOMBIE,
             [((1.0, 64.0, 0.0), (0.0, 0.0, 0.0))]),
            ("nothing asked for, nothing built", None, {"type": "minecraft:zombie", "x": 1.0}, {}, [])]

    def test_rows_over_the_table(self):
        for name, prev, reading, kinds, want in self.ROWS:
            with self.subTest(name):
                mem = {} if prev is None else {7: prev}
                e = dict({"id": 7, "y": 64.0, "z": 0.0}, **reading)
                got = threat.rows([e], mem, 101.0 if prev else 100.0, kinds)
                self.assertEqual([(r[0], tuple(r[2])) for r in got], want)


class Answers(unittest.TestCase):
    """What the threat layer OFFERS, not which one it picks.

    Picking is the planner's job — the same objective that ranks mining and crafting — so asserting a choice here
    would pin the model to whatever the numbers happen to be this week. What must hold is the shape of the option
    set: every answer priced in health and seconds, nothing missing, and the one answer that is never allowed.
    """

    def priced(self, hazards, **kw):
        state = {"here": HERE, "hp": 20, "sword": 0, "protection": 0.0, "night": False, "blocks": 0,
                 "hazards": hazards, "ids": list(range(len(hazards)))}
        state.update(kw)
        return {o.kind: o for o in threat.options(state)}


    # (threats) → where to run: 16 blocks away from them, never between two of them
    ESCAPE = [("must fail: running between them — two archers flanking east", [("minecraft:skeleton", 10, 2), ("minecraft:skeleton", 10, -2)],
               (-16, 64, 0)),
              ("one zombie east", [("minecraft:zombie", 5, 0)], (-16, 64, 0)),
              ("one zombie west", [("minecraft:zombie", -5, 0)], (16, 64, 0)),
              ("one zombie south", [("minecraft:zombie", 0, 6)], (0, 64, -16)),
              ("nothing: an arbitrary way, still a spot", [], (16, 64, 0))]

    def test_escape_spot_over_the_table(self):
        for name, hz, want in self.ESCAPE:
            with self.subTest(name):
                self.assertEqual(threat.escape_spot(HERE, [row(k, x, z) for k, x, z in hz]), want)


class Interrupt(unittest.TestCase):
    # (seconds until dead at this pressure, the task under way) → the interrupt. Closer than one planning round
    # (4 s) stops the task; an attack is its own answer and is never interrupted for being hit.
    ROWS = [(2.0, "mine", "hostiles"), (4.0, "mine", "hostiles"), (4.01, "mine", None), (30.0, "mine", None),  # must fail: just past one round, no interrupt
            (None, "mine", None), (2.0, "attack", None)]

    def test_perception_stops_a_task_when_arrows_would_kill_soon(self):
        from bonobo import perception
        self.assertEqual(perception.interrupt_within_s(), 4.0)
        for ttd, task, want in self.ROWS:
            with self.subTest(ttd=ttd, task=task):
                state = {"health": 12, "food": 20, "x": 0.0, "y": 64.0, "z": 0.0, "control": {"task": {"type": task}}}
                self.assertEqual(perception.danger(state, time_to_die=lambda: ttd), want)


class OneComparison(unittest.TestCase):
    """The pool and the reflex must rank the same options the same way, or the agent oscillates between them."""

    def state(self, **kw):
        s = dict(here=(0, 0, 0), hp=20.0, sword=1, protection=0.0, night=True, blocks=64,
                 hazards=[threat.row((4, 0, 0), 2.0, (-1.0, 0, 0), "minecraft:zombie")], work_s=20.0)
        s.update(kw)
        return s

    def test_decide_agrees_with_what_the_pool_would_offer(self):
        st = self.state()
        opts = threat.options(st)
        price = lambda dhp: dhp
        best_by_saving = max((o for o in opts if o.kind != "ignore"),
                             key=lambda o: threat.saves(o, opts, price, st["work_s"]))
        self.assertEqual(threat.decide(st, price).kind, best_by_saving.kind)


class PricesForTheOtherPlanner(unittest.TestCase):
    """What combat hands ordinary play: a rate and a field, never an answer."""

    def rows(self, *hazards):
        return dict(here=(0, 0, 0), hp=20.0, sword=1, protection=0.0, hazards=list(hazards))


    # (the threat) → the circle not to walk into: its reach plus a margin
    NO_GO = [("a zombie, reach 2", threat.row((10, 0, 0), 2.0, (0, 0, 0), "minecraft:zombie"), [((10, 0, 0), 4.0)]),
             ("a skeleton, reach 15", threat.row((10, 0, 0), 15.0, (0, 0, 0), "minecraft:skeleton"),
              [((10, 0, 0), 17.0)]),
             ("a creeper, reach 3", threat.row((10, 0, 0), 3.0, (0, 0, 0), "minecraft:creeper"), [((10, 0, 0), 5.0)]),
             ("must fail: nothing: no circle", None, [])]

    def test_no_go_over_the_table(self):
        for name, hazard, want in self.NO_GO:
            with self.subTest(name):
                zones = threat.no_go(self.rows(*([hazard] if hazard else [])))
                self.assertEqual(zones, want)


class TheFastLane(unittest.TestCase):
    """Threat answers are bid for the body at perception's cadence, not queued for the next ten-second round."""

    def setUp(self):
        from bonobo import fight_loop, threat as sv
        self.fight_loop, self.sv = fight_loop, sv
        fight_loop.STATE.held = None      # each case is its own situation, not a continuation of the last

    def bid(self, rows, hp=20, sword=2, armor=8):
        ss = self.sv.price_state(hp=hp, sword=sword, armor=armor)
        state = {"x": 0, "y": 64, "z": 0, "health": hp, "armor": armor, "sword_tier": sword, "blocks": 64}
        return self.fight_loop.bid(state, rows, lambda dhp: self.sv.hp_seconds(ss, dhp))

    # (rows in sight) → (answer, seconds it is worth), or None: no bid
    BIDS = [("must fail: nothing near", [], None),
            ("a zombie 5 away", [row("minecraft:zombie", 5, 0)], ("fight", 197.2)),
            ("a zombie 60 away: not worth the body", [row("minecraft:zombie", 60, 0)], None),
            ("a skeleton 10 away", [row("minecraft:skeleton", 10, 0)], ("fight", 173.1))]

    def test_bids_over_the_table(self):
        for name, rows, want in self.BIDS:
            with self.subTest(name):
                self.fight_loop.STATE.held = None
                got = self.bid(rows)
                self.assertEqual(None if got is None else (got[0].kind, got[1]), want)

    def test_the_bid_is_what_its_own_answer_saves(self):
        """Closed against the state the bid itself built, not against one reassembled here: a test that rebuilds
        the state vector is a second copy of `fight_loop.threat_state`, and the two drifted the moment the live
        one started reading the ground and the kit.
        """
        rows = [row("minecraft:zombie", 5, 0)]
        ss = self.sv.price_state(hp=20, sword=2, armor=8)
        price = lambda dhp: self.sv.hp_seconds(ss, dhp)
        state = {"x": 0, "y": 64, "z": 0, "health": 20, "armor": 8, "sword_tier": 2, "blocks": 64}
        option, worth = self.fight_loop.bid(state, rows, price)
        st = self.fight_loop.threat_state(state, rows)
        opts = threat.options(st)
        same = next(o for o in opts if o.kind == option.kind)
        self.assertAlmostEqual(worth, round(threat.saves(same, opts, price, threat.horizon_for(st)), 1), places=1)


class AStaleTargetIsDecidedAgain(unittest.TestCase):
    """An attack ending 'target not found' means the held choice names a gone mob: fight_loop.redecide drops HELD
    and decides again at once on the latest reading without it — the next post carries the live target's id."""

    def test_redecide_over_the_table(self):
        from unittest import mock
        from bonobo import fight_loop, threat as sv
        ss = sv.price_state(hp=20, sword=2, armor=8)
        price = lambda dhp: sv.hp_seconds(ss, dhp)  # noqa: E731
        state = {"x": 0, "y": 64, "z": 0, "health": 20, "armor": 8, "sword_tier": 2, "blocks": 64}
        dead, live = row("minecraft:zombie", 1.5, 0), row("minecraft:zombie", 5, 0)
        # (name, the reading (rows, ids), a bid seen before?) → the target the next post carries, or None
        rows = [("the gone one still listed, a live one 5 off: the live id", ([dead, live], [7, 9]), True, 9),
                ("only the live one read: its id", ([live], [9]), True, 9),
                ("must fail: only the gone one: nothing to post", ([dead], [7]), True, None),
                ("no bid made yet: nothing to decide on", ([dead, live], [7, 9]), False, None)]
        for name, reading, seen, want in rows:
            with self.subTest(name):
                fight_loop.reset()
                if seen:
                    fight_loop.bid(state, [dead], price, ids=[7])      # the held choice: fight 7
                    self.assertEqual(fight_loop.STATE.held.choice.action.option.target, 7)
                stale_held = fight_loop.STATE.held
                with mock.patch.object(fight_loop.threat, "threats_seen", return_value=reading):
                    fresh = fight_loop.redecide(7)
                self.assertEqual(None if fresh is None else fresh.target, want)
                if seen:
                    self.assertIsNot(fight_loop.STATE.held, stale_held, "the stale HELD was dropped")


class AFailedAnswerIsDecidedAgain(unittest.TestCase):
    """An answer that raised (a jar error, nothing to do it with) is refused a while and the held choice dropped: the
    next decision is a fresh one without it, never the same option bid again (combat__dig_in 01:03:09-11: the same
    fight ×3, then death). An attack naming no mob is never built, nor kept as the held choice."""

    def setUp(self):
        from bonobo import fight_loop
        fight_loop.reset()
        self.addCleanup(fight_loop.reset)

    def test_rows(self):
        from unittest import mock
        from bonobo import fight_loop, threat as sv
        ss = sv.price_state(hp=20, sword=2, armor=8)
        price = lambda dhp: sv.hp_seconds(ss, dhp)  # noqa: E731
        state = {"x": 0, "y": 64, "z": 0, "health": 20, "armor": 8, "sword_tier": 2, "blocks": 64}
        live = row("minecraft:zombie", 1.5, 0)
        first = fight_loop.bid(state, [live], price, ids=[9], now=100.0)
        self.assertEqual((first[0].kind, first[0].target), ("fight", 9))
        # (situation, marked failed?, when the next bid is made) → the same fight bid again?
        rows = [("must fail: nothing failed: the same fight is held", False, 100.5, True),
                ("it failed: refused, the next bid is another answer", True, 100.5, False),
                ("FAILED_S later it may be chosen again", True, 100.0 + fight_loop.FAILED_S + 0.5, True)]
        for name, failed, when, again in rows:
            with self.subTest(name):
                fight_loop.reset()
                fight_loop.bid(state, [live], price, ids=[9], now=100.0)
                if failed:
                    fight_loop._mark_failed(first[0], now=100.0)
                    self.assertIsNone(fight_loop.STATE.held, "the held choice is dropped")
                with mock.patch.object(fight_loop.time, "time", return_value=when):
                    nxt = fight_loop.bid(state, [live], price, ids=[9], now=when)
                same = nxt is not None and (nxt[0].kind, nxt[0].target) == ("fight", 9)
                self.assertEqual(same, again)

    def test_carry_re_decides_a_failed_answer(self):
        from unittest import mock
        from bonobo import api, fight_loop
        a, b = (type("Option", (), {"kind": k, "target": t})() for k, t in (("fight", 9), ("evade", (3, 64, 0))))
        wants, posted, failed = [a, b, None], [], []

        def answer(want):
            if want is a:
                raise api.McError("/task?wait=0: 500 JsonNull")
            posted.append(want.kind)
            return {"id": 1}

        def on_failed(want, err):
            failed.append((want.kind, str(err)))
            wants.pop(0)                         # decided again: what is wanted now is the next answer

        # (situation, the failure handler) → (raised?, failed, posted)
        rows = [("a failed answer is decided again, the next one posted", on_failed, False,
                 [("fight", "/task?wait=0: 500 JsonNull")], ["evade"]),
                ("must fail: without the handler the error ends the loop (the old engagement's end)", None, True, [],
                 [])]
        for name, handler, raises, want_failed, want_posted in rows:
            with self.subTest(name), mock.patch.object(fight_loop.time, "sleep"), \
                    mock.patch.object(api, "get", return_value={"status": "running"}):
                wants[:], posted[:], failed[:] = [a, b, None], [], []
                held = {"done": None, "task_id": None}
                steps = iter(range(4))
                loop = fight_loop.carry(lambda: wants[0], answer, lambda: next(steps, None) is not None, held,
                                        failed=handler)
                try:
                    list(loop)
                    got = False
                except api.McError:
                    got = True
                self.assertEqual((got, failed, posted), (raises, want_failed, want_posted))

    def test_an_attack_names_its_mob(self):
        from bonobo import fight_loop
        opt = lambda target: type("Option", (), {"kind": "fight", "target": target})()  # noqa: E731
        state = {"feet": (0, 64, 0), "inv": None, "region": None, "protected": set(), "hazards": []}
        self.assertEqual(fight_loop._attack(opt(None), state), [], "must fail: attack(entity=None) is never built")
        self.assertEqual(fight_loop._attack(opt(7), state)[0]["entity"], 7)


class AOneShotAnswerIsDoneOnce(unittest.TestCase):
    """carry re-posts only what continues (a fight swings on): a dig, a pillar, a walk away is done once its task is,
    and posted again only for a fresh decision — never the finished one again at the air it just dug."""

    def test_rows(self):
        from unittest import mock
        from bonobo import api, fight_loop
        opt = lambda kind: type("Option", (), {"kind": kind, "target": ("down", 2) if kind == "reshape" else 7})()  # noqa: E731
        # (situation, the wants in turn, perception's answer each loop) → posts
        dig, dig2, hit = opt("reshape"), opt("reshape"), opt("fight")
        rows = [("must fail: a finished dig is not posted again (the held decision)", [dig, dig, dig, dig], 1),
                ("a fresh decision to dig again is posted", [dig, dig, dig2, dig2], 2),
                ("a fight swings on: posted again when its task ends", [hit, hit, hit, hit], 2)]
        for name, wants, posts in rows:
            with self.subTest(name), mock.patch.object(fight_loop.time, "sleep"), \
                    mock.patch.object(api, "get", return_value={"status": "succeeded", "message": "done"}):
                posted = []
                seq = iter(wants)
                current = {"w": None}

                def want():
                    current["w"] = next(seq, None)
                    return current["w"]
                held = {"done": None, "task_id": None}
                list(fight_loop.carry(want, lambda w: posted.append(w) or {"id": len(posted)},
                                      lambda: True, held, again=True))
                self.assertEqual(len(posted), posts)

    def test_still_to_mine(self):
        from bonobo import fight_loop
        tasks = [{"type": "mine", "x": 0, "y": 63, "z": 0}, {"type": "mine", "x": 0, "y": 62, "z": 0},
                 {"type": "pillar", "item": "minecraft:dirt"}]
        solid = {(0, 62, 0)}
        got = fight_loop.still_to_mine(tasks, lambda c: c in solid)
        self.assertEqual([t.get("y") for t in got], [62, None], "must fail: the dug cell (63, air now) is dropped")


class ARequestTheGameDroppedIsNotALostGame(unittest.TestCase):
    """api: a connection reset on one request while the game still answers /status is that request's error (McError,
    the jar's log names it), not GameUnreachable — which stood the brain down to wait for a game that was there."""

    def test_rows(self):
        import http.client
        from unittest import mock
        from bonobo import api
        # (situation, the request's failure, the game answers /status) → the error raised
        rows = [("the jar dropped the request, the game is up: its error", http.client.RemoteDisconnected("x"), True,
                 "McError"),
                ("must fail: the game is gone: GameUnreachable", http.client.RemoteDisconnected("x"), False,
                 "GameUnreachable"),
                ("refused outright: GameUnreachable, no probe needed", ConnectionRefusedError(), True,
                 "GameUnreachable")]
        for name, err, up, want in rows:
            with self.subTest(name), mock.patch.object(api._DIRECT, "open", side_effect=err), \
                    mock.patch.object(api, "_game_up", return_value=up), \
                    mock.patch.object(api, "_token", return_value="t"), mock.patch.object(api.tape, "REPLAY", None):
                try:
                    api.api("POST", "/task?wait=0", {"tasks": []})
                    got = None
                except api.McError as e:
                    got = type(e).__name__
                self.assertEqual(got, want)


class TheSkillsBatches(unittest.TestCase):
    """combat's shoot, as the pure batch the skills post."""

    class Bag:
        def __init__(self, items=(), offhand="minecraft:air"):
            self.items, self.hand = set(items), offhand

        def count(self, item):
            return 1 if item in self.items else 0

        def offhand(self):
            return self.hand

    def test_shoot_batch(self):
        from bonobo import combat
        eye = (0.0, 65.62, 0.0)
        drop = lambda d: 0.5 * combat.GRAVITY * (d / combat.ARROW_SPEED) ** 2      # noqa: E731
        # (situation, entity, hold) → where the arrow is aimed (x, y, z) and how long the draw
        rows = [("10 blocks, default height: body at 0.6, raised by the drop", {"x": 10.0, "y": 64.0, "z": 0.0},
                 22, (10.0, 64.6 + drop(10.0), 0.0)),
                ("must fail: a drop at no distance — point blank: no drop", {"x": 0.0, "y": 64.0, "z": 0.0}, 22, (0.0, 64.6, 0.0)),
                ("a tall target aims higher", {"x": 0.0, "y": 64.0, "z": 20.0, "height": 2.0}, 22,
                 (0.0, 65.2 + drop(20.0), 20.0)),
                ("a short draw", {"x": 10.0, "y": 64.0, "z": 0.0}, 10, (10.0, 64.6 + drop(10.0), 0.0))]
        for name, e, hold, (x, y, z) in rows:
            with self.subTest(name):
                (shot,) = combat.shoot_batch(e, eye, hold_ticks=hold)
                self.assertEqual((shot["type"], shot["item"], shot["holdTicks"]), ("use_item", "minecraft:bow", hold))
                self.assertEqual([round(shot[k], 6) for k in "xyz"], [round(x, 6), round(y, 6), round(z, 6)])

    def test_a_fight_is_on_while_the_body_is_engaged(self):
        import threading
        from unittest import mock
        from bonobo import arbiter, fight_loop
        done = threading.Event()
        live = threading.Thread(target=done.wait, daemon=True)
        live.start()
        # (situation, the body engaged by a boss fight, our engagement's thread) → a fight is on
        rows = [("must fail: nothing engaged", False, None, False),
                ("a boss fight holds the body", True, None, True),
                ("our engagement is running", False, live, True),
                ("must fail: our engagement's thread has ended", False, threading.Thread(target=lambda: None), False)]
        try:
            for name, engaged, thread, want in rows:
                with self.subTest(name), mock.patch.multiple(fight_loop.STATE, thread=thread, intent="ours"):
                    body = arbiter.Motion()
                    if engaged:
                        body.engage()
                    saved, arbiter.BODY = arbiter.BODY, body
                    try:
                        self.assertEqual(fight_loop.active(), want)
                    finally:
                        arbiter.BODY = saved
        finally:
            done.set()


def nav_mine(cell, down=False):
    from bonobo import nav
    return nav.mine_task(cell, down=down)


class EachAnswerIsABatch(unittest.TestCase):
    """`fight_loop.batch`: what one answer posts, from a body state. Pure; [] means it cannot be done from here."""

    class Bag:
        def __init__(self, counts=None, offhand="minecraft:air"):
            self.counts, self.hand = dict(counts or {}), offhand

        def count(self, item):
            return self.counts.get(item, 0)

        def offhand(self):
            return self.hand

    @staticmethod
    def option(kind, target=None):
        return type("Option", (), {"kind": kind, "target": target})()

    def state(self, **bag):
        return {"feet": (0, 64, 0), "inv": self.Bag(**bag), "region": None, "protected": set(),
                "threats": [row("minecraft:zombie", 5, 0)]}

    ROWS = [("fight: attack the target", ("fight", 42), {}, [{"type": "attack", "entity": 42}]),
            ("evade: travel there, digging and bridging as priced, never over the void (nav.MOVES)",
             ("evade", (-16, 64, 0)), {"counts": {"building": 12}},
             [{"type": "travel", "x": -16, "y": 64, "z": 0, "range": 3, "break": True, "place": True,
               "voidBridge": False, "placeBudget": 12, "avoid": []}]),
            ("eat the first food carried", ("eat",), {"counts": {"minecraft:cooked_beef": 3}},
             [{"type": "eat", "item": "minecraft:cooked_beef"}]),
            ("must fail: eat with nothing to eat: not an answer", ("eat",), {}, []),
            ("must fail: the shield is no answer (the jar's reflex raises it): no batch", ("shield",),
             {"offhand": "minecraft:shield"}, []),
            ("dig down two", ("reshape", ("down", 2)), {},
             [nav_mine((0, 63, 0), down=True), nav_mine((0, 62, 0), down=True)]),
            ("stand two up", ("reshape", ("under", 2)), {"counts": {"minecraft:cobblestone": 5}},
             [{"type": "pillar", "item": "minecraft:cobblestone"}] * 2),
            ("a wall toward the zombie in the east", ("reshape", ("between", 2)),
             {"counts": {"minecraft:cobblestone": 5}},
             [{"type": "place", "item": "minecraft:cobblestone", "x": 1, "y": 64 + i, "z": 0} for i in range(2)]),
            ("no blocks to stand on: not an answer", ("reshape", ("under", 1)), {}, []),
            ("walling in with no region read: not an answer", ("wall_in",), {}, []),
            ("ignoring is no batch", ("ignore",), {}, [])]

    def test_batch_over_the_table(self):
        from bonobo import fight_loop
        for name, (kind, *target), bag, want in self.ROWS:
            with self.subTest(name):
                got = fight_loop.batch(self.option(kind, target[0] if target else None), self.state(**bag))
                self.assertEqual(got, want)


class TheLeaseSurvivesBlindMoments(unittest.TestCase):
    """Perception goes blind for a moment all the time: the entity read is a second old, the thread was busy, the
    rows aged out. A lease that reads "nothing visible" as "nothing to answer" hands the body back mid-fight, and
    the planner's next mine task lands on top of the answer. Only a real price says the answer is done."""

    def setUp(self):
        from bonobo import fight_loop, threat as sv
        self.fight_loop, self.sv = fight_loop, sv
        fight_loop.STATE.held = None

    def release(self, frames):
        """The lease's own judgement over a few perception frames [(seconds since the first, rows seen)]."""
        from unittest import mock
        from bonobo import field
        state = {"x": 0, "y": 64, "z": 0, "health": 12, "armor": 0, "sword_tier": 2,
                 "food_items": 0, "shield": False, "blocks": 64, "field": field.Field()}
        ss = self.sv.price_state(hp=12, sword=2)
        price = lambda dhp: self.sv.hp_seconds(ss, dhp)
        self.fight_loop.STATE.chase_at = None
        done = None
        for dt, rows in frames:
            with mock.patch.object(self.fight_loop.time, "time", return_value=1000.0 + dt):
                done = self.fight_loop.lease_done(state, rows, price)
        return done

    # (what perception sees, frame by frame) → is the answer done (hand the body back)?
    LOST = 3.5            # a little past fight_loop.LOST_S
    LEASE = [("must fail: a blind moment: nothing visible for a second", [(0, []), (1.0, [])], False),
             ("a zombie still at 4", [(0, [row("minecraft:zombie", 4, 0)]), (LOST, [row("minecraft:zombie", 4, 0)])],
              False),
             ("a zombie at 20, inside its notice radius: it follows, not over",
              [(0, [row("minecraft:zombie", 20, 0)]), (LOST, [row("minecraft:zombie", 20, 0)])], False),
             ("a skeleton at 10", [(0, [row("minecraft:skeleton", 10, 0)]), (LOST, [row("minecraft:skeleton", 10, 0)])],
              False),
             ("must fail: closing at 2 b/s is no longer read here (the jar predicts the hit): 60 away, over",
              [(0, [row("minecraft:zombie", 60, 0, vel=(-2.0, 0.0, 0.0))]),
               (LOST, [row("minecraft:zombie", 60, 0, vel=(-2.0, 0.0, 0.0))])], True),
             ("a zombie 60 away, standing, for LOST_S: outrun, and nothing owed",
              [(0, [row("minecraft:zombie", 60, 0)]), (LOST, [row("minecraft:zombie", 60, 0)])], True),
             ("the zombie killed: gone for LOST_S", [(0, [row("minecraft:zombie", 4, 0)]), (0.5, []), (LOST + 1, [])],
              True),
             ("gone only a moment ago", [(0, [row("minecraft:zombie", 4, 0)]), (1.0, [])], False)]

    def test_the_lease_over_the_table(self):
        for name, frames, done in self.LEASE:
            with self.subTest(name):
                self.assertEqual(self.release(frames), done)

    def test_a_hit_the_jar_predicts_keeps_the_answer(self):
        """The jar's time to impact (/entities tti_ticks → threat.hit_due_s) is what "still coming" means."""
        from unittest import mock
        far = [(0, [row("minecraft:zombie", 60, 0)]), (self.LOST, [row("minecraft:zombie", 60, 0)])]
        # (situation, the jar's soonest hit, seconds) → done?
        rows = [("a hit due in 1.5 s: not over", 1.5, False),
                ("must fail: nothing predicted: outrun, over", None, True),
                ("a hit due past LOST_S: over", self.fight_loop.LOST_S + 2, True)]
        for name, hit_s, done in rows:
            with self.subTest(name), mock.patch.object(self.fight_loop.threat, "hit_due_s", return_value=hit_s):
                self.assertEqual(self.release(far), done)

if __name__ == "__main__":
    unittest.main()


class Losses(unittest.TestCase):
    """The day's price terms (threat.*_loss): each one's zero, its full cost and a point between, read against the
    belief table the module prices with (`_R`, `_T`, `_K`)."""
    R, T, K = threat._R, threat._T, threat._K

    def st(self, **kw):
        return threat.price_state(**kw)

    def test_bag_loss(self):
        full = self.T["day_s"] * self.K["mining_share_of_day"]
        c = self.R["bag_comfortable"]
        for name, free, want in [("empty bag: nothing lost", 36, 0.0), ("at the comfortable line: nothing", c, 0.0),
                                 ("half way down", c / 2, full / 2), ("full: the whole mining share", 0, full)]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.bag_loss(self.st(bag_free=free)), want)

    def test_larder_and_light(self):
        day, death = self.T["day_s"], self.T["death_cost_s"]
        starving = self.R["starving_slowdown"] * day
        for name, meals, want in [("a day's meals", 8, 0.0), ("some: runs out today", 2, 0.15 * day),
                                  ("one: starving", 1, starving),
                                  ("none: starving and may die", 0, starving + self.R["starving_death"] * death)]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.larder_loss(self.st(food_items=meals)), want)
        for name, torches, want in [("torches: nothing", True, 0.0),
                                    ("dark: the chance of a death", False, self.R["dark_work_death"] * death)]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.light_loss(self.st(torches=torches)), want)

    def test_hunger_loss(self):
        span = self.T["day_s"] * self.R["meal_share_of_day"]
        full, low = self.R["food_full"], self.R["food_low"]
        for name, food, want in [
                ("full bar: nothing", full, 0.0),
                ("a little down: scaled slowdown", full - 2, span * 2 / full * self.R["hunger_slowdown"]),
                ("at the low line: starving floor", low,
                 span * max((full - low) / full * self.R["hunger_slowdown"], self.R["starving_slowdown"])),
                ("empty", 0, span * max(self.R["hunger_slowdown"], self.R["starving_slowdown"]))]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.hunger_loss(self.st(food=food)), want)

    def test_food_loss_is_both_terms(self):
        for name, kw in [("fed and stocked", {}), ("hungry, stocked", {"food": 4, "food_items": 8}),
                         ("fed, empty larder", {"food_items": 0}), ("hungry and empty", {"food": 2, "food_items": 0})]:
            with self.subTest(name):
                s = self.st(**kw)
                self.assertAlmostEqual(threat.food_loss(s), threat.hunger_loss(s) + threat.larder_loss(s))

    def test_tool_loss(self):
        mining = self.K["mining_share_of_day"] * self.T["day_s"]
        iron = self.K["mine_time_iron"]
        for name, tier, want in [("iron: the reference", 2, 0.0), ("diamond: no better than the reference", 3, 0.0),
                                 ("stone", 1, mining * (self.K["mine_time_stone"] - iron)),
                                 ("no pickaxe", 0, mining * (self.K["mine_time_no_pickaxe"] - iron))]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.tool_loss(self.st(pickaxe=tier)), want)

    def test_night_loss(self):
        death, night = self.T["death_cost_s"], self.T["night_s"]
        for name, kw, want in [
                ("bed in a shelter: slept through", {"bed": True, "sheltered": True}, 0.0),
                ("bed in the open", {"bed": True}, self.R["night_bed_open"] * death),
                ("sheltered, armed", {"sheltered": True, "sword": 1}, self.R["night_sheltered"] * death),
                ("open, unarmed, 3 sleepless nights",
                 {"nights_missed": 3},
                 (self.R["night_open"] + self.R["no_sword_night"] + self.R["phantom_night_death"]) * death + night)]:
            with self.subTest(name):
                self.assertAlmostEqual(threat.night_loss(self.st(**kw)), want)

    def test_fights_and_hurt(self):
        # (situation, state, what must hold) — the fight's price falls with gear, the deficit's with health
        base = threat.fight_loss(self.st())
        rows = [("a sword makes a day of fights cheaper", threat.fight_loss(self.st(sword=2)) < base, True),
                ("armour makes the same fight cheaper in health",
                 threat.encounter_damage(self.st(armor=4))[1] < threat.encounter_damage(self.st())[1], True),
                ("must fail: full health: no deficit to pay", threat.hurt_loss(self.st(hp=20)), 0.0),
                ("a deficit costs at least its regeneration",
                 threat.hurt_loss(self.st(hp=10)) >= 10 * self.R["regen_s_per_hp"], True),
                ("0 hp is priced like 0.1 (clamped, no division by zero)",
                 threat.hurt_loss(self.st(hp=0)) == threat.hurt_loss(self.st(hp=0.1)), True)]
        for name, got, want in rows:
            with self.subTest(name):
                self.assertEqual(got, want)

    def test_expected_loss_is_the_sum(self):
        for name, kw in [("fresh start", {}), ("night, hungry, hurt", {"night": True, "food": 3, "hp": 8}),
                         ("well kept", {"bed": True, "sheltered": True, "torches": True, "pickaxe": 2, "sword": 2,
                                        "food_items": 8}), ("full bag", {"bag_free": 0})]:
            with self.subTest(name):
                s = self.st(**kw)
                parts = (threat.night_loss(s) + threat.food_loss(s) + threat.tool_loss(s) + threat.light_loss(s)
                         + threat.fight_loss(s) + threat.hurt_loss(s) + threat.bag_loss(s))
                self.assertAlmostEqual(threat.expected_loss(s), parts)

    def test_price_state_refuses_unknown_keys(self):
        for name, kw, err in [("known key", {"hp": 5}, None), ("typo", {"hpp": 5}, "hpp"),
                              ("two unknown", {"a": 1, "b": 2}, "a"), ("empty", {}, None)]:
            with self.subTest(name):
                if err is None:
                    self.assertEqual(threat.price_state(**kw)["hp"], kw.get("hp", 20))
                else:
                    with self.assertRaisesRegex(KeyError, err):
                        threat.price_state(**kw)


class Kit(unittest.TestCase):
    """What the threat model believes we fight with: read from the bag, again whenever it may have changed."""

    # (situation, sword items carried (id, damage)) → the dps table's sword level
    SWORDS = [("an iron sword in the bag", [("minecraft:iron_sword", 0)], 2),
              ("a diamond sword in hand (a hotbar slot)", [("minecraft:diamond_sword", 10)], 3),
              ("must fail: no sword: the fist", [], 0),
              ("a stone sword", [("minecraft:stone_sword", 0)], 1),
              ("a wooden sword: level 1 (wood/stone), not the fist", [("minecraft:wooden_sword", 0)], 1),
              ("a broken-down iron sword and a stone one: the stone", [("minecraft:iron_sword", 250),
                                                                      ("minecraft:stone_sword", 3)], 1),
              ("netherite: the table's top", [("minecraft:netherite_sword", 0)], 3)]

    def test_sword_level_over_the_table(self):
        from unittest import mock
        from bonobo import perception
        from tests.world import bag, inventory, slot
        for name, swords, want in self.SWORDS:
            with self.subTest(name):
                inv = bag(inventory(*[slot(i.split(":")[1], 1, d) for i, d in swords]))
                with mock.patch("bonobo.world.Inventory", return_value=inv):
                    perception.STATE.kit_sig = object()                 # a fresh read
                    self.assertEqual(perception.kit(("x", name))["sword_tier"], want)

    def test_signature_changes_when_the_bag_may_have(self):
        from bonobo import perception
        base = {"selectedSlot": 0, "screen": "none", "armor": 0}
        sig = perception.kit_signature
        rows = [("the same state a moment later", base, 10.0, base, 10.5, True),
                ("must fail: the held slot moved", base, 10.0, {**base, "selectedSlot": 3}, 10.1, False),
                ("armour put on", base, 10.0, {**base, "armor": 15}, 10.1, False),
                ("a chest screen closed", {**base, "screen": "GenericContainerScreen"}, 10.0, base, 10.1, False),
                ("KIT_TTL_S passed (a sword given by command)", base, 10.0, base, 10.0 + perception.KIT_TTL_S, False)]
        for name, a, ta, b, tb, same in rows:
            with self.subTest(name):
                self.assertEqual(sig(a, ta) == sig(b, tb), same)


class EvadeOnlyPostpones(unittest.TestCase):
    """Walking away from what follows (it notices us at the spot, or shoots that far) is no answer while a fight is
    on offer: the same threat is there again. Evade is for what cannot be fought, or cover."""

    # (situation, threats, our state) → the answer
    ROWS = [("one zombie, an iron sword", [row("minecraft:zombie", 4, 0)], dict(sword=2, protection=0.5), "fight"),
            ("one skeleton 8 off, an iron sword", [row("minecraft:skeleton", 8, 0)], dict(sword=2), "fight"),
            ("one skeleton 14 off, an iron sword: it shoots that far, fight it", [row("minecraft:skeleton", 14, 0)],
             dict(sword=2, protection=0.5), "fight"),
            ("a creeper, cover 6 behind, an iron sword: fought hit-and-back all the same (it stays, or follows)",
             [row("minecraft:creeper", 3, 0)], dict(sword=2, cover=(HERE[0] - 6, HERE[1], HERE[2])), "fight"),
            ("a creeper 4 off, an iron sword: fight (keep off)", [row("minecraft:creeper", 4, 0)], dict(sword=2),
             "fight"),
            ("a creeper 4 off, a stone sword: fight (keep off)", [row("minecraft:creeper", 4, 0)], dict(sword=1),
             "fight"),
            ("a creeper 4 off, bare hands: leave", [row("minecraft:creeper", 4, 0)], dict(sword=0), "evade"),
            ("a creeper 2 off at 4 hp: one late step is the end, leave", [row("minecraft:creeper", 2, 0)],
             dict(sword=2, hp=4), "evade"),
            ("a creeper and a zombie, an iron sword: the fight, creeper first",
             [row("minecraft:creeper", 4, 0), row("minecraft:zombie", -3, 0)], dict(sword=2), "fight"),
            ("three zombies, 5 hp, a stone sword: cannot win, leave",
             [row("minecraft:zombie", 3, 0), row("minecraft:zombie", 0, 3), row("minecraft:zombie", -3, 0)],
             dict(hp=5, sword=1), "evade"),
            ("must fail: one zombie 40 away", [row("minecraft:zombie", 40, 0)], dict(sword=2), "ignore"),
            # A zombie 3 off, by what is in hand (ban_needs_a_failure / resume_after_combat evaded: their report's bag
            # was empty — no sword read, no fight on offer).
            ("a zombie 3 off, an iron sword: fight", [row("minecraft:zombie", 3, 0)], dict(sword=2), "fight"),
            ("a zombie 3 off, a stone sword, full health: fight", [row("minecraft:zombie", 3, 0)], dict(sword=1),
             "fight"),
            ("a zombie 3 off, a stone sword, 10 hp: the fight would cost it all, leave", [row("minecraft:zombie", 3, 0)],
             dict(sword=1, hp=10), "evade"),
            ("a zombie 3 off, bare hands: no fight on offer, leave", [row("minecraft:zombie", 3, 0)], dict(sword=0),
             "evade")]

    def test_no_evade_past_a_lethal_drop(self):
        """Nowhere to walk to on connected ground (footing answers None everywhere): evade is not offered."""
        nowhere, anywhere = (lambda spot: None), (lambda spot: tuple(spot))
        rows = [("bare hands, open ground: leave", dict(sword=0, footing=anywhere), "evade"),
                ("must fail: bare hands, a pillar in the sky, nothing to wall in with: carry on (no evade)",
                 dict(sword=0, footing=nowhere), "ignore"),
                ("bare hands, a pillar in the sky, 16 blocks: wall in", dict(sword=0, blocks=16, footing=nowhere),
                 "wall_in"),
                ("an iron sword, a pillar: fight", dict(sword=2, footing=nowhere), "fight"),
                ("no ground read (footing None): as before", dict(sword=0), "evade")]
        for name, kw, want in rows:
            with self.subTest(name):
                self.assertEqual(decide([row("minecraft:zombie", 3, 0)], **kw).kind, want)

    def test_answers_over_the_table(self):
        for name, hazards, kw, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(decide(hazards, **kw).kind, want)

    def test_follows_to(self):
        from bonobo import estimate
        cases = [("a zombie 20 from the spot: notices (35)", row("minecraft:zombie", 20, 0), True),
                 ("a zombie 50 from the spot: left behind", row("minecraft:zombie", 50, 0), False),
                 ("a skeleton 15 off: in reach", row("minecraft:skeleton", 15, 0), True),
                 ("must fail: a skeleton 30 off: neither", row("minecraft:skeleton", 30, 0), False)]
        for name, h, want in cases:
            with self.subTest(name):
                self.assertIs(estimate.follows_to(HERE, h), want)


class ShieldAndHole(unittest.TestCase):
    """A shield is no answer of its own — the jar's reflex raises it for any predicted hit, so it is protection in
    the price of every answer; a hole down needs ground that digs, not blocks carried."""

    def test_answers_over_the_table(self):
        from bonobo import field
        crowd = [row("minecraft:zombie", 4, 0), row("minecraft:zombie", 0, 4), row("minecraft:zombie", -4, 0)]
        rows = [("a zombie, a sword, a shield: fight (the reflex shields)", [row("minecraft:zombie", 4, 0)],
                 dict(sword=2, shield=True), ("fight", 0)),
                ("a zombie, a sword, no shield: fight", [row("minecraft:zombie", 4, 0)], dict(sword=2), ("fight", 0)),
                ("three zombies at night, 10 hp, no sword, ground that digs: a hole down", crowd,
                 dict(sword=0, hp=10, night=True, dig_ok=True, field=field.Field()), ("reshape", ("down", 2))),
                ("must fail: the same, ground that does not dig: leave", crowd,
                 dict(sword=0, hp=10, night=True, dig_ok=False, field=field.Field()), ("evade", (0, 64, -16)))]
        for name, hazards, kw, want in rows:
            with self.subTest(name):
                d = decide(hazards, **kw)
                self.assertEqual((d.kind, d.target), want)

    def test_no_shield_column(self):
        """must fail: no world offers a shield or a shielded fight as an answer — the reflex is the one shield."""
        kinds = {o.kind for o in threat.options({"here": HERE, "hp": 12, "sword": 2, "protection": 0.0,
                                                 "hazards": [row("minecraft:zombie", 4, 0)], "ids": [7],
                                                 "shield": True})}
        self.assertFalse(kinds & {"shield", "fight_shielded"}, kinds)



class EveryColumnMustBeSurvivable(unittest.TestCase):
    """One veto for every column (`threat.survivable`): what an answer expects to lose over its own seconds stays
    under the health we have. Only the fight had it; a 1.2 s pillar at 3.1 hp beside three zombies was offered."""

    def test_offered_over_the_table(self):
        from bonobo import field
        crowd = [row("minecraft:zombie", 1.5, 0), row("minecraft:zombie", 0, 1.5), row("minecraft:zombie", -1.5, 0)]
        one = [row("minecraft:zombie", 4, 0)]
        # (name, hp, hazards, a shield in hand, column, offered?)
        coming = [row("minecraft:zombie", 6, 0), row("minecraft:zombie", 0, 6), row("minecraft:zombie", -6, 0)]
        # (beside, three zombies' hits knock every pillar down: APillarUnderHits prices that; 6 off it is on offer)
        rows = [("20 hp, three zombies 6 off: the pillar is on offer", 20, coming, False, "reshape", True),
                ("must fail: 3.1 hp, three zombies beside, a shield that survives: the 1.2 s pillar is not", 3.1,
                 crowd, True, "reshape", False),
                ("20 hp, one zombie 4 off: the fight is on offer", 20, one, False, "fight", True),
                ("4 hp, three zombies beside: no fight either", 4, crowd, False, "fight", False),
                ("3.1 hp: carrying on is never vetoed", 3.1, crowd, False, "ignore", True)]
        for name, hp, hazards, shield, kind, offered in rows:
            with self.subTest(name):
                state = {"here": HERE, "hp": hp, "sword": 2, "protection": 0.0, "blocks": 5, "hazards": hazards,
                         "ids": list(range(len(hazards))), "field": field.Field(), "shield": shield}
                self.assertEqual(kind in {o.kind for o in threat.options(state)}, offered)

    def test_nothing_survives_keeps_the_least_loss(self):
        from bonobo import threat as t
        opt = lambda kind, hp: t.Option(kind, None, hp, 1.0, kind)
        # (name, options, hp) → the columns kept
        rows = [("one survives: only it", [opt("ignore", 0), opt("fight", 30), opt("evade", 2)], 10, ["ignore", "evade"]),
                ("none survives: the least loss is kept", [opt("ignore", 0), opt("fight", 30), opt("evade", 11)], 4,
                 ["ignore", "evade"]),
                ("must fail: all survive, none dropped", [opt("ignore", 0), opt("fight", 3), opt("evade", 2)], 20,
                 ["ignore", "fight", "evade"]),
                ("carrying on alone stays alone", [opt("ignore", 0)], 1, ["ignore"]),
                ("a lethal fight alone is not the way out", [opt("ignore", 0), opt("fight", 30)], 4, ["ignore"])]
        for name, opts, hp, want in rows:
            with self.subTest(name):
                self.assertEqual([o.kind for o in t.survivors(opts, hp)], want)


class ASealedPassageLeavesNothing(unittest.TestCase):
    """Reshaping is priced by what still gets through afterwards, from the ground: a walker shut out of a sealed
    1-wide passage follows no one (leaves 0), so closing the gap beats the fight; in the open a pillar still leaves
    walking away's share (follow_p), so the fight stands (combat__block_gap fought where it should have closed)."""

    def test_answers_over_the_table(self):
        from bonobo import field
        walker = [row("minecraft:zombie", 0, 8, vel=(0.0, 0.0, -4.0))]
        rows = [("a sealed passage, a walker 8 off: close the gap", field.Field(seal=2), ("reshape", ("between", 2))),
                ("open ground, the same walker: fight", field.Field(), ("fight", 0)),
                ("under a roof, no passage: fight", field.Field(bucket="underground"), ("fight", 0))]
        for name, ground, want in rows:
            with self.subTest(name):
                d = decide(walker, sword=2, protection=0.4, blocks=5, field=ground)
                self.assertEqual((d.kind, d.target), want)

    def test_a_walker_arrives_until_the_passage_is_sealed(self):
        from bonobo import field
        walker = row("minecraft:zombie", 0, 5, vel=(0.0, 0.0, -4.0))
        sealed = field.Field(seal=2)
        rows = [("nothing placed: it arrives", sealed, True),
                ("must fail: one block — a walker jumps it: it still arrives", sealed.with_block(), True),
                ("two blocks, feet and head: never", sealed.with_block().with_block(), False),
                ("two blocks in the open seal nothing", field.Field().with_block().with_block(), True)]
        for name, ground, arrives in rows:
            with self.subTest(name):
                self.assertEqual(threat.arrival(HERE, walker, ground=ground) != float("inf"), arrives)


class APillarUnderHits(unittest.TestCase):
    """Standing a block up is priced with the hits that knock us off it: under a walker's reach each hit resets the
    jump (escape__walker_open_blocks: priced 1.2 s, ran 4 s rising nothing)."""

    def test_the_pillar_over_the_table(self):
        from bonobo import field
        # (a zombie this far off) → the seconds of each pillar on offer {n: seconds}
        # one block up stops no walker (reaches_share: a step at melee_stop_blocks): never on offer
        rows = [("6 off: nothing hits while we build, from two up", 6, {2: 1.2, 3: 1.8, 4: 2.4}),
                ("must fail: 1.5 off, in reach: one block stops nothing, two are too many to live", 1.5, {}),
                ("12 off: nothing near enough to be worth it", 12, {})]
        for name, x, want in rows:
            with self.subTest(name):
                state = {"here": HERE, "hp": 20, "sword": 0, "protection": 0.0, "blocks": 5,
                         "hazards": [row("minecraft:zombie", x, 0)], "ids": [0], "field": field.Field()}
                got = {o.target[1]: round(o.seconds, 2) for o in threat.options(state)
                       if o.kind == "reshape" and o.target[0] == "under"}
                self.assertEqual(got, want)
        with self.subTest("must fail: under a zombie's hits a block is not the 0.6 s of a quiet one"):
            self.assertGreater(threat.block_under_hits_s(0.6, threat.knockback_rate(
                HERE, [row("minecraft:zombie", 1.5, 0)], 0.6)), 0.6 * 3)

    def test_knockback_rate(self):
        rows = [("a zombie in reach: one hit per attack_s", [row("minecraft:zombie", 1.5, 0)], 1 / 0.48),
                ("a zombie 10 off: none yet", [row("minecraft:zombie", 10, 0)], 0.0),
                ("a skeleton in reach: arrows do not knock a pillar down here", [row("minecraft:skeleton", 2, 0)], 0.0),
                ("two zombies in reach", [row("minecraft:zombie", 1.5, 0), row("minecraft:zombie", 0, 1.5)], 2 / 0.48)]
        for name, hazards, want in rows:
            with self.subTest(name):
                self.assertAlmostEqual(threat.knockback_rate(HERE, hazards, 0.6), want, places=3)


class AHoleDeepEnoughToStopThem(unittest.TestCase):
    """Digging down is priced by the depth that really puts us out of a walker's reach (melee_stop_blocks): a
    1-deep hole leaves every hit landing, so it is never an answer (combat__dig_in 01:38:51 died in one)."""

    def test_rows(self):
        from bonobo import field
        stop = int(threat.ENGAGE["melee_stop_blocks"])
        state = {"here": HERE, "hp": 14, "sword": 0, "protection": 0.0, "blocks": 0, "dig_ok": True,
                 "hazards": [row("minecraft:zombie", 3, 0)], "ids": [0], "field": field.Field()}
        opts = threat.options(state)
        downs = {o.target[1]: o for o in opts if o.kind == "reshape" and o.target[0] == "down"}
        press = next(o.leaves for o in opts if o.kind == "ignore")
        with self.subTest("must fail: a 1-deep hole is not on offer"):
            self.assertNotIn(1, downs)
        with self.subTest("the stopping depth is: only what waits for us up top follows (follow_p), no hit lands"):
            self.assertIn(stop, downs)
            self.assertAlmostEqual(downs[stop].leaves, round(press * float(threat.ENGAGE["follow_p"]), 3), places=2)


class ADelayIsNotASeal(unittest.TestCase):
    """Blocks in the way are priced by the time they buy: a seal leaves nothing, a block walked round leaves the
    pressure from its later arrival to the end of the work — neither nothing nor all of it."""

    def test_leaves_over_the_table(self):
        from bonobo import field
        walker = [row("minecraft:zombie", 0, 8, vel=(0.0, 0.0, -4.0))]
        # (ground, blocks between) → leaves as a share of the pressure now: exactly 0, or strictly between 0 and 1
        rows = [("a sealed passage, two blocks: nothing comes", field.Field(seal=2), 2, "zero"),
                ("a passage, one block (a walker jumps it): a delay", field.Field(seal=2), 1, "part"),
                ("must fail: four blocks on roofed open floor are no seal", field.Field(bucket="underground"), 4, "part"),
                ("one block on roofed open floor: a delay", field.Field(bucket="underground"), 1, "part")]
        for name, ground, n, want in rows:
            with self.subTest(name):
                after = ground
                for _ in range(n):
                    after = after.with_block()
                now = threat.pressure(HERE, walker, 0.0, ground=ground)
                left = threat.delayed_pressure(HERE, walker, 0.0, ground, after, 20.0)
                self.assertEqual("zero" if left == 0.0 else "part" if 0.0 < left < now else "all", want)

    def test_between_then_pillar(self):
        from bonobo import field
        walker = [row("minecraft:zombie", 0, 8, vel=(0.0, 0.0, -4.0))]
        rows = [("roofed open floor: the pillar, not a wall walked round", field.Field(bucket="underground"),
                 ("reshape", ("under", 2))),
                ("a sealed passage: the wall", field.Field(seal=2), ("reshape", ("between", 2)))]
        for name, ground, want in rows:
            with self.subTest(name):
                d = decide(walker, sword=0, blocks=128, field=ground, dig_ok=True)
                self.assertEqual((d.kind, d.target), want)


class WhoIsAfterUs(unittest.TestCase):
    """Neutral or hostile is read per mob from the reading and ours (threat.aggro), not from the type alone."""

    DAY, NIGHT = {"day": True, "gold_worn": False}, {"day": False, "gold_worn": False}
    GOLD = {"day": True, "gold_worn": True}

    def test_aggro_over_the_table(self):
        rows = [("a zombie, in daylight too", {"type": "minecraft:zombie"}, self.DAY, True),
                ("a spider in daylight: left alone", {"type": "minecraft:spider"}, self.DAY, False),
                ("a spider at night", {"type": "minecraft:spider"}, self.NIGHT, True),
                ("a spider we hit (angry), in daylight", {"type": "minecraft:spider", "angry": True}, self.DAY, True),
                ("an enderman not looked at", {"type": "minecraft:enderman"}, self.NIGHT, False),
                ("must fail: an enderman provoked is no neutral", {"type": "minecraft:enderman", "angry": True},
                 self.DAY, True),
                ("a piglin, no gold on", {"type": "minecraft:piglin"}, self.DAY, True),
                ("a piglin, gold worn", {"type": "minecraft:piglin"}, self.GOLD, False),
                ("must fail: a piglin provoked, gold or not", {"type": "minecraft:piglin", "angry": True}, self.GOLD,
                 True),
                ("a ghast", {"type": "minecraft:ghast"}, self.DAY, True)]
        for name, e, ctx, want in rows:
            with self.subTest(name):
                self.assertEqual(threat.aggro(e, ctx), want)

    def test_context_of(self):
        rows = [("overworld noon", {"dimension": "minecraft:overworld", "timeOfDay": 6000}, {}, True, False),
                ("overworld midnight", {"dimension": "minecraft:overworld", "timeOfDay": 18000}, {}, False, False),
                ("the nether: no sun", {"dimension": "minecraft:the_nether", "timeOfDay": 6000}, {}, False, False),
                ("gold on", {"dimension": "minecraft:overworld", "timeOfDay": 6000}, {"gold_worn": True}, True, True)]
        for name, state, kit, day, gold in rows:
            with self.subTest(name):
                self.assertEqual(threat.context_of(state, kit), {"day": day, "gold_worn": gold})

    def test_the_new_rows_are_threats(self):
        for kind in ("minecraft:ghast", "minecraft:blaze", "minecraft:wither_skeleton", "minecraft:witch",
                     "minecraft:phantom", "minecraft:drowned", "minecraft:spider", "minecraft:enderman",
                     "minecraft:piglin"):
            with self.subTest(kind):
                self.assertIn(kind, threat.MOBS)
        self.assertTrue(threat.MOBS["minecraft:ghast"].get("ranged"))


class DodgeThePredictedImpact(unittest.TestCase):
    """Evade lands out of the jar's predicted impact (its point and time), a spot reached before it lands."""

    def test_impacts_of(self):
        rows = [("a fireball predicted", [{"type": "minecraft:fireball", "impact": {"x": 1, "y": 64, "z": 0},
                                           "tti_ticks": 20}], [((1.0, 64.0, 0.0), 1.0, 6.0)]),
                ("no prediction on this jar", [{"type": "minecraft:fireball"}], []),
                ("a zombie's lunge as a list", [{"type": "minecraft:zombie", "impact": [0, 64, 0], "tti_ticks": 10}],
                 [((0.0, 64.0, 0.0), 0.5, 3.0)]),
                ("nothing near", [], [])]
        for name, near, want in rows:
            with self.subTest(name):
                self.assertEqual(threat.impacts_of(near), want)

    def test_dodge_spot(self):
        here = (0.0, 64.0, 0.0)
        near_spot, far_spot = (4.0, 64.0, 0.0), (20.0, 64.0, 0.0)
        # (impacts, candidates) → the spot
        rows = [("aimed at us, 1 s: the near spot out of 3", [(here, 1.0, 3.0)], [near_spot, far_spot], near_spot),
                ("must fail: the spot we stand on is in the path", [(here, 1.0, 3.0)], [here], None),
                ("too late: 0.2 s is not enough for 4 blocks", [(here, 0.2, 3.0)], [near_spot], None),
                ("landing on the near spot: the far one", [(near_spot, 10.0, 3.0)], [near_spot, far_spot], far_spot)]
        for name, impacts, cands, want in rows:
            with self.subTest(name):
                self.assertEqual(threat.dodge_spot(here, impacts, cands), want)


class EatingInAFight(unittest.TestCase):
    """Ordinary food heals by regen, later and only undisturbed; a golden apple heals now."""

    def test_eat_options_over_the_table(self):
        rows = [("must fail: mid-melee, 6 hp, bread: not an answer (regen needs quiet)", dict(hp=6, food_items=4, hunger=10), 2.0,
                 []),
                ("walled in, 6 hp, bread, hungry: eat", dict(hp=6, food_items=4, hunger=10), 0.1, [("eat", None, 6.0)]),
                ("mid-melee, 6 hp, a golden apple: eat it now", dict(hp=6, golden_apples=1), 2.0,
                 [("eat", "minecraft:golden_apple", 8.0)]),
                ("walled in, full hunger bar: cannot eat", dict(hp=6, food_items=4, hunger=20), 0.1, []),
                ("full health: nothing to heal", dict(hp=20, food_items=4, golden_apples=1, hunger=10), 0.1, [])]
        for name, st, press, want in rows:
            with self.subTest(name):
                got = threat.eat_options(st, st["hp"], press, 0.0)
                self.assertEqual([(o.kind, o.target, o.heals) for o in got], want)
