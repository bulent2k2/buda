# BUDA environment wrapper (PowerShell twin of bin/buda).
# Sets up PYTHONPATH to include the build directory before running the CLI.
#
# Works under Windows PowerShell 5.1 and pwsh 7+ (any OS).  The macOS
# per-cell .app relaunch of the bash wrapper is Darwin-only and has no
# PowerShell counterpart; this script always launches the CLI directly.
# The BUDA_TEST_ANCHOR hook is ported 1:1 so the arg-handling contract is
# testable through either wrapper (test/tests/test_buda_wrapper_args.py).

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Sep = [IO.Path]::PathSeparator

# Options that take a VALUE which is NOT a path: the next token must pass
# through verbatim — never taken as the script, never anchored to $PWD.
function Test-ValueOpt([string]$a) {
    return @('-t', '--tag', '-j', '--threads') -contains $a
}

# Scan for the first non-option arg (the script), skipping every value-taking
# option's value.  Mirrors scan_script_arg in bin/buda.
function Get-ScriptArg([string[]]$argv) {
    $skip = $false
    foreach ($a in $argv) {
        if ($skip) { $skip = $false; continue }
        if (Test-ValueOpt $a) { $skip = $true; continue }
        if ($a -like '-*') { continue }
        return $a
    }
    return ''
}

# Anchor every relative path arg to THIS cwd — including the `.buda`-less
# script form the CLI would infer.  Absolute paths, options, and the values
# of non-path options pass through verbatim.  Mirrors anchor_args in bin/buda.
function Get-AnchoredArgs([string[]]$argv) {
    $out = @()
    $skip = $false
    foreach ($a in $argv) {
        if ($skip) { $out += $a; $skip = $false; continue }
        if (Test-ValueOpt $a) { $out += $a; $skip = $true; continue }
        if (Test-Path -LiteralPath $a) {
            $out += (Resolve-Path -LiteralPath $a).Path
        } elseif (Test-Path -LiteralPath "$a.buda") {
            $out += (Resolve-Path -LiteralPath "$a.buda").Path
        } elseif ($a -like '-*' -or [IO.Path]::IsPathRooted($a)) {
            $out += $a
        } else {
            $out += (Join-Path (Get-Location).Path $a)
        }
    }
    return ,$out
}

# Test hook (any OS): print what the anchoring logic computes — the detected
# script arg, then one anchored arg per line — and exit.
if ($env:BUDA_TEST_ANCHOR) {
    Write-Output "SCRIPT_ARG=$(Get-ScriptArg $args)"
    foreach ($a in (Get-AnchoredArgs $args)) { Write-Output $a }
    exit 0
}

# Add build and tools directories to PYTHONPATH (build first), preserving a
# caller's PYTHONPATH as the tail — same shape as the bash wrapper's
# `export PYTHONPATH="$BUILD_DIR:$PROJECT_ROOT/tools:$PYTHONPATH"`.
$parts = @()
$rel = Join-Path $ProjectRoot 'build/Release'
if (Test-Path -LiteralPath $rel) { $parts += $rel }   # VS multi-config layout (Codex P2 #735)
$parts += @((Join-Path $ProjectRoot 'build'), (Join-Path $ProjectRoot 'tools'))
if ($env:PYTHONPATH) { $parts += $env:PYTHONPATH }
$env:PYTHONPATH = $parts -join $Sep

# Pick the interpreter: native Windows installs expose `python` (a bare
# `python3` may be the Microsoft-Store alias); POSIX installs expose python3.
$py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }

& $py (Join-Path $ProjectRoot 'src/buda_cli.py') @args
exit $LASTEXITCODE
