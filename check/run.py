"""python3 -m check.run [limit] [out.md] [workers] [--all]: judge the fuzzer's kept states (check/corpus; --all: and the
explorer's enumeration, minutes), every edge, gate the coverage, write the violations."""
import sys
import time
from collections import defaultdict

from . import explore, fuzz, oracle
from .coverage import Gate
from .facts import DOMAINS  # noqa: F401


WORKERS = 6         # the machine is shared with other agents' test runs: at most this many processes
SLOW_S = 10.0       # a state judged slower than this is named in the report
CASES = 10          # the budget-cut rounds that lost most, named in the report


def _shard(keys):
    """A worker: judge the states of `keys` with the coverage gate on, the round's prints dropped; (results, arms hit)."""
    import contextlib
    import io
    from bonobo import api
    with contextlib.redirect_stdout(io.StringIO()), Gate() as gate:
        api.detail = lambda *a: None
        out = [explore.judged(explore.of(**dict(zip(explore.DOMAINS, k)))) for k in keys]
    return out, gate.hits()


def _spread(xs):
    """One line: how many, how many lost anything, the loss at its median, p75, p90 and worst."""
    if not xs:
        return "- none"
    xs = sorted(xs)
    at = lambda q: xs[min(len(xs) - 1, int(q * len(xs)))]      # noqa: E731
    return (f"- {len(xs)} rounds; lost {sum(x > 0 for x in xs)}; median {at(0.5):.1f} s, p75 {at(0.75):.1f} s, "
            f"p90 {at(0.9):.1f} s, worst {xs[-1]:.1f} s")


def main(argv):
    import multiprocessing
    every = "--all" in argv
    argv = [a for a in argv if a != "--all"]
    limit = int(argv[0]) if argv else 0
    out = argv[1] if len(argv) > 1 else "check-run.md"
    workers = int(argv[2]) if len(argv) > 2 else min(WORKERS, multiprocessing.cpu_count())
    t0 = time.time()
    # the gate judges the fuzzer's kept states (check/corpus); --all adds the explorer's enumeration (offline: minutes)
    todo = [explore.key(f) for f in explore.states()] if every else []
    todo += sorted({explore.key(f) for f in fuzz.corpus()} - set(todo))
    todo = todo[:limit] if limit else todo
    shards = [todo[i::workers] for i in range(workers)]
    rows, graph, roundtrip, first, slow, lost, cases = defaultdict(list), {}, [], {}, [], [], []
    gate = Gate()
    # spawn: each worker imports check afresh, so its memory, tasks and tape live in a data dir of its own
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for results, hits in pool.imap_unordered(_shard, shards):
            gate.merge(hits)
            for k, after, d, progress, found, mismatch, got, loss, secs in results:
                if loss is not None:
                    lost.append(loss[0])
                    cases.append((loss[0], dict(zip(explore.DOMAINS, k)), loss[1], loss[2]))
                if secs > SLOW_S:
                    slow.append((secs, dict(zip(explore.DOMAINS, k))))
                if after is not None:          # a crashed state has no successor
                    first.setdefault("step", (explore.of(**dict(zip(explore.DOMAINS, k))), d))
                    graph[k] = (after, d, progress)
                if mismatch:
                    roundtrip.append((dict(zip(explore.DOMAINS, k)), got))
                for inv, msg in found:
                    rows[inv].append((dict(zip(explore.DOMAINS, k)), d, msg))
    seen = graph
    loops = explore.cycles(graph)
    hit, total, unhit = gate.report()
    secs = time.time() - t0
    unchecked = oracle.unchecked(*first["step"]) if "step" in first else {}
    lines = [f"# check run: {len(seen)} abstract states, {secs:.0f} s, coverage {hit}/{total} branch arms "
             f"({100 * hit / max(1, total):.1f} %)", "",
             "## violations by invariant", "| invariant | count |", "|---|---|"]
    lines += [f"| {k} | {len(v)} |" for k, v in sorted(rows.items())] + [f"| D7 | {len(loops)} |"]
    lines += ["", "## violations (invariant | abstract state | production chose | violates)",
              "| inv | state | chose | violates |", "|---|---|---|---|"]
    for k, v in sorted(rows.items()):
        for before, dd, msg in v:
            state = ", ".join(f"{n}={x}" for n, x in before.items() if n != "dimension" or x != "minecraft:overworld")
            lines.append(f"| {k} | {state} | {dd.layer}: {dd.name} | {msg} |")
    for k, name, n in loops:
        lines.append(f"| D7 | {k} | {name} | a {n}-step cycle whose decisions leave nothing in the world |")
    lines += ["", "## unchecked (no offline fact; named)", "| inv | why |", "|---|---|"]
    lines += [f"| {k} | {why} |" for k, why in unchecked.items()]
    lines += ["", f"## states judged slower than {SLOW_S:.0f} s: {len(slow)}"]
    lines += [f"- {s:.1f} s: {f}" for s, f in sorted(slow, key=lambda p: -p[0])]
    lines += ["", "## P5 on budget-cut rounds (no violation): seconds the chosen plan loses to the unpruned reference"]
    lines += [_spread(lost), "", f"### the {CASES} largest losses (seconds lost, the plan chosen, the reference's, facts)"]
    lines += [f"- {s:.1f} s: chose {chosen} | reference {ref} | {f}" for s, f, chosen, ref in
              sorted(cases, key=lambda c: -c[0])[:CASES] if s > 0]
    lines += ["", f"## γ round-trip mismatches: {len(roundtrip)}"]
    lines += [f"- asked {f} → alpha {g}" for f, g in roundtrip[:20]]
    lines += ["", "## excluded from the denominator (execution, by structure: check/coverage.py)", "| why | functions |",
              "|---|---|"]
    lines += [f"| {why} | {len(names)}: {', '.join(names)} |" for why, names in sorted(gate.exclusions().items())]
    lines += ["", "## coverage by module (entered = a function the round called)",
              "| module | functions entered | arms hit in entered functions | unhit arms in entered functions (line qualname) |",
              "|---|---|---|---|"]
    for m, (f, n, h, t, u) in sorted(gate.clusters().items()):
        lines.append(f"| {m} | {f}/{n} | {h}/{t} | {'; '.join(u)} |")
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"{len(seen)} states, {secs:.0f}s, coverage {hit}/{total}, violations "
          + ", ".join(f"{k}:{len(v)}" for k, v in sorted(rows.items())) + f", D7:{len(loops)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
