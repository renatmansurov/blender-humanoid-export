# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

"""Export the current timeline of every single-character .blend in a folder.

    batch_export.sh <input folder>
        [--face | --no-face] [--jaweyes | --no-jaweyes] [--chains | --no-chains]

The output folder is created beside the input folder as ``<name>_export``. When that folder
already exists, the next run uses ``<name>_export_2``, ``<name>_export_3`` and so on. Pass a
second folder to choose the output path yourself. ``batch_export.sh`` finds Blender
(``/Applications/Blender.app`` on macOS, or ``BLENDER=/path/to/Blender``).

Facial joints (--face: lips, brows, cheeks, tongue, ears) and jaw and eyes (--jaweyes) are
exported by default. Extra chains (--chains: tail, hair, skirt, cape) stay merged unless asked
for. One Blender process per file, so one failure does not stop the rest. A file is skipped
unless it has exactly one visible armature with a visible skinned mesh. The FBX is named after
the .blend and the take inside it uses that same name. The scene frame range is baked at 30 fps,
with NLA left on. Source files are not saved.
"""

import argparse
import os
import subprocess
import sys

import bpy

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
from humanoid_export import anim as anim_mod  # noqa: E402
from humanoid_export import build as build_mod  # noqa: E402
from humanoid_export import export as export_mod  # noqa: E402

FPS = 30
USAGE = ("batch_export.sh <input folder> [<output folder>] "
         "[--face | --no-face] [--jaweyes | --no-jaweyes] [--chains | --no-chains]")


def visible_character_rigs(view_layer):
    """Visible armatures that deform at least one visible mesh."""
    rigs = []
    for ob in view_layer.objects:
        if ob.type != 'ARMATURE' or ob.get(build_mod.TMP_PROP):
            continue
        if not ob.visible_get(view_layer=view_layer):
            continue
        if build_mod.find_meshes(ob, view_layer):
            rigs.append(ob)
    return rigs


def export_open_file(output_dir, face=True, jaweyes=True, chains=False):
    """Bakes the open file's current timeline to ``<blend stem>.fbx`` in ``output_dir``.

    ``face`` keeps the other facial deform bones as joints. ``jaweyes`` keeps jaw, eye_l and
    eye_r. ``chains`` keeps unmapped deform chains such as a tail, hair, a skirt or a cape.
    """
    path = bpy.data.filepath
    context = bpy.context
    rigs = visible_character_rigs(context.view_layer)
    if len(rigs) != 1:
        names = ", ".join(ob.name for ob in rigs) or "none"
        print(f"BATCH_SKIP {path}: {len(rigs)} visible skinned rigs ({names})", flush=True)
        return 2
    rig = rigs[0]
    anim_rig = anim_mod.find_animated_rig(rig, build_mod.find_deform_rig(rig, context.view_layer))
    if not anim_mod.is_animated(anim_rig):
        print(f"BATCH_FAIL {path}: '{anim_rig.name}' has no action or NLA strips", flush=True)
        return 1
    stem = anim_mod.safe_name(os.path.splitext(os.path.basename(path))[0])
    filepath = os.path.join(output_dir, f"{stem}.fbx")
    os.makedirs(output_dir, exist_ok=True)
    scene = context.scene
    _name, action, start, end = anim_mod.clips(context, anim_rig, 'ACTIVE')[0]
    result = None
    try:
        result = build_mod.build(context, rig, jaweyes, export_mod.centimetre_scale(scene),
                                 meshes=False, extra_chains=chains, facial_joints=face)
        with anim_mod.use_action(context, anim_rig, action):
            baked = anim_mod.bake(context, result, start, end, stem, FPS)
        export_mod.export_anim_fbx(context, filepath, result, baked, stem, FPS)
    except build_mod.BuildError as exc:
        print(f"BATCH_FAIL {path}: {exc}", flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001 - one file must not take down the batch
        print(f"BATCH_FAIL {path}: {exc}", flush=True)
        return 1
    finally:
        build_mod.clear(context)
    for warning in result.warnings:
        print(f"BATCH_WARN {path}: {warning}", flush=True)
    keys = len(anim_mod.resampled_frames(scene, start, end, FPS))
    scene_fps = anim_mod.scene_fps(scene)
    print(f"BATCH_OK {filepath} frames={start}-{end} scene_fps={scene_fps:g} keys={keys}", flush=True)
    return 0


def _blend_files(input_dir, output_dir):
    files = []
    for dirpath, _dirnames, names in os.walk(input_dir):
        folder = os.path.abspath(dirpath)
        if folder == output_dir or folder.startswith(output_dir + os.sep):
            continue
        for name in names:
            if name.lower().endswith(".blend"):
                files.append(os.path.join(dirpath, name))
    files.sort()
    return files


def _cli_flags(face, jaweyes, chains):
    """Flags forwarded to the per-file process, including the ones that repeat a default."""
    return [
        "--face" if face else "--no-face",
        "--jaweyes" if jaweyes else "--no-jaweyes",
        "--chains" if chains else "--no-chains",
    ]


def _run_one(blend, output_dir, face, jaweyes, chains):
    cmd = [
        bpy.app.binary_path, "-b", "--factory-startup", blend,
        "--python", os.path.abspath(__file__), "--", output_dir,
        *_cli_flags(face, jaweyes, chains),
    ]
    print(f"BATCH_RUN {blend}", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    status = None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if line.startswith("BATCH_OK "):
            status = 0
        elif line.startswith("BATCH_SKIP "):
            status = 2
        elif line.startswith("BATCH_FAIL "):
            status = 1
    proc.wait()
    if status is None:
        print(f"BATCH_FAIL {blend}: blender exited {proc.returncode} without a status line", flush=True)
        return 1
    return status


def _next_export_dir(input_dir):
    """``<input>_export``, or ``<input>_export_2``, ``_3``, ... when that folder already exists."""
    base = input_dir.rstrip(os.sep) + "_export"
    if not os.path.exists(base):
        return base
    number = 2
    while os.path.exists(f"{base}_{number}"):
        number += 1
    return f"{base}_{number}"


def export_folder(input_dir, output_dir, face=True, jaweyes=True, chains=False):
    """Exports every .blend under ``input_dir`` into ``output_dir``. Returns 0 when none failed."""
    if not os.path.isdir(input_dir):
        print(f"BATCH_FAIL {input_dir}: input folder not found", flush=True)
        return 1
    os.makedirs(output_dir, exist_ok=True)
    print(f"BATCH_OUT {output_dir}", flush=True)
    print(f"BATCH_OPTS face={int(face)} jaweyes={int(jaweyes)} chains={int(chains)}", flush=True)
    exported = skipped = failed = 0
    seen = {}
    for path in _blend_files(input_dir, output_dir):
        stem = anim_mod.safe_name(os.path.splitext(os.path.basename(path))[0])
        if stem in seen:
            print(f"BATCH_FAIL {path}: output name {stem}.fbx already used by {seen[stem]}", flush=True)
            failed += 1
            continue
        seen[stem] = path
        code = _run_one(path, output_dir, face, jaweyes, chains)
        if code == 0:
            exported += 1
        elif code == 2:
            skipped += 1
        else:
            failed += 1
    print(f"BATCH_DONE exported={exported} skipped={skipped} failed={failed}", flush=True)
    return 1 if failed else 0


def _parser():
    parser = argparse.ArgumentParser(prog="batch_export.py", usage=USAGE)
    parser.add_argument("paths", nargs="*", metavar="folder")
    parser.add_argument("--face", action=argparse.BooleanOptionalAction, default=True,
                        help="export facial joints (lips, brows, cheeks, tongue, ears); default on")
    parser.add_argument("--jaweyes", action=argparse.BooleanOptionalAction, default=True,
                        help="export jaw, eye_l and eye_r; default on")
    parser.add_argument("--chains", action=argparse.BooleanOptionalAction, default=False,
                        help="export extra deform chains (tail, hair, skirt, cape); default off")
    return parser


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = _parser().parse_args(argv)
    paths = [os.path.abspath(path) for path in args.paths]
    if len(paths) == 1 and bpy.data.filepath:
        return export_open_file(paths[0], args.face, args.jaweyes, args.chains)
    if len(paths) == 1 and not bpy.data.filepath:
        return export_folder(paths[0], _next_export_dir(paths[0]), args.face, args.jaweyes, args.chains)
    if len(paths) == 2 and not bpy.data.filepath:
        return export_folder(paths[0], paths[1], args.face, args.jaweyes, args.chains)
    print(f"usage: {USAGE}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
