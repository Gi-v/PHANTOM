"""
model.py — PHANTOM GNN+LSTM Predictive Model

Architecture:
  1. GraphSAGEEncoder  — aggregates neighbourhood context per node
  2. Per-node LSTM     — learns temporal patterns across W timesteps
  3. MLP head          — predicts RPS at horizon H; confidence head via ensemble

Input:
  graph_sequence : list of W GraphSnapshot objects
  node_indices   : not used (kept for API compat) — pass None or omit

Output:
  predicted_rps  : Tensor [N]  float32  >= 0
  confidence     : Tensor [N]  float32  in (0, 1)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import SAGEConv


# ── Data container ────────────────────────────────────────────────────────────

@dataclass
class GraphSnapshot:
    """Single graph observation at one timestep."""
    node_features: torch.Tensor   # [N, 4]  rps, p99, error_rate, replicas
    edge_index:    torch.Tensor   # [2, E]  COO format; zeros((2,0)) if no edges
    edge_attr:     torch.Tensor   # [E, 3]  weight, p99_latency, error_rate
    timestamp:     float = 0.0


# ── GNN encoder ───────────────────────────────────────────────────────────────

class GraphSAGEEncoder(nn.Module):
    """2-layer GraphSAGE.  Handles graphs with zero edges safely."""

    def __init__(self, node_feat_dim: int, edge_feat_dim: int, hidden_dim: int):
        super().__init__()
        # Project edge features to node feature space for pre-aggregation
        self.edge_proj = nn.Linear(edge_feat_dim, node_feat_dim)
        self.conv1     = SAGEConv(node_feat_dim, hidden_dim)
        self.conv2     = SAGEConv(hidden_dim,    hidden_dim)
        self.norm1     = nn.LayerNorm(hidden_dim)
        self.norm2     = nn.LayerNorm(hidden_dim)

    def forward(
        self,
        x:          torch.Tensor,   # [N, node_feat_dim]
        edge_index: torch.Tensor,   # [2, E]
        edge_attr:  torch.Tensor,   # [E, edge_feat_dim]
    ) -> torch.Tensor:              # [N, hidden_dim]

        # Fuse edge features into source nodes (only when edges exist)
        if edge_attr.size(0) > 0 and edge_index.size(1) > 0:
            edge_emb = self.edge_proj(edge_attr)          # [E, node_feat_dim]
            src      = edge_index[0]                       # [E]
            # Accumulate edge embeddings at source nodes
            agg = torch.zeros_like(x)                      # [N, node_feat_dim]
            agg.scatter_add_(
                0,
                src.unsqueeze(1).expand(-1, x.size(1)),    # [E, node_feat_dim]
                edge_emb,
            )
            x = x + agg

        h = F.relu(self.norm1(self.conv1(x, edge_index)))
        h = F.dropout(h, p=0.1, training=self.training)
        h = self.norm2(self.conv2(h, edge_index))
        return h   # [N, hidden_dim]


# ── Main model ────────────────────────────────────────────────────────────────

class PHANTOMModel(nn.Module):
    """
    GNN+LSTM traffic predictor.

    Args:
        node_feat_dim  : node feature width  (default 4)
        edge_feat_dim  : edge feature width  (default 3)
        hidden_dim     : GNN output dim      (default 64)
        lstm_hidden    : LSTM hidden size    (default 128)
        lstm_layers    : LSTM depth          (default 2)
        history_window : sequence length W   (default 12)
    """

    def __init__(
        self,
        node_feat_dim:  int = 4,
        edge_feat_dim:  int = 3,
        hidden_dim:     int = 64,
        lstm_hidden:    int = 128,
        lstm_layers:    int = 2,
        history_window: int = 12,
    ):
        super().__init__()
        self.hidden_dim     = hidden_dim
        self.lstm_hidden    = lstm_hidden
        self.history_window = history_window

        self.gnn = GraphSAGEEncoder(
            node_feat_dim=node_feat_dim,
            edge_feat_dim=edge_feat_dim,
            hidden_dim=hidden_dim,
        )

        self.lstm = nn.LSTM(
            input_size=hidden_dim,
            hidden_size=lstm_hidden,
            num_layers=lstm_layers,
            batch_first=True,
            dropout=0.1 if lstm_layers > 1 else 0.0,
        )

        self.head = nn.Sequential(
            nn.Linear(lstm_hidden, lstm_hidden // 2),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(lstm_hidden // 2, 1),
            nn.Softplus(),          # guarantees non-negative RPS output
        )

    def forward(
        self,
        graph_sequence: list[GraphSnapshot],   # length W
        node_indices:   torch.Tensor | None = None,  # unused; kept for API compat
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Returns:
            rps        : [N] predicted RPS at horizon H
            conf_stub  : [N] ones — real confidence from PHANTOMEnsemble
        """
        N = graph_sequence[0].node_features.size(0)

        gnn_outs = []
        for snap in graph_sequence:
            emb = self.gnn(snap.node_features, snap.edge_index, snap.edge_attr)
            gnn_outs.append(emb)                           # [N, hidden_dim]

        temporal    = torch.stack(gnn_outs, dim=1)         # [N, W, hidden_dim]
        lstm_out, _ = self.lstm(temporal)                  # [N, W, lstm_hidden]
        last        = lstm_out[:, -1, :]                   # [N, lstm_hidden]

        rps = self.head(last).squeeze(-1)                  # [N]
        return rps, torch.ones(N, dtype=torch.float32, device=rps.device)


# ── Ensemble ──────────────────────────────────────────────────────────────────

class PHANTOMEnsemble(nn.Module):
    """
    N independent PHANTOMModels.
    Confidence = 1 − clamp(std / (mean + ε), 0, 1)
    High std relative to mean → low confidence → controller falls back to HPA.
    """

    def __init__(self, n_models: int = 5, **model_kwargs):
        super().__init__()
        self.models = nn.ModuleList(
            [PHANTOMModel(**model_kwargs) for _ in range(n_models)]
        )

    def forward(
        self,
        graph_sequence: list[GraphSnapshot],
        node_indices:   torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Returns:
            mean_rps   : [N]  mean prediction across ensemble
            confidence : [N]  in (0, 1)
        """
        preds = []
        for m in self.models:
            rps, _ = m(graph_sequence, node_indices)
            preds.append(rps)

        stack      = torch.stack(preds, dim=0)              # [n_models, N]
        mean_rps   = stack.mean(dim=0)                      # [N]
        std_rps    = stack.std(dim=0)                       # [N]
        confidence = 1.0 - (std_rps / (mean_rps + 1e-6)).clamp(0.0, 1.0)
        return mean_rps, confidence


# ── Replica calculator ────────────────────────────────────────────────────────

def compute_replicas(
    predicted_rps:   float,
    rps_per_replica: float = 100.0,
    buffer:          float = 1.2,
    min_replicas:    int   = 1,
    max_replicas:    int   = 20,
) -> int:
    """Convert predicted RPS to a replica count with headroom buffer."""
    if rps_per_replica <= 0:
        rps_per_replica = 100.0
    raw = (predicted_rps / rps_per_replica) * buffer
    return max(min_replicas, min(int(raw) + 1, max_replicas))
