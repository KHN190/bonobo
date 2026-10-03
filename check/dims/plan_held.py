"""plan_held: what the brain holds for the queue's first task from an earlier round (brain.held: the plan cache
_task_act checks cheaply and makes again from the world on events, K4) — none; a plan marked for repair (an event); a
plan run out while the goal is still short (waiting, or failed); a plan none of whose steps can run (the step cools); a
step of it just ran (the next round plans again from the world, no step count kept); a road held after an event. Read
only over the task values it is made for."""
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


def held_for(value, goal, snap, mem, key=None, blacklist=None):
    """The held plan of `value` for `goal` (brain.held's shape: steps, sig, event, dim, want), each step priced by the
    production cost model over the round's readings (its look, perception's ground) as the planner prices its own
    (Step.est: D6 reads it)."""
    from bonobo.cost import Cost
    from bonobo.planner import from_bag, price_as_run
    from typing import cast
    out = _held_for(value, goal, snap, mem, key)
    cost = Cost(snap, mem, blacklist)
    steps = cast(list, out["steps"])
    for st, est in zip(steps, price_as_run(steps, list(from_bag(snap.inv, reserved=cost.reserved).tools), cost)):
        st.est = est                 # as the plan runs: the tools its earlier steps make (planner.plan_round)
    return out


def _held_for(value, goal, snap, mem, key=None):
    from bonobo.bag import bag_signature
    from bonobo.decompose import Step
    out = {"sig": bag_signature(snap.inv), "event": False, "dim": snap.dimension, "want": key, "hand_made": True}
    if value == "event":
        return dict(out, steps=[_iron_pickaxe()], event=True)
    if value == "emptied":
        return dict(out, steps=[])
    if value == "stuck":
        # its inputs are not in the bag: runnable() refuses it (brain.valid)
        return dict(out, steps=[_iron_pickaxe({"minecraft:iron_ingot": 3, "minecraft:stick": 2})])
    if value == "ran":
        return dict(out, steps=[Step("skill", goal["args"]["name"], 1, {"args": list(goal["args"].get("args", []))})],
                    ran=True)
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
    from bonobo import tasks
    from bonobo.knowledge import SOURCE_BLOCKS
    from bonobo.survive import ROUND_GROUND
    from bonobo.world import Snapshot
    from bonobo.brain import round_key
    tid = task_id()
    live = [t for t in tasks.load() if t["state"] in tasks.LIVE]
    seq, task = next((i, t) for i, t in enumerate(live) if t["id"] == tid)
    snap = Snapshot.read(SOURCE_BLOCKS, ROUND_GROUND)    # the round's own reading (check/round.py reads the same)
    brain.held[tid] = held_for(facts["plan_held"], tasks.goal_of(task), snap, brain.mem,
                               round_key([(f"task {tid}", tasks.goal_of(task), seq)], snap, brain.mem), brain.blacklist)


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
    if h.get("ran"):
        return "ran"
    if not h["steps"]:
        return "emptied"
    return "stuck"


def gamma(value, facts, g):
    pass                 # the brain's own state: set by prepare
