"""The bench's primitives: where a scenario stands, how a command is sent, and the sweep engine under every swept bench. Everything here is shared by the tables (`bench.vocab`) and by the runner; nothing here knows about any particular scenario."""

import json
import os
import time

from .. import lifecycle, paths
from ..api import READ_EVERY_S
from ..data import CRITICAL_HP

SCENARIOS = {}

FLAG = paths.data("test-world")
TABLE = paths.data("readiness.json")
NOTES = paths.data("test-world-notes.json")
BENCH = paths.data("bench")
ORIGIN = (10000, 200, 10000)
TREE_HEIGHT = 5                # logs in one bench tree (its trunk)
KEPT_HP = CRITICAL_HP + 1      # a hazard row's floor: above perception's critical health   # a sky platform: skills search 48 blocks, natural terrain (y ≤ ~120) stays out of it
PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# everything a scenario touches lies in this box (cleared and force-loaded before each setup)
BOX = ((-10, -17, -10), (20, 9, 10))   # down to -17: the underground rows (cave_escape, night_mines) are reset too
UNCOUNTED = ("setup", "harness")

# the world settings a row changes, as the bench holds them (runner's setup), and the game's normal value of every
# setting the bench changes anywhere — restored before a free run (tools.leave_bench); a bench frozen clock kept
# free play at noon for 8 min, no night ever planned for
BENCH_WORLD = {"gamerule spawn_mobs": "false", "gamerule random_tick_speed": "0", "gamerule advance_weather": "false"}
WORLD_NORMAL = {"gamerule spawn_mobs": "true", "gamerule random_tick_speed": "3", "gamerule advance_weather": "true",
                "gamerule advance_time": "true", "gamerule natural_regeneration": "true", "tick rate": "20",
                "difficulty": "normal"}
RESTORE_ALSO = ("forceload remove all",)        # the site chunks the rows force-loaded


def fight_line_hp(mob, sword, armor=0, shield=False):
    """Pure: the health an optional fight with `mob` starts from at least (S5) — critical plus its loss's quantile
    with this sword (item id, None = the hand) and armour: the production line (estimate.fight_line_ok), never a typed hp."""
    from .. import beliefs, estimate
    from ..data import critical_hp
    mean, hit = estimate.melee_loss([mob], sword, beliefs.protection(armor, shield, hit=beliefs.MOBS[mob]["attack"]))
    return critical_hp({}) + estimate.loss_q(mean, hit)


def restore_commands():
    """Pure: the commands that put every bench-changed setting back to the game's normal."""
    return [f"{k} {v}" for k, v in WORLD_NORMAL.items()] + list(RESTORE_ALSO)


SITE_B = (100, 0, 0)

# the kit rule: a row whose work uses a tool gets the best one, unless the tool is what is tested
BEST_TOOLS = {"axe": "give @p diamond_axe", "pickaxe": "give @p diamond_pickaxe", "shovel": "give @p diamond_shovel"}
# the tools a skill's work uses, and a queued need's
SKILL_JOBS = {"item:log": ("axe",), "chop": ("axe",), "mine": ("pickaxe",), "bridge_toward": ("pickaxe",),
              "burrow": ("pickaxe",), "cast_portal": ("pickaxe",), "dig_out": ("pickaxe",), "find_air": ("pickaxe",),
              "strip_mine_step": ("pickaxe",), "unbury": ("shovel",), "shelter:dig in": ("pickaxe", "shovel"),
              "hunt": ("sword",), "smelt": ("pickaxe",)}
NEED_JOBS = {"log": "axe", "minecraft:cobblestone": "pickaxe", "minecraft:raw_iron": "pickaxe",
             "minecraft:diamond": "pickaxe"}

def kit_jobs(row):
    """Pure: the jobs a row's kit serves — its own `kit` when it states one (a fight, a tool under test), else its
    skills' and its queue's needs'."""
    if "kit" in row:
        return tuple(row["kit"])
    jobs = {j for s in row.get("skills") or () for j in SKILL_JOBS.get(s, ())}
    jobs |= {NEED_JOBS[n[0]] for g in row.get("queue") or () if isinstance(g, dict)
             for n in g.get("args", {}).get("needs", []) if n[0] in NEED_JOBS}
    return tuple(sorted(jobs))
HIGH_TIER_MOBS = ("blaze", "wither_skeleton", "enderman", "ravager", "warden", "ender_dragon", "wither",
                  "elder_guardian", "evoker")
ORDINARY_WEAPON = "iron_sword"

def kit_sword(mobs):
    """Pure: the sword a row fighting `mobs` (bare names) is given — the best any of them calls for."""
    return f"give @p {'diamond_sword' if any(m in HIGH_TIER_MOBS for m in mobs) else ORDINARY_WEAPON}"
WORLD_CMDS = ("fill", "setblock", "clone", "place", "forceload")     # the build: no player in it, built ahead
LATE_CMDS = ("summon",)                  # actors: summoned in the row itself — built ahead they wander or burn

def body_reset(sc):
    """Pure: commands that refill the body after setup — health always, food unless the row makes the player hungry."""

    hungry = sc.get("tags", {}).get("state") == "hungry" or any("minecraft:hunger" in c for c in sc.get("setup", ()))
    return ["effect give @p minecraft:instant_health 1 10 true"] + \
        ([] if hungry else ["effect give @p minecraft:saturation 1 10 true"])

def classify_command(cmd):
    """Pure: "world" (absolute block build: ahead at site B), "late" (a summon: in the row) or "body" (player, global state, `~`: in the row)."""

    inner = cmd
    while inner.startswith("execute ") and " run " in inner:
        inner = inner.split(" run ", 1)[1]
    head = (inner.split() or [""])[0]
    if head in WORLD_CMDS and "~" not in inner and "@" not in inner:
        return "world"
    return "late" if head in LATE_CMDS else "body"

def split_setup(setup):
    """Pure: (world commands, the rest in their order) — what can be built ahead, and what the row runs itself."""
    return [c for c in setup if classify_command(c) == "world"], [c for c in setup if classify_command(c) != "world"]

_TRIPLE = __import__("re").compile(r"(?<![\w.~^-])(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)(?![\w.])")

def shift(cmd, offset=SITE_B, origin=ORIGIN, box=BOX, margin=8):
    """Pure: the command with every coordinate triple inside site A's box (± margin) moved by `offset` — the same build at site B."""

    lo = [origin[i] + box[0][i] - margin for i in range(3)]
    hi = [origin[i] + box[1][i] + margin for i in range(3)]

    def move(m):
        vals = [m.group(i + 1) for i in range(3)]
        nums = [float(v) for v in vals]
        if not all(lo[i] <= nums[i] <= hi[i] for i in range(3)):
            return m.group(0)
        out = [str(int(nums[i]) + offset[i]) if "." not in vals[i] else f"{nums[i] + offset[i]:g}" for i in range(3)]
        return " ".join(out)
    return _TRIPLE.sub(move, cmd)

def at(dx, dy, dz, origin=ORIGIN):
    return origin[0] + dx, origin[1] + dy, origin[2] + dz

def pos(p):
    """("@", dx, dy, dz) → the absolute position; anything else unchanged."""
    return at(*p[1:]) if isinstance(p, tuple) and len(p) == 4 and p[0] == "@" else p

def _c(p):
    p = pos(p)
    return f"{p[0]} {p[1]} {p[2]}"

BRAIN = None     # set by `mc.py scenario`: plan-driven scenarios execute steps exactly as the brain does

def _achieve(ctx, needs, done, rounds=12):
    """Plan the needs from the bag and run the first step until `done()` — the brain's own path."""
    from .. import decompose, dispatch, goals
    from ..cost import Cost
    from .. import api
    from ..world import Inventory, Snapshot
    from ..knowledge import SOURCE_BLOCKS
    from .. import survive
    from .. import jobs as _jobs
    brain = BRAIN
    assert brain is not None, "set_brain first: plan-driven rows run the brain's own path"
    for _ in range(rounds * 4):
        if done():
            return True
        snap = Snapshot.read(SOURCE_BLOCKS, survive.ROUND_GROUND)
        pending = brain.mem.jobs(snap.dimension)
        from .. import world as _world
        ready = [j for j in pending if _world.job_ready(j, snap.state.get("gameTime"))]
        if ready:
            _jobs.collect(ctx, ready[0])        # a background furnace finished: take its output (the brain's job)
            continue
        plan = decompose.decompose(snap.inv, goals.have(*needs), Cost(snap, brain.mem),
                                   pending=brain.mem.pending_outputs(snap.dimension))
        if not plan:
            if pending:
                time.sleep(1)                   # everything else is done; the furnace is still cooking
                continue
            break
        dispatch.execute(ctx, plan[0], False)
    if not done():
        from ..api import McError
        raise McError(f"needs {needs} not met after {rounds} plan steps")
    return True


# the engine under every sweep bench: cells, their build commands, what a row records, rules over the table

SWEEP = {}
lifecycle.on_reset(lambda: SWEEP.clear(), covers=("SWEEP",))       # a sweep's check reads only the rows this row writes

def _platform(reach=9, walled=False):
    """Bare stone, nothing alive, us in the middle: where every cell starts."""

    lo, hi = at(-reach, -2, -reach), at(reach + 3, 6, reach)
    floor_hi = at(reach + 3, -1, reach)
    out = [f"fill {_c(lo)} {_c(hi)} air", f"fill {_c(lo)} {_c(floor_hi)} stone",
           f"tp @p {_c(at(0, 0, 0))}", "kill @e[type=!player,distance=..40]"]
    if walled:
        for a, b in (((-reach, 0, -reach), (reach + 3, 2, -reach)), ((-reach, 0, reach), (reach + 3, 2, reach)),
                     ((-reach, 0, -reach), (-reach, 2, reach)), ((reach + 3, 0, -reach), (reach + 3, 2, reach))):
            out.append(f"fill {_c(at(*a))} {_c(at(*b))} stone")
    return out

def summoned(cmds):
    """Pure: {entity type: n} that `cmds` summon (execute … run summon T … counts as a summon of T)."""
    out = {}
    for c in cmds:
        while c.startswith("execute ") and " run " in c:
            c = c.split(" run ", 1)[1]
        w = c.split()
        if len(w) > 1 and w[0] == "summon":
            t = w[1] if ":" in w[1] else f"minecraft:{w[1]}"
            out[t] = out.get(t, 0) + 1
    return out


CELL_CAP_S = 2.0        # a cell's summons counted on the server by then, or the row records what is there


def _cell_ready(cmds, feedback):
    """Until what the cell summoned is on the server (a cell with no summon: at once)."""
    want = summoned(cmds)
    t0 = time.time()
    while want and time.time() - t0 < CELL_CAP_S:
        if all(server_count(_command(f"execute as @p at @s if entity @e[type={t},distance=..40]", feedback)) >= n
               for t, n in want.items()):
            return
        time.sleep(READ_EVERY_S)


def _sweep(name, cells, build, record, path, settle=CELL_CAP_S):
    """One pass: build each cell, record one row, append it; returns this pass's rows. `settle` > 0: wait for the
    cell's summons (never a fixed sleep); 0: none (a window that must begin at once)."""
    def run(_ctx):
        feedback, rows = [], []
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as out:
            for cell in cells():
                cmds = list(build(cell))
                for cmd in cmds:
                    _command(cmd, feedback)
                if settle > 0:
                    _cell_ready(cmds, feedback)
                row = {"t": time.time(), **cell, **record(cell)}
                rows.append(row)
                out.write(json.dumps(row) + "\n")
                out.flush()
        SWEEP[name] = rows
        return rows
    return run

def _sweep_check(name, path, rules, least):
    """The verdict is what the RULES say about the finished table — never which name won a cell. Only this run's rows
    (SWEEP[name], cleared at each row's setup): the file `path` is shared by every shard and holds older runs, so a
    run that wrote no rows fails instead of passing on another run's."""
    said = {}                    # each part's messages from the last judging: what check_parts reads back

    def judge():
        rows = SWEEP.get(name) or []
        said.clear()
        said["rows"] = [] if len(rows) >= least else [f"{len(rows)} rows, need ≥ {least}"]
        if not said["rows"]:
            for rule in rules:
                said[_rule_name(rule)] = list(rule(rows) or [])
        return said

    def check(_api, _inv):
        bad = [m for msgs in judge().values() for m in msgs]
        if bad:
            print(f"{name}: " + "; ".join(bad[:6]))
        return not bad

    def part(key):
        def ask(_api, _inv):
            got = said if said else judge()
            return key in got and not got[key]          # a rule not judged (too few rows) said nothing: no pass
        ask.__table__ = (key,)
        ask.why = lambda: "; ".join(said[key]) if said.get(key) else ("not judged" if key not in said else False)
        return ask
    # the parts a failed row's readout names (runner.check_parts): the row count, then each rule
    check.parts = [part("rows")] + [part(_rule_name(r)) for r in rules]
    return check

def _rule_name(rule):
    return str(getattr(rule, "__table__", None) or getattr(rule, "__name__", "?"))

# chat has no ids: every sender holds this for its send-and-read so no reply lands in another's read
CHAT_LOCK = __import__("threading").RLock()

def _chat(cmd):
    """A command from a `before` hook (after setup, perception running): world changes the skill must react to."""
    from .. import api
    with CHAT_LOCK:
        api.post("/chat", {"message": "/" + cmd})
        time.sleep(0.3)

def _drain(result):
    """Run a skill generator to its end when it's called directly (the @skill wrapper normally drives it)."""
    if hasattr(result, "__next__"):
        for _ in result:
            pass
    return result

class SetupInvalid(Exception):
    """The scenario wasn't built as specified: the run says nothing about the skill."""

def _count_blocks(api, lo, hi, name):
    from ..world import Region
    return sum(1 for n in Region(lo, hi).blocks.values() if n == name)

def _chat_log():
    from .. import api
    return os.path.join(api.INSTANCE, "logs", "latest.log")

REPLY_WAIT_S = 0.6        # the most a batch waits for its replies (they come within ~0.3 s; a silent one never)
REPLY_POLL_S = 0.05

def chat_lines(text):
    """Pure: the chat lines in a stretch of the client log (what follows "[CHAT] ")."""
    return [l.split("[CHAT] ", 1)[1] for l in text.splitlines() if "[CHAT] " in l]

def _send(cmds, expect, wait):
    """Send `cmds` back to back and read the log until `expect` chat lines came back, or `wait` seconds."""

    from .. import api
    with CHAT_LOCK:
        path = _chat_log()
        size = os.path.getsize(path)
        for cmd in cmds:
            api.post("/chat", {"message": "/" + cmd})
        t0, lines = time.time(), []
        while time.time() - t0 < wait:
            time.sleep(REPLY_POLL_S)
            with open(path, "rb") as f:
                f.seek(size)
                lines = chat_lines(f.read().decode(errors="replace"))
            if len(lines) >= expect:
                break
    return lines

def bag_now():
    """The bag as the game reports it now — the bench's one bag read (world.Inventory is the only /inventory caller)."""
    from ..world import Inventory
    return Inventory()

def _command(cmd, feedback, timeout=REPLY_WAIT_S):
    """Send one command and wait for its chat feedback in the client log; returns the new chat lines."""
    lines = _send([cmd], 1, timeout)
    feedback.append({"cmd": cmd, "reply": lines})
    return lines

SILENT = ("gamemode",)      # may answer nothing (the mode already set): never waited on


def replies(cmds):
    """Pure: how many of `cmds` always answer in chat (execute … run X answers as X)."""
    def head(c):
        while c.startswith("execute ") and " run " in c:
            c = c.split(" run ", 1)[1]
        return (c.split() or [""])[0]
    return sum(1 for c in cmds if head(c) not in SILENT)


def _batch(cmds, feedback, settle=REPLY_WAIT_S):
    """Send commands back to back, then read the replies at once; any error line fails the setup. Waits only for
    the commands that answer: a silent one cost the whole settle."""

    lines = _send(list(cmds), replies(cmds), settle) if cmds else []
    feedback.append({"cmd": f"batch of {len(cmds)}", "cmds": list(cmds), "reply": lines})
    from .runner import feedback_errors      # runner imports core: ask for it when needed, not at import time
    bad = feedback_errors(lines)
    if bad:
        raise SetupInvalid(f"setup batch → {bad[0]}")
    return lines

def _checked(cmd, feedback):
    from .runner import feedback_errors      # see above: one reader of what the game said back
    bad = feedback_errors(_command(cmd, feedback))
    if bad:
        raise SetupInvalid(f"/{cmd} → {bad[0]}")

def server_count(lines):
    """Pure: N from '/execute if entity' feedback ('Test passed, count: N'); 0 for 'Test failed'."""
    import re
    for line in lines:
        m = re.search(r"count: (\d+)", line, re.IGNORECASE)   # the server says "Test passed. Count: 1"
        if m:
            return int(m.group(1))
    return 0


def fresh_row(brain):
    """Before every row: each world-scoped store a row can leave behind (fresh.WORLD_SCOPED: taught doors, tasks…)
    and the bench's notes (home sites) dropped, then a brain on empty notes (031434: a hatch lesson steered the side
    room's walk)."""
    from .. import fresh
    from ..memory import Memory
    if not os.environ.get("MC_DATA"):
        raise RuntimeError("bench rows drop world-scoped stores: run with MC_DATA set (mc.py scenario sets its own)")
    fresh.drop(fresh.WORLD_SCOPED + (os.path.basename(NOTES),))
    reset_brain(brain, Memory(NOTES))


def reset_brain(brain, mem):
    """Every row starts from a brain that knows nothing of earlier rows (upkeep's tool notes outlived `clear @p`)."""

    from .. import arbiter, fight_loop, nav, needs, reflexes, retry, skill as skillkit
    # a fight left engaged by the last row still holds the body
    held = fight_loop.engaged()
    if held is not None:
        fight_loop.disengage(held)
    holder = arbiter.BODY.holder()
    if holder is not None:
        arbiter.BODY.hand_back(holder)
    brain.mem = mem
    skillkit.STATS = nav.ROAD_MEM = mem
    brain.blacklist.clear()           # in place: fight_loop and every Context share this dict
    brain.retry = retry.Retry()
    brain.picks = __import__("collections").Counter()
    brain.held = {}
    brain.needs, brain.reflexes = needs.Needs(brain), reflexes.Maintain(brain)
    brain.place = brain.idle_since = brain.committed = brain.last_failure = None
    fight_loop.wire(brain.mem, lambda snap: brain.policy(snap, snap.night), brain.blacklist,
                    prices=brain.price_table)

def set_brain(brain):
    """`mc.py scenario` hands the bench the brain: plan-driven scenarios execute steps exactly as it does."""
    global BRAIN
    BRAIN = brain
    _wire_farm_probe()                 # the bench's server-side reads for the farm's instrumentation


PROBE_SEQ = paths.session("bench.core.PROBE_SEQ", lambda: [0])

def _probe(cmd):
    """One test command's own reply: sent with a numbered `say` after it, the reply is the chat line before that
    marker — never a line from another command (both farm probes once read the same 'Test passed')."""
    PROBE_SEQ[0] += 1
    mark = f"probe-{PROBE_SEQ[0]}"
    lines = _send([cmd, f"say {mark}"], 2, 1.5)
    return reply_before(lines, mark)

def reply_before(lines, mark):
    """Pure: the chat lines that came before the line carrying `mark` (all of them when it never came)."""
    for k, line in enumerate(lines):
        if mark in line:
            return lines[:k]
    return list(lines)

def probe_answer(lines):
    """Pure: True for an exact 'Test passed', False for 'Test failed', None when neither is there (no answer)."""
    for line in lines:
        text = line.strip()
        if text.startswith("Test passed"):
            return True
        if text.startswith("Test failed"):
            return False
    return None

def _wire_farm_probe():
    """The farm's instrumentation (farming.PROBE): the server's view of a cell's block (an `execute if block` reply) and the
    random_tick_speed in effect (a `gamerule` reply) — the client's reads cannot tell a ghost block."""
    from .. import farming

    def server_block(pos, block):
        return probe_answer(_probe(f"execute if block {pos[0]} {pos[1]} {pos[2]} minecraft:{block.split(':')[-1]}"))

    def tick_speed():
        return " / ".join(str(l) for l in _command("gamerule random_tick_speed", []))
    farming.PROBE.update(server_block=server_block, tick_speed=tick_speed)

