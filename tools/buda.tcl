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

package require Tcl 8.5

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
    # Captured HERE, at source time.  Inside a proc `info script` names the
    # script being RUN, not the one the proc was defined in, so resolving the
    # server relative to it would look for it beside the site's flow script.
    variable dir [file dirname [file normalize [info script]]]
}

# Start the engine.  `-python` picks the interpreter, `-echo 0` keeps command
# output out of the terminal (it stays available as [buda::output]).
proc ::buda::start {args} {
    variable fh
    variable commands
    variable echo
    variable dir
    if {$fh ne ""} { error "buda::start: already started" }

    set python "python3"
    set server [file join $dir buda_server.py]
    foreach {k v} $args {
        switch -- $k {
            -python { set python $v }
            -server { set server $v }
            -echo   { set echo $v }
            default { error "buda::start: unknown option $k" }
        }
    }
    if {![file exists $server]} { error "buda::start: no server at $server" }

    set buda::async_done "DONE"
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
    return $commands
}

proc ::buda::stop {} {
    variable fh
    if {$fh ne ""} {
        catch {buda::_request "__exit"}
        catch {close $fh}
        set fh ""
    }
    buda::_restore_stdout
}

proc ::buda::_use_utf8_stdout {} {
    variable saved_stdout_encoding
    if {$saved_stdout_encoding ne ""} { return }
    if {[catch {fconfigure stdout -encoding} enc]} { return }
    if {$enc eq "utf-8"} { return }
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

# Stream command output as it is produced (OUT frames) instead of one blob
# at the end.  What a GUI wants; `buda::output` is identical either way.
proc ::buda::stream {onoff} {
    buda::_request [expr {$onoff ? "__stream on" : "__stream off"}]
    return $onoff
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

proc ::buda::_define {name} {
    # The handlers split on whitespace, so arguments are joined and not
    # re-quoted: a command must mean the same thing from Tcl as from a .buda
    # script, and adding quoting here would silently make it mean something
    # else.  A Tcl list argument therefore arrives as its space-joined
    # elements, which is what {a b c} already looks like to the parser.
    proc ::buda::$name {args} [string map [list @NAME@ [list $name]] {
        return [buda::_request [string trim "@NAME@ [join $args { }]"]]
    }]
}
