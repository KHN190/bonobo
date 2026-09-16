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
