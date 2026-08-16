# btcl.ps1 — run a Tcl flow that drives BUDA (PowerShell twin of bin/btcl).
#
#   btcl.ps1 flow.tcl [args...]        # run the flow
#   btcl.ps1 -v flow.tcl [args...]     # ...and pop a viewer when the flow ends
#   btcl.ps1 -j N flow.tcl [args...]   # ...with N worker threads (Tcl twin of
#                                      # `buda -j`; travels as BUDA_THREADS_REQUEST).
#                                      # `-j max` = the whole maximum; no -j =
#                                      # half the maximum, as `buda` caps.
#   btcl.ps1 -- -v.tcl                 # a script whose NAME starts with a dash
#
# WRAPPER OPTIONS ARE READ ONLY BEFORE THE SCRIPT — the first non-option word
# is the script, and everything from it onward (including any -v of the flow's
# OWN) passes through untouched, in its original position.  `--` ends option
# parsing explicitly.  The viewer request travels as the BUDA_VIZ_FINAL
# environment variable, never as an injected argv token — same contract as
# bin/btcl, where the rationale is documented in full.

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Sep = [IO.Path]::PathSeparator

$parts = @()
$rel = Join-Path $ProjectRoot 'build/Release'
if (Test-Path -LiteralPath $rel) { $parts += $rel }   # VS multi-config layout (Codex P2 #735)
$parts += @((Join-Path $ProjectRoot 'build'), (Join-Path $ProjectRoot 'tools'))
if ($env:PYTHONPATH) { $parts += $env:PYTHONPATH }
$env:PYTHONPATH = $parts -join $Sep

# Every slice below is wrapped in @(): assigning an if-expression's output
# UNWRAPS a single-element result to a bare string, after which $rest[0] is
# the first CHARACTER and `tclsh @rest` splats one argument PER CHARACTER —
# measured on windows-validate (PR #735): `btcl.ps1 -v flow.tcl` handed
# tclsh the argument "C" (from C:\...) and every flagged invocation failed
# with `couldn't read file "C"`, while the multi-arg `--` form stayed an
# array and passed.  The @() must wrap the WHOLE if-expression: wrapping
# only the inner slice still unwraps, because the enumeration happens when
# the if's output is assigned (verified with pwsh 7.4 both ways).
# A wrapper-level error exits 2 — the same status bin/btcl uses.  It must NOT
# go through Write-Error: $ErrorActionPreference is 'Stop', so Write-Error
# RAISES a terminating error and PowerShell exits 1 before `exit 2` is reached.
# Write straight to stderr instead, matching the Bash wrapper's `echo … >&2`.
function Fail2($msg) { [Console]::Error.WriteLine($msg); exit 2 }

# A thread request is an integer (optionally signed) or the literal `max`.  A
# negative/out-of-range count is ACCEPTED, not rejected — buda_cli clamps it
# LOUD to [1, max] and `buda -j` does the same, so rejecting here would break
# the two launchers' identical `-j` semantics.
$reThreads = '^(-?\d+|max)$'

$rest = @() + $args
while ($rest.Count -gt 0) {
    $a = $rest[0]
    if ($a -in '-v', '--visualize') {
        $env:BUDA_VIZ_FINAL = '1'
        $rest = @(if ($rest.Count -gt 1) { $rest[1..($rest.Count - 1)] } else { @() })
    } elseif ($a -in '-j', '--threads') {
        # The value is NOT a path — it travels to the child engine as an env
        # var (same shape as BUDA_VIZ_FINAL); buda_server resolves it through
        # buda_cli so `btcl -j` means exactly what `buda -j` means.  An integer
        # or the literal `max`.
        if ($rest.Count -lt 2) { Fail2 "btcl: $a requires a thread count" }
        $val = $rest[1]
        if ($val -notmatch $reThreads) { Fail2 "btcl: ${a}: invalid thread count '$val' (an integer or 'max')" }
        $env:BUDA_THREADS_REQUEST = $val
        $rest = @(if ($rest.Count -gt 2) { $rest[2..($rest.Count - 1)] } else { @() })
    } elseif ($a -like '-j*' -or $a -like '--threads=*') {
        $val = if ($a -like '-j*') { $a.Substring(2) } else { $a.Substring('--threads='.Length) }
        if ($val -notmatch $reThreads) { Fail2 "btcl: invalid thread count '$val' (an integer or 'max')" }
        $env:BUDA_THREADS_REQUEST = $val
        $rest = @(if ($rest.Count -gt 1) { $rest[1..($rest.Count - 1)] } else { @() })
    } elseif ($a -eq '--') {
        $rest = @(if ($rest.Count -gt 1) { $rest[1..($rest.Count - 1)] } else { @() })
        break
    } elseif ($a -in '-h', '--help') {
        # The header block IS the help, read to the first non-comment line.
        foreach ($line in (Get-Content $PSCommandPath | Select-Object -Skip 1)) {
            if ($line -notmatch '^#') { break }
            Write-Output ($line -replace '^# ?', '')
        }
        exit 0
    } elseif ($a -like '-*') {
        Fail2 "btcl: unknown option '$a'"
    } else {
        break   # the script — parsing stops here
    }
}

# No -j?  Send the launcher DEFAULT so btcl caps like buda (half the machine
# maximum); a value the caller already exported wins.  Only the launchers
# impose this — a bare tclsh sends nothing and keeps the engine's auto count.
if (-not $env:BUDA_THREADS_REQUEST) { $env:BUDA_THREADS_REQUEST = 'default' }

# A script whose NAME begins with a dash is an OPTION to tclsh (it runs
# nothing and returns 0 — measured on 8.6.14, see bin/btcl).  Normalise the
# operand to a path tclsh cannot misread.
if ($rest.Count -gt 0 -and $rest[0] -like '-*') {
    $rest = @(".$([IO.Path]::DirectorySeparatorChar)$($rest[0])") + `
            $(@(if ($rest.Count -gt 1) { $rest[1..($rest.Count - 1)] } else { @() }))
}

tclsh @rest
exit $LASTEXITCODE
