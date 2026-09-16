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
SLOW = {"tests.test_acceptance", "tests.test_offline", "tests.test_incidents"}


def files():
    return [f"tests.{os.path.basename(p)[:-3]}" for p in sorted(glob.glob("tests/test_*.py"))]


def run_one(name):
    """Run one test file in its own process.

    Both styles live here: files of TestCases, and files of top-level `check(...)` calls that run on IMPORT. The
    second kind collects no tests and exits 5 ("NO TESTS RAN") — but it has already run by then, and raises on a
    failed check, so 5 is a pass. It was being reported as a failure for a suite that had in fact passed: a red
    that vanished when the file was run by hand, which is the worst kind.
    """
    began = time.time()
    p = subprocess.run([sys.executable, "-m", "unittest", name],
                       capture_output=True, text=True, env=dict(os.environ))
    code = 0 if p.returncode == 5 else p.returncode      # 5 = "NO TESTS RAN": a check-style file, already run
    return name, code, p.stdout + p.stderr, time.time() - began


def main(argv):
    names = [a for a in argv if a.startswith("tests.")]
    if not names:
        names = [n for n in files() if not ("--fast" in argv and n in SLOW)]
    began = time.time()
    failed, total = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(names) or 1)) as pool:
        for name, code, out, took in pool.map(run_one, names):
            ran = [ln for ln in out.splitlines() if ln.startswith("Ran ")]
            total += int(ran[0].split()[1]) if ran else 0
            if code:
                failed.append(name)
                print(f"FAIL {name} ({took:.1f}s)")
                print("\n".join(ln for ln in out.splitlines()
                                if ln.startswith(("FAIL", "ERROR", "AssertionError")) or "Error:" in ln)[:2000])
    print(f"\n{total} tests in {time.time() - began:.1f}s — {'FAILED: ' + ', '.join(failed) if failed else 'OK'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
