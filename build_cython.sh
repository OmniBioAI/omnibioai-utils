#!/bin/bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ecosystem_path.sh"
# OmniBioAI Cython compilation script
# Compiles all HIGH priority files across all repos
# Run before building Docker images

set -e
MACHINE="$OMNIBIOAI_ROOT"
LOG=$MACHINE/out/cython_build.log
mkdir -p $MACHINE/out

echo "OmniBioAI Cython IP Protection Build"
echo "========================================"
echo "Started: $(date)"
echo ""

pip install cython setuptools --break-system-packages -q

SUCCESS=0
FAILED=0

for repo in omnibioai omnibioai-tes omnibioai-lims omnibioai-toolserver \
            omnibioai-rag omnibioai-security-sdk omnibioai-hpc-policy-engine \
            omnibioai-policy-engine omnibioai-model-registry; do
    path=$MACHINE/$repo
    setup_file="setup.py"
    [ "$repo" = "omnibioai" ] && setup_file="setup_cython.py"

    if [ ! -f "$path/$setup_file" ]; then
        echo "SKIP  $repo - no $setup_file found"
        continue
    fi

    echo "Building $repo..."
    cd "$path"

    if python "$setup_file" build_ext --inplace >> "$LOG" 2>&1; then
        count=$(find . -name "*.so" -newer "$setup_file" 2>/dev/null | wc -l)
        echo "OK    $repo - $count .so files compiled"
        SUCCESS=$((SUCCESS + 1))
    else
        echo "FAIL  $repo - see $LOG for details"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "========================================"
echo "Success: $SUCCESS repos"
echo "Failed:  $FAILED repos"
echo "Log:     $LOG"
echo "Finished: $(date)"
echo ""
echo "Next step: docker compose build"
