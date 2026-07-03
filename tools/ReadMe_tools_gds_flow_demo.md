# From PR #145: https://github.com/bulent2k2/buda/pull/145

git fetch origin claude/bdb-test-data-management-v4m069
git checkout claude/bdb-test-data-management-v4m069
  # or main, once #145 merges
source bin/activate
bb
python3 tools/gds_demo.py
  # → interactive visualizer

What it does: generates a small SoC as real GDSII (a 2×2 core array
via AREF — each core containing an alu and regfile, so the hierarchy
has depth — plus an L2 slab and IO strip), imports it with import_gds,
and routes 4 core→L2 buses + a 16-bit L2→IO stream through the full
pipeline. In the window, click a bus to highlight it, then toggle
[Detailed] and [Vias/Conns] to see the 64 individual bit-wires and the
16 per-bit via staircases at the bends. The BDB checkpoint lands in
$TMPDIR/buda_gds_demo/chip.bdb — you can reopen it later with
load_pipeline to resume. Variants: --png out.png (headless render) and
--no-viz (summary only).

