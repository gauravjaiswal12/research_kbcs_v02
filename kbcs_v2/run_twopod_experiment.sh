#!/bin/bash
# ============================================================================
# KBCS v2 — Two-Pod Topology Experiment Runner
# ============================================================================
# Topology:   h1-h4 → S1(KBCS) ═══3Mbps═══ CORE(S3,KBCS) ═══ h9-h12
#             h5-h8 → S2(KBCS) ═══3Mbps═══╝
#
# Two independent 3 Mbps bottlenecks (S1→CORE and S2→CORE).
# Max 4 flows per leaf switch. No cross-links. No flow overload.
# CORE tracks all 8 flows for global karma enforcement.
#
# Traffic flows:
#   Pod-A: h1(CUBIC)→h9, h2(BBR)→h9, h3(Vegas)→h10, h4(Illinois)→h10
#   Pod-B: h5(CUBIC)→h11, h6(BBR)→h11, h7(Vegas)→h12, h8(Illinois)→h12
# ============================================================================

cd "$(dirname "$0")"
KBCS_DIR="$(pwd)"
VENV_PYTHON=/home/p4/src/p4dev-python-venv/bin/python3

DURATION=${1:-300}
if [[ "$1" == "--duration" ]]; then
    DURATION=${2:-300}
fi

echo "p4" | sudo -S true > /dev/null 2>&1

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     KBCS v2 — Two-Pod Topology Experiment               ║"
echo "║     Duration: ${DURATION}s                                       ║"
echo "║     Bottleneck: 2 × 3 Mbps (S1→CORE, S2→CORE)          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Load TCP kernel modules ─────────────────────────────────────────
echo "[1/6] Loading TCP congestion control kernel modules..."
sudo modprobe tcp_bbr      2>/dev/null && echo "  ✓ tcp_bbr"      || echo "  ⚠ tcp_bbr (skip)"
sudo modprobe tcp_vegas    2>/dev/null && echo "  ✓ tcp_vegas"    || echo "  ⚠ tcp_vegas (skip)"
sudo modprobe tcp_illinois 2>/dev/null && echo "  ✓ tcp_illinois" || echo "  ⚠ tcp_illinois (skip)"
echo ""

# ── Step 2: Start Docker (InfluxDB + Grafana) ───────────────────────────────
echo "[2/6] Starting Docker (InfluxDB + Grafana)..."
sudo docker-compose down -v 2>/dev/null || true
sudo docker-compose up -d 2>&1 | tail -3
echo "  Waiting for InfluxDB..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8086/ping > /dev/null 2>&1; then
        echo "  ✓ InfluxDB ready"
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
echo "  ✓ Clean"
echo ""

# ── Step 4: Start P4 Two-Pod topology + inject traffic ──────────────────────
echo "[4/6] Starting P4 Two-Pod topology and injecting traffic..."
echo "  Using Python: $VENV_PYTHON"

# Only 3 switches: S1(9090)=Pod-A Leaf, S2(9091)=Pod-B Leaf, S3(9092)=CORE
cat <<MNEOF | sudo "$VENV_PYTHON" /home/p4/tutorials/utils/run_exercise.py \
    -t kbcs-topo/twopod_topology.json \
    -j p4src/kbcs_v2.json \
    -b simple_switch_grpc > /tmp/kbcs_twopod_mininet.log 2>&1 &
MININET_PID=$!
echo "  Mininet PID: $MININET_PID"

echo "  Waiting for switches to start..."
sleep 15

# Set bottleneck rate ONLY on leaf switches (S1 and S2), port 5 (uplink to CORE)
# 250 pps * ~1500 bytes = ~3 Mbps.  CORE's egress to servers is unrestricted.
for port in 9090 9091; do
    echo "set_queue_rate 250 5" | simple_switch_CLI --thrift-port $port > /dev/null 2>&1
    echo "  ✓ set_queue_rate 250 pps on port $port (port 5 → CORE)"
done

# Start iperf3 servers on all receivers
h9  iperf3 -s -D
h10 iperf3 -s -D
h11 iperf3 -s -D
h12 iperf3 -s -D
sh sleep 3

# Pod-A flows: h1-h4 → h9 (10.0.3.1) and h10 (10.0.3.2)
h1 iperf3 -c 10.0.3.1 -t $DURATION -J --logfile /tmp/twopod_h1.json &
h2 iperf3 -c 10.0.3.1 -t $DURATION -J --logfile /tmp/twopod_h2.json &
h3 iperf3 -c 10.0.3.2 -t $DURATION -J --logfile /tmp/twopod_h3.json &
h4 iperf3 -c 10.0.3.2 -t $DURATION -J --logfile /tmp/twopod_h4.json &

# Pod-B flows: h5-h8 → h11 (10.0.4.1) and h12 (10.0.4.2)
h5 iperf3 -c 10.0.4.1 -t $DURATION -J --logfile /tmp/twopod_h5.json &
h6 iperf3 -c 10.0.4.1 -t $DURATION -J --logfile /tmp/twopod_h6.json &
h7 iperf3 -c 10.0.4.2 -t $DURATION -J --logfile /tmp/twopod_h7.json &
h8 iperf3 -c 10.0.4.2 -t $DURATION -J --logfile /tmp/twopod_h8.json &

sh sleep $((DURATION + 15))
exit
MNEOF

MININET_PID=$!
echo "  Mininet PID: $MININET_PID"

# Wait for all 3 switches to come up
echo "  Waiting for switches..."
for port in 9090 9091 9092; do
    for i in $(seq 1 60); do
        if echo "" | simple_switch_CLI --thrift-port $port > /dev/null 2>&1; then
            echo "  ✓ Switch on port $port ready"
            break
        fi
        sleep 1
    done
done
sleep 3
echo ""

# ── Step 5: Start feeder + RL controllers ───────────────────────────────────
echo "[5/6] Starting telemetry feeder and RL controllers..."
source /home/p4/src/p4dev-python-venv/bin/activate 2>/dev/null || true

python3 telemetry/grafana_feeder.py > /tmp/kbcs_feeder.log 2>&1 &
FEEDER_PID=$!
echo "  ✓ Feeder PID: $FEEDER_PID"

# RL agent for S1 — Pod-A flows (fid 1-4)
python3 controller/rl_controller.py \
    --flows 1,2,3,4 \
    --duration $DURATION \
    --switches 9090 \
    --reset > /tmp/kbcs_rl_s1.log 2>&1 &
RL_S1_PID=$!
echo "  ✓ RL S1 (Pod-A) PID: $RL_S1_PID  (flows 1-4, port 9090)"

# RL agent for S2 — Pod-B flows (fid 1-4, independent karma domain)
python3 controller/rl_controller.py \
    --flows 1,2,3,4 \
    --duration $DURATION \
    --switches 9091 \
    --reset > /tmp/kbcs_rl_s2.log 2>&1 &
RL_S2_PID=$!
echo "  ✓ RL S2 (Pod-B) PID: $RL_S2_PID  (flows 1-4, port 9091)"

# RL agent for CORE — all 8 flows (fid 1-8, global karma)
python3 controller/rl_controller.py \
    --flows 1,2,3,4,5,6,7,8 \
    --duration $DURATION \
    --switches 9092 \
    --reset > /tmp/kbcs_rl_core.log 2>&1 &
RL_CORE_PID=$!
echo "  ✓ RL CORE PID: $RL_CORE_PID  (flows 1-8, port 9092)"
echo ""

# ── Step 6: Start Flask live dashboard ───────────────────────────────────────
echo "[6/6] Starting Flask live dashboard..."
sudo fuser -k 5002/tcp 2>/dev/null || true
sleep 1
python3 dashboard/live_dashboard.py --port 5002 > /tmp/kbcs_twopod_dashboard.log 2>&1 &
DASH_PID=$!
echo "  ✓ Two-Pod Dashboard PID: $DASH_PID  (port 5002)"
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════╗"
echo "║               TWO-POD EXPERIMENT RUNNING                ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Dashboard:      http://localhost:5002                  ║"
echo "║  Grafana:        http://localhost:3000                  ║"
echo "║  Duration:       ${DURATION}s                                  ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Switches:  S1(L1)=9090  S2(L2)=9091  CORE=9092        ║"
echo "║  Bottleneck: S1→CORE 3Mbps  S2→CORE 3Mbps              ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Logs:                                                  ║"
echo "║    tail -f /tmp/kbcs_rl_s1.log                          ║"
echo "║    tail -f /tmp/kbcs_rl_s2.log                          ║"
echo "║    tail -f /tmp/kbcs_rl_core.log                        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Press Ctrl+C to stop early, or wait ${DURATION}s..."

sleep $DURATION

echo ""
echo "[cleanup] Reading final karma values..."

echo ""
echo "════════ FINAL KARMA — S1 / Pod-A (flows 1-4) ══════════"
for fid in 1 2 3 4; do
    karma=$(echo "register_read MyIngress.reg_karma $fid" | \
        simple_switch_CLI --thrift-port 9090 2>/dev/null | \
        grep '=' | awk -F'= ' '{print $2}')
    flow_names=("" "h1/CUBIC" "h2/BBR" "h3/Vegas" "h4/Illinois")
    echo "  Flow $fid (${flow_names[$fid]}): karma=$karma"
done

echo ""
echo "════════ FINAL KARMA — S2 / Pod-B (flows 1-4) ══════════"
for fid in 1 2 3 4; do
    karma=$(echo "register_read MyIngress.reg_karma $fid" | \
        simple_switch_CLI --thrift-port 9091 2>/dev/null | \
        grep '=' | awk -F'= ' '{print $2}')
    flow_names=("" "h5/CUBIC" "h6/BBR" "h7/Vegas" "h8/Illinois")
    echo "  Flow $fid (${flow_names[$fid]}): karma=$karma"
done

echo ""
echo "════════ FINAL KARMA — CORE (flows 1-8) ════════════════"
for fid in 1 2 3 4 5 6 7 8; do
    karma=$(echo "register_read MyIngress.reg_karma $fid" | \
        simple_switch_CLI --thrift-port 9092 2>/dev/null | \
        grep '=' | awk -F'= ' '{print $2}')
    echo "  Flow $fid: karma=$karma"
done

kill $FEEDER_PID $RL_S1_PID $RL_S2_PID $RL_CORE_PID $DASH_PID 2>/dev/null || true
echo ""
echo "Done. Check /tmp/twopod_h*.json for per-flow iperf3 results."
