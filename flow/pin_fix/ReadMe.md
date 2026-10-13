# fix_pin vehicles

Flows for the fixed-pin primitive ([design](../../docs/internal/fixed_pin_design.md)).
Each PR adds the flows that show its change; run any of them with
`bin/buda flow/pin_fix/<name>.buda` (viewer) or `--no-viz` (headless).

| flow | PR | shows |
|---|---|---|
| `baseline_macro.buda` | 1a | a macro with buses on three faces — the "before" picture |
| `baseline_teg.buda` | 1a | a two-rect `teg_mode over` block — the multi-rect "before" |
| `faces.buda` | 1b | four macros: free, west-only, north-only, east-only (the wrap-around pin, crossed over the body) |
| `window.buda` | 1b | a window along the face (`at lo..hi`, block-local) on a west and an east pin bank |
| `hier.buda` | 1b | fixes by instance path in a hierarchical flow (hbundles/01 with two windowed pins) |
| `pins.buda` | 1c | one macro, three buses, three faces — per-pin selectors (exact pin, prefix) and the fix-uniform bundle split |
| `layers.buda` | 2a | `layer`: a west pin forced onto a LOW horizontal layer, a north pin onto a LOW vertical one, a free bus left on the TOP layers |
| `order.buda` | 2b | `order index\|rindex`: bit 0 low vs high in two windows on one face, and a straight wire between opposite orders dropped for a bend |
| `persist_build.buda` / `persist_resume.buda` | 1d | fixes written to a file BDB, then a flow with NO `fix_pin` line that gets them back |

The viewer draws each admissible landing as a thick green line on its face;
`bin/buda --no-viz` runs headless and `tools/render_design.py <flow>` writes the
same picture as PNGs.
