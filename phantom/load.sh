#!/usr/bin/env bash
# PHANTOM — Generate load + collect traces
# Usage: bash codespaces/load.sh [scenario: spike|ramp|quick]
set -euo pipefail

cd "$(dirname "$0")/.."
SCENARIO=${1:-quick}

echo "[PHANTOM] Running load scenario: $SCENARIO"

case $SCENARIO in
  spike)
    cd load-testing && locust -f locustfile.py \
      --host http://localhost:8080 \
      --users 200 --spawn-rate 40 \
      --run-time 5m --headless \
      --csv ../research/data/spike_$(date +%Y%m%d_%H%M)
    ;;
  ramp)
    cd load-testing && locust -f locustfile.py \
      --host http://localhost:8080 \
      --users 100 --spawn-rate 5 \
      --run-time 15m --headless \
      --csv ../research/data/ramp_$(date +%Y%m%d_%H%M)
    ;;
  quick)
    # 2-min light load — enough to populate graph
    cd load-testing && locust -f locustfile.py \
      --host http://localhost:8080 \
      --users 20 --spawn-rate 5 \
      --run-time 2m --headless
    ;;
esac

echo "[PHANTOM] Load test complete."
echo "[PHANTOM] Check graph: curl http://localhost:8000/graph | python3 -m json.tool"