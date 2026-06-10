#!/usr/bin/env bash
set -euo pipefail
echo "[PHANTOM] Running post-create setup..."

# k3d
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

# Python ML deps (CPU torch only — no CUDA in Codespaces 4-core)
pip install --quiet \
    torch==2.3.0+cpu torchvision==0.18.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu

pip install --quiet \
    torch-geometric==2.5.3 \
    fastapi==0.111.0 uvicorn[standard]==0.29.0 \
    httpx==0.27.0 networkx==3.3 structlog==24.1.0 \
    prometheus-client==0.20.0 scikit-learn==1.4.2 \
    pandas==2.2.2 matplotlib==3.9.0 scipy==1.13.0 \
    locust==2.24.0 ruff==0.4.0

# Node deps for dashboard
cd dashboard && npm install --silent && cd ..

# Go deps
cd controller && go mod download && cd ..

echo "[PHANTOM] Setup complete. Run 'make codespaces-start' to launch."
