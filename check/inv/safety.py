"""S7: a hazard mid-plan stops the search at once and the round answers it (ctx hazard: check.round.hazard_round)."""
from ..oracle import DANGER_LAYERS, Unchecked

STOP_EVERY = 1       # search steps between two reads of the stop


def S7(b, d, a, ctx):
    """Danger to the rescue's start within a perception cycle and one round: planning stops, the round answers it."""
    if "hazard" not in ctx:
        return Unchecked("no round with a hazard mid-plan decided for this state")
    if ctx["hazard"] is None:
        return None
    layer, after = ctx["hazard"]
    if after > STOP_EVERY:
        return f"the search ran {after} steps after the hazard (stop read every {STOP_EVERY})"
    if layer not in DANGER_LAYERS:
        return f"a hazard mid-plan answered by the {layer} layer, not a danger layer"
    return None


CHECKS = {"S7": S7}
