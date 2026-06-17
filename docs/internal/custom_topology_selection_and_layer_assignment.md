# Custom topology selection and layer assignment in the Buda UI

This document describes how topology selection precedence is handled between `.json` sidecar files and `.buda` script commands. The behavior described below is implemented.

## Overview
When a `select_topology` or `select_topologies` call in a `.buda` script and a `.json` sidecar file both specify a topology for the same bus, a conflict arises. Previously, the sidecar unconditionally overwrote the script because `run_planner` applied it blindly. A simple "skip if pinned" logic would lose the detailed layer overrides (GUI manual tuning) which are a key feature of the sidecar system.

This architecture ensures the **sidecar acts as a persistent baseline** and the **script acts as an explicit override**, while maintaining full metadata compatibility.

## 1. Baseline Restoration
*   `generate_topologies` (and `generate_hier_topologies`) automatically calls `_apply_selections()` at the end of the command.
*   **Result:** As soon as topologies are generated, any GUI-tuned state (topology pins + segment layer overrides) from the `.json` file is immediately restored to memory as a baseline.

## 2. Explicit Script Overrides
*   Any `select_topology` command in the `.buda` script that appears **after** `generate_topologies` will now act as an explicit override.
*   **Intelligent Merging:** If a script command changes the topology choice, the system will **search the sidecar** for any saved layer overrides that specifically match the newly selected topology (by matching its Type and Wirelength). 
*   **Precedence:** The script dictates the *Topology*, but the sidecar still provides the *Layer overrides* for that topology if they exist.

## 3. Robust `_apply_selections` Merging
`_apply_selections` distinguishes between "Initial Pinning" and "Metadata Enrichment", resolved **per matching wrapper** (so multi-instance bundles with different pin states are handled independently):
*   If a bundle is **not yet pinned**: Apply both the topology choice and the segment layers from the sidecar.
*   If a bundle is **already pinned** (by a script): **Respect the script's topology choice**, but still copy the `seg_layers` from the sidecar if the sidecar entry matches the script-chosen topology (same type/wirelength and segment count).

## 4. Consistent GUI Selection
The `TopologyExplorer` resolves a bundle's pinned topology through **one** shared helper so display and navigation always agree:

*   `_selected_topo_index()` — the single pinned topology (a bundle has at most one): the **live pin** (`topology_pinned` + `plan.selected_topology_index`, which reflects §1–§3 and honors script-vs-sidecar precedence) if set, otherwise the sidecar entry for that bundle (saved index, else first candidate matching type/wirelength). The pin **badge** (`★ PINNED` / `★ PLANNER SELECTED` / `★ PINNED & PLANNER SELECTED`) is driven by this.
*   `_focus_topo_index()` — which topology to *show* when opening or switching to a bundle: the pinned topology above, else the planner's choice, else topo 0. Used by **initial open, bundle cycling (`◀/▶ Bundle`, `[`/`]`), and direct jump** alike, so the explorer always lands on the bundle's pinned topology — not just on first open.

Because §1 restores the sidecar baseline into live state at `generate_topologies` time, the live pin is authoritative even before `run_planner` / `visualize_topologies`, keeping the engine and the GUI consistent.

## Example Scenario
1.  **Sidecar (`.json`)**: Pins **Topo 1** and overrides **Segment 0 to Layer M6**.
2.  **Script (`.buda`)**: calls `select_topology 1 2`.
3.  **Result:** The system pins **Topo 2**. If layer overrides for Topo 2 were ever saved in the GUI, they are applied. If not, Topo 2 uses default layers, but the script's choice of Topo 2 is maintained.

This approach ensures that manual GUI tuning is never accidentally discarded, while still giving the `.buda` script the final authority over the high-level topology decisions.
