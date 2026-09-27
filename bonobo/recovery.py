"""The recovery table: trigger in, fixed action out. One place, with a default that always answers. Recoveries used to live as an if/elif chain inside each fight skill. Two things went wrong with that, both of them fatal in the literal sense: a trigger nobody had thought of fell through the chain and the agent stood still while it was hit, and the same trigger got a different answer in each skill that handled it. So: one table, one lookup, and an `else` that is never "do nothing". Every entry is a fixed action — decided in advance, executed on sight, not scored against alternatives. Deciding what to do about a dragon's head arriving is not something to do while it arrives."""

# (kind, action, why), matched exactly on perception's danger kind; first match wins
TABLE = [
    ("enderman", "shake_enderman",
     "never trade hits: water or distance breaks the aggro, swinging back starts a second fight"),
    ("breath", "retreat_to_cover",
     "clouds pool at the mouth and spread along the floor; the corridor is the only place they do not reach"),
    ("airborne", "water_clutch",
     "flung by a take-off: pathing does nothing in mid-air, and the fall is what kills, not the hit"),
    ("critical_health", "retreat_and_eat",
     "below the floor nothing sprints or regenerates, so the next hit is the last one"),
    ("hostiles", "retreat_to_cover", "anything hostile within reach is answered from inside cover"),
    ("stale", "retreat_to_cover",
     "perception older than the reaction window is not perception; treat blindness as danger"),
]

# an unrecognised danger is still a danger: the corridor answers them all
DEFAULT = "retreat_to_cover"

def _kind(reason):
    """The danger kind in a message: exact kind, or the kind a prefixed message ("claude: breath") ends with."""
    text = (reason or "").strip().lower()
    return text.split(":")[-1].strip() if ":" in text else text

def explain(reason):
    """Pure: (action, why) — the reason is logged so a wrong table entry is visible in the run, not just its effect."""
    k = _kind(reason)
    for kind, act, why in TABLE:
        if kind == k:
            return act, why
    return DEFAULT, "unrecognised danger: cover first, diagnose afterwards"
