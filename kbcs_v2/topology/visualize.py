#!/usr/bin/env python3
"""
KBCS v2 — Topology Visualizer
================================
Draws the KBCS Two-Tier Multi-Bottleneck Topology using NetworkX
and Matplotlib. Can run in two modes:

  1. Static PNG  : Saves topology diagram to results/topology.png
  2. Live window : Opens an interactive Tk/Qt window (requires display)

This is the "topology visualization tool" required for project submission.
The diagram shows:
  - 4 P4 KBCS switches (S1, S2 = access, S3, S4 = aggregation)
  - 8 sender hosts (H1-H8) with their CCA types labeled
  - 4 receiver servers (Srv1-Srv4)
  - Bottleneck links in RED with bandwidth labels
  - Access links in BLUE
  - Cross-links clearly marked between access and aggregation layers

Usage (inside P4 VM):
  python3 topology/visualize.py              # saves PNG only
  python3 topology/visualize.py --show       # also opens interactive window
  python3 topology/visualize.py --live       # refreshes every 5s (for demo)
"""

import argparse
import os
import sys
import time

import matplotlib
matplotlib.use('Agg')   # default non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx
import numpy as np

RESULTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'results')
OUTPUT_PNG  = os.path.join(RESULTS_DIR, 'topology.png')

# ─── Node definitions ─────────────────────────────────────────────────────────

NODES = {
    # Sender hosts (left side)
    'H1': {'layer': 0, 'pos': (0.0, 8.0), 'type': 'host',   'label': 'H1\nCUBIC'},
    'H2': {'layer': 0, 'pos': (0.0, 6.5), 'type': 'host',   'label': 'H2\nBBR'},
    'H3': {'layer': 0, 'pos': (0.0, 5.5), 'type': 'host',   'label': 'H3\nVegas'},
    'H4': {'layer': 0, 'pos': (0.0, 4.0), 'type': 'host',   'label': 'H4\nIllinois'},
    'H5': {'layer': 0, 'pos': (0.0, 2.5), 'type': 'host',   'label': 'H5\nCUBIC'},
    'H6': {'layer': 0, 'pos': (0.0, 1.5), 'type': 'host',   'label': 'H6\nBBR'},
    'H7': {'layer': 0, 'pos': (0.0, 0.5), 'type': 'host',   'label': 'H7\nVegas'},
    'H8': {'layer': 0, 'pos': (0.0,-0.5), 'type': 'host',   'label': 'H8\nIllinois'},

    # Access layer switches
    'S1': {'layer': 1, 'pos': (3.0, 6.0), 'type': 'switch', 'label': 'S1\n(KBCS)\nAccess'},
    'S2': {'layer': 1, 'pos': (3.0, 1.0), 'type': 'switch', 'label': 'S2\n(KBCS)\nAccess'},

    # Aggregation layer switches
    'S3': {'layer': 2, 'pos': (7.0, 6.0), 'type': 'switch', 'label': 'S3\n(KBCS)\nAggr.'},
    'S4': {'layer': 2, 'pos': (7.0, 1.0), 'type': 'switch', 'label': 'S4\n(KBCS)\nAggr.'},

    # Receiver servers (right side)
    'Srv1': {'layer': 3, 'pos': (10.0, 7.0), 'type': 'server', 'label': 'Server 1'},
    'Srv2': {'layer': 3, 'pos': (10.0, 5.0), 'type': 'server', 'label': 'Server 2'},
    'Srv3': {'layer': 3, 'pos': (10.0, 2.0), 'type': 'server', 'label': 'Server 3'},
    'Srv4': {'layer': 3, 'pos': (10.0, 0.0), 'type': 'server', 'label': 'Server 4'},
}

# ─── Edge definitions ─────────────────────────────────────────────────────────

EDGES = [
    # Host → S1 (access, 100 Mbps)
    ('H1', 'S1', {'bw': '100M', 'type': 'access'}),
    ('H2', 'S1', {'bw': '100M', 'type': 'access'}),
    ('H3', 'S1', {'bw': '100M', 'type': 'access'}),
    ('H4', 'S1', {'bw': '100M', 'type': 'access'}),

    # Host → S2 (access, 100 Mbps)
    ('H5', 'S2', {'bw': '100M', 'type': 'access'}),
    ('H6', 'S2', {'bw': '100M', 'type': 'access'}),
    ('H7', 'S2', {'bw': '100M', 'type': 'access'}),
    ('H8', 'S2', {'bw': '100M', 'type': 'access'}),

    # S1 → S3  (direct bottleneck, 10 Mbps)
    ('S1', 'S3', {'bw': '10M',  'type': 'bottleneck'}),
    # S2 → S4  (direct bottleneck, 10 Mbps)
    ('S2', 'S4', {'bw': '10M',  'type': 'bottleneck'}),
    # S1 → S4  (cross-link, 10 Mbps)
    ('S1', 'S4', {'bw': '10M',  'type': 'crosslink'}),
    # S2 → S3  (cross-link, 10 Mbps)
    ('S2', 'S3', {'bw': '10M',  'type': 'crosslink'}),

    # S3 → Servers (100 Mbps)
    ('S3', 'Srv1', {'bw': '100M', 'type': 'access'}),
    ('S3', 'Srv2', {'bw': '100M', 'type': 'access'}),
    # S4 → Servers (100 Mbps)
    ('S4', 'Srv3', {'bw': '100M', 'type': 'access'}),
    ('S4', 'Srv4', {'bw': '100M', 'type': 'access'}),
]

# ─── Colours & styles ─────────────────────────────────────────────────────────

NODE_COLORS = {
    'host'  : '#4A90D9',   # steel blue
    'switch': '#2C3E50',   # dark navy (P4 switch)
    'server': '#27AE60',   # green
}

NODE_SIZES = {
    'host'  : 800,
    'switch': 1800,
    'server': 900,
}

EDGE_COLORS = {
    'access'    : '#7F8C8D',   # grey
    'bottleneck': '#E74C3C',   # red — the critical link
    'crosslink' : '#E74C3C',   # red — also bottleneck capacity
}

EDGE_WIDTHS = {
    'access'    : 1.5,
    'bottleneck': 3.5,
    'crosslink' : 3.5,
}

EDGE_STYLES = {
    'access'    : 'solid',
    'bottleneck': 'solid',
    'crosslink' : 'dashed',   # dashed to distinguish cross-links visually
}


# ─── Drawing ──────────────────────────────────────────────────────────────────

def draw_topology(title: str = "KBCS Two-Tier Multi-Bottleneck Topology"):
    G = nx.Graph()

    # Add nodes
    for node_id, attrs in NODES.items():
        G.add_node(node_id, **attrs)

    # Add edges
    for src, dst, attrs in EDGES:
        G.add_edge(src, dst, **attrs)

    # Fixed positions from our layout
    pos = {nid: attrs['pos'] for nid, attrs in NODES.items()}

    # ── Figure setup ──────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor('#F8F9FA')
    ax.set_facecolor('#F8F9FA')
    ax.set_title(title, fontsize=16, fontweight='bold', pad=20,
                 color='#2C3E50')

    # ── Draw edges by type ────────────────────────────────────────────────────
    for edge_type in ['access', 'bottleneck', 'crosslink']:
        edges_of_type = [(u, v) for u, v, d in G.edges(data=True)
                         if d.get('type') == edge_type]
        if not edges_of_type:
            continue

        # Dashed style for cross-links
        style = EDGE_STYLES[edge_type]
        nx.draw_networkx_edges(
            G, pos,
            edgelist=edges_of_type,
            edge_color=EDGE_COLORS[edge_type],
            width=EDGE_WIDTHS[edge_type],
            style=style,
            alpha=0.85,
            ax=ax
        )

    # ── Draw nodes by type ────────────────────────────────────────────────────
    for node_type in ['host', 'switch', 'server']:
        nodes_of_type = [n for n, d in G.nodes(data=True)
                         if d.get('type') == node_type]
        nx.draw_networkx_nodes(
            G, pos,
            nodelist=nodes_of_type,
            node_color=NODE_COLORS[node_type],
            node_size=NODE_SIZES[node_type],
            ax=ax,
            alpha=0.95
        )

    # ── Node labels ───────────────────────────────────────────────────────────
    labels = {nid: attrs['label'] for nid, attrs in NODES.items()}
    nx.draw_networkx_labels(
        G, pos, labels=labels,
        font_size=7.5, font_color='white',
        font_weight='bold', ax=ax
    )

    # ── Edge bandwidth labels ─────────────────────────────────────────────────
    edge_labels = {(u, v): d['bw'] for u, v, d in G.edges(data=True)}
    nx.draw_networkx_edge_labels(
        G, pos, edge_labels=edge_labels,
        font_size=7, font_color='#2C3E50',
        bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7),
        ax=ax
    )

    # ── Layer labels ─────────────────────────────────────────────────────────
    ax.text(0.0, 9.2, "Senders\n(Hosts)", ha='center', va='center',
            fontsize=9, color='#4A90D9', fontweight='bold', transform=ax.transData)
    ax.text(3.0, 9.2, "Access\nLayer", ha='center', va='center',
            fontsize=9, color='#2C3E50', fontweight='bold', transform=ax.transData)
    ax.text(7.0, 9.2, "Aggregation\nLayer", ha='center', va='center',
            fontsize=9, color='#2C3E50', fontweight='bold', transform=ax.transData)
    ax.text(10.0, 9.2, "Receivers\n(Servers)", ha='center', va='center',
            fontsize=9, color='#27AE60', fontweight='bold', transform=ax.transData)

    # ── Legend ───────────────────────────────────────────────────────────────
    legend_handles = [
        mpatches.Patch(color='#4A90D9',   label='Sender Host (100 Mbps access)'),
        mpatches.Patch(color='#2C3E50',   label='P4 KBCS Switch (independent karma)'),
        mpatches.Patch(color='#27AE60',   label='Receiver Server (100 Mbps access)'),
        mpatches.Patch(color='#E74C3C',   label='Bottleneck / Cross-link (10 Mbps)'),
        plt.Line2D([0],[0], color='#E74C3C', linewidth=2.5,
                   linestyle='dashed',          label='Cross-link (S1↔S4, S2↔S3)'),
    ]
    ax.legend(handles=legend_handles, loc='lower center',
              ncol=3, fontsize=8.5, framealpha=0.9,
              bbox_to_anchor=(0.5, -0.08))

    # ── Annotation: key research point ───────────────────────────────────────
    ax.annotate(
        "Each switch independently\ntracks karma per flow",
        xy=(5.0, 3.5), xytext=(5.0, -1.2),
        fontsize=8, color='#8E44AD',
        ha='center',
        arrowprops=dict(arrowstyle='->', color='#8E44AD', lw=1.5),
        bbox=dict(boxstyle='round', facecolor='#EDE9F6', alpha=0.85)
    )

    ax.set_xlim(-1.5, 11.5)
    ax.set_ylim(-2.0, 10.0)
    ax.axis('off')
    plt.tight_layout(rect=[0, 0.05, 1, 1])

    return fig


def save_png(fig):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    fig.savefig(OUTPUT_PNG, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    print(f"[Visualizer] Topology saved to: {OUTPUT_PNG}")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description='KBCS v2 Topology Visualizer')
    parser.add_argument('--show', action='store_true',
                        help='Open interactive window (requires X11/display)')
    parser.add_argument('--live', action='store_true',
                        help='Refresh every 5s for live demo (with --show)')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()

    if args.show:
        matplotlib.use('TkAgg')  # switch to interactive backend

    while True:
        fig = draw_topology()
        save_png(fig)

        if args.show:
            plt.show()

        if not args.live:
            break

        print("[Visualizer] Refreshing in 5 seconds...")
        plt.close('all')
        time.sleep(5)

    print("[Visualizer] Done.")
