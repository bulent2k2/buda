# bigHalf `bus_005` dangling-segment scan (2026-07)

## What this is

A focused repro + audit of the DANGLING candidate geometry on `bus_005`
(bundle 67 in `flow/big_data_test/bigHalf.buda` — the 48-bit `blk_34 →
[blk_19, io_pad_br]` corner-IO bus that has been a healer hot spot, cf.
issue #399 and `relay_jog_phantom_span_2026-07.md`).

- **Repro flow:** `flow/big_data_test/bigHalf_bus005_dangling.buda` — the
  bigHalf setup, but `generate_topologies_for_bundle bus_005` (src/dst
  auto-derived) instead of the whole-design `generate_topologies`, so the
  candidate pool is just this one bus. Ends with `dump_topologies bus_005
  --conn` + `check_design topo all`.
- **Scanner:** `tools/scan_dangling.py <flow.buda> <hint>` — runs the flow,
  then walks every candidate of the matching bundle, builds its
  `ConnTopology`, and categorizes each dangling segment. Reusable on any
  flow/bundle.

## Two kinds of "dangling"

Mirrors `set_drop_dangling`'s predicates (`src/buda_session/edit.py`
`_topo_dangling_reason` / `_topo_truly_dangling_reason`):

- **A — truly-dangling stub:** a `ConnSeg` with a *single* connection that
  is *not* a block tap — a wire whose other end connects to nothing.
  DetailedNUTS silently shrinks it when it re-derives bits, which is why the
  NUTS-level viz never flagged it.
- **B — unbounded slide window:** the `±2³⁰` no-clamp sentinel on
  `perp_lo/hi` — an OOB-detour trunk whose slide window was never bounded.
  `set_drop_dangling clamp` bounds these to the design extent.

## Finding (36 candidates on this fixture, 2026-07-23)

| Category | Candidates | Types |
|---|---|---|
| A — truly-dangling stub | 3 | `TRUNK_H+MST@y2770`, `@y3095`, `@y2410` — **all `TRUNK_*+MST`** |
| B — unbounded window | 5 | `TRUNK_H_OOB+MST@y-232`, `TRUNK_V_OOB+MST@x-569`, `TRUNK_H+MST@y2410` (MST) **and** `TRUNK_V_OOB@x-569`, `TRUNK_H_OOB@y-232` (pure OOB) |

**The hypothesis holds for genuine dangling stubs:** every Category-A stub is
a `TRUNK_*+MST` hybrid — the MST-relay artifact (`complete_relay_junctions`
leaves a relay stub whose far end is only a junction, so the extend-only
span guarantee bakes a phantom tail). None are pure trunks.

**Unbounded windows (Category B) are a separate class.** They ride the
OOB-detour geometry and appear on *pure* OOB trunks (`TRUNK_V_OOB@x-569`,
`TRUNK_H_OOB@y-232`) as well as MST hybrids. Conflating the two would
misattribute the OOB-window issue to MST.

Note candidate 35 (`TRUNK_H+MST@y2410`, 11 segments) exhibits **both**: a
truly-dangling seg0 and six unbounded-window segs — the pathological
high-segment MST hybrid.

## Why nothing caught it before

`check_design topo all` on this pool reports **"no violations found."** A
dangling stub is not a `DISCONNECTED`/`FEEDTHRU_RELAY`/`BUSTERM_OPEN`
violation — the block is still covered and the graph is still one island —
and abstract NUTS renders the stub within the design extent, so neither the
audit nor the NUTS viz flags it. Only DetailedNUTS's re-derivation shrinks
the truly-dangling stub (masking it), and only the opt-in `set_drop_dangling`
(drop / clamp / clamp_drop) acts on either category. `scan_dangling.py` is
the standalone lens.

## Reproduce

```bash
python3 tools/scan_dangling.py \
    flow/big_data_test/bigHalf_bus005_dangling.buda bus_005
# or run the flow directly:
bin/buda --no-viz flow/big_data_test/bigHalf_bus005_dangling.buda
```
