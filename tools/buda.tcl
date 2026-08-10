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

namespace eval buda {
    variable fh ""
    variable output ""
    variable echo 1
    variable commands {}
    variable saved_stdout_encoding ""
    # Captured HERE, at source time.  Inside a proc `info script` names the
    # script being RUN, not the one the proc was defined in, so resolving the
    # server relative to it would look for it beside the site's flow script.
    variable dir [file dirname [file normalize [info script]]]
}

# Start the engine.  `-python` picks the interpreter, `-echo 0` keeps command
# output out of the terminal (it stays available as [buda::output]).
proc buda::start {args} {
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

proc buda::stop {} {
    variable fh
    if {$fh ne ""} {
        catch {buda::_request "__exit"}
        catch {close $fh}
        set fh ""
    }
    buda::_restore_stdout
}

proc buda::_use_utf8_stdout {} {
    variable saved_stdout_encoding
    if {$saved_stdout_encoding ne ""} { return }
    if {[catch {fconfigure stdout -encoding} enc]} { return }
    if {$enc eq "utf-8"} { return }
    set saved_stdout_encoding $enc
    catch {fconfigure stdout -encoding utf-8}
}

proc buda::_restore_stdout {} {
    variable saved_stdout_encoding
    if {$saved_stdout_encoding eq ""} { return }
    catch {fconfigure stdout -encoding $saved_stdout_encoding}
    set saved_stdout_encoding ""
}

# One scalar about the session -- `bundles`, `blocks`, `nets`, `overlaps`,
# `unplaced`, `messages`.  This is why a flow is worth driving from Tcl at
# all: a command that RETURNS a value can be branched on.
proc buda::query {name} {
    return [string trim [buda::_request "__query $name"]]
}

# The output of the last command, echoed or not.
proc buda::output {} {
    variable output
    return $output
}

proc buda::commands {} {
    variable commands
    return $commands
}

# ── the wire ───────────────────────────────────────────────────────────────

proc buda::_request {line} {
    variable fh
    variable output
    variable echo
    if {$fh eq ""} { error "buda: not started -- call buda::start first" }
    puts $fh $line
    flush $fh

    if {[gets $fh header] < 0} {
        set fh ""
        error "buda: the engine exited unexpectedly"
    }
    set status [lindex $header 0]
    set n [lindex $header 1]
    set payload ""
    if {$n > 0} { set payload [read $fh $n] }
    set output $payload
    if {$echo && $payload ne "" && ![string match "__*" $line]} {
        puts -nonewline $payload
        flush stdout
    }
    switch -- $status {
        OK  { return $payload }
        ERR { error [string trim $payload] }
        BYE {
            catch {close $fh}
            set fh ""
            return $payload
        }
        FATAL {
            # The command ended the session AND failed.  Both halves have to
            # be reported: closing quietly would let the flow run on and
            # blame the next command for "the engine exited unexpectedly".
            catch {close $fh}
            set fh ""
            error [string trim $payload]
        }
        default { error "buda: bad response header '$header'" }
    }
}

proc buda::_define {name} {
    # The handlers split on whitespace, so arguments are joined and not
    # re-quoted: a command must mean the same thing from Tcl as from a .buda
    # script, and adding quoting here would silently make it mean something
    # else.  A Tcl list argument therefore arrives as its space-joined
    # elements, which is what {a b c} already looks like to the parser.
    proc ::buda::$name {args} [string map [list @NAME@ [list $name]] {
        return [buda::_request [string trim "@NAME@ [join $args { }]"]]
    }]
}
