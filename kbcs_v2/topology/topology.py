#!/usr/bin/env python3
"""
KBCS v2 — Two-Tier Multi-Bottleneck Topology
============================================
Topology (matches kbcs_topology.png in docs/):

  Host1 ─┐                    ┌─ Server1
  Host2 ─┤                    ├─ Server2
  Host3 ─┼── S1(KBCS) ───────►S3(KBCS)
  Host4 ─┘      ╲             └─ Server3 (cross via S3)
                 ╲    ┌──────►S4(KBCS)─┬─ Server3
  Host5 ─┐        ╲  /                 └─ Server4
  Host6 ─┤         ╲/
  Host7 ─┼── S2(KBCS) ──────►S3(KBCS)
  Host8 ─┘           ╲       (cross)
                       └─────►S4(KBCS)

Access Layer  : S1, S2  (each runs kbcs_v2.p4 independently)
Aggregation   : S3, S4  (each runs kbcs_v2.p4 independently)

Bottleneck links (all 10 Mbps, 1ms delay, max 1000 pkts):
  S1 <-> S3  (direct)
  S1 <-> S4  (cross-link)
  S2 <-> S3  (cross-link)
  S2 <-> S4  (direct)

Host links    : 100 Mbps, 2ms delay
Server links  : 100 Mbps, 2ms delay

CCA mix on senders (to create inter-CCA congestion):
  H1 → CUBIC     H5 → CUBIC
  H2 → BBR       H6 → BBR
  H3 → Vegas     H7 → Vegas
  H4 → Illinois  H8 → Illinois
"""

import os
import sys
from mininet.net import Mininet
from mininet.node import Host
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI

# ─── P4 switch support ────────────────────────────────────────────────────────
# We use the P4-capable switch provided by the P4 VM.
# Adjust the path if your VM has p4app or a different bmv2 wrapper.
sys.path.append('/home/p4/tutorials/utils')
try:
    from p4_mininet import P4Switch, P4Host
    P4_AVAILABLE = True
except ImportError:
    # Fall-back: run as plain OVSSwitch for topology verification only
    from mininet.node import OVSSwitch as P4Switch
    Host as P4Host
    P4_AVAILABLE = False
    print("[WARNING] p4_mininet not found — running topology without P4 program.")

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
P4_JSON       = os.path.join(SCRIPT_DIR, '..', 'p4src', 'kbcs_v2.json')
RUNTIME_JSON  = os.path.join(SCRIPT_DIR, '..', 'p4src', 'kbcs_v2_runtime.json')

# ─── Topology parameters ──────────────────────────────────────────────────────
# Bottleneck links (S1/S2 <-> S3/S4)
BOTTLENECK_BW    = 10    # Mbps  — the congestion point KBCS must control
BOTTLENECK_DELAY = '1ms'
BOTTLENECK_MAXQ  = 1000  # max queue size in packets

# Host / server access links (not the bottleneck)
ACCESS_BW        = 100   # Mbps
ACCESS_DELAY     = '2ms'


def build_topology():
    """Build and return the configured Mininet network."""

    net = Mininet(link=TCLink, autoSetMacs=True)

    info("*** Creating hosts (senders)\n")
    h1 = net.addHost('h1', ip='10.0.1.1/24', cls=P4Host if P4_AVAILABLE else Host)
    h2 = net.addHost('h2', ip='10.0.1.2/24', cls=P4Host if P4_AVAILABLE else Host)
    h3 = net.addHost('h3', ip='10.0.1.3/24', cls=P4Host if P4_AVAILABLE else Host)
    h4 = net.addHost('h4', ip='10.0.1.4/24', cls=P4Host if P4_AVAILABLE else Host)
    h5 = net.addHost('h5', ip='10.0.2.1/24', cls=P4Host if P4_AVAILABLE else Host)
    h6 = net.addHost('h6', ip='10.0.2.2/24', cls=P4Host if P4_AVAILABLE else Host)
    h7 = net.addHost('h7', ip='10.0.2.3/24', cls=P4Host if P4_AVAILABLE else Host)
    h8 = net.addHost('h8', ip='10.0.2.4/24', cls=P4Host if P4_AVAILABLE else Host)

    info("*** Creating servers (receivers)\n")
    srv1 = net.addHost('srv1', ip='10.0.3.1/24', cls=P4Host if P4_AVAILABLE else Host)
    srv2 = net.addHost('srv2', ip='10.0.3.2/24', cls=P4Host if P4_AVAILABLE else Host)
    srv3 = net.addHost('srv3', ip='10.0.4.1/24', cls=P4Host if P4_AVAILABLE else Host)
    srv4 = net.addHost('srv4', ip='10.0.4.2/24', cls=P4Host if P4_AVAILABLE else Host)

    info("*** Creating P4 switches (Access Layer: S1, S2)\n")
    switch_kwargs = {}
    if P4_AVAILABLE:
        switch_kwargs = {
            'sw_path': 'simple_switch',
            'json_path': P4_JSON,
            'thrift_port': 9090,   # S1 uses 9090, S2 uses 9091 (set below)
            'priority_queues_num': 3,   # 3 queues: 0=RED, 1=YELLOW, 2=GREEN
        }

    s1 = net.addSwitch('s1', cls=P4Switch, thrift_port=9090, **{k: v for k, v in switch_kwargs.items() if k != 'thrift_port'}) if P4_AVAILABLE else net.addSwitch('s1')
    s2 = net.addSwitch('s2', cls=P4Switch, thrift_port=9091, **{k: v for k, v in switch_kwargs.items() if k != 'thrift_port'}) if P4_AVAILABLE else net.addSwitch('s2')

    info("*** Creating P4 switches (Aggregation Layer: S3, S4)\n")
    s3 = net.addSwitch('s3', cls=P4Switch, thrift_port=9092, **{k: v for k, v in switch_kwargs.items() if k != 'thrift_port'}) if P4_AVAILABLE else net.addSwitch('s3')
    s4 = net.addSwitch('s4', cls=P4Switch, thrift_port=9093, **{k: v for k, v in switch_kwargs.items() if k != 'thrift_port'}) if P4_AVAILABLE else net.addSwitch('s4')

    info("*** Linking hosts to S1 (access)\n")
    net.addLink(h1, s1, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(h2, s1, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(h3, s1, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(h4, s1, bw=ACCESS_BW, delay=ACCESS_DELAY)

    info("*** Linking hosts to S2 (access)\n")
    net.addLink(h5, s2, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(h6, s2, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(h7, s2, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(h8, s2, bw=ACCESS_BW, delay=ACCESS_DELAY)

    info("*** Linking bottleneck links (Access <-> Aggregation, all 10 Mbps)\n")
    # Direct links
    net.addLink(s1, s3, bw=BOTTLENECK_BW, delay=BOTTLENECK_DELAY, max_queue_size=BOTTLENECK_MAXQ)
    net.addLink(s2, s4, bw=BOTTLENECK_BW, delay=BOTTLENECK_DELAY, max_queue_size=BOTTLENECK_MAXQ)
    # Cross-links (the key addition over simple dumbbell)
    net.addLink(s1, s4, bw=BOTTLENECK_BW, delay=BOTTLENECK_DELAY, max_queue_size=BOTTLENECK_MAXQ)
    net.addLink(s2, s3, bw=BOTTLENECK_BW, delay=BOTTLENECK_DELAY, max_queue_size=BOTTLENECK_MAXQ)

    info("*** Linking servers to S3 and S4 (aggregation)\n")
    net.addLink(s3, srv1, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(s3, srv2, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(s4, srv3, bw=ACCESS_BW, delay=ACCESS_DELAY)
    net.addLink(s4, srv4, bw=ACCESS_BW, delay=ACCESS_DELAY)

    return net


def configure_ccas(net):
    """
    Set CCA per sender host.
    This must run AFTER net.start() so the network interfaces exist.
    Creates inter-CCA competition: CUBIC vs BBR vs Vegas vs Illinois.
    """
    cca_map = {
        'h1': 'cubic',
        'h2': 'bbr',
        'h3': 'vegas',
        'h4': 'illinois',
        'h5': 'cubic',
        'h6': 'bbr',
        'h7': 'vegas',
        'h8': 'illinois',
    }
    for host_name, cca in cca_map.items():
        host = net.get(host_name)
        result = host.cmd(f'sysctl -w net.ipv4.tcp_congestion_control={cca} 2>&1')
        if 'Invalid argument' in result:
            info(f"[WARNING] {cca} not available on {host_name}, using cubic\n")
            host.cmd('sysctl -w net.ipv4.tcp_congestion_control=cubic')
        else:
            info(f"  {host_name}: CCA set to {cca}\n")


def start_iperf_servers(net):
    """Start iperf3 servers on all receiver hosts."""
    servers = ['srv1', 'srv2', 'srv3', 'srv4']
    for srv_name in servers:
        srv = net.get(srv_name)
        srv.cmd('iperf3 -s -D')   # -D = daemon mode
        info(f"  iperf3 server started on {srv_name} ({srv.IP()})\n")


def print_topology_summary(net):
    """Print a clear summary of the network for verification."""
    info("\n" + "="*60 + "\n")
    info("KBCS v2 - Two-Tier Multi-Bottleneck Topology - READY\n")
    info("="*60 + "\n")
    info("Access Layer Switches:\n")
    info(f"  S1 (Thrift: 9090) — hosts: h1(CUBIC) h2(BBR) h3(Vegas) h4(Illinois)\n")
    info(f"  S2 (Thrift: 9091) — hosts: h5(CUBIC) h6(BBR) h7(Vegas) h8(Illinois)\n")
    info("\nAggregation Layer Switches:\n")
    info(f"  S3 (Thrift: 9092) — servers: srv1 srv2\n")
    info(f"  S4 (Thrift: 9093) — servers: srv3 srv4\n")
    info("\nBottleneck Links (10 Mbps each):\n")
    info("  S1 <-> S3  [direct]       S1 <-> S4  [cross-link]\n")
    info("  S2 <-> S4  [direct]       S2 <-> S3  [cross-link]\n")
    info("\nTo start traffic manually inside Mininet CLI:\n")
    info("  h1 iperf3 -c srv1 -t 60 &\n")
    info("  h2 iperf3 -c srv1 -t 60 &\n")
    info("  h3 iperf3 -c srv2 -t 60 &\n")
    info("  h4 iperf3 -c srv2 -t 60 &\n")
    info("\nTo start the Q-Learning controller (in a separate terminal):\n")
    info("  python3 controller/rl_controller.py\n")
    info("="*60 + "\n")


def run():
    setLogLevel('info')

    info("*** Building KBCS v2 topology\n")
    net = build_topology()

    info("*** Starting network\n")
    net.start()

    info("*** Configuring CCAs on sender hosts\n")
    configure_ccas(net)

    info("*** Starting iperf3 servers on receiver hosts\n")
    start_iperf_servers(net)

    print_topology_summary(net)

    info("*** Opening Mininet CLI (type 'exit' to stop)\n")
    CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == '__main__':
    run()
