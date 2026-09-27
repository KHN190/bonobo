"""The bench's primitives: where a scenario stands, how a command is sent, and the sweep engine under every
swept bench. Everything here is shared by the sheets (`bench.fight`, `scenarios`) and by the
runner; nothing here knows about any particular scenario.
"""
import json
import os
import time

from .. import paths

SCENARIOS = {}

FLAG = paths.data("test-world")
TABLE = paths.data("readiness.json")
NOTES = paths.data("test-world-notes.json")
BENCH = paths.data("bench")
ORIGIN = (10000, 200, 10000)   # a sky platform: skills search 48 blocks, natural terrain (y ≤ ~120) stays out of it
PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Everything a scenario touches lies inside this box (cleared to air before each setup, force-loaded).
BOX = ((-10, -17, -10), (20, 9, 10))   # down to -17: the underground rows (cave_escape, night_mines) are reset too
UNCOUNTED = ("setup", "harness")


# Two sites, one box shape: the row runs at ORIGIN (site A) while the next row's world is built at ORIGIN + SITE_B,
# then cloned over in one command. The player never goes to B; every coordinate a row knows is site A's.
SITE_B = (100, 0, 0)
WORLD_CMDS = ("fill", "setblock", "clone", "place", "forceload")     # the build: no player in it, built ahead
LATE_CMDS = ("summon",)                  # actors: summoned in the row itself — built ahead they wander or burn


def body_reset(sc):
    """Pure: the commands that put the body back to full after a row's setup — health always, food unless the row
    itself makes the player hungry (a hunger effect in its setup, or tagged hungry for its `before` hook): the
    saturation given after the setup refilled the bar the row had just drained (eat_while_walking was never hungry)."""
    hungry = sc.get("tags", {}).get("state") == "hungry" or any("minecraft:hunger" in c for c in sc.get("setup", ()))
    return ["effect give @p minecraft:instant_health 1 10 true"] + \
        ([] if hungry else ["effect give @p minecraft:saturation 1 10 true"])


def classify(cmd):
    """Pure: "world" (a block build with absolute coordinates: built ahead at site B), "late" (a summon: in the
    row, after the switch) or "body" (the player and the world's global state — bag, effects, position, time,
    rules, difficulty — and anything relative to the player, `~`: in the row)."""
    inner = cmd
    while inner.startswith("execute ") and " run " in inner:
        inner = inner.split(" run ", 1)[1]
    head = (inner.split() or [""])[0]
    if head in WORLD_CMDS and "~" not in inner and "@" not in inner:
        return "world"
    return "late" if head in LATE_CMDS else "body"


def split_setup(setup):
    """Pure: (world commands, the rest in their order) — what can be built ahead, and what the row runs itself."""
    return [c for c in setup if classify(c) == "world"], [c for c in setup if classify(c) != "world"]


_TRIPLE = __import__("re").compile(r"(?<![\w.~^-])(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)(?![\w.])")


def shift(cmd, offset=SITE_B, origin=ORIGIN, box=BOX, margin=8):
    """Pure: the command with every coordinate triple inside site A's box (± margin) moved by `offset` — the same
    build at site B. Numbers keep their form (10000 stays an integer, 10000.5 a decimal)."""
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


def _c(p):
    return f"{p[0]} {p[1]} {p[2]}"


BRAIN = None     # set by `mc.py scenario`: plan-driven scenarios execute steps exactly as the brain does


def _achieve(ctx, needs, done, rounds=12):
    """Plan the needs from the current bag and execute the first step until `done()` — the brain's own path."""
    from .. import decompose, dispatch, goals
    from ..cost import Cost
    from ..world import Snapshot
    from .. import jobs as _jobs
    for _ in range(rounds * 4):
        if done():
            return True
        snap = Snapshot()
        pending = BRAIN.mem.jobs(snap.dimension)
        from .. import skills as _skills
        ready = [j for j in pending if _skills.job_ready(j, snap.state.get("gameTime"))]
        if ready:
            _jobs.collect(ctx, ready[0])        # a background furnace finished: take its output (the brain's job)
            continue
        plan = decompose.decompose(snap.inv, goals.have(*needs), Cost(snap, BRAIN.mem),
                                   pending=BRAIN.mem.pending_outputs(snap.dimension))
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


def _inv_has(item, n):
    from ..world import Inventory
    return lambda: Inventory().count(item) >= n


# The engine under every sweep bench (`decision_arena`, `combat_arena`, `escape`, `siege`): a bench is the cells
# it visits, the commands that build one, what a row records, and rules over the finished table. Resetting the
# platform, appending JSONL, re-reading it and printing what broke were written out three times; they live here.

SWEEP = {}


def _platform(reach=9, walled=False):
    """Bare stone, nothing alive, us in the middle: where every cell starts.

    `reach` is how far from the middle the ground must hold. A fighting bench needs more of it than a deciding one:
    the answers the threat model picks walk up to sixteen blocks away, and on a platform nine blocks wide the first
    cell of the first online run walked off the edge and fell two hundred blocks — every later cell then ran with a
    dead player and the whole pass measured nothing. `walled` puts a lip round it so a wrong answer is a wrong
    answer rather than a fall.
    """
    lo, hi = at(-reach, -2, -reach), at(reach + 3, 6, reach)
    floor_hi = at(reach + 3, -1, reach)
    out = [f"fill {_c(lo)} {_c(hi)} air", f"fill {_c(lo)} {_c(floor_hi)} stone",
           f"tp @p {_c(at(0, 0, 0))}", "kill @e[type=!player,distance=..40]"]
    if walled:
        for a, b in (((-reach, 0, -reach), (reach + 3, 2, -reach)), ((-reach, 0, reach), (reach + 3, 2, reach)),
                     ((-reach, 0, -reach), (-reach, 2, reach)), ((reach + 3, 0, -reach), (reach + 3, 2, reach))):
            out.append(f"fill {_c(at(*a))} {_c(at(*b))} stone")
    return out


def _sweep_rows(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _sweep(name, cells, build, record, path, settle=0.5):
    """One pass: build each cell, record one row in it, append it. Returns the rows of THIS pass."""
    def run(_ctx):
        feedback, rows = [], []
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as out:
            for cell in cells():
                for cmd in build(cell):
                    _command(cmd, feedback)
                time.sleep(settle)
                row = {"t": time.time(), **cell, **record(cell)}
                rows.append(row)
                out.write(json.dumps(row) + "\n")
                out.flush()
        SWEEP[name] = rows
        return rows
    return run


def _sweep_check(name, path, rules, least):
    """The verdict is what the RULES say about the finished table — never which name won a cell."""
    def check(_api, _inv):
        rows = SWEEP.get(name) or _sweep_rows(path)[-least:]
        if len(rows) < least:
            return False
        bad = [m for rule in rules for m in (rule(rows) or [])]
        if bad:
            print(f"{name}: " + "; ".join(bad[:6]))
        return not bad
    return check


def _by(rows, *keys):
    """Rows by a tuple of dimension values. New sheets key by `cells.key_of` instead — one vocabulary."""
    return {tuple(r.get(k) for k in keys): r for r in rows}


# Chat is one channel with no ids: a reply belongs to whoever sent and read inside the same window. Every sender
# (the row, the background build at site B) holds this for its send-and-read, so no reply lands in another's read.
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


def _near(api, pos, r):
    import math
    s = api.get("/state")
    return math.dist((s["x"], s["y"], s["z"]), pos) <= r


def _chat_log():
    from .. import api
    return os.path.join(api.INSTANCE, "logs", "latest.log")


def _command(cmd, feedback, timeout=2.0):
    """Send one command and wait for its chat feedback in the client log; returns the new chat lines."""
    from .. import api
    with CHAT_LOCK:
        path = _chat_log()
        size = os.path.getsize(path)
        api.post("/chat", {"message": "/" + cmd})
        t0, lines = time.time(), []
        while time.time() - t0 < timeout:
            time.sleep(0.15)
            with open(path, "rb") as f:
                f.seek(size)
                new = f.read().decode(errors="replace")
            lines = [l.split("[CHAT] ", 1)[1] for l in new.splitlines() if "[CHAT] " in l]
            if lines:
                break
    feedback.append({"cmd": cmd, "reply": lines})
    return lines


def _batch(cmds, feedback, settle=0.6):
    """Send commands back to back, then read all their chat replies at once; any error line fails the setup."""
    from .. import api
    with CHAT_LOCK:
        path = _chat_log()
        size = os.path.getsize(path)
        for cmd in cmds:
            api.post("/chat", {"message": "/" + cmd})
        time.sleep(settle)
        with open(path, "rb") as f:
            f.seek(size)
            lines = [l.split("[CHAT] ", 1)[1] for l in f.read().decode(errors="replace").splitlines()
                     if "[CHAT] " in l]
    feedback.append({"cmd": f"batch of {len(cmds)}", "cmds": cmds, "reply": lines})
    from .runner import feedback_errors      # runner imports core: ask for it when needed, not at import time
    bad = feedback_errors(lines)
    if bad:
        raise SetupInvalid(f"setup batch → {bad[0]}")


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


def entity_mismatches(ents, expect):
    """Pure: expected entity counts [(type, min)] that don't hold (with what was seen, to tell a sync delay from a
    mob that died or wandered off)."""
    seen = sorted({e["type"].split(":")[-1] for e in ents})
    return [f"{t}: {sum(1 for e in ents if e['type'] == t)}, expected ≥ {n} (seen: {', '.join(seen) or 'nothing'})"
            for t, n in expect if sum(1 for e in ents if e["type"] == t) < n]




def reset_brain(brain, mem):
    """Every row starts from a brain that knows nothing of the rows before it: fresh memory, no bans, no retry
    ledger, no held plans, and a fresh upkeep table (its `working`/`broken` tool notes outlived `clear @p`: the
    next row's first round reported "the axe broke" for an axe the previous row's setup had cleared)."""
    from .. import arbiter, fight_loop, nav, needs, reflexes, retry, skill as skillkit
    # A fight the last row left engaged still holds the body: every later row failed "body owned by the arbiter".
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
    brain.held = {}
    brain.needs, brain.reflexes = needs.Needs(brain), reflexes.Maintain(brain)
    brain.place = brain.idle_since = brain.committed = brain.last_failure = None
    fight_loop.wire(brain.mem, lambda snap: brain.policy(snap, snap.night), brain.blacklist,
                    prices=brain.price_table)


def set_brain(brain):
    """`mc.py scenario` hands the bench the brain: plan-driven scenarios execute steps exactly as it does."""
    global BRAIN
    BRAIN = brain
