# research_kbcs_v02

This repository contains the completed, publication-ready codebase for the KBCS-AQM research project, comparing our proposed Karma-Based Congestion Control (KBCS) active queue management mechanism against the baseline P4CCI system.

## Repository Structure

*   **`kbcs_v2/`**: Contains the complete P4 implementation, Ryu controller logic, Mininet topologys, and evaluation scripts for the proposed KBCS-AQM framework.
*   **`baseline_p4cci/`**: Contains the baseline comparison implementation (P4-Based Online TCP Congestion Control Algorithm Identification) and its evaluation scripts.
*   **`METHODOLOGY.md`**: Comprehensive breakdown of the architectural design, algorithmic decisions, and testing framework for KBCS-AQM.
*   **`WEEKLY_PROGRESS_REPORT.md`**: Log of project development phases and milestones.

All experimental data, plots, and CSV results for the paper can be found within the `kbcs_v2/plots/` and `kbcs_v2/results/` directories.
