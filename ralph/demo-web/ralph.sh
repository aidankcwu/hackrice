#!/bin/bash
# Ralph loop for the demo-web branch (web side of the demo UI). Usage: ./ralph.sh [max_iterations]
set -u
MAX=${1:-6}
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
CLAUDE="$HOME/.local/bin/claude"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$DIR/logs"
[ -f "$DIR/progress.txt" ] || printf '# Ralph progress (demo, web)\nStarted: %s\n---\n' "$(date)" > "$DIR/progress.txt"
for i in $(seq 1 "$MAX"); do
  echo "=== iteration $i/$MAX $(date '+%H:%M') ==="
  OUT=$("$CLAUDE" --model claude-opus-5-5 --dangerously-skip-permissions --print --max-turns 120 < "$DIR/PROMPT.md" 2>&1 | tee "$DIR/logs/iter-$i.log")
  if echo "$OUT" | grep -q "<promise>COMPLETE</promise>"; then echo "COMPLETE at iteration $i"; exit 0; fi
  sleep 3
done
echo "max iterations reached"; exit 1
