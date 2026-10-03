#!/usr/bin/env python3
"""mc — command line for the Minecraft brain (package `bonobo`). Long runs: `supervise.sh`."""
import argparse
import json
import sys

from bonobo import api, skillcore, skills
from bonobo.api import McError, log
from bonobo.brain import Brain, autoplay
from bonobo.data import bare
from bonobo.memory import Memory, boxes_of
from bonobo.planner import Unplannable
from bonobo.world import Inventory, Region, Snapshot, find


def cmd_state(_):
    s = api.get("/state")
    keep = ["x", "y", "z", "health", "food", "timeOfDay", "blockLight", "skyLight", "mainHand", "screen", "control"]
    print(json.dumps({k: s.get(k) for k in keep}, indent=1))


def cmd_inv(_):
    inv = Inventory()
    print(", ".join(f"{bare(s['id'])}×{s['count']}" + (f"({s.get('maxDamage', 0) - s.get('damage', 0)})" if "damage" in s else "")
                    for s in inv.slots))
    print("worn:", {k: bare(v["id"]) for k, v in inv.equipment.items() if v["count"]})


def cmd_plan(_):
    """Every live task, whether it is done, and the plan the brain would follow for it from this bag."""
    from bonobo import decompose, goals, tasks
    from bonobo.cost import Cost
    snap = Snapshot.from_readings(api.get("/state"), Inventory())
    mem = Memory()
    for t in tasks.load():
        if t["state"] not in tasks.LIVE:
            continue
        goal = tasks.goal_of(t)
        if goals.done(goal, snap, mem):
            print(f"✔ {tasks.describe_task(t)}")
            continue
        try:
            plan = decompose.decompose(snap.inv, goal, Cost(snap, mem))
            print(f"· {tasks.describe_task(t)}: " + (" → ".join(str(s) for s in plan) or "finish"))
        except Unplannable as e:
            print(f"✗ {tasks.describe_task(t)}: {e}")


def cmd_step(_):
    api.take_control()
    Brain().round()


def cmd_autoplay(a):
    autoplay(a.hours)


def cmd_home(a):
    """The player's home: add NAME --box X1 Y1 Z1 X2 Y2 Z2 [--box …] (each box read, every block in them protected,
    their beds, chests and stations found) | list | remove NAME."""
    mem = Memory()
    if a.action == "list":
        for h in mem.sites(kinds=["home"]):
            snap, parts = h.get("snapshot") or {}, h.get("parts") or {}
            print(f"{h['name']:<12} {h['dimension']} boxes {boxes_of(h)} "
                  f"{len(snap.get('blocks', {}))} blocks, beds {len(parts.get('beds', []))}, "
                  f"chests {len(parts.get('chests', []))}, stations {[b for b, _p in parts.get('stations', [])]}")
        return
    if a.action == "remove":
        print("removed" if mem.remove_home(a.name) else f"no home named {a.name}")
        return
    if a.name is None or not a.box:
        raise McError("home add NAME --box X1 Y1 Z1 X2 Y2 Z2 [--box …]")
    boxes = [(b[:3], b[3:]) for b in a.box]
    dim = a.dim or api.get("/state")["dimension"]
    blocks = {}
    for lo, hi in boxes:
        blocks.update(Region([min(x, y) for x, y in zip(lo, hi)], [max(x, y) for x, y in zip(lo, hi)]).blocks)
    site = mem.add_home(a.name, boxes, dim, blocks)
    parts = site["parts"]
    log(f"home {a.name}: {len(site['snapshot']['blocks'])} blocks protected, beds {len(parts['beds'])}, "
        f"chests {len(parts['chests'])}, stations {[b for b, _p in parts['stations']]}")


def cmd_skills(_):
    """Every skill with its contract (budget, stall limit, purpose)."""
    from bonobo.skill import REGISTRY
    for c in REGISTRY.values():
        print(c.describe())


def cmd_task(a):
    """The task queue (tasks.json): what the cerebrum wants done, in order. The only door in."""
    from bonobo import goals, tasks
    if a.action == "list":
        for t in tasks.load():
            if a.all or t["state"] in tasks.LIVE:
                print(tasks.describe_task(t))
        return
    if a.action == "cancel":
        tasks.cancel(a.args[0] if a.args else None)
        print("cancelled", a.args[0] if a.args else "every live task")
        return
    if a.action == "clear":
        tasks.drop_done()
        print("finished tasks cleared")
        return
    if a.action == "milestones":
        for name, needs in goals.MILESTONES.items():
            print(f"{name:16} {needs}")
        return
    template, rest = a.args[0], a.args[1:]
    if template in ("have", "craft"):
        needs = [goals.parse_need(rest[i], rest[i + 1] if i + 1 < len(rest) and not rest[i].startswith("tool:")
                                  else 1)
                 for i in _need_starts(rest)]
        goal = goals.make(template, needs=needs)
    elif template == "milestone":
        goal = goals.make("milestone", name=" ".join(rest))
    elif template == "goto":
        goal = goals.make("goto", pos=[int(v) for v in rest[:3]], range=float(rest[3]) if len(rest) > 3 else 2)
    elif template == "road":
        goal = goals.make("road", a=[int(v) for v in rest[:3]], b=[int(v) for v in rest[3:6]])
    elif template == "build":
        goal = goals.make("build", bp=rest[0], at=[int(v) for v in rest[1:4]] if len(rest) >= 4 else None)
    elif template == "sleep":
        goal = goals.make("sleep")
    elif template == "skill":
        goal = goals.make("skill", name=rest[0],
                          args=[int(v) if v.lstrip("-").isdigit() else v for v in rest[1:]])
    elif template == "effect":
        goal = goals.make("effect", effect=rest[0], count=int(rest[1]) if len(rest) > 1 else 1)
    else:
        raise McError(f"unknown goal {template}: one of {', '.join(goals.TEMPLATES)}")
    t = tasks.add(goal, expires_s=a.expires_s, front=a.front)
    print(tasks.describe_task(t))


def _need_starts(rest):
    """Indexes where each need begins in `TOKEN N TOKEN N tool:KIND:TIER ...`."""
    out, i = [], 0
    while i < len(rest):
        out.append(i)
        i += 1 if rest[i].startswith("tool:") else 2
    return out


def cmd_scenario(a):
    """Scenario bench (test world only): enable | disable | list | run NAME... | all | table."""
    import os
    from bonobo.bench import table as sheet
    from bonobo.bench import core, runner
    from bonobo.brain import Brain
    from bonobo.world import Snapshot
    if a.action == "enable":
        open(core.FLAG, "w").write("test world confirmed by the user\n")
        print("scenario commands enabled for this world — never enable in the real world")
        return
    if a.action == "disable":
        if os.path.exists(core.FLAG):
            os.remove(core.FLAG)
        print("scenario commands disabled")
        return
    point = getattr(a, "point", None)
    selected = set(_scenario_selection(a, sheet))
    if a.action == "list":
        for name, sc in sheet.SCENARIOS.items():
            if (point and sc.get("point", "A") != point) or name not in selected:
                continue
            print(f"{name:20} budget {sc['budget']:>3}s  {sc['doc']}")
        return
    if a.action == "migrate":
        _scenario_migrate(sheet)
        return
    if a.action == "table":
        table = runner.load_table()
        for name in sheet.SCENARIOS:
            code = runner.code_for(name)
            st, med = runner.status(table, name, code)
            print(f"{name:20} {code} {st:9} E4 {runner.e4_status(table, name, code):4} "
                  f"{'' if med is None else f'median {med}s'}")
        return
    from bonobo import perception
    # `all` skips release-only scenarios (the dragon, the portal room, long real-world searches): run them by name.
    # `all` skips release-only rows (minutes each) unless a tier was named: a tier's rows are the tier, all of them.
    tiered = getattr(a, "tier", "core") not in (None, "all")
    names = [n for n, sc in sheet.SCENARIOS.items() if (tiered or not sc.get("release")) and n in selected] \
        if a.action == "all" else a.names
    if point:
        names = [n for n in names if sheet.SCENARIOS[n].get("point", "A") == point]
    api.take_control()       # the bench's start takes the body (the pause menu closed, the player's toggle lifted)
    brain = Brain()
    core.set_brain(brain)   # plan-driven scenarios execute steps the way the brain does
    perception.start_watching()   # same danger interrupts as a real run
    same, running, built = runner.jar_matches_source()
    if not same:
        raise McError(f"game runs mod {running} but the sources are {built}: install the jar and restart first")
    # A scenario that SWEEPS (the arena) is its own sample: one pass writes dozens of rows, and running it three
    # times only re-measures the same code against the same cells. Yes/no scenarios still repeat until two
    # counted runs agree, because one of those is a coin toss about flaky execution, not a measurement.
    # Each row runs once; a failure is re-run, three runs at most, ≥ 2 of 3 passes (runner.verdict_of). A row the
    # current code already has a verdict for is not run again (unless --force, once).
    if getattr(a, "idle", False):
        _scenario_idle(sheet, names, brain)
        return
    runs = [(name, attempt) for name in names
            for attempt in range(1 if sheet.SCENARIOS[name].get("sweep") else runner.MAX_RUNS)]
    for i, (name, attempt) in enumerate(runs):
        # The row after this one, so its world is built at site B while this one runs (runner.prebuild).
        runner.NEXT_ROW[0] = next((n for n, _a in runs[i + 1:] if n != name), None)
        table = runner.load_table()
        code = runner.code_for(name)
        cached = runner.cached_timeout(table, name, code)
        if cached and not (a.force and attempt == 0):
            if attempt == 0:
                print(f"FAIL {name} 0s {cached}")      # stopped at its limit last time, nothing changed since
            continue
        decided = runner.verdict(table, name, code)
        if decided and not (a.force and attempt == 0):
            continue
        # Fresh memory per scenario: the real world's remembered pools/builds must not steer the test, and the
        # test must not write into the real world's notes.
        core.fresh_row(brain)
        def make_ctx():
            snap = Snapshot.from_readings(api.get("/state"), Inventory())
            # Prices too: a skill that asks what a thing is worth (the looter) gets the same table the round uses.
            # Without it the bench reproduced the live bug — "looted 0 stacks" — for the wrong reason.
            return skillcore.Context(brain.mem, brain.policy(snap, snap.night), snap.dimension, brain.blacklist,
                                  prices=brain.price_table)
        ok, seconds, note, cls, code = runner.run_named(name, make_ctx)
        print(f"{'PASS' if ok else 'FAIL'} {name} {seconds:.0f}s {note}")


def _idle_ctx(brain):

    def make_ctx():
        snap = Snapshot.from_readings(api.get("/state"), Inventory())
        return skillcore.Context(brain.mem, brain.policy(snap, snap.night), snap.dimension, brain.blacklist,
                                 prices=brain.price_table)
    return make_ctx


def _scenario_idle(sheet, names, brain):
    """Idle mode: each row set up as normal, then a body that does nothing for its budget, then its check — once,
    no retries, no cache. INVALID: an idle body passes it (it tests nothing). Written to runner.IDLE_TABLE only."""
    from bonobo.bench import core, runner
    out = {}
    for i, name in enumerate(names):
        runner.NEXT_ROW[0] = names[i + 1] if i + 1 < len(names) else None
        core.fresh_row(brain)
        verdict, note, _code = runner.run_idle(name, _idle_ctx(brain))
        out.setdefault(verdict, []).append(name)
        print(f"IDLE {verdict} {name} {note}", flush=True)
    print("idle: " + ", ".join(f"{v} {len(n)}" for v, n in sorted(out.items())))
    if out.get(runner.INVALID):
        print("INVALID (an idle body passes them): " + " ".join(out[runner.INVALID]))
    if out.get(runner.DIED_ONLY):
        print("DIED_ONLY (the idle check passed; only the death failed them): " + " ".join(out[runner.DIED_ONLY]))


MIGRATE_KEYS = r"""
import importlib.util, json, os, sys
wt, rowkey = sys.argv[1], sys.argv[2]
sys.path.insert(0, wt)
spec = importlib.util.spec_from_file_location("rowkey_now", rowkey)
rk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rk)
from bonobo import brain  # noqa: F401  (every skill registers)
from bonobo.bench import table as sc
from bonobo import skill
pkg = os.path.join(wt, "bonobo")
index = rk.code_index(pkg)
print(json.dumps({n: rk.reach_hash(r, index, skill.REGISTRY, pkg) + rk.row_hash(r) for n, r in sc.SCENARIOS.items()}))
"""


def _scenario_migrate(sheet):
    """Carry the readiness table's old verdicts over to the current key format (runner.migrate): each old record's
    code — the commit that was HEAD when it ran, checked out in a temporary worktree — keyed the new way. Reusable
    whenever the key format changes."""
    import json
    import os
    import subprocess
    import tempfile
    from bonobo.bench import runner, vocab
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    here = os.path.dirname(os.path.abspath(__file__))
    rowkey = os.path.join(here, "bonobo", "bench", "rowkey.py")
    rel = os.path.relpath(here, root)
    current = {n: runner.code_for(n) for n in sheet.SCENARIOS}
    if any(k.endswith("jar-unknown") for k in current.values()):
        # The game is down: the mod part is the version the sources build (the bench demands jar == source).
        props = os.path.expanduser("~/minecraft-claude-bridge/anaka/gradle.properties")
        version = next((l.split("=", 1)[1].strip() for l in open(props) if l.startswith("mod_version=")), "unknown")
        current = {n: k.replace("jar-unknown", f"jar-{version}") for n, k in current.items()}
    keys_at, commit_of = {}, {}

    def key_then(name, t):
        stamp = int(t)
        if stamp not in commit_of:
            commit_of[stamp] = subprocess.run(["git", "rev-list", "-1", f"--before={stamp}", "HEAD"],
                                              capture_output=True, text=True, cwd=root).stdout.strip()
        commit = commit_of[stamp]
        if not commit:
            return None
        if commit not in keys_at:
            wt = tempfile.mkdtemp(prefix="migrate-")
            try:
                subprocess.run(["git", "worktree", "add", "--detach", wt, commit], cwd=root, capture_output=True,
                               check=True)
                out = subprocess.run([sys.executable, "-c", MIGRATE_KEYS, os.path.join(wt, rel), rowkey],
                                     capture_output=True, text=True, timeout=300)
                keys_at[commit] = json.loads(out.stdout.strip().splitlines()[-1]) if out.returncode == 0 else {}
                if out.returncode != 0:
                    print(f"  {commit[:8]}: its code could not be keyed ({out.stderr.strip().splitlines()[-1:]})")
            except (subprocess.SubprocessError, ValueError, IndexError) as e:
                keys_at[commit] = {}
                print(f"  {commit[:8]}: {e}")
            finally:
                subprocess.run(["git", "worktree", "remove", "--force", wt], cwd=root, capture_output=True)
        return keys_at[commit].get(name)

    table = runner.load_table()
    moved = runner.migrate(table, current, key_then)
    runner.save_table(table)
    pend = set(runner.pending(table, current))
    by = {}
    for n, sc in sheet.SCENARIOS.items():
        tier = sc.get("tier") or vocab.tier_of(n, sc)
        total, left = by.get(tier, (0, 0))
        by[tier] = (total + 1, left + (n in pend))
    print(f"migrated {len(moved)} rows over {len(keys_at)} commits; pending: "
          + ", ".join(f"{t} {left}/{total}" for t, (total, left) in sorted(by.items())))


def _scenario_selection(a, sheet):
    """The rows `--tier` / `--changed` name (sheet.select): the diff against the merge-base with main, mapped to
    the skills whose functions it touched."""
    import subprocess
    from bonobo.bench import runner, vocab
    from bonobo.bench.words import runs as words_runs
    # Acceptance is its own run; a tier narrows --failed and --pending alike when one is named.
    in_tier = set(words_runs.tier_rows(sheet.SCENARIOS, a.tier, "--tier" in sys.argv))
    if getattr(a, "failed", False):
        return [n for n in runner.failed_last(runner.load_table()) if n in in_tier]
    if getattr(a, "pending", False):
        return runner.pending(runner.load_table(), {n: runner.code_for(n) for n in sheet.SCENARIOS
                                                           if n in in_tier})
    changed = None
    if getattr(a, "changed", False):
        from bonobo import brain  # noqa: F401  (every skill module registers)
        from bonobo.skill import REGISTRY
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        base = subprocess.run(["git", "merge-base", "HEAD", "main"], capture_output=True, text=True).stdout.strip()
        diff = subprocess.run(["git", "diff", "-U0", base or "HEAD"], capture_output=True, text=True, cwd=root).stdout
        changed = vocab.touched_skills(vocab.diff_hunks(diff), vocab.skill_spans(REGISTRY, root))
        print(f"changed skills: {', '.join(sorted(changed)) or 'none (core rows)'}")
        from bonobo.skill import REGISTRY as registry
    else:
        registry = None
    return vocab.select(sheet.SCENARIOS, getattr(a, "tier", "core") or "core", changed, registry)


def cmd_interrupt(a):
    """End the running skill now (via the perception thread) so the queue's head runs next round."""
    from bonobo import perception
    with open(perception.FLAG, "w") as f:
        f.write(a.why)
    print("interrupt requested:", a.why)


def cmd_review(a):
    """The review packet for Claude: last N minutes of decisions, failures, progress, skill stats, directives."""
    from bonobo import review
    try:
        state, inv = api.get("/state"), Inventory()
    except McError:
        state, inv = None, None
    try:
        from bonobo.bench import table as sheet
        from bonobo.bench.words import runs as words_runs
        readiness = words_runs.readiness_lines()
    except Exception as e:      # the review must never fail because of the bench table
        readiness = [f"  unavailable: {e}"]
    print(review.packet(a.minutes, state, inv, Memory(), readiness=readiness))


def cmd_notes(_):
    print(json.dumps(Memory().data, indent=1)[:20000])


def cmd_find(a):
    for b in find(a.blocks.split(","), a.radius, a.limit):
        print(bare(b["block"]), b["x"], b["y"], b["z"], f"{b['distance']}m")


def cmd_collect(a):
    from bonobo.tools import collect
    sys.argv = ["collect", str(a.cycles), str(a.ticks)] + (["--bait"] if a.bait else [])
    collect.main()


def cmd_report(a):
    from bonobo.tools import report
    sys.argv = ["report"] + ([a.prefix] if a.prefix else [])
    report.main()


def cmd_once(a):
    from bonobo.tools import once
    sys.argv = ["once", a.scenario] + ([a.log] if a.log else [])
    once.main()


def cmd_intent(a):
    import json
    from bonobo import intent
    try:
        with open(intent.FILE) as f:
            lines = json.load(f).get("lines", [])
    except (OSError, ValueError):
        lines = []
    print("\n".join(lines) if lines else "(no intention published yet)")


def cmd_incidents(a):
    from bonobo.tools import incidents
    sys.argv = ["incidents"] + list(a.rest)
    raise SystemExit(incidents.main())


def cmd_rounds(a):
    from bonobo.tools import rounds
    raise SystemExit(rounds.main([a.since] if a.since else []))


def cmd_mech(a):
    """Taught mechanisms (press this → these cells open): add, list, remove."""
    from bonobo import mechanisms as mech
    dim = api.get("/state")["dimension"] if a.action != "list" else None
    press = tuple(a.press) if a.press else None
    if a.action == "list":
        for m in mech.read_lessons():
            print(f"{m['dimension']} press {tuple(m['press'])} opens {[tuple(c) for c in m['opens']]}"
                  + (" (closed behind)" if m.get("close") else ""))
        return
    if press is None:
        raise SystemExit("--press X Y Z is required")
    if a.action == "remove":
        print(f"removed {mech.remove(dim, press)}")
        return
    opens = [tuple(a.opens[i:i + 3]) for i in range(0, len(a.opens or ()), 3)]
    if not opens or len(a.opens) % 3:
        raise SystemExit("--opens takes X Y Z triples")
    print("taught:", mech.learn(dim, press, opens, close=a.close))

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("state").set_defaults(fn=cmd_state)
    sub.add_parser("inv").set_defaults(fn=cmd_inv)
    sub.add_parser("plan").set_defaults(fn=cmd_plan)
    sub.add_parser("step", help="run one brain round").set_defaults(fn=cmd_step)
    p = sub.add_parser("autoplay")
    p.add_argument("--hours", type=float, default=10)
    p.set_defaults(fn=cmd_autoplay)
    p = sub.add_parser("home", help="the player's home: add NAME --box X1 Y1 Z1 X2 Y2 Z2 [--box …] | list | remove NAME")
    p.add_argument("action", choices=["add", "list", "remove"])
    p.add_argument("name", nargs="?")
    p.add_argument("--box", type=int, nargs=6, action="append")
    p.add_argument("--dim", help="dimension (default: where the body is)")
    p.set_defaults(fn=cmd_home)
    sub.add_parser("notes").set_defaults(fn=cmd_notes)
    p = sub.add_parser("mech", help="taught mechanisms: add|list|remove --press X Y Z [--opens X Y Z ...]")
    p.add_argument("action", choices=["add", "list", "remove"])
    p.add_argument("--press", type=int, nargs=3)
    p.add_argument("--opens", type=int, nargs="+")
    p.add_argument("--close", action="store_true", help="shut it behind after crossing (from the far side's press)")
    p.set_defaults(fn=cmd_mech)
    sub.add_parser("skills", help="list skill contracts").set_defaults(fn=cmd_skills)
    p = sub.add_parser("task", help="task queue: add have|craft|milestone|goto|road|build|sleep|skill ... | list | "
                                     "cancel [id] | clear | milestones")
    p.add_argument("action", choices=["add", "list", "cancel", "clear", "milestones"])
    p.add_argument("args", nargs="*")
    p.add_argument("--front", action="store_true", help="put it at the head of the queue")
    p.add_argument("--expires-s", dest="expires_s", type=float, default=None)
    p.add_argument("--all", action="store_true", help="list: include finished tasks")
    p.set_defaults(fn=cmd_task)
    p = sub.add_parser("scenario", help="scenario bench (test world only): enable|disable|list|table|run NAME|all")
    p.add_argument("action", choices=["enable", "disable", "list", "table", "run", "all", "migrate"])
    p.add_argument("names", nargs="*")
    p.add_argument("--force", action="store_true", help="run once even when the current code already has a verdict")
    p.add_argument("--idle", action="store_true",
                   help="run/all: set each row up at 1 hp, do nothing until its check passes, it dies or its budget "
                        "— a row that passes is INVALID, one that passed but died DIED_ONLY (recorded apart from the "
                        "readiness table)")
    p.add_argument("--point", choices=["A", "B", "C", "D"], help="only the scenarios of this test point")
    p.add_argument("--tier", choices=["core", "common", "brain", "combat", "exception", "acceptance", "all"], default="core",
                   help="which tier to run with `all` / list (default core)")
    p.add_argument("--changed", action="store_true",
                   help="only rows proving skills changed since the merge-base with main (else core)")
    p.add_argument("--failed", action="store_true",
                   help="only rows whose latest run failed (FAIL or TIMEOUT in the readiness table)")
    p.add_argument("--pending", action="store_true",
                   help="rows with no result under their current key (new or changed) or whose last run failed")
    p.set_defaults(fn=cmd_scenario)
    p = sub.add_parser("interrupt", help="end the running skill so the queue's head runs next")
    p.add_argument("--why", default="Claude redirected")
    p.set_defaults(fn=cmd_interrupt)
    p = sub.add_parser("review", help="review packet for the last N minutes")
    p.add_argument("--minutes", type=int, default=5)
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("rounds", help="round phase times from detail.log: median/max per phase, the 3 longest gaps")
    p.add_argument("since", nargs="?", help="HH:MM:SS: only rounds from then on")
    p.set_defaults(fn=cmd_rounds)
    p = sub.add_parser("find")
    p.add_argument("blocks")
    p.add_argument("--radius", type=int, default=32)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_find)
    sub.add_parser("stop").set_defaults(fn=lambda a: api.post("/stop"))
    sub.add_parser("release").set_defaults(fn=lambda a: api.post("/release"))

    # Operator tools. Not part of playing: these collect data, report on it, and run single checks.
    p = sub.add_parser("collect", help="record dragon-behaviour tapes in bulk (observe or bait mode)")
    p.add_argument("cycles", type=int, nargs="?", default=5)
    p.add_argument("ticks", type=int, nargs="?", default=3600)
    p.add_argument("--bait", action="store_true", help="stand where the dragon will attack, to record its attacks")
    p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("report", help="aggregate the recorded tapes: phase durations, cycle order, damage")
    p.add_argument("prefix", nargs="?", help="only tapes whose name starts with this")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("once", help="run one bench scenario exactly once (no retries)")
    p.add_argument("scenario")
    p.add_argument("log", nargs="?")
    p.set_defaults(fn=cmd_once)
    sub.add_parser("intent", help="what the agent means to do right now, layer by layer (MC_DATA/intent.json)") \
        .set_defaults(fn=cmd_intent)
    p = sub.add_parser("incidents", help="replay captured planner states from live failures; `adopt NAME` to keep one")
    p.add_argument("rest", nargs="*")
    p.set_defaults(fn=cmd_incidents)
    args = ap.parse_args()
    try:
        args.fn(args)
        return 0
    except McError as e:
        log("ERROR:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
