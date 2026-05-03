#!/bin/bash
# ============================================================================
# KBCS v2 — 30-Run Statistical Test Suite
# ============================================================================
# Runs the experiment N times (default 30), each for RUN_DURATION seconds.
# After each run, calls collect_metrics.py to read registers and append to CSV.
# Usage:
#   bash test_suite.sh                          # 30 runs, 60s each, cross-topo
#   bash test_suite.sh --runs 10 --duration 60  # 10 runs, 60s each
#   bash test_suite.sh --topo dumbbell          # dumbbell topology
#   bash test_suite.sh --mode fifo              # FIFO baseline (no KBCS)
# ============================================================================

cd "$(dirname "$0")"
KBCS_DIR="$(pwd)"
VENV_PYTHON=/home/p4/src/p4dev-python-venv/bin/python3

# Defaults
NUM_RUNS=30
RUN_DURATION=60
TOPO="cross"      # "cross" or "dumbbell"
MODE="kbcs"       # "kbcs" or "fifo"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --runs)     NUM_RUNS="$2";      shift 2 ;;
        --duration) RUN_DURATION="$2";  shift 2 ;;
        --topo)     TOPO="$2";          shift 2 ;;
        --mode)     MODE="$2";          shift 2 ;;
        *)          shift ;;
    esac
done

# Set topology-specific config
# Set CSV prefix based on mode
if [[ "$MODE" == "fifo" ]]; then
    CSV_PREFIX="fifo_"
else
    CSV_PREFIX=""
fi

if [[ "$TOPO" == "dumbbell" ]]; then
    TOPO_JSON="kbcs-topo/dbell_topology.json"
    CSV_FILE="results/${CSV_PREFIX}dumbbell_results.csv"
    THRIFT_PORTS="9090 9091"
    SWITCH_COUNT=2
    IPERF_CMDS=$(cat <<'IEOF'
h5 iperf -s -D
h6 iperf -s -D
h7 iperf -s -D
h8 iperf -s -D
sh sleep 3
h1 iperf -c 10.1.2.1 -t __DUR__ -P 1 &
h2 iperf -c 10.1.2.2 -t __DUR__ -P 1 &
h3 iperf -c 10.1.2.3 -t __DUR__ -P 1 &
h4 iperf -c 10.1.2.4 -t __DUR__ -P 1 &
IEOF
)
    RL_CMD="python3 controller/rl_controller.py --flows 1,2,3,4 --duration $RUN_DURATION --switches 9090 --reset"
else
    TOPO_JSON="kbcs-topo/topology.json"
    CSV_FILE="results/${CSV_PREFIX}cross_results.csv"
    THRIFT_PORTS="9090 9091 9092 9093"
    SWITCH_COUNT=4
    IPERF_CMDS=$(cat <<'IEOF'
h9 iperf -s -D
h10 iperf -s -D
h11 iperf -s -D
h12 iperf -s -D
sh sleep 3
h1 iperf -c 10.0.3.1 -t __DUR__ -P 1 &
h2 iperf -c 10.0.3.1 -t __DUR__ -P 1 &
h3 iperf -c 10.0.3.2 -t __DUR__ -P 1 &
h4 iperf -c 10.0.3.2 -t __DUR__ -P 1 &
h5 iperf -c 10.0.4.1 -t __DUR__ -P 1 &
h6 iperf -c 10.0.4.1 -t __DUR__ -P 1 &
h7 iperf -c 10.0.4.2 -t __DUR__ -P 1 &
h8 iperf -c 10.0.4.2 -t __DUR__ -P 1 &
IEOF
)
    RL_CMD="python3 controller/rl_controller.py --flows 1,2,3,4 --duration $RUN_DURATION --switches 9090 --reset"
fi

# Replace duration placeholder
IPERF_CMDS="${IPERF_CMDS//__DUR__/$RUN_DURATION}"

# Cache sudo
echo "p4" | sudo -S true > /dev/null 2>&1

# Load TCP modules
sudo modprobe tcp_bbr      2>/dev/null || true
sudo modprobe tcp_vegas    2>/dev/null || true
sudo modprobe tcp_illinois 2>/dev/null || true

# Create results directory
mkdir -p results

# Delete old CSV and start fresh (avoids mixing data from different configs)
rm -f "$CSV_FILE"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║       KBCS v2 — 30-Run Statistical Test Suite           ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Topology:  $TOPO                                       "
echo "║  Mode:      $MODE                                       "
echo "║  Runs:      $NUM_RUNS                                   "
echo "║  Duration:  ${RUN_DURATION}s per run                    "
echo "║  Total:     ~$((NUM_RUNS * (RUN_DURATION + 30)))s       "
echo "║  CSV:       $CSV_FILE                                   "
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

source /home/p4/src/p4dev-python-venv/bin/activate 2>/dev/null || true

for RUN in $(seq 1 $NUM_RUNS); do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  RUN $RUN / $NUM_RUNS  ($TOPO topology, ${RUN_DURATION}s)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # ── Clean ────────────────────────────────────────────────────
    echo "  [1/5] Cleaning..."
    sudo mn -c > /dev/null 2>&1 || true
    sudo killall -9 simple_switch_grpc 2>/dev/null || true
    sudo rm -rf pcaps/* logs/* 2>/dev/null || true
    sleep 2

    # ── Start Mininet + traffic ──────────────────────────────────
    echo "  [2/5] Starting Mininet ($TOPO)..."

    # Build the Mininet CLI pipe
    WAIT_TIME=$((RUN_DURATION + 15))
    cat <<MNEOF | sudo "$VENV_PYTHON" /home/p4/tutorials/utils/run_exercise.py \
        -t "$TOPO_JSON" \
        -j p4src/kbcs_v2.json \
        -b simple_switch_grpc > /tmp/kbcs_test_mn.log 2>&1 &
$IPERF_CMDS
sh sleep $WAIT_TIME
exit
MNEOF
    MN_PID=$!

    # Wait for switches
    echo "  [3/5] Waiting for switches..."
    sleep 15
    for port in $THRIFT_PORTS; do
        for i in $(seq 1 60); do
            if echo "" | simple_switch_CLI --thrift-port $port > /dev/null 2>&1; then
                break
            fi
            sleep 1
        done
    done

    # Set queue rates on bottleneck ports
    for port in $THRIFT_PORTS; do
        echo "set_queue_rate 250 5" | simple_switch_CLI --thrift-port $port >/dev/null 2>&1
        echo "set_queue_rate 250 6" | simple_switch_CLI --thrift-port $port >/dev/null 2>&1
    done
    sleep 3

    # ── Start RL controller (or FIFO bypass) ─────────────────────
    if [[ "$MODE" == "fifo" ]]; then
        echo "  [4/5] FIFO mode — skipping RL controller..."
        # Set fair_bytes to huge value so no flow ever exceeds budget
        # → all flows stay GREEN, no karma penalties, no PFQ drops = pure FIFO
        for port in $THRIFT_PORTS; do
            echo "register_write MyIngress.reg_fair_bytes 0 999999" | simple_switch_CLI --thrift-port $port >/dev/null 2>&1
        done
        RL_PID=""
    else
        echo "  [4/5] Starting RL controller..."
        $RL_CMD > /tmp/kbcs_test_rl.log 2>&1 &
        RL_PID=$!
    fi

    # ── Wait for experiment to complete ──────────────────────────
    echo "  [5/5] Running traffic for ${RUN_DURATION}s..."
    sleep $RUN_DURATION

    # ── Collect metrics ──────────────────────────────────────────
    echo "  Collecting metrics..."
    python3 collect_metrics.py \
        --run "$RUN" \
        --topo "$TOPO" \
        --duration "$RUN_DURATION" \
        --csv "$CSV_FILE" \
        --thrift-port 9090

    # ── Cleanup ──────────────────────────────────────────────────
    if [[ -n "$RL_PID" ]]; then kill $RL_PID 2>/dev/null || true; fi
    kill $MN_PID 2>/dev/null || true
    sudo mn -c > /dev/null 2>&1 || true
    sudo killall -9 simple_switch_grpc 2>/dev/null || true
    sleep 3

    echo "  ✓ Run $RUN complete"
done

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║            ALL $NUM_RUNS RUNS COMPLETE                  ║"
echo "║  Results saved to: $CSV_FILE                            ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Run 'python3 analyze_results.py' to generate statistics."
