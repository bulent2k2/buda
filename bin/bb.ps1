# bb.ps1 — build BUDA (PowerShell twin of bin/bb).
#
# Incremental build by default; same options as bin/bb.  Windows PowerShell
# 5.1 and pwsh 7+ (any OS).  Deviations from the bash wrapper, deliberate:
#   * cmake --build (generator-agnostic) instead of make; Ninja is selected
#     when available (single-config: artifacts land in build\, the layout
#     pytest.ini and the docs assume), else the default generator with
#     --config Release (artifacts in build\Release — PYTHONPATH needed,
#     see docs/WINDOWS_BUILD.md section 4).
#   * On Windows the MSVC environment must already be set up for Ninja
#     (vcvars64 / a developer shell) — the wrapper does not conjure it.
#   * Test output goes to the console AND the log via Tee-Object; the bash
#     wrapper's awk milestone filter is not ported.
#   * `bb web` (the sbt Scala.js client build) is not ported; use bin/bb.

$ErrorActionPreference = 'Stop'
$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoDir
$StartTime = Get-Date

function Format-Duration([TimeSpan]$t) {
    $secs = [int]$t.TotalSeconds
    if ($secs -ge 60) { return "{0}m {1}s" -f [math]::Floor($secs / 60), ($secs % 60) }
    return "${secs}s"
}

function Show-Usage {
    @'
Usage: bb.ps1 [options]

Build BUDA (incremental by default) and optionally run the test suite.

Build options:
  -c, --clean       Clean rebuild

Test options (each implies a build first; tiers are cumulative):
  -t, --test, test  Run the FAST tier only       (serial by default; -p to parallelize)
  -m, --mid,  mid   Run FAST + MID tiers          (+full-pipeline integration tests)
  -s, --slow, slow  Run ALL tiers                 (+SA/GA optimizer storms)
  -p, --parallel    Parallelize the run (implies -t when no tier is given)

Other:
  -h, --help        Show this help and exit

Tier definitions live in pytest.ini; see docs/internal/test/.  The Scala.js
client build (`bb web`) is bash-only — use bin/bb for it.
'@ | Write-Output
}

$Clean = $false; $RunTests = $false; $TestTier = 'fast'; $Parallel = $false
foreach ($arg in $args) {
    switch ($arg) {
        { $_ -in '--clean', '-c' }            { $Clean = $true }
        { $_ -in '--test', '-t', 'test' }     { $RunTests = $true }
        { $_ -in '--mid', '-m', 'mid' }       { $RunTests = $true; $TestTier = 'mid' }
        { $_ -in '--slow', '-s', 'slow' }     { $RunTests = $true; $TestTier = 'slow' }
        { $_ -in '--parallel', '-p', 'parallel' } { $RunTests = $true; $Parallel = $true }
        { $_ -in '--help', '-h', 'help' }     { Show-Usage; exit 0 }
        'web' {
            Write-Error "bb.ps1: 'web' (the sbt Scala.js build) is not ported; run bin/bb web from bash."
            exit 1
        }
        default {
            Write-Error "Unknown option: $arg`nTry 'bb.ps1 --help' for usage."
            exit 1
        }
    }
}

if ($Clean) { Write-Output '=== Cleaning and Building BUDA (Clean Build) ===' }
else        { Write-Output '=== Building BUDA (Incremental Build) ===' }
$BuildStart = Get-Date

# Configure.  BUDA_ARCH forwards the target ISA exactly like bin/bb (default
# "native"; CI pins an explicit baseline).  Prefer Ninja for the single-config
# build\ layout the docs and pytest.ini assume — but ONLY when creating a
# FRESH build tree: CMake rejects a -G that differs from an existing tree's
# recorded generator instead of rebuilding (Codex P1 on #735), so an
# incremental run must reuse whatever the cache records and derive the
# multi-config question from it.
$cmakeArgs = @('-S', '.', '-B', 'build')
$cache = 'build/CMakeCache.txt'
if (Test-Path -LiteralPath $cache) {
    $genLine = Select-String -LiteralPath $cache `
        -Pattern '^CMAKE_GENERATOR:INTERNAL=(.+)$' | Select-Object -First 1
    $gen = if ($genLine) { $genLine.Matches[0].Groups[1].Value } else { '' }
    $multiConfig = ($gen -match 'Visual Studio|Xcode|Multi-Config')
} elseif (Get-Command ninja -ErrorAction SilentlyContinue) {
    $cmakeArgs += @('-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release')
    $multiConfig = $false
} else {
    $multiConfig = $true
}
if ($env:BUDA_ARCH) { $cmakeArgs += "-DBUDA_ARCH=$($env:BUDA_ARCH)" }
Write-Output 'Configuring with CMake...'
cmake @cmakeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Clean) {
    Write-Output 'Cleaning previous build...'
    cmake --build build --target clean
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Output 'Building target (buda)...'
$buildArgs = @('--build', 'build', '--parallel')
if ($multiConfig) { $buildArgs += @('--config', 'Release') }
cmake @buildArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Remove any stale extension copies from src/ so they cannot shadow the build.
Remove-Item -Force -ErrorAction SilentlyContinue `
    (Join-Path $RepoDir 'src/buda*.so'), (Join-Path $RepoDir 'src/libbuda_core*.so'), `
    (Join-Path $RepoDir 'src/buda*.pyd'), (Join-Path $RepoDir 'src/buda_core*.dll')
Write-Output "Build artifacts in build/. Use 'PYTHONPATH=build' to run scripts directly."
$BuildDuration = (Get-Date) - $BuildStart
Write-Output '=== BUDA build successful ==='

$TestDuration = [TimeSpan]::Zero
$py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
if ($RunTests) {
    Write-Output '=== Running Tests ==='
    if (-not $env:MPLBACKEND) { $env:MPLBACKEND = 'Agg' }
    if (-not $env:PYTHONUTF8) { $env:PYTHONUTF8 = '1' }   # measured requirement on Windows
    # Multi-config layout: the extensions land in build\Release, which
    # pytest.ini's `pythonpath = build src` does not cover (Codex P2 on
    # #735; the documented VS-layout PYTHONPATH in WINDOWS_BUILD.md §4).
    $rel = Join-Path $RepoDir 'build/Release'
    if (Test-Path -LiteralPath $rel) {
        $sep = [IO.Path]::PathSeparator
        if ($env:PYTHONPATH) { $env:PYTHONPATH = "$rel$sep$($env:PYTHONPATH)" }
        else                 { $env:PYTHONPATH = $rel }
    }
    $TestStart = Get-Date
    New-Item -ItemType Directory -Force -Path 'log' | Out-Null
    if (-not $env:MPLCONFIGDIR) { $env:MPLCONFIGDIR = Join-Path $RepoDir 'log/matplotlib' }
    New-Item -ItemType Directory -Force -Path $env:MPLCONFIGDIR | Out-Null
    $TestLog = 'log/pytest_{0:yyyyMMdd_HHmmss}.log' -f (Get-Date)

    $markOpt = @()
    switch ($TestTier) {
        'slow' { Write-Output "Running pytest (fast + mid + slow tiers)... (log: $TestLog)"
                 $markOpt = @('-o', 'addopts=') }
        'mid'  { Write-Output "Running pytest (fast + mid tiers)... (log: $TestLog)"
                 $markOpt = @('-o', 'addopts=-m "not slow"') }
        default { Write-Output "Running pytest (fast tier only)... (log: $TestLog)" }
    }

    $xdistOpt = @()
    $bbJobs = if ($env:BB_JOBS) { $env:BB_JOBS } else { 'auto' }
    if ((($TestTier -ne 'fast') -or $Parallel) -and ($bbJobs -ne '0')) {
        & $py -c 'import xdist' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $xdistOpt = @('-n', $bbJobs, '--dist', 'loadfile')
            Write-Output "  (parallel: pytest-xdist -n $bbJobs --dist loadfile)"
        } else {
            Write-Output "  (serial: 'pip install pytest-xdist' to parallelize this tier)"
        }
    }

    $env:PYTHONUNBUFFERED = '1'
    & $py -m pytest -q @markOpt @xdistOpt 2>&1 | Tee-Object -FilePath $TestLog
    $pytestRc = $LASTEXITCODE
    $TestDuration = (Get-Date) - $TestStart
    if ($pytestRc -ne 0) { Write-Output "Tests failed. See $TestLog for details." }
}

Write-Output ''
Write-Output '=== Time Summary ==='
Write-Output "Building: $(Format-Duration $BuildDuration)"
if ($RunTests) { Write-Output "Testing:  $(Format-Duration $TestDuration)" }
Write-Output "Total:    $(Format-Duration ((Get-Date) - $StartTime))"
if ($RunTests -and $pytestRc -ne 0) { exit $pytestRc }
