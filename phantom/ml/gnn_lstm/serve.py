"""
serve.py — PHANTOM ML Service
FastAPI service. Controller calls GET /predict/{service} every 30s.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import torch
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import Counter, Gauge, Histogram, generate_latest

from model import GraphSnapshot, PHANTOMEnsemble, compute_replicas

logger = logging.getLogger("phantom-ml")
logging.basicConfig(level=logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
GRAPH_BUILDER_URL  = os.getenv("GRAPH_BUILDER_URL",  "http://graph-builder:8000")
MODEL_CHECKPOINT   = os.getenv("MODEL_CHECKPOINT",   "/models/phantom_latest.pt")
PREDICTION_HORIZON = int(os.getenv("PREDICTION_HORIZON", "300"))
HISTORY_WINDOW     = int(os.getenv("HISTORY_WINDOW",     "12"))
MODEL_N_ENSEMBLE   = int(os.getenv("MODEL_N_ENSEMBLE",   "5"))
GRAPH_REFRESH_S    = int(os.getenv("GRAPH_REFRESH_S",    "60"))

# ── Prometheus ────────────────────────────────────────────────────────────────
predictions_total   = Counter("phantom_predictions_total",           "Total predictions", ["service"])
prediction_latency  = Histogram("phantom_prediction_latency_seconds","Inference latency")
confidence_gauge    = Gauge("phantom_model_confidence",              "Last confidence",   ["service"])
predicted_rps_gauge = Gauge("phantom_predicted_rps",                 "Last predicted RPS",["service"])
graph_nodes_gauge   = Gauge("phantom_graph_nodes_total",             "Graph node count")

# ── Mutable state ─────────────────────────────────────────────────────────────
_model:          PHANTOMEnsemble | None = None
_model_version:  str                   = "unloaded"
_graph_cache:    dict                  = {}          # {nodes:[...], edges:[...]}
_history:        dict[str, list[GraphSnapshot]] = {}  # service → recent snapshots


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _model_version
    try:
        ckpt = torch.load(MODEL_CHECKPOINT, map_location="cpu", weights_only=False)
        kwargs = ckpt.get("model_kwargs", {})
        _model = PHANTOMEnsemble(n_models=MODEL_N_ENSEMBLE, **kwargs)
        _model.load_state_dict(ckpt["model_state"])
        _model.eval()
        _model_version = ckpt.get("version", "unknown")
        logger.info(f"Loaded model {_model_version}")
    except FileNotFoundError:
        logger.warning("No checkpoint — running in dev-fallback mode")
        _model = None

    task = asyncio.create_task(_graph_refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="PHANTOM ML", version="1.0.0", lifespan=lifespan)


# ── Background graph refresh ──────────────────────────────────────────────────

async def _graph_refresh_loop():
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                r = await client.get(f"{GRAPH_BUILDER_URL}/graph")
                if r.status_code == 200:
                    data = r.json()
                    _graph_cache.clear()
                    _graph_cache.update(data)
                    graph_nodes_gauge.set(len(data.get("nodes", [])))
                    _update_history()
            except Exception as e:
                logger.warning(f"Graph refresh error: {e}")
            await asyncio.sleep(GRAPH_REFRESH_S)


def _update_history():
    """Append current graph snapshot to per-service history buffers."""
    nodes = _graph_cache.get("nodes", [])
    edges = _graph_cache.get("edges", [])
    if not nodes:
        return
    snap = _build_snapshot(nodes, edges)
    for n in nodes:
        svc = n["id"]
        buf = _history.setdefault(svc, [])
        buf.append(snap)
        if len(buf) > HISTORY_WINDOW:
            buf.pop(0)


# ── Inference helpers ─────────────────────────────────────────────────────────

def _build_snapshot(nodes: list[dict], edges: list[dict]) -> GraphSnapshot:
    node_ids = [n["id"] for n in nodes]

    x = torch.tensor(
        [[n.get("rps", 0.0), n.get("p99", 0.0),
          n.get("error_rate", 0.0), float(n.get("replicas", 1))]
         for n in nodes],
        dtype=torch.float32,
    )

    valid_edges = [
        e for e in edges
        if e.get("source") in node_ids and e.get("target") in node_ids
    ]

    if valid_edges:
        ei = torch.tensor(
            [[node_ids.index(e["source"]), node_ids.index(e["target"])]
             for e in valid_edges],
            dtype=torch.long,
        ).t().contiguous()
        ea = torch.tensor(
            [[e.get("weight", 0.0), e.get("p99_latency", 0.0), e.get("error_rate", 0.0)]
             for e in valid_edges],
            dtype=torch.float32,
        )
    else:
        ei = torch.zeros((2, 0), dtype=torch.long)
        ea = torch.zeros((0, 3), dtype=torch.float32)

    return GraphSnapshot(
        node_features=x,
        edge_index=ei,
        edge_attr=ea,
        timestamp=time.time(),
    )


def _run_inference(service_name: str, namespace: str) -> tuple[float, float]:
    """Returns (predicted_rps, confidence). Raises on failure."""
    nodes    = _graph_cache.get("nodes", [])
    edges    = _graph_cache.get("edges", [])
    node_ids = [n["id"] for n in nodes]

    if not node_ids:
        raise ValueError("empty graph cache")

    # Find target node index
    target_idx = None
    for key in (f"{namespace}/{service_name}", service_name):
        if key in node_ids:
            target_idx = node_ids.index(key)
            break
    if target_idx is None:
        target_idx = 0  # fallback to first node

    snap = _build_snapshot(nodes, edges)

    # Build history sequence — pad with current snap if not enough history
    buf = _history.get(service_name, [])
    sequence = (buf + [snap])[-HISTORY_WINDOW:]
    while len(sequence) < HISTORY_WINDOW:
        sequence = [snap] + sequence

    N = len(node_ids)
    with torch.no_grad():
        mean_rps, conf = _model(sequence)

    return float(mean_rps[target_idx].item()), float(conf[target_idx].item())


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/predict/{service_name}")
async def predict(
    service_name: str,
    namespace: str = "default",
    horizon: int = PREDICTION_HORIZON,
):
    t0 = time.perf_counter()

    nodes      = {n["id"]: n for n in _graph_cache.get("nodes", [])}
    node       = nodes.get(f"{namespace}/{service_name}") or nodes.get(service_name)
    current_rps      = float(node["rps"])      if node else 10.0
    rps_per_replica  = float(node.get("rps_per_replica", 100.0)) if node else 100.0

    if _model is None or not _graph_cache:
        predicted_rps = current_rps * 1.1
        confidence    = 0.5
    else:
        try:
            predicted_rps, confidence = _run_inference(service_name, namespace)
        except Exception as e:
            logger.error(f"Inference failed for {service_name}: {e}")
            predicted_rps = current_rps * 1.1
            confidence    = 0.3

    elapsed = time.perf_counter() - t0
    prediction_latency.observe(elapsed)
    predictions_total.labels(service=service_name).inc()
    confidence_gauge.labels(service=service_name).set(confidence)
    predicted_rps_gauge.labels(service=service_name).set(predicted_rps)

    return {
        "service_name":    service_name,
        "namespace":       namespace,
        "predicted_rps":   round(predicted_rps, 2),
        "current_rps":     round(current_rps, 2),
        "confidence":      round(confidence, 4),
        "rps_per_replica": rps_per_replica,
        "horizon_seconds": horizon,
        "model_version":   _model_version,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "inference_ms":    round(elapsed * 1000, 2),
    }


@app.get("/graph")
async def get_graph():
    return _graph_cache


@app.get("/health")
async def health():
    return {
        "status":       "ok",
        "model_loaded": _model is not None,
        "model_version": _model_version,
        "graph_nodes":  len(_graph_cache.get("nodes", [])),
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    return generate_latest()
