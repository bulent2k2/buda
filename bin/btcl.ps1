# btcl.ps1 — run a Tcl flow that drives BUDA (PowerShell twin of bin/btcl).
#
#   btcl.ps1 flow.tcl [args...]        # run the flow
#   btcl.ps1 -v flow.tcl [args...]     # ...and pop a viewer when the flow ends
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

$rest = @() + $args
while ($rest.Count -gt 0) {
    $a = $rest[0]
    if ($a -in '-v', '--visualize') {
        $env:BUDA_VIZ_FINAL = '1'
        $rest = if ($rest.Count -gt 1) { $rest[1..($rest.Count - 1)] } else { @() }
    } elseif ($a -eq '--') {
        $rest = if ($rest.Count -gt 1) { $rest[1..($rest.Count - 1)] } else { @() }
        break
    } elseif ($a -in '-h', '--help') {
        # The header block IS the help, read to the first non-comment line.
        foreach ($line in (Get-Content $PSCommandPath | Select-Object -Skip 1)) {
            if ($line -notmatch '^#') { break }
            Write-Output ($line -replace '^# ?', '')
        }
        exit 0
    } elseif ($a -like '-*') {
        Write-Error "btcl: unknown option '$a'"
        exit 2
    } else {
        break   # the script — parsing stops here
    }
}

# A script whose NAME begins with a dash is an OPTION to tclsh (it runs
# nothing and returns 0 — measured on 8.6.14, see bin/btcl).  Normalise the
# operand to a path tclsh cannot misread.
if ($rest.Count -gt 0 -and $rest[0] -like '-*') {
    $rest = @(".$([IO.Path]::DirectorySeparatorChar)$($rest[0])") + `
            $(if ($rest.Count -gt 1) { $rest[1..($rest.Count - 1)] } else { @() })
}

tclsh @rest
exit $LASTEXITCODE
