"""The task queue (L3), MC_DATA/tasks.json: what the cerebrum wants done, first live task first.

A task's `plan` is saved with it so half-done work survives a restart — only a hint, re-checked against the bag."""
import json
import os
import time
from typing import Any

from . import goals, paths

FILE = paths.data("tasks.json", env="MC_TASKS")
STATES = ("pending", "running", "done", "failed", "cancelled")
LIVE = ("pending", "running")

def load(path=None) -> list:
    return paths.read_json(path or FILE, {}).get("tasks", [])

def save(items, path=None):
    current = load(path)
    item_ids = {t["id"] for t in items}
    merged = list(items) + [t for t in current if t.get("id") not in item_ids]
    paths.save_json(path or FILE, {"tasks": merged})

def add(goal, expires_s=None, front=False, source="cerebrum", path=None, now=None) -> dict:
    """Queue a goal (goals.make / goals.have). Returns the task. An identical live goal is not queued twice."""
    now = time.time() if now is None else now
    items = load(path)
    for t in items:
        if t["state"] in LIVE and t["goal"] == goal["goal"] and t["args"] == goal.get("args", {}):
            return t
    n = 1 + max((int(t["id"].lstrip("t")) for t in items if t["id"].lstrip("t").isdigit()), default=0)
    task = {"id": f"t{n}", "goal": goal["goal"], "args": goal.get("args", {}), "state": "pending", "reason": "",
            "created": now, "expires": now + expires_s if expires_s else None, "source": source, "plan": None}
    items = [task] + items if front else items + [task]
    save(items, path)
    return task

def expire(items, now=None):
    """Pure: live tasks past their expiry become cancelled ("expired"). Returns whether anything changed."""
    now = time.time() if now is None else now
    changed = False
    for t in items:
        if t["state"] in LIVE and t.get("expires") and t["expires"] <= now:
            t["state"], t["reason"] = "cancelled", "expired"
            changed = True
    return changed

def head(items):
    """Pure: the first live task, or None."""
    return next((t for t in items if t["state"] in LIVE), None)

def update(task_id, path=None, **fields) -> dict | None:
    """Change one task's fields in the file (state, reason, plan). Returns the task, or None when it is gone."""
    items = load(path)
    for t in items:
        if t["id"] == task_id:
            t.update(fields)
            save(items, path)
            return t
    return None

def marked(state, reason=""):
    """Pure: the fields a task in `state` gets."""
    if state not in STATES:
        raise ValueError(f"unknown task state {state!r}")
    return {"state": state, "reason": reason}

def mark(task_id, state, reason="", path=None):
    return update(task_id, path=path, **marked(state, reason))

def cancel(task_id=None, path=None, reason="cancelled"):
    """Cancel one task, or every live one."""
    items = load(path)
    for t in items:
        if t["state"] in LIVE and (task_id is None or t["id"] == task_id):
            t["state"], t["reason"] = "cancelled", reason
    save(items, path)

def drop_done(path=None):
    """Drop everything that is no longer live."""
    save([t for t in load(path) if t["state"] in LIVE], path)

def goal_of(task) -> dict:
    return {"goal": task["goal"], "args": task.get("args", {})}

def describe_task(task):
    extra = f" ({task['reason']})" if task.get("reason") else ""
    return f"{task['id']} [{task['state']}] {goals.describe(goal_of(task))}{extra}"
