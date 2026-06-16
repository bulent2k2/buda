#!/bin/bash
set -e

# Format seconds to a human-readable duration (e.g. 1m 15s)
format_duration() {
    local secs=$1
    if [ $secs -ge 60 ]; then
        echo "$((secs / 60))m $((secs % 60))s"
    else
        echo "${secs}s"
    fi
}

START_TIME=$(date +%s)

# Get the directory of this script to run from the root
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# Parse arguments
CLEAN=false
RUN_TESTS=false
SLOW_TESTS=false

for arg in "$@"; do
    case "$arg" in
        --clean|-c)
            CLEAN=true
            ;;
        --test|-t|test)
            RUN_TESTS=true
            ;;
        --slow|-s|slow)
            SLOW_TESTS=true
            ;;
    esac
done

if [ "$CLEAN" = true ]; then
    echo "=== Cleaning and Building BUDA (Clean Build) ==="
else
    echo "=== Building BUDA (Incremental Build) ==="
fi

BUILD_START=$(date +%s)

mkdir -p build
cd build

echo "Configuring with CMake..."
cmake ..

if [ "$CLEAN" = true ]; then
    echo "Cleaning previous build..."
    make clean
fi

echo "Building target (buda)..."
make -j$(sysctl -n hw.ncpu 2>/dev/null || echo 4)

# Remove any stale .so copies from src/ so they cannot shadow the fresh build.
rm -f "$REPO_DIR"/src/buda*.so "$REPO_DIR"/src/libbuda_core*.so
echo "Build artifacts in build/. Use 'PYTHONPATH=build' to run scripts directly."

BUILD_END=$(date +%s)
BUILD_DURATION=$((BUILD_END - BUILD_START))

echo "=== BUDA build successful ==="

TEST_DURATION=0
if [ "$RUN_TESTS" = true ]; then
    echo "=== Running Tests ==="
    TEST_START=$(date +%s)

    cd ..
    mkdir -p log
    TEST_LOG="log/pytest_$(date +%Y%m%d_%H%M%S).log"
    # Define awk filter for milestone-based progress indicator
    AWK_FILTER='
    BEGIN {
        next_milestone = 0
    }
    {
        if (log_file) {
            print $0 > log_file
            fflush(log_file)
        }
    }
    /\[[[:space:]]*[0-9]+%\]/ {
        match($0, /\[[[:space:]]*[0-9]+%\]/)
        pct_str = substr($0, RSTART, RLENGTH)
        gsub(/[^0-9]/, "", pct_str)
        pct = int(pct_str)
        
        if (pct >= next_milestone) {
            test_info = $1
            n = split(test_info, parts, "/")
            display_name = parts[n]
            if (length(display_name) > 65) {
                display_name = substr(display_name, 1, 62) "..."
            }
            printf "Progress: [%d%%] %s\n", pct, display_name
            fflush()
            next_milestone = (int(pct / 10) + 1) * 10
        }
        next
    }
    /===|---|FAILURES|test_.*FAILED|collected/ {
        print $0
        next
    }
    '

    if [ "$SLOW_TESTS" = true ]; then
        echo "Running pytest (including slow tests)... (output redirected to $TEST_LOG)"
        set +e
        PYTHONUNBUFFERED=1 pytest -v -o addopts="" 2>&1 | awk -v log_file="$TEST_LOG" "$AWK_FILTER"
        PYTEST_RC=${PIPESTATUS[0]}
        set -e
    else
        echo "Running pytest (excluding slow tests)... (output redirected to $TEST_LOG)"
        set +e
        PYTHONUNBUFFERED=1 pytest -v 2>&1 | awk -v log_file="$TEST_LOG" "$AWK_FILTER"
        PYTEST_RC=${PIPESTATUS[0]}
        set -e
    fi

    TEST_END=$(date +%s)
    TEST_DURATION=$((TEST_END - TEST_START))
    
    if [ $PYTEST_RC -ne 0 ]; then
        echo "Tests failed. See $TEST_LOG for details."
    fi
fi

END_TIME=$(date +%s)
TOTAL_DURATION=$((END_TIME - START_TIME))

echo ""
echo "=== Time Summary ==="
echo "Building: $(format_duration $BUILD_DURATION)"
if [ "$RUN_TESTS" = true ]; then
    echo "Testing:  $(format_duration $TEST_DURATION)"
fi
echo "Total:    $(format_duration $TOTAL_DURATION)"
