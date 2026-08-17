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
#
# BUDA from Tcl (Phase 5 of docs/internal/lefdef_interface_plan.md).
#
# Source this into YOUR tclsh -- that is the point.  BUDA becomes a set of
# commands inside a flow script that keeps its own variables, procs and
# libraries, instead of the flow having to run underneath BUDA's own script
# language.  tools/buda_server.py explains why the process arrangement is
# this way round and documents the pipe protocol.
#
#     source tools/buda.tcl
#     buda::start
#     buda::def_layer 4 M4 H TOP 30
#     foreach b {a b c} { buda::add_block $b 0 0 100 100 }
#     buda::run_bundler STRICT
#     if {[buda::query bundles] == 0} { error "nothing bundled" }
#     buda::stop
#
# Every command in BUDA's registry is available as buda::<name>, discovered
# from the running engine at start -- there is no list here to fall out of
# date.  Arguments are joined with spaces and parsed by the same handler the
# .buda script uses, so a command means exactly what it means in a script.
#
# Errors follow Tcl's convention rather than BUDA's: a command that fails
# raises, so `catch` works and an unhandled failure stops the flow.  That is
# a deliberate difference from a .buda script, where most handlers report by
# printing `Error: ...` and carry on -- inside a flow, silently continuing
# past a failed step is how a wrong result gets shipped.

# `8.5-`, dash included: without it this means "8.x only" and refuses
# Tcl 9 at line 1 of the session — measured on the first Tcl 9.0.4
# install to point at BUDA (a Windows box, 2026-08), where the pin and
# ONE relative namespace write below were the entire port.
package require Tcl 8.5-

# `::buda`, absolute: a package must land in the GLOBAL namespace
# however it is loaded.  Sourced from inside another namespace — a
# GUI doing `namespace eval myapp { source buda.tcl }`, or the
# corpus harness — a RELATIVE name would define the whole package as
# ::myapp::buda::* and the first command would fail somewhere far
# from the cause.
namespace eval ::buda {
    variable fh ""
    variable output ""
    variable echo 1
    variable commands {}
    variable saved_stdout_encoding ""
    variable progress_cb ""      ;# buda::onprogress — invoked per OUT chunk
    # Async state (buda::async / buda::wait / buda::cancel): `async_done`
    # is "" while a command is in flight, then the final status; the
    # fileevent reader assembles frames through `async_mode`/`async_need`.
    variable async_done "DONE"
    variable async_status ""
    variable async_cb ""
    variable async_mode header
    variable async_need 0
    variable async_buf ""
    # The names the BRIDGE owns.  `_define` mints one proc per registry
    # command, and a registry name colliding with one of these — `viz`, say,
    # added one day beside the existing `visualize` — would REPLACE the
    # bridge's own proc with a wrapper that sends it to the engine as a
    # script command.  The flow would then fail far from the cause, so the
    # collision is refused loudly instead (`buda::do <name>` still reaches
    # the engine command).  Listed rather than snapshotted from
    # `info procs`, which would also capture the generated procs if this
    # file were ever sourced twice.  Pinned by the test suite.
    variable reserved {start stop query output commands stream viz vizfinal
                       log endreport onprogress async wait running cancel do}
    # Captured HERE, at source time.  Inside a proc `info script` names the
    # script being RUN, not the one the proc was defined in, so resolving the
    # server relative to it would look for it beside the site's flow script.
    variable dir [file dirname [file normalize [info script]]]
}

# Start the engine.  `-python` picks the interpreter, `-echo 0` keeps command
# output out of the terminal (it stays available as [buda::output]), and
# `-viz 0` declares this a batch flow: `visualize` then reports itself
# skipped instead of blocking on a window (see `buda::viz`).
proc ::buda::start {args} {
    variable fh
    variable commands
    variable echo
    variable dir
    if {$fh ne ""} { error "buda::start: already started" }

    set python "python3"
    set server [file join $dir buda_server.py]
    set viz ""
    foreach {k v} $args {
        switch -- $k {
            -python { set python $v }
            -server { set server $v }
            -echo   { set echo $v }
            -viz    { set viz $v }
            default { error "buda::start: unknown option $k" }
        }
    }
    if {![file exists $server]} { error "buda::start: no server at $server" }

    # Absolute `::buda::`, not `buda::`: inside this proc the relative
    # spelling names ::buda::buda::async_done, which Tcl 8 quietly resolved
    # through its global fallback and Tcl 9 (TIP 278) refuses with "parent
    # namespace doesn't exist" — the one line in the bridge that broke on 9.
    set ::buda::async_done "DONE"
    set fh [open "|[list $python $server] 2>@stderr" r+]
    # lf + utf-8, NOT binary: the payload length is counted in CHARACTERS on
    # both sides, so the diagnostics' non-ASCII text survives intact.
    fconfigure $fh -translation lf -encoding utf-8 -buffering line -blocking 1

    # BUDA's diagnostics contain non-ASCII (`→`, `µm`, box-drawing rules).
    # On a host with no locale set, tclsh's stdout defaults to iso8859-1 and
    # every one of those becomes `?` -- a tool that looks corrupt in the log
    # it just wrote.  We chose to emit UTF-8, so we take responsibility for
    # the channel we echo it on: it is set here and restored by buda::stop.
    buda::_use_utf8_stdout

    set commands [buda::_request "__commands"]
    foreach name $commands { buda::_define $name }
    # Only when asked: the engine's own default is on, and sending nothing
    # keeps `buda::start` byte-identical on the wire for existing flows.
    if {$viz ne ""} { buda::viz $viz }
    # -v via the launcher: turn on viz-final — a viewer on the finished design
    # when the flow ends, even though the flow has no `visualize`.  Read here so
    # a flow only has to be LAUNCHED with -v, never edited.
    #
    # The signal is an ENVIRONMENT VARIABLE (`btcl -v` sets it), deliberately
    # NOT a token in `$argv`.  Tcl is a general language and `-v` is an ordinary
    # flag for a flow to want, so scanning argv for one cannot tell a launcher's
    # request from the flow's own argument: `btcl flow.tcl -v` opened a viewer
    # nobody had asked this wrapper for, and no amount of fixing the wrapper's
    # own parsing repairs that — the misreading is here.  Without a wrapper,
    # `BUDA_VIZ_FINAL=1 tclsh flow.tcl` says the same thing unambiguously, and
    # an embedder calls `buda::vizfinal on` directly.
    #
    # Unset (or off) sends nothing, so this stays byte-identical on the wire.
    if {[info exists ::env(BUDA_VIZ_FINAL)]
        && [string tolower $::env(BUDA_VIZ_FINAL)] ni {"" 0 off false no}} {
        buda::vizfinal on
    }
    return $commands
}

proc ::buda::stop {} {
    variable fh
    if {$fh ne ""} {
        # __exit may carry the -v viewer's output (a headless BUDA-1903 note,
        # or nothing once a window has opened and been closed).  `_request`
        # does not echo INTERNAL (`__…`) replies — they are return values — so
        # surface this one here rather than let the viewer run silently.  An
        # ordinary stop returns "" and prints nothing (byte-identical).
        if {![catch {buda::_request "__exit"} out] && [string trim $out] ne ""} {
            puts $out
        }
        catch {close $fh}
        set fh ""
    }
    buda::_restore_stdout
}

proc ::buda::_use_utf8_stdout {} {
    variable saved_stdout_encoding
    if {$saved_stdout_encoding ne ""} { return }
    if {[catch {fconfigure stdout -encoding} enc]} { return }
    # Leave ANY Unicode-capable encoding alone -- this shim exists only to
    # lift a BYTE-locale default (cp1252, iso8859-*) to utf-8.  utf-8 was
    # the original skip; Tcl 9 on a Windows CONSOLE reports utf-16, the
    # channel feeding the Unicode console API two bytes per code unit, so
    # forcing utf-8 there made every ASCII byte PAIR render as one CJK
    # glyph (measured on the first Tcl 9.0.4 home box: the whole flow log
    # arrived as fluent nonsense with a clean design underneath it).
    if {$enc eq "utf-8" || $enc eq "unicode"
        || [string match -nocase "utf-16*" $enc]
        || [string match -nocase "ucs-2*" $enc]} { return }
    set saved_stdout_encoding $enc
    catch {fconfigure stdout -encoding utf-8}
}

proc ::buda::_restore_stdout {} {
    variable saved_stdout_encoding
    if {$saved_stdout_encoding eq ""} { return }
    catch {fconfigure stdout -encoding $saved_stdout_encoding}
    set saved_stdout_encoding ""
}

# One scalar about the session -- `bundles`, `blocks`, `nets`, `overlaps`,
# `unplaced`, `messages`.  This is why a flow is worth driving from Tcl at
# all: a command that RETURNS a value can be branched on.
proc ::buda::query {name} {
    return [string trim [buda::_request "__query $name"]]
}

# The output of the last command, echoed or not.
proc ::buda::output {} {
    variable output
    return $output
}

proc ::buda::commands {} {
    variable commands
    return $commands
}

# Summarize the run the way `bin/buda` does: full per-command detail to the
# flow log beside the flow (`<dir>/log/<stem>_flow.log`), ONE line per command
# on the terminal.  `buda::log <flow>` arms it, `buda::log off` disarms, and a
# bare `buda::log` reports the path ("" when off); each form returns the path.
#
# Opt-in, and deliberately not what `buda::start` does: a Tcl flow issues its
# commands one at a time and usually wants to see each one's output, so turning
# this on by default would change every existing flow's console.  A driver that
# runs a WHOLE .buda flow in one go wants the other shape — `btcl -i` arms it —
# because the alternative is the flow's entire output in the scrollback and no
# file to read the detail in afterwards.
#
# Disarm around anything whose output IS the point (`dump_topologies` at a
# prompt): while armed, that output goes to the log and the terminal gets the
# one-line abstract.  A re-arm appends, so one session is one log file.
proc ::buda::log {{arg ""}} {
    return [string trim [buda::_request [string trim "__log $arg"]]]
}

# The runtime summary + "Full per-command detail → <path>" line `bin/buda`
# prints when a run ends.  Once per session (the engine's own guard), so a
# driver may call it after the flow without worrying about later replans.
proc ::buda::endreport {} {
    set out [buda::_request "__end_report"]
    if {[string trim $out] ne ""} { puts -nonewline $out ; flush stdout }
    return $out
}

# Stream command output as it is produced (OUT frames) instead of one blob
# at the end.  What a GUI wants; `buda::output` is identical either way.
proc ::buda::stream {onoff} {
    buda::_request [expr {$onoff ? "__stream on" : "__stream off"}]
    return $onoff
}

# May `visualize` open a window?  With no argument, what the setting is.
#
# On by default, because a command must mean from Tcl what it means in a
# `.buda` script — and there it opens a viewer.  Note what that implies: the
# window BLOCKS this interpreter until it is closed, exactly as it blocks a
# script run.  A GUI that wants to stay live sends it with `buda::async
# visualize`; a batch flow turns it off (`buda::start -viz 0`) and every
# `visualize` then reports itself skipped (BUDA-1903) instead of costing a
# viewer nobody is watching.
proc ::buda::viz {{onoff ""}} {
    if {$onoff eq ""} { return [string trim [buda::_request "__viz"]] }
    set mode [expr {[string is boolean -strict $onoff] ? \
                    ($onoff ? "on" : "off") : $onoff}]
    return [string trim [buda::_request "__viz $mode"]]
}

# The -v twin: at buda::stop, open a viewer on the FINISHED design even when
# the flow never called `visualize` (unless it already ended by visualizing) —
# for eyeballing a Tcl test vehicle without editing it.  Turned on for you by
# `btcl -v <flow>.tcl` (buda::start reads a bare `-v` from $::argv); a GUI or
# embedder can also set it directly.  With no argument, reports the setting.
proc ::buda::vizfinal {{onoff ""}} {
    if {$onoff eq ""} { return [string trim [buda::_request "__viz_final"]] }
    set mode [expr {[string is boolean -strict $onoff] ? \
                    ($onoff ? "on" : "off") : $onoff}]
    return [string trim [buda::_request "__viz_final $mode"]]
}

# `buda::onprogress {cmdprefix}` — invoked (at global level) with each chunk
# of command output as it arrives, sync or async.  Empty string clears it.
proc ::buda::onprogress {cb} {
    variable progress_cb
    set progress_cb $cb
}

# ── async: the event-loop face of the same protocol ────────────────────────
#
# `buda::async <command> ?-done cmdprefix?` sends the command and RETURNS
# immediately; frames are assembled by a fileevent reader, so the caller's
# event loop (a Tk GUI, typically) stays live while the engine works.  The
# -done callback is invoked at global level with two arguments, the final
# status (OK/ERR/BYE/FATAL) and the full output — a failure is an argument,
# not a raise, because there is no caller on the stack to catch it.
#
# `buda::wait` blocks (vwait) until the in-flight command finishes and
# returns its status.  `buda::cancel` sends SIGINT to the engine — Python
# turns it into KeyboardInterrupt at the next bytecode boundary and the
# command fails as an ordinary ERR with the session alive.  Cooperative by
# design: a Python-level loop cancels promptly, one long C++ call returns
# first.  POSIX only (it needs `exec kill`); on other hosts it errors.

proc ::buda::async {cmd args} {
    variable fh
    variable output
    variable async_done
    variable async_status
    variable async_cb
    variable async_mode
    variable async_need
    variable async_buf
    if {$fh eq ""} { error "buda: not started -- call buda::start first" }
    if {$async_done eq ""} { error "buda: a command is already running" }
    set async_cb ""
    foreach {k v} $args {
        switch -- $k {
            -done   { set async_cb $v }
            default { error "buda::async: unknown option $k" }
        }
    }
    set output ""
    set async_done ""
    set async_status ""
    set async_mode header
    set async_need 0
    set async_buf ""
    puts $fh $cmd
    flush $fh
    fconfigure $fh -blocking 0
    fileevent $fh readable [list buda::_on_readable]
    return
}

proc ::buda::wait {} {
    variable async_done
    if {$async_done eq ""} { vwait ::buda::async_done }
    return $async_done
}

proc ::buda::running {} {
    variable async_done
    return [expr {$async_done eq ""}]
}

proc ::buda::cancel {} {
    variable fh
    variable async_done
    if {$fh eq "" || $async_done ne ""} { return 0 }
    # `pid` on a command pipeline returns the child process ids.
    set pids [pid $fh]
    if {[catch {exec kill -INT [lindex $pids 0]} err]} {
        error "buda::cancel: cannot signal the engine ($err) --\
               cancellation needs a POSIX kill"
    }
    return 1
}

proc ::buda::_async_finish {status} {
    variable fh
    variable output
    variable async_done
    variable async_status
    variable async_cb
    if {$fh ne ""} {
        catch {fileevent $fh readable {}}
        catch {fconfigure $fh -blocking 1}
    }
    # DEAD closes too: the engine is gone, and an EOF-ready channel left
    # open keeps rescheduling the reader against a dead pipe.
    if {$status in {BYE FATAL DEAD}} {
        catch {close $fh}
        set fh ""
    }
    set async_status $status
    # Completion is recorded BEFORE the callback: the callback may issue
    # sync commands against the finished session or launch the next async,
    # both of which the in-flight guard would otherwise refuse — and a
    # raising callback must not leave buda::running true forever.  The
    # buda::wait guarantee survives the reorder for free: vwait cannot
    # resume until this event handler RETURNS, which is after the callback.
    set async_done $status
    if {$async_cb ne ""} {
        # Caught, not propagated, for the same reason as in _on_readable:
        # this runs inside the fileevent handler, and if the callback
        # launched the NEXT async (legal — completion is recorded above),
        # a raise here would make Tcl delete the channel's CURRENT
        # readable handler, i.e. the new request's.  The error still
        # reaches bgerror, from idle time.
        if {[catch {uplevel #0 [linsert $async_cb end $status $output]} m o]} {
            after idle [list ::buda::_rethrow $m $o]
        }
    }
}

proc ::buda::_on_readable {} {
    variable fh
    variable output
    variable async_mode
    variable async_need
    variable async_buf
    variable async_status
    # Assemble frames from whatever arrived; a header or payload may be
    # split across reads, so this is a small resumable state machine.
    # The EOF branches finish through _async_finish with fh still set —
    # clearing it first would leave the fileevent registered and the
    # channel open, so the dead pipe keeps rescheduling this reader.
    while {1} {
        if {$async_mode eq "header"} {
            if {[gets $fh line] < 0} {
                if {[eof $fh]} { buda::_async_finish DEAD }
                return                          ;# partial line -- wait
            }
            set async_status [lindex $line 0]
            set async_need [lindex $line 1]
            set async_buf ""
            set async_mode payload
        }
        if {$async_need > 0} {
            set got [read $fh $async_need]
            append async_buf $got
            set async_need [expr {$async_need - [string length $got]}]
            if {$async_need > 0} {
                if {[eof $fh]} { buda::_async_finish DEAD }
                return                          ;# more payload to come
            }
        }
        # One whole frame.  The parser advances BEFORE the chunk is
        # delivered, and a raising progress callback is caught and
        # re-raised from an IDLE callback rather than propagated: Tcl
        # DELETES a fileevent handler whose script errors, which would
        # silently unsubscribe this reader and hang the request forever —
        # the error still reaches bgerror (the event-loop convention), the
        # frame is never delivered twice, and a final frame still
        # finishes.
        set st $async_status
        set chunk $async_buf
        set async_mode header
        set async_buf ""
        append output $chunk
        if {[catch {buda::_deliver $chunk} dmsg dopts]} {
            after idle [list ::buda::_rethrow $dmsg $dopts]
        }
        if {$st ne "OUT"} {
            buda::_async_finish $st
            return
        }
    }
}

# Re-raise a deferred callback error at idle time, where an error reaches
# bgerror without costing an event subscription.
proc ::buda::_rethrow {msg opts} {
    return -options $opts $msg
}

# ── the wire ───────────────────────────────────────────────────────────────

proc ::buda::_request {line} {
    variable fh
    variable output
    variable async_done
    if {$fh eq ""} { error "buda: not started -- call buda::start first" }
    if {$async_done eq ""} {
        error "buda: a command is already running (buda::async) --\
               buda::wait or buda::cancel first"
    }
    puts $fh $line
    flush $fh

    # A reply is zero or more OUT frames (streaming mode), then exactly one
    # final status frame.  `output` accumulates them all, so it is the same
    # whole text in either mode — and the error message below is built from
    # the ACCUMULATED output, since in stream mode the final frame carries
    # only the not-yet-streamed tail.
    set output ""
    set internal [string match "__*" $line]
    while {1} {
        if {[gets $fh header] < 0} {
            set fh ""
            error "buda: the engine exited unexpectedly"
        }
        set status [lindex $header 0]
        set n [lindex $header 1]
        set payload ""
        if {$n > 0} { set payload [read $fh $n] }
        append output $payload
        if {!$internal} { buda::_deliver $payload }
        if {$status ne "OUT"} { break }
    }
    switch -- $status {
        OK  { return $output }
        ERR { error [string trim $output] }
        BYE {
            catch {close $fh}
            set fh ""
            return $output
        }
        FATAL {
            # The command ended the session AND failed.  Both halves have to
            # be reported: closing quietly would let the flow run on and
            # blame the next command for "the engine exited unexpectedly".
            catch {close $fh}
            set fh ""
            error [string trim $output]
        }
        default { error "buda: bad response header '$header'" }
    }
}

# Echo and/or hand a chunk to the site's progress callback.  One place, so
# the sync and async paths cannot drift on what "seeing output" means.
proc ::buda::_deliver {chunk} {
    variable echo
    variable progress_cb
    if {$chunk eq ""} { return }
    if {$echo} {
        puts -nonewline $chunk
        flush stdout
    }
    if {$progress_cb ne ""} {
        uplevel #0 [linsert $progress_cb end $chunk]
    }
}

# Send a raw command line — the generic form of every `buda::<name>` proc,
# and the way to reach a registry command whose name the bridge already
# owns (see `reserved` above).  Also what a GUI wants when the command is
# in a variable rather than in the source text.
proc ::buda::do {args} {
    return [buda::_request [string trim [join $args " "]]]
}

proc ::buda::_define {name} {
    variable reserved
    if {[lsearch -exact $reserved $name] >= 0} {
        # The bridge's own proc wins, and says so.  Silently overwriting it
        # would break the bridge from inside — `buda::stop` or `buda::query`
        # would start sending themselves to the engine as script commands —
        # and the flow would fail somewhere far from the cause.
        puts stderr "buda: engine command '$name' collides with the bridge's\
 own buda::$name — call it as \[buda::do $name ...\]"
        return
    }
    # The handlers split on whitespace, so arguments are joined and not
    # re-quoted: a command must mean the same thing from Tcl as from a .buda
    # script, and adding quoting here would silently make it mean something
    # else.  A Tcl list argument therefore arrives as its space-joined
    # elements, which is what {a b c} already looks like to the parser.
    proc ::buda::$name {args} [string map [list @NAME@ [list $name]] {
        return [buda::_request [string trim "@NAME@ [join $args { }]"]]
    }]
}
