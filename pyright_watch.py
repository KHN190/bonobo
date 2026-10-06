#!/usr/bin/env python3
"""MC_PYRIGHT for the type ratchet: one `pyright --watch` kept alive across runs, its last count handed back as the
--outputjson summary once it has seen every source as it is now (no report for SETTLE_S after the last edit's);
restarted when a file is added or removed (a watch misses new files); a one-shot run when it cannot answer.
    pyright_watch.py --outputjson --pythonpath PY   the count (what tests/test_types.py asks)
    pyright_watch.py serve PY                        the watcher (started by the first ask)"""
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.1.414"
STATE = os.path.join(tempfile.gettempdir(), "bonobo-pyright-watch.json")
PID = os.path.join(tempfile.gettempdir(), "bonobo-pyright-watch.pid")
WAIT_S = 90
SETTLE_S = 4.0
SUMMARY = re.compile(r"(\d+) errors?, (\d+) warnings?, (\d+) informations?")


def pyright():
    return ["npx", "-y", f"pyright@{VERSION}"]


def sources():
    return sorted(p for d in ("bonobo", "check", "tests") for p in glob.glob(os.path.join(ROOT, d, "**", "*.py"), recursive=True))


def newest_source():
    return max(os.path.getmtime(p) for p in sources() + [os.path.join(ROOT, "pyrightconfig.json")])


def file_set():
    return hashlib.sha1("\n".join(sources()).encode()).hexdigest()


def write_state(errors, warnings, infos):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"t": time.time(), "errorCount": errors, "warningCount": warnings, "informationCount": infos}, f)
    os.replace(tmp, STATE)


def serve(python):
    with open(PID, "w") as f:
        json.dump({"pid": os.getpid(), "files": file_set()}, f)
    p = subprocess.Popen(pyright() + ["--watch", "--pythonpath", python], cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        m = SUMMARY.search(line)
        if m:
            write_state(*map(int, m.groups()))


def watcher():
    try:
        with open(PID) as f:
            got = json.load(f)
        os.kill(int(got["pid"]), 0)
        return got
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stop(got):
    try:
        os.killpg(int(got["pid"]), signal.SIGTERM)
    except OSError:
        pass
    for path in (PID, STATE):
        try:
            os.remove(path)
        except OSError:
            pass


def start(python):
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve", python], cwd=ROOT, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fresh_count(python):
    running = watcher()
    if running is not None and running["files"] != file_set():
        stop(running)
        running = None
    if running is None:
        start(python)
    since = newest_source()
    deadline, last, quiet_from = time.time() + WAIT_S, None, None
    while time.time() < deadline:
        try:
            with open(STATE) as f:
                got = json.load(f)
        except (OSError, ValueError):
            got = None
        if got is not None and got.get("t", 0) > since:
            if got["t"] - since > SETTLE_S:
                return got
            if last is None or got["t"] != last["t"]:
                last, quiet_from = got, time.time()
            elif time.time() - quiet_from >= SETTLE_S:
                return got
        time.sleep(0.25)
    return None


def main(argv):
    if argv[:1] == ["serve"]:
        return serve(argv[1])
    python = argv[argv.index("--pythonpath") + 1] if "--pythonpath" in argv else sys.executable
    got = fresh_count(python) if shutil.which("npx") else None
    if got is None:
        return subprocess.run(pyright() + argv, cwd=ROOT).returncode
    print(json.dumps({"summary": {k: got[k] for k in ("errorCount", "warningCount", "informationCount")}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
