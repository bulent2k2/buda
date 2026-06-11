# Congestion and Heatmap Logic

This document describes how BUDA measures routing congestion and visualizes it as a heatmap on the Hanan grid.

## 1. Congestion Measurement (`GlobalRouter`)

Congestion is measured using a 2D cut-based model on the Hanan grid.

### Grid Definition
*   The base grid is the **Hanan grid**, formed by the X and Y coordinates of all block boundaries in the floorplan.
*   The `GlobalRouter` may **extend** this grid during the planning phase. Coordinates of segment endpoints from all topology candidates that fall outside the current grid range (e.g., detour trunks) are added to the grid.
*   This ensures that all segments, including detours, are covered by the grid intervals.

### Cuts and Bands
*   **V-cuts (for Horizontal segments):**
    *   One V-cut is created at the midpoint of each X-interval in the grid, for each horizontal layer.
    *   A V-cut is a vertical line that tracks how many horizontal segments cross that X-channel.
    *   The cut is subdivided into **Y-bands** based on the Y-intervals of the grid.
*   **H-cuts (for Vertical segments):**
    *   One H-cut is created at the midpoint of each Y-interval in the grid, for each vertical layer.
    *   An H-cut is a horizontal line that tracks how many vertical segments cross that Y-channel.
    *   The cut is subdivided into **X-bands** based on the X-intervals of the grid.

### Capacity Calculation
*   The capacity of each band is the length of the band (the interval width) minus any portions blocked by floorplan blocks.
*   A block blocks a band if its coordinate range in the cut axis (e.g., X for a V-cut) covers the cut coordinate, and its range in the perpendicular axis overlaps with the band interval.

### Usage Accumulation
*   When a topology is applied, each of its segments contributes to the usage of the cuts it crosses.
*   **Effective Width:** The usage added is `bits × unit_pitch / n_signal_slots` when the layer has a track pattern (`def_track_pattern`); otherwise `bundle_width * layer_dilution_factor`.
*   **Dilution Factor:** Calculated from layer overhead as `100 / (100 - overhead_percent)`.
*   Usage is added to the specific band that contains the segment's coordinate.

### Congestion Cost and Overflow
*   **Congestion Cost:** `kCong * overflow / capacity`, where `overflow = max(0, usage + effective_width - capacity)` — zero when the segment fits. Overflow is additionally enforced as a **hard constraint** during planning; see [congestion_planner.md](congestion_planner.md) for the cost model and escalation ladder.
*   **Overflow:** `(usage + effective_width) - capacity`, clamped to 0. This is used for reporting and debugging.

## 2. Heatmap Visualization (`BudaVisualizer`)

The heatmap provides a 2D representation of the congestion data collected by the `GlobalRouter`.

### Cell Mapping
*   The visualizer iterates through all `GlobalCut` objects provided by the planner.
*   Each band of each cut is mapped back to a Hanan grid cell.
    *   For a **V-cut** at `x_mid` and band `[y_lo, y_hi]`, the corresponding cell is the rectangle `[x_start, x_end] x [y_lo, y_hi]`, where `[x_start, x_end]` is the Hanan X-interval containing `x_mid`.
    *   For an **H-cut** at `y_mid` and band `[x_lo, x_hi]`, the corresponding cell is the rectangle `[x_lo, x_hi] x [y_start, y_end]`, where `[y_start, y_end]` is the Hanan Y-interval containing `y_mid`.

### Coloring and Transparency
*   **Utilisation Ratio:** `ratio = usage / capacity`. If capacity is 0, the ratio defaults to **2.0 (200%)**.
*   **Colormap:** The visualizer uses the `RdYlGn_r` colormap (Red-Yellow-Green reversed).
*   **Alpha (Transparency):** Ranges from 0.12 (ratio 0.0) to 0.34 (ratio 1.0+), making congested cells more prominent.
*   **Overlap Note:** Since each layer has its own set of cuts, the visualization for multiple layers is layered on top of each other with transparency.

### Overflow Labels
*   If the utilisation ratio exceeds 100% (`ratio > 1.0`), an "OVF\nXX%" label is drawn in the center of the cell.

## 3. Known Issues

### Grid Mismatch
Currently, the `BudaVisualizer` derives the Hanan grid directly from the floorplan, while the `GlobalRouter` may have an extended version of the grid. This can lead to incorrect mapping of congestion bands to visual cells if detour segments are present.
