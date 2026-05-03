/* =============================================================================
 * kbcs_v2.p4  —  Karma-Based Congestion Signaling (Version 2)
 * =============================================================================
 * Implements the KBCS methodology pipeline exactly:
 *
 *  Stage 1 : Flow Identification      (table: flow_id_exact)
 *  Stage 2 : Byte Counting            (15ms window, reg_bytes)
 *  Stage 3 : Karma Update             (proportional penalty + momentum)
 *            + Idle Flow Recovery     (500ms idle → karma boost)
 *            + Slow-Start Immunity    (first 20 packets exempt)
 *  Stage 4 : Color Assignment         (GREEN 76-100 / YELLOW 41-75 / RED 0-40)
 *  Stage 5 : AQM Enforcement          (budget-based drop + ECN marking)
 *            + PFQ Buffer Reservation (RED budget = 25%, recycled to GREEN)
 *  Stage 6 : RED Streak Recovery      (20 consecutive RED windows → karma reset to 30)
 *  Stage 7 : Priority Queue Mapping   (GREEN=2, YELLOW=1, RED=0)
 *  Stage 8 : Telemetry Clone          (on color change or every 8th packet)
 *
 * Controller interface (P4Runtime / Thrift):
 *   reg_fair_bytes[0]   — set by rl_controller.py every 2s
 *   reg_penalty_amt[0]  — set by rl_controller.py (Q-Learning action)
 *   reg_reward_amt[0]   — set by rl_controller.py (Q-Learning action)
 *
 * Compatible with: BMv2 simple_switch (v1model architecture)
 * =============================================================================
 */

#include <core.p4>
#include <v1model.p4>

// ─── Constants ────────────────────────────────────────────────────────────────
#define REG_SIZE        1024    // max flows tracked

#define KARMA_INIT      100     // starting karma for new flows
#define MAX_KARMA       100     // karma ceiling

// Color zone values (stored in 2-bit registers)
#define GREEN           2w2
#define YELLOW          2w1
#define RED             2w0

// Measurement window: 15ms = 15,000 microseconds
#define WINDOW_USEC     15000

// Karma thresholds matching methodology:
//   GREEN  : karma > 75  (76-100)
//   YELLOW : karma > 40  (41-75)
//   RED    : karma 0-40
#define GREEN_THRESH    16w76
#define YELLOW_THRESH   16w41

// RED streak recovery threshold: 20 windows × 15ms = 300ms
#define RED_STREAK_MAX  8w20
#define KARMA_RESET_VAL 16w30   // reset to low YELLOW on recovery

// Slow-start immunity: exempt first 20 packets from penalties
#define SLOWSTART_PKTS  32w20

// Idle recovery threshold: 500ms = 500,000 microseconds
#define IDLE_USEC       500000

// Drop thresholds (random 0-255):
//   GREEN  10%  → threshold 26  (26/256 ≈ 10%)
//   YELLOW 35%  → threshold 90  (90/256 ≈ 35%)
//   RED    90%  → threshold 230 (230/256 ≈ 90%)
#define DROP_GREEN      8w26
#define DROP_YELLOW     8w90
#define DROP_RED        8w230

// Telemetry clone session ID (must match simple_switch mirror config)
#define TELEMETRY_SESSION 32w4

// ─── Headers ──────────────────────────────────────────────────────────────────

typedef bit<9>  egressSpec_t;
typedef bit<48> macAddr_t;
typedef bit<32> ip4Addr_t;

header ethernet_t {
    macAddr_t dstAddr;
    macAddr_t srcAddr;
    bit<16>   etherType;
}

header ipv4_t {
    bit<4>    version;
    bit<4>    ihl;
    bit<8>    diffserv;
    bit<16>   totalLen;
    bit<16>   identification;
    bit<3>    flags;
    bit<13>   fragOffset;
    bit<8>    ttl;
    bit<8>    protocol;
    bit<16>   hdrChecksum;
    ip4Addr_t srcAddr;
    ip4Addr_t dstAddr;
}

header tcp_t {
    bit<16> srcPort;
    bit<16> dstPort;
    bit<32> seqNo;
    bit<32> ackNo;
    bit<4>  dataOffset;
    bit<3>  res;
    bit<3>  ecn;
    bit<6>  ctrl;       // FIN=0x01, SYN=0x02, RST=0x04
    bit<16> window;
    bit<16> checksum;
    bit<16> urgentPtr;
}

// Telemetry header stamped onto cloned packets only (etherType 0x1234)
header kbcs_telemetry_t {
    bit<8>  flow_id;       // which flow this telemetry belongs to
    bit<8>  karma_score;   // current karma (0-100)
    bit<2>  color;         // 2=GREEN, 1=YELLOW, 0=RED
    bit<3>  queue_id;      // priority queue assigned
    bit<19> enq_qdepth;    // queue depth at egress enqueue
    bit<1>  is_dropped;    // 1 if packet was dropped before cloning
    bit<7>  _pad;          // byte-align
}

struct parsed_headers_t {
    ethernet_t       ethernet;
    ipv4_t           ipv4;
    tcp_t            tcp;
    kbcs_telemetry_t kbcs_telemetry;
}

struct local_metadata_t {
    bit<16> flow_id;           // assigned by control plane table
    bit<32> flow_bytes;        // bytes this flow sent in current window
    bit<16> karma_score;       // current karma value
    bit<2>  flow_color;        // GREEN/YELLOW/RED
    bit<1>  is_dropped;        // whether this packet was dropped
    bit<1>  should_clone;      // whether egress should clone for telemetry
    bit<19> saved_qdepth;      // queue depth saved for telemetry
    bit<19> pfq_threshold;     // dynamic PFQ drop threshold passed to Egress
}

// ─── Parser ───────────────────────────────────────────────────────────────────

parser MyParser(packet_in packet,
                out parsed_headers_t hdr,
                inout local_metadata_t meta,
                inout standard_metadata_t standard_metadata) {

    state start {
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            16w0x0800: parse_ipv4;
            default:   accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            8w6:    parse_tcp;   // TCP only
            default: accept;
        }
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        transition accept;
    }
}

// ─── Checksum Verification ────────────────────────────────────────────────────

control MyVerifyChecksum(inout parsed_headers_t hdr, inout local_metadata_t meta) {
    apply {
        verify_checksum(hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
              hdr.ipv4.totalLen, hdr.ipv4.identification,
              hdr.ipv4.flags, hdr.ipv4.fragOffset,
              hdr.ipv4.ttl, hdr.ipv4.protocol,
              hdr.ipv4.srcAddr, hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum, HashAlgorithm.csum16);
    }
}

// ─── Ingress: Core KBCS Pipeline ─────────────────────────────────────────────

control MyIngress(inout parsed_headers_t hdr,
                  inout local_metadata_t meta,
                  inout standard_metadata_t standard_metadata) {

    // ── Per-flow state registers (indexed by flow_id) ──────────────────────

    // Stage 2: byte counting
    register<bit<32>>(REG_SIZE) reg_bytes;          // bytes in current window
    register<bit<48>>(REG_SIZE) reg_wstart;         // window start timestamp
    register<bit<48>>(REG_SIZE) reg_last_seen;      // last packet timestamp (for idle detection)
    register<bit<32>>(REG_SIZE) reg_total_pkts;     // total packets seen (slow-start immunity)

    // Stage 3: karma state
    register<bit<16>>(REG_SIZE) reg_karma;          // current karma score
    register<bit<16>>(REG_SIZE) reg_prev_karma;     // karma in previous window (for momentum)

    // Stage 4: color tracking
    register<bit<2>>(REG_SIZE)  reg_prev_color;     // color in previous epoch (for telemetry trigger)

    // Stage 5: drop counting (read by controller for telemetry)
    // NOTE: Ingress drops removed (PFQ moves drops to Egress).
    // reg_drops is now only written by Egress, but we keep the declaration
    // here (BMv2 shares register memory across Ingress/Egress). 
    register<bit<32>>(REG_SIZE) reg_drops;
    register<bit<32>>(REG_SIZE) reg_forwarded_bytes; // bytes actually forwarded (not dropped)

    // Stage 6: RED streak recovery
    register<bit<8>>(REG_SIZE)  reg_red_streak;     // consecutive RED windows per flow

    // PFQ Buffer Recycling: per-flow dynamic Egress drop threshold
    // Written at window expiry (Ingress), read at packet departure (Egress).
    // Encodes PFQ Algorithm 1 / Formula 6: flows that underused their quota
    // get a higher threshold (more headroom), tightly-throttling over-budget
    // flows and recycling that buffer space to well-behaved GREEN flows.
    register<bit<19>>(REG_SIZE) reg_pfq_threshold;

    // ── Controller-writable parameters (single-entry registers) ────────────
    // These are written every 2s by rl_controller.py via Thrift API

    register<bit<32>>(1) reg_fair_bytes;    // per-flow fair share (bytes/window)
    register<bit<32>>(1) reg_penalty_amt;   // base karma penalty (Q-learning tuned)
    register<bit<32>>(1) reg_reward_amt;    // karma reward per good window (Q-learning tuned)

    // ── Actions ────────────────────────────────────────────────────────────

    action set_flow_id(bit<16> fid) {
        meta.flow_id = fid;
    }

    action drop_pkt() {
        mark_to_drop(standard_metadata);
    }

    action ipv4_forward(macAddr_t dst_mac, egressSpec_t port) {
        standard_metadata.egress_spec = port;
        hdr.ethernet.srcAddr = hdr.ethernet.dstAddr;
        hdr.ethernet.dstAddr = dst_mac;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    // Stage 1: Flow ID lookup table  (populated by controller at startup)
    table flow_id_exact {
        key     = { hdr.ipv4.srcAddr : exact; }
        actions = { set_flow_id; NoAction; }
        size    = 1024;
        default_action = NoAction();
    }

    // Standard IPv4 forwarding table (LPM)
    table ipv4_lpm {
        key     = { hdr.ipv4.dstAddr : lpm; }
        actions = { ipv4_forward; drop_pkt; NoAction; }
        size    = 1024;
        default_action = drop_pkt();
    }

    // ── Main pipeline (apply block) ────────────────────────────────────────

    apply {

        // Initialise metadata defaults
        meta.flow_id     = 0;
        meta.is_dropped  = 0;
        meta.should_clone = 0;

        if (hdr.ipv4.isValid()) {

            if (hdr.tcp.isValid()) {

                // ── GUARD: bypass karma for SYN / FIN / RST ──────────────
                // ctrl bits: FIN=0x01, SYN=0x02, RST=0x04
                // We only evaluate DATA and ACK packets (ctrl & 0x07 == 0)
                bit<6> ctrl_flags = hdr.tcp.ctrl & 6w0x07;
                if (ctrl_flags == 0) {

                    // ── STAGE 1: Flow Identification ─────────────────────
                    flow_id_exact.apply();

                    if (meta.flow_id != 0) {
                        bit<32> idx = (bit<32>)meta.flow_id;

                        // ── Read per-flow state ───────────────────────────
                        reg_bytes.read(meta.flow_bytes, idx);
                        reg_karma.read(meta.karma_score, idx);

                        bit<48> wstart;
                        reg_wstart.read(wstart, idx);

                        bit<48> last_seen;
                        reg_last_seen.read(last_seen, idx);

                        bit<32> total_pkts;
                        reg_total_pkts.read(total_pkts, idx);

                        bit<16> prev_karma;
                        reg_prev_karma.read(prev_karma, idx);

                        bit<48> now = standard_metadata.ingress_global_timestamp;

                        // ── Read controller parameters ────────────────────
                        bit<32> fair_bytes;
                        reg_fair_bytes.read(fair_bytes, 0);
                        if (fair_bytes == 0) { fair_bytes = 4688; } // default: 10Mbps/8flows/15ms window

                        bit<32> pen_base;
                        reg_penalty_amt.read(pen_base, 0);
                        if (pen_base == 0) { pen_base = 8; }  // methodology default

                        bit<32> reward_base;
                        reg_reward_amt.read(reward_base, 0);
                        if (reward_base == 0) { reward_base = 4; } // methodology default

                        // Proportional penalty tiers (from methodology Section 3.3):
                        // tier1: > 1.5× fair  → base penalty
                        // tier2: > 4×  fair   → 2× penalty
                        // tier3: > 8×  fair   → 4× penalty
                        // Proportional penalty tiers (methodology 3.3):
                        // tier1 (default): bytes > fair_bytes but not > tier2 → base penalty
                        // tier2: > 4×  fair → 2× penalty
                        // tier3: > 8×  fair → 4× penalty
                        bit<32> tier2_bytes = fair_bytes << 2;                // 4×
                        bit<32> tier3_bytes = fair_bytes << 3;                // 8×

                        // Packet counter increment
                        total_pkts = total_pkts + 1;

                        // ── STAGE 3a: Idle Flow Recovery ─────────────────
                        // Methodology 3.3: flow silent > 500ms → karma boost,
                        // reset window so stale burst doesn't cause false penalty
                        if (last_seen != 0) {
                            bit<48> idle_gap = now - last_seen;
                            if (idle_gap > 48w500000) {
                                meta.karma_score = meta.karma_score + 5;
                                if (meta.karma_score > MAX_KARMA) {
                                    meta.karma_score = (bit<16>)MAX_KARMA;
                                }
                                wstart = now;
                                meta.flow_bytes = 0;
                            }
                        }

                        // ── STAGE 2 & 3: Byte Counting + Karma Update ────

                        if (wstart == 0) {
                            // Brand-new flow: initialise karma and window
                            meta.karma_score = (bit<16>)KARMA_INIT;
                            meta.flow_bytes  = standard_metadata.packet_length;
                            reg_wstart.write(idx, now);
                        } else {
                            bit<48> elapsed = now - wstart;

                            if (elapsed > (bit<48>)WINDOW_USEC) {
                                // ── Window expired: evaluate and reset ────

                                if (meta.flow_bytes > fair_bytes) {

                                    // ── STAGE 3b: Slow-Start Immunity ────
                                    // First 20 packets exempt (TCP slow-start phase)
                                    if (total_pkts > SLOWSTART_PKTS) {

                                        // ── Proportional Penalty ─────────
                                        bit<16> pen = (bit<16>)pen_base; // base (1.5× tier)

                                        if (meta.flow_bytes > tier3_bytes) {
                                            // > 8× fair share: 4× penalty
                                            pen = (bit<16>)(pen_base << 2);
                                        } else if (meta.flow_bytes > tier2_bytes) {
                                            // > 4× fair share: 2× penalty
                                            pen = (bit<16>)(pen_base << 1);
                                        }
                                        // else: 1.5× – 4× → base penalty

                                        if (meta.karma_score > pen) {
                                            meta.karma_score = meta.karma_score - pen;
                                        } else {
                                            meta.karma_score = 0;
                                        }

                                        // ── Karma Momentum ───────────────
                                        // If karma dropped > 15 points this window
                                        // (rapid degradation), add extra penalty
                                        if (prev_karma > meta.karma_score) {
                                            bit<16> delta = prev_karma - meta.karma_score;
                                            if (delta >= 15) {
                                                if (meta.karma_score > 5) {
                                                    meta.karma_score = meta.karma_score - 5;
                                                } else {
                                                    meta.karma_score = 0;
                                                }
                                            }
                                        }
                                    }
                                    // Under slow-start immunity: no penalty applied

                                } else {
                                    // ── Reward: flow stayed under fair share ──
                                    meta.karma_score = meta.karma_score + (bit<16>)reward_base;
                                    if (meta.karma_score > MAX_KARMA) {
                                        meta.karma_score = (bit<16>)MAX_KARMA;
                                    }
                                }

                                // ── PFQ Buffer Recycling (Algorithm 1 / Formula 6) ────
                                // First, compute the budget that applied to this just-expired window:
                                bit<32> expired_budget = fair_bytes;
                                if (meta.flow_color == YELLOW) {
                                    expired_budget = fair_bytes - (fair_bytes >> 2);
                                } else if (meta.flow_color == RED) {
                                    expired_budget = fair_bytes >> 2;
                                }

                                // Compute utilisation ratio: ratio_i = flow_bytes / expired_budget
                                // NOTE: Thresholds scaled for 3 Mbps bottleneck (250 pps)
                                // where max queue depth is ~10-15 packets
                                bit<19> pfq_thresh;
                                if (meta.flow_bytes < (expired_budget >> 2)) {
                                    // < 25% used: heavily underutilised — give GREEN-level headroom
                                    pfq_thresh = 15;
                                } else if (meta.flow_bytes < (expired_budget >> 1)) {
                                    // 25–50% used: moderate headroom
                                    pfq_thresh = 10;
                                } else if (meta.flow_bytes < expired_budget) {
                                    // 50–100% used: within budget, standard headroom
                                    pfq_thresh = 6;
                                } else {
                                    // Over budget: strict color-based throttle
                                    if (meta.flow_color == GREEN) {
                                        pfq_thresh = 12;
                                    } else if (meta.flow_color == YELLOW) {
                                        pfq_thresh = 5;
                                    } else {
                                        pfq_thresh = 2;  // RED: tightly quarantined
                                    }
                                }
                                reg_pfq_threshold.write(idx, pfq_thresh);

                                // Reset window
                                meta.flow_bytes = standard_metadata.packet_length;
                                reg_wstart.write(idx, now);
                                reg_prev_karma.write(idx, meta.karma_score);

                            } else {
                                // Still within window — accumulate bytes
                                meta.flow_bytes = meta.flow_bytes + standard_metadata.packet_length;
                            }
                        }

                        // ── STAGE 4: Color Zone Assignment ───────────────
                        // Methodology Section 3.4 thresholds:
                        //   GREEN  76-100 (karma > 75)
                        //   YELLOW 41-75  (karma > 40)
                        //   RED    0-40

                        if (meta.karma_score >= GREEN_THRESH) {
                            meta.flow_color = GREEN;
                            standard_metadata.priority = 3w7; // Queue 7 (Highest)
                        } else if (meta.karma_score >= YELLOW_THRESH) {
                            meta.flow_color = YELLOW;
                            standard_metadata.priority = 3w4; // Queue 4 (Medium)
                        } else {
                            meta.flow_color = RED;
                            standard_metadata.priority = 3w0; // Queue 0 (Lowest)
                        }

                        // ── STAGE 6: RED Streak Recovery ─────────────────
                        // Methodology Section 3.6:
                        // After 20 consecutive RED windows (~300ms), reset karma to 30
                        // so the flow gets a formal second chance (probation period)
                        bit<8> red_streak;
                        reg_red_streak.read(red_streak, idx);

                        if (meta.flow_color == RED) {
                            red_streak = red_streak + 1;
                            if (red_streak >= RED_STREAK_MAX) {
                                // Recovery: boost to low YELLOW
                                meta.karma_score = KARMA_RESET_VAL;
                                meta.flow_color  = YELLOW;
                                red_streak       = 0;
                            }
                        } else {
                            red_streak = 0; // reset streak whenever not RED
                        }
                        reg_red_streak.write(idx, red_streak);

                        // ── STAGE 5a: Catch dynamic threshold & Flow Budget ────
                        reg_pfq_threshold.read(meta.pfq_threshold, idx);

                        bit<32> flow_budget = fair_bytes;  // GREEN default = 100%
                        if (meta.flow_color == YELLOW) {
                            flow_budget = fair_bytes - (fair_bytes >> 2); // 75%
                        } else if (meta.flow_color == RED) {
                            flow_budget = fair_bytes >> 2;  // 25%
                        }

                        // ── STAGE 5b: Drop + ECN Enforcement ─────────────
                        // Methodology Section 3.5:
                        //   Probabilistic drop when bytes exceed color budget.
                        //   ECN mark on surviving packets.

                        if (meta.flow_bytes > flow_budget) {
                            // Stage 5b PFQ Update: 
                            // Ingress no longer probabilistically drops packets!
                            // It only applies ECN markings on aggressive traffic.
                            // The real queue depth dropping now happens in Egress.
                            hdr.ipv4.diffserv = hdr.ipv4.diffserv | 8w0x03;
                        }

                        // ── STAGE 7: Priority Queue Mapping ──────────────
                        // Methodology Section 3.7:
                        //   GREEN  → Queue 2 (highest priority)
                        //   YELLOW → Queue 1 (medium)
                        //   RED    → Queue 0 (lowest, may be preempted)

                        if (meta.is_dropped == 0) {
                            // ── STAGE 7: PFQ Priority Queue Mapping ──────────
                            // PFQ Queue Architecture (8 queues, --priority-queues 8):
                            //   Q7 — Network Control / ARP / Telemetry (never drops)
                            //   Q2 — GREEN karma flows  (high priority)
                            //   Q1 — YELLOW karma flows (medium priority)
                            //   Q0 — RED karma flows    (quarantine, strict headroom)
                            //
                            // Requires BMv2 started with: --priority-queues 8
                            if (meta.flow_color == GREEN)  { standard_metadata.priority = 3w2; }
                            else if (meta.flow_color == YELLOW) { standard_metadata.priority = 3w1; }
                            else { standard_metadata.priority = 3w0; }

                            // Track forwarded bytes for controller's JFI calculation
                            bit<32> fwd;
                            reg_forwarded_bytes.read(fwd, idx);
                            reg_forwarded_bytes.write(idx, fwd + standard_metadata.packet_length);
                        }

                        // ── STAGE 8: Telemetry Clone Trigger ─────────────
                        // Methodology Section 3.8 (inspired by HINT):
                        // Clone on color transition OR every 8th packet.

                        bit<2> prev_color;
                        reg_prev_color.read(prev_color, idx);

                        if (meta.is_dropped == 0) {
                            if (meta.flow_color != prev_color || (total_pkts & 32w7) == 0) {
                                meta.should_clone = 1;
                                reg_prev_color.write(idx, meta.flow_color);
                            }
                        }

                        // ── Write state back to registers ─────────────────
                        reg_bytes.write(idx, meta.flow_bytes);
                        reg_karma.write(idx, meta.karma_score);
                        reg_last_seen.write(idx, now);
                        reg_total_pkts.write(idx, total_pkts);

                    } // end: meta.flow_id != 0
                } // end: ctrl_flags == 0 (not SYN/FIN/RST)
            } // end: tcp.isValid()

            // ── Standard IPv4 forwarding (all packets including SYN/FIN/ARP) ──
            ipv4_lpm.apply();

        } else {
            // ── Non-IPv4 packets (ARP, ICMP, Telemetry clones) ──
            // Mapped to Queue 7 (highest priority) so they are NEVER
            // dropped or delayed by data-plane congestion.
            // This isolates the control plane from the data plane exactly
            // as recommended by PFQ's buffer reservation design.
            standard_metadata.priority = 3w7;
        }
        // Note: Non-IPv4 packets that reach here without a forwarding entry
        // will be dropped by ipv4_lpm default action, which is correct.
    }
}

// ─── Egress: Telemetry Stamping ───────────────────────────────────────────────

control MyEgress(inout parsed_headers_t hdr,
                 inout local_metadata_t meta,
                 inout standard_metadata_t standard_metadata) {

    register<bit<19>>(8)  reg_qdepth;        // queue depth per egress port (read by controller)
    register<bit<32>>(REG_SIZE) reg_drops;   // per-flow drop counter (Egress drops, not Ingress)
    // NOTE: reg_drops is also declared in Ingress. In BMv2, both declarations
    // map to the SAME shared register memory (BMv2 uses register name as the key).
    // This is a simulator-specific behaviour used intentionally here.

    apply {

        if (standard_metadata.instance_type == 0) {
            
            // ── PFQ Egress Drop Logic (Algorithm 3 — Enqueue quota check) ──
            // Read the per-flow DYNAMIC threshold computed by the Buffer
            // Recycling formula at the last window boundary (Ingress stage 5a).
            // If a flow underused its previous window, it gets a larger threshold
            // (more buffer headroom). This is the core of PFQ buffer recycling:
            // idle flows donate their unused space to active flows.
            
            // ── PFQ Egress Drop Logic (Algorithm 3 — Enqueue quota check) ──
            // GUARD: Only apply PFQ enforcement to flows identified by Ingress
            // (flow_id 1-4). Packets with flow_id==0 are reverse-path ACKs,
            // ARP, or unclassified traffic. Dropping those kills TCP ACKs.
            bit<32> flow_idx = (bit<32>)meta.flow_id;
            bit<19> drop_threshold = 0;

            if (flow_idx != 0) {
                // Read the dynamically computed threshold from metadata (passed from Ingress)
                drop_threshold = meta.pfq_threshold;

                if (drop_threshold == 0) {
                    // New flows whose window has never expired yet:
                    // use conservative static defaults by color
                    // NOTE: Scaled for 3 Mbps bottleneck (max qdepth ~10-15)
                    if (meta.flow_color == 2w2)      { drop_threshold = 12; } // GREEN
                    else if (meta.flow_color == 2w1) { drop_threshold = 5;  } // YELLOW
                    else                             { drop_threshold = 2;  } // RED
                }

                // PFQ Proactive Drop: if this flow's share of the physical queue
                // exceeds its dynamically allocated threshold, drop the packet.
                if (standard_metadata.enq_qdepth > drop_threshold) {
                    bit<32> dc;
                    reg_drops.read(dc, flow_idx);
                    reg_drops.write(flow_idx, dc + 1);
                    meta.is_dropped = 1;
                    mark_to_drop(standard_metadata);
                }
            }

            // Save queue depth for controller polling
            meta.saved_qdepth = standard_metadata.enq_qdepth;
            reg_qdepth.write((bit<32>)standard_metadata.egress_port,
                              standard_metadata.enq_qdepth);

            // Trigger E2E clone for telemetry if ingress requested it
            if (meta.should_clone == 1 || meta.is_dropped == 1) {
                clone(CloneType.E2E, TELEMETRY_SESSION);
            }
        }

        // Stamp telemetry header on ALL clone types (I2E drops + E2E forwards)
        if (standard_metadata.instance_type == 1 ||
            standard_metadata.instance_type == 2) {

            hdr.kbcs_telemetry.setValid();
            hdr.ethernet.etherType         = 16w0x1234;  // custom telemetry etherType
            hdr.kbcs_telemetry.flow_id     = (bit<8>)meta.flow_id;
            hdr.kbcs_telemetry.karma_score = (bit<8>)meta.karma_score;
            hdr.kbcs_telemetry.color       = meta.flow_color;
            hdr.kbcs_telemetry.queue_id    = (bit<3>)standard_metadata.priority;
            hdr.kbcs_telemetry.is_dropped  = meta.is_dropped;
            hdr.kbcs_telemetry._pad        = 0;

            if (standard_metadata.instance_type == 2) {
                // E2E clone: fill in real queue depth
                hdr.kbcs_telemetry.enq_qdepth = meta.saved_qdepth;
            } else {
                // I2E clone (drop notification): queue depth not relevant
                hdr.kbcs_telemetry.enq_qdepth = 0;
            }
        }
    }
}

// ─── Deparser ─────────────────────────────────────────────────────────────────

control MyDeparser(packet_out packet, in parsed_headers_t hdr) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.kbcs_telemetry);  // only valid on cloned telemetry packets
        packet.emit(hdr.ipv4);
        packet.emit(hdr.tcp);
    }
}

// ─── Checksum Update ──────────────────────────────────────────────────────────

control MyComputeChecksum(inout parsed_headers_t hdr, inout local_metadata_t meta) {
    apply {
        update_checksum(hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
              hdr.ipv4.totalLen, hdr.ipv4.identification,
              hdr.ipv4.flags, hdr.ipv4.fragOffset,
              hdr.ipv4.ttl, hdr.ipv4.protocol,
              hdr.ipv4.srcAddr, hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum, HashAlgorithm.csum16);
    }
}

// ─── Switch Instantiation ─────────────────────────────────────────────────────

V1Switch(
    MyParser(),
    MyVerifyChecksum(),
    MyIngress(),
    MyEgress(),
    MyComputeChecksum(),
    MyDeparser()
) main;
