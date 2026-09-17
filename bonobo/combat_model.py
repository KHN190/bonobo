"""What a combat tape means. Pure functions only — they read frames, never the game.

The rule this module exists to serve: don't learn "where is safe", learn "how long until I am hit". Every hazard is
reduced to the same triple so the controller compares a breath cloud and an enderman with one number:

    kind        what it is (the entity id), for the damage model only
    tti         seconds until it reaches us if nothing changes (inf = it never does)
    region      (x, y, z, radius) it will occupy — where not to stand
    dps         health per second while inside it
    persistent  True if it stays put once formed (breath clouds), False if it must be re-aimed (head, enderman)

Everything is measured off the tape rather than tabulated: the numbers that matter (how fast breath spreads, how much
a head sweep takes) belong to this version of the game, not to a wiki page.
"""
import math

TICK = 0.05                 # seconds per tick
HORIZON = 4.0               # seconds ahead worth predicting; past that the dragon has re-decided anyway

# Dragon phase ids (vanilla EnderDragonPhase type ids). Only the sitting ones open an attack window.
LANDING = 3
SITTING_FLAMING = 5         # breath: the perch is lethal, the pit is not
SITTING_SCANNING = 6
SITTING_ATTACKING = 7
WINDOW_PHASES = {SITTING_SCANNING, SITTING_ATTACKING}


def _xyz(d):
    return (d["x"], d["y"], d["z"])


def phase_spans(frames):
    """[(phase, first_tick, last_tick)] — the phase timeline, runs collapsed. None phase = dragon absent."""
    spans = []
    for f in frames:
        d = f.get("dragon") or {}
        p = d.get("phase") if d.get("present") else None
        if spans and spans[-1][0] == p:
            spans[-1][2] = f["tick"]
        else:
            spans.append([p, f["tick"], f["tick"]])
    return [tuple(s) for s in spans]


def phase_stats(frames):
    """{phase: {"n", "mean_s", "min_s", "max_s"}} — how long each phase actually lasts in this version.

    The planner needs this and not a wiki number: the attack window's length is the whole budget for placing,
    detonating and getting back into the hole.
    """
    out = {}
    for phase, a, b in phase_spans(frames):
        secs = (b - a + 1) * TICK
        e = out.setdefault(phase, {"n": 0, "total": 0.0, "min_s": secs, "max_s": secs})
        e["n"] += 1
        e["total"] += secs
        e["min_s"] = min(e["min_s"], secs)
        e["max_s"] = max(e["max_s"], secs)
    for e in out.values():
        e["mean_s"] = round(e.pop("total") / e["n"], 2)
        e["min_s"], e["max_s"] = round(e["min_s"], 2), round(e["max_s"], 2)
    return out


def tti(pos, vel, centre, radius, horizon=HORIZON):
    """Pure: seconds until a point moving at `vel` (blocks per second) enters the sphere at `centre`, inf if it does
    not within `horizon`. Already inside → 0.0.

    This is the one number the controller compares. Solving it geometrically (rather than stepping a simulation)
    keeps it cheap enough to run every frame for every hazard.
    """
    dx = [pos[i] - centre[i] for i in range(3)]
    d0 = math.sqrt(sum(c * c for c in dx))
    if d0 <= radius:
        return 0.0
    vv = sum(v * v for v in vel)
    if vv < 1e-9:
        return float("inf")
    # |d + v t| = r  →  vv t² + 2(d·v) t + (d·d - r²) = 0
    b = 2 * sum(dx[i] * vel[i] for i in range(3))
    c = d0 * d0 - radius * radius
    disc = b * b - 4 * vv * c
    if disc < 0:
        return float("inf")
    root = math.sqrt(disc)
    hits = sorted(t for t in ((-b - root) / (2 * vv), (-b + root) / (2 * vv)) if t >= 0)
    if not hits or hits[0] > horizon:
        return float("inf")
    return round(hits[0], 3)


def velocity(frame, prev):
    """Player velocity in blocks per second (the frame stores a per-tick delta)."""
    v = frame["player"]["vel"]
    if prev is None:
        return (0.0, 0.0, 0.0)
    return (v["x"] / TICK, v["y"] / TICK, v["z"] / TICK)


# Reach of each hazard around its own position, and whether it stays where it formed. Radii are starting values fitted
# from tapes by `fit_damage`; the dragon's head is the one that killed the bench runs, so it is generous.
HAZARD = {
    "minecraft:area_effect_cloud": (3.0, True),
    "minecraft:dragon_fireball": (3.0, False),
    "minecraft:enderman": (3.0, False),
    "dragon_head": (6.0, False),
}
DEFAULT_DPS = {"minecraft:area_effect_cloud": 6.0, "dragon_head": 10.0, "minecraft:enderman": 7.0}


def threats(frame, prev=None, dps=None, horizon=HORIZON):
    """Pure: every hazard in this frame as (kind, tti, region, dps, persistent), soonest first.

    Small mobs get no special case on purpose: an enderman is a moving sphere with a dps, exactly like a breath cloud
    that happens to walk. The controller does not need to know what it is fighting, only when it will be hit.
    """
    dps = dps or DEFAULT_DPS
    p = frame["player"]
    pos, vel = _xyz(p["pos"]), velocity(frame, prev)
    out = []
    for c in frame.get("breath") or []:
        r, persistent = HAZARD.get(c["type"], (3.0, True))
        r = max(r, c.get("radius") or 0)
        centre = _xyz(c["pos"])
        out.append((c["type"], tti(pos, vel, centre, r, horizon), (*centre, r),
                    dps.get(c["type"], 6.0), persistent))
    for e in frame.get("endermen") or []:
        if not e.get("angry"):
            continue        # a neutral enderman is not a hazard at all; treating it as one starts the fight
        r, persistent = HAZARD["minecraft:enderman"]
        centre = _xyz(e["pos"])
        out.append(("minecraft:enderman", tti(pos, vel, centre, r, horizon), (*centre, r),
                    dps.get("minecraft:enderman", 7.0), persistent))
    d = frame.get("dragon") or {}
    if d.get("present") and d.get("head"):
        r, persistent = HAZARD["dragon_head"]
        centre = _xyz(d["head"])
        out.append(("dragon_head", tti(pos, vel, centre, r, horizon), (*centre, r),
                    dps.get("dragon_head", 10.0), persistent))
    return sorted(out, key=lambda t: t[1])


def exposure(frame, prev=None, dps=None):
    """Pure: health per second we are taking right now (sum of the dps of every hazard we are already inside)."""
    return sum(t[3] for t in threats(frame, prev, dps) if t[1] == 0.0)


def hits(frames):
    """[(tick, amount, nearest)] — every recorded damage event."""
    return [(f["tick"], d["amount"], d.get("nearest"))
            for f in frames for d in (f.get("damage") or [])]


def fit_damage(frames):
    """{kind: {"n", "mean", "max"}} — what each source actually took off us on this tape.

    Replaces guessed constants in DEFAULT_DPS: a fitted head sweep is the difference between a window that is worth
    entering and one that kills us.
    """
    out = {}
    for _, amount, nearest in hits(frames):
        e = out.setdefault(nearest or "unknown", {"n": 0, "total": 0.0, "max": 0.0})
        e["n"] += 1
        e["total"] += amount
        e["max"] = max(e["max"], amount)
    for e in out.values():
        e["mean"] = round(e.pop("total") / e["n"], 2)
    return out


def deaths(frames):
    """Ticks where health reached zero."""
    return [f["tick"] for f in frames if f["player"]["hp"] <= 0]


def step_options(frame, reach=4.0, step=1.0):
    """Pure: the positions a single committed step could reach — 8 compass directions plus standing still.

    Deliberately coarse. The controller chooses between "stay", "back off" and "into the hole", not between hundreds
    of micro-positions; a plan it cannot execute in one commitment is not an option.
    """
    x, y, z = _xyz(frame["player"]["pos"])
    out = [(x, y, z)]
    n = max(1, int(reach / step))
    for i in range(8):
        a = i * math.pi / 4
        dx, dz = math.cos(a), math.sin(a)
        for k in range(1, n + 1):
            out.append((x + dx * step * k, y, z + dz * step * k))
    return out


def hypotheses(hazard, here=None, closing=4.3):
    """Pure: plausible futures for one hazard, each a (centre, radius, velocity) row.

    continue   keeps its differenced velocity
    stop       stays where it is (a cloud settling, a mob hesitating)
    pursue     turns toward us at a pursuit speed — the case that matters for anything that hunts
    A hazard with no observed motion still gets `pursue`, because "it has not moved yet" is not "it cannot".
    """
    centre, radius = hazard[0], hazard[1]
    vel = hazard[2] if len(hazard) > 2 else (0.0, 0.0, 0.0)
    kind = hazard[3] if len(hazard) > 3 else None
    out = [(centre, radius, vel, kind), (centre, radius, (0.0, 0.0, 0.0), kind)]
    if here is not None and kind not in STATIC_KINDS:
        d = math.dist(here, centre)
        if d > 1e-9:
            out.append((centre, radius, tuple((here[i] - centre[i]) / d * closing for i in range(3)), kind))
    return out


# Hazards that do not chase: clouds and the perched head sit where they are. Everything else may come for us.
STATIC_KINDS = {"minecraft:area_effect_cloud", "dragon_head", "minecraft:ender_dragon"}


def expand(hazards, here=None):
    """Pure: every hypothesis of every hazard, flattened. Safety is computed over this, never over the raw list."""
    return [h for hz in hazards for h in hypotheses(hz, here)]


def min_tti(spot, hazards, horizon=HORIZON, speed=4.3, here=None):
    """Pure: seconds until the FIRST of these hazards covers `spot`, or inf when none does within `horizon`.

    The union, not the worst one: reasoning about the most dangerous threat is what hides a pincer, since two
    threats each leave an escape and the escapes need not overlap.

    Both directions count. A hazard may travel to the spot (closed-form root on its velocity), and we may travel
    into one on the way there — a static cloud never "arrives" anywhere, yet walking through it is exactly how a
    retreat died. With `here` given, the walk is included.
    """
    soonest = float("inf")
    for h in hazards:
        centre, radius = h[0], h[1]
        vel = h[2] if len(h) > 2 else (0.0, 0.0, 0.0)
        if math.dist(spot, centre) <= radius:
            return 0.0                       # already covered: no time at all
        if any(vel):
            soonest = min(soonest, tti(centre, vel, spot, radius, horizon))
        # Do we cross it on the way? Only meaningful for a hazard we are currently outside: when one already
        # covers the starting point every outbound path begins inside it, and charging that as an entry made
        # every escape score as instantly fatal — so standing still won, which is the paralysis this prevents.
        if here is not None and math.dist(here, centre) > radius:
            d = math.dist(here, spot)
            if d > 1e-9:
                direction = tuple((spot[i] - here[i]) / d * speed for i in range(3))
                soonest = min(soonest, tti(here, direction, centre, radius, min(horizon, d / speed)))
    return soonest


def slack_at(spot, hazards, here, speed=4.3, horizon=HORIZON, margin=0.3):
    """Pure: how much time to spare standing at `spot` — first arrival minus the walk minus a margin.

    Unbounded above on purpose. Clamping it at the horizon made "safe for four seconds" and "safe indefinitely"
    the same number, and a threat two seconds out indistinguishable from no threat.
    """
    travel = math.dist(here, spot) / speed
    first = min_tti(spot, hazards, horizon=horizon, speed=speed, here=here)
    if first == float("inf"):
        return float("inf")
    return round(first - travel - margin, 3)


def safest(frame, options=None, speed=4.3, horizon=HORIZON, dps=None, margin=0.3):
    """Pure: (position, slack) — where to stand, by the rule "first arrival must be later than getting there".

    Ten candidates against a closed-form root: no search, no grid, no simulation. For each option the slack is
    `min_tti - travel_time - margin`; the best positive one wins, and with none positive the least bad direction is
    still an answer, because standing still is what killed the runs.

    This replaces a version that scored every reachable option identically unless it was already inside a hazard —
    which made it blind to anything arriving, and therefore to every moving threat.
    """
    p = _xyz(frame["player"]["pos"])
    hazards = [(t[2][:3], t[2][3], (0.0, 0.0, 0.0)) for t in threats(frame, None, dps, horizon)]
    best, best_key = None, None
    for opt in options or step_options(frame):
        slack = slack_at(opt, hazards, p, speed, horizon, margin)
        nearest = min((math.dist(opt, h[0]) - h[1] for h in hazards), default=float("inf"))
        key = (slack, round(nearest, 2))
        if best_key is None or key > best_key:
            best, best_key = (opt, slack), key
    return best


def best_step(here, hazards, speed=4.3, horizon=HORIZON, margin=0.3, cover=None):
    """Pure: (spot, slack) — the same rule against a plain hazard list, for callers that have no tape frame.

    `cover` is offered as an extra candidate: a bunker mouth is worth considering even when it is further than a
    sidestep, because arriving there ends the problem rather than postponing it.
    """
    frame = {"player": {"pos": {"x": here[0], "y": here[1], "z": here[2]},
                        "vel": {"x": 0, "y": 0, "z": 0}, "hp": 20}}
    options = step_options(frame)
    if cover is not None:
        options = list(options) + [tuple(cover)]
    hazards = expand(hazards, here)          # safe against every future, not the single differenced one
    best, best_key = None, None
    for opt in options:
        slack = slack_at(opt, hazards, here, speed, horizon, margin)
        # Time first; distance from the nearest hazard breaks ties. Without the tie-break every candidate outside
        # a static threat scores `inf`, the first one wins, and the first one is where we already stand.
        nearest = min((math.dist(opt, h[0]) - h[1] for h in hazards), default=float("inf"))
        key = (slack, round(nearest, 2))
        if best_key is None or key > best_key:
            best, best_key = (opt, slack), key
    return best


def could_have_lived(frames, lead_s=2.0, **kw):
    """The postmortem question, and the only one worth asking after a death: `lead_s` before it, was there a step that
    would have kept us out of every hazard?

    Returns [{"tick", "hp", "escape", "margin"}] per death — `escape` None means the death was already unavoidable at
    that point and the mistake is earlier, in the plan, not in the controller.
    """
    by_tick = {f["tick"]: f for f in frames}
    out = []
    for dt in deaths(frames):
        want = dt - int(lead_s / TICK)
        f = by_tick.get(want) or next((x for x in frames if x["tick"] >= want), None)
        if f is None:
            continue
        best = safest(f, **kw)
        out.append({"tick": f["tick"], "hp": f["player"]["hp"],
                    "escape": best[0] if best and best[1] > 0 else None,
                    "margin": best[1] if best else None})
    return out


def windows(frames, window_phases=WINDOW_PHASES):
    """Pure: one entry per attack window, with the three numbers the planner's value model runs on.

    Everything the planner weighs is denominated in seconds, so a window has to report what it actually cost and
    bought, not what it was supposed to: `exposure_s` (how long we stood where the dragon could reach), `hp_lost`
    (what that cost) and `dragon_hp_lost` (what it bought). Remaining-window estimates, and with them the value of
    any structure built to shorten exposure, fall out of these.

    Exposure is measured, not assumed: a frame counts as exposed while any hazard is already on us (tti 0). That is
    the same definition the controller uses, so the plan and the postmortem cannot disagree about what "exposed"
    means.
    """
    by_tick = {f["tick"]: f for f in frames}
    out = []
    for phase, a, b in phase_spans(frames):
        if phase not in window_phases:
            continue
        span = [by_tick[t] for t in range(a, b + 1) if t in by_tick]
        if not span:
            continue
        first, last = span[0], span[-1]
        exposed = sum(1 for f in span if any(t[1] == 0.0 for t in threats(f)))
        dragon_hp = [(f["dragon"] or {}).get("health") for f in span if (f["dragon"] or {}).get("present")]
        out.append({
            "phase": phase,
            "start": a,
            "duration_s": round((b - a + 1) * TICK, 2),
            "exposure_s": round(exposed * TICK, 2),
            "hp_lost": round(first["player"]["hp"] - last["player"]["hp"], 2),
            "dragon_hp_lost": round((dragon_hp[0] - dragon_hp[-1]), 2) if len(dragon_hp) >= 2 else 0.0,
        })
    return out


def window_summary(frames):
    """Pure: the three numbers averaged over every window on the tape, plus how many windows a full-health dragon
    would take at that rate. A planner that knows the rate knows how much a structure built to improve it is worth."""
    ws = windows(frames)
    if not ws:
        return None
    n = len(ws)
    per_window = sum(w["dragon_hp_lost"] for w in ws) / n
    return {
        "windows": n,
        "mean_duration_s": round(sum(w["duration_s"] for w in ws) / n, 2),
        "mean_exposure_s": round(sum(w["exposure_s"] for w in ws) / n, 2),
        "mean_hp_lost": round(sum(w["hp_lost"] for w in ws) / n, 2),
        "mean_dragon_hp_lost": round(per_window, 2),
        "windows_for_200hp": round(200 / per_window, 1) if per_window > 0 else None,
    }


# -- enderman geometry. A fact about a mob and a line of sight, not a tactic: `api.run` consults it before every
# aimed task, and the fight skills consult it too.

ENDERMAN = "minecraft:enderman"
EYE_HEIGHT = 1.62
ENDERMAN_HEAD = 2.55      # eye/head height of a 2.9-block enderman
HEAD_BAND = 1.0           # how close to that height the aim may pass before it counts as "looking at it"


def aim_hits_enderman(aim_at, here, near, half_angle=12.0, radius=24.0, head_band=HEAD_BAND):
    """Pure: would looking at `aim_at` put the crosshair on an enderman's HEAD? Only the head provokes them (zh wiki:
    看向较高的地方以免看到它们的头部), so an aim that passes the same direction but well below or above the head is fine —
    which is what makes shooting crystals and placing beds possible at all in a crowd of them."""
    ax, az = aim_at[0] - here[0], aim_at[2] - here[2]
    span = math.hypot(ax, az)
    if not span:
        return False
    base = math.atan2(az, ax)
    eye = here[1] + EYE_HEIGHT
    for e in near:
        if e["type"] != ENDERMAN:
            continue
        ex, ez = e["x"] - here[0], e["z"] - here[2]
        d = math.hypot(ex, ez)
        if d > radius:
            continue
        diff = abs(math.degrees(math.atan2(ez, ex) - base))
        diff = min(diff, 360 - diff)
        if diff > half_angle:
            continue
        # Height of the aim line where that enderman stands, against its head.
        y_at = eye + (aim_at[1] - eye) * (d / span)
        if abs(y_at - (e["y"] + ENDERMAN_HEAD)) <= head_band:
            return True
    return False


from . import beliefs

# How close movement may plan to stand to each thing that can hurt us. A view of the belief table, not a copy:
# this table and play.toml's said different things about the same skeleton for months.
HAZARD_R = beliefs.keep_out()


def hazard_points(near, radii=None):
    """Pure: [(point, radius)] for everything that can hurt us here. Every ender dragon entry counts, body parts
    included (they carry no health field)."""
    radii = radii or HAZARD_R
    return [((e["x"], e["y"], e["z"]), radii[e["type"]]) for e in near if e["type"] in radii]
