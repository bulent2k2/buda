#!/bin/bash
set -e

# Get the directory of this script to run from the root
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# Parse arguments
CLEAN=false
RUN_TESTS=false

for arg in "$@"; do
    case "$arg" in
        --clean|-c)
            CLEAN=true
            ;;
        --test|-t|test)
            RUN_TESTS=true
            ;;
    esac
done

if [ "$CLEAN" = true ]; then
    echo "=== Cleaning and Building BUDA (Clean Build) ==="
else
    echo "=== Building BUDA (Incremental Build) ==="
fi

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

echo "Copying library to src/..."
# Copy the compiled shared library (supports different Python suffixes on macOS/Linux)
cp buda.cpython-*.so ../src/

echo "=== BUDA build successful ==="

if [ "$RUN_TESTS" = true ]; then
    echo "=== Running Tests ==="
    cd ../test/tests
    PYTHONPATH=../../src pytest
fi
