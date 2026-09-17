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
import subprocess
import sys
import time

# Replay-driven files: they answer "is the model still right", not "does this code run". The start-up gate skips
# them (see supervise.sh); the check-in runs everything.
SLOW = {"tests.test_acceptance", "tests.test_offline", "tests.test_incidents", "tests.test_playthrough"}


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
                       capture_output=True, text=True, env=dict(os.environ))
    code = 0 if p.returncode == 5 else p.returncode      # 5 = "NO TESTS RAN": a check-style file, already run
    return list(names), code, p.stdout + p.stderr, time.time() - began


def groups(names, workers):
    """Files split into `workers` groups: the replay-driven ones get a group each (they are the long pole), the
    rest are dealt round-robin so every worker imports the package once."""
    slow = [n for n in names if n in SLOW]
    rest = [n for n in names if n not in SLOW]
    out = [[n] for n in slow]
    buckets = max(1, workers - len(out))
    out += [rest[i::buckets] for i in range(buckets)]
    return [g for g in out if g]


def main(argv):
    names = [a for a in argv if a.startswith("tests.")]
    if not names:
        names = [n for n in files() if not ("--fast" in argv and n in SLOW)]
    began = time.time()
    failed, total = [], 0
    batches = groups(names, min(8, len(names) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(batches)) as pool:
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
