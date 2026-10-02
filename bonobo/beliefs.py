"""The numbers the planner believes about Minecraft (play.toml), read through one door: `value`, `mob`, the tables."""

import os
import tomllib

from . import formulas, game

TICKS_PER_S = 20.0      # the game's clock, in one place

CONFIG_PATH = os.environ.get("MC_PLAY_CONFIG", os.path.join(os.path.dirname(__file__), "play.toml"))

with open(CONFIG_PATH, "rb") as _f:
    CONFIG = tomllib.load(_f)

def _with_dps(kind, row):
    """A mob's row with the game's numbers (game.py) over play.toml's behaviour; dps = hit / cadence."""
    out = dict(row)
    out.setdefault("hp", game.MOB_HP.get(kind))
    out.setdefault("attack", game.MOB_HIT.get(kind))
    if kind in game.MOB_CADENCE_TICKS:
        out["attack_s"] = game.MOB_CADENCE_TICKS[kind] / TICKS_PER_S
    out["dps"] = out["attack"] / out["attack_s"]
    return out

CONFIG["engage"].setdefault("fuse_s", game.CREEPER_FUSE_TICKS / TICKS_PER_S)
CONFIG["engage"].setdefault("fuse_stop_blocks", game.CREEPER_STOP_BLOCKS)
MOBS = {kind: _with_dps(kind, row) for kind, row in CONFIG["mobs"].items()}
# a creeper is kept off past where its fuse stops (fight_creeper_1: backed to 6.9, blew)
MOBS["minecraft:creeper"]["keep_out"] = float(CONFIG["engage"]["fuse_stop_blocks"]) + 0.5
PLAYER = CONFIG["player"]
COMMON_FOE_HP = MOBS["minecraft:zombie"]["hp"]     # an attack names its mob by id only: its weapon is chosen by this

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

def hardest_hit(kinds):
    """The largest hit among these mob kinds (0 for none known)."""
    return max((float(MOBS[k]["attack"]) for k in kinds if k in MOBS), default=0.0)

def protection(armor_points, shield=False, *, hit, toughness=0.0):
    """Fraction of a hit of `hit` removed: the game's armour formula, and a shield in hand."""
    return min(PLAYER["protection_cap"],
               formulas.armor_reduction(armor_points, toughness, hit) + (PLAYER["shield"] if shield else 0.0))

def slot_cost_s(bag_free):
    """Seconds one more occupied inventory slot costs, given how many are still free."""

    free = max(1.0, float(bag_free))
    return float(CONFIG["plan"]["slot_fill_s"]) / (free * free)

