"""The 5-minute review packet for the cerebrum (Claude): what the agent did, what failed and how often, where it went,
what changed in the bag, which skills are slow or unreliable, and where the directive queue stands. Claude reads it,
distils lessons into SKILL.md / skills / tests, and writes directives for the long-term plan."""
import collections
import datetime
import os
import re

from . import directives, paths

LOG = paths.data("autoplay.log")
LINE = re.compile(r"^(\d\d:\d\d:\d\d) (.*)$")


def recent_lines(lines, minutes, now=None):
    """Pure: log lines from the last `minutes` (log lines carry only HH:MM:SS; a day wrap is handled)."""
    now = now or datetime.datetime.now()
    start = now - datetime.timedelta(minutes=minutes)
    out = []
    for raw in lines:
        m = LINE.match(raw.rstrip("\n"))
        if not m:
            continue
        t = datetime.datetime.combine(now.date(), datetime.time.fromisoformat(m.group(1)))
        if t > now + datetime.timedelta(minutes=1):
            t -= datetime.timedelta(days=1)
        if t >= start:
            out.append((t, m.group(2)))
    return out


def summarize(entries):
    """Pure: counts of decisions, failures by kind, help requests and survival events."""
    goals = collections.Counter()
    failures = collections.Counter()
    unavailable = collections.Counter()
    help_requests, survival, notable = [], [], []
    for _, text in entries:
        if text.startswith("=== "):
            goals[text[4:].split(" | ")[0].split(" (score")[0]] += 1
        elif text.startswith("!! "):
            failures[text[3:].split(":")[0]] += 1
        elif text.startswith("~~ "):
            unavailable[text[3:].split(":")[0]] += 1
        elif text.startswith("?? "):
            help_requests.append(text[3:])
        elif text.startswith("survival: ") or text.startswith("reflex: "):
            survival.append(text)
        elif any(k in text for k in ("slept", "built", "burrowed", "walled in", "dug in", "stored", "collected",
                                     "crafted 1 minecraft:iron", "died")):
            notable.append(text)
    return {"goals": goals, "failures": failures, "unavailable": unavailable, "help": help_requests,
            "survival": survival, "notable": notable}


TRACK = paths.data("track.jsonl")


def macro(track, minutes, now):
    """Pure: track = [{"t", "pos", "done"}] once a minute. Distance covered and main goals completed in the window,
    flagged STALLED when the agent neither moved nor finished anything — the counts alone hid a 30-minute loop."""
    import math
    window = [e for e in track if e["t"] >= now - minutes * 60]
    if len(window) < 2:
        return "- not enough track data"
    far = max(math.dist(e["pos"], window[0]["pos"]) for e in window)
    gained = sorted(set(window[-1]["done"]) - set(window[0]["done"]))
    lost = sorted(set(window[0]["done"]) - set(window[-1]["done"]))
    lines = [f"- moved up to {far:.0f} blocks; main goals done {len(window[-1]['done'])}"
             f" (+{', '.join(gained) or 'none'}; lost: {', '.join(lost) or 'none'})"]
    if far < 4 and not gained:
        lines.append(f"- STALLED: no movement and no goal completed in {minutes} min")
    return "\n".join(lines)


RANKING = paths.data("ranking.jsonl")


def rankings(entries, minutes, now):
    """Pure: from ranking.jsonl entries, what was picked in the window and which valuable candidates kept losing
    (ranked or filtered, never picked) with their most common reason — what Claude tunes priorities.json with."""
    window = [e for e in entries if e["t"] >= now - minutes * 60]
    if not window:
        return "- no ranking data"
    picks = collections.Counter(e["pick"] for e in window)
    lines = ["- picked: " + ", ".join(f"{n}×{c}" for n, c in picks.most_common(6))]
    losing = collections.Counter()
    reasons = collections.defaultdict(collections.Counter)
    for e in window:
        for name, _, base in e.get("top", []):
            if name != e["pick"]:
                losing[name] += 1
                reasons[name]["outscored"] += 1
        for name, why in e.get("filtered", {}).items():
            losing[name] += 1
            reasons[name][why] += 1
    starved = [(n, c) for n, c in losing.most_common() if n not in picks][:5]
    if starved:
        lines.append("- never picked: " + "; ".join(
            f"{n} ({c}×, mostly {reasons[n].most_common(1)[0][0]})" for n, c in starved))
    return "\n".join(lines)


def repeated(entries, at_least=5):
    """Pure: brain decisions/failures that repeat with the same text (numbers normalised) — a loop reads as one line
    with a count, e.g. 'night in the water: swimming to land first ×40', instead of hiding among task results."""
    counts = collections.Counter()
    for _, text in entries:
        if text.startswith("  "):      # task results (goto/place/...) are symptoms; decisions are the pattern
            continue
        counts[re.sub(r"-?\d+(\.\d+)?", "#", text)[:110]] += 1
    top = [(n, t) for t, n in counts.most_common(5) if n >= at_least]
    return "\n".join(f"- ×{n} {t}" for n, t in top) or "- none"


def packet(minutes=5, state=None, inventory=None, memory=None, lines=None):
    if lines is None:
        try:
            with open(LOG) as f:
                lines = f.readlines()[-6000:]
        except OSError:
            lines = []
    entries = recent_lines(lines, minutes)
    s = summarize(entries)
    out = [f"# Review — last {minutes} min ({len(entries)} log lines)"]
    if state:
        out.append(f"- now: pos {(state['blockX'], state['blockY'], state['blockZ'])}, time {state['timeOfDay']}, "
                   f"hp {state['health']}, food {state['food']}, air {state['air']}, sky {state.get('skyLight')}")
    if inventory is not None:
        out.append(f"- bag: {inventory.used_slots()} slots; pickaxes {inventory.tools('pickaxe')}")
    try:
        from . import scenarios
        out.append("- bench readiness (current code):")
        out += scenarios.readiness_lines()
    except Exception as e:      # the review must never fail because of the bench table
        out.append(f"- bench readiness unavailable: {e}")
    try:
        import json
        import time
        with open(TRACK) as f:
            track = [json.loads(line) for line in f.readlines()[-240:]]
        out.append("## Macro progress\n" + macro(track, max(minutes, 30), time.time()))
    except (OSError, ValueError):
        pass
    out.append("## Repeated patterns (same line ≥ 5×)\n" + repeated(entries))
    try:
        import json
        import time
        with open(RANKING) as f:
            ranks = [json.loads(line) for line in f.readlines()[-2000:]]
        out.append("## Priorities (last 30 min)\n" + rankings(ranks, 30, time.time()))
    except (OSError, ValueError):
        pass
    out.append("## Goals chosen\n" + ("\n".join(f"- {n}× {g}" for g, n in s["goals"].most_common(8)) or "- none"))
    out.append("## Failures (!!)\n" + ("\n".join(f"- {n}× {g}" for g, n in s["failures"].most_common(8)) or "- none"))
    out.append("## Not available (~~)\n" + ("\n".join(f"- {n}× {g}" for g, n in s["unavailable"].most_common(8))
                                          or "- none"))
    out.append("## Help requests (??)\n" + ("\n".join(f"- {h}" for h in s["help"][-6:]) or "- none"))
    out.append("## Survival / reflexes\n" + ("\n".join(f"- {h}" for h in s["survival"][-6:]) or "- none"))
    out.append("## Notable\n" + ("\n".join(f"- {h}" for h in s["notable"][-8:]) or "- none"))
    if memory is not None:
        stats = memory.data.get("stats", {})
        worst = sorted(((memory.success_rate(k), k) for k in stats), key=lambda t: t[0])[:6]
        out.append("## Least reliable steps\n" + ("\n".join(f"- {k}: {r:.0%}" for r, k in worst) or "- none"))
        durs = memory.data.get("durations", {})
        slow = sorted(((v["per"], k) for k, v in durs.items() if v.get("n", 0) >= 3), reverse=True)[:6]
        out.append("## Slowest skills (s/unit)\n" + ("\n".join(f"- {k}: {p:.1f}" for p, k in slow) or "- none"))
    items = directives.load()
    out.append("## Directives\n" + ("\n".join(f"- {directives.describe(d)}" for d in items[-8:]) or "- none"))
    out.append("## For the cerebrum\n- Distil repeated failures into skill/rule fixes + an offline test.\n"
               "- Record lessons in SKILL.md.\n- Queue directives for the long-term plan (mc.py direct ...).")
    return "\n".join(out)
