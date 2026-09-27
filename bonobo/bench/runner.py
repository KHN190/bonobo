"""Running a scenario and judging it: setting the world up and checking it took, classing a failure, hashing the
code a verdict belongs to, the readiness table, and the report a red leaves behind.

Pure helpers first (they are what the offline suite tests), the live bench after.
"""
import ast
import hashlib
import io
import json
import os
import re
import sys
import threading
import time

from . import core
from .core import (BENCH, BOX, FLAG, PKG, SCENARIOS, TABLE, UNCOUNTED, SetupInvalid, _batch, _c, _checked,
                  _command, at, server_count)

# ---------------------------------------------------------------- pure helpers (tested offline)

ERROR_MARKS = ("not loaded", "Incorrect argument", "Unknown or incomplete", "<--[HERE]", "Unknown ", "Invalid ",
               "is not within", "Too many blocks", "Could not", "Failed to", "Expected ", "unexpected error",
               "out of the world")


# What the game last said back during a setup: read by scenarios that need the reply of a command they sent
# (a /locate answer, a spawn position). Lives here, with the runner that fills it.
LAST_FEEDBACK = []
# The cerebellum's own log for the run just finished: slice scenarios read it back to find loops.
LAST_LINES = []


def feedback_errors(lines):
    """Pure: the chat feedback lines that mean a command didn't do its job. 'No entity was found' (nothing to
    kill), 'has no effects to remove', 'No items were found' (nothing to clear) and 'No blocks were filled'
    (already that block) are fine."""
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
ROW_LIMIT_S = 60        # no row outside acceptance may ask for more (speedrun standard; target ≤ 30 s)
TIMEOUT = "TIMEOUT"     # the note's prefix for a row stopped at its limit: deterministic slowness, never re-run


def _watchdog(limit, fired):
    """Arm a timer: at `limit` s stop the body (/stop) and interrupt the row's run in the main thread."""
    import _thread

    def fire():
        fired.set()
        try:
            api.post("/stop")
        except Exception:
            pass
        _thread.interrupt_main()
    t = threading.Timer(limit, fire)
    t.daemon = True
    t.start()
    return t


def judge(reached, seconds, budget, crashed=False):
    """Pure: (ok, why not) for a finished row. The world must show the effect, within the budget's slack, and our
    own code must not have crashed on the way — a crash is never a pass, whatever the world looks like after."""
    if not reached:
        return False, "outcome not reached"
    if crashed:
        return False, "crashed on the way (a bug of ours)"
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
    """What one module imports from the package. Cached by path and mtime: this is asked once per module per
    scenario, and re-parsing the package for each answer was most of the offline suite's time."""
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


def dep_hash(module, pkg_dir=PKG):
    h = hashlib.sha1()
    for m in module_deps(module, pkg_dir):
        with open(os.path.join(pkg_dir, m + ".py"), "rb") as f:
            h.update(m.encode() + f.read())
    return h.hexdigest()[:10]


# The mod's Java sources, when they happen to be checked out next to this repository. They are a different
# project, so this is optional: with MC_MOD_SRC unset (the normal case for anyone who installed the mod from a
# release) readiness falls back to the jar version the mod reports over /status. Hashing the sources is the finer
# tool — it re-tests only the scenarios whose feature actually changed — but it cannot be a requirement.
JAVA = os.path.expanduser(os.environ.get("MC_MOD_SRC", ""))
# The mod features a scenario can depend on → their Java sources. A pathfinder change re-tests movement scenarios
# only, not a crafting scenario (a jar version bump used to reset every result).
# Core = what every task runs through. WorldInfo (/state fields) and HttpApi (routes like /plan) change often and
# rarely change behaviour: they are their own features, so adding a /state field doesn't reset every scenario.
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
    """Identity of the mod behind `tags`: a hash of its Java sources when they are available, else its jar version.

    Hashing the sources is the finer instrument — a pathfinder change re-tests movement scenarios and leaves crafting
    results standing — but it needs a checkout of a different repository, which most people running this will not
    have. Without one, fall back to the version the mod reports: coarser (any release resets every scenario) and
    still correct.

    What must never happen is falling back to a CONSTANT. An empty source directory would hash to the same digest
    forever, and every stale scenario result would look fresh.
    """
    if not java or not os.path.isdir(java):
        return "jar-" + _mod_version()
    files = list(MOD_CORE)
    # Default: every behaviour feature, not the /state and HTTP plumbing (tag those explicitly where they matter).
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
    """Readiness key: the skill's Python modules, the scenario's own layout (a broken setup — fences covering the
    pen — must not keep counting against the skill after it is fixed) and the Java sources it uses. Frozen per process:
    editing sources during a bench run changed the key mid-run and a finished scenario was run again 2× on old code."""
    if name not in _CODE:
        _CODE[name] = _code_for(name)
    return _CODE[name]


def names_at(point, scenarios=None):
    """Pure: the scenarios of one test point (refactor.md: A, B, C, D; rows without one are A), in table order."""
    rows = SCENARIOS if scenarios is None else scenarios
    return [n for n, sc in rows.items() if sc.get("point", "A") == point]


def _code_for(name):
    sc = SCENARIOS[name]
    # An expected-failure row's pattern is part of what it asserts: a new pattern is a new test.
    layout = hashlib.sha1(repr((sc["setup"], sc.get("expect"), sc["budget"], sc.get("fails"))).encode()).hexdigest()[:6]
    tags = sc.get("mod")
    if sc.get("mod_extra"):
        tags = sorted(set(tags if tags is not None else [k for k in MOD_FILES if k not in ("state", "http")])
                      | set(sc["mod_extra"]))
    return f"{dep_hash(sc['module'])}{layout}-{mod_hash(tags)}"


# Settled scenarios: obvious mechanics that passed and never failed are not re-run for code or jar changes (placing
# eyes, throwing gold at piglins). Re-test by name with --force, or drop the name here after a live-run problem.
STABLE = {"activate_end_portal", "barter_piglin", "enter_end", "craft_eyes", "gold_helmet_swap", "loot_chest",
          "fill_water_bucket", "enter_nether", "return_from_nether", "relight_portal", "cast_portal",
          "build_light_portal",
          # deterministic layouts with no opponent: once they pass, logic fixes don't need a game run to prove them
          "craft_stone_tools", "iron_ingots", "hunt_food", "gather_logs", "gather_logs_birch", "recover_items",
          "retreat_from_nether", "return_to_portal", "find_fortress", "water_clutch", "cross_lava_3", "cross_lava_8",
          "cross_lava_lake"}


# Only fights change run to run (mob AI, knockback, fireballs). Everything else is settled once it passes.
FIGHTS = {"collect_blaze_rods", "fight_zombie_1", "fight_zombie_3", "fight_skeleton_1", "fight_creeper_1", "fight_blaze_3", "fight_enderman_1", "ghast_fireball", "bed_bomb_kill", "fight_dragon", "siege", "combat_arena",
          "escape"}


def settled(table, name):
    """Pure: a non-fight scenario whose latest counted run (under any code) passed. One pass settles it (user,
    2026-09-16): deterministic layouts don't need a second confirmation; a later failure re-opens it."""
    if name in FIGHTS or name.split("__")[0] in FIGHTS:     # siege__w1, combat_arena__3, escape__…: shards
        return False
    runs = sorted((r for c in table.get(name, {}).values() for r in c if r.get("cls", "skill") not in UNCOUNTED),
                  key=lambda r: r.get("t", 0))
    return bool(runs) and runs[-1]["ok"]


MAX_RUNS = 3      # a row runs once; a failure is re-run, three runs at most, and passes on ≥ 2 of 3


def verdict_of(oks):
    """Pure: the verdict of a row's counted runs, in order (the last MAX_RUNS). One pass is a pass; two failures are a
    fail; one of each needs the third run, which decides (≥ 2 of 3). None = run again."""
    oks = list(oks)[-MAX_RUNS:]
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


def verdict(table, name, code):
    """Pure: 'pass' / 'fail' for the current code's counted runs (`verdict_of`), else None."""
    counted = [r for r in table.get(name, {}).get(code, []) if r.get("cls", "skill") not in UNCOUNTED]
    return verdict_of([TIMEOUT if r.get("note", "").startswith(TIMEOUT) else r["ok"] for r in counted])


# ---------------------------------------------------------------- readiness table (pure helpers are tested offline)

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
    """Pure: 'untested' | 'failing' | 'scenario' (the current code's verdict passes: `verdict_of`) and the median pass
    time. Setup and harness failures don't count: they say nothing about the skill."""
    runs = [r for r in table.get(scenario, {}).get(code, []) if r.get("cls", "skill") not in UNCOUNTED]
    if not runs:
        return "untested", None
    passes = sorted(r["s"] for r in runs if r["ok"])
    median = passes[len(passes) // 2] if passes else None
    return ("scenario" if verdict_of([r["ok"] for r in runs]) == "pass" else "failing"), median


def save_table(table, path=None):
    path = path or TABLE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(table, f, indent=1)


# ---------------------------------------------------------------- live bench

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


def _setup(name, sc, feedback):
    from .. import api
    from ..world import Region, entities
    if api.get("/state").get("dead"):
        api.post("/respawn")
        time.sleep(2)
    api.post("/resume")          # a pause menu freezes the integrated server: commands would do nothing
    time.sleep(0.5)
    lo, hi = at(*BOX[0]), at(*BOX[1])
    combat = sc.get("combat", False)
    dim = sc.get("dimension", "minecraft:overworld")
    moved = api.get("/state")["dimension"] != dim

    def ex(cmd):                 # every command runs in the scenario's dimension (tp included: it moves us there)
        return f"execute in {dim} run {cmd}"

    if sc.get("raw"):
        # Real-world scenarios (a real stronghold, the real dragon): no box, just the body reset and the commands.
        if moved:
            # Another dimension first. "@p" inside "execute in <dim>" only finds players already there (a raw
            # Overworld scenario after an End one failed setup 8×): park with @a on the waiting glass of that
            # dimension, chunks loaded, before any scenario command.
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
        for cmd in ("gamemode survival @p", "effect clear @p", "time set day", "weather clear",
                    f"difficulty {'normal' if combat else 'peaceful'}"):
            _checked(ex(cmd), feedback)
        for cmd in sc["setup"]:
            _checked(ex(cmd), feedback)
        _command(ex("effect give @p minecraft:instant_health 1 10 true"), feedback)
        _command(ex("effect give @p minecraft:saturation 1 10 true"), feedback)
        time.sleep(4 if moved else 1.5)
        s = api.get("/state")
        if s.get("dimension") != dim:
            raise SetupInvalid(f"player in {s.get('dimension')}, scenario needs {dim}")
        # Real-world scenarios need their actors too (a dragon fight ran with no dragon: "target not found").
        for t, want in sc.get("expect_entities", []):
            n = server_count(_command(ex(f"execute as @p at @s if entity @e[type={t},distance=..160]"), feedback))
            if n < want:
                raise SetupInvalid(f"{t}: {n} on the server, expected ≥ {want}")
        return

    # Empty bag first: a water bucket left from the previous scenario made any setup drop a water clutch trigger.
    for cmd in ("clear @p", "gamemode survival @p", "effect clear @p", "time set day", "weather clear",
                "gamerule spawn_mobs false", f"difficulty {'normal' if combat else 'peaceful'}",
                f"forceload add {lo[0]} {lo[2]} {hi[0]} {hi[2]}"):
        _checked(ex(cmd), feedback)
    # Wait until the box's chunks are really loaded: a 1-block fill answers "not loaded" until then.
    probe = _c(at(0, BOX[1][1], 0))
    for _ in range(60):
        if not any("not loaded" in l for l in _command(ex(f"fill {probe} {probe} air"), feedback)):
            break
        time.sleep(0.5)
    else:
        raise SetupInvalid("scenario chunks never loaded")
    # Wait above the box on a glass block: no fall while the box is rebuilt (a fall fired the water clutch).
    glass = _c(at(0, BOX[1][1] + 2, 0))
    _checked(ex(f"fill {glass} {glass} glass"), feedback)   # setblock errors when it's already glass; fill doesn't
    _checked(ex(f"tp @p {_c(at(0, BOX[1][1] + 3, 0))}"), feedback)
    if moved:
        time.sleep(3)            # the client loads the new dimension
    # Mobs of the previous scenario ("No entity was found" is fine).
    _command(ex(f"kill @e[type=!player,x={lo[0]},y={lo[1]},z={lo[2]},dx={hi[0] - lo[0]},dy={hi[1] - lo[1] + 6},"
                f"dz={hi[2] - lo[2]}]"), feedback)
    # Leftovers of the previous scenario (lava!) go first — up to above the waiting glass: water poured on the glass
    # (y 211, outside the box) kept flowing back into every later setup (cross_lava: 48 water, 9 obsidian).
    # Two fills around the glass layer: removing the glass under the player dropped them for a moment.
    # The whole layout in one burst, feedback checked once at the end (waiting for every reply cost ~10 min a round).
    top = _c((hi[0], hi[1] + 6, hi[2]))
    _batch([ex(f"fill {_c(lo)} {_c((hi[0], hi[1] + 1, hi[2]))} air"),
            ex(f"fill {_c((lo[0], hi[1] + 3, lo[2]))} {top} air"),
            # Fluids anywhere in the volume, the glass layer included (water beside the glass survived both fills
            # above and kept flooding cast_obsidian's pool: 47 water, 0 lava).
            ex(f"fill {_c(lo)} {top} air replace water"), ex(f"fill {_c(lo)} {top} air replace lava")]
           + [ex(cmd) for cmd in sc["setup"]], feedback)
    # The waiting glass must go once we're down: a 30-block fall landed on it 18 blocks early (water_clutch, hp 5).
    _checked(ex(f"fill {glass} {glass} air"), feedback)
    _command(ex("kill @e[type=item]"), feedback)
    _checked(ex("effect give @p minecraft:instant_health 1 10 true"), feedback)
    _checked(ex("effect give @p minecraft:saturation 1 10 true"), feedback)
    time.sleep(1.0)
    blocks = Region(lo, hi).blocks
    bad = setup_mismatches(blocks, sc.get("expect", []))
    for _ in range(12):          # summoned mobs and the health effect land a few ticks later (a ghast took > 3 s)
        # Count on the server: the client's entity list missed a summoned ghast 18 blocks away ("seen: nothing").
        ents = [f"{t}: {n} on the server, expected ≥ {want}" for t, want in sc.get("expect_entities", [])
                for n in [server_count(_command(ex(f"execute as @p at @s if entity @e[type={t},distance=..40]"),
                                                feedback))] if n < want]
        s = api.get("/state")
        if not ents and s.get("health", 0) >= 18:
            break
        if s.get("health", 0) < 18:
            _command(ex("effect give @p minecraft:instant_health 1 10 true"), feedback)
        time.sleep(0.5)
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
                        ("x", "y", "z", "health", "dead", "inWater", "inLava", "onGround", "screen")},
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
        # A dead fight becomes an incident: the planner's own last input, replayable offline, adoptable into
        # tests/incidents/. Nothing learned from a live failure stays in a log.
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
    """Set up and run one scenario (test world only). Returns (ok, seconds, note, cls, code).
    `make_ctx()` is called after the setup: a context built before it carries the old place's policy and
    dimension."""
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
    try:
        if rate:
            # Waiting-heavy scenarios (smelting, piglin inspection) run the game faster; skills wait in ticks, so
            # their logic is unchanged — only wall time shrinks. Always reset below.
            _command(f"tick rate {rate}", feedback)
        perception.PAUSED = True
        try:
            _setup(name, sc, feedback)
        except SetupInvalid as e:
            exc, note = e, f"SETUP_INVALID: {e}"
        finally:
            perception.PAUSED = False
        if exc is None:
            threading.Thread(target=_trace, args=(stop, trace), daemon=True).start()
            t0 = time.time()
            result = None
            crashed = False
            LAST_FEEDBACK[:] = feedback
            fired = threading.Event()
            limit = sc["budget"]
            timer = _watchdog(limit, fired)
            try:
              try:
                ctx = make_ctx()
                if sc.get("before"):
                    sc["before"](ctx)
                    ctx = make_ctx()      # the hook may move the player: rebuild policy/dimension there
                from .. import skillcore as _sc
                if _sc.dead():
                    # Dead before the skill began (a dragon fight started at 0 hp): the setup is invalid, not the skill.
                    raise SetupInvalid("player dead before the skill started")
                result = sc["run"](ctx)
              finally:
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
                    # An unexpected crash ("IndexError: tuple index out of range") says nothing without its frames.
                    import traceback as _tb
                    note += " @ " + " < ".join(f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
                                               for f in reversed(_tb.extract_tb(e.__traceback__)[-4:]))
            seconds = time.time() - t0
            LAST_LINES[:] = console.lines          # slices read the cerebellum's own log for loops
            try:
                inv_after = Inventory()
                reached = bool(sc["check"](api, inv_after))
                # A crash (IndexError from our own code) passed the check once and was recorded as PASS.
                ok, why = judge(reached, seconds, sc["budget"], crashed)
                ok = ok and not fired.is_set()
                if reached and not ok:
                    # The outcome happened, just too slowly (or through a crash of ours): say so.
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
                # Died: that's the result, whatever the skill did afterwards (20 "no route" travels after death).
                exc = McError("died")
                note = "died" + (f" ({note})" if note else "")
            if not ok and exc is None:
                # A skill that returned False / nothing without raising: blame the layer of its last failed task.
                exc = silent_failure(console.lines, result)
                note = f"{type(exc).__name__}: {exc} (outcome not reached in {seconds:.0f}s, budget {sc['budget']}s)"
    finally:
        stop.set()
        sys.stdout = console.real
        if rate:
            _command("tick rate 20", feedback)
    cls = classify(exc, ok)
    if not ok and cls not in UNCOUNTED and generic_failure(note):
        # "A failure must carry a reason" (todo): a row that failed without saying why is recorded as such.
        note = f"NO REASON: {note or type(exc).__name__}"
    if cls not in UNCOUNTED:
        save_table(record(load_table(), name, code, ok, seconds, note, cls))
    if not ok:
        folder = _report(name, {"scenario": name, "code": code, "cls": cls, "note": note, "seconds": seconds,
                                "feedback": feedback, "trace": trace, "log": console.lines[-200:]})
        note = f"{note} [{cls}] → {folder}"
    return ok, seconds, note, cls, code


