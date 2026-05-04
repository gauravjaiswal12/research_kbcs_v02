#!/usr/bin/env python3
"""
KBCS v2 — Two-Pod / Two-Rack Topology Visualizer
==================================================
Layout (left → right):
  Pod-A senders (H1-H4)  →  L1 (KBCS)  ─3Mbps─┐
                                                  CORE (plain) → Receivers (Srv1-Srv4)
  Pod-B senders (H5-H8)  →  L2 (KBCS)  ─3Mbps─┘

Key property: exactly 4 flows per KBCS switch → no overload, no cascading.

Output: plots/topo_twopod.png
"""

import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

PLOTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'plots')
OUTPUT_PNG = os.path.join(PLOTS_DIR, 'topo_twopod.png')

CCA_COLORS = {
    'CUBIC':    '#2980B9',
    'BBR':      '#C0392B',
    'Vegas':    '#27AE60',
    'Illinois': '#8E44AD',
}

PODA_HOSTS = [('H1','CUBIC'), ('H2','BBR'), ('H3','Vegas'), ('H4','Illinois')]
PODB_HOSTS = [('H5','CUBIC'), ('H6','BBR'), ('H7','Vegas'), ('H8','Illinois')]


def draw_circle(ax, cx, cy, r, color, zorder=5, alpha=0.95):
    c = plt.Circle((cx, cy), r, color=color, zorder=zorder, alpha=alpha)
    ax.add_patch(c)
    return c


def draw_line(ax, x1, y1, x2, y2, color, lw, zorder=3, alpha=0.85, ls='-'):
    ax.plot([x1, x2], [y1, y2], color=color, linewidth=lw,
            zorder=zorder, alpha=alpha, linestyle=ls,
            solid_capstyle='round')


def edge_point(cx, cy, tx, ty, r):
    dx, dy = tx - cx, ty - cy
    d = np.sqrt(dx**2 + dy**2)
    return cx + r * dx / d, cy + r * dy / d


def draw_topology():
    fig, ax = plt.subplots(figsize=(18, 10))
    fig.patch.set_facecolor('#F8F9FA')
    ax.set_facecolor('#F8F9FA')
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 10)
    ax.axis('off')

    ax.set_title(
        'KBCS Two-Pod / Two-Rack Topology\n'
        '(4 flows per KBCS switch · No Overload · No Cascading · Independent Bottlenecks)',
        fontsize=14, fontweight='bold', pad=16, color='#2C3E50'
    )

    HOST_R   = 0.42
    SW_KBCS  = 0.70
    SW_PLAIN = 0.60
    SRV_R    = 0.42

    # ── Positions ─────────────────────────────────────────────────────────────
    # Pod-A hosts: top half, x=2.0, y=6.5..9.5
    poda_ys = np.linspace(6.6, 9.4, 4)
    # Pod-B hosts: bottom half, x=2.0, y=0.5..3.5
    podb_ys = np.linspace(0.6, 3.4, 4)

    host_x   = 2.0
    l1_x, l1_y   = 6.0, 8.0   # Pod-A leaf switch (KBCS)
    l2_x, l2_y   = 6.0, 2.0   # Pod-B leaf switch (KBCS)
    core_x, core_y = 11.0, 5.0  # Core switch (plain)
    srv_x = 15.5
    srv_ys = [8.0, 6.5, 3.5, 2.0]  # 4 receiver servers

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Access links: Pod-A hosts → L1
    # ─────────────────────────────────────────────────────────────────────────
    for i, (hid, cca) in enumerate(PODA_HOSTS):
        hy = poda_ys[i]
        x1, y1 = edge_point(host_x, hy, l1_x, l1_y, HOST_R)
        x2, y2 = edge_point(l1_x, l1_y, host_x, hy, SW_KBCS)
        draw_line(ax, x1, y1, x2, y2, '#7F8C8D', 1.5)
        # port label near switch
        ax.text((x1+x2)/2 - 0.25, (y1+y2)/2 + 0.12,
                f'p{i+1}', fontsize=6, color='#7F8C8D', ha='center')

    # 2. Access links: Pod-B hosts → L2
    for i, (hid, cca) in enumerate(PODB_HOSTS):
        hy = podb_ys[i]
        x1, y1 = edge_point(host_x, hy, l2_x, l2_y, HOST_R)
        x2, y2 = edge_point(l2_x, l2_y, host_x, hy, SW_KBCS)
        draw_line(ax, x1, y1, x2, y2, '#7F8C8D', 1.5)
        ax.text((x1+x2)/2 - 0.25, (y1+y2)/2 - 0.12,
                f'p{i+1}', fontsize=6, color='#7F8C8D', ha='center')

    # 3. Bottleneck links: L1 → CORE  and  L2 → CORE  (RED, thick)
    x1, y1 = edge_point(l1_x, l1_y, core_x, core_y, SW_KBCS)
    x2, y2 = edge_point(core_x, core_y, l1_x, l1_y, SW_PLAIN)
    draw_line(ax, x1, y1, x2, y2, '#E74C3C', 5.0, zorder=4)

    x1, y1 = edge_point(l2_x, l2_y, core_x, core_y, SW_KBCS)
    x2, y2 = edge_point(core_x, core_y, l2_x, l2_y, SW_PLAIN)
    draw_line(ax, x1, y1, x2, y2, '#E74C3C', 5.0, zorder=4)

    # Bottleneck labels
    ax.text(8.4, 7.2, '~3 Mbps / 250 pps / 5 ms\n← bottleneck →',
            ha='center', va='center', fontsize=8.5, color='#E74C3C',
            fontweight='bold', rotation=22,
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                      edgecolor='#E74C3C', linewidth=1.3, alpha=0.92))

    ax.text(8.4, 2.8, '~3 Mbps / 250 pps / 5 ms\n← bottleneck →',
            ha='center', va='center', fontsize=8.5, color='#E74C3C',
            fontweight='bold', rotation=-22,
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                      edgecolor='#E74C3C', linewidth=1.3, alpha=0.92))

    # 4. Core → Receivers  (10 Mbps, grey)
    for i, sy in enumerate(srv_ys):
        x1, y1 = edge_point(core_x, core_y, srv_x, sy, SW_PLAIN)
        x2, y2 = edge_point(srv_x, sy, core_x, core_y, SRV_R)
        draw_line(ax, x1, y1, x2, y2, '#95A5A6', 2.0, zorder=3)

    # ── Draw nodes ────────────────────────────────────────────────────────────
    # Pod-A hosts
    for i, (hid, cca) in enumerate(PODA_HOSTS):
        hy = poda_ys[i]
        draw_circle(ax, host_x, hy, HOST_R, CCA_COLORS[cca])
        ax.text(host_x, hy + 0.05, f'{hid}\n{cca}',
                ha='center', va='center', fontsize=7.5,
                fontweight='bold', color='white', zorder=6)

    # Pod-B hosts
    for i, (hid, cca) in enumerate(PODB_HOSTS):
        hy = podb_ys[i]
        draw_circle(ax, host_x, hy, HOST_R, CCA_COLORS[cca])
        ax.text(host_x, hy + 0.05, f'{hid}\n{cca}',
                ha='center', va='center', fontsize=7.5,
                fontweight='bold', color='white', zorder=6)

    # L1 (KBCS — dark navy)
    draw_circle(ax, l1_x, l1_y, SW_KBCS, '#2C3E50')
    ax.text(l1_x, l1_y + 0.05, 'L1\n(KBCS)\nPod-A Leaf',
            ha='center', va='center', fontsize=8,
            fontweight='bold', color='white', zorder=6)

    # L2 (KBCS — dark navy)
    draw_circle(ax, l2_x, l2_y, SW_KBCS, '#2C3E50')
    ax.text(l2_x, l2_y + 0.05, 'L2\n(KBCS)\nPod-B Leaf',
            ha='center', va='center', fontsize=8,
            fontweight='bold', color='white', zorder=6)

    # CORE (KBCS — dark navy, same as leaf switches)
    draw_circle(ax, core_x, core_y, SW_PLAIN, '#2C3E50')
    ax.text(core_x, core_y + 0.05, 'CORE\n(KBCS)\nAggr.',
            ha='center', va='center', fontsize=8,
            fontweight='bold', color='white', zorder=6)

    # Receivers
    srv_labels = ['Srv1', 'Srv2', 'Srv3', 'Srv4']
    for i, (sy, sl) in enumerate(zip(srv_ys, srv_labels)):
        draw_circle(ax, srv_x, sy, SRV_R, '#27AE60')
        ax.text(srv_x, sy + 0.05, f'{sl}\n(server)',
                ha='center', va='center', fontsize=7.5,
                fontweight='bold', color='white', zorder=6)

    # ── Access link bw labels ─────────────────────────────────────────────────
    ax.text(host_x - 0.5, 8.0, '100 Mbps\n/ 5 ms',
            ha='center', va='center', fontsize=7.5, color='#7F8C8D',
            fontstyle='italic')
    ax.text(host_x - 0.5, 2.0, '100 Mbps\n/ 5 ms',
            ha='center', va='center', fontsize=7.5, color='#7F8C8D',
            fontstyle='italic')
    ax.text(srv_x + 0.7, 5.0, '10 Mbps\n/ 1 ms',
            ha='center', va='center', fontsize=7.5, color='#95A5A6',
            fontstyle='italic')

    # ── Pod divider line ──────────────────────────────────────────────────────
    ax.axhline(y=5.0, xmin=0.05, xmax=0.45, color='#BDC3C7',
               linewidth=1.2, linestyle='--', zorder=2)
    ax.text(1.0, 5.05, 'Pod A', fontsize=8, color='#BDC3C7', va='bottom')
    ax.text(1.0, 4.95, 'Pod B', fontsize=8, color='#BDC3C7', va='top')

    # ── Layer headers ─────────────────────────────────────────────────────────
    ax.text(host_x, 9.9, 'Senders', ha='center', va='center',
            fontsize=11, color='#2C3E50', fontweight='bold')
    ax.text(l1_x,   9.9, 'Leaf Switches\n(KBCS)', ha='center', va='center',
            fontsize=11, color='#2C3E50', fontweight='bold')
    ax.text(core_x, 9.9, 'Core Switch\n(plain fwd)', ha='center', va='center',
            fontsize=11, color='#6D7F8B', fontweight='bold')
    ax.text(srv_x,  9.9, 'Receivers', ha='center', va='center',
            fontsize=11, color='#27AE60', fontweight='bold')

    # ── Key annotations ───────────────────────────────────────────────────────
    ax.annotate(
        'Exactly 4 flows per KBCS switch\nKarma enforced independently\nPFQ buffer cap per pod',
        xy=(l1_x, l1_y - SW_KBCS),
        xytext=(3.8, 5.3),
        fontsize=8.5, color='#8E44AD', ha='center',
        arrowprops=dict(arrowstyle='->', color='#8E44AD', lw=1.5),
        bbox=dict(boxstyle='round', facecolor='#EDE9F6',
                  edgecolor='#8E44AD', linewidth=1, alpha=0.93)
    )

    ax.annotate(
        'Aggregates all 8 flows\nKBCS enforces fairness\nacross both pods',
        xy=(core_x, core_y - SW_PLAIN),
        xytext=(11.2, 3.0),
        fontsize=8.5, color='#2C3E50', ha='center',
        arrowprops=dict(arrowstyle='->', color='#2C3E50', lw=1.5),
        bbox=dict(boxstyle='round', facecolor='#EBF5FB',
                  edgecolor='#2C3E50', linewidth=1, alpha=0.93)
    )

    # ── CCA info boxes ────────────────────────────────────────────────────────
    ax.text(0.35, 8.0,
            'Pod A:\nCUBIC  BBR\nVegas  Illinois',
            ha='left', va='center', fontsize=8, color='#2C3E50',
            bbox=dict(boxstyle='round', facecolor='#E8F4FD',
                      edgecolor='#4A90D9', linewidth=1.2, alpha=0.92))

    ax.text(0.35, 2.0,
            'Pod B:\nCUBIC  BBR\nVegas  Illinois',
            ha='left', va='center', fontsize=8, color='#2C3E50',
            bbox=dict(boxstyle='round', facecolor='#E8F4FD',
                      edgecolor='#4A90D9', linewidth=1.2, alpha=0.92))

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_handles = [
        mpatches.Patch(color='#2980B9', label='CUBIC  (H1, H5)'),
        mpatches.Patch(color='#C0392B', label='BBR  (H2, H6)'),
        mpatches.Patch(color='#27AE60', label='Vegas  (H3, H7)'),
        mpatches.Patch(color='#8E44AD', label='Illinois  (H4, H8)'),
        mpatches.Patch(color='#2C3E50', label='P4 KBCS Leaf Switch  (karma + PFQ)'),
        mpatches.Patch(color='#2C3E50', label='Core Switch  (KBCS — aggregation layer)'),
        plt.Line2D([0],[0], color='#E74C3C', linewidth=4,
                   label='Bottleneck Link  (~3 Mbps, 250 pps, 5 ms)'),
        plt.Line2D([0],[0], color='#95A5A6', linewidth=2,
                   label='Core/Access Links  (10–100 Mbps)'),
    ]
    ax.legend(handles=legend_handles, loc='lower center',
              ncol=4, fontsize=8.5, framealpha=0.93,
              bbox_to_anchor=(0.5, -0.01))

    plt.tight_layout(rect=[0, 0.07, 1, 1])
    return fig


if __name__ == '__main__':
    os.makedirs(PLOTS_DIR, exist_ok=True)
    fig = draw_topology()
    fig.savefig(OUTPUT_PNG, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    print(f'[OK] Two-Pod topology saved to: {OUTPUT_PNG}')
    plt.close(fig)
