"""python3 -m check.run [limit] [out.md]: explore, judge every edge, gate the coverage, write the violations."""
import sys
import time
from collections import defaultdict

from . import explore, fuzz, oracle
from .coverage import Gate
from .facts import DOMAINS  # noqa: F401


WORKERS = 6         # the machine is shared with other agents' test runs: at most this many processes


def _shard(keys):
    """A worker: judge the states of `keys` with the coverage gate on, the round's prints dropped; (results, arms hit)."""
    import contextlib
    import io
    from bonobo import api
    with contextlib.redirect_stdout(io.StringIO()), Gate() as gate:
        api.detail = lambda *a: None
        out = [explore.judge(explore.of(**dict(zip(explore.DOMAINS, k)))) for k in keys]
    return out, gate.hits()


def main(argv):
    import multiprocessing
    limit = int(argv[0]) if argv else 0
    out = argv[1] if len(argv) > 1 else "check-run.md"
    workers = int(argv[2]) if len(argv) > 2 else min(WORKERS, multiprocessing.cpu_count())
    t0 = time.time()
    todo = [explore.key(f) for f in explore.states()]
    todo += sorted({explore.key(f) for f in fuzz.corpus()} - set(todo))     # the fuzzer's kept states, every run
    todo = todo[:limit] if limit else todo
    shards = [todo[i::workers] for i in range(workers)]
    rows, graph, roundtrip, first = defaultdict(list), {}, [], {}
    gate = Gate()
    # spawn: each worker imports check afresh, so its memory, tasks and tape live in a data dir of its own
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for results, hits in pool.imap_unordered(_shard, shards):
            gate.merge(hits)
            for k, after, d, progress, found, mismatch, got in results:
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
    b, d = first["step"]
    unchecked = oracle.unchecked(b, d)
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
