"""The plan's invariants over the planner's own output: the candidates it priced, the steps the round holds for its
task (brain.held), each step's price (cost.Cost.estimate, the production model), the ways to a mine target
(nav.plan_way against the game's walks), and the threat layer's switches (kernel.switches, recorded as the round
asked them). check/round.py puts them in ctx:
  plan       [Step] the task's held plan this round (None: the act is not the queue's)
  candidates [(name, seconds, steps)] planner.plan_candidates for the task's needs, the chosen first
  price      Step -> ticks: the production cost model on this state (Cost.estimate)
  inv, mem, dimension, feet    the round's bag and memory
  task_goal  the task's goal (a tool it asks for is wanted, not a detour)
  way        (chosen_s, dug_s, walked_s) to the decision's mine target, or None
  switches   [(fresh, staying, lost, noise, switched)] every switch the kernel weighed this round
  holds      [(held name, chosen name, because)] every kernel.Held decision this round
  plan_hand_made  the held plan is check/dims/plan_held's hand-made one (P2 does not judge it)
An invariant whose ctx is missing is Unchecked, said why."""
import math

from ..oracle import Unchecked

TOL_TICKS = 1            # prices are whole ticks: one either way
TOL_S = 0.05             # seconds compared after rounding
MATERIAL = ("gather", "mine", "hunt")     # steps that make a material in the world (a container's alternative)
MAKES = MATERIAL + ("craft", "smelt", "take", "withdraw", "await", "fill", "trade", "farm")   # steps whose token is gained


def _plan(ctx):
    plan = ctx.get("plan")
    return None if plan is None else list(plan)


def _tier(steps):
    """The highest tool tier a plan's steps call for (-1: none)."""
    from bonobo.knowledge import step_call
    return max((int(n.split(":")[2]) for st in steps for n in step_call(st) if n.startswith("tool:")), default=-1)


def _cheapest(ctx):
    """The plan chosen is the fewest seconds of the candidates the search priced, a tie the lowest tier — or why
    not; None without candidates."""
    found = ctx.get("candidates")
    if not found:
        return None
    name, seconds, steps = found[0]
    for other, s, st in found[1:]:
        if s + TOL_S < seconds or (abs(s - seconds) <= TOL_S and _tier(st) < _tier(steps)):
            return f"chose {name} ({seconds:.2f} s, tier {_tier(steps)}) over {other} ({s:.2f} s, tier {_tier(st)})"
    return None


def D6(b, d, a, ctx):
    """The estimate is the planned steps: the chosen candidate's seconds are its steps' prices summed, each step's
    est is what the cost model prices that step at now, and no step that works is free (an unpriced step is hidden
    work)."""
    plan, price = _plan(ctx), ctx.get("price")
    if ctx.get("candidates") and price is not None:
        from bonobo.beliefs import TICKS_PER_S
        name, seconds, steps = ctx["candidates"][0]
        summed = sum(price(st) for st in steps) / TICKS_PER_S
        if abs(seconds - summed) > TOL_S + TOL_TICKS * len(steps) / TICKS_PER_S:
            return f"chose {name} at {seconds:.2f} s, its steps priced {summed:.2f} s"
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
    holds working at that tier or better, unless the goal asks for it; the plan chosen is the cheapest candidate
    (_cheapest)."""
    why = _cheapest(ctx)
    if why:
        return why
    plan, inv = _plan(ctx), ctx.get("inv")
    if plan is None or inv is None:
        return Unchecked("no held plan this round (the act is not the queue's)")
    from bonobo.data import TIER_OF_MATERIAL, TOOL_KINDS, bare
    from bonobo.knowledge import held_tiers, spare_uses
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
        left = sum(spare_uses(d) for t, d, _ in inv.tools(kind) if t >= tier)
        if _breaks(plan, kind) > left:
            continue
        return f"crafts {name} while a {kind} of tier {held[kind]} is carried and works"
    return None


def _breaks(plan, kind):
    """Uses of a `kind` tool the plan's work spends (mine steps' breaks, as the solver's uses row counts them)."""
    from bonobo.knowledge import tool_kind
    return sum(int(st.detail.get("breaks", st.count)) for st in plan
               if st.kind == "mine" and tool_kind((st.detail.get("blocks") or [""])[0]) == kind)


def R2(b, d, a, ctx):
    """Materials by seconds: a material the plan makes (gather, mine, hunt) while a container here holds it is made
    only when making it is not slower than taking it (the walk to the container and the take, priced by the model); the plan chosen is the cheapest candidate."""
    why = _cheapest(ctx)
    if why:
        return why
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
    """The way to a target by seconds: the plan chosen is the cheapest candidate, and the way taken is no slower than
    the cheapest of the dug ways and the game's walks."""
    why = _cheapest(ctx)
    if why:
        return why
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


def _station(st):
    """The station a step works at, or None."""
    if st.kind == "smelt":
        return "minecraft:furnace"
    if st.kind == "craft":
        from bonobo.knowledge import source
        src = source(st.token)
        if src and src[0] == "craft" and len(src[1]) == 9:
            return "minecraft:crafting_table"
    return None


def P2(b, d, a, ctx):
    """The plan runs in its order: each step's inputs, tools and station are there when it starts — in the bag, made
    by an earlier step, or (a station) remembered in this dimension."""
    plan, inv = _plan(ctx), ctx.get("inv")
    if plan is None or inv is None:
        return Unchecked("no held plan this round (the act is not the queue's)")
    if ctx.get("plan_hand_made"):
        return Unchecked("the held plan is the plan_held dimension's hand-made one, not the planner's")
    from collections import Counter
    from bonobo.data import TIER_OF_MATERIAL, bare, mid
    from bonobo.knowledge import members, step_call, tool_ok
    ids = lambda tok: {mid(m) for m in (tok, *members(tok))}                        # noqa: E731
    made, used, tools = Counter(), Counter(), {}

    def have(tok):
        own = ids(tok)
        return (inv.count(tok) + sum(n for k, n in made.items() if ids(k) & own)
                - sum(n for k, n in used.items() if ids(k) & own))

    mem, dim = ctx.get("mem"), ctx.get("dimension")
    for i, st in enumerate(plan):
        for tok, n in st.detail.get("inputs", {}).items():
            if have(tok) < n:
                return f"step {i + 1} {st} needs {n} {bare(tok)}, {max(0, have(tok))} there by then"
            used[tok] += n
        for need in step_call(st):
            if need.startswith("tool:"):
                _, kind, tier = need.split(":")
                if not tool_ok(inv, kind, int(tier)) and tools.get(kind, -1) < int(tier):
                    return f"step {i + 1} {st} needs a tier-{tier} {kind}, none held or made before it"
        station = _station(st)
        if station and have(station) <= 0 and (mem is None or not any(
                s.get("block") in (station, bare(station)) for s in mem.stations(dim))):
            return f"step {i + 1} {st} works at a {bare(station)}, none held, made before it or remembered"
        if st.kind in MAKES:
            made[st.token] += int(st.count)
        material, _, kind = bare(st.token).rpartition("_")
        if st.kind == "craft" and material in TIER_OF_MATERIAL:
            tools[kind] = max(tools.get(kind, -1), TIER_OF_MATERIAL[material])
    return None


CHECKS = {"D4": D4, "D6": D6, "P2": P2, "R1": R1, "R2": R2, "R4": R4}
