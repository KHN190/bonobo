"""D8: a decision is a pure function of its inputs (snapshot, memory, statistics): the round decided cold and the same
round decided with the process's caches already filled by another state agree. check/explore.judge puts in ctx:
  warm       the decision's name with the caches warm (check.round.warm_name)"""
from ..oracle import Unchecked


def D8(b, d, a, ctx):
    """Same inputs, same decision: the cold round's choice is the warm round's."""
    if "warm" not in ctx:
        return Unchecked("no warm round decided for this state")
    if ctx["warm"] != d.name:
        return f"cold chose {d.name}, warm chose {ctx['warm']}: a cached value is not the recomputed one"
    return None


CHECKS = {"D8": D8}
