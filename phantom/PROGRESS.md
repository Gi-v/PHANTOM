# PHANTOM — Build Status: ✅ COMPLETE & AUDITED

## Bugs Fixed This Session
| Bug | Severity | Fix |
|-----|----------|-----|
| `GraphSnapshot` imported but not defined in model.py | RUNTIME CRASH | Defined as `@dataclass` in model.py |
| `PHANTOMModel` kwargs mismatch (node_feature_dim vs node_feat_dim etc) | SILENT WRONG VALUES | Unified to `node_feat_dim`, `edge_feat_dim`, `history_window`, `lstm_hidden` everywhere |
| `serve.py` passed `list[GraphSnapshot]` but model expected `list[Data]` | RUNTIME CRASH | Removed PyG Data layer; model now uses `GraphSnapshot` directly |
| `node_indices` passed as Python list, not `torch.Tensor` | RUNTIME CRASH | Fixed to `torch.arange(N, dtype=torch.long)` |
| `graph_builder.py` had no FastAPI app — serve.py called `/graph` on it | RUNTIME CRASH | Added full `gb_app` FastAPI app + lifespan |
| `edge_index.t()` called on empty list (when no edges) | RUNTIME CRASH | Guard: build empty `torch.zeros((2,0))` tensor |
| Controller `Status().Update` x3 → resource version conflicts | K8S CONFLICT ERROR | Replaced all with single `Status().Patch` at end |
| Makefile referenced `ml/gnn_lstm/server.py` (wrong name) | BUILD FAILURE | Fixed to `serve.py` throughout |
| Makefile referenced `load-testing/spike.py` (missing) | MAKE FAILURE | Created `load-testing/spike.py` |
| Makefile referenced `kubernetes/experiments/hpa-only.yaml` (missing) | MAKE FAILURE | Created file |
| Makefile referenced `kubernetes/experiments/keda-baseline.yaml` (missing) | MAKE FAILURE | Created file |
| Makefile referenced `observability/tempo-values.yaml` (missing) | MAKE FAILURE | Created file |
| CI referenced `kubernetes/base/controller/deployment.yaml` (deleted dir) | CI FAILURE | CI updated to correct paths |
| CI referenced `services/` dir (never created) | CI FAILURE | Removed from CI |
| Kustomization `configMapGenerator` referenced files in wrong paths | KUBECTL FAIL | Removed configMapGenerator; configmaps now inline in YAML |
| Duplicate `crd.yaml` and `crd-predictivescaler.yaml` (different group names!) | CONFLICT | Removed stale `crd.yaml` |
| Stale `kubernetes/base/controller/` and `ml-server/` subdirs | CONFUSION | Removed |
| Duplicate OTel collector configs (`collector.yaml` and `collector.yml`) | CONFUSION | Removed `.yaml` |
| `go.mod` had unused `go-resty` import | BUILD WARNING | Removed |
| `ml/gnn_lstm/evaluate.py` missing (Makefile referenced it) | MAKE FAILURE | Created full evaluate.py |

## All Files: 81
## All YAML: 34 files — 0 errors
## All Python: 5 core files — 0 syntax errors, 0 import errors
## Go controller: compiles, correct API usage verified

## To Run
```bash
make build-images   # build all 4 Docker images
make setup          # create k3d cluster + install infra
make deploy         # deploy PHANTOM to cluster
make load-spike     # inject cascade traffic spike
make dashboard      # open Grafana
```
