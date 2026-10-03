"""measured: how long work takes — the priors (nothing run here yet), or durations the skill runner measured
(memory.record_duration, skill.MIN_SAMPLES runs of a key): cost.measured prices those, planner.bound keys its table on them."""
from bonobo.skill import MIN_SAMPLES

NAME = "measured"
VALUES = ("prior", "measured")
# the keys the skill runner records under (cost.STAT_KEYS) for a gather and a craft, seconds a unit
RUNS = {"chop": 4.0, "craft": 1.5}


def domain():
    return VALUES


def alpha(a):
    return "measured" if any(a.mem.duration(k, min_samples=MIN_SAMPLES) is not None for k in RUNS) else "prior"


def gamma(value, f, g):
    if value == "measured":
        for key, per in RUNS.items():
            for _ in range(MIN_SAMPLES):
                g.mem.record_duration(key, per)
