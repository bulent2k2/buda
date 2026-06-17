# Custom topology selection and layer assignment in the Buda UI

This document describes how topology selection precedence is handled between `.json` sidecar files and `.buda` script commands. The behavior described below is implemented.

## Overview
When a `select_topology` or `select_topologies` call in a `.buda` script and a `.json` sidecar file both specify a topology for the same bus, a conflict arises. Previously, the sidecar unconditionally overwrote the script because `run_planner` applied it blindly. A simple "skip if pinned" logic would lose the detailed layer overrides (GUI manual tuning) which are a key feature of the sidecar system.

This architecture ensures the **sidecar acts as a persistent baseline** and the **script acts as an explicit override**, while maintaining full metadata compatibility.

## 1. Baseline Restoration
*   `generate_topologies` (and `generate_hier_topologies`) automatically calls `_apply_selections()` at the end of the command.
*   **Result:** As soon as topologies are generated, any GUI-tuned state (topology pins + segment layer overrides) from the `.json` file is immediately restored to memory as a baseline.

## 2. Explicit Script Overrides
*   Any `select_topology` / `select_topologies` command in the `.buda` script that appears **after** `generate_topologies` acts as an explicit override.
*   **Precedence:** The script dictates the *Topology*. The sidecar provides the *layer overrides* **only for that same topology**.

## 3. Conflict: sidecar and script pick *different* topologies
This is the key case. Layer overrides are indexed to a specific topology (one entry per segment), so they are meaningful only on the topology they were saved for.

*   **The script's topology wins** — the sidecar never overrides a script pin.
*   If the script's topology **matches** the sidecar's (same `topo_index_hint`, or same type + wirelength, and the segment count agrees): the sidecar's `seg_layers` **are applied**.
*   If the script's topology **differs** from the sidecar's: the sidecar's `seg_layers` belong to a *different* topology, so they are **discarded** — the script topology is used with the planner's automatic layer assignment. `_apply_selections` explicitly **clears** any stale overrides in this case, so the planner never truncates the wrong-length layer list onto the new topology.

## 4. Robust `_apply_selections` Merging
`_apply_selections` runs at `generate_topologies` (baseline) and again inside `run_planner`, and is **authoritative** for each bundle it has a sidecar entry for — resolved **per matching wrapper** (so multi-instance bundles with different pin states are handled independently):
*   If a bundle is **not yet pinned**: adopt the sidecar's topology and its `seg_layers`.
*   If a bundle is **already pinned** (by a script): **respect the script's topology**; copy the sidecar `seg_layers` only when they match that topology (§3), otherwise clear them.
*   Bundles with **no** sidecar entry are left untouched.

## 5. Consistent GUI Selection
The `TopologyExplorer` resolves a bundle's pinned topology through **one** shared helper so display and navigation always agree:

*   `_selected_topo_index()` — the single pinned topology (a bundle has at most one): the **live pin** (`topology_pinned` + `plan.selected_topology_index`, which reflects §1–§4 and honors script-vs-sidecar precedence) if set, otherwise the sidecar entry for that bundle (saved index, else first candidate matching type/wirelength). The pin **badge** (`★ PINNED` / `★ PLANNER SELECTED` / `★ PINNED & PLANNER SELECTED`) is driven by this.
*   `_focus_topo_index()` — which topology to *show* when opening or switching to a bundle: the pinned topology above, else the planner's choice, else topo 0. Used by **initial open, bundle cycling (`◀/▶ Bundle`, `[`/`]`), and direct jump** alike, so the explorer always lands on the bundle's pinned topology — not just on first open.

Because §1 restores the sidecar baseline into live state at `generate_topologies` time, the live pin is authoritative even before `run_planner` / `visualize_topologies`, keeping the engine and the GUI consistent.

## Example Scenario
1.  **Sidecar (`.json`)**: pins **Topo 1** and overrides its segments to custom layers.
2.  **Script (`.buda`)**: calls `select_topology 1 2` — a **different** topology.
3.  **Result:** the system pins **Topo 2** (script wins). The sidecar's layer overrides were for **Topo 1**, so they are **discarded** and Topo 2 uses the planner's automatic layer assignment. Had the script instead selected **Topo 1** (matching the sidecar), the custom layers would be applied.

This approach ensures that manual GUI tuning is never accidentally discarded *for the topology it belongs to*, while still giving the `.buda` script final authority over the topology decision.
