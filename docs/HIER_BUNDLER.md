# HierarchicalBundler — Algorithm and Design

## 1. Role in the Pipeline

`HierarchicalBundler` replaces the flat `Bundler` for designs loaded through
BDB (hierarchical mode). It reads component/net/pin data from BDB and produces
`vector<HBundle>` where each bundle belongs to a specific hierarchy depth and
carries optional `cell_context` / `instances` fields for multiple-occurrence
routing template support.

Prerequisite BDB state before calling `run()`:
1. `add_cell` + `add_inst_to_cell` + `add_inst` — component hierarchy
2. `add_net_pins` (or `bdb_net_mode on` + `add_bus`) — net/pin data with
   hierarchy propagation
3. `derive_busterms` — busterm rows (used for `entry/exit_busterm_ids`)

---

## 2. How `add_net_pins` Propagates the Hierarchy

`add_net_pins(net, driver_path.pin, [rcv_path.pin, …])` computes the longest
common path prefix of all endpoints, then inserts:

- One **leaf pin** per endpoint at its component
- One **interface pin** at every ancestor that is strictly below the common
  ancestor and strictly above the leaf

Direction: driver endpoint → `OUTPUT`; receiver endpoints → `INPUT`.  Interface
pins inherit the same direction as their leaf's endpoint.

### Example — cross-block net `s2p_0`

```
add_net_pins("s2p_0", "src_i/buf_i.out", ["proc_i/pa_i.in"])

common prefix = ""  (src_i ≠ proc_i at depth 0)

pins inserted:
  depth 1   src_i/buf_i   "out"    OUTPUT   ← leaf
  depth 0   src_i         "s2p_0"  OUTPUT   ← interface
  depth 1   proc_i/pa_i   "in"     INPUT    ← leaf
  depth 0   proc_i        "s2p_0"  INPUT    ← interface
```

### Example — intra-block net `pa_pb_0`

```
add_net_pins("pa_pb_0", "proc_i/pa_i.out", ["proc_i/pb_i.in"])

common prefix = "proc_i"  (both live inside proc_i, depth-0 component)
→ loop condition d > common (1 > 1) is false immediately; no interface pins.

pins inserted:
  depth 1   proc_i/pa_i   "out"    OUTPUT   ← leaf only
  depth 1   proc_i/pb_i   "in"     INPUT    ← leaf only
```

**Key consequence**: intra-block nets have pins only at depth 1; cross-block
nets have pins at both depth 0 and depth 1.  Pins (visibility) are per-depth;
**bundling is not** — each net is placed in exactly one HBundle, at its most
specific endpoints (see step 2b).

---

## 3. Algorithm — `HierarchicalBundler::run(int max_depth)`

### Step 1 — Index BDB

```
comp_by_id   : map<int, ComponentRow>          from all_components()
net_name     : map<int, string>                 from all_nets()
pins_by_net  : map<int, vector<PinRow>>         from all_pins()
```

### Step 2 — Per-depth loop (D = 0 … max_depth)

#### 2a. Find endpoints at depth D

```
for each net N in pins_by_net:
  driver_comp_id = first pin where comp.depth == D AND dir == "OUTPUT"
  recv_comp_ids  = [pin.comp_id where comp.depth == D AND dir == "INPUT"]
  unknown_ids    = [pin.comp_id where comp.depth == D AND dir == "UNKNOWN"]

  # Positional fallback for nets whose pins have no OUTPUT/INPUT (e.g. from
  # import_verilog or add_net … unknown):
  if driver_comp_id not valid AND unknown_ids non-empty:
    driver_comp_id = unknown_ids[0]        # first UNKNOWN = tentative driver
    recv_comp_ids += unknown_ids[1:]       # remaining = tentative receivers
    emit warning to stderr

  if driver_comp_id valid AND recv_comp_ids non-empty:
    ep_map[N] = { driver_comp_id, recv_comp_ids }
```

Cross-block nets appear in ep_map at D=0 AND D=1 (their interface pins make
them visible at every depth they cross); intra-block nets only at D=1 (and
deeper).  ep_map is visibility, not assignment — step 2b picks exactly one
depth per net.

**UNKNOWN direction:** Pins stored with `dir="UNKNOWN"` (produced by
`import_verilog`, `import_def_lef` when LEF has no DIRECTION, or `add_net …
unknown` / `add_net_pins_undirected`) are treated with a positional fallback:
the first UNKNOWN pin encountered (in BDB insertion order) is promoted to
driver; all remaining UNKNOWN pins at the same depth become receivers. A
`[HierBundler]` warning line is printed to stderr for each net that uses this
fallback. Nets with exactly one UNKNOWN pin are dropped (no receiver).

#### 2b. Group by STRICT signature — one bundle per net

A net is bundled **only at its most specific projection**: it is skipped at
depth D unless `D == min(leaf_endpoint_depth, max_depth)`.  Ancestor-level
projections of the same net are views of the same physical wires; bundling
them too would route the net once per depth (multiple parallel copies of
metal — the pre-fix behavior).

```
for each net in ep_map:
  if D != min(drv_spec_depth, max_depth): skip
sig = "DRV:" + comp_name[driver] + "|REC:" + sorted(comp_names[recv]), joined by ","
sig_to_nets: map<sig, [net_id]>
```

#### 2c. Create one HBundle per group

Fields set here:

| Field | Value |
|---|---|
| `id` | auto-increment |
| `level` | depth of the endpoints' **common ancestor** (the routing context — same convention as cross-level `bundle_depth`); a cross-chip net is a level-0 routing problem even when its endpoints are leaf pins. Falls back to D when leaf info is unavailable. |
| `net_names` | net names in this group |
| `num_terminals` | 1 + len(recv_comps) for first net |
| `reason` | the sig string (for debugging) |

### Step 3 — Cell context

After creating the HBundle, examine the first net's endpoints at depth D:

```
drv_comp = comp_by_id[ep0.driver_comp_id]
if drv_comp.parent_id >= 0:                          // not a root component
  par = comp_by_id[drv_comp.parent_id]
  if ALL receiver endpoints also have parent == par:  // same parent → intra-cell
    b.cell_context          = par.cell               // e.g. "proc_cell"
    b.instances             = [par.name]             // e.g. ["proc_i"]
    b.entry_busterm_ids     = ["bt:" + drv_comp.name]
    b.exit_busterm_ids      = ["bt:" + rcv.name for each rcv]
```

Cross-block bundles (different parents) leave `cell_context` empty.

### Step 4 — (removed) Cross-depth linkage

Cross-depth parent linkage existed to relate a net's per-depth duplicate
bundles.  With one bundle per net there is nothing to link; `parent_id` /
`child_ids` are now used **only** by the multiple-occurrence merge (step 5,
template ↔ replicas).

### Step 5 — Multiple-occurrence detection

After all depth levels are processed:

```
Group HBundles with non-empty cell_context by (cell_context, cell-local reason):
  local_reason = b.reason with b.instances[0]+"/" stripped everywhere

For each group of size ≥ 2:
  template = first bundle in group
  for each replica (remaining bundles in group):
    template.instances += replica.instances   // accumulate all parent paths
    replica.parent_id   = template.id
    template.child_ids += [replica.id]
```

**Example** (hypothetical two-proc design):
```
  proc_i1/pa_i → proc_i1/pb_i  →  cell_sig = "proc_cell::DRV:pa_i|REC:pb_i,"
  proc_i2/pa_i → proc_i2/pb_i  →  cell_sig = "proc_cell::DRV:pa_i|REC:pb_i,"
  → merged: template.instances = ["proc_i1", "proc_i2"]
```

In the single-proc_i test vehicle, pa_pb and pb_pc have different local
signatures ("pa_i→pb_i" vs "pb_i→pc_i") so they are NOT merged.

---

## 4. Expected Output for the Pipeline Test Vehicle

After `run(max_depth=1)` with 4 buses of 8 bits each — one bundle per bus:

```
hbundle-1  level=0  nets=[s2p_0..7]    reason="DRV:src_i/buf_i|REC:proc_i/pa_i,"
hbundle-2  level=0  nets=[p2s_0..7]    reason="DRV:proc_i/pc_i|REC:snk_i/rcv_i,"
hbundle-3  level=1  nets=[pa_pb_0..7]  cell_context=proc_cell instances=["proc_i"]
hbundle-4  level=1  nets=[pb_pc_0..7]  cell_context=proc_cell instances=["proc_i"]
```

Total: 4 HBundles.  The cross-block buses carry leaf-precision endpoint paths
but level 0 — the depth of their common ancestor (their routing context).
Topology generation builds the floorplan for such bundles from the blocks at
the **endpoint depth** (path segments − 1), not at `level`.

---

## 5. CLI Command

```
run_hier_bundler [depth <N>]
```

Default max_depth = 1. Requires an open BDB with nets populated via
`bdb_net_mode on` before this call. Stores result in `session.bundles`.

Output:
```
HierBundler: N hbundles (D0: a, D1: b, …)
```

---

## 5a. CLI Inspection: `dump_hbundles`

After `run_hier_bundler`, the full HBundle set can be inspected with:

```
dump_hbundles [expanded] [depth N]
```

### Pre-expansion snapshot

At `run_hier_bundler` time, the CLI captures a snapshot of the bundle list in
`_hier_bundles_orig`. By default `dump_hbundles` reads from this snapshot,
so the canonical bundle IDs (as the user sees them) are always available even
after `run_planner hier` has replaced `self.bundles` with expanded per-instance
wrappers.

### Output format

```
hb-{id}  D{level}  {kind}  "{short_reason}"  nets={n}  cands={c}  [{instances}]
```

| Field | Description |
|---|---|
| `hb-{id}` | Bundle integer ID |
| `D{level}` | Hierarchy depth |
| `{kind}` | `cell:{cell_context}` if cell_context set; `cross-level` if drv_spec_depth ≥ 0; else `cross-block` |
| `{short_reason}` | Abbreviated driver/receiver signature |
| `nets={n}` | Number of nets in the bundle |
| `cands={c}` | Number of topology candidates (0 = topology not yet generated) |
| `[{instances}]` | Instance path list — shown only for cell-level bundles |

### Flags

| Flag | Effect |
|---|---|
| `expanded` | Show the current `self.bundles` (post-expansion per-instance wrappers) instead of the pre-expansion snapshot. Useful for verifying that `run_planner hier` expanded cell-level bundles correctly. |
| `depth N` | Filter output to bundles at hierarchy level N only. |

### Example output — pipeline test vehicle

```
hb-1  D0  cross-block  "DRV:src_i|REC:proc_i,"  nets=8  cands=7
hb-2  D0  cross-block  "DRV:proc_i|REC:snk_i,"  nets=8  cands=7
hb-3  D1  cross-block  "DRV:src_i/buf_i|REC:proc_i/pa_i,"  nets=8  cands=5
hb-4  D1  cell:proc_cell  "DRV:pa_i|REC:pb_i,"  nets=8  cands=4  [proc_i]
hb-5  D1  cell:proc_cell  "DRV:pb_i|REC:pc_i,"  nets=8  cands=4  [proc_i]
hb-6  D1  cross-block  "DRV:proc_i/pc_i|REC:snk_i,"  nets=8  cands=5
```

The `cands` field is 0 before `generate_hier_topologies` is called and
updates in-place — re-running `dump_hbundles` after topology generation shows
the filled-in counts.

---

## 6. Differences from Flat Bundler

| Aspect | Flat `Bundler` | `HierarchicalBundler` |
|--------|---------------|----------------------|
| Input | `Netlist` (in-memory) | `BDB` (with pins) |
| Depth awareness | No | Yes — runs depth 0..max_depth |
| Intra-cell nets | Bundled normally | Only visible at their natural depth |
| Cell context | None | `cell_context` + `instances` |
| Multiple occurrence | None | Merged by cell-local sig |
| Depth linkage | None | parent/child HBundle ids |

---

## 7. Files Modified / Created

| File | Change |
|------|--------|
| `src/bundler.h` | Add `HierarchicalBundler` class |
| `src/bundler.cpp` | Implement `HierarchicalBundler::run()` |
| `src/bindings.cpp` | Expose `HierarchicalBundler` to Python |
| `src/buda_cli.py` | Add `run_hier_bundler` command |
| `test/tests/features/hier_bundler.feature` | BDD scenarios |
| `test/tests/test_hier_bundler.py` | Step defs + standalone tests |
