#!/bin/bash
# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

# Generates Rigify test characters and runs tests/run_tests.py on each. Used by CI; works locally
# too (Git Bash on Windows). Prints one RESULT line per variant, exits 1 if any variant fails.
#
#     tests/run_rigify_variants.sh <blender> [out_dir] [variant ...]
#
# out_dir defaults to tests/out/rigify; without variants, all of them are run.

set -u
BLENDER="$1"
TESTS="$(cd "$(dirname "$0")" && pwd)"
OUT="${2:-$TESTS/out/rigify}"
shift $(( $# < 2 ? $# : 2 ))
VARIANTS="${*:-human basic human_noface human_torso2 human_torso5 human_neck1 human_neck3 human_torso2_neck3}"

mkdir -p "$OUT"
failed=0
for v in $VARIANTS; do
    if ! "$BLENDER" -b --factory-startup --python-exit-code 1 --python "$TESTS/make_rigify_variant.py" \
            -- "$v" "$OUT/$v.blend" > "$OUT/$v.make.log" 2>&1; then
        echo "$v: generation failed, see $OUT/$v.make.log"
        failed=1
        continue
    fi
    if ! "$BLENDER" -b "$OUT/$v.blend" --factory-startup --python-exit-code 1 --python "$TESTS/run_tests.py" \
            -- "$OUT/$v" > "$OUT/$v.log" 2>&1; then
        failed=1
        # The list of failures follows the RESULT line.
        sed -n '/^RESULT/,$p' "$OUT/$v.log" | grep '^  - ' | sed "s/^/$v:/"
    fi
    echo "$v: $(grep -h '^RESULT' "$OUT/$v.log" || echo "no RESULT (crashed), see $OUT/$v.log")"
done
exit $failed
