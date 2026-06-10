#!/usr/bin/env bash
# =============================================================================
# PHANTOM — One-command launcher for GitHub Codespaces
# Usage: bash codespaces/launch.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[PHANTOM]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step() { echo -e "\n${BLUE}━━━ $1 ━━━${NC}"; }

# Go to repo root regardless of where script is called from
cd "$(dirname "$0")/.."
ROOT=$(pwd)
log "Working directory: $ROOT"

# ── Step 1: System deps ───────────────────────────────────────────────────────
step "1/5  System dependencies"
sudo apt-get update -qq
sudo apt-get install -y -qq curl python3-pip nodejs npm > /dev/null
docker compose version &>/dev/null || sudo apt-get install -y -qq docker-compose-plugin > /dev/null
log "System deps OK"

# ── Step 2: Python deps ───────────────────────────────────────────────────────
step "2/5  Python dependencies (CPU torch — ~3 min)"
pip install -q torch==2.3.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu

pip install -q \
    torch-geometric==2.5.3 \
    fastapi==0.111.0 \
    "uvicorn[standard]==0.29.0" \
    httpx==0.27.0 \
    networkx==3.3 \
    structlog==24.1.0 \
    prometheus-client==0.20.0 \
    pydantic==2.7.1 \
    numpy==1.26.4 \
    pandas==2.2.2 \
    matplotlib==3.9.0 \
    scipy==1.13.0 \
    locust==2.24.0
log "Python deps OK"

# ── Step 3: Build images ──────────────────────────────────────────────────────
step "3/5  Building Docker images (~3 min)"
docker build -q -t phantom/graph-builder:latest \
    -f ml/graph_builder/Dockerfile ml/graph_builder/ &
PID1=$!
docker build -q -t phantom/ml-server:latest \
    -f ml/gnn_lstm/Dockerfile ml/ &
PID2=$!
docker build -q -t phantom/dashboard:latest \
    dashboard/ &
PID3=$!
wait $PID1 $PID2 $PID3
log "Docker images built"

# ── Step 4: Start services ────────────────────────────────────────────────────
step "4/5  Starting services"

log "Starting observability stack..."
docker compose up -d prometheus grafana loki tempo otel-collector
sleep 20

log "Starting Graph Builder..."
docker compose up -d graph-builder
sleep 15

log "Starting ML service..."
docker compose up -d phantom-ml
sleep 25

log "Starting Dashboard..."
docker compose up -d dashboard
sleep 5

# ── Step 5: Health checks ─────────────────────────────────────────────────────
step "5/5  Health checks"

check() {
    local name=$1 url=$2
    if curl -sf "$url" > /dev/null 2>&1; then
        log "$name ✓"
    else
        warn "$name not ready yet (may need 30s more)"
    fi
}

check "Prometheus"    "http://localhost:9090/-/ready"
check "Grafana"       "http://localhost:3000/api/health"
check "Graph Builder" "http://localhost:8000/health"
check "ML Service"    "http://localhost:8001/health"
check "Dashboard"     "http://localhost:3001"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  PHANTOM is running!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Grafana dashboard → http://localhost:3000  (admin / phantom)"
echo "  React frontend    → http://localhost:3001"
echo "  Prometheus        → http://localhost:9090"
echo "  ML API            → http://localhost:8001/health"
echo "  Graph Builder     → http://localhost:8000/graph"
echo ""
echo "  Next steps:"
echo "    Generate traces:  bash codespaces/load.sh"
echo "    Train the model:  bash codespaces/train.sh"
echo "    Stop everything:  bash codespaces/stop.sh"
echo ""