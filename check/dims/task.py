"""task: the queue's head when it is not a one-item `have` of the base `queued` fact — a milestone (the column
solver: goals.SOLVER_FOR; iron tools: no bag here holds it all), a tool tier, a blueprint. Read only while
`queued` is none (one head)."""
NAME = "task"
TOOL_TIER = 2        # the tool task's tier: iron (one above the base `pickaxe` fact's stone)
VALUES = ("none", "milestone", "tool", "build")   # goto, effect, sleep: one step each, no plan to cover; kept out of the product
DEPENDS = (lambda f: f["queued"] == "none", {"queued": "none"})


def goal(value):
    from bonobo import goals
    return {"milestone": lambda: goals.make("milestone", name="iron tools"),
            "tool": lambda: goals.have(("tool", "pickaxe", TOOL_TIER)),
            "build": lambda: goals.make("build", bp="shelter"),
            }[value]()


def domain():
    return VALUES


def alpha(a):
    """The first live task that is one of this fact's (another dimension's task — a quarry's hunt — is its own)."""
    from bonobo import tasks
    for t in tasks.load():
        if t["state"] not in tasks.LIVE:
            continue
        if t["goal"] == "have" and t["args"]["needs"][0][0] == "tool":
            return "tool"
        if t["goal"] in VALUES:
            return t["goal"]
    return "none"


def gamma(value, facts, g):
    if value != "none":
        from bonobo import tasks
        tasks.add(goal(value))


def step(facts, d, ctx):
    """The task's declared effect: a held tool ends it, a seek finds what it seeks."""
    from check.facts import DOMAINS
    if facts["task"] == "tool" and facts["pickaxe"] >= TOOL_TIER:
        return {"task": "none"}         # a `have` is done when the bag says so (goals): the queue moves on
    if facts["task"] == "none" or not (d.name or "").startswith("task"):
        return {}
    if ctx.get("step_kind") == "seek" and d.token in DOMAINS["station"]:
        return {"station": d.token}
    if ctx.get("step_kind") == "seek" and DOMAINS.get(d.token) == (False, True):
        return {d.token: True}          # a seek for what a base fact says is in sight (tree)
    return {}
