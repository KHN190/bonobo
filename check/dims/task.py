"""task: the queue's head when it is not a one-item `have` of the base `queued` fact — each goal template
(goals.TEMPLATES) the brain decomposes (decompose._decompose), and the states a task is in (brain.plan_proposals,
_task_act): expired, its plan stored, cooling after a failure. Read only while `queued` is none (one head)."""
NAME = "task"
TOOL_TIER = 2        # the tool task's tier: iron (one above the base `pickaxe` fact's stone)
FAR = [40, 64, 0]    # a place to go: off the γ floor's cells, a walk away
# value → (the goal, how it is queued): a template each (milestone: iron tools, which no bag here holds; end: the end
# portal milestone, its THEN steps), a blueprint, and three task states over the tool goal
VALUES = ("none", "milestone", "tool", "build", "portal", "goto", "road", "skill", "effect", "end", "expired",
          "planned", "cooling")
DEPENDS = (lambda f: f["queued"] == "none", {"queued": "none"})
STATES = ("expired", "planned", "cooling")
KEY = "task t1"      # the brain's key for the first task (brain.plan_proposals: f"task {id}")


def goal(value):
    from bonobo import goals
    tool = ("tool", "pickaxe", TOOL_TIER)
    return {"milestone": lambda: goals.make("milestone", name="iron tools"),
            "end": lambda: goals.make("milestone", name="end portal"),
            "tool": lambda: goals.have(tool),
            "expired": lambda: goals.have(tool), "planned": lambda: goals.have(tool), "cooling": lambda: goals.have(tool),
            "build": lambda: goals.make("build", bp="shelter"),
            "portal": lambda: goals.make("build", bp="nether_portal"),
            "goto": lambda: goals.make("goto", pos=FAR, range=2),
            "road": lambda: goals.make("road", a=[0, 64, 0], b=FAR),
            "skill": lambda: goals.make("skill", name="chop", args=[4]),
            "effect": lambda: goals.make("effect", effect="light", count=1),
            }[value]()


def domain():
    return VALUES


def _of(t):
    """The value one live task reads as, or None (another dimension's task)."""
    g, args = t["goal"], t.get("args", {})
    if g == "have":
        if args["needs"][0][0] != "tool":
            return None
        if t.get("expires") is not None:
            return "expired"
        return "planned" if t.get("plan") else "tool"
    if g == "milestone":
        return "end" if args.get("name") == "end portal" else "milestone"
    if g == "build":
        return "portal" if args.get("bp") == "nether_portal" else "build"
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
    from bonobo import decompose, tasks
    t = tasks.add(goal(value), expires_s=-1 if value == "expired" else None)
    if value == "planned":
        # a plan stored with the task (an earlier round's): the brain resumes it (brain._task_act)
        steps = [decompose.Step("craft", "minecraft:iron_pickaxe", 1, {})]
        tasks.update(t["id"], plan=[decompose.to_dict(s) for s in steps])


def step(facts, d, ctx):
    """The task's declared effect: a held tool ends it, a seek finds what it seeks."""
    from check.facts import DOMAINS
    if facts["task"] in ("tool",) + STATES and facts["pickaxe"] >= TOOL_TIER:
        return {"task": "none"}         # a `have` is done when the bag says so (goals): the queue moves on
    if facts["task"] == "none" or not (d.name or "").startswith("task"):
        return {}
    if ctx.get("step_kind") == "goto" and facts["task"] == "goto":
        return {"task": "none"}         # arrived: done is standing there (goals)
    if ctx.get("step_kind") == "seek" and d.token in DOMAINS["station"]:
        return {"station": d.token}
    if ctx.get("step_kind") == "seek" and DOMAINS.get(d.token) == (False, True):
        return {d.token: True}          # a seek for what a base fact says is in sight (tree)
    return {}
