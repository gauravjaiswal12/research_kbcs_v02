#!/usr/bin/env python3
"""
KBCS v2 — Per-Run Metrics Collector
=====================================
Called by test_suite.sh after each run to read all P4 registers
and append one CSV row with the computed metrics.

Usage:
  python3 collect_metrics.py --run 1 --topo cross --duration 60 --csv results/cross_results.csv --thrift-port 9090
"""

import argparse
import subprocess
import csv
import os


def read_register(thrift_port, register_name, index):
    """Read a single register value from a BMv2 switch."""
    try:
        cmd = f"echo 'register_read {register_name} {index}' | simple_switch_CLI --thrift-port {thrift_port} 2>/dev/null"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
        for line in result.stdout.split('\n'):
            if '=' in line and register_name.split('.')[-1] in line:
                val_str = line.split('=')[-1].strip()
                return int(val_str)
    except Exception:
        pass
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=int, required=True)
    parser.add_argument('--topo', type=str, required=True)
    parser.add_argument('--duration', type=int, required=True)
    parser.add_argument('--csv', type=str, required=True)
    parser.add_argument('--thrift-port', type=int, default=9090)
    args = parser.parse_args()

    # Read per-flow metrics
    # For cross topology: S1 has flows 1-4, S2 has flows 5-8
    # For dumbbell: S1 has all flows 1-4
    karma = []
    fwd = []
    drops = []

    # Always read flows 1-4 from S1 (port 9090)
    for fid in range(1, 5):
        karma.append(read_register(9090, "MyIngress.reg_karma", fid))
        fwd.append(read_register(9090, "MyIngress.reg_forwarded_bytes", fid))
        drops.append(read_register(9090, "MyEgress.reg_drops", fid))

    # For cross topology, also read flows 5-8 from S2 (port 9091)
    if args.topo == "cross":
        for fid in range(5, 9):
            karma.append(read_register(9091, "MyIngress.reg_karma", fid))
            fwd.append(read_register(9091, "MyIngress.reg_forwarded_bytes", fid))
            drops.append(read_register(9091, "MyEgress.reg_drops", fid))

    # ── Compute metrics ──────────────────────────────────────────────────────

    # Jain's Fairness Index (from forwarded bytes = proxy for throughput)
    n = len(fwd)
    sum_x = sum(fwd)
    sum_x2 = sum(x * x for x in fwd)
    if sum_x2 > 0 and sum_x > 0:
        jfi = (sum_x ** 2) / (n * sum_x2)
    else:
        jfi = 0.0

    # Aggregate throughput (Mbps)
    agg_throughput_mbps = (sum_x * 8) / (args.duration * 1_000_000) if args.duration > 0 else 0.0

    # Link utilization (bottleneck = 3 Mbps per bottleneck link)
    # Cross has 2 bottleneck links (6 Mbps total), dumbbell has 1 (3 Mbps)
    total_link_capacity = 6.0 if args.topo == "cross" else 3.0
    link_util_pct = min((agg_throughput_mbps / total_link_capacity) * 100.0, 100.0)

    # Packet Drop Ratio
    total_drops = sum(drops)
    total_drop_bytes = total_drops * 1500
    total_bytes = sum_x + total_drop_bytes
    pdr_pct = (total_drop_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0

    # Average karma
    avg_karma = sum(karma) / len(karma) if karma else 0.0

    # ── Append to CSV ────────────────────────────────────────────────────────
    num_flows = len(karma)
    row = {
        'run': args.run,
        'topology': args.topo,
        'duration': args.duration,
        'num_flows': num_flows,
        'jfi': round(jfi, 4),
        'agg_throughput_mbps': round(agg_throughput_mbps, 4),
        'link_util_pct': round(link_util_pct, 2),
        'pdr_pct': round(pdr_pct, 4),
        'avg_karma': round(avg_karma, 2),
    }
    # Add per-flow data
    for i in range(num_flows):
        row[f'karma_{i+1}'] = karma[i]
        row[f'fwd_{i+1}'] = fwd[i]
        row[f'drops_{i+1}'] = drops[i]

    fieldnames = list(row.keys())
    file_exists = os.path.exists(args.csv)

    with open(args.csv, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    # Print summary
    karma_str = ','.join(str(k) for k in karma)
    fwd_str = ','.join(str(f) for f in fwd)
    print(f"    Run {args.run}: JFI={jfi:.4f}  Throughput={agg_throughput_mbps:.2f} Mbps  "
          f"LinkUtil={link_util_pct:.1f}%  PDR={pdr_pct:.2f}%  "
          f"AvgKarma={avg_karma:.0f}  Karma=[{karma_str}]  "
          f"({num_flows} flows)")


if __name__ == '__main__':
    main()

