"""Run one bench scenario exactly ONCE and stop.

`mc.py scenario run` retries a failing scenario up to five times, which is right for flaky fights but wrong when the
point is a single honest measurement ("test it once"). This waits for any running bench to finish, starts the
scenario, and stops it the moment the first verdict line appears.

Usage: one_shot.py SCENARIO [LOGPATH]
"""
import os
import re
import subprocess
import sys
import time

# The repository root, two levels up from bonobo/tools/ — mc.py lives there, not beside this file.
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERDICT = re.compile(r"^(PASS|FAIL) .*$", re.M)


def bench_running():
    out = subprocess.run(["pgrep", "-f", "mc.py scenario"], capture_output=True).stdout.strip()
    return bool(out)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: one_shot.py SCENARIO [LOGPATH]")
    scenario = sys.argv[1]
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, f"{scenario}-once.log")
    while bench_running():
        time.sleep(5)
    with open(log, "w") as out:
        proc = subprocess.Popen([sys.executable, "mc.py", "scenario", "run", scenario, "--force"],
                                cwd=HERE, stdout=out, stderr=subprocess.STDOUT)
    verdict = None
    while proc.poll() is None:
        time.sleep(3)
        try:
            with open(log) as f:
                found = VERDICT.search(f.read())
        except OSError:
            continue
        if found:
            verdict = found.group(0)
            proc.terminate()          # one attempt only: no retry loop
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
    if verdict is None:
        try:
            with open(log) as f:
                found = VERDICT.search(f.read())
            verdict = found.group(0) if found else "no verdict (see the log)"
        except OSError:
            verdict = "no verdict (log unreadable)"
    print(f"{scenario}: {verdict}")
    print(f"log: {log}")


if __name__ == "__main__":
    main()
