#!/usr/bin/env bash
# Usage: bash codespaces/logs.sh [service]
# Services: phantom-ml graph-builder grafana prometheus all
cd "$(dirname "$0")/.."
SERVICE=${1:-phantom-ml}
if [ "$SERVICE" = "all" ]; then
    docker compose logs -f
else
    docker compose logs -f "$SERVICE"
fi