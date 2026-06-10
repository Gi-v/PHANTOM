# PHANTOM — Full Setup Guide

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Local Development (laptop)](#local-development-laptop)
3. [Running the Demo](#running-the-demo)
4. [Training the Model](#training-the-model)
5. [Running Research Experiments](#running-research-experiments)
6. [Cloud Deployment (AWS EKS)](#cloud-deployment-aws-eks)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Install these tools before starting:

```bash
# macOS
brew install k3d kubectl helm docker go python@3.12 terraform
pip3 install locust ruff

# Ubuntu/Debian
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
sudo snap install kubectl --classic
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
# Install Docker from docker.com
# Install Go from go.dev/dl
sudo apt install python3.12 python3-pip
pip3 install locust ruff
```

Verify versions:

```
k3d     >= 5.6     →  k3d version
kubectl >= 1.29    →  kubectl version --client
helm    >= 3.14    →  helm version
docker  >= 25      →  docker --version
go      >= 1.22    →  go version
python  >= 3.12    →  python3 --version
```

---

## Local Development (laptop)

### Step 1 — Clone the repository

```bash
git clone https://github.com/your-org/phantom
cd phantom
```

### Step 2 — Build Docker images

```bash
make build-images
```

This builds four images:
- `phantom/graph-builder:latest` — trace→graph service (Python/FastAPI)
- `phantom/ml-server:latest`     — GNN+LSTM serving (Python/FastAPI)
- `phantom/controller:latest`    — K8s controller (Go)
- `phantom/dashboard:latest`     — React frontend (Nginx)

Expected time: 3–5 minutes on first run (downloading base images).

### Step 3 — Create local Kubernetes cluster

```bash
make setup
```

This command:
1. Creates a k3d cluster named `phantom-local` with 3 agent nodes
2. Loads all PHANTOM images into the cluster (no registry needed)
3. Applies all Kubernetes base manifests (`kubectl apply -k kubernetes/base/`)
4. Installs the observability stack via Helm:
   - `kube-prometheus-stack` (Prometheus + Grafana)
   - `loki-stack` (Loki + Promtail)
   - `tempo` (distributed tracing)

Expected time: 5–8 minutes.

### Step 4 — Deploy PHANTOM

```bash
make deploy
```

This applies the dev overlay (`kubernetes/overlays/dev/`) which includes:
- `phantom-controller` Deployment + RBAC
- `phantom-ml` Deployment (ML serving)
- `graph-builder` Deployment
- `PredictiveScaler` CRs for frontend, checkout, payment, cart

Verify everything is running:

```bash
kubectl get pods -n phantom

# Expected output:
# NAME                                READY   STATUS    RESTARTS
# phantom-controller-xxx-yyy          1/1     Running   0
# phantom-ml-xxx-yyy                  1/1     Running   0
# graph-builder-xxx-yyy               1/1     Running   0
# grafana-xxx-yyy                     1/1     Running   0
# prometheus-xxx-yyy                  1/1     Running   0
# otel-collector-xxx-yyy              1/1     Running   0

kubectl get predictivescalers -n phantom

# Expected output:
# NAME               TARGET      CURRENT   PREDICTED   CONFIDENCE   PHASE
# frontend-scaler    frontend    2         2           0.00         Idle
# checkout-scaler    checkout    2         2           0.00         Idle
```

> **Note:** Confidence will be 0 until the Graph Builder has collected at least 12 graph snapshots (12 minutes). The model ships without a pre-trained checkpoint — see [Training the Model](#training-the-model) to train one, or skip to demo mode which uses the fallback predictor.

### Step 5 — Open the dashboard

```bash
make dashboard
```

Opens Grafana at **http://localhost:3000**

- Username: `admin`
- Password: `phantom`

Navigate to **Dashboards → PHANTOM — Predictive Autoscaler** to see the live dashboard.

For the React frontend:

```bash
cd dashboard && npm install && npm run dev
# Opens at http://localhost:3001
```

---

## Running the Demo

### Demo 1 — Cascade spike (the key demo)

Open two terminals.

Terminal 1 — watch scaling events:
```bash
kubectl get predictivescalers -n phantom -w
```

Terminal 2 — inject spike:
```bash
make load-spike
```

**What happens:**
1. Locust sends 10× traffic spike to `frontend`
2. OTel Collector captures traces → forwards to Tempo
3. Graph Builder rebuilds call graph (every 60s)
4. ML Service predicts checkout/payment/cart will receive cascaded load
5. Controller pre-scales those services 3–5 min before load arrives
6. Grafana dashboard shows: predicted RPS line rises → replica count rises → P99 stays flat

**In Grafana, look at:**
- **Panel: Predicted vs Actual RPS** — predicted line (dashed) leads actual by ~3 min
- **Panel: Replica Count** — PHANTOM target rises before CPU would trigger HPA
- **Panel: P99 Latency** — should stay under 200ms SLO

### Demo 2 — A/B comparison

```bash
make experiment-ab
```

Toggles PHANTOM on/off every 15 minutes under continuous load.

**In Grafana:** P99 latency is visibly lower during PHANTOM-on periods.

### Demo 3 — Baseline comparison

```bash
# Terminal 1 — activate HPA only
make experiment-hpa
make load-spike

# Wait 20 min, note P99 latency spike

# Terminal 1 — switch to PHANTOM
make experiment-phantom
make load-spike

# Compare P99 in Grafana
```

### Demo 4 — Show the call graph

```bash
kubectl port-forward svc/graph-builder 8000:8000 -n phantom &
curl http://localhost:8000/graph | python3 -m json.tool
```

You'll see the live weighted directed graph with RPS and latency on each edge.

### Demo 5 — Chaos injection

```bash
# Kill the checkout pod to simulate failure
kubectl delete pod -n phantom -l app=checkout

# Watch PHANTOM detect the topology change and adapt
kubectl logs -n phantom -l app=phantom-controller -f
```

---

## Training the Model

The model ships without a checkpoint. Train it on your own trace data.

### Step 1 — Collect trace data

Run the demo for at least 30 minutes while generating load:

```bash
# Terminal 1
make load-ramp

# Terminal 2 — export traces as training data
kubectl port-forward svc/graph-builder 8000:8000 -n phantom &

# Collect graph snapshots every 60s for 30 min
mkdir -p ml/data/traces
python3 - << 'EOF'
import time, json, httpx, pathlib

for i in range(30):
    r = httpx.get("http://localhost:8000/graph", timeout=5)
    if r.status_code == 200:
        data = r.json()
        pathlib.Path(f"ml/data/traces/snapshot_{i:03d}.json").write_text(
            json.dumps([{**data, "timestamp": time.time()}])
        )
    time.sleep(60)
    print(f"Collected snapshot {i+1}/30")
EOF
```

### Step 2 — Train

```bash
make ml-train
# or directly:
cd ml && python gnn_lstm/train.py \
    --data-dir data/traces/ \
    --output gnn_lstm/checkpoints/ \
    --epochs 100

# Expected output:
# [PHANTOM] Device: cpu
# Epoch   1/100  train=12.4231  val=11.8932  MAPE=28.41%
# Epoch  10/100  train=4.2103   val=3.9821   MAPE=15.23%
# Epoch  50/100  train=1.8432   val=1.7654   MAPE=10.87%
# Epoch 100/100  train=0.9821   val=0.9543   MAPE=8.34%
# [PHANTOM] Done. Best MAPE: 7.91%
```

### Step 3 — Load checkpoint into cluster

```bash
# Copy checkpoint to phantom-ml pod
POD=$(kubectl get pod -n phantom -l app=phantom-ml -o name | head -1)
kubectl cp ml/gnn_lstm/checkpoints/phantom_latest.pt \
    phantom/${POD#pod/}:/models/phantom_latest.pt

# Restart to load checkpoint
kubectl rollout restart deployment/phantom-ml -n phantom
```

### Step 4 — Evaluate

```bash
make ml-eval
# Reports MAPE, MAE, RMSE on held-out validation set
```

---

## Running Research Experiments

The full experiment compares PHANTOM, HPA, and KEDA across multiple scenarios.

### Step 1 — Set up experiment environment

```bash
# Ensure PHANTOM is deployed and model is loaded (see above)
# Start continuous load in background
cd load-testing && locust -f locustfile.py \
    --host http://localhost:8080 \
    --users 100 --spawn-rate 10 \
    --headless &
```

### Step 2 — Run automated experiment

```bash
python research/experiment.py \
    --scenario spike \
    --duration 1800 \
    --runs 3

# This runs:
#   - HPA baseline for 30min × 3 runs
#   - KEDA baseline for 30min × 3 runs
#   - PHANTOM for 30min × 3 runs
# Total: ~4.5 hours
# Output: research/data/spike_<timestamp>/results.csv
```

### Step 3 — Analyse results

```bash
python research/notebooks/analysis.py \
    --data-dir research/data/spike_<timestamp>/

# Generates:
#   pareto_frontier.png      — latency vs cost scatter
#   latency_comparison.png   — bar chart with error bars
#   cost_comparison.png      — pod-hours comparison
#   statistical_tests.json   — Wilcoxon p-values
```

### Step 4 — A/B automated toggle

```bash
# For continuous in-situ A/B without stopping load:
./scripts/ab-toggle.sh
# Toggles PHANTOM on/off every 15 min for 4 rounds
```

---

## Cloud Deployment (AWS EKS)

### Step 1 — Provision infrastructure

```bash
cd infra/terraform/environments/dev
terraform init
terraform plan    # review what will be created
terraform apply   # creates VPC + EKS cluster (~15 min)
```

### Step 2 — Configure kubectl

```bash
aws eks update-kubeconfig --region us-east-1 --name phantom-dev
kubectl get nodes  # should show 3 nodes
```

### Step 3 — Push images to registry

```bash
# Log in to GHCR (or ECR)
echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_USER --password-stdin

# Tag and push
docker tag phantom/controller:latest     ghcr.io/YOUR_ORG/phantom-controller:latest
docker tag phantom/ml-server:latest      ghcr.io/YOUR_ORG/phantom-ml-server:latest
docker tag phantom/graph-builder:latest  ghcr.io/YOUR_ORG/phantom-graph-builder:latest

docker push ghcr.io/YOUR_ORG/phantom-controller:latest
docker push ghcr.io/YOUR_ORG/phantom-ml-server:latest
docker push ghcr.io/YOUR_ORG/phantom-graph-builder:latest
```

### Step 4 — Update image references

```bash
# Update image tags in overlays
sed -i 's|phantom/controller:latest|ghcr.io/YOUR_ORG/phantom-controller:latest|g' \
    kubernetes/base/phantom-controller.yaml
# Repeat for other images
```

### Step 5 — Install ArgoCD

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f \
    https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Apply ApplicationSet
kubectl apply -f gitops/argocd/applicationset.yaml
```

### Step 6 — Deploy observability stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana              https://grafana.github.io/helm-charts
helm repo update

helm upgrade --install kube-prometheus prometheus-community/kube-prometheus-stack \
    -n monitoring --create-namespace

helm upgrade --install tempo grafana/tempo \
    -n monitoring -f observability/tempo-values.yaml

kubectl apply -k kubernetes/overlays/prod/
```

---

## Troubleshooting

### Controller not scaling

```bash
# Check controller logs
kubectl logs -n phantom -l app=phantom-controller --tail=50

# Check PredictiveScaler status
kubectl describe predictivescaler checkout-scaler -n phantom

# Check ML service is healthy
kubectl port-forward svc/phantom-ml 8001:8001 -n phantom &
curl http://localhost:8001/health
# {"status":"ok","model_loaded":false,"graph_nodes":0}
# model_loaded=false means no checkpoint — train the model first
```

### Confidence always 0 / model in dev-fallback mode

The ML service returns `confidence: 0.5` (fallback) when either:
- No checkpoint at `/models/phantom_latest.pt` → train the model (see above)
- Graph Builder has < 12 snapshots → wait 12 minutes after deploy

```bash
curl http://localhost:8001/health
# graph_nodes: 0  means graph builder hasn't connected to Tempo yet
kubectl logs -n phantom -l app=graph-builder --tail=30
```

### Graph is empty (0 nodes)

The Graph Builder queries Tempo for traces. If no services are instrumented:

```bash
# Check Tempo is receiving traces
kubectl port-forward svc/tempo 3200:3200 -n monitoring &
curl http://localhost:3200/ready
# Should return "ready"

# Check OTel Collector is forwarding
kubectl logs -n phantom -l app=otel-collector --tail=20
```

If running Online Boutique, confirm it has OTel auto-instrumentation.  
Add to each service Deployment:
```yaml
env:
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: "http://otel-collector:4318"
- name: OTEL_SERVICE_NAME
  value: "frontend"  # change per service
```

### Pods stuck in Pending

```bash
kubectl describe pod <pending-pod> -n phantom
# Usually: insufficient CPU/memory on nodes
# For k3d: increase agent resources
k3d cluster create phantom-local --agents 3 \
    --agents-memory 4096m \
    --agents-cpus 2
```

### Grafana shows no data

```bash
# Check Prometheus can reach phantom-ml metrics
kubectl port-forward svc/prometheus 9090:9090 -n phantom &
# Open http://localhost:9090/targets
# Look for phantom-services — should be UP
```

---

## Environment Variables Reference

| Service | Variable | Default | Description |
|---|---|---|---|
| phantom-ml | `GRAPH_BUILDER_URL` | `http://graph-builder:8000` | Graph builder endpoint |
| phantom-ml | `MODEL_CHECKPOINT` | `/models/phantom_latest.pt` | Path to model checkpoint |
| phantom-ml | `PREDICTION_HORIZON` | `300` | Seconds ahead to predict |
| phantom-ml | `HISTORY_WINDOW` | `12` | Graph snapshots in sequence |
| phantom-ml | `MODEL_N_ENSEMBLE` | `5` | Number of ensemble models |
| graph-builder | `TEMPO_URL` | `http://tempo:3200` | Tempo HTTP API |
| graph-builder | `PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus for replica counts |
| graph-builder | `REBUILD_INTERVAL` | `60` | Seconds between graph rebuilds |
| controller | `--phantom-ml-url` | `http://phantom-ml:8001` | ML service endpoint |
| controller | `--leader-elect` | `false` | Enable for multi-replica controller |
