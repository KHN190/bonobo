"""plan_held: what the brain holds for the queue's first task from an earlier round (brain.held: the plan cache
_task_act checks cheaply and repairs on events) — none; a plan marked for repair (an event); a plan run out while
the goal is still short (waiting, or failed); a plan none of whose steps can run (the step cools); a run-once goal
whose plan has run (done); a road with legs left after an event (repair keeps them). Read only over the task values
it is made for."""
NAME = "plan_held"
VALUES = ("none", "event", "emptied", "stuck", "ran", "walking")
FOR = {"event": "tool", "emptied": "tool", "stuck": "tool", "ran": "skill", "walking": "road"}
TASK = "t1"          # the first task's id (tasks.add numbers from 1): the task dimension's only task here
DEPENDS = (lambda f: f["task"] in set(FOR.values()), {"task": "tool"})


def domain():
    return VALUES


def valid(value, facts):
    return value == "none" or FOR[value] == facts["task"]


def _iron_pickaxe(inputs=None):
    from bonobo.decompose import Step
    return Step("craft", "minecraft:iron_pickaxe", 1, {"inputs": dict(inputs or {})})


def held_for(value, goal, snap, mem):
    """The held plan of `value` for `goal` (brain.held's shape: steps, sig, event, dim, want)."""
    from bonobo import goals
    from bonobo.decompose import Step
    rest = goals.remainder(goal, snap, mem)
    out = {"sig": None, "event": False, "dim": snap.dimension, "want": rest}
    if value == "event":
        return dict(out, steps=[_iron_pickaxe()], event=True)
    if value == "emptied":
        return dict(out, steps=[])
    if value == "stuck":
        # its inputs are not in the bag: runnable() refuses it (brain.valid)
        return dict(out, steps=[_iron_pickaxe({"minecraft:iron_ingot": 3, "minecraft:stick": 2})])
    if value == "ran":
        return dict(out, steps=[])
    return dict(out, steps=[Step("goto", "pos", 1, {"pos": list(goal["args"]["b"]), "range": 4.0})], event=True)


def prepare(brain, facts):
    if facts["plan_held"] == "none":
        return
    from bonobo import api, tasks
    from bonobo.world import Inventory, Snapshot
    task = next(t for t in tasks.load() if t["id"] == TASK)
    snap = Snapshot.from_readings(api.get("/state"), Inventory())
    brain.held[TASK] = held_for(facts["plan_held"], tasks.goal_of(task), snap, brain.mem)


def alpha(a):
    h = getattr(a.brain, "held", {}).get(TASK) if a.brain is not None else None
    if h is None:
        return "none"
    if h["event"]:
        return "walking" if any(s.kind == "goto" for s in h["steps"]) else "event"
    if not h["steps"]:
        return "ran" if h["want"] is None else "emptied"
    return "stuck"


def gamma(value, facts, g):
    pass                 # the brain's own state: set by prepare
