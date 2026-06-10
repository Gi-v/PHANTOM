# PHANTOM Runbook

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| k3d  | ≥ 5.6   | `brew install k3d` |
| kubectl | ≥ 1.29 | `brew install kubectl` |
| helm | ≥ 3.14 | `brew install helm` |
| docker | ≥ 25 | docker.com |
| python | ≥ 3.12 | `brew install python` |
| go | ≥ 1.22 | `brew install go` |
| terraform | ≥ 1.7 | `brew install terraform` |
| locust | ≥ 2.24 | `pip install locust` |

## Quickstart (local, k3d)

```bash
# 1. Clone
git clone https://github.com/phantom-io/phantom && cd phantom

# 2. Build images
docker build -t phantom/controller:latest controller/
docker build -t phantom/ml-server:latest ml/gnn_lstm/
docker build -t phantom/graph-builder:latest ml/graph_builder/

# 3. Create cluster + deploy everything
make setup      # creates k3d cluster, installs observability stack
make deploy     # deploys Online Boutique + PHANTOM components

# 4. Open dashboard
make dashboard  # Grafana at http://localhost:3000 (admin/phantom)

# 5. Run load test
make load-spike  # 10× traffic spike to trigger cascade prediction

# 6. Watch PHANTOM pre-scale
kubectl get predictivescalers -n phantom -w
```

## Running Research Experiments

```bash
# Run full experiment (HPA vs KEDA vs PHANTOM, 3 runs each)
python research/experiment.py --scenario spike --duration 1800 --runs 3

# Analyse results
python research/notebooks/analysis.py \
  --data-dir research/data/spike_<timestamp>

# Plots saved to research/data/spike_<timestamp>/plots/
```

## Deploying to AWS EKS

```bash
cd infra/terraform/environments/dev
terraform init
terraform apply   # provisions VPC + EKS cluster

# Configure kubectl
aws eks update-kubeconfig --region us-east-1 --name phantom-dev

# Deploy via ArgoCD
kubectl apply -f gitops/argocd/applicationset.yaml
```

## Troubleshooting

### Controller not scaling
```bash
kubectl logs -n phantom -l app=phantom-controller --tail=50
kubectl describe predictivescaler frontend-scaler -n phantom
```
Check: ML service healthy? `kubectl get pods -n phantom`
Check: confidence above threshold? See `phantom_model_confidence` metric in Grafana.

### Graph is empty
```bash
kubectl logs -n phantom -l app=graph-builder --tail=50
```
Check: Tempo receiving traces? `kubectl port-forward svc/tempo 3200:3200 -n monitoring`
then `curl http://localhost:3200/ready`

Check: Services instrumented? Look for `OTEL_EXPORTER_OTLP_ENDPOINT` env var on app pods.

### Model in dev-mode fallback
No checkpoint found at `/models/phantom_latest.pt`.
Either train a model: `make ml-train`
Or copy a pre-trained checkpoint into the pod:
```bash
kubectl cp ml/gnn_lstm/checkpoints/phantom_latest.pt \
  phantom/$(kubectl get pod -n phantom -l app=phantom-ml -o name | head -1 | cut -d/ -f2):/models/
```

## Metric Reference

| Metric | Description |
|--------|-------------|
| `phantom_predicted_rps{service}` | ML prediction for next horizon |
| `phantom_model_confidence{service}` | Ensemble confidence [0,1] |
| `phantom_target_replicas{deployment}` | Replicas set by controller |
| `phantom_scale_actions_total{deployment,direction}` | Pre-scale event counter |
| `phantom_prediction_mape{deployment}` | Rolling MAPE of predictions |
| `phantom_prediction_latency_seconds` | ML inference latency histogram |
