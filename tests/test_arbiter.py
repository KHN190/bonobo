"""Offline tests for the single motion exit.

What matters is not which intent wins a contest but that the ordering is by time scale and cannot be negotiated,
that a fast layer does not wait for a slow layer's tick, and that during a fight nothing drives the body except the
chosen intent. A fight loses to several commanders long before it loses to a bad plan.

Every case is a row: a sequence of calls on one fresh `Motion`, what each returned, and what actually ran.
"""
import threading
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
    ("must fail: an expired intent is dropped, not run late", [("plan", "stale", 100.0, 1.0)], 102.0, None),
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
        ("must fail: an unknown kind ranked first — an unknown kind ranks after every known one", ["mystery", "night stock"], "night stock"),
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


# A sequence of preemptions on one Motion: ("preempt", layer, reason, kw) →
# [what each call returned], and what ran, in order.
PAYING, STOPPED = (lambda: False), (lambda: True)
SEQUENCES = [
    ("a held answer that still pays keeps the body against its own layer",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "tactic", "step aside", {"worth_s": 1e6})],
     [("tactic", "shield up"), "held"], ["shield up"]),
    ("a held answer that stopped paying hands over",
     [("preempt", "tactic", "shield up", {"release": STOPPED}), ("preempt", "tactic", "step aside", {"worth_s": 0.1})],
     [("tactic", "shield up"), ("tactic", "step aside")], ["shield up", "step aside"]),
    ("a faster layer is never asked anything",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "safety", "lava", {})],
     [("tactic", "shield up"), ("safety", "lava")], ["shield up", "lava"]),
    ("must fail: a slower layer is refused on the layer",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "plan", "mine", {"worth_s": 1e6})],
     [("tactic", "shield up"), "layer"], ["shield up"]),
]


class Sequences(unittest.TestCase):
    def test_sequences_over_the_table(self):
        for name, ops, want, ran_want in SEQUENCES:
            with self.subTest(name):
                ran, m, got = [], arbiter.Motion(), []
                for op in ops:
                    kw = {"now": 0.5, "seen_at": 0.5, "worth_s": 10.0, **op[3]}
                    taken, why = m.preempt(op[1], lambda r=op[2]: ran.append(r), op[2], **kw)
                    got.append(taken if taken is not None else why)
                self.assertEqual(got, want)
                self.assertEqual(ran, ran_want)


# (engaged?, who asks: None = outside any intent / "intent" = inside the running one / "thread" = a preemption on
# another thread) → owns?, violations recorded
OWNERSHIP = [("outside a fight, anyone", False, None, True, []),
             ("must fail: inside a fight, a stray caller is refused and counted", True, None, False, ["nav.go_to"]),
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
                    m.drive("plan", lambda: seen.append(m.owns("nav.go_to")), "dig")
                else:
                    t = threading.Thread(target=lambda: m.preempt("safety", lambda: seen.append(m.owns("nav.go_to")),
                                                                  "breath"))
                    t.start()
                    t.join()
                self.assertEqual(seen, [want])
                self.assertEqual([v[1] for v in m.violations], violations)
                m.disengage()
                self.assertTrue(m.owns("nav.go_to"), "after the fight everything is allowed again")


class LockDiscipline(unittest.TestCase):

    # (the layer that preempts) → the interrupt message it leaves: only safety and faster write it
    INTERRUPTS = [("reflex", "fireball", "fireball"), ("safety", "breath", "breath"), ("tactic", "fight", None),  # must fail: tactic and plan leave no message
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
            ("must fail: an older preemption is not this one", 90.0, api.REPLACED, api.BodyContested),
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
CALM = {"died_recently": False, "food": 20, "meal": False, "swimming": False, "nether_bad": False, "night": False,
        "enclosed": False, "overworld": True, "bed_works": True, "bed_carried": False, "bed_near": False,
        "shelter_ready": False, "job_ready": False, "machine_ready": False, "used_slots": 5, "blocked": False,
        "building": 64, "stuck": False, "feet": (0, 64, 0), "on_land_s": 5.0}
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
        with self.subTest("must fail: a chooser that takes the first submitted is caught by the same check"):
            intents = [arbiter.Intent(layer, lambda: None, f"{layer}:{k}", at=0.0, kind=k)
                       for layer, k in rows[1][1]]
            self.assertGreater(len({list(p)[0].reason for p in itertools.permutations(intents)}), 1)

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
                ("must fail: a tie read as the newest — identical: the first submitted, every time", ((0, 1.0), (0, 1.0)), 0)]
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
        for kind in [k for k in self.KINDS if k not in arbiter.LAST_RESORT]:    # last-resort kinds: `gate`'s table
            with self.subTest(kind):
                self.assertEqual(chosen([arbiter.Intent("plan", lambda: None, "?", at=0.0, kind="no such kind"),
                                         arbiter.Intent("plan", lambda: None, kind, at=0.0, kind=kind)]), kind)



class OnlyUsefulProposals(unittest.TestCase):
    """arbiter.gate: what is cooling is not offered (met and unplannable needs never become intents: judged once,
    where proposed); a waiting kind only when nothing else is; the gate filters and never reorders the layers."""
    P = lambda k, key=None, layer="plan": arbiter.Intent(layer, lambda: None, k, at=0.0, kind=k, key=key or k)  # noqa: E731
    # (situation, intents, facts) → the reason arbitrate picks
    ROWS = [("a cooling need is dropped", [P("broken tool"), P("queue")], {"cooling": {"broken tool"}}, "queue"),
            ("must fail: a fact the gate does not judge (met) drops nothing", [P("food stock"), P("queue")],
             {"met": {"food stock"}}, "food stock"),
            ("work to do: waiting for day is not offered", [P("wait for day"), P("night stock")], {}, "night stock"),
            ("idle stocking is not offered next to a task", [P("idle"), P("queue")], {}, "queue"),
            ("only waiting left: kept", [P("wait for day"), P("food stock")], {"cooling": {"food stock"}},
             "wait for day"),
            ("must fail: everything dropped: nothing", [P("queue")], {"cooling": {"queue"}}, None),
            ("the gate does not reorder layers: the faster still wins", [P("queue"), P("eat", layer="maintain")], {},
             "eat")]

    def test_gate(self):
        for name, intents, facts, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(chosen_with(intents, facts), want)


class NightUnderCover(unittest.TestCase):
    """arbiter.viable with the surface closed (night in the Overworld): speedrun style — the night is worked under
    cover (a tunnel, crafting, smelting), a surface walk (a tree, an animal, stocking out in the open) waits for
    morning, and waiting for day is only the last resort."""
    P = staticmethod(lambda k, key, surface=False: arbiter.Intent("plan", lambda: None, key, at=0.0, kind=k,
                                                                  key=key, surface=surface))

    def test_rows(self):
        P = self.P
        logs = P("queue", "gather logs", surface=True)
        tunnel, wait = P("night stock", "strip mine"), P("wait for day", "wait for day")
        smelt = P("queue", "smelt iron")
        # (situation, intents, surface closed) → the one chosen
        rows = [("night: the tunnel, not the tree", [logs, tunnel, wait], True, "strip mine"),
                ("night: indoor work in the queue goes on", [smelt, tunnel, wait], True, "smelt iron"),
                ("day: unchanged, the queue's tree", [logs, tunnel], False, "gather logs"),
                ("night, nothing under cover to do: wait for day", [logs, wait], True, "wait for day"),
                ("must fail: a surface walk alone at night is not offered", [logs], True, None)]
        for name, intents, closed, want in rows:
            with self.subTest(name):
                self.assertEqual(chosen_with(intents, {"surface_closed": closed}), want)

    def test_on_surface(self):
        rows = [("mine", False), ("craft", False), ("smelt", False), ("gather", True), ("hunt", True),
                ("seek", True)]
        for kind, want in rows:
            with self.subTest(kind):
                self.assertIs(arbiter.on_surface(kind), want)


class GroupsAskedInTurn(unittest.TestCase):
    """arbiter.first_live: the next group is asked only when every earlier one gated to nothing — plan_without_events
    idled every round with the queue never asked, its needs all cooling."""
    P = OnlyUsefulProposals.P
    # (situation, groups by name, cooling keys) → (what arbitrate picks, the groups asked)
    ROWS = [("every need cooling: the queue is asked and picked", [[P("broken tool")], [P("queue")]],
             {"broken tool"}, ("queue", ["g0", "g1"])),
            ("a need that can run: it, the queue not asked", [[P("broken tool")], [P("queue")]], set(),
             ("broken tool", ["g0"])),
            ("must fail: every group empty: nothing", [[], [], []], set(), (None, ["g0", "g1", "g2"])),
            ("only waiting left at the end: kept", [[P("food stock")], [P("wait for day")]], {"food stock"},
             ("wait for day", ["g0", "g1"])),
            ("an empty first group (no fight): the next asked", [[], [P("eat", layer="maintain")], [P("queue")]],
             set(), ("eat", ["g0", "g1"]))]

    def test_first_live(self):
        for name, groups, cooling, want in self.ROWS:
            with self.subTest(name):
                asked = []

                def ask(i, g):
                    return lambda: (asked.append(f"g{i}"), g)[1]
                live, facts = arbiter.first_live([ask(i, g) for i, g in enumerate(groups)],
                                                 lambda intents: {"cooling": {i.key for i in intents} & cooling})
                self.assertEqual((chosen_with(live, facts), asked), want)


def chosen_with(intents, facts):
    got = arbiter.arbitrate(intents, now=0.0, facts=facts)
    return None if got is None else got.reason

class Crowded(unittest.TestCase):
    # (situation: reflex view changes, a fight, a hazard, PLAN kinds) → what drives the body
    ROWS = [("hungry, in water, dusk, fighting: the fight", {**HUNGRY, **IN_WATER}, True, False, ["night prep"], "fight"),
            ("hungry, in water, dusk, a hazard too: the hazard", {**HUNGRY, **IN_WATER}, True, True, ["night prep"],
             "hazard"),
            ("bag full, hungry, a task: eat before emptying, both before the task", {**BAG_FULL, **HUNGRY}, False,
             False, ["queue"], "eat"),
            ("night, no pickaxe, in water: out of the water before any plan", {**IN_WATER, "night": True}, False,
             False, ["broken tool", "night stock"], "reach land"),
            ("bag full, a task, no food on hand: empty the bag", {**BAG_FULL, **HUNGRY, "meal": None}, False,
             False, ["queue", "food stock"], "empty the bag"),
            ("nothing fires, plans only: the plan's order", {}, False, False, ["idle", "food stock", "queue"],
             "food stock"),
            ("must fail: calm and nothing proposed: nothing drives", {}, False, False, [], None)]

    def test_crowded_rounds(self):
        for name, view, fight, hazard, plan, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(chosen(situation(view, fight, hazard, plan)), want)


class Flicker(unittest.TestCase):
    """What stops a shore reading from toggling reach land: `swimming` is in water AND off the ground, so standing in
    a shore block's water is not swimming. The trigger's boundary, and hunger's."""

    def test_in_the_water_boundary(self):
        # (inWater, onGround, air) → swimming (reach land fires)
        rows = [("treading water", True, False, 300, True), ("standing in shore water", True, True, 300, False),
                ("on the bottom of a flooded shaft, head under", True, True, 120, True),
                ("edge: one tick of breath gone, standing in water", True, True, 299, True),
                ("must fail: on land, out of breath from before", False, True, 50, False),
                ("on land", False, True, 300, False), ("falling through air", False, False, 300, False)]
        for name, wet, ground, air, want in rows:
            with self.subTest(name):
                swim = skills.swimming({"inWater": wet, "onGround": ground, "air": air})
                self.assertEqual((swim, "reach land" in [n for _s, n in reflexes.due(dict(CALM, swimming=swim))]),
                                 (want, want))

    def test_hurt_eats_to_regenerate(self):
        # (hp, food) → the eat row fires (something edible, cooked)
        rows = [("hurt, food 15: eat (no regen below 18)", 10, 15, True),
                ("must fail: whole, food 15: not hungry enough", 20, 15, False),
                ("hurt, food 19: eat to the full bar (fast regen)", 10, 19, True),
                ("hurt, food full: nothing to eat for", 10, 20, False),
                ("hungry and whole: the hunger row", 20, reflexes.EAT_BELOW - 1, True)]
        for name, hp, food, want in rows:
            with self.subTest(name):
                self.assertEqual("eat" in [n for _s, n in reflexes.due(dict(CALM, food=food, hp=hp, meal=False))], want)

    def test_hunger_boundary(self):
        # (food, something edible) → eat fires
        rows = [("one below the line", reflexes.EAT_BELOW - 1, True, True), ("at the line", reflexes.EAT_BELOW, True, False),
                ("must fail: starving, nothing to eat", 0, False, False), ("full", 20, True, False)]
        for name, food, edible, want in rows:
            with self.subTest(name):
                meal = False if edible else None
                self.assertEqual("eat" in [n for _s, n in reflexes.due(dict(CALM, food=food, meal=meal))], want)


class Hysteresis(unittest.TestCase):
    """reach land: in on swimming, out only after reflexes.LAND_EXIT_S on something that is not water; every other
    row exits with its trigger. Rounds fed through reflexes.due / latched, as Maintain.proposals does."""
    OUT = reflexes.LAND_EXIT_S
    # (situation, rounds of (swimming, on_land_s), row) → whether the row fires in each round
    ROWS = [("deep water: in", [(True, 0.0)], "reach land", [True]),
            ("shore flicker: swimming off for a tick, still on the way out", [(True, 0.0), (False, 0.1), (True, 0.0),
                                                                                (False, 0.2)], "reach land",
             [True, True, True, True]),
            ("really ashore: out once the exit holds", [(True, 0.0), (False, 0.5), (False, OUT)], "reach land",
             [True, True, False]),
            ("must fail: never in the water: never in", [(False, 0.0), (False, 0.0)], "reach land", [False, False]),
            ("a row without an exit leaves with its trigger", [(True, 0.0), (False, 0.0)], "eat", [True, False])]

    def test_rounds(self):
        for name, rounds, row, want in self.ROWS:
            with self.subTest(name):
                active, got = frozenset(), []
                for n, (swim, land) in enumerate(rounds):
                    food = reflexes.EAT_BELOW - 1 if (row == "eat" and n == 0) else 20
                    v = dict(CALM, swimming=swim, on_land_s=land, food=food)
                    fired = [x for _s, x in reflexes.due(v, active=active)]
                    active = reflexes.latched(fired, v)
                    got.append(row in fired)
                self.assertEqual(got, want)


def simulate(view_of, plan, cause_for, rounds):
    """Rounds of the MAINTAIN layer over the real retry policy, as Maintain.proposals runs them: what fired, the
    last run judged (`stalled`), cooling rows skipped, the arbiter's pick. `view_of(n)` is round n's view;
    `cause_for(name)` is how a run fails (None: it returns fine). Returns the picks."""
    r, active, last, picks = retry_mod.Retry(), frozenset(), None, []
    for n in range(rounds):
        now, v = float(n) * 10, view_of(n)
        fired = [x for _s, x in reflexes.due(v, active=active)]
        if last is not None:
            name, before = last
            last = None
            if reflexes.stalled(name in fired, before, reflexes.progress_of(name, v)):
                r.failed(name, reflexes.NO_PROGRESS, "changed nothing", now, "here")
        active = reflexes.latched(fired, v)
        intents = [arbiter.Intent("maintain", lambda: None, x, at=now, kind=x, seq=reflexes.NAMES.index(x))
                   for x in fired if r.ready(x, now, "here")]
        intents += [arbiter.Intent("plan", lambda: None, k, at=now, kind=k) for k in plan]
        pick = chosen(intents, now=now)
        picks.append(pick)
        if pick in reflexes.NAMES:
            if cause_for(pick):
                r.failed(pick, cause_for(pick), "fails", now, "here")
            else:
                r.succeeded(pick)
                last = (pick, reflexes.progress_of(pick, v))
    return picks


class NoProgress(unittest.TestCase):
    """A reflex that runs, fires again and moved nothing fails (retry's cooling), so what is below it gets the body;
    one that moves its reading keeps it; one whose trigger clears is done; a cooled one comes back."""
    FULL = dict(CALM, **BAG_FULL)

    def test_rounds(self):
        backstop = retry_mod.BACKSTOP[reflexes.NO_PROGRESS]
        # (situation, view of round n, plan kinds, rounds, picks wanted — None: skip that round's check)
        rows = [("must fail: a bag that will not empty: cools, the task gets the body", lambda n: self.FULL, ["queue"], 3,
                 ["empty the bag", "queue", "queue"]),
                ("a bag that empties a little each run: keeps going", lambda n: dict(self.FULL, used_slots=36 - n),
                 ["queue"], 3, ["empty the bag"] * 3),
                ("emptied: the trigger clears, the task next", lambda n: self.FULL if n == 0 else CALM, ["queue"], 2,
                 ["empty the bag", "queue"]),
                ("cooled and the cooling over: tried again", lambda n: self.FULL, ["queue"],
                 int(backstop // 10) + 2, None),
                ("stuck in place and never moving: the plan below gets through", lambda n: dict(CALM, stuck=True),
                 ["idle"], 3, ["unstuck", "idle", "idle"])]
        for name, view_of, plan, rounds, want in rows:
            with self.subTest(name):
                picks = simulate(view_of, plan, lambda x: None, rounds)
                if want is not None:
                    self.assertEqual(picks, want)
                else:
                    self.assertEqual((picks[0], picks[1], picks[-1]), ("empty the bag", "queue", "empty the bag"))


class NoStarvation(unittest.TestCase):
    """Whatever keeps firing above it, a lower row or the plan is eventually chosen within ROUNDS."""
    ROUNDS = 6
    # (situation, view, plan kinds, how the fired reflex's run ends: a cause, or None for fine with no progress)
    ROWS = [("a bag that cannot be emptied: fails", BAG_FULL, ["queue"], "unavailable"),
            ("a bag that 'empties' and stays full", BAG_FULL, ["queue"], None),
            ("must fail: stuck and never freed", {"stuck": True}, ["queue"], None),
            ("a blocked path that cannot be bridged", {"blocked": True}, ["idle"], "nav"),
            ("hungry and eating changes nothing, bag full below it", {**HUNGRY, **BAG_FULL}, ["queue"], None)]

    def test_something_lower_gets_the_body(self):
        for name, view, plan, cause in self.ROWS:
            with self.subTest(name):
                picks = simulate(lambda n: dict(CALM, **view), plan, lambda x: cause, self.ROUNDS)
                self.assertNotEqual({p for p in picks if p != picks[0]}, set())

    def test_control_a_reflex_making_progress_keeps_the_body(self):
        picks = simulate(lambda n: dict(CALM, used_slots=40 - n), ["queue"], lambda x: None, 4)
        self.assertEqual(picks, ["empty the bag"] * 4)

if __name__ == "__main__":
    unittest.main()


class WaitsCounted(unittest.TestCase):
    """arbiter.waits: only rounds that did nothing count — idle stocking is work (six bench rows failed on it)."""

    def test_over_the_table(self):
        import collections
        rows = [("must fail: an idle stocking pick: not a wait", ["idle"], 0),
                ("a wait-for-day pick: a wait", ["wait for day"], 1),
                ("a plain wait: a wait", ["wait"], 1),
                ("the queue five times and idle once: none", ["queue"] * 5 + ["idle"], 0),
                ("mixed: the two waits only", ["queue", "wait for day", "idle", "wait"], 2)]
        for name, kinds, want in rows:
            with self.subTest(name):
                picks = collections.Counter()
                for k in kinds:
                    arbiter.note_pick(picks, arbiter.Intent("plan", lambda: None, k, at=0.0, kind=k))
                self.assertEqual(arbiter.waits(picks), want)


def tearDownModule():
    """A preemption here sets api.INTERRUPT (arbiter.Motion.preempt): it must not leak into the next module's skills
    (test_skill_contract's runner raised Interrupted("lava") when run after this one)."""
    api.INTERRUPT = None
