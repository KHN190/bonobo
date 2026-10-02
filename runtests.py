#!/usr/bin/env python3
"""Run the offline suite, one process per test file, in parallel.

Every test file here is independent — they share no fixtures and no state but the recorded tape, which is read
only — so the suite is embarrassingly parallel and was running serially for no reason. The slow half is one file
(`test_offline` replays the whole tape), so splitting by file turns the wall clock into the length of that one file
rather than the sum of all of them.

    ./runtests.py                 everything
    ./runtests.py --fast          skip the replay files (the start-up gate)
    ./runtests.py tests.test_pool one file
"""
import concurrent.futures
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# Replay-driven files: they answer "is the model still right", not "does this code run". The start-up gate skips
# them (see supervise.sh); the check-in runs everything.
SLOW = {"tests.test_acceptance", "tests.test_offline", "tests.test_incidents", "tests.test_playthrough"}


WORKERS = 6          # processes at once: the CPU rule (≤ 6 cores)

def files():
    return [f"tests.{os.path.basename(p)[:-3]}" for p in sorted(glob.glob("tests/test_*.py"))]


def run_group(names):
    """Run several test files in ONE process.

    The suite is dominated by start-up, not by assertions: forty files meant forty interpreters each importing the
    whole package (brain.py alone is 145 KB), which is thirteen seconds of imports around a few hundred
    milliseconds of tests. Grouping turns that into one import per worker.

    Both styles live here: files of TestCases, and files of top-level `check(...)` calls that run on IMPORT. The
    second kind collects no tests and exits 5 ("NO TESTS RAN") — but it has already run by then, and raises on a
    failed check, so 5 is a pass. It was being reported as a failure for a suite that had in fact passed: a red
    that vanished when the file was run by hand, which is the worst kind.
    """
    began = time.time()
    p = subprocess.run([sys.executable, "-m", "unittest", "--durations", "0"] + list(names),
                       capture_output=True, text=True, env=sandbox())
    out = p.stdout + p.stderr
    code = 0 if p.returncode == 5 else p.returncode      # 5 = "NO TESTS RAN": a check-style file, already run
    # A file that fails to IMPORT is a failure, whatever the exit code says: the loader's traceback (or a missing
    # "Ran N tests" line) means nothing in that file was tested — which reads as green unless it is caught here.
    if "Traceback (most recent call last)" in out and ("ImportError" in out or "Error while importing" in out
                                                       or "Failed to import test module" in out
                                                       or "Ran " not in out):
        code = code or 1
    if "Ran " not in out and p.returncode != 5:
        code = code or 1
    return list(names), code, out, time.time() - began


# Where a test process writes: never the player's data directory. Tests read the recorded tape (in the repo) and
# write nowhere that matters.
_SANDBOX = None


def sandbox():
    """One test process's env: its own empty data dir under the run's root (a shared one leaked a queue across)."""
    global _SANDBOX
    if _SANDBOX is None:
        _SANDBOX = tempfile.mkdtemp(prefix="bonobo-tests-")
    env = dict(os.environ)
    env["MC_DATA"] = tempfile.mkdtemp(dir=_SANDBOX)
    # The per-file overrides too: a module that takes its own env var would otherwise still find the real file.
    for var in ("MC_NOTES", "MC_ROUTE", "MC_DIRECTIVES", "MC_WANTS", "MC_TAPE"):
        env.pop(var, None)
    return env


TIMES = os.path.join(tempfile.gettempdir(), "bonobo-test-times.json")    # seconds per file, from the last runs
_TOOK = re.compile(r"^\s*([\d.]+)s\s+\S+ \((tests\.test_\w+)\.")
_RED = re.compile(r"^(?:FAIL|ERROR): \S+ \((tests\.test_\w+)\.|Failed to import test module: (tests\.test_\w+)")


def times():
    try:
        with open(TIMES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def groups(names, workers):
    """Longest file first into the least-loaded worker, by last run's seconds (unknown files count as the median)."""
    known = times()
    seen = sorted(known.get(n, 0.0) for n in names if n in known)
    guess = seen[len(seen) // 2] if seen else 1.0
    bins = [[0.0, []] for _ in range(workers)]
    for n in sorted(names, key=lambda n: -known.get(n, guess)):
        b = min(bins, key=lambda b: b[0])
        b[0] += known.get(n, guess)
        b[1].append(n)
    return [b[1] for b in bins if b[1]]


def cleanup():
    if _SANDBOX and os.path.isdir(_SANDBOX):
        shutil.rmtree(_SANDBOX, ignore_errors=True)


def main(argv):
    names = [a for a in argv if a.startswith("tests.")]
    if not names:
        names = [n for n in files() if not ("--fast" in argv and n in SLOW)]
    began = time.time()
    failed, total, took_by = [], 0, times()
    sandbox()                       # the run's root: each process's data directory under it, removed at the end
    batches = groups(names, min(WORKERS, len(names) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(WORKERS, len(batches))) as pool:
        for group, code, out, took in pool.map(run_group, batches):
            total += sum(int(ln.split()[1]) for ln in out.splitlines() if ln.startswith("Ran "))
            spent = {}
            for m in map(_TOOK.match, out.splitlines()):
                if m:
                    spent[m.group(2)] = spent.get(m.group(2), 0.0) + float(m.group(1))
            took_by.update({n: spent.get(n, 0.0) for n in group})
            if code:
                # the red names the file from the output; a file that died without naming itself is re-run alone
                named = {a or b for m in map(_RED.match, out.splitlines()) if m for a, b in [m.groups()]}
                blame = [n for n in group if n in named] or (
                    [n for n in group if run_group([n])[1]] if len(group) > 1 else list(group))
                failed.extend(blame or group)
                print(f"FAIL {' '.join(blame or group)} ({took:.1f}s)")
                print("\n".join(ln for ln in out.splitlines()
                                 if ln.startswith(("FAIL", "ERROR", "AssertionError")) or "Error:" in ln)[:2000])
    with open(TIMES, "w") as f:
        json.dump(took_by, f)
    print(f"\n{total} tests in {time.time() - began:.1f}s — {'FAILED: ' + ', '.join(failed) if failed else 'OK'}")
    cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
