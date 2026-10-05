"""idle: nothing queued, and the run's first milestone (goals.MILESTONES) cooling after a failure here
(brain.plan_proposals: a milestone whose key is not ready is not planned) — the round proposes nothing, with its reason."""
NAME = "idle"
VALUES = ("none", "cooling")
DEPENDS = (lambda f: f["task"] == "none" and f["queued"] == "none", {})


def domain():
    return VALUES


def _key():
    from bonobo import goals
    return f"milestone: {goals.describe(goals.make('milestone', name=next(iter(goals.MILESTONES))))}"


def prepare(brain, facts):
    if facts["idle"] == "cooling":
        from bonobo.api import NotAvailable
        brain.failed(_key(), NotAvailable("check: this need failed here"))


def alpha(a):
    return "cooling" if a.brain is not None and not a.brain.ready(_key()) else "none"


def gamma(value, facts, g):
    pass                 # the brain's retry: set by prepare


def step(facts, d, ctx):
    """Waiting out the cooling (nothing proposed) returns idle to none."""
    return {NAME: "none"} if facts[NAME] == "cooling" and d.kind is None else {}
