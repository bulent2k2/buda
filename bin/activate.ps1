# bin/activate.ps1 — DOT-SOURCE this to put BUDA's bin/ on PATH and set
# PYTHONPATH (PowerShell twin of bin/activate).
#
#   . bin/activate.ps1                       # from the repo root
#   . C:\path\to\buda\bin\activate.ps1       # from anywhere
#
# A PATH change only affects the session that dot-sources the file.  Run
# directly (without the leading dot), it just prints how to source it.
#
# Idempotent: re-sourcing does not stack duplicate entries.  Also defines
# functions (bb, buda, fp, bfp, viz, u2b, btcl) so the wrappers run BARE —
# PATH alone would not make `bb` find bb.ps1, since PowerShell does not
# resolve `bb` to `bb.ps1` the way cmd resolves .BAT via PATHEXT.

$_budaRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# --- refuse to be executed (a PATH change would vanish with the process) ----
if ($MyInvocation.InvocationName -ne '.') {
    Write-Host 'bin/activate.ps1 must be dot-sourced, not executed. Run:'
    Write-Host "    . `"$($_budaRoot)\bin\activate.ps1`""
    exit 1
}

$_sep = [IO.Path]::PathSeparator
$_bin = Join-Path $_budaRoot 'bin'

# --- prepend bin/ to PATH (idempotent) --------------------------------------
if (($env:PATH -split [regex]::Escape($_sep)) -notcontains $_bin) {
    $env:PATH = "$_bin$_sep$($env:PATH)"
}

# --- prepend build/ + tools/ to PYTHONPATH (idempotent, build-first) --------
# build/Release first when it exists: the VS multi-config generator puts the
# extensions there, invisible to a bare `build` entry (Codex P2 #735).
$_build = Join-Path $_budaRoot 'build'
$_tools = Join-Path $_budaRoot 'tools'
$_rel = Join-Path $_budaRoot 'build/Release'
$_lead = if (Test-Path -LiteralPath $_rel) { "$_rel$_sep$_build" } else { $_build }
if (-not $env:PYTHONPATH -or
    (($env:PYTHONPATH -split [regex]::Escape($_sep)) -notcontains $_build)) {
    if ($env:PYTHONPATH) { $env:PYTHONPATH = "$_lead$_sep$_tools$_sep$($env:PYTHONPATH)" }
    else                 { $env:PYTHONPATH = "$_lead$_sep$_tools" }
}

# --- bare-name functions for the wrappers -----------------------------------
# The bin path is baked into a global so the functions survive after this
# file's variables are cleaned up.
$global:BudaBin = $_bin
function global:bb   { & (Join-Path $global:BudaBin 'bb.ps1')   @args }
function global:buda { & (Join-Path $global:BudaBin 'buda.ps1') @args }
function global:fp   { & (Join-Path $global:BudaBin 'fp.ps1')   @args }
function global:bfp  { & (Join-Path $global:BudaBin 'bfp.ps1')  @args }
function global:viz  { & (Join-Path $global:BudaBin 'viz.ps1')  @args }
function global:u2b  { & (Join-Path $global:BudaBin 'u2b.ps1')  @args }
function global:btcl { & (Join-Path $global:BudaBin 'btcl.ps1') @args }

Write-Host "BUDA env ready: $_bin on PATH (bb, buda, fp, bfp, viz, u2b, btcl); PYTHONPATH set."
Write-Host "  (run 'bb' once to build if you haven't yet)"

Remove-Variable _budaRoot, _sep, _build, _tools, _rel, _lead -ErrorAction SilentlyContinue
