#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/pangjinfu/code/slam-map/slam-map-engine/octree"
BUILD_DIR="$ROOT/build"

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"
cmake ..
cmake --build . -j"$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"

echo "Built octree binaries in $BUILD_DIR"
