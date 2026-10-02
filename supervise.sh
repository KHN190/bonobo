#!/bin/bash
# Starts (or restarts) autoplay detached, shows its concise events live, and says a WAKE — a death, a task stuck, a
# slow round, an error or refusal loop, idle, a frozen brain, the check-in — while the bot keeps running; it ends
# only when autoplay ends (a crash, the user's stop).
# Tests are the dev loop, not this one. Usage: [MIN_VERSION=x.y.z] [DRY=1] supervise.sh [hours=10] [checkin_s=600]
cd "$(dirname "$0")" || exit 1
export MC_INSTANCE="${MC_INSTANCE:-$HOME/Library/Application Support/ModrinthApp/profiles/Fabric API}"
export MC_MOD_SRC="${MC_MOD_SRC:-$HOME/minecraft-claude-bridge/anaka/src/main/java/dev/anaka}"
HOURS=${1:-10}
CHECKIN=${2:-600}
IDLE_S=${IDLE_S:-30}
DIR="${MC_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/bonobo}"
LOG="$DIR/autoplay.log"
EVENTS="$DIR/events.log"
PIDFILE="$DIR/autoplay.pid"
WATCHFILE="$DIR/supervise.pid"
mkdir -p "$DIR" && touch "$LOG" "$EVENTS"

# one supervisor at a time: retire the previous one and wait for it (a recycled pid is checked against the command,
# which is matched, never printed)
OLD=$(cat "$WATCHFILE" 2>/dev/null)
if [ -n "$OLD" ] && [ "$OLD" != "$$" ] && ps -p "$OLD" -o command= 2>/dev/null | grep -q supervise.sh; then
  kill "$OLD" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    ps -p "$OLD" -o command= 2>/dev/null | grep -q supervise.sh || break
    sleep 1
  done
  ps -p "$OLD" -o command= 2>/dev/null | grep -q supervise.sh && kill -9 "$OLD" 2>/dev/null
fi
echo $$ > "$WATCHFILE"

# can this code run (a second): syntax and import, nothing more
python3 -m py_compile mc.py bonobo/*.py || { echo "WAKE: syntax error in the brain"; exit 1; }
python3 -c "import bonobo.brain" || { echo "WAKE: brain fails to import"; exit 1; }

# a new save starts fresh (fresh.check drops what belongs to another world)
python3 -c "
from bonobo import fresh
world, dropped = fresh.check()
print(f'world {world}: fresh start' + (f', dropped {\", \".join(dropped)}' if dropped else '')) if world else None
"

# the game: reachable and in a world, at MIN_VERSION when asked (after a rebuild the player restarts the game)
WAITED=""
until WHY=$(python3 -c "
import sys
from bonobo import api
try:
    api.get('/state')
    v = api.status()['version'].split('+')[0]
except api.McError as e:
    print(e); sys.exit(1)
want = '$MIN_VERSION'
ok = not want or tuple(map(int, v.split('.'))) >= tuple(map(int, want.split('.')))
print('' if ok else f'jar {v} < {want}'); sys.exit(0 if ok else 1)
" 2>/dev/null); do
  [ "$WHY" != "$WAITED" ] && { echo "waiting for the game: ${WHY:-unreachable}"; WAITED="$WHY"; }
  sleep 10
done

# never begin inside a bench arena: its leftovers killed, the body sent to spawn
[ -z "$DRY" ] && python3 -m bonobo.tools.leave_bench

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  kill "$(cat "$PIDFILE")"
  sleep 1
fi
# roll the logs before the run, never during it (the watch reads from an offset)
python3 -c "import sys; from bonobo import api; [api.roll(p, 2 << 20) for p in sys.argv[1:]]" "$LOG" "$EVENTS"
START=$(( $(wc -l < "$LOG") + 1 ))
OFFSET=$(python3 -m bonobo.tools.wake offset)
if [ -n "$DRY" ]; then
  echo "dry run: autoplay not started (events from offset $OFFSET)"
else
  nohup python3 mc.py autoplay --hours "$HOURS" >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  disown
fi

# the concise events, live
tail -n 0 -F "$EVENTS" 2>/dev/null &
TAIL=$!
trap 'kill $TAIL 2>/dev/null' EXIT

T0=$(date +%s); REASON=""; SEEN=""; STARTED=$T0
while :; do
  sleep 2
  NOW=$(date +%s)
  if [ -z "$DRY" ]; then
    kill -0 "$(cat "$PIDFILE")" 2>/dev/null || { REASON="autoplay process exited"; break; }
    tail -n +"$START" "$LOG" | grep -q "Traceback" && { REASON="crash (Traceback in $LOG)"; break; }
  fi
  if WAKE=$(python3 -m bonobo.tools.wake "$OFFSET" "$IDLE_S" "$SEEN" "$STARTED"); then
    echo "$WAKE"            # said, never an end: the bot keeps running
    OFFSET=$(python3 -m bonobo.tools.wake offset); SEEN=$(python3 -m bonobo.tools.wake mtime); T0=$NOW
  fi
  # player holds control: no check-in until hand-back
  python3 -c "from bonobo import api; exit(0 if api.get('/state')['control'].get('paused') else 1)" 2>/dev/null && T0=$NOW
  [ $((NOW - T0)) -ge "$CHECKIN" ] && { echo "WAKE: check-in (every $((CHECKIN / 60)) min)"; T0=$NOW; }
  [ -n "$DRY" ] && [ $((NOW - T0)) -ge "${DRY_S:-6}" ] && { REASON="dry run over"; break; }
done
echo "WAKE: $REASON"
python3 -m bonobo.tools.wake tail "$OFFSET"
echo "  (events: $EVENTS, working-out: $DIR/detail.log)"
