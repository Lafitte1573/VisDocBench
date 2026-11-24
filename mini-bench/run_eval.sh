#!/usr/bin/env bash
# Run the VisDocBench mini-bench.
#
#   ./run_eval.sh                    # mini-bench subset (300 items)
#   LIMIT=1 ./run_eval.sh            # smoke test (1 item per subset)
#   LIMIT=5 WORKERS=4 ./run_eval.sh  # 5 items per subset, 4 API workers
#   OUTPUT=results/my.json ./run_eval.sh --task mc
#   ./run_eval.sh --data-dir ../release/annotations --limit 0   # full VisDocBench
#
# API credentials can be exported in the environment or placed in a local
# .env file next to this script (never commit that file).
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

LIMIT="${LIMIT:-0}"
WORKERS="${WORKERS:-1}"
TASK="${TASK:-all}"
OUTPUT="${OUTPUT:-results/results.json}"

exec python evaluate.py \
  --limit "$LIMIT" \
  --workers "$WORKERS" \
  --task "$TASK" \
  --output "$OUTPUT" \
  "$@"
