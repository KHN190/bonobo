"""plan_held: what the brain holds for the queue's first task from an earlier round (brain.held: the plan cache
_task_act checks cheaply and repairs on events) — none; a plan marked for repair (an event); a plan run out while
the goal is still short (waiting, or failed); a plan none of whose steps can run (the step cools); a run-once goal
whose plan has run (done); a road with legs left after an event (repair keeps them). Read only over the task values
it is made for."""
NAME = "plan_held"
VALUES = ("none", "event", "emptied", "stuck", "ran", "walking", "other_dim", "craft_run", "replan_fails")
# other_dim: held in the other dimension (repaired on arrival); craft_run: crafts in a row from carried logs, short of
# cobblestone half-way, a later craft needing the table (brain.craft_run, keeps_table); replan_fails: an event on a
# task no solver can plan (repair → replan → Unplannable: the task fails)
FOR = {"event": "tool", "emptied": "tool", "stuck": "tool", "ran": "skill", "walking": "road", "other_dim": "tool",
       "craft_run": "tool", "replan_fails": "build_unknown"}
DEPENDS = (lambda f: f["task"] in set(FOR.values()), {"task": "tool"})


def domain():
    return VALUES


def valid(value, facts):
    return value == "none" or FOR[value] == facts["task"]


def _iron_pickaxe(inputs=None):
    from bonobo.decompose import Step
    return Step("craft", "minecraft:iron_pickaxe", 1, {"times": 1, "inputs": dict(inputs or {})})


def task_id():
    """The id of the task the `task` dimension reads (check/dims/task._of): another dimension's task (a quarry's hunt)
    may stand before it in the queue, so it is never assumed to be t1."""
    from bonobo import tasks
    from . import task as task_dim
    return next((t["id"] for t in tasks.load() if t["state"] in tasks.LIVE and task_dim._of(t) is not None), None)


def craft_chain():
    """planks ← logs, sticks ← planks, a stone pickaxe ← cobblestone + sticks (at a table; no cobblestone carried:
    the run stops there), then a furnace (a table again: the one placed is kept)."""
    from bonobo.decompose import Step
    # each craft step as the planner makes it (planner._craft: `times` crafts of the recipe, the inputs for them)
    return [Step("craft", "minecraft:oak_planks", 4, {"times": 1, "inputs": {"minecraft:oak_log": 1}}),
            Step("craft", "minecraft:stick", 4, {"times": 1, "inputs": {"minecraft:oak_planks": 2}}),
            Step("craft", "minecraft:stone_pickaxe", 1,
                 {"times": 1, "inputs": {"minecraft:cobblestone": 3, "minecraft:stick": 2}}),
            Step("mine", "minecraft:cobblestone", 8, {"blocks": ["minecraft:stone"], "tier": 0}),
            Step("craft", "minecraft:furnace", 1, {"times": 1, "inputs": {"minecraft:cobblestone": 8}})]


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
    if value == "other_dim":
        other = "minecraft:the_nether" if snap.dimension == "minecraft:overworld" else "minecraft:overworld"
        return dict(out, steps=[_iron_pickaxe()], dim=other)
    if value == "craft_run":
        return dict(out, steps=craft_chain())
    if value == "replan_fails":
        return dict(out, steps=[Step("build", goal["args"]["bp"], 1, {})], event=True)
    return dict(out, steps=[Step("goto", "pos", 1, {"pos": list(goal["args"]["b"]), "range": 4.0})], event=True)


def prepare(brain, facts):
    if facts["plan_held"] == "none":
        return
    from bonobo import api, tasks
    from bonobo.world import Inventory, Snapshot
    tid = task_id()
    task = next(t for t in tasks.load() if t["id"] == tid)
    snap = Snapshot.from_readings(api.get("/state"), Inventory())
    brain.held[tid] = held_for(facts["plan_held"], tasks.goal_of(task), snap, brain.mem)


def alpha(a):
    h = getattr(a.brain, "held", {}).get(task_id()) if a.brain is not None else None
    if h is None:
        return "none"
    if h["dim"] != a.snap.dimension:
        return "other_dim"
    if h["event"]:
        if any(s.kind == "build" for s in h["steps"]):
            return "replan_fails"
        return "walking" if any(s.kind == "goto" for s in h["steps"]) else "event"
    if len(h["steps"]) > 1 and all(s.kind in ("craft", "mine") for s in h["steps"]):
        return "craft_run"
    if not h["steps"]:
        return "ran" if h["want"] is None else "emptied"
    return "stuck"


def gamma(value, facts, g):
    pass                 # the brain's own state: set by prepare
