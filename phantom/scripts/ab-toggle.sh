#!/usr/bin/env bash
# ab-toggle.sh — alternates PHANTOM on/off every INTERVAL seconds
# Used for in-situ A/B comparison without restarting load tests
set -euo pipefail

INTERVAL=${INTERVAL:-900}   # 15 min default
NAMESPACE=${NAMESPACE:-phantom}
RUNS=${RUNS:-4}

echo "[phantom-ab] Starting A/B toggle: ${INTERVAL}s per window, ${RUNS} rounds"

for i in $(seq 1 $RUNS); do
  echo "[phantom-ab] Round $i — PHANTOM ON"
  kubectl patch configmap phantom-config -n $NAMESPACE \
    --type=merge -p '{"data":{"PHANTOM_ENABLED":"true"}}'
  kubectl annotate deployment phantom-controller -n $NAMESPACE \
    phantom.io/ab-mode=phantom --overwrite
  sleep $INTERVAL

  echo "[phantom-ab] Round $i — PHANTOM OFF (HPA control)"
  kubectl patch configmap phantom-config -n $NAMESPACE \
    --type=merge -p '{"data":{"PHANTOM_ENABLED":"false"}}'
  kubectl annotate deployment phantom-controller -n $NAMESPACE \
    phantom.io/ab-mode=hpa --overwrite
  sleep $INTERVAL
done

echo "[phantom-ab] A/B experiment complete."
