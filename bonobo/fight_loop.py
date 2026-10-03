"""Hostiles: fight or flight. Perception sees a threat and offers the answer it chose (`offer`); this module takes the body for it and carries it out on its OWN thread (`_engagement`), so perception keeps sensing while the fight runs — an attack used to hold the perception thread for up to 45 s. Environmental hazards are not here (hazard.py). One engagement at a time. While it runs, a new answer from perception is not a second commander: it replaces what the engagement wants (`STATE.want`), and the engagement appends (same answer: the posted task keeps running) or /stops and posts the new one. It ends when the lease says answering has stopped paying, and `disengage` always runs: what it posted is stopped, the lease handed back, the plan drives again."""

import math
import threading
import time
import traceback
from dataclasses import dataclass, field as _dc_field
from typing import Any

from . import api, arbiter, lifecycle, nav, field as _field, kernel, threat
from .api import NotAvailable
from .skillcore import Context
from .world import Inventory, Snapshot
from .estimate import follows_to
from .beliefs import MOBS, hardest_hit, protection
from .knowledge import ALL_FOOD, RAW_MEAT
from .data import GROUPS, home_may_hold, placed_cell

ANSWER = None          # (option) -> None | {"id": task}: carries out one answer with the agent's memory and policy


@dataclass
class FightState(lifecycle.State):
    """What the perception thread (bids, offers), the engagement thread (carries them out) and the body's thread
    share; `lock` guards the engagement record (thread, want, failure, intent)."""

    # per life (lifecycle.reset_all)
    held: Any = None               # the threat layer's held decision (kernel.Held): kept while it pays
    last_bid: dict = _dc_field(default_factory=dict)   # the state and price the last bid was made on
    chase_at: Any = None           # when a threat was last seen chasing, in this engagement
    failed: dict = _dc_field(default_factory=dict)     # (kind, target) → until when an answer that failed is refused
    # the engagement: its own thread's while it runs; forgotten on a reset only when none runs
    thread: Any = None
    want: Any = None               # the answer (a threat.Option) perception wants carried out now
    failure: dict = _dc_field(default_factory=dict)
    intent: Any = None
    lock: Any = _dc_field(default_factory=threading.Lock, repr=False, compare=False)

    LIFE = ("held", "last_bid", "chase_at", "failed")

    def reset(self):
        """Forget the held decision, the last bid (a stale answer re-decided on the last row's state) and the chase
        clock, and a finished engagement's record: nothing a fight decided carries into work that starts now (a
        bench row: the last row's Held named a dead zombie's id). A live engagement's record is its own thread's."""
        super().reset()
        with self.lock:
            if not (self.thread is not None and self.thread.is_alive()):
                self.thread, self.want, self.failure, self.intent = None, None, {}, None


STATE = lifecycle.owns(__name__, FightState())


def held() -> Any:
    """The threat layer's held decision (kernel.Held), or None."""
    return STATE.held


def reset():
    """STATE.reset: the fight's per-life state forgotten (lifecycle.reset_all does it with the rest)."""
    STATE.reset()

def wire(mem, policy_of, blacklist, prices=None):
    """Give the fight what it needs from the agent: memory, a movement policy for the current snapshot, and the shared blacklist."""

    global ANSWER

    def answer(option):
        snap = Snapshot.from_readings(api.get("/state"), Inventory())
        ctx = Context(mem, policy_of(snap), snap.dimension, blacklist, prices=prices)
        return engage(option, snap.state, ctx)
    ANSWER = answer
    reflex(counter=False, **ALWAYS)
    return answer

# -- the jar's combat reflex (anaka combat.Reflex): shield for a predicted hit, a fireball punched back, a counter-hit

ALWAYS = {"shield": True, "deflect": True, "priority": "creeper", "gaze": True}     # from the start: safety, whatever runs (gaze: a walk never stares an enderman)


def reflex(**policy):
    """Set the jar's reflex policy (POST /reflex: shield, counter, deflect, priority). The jar owns the timing and
    the aim; the fight only says what the reflex may do. Refused (a jar without /reflex): said, and nothing else."""
    try:
        api.post("/reflex", policy)
    except api.McError as e:
        api.swallowed("fight: reflex policy", e)


def _reflex_again():
    """Every new life, dimension and bench row: the jar's reflex policy posted again (wire posted it once)."""
    if ANSWER is not None:
        reflex(counter=False, **ALWAYS)


lifecycle.on_reset(_reflex_again)


def wired():
    return ANSWER is not None

def engaged():
    """The engagement running now, or None."""
    t, intent = STATE.thread, STATE.intent
    return intent if t is not None and t.is_alive() else None


def carrying():
    """The answer (a threat.Option) the running engagement is carrying out now, or None when none runs."""
    return STATE.want if engaged() is not None else None

def offer(option, worth, key, now, release, held, seen_at):
    """Answer a threat now."""

    # never BODY.holder() under STATE.lock: its release check bids, and bid takes STATE.lock (hello2 11:56 deadlock)
    with STATE.lock:
        running = engaged()
    if running is not None and arbiter.BODY.holder() is running:
        with STATE.lock:
            if STATE.intent is running:
                STATE.want = option
                return (running.layer, running.reason), None, STATE.failure
    failure = {}

    def run():
        intent = arbiter.BODY.current()
        STATE.chase_at = None                    # a new engagement: its own chase clock
        reflex(counter=True)                     # engaged: the reflex hits back at full cooldown too
        with STATE.lock:
            th = threading.Thread(target=_engagement, args=(intent, failure), daemon=True, name="fight")
            STATE.thread, STATE.want, STATE.failure, STATE.intent = th, option, failure, intent
        th.start()

    taken, refused = arbiter.BODY.preempt("tactic", run, key, worth_s=worth, now=now, clear_first=True,
                                          release=release, held=held, seen_at=seen_at)
    return taken, refused, failure

SAME_R = 4.0           # two walks to points this close are one answer: the posted walk keeps going

def same(a, b):
    """Is the new answer the one already being carried out?"""

    if a is b:
        return True
    if a is None or b is None or a.kind != b.kind:
        return False
    if a.target == b.target:
        return True
    pa, pb = a.target, b.target
    return (isinstance(pa, tuple) and isinstance(pb, tuple) and len(pa) == len(pb) == 3
            and all(isinstance(v, (int, float)) for v in pa + pb) and math.dist(pa, pb) <= SAME_R)

# answers that go on until the lease ends (a fight swings again, a bow shoots again): posted again when their task
# ends. Every other answer (a dig, a pillar, a wall, a walk away, a bite) is done once its task is.
CONTINUING = ("fight", "shoot")

def carry(want_of, answer, going, held, again=False, stale=None, failed=None):
    """The one loop carrying answers: while `going()`, the same answer keeps the posted task, a new one /stops it and
    posts its own. `failed(want, err)`: an answer that raised is decided again at once (without it) instead of
    ending the loop; without it the error ends the loop as before."""

    if not going():
        api.detail("  fight: the lease was gone before the first answer (nothing posted)")
    while going():
        want = want_of()
        if want is None:
            return
        # a one-shot answer done is done: posted again only when perception decides it afresh (a new decision, not
        # the held one) — re-posting a finished dig mined the air it had just dug (combat__dig_in 01:38:51)
        fresh = (want.kind not in CONTINUING and held["task_id"] is None and held["done"] is not None
                 and want is not held["done"])
        if not same(want, held["done"]) or fresh:
            if held["task_id"] is not None:
                api.post("/stop")
            try:
                got = answer(want)
            except api.INTERRUPTIONS:
                raise
            except Exception as e:  # guard: a failed answer is re-decided, said with its traceback (failed → `_refail`)
                if failed is None:
                    raise
                failed(want, e)
                held["task_id"], held["done"] = None, None
                time.sleep(api.READ_EVERY_S)         # a decision that fails at once is not a tight loop
                yield want.kind
                continue
            held["task_id"] = got.get("id") if isinstance(got, dict) else None
            held["done"] = want
        elif held["task_id"] is not None:
            r = api.get(f"/task?id={held['task_id']}&wait=1")
            if r.get("status") == "running" and r.get("type") == "attack":
                api.trail_sample(r)
            if r.get("status") != "running":
                api.trail_end(r)
                # what the jar says the task did (hits, a weapon it could not hold): an attack posted that landed
                # nothing left no trace (fight_zombie_1: the zombie 2 blocks off at 20 hp every sample)
                api.detail(f"  fight {want.kind}: task {r.get('status')} — {r.get('message')} {r.get('result') or ''}")
                held["task_id"] = None
                if stale is not None and any(w in str(r.get("message") or "") for w in STALE):
                    stale(want)          # the held choice is stale: decided again now, the next post carries it
                if again and want.kind in CONTINUING:
                    held["done"] = None
                    # posted again at once: the body never idles on the decision (a poll's sleep here left it
                    # standing half a second after every attack ended); only a refusal backs off a poll, so a
                    # task the jar turns down at once is not a tight loop
                    if r.get("status") == "failed":
                        time.sleep(api.READ_EVERY_S)
        else:
            time.sleep(api.READ_EVERY_S)
        yield want.kind

def _engagement(intent, failure):
    """The fight's own thread: carry out what perception wants, re-reading it every poll, until the lease ends."""
    held = {"done": None, "task_id": None}
    try:
        if api.HOLD is not None and api.ARM is not None and STATE.want is not None:
            # the weapon in hand at engage, not at the attack: a switch then resets the swing's cooldown (B3)
            api.HOLD(api.ARM([{"type": "attack", "entity": STATE.want.target}]))

        def loop():
            for _ in carry(lambda: STATE.want, ANSWER, lambda: arbiter.BODY.holder() is intent, held, again=True,
                           stale=_restale, failed=_refail):
                pass
        arbiter.BODY.carry(intent, loop)
    except Exception as e:  # guard: the engagement's own thread: whatever ends it is said, the body handed back
        failure["failed"] = f"{type(e).__name__}: {e}"
        # and the answer it carried is not bid again as it was: refused a while, the held choice dropped
        _mark_failed(STATE.want or held.get("done"))
        # said, not only recorded: an engagement that dies at once re-bid every round with nothing reaching the jar
        # (fight_zombie_1 20260928-224501: four 'threat: fight_shielded … worth 194s', no task posted, no step taken)
        # broad on purpose: the engagement's own thread, whatever ends it the body is handed back (finally) — and the
        # traceback kept, or a bug here reads as a fight that simply stopped
        api.log(f"!! fight: {getattr(STATE.want or held.get('done'), 'kind', '?')} failed: {failure['failed']}")
        api.detail("".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip())
    finally:
        disengage(intent, stop=held["task_id"] is not None)

FAILED_S = 3.0         # an answer that failed is refused this long: re-deciding at once must not pick it again


def _failed_key(option):
    target = option.target
    return option.kind, target if isinstance(target, (int, str, tuple, type(None))) else repr(target)


def _mark_failed(option, now=None):
    """Refuse `option` (its kind and target) for FAILED_S and drop the held choice: the next decision is a fresh one
    without it. A failed fight was bid again as the same fight each second (combat__dig_in 01:03:09-11, ×3)."""
    if option is None:
        return
    STATE.failed[_failed_key(option)] = (now if now is not None else time.time()) + FAILED_S
    STATE.held = None


def refused_now(option, now=None):
    """Why `option` may not be chosen now (it failed within FAILED_S), or None."""
    until = STATE.failed.get(_failed_key(option))
    if until is not None and (now if now is not None else time.time()) < until:
        return "failed just now"
    return None


def _refail(want, err):
    """The engagement's answer to a failed answer (it raised): said with its traceback, refused a while, and what is
    wanted now decided again at once without it — the same path as a stale target (`_restale`)."""
    api.log(f"!! fight: {want.kind} failed: {type(err).__name__}: {err} → decided again without it")
    api.detail("".join(traceback.format_exception(type(err), err, err.__traceback__)).rstrip())
    _mark_failed(want)
    fresh = redecide(None)
    with STATE.lock:
        if STATE.want is want:
            STATE.want = fresh


def _restale(want):
    """The engagement's answer to 'target not found': what is wanted now, decided again without the gone target."""
    fresh = redecide(want.target)
    with STATE.lock:
        if STATE.want is want:
            STATE.want = fresh

def disengage(intent, stop=True):
    """Always, however the engagement ended: stop what it still has running, hand the body back, forget it."""
    with STATE.lock:
        if STATE.intent is intent:
            STATE.thread, STATE.want, STATE.intent = None, None, None
    try:
        if stop and arbiter.BODY.holder() is intent:
            api.post("/stop")
    except api.McError as e:
        api.swallowed("fight_loop.disengage", e)
        pass
    reflex(counter=False)
    arbiter.BODY.hand_back(intent)

# -- the decision


def active():
    """A fight is on: an engagement of ours is running, or a boss fight holds the body (`arbiter.BODY.engaged`)."""

    return engaged() is not None or bool(arbiter.BODY.engaged)

def threat_state(state, rows, work_s=None, ids=()) -> dict:
    """The threat model's state vector, read off a player state and the rows the watcher last saw."""

    st = {"here": (state["x"], state["y"], state["z"]), "hp": float(state.get("health", 20)),
          "sword": state.get("sword"),        # the sword item carried (perception.kit), None: the hand
          # a shield in the offhand is protection: the jar's reflex raises it for every predicted hit (`reflex`)
          "protection": protection(state.get("armor", 0), bool(state.get("shield")),
                                          hit=hardest_hit(r[3] for r in rows)),
          "night": False, "blocks": int(state.get("blocks", 0)), "hazards": rows,
          "food_items": int(state.get("food_items", 0)), "shield": bool(state.get("shield")), "bow": bool(state.get("bow")),
          "golden_apples": int(state.get("golden_apples", 0)), "hunger": float(state.get("food", 20)),
          "field": state.get("field") or _field.Field(), "ids": list(ids), "dig_ok": bool(state.get("dig_ok")),
          "footing": state.get("footing"),
          "alive": set(threat.THREAT_ALIVE) | {i for i in ids if i is not None},
          "impacts": list(threat.THREAT_IMPACTS), "lit": set(threat.THREAT_LIT),
          "low_cover": getattr(state.get("field"), "cover", None),
          "cover": state.get("cover"), "hide": state.get("hide")}
    if work_s is not None:
        st["work_s"] = work_s
    return st

LAST_LOOK: dict = {}     # the last bid, as the readout needs it: when, the options and their worth, the pick, why none
lifecycle.in_place(__name__, "LAST_LOOK")


def look_detail():
    """The last bid's record (LAST_LOOK) for a look's readout: JSON-plain."""
    return dict(LAST_LOOK)


def _note_look(t, st, field_model, price, horizon, keeper, option, worth, why):
    ground = st.get("field")
    opts = [a.option for a in field_model.opts]
    LAST_LOOK.clear()
    LAST_LOOK.update(
        t=round(t, 2), y=round(float(st["here"][1]), 2),
        shape_now=[list(s) for s in getattr(ground, "shape_now", ())],
        options=[[o.kind, str(o.target), round(threat.saves(o, opts, price, horizon), 1)] for o in opts],
        pick=None if option is None else option.kind, worth=worth, why=why,
        held_because=getattr(keeper, "because", None), engaged=engaged() is not None)


def unanswered_now(now):
    """Why the threats seen now have no answer under the held choice (threat.unanswered), or None: SAFETY's."""
    rows, ids = threat.threats_seen(now=now)
    last, keeper = STATE.last_bid, STATE.held
    if not rows or "state" not in last:
        return None
    option = keeper.choice.action.option if keeper and keeper.choice and keeper.choice.action else None
    if option is not None and option.kind != "ignore":
        return None
    return threat.unanswered(threat.Field(threat_state(last["state"], rows, None, ids), last["price"],
                                          refused=refused_now))


def bid(state, rows, price, work_s=None, now=None, ids=()) -> tuple[Any, float] | None:
    """(the answer, seconds it saves) the held decision stands behind now, or None when nothing pays."""
    if not rows:
        return None
    STATE.last_bid.update(state=state, price=price)
    if work_s is None:
        work_s = arbiter.work_left_s(arbiter.BODY.driving, now if now is not None else time.time())
    st = threat_state(state, rows, work_s, ids)
    field_model = threat.Field(st, price, refused=refused_now)
    with STATE.lock:
        if STATE.held is None:
            STATE.held = kernel.Held()
        keeper = STATE.held
    horizon_now = threat.horizon_for(st)
    choice = keeper.decide(field_model, field_model.state(), now if now is not None else time.time(),
                         holds=lambda c, _s: still_worth(c, field_model, price, horizon_now))
    option = choice.action.option if choice.action is not None else None
    t = now if now is not None else time.time()
    if option is None or option.kind == "ignore":
        _note_look(t, st, field_model, price, horizon_now, keeper, option, None,
                   "no action" if option is None else "ignore is the best")
        return None
    worth = threat.saves(option, [a.option for a in field_model.opts], price, horizon_now)
    forced = worth <= 0 and option is field_model.default.option
    if forced:
        worth = FORCED_WORTH_S       # the fallback under a closing follower: taken though nothing saves
    _note_look(t, st, field_model, price, horizon_now, keeper, option, round(worth, 1),
               "fallback" if forced else ("bid" if worth > 0 else "saves nothing"))
    return (option, round(worth, 1)) if worth > 0 else None

# the jar's word that the named mob is no target any more: dead ("defeated or gone" — the kill) or not found; the
# next target is decided at once, not after one more post at the dead id (fight_zombie_3 23:45:58: 0.4 s idle)
STALE = ("target not found", "target defeated or gone")

def redecide(gone):
    """The held answer named a mob the jar cannot find (`gone`: its entity id): drop the held choice and decide
    again at once on the latest reading without it — the option now wanted, or None when nothing pays. Waiting for
    perception's next offer (a key repeats once a second) posted the dead id again and again: 'target not found',
    0 hits, many times a second (detail.log 23:32:53)."""
    STATE.held = None
    rows, ids = threat.threats_seen()
    kept = [(r, i) for r, i in zip(rows, ids) if gone is None or i != gone] if ids else [(r, None) for r in rows]
    last = STATE.last_bid          # one read: a reset may rebind it meanwhile
    if not kept or "state" not in last:
        return None
    chosen = bid(last["state"], [r for r, _ in kept], last["price"], ids=[i for _, i in kept])
    return chosen[0] if chosen else None

FORCED_WORTH_S = 0.1   # the bid of threat.fallback: enough to take the body, less than any answer that saves

LOST_S = 3.0           # nothing has chased us this long (killed, gone, outrun): the engagement may end

def chasing(rows, here, hit_s=None):
    """Pure: some threat is still after us — it notices or reaches us here (`estimate.follows_to`), or the jar
    predicts one of its hits landing within LOST_S (`hit_s`, threat.hit_due_s: the jar's time to impact, never a
    closing speed differenced here)."""
    if hit_s is not None and hit_s <= LOST_S:
        return True
    return any(follows_to(here, h) for h in rows)

def engagement_over(rows, here, chased_at, now, hit_s=None):
    """Pure: (over, chased_at)."""

    if (rows or hit_s is not None) and chasing(rows, here, hit_s):
        return False, now
    chased_at = now if chased_at is None else chased_at
    return now - chased_at >= LOST_S, chased_at

def lease_done(state, rows, price, ids=()):
    """Has answering stopped paying?"""

    here = (state["x"], state["y"], state["z"])
    over, STATE.chase_at = engagement_over(rows, here, STATE.chase_at, time.time(), threat.hit_due_s())
    if not over:
        return False
    if not rows:
        return True
    try:
        fresh = bid(state, rows, price, now=time.time(), ids=ids)
    except Exception as e:  # guard: the release check (in arbiter.holder): a judgement we cannot make keeps the body
        return api.unexpected("fight: lease_done", e, "the lease is kept") or False
    return fresh is None or fresh[1] <= 0

def still_worth(choice, field_model, price, horizon):
    """The assumption behind a threat answer: that it still beats carrying on."""

    options = [a.option for a in field_model.opts]
    same = next((o for o in options if o.kind == choice.name), None)
    if same is None:
        return False
    held = getattr(choice.action, "option", None)
    alive = (getattr(field_model, "field", None) or {}).get("alive") or set()
    if held is not None and held.kind in TARGETED and held.target is not None and held.target not in alive:
        # the held attack names a mob the reading no longer lists alive (dead, despawned, out of radius — the list
        # is x-ray, so one behind a wall stays): 'target not found' every half second (fight_zombie_1
        # 20260928-230218) — decide again. A mob still there is kept though another is nearer (fight_zombie_3
        # 23:49:29-33: the switch left 0.4 s idle). A position target (a reshape, an evade spot) is never an id:
        # checked against the ids it dropped every held wall and pillar each bid (escape__walker_open_blocks 01:38)
        return False
    if held is not None and held.target is None and held.kind in TARGETED:
        # an attack naming no mob cannot be posted (the jar needs its entity id): never kept, decided again on a
        # reading that names them (combat__dig_in 01:03:09: attack(entity=None) three times, a 500 each)
        return False
    return threat.saves(same, options, price, horizon) > 0

TARGETED = ("fight",)     # the answers whose batch names the mob by its entity id (`_attack`)

# -- the batches

def batch(option, state):
    """Pure: the batch that carries out one answer from a body state; [] when it cannot be carried out from here."""

    make = BATCH.get(option.kind)
    out = list(make(option, state)) if make else []
    protected = state.get("protected")
    if protected is not None and any(t.get("type") in ("place", "pillar") and not home_may_hold(t.get("item", ""))
                                     and placed_cell(t, state.get("feet")) in protected for t in out):
        return []           # a block the home may not hold (a wall-in, a reshape in its hall): no such answer here
    return out

# between swings (jar ≥ 0.1.51): melee back out of reach, ranged strafe off the line, a creeper keepoff past its blast
FOOTWORK = {"melee": "back", "ranged": "strafe", "burst": "keepoff"}
LURE_BLOCKS = 8          # how far a creeper by our builds is led away from them before the fight

def footwork(target, state):
    """Pure: the footwork for fighting `target`, from the rows being answered; None when it is not among them."""

    rows, ids = state.get("threats") or [], list(state.get("threat_ids") or [])
    if target not in ids or ids.index(target) >= len(rows):
        return None
    mob = MOBS.get(rows[ids.index(target)][3], {})
    return FOOTWORK["burst" if mob.get("burst") else "ranged" if mob.get("ranged") else "melee"]

def lure_spot(here, creeper, protected, blast):
    """Pure: where to lead a creeper first — LURE_BLOCKS away from our builds within `blast` of it, or None when none is that near."""

    near = [c for c in protected if math.dist(c, creeper) <= blast]
    if not near:
        return None
    cx, cz = sum(c[0] for c in near) / len(near), sum(c[2] for c in near) / len(near)
    dx, dz = here[0] - cx, here[2] - cz
    n = math.hypot(dx, dz) or 1.0
    return (round(here[0] + LURE_BLOCKS * dx / n), int(here[1]), round(here[2] + LURE_BLOCKS * dz / n))

def _attack(option, state, **extra):
    if option.target is None:
        return []           # no entity id, no attack: refused here (NotAvailable), never posted as entity=None
    task = {"type": "attack", "entity": option.target, **extra}
    step = footwork(option.target, state)
    if step is None:
        return [task]
    out = []
    if step == "keepoff":
        rows, ids = state["threats"], list(state["threat_ids"])
        row = rows[ids.index(option.target)]
        spot = lure_spot(state["feet"], row[0], state.get("protected", ()), float(MOBS[row[3]].get("keep_out", 5.0)))
        if spot is not None:
            out.append({"type": "travel", "x": spot[0], "y": spot[1], "z": spot[2], "range": 2})     # a walk (I3)
    keep = {"keepOff": float(MOBS["minecraft:creeper"]["keep_out"])} if step == "keepoff" else {}   # jar default 5
    return out + [dict(task, footwork=step, **keep)]

def _fight(option, state):
    return _attack(option, state)

def _evade(option, state):
    x, y, z = option.target
    return [{"type": "travel", "x": x, "y": y, "z": z, "range": 3}]         # a walk (I3): the door digs nothing

def _eat(option, state):
    wanted = [option.target] if option.target else list(ALL_FOOD) + list(RAW_MEAT)
    food = next((f for f in wanted if state["inv"].count(f)), None)
    return [{"type": "eat", "item": food}] if food else []

def _reshape(option, state):
    """Change the ground: dig down n, stand n blocks up, or put n blocks between us and the nearest threat."""
    where, n = option.target
    x, y, z = state["feet"]
    if where == "down":
        return [nav.mine_task((x, y - 1 - i, z), down=True) for i in range(n)]
    item = next((b for b in GROUPS["building"] if state["inv"].count(b)), None)
    if item is None:
        return []
    if where == "under":
        return [{"type": "pillar", "item": item} for _ in range(n)]
    if where == "roof":
        return [{"type": "place", "item": item, "x": x + dx, "y": y + 2, "z": z + dz}
                for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
    near = state.get("threats") or []
    toward = min(near, key=lambda h: math.dist((x, y, z), h[0]))[0] if near else (x + 1, y, z)
    # by block, not by centre: a mob in the same row stands at x.5 > x, and its "+1" put the block diagonal (a wall)
    step = [(lambda d: (d > 0) - (d < 0))(math.floor(toward[i]) - (x, z)[j]) for j, i in enumerate((0, 2))]
    return [{"type": "place", "item": item, "x": x + step[0], "y": y + i, "z": z + step[1]} for i in range(n)]

def _place(option, state):
    cells, item = option.target
    if not state["inv"].count(item):
        return []
    return [{"type": "place", "item": item, "x": x, "y": y, "z": z} for x, y, z in cells]

def _cover(option, state):
    x, y, z = option.target
    return [{"type": "travel", "x": x + 0.5, "y": y, "z": z + 0.5, "range": 0.4}]      # a walk (I3)

def _bait(option, state):
    x, y, z = option.target
    if (x, y, z) == tuple(state["feet"]):
        return [{"type": "wait", "ticks": 4}]          # hold: it closes to lit
    return _evade(option, state)

BATCH = {"fight": _fight, "evade": _evade, "eat": _eat, "reshape": _reshape, "bait": _bait, "cover": _cover,
         "hide": _cover,
         "place": _place}       # "shoot" is lent by combat (combat.shoot_batch)
# what a batch reads around the body, by kind; skills register theirs so this module never imports the skill library
REGION = {}

def lend(kind, make, region=None):
    """A skill module lends its command batch as an answer: `make(option, state)`, and the region it reads."""
    BATCH[kind] = make
    if region is not None:
        REGION[kind] = region

def still_to_mine(tasks, solid):
    """Pure: `tasks` without the mines whose cell holds nothing to dig now (`solid(cell)` False)."""
    return [t for t in tasks if t.get("type") != "mine" or solid((t["x"], t["y"], t["z"]))]

def engage(decision, s, ctx):
    """Carry out one threat answer: its batch is posted, not awaited."""

    from .skillcore import body_state
    from .world import feet
    read = REGION.get(decision.kind)
    rows, ids = threat.threats_seen()
    state = body_state(ctx, read(feet()) if read else None, threats=rows, threat_ids=ids)
    tasks = batch(decision, state)
    mines = [(t["x"], t["y"], t["z"]) for t in tasks if t.get("type") == "mine"]
    if mines:
        # the blocks as they are NOW: a cell already dug is no task (it was posted and read back as air), and the
        # pick is chosen for the block really there — not a hand for a cell last read as air (combat__dig_in 01:38:51)
        from .world import Region
        region = Region(tuple(min(c[i] for c in mines) for i in range(3)),
                        tuple(max(c[i] for c in mines) for i in range(3)))
        tasks = still_to_mine(tasks, region.solid)
    if not tasks:
        raise NotAvailable(f"{decision.kind}: nothing to do it with from here")
    # the fight's bag is the one perception read for this answer; blocks read only for a dig (a weapon wants none)
    armed = api.ARM(tasks, inv=state["inv"], read_blocks=bool(mines)) if api.ARM else tasks
    api.detail(f"  fight {decision.kind}: posts " + ", ".join(
        f"{t['type']}{'(' + str(t['item']) + ')' if t.get('item') else ''}" for t in armed))
    r = api.post("/task?wait=0", {"tasks": armed})
    queued = r.get("tasks") or []
    if not queued:
        raise NotAvailable(f"{decision.kind}: the game queued none of it ({r.get('message')})")
    return {"id": queued[-1]["id"]}

Answer = __import__("collections").namedtuple("Answer", "kind target")
