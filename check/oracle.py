"""The oracle: one function per invariant of docs/refactor.md (S1–S6, D1–D7, E1–E3, P1, R1–R5), each
fn(before, d, after, ctx) -> None (holds) | str (why not). An invariant no offline fact can judge returns
UNCHECKED(why): said, never silently passed. Rules and thresholds come from production."""
from .facts import PENDING, night_ways

SHELTER_WAYS = (*night_ways(), "shelter")         # the night ways (SOURCES) and the maintain row running them
DANGER_LAYERS = ("reflex", "safety", "tactic")
GIVING = ("gather", "mine", "craft", "smelt", "hunt", "take", "withdraw", "build", "farm", "fill", "trade", "look")


class Unchecked(str):
    """An invariant this run cannot judge offline (its fact has no production source, or it is live-only)."""


def _name(d):
    return (d.name or "").lower()


def S1(b, d, a, ctx):
    """Danger preempts: a threat that reaches us or can shoot us (ctx["pressed"]: the threat model's pressure and blast
    on the state the threat layer saw — R5's threat), or critical health."""
    danger = (b["threat"] and ctx.get("pressed", True)) or b["hp"] == "crit"
    if danger and not b["takeover"] and d.layer not in DANGER_LAYERS:
        return f"danger ({'threat' if b['threat'] else 'critical hp'}) answered by layer {d.layer}: {d.name}"
    return None


def S2(b, d, a, ctx):
    if b["place"] == "home" and ctx.get("step_kind") in ("mine", "gather") and ctx.get("target_in_home"):
        if not (b["hp"] == "crit" and d.layer == "safety"):
            return f"a home block planned to be broken: {d.name} at {d.target}"
    return None


def S3(b, d, a, ctx):
    if b["place"] == "home" and ctx.get("step_kind") in ("shelter",) and "pod" in _name(d):
        return f"a pod built inside the home: {d.name}"
    return None


def S4(b, d, a, ctx):
    """Open air is the sky over the site (the place fact), never the step's kind: a craft, a mine or a smelt under
    the open night sky is open-air work (data.NIGHT_WORK is not cover)."""
    if not (b["night"] and b["place"] == "open" and b["dimension"] == "minecraft:overworld") or b["takeover"]:
        return None
    # exempt by layer, never by name; prep is exempt only as the shelter row's own night way
    if d.layer in DANGER_LAYERS or "sleep" in _name(d):
        return None
    if d.layer == "maintain" and _name(d) == "shelter":
        prep = ctx.get("step_kind") not in (None, "shelter")
        if not prep or (ctx.get("step_kind"), d.token) in ctx.get("night_steps", ()):
            return None
        return f"open-air at night: the shelter row runs {ctx.get('step_kind')} {d.token}, not a step of its way"
    if any(w in _name(d) for w in SHELTER_WAYS if w != "shelter") or ctx.get("step_kind") == "shelter":
        return None
    if d.kind is None:
        return "idle in the open at night (nothing proposed)"
    return f"open-air at night: {d.name} ({ctx.get('step_kind')})"


def S5(b, d, a, ctx):
    """An optional (goal) fight only above the hp line; threat fights are TACTIC's and not limited."""
    if ctx.get("fight_line"):
        return f"an optional fight chosen below the line: {d.name} ({ctx['fight_line']})"
    return None


def S6(b, d, a, ctx):
    """The body: what the round wrote. A decision taken while the player holds the body is not a breach — the jar
    refuses work then (Agent.requireNotPaused) and reflexes stop (audit K3/S6 holds)."""
    if b["takeover"] and d.writes:
        return f"the player holds the body, yet the round wrote {d.writes}"
    return None


def D1(b, d, a, ctx):
    if d.kind is None and not b["takeover"] and not d.reason:
        return "nothing proposed and no reason stated"
    return None


def D2(b, d, a, ctx):
    return None        # judged on the graph (D7)


def D3(b, d, a, ctx):
    if d.writes:
        return f"the decision wrote while deciding: {d.writes}"
    return None


def D4(b, d, a, ctx):
    return Unchecked(PENDING["F1"])


def D5(b, d, a, ctx):
    if ctx.get("reselected") and d.kind is not None:
        return f"reselected after failing (its key does not cool): {d.name}"
    return None


def D6(b, d, a, ctx):
    return Unchecked(PENDING["F1"])


def D7(b, d, a, ctx):
    return None        # judged on the graph (explore.cycles)


def E1(b, d, a, ctx):
    return Unchecked("live only (§C): executed tasks and the jar's records")


E2 = E3 = P1 = E1


def R1(b, d, a, ctx):
    return Unchecked(PENDING["F1"])


def R2(b, d, a, ctx):
    return Unchecked(PENDING["F1"])


def R3(b, d, a, ctx):
    if not (b["night"] and b["dimension"] == "minecraft:overworld") or b["takeover"] or d.layer in DANGER_LAYERS:
        return None
    if b["bed"] in ("carried", "home") and b["place"] != "enclosed" and "sleep" not in _name(d) \
            and "home" not in _name(d):
        return f"a bed to sleep in ({b['bed']}) and the night spent another way: {d.name}"
    way = ctx.get("night_way")
    # the maintain row "shelter" runs the night's cheapest way itself (reflexes.Maintain: view["night_way"])
    if way and not ("sleep" in _name(d) or way in _name(d) or _name(d) == "shelter"
                    or ctx.get("step_kind") not in (None, "shelter")):
        return f"the night's cheapest way is {way!r}, chosen: {d.name}"
    return None


def R4(b, d, a, ctx):
    return Unchecked(PENDING["F1"])


def R5(b, d, a, ctx):
    if d.layer == "tactic" and d.token in ("fight", "fight_shielded", "shoot") and not b["threat"]:
        return f"a fight with no threat and no goal target: {d.token}"
    return None


CHECKS = {k: globals()[k] for k in ("S1", "S2", "S3", "S4", "S5", "S6", "D1", "D2", "D3", "D4", "D5", "D6", "D7",
                                    "E1", "E2", "E3", "P1", "R1", "R2", "R3", "R4", "R5")}
from .inv import CHECKS as _MORE  # noqa: E402  (check/inv: a family's invariants, each in its own module)
CHECKS.update(_MORE)


def violations(before, d, after, ctx):
    """[(id, message)] for every invariant that does not hold (Unchecked ones apart: `unchecked`)."""
    out = []
    for k, fn in CHECKS.items():
        got = fn(before, d, after, ctx)
        if got is not None and not isinstance(got, Unchecked):
            out.append((k, got))
    return out


def unchecked(before, d):
    """{id: why} of the invariants this run cannot judge (asked on one real step)."""
    return {k: str(v) for k, fn in CHECKS.items() if isinstance(v := fn(before, d, before, {}), Unchecked)}
