"""What a failure here ended in: the cause it is counted and cooled under (data.EXCEPTIONS' first column, of the
exceptions brain.outcome_of calls failed — an interruption is no failure). Read while the night ways cooled here
(`cooled`): the cause their failures were recorded with (brain.retry). The round's D5 failure ends in it too."""
from bonobo.brain import outcome_of
from bonobo.data import EXCEPTIONS
from bonobo.decompose import way_key
from bonobo.retry import Retry

NAME = "failure"
DEPENDS = (lambda f: f["cooled"], {"cooled": True})


def domain():
    return VALUES


def _classes():
    """Every exception class of ours EXCEPTIONS names, by name (the class tree, walked)."""
    out, todo = {}, [Exception]
    while todo:
        c = todo.pop()
        if c.__name__ in EXCEPTIONS and c.__module__.startswith("bonobo"):
            out.setdefault(c.__name__, c)
        todo += c.__subclasses__()
    return out


def _failing():
    """{cause: the first exception class counted under it}, in EXCEPTIONS' order, of the failed outcomes."""
    out = {}
    for name, cls in sorted(_classes().items(), key=lambda kv: list(EXCEPTIONS).index(kv[0])):
        try:
            err = cls("check")
        except (TypeError, AttributeError):
            continue                 # an exception that needs more than a message is not raised by a step here
        cause = EXCEPTIONS[name][0]
        # a failure that cools (retry.Retry.failed: a verdict) — a replan's first ones only count
        if outcome_of(err)[0] == "failed" and Retry().failed("check", cause, "check", 0.0) is not None:
            out.setdefault(cause, cls)
    return out


FAILING = _failing()
VALUES = ("unavailable",) + tuple(c for c in FAILING if c != "unavailable")


def failure(f):
    return FAILING[f[NAME]]("check: the step failed here")


def alpha(a):
    from check.facts import night_ways
    causes = a.brain.retry.causes(way_key(night_ways()[0]))
    return causes[0] if causes else VALUES[0]


def gamma(value, f, g):
    return None              # the failure is recorded in the brain (round.decide: `failure`), not the world
