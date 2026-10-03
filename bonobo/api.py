"""HTTP client for the Agent Bridge mod: requests, task submission, progress watching. No strategy here."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence, cast

from . import arbiter, lifecycle, paths, tape
from .data import EXCEPTIONS, TASK_WAIT_S, item_ids, living

if TYPE_CHECKING:
    from .shapes import Task, TaskResult

# no default instance path: a wrong one read no token and every request came back 401
INSTANCE = paths.instance_dir()
BASE = paths.api_base()
STUCK_SECONDS = 10

@dataclass
class ApiState(lifecycle.State):
    """What the transport shares between threads: the perception thread writes the requests to stop, the body's
    thread reads and takes them; the clock and the posted chain are the body's own."""

    # per life (lifecycle.reset_all): the requests to stop, and the chain last posted
    interrupt: "str | None" = None       # a pending interrupt reason (perception, the arbiter's preemption)
    # a soft request that takes effect only between tasks (a chain's segment boundary, a walk's next leg): nightfall
    # on the surface. Never cuts a task short, never /stop; skipped while the running work is itself the night's way.
    at_boundary: "str | None" = None
    last_posted: "tuple[str, Any] | None" = None      # (chain signature, its last task's id): not restarted
    # per process
    mode: str = "normal"                 # "survival" while a rescue runs
    soft: bool = False                   # a soft skill runs: perception's request stays for it to read, no cut
    last_segment_s: float = 2.0          # how far ahead a watcher must look: a segment's measured length
    posts: int = 0                       # POSTs sent: a read taken since the last one still describes the world
    feet_seen: "tuple[float, float, float] | None" = None     # the body's place in the last /state read
    dim_seen: "str | None" = None                              # its dimension then
    home_break: "str | None" = None      # a rescue's reason while it may break a home block (home_break_allowed)
    # the body's clock for the round log (brain._round's gap): when a task's end was first seen, when a task was
    # first posted since the round began (perf_counter seconds; None when not yet)
    clock: dict = field(default_factory=lambda: {"ended": None, "first_post": None, "ended_id": -1})
    ended_ids: set = field(default_factory=set)       # tasks whose end is already stamped
    # world reads allowed to fail, never invisibly: every quiet handler reports here (mc.py prints the tally)
    swallowed: dict = field(default_factory=dict)
    lock: Any = field(default_factory=threading.RLock, repr=False, compare=False)

    LIFE = ("interrupt", "at_boundary", "last_posted")


STATE = lifecycle.owns(__name__, ApiState())


ANOMALY = None      # events.anomaly, wired by the brain (api stays below the event log)


def swallowed(where, err):
    """Record a world read failed and ignored; returns None so a handler can `return api.swallowed(...)`."""
    key = f"{where}: {type(err).__name__}"
    with STATE.lock:
        n = STATE.swallowed[key] = STATE.swallowed.get(key, 0) + 1
    if ANOMALY is not None:
        ANOMALY(f"swallowed {key}")
    if n in (1, 10, 100):
        log(f"?? {where}: {type(err).__name__} ignored ({n}×) — that feature is off in this round")
    return None


def unexpected(where, err, why):
    """A broad handler's catch, said: the error and `why` it is caught rather than raised on the log, its traceback
    on detail.log — at the 1st, 10th and 100th time (one tally with `swallowed`). Returns None."""
    key = f"{where}: {type(err).__name__}"
    with STATE.lock:
        n = STATE.swallowed[key] = STATE.swallowed.get(key, 0) + 1
    if ANOMALY is not None:
        ANOMALY(f"exception {key}", str(err)[:120])
    if n in (1, 10, 100):
        log(f"!! {where}: {type(err).__name__}: {err} — {why} ({n}×)")
        detail("".join(traceback.format_exception(type(err), err, err.__traceback__)).rstrip())
    return None

from .data import READ_EVERY_S   # noqa: E402 — the fact lives with the facts (estimate charges it)

class McError(Exception):
    """Something went wrong carrying out a goal; `pos`: the target cell it was about (a container, prey, a stand)."""

    def __init__(self, message: object = "", pos=None):
        super().__init__(message)
        self.pos = tuple(pos) if pos is not None else None

class GameUnreachable(McError):
    """The game isn't running or is restarting. Wait; never counts as a goal failure."""

class NotAvailable(McError):
    """The world doesn't offer this here/now (no sheep, no ore in range). Not a bug."""

class NavFailed(NotAvailable):
    """The body couldn't get where a skill needed it."""

class TaskStuck(McError):
    """A task made no visible progress for STUCK_SECONDS or ran over budget; it was cancelled. `then`: what its skill
    declared follows (skill.ABANDON_WAYS); a jar task's own: the round plans again."""

    def __init__(self, message="", then="replan"):
        super().__init__(message)
        self.then = then

class CommitmentExpired(McError):
    """The running task outlived the commitment its plan was made under: the world owes the planner a new decision."""

class Interrupted(McError):
    """The perception thread stopped the running task because of a danger; survival mode takes over next round."""

class NightFell(Interrupted):
    """Nightfall on the surface, taken between two tasks: the night's way first, then the same target (arbiter
    RESUME_OF "night"); never counted, cooled or banned."""


# -- the requests to stop (STATE.interrupt, STATE.at_boundary) and the modes they are read under

def interrupt_pending():
    """The pending interrupt reason, or None (not taken)."""
    return STATE.interrupt


def stop_asked():
    """A stop perception asked for (interrupt_pending): a plan being searched ends, the round starts again from
    survival."""
    return interrupt_pending() is not None


def request_interrupt(reason):
    """Leave `reason` for the running work to take (the arbiter's preemption; perception to a soft skill)."""
    STATE.interrupt = reason


def request_boundary(reason):
    """Ask the running work to stop at its next task boundary (nightfall on the surface)."""
    STATE.at_boundary = reason


def mode():
    """"survival" while a rescue runs, else "normal"."""
    return STATE.mode


def set_mode(m):
    STATE.mode = m


def soft():
    """Is a soft skill running (perception's request stays for it to read)?"""
    return STATE.soft


def set_soft(on):
    """Set the soft flag; returns the previous value (a nested skill restores it)."""
    with STATE.lock:
        prev, STATE.soft = STATE.soft, bool(on)
    return prev


def last_segment_s():
    """A chain segment's measured length: how far ahead a watcher must look."""
    return STATE.last_segment_s


BOUNDARY_EXEMPT = lambda: False          # noqa: E731  (skill.py: is the night's way what runs now?)


def at_boundary():
    """Between two tasks: raise Interrupted for a pending boundary request (cleared), unless exempt."""
    if not STATE.at_boundary or STATE.soft or BOUNDARY_EXEMPT():
        return
    with STATE.lock:
        reason, STATE.at_boundary = STATE.at_boundary, None
    if reason:
        raise NightFell(reason)


CLOCK_HOOK = None      # bench: (seconds) → the game's clock run ahead (tick sprint); production: nothing


def waiting_for_clock(seconds):
    """The body waits only on the game's clock (a furnace cooking): the bench may run the clock ahead (CLOCK_HOOK);
    production waits as it always did. The one place such a wait is said."""
    if CLOCK_HOOK is not None and seconds > 0:
        try:
            CLOCK_HOOK(seconds)
        except McError as e:
            swallowed("api.waiting_for_clock", e)
            pass


def clear_requests():
    """Drop every pending request to stop — the perception thread's INTERRUPT and nightfall's AT_BOUNDARY — so work
    that starts now begins clean (a bench row; a rescue taking the body). A boundary left from the last row raised
    NightFell in the next (deposit_home_chest after dig_in_night, 0 s)."""
    with STATE.lock:
        STATE.interrupt = STATE.at_boundary = None


def consume_interrupt():
    """Return and clear the pending interrupt, or None: soft skills read it and take cover themselves."""
    with STATE.lock:
        reason, STATE.interrupt = STATE.interrupt, None
    return reason

def take_interrupt():
    """Raise Interrupted if the perception thread asked for it (clears the request)."""
    reason = consume_interrupt()
    if reason:
        raise Interrupted(reason)

def interrupt_due(since, soft=False):
    """Should work that began at `since` stop for the pending interrupt?"""

    if not STATE.interrupt or soft:
        return False
    if STATE.mode != "survival":
        return True
    return arbiter.BODY.preempted_at > since

def check_interrupt(since, soft=False):
    """Raise Interrupted when `interrupt_due`."""
    if interrupt_due(since, soft):
        take_interrupt()

class PlayerTookControl(Exception):
    """The player holds control. Automation must stop touching the game until handed back."""

def refuse_unqueued(r, queued):
    """A post that queued nothing: an interruption while a fight holds the body, else the world declining the work (NotAvailable)."""

    if queued:
        return
    if "owned by the arbiter" in str(r.get("message", "")) or arbiter.BODY.holder() is not None \
            or arbiter.BODY.engaged:
        raise FightHolds(f"the body is held ({r.get('message') or 'a fight'}): nothing queued")
    raise NotAvailable("the game queued none of the posted tasks")

class FightHolds(McError):
    """Our own fight has the body: an interruption that ends when the fight does, not an outside driver to stand down for."""

class BodyContested(McError):
    """A task we waited on was replaced by one we did not post: someone else drives the body."""

# interruptions: something else took the body — never a retry, a ban, a /stop or a cooldown
class Died(McError):
    """The body died mid-task: an interruption — recover the items, then replan from here, target kept."""

class DimensionChanged(McError):
    """The body left the task's dimension: the task resumes only back in its own (maps and notes are per dimension)."""

INTERRUPTIONS = cast("tuple[type[Exception], ...]",
                     tuple(c for n, c in list(globals().items())
                           if isinstance(c, type) and EXCEPTIONS.get(n, ("",))[0] in ("interrupt", "replan")))

def interrupted(err):
    """Was this an interruption rather than a failure?"""
    return isinstance(err, INTERRUPTIONS)

def log(*parts):
    """The readable stream (autoplay.log): decisions, failures, dangers, milestones."""
    print(time.strftime("%H:%M:%S"), *parts, flush=True)

DETAIL_FILE = paths.data("detail.log")
DETAIL_MAX_BYTES = 2 << 20        # roll at 2 MB; the previous roll is kept as detail.log.1
# detail.log silent this long while the agent drives: the brain is stuck (supervise wakes "frozen", every thread's
# stack is written); a task running longer says so every half of it (await_task), so only a stuck brain is silent
FROZEN_S = 60.0
LAST_DETAIL = time.time()       # when detail.log was last written: the process's liveness, never one life's

def roll(path, max_bytes):
    """Keep one previous file and start a new one once `path` passes `max_bytes`."""

    try:
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
            return True
    except OSError as e:
        swallowed("api.roll", e)
        pass
    return False

def detail(*parts):
    """The working-out: plans, refusals, every task result, look-ahead."""

    line = time.strftime("%H:%M:%S") + " " + " ".join(str(p) for p in parts) + "\n"
    global LAST_DETAIL
    LAST_DETAIL = time.time()
    try:
        os.makedirs(os.path.dirname(DETAIL_FILE), exist_ok=True)
        roll(DETAIL_FILE, DETAIL_MAX_BYTES)
        with open(DETAIL_FILE, "a") as f:
            f.write(line)
    except OSError as e:
        swallowed("api.detail", e)
        pass          # losing the working-out must never stop the agent

def _token():
    if not INSTANCE:
        raise McError("set MC_INSTANCE to your Minecraft instance directory "
                      "(the one containing config/agent-bridge.json)")
    names = ("anaka.json", "agent-bridge.json")  # current mod id first
    for name in names:
        path = os.path.join(INSTANCE, "config", name)
        if os.path.exists(path):        # the old mod id's file, or the new one's: whichever is there
            with open(path) as f:
                return json.load(f)["token"]
    raise McError(f"no token in {os.path.join(INSTANCE, 'config')} (looked for {' or '.join(names)}); "
                  "launch the game with the mod once, or point MC_INSTANCE at the right instance")

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TERMINAL = ("succeeded", "failed", "cancelled")     # a task's end states (queued and running are not)


def clock_new_round():
    """A round begins: (when a task's end was last seen, or None); the round's first post is not stamped yet."""
    with STATE.lock:
        STATE.clock["first_post"] = None
        return STATE.clock["ended"]


def clock_first_post():
    """When the round's first task was posted (perf_counter seconds), or None."""
    return STATE.clock["first_post"]

def _clock(method, path, out):
    """The one stamp point: every /task answer (a watch, api.run's post, a chain's post or its read-back) is looked
    at; a task seen ended for the first time stamps "ended", the round's first POST stamps "first_post"."""
    if not path.startswith("/task") or not isinstance(out, dict):
        return
    now = time.perf_counter()
    clock, ended_ids = STATE.clock, STATE.ended_ids
    with STATE.lock:
        if method == "POST" and clock["first_post"] is None:
            clock["first_post"] = now
        for t in [out] + list(out.get("tasks") or []):
            if isinstance(t, dict) and t.get("id") is not None and t.get("status") in TERMINAL \
                    and t["id"] not in ended_ids:
                if len(ended_ids) > 4096:
                    ended_ids.clear()
                ended_ids.add(t["id"])
                if isinstance(t["id"], int) and t["id"] < clock.get("ended_id", -1):
                    continue    # an earlier task (ids rise) read back after a later one ended: it ended before that
                clock["ended"], clock["ended_id"] = now, t["id"] if isinstance(t["id"], int) else -1

def api(method, path, body=None, timeout=1200):
    if tape.REPLAY is not None:          # an offline decision replay: the world answers from the recording
        return tape.replayed(method, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + _token()})
    try:
        with _DIRECT.open(req, timeout=timeout) as r:
            out = json.loads(r.read())
            tape.recorded(method, path, out)
            _clock(method, path, out)
            return out
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = e.reason
        if "player has control" in str(msg):
            raise PlayerTookControl()
        if e.code == 409 and "not in a world" in str(msg):
            raise GameUnreachable("not in a world")
        raise McError(f"{path}: {e.code} {msg}")
    except urllib.error.URLError as e:
        raise GameUnreachable(f"game not reachable ({e.reason})")
    except (ConnectionError, TimeoutError, OSError) as e:
        if isinstance(e, ConnectionResetError) and _game_up():
            # the game is up and closed THIS request unanswered (RemoteDisconnected is a ConnectionResetError): the
            # jar's error on this request, not a lost game — combat__dig_in's attack(entity=None) 500s read as
            # "connection to the game lost" ×3 (jar ≥ 0.1.63 answers them as a 400/500 with the reason)
            raise McError(f"{path}: the game closed the request without an answer ({e.__class__.__name__}): "
                          "the jar's error, its log names it")
        raise GameUnreachable(f"connection to the game lost ({e.__class__.__name__})")


def _game_up(timeout=1.0):
    """Does the game answer at all (/status, any HTTP reply)? Tells a request the jar dropped from a lost game."""
    req = urllib.request.Request(BASE + "/status", method="GET", headers={"Authorization": "Bearer " + _token()})
    try:
        with _DIRECT.open(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as e:
        return e.code is not None    # an answer, whatever it said: the game is there
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError) as e:
        swallowed("api._game_up", e)
        return False

def get(path) -> Any:
    r = api("GET", path)
    if path.startswith("/state") and isinstance(r, dict) and "x" in r:
        STATE.feet_seen = (r["x"], r["y"], r["z"])      # read for free where a failure happened
        STATE.dim_seen = r.get("dimension", STATE.dim_seen)
    return r


def feet_seen():
    """The body's place in the last /state any code read (no read of its own), or None."""
    return STATE.feet_seen

BODY_PATHS = ("/task", "/stop")

def with_item_ids(body):
    """A task post with every "only" list as the jar's item ids: the one place tokens become ids."""

    if isinstance(body, dict) and "tasks" in body:
        return dict(body, tasks=[with_item_ids(t) for t in body["tasks"]])
    if isinstance(body, dict) and body.get("only"):
        return dict(body, only=item_ids(body["only"]))
    return body

def dim_seen():
    """The body's dimension at the last /state read, or None."""
    return STATE.dim_seen


class home_break_allowed:
    """`with home_break_allowed(reason):` — a rescue at critical hp may break a home block (GUARD lets it through)."""

    def __init__(self, reason):
        self.reason = reason

    def __enter__(self):
        STATE.home_break = self.reason

    def __exit__(self, *exc):
        STATE.home_break = None


GUARD = None     # fn(task) → raises NotAvailable for a task the home refuses (brain wires memory.home_refusal)

def post(path, body=None):
    if path.startswith("/task"):
        body = with_item_ids(body)
        if GUARD is not None and isinstance(body, dict):
            for t in body.get("tasks") or [body]:
                GUARD(t)        # the one door every task passes: a missed caller can't break the home
    if path.startswith(BODY_PATHS):
        if not arbiter.BODY.owns(f"api.post({path.split('?')[0]})"):
            return {"status": "failed", "message": "body owned by the arbiter", "tasks": []}
    STATE.posts += 1
    return api("POST", path, body or {})

def game_status():
    return get("/status")

def wait_for_game(poll=10):
    announced = False
    while True:
        try:
            s = game_status()
            if s.get("inWorld"):
                if announced:
                    log("game is back")
                return s
        except GameUnreachable as e:
            swallowed("api.wait_for_game", e)
            pass
        if not announced:
            log("game unreachable — waiting for it")
            announced = True
        time.sleep(poll)

PAUSE_SCREEN = "class_433"   # the game menu's screen class (/state screen): it freezes the integrated server


def control_lost(state):
    """Pure: the agent must take the body again — the player holds it (the K toggle), the pause menu is open, or
    the agent is not driving."""
    control = state.get("control") or {}
    return bool(control.get("paused")) or not control.get("active") or state.get("screen") == PAUSE_SCREEN


def take_control():
    """A script's start takes the body: the player's toggle lifted, the pause menu closed, the agent driving. Only at
    a start (and between bench rows): mid-run a K press still wins (wait_for_handback)."""
    if game_status().get("paused"):
        post("/control", {"paused": False})     # the one POST the jar takes while the player holds control
    post("/resume")
    post("/takeover")


def wait_for_handback(poll=3):
    announced = False
    while True:
        try:
            if not game_status().get("paused"):
                if announced:
                    log("▶ player handed control back")
                return
        except GameUnreachable as e:
            swallowed("api.wait_for_handback", e)
            pass
        if not announced:
            log("⏸ player has control — waiting")
            announced = True
        time.sleep(poll)

REPLACED = "replaced by a new task"      # the jar's message when a POST /task cancels what was running

def _raise_if_released(results, since=None):
    if any("released by player" in (t.get("message") or "") for t in results):
        raise PlayerTookControl()
    if any(REPLACED in (t.get("message") or "") for t in results):
        if since is not None and arbiter.BODY.preempted_at > since:
            raise CommitmentExpired(f"a faster layer took the body ({arbiter.BODY.preempted_by}): re-planning")
        raise BodyContested("another commander posted a task while ours ran")

OSCILLATION_RETURNS = 4     # a task back in a state it already left this many times is going round in circles

def returns(seen):
    """Pure: how often the observed task states came back to one already seen."""

    earlier, back = set(), 0
    for sig in seen:
        back += sig in earlier
        earlier.add(sig)
    return back

HELD_SEEN: dict = {}      # task id → what the hand held at the polls while it was mining (said once, held_seen)
APPROACHED: set = set()   # task ids whose jar approach was seen (R1, said once)
TRAILS: dict = {}           # attack task id → its target polled while it ran (the fight trail: instrumentation)
lifecycle.in_place(__name__, "HELD_SEEN", "APPROACHED", "TRAILS")
TRAIL_RADIUS = 64           # the /entities read a trail sample takes: past it a target reads as gone
ATTACKING = re.compile(r"attacking entity (-?\d+)")


def trail_sample(task, st=None):
    """One poll of a running attack: its target as /entities lists it (dying ones too) and the body's feet."""
    m = ATTACKING.match(task.get("doing") or "")
    if m is None:
        return
    try:
        st = st or get("/state")
        found = get(f"/entities?radius={TRAIL_RADIUS}").get("entities") or []
    except McError as e:
        swallowed("api.trail_sample", e)
        return
    e = next((x for x in found if x.get("id") == int(m.group(1))), None)
    TRAILS.setdefault(task["id"], []).append({
        "t": time.time(), "entity": int(m.group(1)), "body": (st.get("x"), st.get("y"), st.get("z")),
        "seen": e is not None, "pos": e and (e.get("x"), e.get("y"), e.get("z")), "hp": e and e.get("health"),
        "dist": e and e.get("distance")})


def trail_line(task_id, samples, now, radius=TRAIL_RADIUS):
    """Pure: the fight trail of an attack — its target last seen (pos, hp, distance), how long ago, the body then,
    and whether it died (hp 0), vanished (unloaded, or out past `radius`) or is still there."""
    seen = [s for s in samples if s["seen"]]
    if not samples:
        return f"   fight trail {task_id}: target never polled"
    if not seen:
        return f"   fight trail {task_id}: target {samples[0]['entity']} never seen in {len(samples)} polls " \
               f"(body {where(samples[-1]['body'])})"
    last = seen[-1]
    ago = now - last["t"]
    if (last["hp"] or 0) <= 0:
        verdict = "died (hp 0)"
    elif samples[-1]["seen"]:
        verdict = "still there"
    else:
        verdict = f"vanished ({'out of range' if (last['dist'] or 0) >= radius - 2 else 'unloaded or gone'})"
    return (f"   fight trail {task_id}: target {last['entity']} last seen {where(last['pos'])} hp {last['hp']} "
            f"{last['dist']} off, {ago:.1f}s ago, {len(seen)}/{len(samples)} polls seen, "
            f"body {where(last['body'])}: {verdict}")


def where(p):
    """Pure: a position as the trail and threat lines print it."""
    return "?" if p is None else "(" + ", ".join(f"{c:.1f}" for c in p) + ")"


def trail_end(r):
    """An attack ended: its fight trail said, then dropped."""
    samples = TRAILS.pop(r.get("id"), None)
    if r.get("type") == "attack" and samples is not None:
        detail(trail_line(r.get("id"), samples, time.time()))



def await_task(task_id, wait, exempt=("wait",)):
    """Wait for a task, cancelling it on no visible progress or past `wait` seconds."""
    began = time.time()
    deadline = began + wait
    last, since, seen, said = None, began, [], began
    while True:
        r = get(f"/task?id={task_id}&wait=2")
        check_interrupt(began, STATE.soft)
        if r["status"] != "running":
            trail_end(r)
            return r
        if time.time() - said >= FROZEN_S / 2:
            said = time.time()
            detail(f"   task {task_id} running {said - began:.0f}s: {r.get('doing')}")
        if time.time() > deadline:
            post("/stop")
            raise TaskStuck(f"{r['type']} exceeded its {wait}s budget: {r['doing']}")
        st = get("/state")
        cur = st["control"]["task"]
        if cur is not None and cur.get("type") == "attack":
            trail_sample(cur, st)
        if cur is not None and cur.get("type") in ("mine", "place", "use") \
                and (cur.get("doing") or "").startswith("walking to") and cur["id"] not in APPROACHED:
            APPROACHED.add(cur["id"])          # R1: the gate passed it, the jar still walked (and may dig): said
            detail(f"!! approach: {cur['type']} task {cur['id']} walked ({cur['doing']}): the stand check disagreed")
            if ANOMALY is not None:
                ANOMALY("jar approach", f"{cur['type']}: {cur['doing']}")
        if cur is not None and "mining" in (cur.get("doing") or ""):
            hand = f"{(st.get('mainHand') or {}).get('id', 'minecraft:air')} (slot {st.get('selectedSlot')})"
            if hand not in HELD_SEEN.setdefault(cur["id"], []):
                HELD_SEEN[cur["id"]].append(hand)
        if cur is None or cur["type"] in exempt or st["control"].get("paused"):
            last, since = None, time.time()
            continue
        # progress = the body moved or the task's report changed; turning the head is not (a chase spins the view)
        sig = (cur["id"], cur["doing"], round(st["x"]), round(st["y"]), round(st["z"]))
        if cur["type"] in ("look", "use_item"):
            sig += (round(st["yaw"] / 5), round(st["pitch"] / 5))
        if sig != last:
            last, since = sig, time.time()
            seen.append(sig)
            if returns(seen) >= OSCILLATION_RETURNS:
                post("/stop")
                raise TaskStuck(f"going round in circles in {cur['type']}: {cur['doing']}")
        elif time.time() - since >= STUCK_SECONDS:
            post("/stop")
            raise TaskStuck(f"no progress for {STUCK_SECONDS}s in {cur['type']}: {cur['doing']}")

# tasks that turn the head: aiming provokes endermen
AIMING_TASKS = ("look", "use_item", "use", "bed_bomb", "place", "mine", "attack")

def vet_aim(task):
    """Warn when a task would sweep the crosshair across an enderman's head."""

    if task.get("type") not in AIMING_TASKS or "x" not in task:
        return None
    try:
        from . import combat_model
        st = get("/state")
        if st.get("dimension") != "minecraft:the_end":
            return None
        near = living(get("/entities?radius=32")["entities"])
        if combat_model.aim_hits_enderman((task["x"], task["y"], task["z"]), (st["x"], st["y"], st["z"]), near):
            return f"aim at {task['x']},{task['y']},{task['z']} crosses an enderman's head"
    except (McError, PlayerTookControl, KeyError, TypeError, ValueError) as e:
        swallowed("api.vet_aim", e)
        return None          # a failed read or a reading short of a field: best-effort, never breaks the task
    return None

# The one door every task passes, its steps wired by the brain (None: offline, the task goes as built):
WALKS = ("travel", "goto")       # never dig nor build (I3): their break/place/voidBridge/useBoat are off at the door
ARM = None       # fn(tasks) → the tasks with the item each holds named (skillcore.arm)
GATE = None      # fn(tasks) → fn(done) said after them: each mine/place/use sent only from a stand the jar's own
#                  check holds, its way walked first (nav.gate, I4); the after-check names any unplanned change (R4)
HOLD = None      # fn(tasks): the item they name put in the main hand first (skillcore.hold, I2)
HELD_TYPES = ("mine", "place", "pillar", "attack", "eat", "use_item", "bed_bomb", "interact")


def walk_only(task):
    """Pure: a walk as the door sends it — digging, building, bridging and boats off (I3); any other task as it is."""
    if task.get("type") not in WALKS:
        return task
    return {**task, "break": False, "place": False, "voidBridge": False, "useBoat": False} \
        if task["type"] == "travel" else {**task, "useBoat": False}


def segments(tasks, size):
    """Pure: `tasks` cut at every change of the item held (I2: one hold per segment; a task naming none rides
    along), at most `size` each."""
    out, cur, held = [], [], None
    for t in tasks:
        named = t.get("item") if t.get("type") in HELD_TYPES else None
        if cur and (len(cur) >= size or (named is not None and held is not None and named != held)):
            out.append(cur)
            cur, held = [], None
        cur.append(t)
        held = named if named is not None else held
    return out + ([cur] if cur else [])

def run(task, *, awaits, wait=TASK_WAIT_S):
    """Run one task to completion; returns its JSON (status may be failed — callers decide)."""

    if not isinstance(awaits, str) or not awaits.strip():
        raise ValueError("api.run: `awaits` must name the world result waited for (else send a chain)")
    if not arbiter.BODY.owns(f"api.run({task.get('type')})"):
        # one funnel with run_chain and go_to: the skill driver waits the fight out and resumes (never a spent try)
        raise FightHolds(f"the body is held: {task.get('type')} not sent")
    at_boundary()          # nightfall: a single send is a boundary too (mine's mine_many went out after the request)
    task = walk_only(task)
    task = ARM([task])[0] if ARM else task
    why = vet_aim(task)
    if why:
        log(f"  !! {why}")
    after = GATE([task]) if GATE else None
    if HOLD:
        HOLD([task])
    began = time.time()
    r = post("/task?wait=0", task)
    refuse_unqueued(r, queued=r.get("id") is not None or r.get("status") != "failed")
    if r["status"] == "running":
        r = await_task(r["id"], wait)
    # surface a sequence's first step failure so it classifies (no path → nav)
    failures = (r.get("result") or {}).get("failures") or []
    if r["status"] != "succeeded" and failures and failures[0].get("reason"):
        r["message"] = f"{r['message']}: {failures[0]['reason']}"
    detail(f"  {r['type']:<9} {r['status']:<9} {r['message']} ({r['seconds']}s)")
    said = break_line(task, r, held_seen(r.get("id")))
    if said:
        detail(said)
    if after is not None:
        after([r])
    _raise_if_released([r], since=began)
    out_of_reach(r)
    return r


def break_line(task, result, held=None):
    """Pure: the detail line of a task that breaks (a mine; a travel allowed to dig): the block, its cell, the
    seconds (the whole task: its approach too), the item asked (ARM's) and what the hand held while it mined (`held`:
    /state's mainHand and selectedSlot at the polls, held_seen) — None for any other task. A travel's result names no
    cell it dug."""
    kind, r = task.get("type"), result or {}
    hand = f", held during {held}" if held is not None else ", held during: not seen (no poll while it mined)"
    asked = f"asked {task.get('item', '(none named)')}{hand}"
    if kind == "mine":
        block = (r.get("result") or {}).get("block") or "?"
        return (f"   break {block} at {(task.get('x'), task.get('y'), task.get('z'))}: {r.get('status')} "
                f"{r.get('seconds')}s {asked}")
    if kind == "travel" and task.get("break"):
        return f"   travel may dig: {r.get('status')} {r.get('seconds')}s {asked} (the jar's result names no dug cell)"
    return None


def held_seen(task_id):
    """What the hand was seen holding while task `task_id` was mining (await_task's polls), said once; None unseen."""
    got = HELD_SEEN.pop(task_id, None)
    return ", ".join(got) if got else None

from .data import UNREACHABLE  # noqa: E402  (the one list of "could not get there" answers)

class Unreachable(NotAvailable):
    """Could not get to it. `cells` are the positions the mod named, when it named any."""

    def __init__(self, message, cells=()):
        super().__init__(message)
        self.cells = tuple(tuple(c) for c in cells)

def out_of_reach(r):
    """Raise `Unreachable` when a task failed for reach reasons, so no caller can read that as success."""

    import re as _re
    if r.get("status") == "succeeded" and "unreachable" not in (r.get("message") or "").lower():
        return r
    text = (r.get("message") or "")
    for step in ((r.get("result") or {}).get("failures") or []):
        text += " " + str(step.get("reason") or "")
    low = text.lower()
    if not any(word in low for word in UNREACHABLE):
        return r
    cells = [(int(m.group(1)), int(m.group(2)), int(m.group(3)))
             for m in _re.finditer(r"(-?\d+),\s*(-?\d+),\s*(-?\d+)", text)]
    raise Unreachable(f"{r.get('type', 'task')}: {text.strip()}", cells)


def chain_signature(tasks):
    """What makes two chains the same work: the task list, verbatim and in order."""
    return json.dumps(tasks, sort_keys=True, default=str)

def resume_id(tasks, running, last_posted):
    """The id of the running task to attach to instead of posting `tasks`, or None to post them."""

    if not last_posted or not running or running.get("status") != "running":
        return None
    signature, task_id = last_posted
    if signature != chain_signature(tasks) or running.get("id") != task_id:
        return None
    return task_id

# tasks: a shapes.Task each (a builder not typed yet hands plain dicts)
def run_chain(tasks: "Sequence[Task | Mapping[str, Any]]", *, stop_on_failure=False, wait=1800, segment=6,
              before_segment=None) -> "list[TaskResult]":
    """Queue tasks in segments so the game never idles, calling `before_segment(segment_tasks)` before each."""

    results: list[TaskResult] = []
    chain_began = time.time()
    armed = [walk_only(dict(t)) for t in tasks]
    armed = ARM(armed) if ARM else armed
    for n, part in enumerate(segments(armed, segment)):
        if n:
            # an interrupt stops the chain at a segment boundary; the skill resumes by what the world lacks, never this index
            check_interrupt(chain_began, STATE.soft)
        at_boundary()          # nightfall: before any segment, the first too — between tasks, never inside one
        after = GATE(part) if GATE else None
        if HOLD:
            HOLD(part)
        began = time.time()
        if before_segment:
            before_segment(part)
        resume = resume_id(part, (get("/state").get("control") or {}).get("task"), STATE.last_posted)
        if resume is not None:
            # The same work is already running: wait for it rather than starting it again.
            await_task(resume, wait)
            done = [get(f"/task?id={resume}")]
        else:
            r = post("/task?wait=0", {"tasks": part, "stopOnFailure": stop_on_failure})
            queued = r.get("tasks") or []
            refuse_unqueued(r, queued=bool(queued))
            STATE.last_posted = (chain_signature(part), queued[-1]["id"])
            await_task(queued[-1]["id"], wait)
            done = [get(f"/task?id={t['id']}") for t in queued]
        for t in done:
            if t["status"] != "succeeded":
                detail(f"  {t['type']:<9} {t['status']:<9} {t['message']}")
        for sent, t in zip(part, done):
            said = break_line(sent, t, held_seen(t.get("id")))
            if said:
                detail(said)
        if after is not None:
            after(done)
        results += done
        STATE.last_segment_s = max(0.2, min(30.0, time.time() - began))
        _raise_if_released(done, since=began)
        if stop_on_failure and any(t["status"] != "succeeded" for t in done):
            break
    if tasks:
        ok = sum(t["status"] == "succeeded" for t in results)
        detail(f"  chain: {ok}/{len(tasks)} succeeded")
    return results


def _stop_quietly():
    """A preemption's /stop: a game that cannot hear it has nothing running to stop."""
    try:
        post("/stop")
    except McError as e:
        swallowed("api._stop_quietly", e)
        pass


# the arbiter says and does through here (it never imports the transport): the one wire, set once at import
arbiter.WIRE.update(tell=request_interrupt, stop=_stop_quietly)
