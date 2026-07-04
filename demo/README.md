# BUDA demos

User- and designer-facing **demo vehicles** — runnable `.buda` scripts that show
off the routing pipeline on illustrative and realistic designs. Run any of them
with the CLI (they end in `visualize`, so drop `--no-viz` for a GUI):

```bash
bin/buda demo/comprehensive_demo.buda        # small end-to-end showcase
bin/buda --no-viz demo/mempool_tile.buda     # batch (no window); inspect the flow log
```

The **R&D / regression vehicles** used by the test suite live under
[`../flow/`](../flow/) instead. This directory is only the curated demos.

## What's here

| Demo | Shows |
|---|---|
| `comprehensive_demo.buda`, `comprehensive_demo_viz_topo.buda` | End-to-end flow (bundler → topology → planner → NUTS → detailed NUTS) + the topology explorer. |
| `quickstart.buda`, `user_guide.buda` | Minimal getting-started flows that pair with the [User Guide](../docs/USER_GUIDE.md). |
| `congestion_demo.buda`, `keepout_demo.buda`, `large_fanout.buda` | Congestion planning, keep-out zones, and large-fan-out datapath trunks. |
| `large_scale_demo.buda`, `large_scale_demo_gen_topo.buda`, `realistic_large_chip.buda` | Larger synthetic chips (with `generate_large_demo.py` / `gen_realistic_large_chip.py` generators). |
| `talk1.buda`, `talk2.buda` | Small presentation/walkthrough scripts. |
| `ariane*.buda` + `ariane/` | The Ariane core (with `gen_ariane136*.py` generators and a DEF/LEF import in `ariane/`). |
| `mempool_{cluster,group,tile}.buda`, `nvdla_cbuf.buda`, `bp_tile.buda` | Realistic block/cluster designs. |
| `ispd19_test{1,2,3,5,7}.buda` | ISPD-2019 contest benchmark designs (large). |

## Shared fixtures

The track/tech-header fixtures (`tracks.buda`, `tracks2top.buda`, …) are shared
between these demos and the `flow/` test vehicles, so they live once in
[`../flow/tracks/`](../flow/tracks/). Demos that need them `source
../flow/tracks/<name>.buda`; the Ariane demos carry their own
`tracks_ariane136.buda` here. `large_scale_demo.buda` also sources
`../flow/large_scale_demo_buses.buda` (kept in `flow/` because a `flow/` test
vehicle uses it too).
