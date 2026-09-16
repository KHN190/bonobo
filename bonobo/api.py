"""HTTP client for the Agent Bridge mod: requests, task submission, progress watching. No strategy here."""
import json
import os
import time
import urllib.error
import urllib.request

from . import paths

# No default instance path. The old one spelled out one launcher's profile directory on one machine; anywhere else
# it silently read no token and every request came back 401 with nothing to suggest why. Empty is better: the token
# read below says what to set.
INSTANCE = paths.instance_dir()
BASE = paths.api_base()
STUCK_SECONDS = 10


class McError(Exception):
    """Something went wrong carrying out a goal."""


class GameUnreachable(McError):
    """The game isn't running or is restarting. Wait; never counts as a goal failure."""


class NotAvailable(McError):
    """The world doesn't offer this here/now (no sheep, no ore in range). Not a bug."""


class NavFailed(NotAvailable):
    """The body couldn't get where a skill needed it. Typed so failure causes never depend on message text."""


class TaskStuck(McError):
    """A task made no visible progress for STUCK_SECONDS, or exceeded its time budget. It has been cancelled."""


class CommitmentExpired(McError):
    """The running task outlived the commitment its plan was made under: the world owes the planner a new decision.

    Not a failure and not an interrupt. The task is left running in the mod — stopping it would throw away work that
    is still probably right — and the caller re-plans; if the new plan is the same action, the task is already under
    way. This is the one decision point a long action used to have none of.
    """


class Interrupted(McError):
    """The perception thread stopped the running task because of a danger; survival mode takes over next round."""


# Set by the perception thread (perception.py): a pending interrupt reason. MODE is "survival" while a rescue runs.
INTERRUPT = None
MODE = "normal"
# Set while a soft skill runs (skill.py): perception's request stays in INTERRUPT for the skill itself to read
# instead of cutting a mod task short. A fight answers danger by taking cover, not by failing halfway through a dig.
SOFT = False


def consume_interrupt():
    """Return and clear the pending interrupt message, or None. Soft skills read it and take cover themselves."""
    global INTERRUPT
    reason, INTERRUPT = INTERRUPT, None
    return reason


def take_interrupt():
    """Raise Interrupted if the perception thread asked for it (clears the request)."""
    reason = consume_interrupt()
    if reason:
        raise Interrupted(reason)


class PlayerTookControl(Exception):
    """The player holds control. Automation must stop touching the game until handed back."""


class BodyContested(McError):
    """A task we were waiting on was replaced by one we did not post: someone else (an operator command, a second
    process) is driving the body. Standing down beats cycling through fallbacks against it — one burst of this
    ran dig-in, burrow and pod in three seconds, every step "replaced by a new task"."""


def log(*parts):
    """The readable stream: decisions, failures, dangers, milestones. Goes to autoplay.log."""
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


DETAIL_FILE = paths.data("detail.log")
DETAIL_MAX_BYTES = 2 << 20        # roll at 2 MB; the previous roll is kept as detail.log.1


def roll(path, max_bytes):
    """Keep one previous file and start a new one once `path` passes `max_bytes`. The whole log policy, in one
    place, because there are two logs and they must age the same way.

    Rolling, not truncating: a log cut in half mid-line loses the end of a session, which is the part anyone is
    reading it for. Rolling at a size rather than a time, because what fills a log here is trouble, not hours.
    """
    try:
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
            return True
    except OSError:
        pass
    return False


def detail(*parts):
    """The working-out: rankings, refusals, every task result, look-ahead. Always written, never to the console.

    A separate function rather than a level argument. A level has to be judged at each call site, which is one more
    human decision to get wrong and no way to see that it was; calling the wrong function shows up when you read
    the line. And it is always on: a detail you only get by re-running the game is a detail you do not have.
    """
    line = time.strftime("%H:%M:%S") + " " + " ".join(str(p) for p in parts) + "\n"
    try:
        os.makedirs(os.path.dirname(DETAIL_FILE), exist_ok=True)
        roll(DETAIL_FILE, DETAIL_MAX_BYTES)
        with open(DETAIL_FILE, "a") as f:
            f.write(line)
    except OSError:
        pass          # losing the working-out must never stop the agent


def detail_window(since, until=None):
    """Lines from the detail log between two clock times ("HH:MM:SS"), for a wake-up packet."""
    until = until or "99:99:99"
    out = []
    for path in (DETAIL_FILE + ".1", DETAIL_FILE):
        try:
            with open(path) as f:
                out += [ln.rstrip("\n") for ln in f if since <= ln[:8] <= until]
        except OSError:
            continue
    return out


def _token():
    if not INSTANCE:
        raise McError("set MC_INSTANCE to your Minecraft instance directory "
                      "(the one containing config/agent-bridge.json)")
    names = ("anaka.json", "agent-bridge.json")      # current mod id first, previous one as a fallback
    for name in names:
        path = os.path.join(INSTANCE, "config", name)
        try:
            with open(path) as f:
                return json.load(f)["token"]
        except OSError:
            continue
    raise McError(f"no token in {os.path.join(INSTANCE, 'config')} (looked for {' or '.join(names)}); "
                  "launch the game with the mod once, or point MC_INSTANCE at the right instance")


def api(method, path, body=None, timeout=1200):
    from . import tape
    if tape.REPLAY is not None:          # an offline decision replay: the world answers from the recording
        return tape.replayed(method, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + _token()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
            tape.recorded(method, path, out)
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


def post(path, body=None):
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
    """Raise for whoever took the body from us: the player, our own faster layer, or an outsider.

    A replaced task used to mean an intruder, always. But a tactic preemption replaces it on purpose — that is
    what taking the body IS — so the waiting thread reported "another commander" every time the threat layer
    answered, and the answer looked like a fault. `since` is when this wait began: a preemption recorded after it
    is ours, and the right response is to go and re-plan.
    """
    if any("released by player" in (t.get("message") or "") for t in results):
        raise PlayerTookControl()
    if any(REPLACED in (t.get("message") or "") for t in results):
        from . import arbiter
        if since is not None and arbiter.BODY.preempted_at > since:
            raise CommitmentExpired(f"a faster layer took the body ({arbiter.BODY.preempted_by}): re-planning")
        raise BodyContested("another commander posted a task while ours ran")


def await_task(task_id, wait, exempt=("wait",)):
    """Waits for a task, cancelling it when it makes no visible progress or exceeds `wait` seconds."""
    deadline = time.time() + wait
    last, since = None, time.time()
    while True:
        r = get(f"/task?id={task_id}&wait=2")
        if INTERRUPT and MODE != "survival" and not SOFT:
            take_interrupt()
        # The commitment the current plan was made under. Checked here because this is where the seconds go: every
        # long action in this codebase is a task and a wait on it.
        from . import arbiter
        current = arbiter.BODY.current()
        if current is not None and current.over_commitment() and r["status"] == "running":
            raise CommitmentExpired(f"{r['type']} outlived the {current.commit_s}s commitment of "
                                    f"'{current.reason}': re-planning")
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
        # Progress = the body moved or the task's own report changed (hits, blocks, items). Turning the head is not
        # progress (an attack chase toward an unreachable mob spins the view forever), except for look-type tasks.
        sig = (cur["id"], cur["doing"], round(st["x"]), round(st["y"]), round(st["z"]))
        if cur["type"] in ("look", "use_item"):
            sig += (round(st["yaw"] / 5), round(st["pitch"] / 5))
        if sig != last:
            last, since = sig, time.time()
        elif time.time() - since >= STUCK_SECONDS:
            post("/stop")
            raise TaskStuck(f"no progress for {STUCK_SECONDS}s in {cur['type']}: {cur['doing']}")


# Tasks that turn the player's head. Aiming is what provokes an enderman, so these are the ones worth vetting.
AIMING_TASKS = ("look", "use_item", "use", "bed_bomb", "place", "mine", "attack")


def vet_aim(task):
    """Warn when a task would sweep the crosshair across an enderman's head. Returns the reason, or None.

    Here rather than in each skill because every task goes through `run`, while each skill had to remember to ask —
    and only one ever did. Advisory on purpose: an aim that provokes is worth knowing about and logging, but
    refusing the task would trade a fight we might win for a fight that stops.
    """
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


def run(task, wait=900):
    """Runs one task to completion; returns its JSON (status may be failed — callers decide).

    During a fight only the arbiter's chosen intent may issue tasks. A task from anywhere else is refused as a
    failed result rather than raised, so a stray caller degrades to "it did not work" instead of a crash.
    """
    from . import arbiter
    if not arbiter.BODY.owns(f"api.run({task.get('type')})"):
        return {"status": "failed", "type": task.get("type"), "message": "body owned by the arbiter", "seconds": 0}
    why = vet_aim(task)
    if why:
        log(f"  !! {why}")
    began = time.time()
    r = post("/task?wait=0", task)
    if r["status"] == "running":
        r = await_task(r["id"], wait)
    # Sequences (mine_many/build) report per-step failures in result.failures: surface the first reason so
    # "1 of 1 steps failed" becomes a classifiable cause (e.g. no path → nav) instead of a bare error.
    failures = (r.get("result") or {}).get("failures") or []
    if r["status"] != "succeeded" and failures and failures[0].get("reason"):
        r["message"] = f"{r['message']}: {failures[0]['reason']}"
    detail(f"  {r['type']:<9} {r['status']:<9} {r['message']} ({r['seconds']}s)")
    _raise_if_released([r], since=began)
    return r


# How long the last chain segment took. A segment is where an atomic action ends and the planner gets the body
# back, so this is how far ahead anything watching has to look. Measured, not configured.
LAST_SEGMENT_S = 2.0


# What was last posted: (chain signature, id of its last task). Re-deciding must not restart work already under
# way — see `resume_id`.
LAST_POSTED = None


def chain_signature(tasks):
    """What makes two chains the same work: the task list, verbatim and in order."""
    return json.dumps(tasks, sort_keys=True, default=str)


def resume_id(tasks, running, last_posted):
    """The id of the running task to attach to instead of posting `tasks`, or None to post them.

    A commitment expiring means the planner owes the world a fresh decision, not that the body must drop what it
    is doing. When the fresh decision is the SAME work — which it usually is, because the plan was right — posting
    it again replaces the running task in the mod and the walk starts from the beginning. The body then left every
    seven seconds and never arrived.
    """
    if not last_posted or not running or running.get("status") != "running":
        return None
    signature, task_id = last_posted
    if signature != chain_signature(tasks) or running.get("id") != task_id:
        return None
    return task_id


def run_chain(tasks, *, stop_on_failure=False, wait=1800, segment=6, before_segment=None):
    """Queues tasks in segments so the game never idles, calling `before_segment(segment_tasks)` first
    (the brain uses it for reflexes: tools, light, site bookkeeping). Returns all task results."""
    global LAST_SEGMENT_S
    results = []
    for start in range(0, len(tasks), segment):
        part = tasks[start:start + segment]
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
            LAST_POSTED = (chain_signature(part), r["tasks"][-1]["id"])
            await_task(r["tasks"][-1]["id"], wait)
            done = [get(f"/task?id={t['id']}") for t in r["tasks"]]
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
