"""Where the threat's damage rate comes from: the belief table (beliefs.MOBS dps), or the jar's own reading on the
entity (`dps`: a row built with it — estimate.row, threat.rows)."""
from bonobo.beliefs import MOBS

NAME = "dps"
VALUES = ("table", "read")
DEPENDS = (lambda f: f["threat"], {"threat": True})
READ_X = 2.0                        # the jar reads it at twice the table's rate (an enchanted weapon, a strength potion)


def domain():
    return VALUES


def alpha(a):
    return "read" if a.threats and a.threats[0].get("dps") is not None else "table"


def gamma(value, f, g):
    if f["threat"] and value == "read":
        e = g.entities[0]
        e["dps"] = float(MOBS[e["type"]].get("dps", 1.0)) * READ_X
