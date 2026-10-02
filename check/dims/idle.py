"""idle: nothing queued, and the first idle stocking need (goals.PREPARE[0]) cooling after a failure here
(brain.need_act: a need whose key is not ready offers no step) — prepare walks on to the next need."""
NAME = "idle"
VALUES = ("none", "cooling")
DEPENDS = (lambda f: f["task"] == "none" and f["queued"] == "none", {})


def domain():
    return VALUES


def _key():
    from bonobo import goals
    return f"idle: {goals.describe(goals.have(*[tuple(n) for n in goals.PREPARE[0]]))}"


def prepare(brain, facts):
    if facts["idle"] == "cooling":
        from bonobo.api import NotAvailable
        brain.failed(_key(), NotAvailable("check: this need failed here"))


def alpha(a):
    return "cooling" if a.brain is not None and not a.brain.ready(_key()) else "none"


def gamma(value, facts, g):
    pass                 # the brain's retry: set by prepare
