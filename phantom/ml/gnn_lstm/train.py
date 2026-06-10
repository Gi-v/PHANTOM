"""
train.py — PHANTOM GNN+LSTM Training Script

Usage:
  python train.py --data-dir ./data/traces --epochs 100 --output ./checkpoints
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from model import GraphSnapshot, PHANTOMModel


# ── Dataset ───────────────────────────────────────────────────────────────────

class TraceDataset(Dataset):
    """
    Each JSON file: list of timestep dicts:
      {timestamp, nodes:[{id,rps,p99,error_rate,replicas}],
                  edges:[{source,target,weight,p99_latency,error_rate}]}

    Returns (window of W GraphSnapshots, target_rps Tensor[N]) per sample.
    """

    def __init__(self, data_dir: str, window: int = 12, horizon: int = 5):
        self.samples: list[tuple[list[GraphSnapshot], torch.Tensor]] = []

        for path in sorted(Path(data_dir).glob("*.json")):
            with open(path) as f:
                timeline: list[dict] = json.load(f)
            for i in range(window, len(timeline) - horizon):
                snaps = [_to_snapshot(s) for s in timeline[i - window: i]]
                rps = torch.tensor(
                    [n.get("rps", 0.0) for n in timeline[i + horizon]["nodes"]],
                    dtype=torch.float32,
                )
                self.samples.append((snaps, rps))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        return self.samples[idx]


def _to_snapshot(raw: dict) -> GraphSnapshot:
    nodes = raw.get("nodes", [])
    edges = raw.get("edges", [])
    node_ids = [n["id"] for n in nodes]

    x = torch.tensor(
        [[n.get("rps", 0.0), n.get("p99", 0.0),
          n.get("error_rate", 0.0), float(n.get("replicas", 1))]
         for n in nodes],
        dtype=torch.float32,
    )

    valid = [(e["source"], e["target"]) for e in edges
             if e.get("source") in node_ids and e.get("target") in node_ids]

    if valid:
        ei = torch.tensor(
            [[node_ids.index(s), node_ids.index(t)] for s, t in valid],
            dtype=torch.long,
        ).t().contiguous()
        ea_rows = [e for e in edges
                   if e.get("source") in node_ids and e.get("target") in node_ids]
        ea = torch.tensor(
            [[e.get("weight", 0.0), e.get("p99_latency", 0.0), e.get("error_rate", 0.0)]
             for e in ea_rows],
            dtype=torch.float32,
        )
    else:
        ei = torch.zeros((2, 0), dtype=torch.long)
        ea = torch.zeros((0, 3), dtype=torch.float32)

    return GraphSnapshot(
        node_features=x, edge_index=ei, edge_attr=ea,
        timestamp=raw.get("timestamp", 0.0),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def mape(pred: torch.Tensor, target: torch.Tensor) -> float:
    mask = target > 1.0
    if not mask.any():
        return 0.0
    return float(((pred[mask] - target[mask]).abs() / target[mask]).mean().item() * 100)


def _move_snaps(snaps: list[GraphSnapshot], device: torch.device) -> list[GraphSnapshot]:
    return [GraphSnapshot(
        node_features=s.node_features.to(device),
        edge_index=s.edge_index.to(device),
        edge_attr=s.edge_attr.to(device),
        timestamp=s.timestamp,
    ) for s in snaps]


# ── Training ──────────────────────────────────────────────────────────────────

def train(args: argparse.Namespace) -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[PHANTOM] Device: {device}")

    dataset = TraceDataset(args.data_dir, window=args.window, horizon=args.horizon)
    if len(dataset) == 0:
        raise ValueError(f"No training samples found in {args.data_dir}")

    n_val   = max(1, int(len(dataset) * 0.15))
    n_train = len(dataset) - n_val
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

    # Each sample has variable N — batch_size=1, no padding needed
    train_loader = DataLoader(train_ds, batch_size=1, shuffle=True,  collate_fn=lambda x: x)
    val_loader   = DataLoader(val_ds,   batch_size=1, shuffle=False, collate_fn=lambda x: x)

    model_kwargs = dict(
        node_feat_dim=4,
        edge_feat_dim=3,
        hidden_dim=args.hidden_dim,
        lstm_hidden=args.lstm_hidden,
        lstm_layers=2,
        history_window=args.window,
    )
    model     = PHANTOMModel(**model_kwargs).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    criterion = nn.HuberLoss(delta=10.0)

    os.makedirs(args.output, exist_ok=True)
    best_mape = float("inf")
    history   = {"train_loss": [], "val_loss": [], "val_mape": []}

    for epoch in range(1, args.epochs + 1):

        # ── Train ──
        model.train()
        train_loss = 0.0
        for [(snaps, target_rps)] in train_loader:
            target_rps = target_rps.to(device)
            snaps_dev  = _move_snaps(snaps, device)
            optimizer.zero_grad()
            pred, _ = model(snaps_dev)
            loss    = criterion(pred, target_rps)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += loss.item()

        scheduler.step()
        train_loss /= max(1, len(train_loader))

        # ── Validate ──
        model.eval()
        val_loss_sum, mape_sum, n_val_items = 0.0, 0.0, 0
        with torch.no_grad():
            for [(snaps, target_rps)] in val_loader:
                target_rps = target_rps.to(device)
                snaps_dev  = _move_snaps(snaps, device)
                pred, _    = model(snaps_dev)
                val_loss_sum += criterion(pred, target_rps).item()
                mape_sum     += mape(pred, target_rps)
                n_val_items  += 1

        val_loss = val_loss_sum / max(1, n_val_items)
        val_mape = mape_sum     / max(1, n_val_items)
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["val_mape"].append(val_mape)

        if epoch % 10 == 0 or epoch == 1:
            print(f"Epoch {epoch:3d}/{args.epochs}  "
                  f"train={train_loss:.4f}  val={val_loss:.4f}  MAPE={val_mape:.2f}%")

        if val_mape < best_mape:
            best_mape = val_mape
            torch.save({
                "epoch":        epoch,
                "model_state":  model.state_dict(),
                "model_kwargs": model_kwargs,
                "val_mape":     val_mape,
                "version":      f"v1.0.0-epoch{epoch}",
            }, os.path.join(args.output, "phantom_best.pt"))

    torch.save({
        "epoch":        args.epochs,
        "model_state":  model.state_dict(),
        "model_kwargs": model_kwargs,
        "val_mape":     best_mape,
        "version":      "v1.0.0-final",
    }, os.path.join(args.output, "phantom_latest.pt"))

    with open(os.path.join(args.output, "history.json"), "w") as f:
        json.dump(history, f, indent=2)

    print(f"\n[PHANTOM] Training complete. Best MAPE: {best_mape:.2f}%")
    print(f"[PHANTOM] Checkpoint: {args.output}/phantom_latest.pt")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Train PHANTOM GNN+LSTM model")
    p.add_argument("--data-dir",    default="./data/traces")
    p.add_argument("--output",      default="./checkpoints")
    p.add_argument("--epochs",      type=int,   default=100)
    p.add_argument("--lr",          type=float, default=1e-3)
    p.add_argument("--window",      type=int,   default=12, help="History window steps")
    p.add_argument("--horizon",     type=int,   default=5,  help="Prediction horizon steps")
    p.add_argument("--hidden-dim",  type=int,   default=64,  dest="hidden_dim")
    p.add_argument("--lstm-hidden", type=int,   default=128, dest="lstm_hidden")
    train(p.parse_args())
