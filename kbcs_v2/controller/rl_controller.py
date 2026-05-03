#!/usr/bin/env python3
"""
KBCS v2 — Q-Learning Adaptive Controller
==========================================
Real Q-Learning implementation. NOT rule-based.

Architecture:
  - One controller process manages all KBCS switches via Thrift API.
  - Each switch is evaluated INDEPENDENTLY every 2 seconds.
  - All switches share ONE Q-table (shared learning brain).
  - When Switch 1 learns something useful in a given state, Switch 2
    immediately benefits from that knowledge on its next epoch.

Q-Learning components:
  State  : (jfi_bucket, utilization_bucket, flow_count_bucket) → 64 states
  Actions: 7 actions (increase/decrease penalty, reward, budget, or hold)
  Q-table: 64 × 7 numpy array, updated via Bellman equation after each epoch
  ε-greedy: starts at 0.15, decays every 50 epochs (explores → exploits)
  α = 0.1  (learning rate)
  γ = 0.9  (discount factor)

Reward function (per switch, per epoch):
  R = 10 × ΔJFI + 3 × utilization − 5 × starvation_count

Q-table is saved to disk every 10 epochs so training persists across runs.

Usage (run INSIDE the P4 VM):
  python3 rl_controller.py --flows 8 --duration 120 --switches 9090,9091,9092,9093

Author: KBCS Research Team
"""

import argparse
import json
import math
import os
import pickle
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# ─── Q-Learning Hyperparameters ───────────────────────────────────────────────
ALPHA           = 0.1    # learning rate
GAMMA           = 0.9    # discount factor
EPSILON_START   = 0.15   # initial exploration rate
EPSILON_MIN     = 0.02   # minimum exploration rate (never fully greedy)
EPSILON_DECAY   = 0.90   # epsilon multiplied by this every DECAY_EVERY epochs
DECAY_EVERY     = 50     # decay epsilon every N epochs

QTABLE_PATH     = os.path.join(os.path.dirname(__file__), 'qtable.pkl')
EPOCH_SECS      = 2.0    # control loop interval
LINK_MBPS       = 3.0    # bottleneck link capacity (must match topology/experiment)
AVG_PKT_BYTES   = 1500   # average packet size for throughput estimation

# ─── Parameter Bounds (safety limits — Q-learning never goes outside these) ───
PENALTY_MIN     = 5
PENALTY_MAX     = 30
REWARD_MIN      = 2
REWARD_MAX      = 15
HEADROOM_MIN    = 1.0    # multiplier on fair_bytes for GREEN flows
HEADROOM_MAX    = 1.5
HEADROOM_STEP   = 0.1

# Default starting values (also used as fallback on fresh install)
PENALTY_DEFAULT = 8
REWARD_DEFAULT  = 4
HEADROOM_DEFAULT= 1.2

# ─── State Space Definition ───────────────────────────────────────────────────
# 3 dimensions × 4 buckets each = 64 total states

JFI_BUCKETS = [
    (0.00, 0.70),   # bucket 0: severe unfairness
    (0.70, 0.85),   # bucket 1: moderate unfairness
    (0.85, 0.95),   # bucket 2: acceptable
    (0.95, 1.01),   # bucket 3: excellent
]

UTIL_BUCKETS = [
    (0.00, 0.30),   # bucket 0: very low utilization
    (0.30, 0.60),   # bucket 1: moderate
    (0.60, 0.80),   # bucket 2: good
    (0.80, 1.01),   # bucket 3: high / saturated
]

FLOW_BUCKETS = [
    (1,  4),        # bucket 0
    (5,  8),        # bucket 1
    (9,  16),       # bucket 2
    (17, 9999),     # bucket 3
]

N_STATES  = len(JFI_BUCKETS) * len(UTIL_BUCKETS) * len(FLOW_BUCKETS)  # 64
N_ACTIONS = 7

# Action index → name (for logging)
ACTION_NAMES = [
    "increase_penalty",   # 0
    "decrease_penalty",   # 1
    "increase_reward",    # 2
    "decrease_reward",    # 3
    "tighten_budget",     # 4  (reduce headroom_factor)
    "loosen_budget",      # 5  (increase headroom_factor)
    "maintain",           # 6  (no change)
]


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SwitchParams:
    """Current tunable parameters for one switch."""
    penalty  : int   = PENALTY_DEFAULT
    reward   : int   = REWARD_DEFAULT
    headroom : float = HEADROOM_DEFAULT

@dataclass
class EpochMetrics:
    """Network metrics observed at end of one epoch for one switch."""
    jfi            : float
    utilization    : float   # 0.0 – 1.0
    flow_rates_mbps: List[float]
    starvation_count: int    # flows below 10% of fair share

@dataclass
class SwitchState:
    """Full state tracked per switch across epochs."""
    thrift_port    : int
    params         : SwitchParams       = field(default_factory=SwitchParams)
    prev_pkts      : Dict[int,int]      = field(default_factory=dict)
    prev_drops     : Dict[int,int]      = field(default_factory=dict)
    prev_fwd_bytes : Dict[int,int]      = field(default_factory=dict)
    prev_jfi       : float              = 0.5
    prev_state_idx : Optional[int]      = None
    prev_action    : Optional[int]      = None
    epoch_count    : int                = 0


# ─── Q-Table ──────────────────────────────────────────────────────────────────

class QTable:
    """
    64 × 7 Q-table with persistence.
    All values initialised to 0 (optimistic start) so
    any action looks equally promising at first — exploration
    picks at random until the table differentiates via experience.
    """

    def __init__(self):
        if os.path.exists(QTABLE_PATH):
            self.load()
            print(f"[QTable] Loaded from {QTABLE_PATH}")
        else:
            self.q = np.zeros((N_STATES, N_ACTIONS), dtype=np.float64)
            print(f"[QTable] Initialised fresh ({N_STATES}×{N_ACTIONS})")
        self.epsilon = EPSILON_START
        self.total_updates = 0

    def state_index(self, jfi: float, util: float, n_flows: int) -> int:
        """Convert continuous observations into a single integer state index."""
        jfi_b  = self._bucket(jfi,     JFI_BUCKETS)
        util_b = self._bucket(util,    UTIL_BUCKETS)
        flow_b = self._flow_bucket(n_flows)
        return jfi_b * (len(UTIL_BUCKETS) * len(FLOW_BUCKETS)) + util_b * len(FLOW_BUCKETS) + flow_b

    def _bucket(self, value: float, buckets: list) -> int:
        for i, (lo, hi) in enumerate(buckets):
            if lo <= value < hi:
                return i
        return len(buckets) - 1  # clamp to last bucket

    def _flow_bucket(self, n_flows: int) -> int:
        for i, (lo, hi) in enumerate(FLOW_BUCKETS):
            if lo <= n_flows <= hi:
                return i
        return len(FLOW_BUCKETS) - 1

    def select_action(self, state_idx: int) -> int:
        """
        ε-greedy action selection.
        With probability ε → random action (explore).
        Otherwise         → argmax Q(state, ·) (exploit).
        """
        if np.random.random() < self.epsilon:
            return np.random.randint(N_ACTIONS)
        return int(np.argmax(self.q[state_idx]))

    def update(self, state: int, action: int, reward: float, next_state: int):
        """
        Bellman equation update:
          Q(s,a) ← Q(s,a) + α × [r + γ × max_a' Q(s',a') − Q(s,a)]
        """
        best_next = float(np.max(self.q[next_state]))
        td_target = reward + GAMMA * best_next
        td_error  = td_target - self.q[state, action]
        self.q[state, action] += ALPHA * td_error
        self.total_updates += 1

    def decay_epsilon(self):
        """Reduce exploration rate — called every DECAY_EVERY epochs."""
        self.epsilon = max(EPSILON_MIN, self.epsilon * EPSILON_DECAY)

    def save(self):
        with open(QTABLE_PATH, 'wb') as f:
            pickle.dump({'q': self.q, 'epsilon': self.epsilon,
                         'updates': self.total_updates}, f)

    def load(self):
        with open(QTABLE_PATH, 'rb') as f:
            data = pickle.load(f)
        self.q            = data['q']
        self.epsilon      = data.get('epsilon', EPSILON_START)
        self.total_updates= data.get('updates', 0)

    def stats(self) -> str:
        return (f"ε={self.epsilon:.4f}  updates={self.total_updates}  "
                f"max_q={self.q.max():.3f}  min_q={self.q.min():.3f}")


# ─── Thrift API Interface ─────────────────────────────────────────────────────

def run_thrift(port: int, commands: str, timeout: int = 3) -> str:
    """
    Send a batch of simple_switch_CLI commands to one switch via Thrift.
    Returns raw stdout text from the CLI.
    """
    try:
        result = subprocess.run(
            ['simple_switch_CLI', '--thrift-port', str(port)],
            input=commands,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"[Thrift:{port}] Timeout!")
        return ""
    except FileNotFoundError:
        print("[Thrift] simple_switch_CLI not found — are you inside the P4 VM?")
        return ""


def read_register(port: int, reg_name: str, index: int) -> int:
    """Read one register entry. Returns integer value."""
    out = run_thrift(port, f"register_read {reg_name} {index}\n")
    match = re.search(r'=\s*(\d+)', out)
    return int(match.group(1)) if match else 0


def write_register(port: int, reg_name: str, index: int, value: int):
    """Write one register entry."""
    run_thrift(port, f"register_write {reg_name} {index} {value}\n")


def read_flow_telemetry(port: int, flow_ids: List[int]) -> Dict[str, Dict[int,int]]:
    """
    Batch-read total_pkts, drops, and forwarded_bytes for all flows.
    Returns dict of {register_name: {flow_id: value}}.
    """
    cmds = ""
    for fid in flow_ids:
        cmds += f"register_read MyIngress.reg_total_pkts {fid}\n"
        cmds += f"register_read MyIngress.reg_drops {fid}\n"
        cmds += f"register_read MyIngress.reg_forwarded_bytes {fid}\n"

    out = run_thrift(port, cmds)
    values = re.findall(r'=\s*(\d+)', out)

    result = {'pkts': {}, 'drops': {}, 'fwd_bytes': {}}
    if len(values) < 3 * len(flow_ids):
        return result  # incomplete read

    for i, fid in enumerate(flow_ids):
        base = i * 3
        result['pkts'][fid]      = int(values[base])
        result['drops'][fid]     = int(values[base + 1])
        result['fwd_bytes'][fid] = int(values[base + 2])

    return result


# ─── Metrics Computation ──────────────────────────────────────────────────────

def compute_jfi(rates: List[float]) -> float:
    """Jain's Fairness Index: (Σx)² / (n × Σx²)"""
    if not rates or all(r == 0 for r in rates):
        return 0.0
    n    = len(rates)
    s    = sum(rates)
    sq   = sum(r * r for r in rates)
    return (s * s) / (n * sq) if sq > 0 else 0.0


def compute_metrics(sw: SwitchState, telemetry: Dict, dt: float,
                    flow_ids: List[int]) -> EpochMetrics:
    """
    Compute per-epoch metrics from raw register deltas.
    """
    rates = []
    for fid in flow_ids:
        cur_fwd   = telemetry['fwd_bytes'].get(fid, 0)
        prev_fwd  = sw.prev_fwd_bytes.get(fid, 0)
        delta_fwd = max(0, cur_fwd - prev_fwd)
        rate_mbps = (delta_fwd * 8) / (dt * 1e6)
        rates.append(rate_mbps)

        # Update previous values
        sw.prev_pkts[fid]      = telemetry['pkts'].get(fid, 0)
        sw.prev_drops[fid]     = telemetry['drops'].get(fid, 0)
        sw.prev_fwd_bytes[fid] = cur_fwd

    jfi        = compute_jfi(rates)
    total_mbps = sum(rates)
    utilization= min(1.0, total_mbps / LINK_MBPS)

    # Starvation: any flow getting < 10% of fair share
    fair_share  = LINK_MBPS / len(flow_ids) if flow_ids else LINK_MBPS
    starvation  = sum(1 for r in rates if r < 0.1 * fair_share)

    return EpochMetrics(
        jfi             = jfi,
        utilization     = utilization,
        flow_rates_mbps = rates,
        starvation_count= starvation,
    )


def compute_reward(prev_jfi: float, metrics: EpochMetrics) -> float:
    """
    Reward function (methodology Section 4.5):
      R = 10 × ΔJFI + 3 × utilization − 5 × starvation_count

    Positive reward when JFI improves.
    Utilization term prevents over-penalization (throttling everyone to 0).
    Starvation heavily penalizes solutions that starve any flow.
    """
    delta_jfi = metrics.jfi - prev_jfi
    reward    = (10.0 * delta_jfi
               + 3.0  * metrics.utilization
               - 5.0  * metrics.starvation_count)
    return reward


# ─── Action Execution ─────────────────────────────────────────────────────────

def apply_action(sw: SwitchState, action: int, n_flows: int):
    """
    Apply a Q-learning action to one switch.
    Updates sw.params in-place and writes new values to P4 registers.
    """
    p = sw.params

    if action == 0:   # increase_penalty
        p.penalty = min(PENALTY_MAX, p.penalty + 2)
    elif action == 1: # decrease_penalty
        p.penalty = max(PENALTY_MIN, p.penalty - 1)
    elif action == 2: # increase_reward
        p.reward  = min(REWARD_MAX,  p.reward + 1)
    elif action == 3: # decrease_reward
        p.reward  = max(REWARD_MIN,  p.reward - 1)
    elif action == 4: # tighten_budget (less headroom)
        p.headroom = max(HEADROOM_MIN, round(p.headroom - HEADROOM_STEP, 2))
    elif action == 5: # loosen_budget (more headroom)
        p.headroom = min(HEADROOM_MAX, round(p.headroom + HEADROOM_STEP, 2))
    elif action == 6: # maintain
        pass

    # Recalculate fair_bytes = (link_rate_bytes/s × window_s) / flows × headroom
    window_s       = 0.015  # 15ms
    link_bytes_s   = (LINK_MBPS * 1e6) / 8.0
    fair_bytes_raw = int((link_bytes_s * window_s) / max(1, n_flows))
    fair_bytes     = int(fair_bytes_raw * p.headroom)
    fair_bytes     = max(1500, min(fair_bytes, 50000))  # safety clamp

    # Write updated parameters to P4 registers
    write_register(sw.thrift_port, 'MyIngress.reg_penalty_amt', 0, p.penalty)
    write_register(sw.thrift_port, 'MyIngress.reg_reward_amt',  0, p.reward)
    write_register(sw.thrift_port, 'MyIngress.reg_fair_bytes',  0, fair_bytes)


# ─── Main Control Loop ────────────────────────────────────────────────────────

def control_loop(thrift_ports: List[int], flow_ids: List[int],
                 duration: int, qtable: QTable):
    """
    Main per-switch independent Q-learning control loop.
    Runs EPOCH_SECS-second cycles for `duration` seconds total.
    """

    # Initialise per-switch state
    switches = {port: SwitchState(thrift_port=port) for port in thrift_ports}

    # Write default parameters to all switches at startup
    for port, sw in switches.items():
        apply_action(sw, 6, len(flow_ids))  # action 6 = maintain (writes defaults)
        print(f"[S:{port}] Initialised: penalty={sw.params.penalty} "
              f"reward={sw.params.reward} headroom={sw.params.headroom}")

    start_time = time.time()
    prev_time  = start_time
    global_epoch = 0

    print(f"\n{'='*60}")
    print(f"KBCS v2 Q-Learning Controller")
    print(f"Switches: {thrift_ports}  Flows: {flow_ids}")
    print(f"Duration: {duration}s  Epoch: {EPOCH_SECS}s")
    print(f"Q-Table: {qtable.stats()}")
    print(f"{'='*60}\n")

    while time.time() - start_time < duration:
        now = time.time()
        dt  = now - prev_time

        if dt < EPOCH_SECS:
            time.sleep(0.05)
            continue

        prev_time   = now
        global_epoch += 1

        # ── Decay epsilon periodically ──────────────────────────────────
        if global_epoch % DECAY_EVERY == 0:
            qtable.decay_epsilon()
            print(f"[Epoch {global_epoch}] Epsilon decayed → {qtable.epsilon:.4f}")

        # ── Per-switch independent evaluation ───────────────────────────
        for port, sw in switches.items():
            sw.epoch_count += 1

            # 1. Read telemetry
            telemetry = read_flow_telemetry(port, flow_ids)
            if not telemetry['pkts']:
                continue

            # 2. Compute metrics
            metrics = compute_metrics(sw, telemetry, dt, flow_ids)

            # 3. Determine current state
            state_idx = qtable.state_index(
                metrics.jfi, metrics.utilization, len(flow_ids)
            )

            # 4. Q-table update (if we have a previous (s, a) pair)
            if sw.prev_state_idx is not None and sw.prev_action is not None:
                reward = compute_reward(sw.prev_jfi, metrics)
                qtable.update(sw.prev_state_idx, sw.prev_action, reward, state_idx)

            # 5. Select next action (ε-greedy)
            action = qtable.select_action(state_idx)

            # 6. Execute action
            apply_action(sw, action, len(flow_ids))

            # 7. Log
            rates_str = "  ".join(f"F{fid}:{r:.2f}M"
                                  for fid, r in zip(flow_ids, metrics.flow_rates_mbps))
            explore_tag = "EXPLORE" if np.random.random() < qtable.epsilon else "EXPLOIT"
            print(f"[Ep{global_epoch:03d} S:{port}] "
                  f"JFI={metrics.jfi:.4f}  Util={metrics.utilization:.2f}  "
                  f"Starv={metrics.starvation_count}  "
                  f"Act={ACTION_NAMES[action]}({action}) [{explore_tag}]")
            print(f"         Rates: {rates_str}")
            print(f"         Params: pen={sw.params.penalty} "
                  f"rew={sw.params.reward} head={sw.params.headroom:.1f}")

            # 8. Save state for next epoch's Q update
            sw.prev_state_idx = state_idx
            sw.prev_action    = action
            sw.prev_jfi       = metrics.jfi

        # ── Save Q-table every 10 epochs ────────────────────────────────
        if global_epoch % 10 == 0:
            qtable.save()
            print(f"\n[QTable] Saved at epoch {global_epoch}. {qtable.stats()}\n")

    # ── End of run ──────────────────────────────────────────────────────
    qtable.save()
    print(f"\n{'='*60}")
    print(f"Run complete. {global_epoch} epochs.")
    print(f"Final Q-Table stats: {qtable.stats()}")
    print(f"Q-Table saved to: {QTABLE_PATH}")

    # Print final Q-table (for analysis)
    print("\nFinal Q-Table (rows=states, cols=actions):")
    print("Actions:", ACTION_NAMES)
    np.set_printoptions(precision=3, suppress=True, linewidth=120)
    print(qtable.q)
    print(f"{'='*60}\n")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description='KBCS v2 Q-Learning Controller')
    parser.add_argument('--flows',    type=str, default='1,2,3,4,5,6,7,8',
                        help='Comma-separated flow IDs (default: 1,2,3,4,5,6,7,8)')
    parser.add_argument('--duration', type=int, default=120,
                        help='Experiment duration in seconds (default: 120)')
    parser.add_argument('--switches', type=str, default='9090,9091,9092,9093',
                        help='Comma-separated Thrift ports, one per switch')
    parser.add_argument('--reset',    action='store_true',
                        help='Delete saved Q-table and start fresh')
    parser.add_argument('--report',   action='store_true',
                        help='Print Q-table stats and exit (no control loop)')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()

    if args.reset and os.path.exists(QTABLE_PATH):
        os.remove(QTABLE_PATH)
        print(f"[QTable] Reset — deleted {QTABLE_PATH}")

    qtable   = QTable()
    flow_ids = [int(f) for f in args.flows.split(',')]
    ports    = [int(p) for p in args.switches.split(',')]

    if args.report:
        print(qtable.stats())
        print(qtable.q)
    else:
        control_loop(ports, flow_ids, args.duration, qtable)
