"""
graph_builder.py
Reconstructs a weighted service dependency graph from OpenTelemetry traces
stored in Tempo. Returns a NetworkX DiGraph where:
  - nodes = service names
  - edges = (caller, callee) with attributes:
      weight      : mean RPS over the window
      p99_latency : p99 span duration (seconds)
      error_rate  : fraction of spans with error=true
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import networkx as nx
import structlog

log = structlog.get_logger()


@dataclass
class GraphBuilderConfig:
    tempo_endpoint: str = "http://localhost:3100"
    lookback_minutes: int = 10
    min_rps_threshold: float = 0.1       # ignore edges with < 0.1 rps
    rebuild_interval_seconds: int = 60


@dataclass
class EdgeMetrics:
    call_count: int = 0
    error_count: int = 0
    total_duration_ms: float = 0.0
    duration_samples: list = field(default_factory=list)

    @property
    def rps(self) -> float:
        return self.call_count / 60.0  # per second over ~1min window

    @property
    def error_rate(self) -> float:
        return self.error_count / max(self.call_count, 1)

    @property
    def mean_latency_ms(self) -> float:
        return self.total_duration_ms / max(self.call_count, 1)

    @property
    def p99_latency_ms(self) -> float:
        if not self.duration_samples:
            return 0.0
        s = sorted(self.duration_samples)
        idx = int(0.99 * len(s))
        return s[min(idx, len(s) - 1)]


class GraphBuilder:
    """
    Polls Tempo's search API, extracts service→service call edges,
    and returns a weighted DiGraph.
    """

    def __init__(self, config: Optional[GraphBuilderConfig] = None):
        self.config = config or GraphBuilderConfig()
        self._client = httpx.AsyncClient(timeout=10.0)
        self._graph: nx.DiGraph = nx.DiGraph()
        self._last_built: Optional[datetime] = None

    async def build(self) -> nx.DiGraph:
        """Fetch traces from Tempo and reconstruct the call graph."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=self.config.lookback_minutes)

        traces = await self._fetch_traces(start, end)
        graph = self._traces_to_graph(traces, start, end)

        self._graph = graph
        self._last_built = end
        log.info("graph_rebuilt",
                 nodes=graph.number_of_nodes(),
                 edges=graph.number_of_edges())
        return graph

    async def _fetch_traces(self, start: datetime, end: datetime) -> list[dict]:
        """Search Tempo for all traces in the time window."""
        params = {
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "limit": 2000,
            "spss": 10,  # spans per span set
        }
        try:
            resp = await self._client.get(
                f"{self.config.tempo_endpoint}/api/search",
                params=params
            )
            resp.raise_for_status()
            data = resp.json()
            trace_ids = [t["traceID"] for t in data.get("traces", [])]

            # Fetch full spans for each trace
            tasks = [self._fetch_trace_spans(tid) for tid in trace_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [r for r in results if isinstance(r, list)]
        except Exception as e:
            log.warning("tempo_fetch_failed", error=str(e))
            return []

    async def _fetch_trace_spans(self, trace_id: str) -> list[dict]:
        """Fetch all spans for a single trace."""
        resp = await self._client.get(
            f"{self.config.tempo_endpoint}/api/traces/{trace_id}"
        )
        resp.raise_for_status()
        data = resp.json()
        spans = []
        for batch in data.get("batches", []):
            service_name = self._extract_service_name(batch)
            for scope_span in batch.get("scopeSpans", []):
                for span in scope_span.get("spans", []):
                    spans.append({
                        "service": service_name,
                        "span_id": span.get("spanId"),
                        "parent_span_id": span.get("parentSpanId"),
                        "duration_ns": span.get("endTimeUnixNano", 0) - span.get("startTimeUnixNano", 0),
                        "status_code": span.get("status", {}).get("code", 0),
                        "trace_id": trace_id,
                    })
        return spans

    @staticmethod
    def _extract_service_name(batch: dict) -> str:
        resource = batch.get("resource", {})
        for attr in resource.get("attributes", []):
            if attr.get("key") == "service.name":
                return attr["value"].get("stringValue", "unknown")
        return "unknown"

    def _traces_to_graph(
        self,
        all_trace_spans: list[list[dict]],
        start: datetime,
        end: datetime
    ) -> nx.DiGraph:
        """
        For each trace, build a span_id → (service, parent_span_id) map,
        then emit (parent_service → child_service) edges.
        """
        edge_metrics: dict[tuple[str, str], EdgeMetrics] = {}

        for spans in all_trace_spans:
            span_map = {s["span_id"]: s for s in spans}
            for span in spans:
                parent_id = span.get("parent_span_id")
                if not parent_id or parent_id not in span_map:
                    continue
                parent_service = span_map[parent_id]["service"]
                child_service = span["service"]
                if parent_service == child_service:
                    continue  # skip intra-service calls

                key = (parent_service, child_service)
                if key not in edge_metrics:
                    edge_metrics[key] = EdgeMetrics()

                m = edge_metrics[key]
                m.call_count += 1
                duration_ms = span["duration_ns"] / 1e6
                m.total_duration_ms += duration_ms
                m.duration_samples.append(duration_ms)
                if span["status_code"] == 2:  # GRPC/OTel ERROR
                    m.error_count += 1

        # Build DiGraph
        G = nx.DiGraph()
        window_seconds = (end - start).total_seconds()

        for (src, dst), metrics in edge_metrics.items():
            rps = metrics.call_count / window_seconds
            if rps < self.config.min_rps_threshold:
                continue
            G.add_edge(
                src, dst,
                weight=rps,
                p99_latency_ms=metrics.p99_latency_ms,
                mean_latency_ms=metrics.mean_latency_ms,
                error_rate=metrics.error_rate,
                call_count=metrics.call_count,
            )

        return G

    def to_pytorch_geometric(self, graph: nx.DiGraph):
        """
        Convert NetworkX DiGraph to PyTorch Geometric Data object
        for GNN input.
        Returns: (node_features, edge_index, edge_features, node_names)
        """
        import torch
        from torch_geometric.data import Data

        nodes = list(graph.nodes())
        node_to_idx = {n: i for i, n in enumerate(nodes)}

        # Node features: [in_degree, out_degree, total_rps_in, total_rps_out]
        node_features = []
        for node in nodes:
            in_rps = sum(d.get("weight", 0) for _, _, d in graph.in_edges(node, data=True))
            out_rps = sum(d.get("weight", 0) for _, _, d in graph.out_edges(node, data=True))
            node_features.append([
                graph.in_degree(node),
                graph.out_degree(node),
                in_rps,
                out_rps,
            ])

        x = torch.tensor(node_features, dtype=torch.float)

        # Edge index and features: [rps, p99_latency_ms, error_rate]
        edge_index = [[], []]
        edge_features = []
        for src, dst, data in graph.edges(data=True):
            edge_index[0].append(node_to_idx[src])
            edge_index[1].append(node_to_idx[dst])
            edge_features.append([
                data.get("weight", 0),
                data.get("p99_latency_ms", 0),
                data.get("error_rate", 0),
            ])

        edge_index_tensor = torch.tensor(edge_index, dtype=torch.long)
        edge_attr = torch.tensor(edge_features, dtype=torch.float)

        return Data(x=x, edge_index=edge_index_tensor, edge_attr=edge_attr), nodes

    async def close(self):
        await self._client.aclose()


# ── FastAPI app ───────────────────────────────────────────────────────────────

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import Counter, Gauge, generate_latest

_builder: GraphBuilder | None = None
_graph_json: dict = {}

rebuild_counter = Counter("phantom_graph_rebuilds_total", "Graph rebuild count")
node_count_gauge = Gauge("phantom_graph_nodes", "Current node count")
edge_count_gauge = Gauge("phantom_graph_edges", "Current edge count")

TEMPO_ENDPOINT      = os.getenv("TEMPO_URL",         "http://tempo:3200")
REBUILD_INTERVAL_S  = int(os.getenv("REBUILD_INTERVAL", "60"))
PROMETHEUS_URL      = os.getenv("PROMETHEUS_URL",    "http://prometheus:9090")


async def _rebuild_loop():
    """Periodically rebuild graph and expose via /graph endpoint."""
    while True:
        try:
            G = await _builder.build()
            nodes = []
            for svc in G.nodes():
                in_rps  = sum(d.get("weight", 0) for _, _, d in G.in_edges(svc,  data=True))
                out_rps = sum(d.get("weight", 0) for _, _, d in G.out_edges(svc, data=True))
                # Also enrich with Prometheus replica count
                replicas = await _fetch_replicas(svc)
                nodes.append({
                    "id":          svc,
                    "rps":         round(out_rps, 3),
                    "p99":         0.0,  # populated from edge data below
                    "error_rate":  0.0,
                    "replicas":    replicas,
                    "rps_per_replica": round(out_rps / max(replicas, 1), 3),
                })
            edges = [
                {
                    "source":      src,
                    "target":      dst,
                    "weight":      round(data.get("weight", 0), 3),
                    "p99_latency": round(data.get("p99_latency_ms", 0), 3),
                    "error_rate":  round(data.get("error_rate", 0), 6),
                }
                for src, dst, data in G.edges(data=True)
            ]
            _graph_json.clear()
            _graph_json.update({"nodes": nodes, "edges": edges})
            rebuild_counter.inc()
            node_count_gauge.set(len(nodes))
            edge_count_gauge.set(len(edges))
        except Exception as e:
            log.warning("rebuild_failed", error=str(e))
        await asyncio.sleep(REBUILD_INTERVAL_S)


async def _fetch_replicas(service: str) -> int:
    """Query Prometheus for current replica count of a service."""
    query = f'kube_deployment_spec_replicas{{namespace="phantom",deployment="{service}"}}'
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(PROMETHEUS_URL + "/api/v1/query", params={"query": query})
            data = r.json().get("data", {}).get("result", [])
            if data:
                return int(float(data[0]["value"][1]))
    except Exception:
        pass
    return 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _builder
    _builder = GraphBuilder(GraphBuilderConfig(
        tempo_endpoint=TEMPO_ENDPOINT,
        rebuild_interval_seconds=REBUILD_INTERVAL_S,
    ))
    task = asyncio.create_task(_rebuild_loop())
    yield
    task.cancel()
    await _builder.close()


gb_app = FastAPI(title="PHANTOM Graph Builder", version="1.0.0", lifespan=lifespan)


@gb_app.get("/graph")
async def get_graph():
    """Return current service dependency graph as JSON."""
    return _graph_json


@gb_app.get("/health")
async def health():
    return {
        "status": "ok",
        "nodes":  len(_graph_json.get("nodes", [])),
        "edges":  len(_graph_json.get("edges", [])),
    }


@gb_app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    return generate_latest()


# Entry point — run with: uvicorn graph_builder:gb_app --host 0.0.0.0 --port 8000
