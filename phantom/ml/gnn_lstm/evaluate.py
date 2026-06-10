#!/usr/bin/env python3
"""
evaluate.py — Offline evaluation of a trained PHANTOM checkpoint
Outputs MAPE, MAE, RMSE per service and overall.

Usage:
  python evaluate.py --checkpoint checkpoints/phantom_latest.pt \
                     --data-dir data/traces/
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

import torch
import numpy as np

from model import PHANTOMModel, GraphSnapshot
from train import TraceDataset


def evaluate(args):
    device = torch.device("cpu")

    ckpt = torch.load(args.checkpoint, map_location=device, weights_only=False)
    kwargs = ckpt["model_kwargs"]
    model = PHANTOMModel(**kwargs).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    print(f"Loaded: {ckpt.get('version','?')}  val_mape={ckpt.get('val_mape','?'):.2f}%")

    ds = TraceDataset(args.data_dir, window=kwargs.get("history_window", 12), horizon=5)
    print(f"Eval samples: {len(ds)}")

    all_pred, all_true = [], []
    with torch.no_grad():
        for snaps, target_rps in ds:
            target_rps = target_rps.to(device)
            N = target_rps.shape[0]
            snaps_dev = [GraphSnapshot(
                node_features=s.node_features.to(device),
                edge_index=s.edge_index.to(device),
                edge_attr=s.edge_attr.to(device),
                timestamp=s.timestamp,
            ) for s in snaps]
            pred, _ = model(snaps_dev)
            all_pred.append(pred.cpu().numpy())
            all_true.append(target_rps.cpu().numpy())

    pred_arr = np.concatenate(all_pred)
    true_arr = np.concatenate(all_true)

    mask = true_arr > 1.0
    mape = float(np.mean(np.abs(pred_arr[mask] - true_arr[mask]) / true_arr[mask]) * 100)
    mae  = float(np.mean(np.abs(pred_arr - true_arr)))
    rmse = float(np.sqrt(np.mean((pred_arr - true_arr) ** 2)))

    print(f"\nResults over {len(ds)} samples:")
    print(f"  MAPE : {mape:.2f}%")
    print(f"  MAE  : {mae:.3f} RPS")
    print(f"  RMSE : {rmse:.3f} RPS")

    out = {"mape": mape, "mae": mae, "rmse": rmse, "n_samples": len(ds),
           "checkpoint": str(args.checkpoint), "version": ckpt.get("version")}
    out_path = Path(args.checkpoint).parent / "eval_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"  Saved: {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--data-dir",   default="./data/traces")
    evaluate(p.parse_args())
