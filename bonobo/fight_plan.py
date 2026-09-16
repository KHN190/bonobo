"""The fight planner: what to do next, and by when. Pure functions — it reads a state and returns an intent.

Two layers live here, in this order, and they must not be merged:

  admissible()  a hard veto. Four rules, each of which can only say no. A plan that violates one is not scored and
                not ranked; it does not exist. This is the layer that stops "the score said it was worth it".
  plan()        among what survives the veto, the intent that minimises expected total seconds to a dead dragon.

Everything is denominated in SECONDS, which is what makes indirect benefits computable without special cases. A
crystal shot and a dug tunnel are both "modifiers": they change how many windows remain, or what each window costs.
Their value is the difference they make to the estimated total, so nothing needs a hand-assigned score and nothing
needs a rule saying when to upgrade — with four windows left a tunnel pays for itself, with one it does not, and the
same arithmetic says so both times.

The numbers come from measured tapes (see phase_report.py), not from a wiki: 149 observed sitting phases, 139 of
them exactly 4.95 s, take-off 0.85 s with a standard deviation of 0.10.
"""
import math
import os
import tomllib

# ---------------------------------------------------------------------------------------------------------------
# Configuration. Read from fight.toml, so an experiment changes a number in one file and nothing else. Keeping a
# second copy of the numbers here as "defaults" would defeat that: the copies drift, and then nobody knows which set
# the last run used. The file is required.

CONFIG_PATH = os.environ.get("MC_FIGHT_CONFIG", os.path.join(os.path.dirname(__file__), "fight.toml"))


def load_config(path=None):
    with open(path or CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


CONFIG = load_config()

def _observed(config):
    """Pure: {phase id: [durations]} expanded from the config's [value, count] pairs.

    Stored compressed because 149 sitting phases is 149 identical numbers; expanded here because a quantile needs the
    sample, not a summary of it.
    """
    ids = config["observed"]["phase_ids"]
    out = {}
    for name, phase in ids.items():
        out[phase] = [v for value, count in config["observed"][name] for v in [value] * int(count)]
    return out


OBSERVED = _observed(CONFIG)
WINDOW_PHASES = set(CONFIG["phases"]["window_phases"])
# The phase that announces a window. 4.5 s minimum across 150 observations: everything that must be in place before
# the window opens has to happen here.
ANNOUNCE_PHASE = CONFIG["phases"]["announce_phase"]

DRAGON_HP = CONFIG["combat"]["dragon_hp"]
# What one detonation takes off — the number the whole plan is most sensitive to, and not yet measured in a real
# fight. The planner stays pessimistic until one reports otherwise.
BED_DAMAGE = CONFIG["combat"]["bed_damage"]
# Cost of dying, in seconds: respawn, re-equip, cross the island, dig again. Large on purpose — it is what makes the
# veto rules bite instead of being traded away.
DEATH_COST_S = CONFIG["combat"]["death_cost_s"]
SPRINT_SPEED = CONFIG["combat"]["sprint_speed"]

# Every action declares four things. `benefit` is not a score: it is a function of the state returning seconds saved.
#   duration_s   how long it takes
#   return_s     how long to get back into cover afterwards
#   phases       which dragon phases it may be started in (None = any)
#   benefit      seconds it saves, given the state
ACTIONS = {}


def timing(name):
    """Pure: (duration, return, segment) for an action, from the config file."""
    t = CONFIG["actions"][name]
    return t["duration_s"], t["return_s"], t.get("segment_s")


def action(name, phases=None):
    """Register an action. `segment_s` marks it interruptible and says how long one indivisible piece takes.

    Without this the veto starves every long action forever: the holding pattern's p10 is 2.0 s (a fifth of the
    observed circles are that short) while digging takes 6 s, so "must finish inside the phase" refuses the dig on
    every single cycle and the planner does nothing but stand around. A dig is not atomic, though — each block
    mined is progress that survives being interrupted, so what has to fit in the remaining time is one block, not
    the whole tunnel.
    """
    def register(benefit):
        duration_s, return_s, segment_s = timing(name)
        ACTIONS[name] = {"name": name, "duration_s": duration_s, "return_s": return_s,
                         "phases": phases, "benefit": benefit, "segment_s": segment_s}
        return benefit
    return register


def commitment(act):
    """Pure: the time that must be available before starting — one segment for interruptible work, the whole thing
    for anything that cannot be abandoned halfway (a bomb half-thrown is a death)."""
    return act["segment_s"] if act.get("segment_s") else act["duration_s"]


# ---------------------------------------------------------------------------------------------------------------
# Time model


def quantile(xs, q):
    """Pure: the q-quantile of a sample, nearest-rank. No numpy in the cerebellum."""
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(math.ceil(q * len(s))) - 1))]


def remaining(phase, elapsed_s, q=0.1):
    """Pure: the q-quantile of how much longer this phase will last, given it has already run `elapsed_s`.

    Conditional, not marginal: "phase 6 lasts 4.95 s" is useless 4 s in. Taking the quantile over only the observed
    durations that got at least this far is what stops the planner from starting a 1 s action with 0.3 s left.
    """
    left = [d - elapsed_s for d in OBSERVED.get(phase, []) if d > elapsed_s]
    if not left:
        return 0.0
    return round(quantile(left, q), 2)


def windows_left(dragon_hp, bed_damage=BED_DAMAGE):
    """Pure: how many more firing windows this fight needs. The multiplier on every per-window improvement."""
    return max(0, math.ceil(dragon_hp / bed_damage)) if bed_damage > 0 else 0


def cycle_seconds():
    """Pure: the median seconds between one window and the next — one full lap of the phase cycle."""
    return sum(quantile(OBSERVED.get(p, [0]), 0.5) for p in (4, 0, 2, 3))


def death_risk(exposure_s, in_cover):
    """Pure: rough probability of dying during an action, from how long it leaves us exposed.

    Not a fitted model — there is no death data yet. It is monotonic in exposure and near zero inside cover, which is
    all the plan needs to prefer cover; when real fights provide numbers this is the one function to replace.
    """
    if in_cover:
        return 0.01
    return min(0.9, 0.10 * exposure_s)


def total_seconds(state):
    """Pure: the objective — estimated seconds from here to a dead dragon, risk included.

    Minimising this is the whole planner. Every action is worth exactly the reduction it makes here, which is how an
    indirect benefit (a tunnel that shortens future exposure) is comparable with a direct one (a bomb).
    """
    n = windows_left(state["dragon_hp"])
    per_window_exposure = state.get("exposure_s", 3.0)
    risk = death_risk(per_window_exposure, state.get("in_cover", False))
    return n * cycle_seconds() + n * per_window_exposure + n * risk * DEATH_COST_S


# ---------------------------------------------------------------------------------------------------------------
# Layer 1: the veto. Each rule can only refuse.


def admissible(state, act):
    """Pure: (allowed, reason). The four hard rules, checked in order of how badly they end a run.

    A refusal is never overridden by a good score: that is the whole point of keeping this separate from plan().
    """
    # The default action is exempt from every rule, deliberately. Retreating into cover is what the rules exist to
    # protect, so a rule that can refuse it produces "no admissible action" — and an agent with no action stands
    # still in the open, which is precisely how the bench fights died. There must always be an answer.
    if act["name"] == "retreat":
        return True, ""
    left = remaining(state["phase"], state["phase_elapsed_s"])
    # 1. Recursive feasibility: whatever we start, we must be able to finish AND be back in cover before this phase
    #    can plausibly end. Take-off gives 0.85 s of warning, which is less than the time to cross open ground — so
    #    "react when it happens" is not available, and the check has to happen before starting.
    need = commitment(act) + act["return_s"]
    if need > left:
        return False, f"needs {need:.1f}s, phase has {left:.1f}s left (p10)"
    # 2. Survivability: the damage expected while doing it must fit in the health we can spare.
    expected = act["duration_s"] * state.get("incoming_dps", 0.0)
    if expected >= state["hp"] - state.get("hp_floor", 6.0):
        return False, f"expects {expected:.0f} damage, only {state['hp'] - state.get('hp_floor', 6.0):.0f} to spare"
    # 3. Resources, including what a retreat would need.
    if act.get("phases") is not None and state["phase"] not in act["phases"]:
        return False, f"wrong phase ({state['phase']})"
    if act["name"] == "fire_window" and state.get("beds", 0) < 1:
        return False, "no beds"
    if act["name"] == "reinforce" and state.get("obsidian", 0) < 5:
        return False, "no obsidian"
    if act["name"] == "shoot_crystal" and not state.get("crystals_open", 0):
        return False, "no open crystal"
    if act["name"] == "water_bucket" and not state.get("water", False):
        return False, "no water bucket"
    # 4. The safety invariant: a path back into the tunnel must exist at every moment. Without a tunnel there is no
    #    invariant to hold, so only the actions that build one are allowed while none exists.
    if not state.get("tunnel_ready", False) and act["name"] in ("fire_window",):
        return False, "no tunnel to retreat into"
    return True, ""


# ---------------------------------------------------------------------------------------------------------------
# The action set. Each benefit returns SECONDS SAVED, so they are directly comparable.

@action("dig_tunnel", phases={0, 2})
def _dig_tunnel(state):
    if state.get("tunnel_ready"):
        return 0.0
    after = dict(state, tunnel_ready=True, in_cover=True, exposure_s=1.0)
    return total_seconds(state) - total_seconds(after)


@action("place_bed", phases={0, 2, 3})
def _place_bed(state):
    if state.get("bed_placed") or state.get("beds", 0) < 1:
        return 0.0
    # Turns a window from "walk, place, click" into "click": the saving is per window, for every window left.
    return 2.0 * windows_left(state["dragon_hp"])


@action("reinforce", phases={0, 2})
def _reinforce(state):
    if state.get("reinforced") or not state.get("tunnel_ready"):
        return 0.0
    # Without it the bunker is demolished by our own blasts partway through; the cost of rebuilding is the dig.
    return 6.0 * max(0, windows_left(state["dragon_hp"]) - 2)


@action("shoot_crystal", phases={0, 2})
def _shoot_crystal(state):
    # A crystal only heals the dragon while it flies past one, so this is worth a fraction of a window, not a window.
    if not state.get("crystals_open"):
        return 0.0
    return 0.3 * cycle_seconds()


@action("water_bucket")
def _water(state):
    return DEATH_COST_S * death_risk(2.0, False) if state.get("angry_endermen") else 0.0


@action("fire_window", phases=WINDOW_PHASES)
def _fire(state):
    # One window's worth of progress, measured the same way the objective measures everything else.
    after = dict(state, dragon_hp=max(0.0, state["dragon_hp"] - BED_DAMAGE))
    return total_seconds(state) - total_seconds(after)


@action("retreat")
def _retreat(state):
    # Always admissible, always available, worth nothing — the default when everything else is vetoed. A planner
    # whose answer can be "nothing" has no answer at all when it matters.
    return 0.0


# ---------------------------------------------------------------------------------------------------------------
# Layer 2: the plan.


def plan(state):
    """Pure: {"intent", "deadline_s", "benefit_s", "rejected": [(name, reason)]}.

    Returns an intent and a deadline, never a trajectory. How it is carried out belongs to the executor: a 0.4 s
    window is run open-loop by the mod because a 98 ms control loop cannot steer inside it, while a 6 s dig is run
    closed-loop and abandoned the moment its abort condition fires.
    """
    allowed, rejected = [], []
    for act in ACTIONS.values():
        ok, why = admissible(state, act)
        if ok:
            allowed.append(act)
        else:
            rejected.append((act["name"], why))
    # Anything worth nothing is not worth doing. Without this the planner picked a zero-benefit action (emptying a
    # water bucket at no endermen) over retreating, purely because both scored 0.0 and one sorted first — busywork
    # in the open while a dragon sits on the fountain.
    scored = sorted(((act["benefit"](state), act) for act in allowed if act["benefit"](state) > 0),
                    key=lambda p: -p[0])
    best_benefit, best = scored[0] if scored else (0.0, ACTIONS["retreat"])
    left = remaining(state["phase"], state["phase_elapsed_s"])
    return {
        "intent": best["name"],
        # The latest moment it may still be started and still finish its commitment inside this phase.
        "deadline_s": round(max(0.0, left - commitment(best) - best["return_s"]), 2),
        "duration_s": best["duration_s"],
        "commitment_s": commitment(best),
        "benefit_s": round(best_benefit, 2),
        "rejected": rejected,
    }
