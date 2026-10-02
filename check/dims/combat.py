"""What the jar reads the threat doing (perception.read_combat's fields): standing, closing in (its velocity), drawing a
shot at us (shooting, and its arrow's impact and time to it: a dodge), charging (busy), a fireball coming at us (a
ghast's or a blaze's: a burst hazard row that is not outrun — threat.escape_spot holds), an arrow landing beside us
(aside: its impact outside our cell by more than its radius — threat.dodge_spot's "already outside" arm)."""
import math

from bonobo.game import ARROWS
from bonobo.perception import read_combat
from bonobo.threat import FIREBALLS, impacts_of

NAME = "combat"
VALUES = ("still", "closing", "shooting", "charging", "fireball", "aside")
DEPENDS = (lambda f: f["threat"], {"threat": True})
CLOSE_BPT = 0.1          # blocks a tick it walks at us (2 blocks/s, a zombie's pace)
SHOT_TICKS = 10          # ticks to the arrow's impact: half a second
ARROW = next(iter(ARROWS))
FIREBALL = FIREBALLS[1]                       # a blaze's small fireball
ASIDE = 3.0                                   # blocks from the feet the arrow lands: past its 1-block radius (ARROWS)


def domain():
    return VALUES


def alpha(a):
    if not a.threats:
        return VALUES[0]
    e = read_combat(a.threats[:1])[0]
    if any(t["type"] in FIREBALLS for t in a.world._entities({"radius": "48"})["entities"]):
        return "fireball"
    impacts = impacts_of(read_combat(a.world._entities({"radius": "48"})["entities"]))
    if impacts:
        s = a.snap.state
        here = (s["x"], s["y"], s["z"])
        return "aside" if all(math.dist(here, p) > r for p, _t, r in impacts) else "shooting"
    if e["busy"]:
        return "charging"
    return "closing" if any(abs(v) > 0 for v in e.get("vel", ())) else "still"


def gamma(value, f, g):
    if not f["threat"] or value == "still":
        return
    e = g.entities[0]
    if value == "closing":
        e["velocity"] = [-CLOSE_BPT, 0.0, 0.0]          # toward the feet (it stands east of them)
    elif value == "shooting":
        # drawing at us, and its arrow in flight to our feet (threat.impacts_of: a dodge)
        s = g.state
        e["shooting"] = True
        g.entities.append({"id": 50, "type": ARROW, "x": e["x"] - 1.0, "y": s["y"] + 1.0, "z": e["z"],
                           "tti_ticks": SHOT_TICKS, "impact": {"x": s["x"], "y": s["y"], "z": s["z"]}})
    elif value == "aside":
        s = g.state
        e["shooting"] = True
        g.entities.append({"id": 50, "type": ARROW, "x": e["x"] - 1.0, "y": s["y"] + 1.0, "z": e["z"],
                           "tti_ticks": SHOT_TICKS, "impact": {"x": s["x"], "y": s["y"], "z": s["z"] + ASIDE}})
    elif value == "fireball":
        s = g.state
        g.entities.append({"id": 51, "type": FIREBALL, "x": s["x"] + 2.0, "y": s["y"] + 1.0, "z": s["z"],
                           "tti_ticks": SHOT_TICKS, "impact": {"x": s["x"], "y": s["y"], "z": s["z"]}})
    else:
        e["charging"] = True

