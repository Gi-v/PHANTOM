#!/usr/bin/env python3
"""
analysis.py — PHANTOM Research Results Analysis

Reads experiment CSVs from research/data/ and produces:
  - Pareto frontier plot (P99 latency vs pod cost)
  - MAPE over time per autoscaler
  - SLO violation comparison bar chart
  - Wilcoxon signed-rank test results

Usage:
  python research/notebooks/analysis.py --data-dir research/data/spike_20240601_120000
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
from scipy import stats

COLORS = {"phantom": "#00e5ff", "hpa": "#9d7fff", "keda": "#ffd060"}
LABELS = {"phantom": "PHANTOM (ours)", "hpa": "HPA (baseline)", "keda": "KEDA (baseline)"}


def load_results(data_dir: str) -> pd.DataFrame:
    rows = []
    for path in Path(data_dir).glob("*.json"):
        with open(path) as f:
            rows.append(json.load(f))
    if not rows:
        raise ValueError(f"No result JSON files found in {data_dir}")
    return pd.DataFrame(rows)


def plot_pareto(df: pd.DataFrame, out_dir: Path):
    """P99 latency vs cost — PHANTOM should Pareto-dominate baselines."""
    fig, ax = plt.subplots(figsize=(7, 5))
    fig.patch.set_facecolor("#08090f")
    ax.set_facecolor("#0d0f1a")

    for scaler, grp in df.groupby("autoscaler"):
        ax.scatter(grp["cost_pod_hours"], grp["p99_latency_ms"],
                   c=COLORS.get(scaler, "gray"), s=80, alpha=0.85,
                   label=LABELS.get(scaler, scaler), zorder=3)
        # Mean marker
        ax.scatter(grp["cost_pod_hours"].mean(), grp["p99_latency_ms"].mean(),
                   c=COLORS.get(scaler, "gray"), s=200, marker="*", zorder=4)

    ax.set_xlabel("Cost proxy (pod-hours)", color="#6a7d96")
    ax.set_ylabel("P99 Latency (ms)", color="#6a7d96")
    ax.set_title("Pareto Frontier: Latency vs Cost", color="#c8d8ec", pad=12)
    ax.axhline(200, color="#ff4d6d", linewidth=1, linestyle="--", alpha=0.6, label="SLO (200ms)")
    ax.tick_params(colors="#6a7d96")
    ax.spines[:].set_color("#1e2840")
    ax.legend(facecolor="#0d0f1a", edgecolor="#1e2840", labelcolor="#c8d8ec", fontsize=9)
    ax.grid(True, color="#1e2840", linewidth=0.5)

    path = out_dir / "pareto_frontier.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  [plot] Saved: {path}")


def plot_latency_comparison(df: pd.DataFrame, out_dir: Path):
    """Bar chart: mean P99 ± std per autoscaler."""
    fig, ax = plt.subplots(figsize=(6, 4))
    fig.patch.set_facecolor("#08090f")
    ax.set_facecolor("#0d0f1a")

    scalers = ["phantom", "hpa", "keda"]
    means = [df[df.autoscaler == s]["p99_latency_ms"].mean() for s in scalers]
    stds  = [df[df.autoscaler == s]["p99_latency_ms"].std()  for s in scalers]
    x = np.arange(len(scalers))

    bars = ax.bar(x, means, yerr=stds, color=[COLORS[s] for s in scalers],
                  alpha=0.75, capsize=6, error_kw={"color": "#6a7d96", "linewidth": 1.5})
    ax.axhline(200, color="#ff4d6d", linewidth=1.5, linestyle="--", alpha=0.7, label="SLO target")
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[s] for s in scalers], color="#c8d8ec", fontsize=9)
    ax.set_ylabel("P99 Latency (ms)", color="#6a7d96")
    ax.set_title("Mean P99 Latency by Autoscaler (±1σ)", color="#c8d8ec", pad=12)
    ax.tick_params(colors="#6a7d96")
    ax.spines[:].set_color("#1e2840")
    ax.legend(facecolor="#0d0f1a", edgecolor="#1e2840", labelcolor="#c8d8ec", fontsize=9)
    ax.grid(True, axis="y", color="#1e2840", linewidth=0.5)

    for bar, mean in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 5,
                f"{mean:.0f}ms", ha="center", va="bottom", color="#c8d8ec", fontsize=9)

    path = out_dir / "latency_comparison.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  [plot] Saved: {path}")


def plot_cost_comparison(df: pd.DataFrame, out_dir: Path):
    """Pod hours per run — PHANTOM should use fewer."""
    fig, ax = plt.subplots(figsize=(6, 4))
    fig.patch.set_facecolor("#08090f")
    ax.set_facecolor("#0d0f1a")

    scalers = ["phantom", "hpa", "keda"]
    means = [df[df.autoscaler == s]["cost_pod_hours"].mean() for s in scalers]
    x = np.arange(len(scalers))

    ax.bar(x, means, color=[COLORS[s] for s in scalers], alpha=0.75)
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[s] for s in scalers], color="#c8d8ec", fontsize=9)
    ax.set_ylabel("Pod-hours per run", color="#6a7d96")
    ax.set_title("Compute Cost Proxy by Autoscaler", color="#c8d8ec", pad=12)
    ax.tick_params(colors="#6a7d96")
    ax.spines[:].set_color("#1e2840")
    ax.grid(True, axis="y", color="#1e2840", linewidth=0.5)

    path = out_dir / "cost_comparison.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  [plot] Saved: {path}")


def statistical_tests(df: pd.DataFrame) -> dict:
    """Wilcoxon signed-rank tests: PHANTOM vs each baseline."""
    results = {}
    phantom_p99 = df[df.autoscaler == "phantom"]["p99_latency_ms"].values

    for baseline in ["hpa", "keda"]:
        base_p99 = df[df.autoscaler == baseline]["p99_latency_ms"].values
        n = min(len(phantom_p99), len(base_p99))
        if n < 2:
            continue
        stat, pval = stats.wilcoxon(phantom_p99[:n], base_p99[:n], alternative="less")
        results[f"phantom_vs_{baseline}"] = {
            "statistic": round(float(stat), 4),
            "p_value": round(float(pval), 6),
            "significant": pval < 0.05,
            "phantom_mean_p99": round(float(phantom_p99.mean()), 2),
            f"{baseline}_mean_p99": round(float(base_p99.mean()), 2),
            "reduction_pct": round((1 - phantom_p99.mean() / base_p99.mean()) * 100, 1),
        }

    return results


def summary_table(df: pd.DataFrame):
    print("\n" + "=" * 65)
    print("PHANTOM Experiment Summary")
    print("=" * 65)
    for scaler in ["phantom", "hpa", "keda"]:
        grp = df[df.autoscaler == scaler]
        if grp.empty:
            continue
        print(f"\n  {LABELS[scaler]}")
        print(f"    P99 latency:    {grp['p99_latency_ms'].mean():.1f}ms ± {grp['p99_latency_ms'].std():.1f}")
        print(f"    Error rate:     {grp['error_rate_pct'].mean():.3f}%")
        print(f"    Avg replicas:   {grp['avg_replicas'].mean():.1f}")
        print(f"    Cost (pod-hr):  {grp['cost_pod_hours'].mean():.4f}")
        if scaler == "phantom":
            print(f"    MAPE:           {grp['avg_mape_pct'].mean():.1f}%")
            print(f"    Confidence:     {grp['avg_confidence'].mean():.3f}")


def main(args):
    print(f"[analysis] Loading results from {args.data_dir}")
    df = load_results(args.data_dir)
    print(f"[analysis] Loaded {len(df)} experiment runs")

    out_dir = Path(args.data_dir) / "plots"
    out_dir.mkdir(exist_ok=True)

    summary_table(df)

    print("\n[analysis] Generating plots...")
    plot_pareto(df, out_dir)
    plot_latency_comparison(df, out_dir)
    plot_cost_comparison(df, out_dir)

    print("\n[analysis] Running statistical tests...")
    test_results = statistical_tests(df)
    for name, res in test_results.items():
        sig = "✓ SIGNIFICANT" if res["significant"] else "✗ not significant"
        print(f"  {name}: p={res['p_value']} {sig} | reduction={res.get('reduction_pct', 0):.1f}%")

    stats_path = out_dir / "statistical_tests.json"
    with open(stats_path, "w") as f:
        json.dump(test_results, f, indent=2)

    df.to_csv(out_dir / "combined_results.csv", index=False)
    print(f"\n[analysis] Done. Plots and stats saved to {out_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, help="Path to experiment results directory")
    main(parser.parse_args())
