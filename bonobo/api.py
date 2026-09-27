"""HTTP client for the Agent Bridge mod: requests, task submission, progress watching. No strategy here."""
import json
import os
import time
import urllib.error
import urllib.request

from . import paths

# no default instance path: a wrong one read no token and every request came back 401
INSTANCE = paths.instance_dir()
BASE = paths.api_base()
STUCK_SECONDS = 10

# world reads allowed to fail, never invisibly: every quiet handler reports here (mc.py prints the tally)
SWALLOWED = {}

def swallowed(where, err):
    """Record a world read failed and ignored; returns None so a handler can `return api.swallowed(...)`."""
    key = f"{where}: {type(err).__name__}"
    SWALLOWED[key] = SWALLOWED.get(key, 0) + 1
    if SWALLOWED[key] in (1, 10, 100):
        log(f"?? {where}: {type(err).__name__} ignored ({SWALLOWED[key]}×) — that feature is off in this round")
    return None

class McError(Exception):
    """Something went wrong carrying out a goal."""

class GameUnreachable(McError):
    """The game isn't running or is restarting. Wait; never counts as a goal failure."""

class NotAvailable(McError):
    """The world doesn't offer this here/now (no sheep, no ore in range). Not a bug."""

class NavFailed(NotAvailable):
    """The body couldn't get where a skill needed it."""

    def __init__(self, message="", pos=None):
        super().__init__(message)
        self.pos = tuple(pos) if pos is not None else None

class TaskStuck(McError):
    """A task made no visible progress for STUCK_SECONDS or ran over budget; it was cancelled."""

class CommitmentExpired(McError):
    """The running task outlived the commitment its plan was made under: the world owes the planner a new decision."""

class Interrupted(McError):
    """The perception thread stopped the running task because of a danger; survival mode takes over next round."""

class NightFell(Interrupted):
    """Nightfall on the surface, taken between two tasks: the night's way first, then the same target (arbiter
    RESUME_OF "night"); never counted, cooled or banned."""


# Set by the perception thread (perception.py): a pending interrupt reason. MODE is "survival" while a rescue runs.
INTERRUPT = None
MODE = "normal"
# set while a soft skill runs: perception's request stays for the skill to read, not cutting a task short
SOFT = False

# A soft request that takes effect only between tasks (a chain's segment boundary, a walk's next leg): nightfall on
# the surface. Never cuts a task short, never /stop; skipped while the running work is itself the night's way.
AT_BOUNDARY = None
BOUNDARY_EXEMPT = lambda: False          # noqa: E731  (skill.py: is the night's way what runs now?)


def at_boundary():
    """Between two tasks: raise Interrupted for a pending boundary request (cleared), unless exempt."""
    global AT_BOUNDARY
    if AT_BOUNDARY and not SOFT and not BOUNDARY_EXEMPT():
        reason, AT_BOUNDARY = AT_BOUNDARY, None
        raise NightFell(reason)


def consume_interrupt():
    """Return and clear the pending interrupt, or None: soft skills read it and take cover themselves."""
    global INTERRUPT
    reason, INTERRUPT = INTERRUPT, None
    return reason

def take_interrupt():
    """Raise Interrupted if the perception thread asked for it (clears the request)."""
    reason = consume_interrupt()
    if reason:
        raise Interrupted(reason)

def interrupt_due(since, soft=False):
    """Should work that began at `since` stop for the pending interrupt?"""

    if not INTERRUPT or soft:
        return False
    if MODE != "survival":
        return True
    from . import arbiter
    return arbiter.BODY.preempted_at > since

def check_interrupt(since, soft=False):
    """Raise Interrupted when `interrupt_due`."""
    if interrupt_due(since, soft):
        take_interrupt()

class PlayerTookControl(Exception):
    """The player holds control. Automation must stop touching the game until handed back."""

def refused(r, queued):
    """A post that queued nothing: an interruption while a fight holds the body, else the world declining the work (NotAvailable)."""

    if queued:
        return
    from . import arbiter
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

INTERRUPTIONS = (Interrupted, CommitmentExpired, BodyContested, FightHolds, PlayerTookControl, Died,
                 DimensionChanged, NightFell)

def interrupted(err):
    """Was this an interruption rather than a failure?"""
    return isinstance(err, INTERRUPTIONS)

def log(*parts):
    """The readable stream (autoplay.log): decisions, failures, dangers, milestones."""
    print(time.strftime("%H:%M:%S"), *parts, flush=True)

DETAIL_FILE = paths.data("detail.log")
DETAIL_MAX_BYTES = 2 << 20        # roll at 2 MB; the previous roll is kept as detail.log.1

def roll(path, max_bytes):
    """Keep one previous file and start a new one once `path` passes `max_bytes`."""

    try:
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
            return True
    except OSError:
        pass
    return False

def detail(*parts):
    """The working-out: plans, refusals, every task result, look-ahead."""

    line = time.strftime("%H:%M:%S") + " " + " ".join(str(p) for p in parts) + "\n"
    try:
        os.makedirs(os.path.dirname(DETAIL_FILE), exist_ok=True)
        roll(DETAIL_FILE, DETAIL_MAX_BYTES)
        with open(DETAIL_FILE, "a") as f:
            f.write(line)
    except OSError:
        pass          # losing the working-out must never stop the agent

def _token():
    if not INSTANCE:
        raise McError("set MC_INSTANCE to your Minecraft instance directory "
                      "(the one containing config/agent-bridge.json)")
    names = ("anaka.json", "agent-bridge.json")  # current mod id first
    for name in names:
        path = os.path.join(INSTANCE, "config", name)
        try:
            with open(path) as f:
                return json.load(f)["token"]
        except OSError:
            continue
    raise McError(f"no token in {os.path.join(INSTANCE, 'config')} (looked for {' or '.join(names)}); "
                  "launch the game with the mod once, or point MC_INSTANCE at the right instance")

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# the body's clock for the round log (brain._round's gap): when a task's end was first seen, when a task was first
# posted since the round began (perf_counter seconds; None when not yet)
CLOCK = {"ended": None, "first_post": None, "ended_id": -1}
TERMINAL = ("succeeded", "failed", "cancelled")     # a task's end states (queued and running are not)
_ENDED_IDS = set()          # tasks whose end is already stamped: a later read of the same ended task is no new end

def _clock(method, path, out):
    """The one stamp point: every /task answer (a watch, api.run's post, a chain's post or its read-back) is looked
    at; a task seen ended for the first time stamps "ended", the round's first POST stamps "first_post"."""
    if not path.startswith("/task") or not isinstance(out, dict):
        return
    now = time.perf_counter()
    if method == "POST" and CLOCK["first_post"] is None:
        CLOCK["first_post"] = now
    for t in [out] + list(out.get("tasks") or []):
        if isinstance(t, dict) and t.get("id") is not None and t.get("status") in TERMINAL \
                and t["id"] not in _ENDED_IDS:
            if len(_ENDED_IDS) > 4096:
                _ENDED_IDS.clear()
            _ENDED_IDS.add(t["id"])
            if isinstance(t["id"], int) and t["id"] < CLOCK.get("ended_id", -1):
                continue        # an earlier task (ids rise) read back after a later one ended: it ended before that
            CLOCK["ended"], CLOCK["ended_id"] = now, t["id"] if isinstance(t["id"], int) else -1

def api(method, path, body=None, timeout=1200):
    from . import tape
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
        raise GameUnreachable(f"connection to the game lost ({e.__class__.__name__})")

def get(path):
    return api("GET", path)

BODY_PATHS = ("/task", "/stop")

def with_item_ids(body):
    """A task post with every "only" list as the jar's item ids: the one place tokens become ids."""

    from .data import item_ids
    if isinstance(body, dict) and "tasks" in body:
        return dict(body, tasks=[with_item_ids(t) for t in body["tasks"]])
    if isinstance(body, dict) and body.get("only"):
        return dict(body, only=item_ids(body["only"]))
    return body

def post(path, body=None):
    if path.startswith("/task"):
        body = with_item_ids(body)
    if path.startswith(BODY_PATHS):
        from . import arbiter
        if not arbiter.BODY.owns(f"api.post({path.split('?')[0]})"):
            return {"status": "failed", "message": "body owned by the arbiter", "tasks": []}
    return api("POST", path, body or {})

def status():
    return get("/status")

def wait_for_game(poll=10):
    announced = False
    while True:
        try:
            s = status()
            if s.get("inWorld"):
                if announced:
                    log("game is back")
                return s
        except GameUnreachable:
            pass
        if not announced:
            log("game unreachable — waiting for it")
            announced = True
        time.sleep(poll)

def wait_for_handback(poll=3):
    announced = False
    while True:
        try:
            if not status().get("paused"):
                if announced:
                    log("▶ player handed control back")
                return
        except GameUnreachable:
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
        from . import arbiter
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

def await_task(task_id, wait, exempt=("wait",)):
    """Wait for a task, cancelling it on no visible progress or past `wait` seconds."""
    began = time.time()
    deadline = began + wait
    last, since, seen = None, began, []
    while True:
        r = get(f"/task?id={task_id}&wait=2")
        check_interrupt(began, SOFT)
        if r["status"] != "running":
            return r
        if time.time() > deadline:
            post("/stop")
            raise TaskStuck(f"{r['type']} exceeded its {wait}s budget: {r['doing']}")
        st = get("/state")
        cur = st["control"]["task"]
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
        from .world import entities
        st = get("/state")
        if st.get("dimension") != "minecraft:the_end":
            return None
        near = entities(32)
        if combat_model.aim_hits_enderman((task["x"], task["y"], task["z"]), (st["x"], st["y"], st["z"]), near):
            return f"aim at {task['x']},{task['y']},{task['z']} crosses an enderman's head"
    except Exception:
        return None          # perception is best-effort here; never let the check break the task
    return None

# How a task is dressed before it is posted (brain: nav.with_avoid over the protected cells), or None.
DRESS = None

def run(task, *, awaits, wait=900):
    """Run one task to completion; returns its JSON (status may be failed — callers decide)."""

    if not isinstance(awaits, str) or not awaits.strip():
        raise ValueError("api.run: `awaits` must name the world result waited for (else send a chain)")
    from . import arbiter
    if not arbiter.BODY.owns(f"api.run({task.get('type')})"):
        return {"status": "failed", "type": task.get("type"), "message": "body owned by the arbiter", "seconds": 0}
    at_boundary()          # nightfall: a single send is a boundary too (mine's mine_many went out after the request)
    task = DRESS(task) if DRESS else task
    why = vet_aim(task)
    if why:
        log(f"  !! {why}")
    began = time.time()
    r = post("/task?wait=0", task)
    refused(r, queued=r.get("id") is not None or r.get("status") != "failed")
    if r["status"] == "running":
        r = await_task(r["id"], wait)
    # surface a sequence's first step failure so it classifies (no path → nav)
    failures = (r.get("result") or {}).get("failures") or []
    if r["status"] != "succeeded" and failures and failures[0].get("reason"):
        r["message"] = f"{r['message']}: {failures[0]['reason']}"
    detail(f"  {r['type']:<9} {r['status']:<9} {r['message']} ({r['seconds']}s)")
    _raise_if_released([r], since=began)
    out_of_reach(r)
    return r

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

# how far ahead a watcher must look: a segment is where the planner gets the body back; measured
LAST_SEGMENT_S = 2.0

# (chain signature, its last task's id): re-deciding must not restart work under way
LAST_POSTED = None

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

def run_chain(tasks, *, stop_on_failure=False, wait=1800, segment=6, before_segment=None):
    """Queue tasks in segments so the game never idles, calling `before_segment(segment_tasks)` before each."""

    global LAST_SEGMENT_S
    results = []
    chain_began = time.time()
    for start in range(0, len(tasks), segment):
        if start:
            # an interrupt stops the chain at a segment boundary; the skill resumes by what the world lacks, never this index
            check_interrupt(chain_began, SOFT)
        at_boundary()          # nightfall: before any segment, the first too — between tasks, never inside one
        part = [DRESS(t) for t in tasks[start:start + segment]] if DRESS else tasks[start:start + segment]
        began = time.time()
        if before_segment:
            before_segment(part)
        global LAST_POSTED
        resume = resume_id(part, (get("/state").get("control") or {}).get("task"), LAST_POSTED)
        if resume is not None:
            # The same work is already running: wait for it rather than starting it again.
            await_task(resume, wait)
            done = [get(f"/task?id={resume}")]
        else:
            r = post("/task?wait=0", {"tasks": part, "stopOnFailure": stop_on_failure})
            queued = r.get("tasks") or []
            refused(r, queued=bool(queued))
            LAST_POSTED = (chain_signature(part), queued[-1]["id"])
            await_task(queued[-1]["id"], wait)
            done = [get(f"/task?id={t['id']}") for t in queued]
        for t in done:
            if t["status"] != "succeeded":
                detail(f"  {t['type']:<9} {t['status']:<9} {t['message']}")
        results += done
        LAST_SEGMENT_S = max(0.2, min(30.0, time.time() - began))
        _raise_if_released(done, since=began)
        if stop_on_failure and any(t["status"] != "succeeded" for t in done):
            break
    if tasks:
        ok = sum(t["status"] == "succeeded" for t in results)
        detail(f"  chain: {ok}/{len(tasks)} succeeded")
    return results
