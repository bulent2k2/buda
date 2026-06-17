# Proposal for custom topology selection and layer assignment features in Buda UI

This document outlines the architecture for handling topology selection precedence between `.json` sidecar files and `.buda` script commands.

## Overview
When a `select_topology` call in a `.buda` script and a `.json` sidecar file both specify a topology for the same bus, a conflict arises. Previously, the sidecar unconditionally overwrote the script because `run_planner` applied it blindly. A simple "skip if pinned" logic would lose the detailed layer overrides (GUI manual tuning) which are a key feature of the sidecar system.

This architecture ensures the **sidecar acts as a persistent baseline** and the **script acts as an explicit override**, while maintaining full metadata compatibility.

## 1. Baseline Restoration
*   `generate_topologies` (and `generate_hier_topologies`) automatically calls `_apply_selections()` at the end of the command.
*   **Result:** As soon as topologies are generated, any GUI-tuned state (topology pins + segment layer overrides) from the `.json` file is immediately restored to memory as a baseline.

## 2. Explicit Script Overrides
*   Any `select_topology` command in the `.buda` script that appears **after** `generate_topologies` will now act as an explicit override.
*   **Intelligent Merging:** If a script command changes the topology choice, the system will **search the sidecar** for any saved layer overrides that specifically match the newly selected topology (by matching its Type and Wirelength). 
*   **Precedence:** The script dictates the *Topology*, but the sidecar still provides the *Layer overrides* for that topology if they exist.

## 3. Robust `_apply_selections` Merging
`_apply_selections` distinguishes between "Initial Pinning" and "Metadata Enrichment":
*   If a bundle is **not yet pinned**: Apply both the topology choice and the segment layers from the sidecar.
*   If a bundle is **already pinned** (by a script): **Respect the script's topology choice**, but still copy the `seg_layers` from the sidecar if the sidecar entry matches the script-chosen topology.

## 4. GUI Refinement
*   The `TopologyExplorer` prioritizes the "live" state in memory (from the script) when first opening, rather than jumping back to the sidecar's preferred index if they differ.

## Example Scenario
1.  **Sidecar (`.json`)**: Pins **Topo 1** and overrides **Segment 0 to Layer M6**.
2.  **Script (`.buda`)**: calls `select_topology 1 2`.
3.  **Result:** The system pins **Topo 2**. If layer overrides for Topo 2 were ever saved in the GUI, they are applied. If not, Topo 2 uses default layers, but the script's choice of Topo 2 is maintained.

This approach ensures that manual GUI tuning is never accidentally discarded, while still giving the `.buda` script the final authority over the high-level topology decisions.
