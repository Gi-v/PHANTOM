#!/usr/bin/env bash
# PHANTOM — Collect graph snapshots + train model
# Usage: bash codespaces/train.sh
set -euo pipefail

cd "$(dirname "$0")/.."
SNAPSHOTS=${1:-15}   # default 15 snapshots = 15 min
EPOCHS=${2:-50}

echo "[PHANTOM] Collecting $SNAPSHOTS graph snapshots (1 per minute)..."
mkdir -p ml/data/traces

python3 - << PYEOF
import time, json, httpx, pathlib, sys

n = int("$SNAPSHOTS")
for i in range(n):
    try:
        r = httpx.get("http://localhost:8000/graph", timeout=5)
        if r.status_code == 200:
            data = r.json()
            nodes = len(data.get("nodes", []))
            p = pathlib.Path(f"ml/data/traces/snap_{i:03d}.json")
            p.write_text(json.dumps([{**data, "timestamp": time.time()}]))
            print(f"  [{i+1}/{n}] {nodes} nodes saved", flush=True)
        else:
            print(f"  [{i+1}/{n}] Graph builder not ready yet", flush=True)
    except Exception as e:
        print(f"  [{i+1}/{n}] Warning: {e}", flush=True)
    if i < n - 1:
        time.sleep(60)

print("Snapshot collection complete.")
PYEOF

echo "[PHANTOM] Training GNN+LSTM model ($EPOCHS epochs)..."
cd ml && python3 gnn_lstm/train.py \
    --data-dir data/traces/ \
    --output gnn_lstm/checkpoints/ \
    --epochs "$EPOCHS"

echo ""
echo "[PHANTOM] Loading checkpoint into running ML service..."
ML_CONTAINER=$(docker compose ps -q phantom-ml 2>/dev/null || echo "")
if [ -n "$ML_CONTAINER" ]; then
    docker cp ml/gnn_lstm/checkpoints/phantom_latest.pt \
        "$ML_CONTAINER":/models/phantom_latest.pt
    docker compose restart phantom-ml
    sleep 15
    echo "[PHANTOM] Checkpoint loaded. Verifying..."
    curl -s http://localhost:8001/health | python3 -m json.tool
else
    echo "[PHANTOM] ML container not running. Start with: bash codespaces/launch.sh"
fi