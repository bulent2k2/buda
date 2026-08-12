# demo/ariane

**The DEF and the LEF here are from DIFFERENT technologies, and that is
expected.**  `ariane.def` is the TILOS MacroPlacement **NanGate45**
ariane133 benchmark (133 x `fakeram45_256x16`); `ariane.lef` is an
**ASAP7** SRAM (`sram_asap7_16x256_1rw`), obtained separately — see
"got it later" below.  Neither file is wrong; the LEF was simply never
the LEF for this DEF.

Nothing depends on them agreeing: no flow imports the pair (the `.buda`
files here are hand-written floorplans derived from the benchmark), and
the one test that reads `ariane.def` goes through the raw `read_def` and
never opens the LEF.  `import_def_lef` on the pair REFUSES unless told
`allow_missing_footprints`, which is the correct answer when a design is
handed a technology that does not describe it — the old importer instead
made every macro on this 2.7 mm die a 0.5 um speck in silence.

The LEF is kept because it is a real 45-pin macro sample the LEF reader
was developed against.  For a working import vehicle use
[`flow/def/`](../../flow/def/) or [`flow/rv/`](../../flow/rv/), which
exist for that; making this pair importable would need the matching
NanGate45 `fakeram45_256x16` LEF and would be redundant coverage.

See `docs/internal/opens_interchange.md` item 9.

---

python3 tools/def_viz_o3.py demo/ariane/ariane.def demo/ariane/ariane.lef

Main
==
  ariane.def
  ariane.lef  (got it later)
  
Buda files work
==
python3 src/buda_cli.py demo/ariane/ariane.buda &
python3 src/buda_cli.py demo/ariane/ariane_core.buda &

A synth placement by Claude
==
BDB=chip_designs/ariane136/ariane_buda5.bdb; python3 tools/def_viz_o3.py $BDB

Works without a LEF (auto synthesize size based on placement and minimum gaps)
==
python3 tools/def_viz_o3.py demo/ariane/ariane.def &

LEF file
==
✦ The ariane_fp_placed_macros.def design from pdn.ucsd.edu (part of the UCSD/TILOS MacroPlacement
  benchmark suite) is a 64-bit RISC-V core (Ariane/CVA6) mapped to the ASAP7 (7nm Predictive
  PDK). 

  To use this DEF file, you need three corresponding LEF components:
   1. Macro LEF: sram_asap7_16x256_1rw.lef (the specific SRAM macro used for all 133 macro
      instances).
   2. Technology LEF: asap7_tech_1x_201209.lef (defines layers, vias, and rules).
   3. Standard Cell LEF: asap7sc7p5t_27_R_1x_201211.lef (defines the standard cell abstracts).

  I have saved the most design-specific component, the Macro LEF (ariane.lef), to the project
  root. This file defines the sram_asap7_16x256_1rw macro referenced in the DEF file's COMPONENTS
  section.

  Summary of Design Parameters (from DEF):
   * Design: ariane
   * Technology: ASAP7
   * Die Area: 226.44µm x 225.97µm
   * Macro Count: 133 (sram_asap7_16x256_1rw)
   * Units: 1000 Distance Units = 1 Micron (1x scaling)

  The files are sourced from the official TILOS-AI-Institute/MacroPlacement
  (https://github.com/TILOS-AI-Institute/MacroPlacement) repository, which hosts the public
  distribution of these UCSD benchmarks.
