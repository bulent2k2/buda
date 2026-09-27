# The Intel JCC erratum, and why the build pads jumps

**Status:** the mitigation is ON by default for x86-64 GCC/Clang builds
(`BUDA_JCC_MITIGATION`, 2026-09-27).  This page is the measurement record
behind it.

## The erratum

On Intel's Skylake and the cores Intel lists as derived from it (Kaby Lake,
Coffee Lake, Whiskey Lake, Amber Lake, Comet Lake and Cascade Lake), the
microcode fix for the "jump conditional code" erratum keeps a jump out of the
decoded-instruction cache when it crosses a 32-byte boundary or ends on one.
Such a jump is fed through the legacy decoders instead, which in a tight loop
costs a large share of the loop's speed.  Whether a given jump sits on a
boundary is decided by where the linker places the code, not by the code, so
the speed of an unchanged hot loop follows edits to unrelated files.

## What it did here

Measured 2026-09-27 on a 4-vCPU Cascade Lake VM (family 6, model 85), GCC 13.3,
binutils 2.42, `-march=x86-64-v2`, with the timed run the only work on the
machine.

- **An unrelated change slowed the planner by a fifth.**  PR #966 added code to
  `detailed_nuts.cpp` and `trial_sweep.cpp` and bindings to `bind_nuts.cpp`,
  none of it planner code.  The planner stage of `flow/chip/chip_bottomup.buda`
  (everything through `run_planner hier`, before any DetailedNUTS solve) went
  from 30.7 s to 37.1 s, +21 %.  That was reproduced on a fresh worktree build
  and on an incremental one, three runs each.  The QoR corpus as a whole read
  +10.7 % and the chip flows +10–15 %; placement-controlled, the change itself
  cost +1.2 %
  ([PR #966](https://github.com/bulent2k2/buda/pull/966#issuecomment-5853404233)).
- **Without LTO the gap reverses.**  The planner's object code is
  instruction-for-instruction identical in the two builds without LTO, and
  there the older build takes 38.6 s and the newer one 32.0 s.
- **The hot loop is not planner code.**  gdb stack samples put 57–59 % of the
  stage's busy samples in `RoutingGrid::count_signal_tracks_in` and
  `RoutingGrid::signal_tracks_in` (`routing_grid.cpp`), loops over a track
  pattern's slots.  `routing_grid.cpp` is linked after `bind_nuts.cpp`, so
  growing the latter moved the loop: `count_signal_tracks_in` went from a
  64-byte boundary to offset 48 of one.
- **The mitigation removes the swing.**  With the assembler padding jumps off
  32-byte boundaries, the same two builds measure 31.8 s and 32.8 s.

So on this CPU class the planner stage could swing by about ±20 %, in either
direction, with any edit that moves `routing_grid.o`, and a runtime A/B across
such an edit measured the layout rather than the change.

## What the build does

`BUDA_JCC_MITIGATION` in `CMakeLists.txt`, on by default:

- **GCC** passes `-Wa,-mbranches-within-32B-boundaries` to the GNU assembler
  (binutils 2.34 or newer).  **Clang** rejects that spelling and takes its own
  driver flag, `-mbranches-within-32B-boundaries`.  CMake checks which one the
  compiler accepts.  With neither, it builds without and says so.
- **Compile and link.**  Under LTO the machine code is generated at the link,
  so the flag goes there too.
- **x86-64 targets only**, keyed on CMake's target processor.  An arm64 build
  skips it, and so does a universal macOS build, where the flag would reach
  the arm64 compile too.  The x86_64 macOS wheel skips it as well: it is
  cross-built on an arm64 runner, and scikit-build-core sets only
  `CMAKE_OSX_ARCHITECTURES`, so CMake reports the host's processor.  A native
  build on an Intel Mac takes the Clang branch; nothing here has run one.
  **MSVC** has its own switch (`/QIntel-jcc-erratum`); it is not applied,
  because nothing here can test it.
- **No instruction changes meaning.**  The assembler inserts instruction
  prefixes or NOPs, so every result is unchanged: the full suite passes with
  every strict placement golden exact.  The `buda` module grows by about
  2.2 %.
- `-DBUDA_JCC_MITIGATION=OFF` opts out.  The configure log names the flag in
  use (`BUDA JCC-erratum mitigation: ...`).

## Checking a build

`tools/jcc_audit.py <module>` counts the jumps that cross or end on a 32-byte
boundary, from `objdump -d`.  An unmitigated `buda` module measures about 12 %
of its jumps there (9,723 of 80,082 on `main` at the time of writing), and a
mitigated one 0.02 %.  `test_jcc_mitigation.py` runs the audit on the imported
module whenever CMake applied a flag.  The flag has to reach the code
generator, and a flag lost on the way changes nothing but the speed, so the
built module is the only honest place to check.

## What it costs

On the machine above, the planner stage of `chip_bottomup` on current `main`
measures 36.7–37.3 s without the mitigation and 32.6–33.7 s with it (three
runs each, alternated).

## Not done: the loop itself

`count_signal_tracks_in` compares `slot.type == "SIGNAL"`, a `std::string`
comparison, for every slot of every pattern period in the queried range, and
walks every keepout for each SIGNAL slot.  That is why it is hot, and why it is
branchy enough for the erratum to matter.  Making it cheaper, for instance by
caching the slot type as an enum, would speed the planner on every CPU.  It
is a separate change, with its own measurement.
