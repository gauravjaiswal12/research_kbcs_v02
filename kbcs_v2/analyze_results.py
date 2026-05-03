#!/usr/bin/env python3
"""
KBCS v2 — Statistical Analysis & Table Generator
==================================================
Reads the CSV from the 30-run test suite and produces:
  1. Per-topology statistics table (Mean ± StdDev, 95% CI)
  2. Comparison table (Cross vs Dumbbell)
  3. LaTeX-ready table for the paper

Usage:
  python3 analyze_results.py                    # analyze both topologies
  python3 analyze_results.py --csv results/cross_results.csv  # analyze one
"""

import argparse
import csv
import math
import os
import sys


def load_csv(path):
    """Load CSV and return list of dicts with numeric conversion."""
    rows = []
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = {}
            for k, v in row.items():
                if k is None or v is None:
                    continue
                try:
                    parsed[k] = float(v)
                except (ValueError, TypeError):
                    parsed[k] = v
            rows.append(parsed)
    return rows


def compute_stats(values):
    """Compute mean, std dev, and 95% CI for a list of values."""
    n = len(values)
    if n == 0:
        return {'mean': 0, 'std': 0, 'ci_lo': 0, 'ci_hi': 0, 'n': 0}

    mean = sum(values) / n
    if n > 1:
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        std = math.sqrt(variance)
    else:
        std = 0.0

    # 95% CI using t-distribution approximation (t ≈ 2.045 for df=29)
    t_val = 2.045 if n >= 30 else 2.262  # df=29 or df=9
    se = std / math.sqrt(n) if n > 0 else 0
    ci_lo = mean - t_val * se
    ci_hi = mean + t_val * se

    return {'mean': mean, 'std': std, 'ci_lo': ci_lo, 'ci_hi': ci_hi, 'n': n}


def print_table(title, metrics_stats):
    """Print a formatted statistics table."""
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)
    print(f"  {'Metric':<30} {'Mean':>10} {'±StdDev':>10} {'95% CI':>22} {'N':>4}")
    print("-" * 78)
    for name, s in metrics_stats.items():
        ci_str = f"[{s['ci_lo']:.4f}, {s['ci_hi']:.4f}]"
        print(f"  {name:<30} {s['mean']:>10.4f} {s['std']:>10.4f} {ci_str:>22} {s['n']:>4}")
    print("=" * 78)


def print_latex_table(title, metrics_stats, label):
    """Print a LaTeX-ready table."""
    print()
    print(f"% LaTeX Table: {title}")
    print(r"\begin{table}[htbp]")
    print(r"\centering")
    print(f"\\caption{{{title}}}")
    print(f"\\label{{tab:{label}}}")
    print(r"\begin{tabular}{lcccc}")
    print(r"\hline")
    print(r"\textbf{Metric} & \textbf{Mean} & \textbf{Std Dev} & \textbf{95\% CI} & \textbf{N} \\")
    print(r"\hline")
    for name, s in metrics_stats.items():
        ci_str = f"[{s['ci_lo']:.3f}, {s['ci_hi']:.3f}]"
        print(f"{name} & {s['mean']:.4f} & {s['std']:.4f} & {ci_str} & {s['n']} \\\\")
    print(r"\hline")
    print(r"\end{tabular}")
    print(r"\end{table}")


def analyze_topology(rows, topo_name):
    """Analyze rows for a single topology."""
    metrics = {
        "Jain's Fairness Index": [r['jfi'] for r in rows],
        "Agg. Throughput (Mbps)": [r['agg_throughput_mbps'] for r in rows],
        "Link Utilization (%)": [r['link_util_pct'] for r in rows],
        "Packet Drop Ratio (%)": [r['pdr_pct'] for r in rows],
        "Mean Karma Score": [r['avg_karma'] for r in rows],
    }

    stats = {name: compute_stats(vals) for name, vals in metrics.items()}

    print_table(f"KBCS v2 — {topo_name} Topology (N={len(rows)} runs)", stats)
    print_latex_table(f"KBCS Performance — {topo_name} Topology", stats, f"kbcs_{topo_name.lower()}")

    return stats


def print_comparison(cross_stats, dbell_stats):
    """Print side-by-side comparison table."""
    print()
    print("=" * 90)
    print("  KBCS v2 — Cross-Topology vs Dumbbell Comparison")
    print("=" * 90)
    print(f"  {'Metric':<30} {'Cross (Mean±SD)':>20} {'Dumbbell (Mean±SD)':>20} {'Delta':>12}")
    print("-" * 90)

    for name in cross_stats:
        c = cross_stats[name]
        d = dbell_stats[name]
        delta = d['mean'] - c['mean']
        sign = "+" if delta >= 0 else ""
        print(f"  {name:<30} {c['mean']:>8.4f}±{c['std']:<8.4f} {d['mean']:>8.4f}±{d['std']:<8.4f} {sign}{delta:>10.4f}")
    print("=" * 90)

def print_kbcs_vs_fifo(topo_name, kbcs_stats, fifo_stats):
    """Print KBCS vs FIFO comparison for a single topology."""
    print()
    print("=" * 90)
    print(f"  KBCS vs FIFO Baseline — {topo_name} Topology")
    print("=" * 90)
    print(f"  {'Metric':<30} {'FIFO (Mean±SD)':<20} {'KBCS (Mean±SD)':<20} {'Improvement':<12}")
    print("-" * 90)

    for name in kbcs_stats:
        f = fifo_stats[name]
        k = kbcs_stats[name]
        delta = k['mean'] - f['mean']
        if 'Drop' in name:
            pct = (-delta / f['mean'] * 100.0) if f['mean'] > 0 else 0
            sign = "↓" if delta < 0 else "↑"
        else:
            pct = (delta / f['mean'] * 100.0) if f['mean'] > 0 else 0
            sign = "↑" if delta > 0 else "↓"
        print(f"  {name:<30} {f['mean']:>8.4f}±{f['std']:<8.4f} {k['mean']:>8.4f}±{k['std']:<8.4f} {sign}{abs(pct):>8.1f}%")
    print("=" * 90)


def analyze_p4cci(rows, topo_name):
    """Analyze P4CCI rows (no karma/PDR from registers)."""
    metrics = {
        "Jain's Fairness Index": [r['jfi'] for r in rows],
        "Agg. Throughput (Mbps)": [r['agg_throughput_mbps'] for r in rows],
        "Link Utilization (%)": [r['link_util_pct'] for r in rows],
    }
    stats = {name: compute_stats(vals) for name, vals in metrics.items()}
    print_table(f"P4CCI Baseline — {topo_name} Topology (N={len(rows)} runs)", stats)
    return stats


def print_three_way(topo_name, fifo_stats, p4cci_stats, kbcs_stats):
    """Print FIFO vs P4CCI vs KBCS comparison."""
    # Use only metrics available in all three
    common = ["Jain's Fairness Index", "Agg. Throughput (Mbps)", "Link Utilization (%)"]
    print()
    print("=" * 106)
    print(f"  FIFO vs P4CCI vs KBCS — {topo_name} Topology (3-Way Comparison)")
    print("=" * 106)
    print(f"  {'Metric':<28} {'FIFO (Mean±SD)':<20} {'P4CCI (Mean±SD)':<20} {'KBCS (Mean±SD)':<20} {'KBCS vs P4CCI':<14}")
    print("-" * 106)

    for name in common:
        f = fifo_stats.get(name, {'mean': 0, 'std': 0})
        p = p4cci_stats.get(name, {'mean': 0, 'std': 0})
        k = kbcs_stats.get(name, {'mean': 0, 'std': 0})
        delta = k['mean'] - p['mean']
        pct = (delta / p['mean'] * 100.0) if p['mean'] > 0 else 0
        sign = "↑" if delta > 0 else "↓"
        print(f"  {name:<28} {f['mean']:>7.4f}±{f['std']:<7.4f} {p['mean']:>7.4f}±{p['std']:<7.4f} {k['mean']:>7.4f}±{k['std']:<7.4f}  {sign}{abs(pct):>7.1f}%")
    print("=" * 106)


def print_three_way_latex(topo_name, fifo_stats, p4cci_stats, kbcs_stats):
    """Print LaTeX 3-way comparison table."""
    common = ["Jain's Fairness Index", "Agg. Throughput (Mbps)", "Link Utilization (%)"]
    print()
    print(f"% LaTeX Table: 3-Way Comparison — {topo_name} Topology")
    print(r"\begin{table}[htbp]")
    print(r"\centering")
    print(f"\\caption{{FIFO vs P4CCI vs KBCS — {topo_name} Topology}}")
    print(f"\\label{{tab:3way_{topo_name.lower()}}}")
    print(r"\begin{tabular}{lccc}")
    print(r"\hline")
    print(r"\textbf{Metric} & \textbf{FIFO} & \textbf{P4CCI} & \textbf{KBCS (Ours)} \\")
    print(r"\hline")
    for name in common:
        f = fifo_stats.get(name, {'mean': 0, 'std': 0})
        p = p4cci_stats.get(name, {'mean': 0, 'std': 0})
        k = kbcs_stats.get(name, {'mean': 0, 'std': 0})
        print(f"{name} & {f['mean']:.4f}$\\pm${f['std']:.4f} & {p['mean']:.4f}$\\pm${p['std']:.4f} & \\textbf{{{k['mean']:.4f}$\\pm${k['std']:.4f}}} \\\\")
    print(r"\hline")
    print(r"\end{tabular}")
    print(r"\end{table}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', type=str, default=None, help='Path to a specific CSV')
    args = parser.parse_args()

    cross_file = 'results/cross_results.csv'
    dbell_file = 'results/dumbbell_results.csv'
    fifo_cross_file = 'results/fifo_cross_results.csv'
    fifo_dbell_file = 'results/fifo_dumbbell_results.csv'
    p4cci_cross_file = 'results/p4cci_cross_results.csv'
    p4cci_dbell_file = 'results/p4cci_dumbbell_results.csv'

    if args.csv:
        if not os.path.exists(args.csv):
            print(f"ERROR: {args.csv} not found")
            sys.exit(1)
        rows = load_csv(args.csv)
        topo = rows[0].get('topology', 'unknown') if rows else 'unknown'
        analyze_topology(rows, topo.title())
        return

    # ── Analyze KBCS results ──
    cross_stats = None
    dbell_stats = None

    if os.path.exists(cross_file):
        rows = load_csv(cross_file)
        if rows:
            cross_stats = analyze_topology(rows, "Cross (KBCS)")

    if os.path.exists(dbell_file):
        rows = load_csv(dbell_file)
        if rows:
            dbell_stats = analyze_topology(rows, "Dumbbell (KBCS)")

    # ── Analyze FIFO baselines ──
    fifo_cross_stats = None
    fifo_dbell_stats = None

    if os.path.exists(fifo_cross_file):
        rows = load_csv(fifo_cross_file)
        if rows:
            fifo_cross_stats = analyze_topology(rows, "Cross (FIFO)")

    if os.path.exists(fifo_dbell_file):
        rows = load_csv(fifo_dbell_file)
        if rows:
            fifo_dbell_stats = analyze_topology(rows, "Dumbbell (FIFO)")

    # ── Analyze P4CCI baselines ──
    p4cci_cross_stats = None
    p4cci_dbell_stats = None

    if os.path.exists(p4cci_cross_file):
        rows = load_csv(p4cci_cross_file)
        if rows:
            p4cci_cross_stats = analyze_p4cci(rows, "Cross (P4CCI)")

    if os.path.exists(p4cci_dbell_file):
        rows = load_csv(p4cci_dbell_file)
        if rows:
            p4cci_dbell_stats = analyze_p4cci(rows, "Dumbbell (P4CCI)")

    # ── Cross vs Dumbbell (KBCS internal) ──
    if cross_stats and dbell_stats:
        print_comparison(cross_stats, dbell_stats)

    # ── KBCS vs FIFO (2-way) ──
    if dbell_stats and fifo_dbell_stats:
        print_kbcs_vs_fifo("Dumbbell", dbell_stats, fifo_dbell_stats)

    if cross_stats and fifo_cross_stats:
        print_kbcs_vs_fifo("Cross", cross_stats, fifo_cross_stats)

    # ── 3-Way Comparison: FIFO vs P4CCI vs KBCS ──
    if dbell_stats and fifo_dbell_stats and p4cci_dbell_stats:
        print_three_way("Dumbbell", fifo_dbell_stats, p4cci_dbell_stats, dbell_stats)
        print_three_way_latex("Dumbbell", fifo_dbell_stats, p4cci_dbell_stats, dbell_stats)

    if cross_stats and fifo_cross_stats and p4cci_cross_stats:
        print_three_way("Cross", fifo_cross_stats, p4cci_cross_stats, cross_stats)
        print_three_way_latex("Cross", fifo_cross_stats, p4cci_cross_stats, cross_stats)

    if not cross_stats and not dbell_stats:
        print("No result files found. Run test_suite.sh first.")
        print(f"  Expected: {cross_file} or {dbell_file}")


if __name__ == '__main__':
    main()

