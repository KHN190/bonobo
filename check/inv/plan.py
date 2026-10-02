"""The plan's invariants over the current planner's own output (no F1 candidates): the steps the round holds for its
task (brain.held), each step's price (cost.Cost.estimate, the production model), the ways to a mine target
(nav.plan_way against the game's walks), and the threat layer's switches (kernel.switches, recorded as the round
asked them). check/round.py puts them in ctx:
  plan       [Step] the task's held plan this round (None: the act is not the queue's)
  price      Step -> ticks: the production cost model on this state (Cost.estimate)
  inv, mem, dimension, feet    the round's bag and memory
  task_goal  the task's goal (a tool it asks for is wanted, not a detour)
  way        (chosen_s, dug_s, walked_s) to the decision's mine target, or None
  switches   [(fresh, staying, lost, noise, switched)] every switch the kernel weighed this round
  holds      [(held name, chosen name, because)] every kernel.Held decision this round
An invariant whose ctx is missing is Unchecked, said why."""
import math

from ..oracle import Unchecked

TOL_TICKS = 1            # prices are whole ticks: one either way
TOL_S = 0.05             # seconds compared after rounding
MATERIAL = ("gather", "mine", "hunt")     # steps that make a material in the world (a container's alternative)


def _plan(ctx):
    plan = ctx.get("plan")
    return None if plan is None else list(plan)


def D6(b, d, a, ctx):
    """The estimate is the planned steps: each step's est is what the cost model prices that step at now, and no
    step that works is free (an unpriced step is hidden work)."""
    plan, price = _plan(ctx), ctx.get("price")
    if plan is None or price is None:
        return Unchecked("no held plan this round (the act is not the queue's)")
    for st in plan:
        now = price(st)
        if st.kind not in ("wait",) and now > 0 and int(getattr(st, "est", 0) or 0) <= 0:
            return f"{st} carries no price (est 0) though the model prices it {now} ticks: hidden work"
        if abs(int(getattr(st, "est", 0) or 0) - int(now)) > TOL_TICKS:
            return f"{st} planned at {st.est} ticks, the model prices it {now} now"
    return None


def _tool_wanted(goal, kind, inv):
    """Tiers of tool `kind` the goal itself asks for (wanted, never a detour)."""
    if not goal:
        return set()
    from bonobo import goals
    return {int(r[2]) for r in goals.needs(goal, inv) if len(r) == 3 and r[0] == "tool" and r[1] == kind}


def R1(b, d, a, ctx):
    """Tool material: a tool carried that works is used — a plan does not craft a tool of a kind the bag already
    holds working at that tier or better, unless the goal asks for it."""
    plan, inv = _plan(ctx), ctx.get("inv")
    if plan is None or inv is None:
        return Unchecked("no held plan this round (the act is not the queue's)")
    from bonobo.data import TIER_OF_MATERIAL, TOOL_KINDS, bare
    from bonobo.knowledge import held_tiers
    held = held_tiers(inv)
    for st in plan:
        if st.kind != "craft":
            continue
        name = bare(st.token)
        material, _, kind = name.rpartition("_")
        if kind not in TOOL_KINDS:
            continue
        tier = TIER_OF_MATERIAL.get(material)
        if tier is None or held.get(kind, -1) < tier or tier in _tool_wanted(ctx.get("task_goal"), kind, inv):
            continue
        return f"crafts {name} while a {kind} of tier {held[kind]} is carried and works"
    return None


def R2(b, d, a, ctx):
    """Materials by seconds: a material the plan makes (gather, mine, hunt) while a container here holds it is made
    only when making it is not slower than taking it (the walk to the container and the take, priced by the model)."""
    plan, price, mem = _plan(ctx), ctx.get("price"), ctx.get("mem")
    if plan is None or price is None or mem is None:
        return Unchecked("no held plan this round (the act is not the queue's)")
    from bonobo.planner import Step
    taken = {}                       # what the plan itself withdraws, by container and item: no longer there to take
    for st in plan:
        if st.kind == "withdraw":
            key = (tuple(st.detail.get("pos") or ()), st.token)
            taken[key] = taken.get(key, 0) + int(st.count)
    for st in plan:
        if st.kind not in MATERIAL:
            continue
        stored = [(pos, item, have - taken.get((tuple(pos), item), 0) - taken.get((tuple(pos), item.removeprefix("minecraft:")), 0))
                  for pos, item, have in mem.stored(st.token, ctx["dimension"])]
        stored = [r for r in stored if r[2] > 0]
        if not stored:
            continue
        pos, item, have = min(stored, key=lambda r: math.dist(r[0], ctx["feet"]))
        take = Step("withdraw", item, min(have, max(1, int(st.count))), {"pos": list(pos)})
        take_t, make_t = price(take), int(getattr(st, "est", 0) or price(st))
        if take_t + TOL_TICKS < make_t:
            return (f"{st} ({make_t} ticks) while {pos} holds {have} {item} (taking: {take_t} ticks)")
    return None


def R4(b, d, a, ctx):
    """The way to a target by seconds: the way taken is no slower than the cheapest of the dug ways and the game's
    walks."""
    way = ctx.get("way")
    if way is None:
        return Unchecked("no mine target this round")
    chosen, dug, walked = way
    best = min((s for s in (dug, walked) if s is not None), default=None)
    if best is not None and (chosen is None or chosen > best + TOL_S):
        return f"the way taken costs {chosen} s, the cheapest way {best:.2f} s (dug {dug}, walked {walked})"
    return None


def D4(b, d, a, ctx):
    """A switch pays (same layer: the threat layer's kernel.Held): a held answer is given up for a new one only when
    the new one's gain over what is left of the held one beats the work thrown away and the estimates' noise — or
    its assumption broke. Every Held decision that changed answers is matched to a switch the kernel weighed."""
    sw, holds = ctx.get("switches") or [], ctx.get("holds") or []
    if not sw and not holds:
        return Unchecked("no held answer weighed against a new one this round")
    for fresh, staying, lost, noise, switched in sw:
        if switched != (fresh - staying > lost + noise):
            return f"switch {'taken' if switched else 'refused'} against its own numbers: gain {fresh - staying:.1f} s, " \
                   f"thrown away {lost:.1f} s + noise {noise:.1f} s"
    paid = sum(1 for *_x, switched in sw if switched)
    changed = [(h, c, why) for h, c, why in holds if h is not None and c != h and why not in ("assumption", "denied")]
    if len(changed) > paid:
        h, c, _why = changed[0]
        return f"gave up {h} for {c} with no switch that pays"
    return None


CHECKS = {"D4": D4, "D6": D6, "R1": R1, "R2": R2, "R4": R4}
