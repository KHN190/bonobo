"""How often each night way failed here (retry.Retry.failed's count for the task): once, or again — its cause's cooling
doubles each time (BACKSTOP × 2^(n−1))."""
from bonobo.decompose import way_key

from .failure import failure

NAME = "repeat"
VALUES = ("once", "again")
DEPENDS = (lambda f: f["cooled"], {"cooled": True})


def domain():
    return VALUES


def prepare(brain, f):
    if f[NAME] == "again":
        from check.facts import night_ways
        for way in night_ways():
            brain.failed(way_key(way), failure(f), quiet=True)


def alpha(a):
    from check.facts import night_ways
    task = way_key(night_ways()[0])
    return "again" if any(e["n"] >= 2 for (t, _c), e in a.brain.retry.entries.items() if t == task) else "once"


def gamma(value, f, g):
    return None              # recorded in the brain's retry (prepare), not the world
