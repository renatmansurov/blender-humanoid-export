#!/bin/sh
# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

# Export every single-character .blend in a folder. The FBX folder is created beside it.
#
#   batch_export.sh <input folder> [<output folder>] [--no-face] [--no-jaweyes] [--chains]
#
# Blender is taken from $BLENDER, then `blender` on PATH, then Blender.app.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

if [ -n "${BLENDER:-}" ]; then
    BLENDER_BIN=$BLENDER
elif command -v blender >/dev/null 2>&1; then
    BLENDER_BIN=$(command -v blender)
elif [ -x /Applications/Blender.app/Contents/MacOS/Blender ]; then
    BLENDER_BIN=/Applications/Blender.app/Contents/MacOS/Blender
else
    echo "Blender not found. Set BLENDER to the Blender binary." >&2
    exit 1
fi

exec "$BLENDER_BIN" -b --factory-startup --python "$ROOT/batch_export.py" -- "$@"
