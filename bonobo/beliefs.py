"""What this agent believes about the world, in one place, with how much each belief is worth trusting.

Every number the planner uses is a belief about Minecraft: how far a skeleton shoots, what a death costs, how much
of the damage a shield removes. They were spread across two TOML files and two Python tables, and the same fact
was written down twice — a skeleton's reach was 15 in `play.toml` and 3 in `combat_model.HAZARD_R`, a death cost
240 s in ordinary play and 120 s in a fight. Nobody wrote a bug: two copies of one fact drift, always.

So: one table, read through one door.

    value("time.death_cost_s")   → the number
    belief("time.death_cost_s")  → (number, observations behind it)
    mob("minecraft:skeleton")    → the row

`observations` is 0 for everything today — every number here is a declared guess, which is what `unmeasured` says.
The count is in the interface now, before anything reads it, because the thing that will read it (a planner that
explores when it is unsure) must not require changing every call site again to arrive. `fit` raises the count as
recorded play accumulates; until it does, nothing should pretend to know more than it does.

This module imports nothing. It is the bottom of the package: facts do not depend on decisions.
"""
import math
import os
import tomllib

CONFIG_PATH = os.environ.get("MC_PLAY_CONFIG", os.path.join(os.path.dirname(__file__), "play.toml"))

with open(CONFIG_PATH, "rb") as _f:
    CONFIG = tomllib.load(_f)

# Published game data (minecraft.wiki, Java Edition, Normal difficulty). Not ours to fit: fitting a constant that
# Mojang publishes is fitting noise. Everything else about a mob — how often the hit lands, how far it hurts from
# in practice, how close movement should stay — is behaviour, and only a tape can answer it.
WIKI_FIELDS = ("hp", "attack", "notice_r")
WIKI_N = 10 ** 6          # "known", in the same unit as an observation count, so one comparison works everywhere


def _with_dps(row):
    """`dps` is derived, never stored: a published hit divided by how often it lands. As a stored number it hid
    which half of it was a guess."""
    out = dict(row)
    out["dps"] = row["attack"] / row["attack_s"]
    return out


MOBS = {kind: _with_dps(row) for kind, row in CONFIG["mobs"].items()}
PLAYER = CONFIG["player"]
# The numbers nobody has measured. Named here so a plan can carry the list and nobody mistakes a ranking built on
# them for a measurement.
UNMEASURED = tuple(CONFIG["tools"].get("unmeasured", ()))

# Observation counts, keyed the same way as `value`. Empty: nothing has been fitted yet. `fit` writes here.
COUNTS = {}


def value(path):
    """The believed number at "section.key", or "mobs.<kind>.<field>". A KeyError for an unknown one — a default
    would hide a typo behind a plausible answer, and every number here changes a decision."""
    if path.startswith("mobs."):
        _, kind, field = path.split(".", 2)
        return MOBS[kind][field]
    section, _, key = path.partition(".")
    return CONFIG[section][key]


def count(path):
    """How many observations stand behind this belief. Zero means: a guess, honestly declared; WIKI_N means the
    game publishes it."""
    if path.startswith("mobs.") and path.rsplit(".", 1)[-1] in WIKI_FIELDS:
        return WIKI_N
    return int(COUNTS.get(path, 0))


def belief(path):
    return value(path), count(path)


# What play has actually measured, keyed like `value`: [(observed, when)]. The bench writes here from the residual
# between what an estimator said and what the clock said; `fit` reads it. Nothing is overwritten in `CONFIG` — a
# belief moves when there are enough observations to move it, and that decision is not this module's.
OBSERVED = {}


def observe(path, measured, now=None):
    """Record one measurement of a believed number, and return (believed, observations).

    Deliberately not an update: a single bench cell is one sample of something noisy, and a table that follows its
    last sample is not a belief, it is a rumour. This raises the count, which is what tells a planner how much to
    trust the number and what `unmeasured` is about.
    """
    import time as _time
    value(path)
    OBSERVED.setdefault(path, []).append((float(measured), now if now is not None else _time.time()))
    COUNTS[path] = int(COUNTS.get(path, 0)) + 1
    return belief(path)


def observed(path):
    """Every measurement of this belief, newest last."""
    return list(OBSERVED.get(path, ()))


def residual(path):
    """(believed, mean measured, relative error) — or None when nothing has been measured.

    The number a calibration run reads: an estimator that is 40% out is not a wrong branch, it is a constant that
    play disagrees with, and it should be visible as such before anybody rewrites the code around it.
    """
    seen = [v for v, _t in OBSERVED.get(path, ())]
    if not seen:
        return None
    mean = sum(seen) / len(seen)
    believed = float(value(path))
    return believed, mean, abs(mean - believed) / max(abs(mean), 1e-6)


def mob(kind):
    """One mob's row: `reach` (how far it hurts), `keep_out` (how close movement may plan), dps, speed, hp."""
    return MOBS[kind]


def keep_out():
    """{kind: radius} movement refuses to plan inside. A view of the table, never a second copy of it."""
    return {kind: m["keep_out"] for kind, m in MOBS.items() if m.get("keep_out")}


def protection(armor_points, shield=False):
    """Fraction of incoming damage removed: armour points (0–20, as /state reports them) and a shield in hand.

    One function. There were two — one over armour points, one over "tiers" — so the same iron chestplate removed
    a different share of the damage depending on which planner asked.
    """
    return min(PLAYER["protection_cap"],
               armor_points * PLAYER["protection_per_point"] + (PLAYER["shield"] if shield else 0.0))


def slot_cost_s(bag_free):
    """Seconds one more occupied inventory slot costs, given how many are still free.

    A belief, so it lives at the bottom of the package where anything may ask it — the looter and the planner have
    to agree about what a slot is worth, and a copy in each would drift.

    Emptying the bag costs a trip (`pool.slot_fill_s` is that trip's seconds); with `free` slots left, taking one
    more brings that trip forward by about 1/free of it, and the next one again — so the marginal cost goes as
    1/free². Roomy: 90/36² ≈ 0.07 s, nothing. Six left: 2.5 s, noticeable. Two left: 22 s, and only what is really
    worth carrying still is. Nobody has to choose a "keep some slots free" rule; the curve is the rule.
    """
    free = max(1.0, float(bag_free))
    return float(CONFIG["pool"]["slot_fill_s"]) / (free * free)


# How much a declared number is discounted while nothing has been measured. One observation is worth this many
# "prior" observations' worth of doubt: with n = 0 a benefit is read at half, and it climbs toward the declared
# value as the count grows. Fitting raises the counts (`fit.py`); nothing else does.
PRIOR_STRENGTH = 1.0


def cautious(path, direction="benefit"):
    """The pessimistic end of a belief: what to use when being wrong is not symmetric.

    Every number here carries how much it is worth trusting (`belief` → (value, observations)). Read at face
    value, an unmeasured guess competes on equal terms with something the world has confirmed a hundred times —
    which is how an untested yield prior ("strip mine brings back forty cobblestone") outscored a bed that was
    standing in a house. A benefit nobody has seen is worth less than claimed; a cost nobody has timed is worth
    more. Same table, same seconds, one honest direction each.
    """
    v, n = belief(path)
    trust = float(n) / (float(n) + PRIOR_STRENGTH)
    if direction == "cost":
        return float(v) * (2.0 - trust)
    return float(v) * (0.5 + 0.5 * trust)


def staleness_s(age_s):
    """Seconds to add because the note about something is old: the drift per doubling of age.

    Lives at the bottom with the other beliefs, so the table that reads notes (`actions`) and the door that
    prices scarcity (`gates.marginal`) can both ask without either importing the other.
    """
    fresh = float(CONFIG["memory"]["fresh_s"])
    drift = float(CONFIG["memory"]["drift_s_per_log2"])
    return round(drift * math.log2(1.0 + max(0.0, float(age_s)) / fresh), 2)


def slots_cost_s(count, free):
    """What `count` more occupied slots cost, each priced against the bag as it will be by then.

    Averaging one slot over a batch is what let a full bag take a full stack; the bag empties one slot at a time
    and so does the price.
    """
    free = float(free)
    return sum(slot_cost_s(max(1.0, free - i)) for i in range(int(count)))


def use_rate(kind, mem=None):
    """Uses per second for this kind of tool: the declared belief, corrected by what this world does with it."""
    rates = CONFIG["tool_use"]
    per_day = float(rates.get(kind, rates["other"]))
    prior = per_day / float(CONFIG["time"]["day_s"])
    return prior if mem is None else mem.tool_use_rate(kind, prior)


def expected_uses(kind, mem, left, horizon_s):
    """How many times this tool will still save us something within the horizon:

        min(uses per second × horizon, uses left in the tool)

    Both halves are needed. Frequency alone says a pickaxe with four blocks left is worth as much as a new one;
    durability alone makes a diamond pickaxe the answer to everything, because nobody can see 1561 uses ahead.
    """
    if left <= 0:
        return 0.0
    return round(min(use_rate(kind, mem) * float(horizon_s), float(left)), 2)


def encounter_prior(dark):
    """Hostiles per second of being out there, believed before anything has been observed in that bin.

    Three a day, most of them after dark, and the dark is about a third of the day — declared here, with the other
    beliefs, so the door that asks (`gates.p("encounter")`) need not import the table of actions to find out.
    """
    risk = CONFIG["risk"]
    per_day, day_s = float(risk["encounters_per_day"]), float(CONFIG["time"]["day_s"])
    share, hours = float(risk["night_encounter_share"]), float(risk["night_share_of_day"])
    if not dark:
        share, hours = 1.0 - share, 1.0 - hours
    return (per_day * share) / (day_s * hours)


TICKS_PER_S = 20.0      # the game's clock, in one place


def detour_s(distance, here=None, there=None, via=None):
    """Seconds going out of the way ADDS to a journey we were making anyway.

    Something we pass on the way is nearly free; something in the opposite direction costs the trip out and back.
    Geometry, so it belongs with the other facts rather than with whoever happens to be ranking errands.
    """
    import math as _m
    straight = max(0.0, float(distance)) / float(CONFIG["player"]["speed"])
    if not (here and there and via):
        return straight
    direct = _m.dist(here, via)
    detoured = _m.dist(here, there) + _m.dist(there, via)
    return max(0.0, (detoured - direct)) / float(CONFIG["player"]["speed"])
