#!/usr/bin/env bash
# Run the harness on every run of a reskinned data dir, one run at a time, then print the leaderboard Score.
#   harvester/synth/measure.sh port "note"        # data/synth/port, results in replay_results/port
# Progress: one line per run in harvester/data/synth/<theme>_measure.progress (and on stderr).
set -euo pipefail
cd "$(dirname "$0")/../.."
THEME=${1:?theme}; NOTE=${2:-"theme $THEME"}; PORT=${PORT:-3996}
DATA=harvester/data/synth/$THEME
RESULTS=harvester/replay_results/$THEME
PROG=harvester/data/synth/${THEME}_measure.progress
N=$(ls -d "$DATA"/runs/*/ | wc -l)
mkdir -p "$RESULTS"; : > "$PROG"
say() { echo "$(date +%H:%M:%S) $*" | tee -a "$PROG" >&2; }

uv run harvester/replay/server.py --data "$DATA" --port "$PORT" --results "$RESULTS" > "$RESULTS/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
until curl -fsS "http://localhost:$PORT/api/v1/me" -H "Authorization: Bearer arena_local" >/dev/null 2>&1; do sleep 1; done
say "server up on :$PORT, $N runs"

for i in $(seq 1 "$N"); do
  say "[$i/$N] start"
  t0=$(date +%s)
  ARENA_BASE_URL=http://localhost:$PORT ARENA_API_KEY=arena_local \
    uv run main.py --kind dev --fresh --note "$NOTE" > "$RESULTS/main_$i.log" 2>&1 || say "[$i/$N] main.py exited $?"
  last=$(ls -t "$RESULTS"/rp*.json 2>/dev/null | head -1)
  sum=$(python3 - "$last" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
t = d.get("tasks", {})
tasks = t.values() if isinstance(t, dict) else t
scores = [((x.get("grade") or {}).get("score") or 0.0) for x in tasks]
print(f"{d.get('run_id')}: {sum(1 for x in tasks if x.get('grade'))}/{len(scores)} graded, A {sum(scores)/max(len(scores),1):.3f}")
EOF
)
  say "[$i/$N] done in $(( $(date +%s) - t0 ))s: $sum"
done
uv run harvester/report.py "$RESULTS"/rp*.json | tee -a "$PROG"
