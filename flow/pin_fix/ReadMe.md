# fix_pin vehicles

Flows for the fixed-pin primitive ([design](../../docs/internal/fixed_pin_design.md)).
Each PR adds the flows that show its change; run any of them with
`bin/buda flow/pin_fix/<name>.buda` (viewer) or `--no-viz` (headless).

| flow | PR | shows |
|---|---|---|
| `baseline_macro.buda` | 1a | a macro with buses on three faces — the "before" picture |
| `baseline_teg.buda` | 1a | a two-rect `teg_mode over` block — the multi-rect "before" |
