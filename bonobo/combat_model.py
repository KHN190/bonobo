"""What a combat tape means. Pure functions only — they read frames, never the game. The rule this module exists to serve: don't learn "where is safe", learn "how long until I am hit". Every hazard is reduced to the same triple so the controller compares a breath cloud and an enderman with one number: kind        what it is (the entity id), for the damage model only tti         seconds until it reaches us if nothing changes (inf = it never does) region      (x, y, z, radius) it will occupy — where not to stand dps         health per second while inside it persistent  True if it stays put once formed (breath clouds), False if it must be re-aimed (head, enderman) Everything is measured off the tape rather than tabulated: the numbers that matter (how fast breath spreads, how much a head sweep takes) belong to this version of the game, not to a wiki page."""

import math

TICK = 0.05                 # seconds per tick
HORIZON = 4.0               # seconds ahead worth predicting; past that the dragon has re-decided anyway

# vanilla EnderDragonPhase ids; only the sitting ones open a window
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

def tti(pos, vel, centre, radius, horizon=HORIZON):
    """Pure: seconds until a point moving at `vel` (blocks per second) enters the sphere at `centre`, inf if it does not within `horizon`."""

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

# reach of each hazard and whether it stays put; starting values fitted by `fit_damage`, the head generous
HAZARD = {
    "minecraft:area_effect_cloud": (3.0, True),
    "minecraft:dragon_fireball": (3.0, False),
    "minecraft:enderman": (3.0, False),
    "dragon_head": (6.0, False),
}
DEFAULT_DPS = {"minecraft:area_effect_cloud": 6.0, "dragon_head": 10.0, "minecraft:enderman": 7.0}

def threats(frame, prev=None, dps=None, horizon=HORIZON):
    """Pure: every hazard in this frame as (kind, tti, region, dps, persistent), soonest first."""

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

def hits(frames):
    """[(tick, amount, nearest)] — every recorded damage event."""
    return [(f["tick"], d["amount"], d.get("nearest"))
            for f in frames for d in (f.get("damage") or [])]

def fit_damage(frames):
    """{kind: {"n", "mean", "max"}} — what each source actually took off us on this tape."""

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
    """Pure: the positions a single committed step could reach — 8 compass directions plus standing still."""

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
    """Pure: plausible futures for one hazard, each a (centre, radius, velocity) row."""

    centre, radius = hazard[0], hazard[1]
    vel = hazard[2] if len(hazard) > 2 else (0.0, 0.0, 0.0)
    kind = hazard[3] if len(hazard) > 3 else None
    out = [(centre, radius, vel, kind), (centre, radius, (0.0, 0.0, 0.0), kind)]
    if here is not None and kind not in STATIC_KINDS:
        d = math.dist(here, centre)
        if d > 1e-9:
            out.append((centre, radius, tuple((here[i] - centre[i]) / d * closing for i in range(3)), kind))
    return out

# hazards that do not chase
STATIC_KINDS = {"minecraft:area_effect_cloud", "dragon_head", "minecraft:ender_dragon"}

def expand(hazards, here=None):
    """Pure: every hypothesis of every hazard, flattened. Safety is computed over this, never over the raw list."""
    return [h for hz in hazards for h in hypotheses(hz, here)]

def min_tti(spot, hazards, horizon=HORIZON, speed=4.3, here=None):
    """Pure: seconds until the FIRST of these hazards covers `spot`, or inf when none does within `horizon`."""

    soonest = float("inf")
    for h in hazards:
        centre, radius = h[0], h[1]
        vel = h[2] if len(h) > 2 else (0.0, 0.0, 0.0)
        if math.dist(spot, centre) <= radius:
            return 0.0                       # already covered: no time at all
        if any(vel):
            soonest = min(soonest, tti(centre, vel, spot, radius, horizon))
        # crossing counts only for a hazard we are outside: charging a covering one made every escape fatal and standing still won
        if here is not None and math.dist(here, centre) > radius:
            d = math.dist(here, spot)
            if d > 1e-9:
                direction = tuple((spot[i] - here[i]) / d * speed for i in range(3))
                soonest = min(soonest, tti(here, direction, centre, radius, min(horizon, d / speed)))
    return soonest

def slack_at(spot, hazards, here, speed=4.3, horizon=HORIZON, margin=0.3):
    """Pure: how much time to spare standing at `spot` — first arrival minus the walk minus a margin."""

    travel = math.dist(here, spot) / speed
    first = min_tti(spot, hazards, horizon=horizon, speed=speed, here=here)
    if first == float("inf"):
        return float("inf")
    return round(first - travel - margin, 3)

def safest(frame, options=None, speed=4.3, horizon=HORIZON, dps=None, margin=0.3, hazards=None):
    """Pure: (position, slack) — where to stand, by the rule "first arrival must be later than getting there"."""

    p = _xyz(frame["player"]["pos"])
    if hazards is None:           # `hazards` given: a plain list already expanded (best_step), not the frame's
        hazards = [(t[2][:3], t[2][3], (0.0, 0.0, 0.0)) for t in threats(frame, None, dps, horizon)]
    best, best_key = None, None
    for opt in options or step_options(frame):
        slack = slack_at(opt, hazards, p, speed, horizon, margin)
        # time first, distance from hazards breaks ties (else the first candidate — where we stand — wins)
        nearest = min((math.dist(opt, h[0]) - h[1] for h in hazards), default=float("inf"))
        key = (slack, round(nearest, 2))
        if best_key is None or key > best_key:
            best, best_key = (opt, slack), key
    return best

def best_step(here, hazards, speed=4.3, horizon=HORIZON, margin=0.3, cover=None):
    """Pure: (spot, slack): each option's slack is `min_tti - travel_time - margin`; best wins, distance from hazards breaks ties."""

    frame = {"player": {"pos": {"x": here[0], "y": here[1], "z": here[2]},
                        "vel": {"x": 0, "y": 0, "z": 0}, "hp": 20}}
    options = step_options(frame)
    if cover is not None:
        options = list(options) + [tuple(cover)]
    # safe against every future, not the single differenced one
    return safest(frame, options, speed, horizon, None, margin, hazards=expand(hazards, here))

def windows(frames, window_phases=WINDOW_PHASES):
    """Pure: one entry per attack window, with the three numbers the planner's value model runs on."""

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

# enderman geometry: a fact `api.run` and the fight skills consult before any aimed task

ENDERMAN = "minecraft:enderman"
EYE_HEIGHT = 1.62
ENDERMAN_HEAD = 2.55      # eye/head height of a 2.9-block enderman
HEAD_BAND = 1.0           # how close to that height the aim may pass before it counts as "looking at it"

def aim_hits_enderman(aim_at, here, near, half_angle=12.0, radius=24.0, head_band=HEAD_BAND):
    """Pure: would looking at `aim_at` put the crosshair on an enderman's HEAD?"""

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

from . import beliefs, lifecycle

# a view of the belief table, not a copy (two copies disagreed for months)
HAZARD_R = beliefs.keep_out()

def hazard_points(near, radii=None):
    """Pure: [(point, radius)] for everything that can hurt us here."""

    radii = radii or HAZARD_R
    return [((e["x"], e["y"], e["z"]), radii[e["type"]]) for e in near if e["type"] in radii]

# the current hazard set: written by perception, read by movement, kept here beside its points
HAZARDS = []          # [(point, radius)], newest perception round wins
HAZARDS_AT = 0.0      # when it was refreshed; stale hazards are worse than none


def _forget_hazards():
    global HAZARDS, HAZARDS_AT
    HAZARDS, HAZARDS_AT = [], 0.0


lifecycle.on_reset(_forget_hazards, covers=("HAZARDS", "HAZARDS_AT"))

def note_hazards(near, now=None):
    """Record what can hurt us right now. Pure apart from the clock; called from the perception round."""
    global HAZARDS, HAZARDS_AT
    import time as _t
    HAZARDS = hazard_points(near or [])
    HAZARDS_AT = now if now is not None else _t.time()
    return HAZARDS

def hazards(max_age_s=3.0, now=None):
    """The current hazard set, or empty when perception has not looked recently enough to be trusted."""
    import time as _t
    if not HAZARDS or (now or _t.time()) - HAZARDS_AT > max_age_s:
        return []
    return HAZARDS
