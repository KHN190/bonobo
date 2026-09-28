"""The cerebellum's loop: each round every layer proposes, the arbiter picks (L0 hazards and fights, then upkeep
reflexes and needs, then the queue's held plan, then idle stocking).

Failures go through retry.py (counted per task and cause, cooled per cause at a place); interruptions are not failures."""
import collections
import json
import os
import time
import traceback

from . import (api, arbiter, bag, decompose, dispatch, explore, goals, hazard, intent, nav, nether, paths, retry,
               needs, reflexes, tape, tasks, world)
from . import skill as skillkit
from . import craft, skillcore, survive
# every module that registers skills: a new one is added here only
from . import brewing, combat, end, farming, fluids, gather, loot, store, ui, wood  # noqa: F401,E402
from .api import GameUnreachable, McError, NotAvailable, PlayerTookControl, log
from .cost import Cost, Prices
from .data import HAND_MINEABLE_SUFFIX, bare
from .memory import Memory
from . import memory as _memory
_memory.TICK_READER = skillcore.game_time      # a look or note outside a round (bench achieve, CLI) reads the game's tick
from .planner import Unplannable, runnable
from .needs import bag_signature
from .world import Inventory, Snapshot, entities

# wired from the top so lower layers never import the skill library
hazard.SKILLS.update(find_air=lambda ctx: survive.find_air(ctx), unbury=lambda ctx: survive.unbury(ctx))
from . import fight_loop  # noqa: E402
fight_loop.lend("wall_in", lambda option, state: survive.pod_commands(state) if state.get("region") is not None else [],
                region=survive._pod_region)
fight_loop.lend("shoot", lambda option, state: combat.shoot_batch(
    option.target, (state["state"]["x"], state["state"]["y"] + 1.62, state["state"]["z"])))

IDLE_WAIT_TICKS = 100
IDLE_SLICE_TICKS = 20      # the idle wait is cut in 1 s slices: queued work ends it within a slice
SCAN_EVERY_S = 20  # seconds
TRACK_FILE = paths.data("track.jsonl")
# Step kinds a night under cover can carry on with (data.NIGHT_WORK). Everything else (a tree, an animal, a plan's wait for day) waits for morning while these are done — the night is not sat out while ore lies below.
from .data import NIGHT_WORK  # noqa: E402

def surface_closed(night, dimension):
    """Pure: surface work waits for morning at night in the Overworld, sheltered or not (caught in the open, it walked out to chop)."""
    return bool(night) and dimension == "minecraft:overworld"

def act_on_surface(act):
    """Pure: does this act's step walk the surface (arbiter.on_surface)? An act with no step (a chain, a whole
    skill) is judged by what it runs elsewhere: not flagged."""
    step = getattr(act, "step", None)
    return step is not None and arbiter.on_surface(step.kind)

def craft_run(steps, first):
    """Pure: `first` and the crafts straight after it: one table sitting, not one per round."""
    if first.kind != "craft" or first not in steps:
        return [first]
    run = []
    for st in steps[steps.index(first):]:
        if st.kind != "craft":
            break
        run.append(st)
    return run

class Act:
    """What the round decided: the layer, a name (the failure key), and what to run. `task`/`step` for queue work."""

    def __init__(self, layer, name, run, task=None, step=None, steps=None):
        self.layer, self.name, self.run, self.task, self.step = layer, name, run, task, step
        self.steps = steps or ([step] if step is not None else [])   # a craft run carries every craft it makes

    def __repr__(self):
        return f"{self.layer}: {self.name}" + (f" → {self.step}" if self.step else "")

class Brain:
    def __init__(self):
        self.mem = Memory()
        skillkit.STATS = self.mem     # skills record measured durations; the cost model reads them back
        nav.ROAD_MEM = self.mem       # travelled legs become a road network (roads.py) for later trips
        self.retry = retry.Retry()
        self.picks = collections.Counter()      # what the arbiter chose, by kind (arbiter.note_pick)
        self.blacklist = {}           # unreachable targets, shared by every round's Context and the cost model
        self.held = {}                # task id -> {"steps": [Step], "sig": bag signature, "event": bool, "dim": str}
        self.needs = needs.Needs(self)
        self.reflexes = reflexes.Maintain(self)
        from . import perception
        perception.IN_SITE = self.reflexes.in_site      # nightfall asks the night way's judgement, every Brain built
        self.policy_cache = nav.Policy(before_segment=self.segment_reflexes)
        self.place = None  # what causes are cooled against
        self.idle_since = None
        self.committed = None
        self.task_writes = None       # while task_act / after_step decide: the task's fields they change (writes)
        self.last_failure = None
        self.last_light = self.last_offhand = self.last_scan = self.last_track = self.last_hold_log = 0
        from . import fight_loop
        fight_loop.wire(self.mem, lambda snap: self.policy(snap, snap.night), self.blacklist,
                        prices=self.price_table)

    # -- movement policy and the hooks that run between chain segments
    def policy(self, snap, night):
        # without a usable pickaxe a dig route would raise ToolMissing mid-trip
        can_dig = any(d >= 3 for _, d, _ in snap.inv.tools("pickaxe"))
        return nav.Policy(allow_dig=True, hand_only=not can_dig, allow_surface=not night,
                          protected=self.mem.protected_cells(snap.dimension),
                          before_segment=self.segment_reflexes if can_dig else self.hand_segment_reflexes)

    def hand_segment_reflexes(self, tasks_):
        dug = [(t["x"], t["y"], t["z"]) for t in tasks_ if t.get("type") == "mine" and "x" in t]
        if dug:
            self.mem.mark_dirty_near(dug, api.get("/state")["dimension"])
        self.invariants()

    def segment_reflexes(self, tasks_):
        dug = [(t["x"], t["y"], t["z"]) for t in tasks_ if t.get("type") == "mine" and "x" in t]
        if dug:
            s = api.get("/state")
            self.mem.mark_dirty_near(dug, s["dimension"])
            # A working pickaxe unless every block is hand-mineable; failing here re-plans the tool first.
            region = world.region_around(dug, pad=0)
            needs_pick = region is None or any(not bare(region.name(c)).endswith(HAND_MINEABLE_SUFFIX) for c in dug)
            if needs_pick and not any(d >= 3 for _, d, _ in Inventory().tools("pickaxe")):
                raise skillcore.ToolMissing("pickaxe", 0)
            fluids.contain_lava(self.context(s["dimension"]))     # the last segment may have broken into lava
        self.invariants()

    def context(self, dimension, policy=None):
        return skillcore.Context(self.mem, policy or self.policy_cache, dimension, self.blacklist,
                              prices=self.price_table)

    # -- reflexes: invariants, not decisions
    def invariants(self):
        s = api.get("/state")
        if s["control"].get("paused"):
            api.wait_for_handback()
            s = api.get("/state")
        if skillcore.dead(s):
            # the carried items are unknowable after the respawn
            self.mem.log_death((s["blockX"], s["blockY"], s["blockZ"]), s["dimension"],
                               carried=[(x["id"], x.get("count", 1)) for x in Inventory().slots])
            log("died → respawning")
            api.post("/respawn")
            s = skillcore.settle(lambda: api.get("/state"), lambda st: not st.get("dead"), timeout=5.0, soft=True)
        if s["screen"] == "class_433":
            api.post("/resume")
        if s["dimension"] == "minecraft:the_nether":
            # a fireball hit within reach flies back
            for fb in entities(6, ["minecraft:fireball"]):
                if fb["distance"] <= 4.5:
                    try:
                        api.run({"type": "attack", "entity": fb["id"]}, wait=2, awaits="each fireball is one swing now, the next read decides the next (reflex latency)")
                    except McError:
                        pass
        inv = Inventory()
        head = bare((inv.equipment.get("head") or {}).get("id") or "")
        if s["screen"] == "none" and s["dimension"] == "minecraft:the_nether" and head != "golden_helmet" \
                and inv.count("minecraft:golden_helmet"):
            nether.wear_gold_helmet()      # gold on in the Nether (piglins), iron back outside
            head = "golden_helmet"
        gold_on_in_nether = s["dimension"] == "minecraft:the_nether" and head == "golden_helmet"
        if s["screen"] == "none" and not gold_on_in_nether and craft.better_armor_carried():
            craft.equip_armor()
        if s["screen"] == "none" and craft.shield_wanted_in_offhand() and time.time() - self.last_offhand > 30:
            self.last_offhand = time.time()
            craft.shield_to_offhand()
        if time.time() - self.last_light > 5 and survive.dark_here(s) and not survive.enclosed():
            self.last_light = time.time()
            try:
                survive.light_area(self.context(s["dimension"]), 4, 1)
            except api.INTERRUPTIONS:
                raise
            except McError:
                pass

    # -- failure policy (retry.py)
    def failed(self, name, err, quiet=False):
        """A failure counted for (name, cause), the cause cooled here; interruptions are not failures."""
        if outcome_of(err)[0] != "failed":
            return None
        self.mem.record_outcome(name, False)
        cause = retry.cause_of(err)
        self.reflexes.failed(cause, err, self.place)
        verdict = self.retry.failed(name, cause, str(err), time.time(), self.place)
        if verdict is not None and verdict.worth_logging and not quiet:
            log(f"{'~~' if isinstance(err, NotAvailable) else '!!'} {name}: {err} "
                f"({cause}, ×{verdict.n}; {cause} cools here for {verdict.wait}s)")
        return verdict

    def ready(self, name, cause=None):
        return self.retry.ready(name, time.time(), self.place, cause)

    def attempt(self, name, fn, also=()):
        """Run fn under the failure policy; returns "ok", "failed" or "interrupted". Failures also count under `also`."""
        self.last_failure = None
        try:
            fn()
            err = None
        except Exception as e:
            err, trace = e, traceback.format_exc()
        outcome, source = outcome_of(err)
        first = arbiter.resume_of(source)[1] if source is not None else None
        if outcome == "ok":
            self.retry.succeeded(name)
            self.mem.record_outcome(name, True)
        elif first == "handback":
            api.wait_for_handback()
        elif first == "wait_game":
            api.wait_for_game()
        elif first == "stand_down":
            log(f"?? {err}; standing down 10 s")
            time.sleep(10)
        elif first == "fight":
            log(f"   {name} interrupted by our fight: resuming when it ends")
            wait_out_fight()
        elif outcome == "interrupted":
            # died: recover next round, then replan; another dimension: the task waits for its own; neither counted
            log(f"   {name} interrupted ({source}{f', {first} first' if first else ''}): {err}")
        elif first == "cool":
            self.last_failure = self.failed(name, err)
            for key in also:
                self.failed(key, err, quiet=True)
            try:
                api.post("/stop")
            except McError:
                pass
        else:
            log("!! crash in " + name + "\n" + trace)
            self.retry.hold(name, 300, time.time())
        return outcome

    # -- one round
    def round(self):
        intent.clear()
        try:
            self._round()
        finally:
            intent.publish()

    def _round(self):
        """One round, its phases timed into one detail.log line (round_line): where an idle body's time goes."""
        clock = {"t0": time.perf_counter(), "marks": [], "ended": api.CLOCK["ended"]}
        api.CLOCK["first_post"] = None
        self._clock = clock
        try:
            self._round_body(clock)
        finally:
            now = time.perf_counter()
            post = api.CLOCK["first_post"]
            gap = (post - clock["ended"]) * 1000 if post is not None and clock["ended"] is not None else None
            api.detail(round_line(phase_ms(clock["t0"], clock["marks"], now), gap))

    def _mark(self, name):
        """End of a timed phase of this round."""
        c = getattr(self, "_clock", None)
        if c is not None:
            c["marks"].append((name, time.perf_counter()))

    def _round_body(self, clock):
        self.invariants()
        self._mark("inv")
        tape.begin()
        nav.forget_routes()
        snap = Snapshot()
        self._mark("snap")
        self.mem.clock = snap.state.get("gameTime")      # None on a jar before 0.1.39: notes then never expire
        self.mem.observe_phase(snap.night)
        self.place = retry.place_signature(snap.feet, snap.night)
        self.policy_cache = self.policy(snap, snap.night)
        protected = self.policy_cache.protected
        api.DRESS = lambda task: nav.with_avoid(task, protected)     # no approach digs through our own builds
        ctx = self.context(snap.dimension)
        self._mark("policy")
        self.needs.observe(snap)
        self.reflexes.observe(snap)
        self.track(snap)
        self._mark("observe")
        if time.time() - self.last_scan >= SCAN_EVERY_S:
            self.last_scan = time.time()
            explore.note_around(self.mem, snap.dimension, snap.feet)
        self._mark("note")
        act = self.decide(snap, ctx)
        if act is None:
            tape.end(self, None, snap)
            self.idle_since = self.idle_since or time.time()
            self.hold_log("nothing to do; waiting")
            intent.set("goal", "holding: nothing to do")
            jobs = self.mem.jobs(snap.dimension)
            if jobs:                           # only a furnace's clock is waited on: the bench may run it ahead
                api.waiting_for_clock(max(0.0, min(j["ready_at"] for j in jobs) - time.time()))
            self.idle_wait(lambda: any(t["state"] in tasks.LIVE for t in tasks.load()))
            return
        self.idle_since = None
        intent.set("goal" if act.layer in ("task", "idle") else "safety", repr(act))
        if act.layer == "L0":
            tape.end(self, act, snap)
            act.run()
            return
        box = {}
        also = (step_key(act.step),) if getattr(act, "step", None) is not None else ()
        ran = arbiter.BODY.drive("plan", lambda: box.update(outcome=self.attempt(act.name, act.run, also)), act.name)
        outcome = box.get("outcome", "interrupted") if ran else "interrupted"
        tape.event(act.name, outcome, str(self.last_failure.__dict__) if self.last_failure else "")
        tape.end(self, act, snap)
        if act.task is not None:
            write(act.task, self.after_step(act, outcome, Inventory))

    def idle_wait(self, work_queued):
        """Nothing to do: wait up to IDLE_WAIT_TICKS in IDLE_SLICE_TICKS slices, ending as soon as work is queued. The
        round that just closed the last task waits not at all — its proposing nothing was a pause for breath, and
        the next round decides at once (a 5 s wait there was the body idle between tasks)."""
        if self.just_finished:
            self.just_finished = False
            return 0
        slices = 0
        for _ in range(max(1, IDLE_WAIT_TICKS // IDLE_SLICE_TICKS)):
            api.run({"type": "wait", "ticks": IDLE_SLICE_TICKS}, wait=15, awaits="one task: a slice of the idle round's wait")
            slices += 1
            if work_queued():
                break
        return slices

    # -- deciding (nothing acts in here beyond queueing tasks)
    def decide(self, snap, ctx):
        """Every layer proposes, the arbiter chooses; nothing here ranks."""
        def fast():
            out = []
            if arbiter.BODY.holder() is not None or api.MODE == "survival":
                out.append(arbiter.Intent("tactic", Act("L0", "yield", lambda: time.sleep(0.5))))
            k = hazard.due(snap.state)
            if k is not None and self.ready(f"rescue {k}"):
                out.append(arbiter.Intent("safety", Act("L0", f"rescue {k}", lambda: hazard.handle(
                    ctx, snap.state, self.attempt, self.ready))))
            return out

        def upkeep():
            self.needs.propose(snap, ctx)
            out = [arbiter.Intent("maintain", Act("upkeep", name, run), seq=seq, key=name)
                   for seq, name, run in self.reflexes.proposals(snap, ctx)]
            for kind, goal, _why in self.needs.needs_now:
                act = self.need_act(kind, goal, snap, ctx)
                if act is not None:
                    out.append(arbiter.Intent("plan", act, kind=kind, key=f"{kind}: {goals.describe(goal)}",
                                              surface=act_on_surface(act)))
            return out

        # the gate's facts: what is cooling, and whether the surface is closed (met and unplannable needs are judged
        # where proposed, never intents)
        def facts_of(intents):
            return {"cooling": {i.key for i in intents if i.key and not self.ready(i.key)},
                    "surface_closed": surface_closed(snap.night, snap.dimension)}

        def timed(name, ask):
            def run():
                out = ask()
                self._mark(name)
                return out
            return run

        intents, facts = arbiter.first_live((timed("fast", fast), timed("upkeep", upkeep),
                                             timed("plan", lambda: self.plan_proposals(snap, ctx))), facts_of)
        chosen = arbiter.arbitrate(intents, facts=facts)
        self._mark("arb")
        arbiter.note_pick(self.__dict__.setdefault("picks", collections.Counter()), chosen)
        return chosen.action if chosen else None

    def plan_proposals(self, snap, ctx):
        """The queue's head (by night a step needing no sun), else night ore, waiting for day or idle stocking; asked after upkeep, which outranks it."""
        items = tasks.load()
        if tasks.expire(items):
            tasks.save(items)
        live = [t for t in items if t["state"] in tasks.LIVE]
        closed = surface_closed(snap.night, snap.dimension)
        self.just_finished = False
        for seq, task in enumerate(live):
            if not self.ready(f"task {task['id']}"):
                continue
            act, update = self.task_act(task, snap, ctx, Cost(snap, self.mem, self.blacklist,
                                                              policy=self.policy_cache))
            write(task, update)
            if act is not None:
                queued = arbiter.Intent("plan", act, kind="queue", seq=seq, key=f"task {task['id']}",
                                        surface=act_on_surface(act))
                if arbiter.viable(queued, {"surface_closed": closed}):
                    return [queued]     # a surface step at night: the next task's, or the night's own work
        if self.just_finished and not any(t["state"] in tasks.LIVE for t in tasks.load()):
            # the round that finished the last task proposes nothing: stocking in the same breath was momentum, not a decision
            return []
        if not closed:
            act = self.prepare(snap, ctx)
            return [arbiter.Intent("plan", act, kind="idle", key=act.name, surface=True)] if act else []
        out = [arbiter.Intent("plan", Act("idle", "wait for day", lambda: survive.wait_for_day(ctx)),
                              kind="wait for day")]
        if "pickaxe" in self.needs.working:
            act = self.night_stock(snap, ctx)
            if act is not None:
                out.append(arbiter.Intent("plan", act, kind="night stock"))
        return out

    def need_act(self, kind, goal, snap, ctx):
        """The first runnable step toward `goal` now, or None; planned each round, never queued (the queue is the player's)."""
        name = f"{kind}: {goals.describe(goal)}"
        if not self.ready(name):
            return None
        cost = Cost(snap, self.mem, self.blacklist, policy=self.policy_cache)
        try:
            steps = decompose.decompose(snap.inv, goal, cost, pending=self.mem.pending_outputs(snap.dimension))
        except Unplannable:
            return None
        closed = surface_closed(snap.night, snap.dimension)
        step = next((st for st in steps if self.valid(st, snap, ctx) and not (closed and arbiter.on_surface(st.kind))),
                    None)
        if step is None:
            return None
        return Act("upkeep", name, lambda: dispatch.execute(ctx, step, snap.night), step=step)

    # -- the queue: hold a plan, check it cheaply, repair it on events
    def task_act(self, task, snap, ctx, cost):
        """The queue's decision for one task, IO outside: (act or None, task fields to write — applied by the caller right after)."""
        return self._collecting(lambda: self._task_act(task, snap, ctx, cost))

    def _collecting(self, decide):
        """(decide(), the task fields it changed): task writes in between are kept, not written."""
        self.task_writes = {}
        try:
            return decide(), self.task_writes
        finally:
            self.task_writes = None

    def _write(self, task, **fields):
        """Kept while deciding, written at once otherwise."""
        if self.task_writes is not None:
            self.task_writes.update(fields)
        else:
            tasks.update(task["id"], **fields)

    def _task_act(self, task, snap, ctx, cost):
        goal = tasks.goal_of(task)
        # reconcile: the remainder is read each round ({} = done); the held plan is a cache of how, never a count
        rest = goals.remainder(goal, snap, self.mem)
        finished = None if rest is None else not rest
        if finished:
            self.finish(task, "done", "")
            return None
        held = self.held.get(task["id"])
        if held is None and task.get("plan"):
            # a plan saved before a restart is a hint, checked against the bag like any event
            held = {"steps": [decompose.from_dict(d) for d in task["plan"]], "sig": None, "event": True,
                    "dim": snap.dimension}
        if held is None or held["event"] or held.get("want") != rest or held["dim"] != snap.dimension:
            held = self.repair(task, goal, snap, held, cost)
            if held is None:
                return None
            held["want"] = rest
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
        step = next((s for s in held["steps"] if self.valid(s, snap, ctx)), None)
        if step is None:
            # same bag, same plan: re-solving every round ran nothing, so the step cools until the next event
            self.fail_step(task, NotAvailable("no step of the plan can run from here"))
            return None
        self.committed = task["id"]
        # never consume our own work: what held plans pass through is kept from tidying and storing
        bag.RESERVED = set().union(*(bag.reserved_ids(h["steps"]) for h in self.held.values())) \
            | bag.reserved_ids([], goals.needs(goal, snap.inv))
        run = craft_run(held["steps"], step)
        if len(run) > 1:
            recipes = [(s.token, s.detail.get("times", s.count)) for s in run]
            return Act("task", f"task {task['id']}", lambda: craft.craft_chain(ctx, recipes), task=task, step=step,
                       steps=run)
        return Act("task", f"task {task['id']}", lambda: dispatch.execute(ctx, step, snap.night), task=task, step=step)

    def valid(self, step, snap, ctx=None):
        """The cheap per-round check: inputs held, and the skill's own preconditions pass."""
        return runnable(step, snap.inv) and self.ready(step_key(step)) and (ctx is None or dispatch.can_start(ctx, step))

    def repair(self, task, goal, snap, held, cost):
        """Bring the held plan up to date: run-once goals keep what is left (a road walks on); item goals are re-solved from the bag."""
        if held is not None and goal["goal"] in goals.RUN_ONCE:
            held.update(event=False, sig=bag_signature(snap.inv), dim=snap.dimension)
            self.held[task["id"]] = held
            return held
        held, why = replan(task, goal, snap, cost, self.mem.pending_outputs(snap.dimension))
        if held is None:
            self.fail_task(task, why)
            return None
        steps = held["steps"]
        self.held[task["id"]] = held
        self._write(task, state="running", plan=[decompose.to_dict(s) for s in steps])
        tape.event(f"task {task['id']}", "plan", " → ".join(map(str, steps)))
        if steps:
            api.detail(f"   plan for {tasks.describe(task)}: " + " → ".join(map(str, steps)))
        return held

    def after_step(self, act, outcome, bag_now):
        """The held plan after a step's outcome; returns the task fields to write. `bag_now()` is read only on success."""
        return self._collecting(lambda: self._after_step(act, outcome, bag_now))[1]

    def _after_step(self, act, outcome, bag_now):
        task = act.task
        held = self.held.get(task["id"])
        if held is None:
            return
        if outcome == "ok":
            for st in act.steps:
                if st in held["steps"]:
                    held["steps"].remove(st)
            held["sig"] = bag_signature(bag_now())
            self._write(task, plan=[decompose.to_dict(s) for s in held["steps"]])
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
        """Fail the task with its reason; the cerebrum decides what next."""
        log(f"?? task {tasks.describe(task)} failed: {reason}")
        self.finish(task, "failed", reason)

    def finish(self, task, state, reason):
        self.just_finished = True
        self._write(task, **tasks.marked(state, reason))
        self.held.pop(task["id"], None)
        self.retry.succeeded(f"task {task['id']}")
        if state == "done":
            log(f"task done: {tasks.describe(task)}")

    # -- nothing queued
    def prepare(self, snap, ctx):
        """Idle: a proposal toward the first of tools, food, light not held — never a task (queued, it took over whenever the row's task cooled)."""
        for needs in goals.PREPARE:
            if goals.short(snap.inv, [tuple(n) for n in needs]):
                act = self.need_act("idle", goals.have(*needs), snap, ctx)
                if act is not None:
                    return act
        return None

    def night_stock(self, snap, ctx):
        """Night under cover, queue idle: a proposal to dig for the first ore not held."""
        needs = next((n for n in goals.NIGHT_STOCK if goals.short(snap.inv, [tuple(x) for x in n])),
                     goals.NIGHT_STOCK[-1])
        return self.need_act("night stock", goals.have(*needs), snap, ctx)

    def price_table(self, snap=None):
        """{item: seconds to get one another way}, for skills that ask what a thing is worth."""
        try:
            snap = snap or Snapshot()
        except McError:
            return {}
        return Prices(Cost(snap, self.mem, self.blacklist, policy=self.policy_cache), snap.inv)

    # -- bookkeeping
    def track(self, snap):
        now = time.time()
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

    def hold_log(self, text):
        if time.time() - self.last_hold_log > 60:
            self.last_hold_log = time.time()
            log(text)

FIGHT_POLL_S, FIGHT_WAIT_MAX_S = 0.5, 60.0

def wait_out_fight(sleep=time.sleep, now=time.monotonic):
    """Poll until our own fight lets the body go, at most FIGHT_WAIT_MAX_S. Returns the seconds waited."""
    from . import fight_loop
    began = now()
    while (fight_loop.engaged() is not None or arbiter.BODY.holder() is not None) \
            and now() - began < FIGHT_WAIT_MAX_S:
        sleep(FIGHT_POLL_S)
    return now() - began

def step_key(step):
    """Pure: a step's failure key, shared by every goal that plans it (a failed gather is not retried for the next goal)."""
    return f"step:{step.kind}:{step.token}"

ROUND_PHASES = ("inv", "snap", "policy", "observe", "note", "fast", "upkeep", "plan", "arb", "act")

def phase_ms(t0, marks, end):
    """Pure: {phase: ms} from the round's start, its (phase, time) marks in order, and its end; the time after the
    last mark is "act" (the dispatch). A phase marked twice adds up."""
    out, last = {}, t0
    for name, t in marks:
        out[name] = out.get(name, 0.0) + (t - last) * 1000
        last = t
    out["act"] = out.get("act", 0.0) + (end - last) * 1000
    out["t"] = (end - t0) * 1000
    return out

def round_line(ms, gap_ms):
    """Pure: the round's detail.log line — `round t=… inv=… … act=… gap=…` in whole ms; a phase not reached is left
    out, gap "-" when no task ended before the round or none was posted in it."""
    parts = [f"t={ms['t']:.0f}"] + [f"{k}={ms[k]:.0f}" for k in ROUND_PHASES if k in ms]
    return "round " + " ".join(parts) + " gap=" + ("-" if gap_ms is None else f"{gap_ms:.0f}")

def write(task, fields):
    """Apply a decision's task writes (task_act, after_step) to the task file: one update, nothing when unchanged."""
    if fields:
        tasks.update(task["id"], **fields)

def outcome_of(err):
    """Pure: (outcome, interrupt source); "interrupted" when the source's rule resumes the work — no count, no /stop, no cooldown."""
    if err is None:
        return "ok", None
    source = retry.source_of(err)
    return ("interrupted" if arbiter.resume_of(source)[0] else "failed"), source

def replan(task, goal, snap, cost, pending=None):
    """Pure given the cost: (held, None), or (None, why) when no solver can plan `goal`."""
    try:
        solver = task.get("solver") or goals.SOLVER_FOR.get(goal["goal"]) or decompose.ORDER[0]
        steps = decompose.decompose(snap.inv, goal, cost, solver=solver, pending=pending)
    except Unplannable:
        try:
            steps = decompose.decompose(snap.inv, goal, cost, solver=None, pending=pending)
        except Unplannable as e:
            return None, f"unplannable: {e}"
    return {"steps": steps, "sig": bag_signature(snap.inv), "event": False, "dim": snap.dimension}, None

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
    perception.start()  # ~5 Hz
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
