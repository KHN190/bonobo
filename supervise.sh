#!/bin/bash
# Starts (or restarts) autoplay detached and watches it. Exits — waking whoever launched it in the background —
# on: autoplay exit, crash, the same failure twice, agent idle >= 30 s, a task stuck >= STUCK_AFTER s (player-held
# pauses excluded), or the periodic check-in. Usage: [MIN_VERSION=x.y.z] supervise.sh [hours=10] [checkin_s=600]
cd "$(dirname "$0")" || exit 1
# Where the mod writes config/agent-bridge.json. Override by exporting MC_INSTANCE before running.
export MC_INSTANCE="${MC_INSTANCE:-$HOME/Library/Application Support/ModrinthApp/profiles/Fabric API}"
# The mod's Java package, when checked out. Optional: without it readiness keys on the jar version.
export MC_MOD_SRC="${MC_MOD_SRC:-$HOME/minecraft-claude-bridge/anaka/src/main/java/dev/anaka}"
HOURS=${1:-10}
CHECKIN=${2:-600}   # the 10-minute review (user, 2026-09-16): Claude reads the packet, distils lessons, plans
# The runtime data directory, same default as bonobo.paths (override with MC_DATA).
DIR="${MC_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/bonobo}"
LOG="$DIR/autoplay.log"
PIDFILE="$DIR/autoplay.pid"
WATCHFILE="$DIR/supervise.pid"

# Only one supervisor at a time: retire the previous one (never ourselves).
if [ -f "$WATCHFILE" ] && [ "$(cat "$WATCHFILE")" != "$$" ] && kill -0 "$(cat "$WATCHFILE")" 2>/dev/null; then
  kill "$(cat "$WATCHFILE")"
fi
echo $$ > "$WATCHFILE"

python3 -m py_compile mc.py bonobo/*.py || { echo "WAKE: syntax error in the brain"; exit 1; }
python3 -c "import bonobo.brain" || { echo "WAKE: brain fails to import"; exit 1; }
# The start-up gate answers one question — can this code run — so it runs the tests that are pure functions and
# structure (about a second). The replay tests (test_acceptance re-decides a recorded round per case, test_offline
# drives the real planner) answer a different one: is the MODEL still right. Those take a minute, and a minute of
# not playing, every restart, to re-confirm something that only changes when the model does. They run at the
# check-in below instead, where there is already a pause.
# One process per test file, in parallel (runtests.py): the files share nothing but the read-only tape, so the
# wall clock is the slowest file rather than the sum. `--fast` leaves out the replay-driven ones — they answer "is
# the model still right", which only changes when the model does, and the check-in below runs everything.
./runtests.py --fast > /tmp/bonobo-tests.log 2>&1 || { echo "WAKE: offline checks failed"; grep -E "^FAIL" /tmp/bonobo-tests.log; exit 1; }
# Catch "parameter shadows a module function" bugs across the package.
python3 - <<'EOF' || { echo "WAKE: name shadowing in the brain"; exit 1; }
import ast, glob, sys
bad = []
for f in glob.glob("bonobo/*.py") + ["mc.py"]:
    tree = ast.parse(open(f).read())
    top = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    bad += [f"{f}:{n.lineno} '{a.arg}'" for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.Lambda))
            for a in n.args.args + n.args.kwonlyargs if a.arg in top]
    # A local variable named like an imported module (`bag = ...` next to `from . import bag`) crashed every round.
    mods = {a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom) and n.module is None
            for a in n.names}
    bad += [f"{f}:{x.lineno} local '{x.id}' shadows a module" for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef) for x in ast.walk(fn)
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store) and x.id in mods]
print("\n".join(bad))
sys.exit(1 if bad else 0)
EOF

# Optional: wait until the game runs at least this mod version (after a rebuild the player must restart).
if [ -n "$MIN_VERSION" ]; then
  until python3 -c "
import sys
from bonobo import api
v = api.status()['version'].split('+')[0]
sys.exit(0 if tuple(map(int, v.split('.'))) >= tuple(map(int, '$MIN_VERSION'.split('.'))) else 1)
" 2>/dev/null; do sleep 10; done
fi

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  kill "$(cat "$PIDFILE")"
  sleep 1
fi
# Roll the readable log before the run starts, never during one: the watcher below reads it by line offset, and a
# file that rolls under a running `tail -n +N` reads back as an empty session. 2 MB, one previous log kept.
python3 -c "import sys; from bonobo import api; api.roll(sys.argv[1], 2 << 20)" "$LOG"
START=$(( $(wc -l < "$LOG" 2>/dev/null || echo 0) + 1 ))
nohup python3 mc.py autoplay --hours "$HOURS" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
disown

T0=$(date +%s); IDLE=0; REASON=""
LAST_SIG=""; SIG_SINCE=$(date +%s)
# The brain cancels and switches goal itself after 10 s without progress; wake only if that didn't work.
STUCK_AFTER=${STUCK_AFTER:-25}
while :; do
  sleep 2
  # One state read gives both the stuck signature and the idle flag. "ok" = paused, waiting, or unreachable.
  READ=$(python3 -c "
from bonobo import api
try:
    s = api.get('/state')
except Exception:
    print('ok|busy'); raise SystemExit
c = s['control']; t = c['task']
if not c.get('paused') and (s['health'] <= 8 or (s['inWater'] and s['air'] < 100) or s['inLava']):
    print(f\"DANGER hp={s['health']} air={s['air']} lava={s['inLava']}|danger\")
elif c.get('paused'):
    print('ok|paused')
elif not t:
    import os, time
    from bonobo import paths; hb = paths.data('skill-heartbeat')
    fresh = os.path.exists(hb) and time.time() - os.path.getmtime(hb) < 15
    print('ok|busy' if fresh else 'ok|idle')
elif t['type'] == 'wait':
    print('ok|busy')
else:
    print(f\"{t['id']} {t['doing'][:40]} {round(s['x'], 1)} {round(s['y'], 1)} {round(s['z'], 1)} {round(s['yaw'] / 5)} {round(s['pitch'] / 5)}|busy\")
" 2>/dev/null)
  SIG=${READ%|*}; S=${READ##*|}
  NOW=$(date +%s)
  if [ "$S" = "danger" ]; then
    DANGER=$((${DANGER:-0} + 2))
    [ "$DANGER" -ge 4 ] && { REASON="player in danger: $SIG"; break; }
  else
    DANGER=0
  fi
  if [ "$SIG" != "$LAST_SIG" ] || [ "$SIG" = "ok" ] || [ -z "$SIG" ]; then
    LAST_SIG="$SIG"; SIG_SINCE=$NOW
  elif [ $((NOW - SIG_SINCE)) -ge "$STUCK_AFTER" ]; then
    REASON="task stuck ${STUCK_AFTER}s: $SIG"; break
  fi
  kill -0 "$(cat "$PIDFILE")" 2>/dev/null || { REASON="autoplay process exited"; break; }
  NEW=$(tail -n +"$START" "$LOG")
  echo "$NEW" | grep -q "Traceback" && { REASON="crash"; break; }
  # Single failures are the cerebellum's job (retry.py waits for the state to change). Claude is woken for macro
  # problems only: "?? " = stalled progress, every rescue exhausted, a directive given up, exposed at night.
  echo "$NEW" | grep -q "^.\{9\}?? " && { REASON="brain asked for help: $(echo "$NEW" | grep "^.\{9\}?? " | tail -1)"; break; }
  if [ "$S" = "idle" ]; then IDLE=$((IDLE + 2)); else IDLE=0; fi
  [ "$IDLE" -ge 15 ] && { REASON="agent idle ${IDLE}s"; break; }
  # Manual mode (player holds control) turns everything off, reviews included: the clock restarts on hand-back.
  [ "$S" = "paused" ] && T0=$NOW
  [ $((NOW - T0)) -ge "$CHECKIN" ] && { REASON="review (every $((CHECKIN / 60)) min)"; break; }
done
# The model tests, at the pause rather than at start-up. A failure here is not a reason to stop playing — it says
# the model drifted from the recorded rounds, which is something to read about, not to crash on.
echo "WAKE: $REASON (autoplay pid $(cat "$PIDFILE") still running: $(kill -0 "$(cat "$PIDFILE")" 2>/dev/null && echo yes || echo no))"
tail -n +"$START" "$LOG" | grep -vE "cancelled step" | tail -30
# The working-out for the same window: rankings, refusals, every task. Always on disk, so a wake-up never needs
# the game re-run to find out why.
FROM=$(tail -n +"$START" "$LOG" | head -1 | cut -c1-8)
[ -n "$FROM" ] && python3 -c "
import sys
sys.path.insert(0, '.')
from bonobo import api
lines = api.detail_window('$FROM')
if lines:
    print('--- detail (' + str(len(lines)) + ' lines, last 40):')
    print(chr(10).join(lines[-40:]))
" 2>/dev/null
echo
./runtests.py > /tmp/bonobo-tests-full.log 2>&1 || { echo "model checks failed:"; grep -E "^FAIL" /tmp/bonobo-tests-full.log; }
python3 mc.py review --minutes 5 2>/dev/null
