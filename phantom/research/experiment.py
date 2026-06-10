"""
experiment.py — PHANTOM Research Experiment Runner

Automates: deploy baseline → run load → collect metrics → switch to PHANTOM → repeat
Outputs CSV for statistical analysis.

Usage:
  python experiment.py --scenario spike --duration 3600 --runs 3
"""

import argparse
import csv
import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Literal

import httpx

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
LOCUST_URL     = os.getenv("LOCUST_URL",     "http://localhost:8089")
NAMESPACE      = os.getenv("NAMESPACE",      "phantom")


@dataclass
class ExperimentResult:
    run_id: str
    autoscaler: str         # phantom | hpa | keda
    scenario: str
    duration_s: int
    p99_latency_ms: float
    p95_latency_ms: float
    error_rate_pct: float
    avg_replicas: float
    max_replicas: float
    pod_churn: int          # number of scale events
    avg_mape_pct: float     # prediction MAPE (phantom only)
    avg_confidence: float   # model confidence (phantom only)
    cost_pod_hours: float   # replicas × duration as proxy
    slo_violations: int     # requests > 200ms SLO


def query_prometheus(query: str) -> float:
    """Execute instant PromQL query, return scalar."""
    try:
        resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query",
                         params={"query": query}, timeout=10)
        data = resp.json()
        result = data.get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
    except Exception as e:
        print(f"  [warn] Prometheus query failed: {query} — {e}")
    return 0.0


def query_range(query: str, start: float, end: float, step: str = "60s") -> list:
    try:
        resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query_range",
                         params={"query": query, "start": start, "end": end, "step": step},
                         timeout=30)
        data = resp.json()
        return data.get("data", {}).get("result", [])
    except Exception:
        return []


def enable_phantom(enabled: bool):
    """Toggle PHANTOM controller on/off via ConfigMap patch."""
    value = "true" if enabled else "false"
    subprocess.run([
        "kubectl", "patch", "configmap", "phantom-config",
        "-n", NAMESPACE,
        "--type=merge",
        f'-p={{"data":{{"PHANTOM_ENABLED":"{value}"}}}}'
    ], check=False, capture_output=True)
    print(f"  [setup] PHANTOM {'enabled' if enabled else 'disabled'}")
    time.sleep(10)  # let controller react


def apply_autoscaler(mode: Literal["phantom", "hpa", "keda"]):
    print(f"  [setup] Switching to autoscaler: {mode}")
    if mode == "phantom":
        enable_phantom(True)
        subprocess.run(["kubectl", "apply", "-f",
                        "kubernetes/overlays/dev/predictivescalers.yaml",
                        "-n", NAMESPACE], check=False, capture_output=True)
    elif mode == "hpa":
        enable_phantom(False)
        subprocess.run(["kubectl", "apply", "-f",
                        "research/baselines/hpa.yaml", "-n", NAMESPACE],
                       check=False, capture_output=True)
    elif mode == "keda":
        enable_phantom(False)
        subprocess.run(["kubectl", "apply", "-f",
                        "research/baselines/keda-scaledobject.yaml", "-n", NAMESPACE],
                       check=False, capture_output=True)
    time.sleep(30)  # stabilise


def collect_metrics(autoscaler: str, scenario: str, run_id: str,
                    start_ts: float, end_ts: float, duration_s: int) -> ExperimentResult:
    print(f"  [collect] Querying Prometheus for {autoscaler} run {run_id}...")

    p99 = query_prometheus(
        f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket'
        f'{{namespace="{NAMESPACE}"}}[{duration_s}s])) by (le)) * 1000')

    p95 = query_prometheus(
        f'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket'
        f'{{namespace="{NAMESPACE}"}}[{duration_s}s])) by (le)) * 1000')

    error_rate = query_prometheus(
        f'sum(rate(http_requests_total{{namespace="{NAMESPACE}",status=~"5.."}}[{duration_s}s])) / '
        f'sum(rate(http_requests_total{{namespace="{NAMESPACE}"}}[{duration_s}s])) * 100')

    avg_replicas = query_prometheus(
        f'avg_over_time(sum(kube_deployment_spec_replicas{{namespace="{NAMESPACE}"}})[{duration_s}s:])')

    max_replicas = query_prometheus(
        f'max_over_time(sum(kube_deployment_spec_replicas{{namespace="{NAMESPACE}"}})[{duration_s}s:])')

    pod_churn = query_prometheus(
        f'increase(phantom_scale_actions_total[{duration_s}s])')

    mape = query_prometheus(
        f'avg_over_time(phantom_prediction_mape[{duration_s}s])') * 100 if autoscaler == "phantom" else 0.0

    confidence = query_prometheus(
        f'avg_over_time(avg(phantom_model_confidence)[{duration_s}s:])') if autoscaler == "phantom" else 0.0

    cost = avg_replicas * (duration_s / 3600) * 0.048  # t3.medium $/hr per pod approx

    slo_violations = query_prometheus(
        f'sum(increase(http_request_duration_seconds_bucket{{namespace="{NAMESPACE}",'
        f'le="0.2"}}[{duration_s}s]))')

    return ExperimentResult(
        run_id=run_id,
        autoscaler=autoscaler,
        scenario=scenario,
        duration_s=duration_s,
        p99_latency_ms=round(p99, 2),
        p95_latency_ms=round(p95, 2),
        error_rate_pct=round(error_rate, 4),
        avg_replicas=round(avg_replicas, 2),
        max_replicas=round(max_replicas, 1),
        pod_churn=int(pod_churn),
        avg_mape_pct=round(mape, 2),
        avg_confidence=round(confidence, 4),
        cost_pod_hours=round(cost, 4),
        slo_violations=int(slo_violations),
    )


def run_load_test(scenario: str, duration_s: int):
    """Trigger Locust via REST API."""
    print(f"  [load] Starting {scenario} load test for {duration_s}s...")
    try:
        httpx.post(f"{LOCUST_URL}/swarm",
                   data={"user_count": 300, "spawn_rate": 20, "host": "http://frontend"},
                   timeout=10)
        time.sleep(duration_s)
        httpx.get(f"{LOCUST_URL}/stop", timeout=10)
    except Exception as e:
        print(f"  [warn] Locust control failed: {e} — assuming manual load")
        time.sleep(duration_s)


def run_experiment(args):
    results = []
    autoscalers = ["hpa", "keda", "phantom"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = f"research/data/{args.scenario}_{timestamp}"
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"PHANTOM Experiment: scenario={args.scenario}, runs={args.runs}")
    print(f"Output: {out_dir}")
    print(f"{'='*60}\n")

    for run_num in range(1, args.runs + 1):
        for autoscaler in autoscalers:
            run_id = f"{autoscaler}_run{run_num}"
            print(f"\n[Run {run_num}/{args.runs}] Autoscaler: {autoscaler.upper()}")

            apply_autoscaler(autoscaler)

            # Warm-up
            print("  [setup] Warming up 60s...")
            time.sleep(60)

            start_ts = time.time()
            run_load_test(args.scenario, args.duration)
            end_ts = time.time()

            result = collect_metrics(autoscaler, args.scenario, run_id,
                                     start_ts, end_ts, args.duration)
            results.append(result)

            print(f"  [result] P99={result.p99_latency_ms}ms "
                  f"err={result.error_rate_pct}% "
                  f"replicas_avg={result.avg_replicas} "
                  f"cost=${result.cost_pod_hours:.3f}")

            # Save per-run
            with open(f"{out_dir}/{run_id}.json", "w") as f:
                json.dump(asdict(result), f, indent=2)

            # Cool-down
            time.sleep(120)

    # Write aggregated CSV
    csv_path = f"{out_dir}/results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        writer.writerows([asdict(r) for r in results])

    print(f"\n[PHANTOM] Experiment complete. Results: {csv_path}")
    _print_summary(results)


def _print_summary(results):
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for autoscaler in ["hpa", "keda", "phantom"]:
        group = [r for r in results if r.autoscaler == autoscaler]
        if not group:
            continue
        avg_p99  = sum(r.p99_latency_ms for r in group) / len(group)
        avg_err  = sum(r.error_rate_pct for r in group) / len(group)
        avg_cost = sum(r.cost_pod_hours for r in group) / len(group)
        print(f"  {autoscaler.upper():10s} P99={avg_p99:.1f}ms  err={avg_err:.3f}%  cost=${avg_cost:.4f}/run")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run PHANTOM research experiments")
    parser.add_argument("--scenario", default="spike",
                        choices=["spike", "ramp", "periodic", "adversarial"])
    parser.add_argument("--duration", type=int, default=1800, help="Load test duration (seconds)")
    parser.add_argument("--runs",     type=int, default=3,    help="Runs per autoscaler")
    args = parser.parse_args()
    run_experiment(args)
