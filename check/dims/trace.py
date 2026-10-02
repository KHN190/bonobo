"""What the threat layer remembers of the threat from the look before (perception.STATE.seen: threat.rows' `memory`):
nothing; the mob a block farther a second ago (a velocity: rows' dt window, arrival read from it); or the jar's row
carries no entity id (rows keep no memory for it)."""
import time

NAME = "trace"
VALUES = ("none", "moving", "no_id")
DEPENDS = (lambda f: f["threat"], {"threat": True})
AGO_S = 1.0                         # inside rows' (0.01, 2.0) s window
FARTHER = 1.0                       # a block farther along x than now


def valid(value, f):
    """A velocity is read off the layer's rows: none when walls hide a ranged mob from it (held.valid: one rule)."""
    from .held import valid as seen
    return value != "moving" or seen("same", f)


def domain():
    return VALUES


def prepare(brain, f):
    if f[NAME] != "moving":
        return
    from bonobo import api, perception
    for e in api.get("/entities?radius=48")["entities"]:
        if e.get("id") is not None:
            perception.STATE.seen[e["id"]] = ((e["x"] + FARTHER, e["y"], e["z"]), time.time() - AGO_S)


def alpha(a):
    if not a.threats:
        return VALUES[0]
    if a.threats[0].get("id") is None:
        return "no_id"
    from bonobo import threat
    return "moving" if any(any(abs(v) > 0 for v in r[2]) for r in threat.THREAT_ROWS) else "none"


def gamma(value, f, g):
    if f["threat"] and value == "no_id":
        g.entities[0].pop("id", None)
