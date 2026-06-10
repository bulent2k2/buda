The helpers in this directory work like this
==
- python3 debug/dump_pull.py flow/pull2.buda [type-prefix] — prints per-segment net_pull, slide ranges, and connections
(optionally for all candidates matching a type prefix like Z_HVH instead of just the selected topology).
- python3 debug/render_flow.py flow/pull2.buda out.png — runs a flow headless and saves the visualize window to a PNG.
  
  One caveat: both scripts skip/mock the interactive window, and dump_pull.py ignores the JSON sidecar pinning (it dumps
  candidates straight from the generator), so use the type-prefix argument to target the pinned topology.

