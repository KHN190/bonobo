"""What the collected tapes say about the dragon, aggregated across every recording.

One tape is an anecdote: a phase that happened to last 5 s once tells the planner nothing about the window it can
count on. This pools every tape in the tape directory and reports the distributions the planner actually budgets
against — how long each phase lasts, how the cycle is ordered, where the breath lands, what damage came from what.

Offline: reads files, never the game.

Usage: phase_report.py [NAME_PREFIX]
"""
import os
import statistics
import sys


from .. import combat_model as cm, combat_tape as ct      # noqa: E402

PHASE_NAMES = {0: "holding pattern", 1: "strafing", 2: "landing approach", 3: "landing",
               4: "taking off", 5: "sitting: flaming", 6: "sitting: scanning", 7: "sitting: attacking",
               8: "charging player", 9: "dying", 10: "hovering", None: "absent"}


def tapes(prefix=None):
    try:
        names = sorted(n for n in os.listdir(ct.DIR) if n.endswith(".json") and (not prefix or n.startswith(prefix)))
    except OSError:
        return []
    return [os.path.join(ct.DIR, n) for n in names]


def fit(paths):
    """Fit the objective's parameters from recorded windows. Returns {name: value} for what the tapes can support.

    Only what is actually observable: damage per window needs windows where the dragon lost health, exposure needs
    windows recorded in and out of cover. Anything the tapes cannot answer is left alone — a fitted-looking number
    with no evidence behind it is worse than an admitted guess.
    """
    windows, covered, open_ = [], [], []
    for path in paths:
        frames = ct.load(path)["frames"]
        for w in cm.windows(frames):
            windows.append(w)
            (covered if w["exposure_s"] <= 1.5 else open_).append(w)
    out = {}
    hits = [w["dragon_hp_lost"] for w in windows if w["dragon_hp_lost"] > 0]
    if hits:
        out["bed_damage"] = round(sum(hits) / len(hits), 1)
    if covered:
        out["exposure_in_cover_s"] = round(sum(w["exposure_s"] for w in covered) / len(covered), 2)
    if open_:
        out["exposure_in_open_s"] = round(sum(w["exposure_s"] for w in open_) / len(open_), 2)
    # Death risk needs deaths. Without any, the slope stays a guess and stays declared as one.
    deaths = sum(len(cm.deaths(ct.load(p)["frames"])) for p in paths)
    exposed = sum(w["exposure_s"] for w in windows)
    if deaths and exposed:
        out["death_risk_per_exposed_s"] = round(deaths / exposed, 3)
    return out


def write_fit(values, path=None):
    """Write fitted values into fight.toml and drop their names from `unmeasured`. Returns what changed."""
    import re
    from .. import fight_plan
    path = path or fight_plan.CONFIG_PATH
    text = open(path).read()
    changed = {}
    for name, value in values.items():
        pattern = rf"^({re.escape(name)}\s*=\s*)([0-9.]+)"
        new, n = re.subn(pattern, lambda m: f"{m.group(1)}{value}", text, count=1, flags=re.M)
        if n:
            changed[name] = value
            text = new
    if changed:
        m = re.search(r"^unmeasured = \[([^\]]*)\]", text, flags=re.M | re.S)
        listed = re.findall(r'"([^"]+)"', m.group(1)) if m else []
        still = [n for n in listed if n not in changed]
        text = re.sub(r"^unmeasured = \[[^\]]*\]",
                      "unmeasured = [" + ", ".join(f'"{n}"' for n in still) + "]", text, count=1, flags=re.M | re.S)
        open(path, "w").write(text)
    return changed


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    prefix = args[0] if args else None
    paths = tapes(prefix)
    if not paths:
        raise SystemExit(f"no tapes in {ct.DIR}")

    durations = {}          # phase -> [seconds]
    follows = {}            # phase -> {next phase: count}
    breath_radius, breath_offsets = [], []
    damage = {}
    total_frames = 0

    for p in paths:
        frames = ct.load(p)["frames"]
        total_frames += len(frames)
        spans = cm.phase_spans(frames)
        for i, (phase, a, b) in enumerate(spans):
            # The first and last span of a tape are cut off by the recording, not by the dragon.
            if 0 < i < len(spans) - 1:
                durations.setdefault(phase, []).append((b - a + 1) * cm.TICK)
            if i + 1 < len(spans):
                follows.setdefault(phase, {}).setdefault(spans[i + 1][0], 0)
                follows[phase][spans[i + 1][0]] += 1
        for f in frames:
            for c in f.get("breath") or []:
                breath_radius.append(c.get("radius") or 0)
                breath_offsets.append(round((c["pos"]["x"] ** 2 + c["pos"]["z"] ** 2) ** 0.5, 1))
        for kind, e in cm.fit_damage(frames).items():
            d = damage.setdefault(kind, {"n": 0, "total": 0.0, "max": 0.0})
            d["n"] += e["n"]
            d["total"] += e["mean"] * e["n"]
            d["max"] = max(d["max"], e["max"])

    print(f"{len(paths)} tapes, {total_frames} frames ({total_frames * cm.TICK / 60:.1f} min of game)\n")

    if "--fit" in sys.argv:
        values = fit(paths)
        if not values:
            print("nothing fittable yet: no recorded attack window shows damage or exposure\n")
        else:
            changed = write_fit(values)
            print("fitted from the tapes:")
            for k, v in sorted(values.items()):
                print(f"  {k:28} {v}" + ("" if k in changed else "   (no such key in fight.toml)"))
            print()

    print("phase durations (seconds)")
    for phase in sorted(durations, key=lambda p: (p is None, p)):
        xs = durations[phase]
        med = statistics.median(xs)
        spread = f"{min(xs):.2f}–{max(xs):.2f}"
        sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        print(f"  {str(phase):>4} {PHASE_NAMES.get(phase, '?'):<20} n={len(xs):<4} median {med:>6.2f}  "
              f"range {spread:<14} sd {sd:.2f}")

    print("\ncycle order (what follows what)")
    for phase in sorted(follows, key=lambda p: (p is None, p)):
        nxt = ", ".join(f"{k}×{v}" for k, v in sorted(follows[phase].items(), key=lambda kv: -kv[1]))
        print(f"  {str(phase):>4} → {nxt}")

    if breath_radius:
        print(f"\nbreath clouds: {len(breath_radius)} samples, radius median "
              f"{statistics.median(breath_radius):.2f}, max {max(breath_radius):.2f}; "
              f"distance from the fountain median {statistics.median(breath_offsets):.1f}, "
              f"max {max(breath_offsets):.1f}")

    if damage:
        print("\ndamage taken")
        for kind, d in sorted(damage.items(), key=lambda kv: -kv[1]["n"]):
            print(f"  {kind:<34} n={d['n']:<4} mean {d['total'] / d['n']:.2f}  max {d['max']:.2f}")
    else:
        print("\ndamage taken: none recorded (a spectator is never a target — run the bait mode for this)")


if __name__ == "__main__":
    main()
