#!/bin/bash
# ============================================================================
# KBCS v2 — Two-Pod Topology Experiment Runner
# ============================================================================
# Topology:   h1-h4 → S1(KBCS/L1) ═══3Mbps═══╗
#                                               CORE(S3,KBCS)═══ h9-h12
#             h5-h8 → S2(KBCS/L2) ═══3Mbps═══╝
#
# Bottleneck: S1-p5→CORE-p1 and S2-p5→CORE-p2, rate-limited to 250 pps (~3 Mbps)
# ============================================================================

cd "$(dirname "$0")"
KBCS_DIR="$(pwd)"
VENV_PYTHON=/home/p4/src/p4dev-python-venv/bin/python3

# Duration
DURATION=${1:-300}
if [[ "$1" == "--duration" ]]; then
    DURATION=${2:-300}
fi

# Cache sudo password
echo "p4" | sudo -S true > /dev/null 2>&1

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     KBCS v2 — Two-Pod Topology Experiment               ║"
echo "║     Duration: ${DURATION}s                                       ║"
echo "║     Bottleneck: 2 x 3 Mbps (S1->CORE, S2->CORE)        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Load TCP kernel modules ─────────────────────────────────────────
echo "[1/6] Loading TCP congestion control kernel modules..."
sudo modprobe tcp_bbr      2>/dev/null && echo "  ok tcp_bbr"      || echo "  skip tcp_bbr"
sudo modprobe tcp_vegas    2>/dev/null && echo "  ok tcp_vegas"    || echo "  skip tcp_vegas"
sudo modprobe tcp_illinois 2>/dev/null && echo "  ok tcp_illinois" || echo "  skip tcp_illinois"
echo ""

# ── Step 2: Start Docker (InfluxDB + Grafana) ───────────────────────────────
echo "[2/6] Starting Docker (InfluxDB + Grafana)..."
sudo docker-compose down -v 2>/dev/null || true
sudo docker-compose up -d 2>&1 | tail -3
echo "  Waiting for InfluxDB..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8086/ping > /dev/null 2>&1; then
        echo "  ok InfluxDB ready"
        break
    fi
    sleep 1
done
echo ""

# ── Step 3: Clean old Mininet state ─────────────────────────────────────────
echo "[3/6] Cleaning old Mininet state..."
sudo mn -c > /dev/null 2>&1 || true
sudo killall -9 simple_switch_grpc 2>/dev/null || true
sudo rm -rf pcaps/* logs/* 2>/dev/null || true
sleep 2
echo "  ok Clean"
echo ""

# ── Step 4: Start P4 Two-Pod topology + inject traffic ──────────────────────
echo "[4/6] Starting P4 Two-Pod topology and injecting traffic..."
echo "  Using Python: $VENV_PYTHON"

# 3 switches: S1(9090)=Pod-A Leaf, S2(9091)=Pod-B Leaf, S3(9092)=CORE
# Everything from here to MNEOF is piped as TEXT into the Mininet CLI.
# Lines that are not valid Mininet commands (like shell loops) are ignored.
# Valid Mininet commands: h1 ..., sh ..., exit
cat << MNEOF | sudo "$VENV_PYTHON" /home/p4/tutorials/utils/run_exercise.py \
    -t kbcs-topo/twopod_topology.json \
    -j p4src/kbcs_v2.json \
    -b simple_switch_grpc > /tmp/kbcs_twopod_mininet.log 2>&1 &
h9  iperf -s -D
h10 iperf -s -D
h11 iperf -s -D
h12 iperf -s -D
sh sleep 3
h1 iperf -c 10.0.3.1 -t $DURATION -P 1 &
h2 iperf -c 10.0.3.1 -t $DURATION -P 1 &
h3 iperf -c 10.0.3.2 -t $DURATION -P 1 &
h4 iperf -c 10.0.3.2 -t $DURATION -P 1 &
h5 iperf -c 10.0.4.1 -t $DURATION -P 1 &
h6 iperf -c 10.0.4.1 -t $DURATION -P 1 &
h7 iperf -c 10.0.4.2 -t $DURATION -P 1 &
h8 iperf -c 10.0.4.2 -t $DURATION -P 1 &
sh sleep $((DURATION + 15))
exit
MNEOF

MININET_PID=$!
echo "  Mininet PID: $MININET_PID"

# Wait for all 3 switches to come up (up to 60s each)
echo "  Waiting for switches..."
for port in 9090 9091 9092; do
    for i in $(seq 1 60); do
        if echo "" | simple_switch_CLI --thrift-port $port > /dev/null 2>&1; then
            echo "  ok Switch on port $port ready"
            break
        fi
        sleep 1
    done
done
sleep 3

# ── Set queue rate on bottleneck ports (OUTSIDE heredoc — real shell) ────────
# 250 pps * ~1500 bytes = ~3 Mbps bottleneck inside the P4 switch.
# S1 port 5 = uplink to CORE, S2 port 5 = uplink to CORE.
echo "  Setting bottleneck queue rate (250 pps)..."
echo "set_queue_rate 250 5" | simple_switch_CLI --thrift-port 9090 >/dev/null 2>&1
echo "  ok S1 (port 9090) port-5 -> 250 pps"
echo "set_queue_rate 250 5" | simple_switch_CLI --thrift-port 9091 >/dev/null 2>&1
echo "  ok S2 (port 9091) port-5 -> 250 pps"
echo ""

# ── Step 5: Start feeder + RL controllers ───────────────────────────────────
echo "[5/6] Starting telemetry feeder and RL controllers..."
source /home/p4/src/p4dev-python-venv/bin/activate 2>/dev/null || true

python3 telemetry/grafana_feeder.py > /tmp/kbcs_feeder.log 2>&1 &
FEEDER_PID=$!
echo "  ok Feeder PID: $FEEDER_PID"

# RL agent for S1 — Pod-A flows (fid 1-4)
python3 controller/rl_controller.py \
    --flows 1,2,3,4 \
    --duration $DURATION \
    --switches 9090 \
    --reset > /tmp/kbcs_rl_s1.log 2>&1 &
RL_S1_PID=$!
echo "  ok RL S1 (Pod-A) PID: $RL_S1_PID  (flows 1-4, port 9090)"

# RL agent for S2 — Pod-B flows (fid 1-4, independent karma domain)
python3 controller/rl_controller.py \
    --flows 1,2,3,4 \
    --duration $DURATION \
    --switches 9091 \
    --reset > /tmp/kbcs_rl_s2.log 2>&1 &
RL_S2_PID=$!
echo "  ok RL S2 (Pod-B) PID: $RL_S2_PID  (flows 1-4, port 9091)"

# RL agent for CORE — all 8 flows (fid 1-8, global karma)
python3 controller/rl_controller.py \
    --flows 1,2,3,4,5,6,7,8 \
    --duration $DURATION \
    --switches 9092 \
    --reset > /tmp/kbcs_rl_core.log 2>&1 &
RL_CORE_PID=$!
echo "  ok RL CORE PID: $RL_CORE_PID  (flows 1-8, port 9092)"
echo ""

# ── Step 6: Dashboard ────────────────────────────────────────────────────────
echo "[6/6] Dashboard..."
# Dashboard scripts are optional — skip if not present on VM
if [ -f dashboard/twopod_dashboard.py ]; then
    sudo fuser -k 5002/tcp 2>/dev/null || true
    sleep 1
    python3 dashboard/twopod_dashboard.py > /tmp/kbcs_twopod_dashboard.log 2>&1 &
    DASH_PID=$!
    echo "  ok Dashboard PID: $DASH_PID  (port 5002)"
else
    DASH_PID=""
    echo "  skip Dashboard (not found, use Grafana at :3000 instead)"
fi
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
echo "================================================================"
echo "         TWO-POD EXPERIMENT RUNNING"
echo "================================================================"
echo "  Grafana:        http://localhost:3000"
echo "  Duration:       ${DURATION}s"
echo "  Switches:  S1(L1)=9090  S2(L2)=9091  CORE=9092"
echo "  Bottleneck: S1->CORE 3Mbps  S2->CORE 3Mbps"
echo "  Logs:"
echo "    tail -f /tmp/kbcs_rl_s1.log"
echo "    tail -f /tmp/kbcs_rl_s2.log"
echo "    tail -f /tmp/kbcs_rl_core.log"
echo "================================================================"
echo ""
echo "Press Ctrl+C to stop early, or wait ${DURATION}s..."

sleep $DURATION

echo ""
echo "[cleanup] Reading final karma values..."

echo ""
echo "======== FINAL KARMA -- S1 / Pod-A (flows 1-4) ========"
for fid in 1 2 3 4; do
    karma=$(echo "register_read MyIngress.reg_karma $fid" | \
        simple_switch_CLI --thrift-port 9090 2>/dev/null | \
        grep '=' | awk -F'= ' '{print $2}')
    flow_names=("" "h1/CUBIC" "h2/BBR" "h3/Vegas" "h4/Illinois")
    echo "  Flow $fid (${flow_names[$fid]}): karma=$karma"
done

echo ""
echo "======== FINAL KARMA -- S2 / Pod-B (flows 1-4) ========"
for fid in 1 2 3 4; do
    karma=$(echo "register_read MyIngress.reg_karma $fid" | \
        simple_switch_CLI --thrift-port 9091 2>/dev/null | \
        grep '=' | awk -F'= ' '{print $2}')
    flow_names=("" "h5/CUBIC" "h6/BBR" "h7/Vegas" "h8/Illinois")
    echo "  Flow $fid (${flow_names[$fid]}): karma=$karma"
done

echo ""
echo "======== FINAL KARMA -- CORE (flows 1-8) =============="
for fid in 1 2 3 4 5 6 7 8; do
    karma=$(echo "register_read MyIngress.reg_karma $fid" | \
        simple_switch_CLI --thrift-port 9092 2>/dev/null | \
        grep '=' | awk -F'= ' '{print $2}')
    echo "  Flow $fid: karma=$karma"
done

if [ -n "$DASH_PID" ]; then
    kill $FEEDER_PID $RL_S1_PID $RL_S2_PID $RL_CORE_PID $DASH_PID 2>/dev/null || true
else
    kill $FEEDER_PID $RL_S1_PID $RL_S2_PID $RL_CORE_PID 2>/dev/null || true
fi
echo ""
echo "Done."
