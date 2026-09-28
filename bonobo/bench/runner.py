"""Running a scenario and judging it: setting the world up and checking it took, classing a failure, hashing the code a verdict belongs to, the readiness table, and the report a red leaves behind. Pure helpers first (they are what the offline suite tests), the live bench after."""

import ast
import hashlib
import io
import json
import os
import re
import sys
import threading
import time

from .core import (BENCH, BOX, body_reset, FLAG, PKG, SCENARIOS, TABLE, UNCOUNTED, SetupInvalid, _batch, _c, _checked,
                  _command, at, server_count)

# -- pure helpers (tested offline)

ERROR_MARKS = ("not loaded", "Incorrect argument", "Unknown or incomplete", "<--[HERE]", "Unknown ", "Invalid ",
               "is not within", "Too many blocks", "Could not", "Failed to", "Expected ", "unexpected error",
               "out of the world")

# what the game last said back during a setup, for scenarios reading a command's reply
LAST_FEEDBACK = []
# The cerebellum's own log for the run just finished: slice scenarios read it back to find loops.
LAST_LINES = []

def feedback_errors(lines):
    """Pure: the chat feedback lines that mean a command didn't do its job."""

    return [l for l in lines if any(m in l for m in ERROR_MARKS)]

def setup_mismatches(blocks, expect):
    """Pure: expected signature counts that don't hold. `blocks` maps (x, y, z) → name (non-air only)."""
    bad = []
    for lo, hi, name, lo_n, hi_n in expect:
        n = sum(1 for p, b in blocks.items()
                if all(lo[i] <= p[i] <= hi[i] for i in range(3)) and (name == "*" or b == name))
        if not lo_n <= n <= hi_n:
            bad.append(f"{name} in {lo}..{hi}: {n}, expected {lo_n}..{hi_n}")
    return bad

def classify(exc, ok):
    """Pure: which layer failed. Only nav and skill failures say something about the skill's readiness."""
    if ok:
        return "pass"
    name = type(exc).__name__ if exc is not None else ""
    if name == "SetupInvalid":
        return "setup"
    if name in ("GameUnreachable", "PlayerTookControl", "HarnessError"):
        return "harness"
    if name in ("NavFailed", "TaskStuck"):
        return "nav"
    msg = str(exc or "")
    if "HTTP" in msg or "unknown task" in msg.lower():
        return "mod"
    return "skill"

BUDGET_SLACK = 1.0      # the budget is a hard limit: the row is stopped there (`_watchdog`) and fails
ROW_LIMIT_S = 25        # no row outside acceptance may ask for more (the user's hard limit per row, prefer less)
TIMEOUT = "TIMEOUT"     # the note's prefix for a row stopped at its limit: deterministic slowness, never re-run

def _watchdog(limit, fired):
    """Arm a timer: at `limit` s stop the body (/stop) and interrupt the row's run in the main thread."""
    import _thread
    import signal
    # a background bench inherits SIGINT as ignored and interrupt_main does nothing: install Python's handler
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, signal.default_int_handler)

    def fire():
        fired.set()
        from .. import api
        try:
            api.post("/stop")
        except Exception:
            pass
        _thread.interrupt_main()
    t = threading.Timer(limit, fire)
    t.daemon = True
    t.start()
    return t

def judge(reached, seconds, budget, crashed=False, run_s=None, target_s=None):
    """Pure: (ok, why not) for a finished row."""

    if not reached:
        return False, "outcome not reached"
    if crashed:
        return False, "crashed on the way (a bug of ours)"
    if target_s is not None and (run_s is None or run_s > target_s):
        took = "never timed" if run_s is None else f"{run_s:.1f}s"
        return False, f"outcome reached but slow: its own run {took} > target {target_s:.1f}s"
    if seconds > budget * BUDGET_SLACK:
        return False, f"outcome reached but over budget: {seconds:.0f}s > {budget * BUDGET_SLACK:.0f}s"
    return True, None

GENERIC = ("finished without reaching its goal", "failed", "error", "none", "")

def generic_failure(note):
    """Pure: does a failure note say nothing about why? (empty, a bare class name, or the runner's own verify line)"""
    text = (note or "").strip().lower()
    text = text.split(" [")[0].split(" @ ")[0]
    if not text or re.fullmatch(r"[a-z_]*(error|exception|stuck|failed):?", text):
        return True
    body = text.split(":", 1)[1].strip() if ":" in text else text
    return any(body.endswith(g) for g in GENERIC if g) or body in GENERIC

def silent_failure(lines, result):
    """Pure: the exception a silent failure stands for — NavFailed when the last failed task was movement."""
    from ..api import McError, NavFailed
    last = next((l.strip() for l in reversed(lines) if " failed " in l), "")
    if last.split(" ", 1)[0] in ("travel", "goto"):
        return NavFailed(last)
    return McError(last or f"skill returned {result!r} without the outcome")

_IMPORTS = {}

def _imports_of(module, pkg_dir):
    """What one module imports from the package."""

    path = os.path.join(pkg_dir, module + ".py")
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return ()
    key = (path, stamp)
    hit = _IMPORTS.get(key)
    if hit is not None:
        return hit
    with open(path) as f:
        tree = ast.parse(f.read())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                out.append(node.module.split(".")[0])
            else:
                out.extend(a.name for a in node.names)
    _IMPORTS[key] = tuple(out)
    return _IMPORTS[key]

def module_deps(module, pkg_dir=PKG):
    """Pure-ish (reads source files): the module and every bonobo module it imports, transitively."""
    seen, todo = set(), [module]
    while todo:
        m = todo.pop()
        if m in seen or not os.path.exists(os.path.join(pkg_dir, m + ".py")):
            continue
        seen.add(m)
        todo.extend(_imports_of(m, pkg_dir))
    return sorted(seen)

# the mod's Java sources, optional (MC_MOD_SRC): without them readiness falls back to the jar version
JAVA = os.path.expanduser(os.environ.get("MC_MOD_SRC", ""))
# mod features → their Java sources, so a change re-tests only the scenarios using it; /state and HTTP are their own
MOD_CORE = ["task/Task.java", "task/TaskFactory.java", "util/WorldUtil.java", "util/InvUtil.java", "Agent.java"]
MOD_FILES = {
    "state": ["WorldInfo.java"],
    "http": ["HttpApi.java"],
    "travel": ["util/BuildPathfinder.java", "util/Pathfinder.java", "task/TravelTask.java", "task/GotoTask.java",
               "util/LavaGuard.java"],
    "use": ["task/UseItemTask.java", "task/PlaceTask.java", "task/UseBlockTask.java"],
    "mine": ["task/MineTask.java", "task/SequenceTask.java", "task/CollectTask.java"],
    "combat": ["task/AttackTask.java", "task/InteractEntityTask.java"],
    "craft": ["task/CraftTask.java"],
    "nets": ["Agent.java", "util/WaterClutch.java"],
}

def mod_hash(tags=None, java=JAVA):
    """Identity of the mod behind `tags`: a hash of its Java sources when they are available, else its jar version."""

    if not java or not os.path.isdir(java):
        return "jar-" + _mod_version()
    files = list(MOD_CORE)
    # default: every behaviour feature, not the /state and HTTP plumbing
    for t in ([k for k in MOD_FILES if k not in ("state", "http")] if tags is None else tags):
        files += MOD_FILES[t]
    h = hashlib.sha1()
    found = 0
    for rel in sorted(set(files)):
        path = os.path.join(java, rel)
        if os.path.exists(path):
            found += 1
            with open(path, "rb") as f:
                h.update(rel.encode() + f.read())
    if not found:
        return "jar-" + _mod_version()
    return h.hexdigest()[:6]

def _mod_version():
    """The running mod's version, or "unknown" when the game is not up (readiness is then simply not trusted)."""
    from .. import api
    try:
        return str(api.get("/status").get("version") or "unknown")
    except Exception:
        return "unknown"

def jar_matches_source():
    """The running jar must be the one built from these sources, or results would be credited to the wrong code."""
    from .. import api
    running = api.status()["version"].split("+")[0]
    with open(os.path.join(JAVA, "..", "..", "..", "..", "..", "gradle.properties")) as f:
        built = next(l.split("=", 1)[1].strip() for l in f if l.startswith("mod_version="))
    return running == built, running, built

_CODE = {}

def code_for(name):
    """Readiness key: the skill's Python modules, the scenario's own layout and the Java sources it uses."""

    if name not in _CODE:
        _CODE[name] = _code_for(name)
    return _CODE[name]

def _code_for(name):
    """The row's own definition (`row_hash`), the production code it reaches (`reach_hash`) and the mod."""

    sc = SCENARIOS[name]
    tags = sc.get("mod")
    if sc.get("mod_extra"):
        tags = sorted(set(tags if tags is not None else [k for k in MOD_FILES if k not in ("state", "http")])
                      | set(sc["mod_extra"]))
    return f"{reach_hash(sc)}{row_hash(sc)}-{mod_hash(tags)}"

from .rowkey import (COMMON, NOT_PRODUCTION, _callable_sources, _names_in, _strings_in, code_index,  # noqa: F401,E402
                     reach_hash, reached, row_hash)

# only fights change run to run; everything else is settled once it passes
FIGHTS = {"collect_blaze_rods", "fight_zombie_1", "fight_zombie_3", "fight_skeleton_1", "fight_creeper_1", "fight_blaze_3", "fight_enderman_1", "ghast_fireball", "bed_bomb_kill", "fight_dragon", "siege", "combat_arena",
          "escape"}

def settled(table, name):
    """Pure: a non-fight scenario whose latest counted run (under any code) passed."""

    if name in FIGHTS or name.split("__")[0] in FIGHTS:     # siege__w1, combat_arena__3, escape__…: shards
        return False
    runs = sorted((r for c in table.get(name, {}).values() for r in c if r.get("cls", "skill") not in UNCOUNTED),
                  key=lambda r: r.get("t", 0))
    return bool(runs) and runs[-1]["ok"]

MAX_RUNS = 3      # a row runs once; a failure is re-run, three runs at most, and passes on ≥ 2 of 3

# rows the world decides by chance; everything else is decided by one run
STOCHASTIC_MARKS = ("summon ", "place feature", "spreadplayers", "barter", "locate ")

def stochastic(row):
    """Pure: does this row's outcome depend on chance?"""

    if "stochastic" in row:
        return bool(row["stochastic"])
    if row.get("combat") or row.get("sweep"):
        return True
    text = " ".join(str(c) for c in row.get("setup", ())) + " " + row.get("doc", "")
    return any(m in text for m in STOCHASTIC_MARKS)

def difficulty_of(row):
    """Pure: the difficulty a row runs on — its own `difficulty`, else normal."""
    return row.get("difficulty", "normal")

def difficulty_set(reply, want):
    """Pure: does the game's reply to `/difficulty want` say it is now (or already) `want`?"""
    text = " ".join(reply).lower()
    return f"set to {want}" in text or f"difficulty to {want}" in text

def needs_clock(row):
    """Pure: does this row need the day to move (sleep, a night to wait out, a set time)? Else the runner stops it."""
    names = " ".join(row.get("skills", ())) + " " + row.get("doc", "")
    return any(str(c).startswith("time set") for c in row.get("setup", ())) or \
        any(w in names for w in ("sleep", "wait:day", "night", "dusk", "morning"))

def verdict_of(oks, chance=True):
    """Pure: the verdict of a row's counted runs, in order (the last MAX_RUNS)."""

    oks = list(oks)[-MAX_RUNS:]
    if not chance and oks:
        return "fail" if oks[-1] == TIMEOUT or not oks[-1] else "pass"
    if oks and oks[-1] == TIMEOUT:
        return "fail"                  # stopped at the limit: slow every time, a re-run only costs the limit again
    oks = [o is True or (bool(o) and o != TIMEOUT) for o in oks]
    if not oks:
        return None
    if len(oks) == 1:
        return "pass" if oks[0] else None
    if len(oks) == 2:
        return "pass" if all(oks) else ("fail" if not any(oks) else None)
    return "pass" if sum(oks) >= 2 else "fail"

def cached_timeout(table, name, code):
    """Pure: the note of a TIMEOUT that sticks to this key, else None."""

    counted = [r for r in table.get(name, {}).get(code, []) if r.get("cls", "skill") not in UNCOUNTED]
    if counted and counted[-1].get("note", "").startswith(TIMEOUT):
        return f"{TIMEOUT} (cached): {counted[-1]['note']}"
    return None

def verdict(table, name, code):
    """Pure: 'pass' / 'fail' for the current code's counted runs (`verdict_of`), else None."""
    counted = [r for r in table.get(name, {}).get(code, []) if r.get("cls", "skill") not in UNCOUNTED]
    row = SCENARIOS.get(name)
    return verdict_of([TIMEOUT if r.get("note", "").startswith(TIMEOUT) else r["ok"] for r in counted],
                      chance=True if row is None else stochastic(row))

# -- readiness table

def load_table(path=None):
    try:
        with open(path or TABLE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def record(table, scenario, code, ok, seconds, note="", cls="skill"):
    """Pure: append one result (last 10 kept per scenario and code version)."""
    runs = table.setdefault(scenario, {}).setdefault(code, [])
    runs.append({"ok": bool(ok), "s": round(seconds, 1), "note": note[:120], "cls": cls, "t": int(time.time())})
    del runs[:-10]
    return table

def status(table, scenario, code):
    """Pure: 'untested' | 'failing' | 'scenario' (the current code's verdict passes: `verdict_of`) and the median pass time."""

    runs = [r for r in table.get(scenario, {}).get(code, []) if r.get("cls", "skill") not in UNCOUNTED]
    if not runs:
        return "untested", None
    passes = sorted(r["s"] for r in runs if r["ok"])
    median = passes[len(passes) // 2] if passes else None
    return ("scenario" if verdict_of([r["ok"] for r in runs]) == "pass" else "failing"), median

def failed_last(table):
    """Pure: the rows whose latest counted run (any code version) failed — FAIL or TIMEOUT — sorted."""

    out = []
    for name, codes in table.items():
        runs = [r for c in codes.values() for r in c if r.get("cls", "skill") not in UNCOUNTED]
        if runs and not max(runs, key=lambda r: r.get("t", 0))["ok"]:
            out.append(name)
    return sorted(out)

def pending(table, codes):
    """Pure: rows with no counted result under their current key, or whose latest counted run under it failed."""

    out = []
    for name, code in codes.items():
        runs = [r for r in table.get(name, {}).get(code, []) if r.get("cls", "skill") not in UNCOUNTED]
        if not runs or not max(runs, key=lambda r: r.get("t", 0))["ok"]:
            out.append(name)
    return sorted(out)

def migrate(table, current, key_then):
    """Pure: carry old verdicts over to rows whose key, recomputed on the code of the time, equals the current one."""

    moved = []
    for name, codes in table.items():
        now = current.get(name)
        if not now or now in codes or "-" not in now:
            continue
        code_now, mod_now = now.split("-", 1)
        best = None
        for code, runs in codes.items():
            if not runs or "-" not in code or code.split("-", 1)[1] != mod_now:
                continue                            # another jar: its verdict is not this jar's
            t = max(r.get("t", 0) for r in runs)
            if key_then(name, t) == code_now and (best is None or t > best[0]):
                best = (t, code)
        if best:
            table[name][now] = list(codes[best[1]])
            moved.append(name)
    return sorted(moved)

def save_table(table, path=None):
    path = path or TABLE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(table, f, indent=1)

# -- live bench

class _Console(io.TextIOBase):
    """Tee stdout so the report carries the skill's own log lines."""
    def __init__(self, real):
        self.real, self.lines = real, []

    def write(self, s):
        self.real.write(s)
        self.lines.extend(l for l in s.splitlines() if l.strip())
        return len(s)

    def flush(self):
        self.real.flush()

# the next row's world, prebuilt at site B while this one runs; mc.py sets it before each run
NEXT_ROW = [None]
SETUP_S = {}          # the last setup's seconds: world (built here, or cloned from B) and body, for the report
PREBUILT = {"name": None, "done": None, "ok": False, "why": ""}

def prebuildable(sc):
    """Pure: a row whose world can be built ahead at site B — boxed (not raw), in the Overworld, not a sweep."""
    return not sc.get("raw") and sc.get("dimension", "minecraft:overworld") == "minecraft:overworld" \
        and not sc.get("sweep")

def prebuild(name):
    """Background: row `name`'s world commands at site B (its box cleared, force-loaded), shifted there."""

    sc = SCENARIOS.get(name)
    done = threading.Event()
    PREBUILT.update(name=name, done=done, ok=False, why="")
    if not sc or not prebuildable(sc):
        PREBUILT["why"] = "not prebuildable"
        done.set()
        return

    def work():
        from .core import shift, split_setup
        try:
            ow = "execute in minecraft:overworld run "
            lo, hi = shift(_c(at(*BOX[0]))), shift(_c(at(*BOX[1])))
            world, _rest = split_setup(sc["setup"])
            fb = []
            _checked(ow + f"forceload add {lo.split()[0]} {lo.split()[2]} {hi.split()[0]} {hi.split()[2]}", fb)
            for _ in range(60):                     # the chunks load a moment after the forceload
                if not any("not loaded" in l for l in _command(ow + f"fill {lo} {lo} air", fb)):
                    break
                time.sleep(0.5)
            _batch([ow + f"fill {lo} {hi} air"] + [ow + shift(c) for c in world], fb, settle=1.0)
            PREBUILT["ok"] = True
        except Exception as e:                      # the old way still works: say why this one did not
            PREBUILT["why"] = str(e)
        finally:
            done.set()
    threading.Thread(target=work, daemon=True, name=f"prebuild {name}").start()

def prebuilt_ready(prebuilt, name):
    """Pure: may row `name` clone its world from site B?"""

    done = prebuilt.get("done")
    return prebuilt.get("name") == name and done is not None and done.is_set() and bool(prebuilt.get("ok"))

def take_prebuilt(name, wait_s=120):
    """Was row `name`'s world built ahead at site B?"""

    if PREBUILT["done"] is not None:
        PREBUILT["done"].wait(wait_s)
    if PREBUILT["name"] != name:
        return False
    ok = prebuilt_ready(PREBUILT, name)
    PREBUILT.update(name=None, done=None, ok=False)
    return ok

def _setup(name, sc, feedback):
    from .. import api
    SETUP_S.clear()
    from ..world import Region
    if api.get("/state").get("dead"):
        api.post("/respawn")
        time.sleep(2)
    api.post("/resume")          # a pause menu freezes the integrated server: commands would do nothing
    time.sleep(0.5)
    lo, hi = at(*BOX[0]), at(*BOX[1])
    dim = sc.get("dimension", "minecraft:overworld")
    moved = api.get("/state")["dimension"] != dim

    def ex(cmd):                 # every command runs in the scenario's dimension (tp included: it moves us there)
        return f"execute in {dim} run {cmd}"

    if sc.get("raw"):
        # real-world scenarios: no box, just the body reset and the commands
        if moved:
            # another dimension first: "@p" in "execute in" only finds players already there, so park with @a on its glass
            _checked(ex(f"forceload add {lo[0]} {lo[2]} {hi[0]} {hi[2]}"), feedback)
            probe = _c(at(0, BOX[1][1], 0))
            for _ in range(60):
                if not any("not loaded" in l for l in _command(ex(f"fill {probe} {probe} air"), feedback)):
                    break
                time.sleep(0.5)
            glass = _c(at(0, BOX[1][1] + 2, 0))
            _checked(ex(f"fill {glass} {glass} glass"), feedback)
            _checked(ex(f"tp @a[limit=1] {_c(at(0, BOX[1][1] + 3, 0))}"), feedback)
            time.sleep(4)
        for cmd in ("gamemode survival @p", "effect clear @p", "time set day", "weather clear"):
            _checked(ex(cmd), feedback)
        said = _command(ex(f"difficulty {difficulty_of(sc)}"), feedback)
        if not difficulty_set(said, difficulty_of(sc)):
            raise SetupInvalid(f"difficulty not {difficulty_of(sc)}: {said[:1]}")
        for cmd in sc["setup"]:
            _checked(ex(cmd), feedback)
        for cmd in body_reset(sc):
            _command(ex(cmd), feedback)
        time.sleep(4 if moved else 1.5)
        s = api.get("/state")
        if s.get("dimension") != dim:
            raise SetupInvalid(f"player in {s.get('dimension')}, scenario needs {dim}")
        # real-world scenarios need their actors too
        for t, want in sc.get("expect_entities", []):
            n = server_count(_command(ex(f"execute as @p at @s if entity @e[type={t},distance=..160]"), feedback))
            if n < want:
                raise SetupInvalid(f"{t}: {n} on the server, expected ≥ {want}")
        return

    # global state in one batch: empty bag, difficulty (its reply read back), no chance left in the world
    want = difficulty_of(sc)
    said = _batch([ex(c) for c in ("clear @p", "gamemode survival @p", "effect clear @p", "time set day",
                                   "weather clear", "gamerule spawn_mobs false",
                                   f"forceload add {lo[0]} {lo[2]} {hi[0]} {hi[2]}", f"difficulty {want}",
                                   "gamerule random_tick_speed 0", "gamerule advance_weather false",
                                   f"gamerule advance_time {'true' if needs_clock(sc) else 'false'}")], feedback)
    if not difficulty_set(said, want):
        raise SetupInvalid(f"difficulty not {want}: {[l for l in said if 'ifficulty' in l][:1] or said[:1]}")
    # wait until the box's chunks load: a fill answers "not loaded" until then
    probe = _c(at(0, BOX[1][1], 0))
    for _ in range(60):
        if not any("not loaded" in l for l in _command(ex(f"fill {probe} {probe} air"), feedback)):
            break
        time.sleep(0.5)
    else:
        raise SetupInvalid("scenario chunks never loaded")
    # wait on a glass block above the box: no fall while it is rebuilt
    glass = _c(at(0, BOX[1][1] + 2, 0))
    # waiting glass, the body onto it, the previous row's mobs — one batch
    _batch([ex(f"fill {glass} {glass} glass"), ex(f"tp @p {_c(at(0, BOX[1][1] + 3, 0))}"),
            ex(f"kill @e[type=!player,x={lo[0]},y={lo[1]},z={lo[2]},dx={hi[0] - lo[0]},dy={hi[1] - lo[1] + 6},"
               f"dz={hi[2] - lo[2]}]")], feedback)
    if moved:
        time.sleep(3)            # the client loads the new dimension
    # clear the previous row's leftovers (lava) up above the glass, the layout in one burst, feedback checked once
    top = _c((hi[0], hi[1] + 6, hi[2]))
    above = [ex(f"fill {_c((lo[0], hi[1] + 3, lo[2]))} {top} air"),
             # fluids anywhere in the volume, the glass layer included
             ex(f"fill {_c(lo)} {top} air replace water"), ex(f"fill {_c(lo)} {top} air replace lava")]
    from .core import SITE_B, split_setup
    t_world = time.time()
    if take_prebuilt(name):
        # prebuilt at site B: one clone brings it over, then only what the row runs itself
        b_lo, b_hi = [a + d for a, d in zip(lo, SITE_B)], [a + d for a, d in zip(hi, SITE_B)]
        _world, rest = split_setup(sc["setup"])
        _batch(above + [ex(f"clone {_c(b_lo)} {_c(b_hi)} {_c(lo)} replace")], feedback)
        SETUP_S.update(prebuilt=True, world_s=round(time.time() - t_world, 2))
        t_body = time.time()
        _batch([ex(cmd) for cmd in rest], feedback)
    else:
        world, rest = split_setup(sc["setup"])
        _batch([ex(f"fill {_c(lo)} {_c((hi[0], hi[1] + 1, hi[2]))} air")] + above + [ex(c) for c in world], feedback)
        SETUP_S.update(prebuilt=False, world_s=round(time.time() - t_world, 2))
        t_body = time.time()
        _batch([ex(cmd) for cmd in rest], feedback)
    SETUP_S["body_s"] = round(time.time() - t_body, 2)
    # glass gone once we are down, the setup's drops, the body reset — one batch
    _batch([ex(f"fill {glass} {glass} air"), ex("kill @e[type=item]")] + [ex(c) for c in body_reset(sc)], feedback)
    # the client sees the build late: read until the expectation holds, then judge
    for i in range(10):
        bad = setup_mismatches(Region(lo, hi).blocks, sc.get("expect", [])) if sc.get("expect") else []
        if not bad:
            break
        time.sleep(0.5)          # read again only when it did not hold yet (a fixed 0.5 s first cost every row)
    for _ in range(12):          # summoned mobs and the health effect land a few ticks later (a ghast took > 3 s)
        # count on the server: the client's entity list misses far summons
        ents = [f"{t}: {n} on the server, expected ≥ {want}" for t, want in sc.get("expect_entities", [])
                for n in [server_count(_command(ex(f"execute as @p at @s if entity @e[type={t},distance=..40]"),
                                                feedback))] if n < want]
        s = api.get("/state")
        if not ents and s.get("health", 0) >= 18:
            break
        if s.get("health", 0) < 18:
            _command(ex("effect give @p minecraft:instant_health 1 10 true"), feedback)
        time.sleep(0.25)
    bad += ents
    if bad:
        raise SetupInvalid("; ".join(bad))
    if s.get("dimension") != dim:
        raise SetupInvalid(f"player in {s.get('dimension')}, scenario needs {dim}")
    if s.get("dead") or s.get("health", 0) < 18:
        raise SetupInvalid(f"player not healthy after setup (hp {s.get('health')})")

def _trace(stop, out):
    from .. import api
    while not stop.is_set():
        try:
            s = api.get("/state")
            out.append({"t": round(time.time(), 1), **{k: s.get(k) for k in
                        ("x", "y", "z", "health", "food", "dead", "inWater", "inLava", "onGround", "screen")},
                        "task": (s.get("control") or {}).get("task")})
        except Exception as e:
            out.append({"t": round(time.time(), 1), "error": str(e)})
        stop.wait(0.2)

def _report(name, data):
    from ..world import Inventory, Region
    try:
        data["inventory"] = [(s["id"], s["count"]) for s in Inventory().slots]
        lo, hi = at(*BOX[0]), at(*BOX[1])
        data["region"] = [[*p, n] for p, n in Region(lo, hi).blocks.items()]
    except Exception as e:
        data["report_error"] = str(e)
    folder = os.path.join(BENCH, name, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report.json"), "w") as f:
        json.dump(data, f, indent=1, default=str)
    if SCENARIOS.get(name, {}).get("combat"):
        # a dead fight becomes a replayable incident (tests/incidents/)
        try:
            from .. import end
            from ..tools import incidents
            state, intent = end.LAST_ROUND
            path = incidents.capture(name, str(data.get("note", "")), state, intent)
            if path:
                print(f"incident captured → {path}")
        except Exception as e:                 # capture must never mask the failure it records
            print(f"incident not captured: {e}")
    return folder

def run(name, make_ctx):
    """Set up and run one scenario (test world only)."""

    from .. import api
    from ..api import McError
    from ..world import Inventory
    if not os.path.exists(FLAG):
        raise RuntimeError("scenarios run only in a test world: `mc.py scenario enable` there first")
    sc = SCENARIOS[name]
    code = code_for(name)
    feedback, trace, stop = [], [], threading.Event()
    console = _Console(sys.stdout)
    exc, ok, seconds, note = None, False, 0.0, ""
    sys.stdout = console
    from .. import perception
    rate = sc.get("tick_rate")
    from .. import api as _api
    # a wait on the game's clock alone (a furnace cooking, api.waiting_for_clock) runs the clock ahead: the row's
    # work and checks stay the same, only the real-time wait for the furnace goes (smelting is 10 s an item)
    _api.CLOCK_HOOK = lambda s: _command(f"tick sprint {max(20, int(s * 20))}", feedback)
    try:
        if rate:
            # waiting-heavy rows run the game faster: skills wait in ticks, only wall time shrinks; reset below
            _command(f"tick rate {rate}", feedback)
        perception.PAUSED = True
        _api.clear_requests()      # nothing the last row left pending (a nightfall boundary) stops this one
        try:
            _setup(name, sc, feedback)
        except SetupInvalid as e:
            exc, note = e, f"SETUP_INVALID: {e}"
        finally:
            perception.PAUSED = False
        if exc is None and NEXT_ROW[0] and NEXT_ROW[0] != name:
            prebuild(NEXT_ROW[0])          # the next row's world, at site B, while this one runs
        if exc is None:
            threading.Thread(target=_trace, args=(stop, trace), daemon=True).start()
            t0 = time.time()
            result = None
            crashed = False
            LAST_FEEDBACK[:] = feedback
            fired = threading.Event()
            limit = sc["budget"]
            timer = None
            try:
              try:
                ctx = make_ctx()
                if sc.get("before"):
                    sc["before"](ctx)
                    ctx = make_ctx()      # the hook may move the player: rebuild policy/dimension there
                from .. import skillcore as _sc
                if _sc.dead():
                    # dead before the skill began: the setup is invalid, not the skill
                    raise SetupInvalid("player dead before the skill started")
                # the budget is the behaviour's: `before` hooks build the scene, so the clock starts here
                t0 = time.time()
                timer = _watchdog(limit, fired)
                result = sc["run"](ctx)
              finally:
                if timer is not None:
                    timer.cancel()
            except KeyboardInterrupt:
                if not fired.is_set():
                    raise                      # the user's ^C, not the limit
                exc = McError(f"{TIMEOUT}: stopped at the {limit}s limit")
                note = str(exc)
            except Exception as e:     # the skill's own failure is a result, not a crash of the bench
                exc, note = e, f"{type(e).__name__}: {e}"
                if not isinstance(e, (api.McError, api.NotAvailable, SetupInvalid)):
                    crashed = True      # a bug in our own code is never a pass, whatever the world looks like after
                    # a crash says nothing without its frames
                    import traceback as _tb
                    note += " @ " + " < ".join(f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
                                               for f in reversed(_tb.extract_tb(e.__traceback__)[-4:]))
            seconds = time.time() - t0
            LAST_LINES[:] = console.lines          # slices read the cerebellum's own log for loops
            try:
                inv_after = Inventory()
                reached = bool(sc["check"](api, inv_after))
                # a crash of ours is never a pass
                from . import vocab as _rows
                ok, why = judge(reached, seconds, sc["budget"], crashed, _rows.BASE.get("run_s"),
                                  _rows.BASE.get("target_s") or sc.get("target_s"))   # a row's own, measured at start
                ok = ok and not fired.is_set()
                if reached and not ok:
                    # the outcome came too slowly (or via our crash): say so
                    exc = exc or McError(why)
                    note = note or str(exc)
                if sc.get("detail"):
                    note = (note + " " if note else "") + sc["detail"](inv_after)   # what a pass really produced
                if ok and sc.get("fails"):
                    note = (note + " " if note else "") + f"(failed as expected: /{sc['fails']}/)"
            except Exception as e:
                exc = exc or type("HarnessError", (Exception,), {})(f"check failed: {e}")
                note = note or f"check failed: {e}"
            from .. import skillcore as _sc
            if not ok and type(exc).__name__ != "SetupInvalid" and _sc.dead():
                # died is the result, whatever the skill did after
                exc = McError("died")
                note = "died" + (f" ({note})" if note else "")
            if not ok and exc is None:
                # returned False without raising: blame the layer of its last failed task
                exc = silent_failure(console.lines, result)
                note = f"{type(exc).__name__}: {exc} (outcome not reached in {seconds:.0f}s, budget {sc['budget']}s)"
    finally:
        stop.set()
        sys.stdout = console.real
        if rate:
            _command("tick rate 20", feedback)
        _api.CLOCK_HOOK = None
    cls = classify(exc, ok)
    if not ok and cls not in UNCOUNTED and generic_failure(note):
        # a failure without a reason is recorded as such
        note = f"NO REASON: {note or type(exc).__name__}"
    if cls not in UNCOUNTED:
        save_table(record(load_table(), name, code, ok, seconds, note, cls))
    if not ok:
        folder = _report(name, {"scenario": name, "code": code, "cls": cls, "note": note, "seconds": seconds,
                                "feedback": feedback, "trace": trace, "log": console.lines[-200:],
                                "setup_s": dict(SETUP_S)})
        note = f"{note} [{cls}] → {folder}"
    return ok, seconds, note, cls, code

