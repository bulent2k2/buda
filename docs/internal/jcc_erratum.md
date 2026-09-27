# The Intel JCC erratum, and why the build pads jumps

**Status:** the mitigation is ON by default for x86-64 GCC and Clang builds
off macOS (`BUDA_JCC_MITIGATION`, 2026-09-27, PR #968).  This page is the
measurement record behind it, what it costs included.

## The erratum

On Intel's Skylake and the cores Intel lists as derived from it (Kaby Lake,
Coffee Lake, Whiskey Lake, Amber Lake, Comet Lake and Cascade Lake), the
microcode fix for the "jump conditional code" erratum keeps a jump out of the
decoded-instruction cache when it crosses a 32-byte boundary or ends on one.
Such a jump is fed through the legacy decoders instead, which in a tight loop
costs a large share of the loop's speed.  Whether a given jump sits on a
boundary is decided by where the linker places the code, not by the code, so
the speed of an unchanged hot loop can follow edits to unrelated files.

## What happened here

All measurements on a 4-vCPU Cascade Lake VM (family 6, model 85, stepping 7),
GCC 13.3, binutils 2.42, `-march=x86-64-v2`, with the timed run the only work
on the machine.

- **An unrelated change moved the planner by a fifth.**  PR #966 added code to
  `detailed_nuts.cpp` and `trial_sweep.cpp` and bindings to `bind_nuts.cpp`,
  none of it planner code.  The planner stage of `flow/chip/chip_bottomup.buda`
  (everything through `run_planner hier`, before any DetailedNUTS solve) went
  from 30.7 s to 37.1 s, +21 %, reproduced on a fresh worktree build and an
  incremental one, three runs each.  Built without LTO, the same two commits
  moved the other way, 38.6 s to 32.0 s (−17 %), on planner object code that
  is instruction-for-instruction identical.  The corpus read +10.7 % for a
  change whose own cost, measured with both sides built with the flag, is
  +1.2 % ([PR #966](https://github.com/bulent2k2/buda/pull/966#issuecomment-5853404233)).
- **Where the planner spends its time.**  In the two timed LTO builds
  (relinked without the post-build strip, which reproduces each module's
  `.text` byte for byte) gdb PC samples of the planner stage put 39-48 % of
  the busy samples in one loop of `RoutingGrid::count_signal_tracks_in` and
  another 16 % in `RoutingGrid::signal_tracks_in` (`routing_grid.cpp`).
- **What moved, and what did not.**  Functions are 16-byte aligned at `-O3`,
  so a function sits at one of exactly two phases against the 32-byte lines,
  and its jumps' exposure is fixed by that phase.  `count_signal_tracks_in` is
  at the SAME phase in both builds, and its hot loop's back-edge (`jne` at
  +0x1df..+0x1e1, taken every iteration) crosses a 32-byte boundary in both.
  Nor would the other phase clear it: shifted by 16, that `jne` clears the
  line but the loop's `jae` (+0x1db..+0x1e1 then) crosses it, so without the
  flag this loop has a jump on a boundary in every layout the linker can
  give it.  `signal_tracks_in` flipped phase (16 → 0), which
  changed which of its jumps sit on a boundary, and the rest of the planner's
  code moved as well.  So the hottest loop does not explain the difference
  between the two builds; which placement does is not established here, since
  that needs hardware counters and this VM exposes none.  What IS established
  is that the planner's speed follows code placement on this core, and that
  the flag removes most of that dependence (below).
- **With the flag.**  No conditional or direct jump in any of those functions
  is left on a boundary (that back-edge moves to +0x1f7 and to +0x1e7 of a
  function at phase 16), and the same two builds measure 31.8 s and 32.8 s in
  the planner stage, 3 % apart where they were 21 % apart.

(An earlier version of this page, and PR #966's comment, placed
`count_signal_tracks_in` "from a 64-byte boundary to offset 48".  That
address came from the no-LTO profiling build (`BUDA_PROFILE`), not from the
LTO builds that were timed, and it does not hold for them.)

## What the build does

`BUDA_JCC_MITIGATION` in `CMakeLists.txt`, on by default:

- **What gets padded.**  The assembler moves conditional jumps (a macro-fused
  compare-and-jump pair kept whole) and direct unconditional jumps off
  32-byte boundaries, with instruction prefixes where it can and NOPs where it
  cannot.  Indirect jumps, calls and returns are penalized by the erratum too,
  but the flag leaves them where they are; that is the default set of both
  GNU as (`-malign-branch=jcc+fused+jmp`) and LLVM.
- **Which flag.**  GCC passes `-Wa,-mbranches-within-32B-boundaries` to the
  GNU assembler, which has it from binutils 2.34 on.  Clang rejects that
  spelling and takes its own driver flag, `-mbranches-within-32B-boundaries`.
  CMake probes each language's compiler separately, because `sqlite3.c` is C
  and nothing makes the C compiler the C++ one's kind: built with clang++ and
  gcc, or g++ and clang, a single probe handed one compiler the other's
  spelling and the build stopped on `sqlite3.c` (reproduced on both
  pairings; both build now).  It probes again on every configure, and the
  configure line says what it applied, or that it is off and why.  A pure
  Clang 18 build was checked end to end: ThinLTO, the flag on both
  languages, routes identical to the GCC build's on a sample flow.
- **Compile and link.**  The two extension modules are linked with LTO, which
  generates their machine code at the link, so they get the flag there too.
  Clang's driver hands it to LTO code generation only from the link line
  (`-plugin-opt=-x86-branches-within-32B-boundaries`); `libbuda_core` is not
  LTO-linked and needs it only at compile time.
- **Where it is not applied.**  MSVC has its own switch
  (`/QIntel-jcc-erratum`), untested here.  macOS is skipped: clang's driver
  forwards nothing of the flag to an LTO link on Darwin (checked with clang
  18's `-###` against `x86_64-apple-macos13`), so an Intel Mac build would
  report the flag and ship unpadded modules, and nothing here can run one.
  Any target other than x86-64 is skipped.
- **No instruction changes meaning.**  The padding is prefixes and NOPs.  The
  full suite passes with every strict placement golden exact, and the corpus
  routes are identical with and without the flag on every flow in every sweep
  below (QoR triple, abstract and detailed WL).  The `buda` module grows by
  about 2.2 %.
- `-DBUDA_JCC_MITIGATION=OFF` opts out.

## Checking a build

`tools/jcc_audit.py <module>` counts the jumps that cross or end on a 32-byte
boundary, from `objdump -d`, in two groups: the ones the flag pads, and the
indirect jumps it cannot move.  On `main` at the time of writing:

| artifact | padded kinds on a boundary, no flag | with the flag |
|---|---|---|
| `buda` | 9,713 of 79,942 (12.15 %) | 1 |
| `buda_db` | 2,525 of 21,685 (11.64 %) | 1 |
| `libbuda_core` | 9,036 of 69,033 (13.09 %) | 0 |

The one left in each module is `register_tm_clones`, in the C runtime's
`crtbeginS.o`, which the build links in already assembled.  A Clang build
leaves more, 55 of 83,730 in `buda` (23 and 37 in the other two): LLVM does
not pad a tail call, a `jmp` to another function's entry, and all but one of
the 55 are `pop; jmp <f>` on cleanup paths (`operator delete`,
`BDB::_exec`).  Indirect jumps on
a boundary go from 10 to 19 of 140 in `buda`: they are not padded, so their
positions are whatever the padding around them makes them.

`test_jcc_mitigation.py` runs the audit on all three artifacts whenever CMake
applied a flag (`mid` tier, ~3 s), and fails when more than 8 padded-kind
jumps are left in any of them under GCC's spelling -- absolute, so that one
translation unit built without the flag still fails -- or more than 0.2 %
under Clang's, which keeps its tail calls.  CI sets `BUDA_JCC_STRICT=1`, under
which a build with no flag applied fails the test instead of skipping it:
the flag changes nothing but the speed, so no other check would notice it
going missing.

## What it costs, and what it buys

**On an affected core.**  One serial chain of eight corpus sweeps (`-j 1`),
four builds, each swept twice, in the order M P U K K U P M so drift hits
every arm alike: M is `main` as built, P the same commit with the flag, U the
commit before #966 (whose layout happened to suit the planner) as built, K
that commit with the flag.  Means of the two runs; the noise is the larger
run-to-run difference of the two arms compared.  `ariane133` is left out: its
runs split into 50 s and 68 s by the time of day they ran, in every arm alike.

| flows | the flag on `main` (M → P) | the flag before #966 (U → K) | #966 without the flag (U → M) | #966 with it (K → P) |
|---|---|---|---|---|
| 56 flows | 822.4 → 766.1 s, **−6.8 %** (±1.4 %) | 742.1 → 756.3 s, **+1.9 %** (±0.3 %) | +10.8 % | +1.3 % |
| 7 chip flows | −9.4 % (±1.2 %) | +2.4 % (±0.4 %) | +9.3 % | −3.4 % |
| the other 49 | −0.3 % (±1.7 %) | +0.6 % (±0.2 %) | +15.1 % | +14.2 % |

So on this core the flag's effect lives in the planner-heavy chip flows, and
its sign depends on where the linker happened to put the code: it bought
6.8 % on `main`'s layout and cost 1.9 % on the one before, with everything
else within a fraction of a percent.  What it does reliably is remove most of
the dependence on placement: across #966 the corpus moved +10.8 % without it
and +1.3 % with it, the latter being #966's own cost (its healers doing real
work in the other 49 flows; `chip_stack_bottomup` got faster, −21 %, because
its negotiate stops earlier).  In the planner stage alone the flag costs 3.6 %
on the good layout (30.7 → 31.8 s) and buys about 10 % on the bad one
(36.7–37.3 → 32.6–33.7 s on current `main`, three runs each, alternated) --
although the hottest loop's back-edge sits on a boundary in both layouts and
off it in both flagged builds, which is to say the padding has costs of its
own that outweighed relieving that loop on the good layout.

**On CPUs the erratum does not affect.**  GitHub's `ubuntu-24.04` runners.
The PR QoR job (`run-qor`) of #968 ran on an Intel Xeon Platinum 8370C (Ice
Lake-SP, 6/106/6) and swept the merge-base without the flag and the head with
it, once each and four flows at a time, so single flows are noisy (`ariane133`
+64 %, `soc_conv_div` −36 %): the corpus total moved −4.1 % and all seven
chip flows got faster, −10 % together.  A serial OFF ON ON OFF sweep of one
commit on two runners is in the next section.

**Serial A/B on the runners.**  A temporary job on #968 built one commit
twice, the flag off and on, and swept the corpus serially OFF ON ON OFF on
two runners:

| runner CPU | routes identical | all 57 | chip (7) | the rest, without `ariane133` |
|---|---|---|---|---|
| AMD EPYC 9V74 (Zen 4, 25/17/1) | 57 of 57 | 846.5 → 777.7 s, **−8.1 %** (±1.9 %) | **−12.2 %** (±0.6 %) | −2.4 % (`rnr`), +0.6 % (`big_data_test`), +0.2 % (`rv`), −1.3 % (`hbundles`) |
| Intel Xeon 6973P-C (Granite Rapids, 6/173/1) | 57 of 57 | 656.6 → 677.3 s, +3.2 % (±4.8 %) | +2.1 % (±3.3 %) | −1.4 % (`rnr`), −1.2 % (`big_data_test`), −0.5 % (`rv`), 0.0 % (`hbundles`) |

`ariane133` split its runs on both runners as it does here (off 41.1 s and
51.6 s, on 53.4 s and 53.1 s on the EPYC; off 37.5 s and 62.4 s on the
Xeon), which is most of the Xeon's +3.2 %: without it that total is +1.1 %.

So the effect is not only the erratum's.  On the EPYC, which it does not
affect, every chip flow got faster with the flag, by 8-21 %, with the two
runs of each arm within a fraction of a second of each other
(`chip_stack_bottomup` 126.3/126.3 s → 100.1/100.3 s).  Padding branches off
32-byte boundaries helps this code on that core's front end as well; on the
Granite Rapids runner the effect is within the noise.  These measure `main`'s
layout only: the layout before #966, where the flag cost 1.9 % here, was not
swept on the runners.

## Not done: the loop itself

`count_signal_tracks_in` compares `slot.type == "SIGNAL"`, a `std::string`
comparison, for every slot of every pattern period in the queried range, and
walks every keepout for each SIGNAL slot.  That is why it is hot.  Making it
cheaper, for instance by caching the slot type as an enum, would speed the
planner on every CPU, with or without this flag.  It is a separate change,
with its own measurement.
