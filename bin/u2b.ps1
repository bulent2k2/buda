# u2b.ps1 — turn a topology unit test into a runnable .buda script and open it
# in the interactive topology explorer (PowerShell twin of bin/u2b).

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if ($args.Count -gt 0 -and ($args[0] -in '-h', '--help')) {
    @'
Usage: u2b.ps1 <test_name> [unit2buda opts]

Turn a topology (or CLI/BDB-flow) unit test into a runnable .buda script and
open it in the interactive topology explorer.

Arguments:
  test_name        test function name, or path/to/test_file.py::test_func
  [unit2buda opts] forwarded to tools/unit2buda.py (e.g. --case N);
                   do NOT pass -o, the wrapper supplies it

Examples:
  u2b.ps1 test_column_datapath_hvh
  u2b.ps1 test_column_datapath_hvh --case 2
  u2b.ps1 test/tests/test_foo.py::test_bar

See tools/unit2buda.py --help for the full opt list.
'@ | Write-Output
    exit 0
}

if ($args.Count -eq 0) {
    Write-Error 'Usage: u2b.ps1 <test_name> [unit2buda opts]   (e.g. u2b.ps1 test_column_datapath_hvh --case 2)'
    exit 1
}

$test = $args[0]
$rest = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }

# A filesystem-safe stem for the output name (strip any path::func decoration).
$stem = $test -replace '[/:.\\]', '_'
$out = Join-Path ([IO.Path]::GetTempPath()) "u2b_$stem.buda"

$py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
& $py (Join-Path $ProjectRoot 'tools/unit2buda.py') $test @rest -o $out
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot 'buda.ps1') $out
exit $LASTEXITCODE
