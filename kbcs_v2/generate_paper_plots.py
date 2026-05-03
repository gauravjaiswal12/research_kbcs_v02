#!/usr/bin/env python3
"""
KBCS v2 — Publication-Quality 3-Way Comparison Plots & Summary Tables
=====================================================================
Generates: FIFO vs P4CCI vs KBCS plots for IEEE paper.
Reads all 6 CSVs from results/ directory.
"""

import csv
import os
import math
import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
except ImportError:
    print("ERROR: pip install matplotlib numpy")
    exit(1)

# ─── Config ───────────────────────────────────────────────────────────────────
RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')
PLOTS_DIR = os.path.join(os.path.dirname(__file__), 'plots')
os.makedirs(PLOTS_DIR, exist_ok=True)

# Professional color palette
FIFO_COLOR  = '#EF4444'   # Red
P4CCI_COLOR = '#F59E0B'   # Amber/Orange
KBCS_COLOR  = '#3B82F6'   # Blue

plt.rcParams.update({
    'font.family': 'serif',
    'font.size': 11,
    'axes.titlesize': 14,
    'axes.titleweight': 'bold',
    'axes.labelsize': 12,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'axes.grid': True,
    'grid.alpha': 0.3,
    'axes.spines.top': False,
    'axes.spines.right': False,
})


def load_csv(filepath):
    rows = []
    if not os.path.exists(filepath):
        return rows
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            clean = {}
            for k, v in row.items():
                if k is None or v is None:
                    continue
                try:
                    clean[k] = float(v)
                except (ValueError, TypeError):
                    clean[k] = v
            rows.append(clean)
    return rows


def get_metric(rows, key):
    return [r[key] for r in rows if key in r]


def compute_stats(values):
    n = len(values)
    if n == 0:
        return {'mean': 0, 'std': 0, 'ci_lo': 0, 'ci_hi': 0, 'n': 0}
    mean = np.mean(values)
    std = np.std(values, ddof=1) if n > 1 else 0
    t_val = 2.045 if n >= 30 else 2.262
    se = std / math.sqrt(n)
    return {'mean': mean, 'std': std, 'ci_lo': mean - t_val * se, 'ci_hi': mean + t_val * se, 'n': n}


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 1: 3-Way JFI Comparison (Hero Chart)
# ═══════════════════════════════════════════════════════════════════════════════
def plot_jfi_3way(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c):
    fig, ax = plt.subplots(figsize=(9, 5.5))

    groups = ['Dumbbell\n(4 flows)', 'Cross\n(8 flows)']
    x = np.arange(len(groups))
    width = 0.22

    data = {
        'FIFO':  [get_metric(fifo_d, 'jfi'),  get_metric(fifo_c, 'jfi')],
        'P4CCI': [get_metric(p4cci_d, 'jfi'), get_metric(p4cci_c, 'jfi')],
        'KBCS':  [get_metric(kbcs_d, 'jfi'),  get_metric(kbcs_c, 'jfi')],
    }
    colors = [FIFO_COLOR, P4CCI_COLOR, KBCS_COLOR]
    labels = ['FIFO (No Fairness)', 'P4CCI (Static Queues)', 'KBCS (Ours)']

    for i, (key, col, lbl) in enumerate(zip(data.keys(), colors, labels)):
        means = [np.mean(v) if v else 0 for v in data[key]]
        stds  = [np.std(v) if v else 0 for v in data[key]]
        bars = ax.bar(x + (i - 1) * width, means, width, yerr=stds,
                      label=lbl, color=col, alpha=0.88, capsize=4,
                      edgecolor='white', linewidth=1.2)
        # Value labels on bars
        for j, (m, s) in enumerate(zip(means, stds)):
            ax.text(x[j] + (i - 1) * width, m + s + 0.008, f'{m:.3f}',
                    ha='center', va='bottom', fontsize=9, fontweight='bold', color=col)

    ax.set_ylabel("Jain's Fairness Index (JFI)")
    ax.set_title("Fairness Comparison: FIFO vs P4CCI vs KBCS")
    ax.set_xticks(x)
    ax.set_xticklabels(groups)
    ax.set_ylim(0.55, 1.08)
    ax.axhline(y=1.0, color='gray', linestyle='--', alpha=0.5, label='Perfect Fairness')
    ax.legend(loc='lower right', framealpha=0.9)

    fig.tight_layout()
    path = os.path.join(PLOTS_DIR, 'jfi_3way_comparison.png')
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 2: Throughput & Link Utilization 3-Way
# ═══════════════════════════════════════════════════════════════════════════════
def plot_throughput_3way(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    groups = ['Dumbbell', 'Cross']
    x = np.arange(len(groups))
    width = 0.22
    colors = [FIFO_COLOR, P4CCI_COLOR, KBCS_COLOR]
    labels = ['FIFO', 'P4CCI', 'KBCS (Ours)']

    # Throughput
    tp_data = {
        'FIFO':  [get_metric(fifo_d, 'agg_throughput_mbps'),  get_metric(fifo_c, 'agg_throughput_mbps')],
        'P4CCI': [get_metric(p4cci_d, 'agg_throughput_mbps'), get_metric(p4cci_c, 'agg_throughput_mbps')],
        'KBCS':  [get_metric(kbcs_d, 'agg_throughput_mbps'),  get_metric(kbcs_c, 'agg_throughput_mbps')],
    }
    for i, (key, col, lbl) in enumerate(zip(tp_data.keys(), colors, labels)):
        means = [np.mean(v) if v else 0 for v in tp_data[key]]
        stds  = [np.std(v) if v else 0 for v in tp_data[key]]
        ax1.bar(x + (i - 1) * width, means, width, yerr=stds,
                label=lbl, color=col, alpha=0.88, capsize=4, edgecolor='white', linewidth=1.2)
        for j, (m, s) in enumerate(zip(means, stds)):
            ax1.text(x[j] + (i - 1) * width, m + s + 0.05, f'{m:.2f}',
                     ha='center', va='bottom', fontsize=8, fontweight='bold', color=col)

    ax1.set_ylabel('Aggregate Throughput (Mbps)')
    ax1.set_title('Aggregate Throughput')
    ax1.set_xticks(x)
    ax1.set_xticklabels(groups)
    ax1.legend(loc='upper left', fontsize=9)

    # Link Utilization
    lu_data = {
        'FIFO':  [get_metric(fifo_d, 'link_util_pct'),  get_metric(fifo_c, 'link_util_pct')],
        'P4CCI': [get_metric(p4cci_d, 'link_util_pct'), get_metric(p4cci_c, 'link_util_pct')],
        'KBCS':  [get_metric(kbcs_d, 'link_util_pct'),  get_metric(kbcs_c, 'link_util_pct')],
    }
    for i, (key, col, lbl) in enumerate(zip(lu_data.keys(), colors, labels)):
        means = [np.mean(v) if v else 0 for v in lu_data[key]]
        stds  = [np.std(v) if v else 0 for v in lu_data[key]]
        ax2.bar(x + (i - 1) * width, means, width, yerr=stds,
                label=lbl, color=col, alpha=0.88, capsize=4, edgecolor='white', linewidth=1.2)
        for j, (m, s) in enumerate(zip(means, stds)):
            ax2.text(x[j] + (i - 1) * width, m + s + 0.5, f'{m:.1f}%',
                     ha='center', va='bottom', fontsize=8, fontweight='bold', color=col)

    ax2.set_ylabel('Link Utilization (%)')
    ax2.set_title('Link Utilization')
    ax2.set_xticks(x)
    ax2.set_xticklabels(groups)
    ax2.set_ylim(0, 115)
    ax2.legend(loc='upper left', fontsize=9)

    fig.tight_layout()
    path = os.path.join(PLOTS_DIR, 'throughput_utilization_3way.png')
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 3: JFI Box Plot (shows distribution across 30 runs)
# ═══════════════════════════════════════════════════════════════════════════════
def plot_jfi_boxplot(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Dumbbell
    dbell_data = [get_metric(fifo_d, 'jfi'), get_metric(p4cci_d, 'jfi'), get_metric(kbcs_d, 'jfi')]
    bp1 = ax1.boxplot(dbell_data, patch_artist=True, tick_labels=['FIFO', 'P4CCI', 'KBCS'],
                       widths=0.5, showmeans=True, meanprops=dict(marker='D', markerfacecolor='black', markersize=6))
    for patch, color in zip(bp1['boxes'], [FIFO_COLOR, P4CCI_COLOR, KBCS_COLOR]):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax1.set_ylabel("Jain's Fairness Index")
    ax1.set_title('Dumbbell Topology (4 Flows)')
    ax1.set_ylim(0.5, 1.05)
    ax1.axhline(y=1.0, color='gray', linestyle='--', alpha=0.4)

    # Cross
    cross_data = [get_metric(fifo_c, 'jfi'), get_metric(p4cci_c, 'jfi'), get_metric(kbcs_c, 'jfi')]
    bp2 = ax2.boxplot(cross_data, patch_artist=True, tick_labels=['FIFO', 'P4CCI', 'KBCS'],
                       widths=0.5, showmeans=True, meanprops=dict(marker='D', markerfacecolor='black', markersize=6))
    for patch, color in zip(bp2['boxes'], [FIFO_COLOR, P4CCI_COLOR, KBCS_COLOR]):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax2.set_ylabel("Jain's Fairness Index")
    ax2.set_title('Cross Topology (8 Flows)')
    ax2.set_ylim(0.5, 1.05)
    ax2.axhline(y=1.0, color='gray', linestyle='--', alpha=0.4)

    fig.suptitle('JFI Distribution Across 30 Runs', fontsize=14, fontweight='bold', y=1.02)
    fig.tight_layout()
    path = os.path.join(PLOTS_DIR, 'jfi_boxplot_3way.png')
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 4: JFI Over Runs (Line plot showing stability)
# ═══════════════════════════════════════════════════════════════════════════════
def plot_jfi_over_runs(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    for ax, title, fifo, p4cci, kbcs in [
        (ax1, 'Dumbbell Topology', fifo_d, p4cci_d, kbcs_d),
        (ax2, 'Cross Topology', fifo_c, p4cci_c, kbcs_c)
    ]:
        runs = range(1, 31)
        fifo_jfi = get_metric(fifo, 'jfi')[:30]
        p4cci_jfi = get_metric(p4cci, 'jfi')[:30]
        kbcs_jfi = get_metric(kbcs, 'jfi')[:30]

        ax.plot(runs, fifo_jfi, 'o-', color=FIFO_COLOR, alpha=0.7, markersize=4, label='FIFO')
        ax.plot(runs, p4cci_jfi, 's-', color=P4CCI_COLOR, alpha=0.7, markersize=4, label='P4CCI')
        ax.plot(runs, kbcs_jfi, '^-', color=KBCS_COLOR, alpha=0.7, markersize=4, label='KBCS (Ours)')

        # Mean lines
        ax.axhline(y=np.mean(fifo_jfi), color=FIFO_COLOR, linestyle=':', alpha=0.5)
        ax.axhline(y=np.mean(p4cci_jfi), color=P4CCI_COLOR, linestyle=':', alpha=0.5)
        ax.axhline(y=np.mean(kbcs_jfi), color=KBCS_COLOR, linestyle=':', alpha=0.5)

        ax.set_xlabel('Run Number')
        ax.set_ylabel("Jain's Fairness Index")
        ax.set_title(title)
        ax.set_ylim(0.5, 1.05)
        ax.legend(loc='lower right', fontsize=9)

    fig.suptitle('JFI Stability Across 30 Experimental Runs', fontsize=14, fontweight='bold', y=1.02)
    fig.tight_layout()
    path = os.path.join(PLOTS_DIR, 'jfi_over_runs_3way.png')
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 5: 4-Panel Multi-Metric (comprehensive view)
# ═══════════════════════════════════════════════════════════════════════════════
def plot_multi_metric_3way(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c):
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))

    metrics = [
        ("Jain's Fairness Index", 'jfi', axes[0, 0]),
        ('Agg. Throughput (Mbps)', 'agg_throughput_mbps', axes[0, 1]),
        ('Link Utilization (%)', 'link_util_pct', axes[1, 0]),
    ]
    # 4th panel = summary text table
    ax_table = axes[1, 1]

    groups = ['Dumbbell', 'Cross']
    x = np.arange(len(groups))
    width = 0.22
    colors = [FIFO_COLOR, P4CCI_COLOR, KBCS_COLOR]
    labels = ['FIFO', 'P4CCI', 'KBCS']

    all_datasets = {
        'FIFO':  {'d': fifo_d,  'c': fifo_c},
        'P4CCI': {'d': p4cci_d, 'c': p4cci_c},
        'KBCS':  {'d': kbcs_d,  'c': kbcs_c},
    }

    for title, key, ax in metrics:
        for i, (name, col, lbl) in enumerate(zip(all_datasets.keys(), colors, labels)):
            d_vals = get_metric(all_datasets[name]['d'], key)
            c_vals = get_metric(all_datasets[name]['c'], key)
            means = [np.mean(d_vals) if d_vals else 0, np.mean(c_vals) if c_vals else 0]
            stds  = [np.std(d_vals) if d_vals else 0, np.std(c_vals) if c_vals else 0]
            ax.bar(x + (i - 1) * width, means, width, yerr=stds,
                   label=lbl, color=col, alpha=0.85, capsize=4, edgecolor='white', linewidth=1)
        ax.set_title(title)
        ax.set_xticks(x)
        ax.set_xticklabels(groups)
        ax.legend(fontsize=8, loc='best')

    # Summary text table in 4th panel
    ax_table.axis('off')
    table_data = []
    for topo, label in [('d', 'Dumbbell'), ('c', 'Cross')]:
        fifo_jfi = np.mean(get_metric(all_datasets['FIFO'][topo], 'jfi'))
        p4cci_jfi = np.mean(get_metric(all_datasets['P4CCI'][topo], 'jfi'))
        kbcs_jfi = np.mean(get_metric(all_datasets['KBCS'][topo], 'jfi'))
        imp_over_fifo = ((kbcs_jfi - fifo_jfi) / fifo_jfi) * 100
        imp_over_p4cci = ((kbcs_jfi - p4cci_jfi) / p4cci_jfi) * 100
        table_data.append([label, f'{fifo_jfi:.4f}', f'{p4cci_jfi:.4f}',
                          f'{kbcs_jfi:.4f}', f'+{imp_over_p4cci:.1f}%'])

    table = ax_table.table(
        cellText=table_data,
        colLabels=['Topology', 'FIFO', 'P4CCI', 'KBCS', 'KBCS vs\nP4CCI'],
        cellLoc='center', loc='center',
        colColours=['#E5E7EB', '#FECACA', '#FDE68A', '#BFDBFE', '#D1FAE5']
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.8)
    ax_table.set_title('JFI Summary', fontweight='bold')

    fig.suptitle('FIFO vs P4CCI vs KBCS — Complete Metric Comparison', fontsize=15, fontweight='bold', y=1.01)
    fig.tight_layout()
    path = os.path.join(PLOTS_DIR, 'multi_metric_3way.png')
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY TABLE (printed to console + saved as text)
# ═══════════════════════════════════════════════════════════════════════════════
def print_summary_table(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c):
    print()
    print("=" * 110)
    print("  PUBLICATION SUMMARY TABLE — FIFO vs P4CCI vs KBCS (30 runs, 60s each)")
    print("=" * 110)

    header = f"  {'Topology':<12} {'Metric':<28} {'FIFO':<18} {'P4CCI':<18} {'KBCS (Ours)':<18} {'KBCS vs P4CCI':<14}"
    print(header)
    print("-" * 110)

    for topo_label, fifo, p4cci, kbcs in [
        ('Dumbbell', fifo_d, p4cci_d, kbcs_d),
        ('Cross', fifo_c, p4cci_c, kbcs_c)
    ]:
        metrics = [
            ("JFI", 'jfi'),
            ("Throughput (Mbps)", 'agg_throughput_mbps'),
            ("Link Util (%)", 'link_util_pct'),
        ]
        for name, key in metrics:
            f_vals = get_metric(fifo, key)
            p_vals = get_metric(p4cci, key)
            k_vals = get_metric(kbcs, key)

            f_s = compute_stats(f_vals)
            p_s = compute_stats(p_vals)
            k_s = compute_stats(k_vals)

            delta = k_s['mean'] - p_s['mean']
            pct = (delta / p_s['mean'] * 100) if p_s['mean'] > 0 else 0
            sign = "+" if delta > 0 else "-"

            f_str = f"{f_s['mean']:.4f}±{f_s['std']:.4f}"
            p_str = f"{p_s['mean']:.4f}±{p_s['std']:.4f}"
            k_str = f"{k_s['mean']:.4f}±{k_s['std']:.4f}"

            print(f"  {topo_label:<12} {name:<28} {f_str:<18} {p_str:<18} {k_str:<18} {sign}{abs(pct):>7.1f}%")
        print("-" * 110)

    print("=" * 110)
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    print("\n  KBCS v2 — Generating Publication Plots & Tables")
    print("=" * 55)

    # Load all CSVs
    kbcs_d  = load_csv(os.path.join(RESULTS_DIR, 'dumbbell_results.csv'))
    kbcs_c  = load_csv(os.path.join(RESULTS_DIR, 'cross_results.csv'))
    fifo_d  = load_csv(os.path.join(RESULTS_DIR, 'fifo_dumbbell_results.csv'))
    fifo_c  = load_csv(os.path.join(RESULTS_DIR, 'fifo_cross_results.csv'))
    p4cci_d = load_csv(os.path.join(RESULTS_DIR, 'p4cci_dumbbell_results.csv'))
    p4cci_c = load_csv(os.path.join(RESULTS_DIR, 'p4cci_cross_results.csv'))

    print(f"  KBCS  Dumbbell: {len(kbcs_d)} runs | Cross: {len(kbcs_c)} runs")
    print(f"  FIFO  Dumbbell: {len(fifo_d)} runs | Cross: {len(fifo_c)} runs")
    print(f"  P4CCI Dumbbell: {len(p4cci_d)} runs | Cross: {len(p4cci_c)} runs")
    print()

    if not all([kbcs_d, kbcs_c, fifo_d, fifo_c, p4cci_d, p4cci_c]):
        print("ERROR: Missing CSV files. Need all 6 in results/")
        return

    # Generate plots
    print("  Generating plots...")
    plot_jfi_3way(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c)
    plot_throughput_3way(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c)
    plot_jfi_boxplot(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c)
    plot_jfi_over_runs(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c)
    plot_multi_metric_3way(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c)

    # Print summary
    print_summary_table(fifo_d, p4cci_d, kbcs_d, fifo_c, p4cci_c, kbcs_c)

    print(f"  All plots saved to: {PLOTS_DIR}/")
    print("  DONE\n")


if __name__ == '__main__':
    main()
