"""D8: the cold round and the warm round (ctx warm: check.round.warm_name) decide alike."""
from ..oracle import Unchecked


def D8(b, d, a, ctx):
    """Same inputs, same decision: the cold round's choice is the warm round's."""
    if "warm" not in ctx:
        return Unchecked("no warm round decided for this state")
    if ctx["warm"] != d.name:
        return f"cold chose {d.name}, warm chose {ctx['warm']}: a cached value is not the recomputed one"
    return None


CHECKS = {"D8": D8}
