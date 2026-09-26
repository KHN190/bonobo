"""The cerebellum's loop. A fixed order, no scores — the first layer that has something to do takes the round:

  1. the player holds control               → wait
  2. L0: a hazard on the body (hazard.py), or a fight holding it (fight_loop.py) → rescue / yield
  3. upkeep, one table (`upkeep`)           → eat, sleep, shelter, land, furnace jobs, bag, stuck — or put a task at
                                              the front of the queue (food before it runs out, a bed before dark, a
                                              pickaxe when it broke)
  4. the queue's head (tasks.py)            → hold its plan (decompose.py); each round only a cheap check of the next
                                              step; repair the plan on an event (failed, interrupted, the bag changed
                                              under it); every solver gets a go only when repair cannot plan
  5. nothing queued                         → prepare (a pickaxe, a sword, food, torches), or wait

Failures go through retry.py: counted per (task, cause), cooled per cause at a place, reported upward (the task is
marked failed with its reason) after three sources. Interruptions are not failures.
"""
import json
import math
import os
import time
import traceback

from . import (api, arbiter, blueprints, decompose, goals, hazard, intent, knowledge, nav, nether, paths, retry,
               skills, tape, tasks, world)
from . import skill as skillkit
from . import skillcore
# Every module that registers skills, so each step finds its provider (skill.provider). A new skill module is added
# here and nowhere else.
from . import brewing, combat, end, farming, fluids, loot, ui, upkeep  # noqa: F401,E402
from .api import GameUnreachable, McError, NotAvailable, PlayerTookControl, log
from .cost import Cost, Prices
from .data import BASE_MARKERS, COVERED_SKY, GROUPS, HAND_MINEABLE_SUFFIX, bare, mid  # noqa: F401
from .knowledge import FIND_AT, food_count
from .memory import Memory
from .planner import Unplannable, runnable
from .world import Inventory, Snapshot, entities, find

LEAD = 1.5                 # how much earlier than a plan's own seconds its upkeep starts: the one margin
EAT_BELOW = 14             # hunger points: eat below this, while there is something to eat
FOOD_POINTS = 6.0          # hunger points one cooked item restores, roughly
BAG_FULL = 34              # slots used before the bag is emptied
DAY_TICKS_END = 12000      # dusk, in timeOfDay ticks
IDLE_WAIT_TICKS = 100
JOB_RANGE = 96
STUCK_LIMIT = 60           # seconds in the same block with the same bag → unstuck
PLAN_S_TTL = 20            # seconds a "how long would that take" answer is kept
TRACK_FILE = paths.data("track.jsonl")


def bag_signature(inv):
    """What the bag holds, exactly: a change the plan did not make is an event."""
    return tuple(sorted((s["id"], s.get("count", 1)) for s in inv.slots))


def dusk_s(snap):
    """Seconds until dark: (12000 − timeOfDay) / 20, 0 once it is dark."""
    t = int(snap.time) % 24000
    return max(0.0, (DAY_TICKS_END - t) / 20.0) if t < DAY_TICKS_END else 0.0


def food_lasts_s(snap):
    """Seconds of work the stomach and the meals in the bag cover (`risk.food_drain_s` per hunger point)."""
    from . import beliefs
    drain = float(beliefs.value("risk.food_drain_s"))
    return (float(snap.get("food", 20)) + FOOD_POINTS * food_count(snap.inv)) * drain


def nether_retreat(snap):
    """In the Nether, head home through the portal when food, health or bag room run low. Pure."""
    if snap.dimension != "minecraft:the_nether":
        return None
    inv, s = snap.inv, snap.state
    if food_count(inv) < 4:
        return "food running out"
    if s.get("health", 20) <= 8:
        return "health low"
    if inv.used_slots() >= 35:
        return "bag full"
    return None


class Act:
    """What the round decided: the layer, a name (the failure key), and what to run. `task`/`step` for queue work."""

    def __init__(self, layer, name, run, task=None, step=None):
        self.layer, self.name, self.run, self.task, self.step = layer, name, run, task, step

    def __repr__(self):
        return f"{self.layer}: {self.name}" + (f" → {self.step}" if self.step else "")


class Brain:
    def __init__(self):
        self.mem = Memory()
        skillkit.STATS = self.mem     # skills record measured durations; the cost model reads them back
        nav.ROAD_MEM = self.mem       # travelled legs become a road network (roads.py) for later trips
        self.retry = retry.Retry()
        self.blacklist = {}           # unreachable targets, shared by every round's Context and the cost model
        self.ban_counts = skillcore._BAN_COUNTS
        self.held = {}                # task id -> {"steps": [Step], "sig": bag signature, "event": bool, "dim": str}
        self.plan_s_cache = {}        # (goal json, bag signature) -> (time, seconds)
        self.policy_cache = nav.Policy(before_segment=self.segment_reflexes)
        self.place = None             # coarse location: what causes are cooled against
        self.idle_since = None
        self.committed = None         # the task being worked on
        self.last_failure = None      # the Verdict of the last failed attempt
        self.history = []             # (time, feet, bag signature) for "stuck in place"
        self.last_light = self.last_offhand = self.last_scan = self.last_track = self.last_hold_log = 0
        self.escalated = {}
        from . import fight_loop
        fight_loop.wire(self.mem, lambda snap: self.policy(snap, snap.night), self.blacklist,
                        prices=self.price_table)

    # -- movement policy and the hooks that run between chain segments
    def policy(self, snap, night):
        # Without a usable pickaxe, movement walks only: a dig route would raise ToolMissing mid-trip.
        can_dig = any(d >= 3 for _, d, _ in snap.inv.tools("pickaxe"))
        return nav.Policy(allow_dig=True, hand_only=not can_dig, allow_surface=not night,
                          protected=self.mem.protected_cells(snap.dimension),
                          before_segment=self.segment_reflexes if can_dig else self.hand_segment_reflexes)

    def hand_segment_reflexes(self, tasks_):
        dug = [(t["x"], t["y"], t["z"]) for t in tasks_ if t.get("type") == "mine" and "x" in t]
        if dug:
            self.mem.mark_dirty_near(dug, api.get("/state")["dimension"])
        self.reflexes()

    def segment_reflexes(self, tasks_):
        dug = [(t["x"], t["y"], t["z"]) for t in tasks_ if t.get("type") == "mine" and "x" in t]
        if dug:
            s = api.get("/state")
            self.mem.mark_dirty_near(dug, s["dimension"])
            # A working pickaxe unless every block is hand-mineable; failing here re-plans the tool first.
            region = world.region_around(dug, pad=0)
            needs_pick = region is None or any(not bare(region.name(c)).endswith(HAND_MINEABLE_SUFFIX) for c in dug)
            if needs_pick and not any(d >= 3 for _, d, _ in Inventory().tools("pickaxe")):
                raise skills.ToolMissing("pickaxe", 0)
            skills.contain_lava(self.context(s["dimension"]))     # the last segment may have broken into lava
        self.reflexes()

    def context(self, dimension, policy=None):
        return skills.Context(self.mem, policy or self.policy_cache, dimension, self.blacklist,
                              prices=self.price_table)

    # -- reflexes: invariants, not decisions
    def reflexes(self):
        s = api.get("/state")
        if s["control"].get("paused"):
            api.wait_for_handback()
            s = api.get("/state")
        if skillcore.dead(s):
            # What was on us is what makes the walk back worth anything; after the respawn it is unknowable.
            self.mem.log_death((s["blockX"], s["blockY"], s["blockZ"]), s["dimension"],
                               carried=[(x["id"], x.get("count", 1)) for x in Inventory().slots])
            log("died → respawning")
            api.post("/respawn")
            time.sleep(2)
            s = api.get("/state")
        if s["screen"] == "class_433":
            api.post("/resume")
        if s["dimension"] == "minecraft:the_nether":
            # Ghast fireballs: hit one that comes within reach and it flies back.
            for fb in entities(6, ["minecraft:fireball"]):
                if fb["distance"] <= 4.5:
                    try:
                        api.run({"type": "attack", "entity": fb["id"]}, wait=2)
                    except McError:
                        pass
        inv = Inventory()
        head = bare((inv.equipment.get("head") or {}).get("id") or "")
        if s["screen"] == "none" and s["dimension"] == "minecraft:the_nether" and head != "golden_helmet" \
                and inv.count("minecraft:golden_helmet"):
            nether.wear_gold_helmet()      # gold on in the Nether (piglins), iron back outside
            head = "golden_helmet"
        gold_on_in_nether = s["dimension"] == "minecraft:the_nether" and head == "golden_helmet"
        if s["screen"] == "none" and not gold_on_in_nether and skills.better_armor_carried():
            skills.equip_armor()
        if s["screen"] == "none" and skills.shield_wanted_in_offhand() and time.time() - self.last_offhand > 30:
            self.last_offhand = time.time()
            skills.shield_to_offhand()
        if time.time() - self.last_light > 5 and not skills.enclosed():
            if skills.place_torch_if_dark(self.context(s["dimension"])):
                self.last_light = time.time()

    # -- failure policy (retry.py)
    def failed(self, name, err):
        """A failure: counted for (name, cause), the cause cooled here. Interruptions are not failures."""
        if api.interrupted(err):
            return None
        self.mem.record_outcome(name, False)
        cause = retry.cause_of(err)
        verdict = self.retry.failed(name, cause, str(err), time.time(), self.place)
        if verdict is not None and verdict.worth_logging:
            log(f"{'~~' if isinstance(err, NotAvailable) else '!!'} {name}: {err} "
                f"({cause}, ×{verdict.n}; {cause} cools here for {verdict.wait}s)")
        return verdict

    def ready(self, name, cause=None):
        return self.retry.ready(name, time.time(), self.place, cause)

    def attempt(self, name, fn):
        """Run fn under the failure policy. Returns "ok", "failed" or "interrupted"."""
        self.last_failure = None
        try:
            fn()
            self.retry.succeeded(name)
            self.mem.record_outcome(name, True)
            return "ok"
        except PlayerTookControl:
            api.wait_for_handback()
        except GameUnreachable:
            api.wait_for_game()
        except api.BodyContested as e:
            log(f"?? {e}; standing down 10 s")
            time.sleep(10)
        except api.INTERRUPTIONS as e:
            log(f"   {name} interrupted: {e}")     # no count, no /stop, no cooldown
        except (McError, skills.ToolMissing) as e:
            self.last_failure = self.failed(name, e)
            try:
                api.post("/stop")
            except McError:
                pass
            return "failed"
        except Exception:
            log("!! crash in " + name + "\n" + traceback.format_exc())
            self.retry.hold(name, 300, time.time())
            return "failed"
        return "interrupted"

    def banned(self, pos):
        exp = self.blacklist.get(tuple(pos))
        return exp is not None and exp > time.time()

    def ban(self, pos, seconds=600):
        """The same blacklist and escalation as `Context.ban`."""
        skillcore.Context.ban(self, pos, seconds)

    # -- one round
    def round(self):
        intent.clear()
        try:
            self._round()
        finally:
            intent.publish()

    def _round(self):
        self.reflexes()
        tape.begin()
        nav.forget_routes()
        snap = Snapshot()
        self.mem.observe_phase(snap.night)
        self.place = retry.place_signature(snap.feet, snap.night)
        self.policy_cache = self.policy(snap, snap.night)
        ctx = self.context(snap.dimension)
        self.track(snap)
        self.scan_resources(snap)
        act = self.decide(snap, ctx)
        if act is None:
            tape.end(self, None, snap)
            self.idle_since = self.idle_since or time.time()
            self.hold_log("nothing to do; waiting")
            intent.set("goal", "holding: nothing to do")
            api.run({"type": "wait", "ticks": IDLE_WAIT_TICKS}, wait=15)
            return
        self.idle_since = None
        intent.set("goal" if act.layer in ("task", "idle") else "safety", repr(act))
        if act.layer == "L0":
            tape.end(self, act, snap)
            act.run()
            return
        box = {}
        ran = arbiter.BODY.drive("plan", lambda: box.update(outcome=self.attempt(act.name, act.run)), act.name)
        outcome = box.get("outcome", "interrupted") if ran else "interrupted"
        tape.event(act.name, outcome, str(self.last_failure.__dict__) if self.last_failure else "")
        tape.end(self, act, snap)
        if act.task is not None:
            self.after_step(act, outcome)

    # -- deciding (nothing acts in here beyond queueing tasks)
    def decide(self, snap, ctx):
        if arbiter.BODY.holder() is not None or api.MODE == "survival":
            return Act("L0", "yield", lambda: time.sleep(0.5))     # a fight or a rescue holds the body
        k = hazard.due(snap.state)
        if k is not None and self.ready(f"rescue {k}"):
            return Act("L0", f"rescue {k}", lambda: hazard.handle(ctx, snap.state, self.attempt, self.ready))
        act = self.upkeep(snap, ctx)
        if act is not None:
            return act
        items = tasks.load()
        if tasks.expire(items):
            tasks.save(items)
        for task in [t for t in items if t["state"] in tasks.LIVE]:
            if not self.ready(f"task {task['id']}"):
                continue
            act = self.task_act(task, snap, ctx)
            if act is not None:
                return act
        return self.prepare(snap)

    # -- upkeep: one table, the first row that applies
    def upkeep(self, snap, ctx):
        s, inv, over = snap.state, snap.inv, snap.dimension == "minecraft:overworld"
        rows = [
            ("recover items", lambda: self.mem.recent_death(snap.dimension) is not None, lambda: _recover(ctx)),
            ("eat", lambda: s.get("food", 20) < EAT_BELOW and skills.edible_carried(inv),
             lambda: skills.eat(raw_ok=not food_count(inv))),
            ("reach land", lambda: skills.swimming(s), lambda: skills.reach_land(ctx)),
            ("leave the Nether", lambda: nether_retreat(snap) is not None,
             lambda: nether.use_portal(ctx, "minecraft:overworld")),
            ("dig out", lambda: not snap.night and skills.enclosed(), lambda: skills.dig_out(ctx)),
            ("sleep", lambda: over and snap.night and skills.can_sleep(s) is None
             and bool(inv.count("bed") > 0 or find(BASE_MARKERS["bed"], radius=48, limit=1)),
             lambda: skills.sleep(ctx, self.policy(snap, True))),
            ("shelter", lambda: over and snap.night and not self.sheltered(snap), lambda: self.shelter(snap, ctx)),
            ("collect job", lambda: self.ready_job(snap) is not None, lambda: self.collect_job(snap, ctx)),
            ("empty the bag", lambda: inv.used_slots() >= BAG_FULL, lambda: self.empty_bag(snap, ctx)),
            ("unstuck", lambda: self.stuck_in_place(snap), lambda: self.unstuck(snap, ctx)),
        ]
        for name, due, run in rows:
            if self.ready(name) and due():
                return Act("upkeep", name, run)
        # Rows that only queue work: the queue does it, at the front.
        if not any(d >= 3 for _, d, _ in inv.tools("pickaxe")):
            self.urgent(goals.have(("tool", "pickaxe", 0)), "no working pickaxe")
        food_goal = goals.have(("food", 8))
        if food_count(inv) < 8 and food_lasts_s(snap) < self.plan_s(food_goal, snap) * LEAD:
            self.urgent(food_goal, "food runs out before more could be had")
        bed_goal = goals.have(("bed", 1))
        if over and not snap.night and inv.count("bed") == 0 \
                and dusk_s(snap) < self.plan_s(bed_goal, snap) * LEAD:
            self.urgent(bed_goal, "dark before a bed could be made")
        return None

    def urgent(self, goal, why):
        task = tasks.add(goal, front=True, source="upkeep", expires_s=1800)
        if task.get("created", 0) >= time.time() - 1:
            log(f"upkeep: {goals.describe(goal)} to the front ({why})")

    def plan_s(self, goal, snap):
        """Seconds the plan for `goal` would take from this bag (Σ Step.est), kept briefly."""
        key = (json.dumps(goal, sort_keys=True), bag_signature(snap.inv))
        hit = self.plan_s_cache.get(key)
        if hit and time.time() - hit[0] < PLAN_S_TTL:
            return hit[1]
        cost = Cost(snap, self.mem, self.blacklist)
        try:
            seconds = cost.plan_s(decompose.decompose(snap.inv, goal, cost))
        except Unplannable:
            seconds = math.inf
        self.plan_s_cache[key] = (time.time(), seconds)
        return seconds

    # -- the queue: hold a plan, check it cheaply, repair it on events
    def task_act(self, task, snap, ctx):
        goal = tasks.goal_of(task)
        finished = goals.done(goal, snap, self.mem)
        if finished:
            self.finish(task, "done", "")
            return None
        held = self.held.get(task["id"])
        if held is None and task.get("plan"):
            # Work half done before a restart: the saved plan is a hint, checked against the bag like any event.
            held = {"steps": [decompose.from_dict(d) for d in task["plan"]], "sig": None, "event": True,
                    "dim": snap.dimension}
        if held is None or held["event"] or held["sig"] != bag_signature(snap.inv) or held["dim"] != snap.dimension:
            held = self.repair(task, goal, snap, held)
            if held is None:
                return None
        if not held["steps"]:
            if finished is None:                  # a run-once goal whose plan has run
                self.finish(task, "done", "")
                return None
            waiting = goals.short(snap.inv, goals.needs(goal, snap.inv))
            if self.mem.jobs(snap.dimension):
                self.hold_log(f"{tasks.describe(task)}: waiting on a furnace for {waiting}")
                return None
            self.fail_task(task, f"nothing left to plan, still short of {waiting}")
            return None
        step = next((s for s in held["steps"] if self.valid(s, snap)), None)
        if step is None:
            held["event"] = True
            held = self.repair(task, goal, snap, held)
            step = next((s for s in held["steps"] if self.valid(s, snap)), None) if held else None
            if step is None:
                if held is not None:
                    self.fail_step(task, NotAvailable("no step of the plan can run from here"))
                return None
        self.committed = task["id"]
        return Act("task", f"task {task['id']}", lambda: self.execute(ctx, step, snap.night), task=task, step=step)

    def valid(self, step, snap):
        """The cheap check made every round: the step's own preconditions hold in this bag."""
        return runnable(step, snap.inv)

    def repair(self, task, goal, snap, held):
        """Bring the held plan up to date with the world. Run-once goals keep what is left of theirs (a road half
        walked is walked on, not restarted); item goals are recomputed from the bag by the task's solver, which
        skips whatever is already held. Only when that cannot plan does every registered solver get a go."""
        if held is not None and goal["goal"] in goals.RUN_ONCE:
            held.update(event=False, sig=bag_signature(snap.inv), dim=snap.dimension)
            self.held[task["id"]] = held
            return held
        cost = Cost(snap, self.mem, self.blacklist)
        pending = self.mem.pending_outputs(snap.dimension)
        try:
            steps = decompose.decompose(snap.inv, goal, cost, solver=task.get("solver") or decompose.ORDER[0],
                                        pending=pending)
        except Unplannable:
            try:
                steps = decompose.decompose(snap.inv, goal, cost, solver=None, pending=pending)
            except Unplannable as e:
                self.fail_task(task, f"unplannable: {e}")
                return None
        held = {"steps": steps, "sig": bag_signature(snap.inv), "event": False, "dim": snap.dimension}
        self.held[task["id"]] = held
        tasks.update(task["id"], state="running", plan=[decompose.to_dict(s) for s in steps])
        tape.event(f"task {task['id']}", "plan", " → ".join(map(str, steps)))
        if steps:
            api.detail(f"   plan for {tasks.describe(task)}: " + " → ".join(map(str, steps)))
        return held

    def after_step(self, act, outcome):
        """What the step's outcome means for the held plan."""
        task = act.task
        held = self.held.get(task["id"])
        if held is None:
            return
        if outcome == "ok":
            if act.step in held["steps"]:
                held["steps"].remove(act.step)
            held["sig"] = bag_signature(Inventory())
            tasks.update(task["id"], plan=[decompose.to_dict(s) for s in held["steps"]])
            return
        held["event"] = True                          # failed or interrupted: repair before the next step
        verdict = self.last_failure
        if outcome == "failed" and verdict is not None and verdict.escalate:
            cause, message = self.retry.exhausted(act.name) or ("error", "failed")
            self.fail_task(task, f"{cause}: {message}")

    def fail_step(self, task, err):
        verdict = self.failed(f"task {task['id']}", err)
        if verdict is not None and verdict.escalate:
            self.fail_task(task, f"{retry.cause_of(err)}: {err}")

    def fail_task(self, task, reason):
        """The report upward: the task is failed, and says why. The cerebrum decides what next."""
        log(f"?? task {tasks.describe(task)} failed: {reason}")
        self.finish(task, "failed", reason)

    def finish(self, task, state, reason):
        tasks.mark(task["id"], state, reason)
        self.held.pop(task["id"], None)
        self.retry.succeeded(f"task {task['id']}")
        if state == "done":
            log(f"task done: {tasks.describe(task)}")

    # -- nothing queued
    def prepare(self, snap):
        """Idle: queue the first of tools, food, light that is not held; else nothing (the round waits)."""
        for needs in goals.PREPARE:
            if goals.short(snap.inv, [tuple(n) for n in needs]):
                goal = goals.have(*needs)
                if not self.ready(f"prepare {goals.describe(goal)}"):
                    continue
                tasks.add(goal, source="idle", expires_s=900)
                return Act("idle", f"prepare {goals.describe(goal)}", lambda: None)
        return None

    # -- executing one plan step
    SEEK_KINDS = ("mine", "gather", "hunt")      # steps whose "nothing in range" is answered by looking elsewhere

    def execute(self, ctx, step, night):
        log(f"   → {step}")
        key = f"{step.kind}:{step.token}"
        try:
            out = self.run_step(ctx, step, night)
            if isinstance(out, NotAvailable):
                if not self.go_find(ctx, step):
                    raise out
                out = self.run_step(ctx, step, night, seek=False)
        except GameUnreachable:
            raise
        except api.INTERRUPTIONS:
            raise      # no statistics
        except (McError, skills.ToolMissing) as e:
            self.mem.record_outcome(f"nav:{step.kind}" if retry.cause_of(e) == "nav" else key, False)
            raise
        if not (isinstance(out, dict) and "ordered" in out):
            self.mem.record_outcome(key, True)   # a furnace loaded is not a step done: that is when it is held

    def run_step(self, ctx, step, night, seek=True):
        """Carry out one step with the skill that provides it (`skill.provider`): no switch here, so a new skill is
        one decorated function. A seeking step that finds nothing in range returns the NotAvailable instead of
        raising it, so `execute` can look elsewhere first."""
        ctx.night = night
        if step.kind == "skill":
            if step.token not in skillkit.REGISTRY:
                raise McError(f"{step.token} is not a registered skill")
            runner, args = skillkit.REGISTRY[step.token].runner, tuple(step.detail.get("args", ()))
        else:
            found = skillkit.provider(ctx, step)
            if found is None:
                raise McError(f"no skill provides {step.kind} {step.token}")
            runner, args = found
        try:
            return runner(ctx, *args)
        except NotAvailable as e:
            if seek and step.kind in self.SEEK_KINDS and not isinstance(e, api.NavFailed):
                return e
            raise

    def go_find(self, ctx, step):
        """Where to look when nothing is in range, in a fixed order: half-done work (a trunk left standing, a vein
        left open), what memory says is there, the depth the kind is richest at (knowledge.FIND_AT), a spiral.
        True when the body got somewhere new to look."""
        here, dim = nav.feet_now(), ctx.dimension
        blocks = list(step.detail.get("blocks", ()))
        partial = {"gather": ["tree"], "mine": [f"vein:{b}" for b in blocks[:1]]}.get(step.kind, [])
        for kind in partial:
            for spot in self.mem.progress(dim, kind=kind, near=here, within=128):
                if math.dist(spot["pos"], here) > 4 and nav.arrived(tuple(spot["pos"]), ctx.policy, range_=4):
                    return True
        names = {"gather": ["tree"], "mine": blocks, "hunt": list(step.detail.get("types", ()))}.get(step.kind, [])
        spots = [tuple(p) for n in names for p in self.mem.resources(bare(n), dim)]
        spots += [tuple(x["pos"]) for n in names for x in self.mem.sightings(n, dim)]
        spots = sorted((p for p in spots if not self.banned(p) and math.dist(p, here) > 4),
                       key=lambda p: math.dist(p, here))
        for spot in spots[:2]:
            if nav.arrived(spot, ctx.policy, range_=4):
                return True
            self.ban(spot)
        token = "log" if step.kind == "gather" else ("food" if step.kind == "hunt" else mid(step.token))
        depth = FIND_AT.get(token)
        if depth is not None and abs(here[1] - depth) > 6 and dim == "minecraft:overworld":
            try:
                return nav.arrive((here[0], depth, here[2]), ctx.policy, range_=3)
            except api.NavFailed:
                pass
        if step.kind == "hunt":
            return bool(skills.explore_for(ctx, list(step.detail["types"])))
        return bool(skills.seek_blocks(ctx, GROUPS["log"] if step.kind == "gather" else blocks))

    # -- upkeep actions
    def shelter(self, snap, ctx):
        """Night, exposed, no bed to sleep in: under the ground with a pickaxe, else a hut, else walls."""
        if any(d >= 3 for _, d, _ in snap.inv.tools("pickaxe")):
            try:
                return skills.dig_in(self.context(snap.dimension, self.policy(snap, True)))
            except api.INTERRUPTIONS:
                raise
            except McError as e:
                log(f"   dig-in failed: {e} → a hut or walls")
        if not skills.materials_missing(blueprints.SHELTER):
            return skills.build_shelter(ctx)
        return skills.pod(ctx)

    def sheltered(self, snap):
        if snap.get("skyLight", 15) <= COVERED_SKY:
            return True
        try:
            if skills.enclosed():
                return True
        except (tape.ReplayMiss, McError):
            pass
        feet = list(snap.feet)
        return any(feet in s.get("interior", []) for s in self.mem.sites(snap.dimension))

    def ready_job(self, snap):
        near = [j for j in self.mem.jobs(snap.dimension)
                if skills.job_ready(j) and math.dist(j["pos"], snap.feet) <= JOB_RANGE]
        return min(near, key=lambda j: math.dist(j["pos"], snap.feet), default=None)

    def collect_job(self, snap, ctx):
        from . import jobs
        job = self.ready_job(snap)
        if job is not None:
            jobs.collect(ctx, job)

    def empty_bag(self, snap, ctx):
        if skills.store_plan(snap.inv.slots) and skills.can_store_here(ctx, local_only=snap.night):
            return skills.deposit(ctx, local_only=snap.night)
        return skills.tidy_inventory(ctx)

    def nearest_machine(self, tag, max_dist=64):
        s = api.get("/state")
        here = (s["blockX"], s["blockY"], s["blockZ"])
        options = [m for m in self.mem.machines(s["dimension"], tag) if math.dist(m["origin"], here) <= max_dist]
        return min(options, key=lambda m: math.dist(m["origin"], here), default=None)

    def price_table(self, snap=None):
        """{item: seconds to get one another way}, for skills that ask what a thing is worth (the looter)."""
        try:
            snap = snap or Snapshot()
        except McError:
            return {}
        return Prices(Cost(snap, self.mem, self.blacklist), snap.inv)

    # -- bookkeeping
    SCAN_EVERY_S = 20
    SCAN_BLOCKS = {"tree": ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log"],
                   "water": ["water"], "lava": ["lava"], "iron": ["iron_ore", "deepslate_iron_ore"],
                   "coal": ["coal_ore", "deepslate_coal_ore"]}
    TAKE_BLOCKS = knowledge.takeable_blocks()
    SCAN_MOBS = ("minecraft:sheep", "minecraft:cow", "minecraft:pig", "minecraft:chicken")

    def scan_resources(self, snap):
        """Map resources while travelling: every 20 s note the nearest tree, water, lava, iron and coal in 48 blocks
        and the animals in sight, so "where to find" starts from known places."""
        now = time.time()
        if now - self.last_scan < self.SCAN_EVERY_S:
            return
        self.last_scan = now
        try:
            for kind, blocks in self.SCAN_BLOCKS.items():
                hits = find(blocks, radius=48, limit=1)
                if hits:
                    h = hits[0]
                    if kind == "lava":
                        self.mem.add_lava(h, snap.dimension)
                    else:
                        self.mem.note_resource(kind, (h["x"], h["y"], h["z"]), snap.dimension)
            for h in find(self.TAKE_BLOCKS, radius=48, limit=16) or ():
                self.mem.note_resource(bare(h["block"]), (h["x"], h["y"], h["z"]), snap.dimension)
            for e in entities(48, list(self.SCAN_MOBS)):
                self.mem.add_sighting(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), snap.dimension)
        except McError as e:
            api.swallowed("scan_resources: looking around", e)

    def track(self, snap):
        now = time.time()
        self.history = [h for h in self.history if now - h[0] <= STUCK_LIMIT + 30]
        self.history.append((now, snap.feet, bag_signature(snap.inv)))
        if now - self.last_track < 60:
            return
        self.last_track = now
        live = [tasks.describe(t) for t in tasks.load() if t["state"] in tasks.LIVE][:6]
        try:
            with open(TRACK_FILE, "a") as f:
                f.write(json.dumps({"t": int(now), "pos": list(snap.feet), "tasks": live,
                                    "cooling": self.retry.cooling_now(now)[:12]}) + "\n")
        except OSError:
            pass

    def stuck_in_place(self, snap):
        """Same block and the same bag for STUCK_LIMIT seconds (sheltered at night excluded)."""
        if snap.night and self.sheltered(snap):
            return False
        old = [h for h in self.history if time.time() - h[0] >= STUCK_LIMIT]
        if not old:
            return False
        ref = old[-1]
        return all(math.dist(h[1], ref[1]) < 2 and h[2] == ref[2] for h in self.history if h[0] >= ref[0])

    def unstuck(self, snap, ctx):
        """One way out per call — the nearest site, up, sideways, down — each skipped once it failed here."""
        x, y, z = snap.feet
        site = self.mem.nearest_site(snap.feet, snap.dimension)
        if site and math.dist(site["pos"], snap.feet) < 12:
            site = None
        methods = ([("site", tuple(site["pos"]))] if site else []) + [
            ("up", (x, y + 12, z)), ("east", (x + 16, y, z)), ("west", (x - 16, y, z)),
            ("south", (x, y, z + 16)), ("north", (x, y, z - 16)), ("down", (x, y - 8, z))]
        for label, target in methods:
            name = f"unstuck:{label}"
            if not self.ready(name):
                continue
            log(f"no progress for {STUCK_LIMIT}s at {snap.feet} → unstuck by heading {label} {target}")
            self.history.clear()
            if nav.go_to(target, self.policy(snap, snap.night), range_=3, attempts=1):
                self.retry.succeeded(name)
                return
            self.failed(name, NotAvailable(f"could not get {label} to {target}"))
            raise NotAvailable(f"unstuck {label} failed")
        self.escalate("stuck", f"every unstuck method failed at {snap.feet}")
        raise NotAvailable("every unstuck method failed here")

    def escalate(self, kind, what):
        """A macro problem: one `?? STALL` line per kind per 20 min — supervise.sh wakes Claude on it."""
        now = time.time()
        if now - self.escalated.get(kind, 0) < 1200:
            return
        self.escalated[kind] = now
        log(f"?? STALL {kind}: {what}")

    def hold_log(self, text):
        if time.time() - self.last_hold_log > 60:
            self.last_hold_log = time.time()
            log(text)

    def survival(self, snap, ctx):
        """The bench's L0 entry (scenarios): the rescue for whatever hazard is on the body now."""
        return hazard.handle(ctx, snap.state, self.attempt, self.ready)


def _recover(ctx):
    from . import upkeep
    return upkeep.recover_items(ctx)


def code_version():
    """Short hash of the package source, logged at start so a review can tell which code is running."""
    import glob
    import hashlib
    h = hashlib.sha1()
    for path in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "*.py"))):
        with open(path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:10]


def autoplay(hours):
    import fcntl
    lock_file = paths.data("autoplay.lock")
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        log("!! another autoplay process is already running; aborting")
        return
    log(f"autoplay start: brain code {code_version()}")
    from . import perception
    perception.start()   # hazards and hostiles, ~5 Hz
    brain = Brain()
    deadline = time.time() + hours * 3600
    while time.time() < deadline:
        try:
            brain.round()
        except PlayerTookControl:
            api.wait_for_handback()
        except GameUnreachable:
            api.wait_for_game()
        except McError as e:
            log(f"!! round: {e}")
            time.sleep(2)
        except Exception:
            log("!! crash in round\n" + traceback.format_exc())
            time.sleep(10)
    try:
        api.post("/release")
    except McError:
        pass
