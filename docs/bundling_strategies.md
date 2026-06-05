# Bundling Strategies

## Problem

When multiple `add_net` and `add_bus` declarations share the same driver/receiver
signature, the bundler must decide how to partition them into physical bus
bundles.  The right answer depends on design intent: a single aggregate bus
occupies a wider track slot and is routed as one object, while splitting gives
the router more flexibility to place pieces on different layers or tracks.

### Running example

```
add_net  net1      u1.tx  u2.rx
add_net  net2      u1.tx  u2.rx
add_net  net3      u1.tx  u2.rx
add_bus  bus1[8]   u1.tx  u2.rx
add_bus  bus2[8]   u1.tx  u2.rx
```

All 19 nets share the STRICT signature `DRV:u1|REC:u2`.

---

## Strategy 1 — All-in-one (current default)

All nets with the same signature are merged into a single bundle.

| Bundle | Nets | Width |
|---|---|---|
| b1 | net1, net2, net3, bus1_0…bus1_7, bus2_0…bus2_7 | 19 |

**Use when:** the entire set of signals travels together and you want a single
wide track allocation.

**`.buda` syntax:**
```
run_bundler strict
```

---

## Strategy 2 — Group by declaration (source-aware)

Each `add_net` group and each `add_bus` declaration becomes its own bundle.
Individual `add_net` calls that share a signature are still merged into one
"scalar" bundle.

| Bundle | Source | Nets | Width |
|---|---|---|---|
| b1 | add_net (3×) | net1, net2, net3 | 3 |
| b2 | add_bus bus1 | bus1_0…bus1_7 | 8 |
| b3 | add_bus bus2 | bus2_0…bus2_7 | 8 |

**Use when:** the buses are logically separate (e.g. different data channels)
even though they share endpoints.

**`.buda` syntax (proposed):**
```
run_bundler strict  group_by_source
```

Implementation note: `add_bus` embeds the bus prefix in every net name
(`bus1_0`, `bus1_1`, …).  The bundler can recover the prefix by stripping the
trailing `_<index>` and use it as a secondary grouping key alongside the
signature.  Plain `add_net` names have no such suffix, so they all fall into
one scalar bundle per signature.

---

## Strategy 3.a — Max-bit-count, aggregate split

A single `max_bits` cap is applied to the total net count across all sources
sharing a signature.  If the total exceeds `max_bits`, the nets are distributed
round-robin (or sequentially) into `ceil(total / max_bits)` bundles.

Example with `max_bits = 8` and 19 nets:

| Bundle | Nets | Width |
|---|---|---|
| b1 | net1, net2, net3, bus1_0…bus1_4 | 8 |
| b2 | bus1_5…bus1_7, bus2_0…bus2_4 | 8 |
| b3 | bus2_5…bus2_7 | 3 |

**Use when:** a congestion limit or layer pitch sets a hard cap on bus width
regardless of where the nets came from.

**`.buda` syntax (proposed):**
```
run_bundler strict  max_bits 64
```

---

## Strategy 3.b — Max-bit-count, per-source split

`max_bits` is applied independently to each source declaration.  The scalar
bundle (merged `add_net` calls) is treated as one source.  A wide `add_bus` is
split into multiple same-signature bundles of at most `max_bits` each.

Example with `max_bits = 2`:

| Bundle | Source | Nets | Width |
|---|---|---|---|
| b1 | add_net part 1 | net1, net2 | 2 |
| b2 | add_net part 2 | net3 | 1 |
| b3 | bus1 part 1 | bus1_0…bus1_1 | 2 |
| b4 | bus1 part 2 | bus1_2…bus1_3 | 2 |
| b5 | bus1 part 3 | bus1_4…bus1_5 | 2 |
| b6 | bus1 part 4 | bus1_6…bus1_7 | 2 |
| b7 | bus2 part 1 | bus2_0…bus2_1 | 2 |
| b8 | bus2 part 2 | bus2_2…bus2_3 | 2 |
| b9 | bus2 part 3 | bus2_4…bus2_5 | 2 |
| b10 | bus2 part 4 | bus2_6…bus2_7 | 2 |

The cap applies to every source — scalar (`add_net`) and named (`add_bus`)
alike.  Each source is split independently at `max_bits`.

**Use when:** each declared bus has a physical identity (shielded, clocked
together) and must not be scattered across bundles, but a single wide bus
declaration should be chunked to match a routing resource limit.

**`.buda` syntax (proposed):**
```
run_bundler strict  max_bits 64  per_source
```

---

## Summary

| Mode | `.buda` keyword(s) | Splits by |
|---|---|---|
| 1 — All-in-one | *(default)* | — |
| 2 — Group by source | `group_by_source` | add_net vs add_bus prefix |
| 3.a — Aggregate cap | `max_bits N` | total net count |
| 3.b — Per-source cap | `max_bits N per_source` | per add_bus declaration |

Modes can compose: `group_by_source max_bits 64` applies the cap after source
grouping, so each declared bus is split independently at 64 bits.
