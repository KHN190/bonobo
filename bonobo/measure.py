"""Reading back what really happened, and comparing it with what was estimated.

`estimate` says what a situation costs; this says what it cost. One table pairs them:

    QUANTITIES[name] = Quantity(predict, measure, tolerance)

`predict` reads the number out of a priced state, `measure` reads it out of a recorded trace, and `tolerance` is
how far apart they may be before the difference is a fault rather than noise. A bench walks this table and writes
`predicted / measured / residual`; it holds no arithmetic of its own, which is how the walk once came back as six
thousand blocks (a respawn counted as walking) and every residual read from it was noise wearing a number.

A trace is what the bench records at its sampling rate: `[{t, hp, pos, near: [(kind, distance, hp)]}]`. Nothing
here knows why the body moved, only that it did.
"""
import math

from . import beliefs, estimate

TRACE_STEP_S = 0.2                 # what the bench samples at; a gap much larger than this is a blind stretch
SPRINT_STEPS = 4.0                 # a single sample cannot cover more ground than this many strides


def _pairs(trace):
    return list(zip(trace, trace[1:]))


def walked(trace, step_cap=None):
    """Blocks the body actually travelled.

    A respawn or a teleport is not distance walked: a single sample cannot cover more ground than a sprint could,
    and anything that does is the world moving us, not us moving.
    """
    if not trace:
        return None
    cap = step_cap if step_cap is not None else float(beliefs.PLAYER["speed"]) * TRACE_STEP_S * SPRINT_STEPS
    gaps = [(b["t"] - a["t"], math.dist(a["pos"], b["pos"])) for a, b in _pairs(trace)]
    return round(sum(d for dt, d in gaps if d <= cap * max(1.0, dt / TRACE_STEP_S)), 2)


def walk_s(trace):
    """Seconds spent walking, at the speed the body walks."""
    distance = walked(trace)
    return None if distance is None else round(distance / float(beliefs.PLAYER["speed"]), 2)


def hp_rate(trace):
    """Health per second lost while it was being lost — the measured twin of `estimate.pressure_hp_s`.

    Over the seconds health actually fell, not over the whole trace: standing safely for five seconds and then
    taking four damage in one is four hp/s of pressure, not 0.8.
    """
    if not trace:
        return None
    losses = [(b["t"] - a["t"], a["hp"] - b["hp"]) for a, b in _pairs(trace) if b["hp"] < a["hp"]]
    bleeding = sum(dt for dt, _d in losses)
    return round(sum(d for _dt, d in losses) / bleeding, 3) if bleeding else 0.0


def hp_lost(trace):
    if not trace:
        return None
    return round(max(0.0, trace[0]["hp"] - min(s["hp"] for s in trace)), 2)


def healed(trace):
    if not trace:
        return None
    return round(sum(max(0.0, b["hp"] - a["hp"]) for a, b in _pairs(trace)), 2)


def first_hit_s(trace):
    for a, b in _pairs(trace or []):
        if b["hp"] < a["hp"]:
            return a["t"]
    return None


def first_arrival(trace, reach=3.0, kind=None):
    """When something first came within `reach` — the measured twin of `estimate.arrival_s`."""
    for sample in trace or []:
        for mob, distance, _hp in sample["near"]:
            if (kind is None or mob == kind) and distance <= reach:
                return sample["t"]
    return None


def in_reach_s(trace, reach=3.0, kind=None):
    """Seconds spent inside something's reach: the denominator pressure is a rate over."""
    if not trace:
        return None
    total = 0.0
    for a, b in _pairs(trace):
        inside = any((kind is None or mob == kind) and distance <= reach
                     for mob, distance, _hp in a["near"])
        if inside:
            total += b["t"] - a["t"]
    return round(total, 2)


def cleared_s(trace, kind=None):
    """When the last of them stopped being there. None while any is still in the final sample."""
    if not trace:
        return None
    left = {mob for mob, _d, _h in trace[-1]["near"] if kind is None or mob == kind}
    if left:
        return None
    last = None
    for sample in trace:
        if any(kind is None or mob == kind for mob, _d, _h in sample["near"]):
            last = sample["t"]
    return last


def fight_s(trace, kind=None):
    """How long the fight took: from the first sample to the one where the last of them was gone."""
    return cleared_s(trace, kind)


def sample_gap_s(trace):
    """The usual gap between samples — how often we actually looked."""
    if not trace or len(trace) < 2:
        return None
    gaps = sorted(b["t"] - a["t"] for a, b in _pairs(trace))
    return round(gaps[len(gaps) // 2], 3)


def worst_gap_s(trace):
    """The longest stretch nobody looked. A measurement taken across a blind stretch is weak evidence, and the
    residual has to be able to say so."""
    if not trace or len(trace) < 2:
        return None
    return round(max(b["t"] - a["t"] for a, b in _pairs(trace)), 3)


def residual(said, seen):
    """How far an estimate was from the measurement, as a share of what was measured."""
    if said is None or seen is None:
        return None
    seen = float(seen)
    if not seen and not said:
        return 0.0
    # Rounded late, and finely: a residual is compared with a tolerance, and rounding it to four places made
    # "exactly a sixth" into a number no arithmetic could reproduce.
    return abs(seen - float(said)) / max(abs(seen), 1e-6)


def disagreement(path):
    """(believed, mean measured, residual) for one belief — or None when nothing has been measured.

    Comparisons live here, with the other comparison: `beliefs` is the bottom of the package and holds facts, not
    arithmetic about them. A constant that play disagrees with by 40% is not a wrong branch, it is a number nobody
    has checked, and it should be visible as such before anybody rewrites the code around it.
    """
    seen = [v for v, _t in beliefs.observed(path)]
    if not seen:
        return None
    mean = sum(seen) / len(seen)
    believed = float(beliefs.value(path))
    return believed, mean, residual(believed, mean)


# How much of a disagreement is the measurement's own noise. A reading taken over two seconds at 5 Hz is ten
# samples: a fifty-per-cent gap there is a couple of ticks of jitter, while the same gap over a minute is a model
# that is wrong. So a tolerance is not a constant — it is a floor plus a term that shrinks with the evidence.
NOISE = 1.5            # how far a single-sample reading may be out before it means anything


def tolerance_for(base, seconds=None, samples=None):
    """The tolerance for a reading of this length: `base` once there is plenty of evidence, wider when there is
    little. Red or not-red must depend on the model, not on how long the bench happened to run."""
    n = float(samples if samples else (float(seconds or 0.0) / TRACE_STEP_S))
    return float(base) + NOISE / max(1.0, n) ** 0.5


class Quantity:
    """One number the agent estimates, with the one way to read it back and how far apart they may be."""

    __slots__ = ("predict", "measure", "tolerance", "belief")

    def __init__(self, predict, measure, tolerance, belief=None):
        self.predict, self.measure, self.tolerance = predict, measure, float(tolerance)
        self.belief = belief          # the belief path a residual is evidence about, if any


def _predict_arrival(state):
    rows = state.get("hazards") or []
    seen = [estimate.arrival_s(state["here"], row, state.get("field")) for row in rows]
    finite = [s for s in seen if s < float("inf")]
    return min(finite) if finite else None


def _predict_pressure(state):
    rows = state.get("hazards") or []
    if not rows:
        return None
    return estimate.pressure_hp_s(state["here"], rows, float(state.get("protection", 0.0)),
                                  ground=state.get("field"))


def _predict_fight(state):
    rows = state.get("hazards") or []
    if not rows:
        return None
    return estimate.fight_cost(state["here"], rows, state.get("sword", 0),
                               float(state.get("protection", 0.0)))[0]


def _predict_fight_hp(state):
    rows = state.get("hazards") or []
    if not rows:
        return None
    return estimate.fight_cost(state["here"], rows, state.get("sword", 0),
                               float(state.get("protection", 0.0)))[1]


def _predict_evade(state):
    from . import threat
    rows = state.get("hazards") or []
    if not rows:
        return None
    spot = threat.escape_spot(state["here"], rows)
    return round(math.dist(state["here"], spot) / float(beliefs.PLAYER["speed"]), 2)


# The whole contract, in one place. A bench iterates this; it decides nothing and computes nothing itself.
QUANTITIES = {
    "arrival_s": Quantity(_predict_arrival, lambda trace, state: first_arrival(
        trace, reach=min((r[1] for r in (state.get("hazards") or [])), default=3.0)), tolerance=1.0),
    "pressure_hp_s": Quantity(_predict_pressure, lambda trace, _state: hp_rate(trace), tolerance=1.5),
    "fight_s": Quantity(_predict_fight, lambda trace, _state: fight_s(trace), tolerance=1.5),
    "fight_hp": Quantity(_predict_fight_hp, lambda trace, _state: hp_lost(trace), tolerance=2.0),
    "evade_s": Quantity(_predict_evade, lambda trace, _state: walk_s(trace), tolerance=1.5),
    "hp_lost": Quantity(lambda _state: None, lambda trace, _state: hp_lost(trace), tolerance=2.0),
    "in_reach_s": Quantity(lambda _state: None, lambda trace, state: in_reach_s(
        trace, reach=min((r[1] for r in (state.get("hazards") or [])), default=3.0)), tolerance=1.0),
    "walked": Quantity(lambda _state: None, lambda trace, _state: walked(trace), tolerance=1.5),
    "healed": Quantity(lambda _state: None, lambda trace, _state: healed(trace), tolerance=2.0),
    "first_hit_s": Quantity(lambda _state: None, lambda trace, _state: first_hit_s(trace), tolerance=1.0),
    "sample_gap_s": Quantity(lambda _state: None, lambda trace, _state: sample_gap_s(trace), tolerance=1.0),
    "worst_gap_s": Quantity(lambda _state: None, lambda trace, _state: worst_gap_s(trace), tolerance=1.0),
}


def read(trace, state, seconds=None, blind_s=0.0):
    """{name: {predicted, measured, residual, tolerance, off}} for every quantity, over one recorded cell.

    `seconds` is how long the window was and `blind_s` how much of it nobody could see: both widen the tolerance,
    because a short window and a blind one are weak evidence and a rule that ignores that reports noise as fault.
    """
    out = {}
    watched = max(0.0, float(seconds or (trace[-1]["t"] if trace else 0.0)) - float(blind_s or 0.0))
    for name, spec in QUANTITIES.items():
        said = spec.predict(state) if state else None
        seen = spec.measure(trace, state or {})
        gap = residual(said, seen)
        allowed = tolerance_for(spec.tolerance, seconds=watched)
        out[name] = {"predicted": said, "measured": seen, "residual": gap, "tolerance": round(allowed, 3),
                     "off": gap is not None and gap > allowed}
    return out
