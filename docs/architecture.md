# PHANTOM — System Architecture

## Design Goals

1. **Novelty**: Use distributed trace topology as prediction signal — not CPU/memory
2. **Safety**: Always fall back to HPA when model confidence is low
3. **Observability**: Every decision logged, metriced, and visible in Grafana
4. **Reproducibility**: All infra in Git, all experiments scripted

---

## Component Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster (phantom ns)                │
│                                                                      │
│  ┌──────────┐   ┌──────────┐   ┌─────────┐   ┌──────────────────┐  │
│  │ frontend │──▶│ checkout │──▶│ payment │   │      cart        │  │
│  └────┬─────┘   └────┬─────┘   └─────────┘   └──────────────────┘  │
│       │              │                                               │
│       └──────────────┴─────────── OTel auto-instrumentation ────────┤
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    OTel Collector                              │  │
│  │   receivers: otlp/grpc + otlp/http                            │  │
│  │   exporters: tempo (traces), prometheus (metrics), loki (logs)│  │
│  └────────┬───────────────────────────┬──────────────────────────┘  │
│           │ traces                     │ metrics                      │
│           ▼                            ▼                              │
│  ┌─────────────┐              ┌──────────────────┐                  │
│  │    Tempo     │              │    Prometheus     │                  │
│  │  (trace DB)  │              │  (metrics TSDB)   │                  │
│  └──────┬──────┘              └────────┬──────────┘                  │
│         │ TraceQL (every 60s)          │ PromQL                       │
│         ▼                              ▼                              │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     Graph Builder                             │   │
│  │  • Queries Tempo: spans grouped by (caller, callee)           │   │
│  │  • Computes per-edge: RPS, P99 latency, error rate            │   │
│  │  • Queries Prometheus: replica counts per service             │   │
│  │  • Builds NetworkX DiGraph, prunes edges < 0.1 RPS            │   │
│  │  • Exposes: GET /graph → JSON snapshot                        │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │ graph JSON (every 60s)                 │
│                              ▼                                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   GNN+LSTM ML Service                         │   │
│  │                                                               │   │
│  │  Background: polls /graph every 60s → history buffer [W=12]  │   │
│  │                                                               │   │
│  │  Inference per request:                                       │   │
│  │    1. Build GraphSnapshot [N, 4] node features                │   │
│  │    2. GraphSAGEEncoder × 2 layers → [N, 64] embeddings        │   │
│  │    3. Stack W=12 snapshots → [N, 12, 64] temporal sequence    │   │
│  │    4. LSTM(128 hidden, 2 layers) → [N, 128] last state        │   │
│  │    5. MLP + Softplus → predicted RPS [N]                      │   │
│  │    6. Repeat × 5 ensemble models → confidence [N]             │   │
│  │                                                               │   │
│  │  Exposes: GET /predict/{service}?namespace=&horizon=          │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │ {predicted_rps, confidence, ...}       │
│                              ▼ (every 30s per PredictiveScaler)      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  PHANTOM Controller (Go)                      │   │
│  │                                                               │   │
│  │  Reconcile loop for each PredictiveScaler CR:                 │   │
│  │    1. Fetch target Deployment                                 │   │
│  │    2. Query ML service for prediction                         │   │
│  │    3. Confidence gate: if conf < 0.75 → skip (HPA in control) │   │
│  │    4. Cooldown check: no scale-down within 2 min              │   │
│  │    5. Compute: replicas = ceil(predicted_rps / rps_per_rep    │   │
│  │                               × buffer_1.2)                  │   │
│  │    6. Patch Deployment.spec.replicas                          │   │
│  │    7. Single Status().Patch (no version conflicts)            │   │
│  │                                                               │   │
│  │  Emits Prometheus metrics:                                    │   │
│  │    phantom_scale_actions_total{deployment, direction}         │   │
│  │    phantom_target_replicas{deployment}                        │   │
│  │    phantom_prediction_confidence{deployment}                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌─────────────────┐  ┌──────────┐  ┌─────────┐  ┌─────────────┐  │
│  │    Grafana       │  │   Loki   │  │ Kyverno │  │    Falco    │  │
│  │  dashboards      │  │  (logs)  │  │policies │  │  (runtime)  │  │
│  └─────────────────┘  └──────────┘  └─────────┘  └─────────────┘  │
└──────────────────────────────────────────────────────────────────────┘

External:
  GitHub ──(push)──▶ GitHub Actions ──(build+scan)──▶ GHCR
                                               │
                                               ▼
                                           ArgoCD ──(sync)──▶ cluster
```

---

## Data Flow: Trace to Scale Decision

```
t=0s   : user traffic hits frontend
t=1s   : OTel auto-instrumentation emits spans to OTel Collector
t=2s   : Collector exports traces to Tempo, metrics to Prometheus
t=60s  : Graph Builder runs TraceQL query against Tempo
t=60s  : Graph Builder queries Prometheus for replica counts
t=60s  : Graph Builder updates /graph endpoint with new snapshot
t=60s  : ML Service background task fetches new snapshot
t=60s  : ML Service appends to 12-step history buffer
t=90s  : Controller reconcile fires for each PredictiveScaler CR
t=90s  : Controller calls GET /predict/checkout?horizon=300
t=90s  : ML Service runs GraphSAGE → LSTM → ensemble → returns {rps: 847, confidence: 0.91}
t=90s  : Controller: ceil(847/100 × 1.2) = 11 replicas; current = 3
t=90s  : Controller patches checkout Deployment.spec.replicas = 11
t=90s  : Kubernetes schedules 8 new checkout pods
t=120s : New pods ready — serving traffic
t=300s : Predicted spike arrives at checkout — already scaled
```

**Without PHANTOM:** spike arrives → CPU crosses threshold → HPA fires → pods schedule → ready ~90s later → 90s of SLO violations.

---

## CRD: PredictiveScaler

```yaml
apiVersion: phantom.io/v1alpha1
kind: PredictiveScaler
metadata:
  name: checkout-scaler
spec:
  targetDeployment: checkout    # Deployment to control
  minReplicas: 2
  maxReplicas: 15
  predictionHorizonSeconds: 240 # How far ahead to predict
  confidenceThreshold: "0.70"   # Below this → HPA takes over
  scaleUpBuffer: "1.3"          # 30% headroom over predicted load
status:
  currentReplicas: 3
  predictedReplicas: 11
  modelConfidence: 0.91
  phase: Scaling                # Idle|Predicting|Scaling|Stable|Error
  lastScaleAction: "2024-06-01T09:41:02Z"
  message: "predicted 847 RPS → 11 replicas (conf 0.91)"
```

---

## GraphSAGEEncoder — Tensor Flow

```
Input snapshot:
  node_features : [N, 4]   (rps, p99, error_rate, replicas)
  edge_index    : [2, E]   COO format
  edge_attr     : [E, 3]   (weight, p99_latency, error_rate)

Step 1 — Edge fusion:
  edge_emb = Linear(3→4)(edge_attr)         [E, 4]
  x = x + scatter_add(edge_emb, src_nodes)  [N, 4]

Step 2 — SAGEConv layer 1:
  h1 = ReLU(LayerNorm(SAGEConv(4→64)(x, edge_index)))   [N, 64]
  h1 = Dropout(0.1)(h1)

Step 3 — SAGEConv layer 2:
  h2 = LayerNorm(SAGEConv(64→64)(h1, edge_index))        [N, 64]

Output: h2  [N, 64]
```

## LSTM Temporal Model — Tensor Flow

```
Input: stack of W=12 GNN outputs    [N, 12, 64]

LSTM(input=64, hidden=128, layers=2, batch_first=True)
  → lstm_out  [N, 12, 128]
  → last      [N, 128]        (final timestep)

MLP head:
  Linear(128→64) → ReLU → Dropout(0.1) → Linear(64→1) → Softplus
  → rps  [N]    (non-negative guaranteed by Softplus)

Ensemble (×5 models):
  stack  [5, N]
  mean   [N]
  std    [N]
  confidence = 1 - clamp(std / (mean + 1e-6), 0, 1)  [N]
```

---

## Security Architecture

```
Shift-left (CI):
  trivy fs .              → secrets + misconfig scan
  trivy image <img>       → CVE scan after build
  kubeconform             → manifest schema validation

Admission (Kubernetes):
  Kyverno: require-resource-limits    → no unbounded pods
  Kyverno: disallow-privilege-escalation
  Kyverno: require-trivy-annotation   → only scanned images deploy

Runtime (cluster):
  Falco: ML model file tampered       → CRITICAL alert
  Falco: unexpected exec in controller → ERROR alert

Secrets:
  Vault with Kubernetes auth          → dynamic short-lived secrets
  No secrets in Git (Vault policy enforced)
```

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| GNN type | GraphSAGE (inductive) | Handles new services without retraining; GAT requires fixed node set |
| Uncertainty | Ensemble variance (5 models) | Computationally cheap, well-calibrated; MC Dropout needs multiple passes |
| Trace backend | Tempo + TraceQL | Structural queries return pre-aggregated service graphs in one API call |
| Controller framework | controller-runtime | Minimal abstraction over K8s reconcile loop; easy to read for research |
| Status update | Single `Status().Patch` | Prevents resource version conflict from multiple intermediate updates |
| Confidence fallback | Pass control to HPA | Safe degradation; PHANTOM never removes HPA, just overrides replicas |
| Edge pruning | < 0.1 RPS | Removes noisy health-check edges that pollute graph topology |
