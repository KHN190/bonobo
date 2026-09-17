"""Claude's instructions to the cerebellum: a persistent queue in directives.json, ranked above normal goals and
below survival. Claude (the cerebrum) writes directives after a review — long-term plans, roads to build, skills
to run — and the script executes them in order. Pure data helpers here; execution lives in brain.py.

A directive: {"id", "kind": "goal" | "goto" | "skill", "status": "pending" | "done" | "failed", "fails": 0,
              "needs": [[token, count], ...]            # kind goal
              "target": [x, y, z], "range": 2           # kind goto
              "name": "build_shelter", "args": [...]    # kind skill (skills.<name>(ctx, *args))
              "note": "why", "created": ts}
"""
import json
import os
import time
from . import paths

FILE = paths.data("directives.json", env="MC_DIRECTIVES")
# An order does not expire because the agent could not carry it out yet (user, 2026-09-18). Failing three times
# retired the directive while the reason was a passing one — "no stone within 48 blocks" because the blocks it
# found were all banned this minute — and Claude's instruction quietly stopped existing. Failures are still
# counted (the retry policy backs off on them); nothing gives up on them.


def load(path=None):
    try:
        with open(path or FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(items, path=None):
    path = path or FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f, indent=1)
    os.replace(tmp, path)


def add(kind, path=None, **fields):
    items = load(path)
    # Unique even for directives added within the same millisecond (a plan queued in one go).
    item = {"id": f"d{int(time.time() * 1000) % 10**9}-{len(items)}", "kind": kind, "status": "pending", "fails": 0,
            "created": time.strftime("%Y-%m-%d %H:%M"), **fields}
    items.append(item)
    save(items, path)
    return item


def current(items):
    """The first runnable directive, or None."""
    return next(iter(runnable(items)), None)


def runnable(items):
    """Pending directives whose `requires` (directive ids) are all done — a dependency graph, not a line: parallel
    branches (obsidian, food, bow, shield) are all runnable at once and the priority pool interleaves them."""
    done = {d["id"] for d in items if d.get("status") == "done"}
    return [d for d in items if d.get("status") == "pending" and all(r in done for r in d.get("requires", []))]


def blocked(items):
    """Pending directives waiting on a dependency that failed: they can never run (reported to Claude)."""
    failed = {d["id"] for d in items if d.get("status") == "failed"}
    return [d for d in items if d.get("status") == "pending" and any(r in failed for r in d.get("requires", []))]


def mark(items, directive_id, *, done=False, failed_reason=None):
    """Record an outcome. Returns (items, gave_up) — `gave_up` is always False: an order stands until it is done
    or Claude clears it."""
    gave_up = False
    for d in items:
        if d["id"] != directive_id:
            continue
        if done:
            d["status"] = "done"
            d["finished"] = time.strftime("%Y-%m-%d %H:%M")
        elif failed_reason is not None:
            d["fails"] = d.get("fails", 0) + 1
            d["last_error"] = failed_reason
    return items, gave_up


def describe(d):
    if d["kind"] == "goal":
        what = ", ".join(f"{n}× {t}" for t, n in d.get("needs", []))
    elif d["kind"] == "goto":
        what = f"go to {tuple(d.get('target', []))}"
    else:
        what = f"{d.get('name')}({', '.join(map(str, d.get('args', [])))})"
    extra = ""
    if d.get("mode", "override") != "override":
        extra += f" ({d['mode']}{' ×' + str(d['x']) if d.get('x') else ''})"
    if d.get("requires"):
        extra += f" after {', '.join(d['requires'])}"
    return f"[{d['status']}] {d['kind']}: {what}{extra}" + (f" — {d['note']}" if d.get("note") else "")
