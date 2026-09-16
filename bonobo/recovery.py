"""The recovery table: trigger in, fixed action out. One place, with a default that always answers.

Recoveries used to live as an if/elif chain inside each fight skill. Two things went wrong with that, both of them
fatal in the literal sense: a trigger nobody had thought of fell through the chain and the agent stood still while it
was hit, and the same trigger got a different answer in each skill that handled it.

So: one table, one lookup, and an `else` that is never "do nothing". Every entry is a fixed action — decided in
advance, executed on sight, not scored against alternatives. Deciding what to do about a dragon's head arriving is
not something to do while it arrives.
"""

# Triggers, most specific first. Each is (substring of the interrupt reason, action name, why).
# The order matters: an interrupt can mention more than one thing, and the first match wins.
# Keyed on perception's danger KIND, matched exactly. (kind, action, why)
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

# What to do when nothing matches. Not "carry on": an unrecognised danger is still a danger, and the cheapest correct
# answer to every one of them is the corridor.
DEFAULT = "retreat_to_cover"


def _kind(reason):
    """The danger kind in a message: exact kind, or the kind a prefixed message ("claude: breath") ends with."""
    text = (reason or "").strip().lower()
    return text.split(":")[-1].strip() if ":" in text else text


def recovery_for(reason):
    """Pure: the action name for a danger kind. Always returns something."""
    k = _kind(reason)
    for kind, act, _ in TABLE:
        if kind == k:
            return act
    return DEFAULT


def explain(reason):
    """Pure: (action, why) — the reason is logged so a wrong table entry is visible in the run, not just its effect."""
    k = _kind(reason)
    for kind, act, why in TABLE:
        if kind == k:
            return act, why
    return DEFAULT, "unrecognised danger: cover first, diagnose afterwards"


# Abort conditions. Every fight action declares when to give up on it and what to do instead — the half that was
# missing from the skill contracts, which could say "this took too long" but never "and now do this".
ABORTS = {
    "dig_tunnel": [("health below the floor", "retreat_and_eat"),
                   ("dragon perched", "retreat_to_cover"),
                   ("breath within 6", "retreat_to_cover")],
    "place_bed": [("dragon perched", "retreat_to_cover"),
                  ("bed cell occupied", "abandon")],
    "reinforce": [("dragon perched", "retreat_to_cover"),
                  ("out of obsidian", "abandon")],
    "shoot_crystal": [("enderman in the line of aim", "abandon"),
                      ("dragon perched", "retreat_to_cover")],
    # The window is open-loop on purpose: 0.4 s is shorter than one perception round trip (98 ms measured, and that
    # is the best case), so there is nothing to abort into partway through. It either starts or it does not.
    "fire_window": [],
}


def aborts_for(action_name):
    """Pure: [(condition, action)] for an action. An empty list means the action is atomic and cannot be aborted."""
    return ABORTS.get(action_name, [])
