# bfp.ps1 — BUDA Floorplanner launcher with built-in demo scenarios
# (PowerShell twin of bin/bfp).

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Sep = [IO.Path]::PathSeparator

$parts = @((Join-Path $ProjectRoot 'build'), (Join-Path $ProjectRoot 'tools'))
if ($env:PYTHONPATH) { $parts += $env:PYTHONPATH }
$env:PYTHONPATH = $parts -join $Sep

$py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
$fpTool = Join-Path $ProjectRoot 'tools/bdb_floorplanner.py'
$first = if ($args.Count -gt 0) { $args[0] } else { '' }

switch -Regex ($first) {
    '^(-h|--help)$' {
        @'
bfp — BUDA Floorplanner launcher with built-in demo scenarios.

Usage:
  bfp.ps1 tc1 [output.bdb]     create TC1 (40-block overlap storm), open in fp
  bfp.ps1 tc2 [output.bdb]     create TC2 (fixed io_pad + 39 free blocks), open in fp
  bfp.ps1 tc3 [output.bdb]     create TC3 (4 io_pads at corners, 40 free, multi-bit buses)
  bfp.ps1 path\to\file.bdb     open an existing BDB directly in fp
  bfp.ps1 path\to\script.buda  run bin/buda.ps1 <script>, then open the BDB it declared
  bfp.ps1                      open fp with no file (blank canvas)

See also: bin/fp.ps1, docs/FLOORPLANNER_USER_GUIDE.md
'@ | Write-Output
        exit 0
    }
    '^(tc1|tc2|tc3)$' {
        $demo = $first
        $out = if ($args.Count -gt 1) { $args[1] }
               else { Join-Path ([IO.Path]::GetTempPath()) "bfp_$demo.bdb" }
        & $py (Join-Path $ProjectRoot 'tools/fp_demo.py') $demo $out
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $py $fpTool $out
        exit $LASTEXITCODE
    }
    '\.buda$' {
        $script = (Resolve-Path -LiteralPath $first).Path
        $scriptDir = Split-Path -Parent $script
        # Same resolution as buda_cli.py: open_bdb paths are script-relative.
        $line = Select-String -LiteralPath $script -Pattern '^open_bdb ' |
                Select-Object -First 1
        if (-not $line) {
            Write-Error "bfp: no 'open_bdb' line found in $first"
            exit 1
        }
        $bdbRaw = ($line.Line -split '\s+')[1]
        $bdb = if ([IO.Path]::IsPathRooted($bdbRaw)) { $bdbRaw }
               else { Join-Path $scriptDir $bdbRaw }
        & (Join-Path $PSScriptRoot 'buda.ps1') $script
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $py $fpTool $bdb
        exit $LASTEXITCODE
    }
    default {
        if ($first -and -not (Test-Path -LiteralPath $first)) {
            Write-Error "bfp: file not found: $first"
            exit 1
        }
        & $py $fpTool @args
        exit $LASTEXITCODE
    }
}
