"""The task queue (L3): what the cerebrum wants done, in the order it wants it. One file, MC_DATA/tasks.json.

A task is {id, goal, args, state, reason, created, expires, source, plan}. State is one of STATES; several tasks may
be pending, and the first live one in the list is the one worked on. The brain may put upkeep tasks at the front
(a bed before dark, food before it runs out); everything else is ordered by whoever wrote the file.

`plan` is the plan the brain holds for the task, as step dicts: it is saved with the task so work half done
survives a restart, and it is only ever a hint — on resume the brain checks it against the bag and repairs it
(steps are amounts to hold, not "chop this tree").
One door in, and it is a queue.
"""
import json
import os
import time

from . import goals, paths

FILE = paths.data("tasks.json", env="MC_TASKS")
STATES = ("pending", "running", "done", "failed", "cancelled")
LIVE = ("pending", "running")

def load(path=None):
    try:
        with open(path or FILE) as f:
            return json.load(f).get("tasks", [])
    except (OSError, ValueError):
        return []

def save(items, path=None):
    path = path or FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"tasks": items}, f, indent=1)
    os.replace(tmp, path)

def add(goal, expires_s=None, front=False, source="cerebrum", path=None, now=None):
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

def update(task_id, path=None, **fields):
    """Change one task's fields in the file (state, reason, plan). Returns the task, or None when it is gone."""
    items = load(path)
    for t in items:
        if t["id"] == task_id:
            t.update(fields)
            save(items, path)
            return t
    return None

def marked(state, reason=""):
    """Pure: the fields a task in `state` gets (`reason`, and no plan once it is not live)."""
    if state not in STATES:
        raise ValueError(f"unknown task state {state!r}")
    fields = {"state": state, "reason": reason}
    if state not in LIVE:
        fields["plan"] = None
    return fields

def mark(task_id, state, reason="", path=None):
    return update(task_id, path=path, **marked(state, reason))

def cancel(task_id=None, path=None, reason="cancelled"):
    """Cancel one task, or every live one."""
    items = load(path)
    for t in items:
        if t["state"] in LIVE and (task_id is None or t["id"] == task_id):
            t["state"], t["reason"], t["plan"] = "cancelled", reason, None
    save(items, path)

def clear(path=None):
    """Drop everything that is no longer live."""
    save([t for t in load(path) if t["state"] in LIVE], path)

def goal_of(task):
    return {"goal": task["goal"], "args": task.get("args", {})}

def describe(task):
    extra = f" ({task['reason']})" if task.get("reason") else ""
    return f"{task['id']} [{task['state']}] {goals.describe(goal_of(task))}{extra}"
