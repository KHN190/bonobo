"""The threat layer's answer held from an earlier round (kernel.Held, fight_loop.STATE.held: D4 across rounds): none
held; held and still the fresh choice; held, another answer whose assumption broke (fight_loop.still_worth: an answer
no reading offers). The switch path (a held answer still worth its keep, kernel.switches) needs the reading to move
between two rounds: live."""
import time

NAME = "held"
VALUES = ("none", "same", "other")
DEPENDS = (lambda f: f["threat"], {"threat": True})
GONE = -1          # an entity id no reading lists
OTHER = "check_held"   # an answer kind threat.options never makes


def domain():
    return VALUES


def valid(value, f):
    """A ranged mob is a threat row only by a line of fire (perception.reaches_us): a hole's walls (ground) cut it, and
    so do the home's walls when it stands far, outside them (place home/enclosed, range far) — the layer sees
    nothing, so it holds nothing."""
    from bonobo.beliefs import MOBS
    walled = f["ground"] in ("hole", "walled") or (f["range"] in ("far", "outside") and f["place"] in ("home", "enclosed"))
    return value == "none" or not (MOBS[f"minecraft:{f['mob']}"].get("ranged") and walled)


def _reading(state):
    """(the threat model on this reading, its price, its horizon) — as fight_loop.bid prices it."""
    from bonobo import fight_loop, perception, threat
    st = perception.perceived(state, time.time())
    sstate = threat.price_state(hp=max(1, int(st.get("health", 20))), armor=int(st.get("armor", 0)))
    price = lambda dhp: threat.hp_seconds(sstate, dhp)     # noqa: E731
    tstate = fight_loop.threat_state(st, threat.THREAT_ROWS, None, threat.THREAT_IDS)
    return threat.Field(tstate, price, refused=fight_loop.refused_now), price, threat.horizon_for(tstate), st


def prepare(brain, f):
    if f[NAME] == "none":
        return
    from bonobo import api, fight_loop, perception, threat
    from bonobo.kernel import Choice
    state = api.get("/state")
    perception.Watcher()._look(state)
    _model, price, _horizon, st = _reading(state)
    fight_loop.bid(st, threat.THREAT_ROWS, price, ids=threat.THREAT_IDS)     # the layer holds its fresh choice
    keeper = fight_loop.held()
    if f[NAME] == "same" or keeper is None or keeper.choice is None or keeper.choice.action is None:
        return
    # "other": an answer no reading offers (OTHER), so it differs from whatever the round's fresh choice is — the reading
    # the round makes need not be the one this bid saw (a fresh choice compared here came out equal there: 050d99e's
    # round-trip mismatch); its assumption fails (fight_loop.still_worth: not among the options) and the layer decides
    # again (kernel.Held.decide: "assumption")
    alt = threat.Answer(threat.Option(OTHER, GONE, 0.0, 1.0, "check: held, no reading offers it"), price)
    keeper._take(Choice(alt, cost_s=alt.cost_s), time.time())


def alpha(a):
    """none | same (the fresh choice is the held one) | other."""
    from bonobo import fight_loop, kernel, threat
    keeper = fight_loop.held()
    if keeper is None or keeper.choice is None or keeper.choice.action is None or not threat.THREAT_ROWS:
        return "none"
    model = _reading(a.snap.state)[0]
    return "same" if kernel.choose(model, model.state()).name == keeper.choice.name else "other"


def gamma(value, f, g):
    return None              # held in the threat layer (prepare), not the world
