"""The decomposition contract: what the one planner (planner.plan_needs over the skills' producing tables) may
assume about the knowledge it plans from, and what every plan it returns must do when replayed.

Four properties, each a list of violations that must be empty (or exactly the gaps named below):
  1. every way and recipe declares what it does (a step that makes the token; a pattern and an output);
  2. closure: everything a way consumes, and every need of its call, is made by some way, or is a bag-only variant
     of a group that is made;
  3. replay: every goal, from an empty bag, replayed by bag arithmetic alone — inputs held before each step, the
     goal held after the last, within a step bound;
  4. a source removed makes the goal unplannable — an answer, not a hang and not a partial plan.
"""
import os
import sys
import unittest
from collections import Counter
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill module registers)
from bonobo import data, knowledge  # noqa: E402
from bonobo.data import GROUPS, TOOL_MATERIAL_FOR_TIER, mid  # noqa: E402
from bonobo.planner import NullCost, Unplannable, plan_needs, way  # noqa: E402
from tests.world import bag, inventory  # noqa: E402

STEP_BOUND = 30          # fixture: planner steps from an empty bag, at most (blaze powder: 23, the Nether trip in it)


def plan(needs, cost=None, items=()):
    """The one planner from a bag of `items` (empty by default), offline."""
    return plan_needs(bag(inventory(*items)), needs, cost or NullCost())


def source(token):
    """The first way a token is made (knowledge.sources), or None."""
    got = knowledge.sources(token)
    return got[0][1] if got else None


def ways():
    """[(token, the step, its inputs)] of every way the producing tables give: what the planner chooses among."""
    out = []
    for g in knowledge.producers():
        for token in g.keys():
            for made, src in knowledge.sources(token):
                got = way(src, made, 1)
                if got is not None:
                    out.append((made, got[0], [t for t, _n in got[1]]))
    return out


# ---------------------------------------------------------------------------------------------- the checks (pure)
def undeclared(made, recipes):
    """Ways whose step makes another token than the one it is for, recipes with an empty pattern or no output."""
    out = [f"{t}: {s.kind} {s.token}" for t, s, _ins in made if s.token != t and s.kind not in ("take", "barter")]
    out += [name for name, (pattern, n) in recipes.items() if not any(pattern) or n < 1]
    return out


def variant_of_made_group(dim, made):
    """A variant (minecraft:oak_log) whose group (log) some way makes: held stock only, by design."""
    return any(g in made and dim in (mid(m) for m in members) for g, members in GROUPS.items())


def unclosed(made_ways):
    """Inputs, and the needs of each way's call, that no way makes and no made group covers."""
    made = {t for t, _s, _ins in made_ways} | {g for g, ms in GROUPS.items()
                                               if any(mid(m) in {t for t, _s, _i in made_ways} for m in ms)}
    used = {i for _t, _s, ins in made_ways for i in ins}
    used |= {d for _t, s, _ins in made_ways for d in knowledge.step_call(s) if not d.startswith("tool:")}
    return {d for d in used - made if not variant_of_made_group(d, made) and mid(d) not in made}


def ungrounded(tokens, source):
    """Tokens `source` cannot ground in a base (mine, hunt, gather, take, fill of a grounded item): a broken chain
    or a cycle with no base entry. Least fixed point, so a cycle never grounds itself."""
    def inputs(src):
        if src[0] == "craft":
            return {t for t in src[1] if t}
        if src[0] in ("smelt", "fill"):
            return {src[1]}
        return set()
    grounded, changed = set(), True
    while changed:
        changed = False
        for t in tokens:
            src = source(t)
            if t not in grounded and src is not None and inputs(src) <= grounded and \
                    (src[0] not in ("craft", "smelt", "fill") or inputs(src)):
                grounded.add(t)
                changed = True
    return set(tokens) - grounded


def all_tokens(source, roots):
    """Every token reachable from `roots` through `source`'s inputs."""
    seen, todo = set(), list(roots)
    while todo:
        t = todo.pop()
        if t in seen:
            continue
        seen.add(t)
        src = source(t)
        if src and src[0] == "craft":
            todo += [x for x in src[1] if x]
        elif src and src[0] in ("smelt", "fill"):
            todo.append(src[1])
    return seen


TIER = {m: t for t, m in TOOL_MATERIAL_FOR_TIER.items()}


def replay_steps(steps, goal):
    """Bag arithmetic over planner.Steps: [(step, missing)] where something was not held, and the end bag."""
    bag, tools, bad = Counter(), [], []
    for st in steps:
        need = dict(st.detail.get("inputs", {}))
        src = next((s for _m, s in knowledge.sources(st.token) if s[0] == "craft"), None)
        if st.kind == "craft" and src and len(src[1]) == 9:
            need.setdefault("minecraft:crafting_table", 0)
            if bag["minecraft:crafting_table"] < 1:
                bad.append((str(st), "minecraft:crafting_table"))
        if st.kind == "smelt" and bag["minecraft:furnace"] < 1:
            bad.append((str(st), "minecraft:furnace"))
        if st.kind == "mine" and st.detail.get("tier") is not None and \
                not any(k == "pickaxe" and t >= st.detail["tier"] for k, t in tools):
            bad.append((str(st), f"pickaxe tier {st.detail['tier']}"))
        for tok, n in need.items():
            if n and held(bag, tok) < n:
                bad.append((str(st), tok))
            take(bag, tok, n)
        bag[st.token] += st.count
        mat_kind = st.token.split(":")[-1].split("_", 1)
        if len(mat_kind) == 2 and mat_kind[0] in TIER:
            tools.append((mat_kind[1], TIER[mat_kind[0]]))
    if goal[0] == "tool":
        done = any(k == goal[1] and t >= goal[2] for k, t in tools)
    else:
        done = held(bag, goal[0]) >= goal[1]
    return bad, done


def held(bag, token):
    """What the replayed bag holds of `token`: itself and, for a group, its members."""
    return bag[token] + sum(bag[mid(m)] for m in GROUPS.get(token, ()) if mid(m) != token)


def take(bag, token, n):
    """Use `n` of `token` from the replayed bag: itself first, then a group's members."""
    for t in [token] + [mid(m) for m in GROUPS.get(token, ())]:
        k = min(n, max(0, bag[t]))
        bag[t] -= k
        n -= k
    bag[token] -= n


def goals():
    """Every recipe output a plan can start from nothing (a variant is made from its group: the group's token), and
    upkeep's goals (a bed, an iron pickaxe)."""
    variants = {mid(m) for g in knowledge.GROUP_RECIPES for m in GROUPS.get(g, ())}
    outs = sorted(set(data.RECIPES) - variants) + sorted(knowledge.GROUP_RECIPES)
    return [(o, 1) for o in outs] + [("bed", 1), ("tool", "pickaxe", 2)]


# ------------------------------------------------------------------------------------------------------ the tests
class Declared(unittest.TestCase):
    def test_the_checker_names_what_is_undeclared(self):
        real = ways()
        step = real[0][1]
        rows = [("the real ways and recipes", real, dict(data.RECIPES), []),
                ("must fail: a way that makes something else", [("minecraft:thing", step, [])], {},
                 [f"minecraft:thing: {step.kind} {step.token}"]),
                ("a recipe with no pattern", [], {"minecraft:thing": ([None] * 4, 1)}, ["minecraft:thing"]),
                ("a recipe with no output", [], {"minecraft:thing": (["log", None, None, None], 0)},
                 ["minecraft:thing"])]
        for name, made, recipes, want in rows:
            with self.subTest(name):
                self.assertEqual(undeclared(made, recipes), want)


class Closed(unittest.TestCase):
    def test_every_input_is_made(self):
        from bonobo.planner import Step
        real = ways()
        log = Step("gather", "log", 1, {})
        rows = [("the real ways: every input made", real, set()),
                ("must fail: a broken chain: an input nothing makes", [("a", log, ["b"])], {"b"}),
                ("a variant of a made group is held stock, not a gap", [("log", log, []), ("p", log, ["minecraft:oak_log"])],
                 set())]
        for name, made, want in rows:
            with self.subTest(name):
                self.assertEqual(unclosed(made), want)

    def test_every_token_is_grounded_in_a_base(self):
        roots = [g[0] for g in goals() if g[0] != "tool"] + ["minecraft:iron_pickaxe"]
        cyclic = {"a": ("craft", ["b"], 1), "b": ("craft", ["a"], 1)}
        rows = [("the real knowledge", source, roots, set()),
                ("must fail: a cycle with no base entry", lambda t: cyclic.get(t), ["a"], {"a", "b"}),
                ("a craft from nothing known", lambda t: {"a": ("craft", ["zzz"], 1)}.get(t), ["a"], {"a", "zzz"}),
                ("a chain down to a mine", lambda t: {"a": ("craft", ["b"], 1), "b": ("mine", ["x"], 0)}.get(t),
                 ["a"], set())]
        for name, src, rs, want in rows:
            with self.subTest(name):
                self.assertEqual(ungrounded(all_tokens(src, rs), src) & (want | {"a", "b", "zzz"} | set(rs)), want)


class Replayed(unittest.TestCase):
    def test_the_planner_from_an_empty_bag(self):
        failed, bad = set(), []
        for goal in goals():
            need = [goal] if goal[0] == "tool" else [(goal[0], goal[1])]
            try:
                steps = plan(need)
            except Unplannable:
                failed.add(goal[0])
                continue
            early, held = replay_steps(steps, goal)
            bad += [(goal, e) for e in early] + ([(goal, "not held at the end")] if not held else []) + \
                ([(goal, f"{len(steps)} steps")] if len(steps) > STEP_BOUND else [])
        self.assertEqual((bad, failed), ([], set()))

    def test_the_replay_catches_a_step_out_of_order(self):
        """The replay itself: a plan whose steps are swapped fails it (else a green replay proves nothing)."""
        steps = plan([("tool", "pickaxe", 0)])
        rows = [("as planned", steps, True), ("the pickaxe before its sticks", [steps[-1]] + steps[:-1], False),
                ("must fail: the table missing", [s for s in steps if s.token != "minecraft:crafting_table"], False),
                ("the last step dropped", steps[:-1], False)]
        for name, st, ok in rows:
            with self.subTest(name):
                early, held = replay_steps(st, ("tool", "pickaxe", 0))
                self.assertEqual(not early and held, ok, early)


def skill_need_keys():
    """Every dimension a registered skill's hard needs name: the static ones, and a call-dependent one (mine's
    pickaxe) at every tier a block is mined at (knowledge.MINE) and with none."""
    from bonobo import brain  # noqa: F401  (every skill module registers)
    from bonobo.skill import REGISTRY, needs_of
    tiers = {t for _blocks, t in knowledge.MINE.values()} | {None}
    keys = {}
    for name, c in REGISTRY.items():
        # a call-dependent need at every argument that changes it: mine's tier, trade's want
        calls = [(None, "minecraft:bread", None, None, t) for t in tiers] if c.needs_fn else [()]
        for args in calls:
            for k, n in needs_of(c, args).items():
                keys[k] = max(keys.get(k, 0), n)
    return keys


def unmade_needs(keys, made_ways):
    """Pure: the need dimensions no way makes and no made group covers (a tool dimension: its tool item)."""
    made = {t for t, _s, _ins in made_ways}
    made |= {g for g, ms in GROUPS.items() if any(mid(m) in made for m in ms)}

    def item(k):
        return knowledge.tool_item(k.split(":")[1], int(k.split(":")[2])) if k.startswith("tool:") else k
    return {k for k in keys if item(k) not in made and mid(item(k)) not in made and not variant_of_made_group(k, made)}


class SkillNeeds(unittest.TestCase):
    """Every skill's hard needs close over the ways (something makes each), and each can be had from an empty bag,
    the plan replayed by bag arithmetic."""

    def test_needs_are_made(self):
        from bonobo.planner import Step
        real = ways()
        rows = [("every registered skill: every need made", skill_need_keys(), real, set()),
                ("must fail: a need nothing makes", {"minecraft:unobtainium": 1}, real, {"minecraft:unobtainium"}),
                ("a tool dimension is made", {"tool:pickaxe:2": 1}, real, set()),
                ("a variant of a made group is held stock", {"minecraft:oak_log": 1},
                 [("log", Step("gather", "log", 1, {}), [])], set())]
        for name, keys, made, want in rows:
            with self.subTest(name):
                self.assertEqual(unmade_needs(keys, made), want)

    def test_every_need_from_an_empty_bag(self):
        bad, failed = [], set()
        for key, n in sorted(skill_need_keys().items()):
            goal = ("tool", key.split(":")[1], int(key.split(":")[2])) if key.startswith("tool:") else (key, n)
            try:
                steps = plan([goal])
            except Unplannable:
                failed.add(key)
                continue
            early, held = replay_steps(steps, goal)
            bad += [(key, e) for e in early] + ([(key, "not held at the end")] if not held else [])
        self.assertEqual((bad, failed), ([], set()))

    def test_a_dropped_producer_leaves_no_source(self):
        """The producer tables are the skills' `gives`: take one away and its token has no source (Unplannable)."""
        rows = [("nothing dropped: the bucket is filled", None, "minecraft:water_bucket", True),
                ("must fail: no filling skill, no water bucket", knowledge.GIVES_FILL, "minecraft:water_bucket", False),
                ("must fail: no farm, no wheat (so no bread)", knowledge.GIVES_FARM, "minecraft:bread", False),
                ("must fail: no trade, no emerald", knowledge.GIVES_TRADE, "minecraft:emerald", False)]
        for name, dropped, token, want in rows:
            with self.subTest(name):
                knowledge.producers()
                kept = [g for g in knowledge.PRODUCERS if g is not dropped]
                with mock.patch.object(knowledge, "PRODUCERS", kept):
                    try:
                        plan([(token, 1)])
                        planned = True
                    except Unplannable:
                        planned = False
                self.assertEqual(planned, want)

    def test_the_empty_bag_replay_fails_a_need_nothing_makes(self):
        with self.assertRaises(Unplannable):
            plan([("minecraft:unobtainium", 1)])


def given_tokens():
    """{token: the skills whose producing tables give it} over every registered skill's `gives`."""
    from bonobo import brain  # noqa: F401  (every skill module registers)
    from bonobo.skill import REGISTRY
    out = {}
    for name, c in REGISTRY.items():
        for table in (g for g in c.gives if not isinstance(g, str)):
            for token in table.keys():
                out.setdefault(token, set()).add(name)
    return out


def chain_faults(steps, token, handles):
    """Pure: what is wrong with the planner's chain for one `token` from an empty bag — nothing planned, a step no
    skill carries out (`handles`), a step run before what it uses was held, the token not held at the end, or a last
    step that is not the token's own. [] for a whole chain."""
    if not steps:
        return ["no steps"]
    faults = [f"no skill for {s}" for s in steps if not handles(s)]
    early, held = replay_steps(steps, (token, 1))
    faults += [f"{s} before {tok} was held" for s, tok in early] + ([] if held else ["not held at the end"])
    return faults + ([] if steps[-1].token == token else [f"ends at {steps[-1].token}"])


class EveryProduct(unittest.TestCase):
    """Every item a producing skill gives is reachable from an empty bag: the planner's chain is whole (every step a
    skill's, in an order the bag allows, ending at the token). A taken-only item is planned only where one is known
    (none is, offline); a variant of a group recipe (oak planks, a red bed) may be held stock only — but any chain
    planned for it is whole too."""

    def test_every_given_token_from_an_empty_bag(self):
        from bonobo.skill import handles
        variants = {mid(m) for g in knowledge.GROUP_RECIPES for m in GROUPS.get(g, ())}
        faults, kinds = [], Counter()
        for token, by in sorted(given_tokens().items()):
            kind = ("held stock" if token in variants else
                    "taken" if all(src[0] == "take" for _m, src in knowledge.sources(token)) else "planned")
            kinds[kind] += 1
            try:
                steps = plan([(token, 1)])
                faults += [(token, sorted(by), f) for f in chain_faults(steps, token, handles)]
            except Unplannable as e:
                if kind == "planned":
                    faults.append((token, sorted(by), str(e)))
        self.assertEqual(faults, [])
        self.assertTrue(all(kinds[k] >= 1 for k in ("held stock", "taken", "planned")), kinds)

    def test_a_broken_chain_is_caught(self):
        from bonobo.skill import handles
        from bonobo.planner import Step
        steps = plan([("minecraft:stone_pickaxe", 1)])
        rows = [("as planned", steps, []),
                ("must fail: nothing planned", [], ["no steps"]),
                ("must fail: the last step dropped", steps[:-1], ["not held at the end", f"ends at {steps[-2].token}"]),
                ("must fail: a step no skill carries out", steps + [Step("teleport", "minecraft:stone_pickaxe", 1)],
                 [f"no skill for {Step('teleport', 'minecraft:stone_pickaxe', 1)}"]),
                ("must fail: the pickaxe before its sticks", [steps[-1]] + steps[:-1],
                 lambda got: bool(got) and all("before" in f or f.startswith("ends at") for f in got))]
        for name, chain, want in rows:
            with self.subTest(name):
                got = chain_faults(chain, "minecraft:stone_pickaxe", handles)
                if callable(want):
                    self.assertTrue(want(got), got)
                else:
                    self.assertEqual(got, want)


def skill_calls():
    """[(skill, what, the call's args)]: every registered skill once, and a skill whose needs depend on the call at
    every call the planner makes of it — a step for each token it gives (decompose.effect_detail, the args its
    `provides` builds), plus what else it is asked for (ASKED_TOO)."""
    from bonobo import brain  # noqa: F401  (every skill module registers)
    from bonobo.decompose import effect_detail
    from bonobo.planner import Step
    from bonobo.skill import REGISTRY
    out = []
    for name, c in sorted(REGISTRY.items()):
        if not c.needs_fn:
            out.append((name, "any call", ()))
            continue
        tokens = [t for g in c.gives if not isinstance(g, str) for t in g.keys()] + ASKED_TOO.get(name, [])
        for effect, make in c.provides.items():
            for token in tokens:
                got = make(None, Step(effect, token, 1, effect_detail(effect, token, 1)))
                if got is not None:
                    out.append((name, token, (None,) + tuple(got)))
    return out


ASKED_TOO = {"trade": ["minecraft:bread"],      # a villager sells it: paid in emeralds
             "build_blueprint": ["nether_portal"]}      # a machine by its blueprint's name


def _holds(bag, tools, dim, n):
    if dim.startswith("tool:"):
        _, kind, tier = dim.split(":")
        return any(k == kind and t >= int(tier) for k, t in tools)
    return sum(bag[m] for m in {dim, *knowledge.members(dim)}) >= n


def needs_faults(steps, needs=None):
    """Pure given the skills: [(step, dim)] where a step's own skill needs (knowledge.step_call → needs_of for its
    call) were not held when it came up, replayed by bag arithmetic — and, with `needs`, each of those not held at
    the end."""
    bag, tools, out = Counter(), [], []
    for st in steps:
        own = knowledge.step_call(st)
        out += [(str(st), d) for d, n in own.items() if not _holds(bag, tools, d, n)]
        for tok, n in st.detail.get("inputs", {}).items():
            bag[tok] -= n
        bag[st.token] += st.count
        mat_kind = st.token.split(":")[-1].split("_", 1)
        if len(mat_kind) == 2 and mat_kind[0] in TIER:
            tools.append((mat_kind[1], TIER[mat_kind[0]]))
    return out + [("the end", d) for d, n in (needs or {}).items() if not _holds(bag, tools, d, n)]


def as_rows(needs):
    return [("tool", k.split(":")[1], int(k.split(":")[2])) if k.startswith("tool:") else (k, n)
            for k, n in needs.items()]


class CallNeeds(unittest.TestCase):
    """A skill's `needs` are what the planner plans (planner.before → knowledge.step_call → needs_of for the call the
    step makes): every registered skill's call has its needs planned from an empty bag, each step's own needs held
    when it comes up; a changed need changes the plan, and a need ignored or unmet is caught."""

    def test_every_skill_call_plans_its_needs(self):
        from bonobo.skill import REGISTRY, needs_of
        calls, faults = skill_calls(), []
        self.assertGreaterEqual(len({n for n, _w, _a in calls}), len(REGISTRY))
        for name, what, args in calls:
            needs = needs_of(REGISTRY[name], args)
            try:
                steps = plan(as_rows(needs))
            except Unplannable as e:
                faults.append((name, what, str(e)))
                continue
            faults += [(name, what, f) for f in needs_faults(steps, needs)]
        self.assertEqual(faults, [])

    def test_a_need_ignored_or_unmet_is_caught(self):
        from bonobo import brain  # noqa: F401  (every skill module registers)
        from bonobo.planner import Step
        from bonobo.skill import REGISTRY
        diamond = [("minecraft:diamond", 1)]
        with mock.patch.object(REGISTRY["mine"], "needs_fn", None):      # planned from the no-tier default only
            blind = plan(diamond)
        iron = plan([("minecraft:raw_iron", 1)])
        rows = [("the diamonds as planned", plan(diamond), False),
                ("must fail: mine's needs_fn ignored (no-tier needs only)", blind, True),
                ("must fail: a tier-1 chain, then a diamond", iron + [Step("mine", "minecraft:diamond", 1, {
                    "blocks": ["diamond_ore"], "tier": 2})], True),
                ("must fail: a spider hunted bare-handed", [Step("hunt", "minecraft:string", 1, {
                    "types": ["minecraft:spider"], "kills": 1})], True),
                ("a cow hunted bare-handed", [Step("hunt", "minecraft:beef", 1, {"types": ["minecraft:cow"]})], False),
                ("must fail: a furnace taken bare-handed (take's needs: TAKEABLE's tool)",
                 [Step("take", "minecraft:furnace", 1, {"blocks": ["furnace"]})], True),
                ("a bed taken bare-handed", [Step("take", "bed", 1, {"blocks": ["red_bed"]})], False)]
        for name, steps, bad in rows:
            with self.subTest(name):
                self.assertEqual(bool(needs_faults(steps)), bad, needs_faults(steps))

    def test_a_changed_need_changes_the_plan(self):
        from bonobo import brain  # noqa: F401  (every skill module registers)
        from bonobo.skill import REGISTRY
        mine_needs = REGISTRY["mine"].needs_fn
        # (situation, skill, the field patched, its new value, the goal, a craft the plan has, has it after)
        rows = [("nothing changed: the wheat plot's hoe", "plant_farm", "needs", None, "minecraft:wheat",
                 "minecraft:wooden_hoe", True),
                ("must fail: plant_farm needing no hoe plans none", "plant_farm", "needs", {}, "minecraft:wheat",
                 "minecraft:wooden_hoe", False),
                ("mine asking an iron pickaxe for coal plans one", "mine", "needs_fn",
                 lambda a: {"tool:pickaxe:2": 1} if "coal" in str(a[1]) else mine_needs(a), "minecraft:coal",
                 "minecraft:iron_pickaxe", True),
                ("must fail: hunt needing no sword hunts spiders bare-handed", "hunt", "needs_fn", lambda a: {},
                 "minecraft:string", "minecraft:stone_sword", False),
                ("nothing changed: the spider's sword", "hunt", "needs_fn", None, "minecraft:string",
                 "minecraft:stone_sword", True)]
        for name, skill, field, value, goal, craft, has in rows:
            with self.subTest(name):
                c = REGISTRY[skill]
                patch = mock.patch.object(c, field, value) if value is not None else mock.patch.object(c, "prefer",
                                                                                                        c.prefer)
                with patch:
                    steps = plan([(goal, 1)])
                self.assertEqual(any(s.token == craft for s in steps), has, [str(s) for s in steps])


class CallSpeed(unittest.TestCase):
    """What a carried tool saves on a step is its work's break and kill times with it, against the hand
    (cost._sped_up → knowledge.own_work / work_s over break_ticks and kill_s): no per-skill speed constant."""

    def estimate(self, step, tool):
        from tests.world import cost, inventory, snapshot, state
        return cost(snapshot(state(), inventory(*([(tool, 1)] if tool else [])))).estimate(step)

    def test_every_work_kind_saves_in_the_cost(self):
        from bonobo.planner import Step
        # (step, the tool carried) → it saves
        rows = [("logs, an axe", ("gather", "log", 8, {}), "wooden_axe", True),
                ("a cow hunt, a sword", ("hunt", "minecraft:beef", 4, {"types": ["minecraft:cow"], "kills": 2}),
                 "wooden_sword", True),
                ("dirt, a shovel", ("mine", "minecraft:dirt", 8, {"blocks": ["dirt"], "tier": None, "breaks": 8}),
                 "wooden_shovel", True),
                ("must fail: logs, a shovel", ("gather", "log", 8, {}), "wooden_shovel", False),
                ("must fail: a cow hunt, a pickaxe beats no hand", ("hunt", "minecraft:beef", 4,
                 {"types": ["minecraft:cow"], "kills": 2}), "wooden_shovel", False)]
        for name, (kind, token, n, detail), tool, saves in rows:
            with self.subTest(name):
                step = Step(kind, token, n, dict(detail))
                self.assertEqual(self.estimate(step, tool) < self.estimate(step, None), saves)

    def test_a_shovel_is_no_help_in_stone(self):
        from bonobo.planner import Step
        rows = [("dirt: the shovel saves", ("mine", "minecraft:dirt", {"blocks": ["dirt"], "tier": None}), True),
                ("must fail: stone asks a pickaxe, the shovel saves nothing",
                 ("mine", "minecraft:cobblestone", {"blocks": ["stone"], "tier": 0}), False),
                ("gravel: the shovel saves", ("mine", "minecraft:gravel", {"blocks": ["gravel"], "tier": None}), True),
                ("must fail: iron ore, nothing saved", ("mine", "minecraft:raw_iron", {"blocks": ["iron_ore"], "tier": 1}),
                 False)]
        for name, (kind, token, detail), saves in rows:
            with self.subTest(name):
                step = Step(kind, token, 4, dict(detail, breaks=4))
                self.assertEqual(self.estimate(step, "wooden_shovel") < self.estimate(step, None), saves)


class RipeFirst(unittest.TestCase):
    """A crop already grown is harvested (a take step) or a plot sown (the farm step), by price: harvesting what
    stands costs a take's work, sowing a plot a farm's (knowledge.prior_ticks)."""

    KIT = [("wheat_seeds", 8), ("water_bucket", 1), ("iron_hoe", 1)]

    class Cost(NullCost):
        def __init__(self, ripe):
            super().__init__()
            self._n = ripe

        def ripe(self, token):
            return self._n if token == "minecraft:wheat" else 0

    def test_pricing_touches_no_world(self):
        """An estimate reads memory only: with nothing remembered, pricing wheat asks the game nothing (every
        request raises here) and plans a plot — the look for a grown crop is the farm step's, at execution."""
        from bonobo import api
        from bonobo.cost import Cost
        from tests.world import snapshot
        # (situation, crop jobs in memory, wheat wanted) → the wheat step kinds; no request made in any row
        due = {"kind": "crop", "item": "minecraft:wheat", "count": 8, "ready_at": 0, "pos": [0, 64, 0]}
        rows = [("nothing remembered: sow", [], 1, ["farm"]),
                ("a due crop job of 8: harvest", [due], 1, ["take"]),
                ("must fail: a job not yet due: sow", [dict(due, ready_at=9e12)], 1, ["farm"]),
                ("a due job too small for the need: sow", [dict(due, count=1)], 5, ["farm"])]
        for name, jobs, n, want in rows:
            with self.subTest(name), mock.patch.object(api, "api", side_effect=AssertionError("a world read")):
                import tempfile
                from bonobo.memory import Memory
                mem = Memory(os.path.join(tempfile.mkdtemp(prefix="ripe"), "notes.json"))
                mem.data["jobs"] = [dict(j, dimension="minecraft:overworld") for j in jobs]
                cost = Cost(snapshot(), mem=mem, known=lambda kinds: None, finds={})
                steps = plan([("minecraft:wheat", n)], cost, self.KIT)
                self.assertEqual([s.kind for s in steps if s.token == "minecraft:wheat"], want)

    def test_ripe_or_sown_by_price(self):
        from bonobo.planner import Step
        take = knowledge.prior_ticks(Step("take", "minecraft:wheat", 1, {}))
        farm = knowledge.prior_ticks(Step("farm", "minecraft:wheat", 1, {}))
        cheaper = "take" if take < farm else "farm"
        # (situation, ripe wheat cells known, wheat wanted) → the step kinds for the wheat
        rows = [("nine ripe cells, one wanted: the cheaper of harvest and sowing", 9, 1, [cheaper]),
                ("nine ripe, nine wanted (the boundary): the cheaper", 9, 9, [cheaper]),
                ("two ripe, three wanted: not enough grown, sow a plot", 2, 3, ["farm"]),
                ("must fail to harvest: none ripe, sow", 0, 1, ["farm"])]
        for name, ripe, n, want in rows:
            with self.subTest(name):
                steps = plan([("minecraft:wheat", n)], self.Cost(ripe), self.KIT)
                self.assertEqual([s.kind for s in steps if s.token == "minecraft:wheat"], want)

class BreadFromTheBag(unittest.TestCase):
    """Bread wanted: the plan farms only for the wheat the bag lacks (the bench proves the farm's two halves apart:
    farm__plant sows, bread_from_a_farm reaps a ripe field and bakes)."""

    def test_rows(self):
        # production's own path: the planner by decompose, over the real cost model and memory (tests.test_sources.world)
        from bonobo import brain  # noqa: F401  (registers every skill: the farm step's producer)
        from bonobo import decompose, goals
        from tests.test_sources import world
        kit = [("wheat_seeds", 8), ("water_bucket", 1), ("iron_hoe", 1), ("crafting_table", 1)]
        # (situation, wheat carried) → the plan has a farm step
        rows = [("no wheat: farmed", 0, True),
                ("two wheat, bread takes three: farmed for the one short", 2, True),
                ("three wheat: baked from the bag, no farm", 3, False),
                ("must fail: wheat to spare carried, a farm step would be waste", 9, False)]
        for name, wheat, farmed in rows:
            with self.subTest(name):
                inv, cost = world(items=kit + ([("wheat", wheat)] if wheat else []), finds={"grass_block": 3})
                steps = decompose.decompose(inv, goals.have(("minecraft:bread", 1)), cost)
                self.assertEqual(any(s.kind == "farm" for s in steps), farmed, [str(s) for s in steps])


class SourceRemoved(unittest.TestCase):
    """A source taken away: the goal is unplannable, answered at once — no hang, no partial plan."""

    def test_the_planner(self):
        rows = [("nothing removed: planned", {}, {}, ("bed", 1), True),
                ("no wool from sheep: the spiders' string makes it", {"HUNT": ["wool"]}, {}, ("bed", 1), True),
                ("must fail: no wool from sheep, no string recipe: no bed", {"HUNT": ["wool"]},
                 {"minecraft:white_wool": None}, ("bed", 1), False),
                ("no stick recipe: no pickaxe", {}, {"minecraft:stick": None}, ("tool", "pickaxe", 0), False),
                ("no iron ore: no iron ingot", {"MINE": ["minecraft:raw_iron"]}, {}, ("minecraft:iron_ingot", 1),
                 False),
                ("no furnace recipe: no iron ingot", {}, {"minecraft:furnace": None}, ("minecraft:iron_ingot", 1),
                 False)]
        for name, drop_tables, drop_recipes, goal, ok in rows:
            with self.subTest(name):
                patches = [mock.patch.dict(getattr(knowledge, t)) for t in drop_tables] + \
                    [mock.patch.dict(knowledge.RECIPES)]
                for p in patches:
                    p.start()
                try:
                    for t, keys in drop_tables.items():
                        for k in keys:
                            del getattr(knowledge, t)[k]
                    for k in drop_recipes:
                        del knowledge.RECIPES[k]
                    need = [goal] if goal[0] == "tool" else [goal]
                    try:
                        steps = plan(need)
                        got = replay_steps(steps, goal)[1]
                    except Unplannable:
                        got = False
                    self.assertEqual(got, ok)
                finally:
                    for p in reversed(patches):
                        p.stop()


if __name__ == "__main__":
    unittest.main()
