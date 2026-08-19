# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ============================================================
# tools/buda_interact.tcl — interactive iteration on ANY .buda flow.
#
#   bin/btcl -i <flow>.buda ?<ckpt.bdb>? ?<stage>?
#   bin/btcl -b <flow>.buda ?<ckpt.bdb>?        build; checkpoint auto-named
#   bin/btcl -r ?-s <stage>? <flow>.buda ?<ckpt>?  resume; checkpoint
#                                               auto-discovered, stage
#                                               defaulting to the deepest
#   (or: tclsh tools/buda_interact.tcl <same arguments>)
#
# design.tcl and hdesign.tcl carry their own design and their own route
# recipe; this driver carries NEITHER — it runs an arbitrary flow verbatim
# (`buda::source`, the engine's own command, so the flow means exactly what
# `bin/buda` makes it mean) and then drops into the same pin/edit prompt
# (tools/buda_prompt.tcl — the shared loop the vehicles use).
#
# The flow itself supplies the `replan` recipe, through the RECORDER: with
# BUDA_RECORD armed, every command the flow executes is written down at
# `do_command` — loops unrolled, `source` trees flattened — so after the
# run this driver knows, without parsing a line of the flow's text:
#
#   * whether the flow is HIER or FLAT (a recorded `run_planner hier`),
#   * the flow's own ROUTING TAIL — everything from the first `run_planner`
#     on, minus what a replay must not repeat (viz/exports/dumps, pins and
#     edits, generation and bundling — an iteration re-plans, it does not
#     rebuild) — which becomes the prompt's `replan`, healers, checks and
#     reports included;
#   * whether it routed at all (no `run_nuts` recorded = nothing to verdict),
#   * and where its checkpoint lives (the last file-backed `open_bdb`), so
#     the prompt can say whether a pin will outlive the session.
#
# A flow with no file-backed BDB still iterates IN-session (pin/replan),
# but its pins die with the session: `save_bdb` snapshots the OPEN BDB, so
# only a flow that opened one has a snapshot to write (opening one after
# the fact would not backfill the rows the pipeline persists as it runs).
# The optional SECOND argument closes exactly that gap the honest way
# round: `btcl -i <flow>.buda ckpt.bdb` opens the BDB BEFORE the flow, so
# every stage persists as it runs and a prompt pin lands durably — the
# next session with the same pair restores it onto the rebuilt candidate
# pool by content uid (_apply_bdb_pins) and the flow's own `run_planner`
# honors it.  A flow that ends in `exit` has ended the engine session —
# reported, nothing to iterate.
#
# ── RESUME: the optional THIRD argument ───────────────────────────────────
#
#   btcl -i <flow>.buda ckpt.bdb plan     # skip bundling+generation
#   btcl -i <flow>.buda ckpt.bdb topo     # skip bundling only
#   btcl -i <flow>.buda ckpt.bdb nuts     # keep the plan too
#   btcl -i <flow>.buda ckpt.bdb dnuts    # keep abstract NUTS too
#   btcl -i <flow>.buda ckpt.bdb build    # explicit full rerun (the default)
#
# A rebuild redoes stages whose result the checkpoint already holds; the
# stage argument is the design.tcl/hdesign.tcl RESUME recipe generalized:
# replay only the SETUP portion of the build session's recorded trace
# (written to `<ckpt.bdb>.trace` by every build session with a live
# file-backed checkpoint), then `load_pipeline` — which restores bundles,
# every candidate, the selection, the PINS and the routing, as deep as was
# persisted, through the machinery built for exactly this — then re-enter
# the flow's own recorded commands at the chosen stage.  An iteration
# re-plans rather than rebuilds, so `plan` is the stage to reach for.
#
# What "setup" means differs by flow kind, and the vehicles are the spec:
#   * FLAT (design.tcl): blocks and buses are SESSION state the checkpoint's
#     geometry is interpreted against — re-declared every session — so the
#     whole pre-bundler prefix replays (minus outputs/session control).
#   * HIER (hdesign.tcl): cells, instances, buses and busterms are IN the
#     checkpoint (re-running add_inst into a populated BDB is a
#     duplicate-instance error; a re-derive would renumber the busterm ids
#     the restored bundles reference) — so only the session-state verbs
#     replay (stack, patterns, floorplan projections, policies: the
#     whitelist below) and the construction verbs are skipped, counted, and
#     said.  Hier `topo`/`plan` restore the pre-expansion view (the cuts
#     that still run `run_planner hier`, whose expansion it feeds); hier
#     `nuts`/`dnuts` restore the POST-expansion view (`load_pipeline
#     expanded`) as an INSPECTION session — the quick look at a long
#     healer flow's result — where pins/edits/replan are guarded (their
#     persist would write the expanded view over the checkpoint's template
#     rows) and the verdict, dumps and explorer are the point.
#
# A cut below the planner HOLDS the planner-dependent commands (healers,
# refine_selection, run_planner post_nuts): load_pipeline restores the
# plan, not the planner OBJECT, so they would refuse — and for a healed
# checkpoint the restored plan already CARRIES the healing (a healer
# commit is a full-pipeline state), so re-solving NUTS/DNUTS from it
# reproduces the healed endpoint without paying for the healers again.
# ============================================================

set here [file dirname [file normalize [info script]]]
source [file join $here buda.tcl]
source [file join $here buda_prompt.tcl]

# ── arguments ─────────────────────────────────────────────────────────────
# Two spellings of the same machinery (btcl forwards its -b/-r/-s here):
#   <flow> ?<ckpt>? ?<stage>?           the original, fully explicit
#   --build  <flow> ?<ckpt>?            build; ckpt auto-named when omitted
#   --resume [--stage <s>] <flow> ?<ckpt>?   resume; ckpt auto-discovered and
#                                       stage defaulting to the DEEPEST the
#                                       trace records when omitted
# The flags make the MODE explicit instead of inferred from argument count,
# and take the two things a user had to invent off their plate: the
# checkpoint filename (auto: <flow_dir>/<stem>.ckpt.bdb) and the stage name.
set usage "usage: btcl -i <flow>.buda ?<ckpt.bdb>? ?build|topo|plan|nuts|dnuts?\n\
       btcl -b <flow>.buda ?<ckpt.bdb>?           (build, auto checkpoint)\n\
       btcl -r ?-s <stage>? <flow>.buda ?<ckpt>?  (resume, deepest stage)"
set mode ""
set stage_flag ""
set pos {}
for {set i 0} {$i < [llength $argv]} {incr i} {
    set a [lindex $argv $i]
    switch -- $a {
        -b - --build {
            if {$mode eq "resume"} { puts stderr "btcl -i: -b and -r are\
                  mutually exclusive"; exit 2 }
            set mode build
        }
        -r - --resume {
            if {$mode eq "build"} { puts stderr "btcl -i: -b and -r are\
                  mutually exclusive"; exit 2 }
            set mode resume
        }
        -s - --stage {
            incr i
            if {$i >= [llength $argv]} { puts stderr "btcl -i: $a requires a\
                  stage (topo|plan|nuts|dnuts)"; exit 2 }
            set stage_flag [string tolower [lindex $argv $i]]
            # A stage is a RESUME concept, so -s alone implies -r.
            if {$mode eq ""} { set mode resume }
        }
        default { lappend pos $a }
    }
}
if {[llength $pos] < 1 || [llength $pos] > 3} {
    puts stderr $usage
    exit 2
}
set flow [file normalize [lindex $pos 0]]
if {![file exists $flow]} {
    puts stderr "buda_interact: no such flow: $flow"
    exit 2
}
set tag [file tail $flow]
set auto_ckpt [file rootname $flow].ckpt.bdb
set armed ""
if {[llength $pos] >= 2} { set armed [file normalize [lindex $pos 1]] }
set stage_pos ""
if {[llength $pos] == 3} { set stage_pos [string tolower [lindex $pos 2]] }

if {$mode eq "build"} {
    # -b takes no stage: a build IS the whole run.  Refusing both spellings
    # keeps `-b -s dnuts` from silently meaning something the user did not
    # ask for.
    if {$stage_flag ne "" || $stage_pos ne ""} {
        puts stderr "$tag: -b takes no stage (a build runs the whole flow;\
              resume at a stage with -r -s <stage>)"
        exit 2
    }
    set stage build
} elseif {$mode eq "resume"} {
    if {$stage_flag ne "" && $stage_pos ne "" && $stage_flag ne $stage_pos} {
        puts stderr "$tag: -s $stage_flag and the positional stage $stage_pos\
              disagree -- give one"
        exit 2
    }
    set stage [expr {$stage_flag ne "" ? $stage_flag : $stage_pos}]
    if {$stage eq "build"} {
        puts stderr "$tag: `build` is not a resume stage -- use -b"
        exit 2
    }
    if {$stage ni {"" topo plan nuts dnuts}} {
        puts stderr "$tag: unknown stage `$stage` -- topo|plan|nuts|dnuts"
        exit 2
    }
    # stage "" = resolve to the DEEPEST stage the trace records, below.
} else {
    # Legacy positional spelling, byte-compatible.
    set stage [expr {$stage_pos ne "" ? $stage_pos : "build"}]
    if {$stage eq "bundle"} {
        # Re-bundling regenerates everything downstream of it, so a
        # bundle-level "resume" IS the full rerun — named rather than
        # silently aliased.
        puts "$tag: stage `bundle` re-bundles, which regenerates everything --\
              that is what `build` does; running build"
        set stage build
    }
    if {$stage ni {build topo plan nuts dnuts}} {
        puts stderr "$tag: unknown stage `$stage` -- build|topo|plan|nuts|dnuts"
        exit 2
    }
    if {$stage ne "build" && $armed eq ""} {
        puts stderr "$tag: stage `$stage` resumes from a checkpoint -- give the\
                     <ckpt.bdb> argument (and run a build session first)"
        exit 2
    }
}
# Set by the resume path for a hier below-plan (post-expansion) session.
set inspect 0
# Set by -b's read-only-input redirect: the checkpoint the materialization
# lands in, and the .sql it derives from (both "" outside that mode).
set redirect ""
set redirect_input ""

# ── shared: analyze a recorded command list ───────────────────────────────
# Sets the globals the banners, the prompt and the verdict read.  The engine
# lowercases the COMMAND NAME (do_command's parts[0].lower()) but the
# recorder writes the line verbatim, so a flow spelling `RUN_PLANNER` ran
# fine and must be recognized here too.  Only the verb: arguments are
# case-sensitive to the engine (`run_planner HIER` would have been refused),
# and an `open_bdb` PATH must never be case-folded.
proc _verb {ln} { string tolower [lindex [split $ln] 0] }

# THE `.buda` argument rule, in Tcl: the twin of
# `buda_script.split_quoted_args` (src/buda_script.py), which is what the
# ENGINE reads a recorded line with.  This driver is a `.buda` reader too —
# the fifth, after buda2tcl/buda2bdb/qor_corpus/scan_fanin — and it was the
# one the shared-helper fix could not reach, being in a different language.
#
# Splitting a recorded `open_bdb "my designs/ck.bdb"` on whitespace took the
# path as `"my`, which `_resolve_bdb` then joined onto the flow's directory:
# the checkpoint banner named a file nobody has, the resume recipe it prints
# named the same one, and no `.trace` was written — so every later
# `btcl -i <flow> <ckpt> <stage>` refused with "run a build session first",
# the session that had just run.
#
# A quote is honoured only where a token BEGINS, so this is a plain split for
# every line that does not use one, and an unterminated quote is an ordinary
# character.  `#` at a token start ends the line (the recorder strips comments
# already; matching the engine here is what lets the two be compared directly
# — see test_btcl_quoted_paths.py, which runs the same cases through both).
proc _split_args {ln {skip 1}} {
    set out {}
    set i 0
    set n [string length $ln]
    set at_start 1
    while {$i < $n} {
        set ch [string index $ln $i]
        if {[string is space -strict $ch]} {
            set at_start 1
            incr i
            continue
        }
        if {$at_start && $ch eq "#"} { break }
        if {$at_start && ($ch eq "\"" || $ch eq "'")} {
            set end [string first $ch $ln [expr {$i + 1}]]
            if {$end >= 0} {
                lappend out [string range $ln [expr {$i + 1}] [expr {$end - 1}]]
                set i [expr {$end + 1}]
                set at_start 0
                continue
            }
        }
        set j $i
        while {$j < $n && ![string is space -strict [string index $ln $j]]} {
            incr j
        }
        lappend out [string range $ln $i [expr {$j - 1}]]
        set i $j
        set at_start 1
    }
    return [lrange $out $skip end]
}

# A path spelled back to the user must be one they can TYPE.  The banners
# print a resume recipe (`btcl -i <flow> <ckpt> plan`), and an unquoted
# spaced path there is four shell words, not a command.
proc _shell_word {p} {
    # SINGLE quotes, not double (Codex #783 P2).  Inside double quotes a
    # POSIX shell still expands `$VAR`, backticks and `$(...)`, so a
    # checkpoint named `ck $(date).bdb` would print a recipe that RUNS
    # something when typed — and the earlier cut returned a path containing a
    # `"` completely unquoted, which is the same failure the quoting exists
    # to remove.  Inside single quotes nothing is special but `'` itself, and
    # the standard `'\''` splice covers that.  This is the shell `bin/btcl`
    # runs under; a path needing no quoting is printed exactly as typed.
    if {[regexp {^[A-Za-z0-9_./:@%+=,-]+$} $p]} { return $p }
    return '[string map [list ' {'\''}] $p]'
}

# ── -b pre-flight: what the flow TEXT says about its checkpoint ───────────
# `.buda` is a flat command language — no conditionals — so a static scan in
# the engine's own reading order (`source`-following, stopping where an
# `exit` stops the run) sees the open_bdb sequence the run will execute.
# The verdict is about the LAST effective open, because that is the BDB the
# routed result lands in: `analyze` applies the same last-one-wins rule to
# the RECORDED lines after the fact, and this is that rule applied BEFORE
# the fact — the only time a refusal costs nothing.  The shape it exists to
# refuse is the one that cost a 2000-second heal: a flow whose last
# `open_bdb` is a `.sql` without `writeback` routes to the end and then
# DISCARDS everything, with no message during the run saying so.
#
# `_strip_comment` and `_rest_of_line` are the Tcl twins of
# `buda_script.strip_inline_comment` and `sole_path_arg` (the `source` rule:
# the rest of the line IS the path, spaces included), the same way
# `_split_args` twins `split_quoted_args` — a reader in another language
# needs a twin whose agreement is measured, not assumed
# (test_btcl_quoted_paths.py runs the same cases through both).
proc _strip_comment {ln} {
    set i 0
    set n [string length $ln]
    set at_start 1
    while {$i < $n} {
        set ch [string index $ln $i]
        if {[string is space -strict $ch]} { set at_start 1; incr i; continue }
        if {$at_start && $ch eq "#"} {
            return [string range $ln 0 [expr {$i - 1}]]
        }
        if {$at_start && ($ch eq "\"" || $ch eq "'")} {
            set end [string first $ch $ln [expr {$i + 1}]]
            if {$end >= 0} { set i [expr {$end + 1}]; set at_start 0; continue }
        }
        while {$i < $n && ![string is space -strict [string index $ln $i]]} {
            incr i
        }
        set at_start 1
    }
    return $ln
}
proc _rest_of_line {ln {skip 1}} {
    set s [string trim [_strip_comment $ln]]
    for {set k 0} {$k < $skip} {incr k} {
        if {![regexp {^\S+\s+(.*)$} $s -> s]} { return "" }
    }
    set s [string trim $s]
    if {[string length $s] >= 2} {
        set q [string index $s 0]
        if {($q eq "\"" || $q eq "'") && [string index $s end] eq $q} {
            set s [string range $s 1 end-1]
        }
    }
    return $s
}
# One file's crc32 — the input-BDB staleness stamp (`_flow_crc` is the
# source-TREE form of the same idea; this one is for a single binary/text
# input the tree walk does not read).
proc _file_crc {p} {
    set f [open $p rb]
    set d [read $f]
    close $f
    format %u [zlib crc32 $d]
}

# The `# flow:` header of a build trace — which flow built this checkpoint.
# "" when the file cannot be read or carries no header.
proc _trace_flow {tf} {
    set hdr ""
    catch {
        set f [open $tf]
        foreach ln [split [read $f] \n] {
            if {[regexp {^# flow: (.*)$} $ln -> p]} { set hdr $p; break }
        }
        close $f
    }
    return $hdr
}

# The scanner: returns 1 when an `exit` ended the run (so a caller mid-tree
# stops too), and records the last open_bdb's verdict in _pf_last.  Relative
# paths — the sourced file's and the BDB's — resolve against the SCRIPT's
# directory, the engine's own rule, so the verdict names the file the run
# would actually open.  Two more engine rules, both measured to matter
# (Codex #794 P1s):
#   * `source foo` with only `foo.buda` on disk gets the `.buda` suffix
#     appended (cmd_source's fallback), so the scanner must follow it too —
#     skipping it silently approved a build whose included file ends in a
#     non-durable open, the exact loss the pre-flight exists to refuse;
#   * a file sourced TWICE executes twice, so the guard is an ACTIVE
#     RECURSION STACK (cycle protection), not a visited memo — the memo
#     skipped the second execution, and `include(nondurable-open);
#     open_bdb good.bdb; include again` read as durable while the run ends
#     on the include's non-durable open.
proc _preflight_scan {path depth} {
    global _pf_last _pf_visited _pf_files
    if {$depth > 16} { return 0 }
    set norm [file normalize $path]
    if {![file isfile $norm] && ![string match *.buda $norm]
            && [file isfile $norm.buda]} {
        set norm $norm.buda
    }
    if {[info exists _pf_visited($norm)]} { return 0 }
    set _pf_visited($norm) 1
    try {
        if {[catch {open $norm} f]} { return 0 }
        set text [read $f]
        close $f
        lappend _pf_files $norm
        set dir [file dirname $norm]
        set lno 0
        foreach ln [split $text \n] {
            incr lno
            set ln [string trim $ln]
            set verb [_verb $ln]
            if {$verb eq "exit"} { return 1 }
            if {$verb eq "source"} {
                set p [_rest_of_line $ln]
                if {$p eq ""} continue
                if {[file pathtype $p] ne "absolute"} {
                    set p [file join $dir $p]
                }
                if {[_preflight_scan $p [expr {$depth + 1}]]} { return 1 }
                continue
            }
            if {$verb ne "open_bdb"} continue
            set rest [_split_args $ln]
            set p [lindex $rest 0]
            if {$p eq ""} continue
            incr ::_pf_nopens
            if {$p eq ":memory:"} {
                set _pf_last [list nondurable :memory: \
                      "`:memory:` dies with the process" $norm $lno]
            } else {
                set raw $p
                if {[file pathtype $p] ne "absolute"} {
                    set p [file join $dir $p]
                }
                set p [file normalize $p]
                # raw token -> the file the ENGINE opens.  The recorder
                # writes the line verbatim and flattens the source tree, so
                # a relative token recorded from a SOURCED file has lost the
                # directory it resolved against — the scanner is the one
                # reader that still knows it (_resolve_bdb consults this;
                # the same token resolving differently in two files is
                # marked ambiguous and falls back to the entry-dir rule).
                global _pf_bdb
                if {![info exists _pf_bdb($raw)]} {
                    set _pf_bdb($raw) $p
                } elseif {$_pf_bdb($raw) ne $p} {
                    set _pf_bdb($raw) __AMBIGUOUS__
                }
                if {[string match -nocase *.sql $p] && "writeback" ni $rest} {
                    set _pf_last [list nondurable $p "a `.sql` opened\
                          without `writeback` is a throwaway materialized\
                          copy" $norm $lno]
                } else {
                    set _pf_last [list durable $p]
                }
            }
        }
        return 0
    } finally {
        unset _pf_visited($norm)
    }
}
# {none} | {durable <path>} | {nondurable <path> <why> <file> <line>}
proc _preflight_ckpt {flowpath} {
    global _pf_last _pf_visited _pf_files _pf_bdb _pf_nopens
    set _pf_last {none}
    set _pf_nopens 0
    array unset _pf_visited
    array unset _pf_bdb
    set _pf_files {}
    _preflight_scan $flowpath 0
    return $_pf_last
}
# The staleness fingerprint: one crc32 over the flow's WHOLE source tree,
# in scan order — the recorded recipe is a flattening of the entry file AND
# everything it sources, so stamping only the entry file let an edit to an
# included `.buda` resume silently un-applied (Codex #794 P1).  The walk is
# the pre-flight's, so both sides of the comparison enumerate the same
# files the same way; a file that vanished since simply drops out and the
# crc differs, which is the right verdict.
proc _flow_crc {flowpath} {
    _preflight_ckpt $flowpath
    set crc 0
    foreach p $::_pf_files {
        set f [open $p rb]
        set d [read $f]
        close $f
        set crc [zlib crc32 $d $crc]
    }
    format %u $crc
}

# A replan replay must not repeat: outputs and windows, session control, the
# checkpoint plumbing, pins/edits (already session state — and a prompt
# unpin must not be fought by a replayed pin), or generation/bundling (an
# iteration re-plans, it does not rebuild; regenerating would renumber the
# candidate pools under the user's pins).
set skip_prefixes {
    visualize save_bdb exit open_bdb load_pipeline dump_ edit_ emit_
    export_ select_topolog unpin_topology generate_ run_bundler
    run_hier_bundler import_
}
proc _skipped {verb {skips {}}} {
    if {![llength $skips]} { set skips $::skip_prefixes }
    foreach p $skips {
        if {[string match ${p}* $verb]} { return 1 }
    }
    return 0
}

# The engine resolves a relative path against the SCRIPT's directory (CWD
# only when no script is running), and the recorder writes the line
# verbatim — so a recorded relative `open_bdb` token must be resolved the
# way the engine resolved it (against the flow), not the way a naive
# replay would (against this driver's CWD): the trace would land in the
# wrong directory and the resume's raw `buda::do` replay — which runs with
# NO script — would open a DIFFERENT file than the build did.
proc _resolve_bdb {p} {
    if {$p eq ":memory:" || [file pathtype $p] eq "absolute"} { return $p }
    # The engine resolves against the INNERMOST sourced file's directory,
    # and the flattened record has lost which file that was — but the
    # pre-flight scan walks the same tree and kept the answer (a relative
    # open in a sourced sub-directory landed the checkpoint THERE, while
    # the entry-dir guess put the trace beside a file nobody has).  The
    # entry-dir rule stays as the fallback: token unseen by the scan, or
    # ambiguous across files.
    if {[info exists ::_pf_bdb($p)] && $::_pf_bdb($p) ne "__AMBIGUOUS__"} {
        return $::_pf_bdb($p)
    }
    file normalize [file join [file dirname $::flow] $p]
}

proc analyze {lines} {
    global is_hier routed ckpt ckpt_live ckpt_why first_bus tail
    set is_hier 0; set routed 0; set ckpt ""; set ckpt_live 0; set ckpt_why ""
    set first_bus ""; set tail {}
    set planning 0
    foreach ln $lines {
        set verb [_verb $ln]
        if {$verb eq "run_planner" && [lindex [split $ln] 1] eq "hier"} {
            set is_hier 1
        }
        if {$verb eq "run_nuts"} { set routed 1 }
        if {$verb eq "open_bdb"} {
            set rest [_split_args $ln]
            set p [_resolve_bdb [lindex $rest 0]]
            # `ckpt_live` distinguishes "a file-backed BDB was SEEN" from
            # "the file-backed BDB is the one still OPEN and DURABLE":
            #  * a flow that opens `:memory:` after it (an armed BDB
            #    followed by the flow's own open) replaced it — pins made
            #    at the prompt then go to memory;
            #  * a `.sql` text fixture opened WITHOUT `writeback` is
            #    materialized to a throwaway binary the pipeline writes to
            #    and discards — nothing flushes back to the fixture, so a
            #    "checkpoint" claim on it would promise a resume that
            #    restores none of this session's work.
            if {$p eq ":memory:"} {
                set ckpt_live 0
                set ckpt_why "the flow reopened :memory: after it"
            } else {
                set ckpt $p
                if {[string match -nocase *.sql $p]
                        && "writeback" ni $rest} {
                    set ckpt_live 0
                    set ckpt_why "a .sql fixture opened without `writeback`\
                          is a throwaway materialized copy -- nothing\
                          flushes back"
                } else {
                    set ckpt_live 1
                    set ckpt_why ""
                }
            }
        }
        if {$first_bus eq "" && $verb eq "add_bus"} {
            # `add_bus d0[8] ...` -> hint `d0`, for the banner's examples.
            regexp {^(\S+?)\[} [lindex [split $ln] 1] -> first_bus
        }
        if {$verb eq "run_planner"} { set planning 1 }
        if {$planning && ![_skipped $verb]} { lappend tail $ln }
    }
    if {$first_bus eq ""} { set first_bus 1 }  ;# bundle 1: a valid selector
}

# ── shared: summarized output, as `bin/buda` gives it ─────────────────────
#
# A `.buda` flow run through `bin/buda` prints ONE line per command and files
# the detail in `<flow_dir>/log/<stem>_flow.log`.  Run through this driver the
# same flow printed every line of every command — `flow/big_data_test/bigHalf`
# measured 677 lines against the CLI's 51 — and left no log to read the detail
# in afterwards.  Nothing about the engine differed: the summarizer is gated on
# a flow log being open, and only the CLI ever opened one.  `buda::log` opens
# the same one, so a flow means the same thing through both doors.
#
# Armed around the FLOW and around every replay, dropped while the prompt
# waits: a command the user TYPED is one whose output they asked to read
# (`topos` exists to print a table), and summarizing that would answer a
# question with a line count.  Re-arming appends, so one session is one log.
proc _log_on  {} { catch {buda::log $::flow} }
proc _log_off {} { catch {buda::log off} }
# Every exit from an armed region goes through here, the failing ones
# included: the end report is what NAMES the log file, and a run that died is
# the case where being told where the detail went matters most.  (The failing
# command's own detail is on the terminal regardless — the engine prints a
# raising command in full rather than summarizing it.)
proc _log_finish {} { catch {buda::endreport} ; _log_off }

# The one line of a failed command's payload worth putting on stderr.
#
# DISPLAY only: whether the flow failed is the ENGINE's decision, already
# made (the bridge raised).  This just picks which line to name, because the
# payload of a failed `source` opens with the flow's FIRST output — "Bundler
# created 1 hbundles." — and printing all of it duplicates what the bridge
# has already echoed to stdout, at the length of a whole runtime summary.
# Two shapes: a summarized command leads with the `x` marker, an
# unsummarized one leads with the diagnostic itself.  Falls back to the
# first line, so an unrecognised payload still says something.
proc _fail_line {text} {
    foreach ln [split $text \n] {
        set t [string trim $ln]
        if {[string match {x *} $ln]
            || [string match -nocase {Error*} $t]
            || [regexp {^BUDA-[0-9]+: (ERROR|FATAL):} $t]} {
            return $t
        }
    }
    return [string trim [lindex [split $text \n] 0]]
}

# ── shared: the replay engine ─────────────────────────────────────────────
# A replay that stops partway is a FAILURE, not a finished route: the session
# then holds a mix of old and new state, and a verdict read off it would be
# stale.  So the error is RE-RAISED (the prompt's catch prints it and keeps
# `pins_dirty` set, since its clear follows the route call), and the sticky
# flag keeps the exit code honest even when the user routes on and quits
# with clean pins — only a replay that runs to the end clears it.
set replay_failed 0
proc _replay {lines} {
    set ::replay_failed 1
    foreach ln $lines {
        puts "replay> $ln"
        if {[catch {buda::do $ln} err]} {
            error "replay stopped at `$ln`: $err"
        }
    }
    set ::replay_failed 0
}
proc replay_tail {} {
    if {![llength $::tail]} {
        puts "[set ::tag]: the flow never planned -- no replan recipe to replay"
        return
    }
    # A replan re-runs the flow's own routing tail, so it is summarized like
    # the flow was.  The disarm is unconditional — a replay that RAISES must
    # still hand the prompt back with its output un-summarized, or the failure
    # would be followed by a prompt that silently swallows what you type next.
    _log_on
    set rc [catch {_replay $::tail} err opts]
    _log_off
    if {$rc} { return -options $opts $err }
}

# The inspection session's prompt hooks (hier nuts/dnuts — a post-expansion
# restore): the guard blocks the persisting mutators, the route refuses the
# replan, and both say why with the remedy.  The guard sees EVERY raw
# engine command (the prompt does not pretend to know which verbs mutate):
# blocked are the pin family, edits, and anything that re-plans, re-bundles
# or re-generates — `run_planner hier` here would re-expand the
# already-expanded wrappers, and a re-bundle/regenerate's persist writes
# the pre-expansion view, which on this session IS the expanded list
# (Codex #763).  Re-solving is fine: run_nuts/run_detailed_nuts persist
# through the selective expanded machinery, which is what the stage replay
# itself runs.
proc _inspect_guard {verb} {
    if {[string match edit_* $verb]
            || [string match generate_* $verb]
            || $verb in {select_topology select_topologies unpin_topology
                         run_planner run_bundler run_hier_bundler}} {
        puts "[set ::tag]: `$verb` is disabled in this INSPECTION session --\
              a persist here would write the post-expansion view over the\
              checkpoint's template rows; pin/edit from a `plan` resume\
              instead"
        return 1
    }
    return 0
}
proc _inspect_replan {} {
    puts "[set ::tag]: `replan` needs `run_planner hier`, which this session\
          resumed BELOW (the restored plan is the result being inspected) --\
          resume at `plan` to re-plan or re-heal"
}

# Default TRUE: a RESUME session got here because its trace already exists, so
# the shared checkpoint-reporting block below may offer a further resume.  The
# BUILD path resets this to 0 and flips it back only when it writes the trace,
# so a build whose trace write failed does not over-promise (Codex #789 P2).
set trace_ok 1

# ══════════════════════════════════════════════════════════════════════════
if {$stage eq "build"} {
    # ── BUILD: arm the recorder, run the flow verbatim ────────────────────
    if {$mode eq "build"} {
        # -b decides its checkpoint from the flow TEXT, before an engine is
        # spawned (see _preflight_ckpt above).  Three verdicts:
        #   * nondurable — REFUSE at t=0: the run would end by discarding
        #     everything it routed, and t=0 is the only cheap place to say
        #     so (the alternative is a message after the 30-minute route).
        #   * durable    — the flow owns its checkpoint; arming another
        #     would just be replaced by the flow's own open, so -b arms
        #     nothing (an EXPLICIT <ckpt> stays armed — the user asked, and
        #     the banner's replacement NOTE reports what the flow did).
        #   * none       — arm the auto-named <flow_dir>/<stem>.ckpt.bdb.
        set pf [_preflight_ckpt $flow]
        switch -- [lindex $pf 0] {
            nondurable {
                lassign $pf - p why where lno
                if {$p ne ":memory:" && $::_pf_nopens == 1} {
                    # THE read-only-input flow: the flow's ONLY open_bdb is
                    # a `.sql` without `writeback` — "read the design, never
                    # write it back".  The engine already copies that .sql
                    # to a binary and persists the whole pipeline into the
                    # copy; the only thing wrong with the copy was its NAME
                    # (a throwaway temp).  -b names it: the materialization
                    # lands in the checkpoint (BUDA_BDB_MATERIALIZE_TO), the
                    # input stays read-only by construction — same code
                    # path, no writeback source armed — and the copy simply
                    # survives the session, continuously written, so even a
                    # killed run keeps its routed state.  Only the
                    # SINGLE-open shape redirects: with several opens the
                    # request could land on the wrong one, so those keep the
                    # refusal below.
                    if {![file isfile $p]} {
                        puts stderr "$tag: -b refused before running: the\
                              flow's input BDB $p does not exist\
                              ($where line $lno)"
                        exit 2
                    }
                    set redirect [expr {$armed ne "" ? $armed : $auto_ckpt}]
                    if {[string match -nocase *.sql $redirect]} {
                        puts stderr "$tag: -b: the checkpoint must be a\
                              BINARY path (got $redirect) -- the input .sql\
                              stays read-only and the checkpoint is the\
                              binary it materializes into"
                        exit 2
                    }
                    if {[file exists $redirect] && ![file isfile $redirect]} {
                        # A directory (or other non-file) here is a typo,
                        # and the fresh path below DELETES the target — a
                        # recursive `file delete -force` on a mistyped
                        # directory would erase it wholesale (Codex #796
                        # P1).  Refuse before anything is deleted.
                        puts stderr "$tag: -b: the checkpoint $redirect\
                              exists and is not a regular file (a\
                              directory?) -- pick a checkpoint FILENAME"
                        exit 2
                    }
                    if {[file exists $redirect]} {
                        # Reuse keeps pins (they re-attach to the rebuilt
                        # pool), and is sound only while the checkpoint
                        # still derives from THIS input — the trace's input
                        # stamp decides.
                        set old_crc ""
                        catch {
                            set f [open ${redirect}.trace]
                            foreach tln [split [read $f] \n] {
                                if {[regexp {^# input_crc32: (\S+)$} \
                                        $tln -> v]} {
                                    set old_crc $v
                                    break
                                }
                            }
                            close $f
                        }
                        if {$old_crc ne "" && ![catch {_file_crc $p} cur]
                                && $cur eq $old_crc} {
                            puts "$tag: -b reusing the checkpoint $redirect\
                                  (materialized from this input before;\
                                  pins persisted there re-attach to the\
                                  rebuilt pool)"
                        } else {
                            puts "$tag: -b re-materializing $redirect fresh\
                                  -- the input changed since it was built\
                                  (or its build trace is gone), so the old\
                                  checkpoint no longer derives from this\
                                  input; pins in it are discarded"
                            # Each victim individually, and only when it
                            # is a regular file: `-force` on a directory
                            # recurses, and while the target itself was
                            # just validated, a same-named sidecar must
                            # never widen a delete either.
                            foreach victim [list $redirect \
                                    ${redirect}-wal ${redirect}-shm \
                                    ${redirect}.trace] {
                                if {[file isfile $victim]} {
                                    file delete -force -- $victim
                                }
                            }
                        }
                    } else {
                        puts "$tag: -b materializing the read-only input\
                              $p into the checkpoint $redirect (the input\
                              is never written)"
                    }
                    set ::env(BUDA_BDB_MATERIALIZE_TO) $redirect
                    set redirect_input $p
                    # The flow's own open lands in the target — arming a
                    # BDB of our own would just be replaced by it.
                    set armed ""
                } else {
                    puts stderr "$tag: -b refused before running: the flow's\
                          last open_bdb is not durable -- $why -- so\
                          everything the build routes would be DISCARDED at\
                          the end."
                    puts stderr "$tag: $where line $lno: opens $p"
                    puts stderr "$tag: make it durable (`open_bdb <file>.sql\
                          writeback`, or a binary `.bdb`), or drop the open\
                          and let -b arm its own checkpoint"
                    puts stderr "$tag: (a flow whose ONLY open_bdb is a\
                          read-only .sql input is redirected instead: -b\
                          materializes it into a durable checkpoint)"
                    exit 2
                }
            }
            durable {
                if {$armed eq ""} {
                    puts "$tag: the flow opens its own durable checkpoint\
                          ([lindex $pf 1]) -- using it, no auto checkpoint\
                          armed"
                }
            }
            none {
                if {$armed eq ""} {
                    set armed $auto_ckpt
                    if {[file exists $armed]} {
                        puts "$tag: -b re-arming the existing checkpoint\
                              $armed (pins persisted there re-attach to the\
                              rebuilt pool)"
                    } else {
                        puts "$tag: -b arming checkpoint $armed"
                    }
                }
            }
        }
    }
    close [file tempfile recpath buda_record]
    set ::env(BUDA_RECORD) $recpath
    set ::env(BUDA_RECORD_NOTE) "btcl -i $tag"

    buda::start
    # Stream, so the summaries appear AS the flow runs.  The whole flow is one
    # `source` command on the wire, and buffered that is one frame at the end:
    # a 45-second route would sit behind a blank screen and then print its
    # whole summary at once, which is not what `bin/buda` looks like either.
    catch {buda::stream 1}
    _log_on
    if {$armed ne ""} {
        # Arm a file-backed BDB BEFORE the flow runs, so the whole pipeline
        # persists as it goes — the flow/tcl/design.tcl pattern, without
        # editing the flow.  This is what gives a flow that never opens a
        # BDB durable pins: a `pin` at the prompt writes through, and the
        # next `btcl -i <flow> <same.bdb>` restores it onto the rebuilt
        # pool (_apply_bdb_pins, uid-keyed).  It cannot be done at SAVE
        # time instead: the pipeline persists its rows as it runs, so a BDB
        # opened after the fact holds none of them.  The open is recorded
        # like everything else, so the checkpoint detection needs no
        # special case.  A failure here is most often ANOTHER session
        # holding the same BDB — SQLite allows one writer, which is what
        # keeps two concurrent sessions from corrupting the file: the loser
        # must fail LOUDLY at the door, in this driver's voice, not as a
        # raw Tcl stack trace.
        if {[catch {buda::open_bdb $armed} err]} {
            puts stderr "$tag: cannot open the armed BDB $armed: $err"
            puts stderr "$tag: (another session holding it?  one writer at\
                  a time -- finish or kill it, or arm a different path)"
            catch {buda::stop}
            exit 1
        }
    }
    set rc [catch {buda::source $flow} err]
    _log_finish
    if {$rc} {
        # ONE line of $err, not all of it: the bridge has already echoed the
        # whole payload to stdout, and on a fail-fast that payload now carries
        # the end report too — so repeating it here printed an entire runtime
        # summary a second time, on stderr, where Tcl has not set the UTF-8
        # encoding and its arrow comes out as `?`.
        puts stderr "$tag: the flow failed: [_fail_line $err]"
        catch {buda::stop}
        exit 1
    }
    # Read what the recorder learned BEFORE deciding what to do about the
    # session — because a flow ending in `exit` still built a checkpoint and
    # still deserves a trace.  Reading it only on the drive-to-a-prompt path
    # is what cost a 2000s heal: the flow ended in `exit`, the exit branch
    # returned before this block, and the trace was never written — so every
    # later `<stage>` resume refused, advising the build session that had
    # just run.  The recorder file is written per-command and flushed by the
    # child, so it survives the flow's own `exit`.
    set lines {}
    set f [open $recpath]
    foreach ln [split [read $f] \n] {
        set ln [string trim $ln]
        if {$ln eq "" || [string index $ln 0] eq "#"} continue
        lappend lines $ln
    }
    close $f
    file delete $recpath
    # The scan that feeds _resolve_bdb's map (idempotent; -b already ran
    # it): analyze is about to resolve recorded open_bdb tokens, and only
    # the walk knows which sourced file each one resolved against.
    catch {_preflight_ckpt $flow}
    analyze $lines
    if {$redirect ne ""} {
        if {[file exists $redirect]} {
            # The flow's open landed in the redirect target: what analyze
            # read as a non-durable .sql open is in fact the durable
            # checkpoint under its materialized name — so the trace, the
            # banner and the verdict all speak about the real file.
            set ckpt $redirect
            set ckpt_live 1
            set ckpt_why ""
        } else {
            puts "$tag: NOTE -- the redirect checkpoint $redirect was never\
                  created (the flow did not reach its open_bdb), so this\
                  run's result is not durable"
        }
    }

    puts "\n$tag: [llength $lines] command(s) ran --\
          [expr {$is_hier ? "HIER" : "FLAT"}] flow"

    # Write the resume trace beside a LIVE file-backed checkpoint: the
    # flattened build recipe is what a `<stage>` session replays the setup
    # of.  Only a BUILD session writes it (a resume session's record is
    # setup + load_pipeline + a tail — not a build recipe), and only when
    # the checkpoint is the live BDB (a dead one resumes nothing).  Runs on
    # BOTH the exit and prompt paths, so an `exit`-ending flow with a durable
    # checkpoint is resumable.
    set trace_ok 0
    if {$ckpt ne "" && $ckpt_live} {
        set tf ${ckpt}.trace
        if {[catch {
            set f [open $tf w]
            puts $f "# btcl -i build trace v1"
            puts $f "# flow: $flow"
            puts $f "# cwd: [pwd]"
            # A staleness stamp for `-r`: a resume replays the RECORDED
            # lines, so a flow whose TEXT changed since the build gets a
            # NOTE rather than a silent mix of old recipe and new intent.
            # One crc32 over the whole SOURCE TREE (_flow_crc), because the
            # recorded recipe flattens the sourced files too.  crc32 is in
            # the Tcl core (no subprocess); it detects "changed", it does
            # not authenticate.  A build that cannot read its own flow back
            # stamps the size instead.
            if {[catch {
                puts $f "# flow_crc32: [_flow_crc $flow]"
            }]} {
                puts $f "# flow_size: [file size $flow]"
            }
            if {$redirect ne "" && $redirect_input ne ""} {
                # A redirect build's provenance: which read-only input this
                # checkpoint was materialized from, and its stamp — what a
                # later -b reads to decide reuse-vs-fresh, and a resume
                # reads to NOTE a changed input and to rewrite the recorded
                # open onto the checkpoint.
                puts $f "# input: $redirect_input"
                catch {puts $f "# input_crc32: [_file_crc $redirect_input]"}
            }
            foreach ln $lines { puts $f $ln }
            close $f
        } err]} {
            puts "$tag: NOTE -- could not write the resume trace $tf ($err);\
                  `$tag <ckpt> plan` will need a build session that can"
        } else {
            set trace_ok 1
            puts "$tag: resume trace $tf -- next session can skip the\
                  rebuild: btcl -i [_shell_word $tag] [_shell_word $ckpt] plan"
        }
    }

    # A flow ending in `exit` has ended the ENGINE session — no live pipeline
    # to drive at a prompt.  The trace (above) was still written if the
    # checkpoint is durable, so a `<stage>` resume works; if it is NOT, the
    # routing this session did was DISCARDED, and the user must hear that now
    # — not at a refused resume, a long route later.
    if {[catch {buda::query bundles} nb]} {
        if {$ckpt ne "" && $ckpt_live && $trace_ok} {
            puts "$tag: the flow ended in `exit`; the checkpoint above holds\
                  the routed result, so a `<stage>` resume works"
        } elseif {$ckpt ne "" && $ckpt_live} {
            # Durable checkpoint, but the trace write failed (see the NOTE
            # above) — a `<stage>` resume reads that trace, so promising it
            # here would advertise a command that immediately refuses.
            puts "$tag: the flow ended in `exit`; the checkpoint holds the\
                  routed result, but the resume trace could not be written\
                  (above), so a `<stage>` resume is unavailable until that\
                  path can be -- free it and re-run, or drive the flow live"
        } else {
            set why [expr {$ckpt_why ne "" ? $ckpt_why : "no file-backed\
                  open_bdb"}]
            set repl [expr {$armed ne "" && $ckpt ne "" && $ckpt ne $armed \
                  ? "  The flow opened its own BDB ($ckpt) after the armed\
                  $armed, replacing it." : ""}]
            puts "$tag: WARNING -- the flow ended in `exit` and left no\
                  durable checkpoint ($why), so everything this session\
                  routed was DISCARDED.$repl  For a resumable run open the\
                  checkpoint durably: `open_bdb <file>.sql writeback`, or a\
                  binary `.bdb`.  (btcl -b refuses this shape BEFORE running,\
                  and arms an auto checkpoint when the flow opens no BDB.)"
        }
        exit 0
    }
} else {
    # ── RESUME: setup from the trace + load_pipeline + re-enter at stage ──
    if {$mode eq "resume" && $armed eq ""} {
        # -r discovers the checkpoint: the auto name first, else any trace
        # beside the flow whose `# flow:` header names THIS flow (a build
        # with an explicit <ckpt> wrote one of those).  Zero is a refusal
        # with the remedy; two or more is a QUESTION, not a guess.
        if {[file exists $auto_ckpt] && [file exists ${auto_ckpt}.trace]} {
            set armed $auto_ckpt
        } else {
            # The flow may OWN its checkpoint (-b arms nothing then), and a
            # sourced file's relative open lands it — with its trace —
            # OUTSIDE the entry flow's directory, where the glob below
            # cannot see it (Codex #794 P2).  The pre-flight walks the same
            # source tree the run does, so ask it first; the `# flow:`
            # header check keeps a checkpoint another flow built from being
            # adopted (mismatch falls through to the glob, not a refusal).
            set pf [_preflight_ckpt $flow]
            if {[lindex $pf 0] eq "durable"} {
                set own [lindex $pf 1]
                if {[file exists $own] && [file exists ${own}.trace]
                        && [_trace_flow ${own}.trace] eq $flow} {
                    set armed $own
                }
            }
        }
        if {$armed eq ""} {
            set found {}
            foreach t [glob -nocomplain -directory [file dirname $flow] \
                           *.trace] {
                set c [string range $t 0 end-6]
                if {![file exists $c]} continue
                if {[_trace_flow $t] eq $flow} { lappend found $c }
            }
            if {[llength $found] == 1} {
                set armed [lindex $found 0]
            } elseif {[llength $found] == 0} {
                puts stderr "$tag: -r found no checkpoint for this flow\
                      (looked for $auto_ckpt, then for any *.trace beside\
                      the flow naming it) -- build one first: btcl -b\
                      [_shell_word $flow]"
                exit 2
            } else {
                puts stderr "$tag: -r found [llength $found] checkpoints\
                      for this flow -- name one:"
                foreach c $found {
                    puts stderr "  btcl -r [_shell_word $flow]\
                          [_shell_word $c]"
                }
                exit 2
            }
        }
        puts "$tag: -r resuming from $armed"
    }
    # `-r` may still be resolving its stage (the deepest recorded one), so
    # the refusals up to that point name the MODE rather than interpolate
    # an empty stage into their message.
    set what [expr {$stage eq "" ? "-r" : "stage `$stage`"}]
    set tf ${armed}.trace
    if {![file exists $armed]} {
        puts stderr "$tag: $what needs the checkpoint $armed, which\
              does not exist -- run a build session first: btcl -i\
              [_shell_word $tag] [_shell_word $armed]"
        exit 2
    }
    if {![file exists $tf]} {
        # The BDB is here but the build wrote no trace.  A build writes the
        # trace only beside a DURABLE checkpoint, so its absence means the
        # last build's checkpoint was not durable — the flow opened a `.sql`
        # without `writeback` (or `:memory:`), or replaced the armed BDB with
        # its own — OR the build did not finish.  Either way the routing was
        # not saved.  Advising "run a build session first" would name the very
        # command that produced this state; name the cause instead.
        puts stderr "$tag: $what: the checkpoint $armed exists but\
              its build trace $tf does not.  A build writes the trace only\
              beside a DURABLE checkpoint, so the last build either left a\
              non-durable one (a `.sql` opened without `writeback`, or\
              `:memory:`, or the flow replaced the armed BDB) or did not\
              finish -- so its routing was not saved.  Re-run the build with a\
              durable checkpoint: open the BDB `.sql writeback`, or a binary\
              `.bdb`."
        exit 2
    }
    set traced_flow ""; set traced_cwd ""
    set traced_crc ""; set traced_size ""
    set traced_input ""; set traced_input_crc ""
    set lines {}
    set f [open $tf]
    foreach ln [split [read $f] \n] {
        set ln [string trim $ln]
        if {[regexp {^# flow: (.*)$} $ln -> p]} { set traced_flow $p; continue }
        if {[regexp {^# cwd: (.*)$} $ln -> p]}  { set traced_cwd $p; continue }
        if {[regexp {^# flow_crc32: (\S+)$} $ln -> p]} {
            set traced_crc $p
            continue
        }
        if {[regexp {^# flow_size: (\S+)$} $ln -> p]} {
            set traced_size $p
            continue
        }
        if {[regexp {^# input: (.*)$} $ln -> p]} {
            set traced_input $p
            continue
        }
        if {[regexp {^# input_crc32: (\S+)$} $ln -> p]} {
            set traced_input_crc $p
            continue
        }
        if {$ln eq "" || [string index $ln 0] eq "#"} continue
        lappend lines $ln
    }
    close $f
    if {$traced_flow ne "" && $traced_flow ne $flow} {
        # Resuming flow A's checkpoint under flow B's name would label every
        # message, verdict and replan with a flow that never built it.
        puts stderr "$tag: $tf was built by $traced_flow, not $flow --\
              rerun a build session for THIS flow (or resume that one)"
        exit 2
    }
    if {$traced_cwd ne "" && $traced_cwd ne [pwd]} {
        # Recorded relative paths only replay from the directory the build
        # ran in (the recorder's own `# cwd:` rule, tools/tcl2buda.py).
        puts "$tag: NOTE -- the build ran in $traced_cwd, this session in\
              [pwd]; recorded relative paths resolve differently"
    }
    # The staleness stamp (written at build): a resume replays the RECORDED
    # lines, so an edit to the flow's text since then does not take effect
    # here — worth one NOTE, not a refusal (the recorded build is a
    # perfectly good thing to resume; a pre-stamp trace checks nothing).
    set stale 0
    if {$traced_crc ne ""} {
        if {![catch {_flow_crc $flow} cur] && $cur ne $traced_crc} {
            set stale 1
        }
    } elseif {$traced_size ne "" && [file size $flow] != $traced_size} {
        set stale 1
    }
    if {$stale} {
        puts "$tag: NOTE -- the flow's text (or a sourced file's) changed\
              since this build; the resume replays the build as RECORDED\
              (rebuild to pick up the edit: btcl -b [_shell_word $flow])"
    }
    if {$traced_input ne "" && $traced_input_crc ne ""
            && ([catch {_file_crc $traced_input} icur]
                || $icur ne $traced_input_crc)} {
        puts "$tag: NOTE -- the input BDB $traced_input changed since this\
              build (or cannot be read); the resume opens the checkpoint\
              materialized from the OLD input (rebuild: btcl -b\
              [_shell_word $flow])"
    }
    catch {_preflight_ckpt $flow}
    analyze $lines
    if {$traced_input ne ""} {
        # A redirect build: the recorded open is the read-only .sql, but
        # the durable state lives in the checkpoint it materialized into —
        # the file this trace sits beside.
        set ckpt $armed
        set ckpt_live 1
        set ckpt_why ""
    }
    if {!$ckpt_live || $ckpt eq ""} {
        puts stderr "$tag: the build left no durable checkpoint\
              ([expr {$ckpt_why ne "" ? $ckpt_why : "no file-backed\
              open_bdb"}]) -- nothing to resume; run build"
        exit 2
    }
    # The stage's cut: the first recorded command the resumed session
    # re-runs.  Everything before it is either replayed as setup or held by
    # the checkpoint; everything from it on replays (minus the outputs/pins
    # filter — same rules as the prompt's `replan`).
    array set cut_verbs {
        topo  {generate_topologies generate_hier_topologies
               generate_topologies_for_bundle generate_topologies_for_hbundle
               generate_more_topologies}
        plan  {run_planner}
        nuts  {run_nuts}
        dnuts {run_detailed_nuts}
    }
    if {$stage eq ""} {
        # -r with no -s: the DEEPEST stage the trace records.  Deepest is
        # the cheapest honest default — everything above it is restored,
        # nothing is recomputed — and `-s` is the override for a session
        # that wants to re-enter higher (re-plan, re-generate).
        foreach s {dnuts nuts plan topo} {
            foreach ln $lines {
                if {[_verb $ln] in $cut_verbs($s)} { set stage $s; break }
            }
            if {$stage ne ""} break
        }
        if {$stage eq ""} {
            puts stderr "$tag: the build recorded no resumable stage (no\
                  generation, planner or NUTS command in the trace) --\
                  nothing to resume; rebuild: btcl -b [_shell_word $flow]"
            exit 2
        }
        puts "$tag: resuming at the deepest recorded stage `$stage`\
              (override: -s topo|plan|nuts|dnuts)"
    }
    # A hier resume BELOW the planner restores the post-expansion view
    # (`load_pipeline expanded`) — an INSPECTION session: the quick look at
    # a long healer flow's result.  What it cannot host is anything that
    # persists the pre-expansion view, because on an expanded restore that
    # view is not there to persist (_persist_wrappers would fall through to
    # the expanded list and write it over the checkpoint's template rows —
    # the #723 clobber through a new door), and a `run_planner hier` here
    # would re-expand already-expanded wrappers.  So pins/edits/replan are
    # guarded at the prompt, and the verdict/explorer/dumps are the point.
    set inspect [expr {$is_hier && $stage in {nuts dnuts}}]
    set cut -1
    for {set i 0} {$i < [llength $lines]} {incr i} {
        if {[_verb [lindex $lines $i]] in $cut_verbs($stage)} {
            set cut $i
            break
        }
    }
    if {$cut < 0} {
        puts stderr "$tag: the build never ran a `$stage`-stage command --\
              nothing to resume there (the trace has: [lsort -unique\
              [lmap l $lines {_verb $l}]])"
        exit 2
    }

    # Setup = the pre-cut prefix, classified:
    #   * FLAT flow — session state by nature (design.tcl re-declares blocks
    #     and buses every session), so everything replays except outputs and
    #     session control, and except the PIPELINE stages the cut skips.
    #   * HIER flow (or any flow that constructs hierarchy/netlist in the
    #     BDB) — construction is IN the checkpoint (a replayed add_inst is a
    #     duplicate-instance error; a re-derive would renumber busterm ids
    #     the restored bundles reference), so only the session-state verbs
    #     replay and the rest is skipped, counted, and said.
    set construction_verbs {
        add_cell add_cell_pin add_inst add_inst_to_cell add_comp
        move_comp flip_comp rotate_comp resize_cell set_comp_bbox
        import_def_lef import_verilog import_gds
        derive_busterms refine_busterms derive_container_bboxes
        bdb_net_mode align_bottom_up check_template_tracks
    }
    set bdb_built 0
    foreach ln $lines {
        if {[_verb $ln] in $construction_verbs} { set bdb_built 1; break }
    }
    if {!$bdb_built} { set bdb_built $is_hier }
    # Session-state verbs that are safe AND needed on every session (the
    # hdesign.tcl resume list, generalized): the stack, patterns, floorplan
    # projections, and the persisted-but-idempotent policies a resume may
    # legitimately re-declare (hdesign re-declares caps/shares on purpose —
    # changing them IS the experiment, and a tightened cap voids the
    # restored plan loudly).
    set session_verbs {
        open_bdb def_layer import_lef_tech def_track_pattern
        add_grid_override def_gds_layer corner_margin set_track_pitch
        set_unit_check set_import_scale detour_channel set_feedthru
        set_bundling set_max_bundle_bits set_keepout_loci
        set_prune_dominated set_dedup_loci set_trim_mst_legs
        set_trim_trunk_stubs set_drop_dangling set_dead_span_escalate
        set_pair_align_heal set_planner_param set_die set_bottom_up
        set_cell_layer_cap set_cell_layer_share set_layer_caps_by_depth
        reserve_top_layers add_block add_keepout add_blocks_from_bdb
        require_file
    }
    set session_prefixes {set_min_stub_length def_ndr set_ndr}
    if {!$bdb_built} {
        # Flat: the netlist is session state too (design.tcl's rule).
        lappend session_verbs add_bus add_net
    }
    set pipeline_prefixes {run_bundler run_hier_bundler generate_
                           run_planner run_nuts run_detailed_nuts
                           ripup_reroute negotiate_congestion
                           refine_selection check_}
    # Neither kind replays outputs, session control, or pins/edits in setup
    # (pins are load_pipeline's to restore — a replayed flow-text pin would
    # override the prompt pin the user made last session, the same rule the
    # replan tail follows).
    set never_prefixes {visualize save_bdb exit load_pipeline dump_ edit_
                        emit_ export_ select_topolog unpin_topology}
    set setup {}
    set held 0
    foreach ln [lrange $lines 0 [expr {$cut - 1}]] {
        set verb [_verb $ln]
        if {[_skipped $verb $pipeline_prefixes]} { continue }
        if {[_skipped $verb $never_prefixes]} { continue }
        if {$verb eq "open_bdb"} {
            # The recorded token may be relative — the engine resolved it
            # against the FLOW's directory, but this replay runs with no
            # script, so a raw resend would resolve against the CWD and
            # open a different file.  Rewrite to the path the build used.
            # A redirect build's open is rewritten FURTHER: the recorded
            # token names the read-only .sql, and re-opening that here
            # would materialize a fresh throwaway copy and restore nothing
            # — the durable state is in the checkpoint it materialized
            # into, so that is the file this session opens.
            set rest [_split_args $ln]
            if {$traced_input ne ""
                    && [_resolve_bdb [lindex $rest 0]] eq $traced_input} {
                set ln "open_bdb [::buda::_join_args [list $armed]]"
                lappend setup $ln
                continue
            }
            # Re-quoted through the bridge's OWN rule rather than joined
            # raw: `_resolve_bdb` returns an absolute path that may contain
            # spaces, and this line is sent verbatim (`buda::do`), so a bare
            # join would hand the engine the very split this proc undid.
            set ln "open_bdb [::buda::_join_args \
                    [concat [list [_resolve_bdb [lindex $rest 0]]] \
                            [lrange $rest 1 end]]]"
        }
        if {!$bdb_built} {
            # Flat: setup is session state by nature — replay it all.
            lappend setup $ln
            continue
        }
        set ok [expr {$verb in $session_verbs}]
        if {!$ok} {
            foreach p $session_prefixes {
                if {[string match ${p}* $verb]} { set ok 1; break }
            }
        }
        if {$ok} { lappend setup $ln } else { incr held }
    }

    # The post-cut replay: the flow's own commands from the stage on, under
    # the replan filter — except that a `topo` cut must of course replay
    # the generation commands the filter normally holds back.  A cut BELOW
    # the planner additionally HOLDS the planner-dependent commands
    # (healers, refine, post_nuts): load_pipeline restores the plan onto the
    # wrappers but not the planner OBJECT, so negotiate/ripup/refine would
    # refuse outright — and for a HEALED checkpoint holding them is also the
    # honest fast path, because a healer commit is a full-pipeline state:
    # the restored plan already CARRIES the healing, and re-solving
    # NUTS/DNUTS from it reproduces the healed endpoint without paying for
    # the healers again.  Re-healing is what a `plan` resume is for.
    set stage_skips $skip_prefixes
    if {$stage eq "topo"} {
        set stage_skips [lsearch -all -inline -not -exact $stage_skips generate_]
    }
    set held_planner {}
    if {$stage in {nuts dnuts}} {
        set planner_dep {run_planner ripup_reroute negotiate_congestion
                         refine_selection}
    } else {
        set planner_dep {}
    }
    set stage_lines {}
    foreach ln [lrange $lines $cut end] {
        set verb [_verb $ln]
        if {[llength $planner_dep] && [_skipped $verb $planner_dep]} {
            lappend held_planner $verb
            continue
        }
        if {![_skipped $verb $stage_skips]} { lappend stage_lines $ln }
    }

    puts "\n$tag: RESUMING at `$stage` from $ckpt --\
          [expr {$is_hier ? "HIER" : "FLAT"}] flow, [llength $setup] setup\
          command(s)[expr {$held ? ", $held held by the checkpoint" : ""}],\
          [llength $stage_lines] to replay"
    if {[llength $held_planner]} {
        puts "$tag: holding [llength $held_planner] planner-dependent\
              command(s) ([lsort -unique $held_planner]) -- the restored\
              plan already carries their result; resume at `plan` to\
              re-plan or re-heal"
    }
    if {$inspect} {
        puts "$tag: INSPECTION session (hier `$stage` = post-expansion\
              restore): pins, edits and replan are disabled here -- they\
              would persist the expanded view over the checkpoint's\
              templates; use a `plan` resume to change the design"
    }
    buda::start
    catch {buda::stream 1}
    _log_on
    if {[catch {_replay $setup} err]} {
        _log_finish
        puts stderr "$tag: resume setup failed: $err"
        catch {buda::stop}
        exit 1
    }
    # A hier below-plan resume needs the POST-expansion view — the routed
    # per-instance wrappers with their assigned layers — which is exactly
    # what `load_pipeline expanded` restores (bottom-up template wrappers
    # and fixed copies included, so a post-run_nuts checkpoint keeps its
    # persisted routing).
    set lp [expr {$inspect ? "load_pipeline expanded" : "load_pipeline"}]
    if {[catch {buda::do $lp} err]} {
        _log_finish
        puts stderr "$tag: $lp failed: $err"
        catch {buda::stop}
        exit 1
    }
    set nb [buda::query bundles]
    if {$nb == 0} {
        _log_finish
        puts stderr "$tag: $ckpt restored no bundles -- not this flow's\
              checkpoint?  `btcl -i [_shell_word $tag] [_shell_word $armed]` rebuilds it"
        catch {buda::stop}
        exit 1
    }
    puts "$tag: RESUMED $nb bundles from $ckpt"
    set rc [catch {_replay $stage_lines} err]
    _log_finish
    if {$rc} {
        puts stderr "$tag: $err"
        catch {buda::stop}
        puts stderr "$tag: FAILED -- the `$stage` replay stopped partway"
        exit 1
    }
}

# ── banners the prompt needs (both modes) ─────────────────────────────────
if {[llength $tail]} {
    puts "$tag: `replan` replays the flow's own routing tail\
          ([llength $tail] command(s), starting `[lindex $tail 0]`)"
} else {
    puts "$tag: the flow never ran the planner -- `replan` is unavailable"
}
if {$ckpt ne "" && !$ckpt_live} {
    # A file-backed BDB was seen but it is not the durable live one — the
    # flow reopened `:memory:` after it, or it is a read-only .sql fixture
    # (no `writeback`).  Pins made at this prompt are durable only via an
    # explicit `save <path>` snapshot of the live session.
    puts "$tag: NOTE -- $ckpt is not a durable checkpoint ($ckpt_why);\
          prompt pins die with this session (`save` snapshots it)"
    set snap_base [file rootname $tag].bdb
} elseif {$ckpt ne ""} {
    # The `(or resume: ...)` offer is gated on the trace actually being on
    # disk — the same trace_ok the exit branch checks (Codex #789 P2).  A
    # durable checkpoint whose trace write failed keeps its pins across a
    # RERUN, but a `<stage>` resume reads the trace, so offering it would name
    # a command that refuses.
    if {$trace_ok} {
        puts "$tag: checkpoint $ckpt -- pins persist; rerun the flow (or\
              resume: btcl -i [_shell_word $tag] [_shell_word $ckpt] plan) and\
              they hold"
    } else {
        puts "$tag: checkpoint $ckpt -- pins persist across a rerun of the\
              flow; the resume trace could not be written (above), so\
              `btcl -i <flow> <ckpt> <stage>` is unavailable until that path\
              can be"
    }
    if {$armed ne "" && $ckpt ne $armed} {
        # The flow opened its OWN BDB after the armed one, so the armed file
        # holds only what ran before that open — the flow's checkpoint is
        # the real one, and pretending otherwise would promise persistence
        # in a file the routing never reached.
        puts "$tag: NOTE -- the flow opened its own BDB after the armed\
              $armed; the flow's checkpoint above is the live one"
    }
    if {$redirect ne "" && $ckpt eq $redirect} {
        # The generic banner says "rerun the flow and they hold", which for
        # a redirect session is true only of a -b rerun: a BARE rerun
        # re-materializes the input into a throwaway temp and never sees
        # this checkpoint.
        puts "$tag: NOTE -- the input $redirect_input stays read-only; pins\
              hold across `btcl -b` reruns (which reuse this checkpoint),\
              not across a bare rerun"
    }
    set snap_base $ckpt
} else {
    # `save_bdb <path>` snapshots the OPEN BDB (it refuses with `open_bdb
    # first` otherwise), and opening one now would not backfill the rows the
    # pipeline persists as it runs — so a flow that never opened a BDB has
    # no snapshot to offer, and promising one here handed the user a verb
    # that could only fail.  Say what is true instead.
    puts "$tag: no file-backed BDB -- pins and edits die with this session\
          (a snapshot needs a BDB the flow itself opened); their flow-text\
          form prints at exit, and `btcl -b [_shell_word $flow]` arms an\
          auto checkpoint before the flow so they persist"
    set snap_base [file rootname $tag].bdb
}

if {$inspect} {
    set pins_dirty [prompt::run $tag "[file rootname $tag]>" _inspect_replan \
                        $snap_base $first_bus _inspect_guard]
} else {
    set pins_dirty [prompt::run $tag "[file rootname $tag]>" replay_tail \
                        $snap_base $first_bus]
}
if {$pins_dirty} {
    puts "$tag: pins changed since the last route -- re-planning so the\
          checkpoint stays coherent"
    if {[catch {replay_tail} err]} {
        puts stderr "$tag: $err"
    }
}
# Pins (and committed edits) made at THIS prompt with nowhere durable to
# land: their durable form is FLOW TEXT, so print the lines rather than let
# the session's outcome die silently — pasted before the flow's
# `run_planner`, they mean next run exactly what they meant here.  A live
# durable checkpoint needs none of this: there the rows persist and the
# next session restores them.
if {[llength $prompt::pin_lines] && !$ckpt_live} {
    puts "$tag: this session's pins, as flow text (paste before the flow's\
          run_planner to keep them):"
    foreach ln $prompt::pin_lines { puts "    $ln" }
}
if {$replay_failed} {
    catch {buda::stop}
    puts stderr "$tag: FAILED -- the last replan stopped partway, so the\
          session's state is not the flow's routed result"
    exit 1
}

# The exit code is the design's cleanliness — but only when the flow
# actually routed: a deliberately partial flow (stopped before run_nuts)
# has nothing to verdict, and -1 ("never computed") must not read as dirty.
if {!$routed} {
    buda::stop
    puts "$tag: flow did not route (no run_nuts) -- no verdict"
    exit 0
}
set ov [buda::query overlaps]
set un [buda::query unplaced]
set vi [buda::query violations]
buda::stop
# The vehicles own their flows and demand all three legs; an arbitrary flow
# may deliberately stop before detailed NUTS or never audit, and -1 ("never
# computed") must not read as dirty HERE — but it must not read as clean
# either, so the never-computed legs are named.
set dirty 0
set legs {}
foreach {v what} [list $ov "overlaps" $un "unplaced" $vi "audit violations"] {
    if {$v > 0} { set dirty 1 }
    lappend legs [expr {$v < 0 ? "$what never computed" : "$v $what"}]
}
if {$dirty} {
    puts stderr "$tag: FAILED -- [join $legs {, }]"
    exit 1
}
puts "$tag: done -- [join $legs {, }]"
