# BUDA floorplanner environment wrapper (PowerShell twin of bin/fp).
# Sets up PYTHONPATH before running the floorplanner GUI.  The macOS
# Floorplanner.app relaunch is Darwin-only and not ported.

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Sep = [IO.Path]::PathSeparator

if ($args.Count -gt 0 -and ($args[0] -in '-h', '--help')) {
    @'
Usage: fp.ps1 [file.bdb]

Open the interactive BUDA Floorplanner (Tk/matplotlib GUI) to edit block
placement in a BDB and, optionally, launch the hier routing flow.

Arguments:
  file.bdb    BDB to open; omit for a blank canvas

See also:
  bin/bfp.ps1                      demo scenarios (tc1/tc2/tc3) and .buda flows
  docs/FLOORPLANNER_USER_GUIDE.md
'@ | Write-Output
    exit 0
}

$parts = @()
$rel = Join-Path $ProjectRoot 'build/Release'
if (Test-Path -LiteralPath $rel) { $parts += $rel }   # VS multi-config layout (Codex P2 #735)
$parts += @((Join-Path $ProjectRoot 'build'), (Join-Path $ProjectRoot 'tools'))
if ($env:PYTHONPATH) { $parts += $env:PYTHONPATH }
$env:PYTHONPATH = $parts -join $Sep

$py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
& $py (Join-Path $ProjectRoot 'tools/bdb_floorplanner.py') @args
exit $LASTEXITCODE
