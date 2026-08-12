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

**The description that used to sit here was of a DIFFERENT DEF and has been
removed.**  It said this design was ASAP7, 226.44 x 225.97 um, and built
from 133 x `sram_asap7_16x256_1rw`.  The checked-in `ariane.def` says
otherwise, and the file is the authority:

| | the old text claimed | `ariane.def` actually says |
|---|---|---|
| technology | ASAP7 (7nm predictive) | **FreePDK45** (`ROW … FreePDK45_38x28_10R_NP_162NW_34O`, 962 rows) |
| macro | `sram_asap7_16x256_1rw` | **`fakeram45_256x16`**, x133 |
| die | 226.44 x 225.97 um | **1357.4 x 1356.9 um** (`DIEAREA … 2714720 2713760` at 2000 DBU/um) |
| units | 1000 DBU/um | **2000 DBU/um** |

That block also told the reader to fetch the ASAP7 technology and standard
cell LEFs, which would not describe this DEF either — so it was not merely
stale, it was directions to the wrong files.

`ariane.lef` IS the ASAP7 macro it names, which is how the mismatch arose:
the prose and the LEF describe the ASAP7 ariane, and the DEF checked in is
the NanGate45/FreePDK45 one.  See the header of this file.

To make the pair importable you would need the matching NanGate45
`fakeram45_256x16` LEF.  Nothing here needs it: `def_viz_o3.py` with no LEF
argument infers sizes from the placement (above), and for a working
LEF/DEF/Verilog import vehicle use [`flow/def/`](../../flow/def/) or
[`flow/rv/`](../../flow/rv/).

Source: the files came from the UCSD/TILOS MacroPlacement benchmark suite
(https://github.com/TILOS-AI-Institute/MacroPlacement), which distributes
the ariane design in more than one technology — which is the likeliest way
the two halves came to be from different ones.
