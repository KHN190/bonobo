"""The cerebellum's loop. A fixed order, no scores — the first layer that has something to do takes the round:

  1. the player holds control               → wait
  2. L0: a hazard on the body (hazard.py), or a fight holding it (fight_loop.py) → rescue / yield
  3. reflexes (reflexes.py), needs (needs.py) → eat, sleep, shelter, land, furnace jobs, bag, path blocked, stuck — or
                                              put a task at the front of the queue (food before it runs out, a bed
                                              before dark, a tool that broke, blocks to bridge with)
  4. the queue's head (tasks.py)            → hold its plan (decompose.py); each round only a cheap check of the next
                                              step; repair the plan on an event (failed, interrupted, the bag changed
                                              under it); every solver gets a go only when repair cannot plan. A
                                              step is carried out by dispatch.py
  5. nothing queued                         → prepare (a pickaxe, a sword, food, torches), or wait

Failures go through retry.py: counted per (task, cause), cooled per cause at a place, reported upward (the task is
marked failed with its reason) after three sources. Interruptions are not failures.
"""
import collections
import json
import os
import time
import traceback

from . import (api, arbiter, bag, decompose, dispatch, explore, goals, hazard, intent, nav, nether, paths, retry,
               needs, reflexes, skills, tape, tasks, world)
from . import skill as skillkit
from . import skillcore
# Every module that registers skills, so each step finds its provider (skill.provider). A new skill module is added
# here and nowhere else.
from . import brewing, combat, end, farming, fluids, loot, ui  # noqa: F401,E402
from .api import GameUnreachable, McError, NotAvailable, PlayerTookControl, log
from .cost import Cost, Prices
from .data import HAND_MINEABLE_SUFFIX, bare
from .memory import Memory
from .planner import Unplannable, runnable
from .needs import bag_signature
from .world import Inventory, Snapshot, entities

# Wiring from the top, so the lower layers never import the skill library: the L0 rescues hazard.py dispatches, and
# pod's command batch as the fight answer "wall_in", the bow's as "shoot".
hazard.SKILLS.update(find_air=lambda ctx: skills.find_air(ctx), unbury=lambda ctx: skills.unbury(ctx))
from . import fight_loop  # noqa: E402
fight_loop.lend("wall_in", lambda option, state: skills.pod_commands(state) if state.get("region") is not None else [],
                region=skills._pod_region)
fight_loop.lend("shoot", lambda option, state: combat.shoot_batch(
    option.target, (state["state"]["x"], state["state"]["y"] + 1.62, state["state"]["z"])))

IDLE_WAIT_TICKS = 100
SCAN_EVERY_S = 20          # seconds between travel scans (explore.note_around)
TRACK_FILE = paths.data("track.jsonl")
# Step kinds a night under cover can carry on with (data.NIGHT_WORK). Everything else (a tree, an animal, a plan's
# wait for day) waits for morning while these are done — the night is not sat out while ore lies below.
from .data import NIGHT_WORK  # noqa: E402


def surface_closed(night, dimension):
    """Pure: surface work (a tree, an animal) waits for morning — night in the Overworld, sheltered or not. Only
    under cover was the rule once, and a night caught in the open (the shelter row cooling) walked out to chop."""
    return bool(night) and dimension == "minecraft:overworld"


def craft_run(steps, first):
    """Pure: the crafts made in one sitting — `first` and the craft steps straight after it in the plan (a step of
    another kind ends the run). One craft per round opened and closed the table every time."""
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
        self.needs = needs.Needs(self)          # what must be planned to be had (PLAN proposals)
        self.reflexes = reflexes.Maintain(self)  # the fixed maintenance reflexes (MAINTAIN)
        self.policy_cache = nav.Policy(before_segment=self.segment_reflexes)
        self.place = None             # coarse location: what causes are cooled against
        self.idle_since = None
        self.committed = None         # the task being worked on
        self.last_failure = None      # the Verdict of the last failed attempt
        self.last_light = self.last_offhand = self.last_scan = self.last_track = self.last_hold_log = 0
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
                raise skills.ToolMissing("pickaxe", 0)
            skills.contain_lava(self.context(s["dimension"]))     # the last segment may have broken into lava
        self.invariants()

    def context(self, dimension, policy=None):
        return skills.Context(self.mem, policy or self.policy_cache, dimension, self.blacklist,
                              prices=self.price_table)

    # -- reflexes: invariants, not decisions
    def invariants(self):
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
            s = skillcore.settle(lambda: api.get("/state"), lambda st: not st.get("dead"), timeout=5.0, soft=True)
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
        if time.time() - self.last_light > 5 and skills.dark_here(s) and not skills.enclosed():
            self.last_light = time.time()
            try:
                skills.light_area(self.context(s["dimension"]), 4, 1)     # one torch where we stand in the dark
            except api.INTERRUPTIONS:
                raise
            except McError:
                pass

    # -- failure policy (retry.py)
    def failed(self, name, err):
        """A failure: counted for (name, cause), the cause cooled here. Interruptions are not failures."""
        if api.interrupted(err):
            return None
        self.mem.record_outcome(name, False)
        cause = retry.cause_of(err)
        self.reflexes.failed(cause, err, self.place)
        verdict = self.retry.failed(name, cause, str(err), time.time(), self.place)
        if verdict is not None and verdict.worth_logging:
            log(f"{'~~' if isinstance(err, NotAvailable) else '!!'} {name}: {err} "
                f"({cause}, ×{verdict.n}; {cause} cools here for {verdict.wait}s)")
        return verdict

    def ready(self, name, cause=None):
        return self.retry.ready(name, time.time(), self.place, cause)

    def attempt(self, name, fn):
        """Run fn under the failure policy (`outcome_of`). Returns "ok", "failed" or "interrupted"."""
        self.last_failure = None
        try:
            fn()
            err = None
        except Exception as e:
            err, trace = e, traceback.format_exc()
        outcome, then = outcome_of(err)
        if outcome == "ok":
            self.retry.succeeded(name)
            self.mem.record_outcome(name, True)
        elif then == "handback":
            api.wait_for_handback()
        elif then == "wait_game":
            api.wait_for_game()
        elif then == "stand_down":
            log(f"?? {err}; standing down 10 s")
            time.sleep(10)
        elif then is None:
            log(f"   {name} interrupted: {err}")     # no count, no /stop, no cooldown
        elif then == "stop":
            self.last_failure = self.failed(name, err)
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
        self.invariants()
        tape.begin()
        nav.forget_routes()
        snap = Snapshot()
        self.mem.clock = snap.state.get("gameTime")      # None on a jar before 0.1.39: notes then never expire
        self.mem.observe_phase(snap.night)
        self.place = retry.place_signature(snap.feet, snap.night)
        self.policy_cache = self.policy(snap, snap.night)
        protected = self.policy_cache.protected
        api.DRESS = lambda task: nav.with_avoid(task, protected)     # no approach digs through our own builds
        ctx = self.context(snap.dimension)
        self.needs.observe(snap)
        self.reflexes.observe(snap)
        self.track(snap)
        if time.time() - self.last_scan >= SCAN_EVERY_S:
            self.last_scan = time.time()
            explore.note_around(self.mem, snap.dimension)
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
        """Every layer proposes, the arbiter chooses (arbiter.arbitrate: layer, then arbiter.PLAN_ORDER). Nothing
        here ranks: a fight or a rescue holding the body is a faster layer; a hazard is SAFETY; upkeep's rows, the
        queue's head, the night's work under cover and idle stocking are PLAN proposals of their own kind."""
        intents = []
        if arbiter.BODY.holder() is not None or api.MODE == "survival":
            # a fight or a rescue holds the body
            intents.append(arbiter.Intent("tactic", Act("L0", "yield", lambda: time.sleep(0.5))))
        k = hazard.due(snap.state)
        if k is not None and self.ready(f"rescue {k}"):
            intents.append(arbiter.Intent("safety", Act("L0", f"rescue {k}", lambda: hazard.handle(
                ctx, snap.state, self.attempt, self.ready))))
        if not intents:
            self.needs.propose(snap, ctx)
            intents += [arbiter.Intent("maintain", Act("upkeep", name, run), seq=seq, key=name)
                        for seq, name, run in self.reflexes.proposals(snap, ctx)]
            for kind, goal, _why in self.needs.needs_now:
                act = self.need_act(kind, goal, snap, ctx)
                if act is not None:
                    intents.append(arbiter.Intent("plan", act, kind=kind, key=f"{kind}: {goals.describe(goal)}"))
        if not intents:
            intents += self.plan_proposals(snap, ctx)
        # The round's facts for the gate (arbiter.gate): what is cooling under the retry policy, by the name it
        # failed under. Met and unplannable needs never become intents (need_act answers None for them).
        facts = {"cooling": {i.key for i in intents if i.key and not self.ready(i.key)}}
        chosen = arbiter.arbitrate(intents, facts=facts)
        arbiter.note_pick(self.picks, chosen)
        return chosen.action if chosen else None

    def plan_proposals(self, snap, ctx):
        """The queue's head (the first task with a step that can run now: by night, a step that needs no sun —
        data.NIGHT_WORK), and what is proposed when the queue has nothing: the night's ore underground with a
        pickaxe, waiting for day, or idle stocking. Asked only when upkeep proposed nothing: the queue ranks after
        every upkeep row (arbiter.PLAN_ORDER), so asking it earlier would only repair plans for nothing."""
        items = tasks.load()
        if tasks.expire(items):
            tasks.save(items)
        live = [t for t in items if t["state"] in tasks.LIVE]
        closed = surface_closed(snap.night, snap.dimension)
        for seq, task in enumerate(live):
            if not self.ready(f"task {task['id']}"):
                continue
            act = self.task_act(task, snap, ctx)
            if act is not None and (not closed or act.step.kind in NIGHT_WORK):
                return [arbiter.Intent("plan", act, kind="queue", seq=seq, key=f"task {task['id']}")]
        if not closed:
            act = self.prepare(snap)
            return [arbiter.Intent("plan", act, kind="idle")] if act else []
        out = [arbiter.Intent("plan", Act("idle", "wait for day", lambda: skills.wait_for_day(ctx)),
                              kind="wait for day")]
        if "pickaxe" in self.needs.working:
            act = self.night_stock(snap, ctx)
            if act is not None:
                out.append(arbiter.Intent("plan", act, kind="night stock"))
        return out

    def need_act(self, kind, goal, snap, ctx):
        """A proposal to get `goal` now (an upkeep need, the night's ore): the first step of its plan from this bag
        that can run here — by night one that needs no sun — or None (met, unplannable, nothing runnable, cooling).
        Planned each round from the bag, never queued: the queue is the player's and the cerebrum's."""
        name = f"{kind}: {goals.describe(goal)}"
        if not self.ready(name):
            return None
        cost = Cost(snap, self.mem, self.blacklist, policy=self.policy_cache)
        try:
            steps = decompose.decompose(snap.inv, goal, cost, pending=self.mem.pending_outputs(snap.dimension))
        except Unplannable:
            return None
        closed = surface_closed(snap.night, snap.dimension)
        step = next((st for st in steps if self.valid(st, snap, ctx) and (not closed or st.kind in NIGHT_WORK)), None)
        if step is None:
            return None
        return Act("upkeep", name, lambda: dispatch.execute(ctx, step, snap.night), step=step)

    def upkeep(self, snap, ctx):
        """The reflex the arbiter picks this round (reflexes.Maintain.act), as an act."""
        got = self.reflexes.act(snap, ctx)
        return Act("upkeep", *got) if got else None

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
        step = next((s for s in held["steps"] if self.valid(s, snap, ctx)), None)
        if step is None:
            held["event"] = True
            held = self.repair(task, goal, snap, held)
            step = next((s for s in held["steps"] if self.valid(s, snap, ctx)), None) if held else None
            if step is None:
                if held is not None:
                    self.fail_step(task, NotAvailable("no step of the plan can run from here"))
                return None
        self.committed = task["id"]
        # Never consume our own work: what the held plans pass through is kept out of tidying and storing.
        bag.RESERVED = set().union(*(bag.reserved_ids(h["steps"]) for h in self.held.values())) \
            | bag.reserved_ids([], goals.needs(goal, snap.inv))
        run = craft_run(held["steps"], step)
        if len(run) > 1:
            recipes = [(s.token, s.detail.get("times", s.count)) for s in run]
            return Act("task", f"task {task['id']}", lambda: skills.craft_chain(ctx, recipes), task=task, step=step,
                       steps=run)
        return Act("task", f"task {task['id']}", lambda: dispatch.execute(ctx, step, snap.night), task=task, step=step)

    def valid(self, step, snap, ctx=None):
        """The cheap check made every round: the step's inputs are in this bag, and the skill that would carry it
        out passes its own declared preconditions (`dispatch.can_start` → `skill.can_run`)."""
        return runnable(step, snap.inv) and (ctx is None or dispatch.can_start(ctx, step))

    def repair(self, task, goal, snap, held):
        """Bring the held plan up to date with the world. Run-once goals keep what is left of theirs (a road half
        walked is walked on, not restarted); item goals are recomputed from the bag by the task's solver, which
        skips whatever is already held. Only when that cannot plan does every registered solver get a go."""
        if held is not None and goal["goal"] in goals.RUN_ONCE:
            held.update(event=False, sig=bag_signature(snap.inv), dim=snap.dimension)
            self.held[task["id"]] = held
            return held
        held, why = replan(task, goal, snap, Cost(snap, self.mem, self.blacklist, policy=self.policy_cache),
                           self.mem.pending_outputs(snap.dimension))
        if held is None:
            self.fail_task(task, why)
            return None
        steps = held["steps"]
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
            for st in act.steps:
                if st in held["steps"]:
                    held["steps"].remove(st)
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

    def night_stock(self, snap, ctx):
        """Night under cover, nothing in the queue to do there: a proposal to dig down for the first ore not held
        (goals.NIGHT_STOCK) — its next step, not a task."""
        needs = next((n for n in goals.NIGHT_STOCK if goals.short(snap.inv, [tuple(x) for x in n])),
                     goals.NIGHT_STOCK[-1])
        return self.need_act("night stock", goals.have(*needs), snap, ctx)

    def price_table(self, snap=None):
        """{item: seconds to get one another way}, for skills that ask what a thing is worth (the looter)."""
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

    def survival(self, snap, ctx):
        """The bench's L0 entry (scenarios): the rescue for whatever hazard is on the body now."""
        return hazard.handle(ctx, snap.state, self.attempt, self.ready)


def outcome_of(err):
    """Pure: what an exception out of an attempt means — (outcome, what to do about it). Interruptions are not
    failures: no count, no /stop, no cooldown."""
    if err is None:
        return "ok", None
    if isinstance(err, PlayerTookControl):
        return "interrupted", "handback"
    if isinstance(err, GameUnreachable):
        return "interrupted", "wait_game"
    if isinstance(err, api.BodyContested):
        return "interrupted", "stand_down"
    if isinstance(err, api.INTERRUPTIONS):
        return "interrupted", None
    if isinstance(err, (McError, skills.ToolMissing)):
        return "failed", "stop"
    return "failed", "crash"


def replan(task, goal, snap, cost, pending=None):
    """Pure given the cost model: a fresh held plan for `goal` from this bag — the task's solver, else every
    registered one. (held, None), or (None, why) when nothing can plan it."""
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
