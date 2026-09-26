#!/bin/bash
# A timed speedrun in a fresh world: its own notes and task queue (the old world's memory never leaks in), the
# milestones queued, then supervise.sh as usual. Usage:
#   speedrun.sh new     — start a run (player standing in the new world, cheats irrelevant: none are used)
#   speedrun.sh         — resume the latest run after a wake (same files, same clock)
#   speedrun.sh splits  — print the splits of the latest run
cd "$(dirname "$0")" || exit 1
# Where the mod writes config/agent-bridge.json. Override by exporting MC_INSTANCE before running.
export MC_INSTANCE="${MC_INSTANCE:-$HOME/Library/Application Support/ModrinthApp/profiles/Fabric API}"
# The mod's Java package, when checked out. Optional: without it readiness keys on the jar version.
export MC_MOD_SRC="${MC_MOD_SRC:-$HOME/minecraft-claude-bridge/anaka/src/main/java/dev/anaka}"
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
export MC_NOTES="$RUN/notes.json" MC_TASKS="$RUN/tasks.json"
if [ "$1" = "splits" ]; then
  START=$(cat "$RUN/start")
  grep -h "task done:\|failed:\|dragon is gone" "$DIR/autoplay.log" | tail -40
  echo "elapsed: $(( ($(date +%s) - START) / 60 )) min"
  exit 0
fi
# Scenario commands must never run in a real run.
python3 mc.py scenario disable >/dev/null
if [ "$1" = "new" ]; then
  for m in "stone tools" "station kit" "food" "bed" "iron pickaxe" "iron tools" "water bucket"; do
    python3 mc.py task add milestone $m >/dev/null
  done
  echo "speedrun started $(date '+%H:%M:%S') → $RUN"
fi
exec ./supervise.sh 2 600
