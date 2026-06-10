.PHONY: help setup deploy dashboard load-spike load-ramp clean \
        experiment-hpa experiment-keda experiment-phantom experiment-ab \
        ml-train ml-eval ml-serve \
        build-images scan lint

CLUSTER_NAME ?= phantom-local
GRAFANA_PORT ?= 3000
LOCUST_USERS ?= 50
NS           ?= phantom

help:
	@echo ""
	@echo "  PHANTOM — Predictive Autoscaler"
	@echo ""
	@echo "  Setup"
	@echo "    make setup          Create k3d cluster + install all infra"
	@echo "    make build-images   Build all Docker images"
	@echo "    make deploy         Deploy PHANTOM to cluster"
	@echo ""
	@echo "  Observability"
	@echo "    make dashboard      Open Grafana (admin/phantom)"
	@echo "    make traces         Port-forward Tempo UI"
	@echo ""
	@echo "  Load testing"
	@echo "    make load-spike     10x traffic spike (key demo)"
	@echo "    make load-ramp      Gradual ramp scenario"
	@echo ""
	@echo "  Experiments"
	@echo "    make experiment-hpa      Activate HPA baseline"
	@echo "    make experiment-keda     Activate KEDA baseline"
	@echo "    make experiment-phantom  Activate PHANTOM"
	@echo "    make experiment-ab       Toggle PHANTOM on/off every 15min"
	@echo ""
	@echo "  ML"
	@echo "    make ml-train       Train GNN+LSTM model"
	@echo "    make ml-eval        Evaluate checkpoint"
	@echo "    make ml-serve       Run ML serving locally"
	@echo ""
	@echo "  Infra"
	@echo "    make scan           Trivy image + cluster scan"
	@echo "    make clean          Delete local cluster"
	@echo ""

# ── Build ─────────────────────────────────────────────────────────────────────
build-images:
	docker build -t phantom/graph-builder:latest -f ml/graph_builder/Dockerfile ml/
	docker build -t phantom/ml-server:latest     -f ml/gnn_lstm/Dockerfile ml/
	docker build -t phantom/controller:latest    controller/
	docker build -t phantom/dashboard:latest     dashboard/

# ── Cluster setup ─────────────────────────────────────────────────────────────
setup:
	@echo "==> Creating k3d cluster..."
	k3d cluster create $(CLUSTER_NAME) \
		--agents 3 \
		--port "8080:80@loadbalancer" \
		--port "3000:3000@loadbalancer"
	@echo "==> Loading images into cluster..."
	k3d image import phantom/graph-builder:latest -c $(CLUSTER_NAME)
	k3d image import phantom/ml-server:latest     -c $(CLUSTER_NAME)
	k3d image import phantom/controller:latest    -c $(CLUSTER_NAME)
	@echo "==> Deploying core manifests..."
	kubectl apply -k kubernetes/base/
	@echo "==> Installing observability stack..."
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
	helm repo add grafana              https://grafana.github.io/helm-charts
	helm repo update
	helm upgrade --install kube-prometheus prometheus-community/kube-prometheus-stack \
		-n monitoring --create-namespace \
		-f observability/prometheus-values.yaml
	helm upgrade --install loki grafana/loki-stack \
		-n monitoring --set grafana.enabled=false
	helm upgrade --install tempo grafana/tempo \
		-n monitoring -f observability/tempo-values.yaml
	@echo "==> Done. Run 'make deploy' next."

# ── Application deploy ────────────────────────────────────────────────────────
deploy:
	kubectl apply -k kubernetes/overlays/dev/
	@echo "==> Waiting for PHANTOM pods..."
	kubectl rollout status deployment/phantom-controller -n $(NS) --timeout=120s
	kubectl rollout status deployment/phantom-ml         -n $(NS) --timeout=180s
	kubectl rollout status deployment/graph-builder      -n $(NS) --timeout=120s

# ── Observability ─────────────────────────────────────────────────────────────
dashboard:
	@echo "==> Grafana → http://localhost:$(GRAFANA_PORT)  (admin / phantom)"
	kubectl port-forward svc/grafana $(GRAFANA_PORT):3000 -n $(NS)

traces:
	@echo "==> Tempo → http://localhost:3200"
	kubectl port-forward svc/tempo 3200:3200 -n monitoring

# ── Load testing ──────────────────────────────────────────────────────────────
load-spike:
	@echo "==> Injecting 10× traffic spike..."
	cd load-testing && SCENARIO=spike locust -f locustfile.py \
		--host http://localhost:8080 \
		--users 500 --spawn-rate 50 --run-time 5m \
		--headless --csv ../research/data/spike_$$(date +%Y%m%d_%H%M%S)

load-ramp:
	@echo "==> Ramp load test..."
	cd load-testing && SCENARIO=ramp locust -f locustfile.py \
		--host http://localhost:8080 \
		--users 300 --spawn-rate 5 --run-time 20m \
		--headless --csv ../research/data/ramp_$$(date +%Y%m%d_%H%M%S)

# ── Research experiments ──────────────────────────────────────────────────────
experiment-hpa:
	kubectl apply -f kubernetes/experiments/hpa-only.yaml
	kubectl delete predictivescalers --all -n $(NS) 2>/dev/null || true
	@echo "==> HPA baseline active."

experiment-keda:
	kubectl apply -f kubernetes/experiments/keda-baseline.yaml
	kubectl delete predictivescalers --all -n $(NS) 2>/dev/null || true
	@echo "==> KEDA baseline active."

experiment-phantom:
	kubectl apply -f kubernetes/experiments/phantom-full.yaml
	@echo "==> PHANTOM predictive scaling active."

experiment-ab:
	@echo "==> A/B toggle PHANTOM on/off every 15 min..."
	./scripts/ab-toggle.sh

experiment-full:
	python research/experiment.py --scenario spike --duration 1800 --runs 3

# ── ML pipeline ───────────────────────────────────────────────────────────────
ml-train:
	cd ml && python gnn_lstm/train.py \
		--data-dir data/traces/ \
		--output gnn_lstm/checkpoints/ \
		--epochs 100

ml-eval:
	cd ml && python gnn_lstm/evaluate.py \
		--checkpoint gnn_lstm/checkpoints/phantom_latest.pt \
		--data-dir data/traces/

ml-serve:
	cd ml && uvicorn gnn_lstm.serve:app --port 8001 --reload

# ── Security ──────────────────────────────────────────────────────────────────
scan:
	trivy image phantom/controller:latest
	trivy image phantom/ml-server:latest
	trivy image phantom/graph-builder:latest

lint:
	cd ml && python -m ruff check .
	cd research && python -m ruff check .

# ── Teardown ──────────────────────────────────────────────────────────────────
clean:
	k3d cluster delete $(CLUSTER_NAME)
	docker rmi phantom/controller phantom/ml-server phantom/graph-builder phantom/dashboard 2>/dev/null || true

# ── GitHub Codespaces (4-core) ────────────────────────────────────────────────
codespaces-start:
	@echo "==> Starting PHANTOM in Codespaces (docker-compose mode)..."
	@echo "==> Note: K8s controller runs outside compose — use 'make codespaces-k3d' for full stack"
	docker compose up -d prometheus grafana loki tempo otel-collector
	@echo "==> Waiting 15s for observability stack..."
	sleep 15
	docker compose up -d graph-builder
	@echo "==> Waiting 20s for graph-builder..."
	sleep 20
	docker compose up -d phantom-ml
	@echo "==> Waiting 20s for phantom-ml..."
	sleep 20
	docker compose up -d dashboard
	@echo ""
	@echo "==> PHANTOM running:"
	@echo "    Grafana:    http://localhost:3000  (admin/phantom)"
	@echo "    Dashboard:  http://localhost:3001"
	@echo "    ML API:     http://localhost:8001/health"
	@echo "    Graph:      http://localhost:8000/graph"
	@echo "    Prometheus: http://localhost:9090"

codespaces-k3d:
	@echo "==> Full Codespaces setup with k3d..."
	k3d cluster create phantom-local \
		--agents 2 \
		--agents-memory 1536m \
		--servers-memory 1536m \
		--no-lb
	$(MAKE) build-images
	k3d image import phantom/graph-builder:latest -c phantom-local
	k3d image import phantom/ml-server:latest     -c phantom-local
	k3d image import phantom/controller:latest    -c phantom-local
	kubectl apply -k kubernetes/base/
	kubectl apply -k kubernetes/overlays/dev/
	@echo "==> Done. Run 'make dashboard' to port-forward Grafana."

codespaces-stop:
	docker compose down

codespaces-logs:
	docker compose logs -f phantom-ml graph-builder

codespaces-status:
	@echo "=== Docker Compose Services ==="
	docker compose ps
	@echo ""
	@echo "=== ML Service Health ==="
	curl -s http://localhost:8001/health | python3 -m json.tool || echo "ML service not ready"
	@echo ""
	@echo "=== Graph Builder Health ==="
	curl -s http://localhost:8000/health | python3 -m json.tool || echo "Graph builder not ready"
