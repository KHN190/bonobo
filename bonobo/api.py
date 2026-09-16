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


class Interrupted(McError):
    """The perception thread stopped the running task because of a danger; survival mode takes over next round."""


# Set by the perception thread (perception.py): a pending interrupt reason. MODE is "survival" while a rescue runs.
INTERRUPT = None
MODE = "normal"
# Set while a soft skill runs (skill.py): perception's request stays in INTERRUPT for the skill itself to read
# instead of cutting a mod task short. A fight answers danger by taking cover, not by failing halfway through a dig.
SOFT = False


def take_interrupt():
    """Raise Interrupted if the perception thread asked for it (clears the request)."""
    global INTERRUPT
    reason, INTERRUPT = INTERRUPT, None
    if reason:
        raise Interrupted(reason)


class PlayerTookControl(Exception):
    """The player holds control. Automation must stop touching the game until handed back."""


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


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


def post(path, body=None):
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


def _raise_if_released(results):
    if any("released by player" in (t.get("message") or "") for t in results):
        raise PlayerTookControl()


def await_task(task_id, wait, exempt=("wait",)):
    """Waits for a task, cancelling it when it makes no visible progress or exceeds `wait` seconds."""
    deadline = time.time() + wait
    last, since = None, time.time()
    while True:
        r = get(f"/task?id={task_id}&wait=2")
        if INTERRUPT and MODE != "survival" and not SOFT:
            take_interrupt()
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


def run(task, wait=900):
    """Runs one task to completion; returns its JSON (status may be failed — callers decide)."""
    r = post("/task?wait=0", task)
    if r["status"] == "running":
        r = await_task(r["id"], wait)
    # Sequences (mine_many/build) report per-step failures in result.failures: surface the first reason so
    # "1 of 1 steps failed" becomes a classifiable cause (e.g. no path → nav) instead of a bare error.
    failures = (r.get("result") or {}).get("failures") or []
    if r["status"] != "succeeded" and failures and failures[0].get("reason"):
        r["message"] = f"{r['message']}: {failures[0]['reason']}"
    log(f"  {r['type']:<9} {r['status']:<9} {r['message']} ({r['seconds']}s)")
    _raise_if_released([r])
    return r


def run_chain(tasks, *, stop_on_failure=False, wait=1800, segment=6, before_segment=None):
    """Queues tasks in segments so the game never idles, calling `before_segment(segment_tasks)` first
    (the brain uses it for reflexes: tools, light, site bookkeeping). Returns all task results."""
    results = []
    for start in range(0, len(tasks), segment):
        part = tasks[start:start + segment]
        if before_segment:
            before_segment(part)
        r = post("/task?wait=0", {"tasks": part, "stopOnFailure": stop_on_failure})
        await_task(r["tasks"][-1]["id"], wait)
        done = [get(f"/task?id={t['id']}") for t in r["tasks"]]
        for t in done:
            if t["status"] != "succeeded":
                log(f"  {t['type']:<9} {t['status']:<9} {t['message']}")
        results += done
        _raise_if_released(done)
        if stop_on_failure and any(t["status"] != "succeeded" for t in done):
            break
    if tasks:
        ok = sum(t["status"] == "succeeded" for t in results)
        log(f"  chain: {ok}/{len(tasks)} succeeded")
    return results
