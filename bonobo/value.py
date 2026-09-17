"""What anything is worth, in seconds. One formula, for every candidate.

    worth(x) = Σ_t γ(t)·[ V(s_t) − V(s_t ⊕ x) ]·Δt/H  −  slot_cost(bag)·slots(x)

    V(s)  what finishing everything costs from state s: the terminal goods (`solve.reach_cost` over
          `survival.END_DIMS`) plus what is left of the run (`play.toml [progress]`)
    γ(t)  1 / (1 + t/H): the discount the pool already applies to anything that pays later
    s_t   where ordinary play leaves us after t seconds — hunger falls, tools wear, stock is spent

The planner used to answer this question four different ways (points, a yield table times shadow prices, a
progress ladder, a credit walk), and each one needed a rule to stop it misbehaving: the fortieth cobblestone was
worth as much as the first, an untested "strip mine" prior sat at 720 s all morning, and food was worth nothing
while the belly was full — because the price of a good was read at the instant it was asked about, and at that
instant we were not yet hungry.

Reading it over the HORIZON instead makes those rules unnecessary:

  * repetition decays, because the second unit is priced against a state that already holds the first;
  * food and tools are worth something before they are needed, because hunger and wear are in the dynamics;
  * nothing the terminal goods (or the run) do not want is worth anything, without a list of what to ignore;
  * carrying costs what a slot costs, so a full bag stops taking things without a "keep N free" rule.

Pure: the world arrives as two functions (`value_of`, `evolve`) and two numbers (`horizon_s`, `bag_free`), so the
shape of the number can be tested without a game, and the brain supplies the real ones.
"""
from .beliefs import CONFIG, slot_cost_s   # noqa: F401  (kept: `gates.marginal("slot")` is the door)

STEPS = 3          # how finely the horizon is sampled (see NODES): the integrand is smooth, so three is plenty

# Gauss-Legendre nodes and weights on [0, 1]. The integrand is γ(t)·gain(t): γ is a smooth hyperbola and the gain
# moves with the dynamics, so a three-point rule is exact to about a part in a thousand where eight midpoints cost
# nearly three times as many V evaluations. Midpoints are the n=1 row of the same table, which is why the two
# agree on constant integrands and the algebra tests did not have to change.
_GAUSS = {
    1: ((0.5,), (1.0,)),
    2: ((0.211324865405187, 0.788675134594813), (0.5, 0.5)),
    3: ((0.112701665379258, 0.5, 0.887298334620742),
        (0.277777777777778, 0.444444444444444, 0.277777777777778)),
    4: ((0.069431844202974, 0.330009478207572, 0.669990521792428, 0.930568155797026),
        (0.173927422568727, 0.326072577431273, 0.326072577431273, 0.173927422568727)),
}


def nodes(steps):
    """(t_fraction, weight) pairs over the horizon. Above the table, fall back to midpoints — the rule stays a
    quadrature either way, and no caller has to know which one it got."""
    got = _GAUSS.get(int(steps))
    if got:
        return list(zip(*got))
    n = int(steps)
    return [((i + 0.5) / n, 1.0 / n) for i in range(n)]


def discount(t, horizon_s):
    """What a saving t seconds from now is worth against the same saving now. The same curve `priority` uses for
    a benefit that arrives late, so a candidate cannot be worth more here than it scores there."""
    return 1.0 / (1.0 + max(0.0, float(t)) / float(horizon_s))


def apply(state, effect):
    """The state with `effect` in it. Additive, like everything else in the vector."""
    out = dict(state)
    for dim, delta in (effect or {}).items():
        out[dim] = out.get(dim, 0) + delta
    return out


def gain(before, after):
    """How much cheaper things got, when `value_of` answers with COMPONENTS rather than one number.

    A stand-in, not a second way of pricing: the honest V is the cost of ONE plan that reaches every terminal good
    together, which `solve` computes exactly — and at 160 ms it cannot run a few hundred times a round. Until it
    can, the components are priced independently (`reach_cost`, microseconds) and combined here. Delete this the
    day the joint solve is affordable.

    Terminal goods compete for the same stock: sixteen planks can be a bed or a shelter, not both, so adding up
    what they save every end at once prices them three times over (a stack of planks came out worth more than the
    bed it would become). Keys that start with "end:" therefore contribute their BIGGEST single saving — the same
    lower-bound rule `solve.credits` uses, and shy is the safe direction. Everything else (what is left of the
    run) is not in competition with anything and adds up.
    """
    if not isinstance(before, dict):
        return float(before) - float(after)
    ends = [float(before[k]) - float(after.get(k, before[k])) for k in before if str(k).startswith("end:")]
    rest = sum(float(v) - float(after.get(k, v)) for k, v in before.items() if not str(k).startswith("end:"))
    return (max(ends) if ends else 0.0) + rest


def worth_s(state, effect, *, value_of, evolve=None, horizon_s=None, bag_free=36, slots=1.0, steps=STEPS,
            takes_s=0.0):
    """Seconds having `effect` is worth from `state`, over the horizon, minus what getting and carrying it costs.

    `value_of(s)` is what finishing costs from s; `evolve(s, t)` is where play leaves us after t seconds (None
    means the state stands still, which is what the algebra tests use).

    `takes_s` is how long doing it takes, and it goes INSIDE the comparison rather than being subtracted after:
    the state we end up in is the one `takes_s` seconds further on, hungrier and with more of the tool worn away.
    Subtracting the work outside would be a second pipeline for the same seconds — and that is exactly how a bed
    behind a lava sheet came to score HIGHER than one on flat ground, because the harder ground made the
    before-state dearer (value went up) while only six seconds of walking were charged against it.
    """
    horizon_s = float(horizon_s or CONFIG["time"]["day_s"])
    evolve = evolve or (lambda s, _t: dict(s))
    saved = 0.0
    for fraction, weight in nodes(steps):
        t = fraction * horizon_s
        dt = weight * horizon_s
        s_t = evolve(state, t)
        # Now, against then: the work has happened, the clock has moved on by what it took, and the thing is ours.
        # The saving is discounted from when it ARRIVES (t + takes_s), not from when the comparison is made: a
        # bed four hundred seconds of walking away is not the same bed as one underfoot.
        after = apply(evolve(state, t + float(takes_s)), effect)
        saved += discount(t + float(takes_s), horizon_s) * gain(value_of(s_t), value_of(after)) * dt
    from . import gates
    # Each slot priced against the bag as it will be by then, not one slot averaged over a batch: that average is
    # what let a full bag take a full stack.
    return saved / horizon_s - gates.marginal("slots", count=max(1, int(round(slots))), free=bag_free)
