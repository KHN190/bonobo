"""The fight planner: what to do next, and by when. Pure — it reads a state and returns an intent. Three layers, in this order, never merged: state         {self, boss, threats, resources, terrain}. Structure, not a list of nouns: adding an enemy adds rows to `threats`, not fields to the dict; adding a resource adds a key to `resources` and a `requires` entry on the action that needs it, never a branch in the planner. safety        `Fight.admissible` — a veto and only a veto. It answers "can this be started and still leave a safe cell reachable before the first threat arrives" (the union over every threat, earliest arrival), plus the data an action declares it needs. It never scores. planning      `Fight.plan` — among what survives, minimise the general cost cost = work / rate  +  Σ exposure  +  p(death) × death_cost Every action's value is the change it makes to that cost after its declared `effect` is applied to the state. There are no hand-written benefit numbers: a tunnel is worth exactly the exposure and risk it removes from the windows still to come, and stops being worth it when few remain. Actions are instantiated per fight from a profile, not held in a module-level table filtered by phase. The profile says what "work" and "rate" mean for this boss; the dragon's profile lives here because it is the only one so far, and nothing outside the profile knows it is a dragon. Numbers come from fight.toml. Some of them are still guesses; the config says which, and every plan carries that list as `assumptions` so nobody mistakes the ranking for a measurement."""

import math
import os
import tomllib

# ---------------------------------------------------------------------------------------------------------------
# Configuration. One file. A second copy of a number in code is a number that will drift.

CONFIG_PATH = os.environ.get("MC_FIGHT_CONFIG", os.path.join(os.path.dirname(__file__), "fight.toml"))

NOW_S = 1e-6      # "now" as a horizon: only what already reaches us is inside it
PLAYER_HP = 20.0  # a full bar, the health a window's risk is measured against

def load_config(path=None):
    with open(path or CONFIG_PATH, "rb") as f:
        return tomllib.load(f)

CONFIG = load_config()
# What a death costs is a fact about the game, not about this fight: it lives in the belief table with everything
# else. It was written down twice (240 s there, 120 s here), so the fight veto and ordinary play disagreed about
# the price of the same death.
from . import beliefs, estimate  # noqa: E402
CONFIG["combat"]["death_cost_s"] = beliefs.value("time.death_cost_s")

def _observed(config):
    """{phase id: [durations]} expanded from the config's [value, count] pairs."""
    ids = config["observed"]["phase_ids"]
    return {phase: [v for value, count in config["observed"][name] for v in [value] * int(count)]
            for name, phase in ids.items()}

OBSERVED = _observed(CONFIG)
UNMEASURED = list(CONFIG["combat"].get("unmeasured", []))

# ---------------------------------------------------------------------------------------------------------------
# State. Built by `fight_state`, checked by `validate_state`; the planner never reads a state it did not validate.

REQUIRED = {"self": ("pos", "hp"), "boss": ("phase", "phase_elapsed_s", "hp")}
LIMITS = {("self", "hp"): (0.0, 20.0), ("boss", "phase_elapsed_s"): (0.0, 300.0), ("boss", "hp"): (0.0, None)}

def fight_state(self_, boss, threats=(), resources=None, terrain=None):
    """Assemble a fight state."""

    s = dict(self_)
    s.setdefault("hp_floor", CONFIG["combat"]["hp_floor"])
    s.setdefault("speed", CONFIG["combat"]["sprint_speed"])
    s.setdefault("in_cover", False)
    return {"self": s, "boss": dict(boss), "threats": [tuple(t) for t in threats],
            "resources": dict(resources or {}), "terrain": dict(terrain or {})}

def validate_state(state):
    """Pure: [(where, problem)]."""

    out = []
    for section, keys in REQUIRED.items():
        if section not in state:
            out.append((section, "missing"))
            continue
        for k in keys:
            if k not in state[section]:
                out.append((f"{section}.{k}", "missing"))
    for (section, key), (lo, hi) in LIMITS.items():
        v = state.get(section, {}).get(key)
        if v is None:
            continue
        if v < lo or (hi is not None and v > hi):
            out.append((f"{section}.{key}", f"{v} outside {lo}..{hi if hi is not None else '∞'}"))
    for i, row in enumerate(state.get("threats", [])):
        if len(row) < 4:
            out.append((f"threats[{i}]", "needs (centre, radius, velocity, kind)"))
    return out

def lookup(state, path):
    """Pure: a dotted path into the state."""

    head, _, rest = path.partition(".")
    if head == "threats":
        return sum(1 for t in state.get("threats", []) if t[3] == rest)
    v = state.get(head, {}).get(rest)
    if isinstance(v, bool):
        return 1 if v else 0
    return 0 if v is None else v

def _set(state, section, key, value):
    """A copy of `state` with one field changed. Effects are pure; the caller keeps the original."""
    out = dict(state)
    if section == "threats":
        out["threats"] = list(value)
    else:
        out[section] = dict(state[section])
        out[section][key] = value
    return out

# ---------------------------------------------------------------------------------------------------------------
# Time model. Conditional quantiles over the observed samples, never a stored median.

def quantile(xs, q):
    """Pure: nearest-rank q-quantile."""
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(math.ceil(q * len(s))) - 1))]

def remaining(phase, elapsed_s, q=0.1):
    """Pure: how much longer this phase lasts, at quantile q, GIVEN it has already run `elapsed_s`."""

    left = [d - elapsed_s for d in OBSERVED.get(phase, []) if d > elapsed_s]
    return round(quantile(left, q), 2) if left else 0.0

def cycle_seconds():
    """Pure: median seconds from one window to the next — one lap of the phase cycle."""
    return sum(quantile(OBSERVED.get(p, [0]), 0.5) for p in (4, 0, 2, 3))

# ---------------------------------------------------------------------------------------------------------------
# Actions. Data plus an effect. Instantiated per fight by the profile, never a module-level table.

class Action:
    """One thing the agent can decide to do."""

    __slots__ = ("name", "duration_s", "return_s", "segment_s", "in_cycle", "phases", "requires", "needs",
                 "effect", "default")

    def __init__(self, name, timing, phases=None, requires=None, needs=None, effect=None, default=False):
        self.name = name
        self.duration_s = timing["duration_s"]
        self.return_s = timing["return_s"]
        self.segment_s = timing.get("segment_s")
        self.in_cycle = bool(timing.get("in_cycle", False))
        self.phases = None if phases is None else set(phases)
        self.requires = dict(requires or {})
        self.needs = dict(needs or {})
        self.effect = effect or (lambda state: state)
        self.default = default

    def __repr__(self):
        return f"Action({self.name})"

    @property
    def commitment_s(self):
        """kernel's contract: the part that cannot be abandoned half-way — one segment for interruptible work, the whole duration for anything atomic."""

        return self.segment_s if self.segment_s else self.duration_s

    @property
    def cost_s(self):
        """kernel's contract: seconds this action adds to the fight."""

        return 0.0 if self.in_cycle else self.duration_s + self.return_s

from .kernel import commitment          # noqa: E402  one definition of "how much of this cannot be called off"

# ---------------------------------------------------------------------------------------------------------------
# The dragon's profile: what work, rate and exposure mean here, and which actions exist.
# Nothing outside this section knows the boss is a dragon.

ENDERMAN = "minecraft:enderman"

def _fx_dig(state):
    return _set(_set(state, "terrain", "tunnel_ready", True), "self", "in_cover", True)

def _fx_bed(state):
    return _set(state, "terrain", "bed_placed", True)

def _fx_reinforce(state):
    return _set(state, "terrain", "reinforced", True)

def _fx_crystal(state):
    return _set(state, "terrain", "crystals_open", max(0, lookup(state, "terrain.crystals_open") - 1))

def _fx_water(state):
    return _set(state, "threats", None, [t for t in state["threats"] if t[3] != ENDERMAN])

def _fx_fire(state):
    return _set(state, "boss", "hp", max(0.0, state["boss"]["hp"] - CONFIG["combat"]["bed_damage"]))

def _fx_retreat(state):
    return _set(state, "self", "in_cover", bool(lookup(state, "terrain.tunnel_ready")))

class Profile:
    """A boss, described by what removes its work and what that costs in exposure."""

    def __init__(self, name, specs, work, per_window, exposure):
        self.name = name
        self.specs = specs
        self.work = work
        self.per_window = per_window
        self.exposure = exposure

def _dragon_work(state):
    """Health to remove, plus the heal debt of every crystal it can still reach on a lap."""
    return state["boss"]["hp"] + lookup(state, "terrain.crystals_open") * CONFIG["combat"]["crystal_heal_hp"]

def _dragon_per_window(state):
    """Work removed per window: a bed when we have one, otherwise whatever a sword manages."""
    if lookup(state, "resources.beds") > 0:
        return CONFIG["combat"]["bed_damage"]
    return CONFIG["combat"]["melee_per_window"]

def _dragon_exposure(fight, state, n):
    """(seconds exposed, p(death)) summed over the next n windows."""

    c = fight.cfg["combat"]
    tunnel = bool(lookup(state, "terrain.tunnel_ready"))
    reinforced = bool(lookup(state, "terrain.reinforced"))
    covered = n if (tunnel and reinforced) else (min(n, c["cover_windows_unreinforced"]) if tunnel else 0)
    # A blast destroys its bed, so every window but the one already prepared pays the placing time.
    placing = fight.timing("place_bed")["duration_s"]
    unprepared = n - (1 if lookup(state, "terrain.bed_placed") else 0)
    e_cov, e_open = c["exposure_in_cover_s"], c["exposure_in_open_s"]
    exposure = covered * e_cov + (n - covered) * e_open + unprepared * placing
    risk = covered * fight.death_risk(e_cov, True) + (n - covered) * fight.death_risk(e_open, False)
    risk += unprepared * fight.death_risk(placing, tunnel)
    return exposure, risk

DRAGON = Profile(
    "ender_dragon",
    specs=[
        dict(name="dig_tunnel", phases={0, 2}, needs={"tunnel_ready": False}, effect=_fx_dig),
        dict(name="place_bed", phases={0, 2, 3}, requires={"resources.beds": 1}, needs={"bed_placed": False},
             effect=_fx_bed),
        dict(name="reinforce", phases={0, 2}, requires={"resources.obsidian": 5},
             needs={"tunnel_ready": True, "reinforced": False}, effect=_fx_reinforce),
        dict(name="shoot_crystal", phases={0, 2},
             requires={"resources.bow": 1, "resources.arrows": 1, "terrain.crystals_open": 1}, effect=_fx_crystal),
        dict(name="water_bucket", requires={"resources.water": 1, f"threats.{ENDERMAN}": 1}, effect=_fx_water),
        dict(name="fire_window", phases=set(CONFIG["phases"]["window_phases"]),
             requires={"resources.beds": 1}, needs={"tunnel_ready": True}, effect=_fx_fire),
        dict(name="retreat", default=True, effect=_fx_retreat),
    ],
    work=_dragon_work,
    per_window=_dragon_per_window,
    exposure=_dragon_exposure,
)

# ---------------------------------------------------------------------------------------------------------------
# The fight: one per engagement, holding its own actions and its own config.

class Fight:
    def __init__(self, profile=DRAGON, config=None):
        self.cfg = config or CONFIG
        self.profile = profile
        self.actions = [Action(sp["name"], self.timing(sp["name"]), sp.get("phases"), sp.get("requires"),
                               sp.get("needs"), sp.get("effect"), sp.get("default", False))
                        for sp in profile.specs]
        defaults = [a for a in self.actions if a.default]
        if len(defaults) != 1:
            raise ValueError(f"a fight needs exactly one default action, {profile.name} has {len(defaults)}")
        self.default = defaults[0]
        self.dps = dict(self.cfg.get("dps", {}))

    def timing(self, name):
        return self.cfg["actions"][name]

    def action(self, name):
        return next(a for a in self.actions if a.name == name)

    # -- statistics ---------------------------------------------------------------------------------------------

    def dragon_dps(self, in_cover):
        """Health per second the dragon takes off while a window is open."""

        rate = float(self.cfg["combat"]["death_risk_per_exposed_s"]) * float(PLAYER_HP)
        return rate * float(self.cfg["combat"]["cover_lets_through"]) if in_cover else rate

    def death_risk(self, exposure_s, in_cover, hp=None):
        """p(death) over one window: the damage those seconds imply at this rate, through the one curve."""

        spare = float(PLAYER_HP if hp is None else hp)
        damage = estimate.damage_over(self.dragon_dps(in_cover), exposure_s)
        return estimate.fatal_chance(spare, damage, cap=0.9)

    def dps_here(self, state):
        """Health per second we are taking NOW: the one pressure function over a horizon of nothing, which is what "now" means — only what already covers us counts, and anything on its way counts once it arrives."""

        pos = state["self"]["pos"]
        rows = [estimate.row(t[0], t[1], t[2], t[3], dps=self.dps.get(t[3], 0.0)) for t in state["threats"]]
        return estimate.pressure_hp_s(pos, rows, horizon=NOW_S)

    def immediate_risk(self, state):
        """p(death) from what is hitting us right now, before any window: the damage a reaction time lets through at the rate covering us, against the health we can spare."""

        spare = max(state["self"]["hp"] - state["self"]["hp_floor"], 1.0)
        damage = estimate.damage_over(self.dps_here(state), self.cfg["combat"]["reaction_s"])
        return estimate.fatal_chance(spare, damage, cap=0.9)

    # -- the objective -----------------------------------------------------------------------------------------

    # kernel's model contract: `price`, `actions`, `admissible`, `default`, `fault`, `assumptions`. The fight
    # planner IS kernel.choose with these four; see kernel.py. Nothing below knows it is a dragon.

    def price(self, state):
        """kernel: what the future costs from here, in seconds."""

        return self.objective(state)

    def fault(self, state):
        return validate_state(state)

    @property
    def assumptions(self):
        return list(UNMEASURED)

    def objective(self, state):
        """Expected seconds from here to a finished fight, risk included."""

        work = self.profile.work(state)
        if work <= 0:
            return 0.0
        per = max(self.profile.per_window(state), 1e-6)
        n = math.ceil(work / per)
        exposure, risk = self.profile.exposure(self, state, n)
        risk += self.immediate_risk(state)
        return n * cycle_seconds() + exposure + min(1.0, risk) * self.cfg["combat"]["death_cost_s"]

    def benefit(self, state, action):
        """Seconds saved by this action: the one scoring rule (`estimate.saved_s`) over this model's price."""
        return round(estimate.saved_s(self.objective, state, action.effect(state)), 2)

    # -- the veto -----------------------------------------------------------------------------------------------

    def admissible(self, state, action):
        """(allowed, reason)."""

        if action.default:
            return True, ""
        me, boss = state["self"], state["boss"]
        if me["hp"] <= 0:
            return False, "no health: nothing is admissible until alive again"
        if action.phases is not None and boss["phase"] not in action.phases:
            return False, f"wrong phase ({boss['phase']})"
        busy = commitment(action) + action.return_s
        left = remaining(boss["phase"], boss["phase_elapsed_s"])
        if busy > left:
            return False, f"needs {busy:.1f}s, phase has {left:.1f}s left (p10)"
        for path, minimum in action.requires.items():
            have = lookup(state, path)
            if have < minimum:
                return False, f"needs {path} ≥ {minimum}, have {have}"
        for key, wanted in action.needs.items():
            if bool(lookup(state, f"terrain.{key}")) != bool(wanted):
                return False, f"needs terrain.{key} = {wanted}"
        if state["threats"]:
            # Recursive feasibility on the field: after committing for `busy` seconds, is a safe cell still
            # reachable before the FIRST threat arrives? Union over all threats — a pincer is invisible to any
            # single-threat test, and one killed a run.
            from . import combat_model
            _spot, slack = combat_model.best_step(me["pos"], state["threats"], speed=me["speed"],
                                                  horizon=busy + 1.0, cover=me.get("cover"))
            if slack is not None and slack < busy:
                return False, f"committing {busy:.1f}s, first threat arrives in {slack + busy:.1f}s"
        expected = action.duration_s * self.dps_here(state)
        spare = me["hp"] - me["hp_floor"]
        if expected > 0 and expected >= spare:
            return False, f"expects {expected:.0f} damage, only {max(spare, 0):.0f} to spare"
        return True, ""

    # -- the plan -----------------------------------------------------------------------------------------------

    def plan(self, state):
        """{intent, deadline_s, duration_s, commitment_s, benefit_s, rejected, fault, assumptions}."""

        from . import kernel
        choice = kernel.choose(self, state)
        best = choice.action or self.default
        left = remaining(state["boss"]["phase"], state["boss"]["phase_elapsed_s"])
        return {
            "fault": choice.fault,
            "assumptions": choice.assumptions,
            "intent": best.name,
            "deadline_s": round(max(0.0, left - commitment(best) - best.return_s), 2),
            "duration_s": best.duration_s,
            "commitment_s": commitment(best),
            "benefit_s": round(choice.score if choice.action is not None else 0.0, 2),
            "rejected": choice.rejected,
        }
