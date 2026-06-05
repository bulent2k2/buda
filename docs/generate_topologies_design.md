# `generate_topologies_for_bundle` — Design Options

## Context

After `run_bundler`, each `Bundle` contains one or more nets that share the same
driver block and receiver block set.  Before the planner can run, every bundle
needs a set of candidate topologies (L / Z / U shapes).

Today the script author must call `generate_topologies_for_bundle` once per
bundle, supplying a **hint** string that is matched against the first net name
in the bundle.

---

## Current syntax (v1)

```
generate_topologies_for_bundle <hint> <src> <dst> [<dst2> ...] [center_mode] [double_detour]
```

| Argument | Purpose |
|---|---|
| `hint` | Prefix-matched against the bundle's first net name to select the target bundle |
| `src` | Source (driver) block name |
| `dst …` | One or more destination (receiver) block names |
| `center_mode` | Route to/from block centres instead of busterm face midpoints |
| `double_detour` | Include UU_HVH / UU_VHV high-congestion variants |

**Problem:** the hint is a fragile, redundant key.  When nets are declared with
`add_bus bb1[8] u1.tx u2.rx`, the first net is `bb1_0` — so the hint must be
`bb1`, not `b1`.  The src/dst pair already uniquely identifies the bundle
(STRICT mode guarantees at most one bundle per driver-block × receiver-block
pair), so the hint buys nothing.

---

## Option A — match by src/dst, drop the hint

```
generate_topologies_for_bundle <src> <dst> [<dst2> ...] [center_mode] [double_detour]
```

The command looks up the bundle whose nets' driver block == `src` and receiver
block set == `{dst …}`.  No hint needed.

```
# three_blocks_3_bundles.buda — Option A
generate_topologies_for_bundle u1 u2
generate_topologies_for_bundle u1 u3
generate_topologies_for_bundle u2 u3
```

**Pros:** unambiguous, robust to net naming.  
**Cons:** still requires one explicit call per bundle; order doesn't matter but
the author must remember to cover every bundle.

---

## Option B — auto-generate for all bundles inside `run_bundler`

`run_bundler` already knows every bundle's src and dst (from the net pin
lists).  It can call the topology generator internally for each bundle with
default settings, so no explicit topology command is needed in the common case.

```
# three_blocks_3_bundles.buda — Option B (common case)
run_bundler strict          # also generates topologies for every bundle
run_planner 1
run_nuts 2.0
visualize
```

`generate_topologies_for_bundle` becomes an **override** command — only needed
when non-default options are required for a specific bundle:

```
# override bundle u1→u2 with double_detour enabled
generate_topologies_for_bundle u1 u2 double_detour

# override bundle u1→[u2,u3] as a multicast trunk
generate_topologies_for_bundle u1 u2 u3
```

When an override is present, it replaces the candidates generated automatically
for that bundle.

**Pros:** zero boilerplate in the common case; scripts stay short.  
**Cons:** automatic generation happens silently — the author must use an override
to change routing mode.  Multicast topologies still require an explicit call
(the bundler cannot infer that multiple receivers should share a trunk vs. get
independent routes).

---

## Modifier flags (both options)

| Flag | Effect |
|---|---|
| `center_mode` | Route to/from block centre coordinates instead of busterm face midpoints |
| `double_detour` | Add UU_HVH / UU_VHV candidates for high-congestion escape routing |

---

## Recommendation

Implement **Option B** as the default, with **Option A** syntax for overrides.
This gives the shortest scripts for typical cases while preserving full control.
The migration path from the current syntax is: remove all hint-only
`generate_topologies_for_bundle` calls; keep (and update to drop the hint) any
calls that use `center_mode`, `double_detour`, or multicast.
