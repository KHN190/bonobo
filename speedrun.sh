#!/bin/bash
# A timed speedrun in a fresh world: its own notes/route/priorities/directives (the old world's memory never leaks
# in), route + profile set, then supervise.sh as usual. Usage:
#   speedrun.sh new     — start a run (player standing in the new world, cheats irrelevant: none are used)
#   speedrun.sh         — resume the latest run after a wake (same files, same clock)
#   speedrun.sh splits  — print the splits of the latest run
cd "$(dirname "$0")" || exit 1
# Where the mod writes config/agent-bridge.json. Override by exporting MC_INSTANCE before running.
export MC_INSTANCE="${MC_INSTANCE:-$HOME/Library/Application Support/ModrinthApp/profiles/Fabric API}"
# The runtime data directory, same default as bonobo.paths (override with MC_DATA).
DIR="${MC_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/bonobo}"
RUNS="$DIR/runs"
if [ "$1" = "new" ]; then
  RUN="$RUNS/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$RUN"
  date +%s > "$RUN/start"
  ln -sfn "$RUN" "$RUNS/latest"
else
  RUN="$(readlink "$RUNS/latest")"
  [ -d "$RUN" ] || { echo "no run yet: speedrun.sh new"; exit 1; }
fi
export MC_NOTES="$RUN/notes.json" MC_ROUTE="$RUN/route.json" MC_PRIORITIES="$RUN/priorities.json" \
       MC_DIRECTIVES="$RUN/directives.json"
if [ "$1" = "splits" ]; then
  START=$(cat "$RUN/start")
  grep -h "route: segment\|route: now\|dragon is gone\|?? STALL route" "$DIR/autoplay.log" | tail -40
  echo "elapsed: $(( ($(date +%s) - START) / 60 )) min"
  exit 0
fi
# Scenario commands must never run in a real run.
python3 mc.py scenario disable >/dev/null
if [ "$1" = "new" ]; then
  python3 mc.py route speedrun
  python3 mc.py prio profile speedrun --ttl 7200
  echo "speedrun started $(date '+%H:%M:%S') → $RUN"
fi
exec ./supervise.sh 2 600
