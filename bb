#!/bin/bash
set -e

# Get the directory of this script to run from the root
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo "=== Cleaning and Building BUDA ==="
mkdir -p build
cd build

echo "Configuring with CMake..."
cmake ..

echo "Cleaning previous build..."
make clean

echo "Building target (buda)..."
make -j$(sysctl -n hw.ncpu 2>/dev/null || echo 4)

echo "Copying library to src/..."
# Copy the compiled shared library (supports different Python suffixes on macOS/Linux)
cp buda.cpython-*.so ../src/

echo "=== BUDA build successful ==="

# Check if tests should be run
RUN_TESTS=false
for arg in "$@"; do
    if [ "$arg" = "--test" ] || [ "$arg" = "-t" ] || [ "$arg" = "test" ]; then
        RUN_TESTS=true
    fi
done

if [ "$RUN_TESTS" = true ]; then
    echo "=== Running Tests ==="
    cd ../test/tests
    PYTHONPATH=../../src pytest
fi
