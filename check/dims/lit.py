"""A creeper's fuse lit (the jar's `ignited`, threat.fuse_lit): its blast is coming, bait and hit-and-back read it."""
from bonobo.perception import read_combat
from bonobo.threat import fuse_lit

NAME = "lit"
VALUES = (False, True)
DEPENDS = (lambda f: f["threat"] and f["mob"] == "creeper", {"threat": True, "mob": "creeper"})


def domain():
    return VALUES


def alpha(a):
    return bool(a.threats) and fuse_lit(read_combat(a.threats[:1])[0])


def gamma(value, f, g):
    if value:
        g.entities[0]["ignited"] = True
