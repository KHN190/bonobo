"""The numbers the planner believes about Minecraft (play.toml), read through one door: `value`, `mob`, the tables."""

import os
import tomllib

CONFIG_PATH = os.environ.get("MC_PLAY_CONFIG", os.path.join(os.path.dirname(__file__), "play.toml"))

with open(CONFIG_PATH, "rb") as _f:
    CONFIG = tomllib.load(_f)

def _with_dps(row):
    """`dps` is derived, never stored: a published hit divided by how often it lands."""

    out = dict(row)
    out["dps"] = row["attack"] / row["attack_s"]
    return out

MOBS = {kind: _with_dps(row) for kind, row in CONFIG["mobs"].items()}
# a creeper is kept off past where its fuse stops (fight_creeper_1: backed to 6.9, blew)
MOBS["minecraft:creeper"]["keep_out"] = float(CONFIG["engage"]["fuse_stop_blocks"]) + 0.5
PLAYER = CONFIG["player"]

def value(path):
    """The number at `section.key` (or `mobs.<kind>.<field>`); KeyError for one not in the table."""
    if path.startswith("mobs."):
        _, kind, field = path.split(".", 2)
        return MOBS[kind][field]
    section, _, key = path.partition(".")
    return CONFIG[section][key]

def mob(kind):
    """One mob's row: `reach` (how far it hurts), `keep_out` (how close movement may plan), dps, speed, hp."""
    return MOBS[kind]

def fights_back(types):
    """Does any of these mob types hit back (a row in the table: its dps is known)? Animals do not."""
    return any(t in MOBS for t in types or ())

def keep_out():
    """{kind: radius} movement refuses to plan inside (a view of the table, not a copy)."""
    return {kind: m["keep_out"] for kind, m in MOBS.items() if m.get("keep_out")}

def protection(armor_points, shield=False):
    """Fraction of incoming damage removed: armour points (0–20, as /state reports them) and a shield in hand."""

    return min(PLAYER["protection_cap"],
               armor_points * PLAYER["protection_per_point"] + (PLAYER["shield"] if shield else 0.0))

def slot_cost_s(bag_free):
    """Seconds one more occupied inventory slot costs, given how many are still free."""

    free = max(1.0, float(bag_free))
    return float(CONFIG["plan"]["slot_fill_s"]) / (free * free)

TICKS_PER_S = 20.0      # the game's clock, in one place
