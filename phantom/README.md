<div align="center">

# PHANTOM
### Predictive Horizontal Auto-scaling via Neural Time-series for Microservice Orchestration

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![Go 1.22](https://img.shields.io/badge/Go-1.22-00ADD8.svg)](https://golang.org)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-1.29-326CE5.svg)](https://kubernetes.io)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.3-EE4C2C.svg)](https://pytorch.org)

**The first open-source Kubernetes autoscaler that derives its prediction signal from live distributed trace topology — not CPU metrics.**

[Quick Start](#quick-start) · [How It Works](#how-it-works) · [Research](#research-novelty) · [Setup Guide](#full-setup-guide) · [Architecture](docs/architecture.md)

</div>

---

## The Problem

Standard Kubernetes HPA scales *after* CPU spikes. By then, your P99 latency SLO is already breached.

Existing predictive autoscalers (KEDA, Autopilot) treat each service independently — they miss the cascade: a spike on `frontend` propagates through `checkout → payment → cart` in a **predictable, topology-driven pattern**. No open-source system exploits this.

## The Solution

PHANTOM builds a **live weighted call graph** from OpenTelemetry distributed traces every 60 seconds. A **GraphSAGE + LSTM ensemble** predicts per-service RPS 2–5 minutes ahead using both temporal history and graph topology. A **Go Kubernetes controller** pre-scales downstream services before the wave arrives.

```
User Traffic Spike
      │
      ▼  (detected immediately)
  frontend  ──traces──▶  OTel Collector
      │                        │
      │                        ▼
      │                  Graph Builder
      │                  (NetworkX DAG)
      │                        │
      │                        ▼
      │                  GNN+LSTM Model
      │                  (5-model ensemble)
      │                        │
      │                        ▼
      │                  K8s Controller
      │                  (patches replicas)
      │                        │
      ▼  (pre-scaled 3-5 min ahead)
  checkout, payment, cart — already scaled up
```

## Research Novelty

> **Research gap:** All existing predictive autoscalers model services independently. None uses the service call graph as a prediction signal.

**PHANTOM's contribution:**
1. Live trace-derived weighted DAG reconstructed from Tempo TraceQL queries
2. GraphSAGE encoder aggregates neighbourhood topology at each timestep
3. Per-node LSTM learns temporal load patterns across 12-step history window
4. 5-model ensemble provides calibrated confidence scores
5. Controller confidence gate falls back to HPA when model uncertainty is high

**Paper:** *"Topology-Aware Predictive Autoscaling via Trace-Derived Service Dependency Graphs"*  
**Venue targets:** EuroSys, SoCC, ICPE, IEEE TNSM

**Research questions:**
- RQ1: Does topology-aware prediction reduce P99 latency vs reactive HPA under cascade load?
- RQ2: What prediction horizon maximises benefit before accuracy degrades?
- RQ3: Does pre-scaling reduce over-provisioning vs naive predictive scaling?

## How It Works

### 1 — Graph Builder (`ml/graph_builder/`)
Queries Tempo's TraceQL API every 60s with a 10-minute lookback window.  
For each `(caller, callee)` span pair, computes edge weight (RPS), P99 latency, error rate.  
Outputs a JSON graph: `{nodes:[{id, rps, p99, error_rate, replicas}], edges:[{source, target, weight, ...}]}`

### 2 — GNN+LSTM Model (`ml/gnn_lstm/`)
- **GraphSAGEEncoder**: 2-layer message passing, aggregates edge features into node embeddings `[N, 64]`
- **LSTM**: processes 12-step sequence of node embeddings → `[N, 128]` temporal state
- **MLP + Softplus head**: outputs non-negative predicted RPS per node
- **Ensemble**: 5 independent models — confidence = `1 - std/(mean+ε)`

### 3 — Kubernetes Controller (`controller/`)
Custom `controller-runtime` reconciler watching `PredictiveScaler` CRDs.  
Every 30s: queries ML service → confidence gate (τ=0.75) → compute replicas → patch Deployment.  
Cooldown (2 min) prevents thrashing. Single `Status().Patch` call avoids resource version conflicts.

### 4 — Observability
Full LGTM stack: Prometheus scrapes all services, Grafana shows prediction vs actual overlay, Tempo stores distributed traces, Loki aggregates logs — all wired via OpenTelemetry Collector.

## Stack

| Layer | Tool | Why |
|---|---|---|
| Orchestration | Kubernetes (K3s local → EKS prod) | K3s for laptop dev, EKS for real experiments |
| GitOps | ArgoCD | ApplicationSet for multi-env, best demo UI |
| CI/CD | GitHub Actions + Trivy | Free tier, OIDC keyless auth, SARIF security reports |
| Tracing | OpenTelemetry + Tempo | Tempo TraceQL enables structural graph queries |
| Metrics | Prometheus + Grafana | Industry standard, ServiceMonitor CRD |
| Logs | Loki + Promtail | Correlated with traces via derived fields |
| ML | PyTorch + PyTorch Geometric | GraphSAGE in PyG, standard LSTM in PyTorch |
| Controller | Go + controller-runtime | Production-grade K8s reconciler pattern |
| Security | Kyverno + Falco + Vault | Policy admission + runtime detection + secrets |
| Infra | Terraform | EKS module, remote state in S3 |

## Project Structure

```
phantom/
├── controller/                 # Go Kubernetes controller
│   ├── api/v1alpha1/          # PredictiveScaler CRD types
│   ├── cmd/controller/        # main.go entrypoint
│   └── internal/
│       ├── controller/        # Reconcile loop
│       ├── predictor/         # HTTP client to ML service
│       └── scaler/            # Deployment patch + Prometheus metrics
├── ml/
│   ├── graph_builder/         # Trace → NetworkX DAG service
│   └── gnn_lstm/              # Model, training, serving, evaluation
├── kubernetes/
│   ├── base/                  # Kustomize base manifests + CRD + ConfigMaps
│   ├── overlays/dev|prod/     # Environment-specific patches
│   ├── experiments/           # HPA / KEDA / PHANTOM experiment manifests
│   └── helm/phantom-controller/ # Helm chart for controller
├── observability/             # Prometheus, Grafana dashboards, OTel, Tempo configs
├── security/                  # Kyverno policies, Falco rules, Vault setup
├── gitops/argocd/             # ApplicationSet definitions
├── infra/terraform/           # EKS provisioning
├── research/
│   ├── baselines/             # HPA + KEDA baseline manifests
│   ├── loadtest/              # Locust load scenarios (spike, ramp, periodic)
│   ├── notebooks/             # Statistical analysis + Pareto plots
│   ├── experiment.py          # Automated experiment runner
│   └── paper/phantom.tex      # LaTeX paper (ACM sigconf)
├── load-testing/              # Locust files for make load-spike
├── dashboard/                 # React frontend
├── docs/
│   ├── architecture.md        # Full system design
│   ├── runbook.md             # Operations guide
│   └── setup-guide.md        # Step-by-step setup
└── scripts/ab-toggle.sh       # A/B PHANTOM on/off experiment script
```

## Quick Start

```bash
# 1. Clone
git clone https://github.com/your-org/phantom && cd phantom

# 2. Build all Docker images
make build-images

# 3. Create local cluster + deploy everything
make setup    # creates k3d cluster, installs observability stack
make deploy   # deploys PHANTOM + Online Boutique demo app

# 4. Open Grafana dashboard
make dashboard    # http://localhost:3000  (admin / phantom)

# 5. Trigger cascade demo
make load-spike   # 10× traffic spike → watch PHANTOM pre-scale
```

See [docs/setup-guide.md](docs/setup-guide.md) for the full step-by-step guide.

## Demo Scenarios

| Scenario | Command | What you see |
|---|---|---|
| Cascade spike | `make load-spike` | PHANTOM pre-scales checkout/payment before CPU rises |
| A/B comparison | `make experiment-ab` | Toggle PHANTOM on/off, watch P99 latency difference |
| HPA baseline | `make experiment-hpa` | Reactive scaling, latency spikes during ramp-up |
| KEDA baseline | `make experiment-keda` | Metric-driven scaling, no topology awareness |
| Full experiment | `make experiment-full` | 3 runs × 3 autoscalers, produces research CSV |
| Train model | `make ml-train` | Train GNN+LSTM on trace data |

## Research Metrics

| Metric | Target |
|---|---|
| Prediction MAPE | < 15% at 5-min horizon |
| P99 latency reduction vs HPA | ≥ 20% under spike load |
| Pod-hours overhead | ≤ 5% vs HPA |
| Model confidence calibration | Wilcoxon p < 0.05 |

## License

MIT — see [LICENSE](LICENSE)
