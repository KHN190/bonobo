"""The concise event log: only changes. events.jsonl (one record each: t, kind, line, fields) and events.log (the line).
detail.log stays the working-out; this is what a session did: decisions that changed, tasks and their outcomes,
harm, deaths, goals, milestones, anomalies. An identical line repeated is counted, not rewritten."""
from __future__ import annotations

import json
import os
import threading
import time

from . import paths

EVENTS_FILE = paths.data("events.jsonl")
EVENTS_LOG = paths.data("events.log")
SLOW_ROUND_S = 5.0              # a round slower than this is an anomaly
ANOMALY_AT = (1, 10, 100, 1000)  # an anomaly is said at its 1st, 10th, 100th … time
IDLE_GOAL = "holding: nothing to do"  # the goal when there is none
MILESTONES = ("minecraft:iron_ingot", "minecraft:diamond", "minecraft:obsidian", "minecraft:blaze_rod",
              "minecraft:ender_pearl", "minecraft:ender_eye", "minecraft:bed", "minecraft:bucket",
              "minecraft:stone_pickaxe", "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe",
              "minecraft:iron_sword", "minecraft:diamond_sword", "minecraft:shield")

_LOCK = threading.Lock()
STATE: dict = {}                 # last decision, last goal, the pending repeat, anomaly counts, milestones seen


def _write(rec, sink=None):
    if sink is not None:
        sink.append(rec)
        return
    try:
        os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        with open(EVENTS_LOG, "a") as f:
            f.write(time.strftime("%H:%M:%S", time.localtime(rec["t"])) + " " + rec["line"] + "\n")
    except OSError:
        pass        # the log must never stop the agent


def emit(kind, line, t=None, sink=None, **fields):
    """One event. The same (kind, line) as the last one is counted, and the count said with the next different one."""
    t = time.time() if t is None else t
    with _LOCK:
        last = STATE.get("last")
        if last is not None and last["kind"] == kind and last["line"] == line:
            last["repeats"] = last.get("repeats", 0) + 1
            return
        if last is not None and last.get("repeats"):
            _write({"t": t, "kind": "repeat", "line": f"  (×{last['repeats'] + 1} {last['line']})",
                    "of": last["kind"], "count": last["repeats"] + 1}, sink)
        rec = {"t": round(t, 2), "kind": kind, "line": line, **fields}
        STATE["last"] = dict(rec)
        _write(rec, sink)


def decision(layer, pick, worth=None, why="", t=None, sink=None):
    """A decision, said only when it changes (layer and pick), never the same bid each round."""
    key = (layer, pick)
    if STATE.get("decision") == key:
        return
    STATE["decision"] = key
    w = "" if worth is None else f" worth {worth:.0f}s"
    emit("decision", f"{layer}: {pick}{w} — {why}".rstrip(" —"), t, sink, layer=layer, pick=pick, worth=worth,
         why=why)


def task(name, outcome, seconds, source=None, t=None, sink=None):
    """A task's end: outcome and seconds; an interruption names its source (and its next start is a resume)."""
    src = f" by {source}" if source else ""
    if outcome == "interrupted":
        STATE.setdefault("interrupted", {})[name] = source
    emit("task", f"{name}: {outcome}{src} ({seconds:.1f}s)", t, sink, name=name, outcome=outcome,
         seconds=round(seconds, 1), source=source)


def task_start(name, t=None, sink=None):
    """A task starting: said only when it resumes one an interruption stopped."""
    pending = STATE.get("interrupted", {})
    if name in pending:
        source = pending.pop(name)
        emit("resume", f"{name}: resumed after {source or 'an interruption'}", t, sink, name=name, source=source)


STEP = " → "      # a goal's text: "<goal> → <step>"; a new step under the same goal is said as the step alone


def goal(text, counts=None, t=None, sink=None):
    """The goal, when it changes — the goal it replaces closed with what the bag gained under it (`counts`: the bag
    now, {id: n}). A new step of the same goal is said as its step only."""
    if STATE.get("goal") == text:
        return
    t = time.time() if t is None else t
    was, since, start = STATE.get("goal"), STATE.get("goal_t"), STATE.get("goal_bag")
    head, step = text.split(STEP, 1) if STEP in text else (text, None)
    same = step is not None and was is not None and was.split(STEP, 1)[0] == head
    if was is not None and counts is not None and start is not None:
        gained = gains(start, counts)
        if gained:
            took = f" in {t - since:.0f}s" if since is not None else ""
            said = STEP.lstrip() + was.split(STEP, 1)[1] if same and STEP in was else was
            emit("progress", ("  " if same else "") + f"{said}: "
                 + ", ".join(f"+{n} {k.split(':')[-1]}" for k, n in gained.items()) + took,
                 t, sink, goal=was, gained=gained)
    STATE.update(goal=text, goal_t=t, goal_bag=dict(counts) if counts is not None else None)
    emit("goal", f"  {STEP.lstrip()}{step}" if same else f"goal: {text}", t, sink, goal=text)


def gains(before, after):
    """Pure: the items the bag holds more of now ({id: +n})."""
    return {k: n - before.get(k, 0) for k, n in sorted(after.items()) if n > before.get(k, 0)}


def hurt(amount, hp, source=None, t=None, sink=None):
    if source:
        STATE["hurt_by"] = source
    emit("hurt", f"hurt {amount:.1f} → {hp:.1f} hp" + (f" by {source}" if source else ""), t, sink,
         amount=round(amount, 1), hp=round(hp, 1), source=source)


def last_hurt_by(path=None, tail=1 << 16):
    """What last hurt us: this process's last hurt event, else the last one on file (a run that starts dead)."""
    if STATE.get("hurt_by"):
        return STATE["hurt_by"]
    try:
        with open(path or EVENTS_FILE, "rb") as f:
            f.seek(max(0, os.fstat(f.fileno()).st_size - tail))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") in ("death", "respawn"):
            return None               # the last life's harm is not this death's
        if rec.get("kind") == "hurt" and rec.get("source"):
            return rec["source"]
    return None


def death(cause, place, t=None, sink=None):
    """A death, named by its cause — the jar's, else the last damage source we saw."""
    cause = cause or last_hurt_by() or "unknown"
    STATE.pop("hurt_by", None)
    emit("death", f"DIED ({cause}) at {place}", t, sink, cause=cause, place=place)


def respawn(place, t=None, sink=None):
    emit("respawn", f"respawned at {place}", t, sink, place=place)


def ate(item, t=None, sink=None):
    emit("eat", f"ate {item}", t, sink, item=item)


def milestones(counts, t=None, sink=None):
    """The first time each milestone item is held this session."""
    seen = STATE.setdefault("seen", set())
    for item in MILESTONES:
        if counts.get(item, 0) > 0 and item not in seen:
            seen.add(item)
            emit("milestone", f"first {item.split(':')[-1]}", t, sink, item=item)


def anomaly(what, detail="", t=None, sink=None):
    """Something wrong: said at its 1st, 10th, 100th time, with the count."""
    counts = STATE.setdefault("anomalies", {})
    n = counts[what] = counts.get(what, 0) + 1
    if n in ANOMALY_AT:
        emit("anomaly", f"!! {what}" + (f": {detail}" if detail else "") + (f" (×{n})" if n > 1 else ""), t, sink,
             what=what, detail=detail, count=n)


def round_time(seconds, ran_s=0.0, t=None, sink=None):
    """A round, said only when its deciding is slow: the round less `ran_s`, the task (or idle wait) it ran."""
    deciding = seconds - ran_s
    if deciding > SLOW_ROUND_S:
        anomaly("slow round", f"{deciding:.1f}s deciding" + (f" (+{ran_s:.1f}s running)" if ran_s else ""), t, sink)


def reset_state():
    """Test helper: forget every remembered change."""
    STATE.clear()
