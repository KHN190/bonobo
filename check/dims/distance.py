"""How far the threat stands: in its reach (the jar's in_reach), a few blocks off, or past the fight's notice radius
(threat.awareness under 1) yet inside its own (a zombie, an enderman: they notice farther than ENGAGE's)."""
from bonobo.beliefs import MOBS
from bonobo.perception import read_combat
from bonobo.threat import ENGAGE, awareness, context_of

NAME = "range"        # (file distance.py: before pack.py, whose second mob stands by the first as placed)
VALUES = ("mid", "reach", "far")
DEPENDS = (lambda f: f["threat"], {"threat": True})
NOTICE = float(ENGAGE["notice_r"])


def domain():
    return VALUES


def valid(value, f):
    return value != "far" or float(MOBS[f"minecraft:{f['mob']}"]["notice_r"]) > NOTICE


def alpha(a):
    if not a.threats:
        return VALUES[0]
    e = read_combat(a.threats[:1])[0]
    s = a.snap.state
    if e["reach_now"]:                    # the jar's in_reach (a provoked one is "at us" without it: at_us)
        return "reach"
    return "far" if awareness(e, (s["x"], s["y"], s["z"]), context_of(s, None)) < 1.0 else "mid"


def gamma(value, f, g):
    if not f["threat"] or value == "mid":
        return
    e = g.entities[0]
    if value == "reach":
        e.update(x=g.state["x"] + 1.0, in_reach=True)
    else:
        e.update(x=g.state["x"] + NOTICE * 1.25)      # a quarter past the notice radius: awareness 0.75
