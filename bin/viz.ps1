# DEF/BDB visualizer environment wrapper (PowerShell twin of bin/viz).
# Sets up PYTHONPATH before running the def_viz script.

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Sep = [IO.Path]::PathSeparator

if ($args.Count -gt 0 -and ($args[0] -in '-h', '--help')) {
    @'
Usage: viz.ps1 [file.def [file.lef]] [--ipc <name>]
       viz.ps1 [file.bdb] [--ipc <name>]

Open the interactive DEF/LEF/BDB net-cluster visualizer (Tk GUI).

Arguments:
  file.def [file.lef]   DEF to load (a sibling .lef is auto-found if omitted)
  file.bdb              a BDB to load instead of DEF/LEF
  --ipc <name>          selection-sync IPC channel to pair with buda_viz
                        (defaults to the input file's stem)

Note: selection-sync IPC (tools/viz_ipc.py) is Unix-only (AF_UNIX); on
native Windows the viewer works but --ipc pairing does not.

See also: docs/detailed_viz.md
'@ | Write-Output
    exit 0
}

$parts = @((Join-Path $ProjectRoot 'build'), (Join-Path $ProjectRoot 'tools'))
if ($env:PYTHONPATH) { $parts += $env:PYTHONPATH }
$env:PYTHONPATH = $parts -join $Sep

$py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
& $py (Join-Path $ProjectRoot 'tools/def_viz_o3.py') @args
exit $LASTEXITCODE
