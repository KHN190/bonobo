"""What the maintain rows remember from earlier rounds (reflexes.Maintain's own state): nothing; the body in one block
with one bag for STUCK_LIMIT (history: unstuck); ashore from the water less than LAND_EXIT_S ago (afloat,
land_since: the hysteresis, SAFETY's hazard "swimming"); a row that ran last round and changed nothing (last_run: stalled — the bag still full);
a path failure here a moment ago toward a target (blocked: path blocked)."""
import time

NAME = "past"
VALUES = ("none", "stuck", "latched", "stalled", "blocked")
TARGET = (12, 64, 12)               # where the failed walk was going


def domain():
    return VALUES


def valid(value, f):
    """stalled: the row that ran (empty the bag) must fire again — the bag still full."""
    return value != "stalled" or f["bag"] == "full"


def prepare(brain, f):
    from bonobo import api, reflexes
    from bonobo.bag import bag_signature
    from bonobo.world import Inventory
    m, now, value = brain.reflexes, time.time(), f[NAME]
    s = api.get("/state")
    feet = (s["blockX"], s["blockY"], s["blockZ"])
    if value == "stuck":
        sig = bag_signature(Inventory())
        m.history = [(now - reflexes.STUCK_LIMIT - 1, feet, sig), (now - 1, feet, sig)]
    elif value == "latched":
        m.afloat = True
        m.land_since = now - reflexes.LAND_EXIT_S / 2
    elif value == "stalled":
        m.last_run = ("empty the bag", Inventory().used_slots())
    elif value == "blocked":
        m.blocked = {"t": now, "place": None, "pos": TARGET}     # the round decides with b.place None (check/round.py)


def alpha(a):
    m = a.brain.reflexes
    if m.blocked is not None and m.blocked["pos"] is not None:      # a nav failure with no target (cooled) is not it
        return "blocked"
    if m.last_run is not None:
        return "stalled"
    if m.afloat and m.land_since is not None:
        return "latched"
    return "stuck" if len(m.history) >= 2 else "none"


def gamma(value, f, g):
    return None                      # the brain's own memory (prepare), not the world


def step(facts, d, ctx):
    """The rows these memories fire end them: unstuck moves the body, the swimming rescue stands it ashore, path
    blocked bridges the way (blocked cleared)."""
    name = (d.name or "").lower()
    return {NAME: "none"} if name in ("unstuck", "rescue swimming", "path blocked") else {}
