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
PERCHED = {LANDING, SITTING_FLAMING, SITTING_SCANNING, SITTING_ATTACKING}


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


def safest(frame, options=None, speed=4.3, horizon=HORIZON, dps=None):
    """Pure: (position, earliest_tti) of the option that keeps us untouched longest, assuming we walk straight there.

    `speed` is sprinting on flat ground. A position we cannot reach before the hazard arrives is not safe, so each
    option is scored at the time we would get there, not at time zero.
    """
    p = _xyz(frame["player"]["pos"])
    best = None
    for opt in options or step_options(frame):
        travel = math.dist(p, opt) / speed
        worst = horizon
        for kind, _, region, _, _ in threats(frame, None, dps, horizon):
            cx, cy, cz, r = region
            if math.dist(opt, (cx, cy, cz)) <= r:
                worst = 0.0      # standing in it on arrival: not an option at all
                break
            worst = min(worst, horizon)
        score = worst - travel
        if best is None or score > best[1]:
            best = (opt, round(score, 3))
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
