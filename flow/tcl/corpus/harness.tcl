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

# ============================================================================
# flow/tcl/corpus/harness.tcl — start, measure, report, for the translated
# QoR corpus (see tools/buda2tcl.py).  Hand-written; the flow bodies beside it
# are generated.
#
#     tclsh flow/tcl/corpus/rnr/mix.tcl
#
# Every translated file calls `corpus::begin` at its top and `corpus::end` at
# its bottom, and BOTH are no-ops except in the file tclsh was actually given:
# a shared fixture (tracks/tracks.tcl) is `source`d by 40 flows and must not
# start a second engine or report a second result.  That is what makes one
# generated shape work for a top-level flow and an included fixture alike.
#
# `corpus::end` prints ONE machine-readable line:
#
#     QOR {"flow": "flow/rnr/mix.buda", "overlaps": 0, ...}
#
# in the schema `tools/qor_corpus.py --out` writes, so the existing
# `qor_corpus.py --compare` diffs a Tcl sweep against a CLI sweep with no
# second comparison tool to keep in step.
# ============================================================================

# 8.5, matching `tools/buda.tcl` — the bridge this harness drives — and NOT
# 8.6, which it asked for and never used.  The difference is not academic:
# macOS ships Tcl **8.5.9** as /usr/bin/tclsh and has for over a decade, so
# `package require Tcl 8.6` failed the whole corpus on any stock Mac, and
# `bin/bb test` with it — measured on an Apple Silicon runner, the one test
# failure in an otherwise clean 2353-pass fast tier:
#
#   version conflict for package "Tcl": have 8.5.9, need 8.6
#
# The harness uses no 8.6 construct (no lmap, try, dict map, oo, coroutine,
# zlib, file tempfile, chan pipe), and neither does any corpus flow — the
# requirement was declared, not earned.  Raise it again only alongside the
# feature that needs it, and expect that to cost macOS its Tcl corpus.
#
# The trailing `-` is the OTHER direction of the same mistake: without it,
# `require Tcl 8.5` means "8.x only" and refuses Tcl 9 outright — measured
# on the first Tcl 9.0.4 install to point at BUDA (a Windows box, 2026-08),
# where this line was the whole failure.  `8.5-` = 8.5 or ANY later major.
package require Tcl 8.5--

namespace eval corpus {
    variable top ""          ;# the file tclsh was given (the one that reports)
    variable root ""         ;# this directory (fixtures resolve against it)
    variable origin ""       ;# its .buda original, for the QOR line
    variable t0 0
    variable repo ""
}

# Both anchors are captured HERE, when the harness is sourced — which happens
# before `corpus::begin` changes directory.  After that cd, `file normalize` of
# any relative path (including `[info script]`) resolves against the flow's
# directory instead of the corpus, so anything computed later would be wrong.
set corpus::root [file dirname [file normalize [info script]]]
# The repo is found by WALKING UP for tools/buda.tcl rather than counting
# directories: a fixed `dirname` depth silently produces a wrong path the
# moment this harness sits anywhere but flow/tcl/corpus/ — which is exactly
# where a test that generates a throwaway corpus puts it.  BUDA_REPO is the
# escape hatch for that case.
set corpus::repo ""
set _p $corpus::root
while {1} {
    if {[file exists [file join $_p tools buda.tcl]]} { set corpus::repo $_p; break }
    set _up [file dirname $_p]
    if {$_up eq $_p} break
    set _p $_up
}
if {$corpus::repo eq "" && [info exists ::env(BUDA_REPO)]} {
    set corpus::repo $::env(BUDA_REPO)
}
if {$corpus::repo eq ""} {
    error "corpus harness: no tools/buda.tcl above [info script] (set BUDA_REPO)"
}

proc corpus::begin {script origin_buda} {
    variable top
    variable origin
    variable t0
    variable repo
    # Already running: this is a sourced fixture, not the top-level flow.
    if {$top ne ""} { return }
    # The RAW string, not a normalized path: `corpus::end` runs after the cd
    # below, where normalizing a relative script path gives a different answer
    # than it did here — and the top-level file would then fail to recognise
    # itself and report nothing at all.  `[info script]` hands both calls the
    # identical string, so comparing that is exact and cd-proof.
    set top $script
    set origin $origin_buda

    # `uplevel #0`: sourcing inside a proc would evaluate buda.tcl in THIS
    # namespace and define the whole package as ::corpus::buda::*.  A package
    # belongs at global scope no matter who loads it.
    uplevel #0 [list source [file join $repo tools buda.tcl]]

    # The cd comes BEFORE start, and the order is load-bearing: the engine is
    # a CHILD PROCESS, and a child inherits the working directory it was
    # SPAWNED in — a later `cd` here moves this interpreter and leaves the
    # engine where it was.  The .buda side resolves a relative path against
    # the script's own directory; the Tcl side has no script running, so the
    # engine resolves against its CWD, and only sitting in the flow's
    # directory before spawning makes the two rules agree.  (Found by this
    # corpus: `open_bdb mix.bdb.sql` looked for the BDB at the repo root.)
    cd [file dirname [file join $repo $origin_buda]]

    # -echo 0: a corpus flow prints megabytes of planner detail, and the only
    # thing this harness's stdout is FOR is the QOR line a sweep parses.  The
    # output still travels the pipe (buda::output holds it), so the engine
    # side is exercised identically — this suppresses only the terminal echo.
    #
    # -viz 0: 31 of the 41 corpus flows end in `visualize`, and a window
    # BLOCKS the run until it is closed.  The sweep is a batch measurement,
    # so it says so — the same declaration `qor_corpus.py` makes with
    # `no_viz` on the CLI side, which is also what keeps the two drivers
    # comparable.  Without it a sweep on a machine with a display would
    # simply stop at the first flow's viewer.
    buda::start -echo 0 -viz 0
    set t0 [clock milliseconds]
}

# The measurement.  Deliberately the SAME three metrics qor_corpus.py gates on
# — overlaps, unplaced, and the number of bundles check_design faults — plus
# the two wirelengths as the fine-grained fingerprint: two runs can agree on
# three integers by luck, and cannot agree on the wirelength by luck.
proc corpus::end {script {forced -1}} {
    variable top
    variable origin
    variable t0
    # `forced` >= 0 means an `exit` in the original brought us here, and an
    # exit ends the run wherever it appears — so the top-file guard applies
    # only to the fell-off-the-end case.
    if {$top eq "" || ($forced < 0 && $script ne $top)} { return }

    set ov [buda::query overlaps]
    set un [buda::query unplaced]
    set vb [corpus::_viol_bundles]
    lassign [corpus::_wirelengths] awl dwl
    set sec [format %.1f [expr {([clock milliseconds] - $t0) / 1000.0}]]

    corpus::_shutdown 0
    puts [format {QOR {"flow": "%s", "overlaps": %s, "unplaced": %s, "viol_bundles": %s, "abstract_wl": %s, "detailed_wl": %s, "sec": %s}} \
              $origin [corpus::_json $ov] [corpus::_json $un] \
              [corpus::_json $vb] [corpus::_json $awl] [corpus::_json $dwl] $sec]
    flush stdout
}

# `exit` in the original, from ANYWHERE — including inside a sourced fixture.
# It never returns, because in the CLI `exit` raises SystemExit and unwinds
# the whole run: a Tcl `return` would only leave the sourced file and let the
# parent keep issuing commands the original never reached.
#
# A non-zero code is a fail-fast, not an ending: the run did not reach a
# measurable state, and saying so is the point of BUDA's own FATAL contract.
proc corpus::finish {script code} {
    variable origin
    if {$code == 0} {
        corpus::end $script $code
        exit 0
    }
    corpus::_shutdown $code
    puts [format {QOR {"flow": "%s", "err": "exit(%s)"}} $origin $code]
    flush stdout
    exit 1
}

# End the engine session the way a `.buda` run ends it.
#
# `buda::stop` alone is NOT that: it sends the server's `__exit`, which is
# answered by closing the session without ever running the script-level
# `exit` handler — and that handler is where `_flush_bdb_writeback` persists
# an `open_bdb ... writeback` fixture.  Stopping without it would discard the
# modified BDB while this harness printed a successful row (Codex P2 on
# #686).  Issuing the real command flushes; the catch is because a non-zero
# code comes back as FATAL, which the bridge correctly raises.
proc corpus::_shutdown {code} {
    if {$code == 0} {
        catch {buda::exit}
    } else {
        catch {buda::exit $code}
    }
    catch {buda::stop}
}

# `check_design` with no argument audits the deepest completed stage and is
# read-only.  Its own summary line is parsed — the same string qor_corpus.py
# reads — so the number means exactly what the CLI reports rather than being a
# second, subtly different count.  It reports violations by PRINTING them, and
# the bridge turns a printed `Error:` into a Tcl error, so the call is caught
# and the captured output read either way.
proc corpus::_viol_bundles {} {
    catch {buda::check_design}
    set out [buda::output]
    if {[string match {*no violations found*} $out]} { return 0 }
    if {[regexp {across ([0-9]+) bundle\(s\)} $out -> n]} { return $n }
    return null
}

# Abstract (after NUTS) and detailed (after DNUTS) wirelength, read off
# `report_wirelength`'s own totals.  `null` when that stage never ran, which is
# the convention the rest of the corpus schema uses — a flow that stopped
# before NUTS must not be indistinguishable from one that routed zero length.
proc corpus::_wirelengths {} {
    catch {buda::report_wirelength}
    set out [buda::output]
    set awl null
    set dwl null
    regexp {total abstract WL = ([0-9]+)} $out -> awl
    regexp {total detailed WL = ([0-9]+)} $out -> dwl
    return [list $awl $dwl]
}

proc corpus::_json {v} {
    if {$v eq "" || $v eq "null"} { return null }
    return $v
}
