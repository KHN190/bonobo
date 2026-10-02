"""The cerebellum's loop: each round every layer proposes, the arbiter picks (L0 hazards and fights, then upkeep
reflexes and needs, then the queue's held plan, then idle stocking).

Failures go through retry.py (counted per task and cause, cooled per cause at a place); interruptions are not failures."""
from __future__ import annotations

import collections
import json
import os
import time
import traceback

from . import (api, arbiter, bag, decompose, dispatch, explore, goals, hazard, intent, nav, nether, paths, retry,
               needs, reflexes, tape, tasks, threat, world, perception)
from . import skill as skillkit
from . import craft, events, lifecycle, mechanisms, skillcore, survive
api.ANOMALY = events.anomaly      # a swallowed or unexpected error is an event (counted, said at 1st/10th/100th)
# every module that registers skills: a new one is added here only
from . import brewing, combat, dragon, end, farming, fluids, gather, loot, store, ui, wood  # noqa: F401,E402
from .api import GameUnreachable, McError, NotAvailable, PlayerTookControl, log
from . import cost as costmod
from .cost import Cost, Prices
from .data import HAND_MINEABLE_SUFFIX, bare
from .game import EYE_HEIGHT
from .memory import Memory
from . import memory as _memory
from . import knowledge as _k
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .shapes import Outcome, Source
_memory.TICK_READER = skillcore.game_time_or_none      # a look or note outside a round (bench achieve, CLI) reads the game's tick
api.ARM = skillcore.arm                       # every task that breaks or fights names the item it holds (the jar picks none)
from .planner import Unplannable, runnable
from .needs import bag_signature
from .world import Inventory, Snapshot

# wired from the top so lower layers never import the skill library
hazard.SKILLS.update(find_air=lambda ctx: survive.find_air(ctx), unbury=lambda ctx: survive.unbury(ctx),
                     cover=lambda ctx, s: needs.cover(ctx, s), eat=lambda ctx: survive.eat(ctx))
from . import fight_loop  # noqa: E402
fight_loop.lend("wall_in", lambda option, state: survive.pod_commands(state) if state.get("region") is not None else [],
                region=survive._pod_region)
fight_loop.lend("shoot", lambda option, state: combat.shoot_batch(
    option.target, (state["state"]["x"], state["state"]["y"] + EYE_HEIGHT, state["state"]["z"])))

IDLE_WAIT_TICKS = 100
IDLE_SLICE_TICKS = 20      # the idle wait is cut in 1 s slices: queued work ends it within a slice
SCAN_EVERY_S = 20  # seconds
TRACK_FILE = paths.data("track.jsonl")
# Step kinds a night under cover can carry on with (data.NIGHT_WORK). Everything else (a tree, an animal, a plan's wait for day) waits for morning while these are done — the night is not sat out while ore lies below.
from .data import NIGHT_WORK  # noqa: E402
from . import beliefs, estimate  # noqa: E402
from .data import critical_hp  # noqa: E402


def fight_line_holds(contract, args, state, inv):
    """Pure (S5): (ok, why) — a skill that makes an optional fight (`contract.fights(call)`: its mob kinds) is offered
    only with the health above critical covering its loss's quantile (estimate.fight_line_ok) at this sword and
    armour; a threat fight never comes through here."""
    kinds = list(contract.fights(skillkit.Call(args, {})) or ()) if getattr(contract, "fights", None) else []
    if not kinds:
        return True, None
    shield = (inv.equipment.get("offhand") or {}).get("id") == "minecraft:shield"
    mean, hit = estimate.melee_loss(kinds, _k.held_tiers(inv).get("sword", 0),
                                    beliefs.protection(state.get("armor", 0), shield))
    hp, floor = float(state.get("health", 0.0)), critical_hp(state)
    if estimate.fight_line_ok(hp, floor, mean, hit):
        return True, None
    return False, f"health {hp:.0f} under the fight line for {kinds} ({floor:.0f} + {estimate.loss_q(mean, hit):.0f})"

def surface_closed(night, dimension):
    """Pure: surface work waits for morning at night in the Overworld, sheltered or not (caught in the open, it walked out to chop)."""
    return bool(night) and dimension == "minecraft:overworld"

def act_on_surface(act):
    """Pure: does this act's step walk the surface (arbiter.on_surface)? An act with no step (a chain, a whole
    skill) is judged by what it runs elsewhere: not flagged."""
    step = getattr(act, "step", None)
    return step is not None and arbiter.on_surface(step.kind)

def craft_run(steps, first, inv=None):
    """Pure: `first` and the crafts straight after it: one table sitting, not one per round — up to the first craft
    whose inputs the bag (`inv`) and the crafts before it do not hold (its coal still to be mined)."""
    if first.kind != "craft" or first not in steps:
        return [first]
    run, made = [], {}
    for st in steps[steps.index(first):]:
        if st.kind != "craft":
            break
        if inv is not None and run and any(inv.count(t) + made.get(t, 0) < n
                                           for t, n in st.detail.get("inputs", {}).items()):
            break
        for t, n in st.detail.get("inputs", {}).items():
            made[t] = made.get(t, 0) - n
        made[st.token] = made.get(st.token, 0) + st.count
        run.append(st)
    return run

def keeps_table(steps, run):
    """Pure: a craft later in the plan than `run` needs a table — the one placed now is left standing."""
    if not run or run[-1] not in steps:
        return False
    return any(st.kind == "craft" and craft.recipe_needs_table(st.token) for st in steps[steps.index(run[-1]) + 1:])

def craft_act(layer, name, ctx, steps, step, night, task=None, inv=None):
    """The act for `step`: a craft runs on through the crafts after it at one sitting (craft_run), the table left
    standing when the plan crafts at one again (keeps_table); anything else as it is."""
    run = craft_run(steps, step, inv)
    keep = step.kind == "craft" and keeps_table(steps, run)
    if len(run) > 1 or keep:
        recipes = [(s.token, s.detail.get("times", s.count)) for s in run]
        return Act(layer, name, lambda: craft.craft_chain(ctx, recipes, keep), task=task, step=step, steps=run)
    return Act(layer, name, lambda: dispatch.execute(ctx, step, night), task=task, step=step)

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
        nav.DOORS = mechanisms.doors_on_way   # taught doors: pressed on the way, never dug
        mechanisms.WALK_TO = nav.go_to
        nav.HOME_DOOR = mechanisms.home_exit
        nav.DOOR_ROUTE = costmod.DOOR_ROUTE = mechanisms.route_s     # and priced through, not as rock
        nav.ROAD_MEM = self.mem       # travelled legs become a road network (roads.py) for later trips
        self.retry = retry.Retry()
        self.planning = True                    # False for a round without the plan layer (Brain.round(plan=False))
        self.picks = collections.Counter()      # what the arbiter chose, by kind (arbiter.note_pick)
        self.blacklist = {}           # unreachable targets, shared by every round's Context and the cost model
        self.held = {}                # task id -> {"steps": [Step], "sig": bag signature, "event": bool, "dim": str}
        self.needs = needs.Needs(self)
        self.reflexes = reflexes.Maintain(self)
        perception.IN_SITE = self.reflexes.in_site      # nightfall asks the night way's judgement, every Brain built
        self.policy_cache = nav.Policy(before_segment=self.segment_reflexes)
        self.place = None  # what causes are cooled against
        self.idle_since = None
        self.just_finished = False    # set by plan_proposals; idle_wait reads it on any round, a plan-less one too
        self.wake = None              # fn() → True ends an idle wait at once (a bench row's `until`: its outcome)
        api.GUARD = self.home_guard   # every task posted passes the home's rules
        self.committed = None
        self.task_writes = None       # while task_act / after_step decide: the task's fields they change (writes)
        self.last_failure = None
        self.last_light = self.last_offhand = self.last_scan = self.last_track = self.last_hold_log = 0
        self.lit_place = None      # where the last first lighting was done (a place signature)
        fight_loop.wire(self.mem, lambda snap: self.policy(snap, snap.night), self.blacklist,
                        prices=self.price_table)

    def home_guard(self, task):
        """api's door for a home: a refused task raises (memory.home_refusal); a home block broken at a rescue's
        allowance is said as an event; a block we place inside is ours to take back later."""
        dim = api.dim_seen()
        homes = self.mem.homes(dim) if dim else []
        if not homes:
            return
        mine = self.mem.placed_in_home(dim)

        def entity_at(eid):
            e = next((e for e in world.entities(24) if e.get("id") == eid), None)
            return (e["type"], (e["x"], e["y"], e["z"])) if e else None
        why = _memory.home_refusal(task, homes, mine, entity_at, allow_break=api.STATE.home_break is not None,
                                   feet=api.feet_seen())
        if why:
            raise NotAvailable(why)
        if "x" not in task or task.get("type") not in ("mine", "place"):
            return
        cell = (int(task["x"]), int(task["y"]), int(task["z"]))
        if not any(_memory.in_box(b, cell) for b in _memory.home_boxes(homes)):
            return
        if task["type"] == "place":
            self.mem.note_placed(cell, dim)
        elif cell in mine:
            self.mem.note_placed(cell, dim, placed=False)        # ours, taken back
        else:
            events.emit("home_break", f"broke home block {cell}: {api.STATE.home_break}", cell=cell,
                        cause=api.STATE.home_break)

    # -- movement policy and the hooks that run between chain segments
    def policy(self, snap, night):
        # without a usable pickaxe a dig route would raise ToolMissing mid-trip
        can_dig = any(_k.working(d) for _, d, _ in snap.inv.tools("pickaxe"))
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
            if needs_pick and not any(_k.working(d) for _, d, _ in Inventory().tools("pickaxe")):
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
            self._running(api.wait_for_handback)
            s = api.get("/state")
        if skillcore.really_dead(s):
            # the carried items are unknowable after the respawn
            self.mem.log_death((s["blockX"], s["blockY"], s["blockZ"]), s["dimension"],
                               carried=[(x["id"], x.get("count", 1)) for x in Inventory().slots])
            log("died → respawning")
            events.death(events.damage_label(s.get("lastDamage")), (s["blockX"], s["blockY"], s["blockZ"]))
            api.post("/respawn")
            lifecycle.reset_all()     # a new life: nothing the last one held (a target id, a boundary, a threat) carries over
            s = skillcore.settle(lambda: api.get("/state"), lambda st: not st.get("dead"), timeout=5.0, soft=True)
            events.respawn((s.get("blockX"), s.get("blockY"), s.get("blockZ")))
        if s["screen"] == "class_433":
            api.post("/resume")
        # a fireball in reach is punched back by the jar's reflex (anaka combat.Reflex, policy fight_loop.ALWAYS)
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
        # an open dark area underground: lit first where work starts, then a torch a segment (never the surface,
        # a short shaft or a sealed night hole)
        if time.time() - self.last_light > 5 and _k.under_rock(s.get("skyLight", 15)) and survive.dark_here(s) \
                and not survive.enclosed():
            self.last_light = time.time()
            try:
                spots = world.dark_spots(radius=survive.LIGHT_R, max_light=0, limit=40)
            except McError as e:
                spots = api.swallowed("brain: dark spots", e) or []     # unread: nothing lit this time, said
            try:
                if survive.light_due(s, False, len(spots)):
                    first = self.lit_place != self.place
                    self.lit_place = self.place
                    self._running(lambda: survive.light_area(self.context(s["dimension"]), survive.LIGHT_R,
                                                             survive.LIGHT_FIRST if first else 1, spots=spots))
            except api.INTERRUPTIONS:
                raise
            except McError as e:
                api.swallowed("brain.invariants", e)
                pass

    # -- failure policy (retry.py)
    def failed(self, name, err, quiet=False):
        """A failure counted for (name, cause), the cause cooled here; interruptions are not failures."""
        if outcome_of(err)[0] != "failed":
            return None
        self.mem.record_outcome(name, False)
        cause = retry.cause_of(err)
        self.reflexes.failed(cause, err, self.place)
        target = getattr(err, "pos", None)
        if target is not None:
            # about a target (prey, a vein, a station, a container): that target is not there for anyone (the reach
            # verdict, process memory) and the cause cools at it, never at where the body stood
            if not skillcore.banned(self.blacklist, target):       # the skill may have banned it already, for its own while
                self.context(api.STATE.dim_seen).ban(target)
            here, place = None, ("target", tuple(int(v) for v in target))
        else:
            here, place = self.place_now(), self.place
        verdict = self.retry.failed(name, cause, str(err), time.time(), place,
                                    also_at=(here,) if here is not None else ())
        if verdict is not None and verdict.worth_logging and not quiet:
            log(f"{'~~' if isinstance(err, NotAvailable) else '!!'} {name}: {err} "
                f"({cause}, ×{verdict.n}; {cause} cools here for {verdict.wait}s; at {api.feet_seen()}, "
                f"cooled at {place} and {here})")
        return verdict

    def place_now(self):
        """Where the body stood at the last /state read, as causes are cooled (the round's start place may be 70
        blocks back); no read of its own — a failure is counted without asking the world."""
        feet = api.feet_seen()
        night = self.place[1] if self.place else False
        return retry.place_signature(feet, night) if feet is not None else None

    def ready(self, name, cause=None):
        return self.retry.ready(name, time.time(), self.place, cause)

    def attempt(self, name, fn, also=()):
        """Run fn under the failure policy; returns "ok", "failed" or "interrupted". Failures also count under `also`."""
        self.last_failure = None
        began = time.time()
        events.task_start(name, t=began)
        try:
            fn()
            err = None
        except Exception as e:  # guard: any failure of a step is the failure policy's to count, with its traceback
            err, trace = e, traceback.format_exc()
        outcome, source = outcome_of(err)
        events.task(name, outcome, time.time() - began, source,
                    cause=retry.cause_of(err) if outcome == "failed" else None)
        if isinstance(err, api.TaskStuck):
            events.anomaly("task stuck", f"{name}: {err}")
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
            if source in ("death", "dimension change"):
                lifecycle.reset_all()     # the last life's (or dimension's) state names things that are not here
        elif first == "cool":
            self.last_failure = self.failed(name, err)
            for key in also:
                self.failed(key, err, quiet=True)
            try:
                api.post("/stop")
            except McError as e:
                api.swallowed("brain.attempt", e)
                pass
        else:
            log("!! crash in " + name + "\n" + trace)
            self.retry.hold(name, 300, time.time())
        return outcome

    # -- one round
    def round(self, plan=True):
        """One round. `plan=False`: reflexes and safety only (the upkeep reflexes — eat, reach land — and the yield to
        the fight), no needs' goals and no queue or idle stocking: a fight row's rounds (bench fight_until), where the
        plan layer took the body after the fight and mined coal for 14 s (combat__low_hp_eat 23:33:02)."""
        intent.clear()
        self.planning = plan
        try:
            self._round()
        finally:
            self.planning = True
            intent.publish()

    def _round(self):
        """One round, its phases timed into one detail.log line (round_line): where an idle body's time goes."""
        clock = {"t0": time.perf_counter(), "marks": [], "ended": api.clock_new_round(), "ran": 0.0}
        self._clock = clock
        try:
            self._round_body(clock)
        finally:
            now = time.perf_counter()
            post = api.clock_first_post()
            gap = (post - clock["ended"]) * 1000 if post is not None and clock["ended"] is not None else None
            ms = phase_ms(clock["t0"], clock["marks"], now)
            api.detail(round_line(ms, gap))
            events.round_time(ms["t"] / 1000.0, clock["ran"])

    def _mark(self, name):
        """End of a timed phase of this round."""
        c = getattr(self, "_clock", None)
        if c is not None:
            c["marks"].append((name, time.perf_counter()))

    def _running(self, fn):
        """fn(), its time kept apart from the round's deciding (events.round_time: a long task is no slow round)."""
        c, t = getattr(self, "_clock", None), time.perf_counter()
        try:
            return fn()
        finally:
            if c is not None:
                c["ran"] += time.perf_counter() - t

    def _round_body(self, clock):
        self.invariants()
        self._mark("inv")
        tape.begin()
        nav.forget_routes()
        snap = Snapshot.from_readings(api.get("/state"), Inventory())
        self._mark("snap")
        events.milestones(_bag_counts(snap))
        self.mem.clock = snap.state.get("gameTime")      # None on a jar before 0.1.39: notes then never expire
        self.mem.observe_phase(snap.night)
        self.place = retry.place_signature(snap.feet, snap.night)
        self.policy_cache = self.policy(snap, snap.night)
        policy = self.policy_cache
        api.GATE = lambda tasks: nav.gate(tasks, policy)    # each mine/place/use from a stand the jar's check holds
        prices = self.price_table(snap)
        api.HOLD = lambda tasks: skillcore.hold(tasks, prices.get)     # the main hand set by Python (I2)
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
            intent.set("goal", events.IDLE_GOAL)
            events.goal(events.IDLE_GOAL, _bag_counts(snap))
            if not self.planning:
                return                         # a fight row's round: its caller polls again, no idle wait posted
            jobs = self.mem.jobs(snap.dimension)
            if jobs:                           # only a furnace's clock is waited on: the bench may run it ahead
                api.waiting_for_clock(max(0.0, min(j["ready_at"] for j in jobs) - time.time()))
            self._running(lambda: self.idle_wait(lambda: any(t["state"] in tasks.LIVE for t in tasks.load())))
            return
        self.idle_since = None
        intent.set("goal" if act.layer in ("task", "idle") else "safety", repr(act))
        events.goal(repr(act), _bag_counts(snap))
        if act.layer == "L0":
            tape.end(self, act, snap)
            self._running(act.run)
            return
        box = {}
        also = (step_key(act.step),) if getattr(act, "step", None) is not None else ()
        ran = self._running(lambda: arbiter.BODY.drive(
            "plan", lambda: box.update(outcome=self.attempt(act.name, act.run, also)), act.name))
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
        wake = getattr(self, "wake", None)       # a Brain built without __init__ (a test's) has none
        for _ in range(max(1, IDLE_WAIT_TICKS // IDLE_SLICE_TICKS)):
            if wake is not None and wake():
                break
            try:
                api.run({"type": "wait", "ticks": IDLE_SLICE_TICKS}, wait=15, awaits="one task: a slice of the idle round's wait")
            except api.FightHolds as e:
                api.detail(f"  idle: {e} — no idling over it")
                break                            # a faster layer drives
            slices += 1
            if work_queued():
                break
        return slices

    # -- deciding (nothing acts in here beyond queueing tasks)
    def decide(self, snap, ctx):
        """Every layer proposes, the arbiter chooses; nothing here ranks."""
        def fast():
            out = []
            if arbiter.BODY.holder() is not None or api.mode() == "survival":
                out.append(arbiter.Intent("tactic", Act("L0", "yield", lambda: time.sleep(0.5)), key="yield"))
            k = hazard.rescue_due(snap.state)
            if k is not None and self.ready(f"rescue {k}"):
                out.append(arbiter.Intent("safety", Act("L0", f"rescue {k}", lambda: hazard.handle(
                    ctx, snap.state, self.attempt, self.ready, threatened=bool(threat.threats_seen()[0]))),
                    key=f"rescue {k}"))
            return out

        def upkeep():
            self.needs.propose(snap, ctx)
            out = [arbiter.Intent("maintain", Act("upkeep", name, run), seq=seq, key=name)
                   for seq, name, run in self.reflexes.proposals(snap, ctx)]
            for kind, goal, _why in (self.needs.needs_now if getattr(self, "planning", True) else ()):
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

        layers = (timed("fast", fast), timed("upkeep", upkeep)) + \
            ((timed("plan", lambda: self.plan_proposals(snap, ctx)),) if getattr(self, "planning", True) else ())
        intents, facts = arbiter.first_live(layers, facts_of)
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
                              kind="wait for day", key="wait for day")]
        if "pickaxe" in self.needs.working:
            act = self.night_stock(snap, ctx)
            if act is not None:
                out.append(arbiter.Intent("plan", act, kind="night stock", key=act.name))
        return out

    def need_act(self, kind, goal, snap, ctx):
        """The first runnable step toward `goal` now, or None; planned each round, never queued (the queue is the player's)."""
        name = f"{kind}: {goals.describe(goal)}"
        if not self.ready(name):
            return None
        cost = Cost(snap, self.mem, self.blacklist, policy=self.policy_cache, reserved=bag.RESERVED)
        try:
            steps = decompose.decompose(snap.inv, goal, cost, pending=self.mem.pending_outputs(snap.dimension))
        except Unplannable as e:
            self.__dict__.setdefault("unplannable", {})[name] = str(e)     # why this need offers no step (readout)
            return None
        closed = surface_closed(snap.night, snap.dimension)
        step = next((st for st in steps if self.valid(st, snap, ctx) and not (closed and arbiter.on_surface(st.kind))),
                    None)
        if step is None:
            return None
        return craft_act("upkeep", name, ctx, steps, step, snap.night, inv=snap.inv)

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
        return craft_act("task", f"task {task['id']}", ctx, held["steps"], step, snap.night, task=task,
                         inv=snap.inv)

    def valid(self, step, snap, ctx=None):
        """The cheap per-round check: inputs held, and the skill's own preconditions pass."""
        if not (runnable(step, snap.inv) and self.ready(step_key(step))):
            return False
        if ctx is None:
            return True
        found = dispatch.runner_for(ctx, step)
        if found is not None and not fight_line_holds(found[0].contract, (ctx,) + tuple(found[1]), snap.state, snap.inv)[0]:
            return False
        return dispatch.can_start(ctx, step)

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
        """Idle: a proposal toward the first of tools, food, light not held, then the run's next milestone not met (its
        first step) — never a task (queued, it took over whenever the row's task cooled; the queue outranks it)."""
        for needs in goals.PREPARE:
            if goals.short(snap.inv, [tuple(n) for n in needs]):
                act = self.need_act("idle", goals.have(*needs), snap, ctx)
                if act is not None:
                    return act
        for name in goals.MILESTONES:
            goal = goals.make("milestone", name=name)
            if goals.remainder(goal, snap, self.mem) == {}:
                continue                    # met: the next one
            act = self.need_act("milestone", goal, snap, ctx)
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
            snap = snap or Snapshot.from_readings(api.get("/state"), Inventory())
        except McError as e:
            api.swallowed("brain.price_table", e)
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
        except OSError as e:
            api.swallowed("brain.track", e)
            pass

    def hold_log(self, text):
        if time.time() - self.last_hold_log > 60:
            self.last_hold_log = time.time()
            log(text)

FIGHT_POLL_S, FIGHT_WAIT_MAX_S = 0.5, 60.0

def wait_out_fight(sleep=time.sleep, now=time.monotonic):
    """Poll until our own fight lets the body go, at most FIGHT_WAIT_MAX_S. Returns the seconds waited."""
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

def _bag_counts(snap):
    """{id: count} of the bag in a snapshot (for the event log's goal progress and milestones)."""
    out = {}
    for x in snap.inv.slots:
        out[x["id"]] = out.get(x["id"], 0) + int(x.get("count", 1))
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

def outcome_of(err) -> "tuple[Outcome, Source | None]":
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

def registrations(homes, mechs):
    """Pure: what the start read of the player's registrations — each home's blocks and parts, the taught doors."""
    said = [f"home {h['name']}: {len(h['snapshot'].get('blocks', {}))} blocks, "
            + ", ".join(f"{len((h.get('parts') or {}).get(k, []))} {k}" for k in ("beds", "chests", "stations"))
            for h in homes] or ["no home registered"]
    return "; ".join(said + [f"mechanisms: {len(mechs)}"])


def say_registrations(brain):
    """The start says what it read (memory's homes, mechanisms' lessons): never a silent start without them."""
    dim = api.get("/state")["dimension"]
    line = registrations(brain.mem.homes(dim), mechanisms.in_dimension(dim))
    log(f"start: {line}")
    events.emit("start", line)


def stack_dump():
    """Every thread's stack as detail lines: where a stuck brain stands (sys._current_frames)."""
    import sys
    import threading
    names = {t.ident: t.name for t in threading.enumerate()}
    for ident, frame in sys._current_frames().items():
        api.detail(f"   stack of {names.get(ident, ident)}:\n" + "".join(traceback.format_stack(frame)).rstrip())


def watchdog(stop, limit_s=api.FROZEN_S):
    """A thread: detail.log silent past `limit_s` while the agent drives (control not paused) → every thread's stack
    written once per silence (stack_dump)."""
    while not stop.wait(limit_s / 4):
        silent = time.time() - api.LAST_DETAIL
        if silent < limit_s:
            continue
        try:
            if api.game_status().get("paused"):
                continue
        except McError as e:
            api.swallowed("brain.watchdog", e)
            continue                    # no game to ask: not the brain's freeze
        api.detail(f"!! frozen: detail.log silent {silent:.0f}s (over {limit_s:.0f}s); every thread's stack:")
        stack_dump()


def autoplay(hours):
    import fcntl
    import threading
    lock_file = paths.data("autoplay.lock")
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        log("!! another autoplay process is already running; aborting")
        return
    log(f"autoplay start: brain code {code_version()}")
    try:
        api.take_control()     # the start takes the body (the pause menu closed, the player's toggle lifted)
    except GameUnreachable:
        api.wait_for_game()
        api.take_control()
    perception.start_watching()  # ~5 Hz
    brain = Brain()
    say_registrations(brain)
    threading.Thread(target=watchdog, args=(threading.Event(),), daemon=True, name="watchdog").start()
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
        except Exception:  # guard: the main loop must not die on a bug in one round; the crash is logged with frames
            log("!! crash in round\n" + traceback.format_exc())
            time.sleep(10)
    try:
        api.post("/release")
    except McError as e:
        api.swallowed("brain.autoplay", e)
        pass
