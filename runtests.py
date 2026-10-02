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
import os
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
    p = subprocess.run([sys.executable, "-m", "unittest"] + list(names),
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
    """The environment a test process gets: its own empty data directory, made once per run."""
    global _SANDBOX
    if _SANDBOX is None:
        _SANDBOX = tempfile.mkdtemp(prefix="bonobo-tests-")
    env = dict(os.environ)
    env["MC_DATA"] = _SANDBOX
    # The per-file overrides too: a module that takes its own env var would otherwise still find the real file.
    for var in ("MC_NOTES", "MC_ROUTE", "MC_DIRECTIVES", "MC_WANTS", "MC_TAPE"):
        env.pop(var, None)
    return env


def groups(names, workers):
    """Files split into `workers` groups: the replay-driven ones get a group each (they are the long pole), the
    rest are dealt round-robin so every worker imports the package once."""
    slow = [n for n in names if n in SLOW]
    rest = [n for n in names if n not in SLOW]
    out = [[n] for n in slow]
    buckets = max(1, workers - len(out))
    out += [rest[i::buckets] for i in range(buckets)]
    return [g for g in out if g]


def cleanup():
    if _SANDBOX and os.path.isdir(_SANDBOX):
        shutil.rmtree(_SANDBOX, ignore_errors=True)


def main(argv):
    names = [a for a in argv if a.startswith("tests.")]
    if not names:
        names = [n for n in files() if not ("--fast" in argv and n in SLOW)]
    began = time.time()
    failed, total = [], 0
    sandbox()                       # one empty data directory for the whole run, removed at the end
    batches = groups(names, min(WORKERS, len(names) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(WORKERS, len(batches))) as pool:
        for group, code, out, took in pool.map(run_group, batches):
            total += sum(int(ln.split()[1]) for ln in out.splitlines() if ln.startswith("Ran "))
            if code:
                # Groups are for start-up cost, not for reporting: re-run the group's files one at a time so the
                # red names the file, not the batch it happened to share a process with.
                blame = [n for n in group if run_group([n])[1]] if len(group) > 1 else list(group)
                failed.extend(blame or group)
                print(f"FAIL {' '.join(blame or group)} ({took:.1f}s)")
                print("\n".join(ln for ln in out.splitlines()
                                 if ln.startswith(("FAIL", "ERROR", "AssertionError")) or "Error:" in ln)[:2000])
    print(f"\n{total} tests in {time.time() - began:.1f}s — {'FAILED: ' + ', '.join(failed) if failed else 'OK'}")
    cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
