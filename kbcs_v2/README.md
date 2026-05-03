# KBCS v2 — Karma-Based Congestion Signaling (Version 2)

> **What is this folder?**
> This is a complete, clean rebuild of KBCS following the revised methodology (April 2026).
> The old implementation is preserved in `../kbcs/` for reference. This version incorporates:
> - Multi-switch topology (2-switch dumbbell via Mininet)
> - RED streak recovery mechanism
> - PFQ-inspired proactive buffer reservation
> - Q-Learning adaptive controller (per-switch independent control)
> - Telemetry pipeline (P4 clone → InfluxDB → Grafana)

---

## Folder Structure

```
kbcs_v2/
├── README.md                ← You are here. Start here.
├── p4src/
│   └── kbcs_v2.p4           ← The P4 program running inside each switch
├── controller/
│   └── rl_controller.py     ← Python Q-Learning controller
├── topology/
│   └── topology.py          ← Mininet script: hosts, 2 switches, bottleneck link
├── telemetry/
│   └── int_collector.py     ← Catches cloned INT packets, writes to InfluxDB
└── results/
    └── .gitkeep             ← Experiment outputs (CSVs, graphs) go here
```

---

## What Each File Does

### `p4src/kbcs_v2.p4` — The Brain of Each Switch

This is the P4 program that runs inside the BMv2 software switch. Every TCP packet
that enters the switch goes through this pipeline:

```
Packet In
   ↓
1. Flow ID Lookup       → Map source IP to a flow number (1, 2, 3...)
   ↓
2. Byte Counting        → Add packet size to reg_bytes[flow_id]
   ↓
3. Window Check         → Has 15ms elapsed since last evaluation?
   If YES:
     ↓
4. Karma Update         → Compare bytes against fair_bytes
                          If over → apply penalty (proportional)
                          If under → apply reward (+4 points)
   ↓
5. Color Assignment     → karma 76-100 = GREEN
                          karma 41-75  = YELLOW
                          karma 0-40   = RED
   ↓
6. Enforcement          → GREEN: 100% budget, 10% drop if over, ECN mark
                          YELLOW: 75% budget, 35% drop if over
                          RED:    25% budget, 90% drop if over
   ↓
7. RED Streak Tracking  → Increment reg_red_streak[flow_id]
                          If streak >= 20 windows (300ms):
                            Reset karma to 30, reset streak
   ↓
8. Priority Queue       → GREEN → Queue 2 (highest)
                          YELLOW → Queue 1
                          RED    → Queue 0 (lowest)
   ↓
9. Telemetry Clone      → On color change or every 8th packet:
                          Clone packet → redirect to CPU port → collector
   ↓
Packet Out (or Dropped)
```

**Key P4 registers used:**

| Register | Size | What it stores |
|---|---|---|
| `reg_bytes` | 1024 entries | Bytes sent per flow in current 15ms window |
| `reg_karma` | 1024 entries | Current karma score per flow (0-100) |
| `reg_color` | 1024 entries | Current color zone per flow (0=RED, 1=YELLOW, 2=GREEN) |
| `reg_red_streak` | 1024 entries | Consecutive RED windows per flow |
| `reg_pkt_count` | 1024 entries | Total packets seen per flow (for telemetry scheduling) |
| `reg_drops` | 1024 entries | Total drops per flow (read by controller) |
| `fair_bytes` | 1 entry | Set by controller — max bytes per flow per window |
| `penalty_amt` | 1 entry | Set by controller — karma penalty magnitude |
| `reward_amt` | 1 entry | Set by controller — karma reward magnitude |

---

### `topology/topology.py` — The Network in Mininet

Creates the **KBCS Two-Tier Multi-Bottleneck Topology** with 4 KBCS switches and cross-links:

```
                  Access Layer          Aggregation Layer

Host 1 ─┐                                        ┌─ Server 1
Host 2 ─┤                          ┌── S3 (KBCS)─┤
Host 3 ─┼── S1 (KBCS) ────────────┤              └─ Server 2
Host 4 ─┘         ╲               └── S4 (KBCS)─┐
                    ╲ (cross-link)               ├─ Server 3
Host 5 ─┐            ╲            ┌── S3 (KBCS)  └─ Server 4
Host 6 ─┤             ╲           │
Host 7 ─┼── S2 (KBCS) ────────────┤
Host 8 ─┘                         └── S4 (KBCS)
```

**All bottleneck links are 10 Mbps:**
- S1 ↔ S3 (direct)
- S1 ↔ S4 (cross-link)
- S2 ↔ S3 (cross-link)
- S2 ↔ S4 (direct)

- **H1–H4**: Senders on S1's access side, running CUBIC, BBR, Vegas, Illinois
- **H5–H8**: Senders on S2's access side, running another mix of CCAs
- **S1, S2 (Access Layer)**: Run `kbcs_v2.p4`, evaluate all flows from hosts
- **S3, S4 (Aggregation Layer)**: Run `kbcs_v2.p4`, evaluate flows coming from access switches
- **Server 1–2**: Connected to S3, running `iperf3` servers
- **Server 3–4**: Connected to S4, running `iperf3` servers

**What the cross-links prove:**
A flow from H1 can reach Server 3 via two paths: S1→S3→Server3 OR S1→S4→Server3. This creates realistic multi-path congestion scenarios where different paths have different karma states, and tests whether independent KBCS instances at every switch correctly handle overlapping flows without coordination.

---

### `controller/rl_controller.py` — The Q-Learning Brain

This Python script runs on the host machine alongside Mininet. It connects to each switch via P4Runtime/Thrift API and runs one control loop every 2 seconds.

**For each switch independently:**
1. Read per-flow packet counts, drops, and ECN marks from P4 registers
2. Compute throughput per flow
3. Compute Jain's Fairness Index (JFI)
4. Determine current state: (JFI bucket, utilization bucket, flow count bucket)
5. Look up best action in shared Q-table
6. Execute action (adjust penalty, reward, or headroom)
7. Recalculate fair_bytes = (link_rate × window) / active_flows × headroom
8. Write updated parameters to P4 registers
9. After 2 seconds: measure reward, update Q-table

**State space:** 4 × 4 × 4 = 64 states
**Action space:** 7 actions (increase/decrease penalty, reward, budget, or hold)
**Q-table size:** 64 × 7 = 448 entries (tiny, converges fast)

**Reward function:**
```
Reward = 10 × (JFI improvement) + 3 × (link utilization) − 5 × (starvation count)
```

The Q-table is shared across both switches. When S1 learns that "increase penalty
in a high-congestion state improves JFI," S2 benefits from that knowledge too. But
each switch's parameters are set independently based on its own local JFI reading.

---

### `telemetry/int_collector.py` — Watching the Network in Real Time

The P4 switch clones packets when flow states change and sends them to a dedicated
monitoring port. This script:
1. Listens on that monitoring interface
2. Extracts per-flow telemetry (flow_id, karma, color, drops, bytes)
3. Writes timestamped records to InfluxDB
4. Grafana dashboards read from InfluxDB to show live karma trajectories,
   per-flow throughput, JFI, and drop rates

---

## How to Run (Full Experiment)

> **Note:** All commands run INSIDE the P4 VM via SSH.
> `ssh p4@localhost -p 2222` (password: p4)

**Step 1** — Compile the P4 program:
```bash
p4c --target bmv2 --arch v1model -o p4src/ p4src/kbcs_v2.p4
```

**Step 2** — Start the Mininet topology:
```bash
sudo python3 topology/topology.py
```

**Step 3** — In a new terminal, start the controller:
```bash
python3 controller/rl_controller.py
```

**Step 4** — In another terminal, start the telemetry collector:
```bash
python3 telemetry/int_collector.py
```

**Step 5** — Generate traffic from inside Mininet:
```
mininet> h1 iperf3 -c h5 -t 60 -C cubic &
mininet> h2 iperf3 -c h6 -t 60 -C bbr &
mininet> h3 iperf3 -c h7 -t 60 -C vegas &
mininet> h4 iperf3 -c h8 -t 60 -C illinois &
```

**Step 6** — After 60 seconds, collect results:
```bash
python3 controller/rl_controller.py --report > results/run_1.csv
```

---

## What Changed from kbcs/ (Version 1)

| Feature | kbcs/ (v1) | kbcs_v2/ (this) |
|---|---|---|
| Topology | 1 switch, 1 bottleneck | 2 switches, 2 hops |
| Controller | Rule-based (if JFI < 0.7) | Q-Learning with 64 states |
| Recovery | None — RED flows starved forever | RED streak → karma reset after 300ms |
| Buffer management | Equal allocation | PFQ-inspired: RED gets 25%, recycled to GREEN |
| Telemetry | Basic print to terminal | Clone → InfluxDB → Grafana dashboard |
| Parameter tuning | Static hardcoded values | Dynamic: fair_bytes, penalty, reward all adapt |
| CCA treatment | Blind (all flows equal) | Implicit via karma (BBR drops to RED naturally) |

---

## Key Research Contribution

KBCS v2 is the **first AQM system that introduces long-term behavioral memory
into the P4 data plane.** Every other AQM (RED, CoDel, P4air, CCQM) judges
a flow only on what it is doing right now. KBCS judges a flow on what it has
been doing over its entire lifetime in the network.

This one change — adding memory — enables two things no other system provides:
1. **Resistance to gaming:** Bursty flows cannot escape punishment by briefly pausing.
2. **Formal recovery:** Penalized flows get a second chance after sustained good behavior,
   eliminating permanent starvation without sacrificing fairness enforcement.
