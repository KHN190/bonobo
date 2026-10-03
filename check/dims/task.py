"""task: the queue's head when it is not a one-item `have` of the base `queued` fact — each goal template
(goals.TEMPLATES) the brain decomposes (decompose._decompose), and the states a task is in (brain.plan_proposals,
_task_act): expired, cooling after a failure. Read only while `queued` is none (one head)."""
NAME = "task"
TOOL_TIER = 2        # the tool task's tier: iron (one above the base `pickaxe` fact's stone)
FAR = [40, 64, 0]    # a place to go: off the γ floor's cells, a walk away
# value → (the goal, how it is queued): a template each (milestone: iron tools, which no bag here holds; end: the end
# portal milestone, its THEN steps), a blueprint, and two task states over the tool goal
VALUES = ("none", "milestone", "tool", "build", "portal", "goto", "road", "skill", "effect", "end", "expired",
          "cooling", "solver", "food", "bed", "torches", "blocks", "blaze", "pearls", "second",
          "effect_hunt", "effect_mine", "effect_take", "effect_craft", "effect_bare", "build_unknown",
          "logs", "iron", "fill", "farm")
# effect goals of each kind decompose fills a detail for (decompose.effect_detail: hunt, mine, take, craft) and one
# whose step lacks the detail its provider reads (missing_detail: refused, Unplannable)
EFFECTS = {"effect": "light", "effect_hunt": "hunt:minecraft:beef", "effect_mine": "mine:minecraft:coal",
           "effect_take": "take:minecraft:crafting_table", "effect_craft": "craft:minecraft:stick",
           "effect_bare": "goto"}
# one-item `have`s outside the base `queued` fact: a material a container may hold (logs, iron: from_containers, B1),
# a bucket of water (a fill column), wheat (a farm column)
HAVE = {"logs": ("minecraft:oak_log", 16), "iron": ("minecraft:iron_ingot", 3),
        "fill": ("minecraft:water_bucket", 1), "farm": ("minecraft:wheat", 8)}
UNKNOWN_BP = "check-none"    # a blueprint nobody registered (decompose._decompose refuses it)
# the column solver's milestones with their own branches: a meal (planner._food, cooked_from_carried), a bed (wool: a
# hunt or a shear), torches (coal or charcoal: a smelt), building blocks (a mine at a tier)
# blaze rods: a hunt in FALL_RISK (needs.needs_water_bucket); pearls: a hunt of a fighter (actions._hunt's FIGHTERS)
MILESTONES = {"food": "food", "bed": "bed", "torches": "torches", "blocks": "building blocks", "blaze": "blaze rods",
              "pearls": "ender pearls"}
SECOND = "check-second"      # second: the tool task queued behind a finished one (plan_proposals walks past it)
NO_SOLVER = "check-none"     # a task naming a solver nobody registered (decompose.solve_needs: then every solver)
DEPENDS = (lambda f: f["queued"] == "none", {"queued": "none"})
STATES = ("expired", "cooling")
# the values whose goal is done when its one step has run (goals.RUN_ONCE: skill, effect; a road is two steps)
RUN_ONCE = ("skill", "effect", "effect_hunt", "effect_mine", "effect_take", "effect_craft", "effect_bare")
KEY = "task t1"      # the brain's key for the first task (brain.plan_proposals: f"task {id}")


def goal(value):
    from bonobo import goals
    tool = ("tool", "pickaxe", TOOL_TIER)
    return {"milestone": lambda: goals.make("milestone", name="iron tools"),
            "end": lambda: goals.make("milestone", name="end portal"),
            "tool": lambda: goals.have(tool),
            "expired": lambda: goals.have(tool), "cooling": lambda: goals.have(tool),
            "solver": lambda: goals.have(tool), "second": lambda: goals.have(tool),
            **{v: (lambda n=n: goals.make("milestone", name=n)) for v, n in MILESTONES.items()},
            "build": lambda: goals.make("build", bp="shelter"),
            "portal": lambda: goals.make("build", bp="nether_portal"),
            "goto": lambda: goals.make("goto", pos=FAR, range=2),
            "road": lambda: goals.make("road", a=[0, 64, 0], b=FAR),
            "skill": lambda: goals.make("skill", name="chop", args=[4]),
            **{v: (lambda e=e: goals.make("effect", effect=e, count=1)) for v, e in EFFECTS.items()},
            **{v: (lambda r=r: goals.have(r)) for v, r in HAVE.items()},
            "build_unknown": lambda: goals.make("build", bp=UNKNOWN_BP),
            }[value]()


def domain():
    return VALUES


def _of(t):
    """The value one live task reads as, or None (another dimension's task)."""
    g, args = t["goal"], t.get("args", {})
    if g == "have":
        if args["needs"][0][0] != "tool":
            item = args["needs"][0][0]
            return next((v for v, (i, _n) in HAVE.items() if i == item), None)
        if t.get("expires") is not None:
            return "expired"
        if t.get("solver"):
            return "solver"
        if t.get("source") == SECOND:
            return "second"
        return "tool"
    if g == "milestone":
        named = {n: v for v, n in MILESTONES.items()}
        return "end" if args.get("name") == "end portal" else named.get(args.get("name"), "milestone")
    if g == "build":
        return {"nether_portal": "portal", UNKNOWN_BP: "build_unknown"}.get(args.get("bp"), "build")
    if g == "effect":
        return next((v for v, e in EFFECTS.items() if e == args.get("effect")), None)
    return g if g in VALUES else None


def alpha(a):
    """The first live task that is one of this fact's (another dimension's task — a quarry's hunt — is its own)."""
    from bonobo import tasks
    for t in tasks.load():
        if t["state"] in tasks.LIVE and (v := _of(t)) is not None:
            return "cooling" if v == "tool" and a.brain is not None and not a.brain.ready(KEY) else v
    return "none"


def prepare(brain, facts):
    """cooling: the task's key failed here (brain.failed), so plan_proposals passes it by."""
    if facts["task"] == "cooling":
        from bonobo.api import NotAvailable
        brain.failed(KEY, NotAvailable("check: this task failed here"))

def gamma(value, facts, g):
    if value == "none":
        return
    from bonobo import goals, tasks
    if value == "second":
        done = tasks.add(goals.make("goto", pos=FAR, range=2))
        tasks.update(done["id"], state="done")
    t = tasks.add(goal(value), expires_s=-1 if value == "expired" else None,
                  source=SECOND if value == "second" else "cerebrum")
    if value == "solver":
        tasks.update(t["id"], solver=NO_SOLVER)


def step(facts, d, ctx):
    """The task's declared effect: a held tool ends it, a round that proposes nothing ended it, a run-once goal's step
    ends it, a seek finds what it seeks."""
    from check.facts import DOMAINS
    if facts["task"] in ("tool", "solver", "second") + STATES and facts["pickaxe"] >= TOOL_TIER:
        return {"task": "none"}         # a `have` is done when the bag says so (goals): the queue moves on
    if facts["task"] not in ("none", "cooling") and d.name is None:
        return {"task": "none"}         # nothing proposed with a live task: it ended this round (brain.finish, done
        #                                 or failed; plan_proposals then proposes nothing — brain.py:537)
    if facts["task"] == "none" or not (d.name or "").startswith("task"):
        return {}
    if facts["task"] in RUN_ONCE:
        return {"task": "none"}         # a run-once goal: done when its plan has run (goals.RUN_ONCE)
    if facts["task"] == "road" and ctx.get("step_kind") == "goto":
        # a road is two legs (decompose: goto a, goto b): the first walked leaves one (plan_held walking), the second ends it
        return {"task": "none"} if facts["plan_held"] == "walking" else {"plan_held": "walking"}
    if ctx.get("step_kind") == "goto" and facts["task"] == "goto":
        return {"task": "none"}         # arrived: done is standing there (goals)
    if ctx.get("step_kind") == "seek" and d.token in DOMAINS["station"]:
        return {"station": d.token}
    if ctx.get("step_kind") == "seek" and DOMAINS.get(d.token) == (False, True):
        return {d.token: True}          # a seek for what a base fact says is in sight (tree)
    return {}
