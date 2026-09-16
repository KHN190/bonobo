#!/usr/bin/env python3
"""mc — command line for the Minecraft brain (package `bonobo`). Long runs: `supervise.sh`."""
import argparse
import json
import sys
import time

from bonobo import api, skills
from bonobo.api import McError, log
from bonobo.brain import Brain, LiveCost, autoplay, goals
from bonobo.data import bare
from bonobo.memory import Memory
from bonobo.planner import Planner, Unplannable
from bonobo.world import Inventory, Snapshot, find


def cmd_state(_):
    s = api.get("/state")
    keep = ["x", "y", "z", "health", "food", "timeOfDay", "blockLight", "skyLight", "mainHand", "screen", "control"]
    print(json.dumps({k: s.get(k) for k in keep}, indent=1))


def cmd_inv(_):
    inv = Inventory()
    print(", ".join(f"{bare(s['id'])}×{s['count']}" + (f"({s['maxDamage'] - s['damage']})" if "damage" in s else "")
                    for s in inv.slots))
    print("worn:", {k: bare(v["id"]) for k, v in inv.equipment.items() if v["count"]})


def cmd_plan(_):
    """Shows every goal, whether it's done, and the plan the brain would follow for open ones."""
    snap = Snapshot()
    mem = Memory()
    for g in goals(snap, mem):
        if g.done():
            print(f"✔ [p{g.phase}] {g.name}")
            continue
        try:
            plan = Planner.from_inventory(snap.inv, LiveCost(snap)).plan(g.needs)
            print(f"· [p{g.phase}] {g.name} (value {g.value}): " + (" → ".join(str(s) for s in plan) or "finish"))
        except Unplannable as e:
            print(f"✗ [p{g.phase}] {g.name}: {e}")


def cmd_step(_):
    Brain().round()


def cmd_autoplay(a):
    autoplay(a.hours)


def cmd_home(a):
    """Record the base as the home site (with a structure snapshot for repairs)."""
    mem = Memory()
    pos = tuple(a.pos) if a.pos else skills.find_base()
    if pos is None:
        raise McError("no base found nearby; stand at home or pass --pos x y z")
    snap = skills.snapshot(pos, half=a.half, down=4, up=6)
    site = mem.add_site("home", pos, api.get("/state")["dimension"], snapshot=snap, name="home")
    log(f"home recorded at {site['pos']} with {len(snap['blocks'])} structure blocks")


def cmd_skills(_):
    """Every skill with its contract (budget, stall limit, purpose)."""
    from bonobo.skill import REGISTRY
    mem = Memory()
    for c in REGISTRY.values():
        print(c.describe(mem))


def cmd_direct(a):
    """Claude's instructions to the script (above normal goals, below survival)."""
    from bonobo import directives
    if a.action == "list":
        for d in directives.load():
            print(d["id"], directives.describe(d))
    elif a.action == "clear":
        directives.save([d for d in directives.load() if d["status"] != "pending"])
        print("pending directives cleared")
    else:
        # Dependency graph + mode: --after ID... waits for those directives; --mode boost/background joins the pool.
        common = {"note": a.note, "requires": a.after or [], "mode": a.mode}
        if a.x is not None:
            common["x"] = a.x
        if a.action == "goal":
            pairs = [[a.args[i], int(a.args[i + 1])] for i in range(0, len(a.args) - 1, 2)]
            d = directives.add("goal", needs=pairs, **common)
        elif a.action == "goto":
            d = directives.add("goto", target=[int(v) for v in a.args[:3]], **common)
        else:
            args = [int(v) if v.lstrip("-").isdigit() else v for v in a.args[1:]]
            d = directives.add("skill", name=a.args[0], args=args, **common)
        print(d["id"], directives.describe(d))


def cmd_prio(a):
    """Claude's priority adjustments (priorities.json): hot-reloaded every round, always expiring."""
    from bonobo import priority
    if a.action == "list":
        for target, w in priority.load().items():
            print(target, {k: v for k, v in w.items() if k != "target"})
        return
    if a.action == "clear":
        with open(priority.FILE, "w") as f:
            json.dump({"weights": []}, f)
        print("priorities cleared")
        return
    if a.action == "profile":
        print(f"{priority.apply_profile(a.target, ttl=a.ttl)} weights from profile {a.target}")
        return
    fields = {"ttl": a.ttl, "why": a.why}
    if a.action == "set":
        fields["x"] = float(a.value)
    else:
        fields[a.action] = True       # ban | pin
    print(priority.add_weight(a.target, **fields))


def cmd_scenario(a):
    """Scenario bench (test world only): enable | disable | list | run NAME... | all | table."""
    import os
    from bonobo import scenarios, skills
    from bonobo.brain import Brain, code_version
    from bonobo.world import Snapshot
    if a.action == "enable":
        open(scenarios.FLAG, "w").write("test world confirmed by the user\n")
        print("scenario commands enabled for this world — never enable in the real world")
        return
    if a.action == "disable":
        if os.path.exists(scenarios.FLAG):
            os.remove(scenarios.FLAG)
        print("scenario commands disabled")
        return
    if a.action == "list":
        for name, sc in scenarios.SCENARIOS.items():
            print(f"{name:20} budget {sc['budget']:>3}s  {sc['doc']}")
        return
    if a.action == "table":
        table = scenarios.load_table()
        for name in scenarios.SCENARIOS:
            code = scenarios.code_for(name)
            st, med = scenarios.status(table, name, code)
            print(f"{name:20} {code} {st:9} {'' if med is None else f'median {med}s'}")
        return
    from bonobo import perception
    from bonobo import skill as skillkit
    # `all` skips release-only scenarios (the dragon, the portal room, long real-world searches): run them by name.
    names = [n for n, sc in scenarios.SCENARIOS.items() if not sc.get("release")] if a.action == "all" else a.names
    brain = Brain()
    scenarios.BRAIN = brain   # plan-driven scenarios execute steps the way the brain does
    perception.start()   # same danger interrupts as a real run
    same, running, built = scenarios.jar_matches_source()
    if not same:
        raise McError(f"game runs mod {running} but the sources are {built}: install the jar and restart first")
    runs = [(name, attempt) for name in names for attempt in range(5)]
    for name, attempt in runs:
        # Run only until the current code has a verdict (2 agreeing counted runs): an already-decided scenario isn't
        # run at all; setup/harness failures retry up to 5 times.
        table = scenarios.load_table()
        decided = scenarios.settled(table, name) or scenarios.verdict(table, name, scenarios.code_for(name))
        if (decided and not (a.force and attempt == 0)) or attempt >= 4:
            continue
        # Fresh memory per scenario: the real world's remembered pools/builds must not steer the test, and the
        # test must not write into the real world's notes.
        if os.path.exists(scenarios.NOTES):
            os.remove(scenarios.NOTES)
        brain.mem = Memory(scenarios.NOTES)
        skillkit.STATS = brain.mem
        from bonobo import nav as _nav
        _nav.ROAD_MEM = brain.mem
        brain.blacklist = {}
        def make_ctx():
            snap = Snapshot()
            return skills.Context(brain.mem, brain.policy(snap, snap.night), snap.dimension, brain.blacklist)
        ok, seconds, note, cls, code = scenarios.run(name, make_ctx)
        print(f"{'PASS' if ok else 'FAIL'} {name} {seconds:.0f}s {note}")


def cmd_decide(a):
    """Decisions without the game: diff recorded rounds against the current code, list golden cases, simulate loops."""
    from bonobo import decide
    if a.action == "golden":
        for name, ok, msg in decide.golden_results():
            print(f"{'ok  ' if ok else 'FAIL'} {name}: {msg}")
        return
    rows = decide.load(last=a.last)
    if a.action == "diff":
        changed = decide.diff(rows)
        for t, old, new, top in changed:
            print(f"{time.strftime('%H:%M:%S', time.localtime(t))}  {old} → {new}  {top or ''}")
        print(f"{len(changed)} of {len(rows)} recorded rounds decide differently now")
    elif a.action == "simulate":
        row = rows[-1]
        fails = set(a.fail or [])
        picks = decide.simulate(row, lambda name, i: McError("scripted failure") if name in fails else True,
                                rounds=a.rounds)
        names = [p for _, p in picks]
        for n in sorted(set(names), key=names.count, reverse=True):
            print(f"{names.count(n):4}× {n} (longest run {decide.longest_run(picks, n)})")


def cmd_route(a):
    """Follow a route (speedrun) or turn routes off; shows the active segment."""
    from bonobo import route
    from bonobo.world import Snapshot
    mem = Memory()
    if a.name == "off":
        route.choose(None)
    elif a.name != "show":
        if a.name not in route.ROUTES:
            raise McError(f"unknown route {a.name}; have {sorted(route.ROUTES)}")
        route.choose(a.name)
    name = route.current()
    if name:
        snap = Snapshot()
        seg = route.active_segment(route.ROUTES[name], snap.inv, mem, snap.dimension)
        print(f"route {name}: active segment {seg['name'] if seg else 'finished'}")
        if seg and seg["name"] == "nether kit":
            print("  kit missing:", route.nether_kit_missing(snap.inv))
    else:
        print("no route")


def cmd_interrupt(a):
    """End the running skill now (via the perception thread) so override directives run next round."""
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
    print(review.packet(a.minutes, state, inv, Memory()))


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


def cmd_dryrun(_):
    from bonobo.tools import dryrun
    raise SystemExit(dryrun.main())


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
    p = sub.add_parser("home", help="record home site + snapshot")
    p.add_argument("--pos", type=int, nargs=3)
    p.add_argument("--half", type=int, default=10)
    p.set_defaults(fn=cmd_home)
    sub.add_parser("notes").set_defaults(fn=cmd_notes)
    sub.add_parser("skills", help="list skill contracts").set_defaults(fn=cmd_skills)
    p = sub.add_parser("direct", help="Claude directives: list | clear | goal TOKEN N ... | goto X Y Z | skill NAME ARGS")
    p.add_argument("action", choices=["list", "clear", "goal", "goto", "skill"])
    p.add_argument("args", nargs="*")
    p.add_argument("--note", default="")
    p.add_argument("--after", nargs="*", help="directive ids this one waits for")
    p.add_argument("--mode", choices=["override", "boost", "background"], default="override")
    p.add_argument("--x", type=float, default=None, help="boost multiplier")
    p.set_defaults(fn=cmd_direct)
    p = sub.add_parser("prio", help="priority weights: list | clear | set TARGET X | ban TARGET | pin TARGET")
    p.add_argument("action", choices=["list", "clear", "set", "ban", "pin", "profile"])
    p.add_argument("target", nargs="?")
    p.add_argument("value", nargs="?")
    p.add_argument("--ttl", type=int, default=1800)
    p.add_argument("--why", default="")
    p.set_defaults(fn=cmd_prio)
    p = sub.add_parser("scenario", help="scenario bench (test world only): enable|disable|list|table|run NAME|all")
    p.add_argument("action", choices=["enable", "disable", "list", "table", "run", "all"])
    p.add_argument("names", nargs="*")
    p.add_argument("--force", action="store_true", help="run once even when the current code already has a verdict")
    p.set_defaults(fn=cmd_scenario)
    p = sub.add_parser("decide", help="decision tests offline: diff | golden | simulate")
    p.add_argument("action", choices=["diff", "golden", "simulate"])
    p.add_argument("--last", type=int, default=200)
    p.add_argument("--rounds", type=int, default=200)
    p.add_argument("--fail", nargs="*", help="candidate names that always fail in the simulation")
    p.set_defaults(fn=cmd_decide)
    p = sub.add_parser("route", help="follow a route: speedrun | off | show")
    p.add_argument("name")
    p.set_defaults(fn=cmd_route)
    p = sub.add_parser("interrupt", help="end the running skill so a directive runs next")
    p.add_argument("--why", default="Claude redirected")
    p.set_defaults(fn=cmd_interrupt)
    p = sub.add_parser("review", help="review packet for the last N minutes")
    p.add_argument("--minutes", type=int, default=5)
    p.set_defaults(fn=cmd_review)
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
    sub.add_parser("dryrun", help="read-only check that the candidate pool builds against the live game") \
        .set_defaults(fn=cmd_dryrun)
    args = ap.parse_args()
    try:
        args.fn(args)
        return 0
    except McError as e:
        log("ERROR:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
