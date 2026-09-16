"""The pool's veto layer, pure: what may be offered this round, and why not.

`brain.candidates` used to decide admission, step gating, goal-level reasons, scoring and logging in one 250-line
loop. The refusals were f-strings assigned into a dict as they were discovered, so the only way to know why a goal
was missing was to read the loop. This module holds the refusing half: functions of (candidate, context) that
return a Refusal or None, and never score. Scoring stays in priority.Candidate; orchestration stays in brain.

Refusal texts are kept verbatim — the decision tape and the golden replays compare them.
"""


class Refusal:
    """Why something was not offered. `kind` is stable for code; `text` is what the log and the tape carry."""

    __slots__ = ("kind", "text")

    def __init__(self, kind, text):
        self.kind, self.text = kind, text

    def __str__(self):
        return self.text

    def __eq__(self, other):
        return str(other) == self.text

    def __hash__(self):
        return hash(self.text)

    def __repr__(self):
        return f"Refusal({self.kind}, {self.text!r})"


# What being off the speedrun's route costs: seconds the run does not get back. A number, so a side errand that is
# genuinely worth more can still win; a wall, and the route becomes a rail.
OFF_ROUTE_S = 120.0

# Errands that walk away from the spot a failed goal is about to retry on.
WANDERING = {"deposit", "open space", "explore", "strip mine", "light up"}


class Context:
    """Everything admission and gating read. Plain data, built once per round by the brain."""

    def __init__(self, *, segment, seg_name, seg_goals, bench_failing, staying, committed, weights, ready,
                 force, seg_misses, seg_misses_limit, retry, sig, night, night_capable, snap, has_pickaxe,
                 sightings, way_into=None, no_go=(), sheltered=False, place=None):
        self.segment, self.seg_name, self.seg_goals = segment, seg_name, seg_goals
        self.bench_failing, self.staying, self.committed = bench_failing, staying, committed
        self.weights, self.ready, self.force = weights, ready, force
        self.seg_misses, self.seg_misses_limit = seg_misses, seg_misses_limit
        self.retry, self.sig = retry, sig
        self.night, self.night_capable, self.snap = night, night_capable, snap
        self.has_pickaxe, self.sightings = has_pickaxe, sightings
        self.way_into = way_into          # dim -> why we cannot get there yet, or None (memory only, no world call)
        # Places a step may not be sent to: [(centre, radius)] around what can hurt us. The threat layer decides
        # what counts; the pool only refuses to plan work inside one.
        self.no_go = list(no_go)
        self.sheltered = sheltered        # a roof we can reach: whether running into the dark is survivable
        self.place = place                # coarse location: what "failed here before" is counted against


def admit(c, ctx, weight_for, allowed, exhausted_after):
    """Pure: the Refusal that keeps candidate `c` out of the pool this round, or None (and `c.weight` is set).

    Order matters and is the old order: route, bench, staying, ban, cooling, segment misses, exhausted. A banned
    fallback used to be resurrected because "staying" answered before "banned" — the brain asks the ban itself
    before undoing a hold, and this function keeps the same sequence so the tape reads the same.
    """
    w, banned = weight_for(c.name, ctx.weights)
    seg = ctx.segment
    if seg is not None and c.kind == "goal" and not allowed(seg, c.name) and not getattr(c, "craft_only", False):
        # Being off the route is a detour, not an impossibility: what it costs is the time it does not spend on
        # the run. Charged, the route still wins every time it matters, and a genuinely valuable side errand can
        # still win — which is the difference between a plan and a rail.
        c.off_route_s = float(ctx.weights.get("_off_route_s", 0.0) or OFF_ROUTE_S)
    if c.name in ctx.bench_failing and ctx.seg_goals - ctx.bench_failing - {c.name}:
        return Refusal("bench", "its bench scenario fails for this code; another segment goal first")
    if ctx.staying and c.name in WANDERING:
        return Refusal("staying", f"staying at {ctx.committed} while it retries")
    if banned:
        return Refusal("banned", "banned by Claude")
    # What the skill itself says about whether it can run. Asked here, before the work is priced and chosen, and
    # NOT waived by `force`: the idle rule exists to thaw things that are merely cooling, not to make the
    # impossible possible. Without this the pool offered "light up" with no torches ninety times in four minutes.
    precheck = getattr(c, "precheck", None)
    if precheck is not None:
        ok, why = precheck()
        if not ok:
            return Refusal("unavailable", why or "its preconditions do not hold")
    if not ctx.ready(c.key) and not (ctx.force and c.kind == "fallback"):
        return Refusal("cooling", "cooling after a failure")
    # The same step, cooling for whoever wants it. Without this the backstop was per goal, so the goals queued up
    # behind one unreachable furnace and each got its own turn to fail at it.
    if "/" in c.key and not ctx.ready("step:" + c.key.split("/", 1)[1]) \
            and not (ctx.force and c.kind == "fallback"):
        return Refusal("cooling", "cooling after a failure")
    if c.kind == "goal" and ctx.seg_misses.get((ctx.seg_name, c.name), 0) >= ctx.seg_misses_limit:
        return Refusal("segment_misses",
                       f"nothing of this kind found in segment '{ctx.seg_name}' ({ctx.seg_misses_limit}×)")
    # A step that cannot be done here cannot be done here for anyone. Goal keys are "<goal>/<step>", so each goal
    # counted that step's failures separately and they took turns discovering the same wall — six rounds, six
    # goals, one unreachable furnace. The shared "step:<kind>:<token>" key is already recorded; this reads it.
    if c.kind != "fallback" and "/" in c.key:
        shared = "step:" + c.key.split("/", 1)[1]
        if ctx.retry.exhausted(shared, ctx.sig, ctx.place):
            return Refusal("exhausted", f"exhausted here ({shared}); needs another method or a changed state")
    if c.kind != "fallback" and ctx.retry.exhausted(c.key, ctx.sig, ctx.place):
        return Refusal("exhausted", f"exhausted here ({c.key}); needs another method or a changed state")
    c.weight = w
    return None


def gate_step(step, goal, plan, ctx, underground_kinds, escape_ready, bare):
    """Pure: why this STEP cannot run now, or None. Rejects the step, never the goal."""
    inv = ctx.snap.inv
    if ctx.night and step.kind not in underground_kinds and not ctx.night_capable:
        return f"{step.kind} waits for day"
    if goal.background and step.kind == "mine" and not escape_ready(inv):
        return "no escape kit for digging"
    # Admissibility, not preference: a plan that cannot finish before dark, with no bed to end the night and no
    # shelter to reach, is a plan to be caught outside. It used to be checked only for gathering and hunting, and
    # only when there was no pickaxe — so every other kind of work walked into the night unexamined.
    if not ctx.night and not inv.count("bed") and not ctx.sheltered and not ctx.has_pickaxe \
            and sum(x.est for x in plan) > ctx.snap.ticks_until_dusk:
        return "would run into the night with no bed, no shelter and nothing to dig with"
    # A step whose destination sits inside something that can hurt us is refused outright. Damage on the way is
    # priced (LiveCost.risk_s); standing in the fire is not a price, it is a mistake.
    where = step.detail.get("pos")
    if where and ctx.no_go:
        import math
        for centre, radius in ctx.no_go:
            if math.dist(where, centre) <= radius:
                return f"{step.kind} would put us inside a threat's reach"
    if step.kind == "hunt" and ctx.snap.dimension == "minecraft:overworld" and ctx.segment is not None:
        kinds = step.detail.get("types", [])
        if kinds and not any(ctx.sightings(k, ctx.snap.dimension) for k in kinds):
            return f"no {bare(kinds[0])} seen yet"
    return None


def pick_step(plan, runnable_steps, prep_tokens, gate):
    """Pure: (step, reason) — the first runnable step of the goal's own work that passes the gate.

    A gated step must not hide the other work the goal can do; jumping past a FAILED step is still forbidden.
    Look-ahead preparation (torches, a spare pickaxe) may run but is not progress.
    """
    own = [s for s in runnable_steps if s.token not in prep_tokens]
    step, reason = None, None
    for cand in own:
        why = gate(cand)
        if why is None:
            return cand, None
        if step is None:
            step, reason = cand, why
    if plan and not runnable_steps:
        return None, "no runnable step"
    if runnable_steps and not own:
        return runnable_steps[0], "only look-ahead preparation can run"
    return step, reason


def goal_reason(goal, plan, ctx, nether_kit_missing, nether_dim, nether_mobs):
    """Pure: goal-level reasons that are not about a step — feasibility, the Nether kit, a Nether dependency."""
    if goal.feasible:
        why = goal.feasible()
        if why:
            return why
    if goal.dimension != ctx.snap.dimension and ctx.way_into is not None:
        # "Going through the portal is this goal's next step" is only a step when a portal is known. Without this
        # the dragon goal was picked ten times in the Overworld with no End portal in memory, failed, cooled, and
        # came back — a goal that can never start must never be offered.
        why = ctx.way_into(goal.dimension)
        if why:
            return why
    if goal.dimension == nether_dim and ctx.snap.dimension != goal.dimension:
        kit = nether_kit_missing(ctx.snap.inv)
        if kit:
            return "Nether kit not ready: " + ", ".join(kit)
    if ctx.snap.dimension != nether_dim and goal.dimension != nether_dim and any(
            s.kind == "hunt" and set(s.detail.get("types", [])) & nether_mobs for s in plan):
        return "its plan needs Nether mobs (blaze rods): the blaze-rod goal comes first"
    return None
