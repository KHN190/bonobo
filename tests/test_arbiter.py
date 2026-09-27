"""Offline tests for the single motion exit.

What matters is not which intent wins a contest but that the ordering is by time scale and cannot be negotiated,
that a fast layer does not wait for a slow layer's tick, and that during a fight nothing drives the body except the
chosen intent. A fight loses to several commanders long before it loses to a bad plan.

Every case is a row: a sequence of calls on one fresh `Motion`, what each returned, and what actually ran.
"""
import threading
import time
import unittest

from bonobo import api, arbiter


def intent(layer, mark, out, **kw):
    return arbiter.Intent(layer, lambda: out.append(mark), mark, **kw)


# (intents as (layer, reason, at, deadline_s), now) → the reason arbitrate picks, or None
ARBITRATE = [
    ("plan vs tactic: the faster", [("plan", "p", 0.0, None), ("tactic", "t", 0.0, None)], 0.0, "t"),
    ("tactic vs safety", [("tactic", "t", 0.0, None), ("safety", "s", 0.0, None)], 0.0, "s"),
    ("safety vs reflex", [("safety", "s", 0.0, None), ("reflex", "r", 0.0, None)], 0.0, "r"),
    ("the order of submission does not matter", [("reflex", "r", 0.0, None), ("plan", "p", 5.0, None)], 5.0, "r"),
    ("within a layer the newest reading", [("tactic", "old", 100.0, None), ("tactic", "new", 101.0, None)], 101.0,
     "new"),
    ("an expired intent is dropped, not run late", [("plan", "stale", 100.0, 1.0)], 102.0, None),
    ("an expired faster intent loses to a live slower one",
     [("reflex", "stale", 100.0, 1.0), ("plan", "live", 101.5, None)], 102.0, "live"),
    ("nothing to arbitrate", [], 0.0, None),
]


class Ordering(unittest.TestCase):
    def test_arbitrate_over_the_table(self):
        for name, rows, now, want in ARBITRATE:
            with self.subTest(name):
                out = []
                got = arbiter.arbitrate([intent(layer, r, out, at=at, deadline_s=d) for layer, r, at, d in rows],
                                        now=now)
                self.assertEqual(None if got is None else got.reason, want)

    # The combinations the brain bench no longer runs (it moves one condition at a time): (PLAN proposals as kinds
    # in the order proposed) → the kind that drives. What each cell of the old product decided, as one table.
    PLAN_COMBOS = [
        ("dusk and low food: the night's parts first", ["food stock", "night prep"], "night prep"),
        ("low food and a queued task: food first", ["queue", "food stock"], "food stock"),
        ("a broken tool under a held plan beats the queue", ["queue", "broken tool"], "broken tool"),
        ("underground at night: the queue before the night's stock", ["night stock", "queue"], "queue"),
        ("blocked path at dusk: night prep before bridge blocks", ["bridge stock", "night prep"], "night prep"),
        ("an unknown kind ranks after every known one", ["mystery", "idle"], "idle"),
        ("only waiting for day", ["wait for day"], "wait for day"),
    ]

    def test_plan_order_over_the_combinations(self):
        for name, kinds, want in self.PLAN_COMBOS:
            with self.subTest(name):
                out = []
                got = arbiter.arbitrate([intent("plan", k, out, at=0.0, kind=k) for k in kinds], now=0.0)
                self.assertEqual(got.kind, want)

    def test_an_unknown_layer_is_refused(self):
        for layer in ("urgent", "", "PLAN"):
            with self.subTest(layer=layer), self.assertRaises(ValueError):
                arbiter.Intent(layer, lambda: None)


# A sequence on one Motion: ("submit", layer, reason) / ("preempt", layer, reason, kw) / ("step",) →
# [what each call returned], and what ran, in order.
PAYING, STOPPED = (lambda: False), (lambda: True)
SEQUENCES = [
    ("a preemption drops slower pending intents",
     [("submit", "plan", "dig"), ("preempt", "safety", "breath", {}), ("step",)],
     [None, ("safety", "breath"), None], ["breath"]),
    ("a plan submitted after the preemption is fresh",
     [("preempt", "safety", "breath", {}), ("submit", "plan", "dig"), ("step",)],
     [("safety", "breath"), None, ("plan", "dig")], ["breath", "dig"]),
    ("a preemption keeps faster pending intents",
     [("submit", "reflex", "fireball"), ("preempt", "safety", "breath", {}), ("step",)],
     [None, ("safety", "breath"), ("reflex", "fireball")], ["breath", "fireball"]),
    ("only the winner of a step runs, and the queue clears",
     [("submit", "plan", "dig"), ("submit", "tactic", "reposition"), ("step",), ("step",)],
     [None, None, ("tactic", "reposition"), None], ["reposition"]),
    ("a held answer that still pays keeps the body against its own layer",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "tactic", "step aside", {"worth_s": 1e6})],
     [("tactic", "shield up"), "held"], ["shield up"]),
    ("a held answer that stopped paying hands over",
     [("preempt", "tactic", "shield up", {"release": STOPPED}), ("preempt", "tactic", "step aside", {"worth_s": 0.1})],
     [("tactic", "shield up"), ("tactic", "step aside")], ["shield up", "step aside"]),
    ("a faster layer is never asked anything",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "safety", "lava", {})],
     [("tactic", "shield up"), ("safety", "lava")], ["shield up", "lava"]),
    ("a slower layer is refused on the layer",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "plan", "mine", {"worth_s": 1e6})],
     [("tactic", "shield up"), "layer"], ["shield up"]),
]


class Sequences(unittest.TestCase):
    def test_sequences_over_the_table(self):
        for name, ops, want, ran_want in SEQUENCES:
            with self.subTest(name):
                ran, m, got = [], arbiter.Motion(), []
                for op in ops:
                    if op[0] == "submit":
                        m.submit(op[1], lambda r=op[2]: ran.append(r), op[2])
                        got.append(None)
                    elif op[0] == "preempt":
                        kw = {"now": 0.5, "seen_at": 0.5, "worth_s": 10.0, **op[3]}
                        taken, why = m.preempt(op[1], lambda r=op[2]: ran.append(r), op[2], **kw)
                        got.append(taken if taken is not None else why)
                    else:
                        got.append(m.step())
                self.assertEqual(got, want)
                self.assertEqual(ran, ran_want)


# (engaged?, who asks: None = outside any intent / "intent" = inside the running one / "thread" = a preemption on
# another thread) → owns?, violations recorded
OWNERSHIP = [("outside a fight, anyone", False, None, True, []),
             ("inside a fight, a stray caller is refused and counted", True, None, False, ["nav.go_to"]),
             ("inside a fight, the chosen intent while it runs", True, "intent", True, []),
             ("inside a fight, a preemption on its own thread", True, "thread", True, [])]


class Ownership(unittest.TestCase):
    def test_ownership_over_the_table(self):
        for name, engaged, asker, want, violations in OWNERSHIP:
            with self.subTest(name):
                m, seen = arbiter.Motion(), []
                if engaged:
                    m.engage()
                if asker is None:
                    seen.append(m.owns("nav.go_to"))
                elif asker == "intent":
                    m.submit("plan", lambda: seen.append(m.owns("nav.go_to")), "dig")
                    m.step()
                else:
                    t = threading.Thread(target=lambda: m.preempt("safety", lambda: seen.append(m.owns("nav.go_to")),
                                                                  "breath"))
                    t.start()
                    t.join()
                self.assertEqual(seen, [want])
                self.assertEqual([v[1] for v in m.violations], violations)
                m.disengage()
                self.assertTrue(m.owns("nav.go_to"), "after the fight everything is allowed again")

    # (a Motion) → does it watch the handover file: only the one real body; a test's Motion is its own world
    WATCH = [("the shared body", lambda: arbiter.BODY, True), ("a fresh Motion", arbiter.Motion, False),
             ("a Motion asked to watch", lambda: arbiter.Motion(watch_handover=True), True),
             ("a Motion told not to", lambda: arbiter.Motion(watch_handover=False), False)]

    def test_who_watches_the_handover(self):
        for name, make, want in self.WATCH:
            with self.subTest(name):
                body = make()
                self.assertEqual((type(body), body.watch_handover), (arbiter.Motion, want))


class LockDiscipline(unittest.TestCase):
    def test_a_long_preempting_action_does_not_block_submit(self):
        """perception preempts with a slow action; the fight thread must still be able to submit."""
        m = arbiter.Motion()
        started, release = threading.Event(), threading.Event()

        def slow():
            started.set()
            release.wait(2.0)

        t = threading.Thread(target=lambda: m.preempt("safety", slow, "slow"))
        t.start()
        started.wait(1.0)
        t0 = time.time()
        m.submit("plan", lambda: None, "dig")          # must not wait for `slow`
        blocked = time.time() - t0
        release.set()
        t.join()
        self.assertLess(blocked, 0.5, "submit blocked behind a running preemption: action ran under the lock")
        self.assertEqual([p.reason for p in m.pending], ["dig"])

    # (the layer that preempts) → the interrupt message it leaves: only safety and faster write it
    INTERRUPTS = [("reflex", "fireball", "fireball"), ("safety", "breath", "breath"), ("tactic", "fight", None),
                  ("plan", "mine", None)]

    def test_who_writes_the_interrupt_message(self):
        for layer, reason, want in self.INTERRUPTS:
            with self.subTest(layer):
                api.INTERRUPT = None
                arbiter.Motion().preempt(layer, lambda: None, reason)
                self.assertEqual(api.INTERRUPT, want)
        api.INTERRUPT = None


class APreemptionIsNotAnIntruder(unittest.TestCase):
    """When a fast layer takes the body, the slow layer's task really is replaced — by us. The waiting thread must
    read that as "go and re-plan", not as an outsider fighting over the player."""

    # (when our last preemption happened, the mod's message) → what the waiting thread raises
    ROWS = [("replaced by an outsider", 0.0, api.REPLACED, api.BodyContested),
            ("replaced by our own preemption since the task began", 105.0, api.REPLACED, api.CommitmentExpired),
            ("an older preemption is not this one", 90.0, api.REPLACED, api.BodyContested),
            ("the player always wins", 105.0, "released by player", api.PlayerTookControl)]

    def test_the_release_over_the_table(self):
        for name, preempted_at, message, raised in self.ROWS:
            with self.subTest(name):
                arbiter.BODY.preempted_at, arbiter.BODY.preempted_by = preempted_at, None
                with self.assertRaises(raised):
                    api._raise_if_released([{"message": message}], since=100.0)
        arbiter.BODY.preempted_at, arbiter.BODY.preempted_by = 0.0, None



# -- the arbiter over the brain's real inputs: reflexes.due (the MAINTAIN rows) and PLAN kinds --------------------
import itertools                                    # noqa: E402

from bonobo import reflexes, retry as retry_mod, skills   # noqa: E402

# A calm round: nothing fires. Every key a reflexes.TABLE trigger reads.
CALM = {"died_recently": False, "food": 20, "edible": True, "swimming": False, "nether_bad": False, "night": False,
        "enclosed": False, "overworld": True, "bed_works": True, "bed_carried": False, "bed_near": False,
        "shelter_ready": False, "job_ready": False, "machine_ready": False, "used_slots": 5, "blocked": False,
        "building": 64, "stuck": False}
HUNGRY = {"food": reflexes.EAT_BELOW - 1}
IN_WATER = {"swimming": True}
BAG_FULL = {"used_slots": reflexes.BAG_FULL}


def situation(view=(), fight=False, hazard=False, plan=(), ready=lambda name: True, at=0.0):
    """The round's intents as the brain submits them: a hazard (SAFETY), a fight (TACTIC), every MAINTAIN row
    that fires (reflexes.due, its table place as seq), and the PLAN proposals by kind."""
    v = dict(CALM, **dict(view))
    out = []
    mk = lambda layer, reason, **kw: arbiter.Intent(layer, lambda: None, reason, at=at, **kw)   # noqa: E731
    if hazard:
        out.append(mk("safety", "hazard"))
    if fight:
        out.append(mk("tactic", "fight"))
    out += [mk("maintain", name, kind=name, seq=seq) for seq, name in reflexes.due(v, ready)]
    out += [mk("plan", kind, kind=kind) for kind in plan]
    return out


def chosen(intents, now=0.0):
    got = arbiter.arbitrate(intents, now=now)
    return None if got is None else got.reason


class Invariants(unittest.TestCase):
    """Generated over every layer and kind, not written pair by pair; the expectations are relations (a smaller time
    scale is faster), never a copy of the order being tested."""
    LAYERS = sorted(arbiter.SCALES)
    KINDS = list(arbiter.PLAN_ORDER)

    def test_any_submission_order_gives_the_same_choice(self):
        # (situation) → one choice whatever the order the layers submitted in
        rows = [("every layer", [("safety", None), ("tactic", None), ("maintain", "eat"), ("plan", "queue")]),
                ("two plans and a reflex", [("plan", "idle"), ("plan", "food stock"), ("maintain", "unstuck")]),
                ("plans only", [("plan", k) for k in ("queue", "night prep", "idle", "wait for day")]),
                ("one intent", [("plan", "queue")])]
        for name, spec in rows:
            with self.subTest(name):
                intents = [arbiter.Intent(layer, lambda: None, f"{layer}:{k}", at=0.0, kind=k) for layer, k in spec]
                picks = {chosen(list(p)) for p in itertools.permutations(intents)}
                self.assertEqual(len(picks), 1)

    def test_every_pair_of_layers_the_faster_wins(self):
        for a, b in itertools.permutations(self.LAYERS, 2):
            with self.subTest(f"{a} vs {b}"):
                want = a if arbiter.SCALES[a] < arbiter.SCALES[b] else b
                self.assertEqual(chosen([arbiter.Intent(a, lambda: None, a, at=0.0, kind="queue"),
                                         arbiter.Intent(b, lambda: None, b, at=0.0, kind="queue")]), want)

    def test_a_tie_in_layer_and_kind_is_settled_by_place_then_newest(self):
        # (seq, at) of two same-layer same-kind intents → which wins
        rows = [("the earlier place in line", ((1, 0.0), (0, 0.0)), 1),
                ("same place: the newer reading", ((0, 1.0), (0, 2.0)), 1),
                ("place beats newness", ((0, 1.0), (1, 5.0)), 0),
                ("identical: the first submitted, every time", ((0, 1.0), (0, 1.0)), 0)]
        for name, ((s0, a0), (s1, a1)), want in rows:
            with self.subTest(name):
                intents = [arbiter.Intent("plan", lambda: None, str(i), at=a, kind="queue", seq=sq)
                           for i, (sq, a) in enumerate(((s0, a0), (s1, a1)))]
                self.assertEqual([chosen(intents, now=10.0) for _ in range(3)], [str(want)] * 3)

    def test_an_expired_intent_never_runs(self):
        for layer in self.LAYERS:
            with self.subTest(layer):
                stale = arbiter.Intent(layer, lambda: None, "stale", at=0.0, deadline_s=1.0, kind="queue")
                live = arbiter.Intent("plan", lambda: None, "live", at=0.0, kind="idle")
                self.assertEqual((chosen([stale], now=2.0), chosen([stale, live], now=2.0), chosen([stale], now=0.5)),
                                 (None, "live", "stale"))

    def test_an_unknown_kind_ranks_after_every_known_one(self):
        for kind in self.KINDS:
            with self.subTest(kind):
                self.assertEqual(chosen([arbiter.Intent("plan", lambda: None, "?", at=0.0, kind="no such kind"),
                                         arbiter.Intent("plan", lambda: None, kind, at=0.0, kind=kind)]), kind)


class Crowded(unittest.TestCase):
    # (situation: reflex view changes, a fight, a hazard, PLAN kinds) → what drives the body
    ROWS = [("hungry, in water, dusk, fighting: the fight", {**HUNGRY, **IN_WATER}, True, False, ["night prep"], "fight"),
            ("hungry, in water, dusk, a hazard too: the hazard", {**HUNGRY, **IN_WATER}, True, True, ["night prep"],
             "hazard"),
            ("bag full, hungry, a task: eat before emptying, both before the task", {**BAG_FULL, **HUNGRY}, False,
             False, ["queue"], "eat"),
            ("night, no pickaxe, in water: out of the water before any plan", {**IN_WATER, "night": True}, False,
             False, ["broken tool", "night stock"], "reach land"),
            ("bag full, a task, no food on hand: empty the bag", {**BAG_FULL, **HUNGRY, "edible": False}, False,
             False, ["queue", "food stock"], "empty the bag"),
            ("nothing fires, plans only: the plan's order", {}, False, False, ["idle", "food stock", "queue"],
             "food stock"),
            ("calm and nothing proposed: nothing drives", {}, False, False, [], None)]

    def test_crowded_rounds(self):
        for name, view, fight, hazard, plan, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(chosen(situation(view, fight, hazard, plan)), want)


class Flicker(unittest.TestCase):
    """What stops a shore reading from toggling reach land: `swimming` is in water AND off the ground, so standing in
    a shore block's water is not swimming. The trigger's boundary, and hunger's."""

    def test_in_the_water_boundary(self):
        # (inWater, onGround) → swimming (reach land fires)
        rows = [("treading water", True, False, True), ("standing in shore water", True, True, False),
                ("on land", False, True, False), ("falling through air", False, False, False)]
        for name, wet, ground, want in rows:
            with self.subTest(name):
                swim = skills.swimming({"inWater": wet, "onGround": ground})
                self.assertEqual((swim, "reach land" in [n for _s, n in reflexes.due(dict(CALM, swimming=swim))]),
                                 (want, want))

    def test_hunger_boundary(self):
        # (food, something edible) → eat fires
        rows = [("one below the line", reflexes.EAT_BELOW - 1, True, True), ("at the line", reflexes.EAT_BELOW, True, False),
                ("starving, nothing to eat", 0, False, False), ("full", 20, True, False)]
        for name, food, edible, want in rows:
            with self.subTest(name):
                self.assertEqual("eat" in [n for _s, n in reflexes.due(dict(CALM, food=food, edible=edible))], want)


class NoStarvation(unittest.TestCase):
    """Rounds simulated with the real retry policy: a reflex that keeps firing and keeps failing cools, and what is
    below it gets the body within a bounded number of rounds."""
    # (situation, view, plan kinds, cause the fired reflex fails with) → the first round (≤ N) a lower choice drives
    ROWS = [("a bag that cannot be emptied (nowhere to put it)", BAG_FULL, ["queue"], "unavailable", "queue"),
            ("stuck and never freed", {"stuck": True}, ["queue"], "stuck", "queue"),
            ("a blocked path that cannot be bridged", {"blocked": True}, ["idle"], "nav", "idle"),
            ("hungry and eating fails, bag full below it", {**HUNGRY, **BAG_FULL}, ["queue"], "game", "empty the bag")]
    ROUNDS = 5

    def test_a_failing_reflex_lets_lower_work_through(self):
        for name, view, plan, cause, want in self.ROWS:
            with self.subTest(name):
                r = retry_mod.Retry()
                picks = []
                for n in range(self.ROUNDS):
                    now = float(n)
                    pick = chosen(situation(view, plan=plan, ready=lambda task: r.ready(task, now, "here"), at=now),
                                  now=now)
                    picks.append(pick)
                    if pick == picks[0]:
                        r.failed(pick, cause, "fails every time", now, "here")
                self.assertEqual(picks[1], want)

    def test_control_a_reflex_that_is_not_failing_keeps_the_body(self):
        r = retry_mod.Retry()
        picks = [chosen(situation(BAG_FULL, plan=["queue"], ready=lambda t: r.ready(t, float(n), "here"), at=float(n)),
                        now=float(n)) for n in range(4)]
        self.assertEqual(picks, ["empty the bag"] * 4)

if __name__ == "__main__":
    unittest.main()
