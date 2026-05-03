#!/bin/bash
# ============================================================================
# KBCS v2 — Dumbbell Topology Experiment Runner
# ============================================================================
# Topology:   h1-h4 → S1 ═══ S2 → h5-h8  (classic dumbbell)
# Bottleneck: Single link S1-p5 ↔ S2-p5, rate-limited to 250 pps (~3 Mbps)
# Dashboard:  Port 5001 (isolated from the cross-topology on port 5000)
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
echo "║     KBCS v2 — Dumbbell Topology Experiment              ║"
echo "║     Duration: ${DURATION}s                                       ║"
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

# ── Step 4: Start P4 dumbbell topology + inject traffic ─────────────────────
echo "[4/6] Starting P4 dumbbell topology and injecting traffic..."
echo "  Using Python: $VENV_PYTHON"

# Only 2 switches in dumbbell → Thrift ports 9090 (S1) and 9091 (S2)
cat << MNEOF | sudo "$VENV_PYTHON" /home/p4/tutorials/utils/run_exercise.py \
    -t kbcs-topo/dbell_topology.json \
    -j p4src/kbcs_v2.json \
    -b simple_switch_grpc > /tmp/kbcs_dbell_mininet.log 2>&1 &
MININET_PID=$!
echo "  Mininet PID: $MININET_PID"

echo "  Waiting for switches to start..."
sleep 15
# Set queue rate on bottleneck port 5 for both S1 and S2
for port in 9090 9091; do
    echo "set_queue_rate 250 5" | simple_switch_CLI --thrift-port $port >/dev/null 2>&1
done
h5 iperf -s -D
h6 iperf -s -D
h7 iperf -s -D
h8 iperf -s -D
sh sleep 3
h1 iperf -c 10.1.2.1 -t $DURATION -P 1 &
h2 iperf -c 10.1.2.2 -t $DURATION -P 1 &
h3 iperf -c 10.1.2.3 -t $DURATION -P 1 &
h4 iperf -c 10.1.2.4 -t $DURATION -P 1 &
sh sleep $((DURATION + 15))
exit
MNEOF

MININET_PID=$!
echo "  Mininet PID: $MININET_PID"

# Wait for switches to come up (only 2 switches)
echo "  Waiting for switches..."
for port in 9090 9091; do
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

# ── Step 5: Start feeder + RL controller ────────────────────────────────────
echo "[5/6] Starting telemetry feeder and RL controller..."
source /home/p4/src/p4dev-python-venv/bin/activate 2>/dev/null || true

python3 telemetry/grafana_feeder.py > /tmp/kbcs_dbell_feeder.log 2>&1 &
FEEDER_PID=$!
echo "  ✓ Feeder PID: $FEEDER_PID"

# Single RL agent for S1 (all 4 flows pass through S1 bottleneck)
python3 controller/rl_controller.py \
    --flows 1,2,3,4 \
    --duration $DURATION \
    --switches 9090 \
    --reset > /tmp/kbcs_dbell_rl.log 2>&1 &
RL_PID=$!
echo "  ✓ RL PID: $RL_PID  (flows 1-4, port 9090)"
echo ""

# ── Step 6: Start Flask live dashboard on PORT 5001 ──────────────────────────
echo "[6/6] Starting Flask live dashboard on port 5001..."
sudo fuser -k 5001/tcp 2>/dev/null || true
sleep 1
python3 dashboard/dbell_dashboard.py > /tmp/kbcs_dbell_dashboard.log 2>&1 &
DASH_PID=$!
echo "  ✓ Dashboard PID: $DASH_PID"
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════╗"
echo "║            DUMBBELL EXPERIMENT RUNNING                  ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Dumbbell ObsCenter: http://localhost:5001              ║"
echo "║  Grafana:            http://localhost:3000               ║"
echo "║  Duration:           ${DURATION}s                               ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Logs:                                                  ║"
echo "║    tail -f /tmp/kbcs_dbell_feeder.log                   ║"
echo "║    tail -f /tmp/kbcs_dbell_rl.log                       ║"
echo "║    tail -f /tmp/kbcs_dbell_dashboard.log                ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Press Ctrl+C to stop early, or wait ${DURATION}s..."

sleep $DURATION

echo ""
echo "[cleanup] Collecting final results..."

echo ""
echo "═══════════════════ FINAL KARMA (S1, flows 1-4) ═══════════"
for fid in 1 2 3 4; do
    karma=$(echo "register_read MyIngress.reg_karma $fid" | \
        simple_switch_CLI --thrift-port 9090 2>/dev/null | \
        grep '=' | awk -F'= ' '{print $2}')
    echo "  Flow $fid: karma=$karma"
done

kill $FEEDER_PID $RL_PID $DASH_PID 2>/dev/null || true
echo ""
echo "Done."
