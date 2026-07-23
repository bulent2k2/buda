# `tools/data/` — demo input fixtures

Small input files used by the DEF/LEF visualizer and IPC demos in
[`tools/ReadMe_tools.md`](../ReadMe_tools.md).

## What ships here

| File | Origin | License |
|---|---|---|
| `four_blocks.def` / `four_blocks.lef` | **BUDA-original** toy design (one `BLOCK_MACRO`, four placed instances) driving `flow/four_blocks.buda` | Apache-2.0 (this repo) |

Only tiny, BUDA-original fixtures are committed here.

## Third-party benchmarks (download separately)

Realistic designs use standard third-party libraries/benchmarks that carry their
**own** licenses, so they are intentionally **not** committed to this repo.
Download them and point the tools at your local copies:

- **Nangate45 open library (LEF)** and the **`gcd`** example design (DEF) — the
  standard OpenROAD demo pair:
  - OpenROAD-flow-scripts (Nangate45 platform LEF):
    <https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts>
  - OpenROAD (gcd test design DEF):
    <https://github.com/The-OpenROAD-Project/OpenROAD>
- **ISPD 2019** detailed-routing contest benchmarks:
  <https://www.ispd.cc/contests/19/>

Example once downloaded:

```bash
bin/viz /path/to/gcd.def /path/to/Nangate45.lef
```
