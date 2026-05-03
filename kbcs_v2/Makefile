# KBCS v2 — Makefile for P4 Tutorial Framework
# ================================================
# Usage (inside the P4 VM, from ~/kbcs_v2):
#
#   source ~/src/p4dev-python-venv/bin/activate   (activate venv first!)
#   make compile     — compile P4 program only
#   make run         — start Mininet with 4-switch KBCS topology
#   make stop        — stop running Mininet
#   make clean       — remove compiled artifacts
#
# After `make run` you will see:
#   mininet> h1 ping h2   (test connectivity)
#   mininet> xterm h1     (open terminal on host 1)

BMV2_SWITCH_EXE = simple_switch_grpc
TOPO = kbcs-topo/topology.json

# Pull in the standard targets from the tutorial framework
include /home/p4/tutorials/utils/Makefile

# OVERRIDE after include so our values win:
# 1) Fix the relative path to run_exercise.py
RUN_SCRIPT = /home/p4/tutorials/utils/run_exercise.py

# 2) Skip the tutorial's built-in compile step (we compile separately
#    because our P4 file lives in p4src/, not the root directory).
#    Setting these empty means `build: dirs` just creates directories.
source =
compiled_json =

# 3) Point directly at the pre-compiled JSON in p4src/
DEFAULT_JSON = p4src/kbcs_v2.json

# Custom compile target for our P4 program
compile:
	p4c-bm2-ss --p4v 16 \
		--p4runtime-files p4src/kbcs_v2.p4info.txtpb \
		-o p4src/kbcs_v2.json \
		p4src/kbcs_v2.p4
	@echo "Compiled: p4src/kbcs_v2.json + p4src/kbcs_v2.p4info.txtpb"
