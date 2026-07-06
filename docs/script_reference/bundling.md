# BUDA Script Reference — Stage 1 — Bundler

Grouping nets into buses: `run_bundler`, `run_hier_bundler`, `dump_hbundles`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Stage 1 — Bundler

### `run_bundler`

```
run_bundler strict
run_bundler convergent
run_bundler bidirectional
```

Group all nets in the netlist into `Bundle` objects (default `strict`). Must be
called after all `add_net` / `add_bus` commands and before
`generate_topologies_for_bundle`.

| Strategy | Grouping rule |
|---|---|
| `strict` | Driver instance **and** sorted receiver instances must match exactly (a true parallel bus). |
| `convergent` | Only sorted receiver instances must match; different drivers allowed (fan-in). |
| `bidirectional` | Direction-agnostic: the signature is the sorted set of **all** endpoint instances (driver + receivers), so nets connecting the same group of blocks in any roles bundle together — `A→B` with its return `B→A`, or the cyclic `a→b,c` / `b→c,a` / `c→b,a`. |

> ℹ️ `bidirectional` groups nets that connect the **same** blocks, so the single
> block-to-block trunk routes every net (routing is direction-agnostic) — it is
> sound. In the visualizer such a busterm is both a driver and a receiver and is
> drawn with its own symbol (green diamond).
>
> ⚠️ `convergent`, by contrast, can group nets whose drivers are **different
> blocks** at different locations, which topology generation (a single `src→dst`
> per bundle) cannot yet route faithfully — only one driver is reached, the
> others are left unrouted — so it prints a warning. See
> [`docs/internal/convergent_bundling.md`](../internal/convergent_bundling.md).

Bundle width is computed automatically as `1.5 × (number of nets)` layout
units. The bundler prints the number of bundles created.

**Sidecar:** topology selections saved from a previous `visualize_topologies`
session are loaded later by `run_planner` and applied on top of the planner's
choices (architect overrides).

**Example:**
```
run_bundler strict
```

---

### `run_hier_bundler`

```
run_hier_bundler [depth <N>] [STRICT|BIDIRECTIONAL]
```

Group nets into hierarchy-aware bundles (HBundles) by querying the component hierarchy in the open BDB. Must be called after `open_bdb` and populating the database.

| Argument | Type | Description |
|---|---|---|
| `depth N` | keyword+int | Optional. Maximum hierarchy depth to traverse and group (defaults to `1`). |
| `STRICT` / `BIDIRECTIONAL` | keyword | Optional grouping strategy (default `STRICT`, matching `run_bundler`). `BIDIRECTIONAL` is direction-agnostic: it keys on the sorted set of **all** endpoint instances at the bundle depth, so a net and its reverse (and cyclic multi-receiver groups) bundle together — a single bundle may then hold both bidirectional pairs and plain one-way nets. Since a bidirectional bundle connects the **same** blocks, the direction-agnostic block-to-block trunk routes every net (sound, no warning). |

HBundles group signals that cross cell boundaries at different levels of the physical hierarchy, allowing the planner and routing engines to distinguish local intra-cell routing from top-level inter-cell interconnect.

Each net is bundled **exactly once**, at its most specific endpoint projection within `depth N`; the bundle's level is the depth of the endpoints' common ancestor (its routing context). Identical cell-local buses across instances of the same cell merge into one template bundle carrying all instance paths (expanded back per instance by `run_planner hier`).

**Example:**
```buda
run_hier_bundler depth 1
run_hier_bundler depth 1 bidirectional
```

---

### `dump_hbundles`

```
dump_hbundles [expanded] [depth N]
```

Print a one-line summary for every HBundle. Must be called after `run_hier_bundler`.

| Argument | Type | Description |
|---|---|---|
| `expanded` | keyword | Show the post-expansion per-instance wrappers in `self.bundles` (state after `run_planner hier`). Without this flag the pre-expansion snapshot captured at `run_hier_bundler` time is shown. |
| `depth N` | keyword+int | Filter output to bundles at hierarchy level N. |

**Output format per line:**

```
hb-{id}  D{level}  {kind}  "{short_reason}"  nets={n}  cands={c}  [{instances}]
```

| Field | Description |
|---|---|
| `hb-{id}` | Bundle integer ID |
| `D{level}` | Hierarchy depth of this bundle |
| `{kind}` | `cell:{cell_context}` if cell_context is set; `cross-level` if drv_spec_depth ≥ 0; else `cross-block` |
| `{short_reason}` | Abbreviated reason string (driver/receiver signature) |
| `nets={n}` | Number of nets in the bundle |
| `cands={c}` | Number of topology candidates (0 = topology not yet generated) |
| `[{instances}]` | Instance list — shown only for cell-level bundles |

By default, output reflects the **pre-expansion snapshot** (`_hier_bundles_orig`) captured at `run_hier_bundler` time — the canonical bundle IDs as the user sees them. After `run_planner hier`, adding `expanded` shows the runtime per-instance wrappers instead.

**Example output (pipeline test vehicle):**
```
hb-1  D0  cross-block  "DRV:src_i|REC:proc_i,"  nets=8  cands=7
hb-2  D0  cross-block  "DRV:proc_i|REC:snk_i,"  nets=8  cands=7
hb-3  D1  cross-block  "DRV:src_i/buf_i|REC:proc_i/pa_i,"  nets=8  cands=5
hb-4  D1  cell:proc_cell  "DRV:pa_i|REC:pb_i,"  nets=8  cands=4  [proc_i]
hb-5  D1  cell:proc_cell  "DRV:pb_i|REC:pc_i,"  nets=8  cands=4  [proc_i]
hb-6  D1  cross-block  "DRV:proc_i/pc_i|REC:snk_i,"  nets=8  cands=5
```

**Example:**
```buda
run_hier_bundler depth 1
dump_hbundles                 # show all 6 bundles
dump_hbundles depth 1         # show only depth-1 bundles
dump_hbundles expanded        # show expanded per-instance view (after run_planner hier)
```

---
