#!/bin/bash
# ============================================================================
# KBCS v2 — Full Experiment Runner (Fixed)
# ============================================================================
# Fix: use /home/p4/src/p4dev-python-venv/bin/python3 for sudo (not python3)
# Fix: inject traffic via Mininet CLI pipe (not nsenter/start_traffic.py)
# Fix: separate RL agent per switch (S1→flows1-4, S2→flows5-8)
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
echo "║          KBCS v2 — Full Experiment Runner               ║"
echo "║          Duration: ${DURATION}s                                  ║"
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

# ── Step 4: Start P4 topology + inject traffic via Mininet CLI ──────────────
echo "[4/6] Starting P4 topology and injecting traffic via Mininet CLI..."
echo "  Using Python: $VENV_PYTHON"

# Pipe Mininet CLI commands directly — this is the ONLY approach that works.
# nsenter/ip-netns approach causes switches to crash and zero karma.
cat << MNEOF | sudo "$VENV_PYTHON" /home/p4/tutorials/utils/run_exercise.py \
    -t kbcs-topo/topology.json \
    -j p4src/kbcs_v2.json \
    -b simple_switch_grpc > /tmp/kbcs_mininet.log 2>&1 &
MININET_PID=$!
echo "  Mininet PID: $MININET_PID"

echo "  Waiting for switches to start before setting internal P4 queue rates..."
sleep 15
# Move the bottleneck from Linux `tc` INTO the P4 switch internal queue
# Rate is in packets/sec (PPS). 250 pps * 1500 bytes = ~3 Mbps link capacity
# This guarantees enq_qdepth builds up inside P4 rather than in the Linux OS
for port in 9090 9091 9092 9093; do
    echo "set_queue_rate 250 5" | simple_switch_CLI --thrift-port $port >/dev/null 2>&1
    echo "set_queue_rate 250 6" | simple_switch_CLI --thrift-port $port >/dev/null 2>&1
done
h9 iperf -s -D
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

# Wait for switches to come up (up to 60s each)
echo "  Waiting for switches..."
for port in 9090 9091 9092 9093; do
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

# Independent RL agent for S1 (flows 1-4: h1-h4 → h9,h10 via S1)
python3 controller/rl_controller.py \
    --flows 1,2,3,4 \
    --duration $DURATION \
    --switches 9090 \
    --reset > /tmp/kbcs_rl_s1.log 2>&1 &
RL_S1_PID=$!
echo "  ✓ RL S1 PID: $RL_S1_PID  (flows 1-4, port 9090)"

# Independent RL agent for S2 (flows 5-8: h5-h8 → h11,h12 via S2)
python3 controller/rl_controller.py \
    --flows 5,6,7,8 \
    --duration $DURATION \
    --switches 9091 \
    --reset > /tmp/kbcs_rl_s2.log 2>&1 &
RL_S2_PID=$!
echo "  ✓ RL S2 PID: $RL_S2_PID  (flows 5-8, port 9091)"
echo ""

# ── Step 6: Start Flask live dashboards ──────────────────────────────────────
echo "[6/6] Starting Flask live dashboards..."
# Kill any old dashboards holding ports 5000 and 5001
sudo fuser -k 5000/tcp 2>/dev/null || true
sudo fuser -k 5001/tcp 2>/dev/null || true
sleep 1
python3 dashboard/live_dashboard.py > /tmp/kbcs_dashboard.log 2>&1 &
DASH_PID=$!
echo "  ✓ Cross-Topology Dashboard PID: $DASH_PID  (port 5000)"
python3 dashboard/dbell_dashboard.py > /tmp/kbcs_dbell_dashboard.log 2>&1 &
DBELL_PID=$!
echo "  ✓ Dumbbell Dashboard PID: $DBELL_PID  (port 5001)"
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                 EXPERIMENT RUNNING                      ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Cross-Topo:     http://localhost:5000                  ║"
echo "║  Dumbbell:       http://localhost:5001                  ║"
echo "║  Grafana:        http://localhost:3000                  ║"
echo "║  Duration:       ${DURATION}s                                  ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Logs:                                                  ║"
echo "║    tail -f /tmp/kbcs_feeder.log                         ║"
echo "║    tail -f /tmp/kbcs_rl_s1.log                          ║"
echo "║    tail -f /tmp/kbcs_rl_s2.log                          ║"
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

echo ""
echo "═══════════════════ FINAL KARMA (S2, flows 5-8) ═══════════"
for fid in 1 2 3 4; do
    karma=$(echo "register_read MyIngress.reg_karma $fid" | \
        simple_switch_CLI --thrift-port 9091 2>/dev/null | \
        grep '=' | awk -F'= ' '{print $2}')
    echo "  Flow $((fid+4)): karma=$karma"
done

kill $FEEDER_PID $RL_S1_PID $RL_S2_PID $DASH_PID $DBELL_PID 2>/dev/null || true
echo ""
echo "Done."
