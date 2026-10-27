# Contributing to Humanoid Export

Humanoid Export exports Auto-Rig Pro and Rigify characters from Blender to Unity and Cascadeur,
and bakes animation clips for Unity. See [README.md](README.md) for supported workflows.

## Project naming

- Display name: **Humanoid Export**.
- Sidebar location: 3D View → N panel → **Humanoid** tab → **Humanoid Export** panel.
- Repository: [blender-humanoid-export](https://github.com/renatmansurov/blender-humanoid-export).
- The Blender extension ID and source package remain `cascadeur_export` for compatibility with
  existing installations. Built archives use `humanoid-export-<version>.zip`; the ZIP filename is
  independent of the extension ID.
- GitHub Actions uploads the installable ZIP directly with `archive: false`. The artifact takes
  the ZIP's filename, with no outer archive to extract.
- Existing `csc_*` properties, operator IDs and temporary object names are implementation
  identifiers; keep them stable when changing public-facing names.

## Repository layout

```
cascadeur_export/        The add-on itself (a Blender extension). Everything in here ships in the zip.
  blender_manifest.toml  Extension manifest: id, version, Blender version, license, permissions.
  __init__.py            UI panel, properties, operators, register/unregister.
  mapping.py             Auto-Rig Pro / Rigify deform bones -> UE4 Mannequin skeleton.
  build.py               Builds the temporary export skeleton and skinned mesh copies.
  checks.py              Pre-export checks shown in the panel.
  anim.py                Bakes the rig's animation onto the export skeleton.
  export.py              FBX export settings and calls.
tests/                   Headless test scripts (run inside Blender).
  run_tests.py           Checks a character .blend end to end.
  make_rigify_variant.py Generates Rigify test characters.
  run_rigify_variants.sh Generates every Rigify variant and runs the tests on it (used by CI).
  in/, out/              Local test characters and results. Not tracked by git.
docs/ru/                 Russian translation of the documentation.
.github/workflows/       CI: validates and builds the extension, publishes releases.
```

Only files needed by users and contributors are tracked. Test characters, exported FBX files,
build output (`dist/`), the local Unity test project and editor/AI-assistant files are ignored
(see `.gitignore`).

## Conventions

- All code, comments, docstrings, commit messages and documentation are in English. Translations
  live in `docs/<language>/`; the English text is authoritative.
- Every Python file starts with the SPDX header:
  ```python
  # SPDX-FileCopyrightText: 2026 Renat Mansurov
  # SPDX-License-Identifier: GPL-3.0-or-later
  ```
- The add-on must keep working on the oldest Blender in `blender_version_min` (4.2) and on the
  current Blender release. Guard newer APIs with `hasattr` checks.
- The add-on never leaves the user's scene modified: temporary objects, renames, selection,
  active object, actions, NLA state and the current frame are restored after every operation.
- Use relative imports inside `cascadeur_export/` (`from . import build`), as required for
  extensions.
- User-facing behaviour changes are documented in `README.md` and `CHANGELOG.md`.

## Testing

Tests run inside Blender in background mode on a character .blend (Auto-Rig Pro or Rigify,
skinned, optionally animated). The .blend is never saved.

```
blender -b <character.blend> --factory-startup --python-exit-code 1 --python tests/run_tests.py -- [out_dir]
```

- `out_dir` defaults to `tests/out/`.
- The skeleton is checked against Cascadeur's `UE4_New.qrigcasc` Quick Rigging Tool template.
  The default path is the standard Cascadeur install on Windows; set `CSC_UE4_TEMPLATE` to use
  another location. Without the template a built-in copy of its hierarchy is used; with it, the
  built-in copy is checked against the template.
- The script prints `RESULT: PASS` or the list of failures, and exits with code 1 on failure.

Rigify test characters can be generated without any external assets:

```
blender -b --factory-startup --python tests/make_rigify_variant.py -- <variant> <out.blend>
```

Variants: `human`, `basic`, `human_noface`, `human_torso2`, `human_torso5`, `human_neck1`,
`human_neck3`, `human_tail` and combinations such as `human_torso2_neck3`. `tail` adds a
three-bone tail off the hips and a two-bone hair chain off the head, for **Keep Extra Chains**.

To generate all of them and run the tests on each (Git Bash on Windows works):

```
bash tests/run_rigify_variants.sh <blender> [out_dir] [variant ...]
```

It prints one `RESULT` line per variant and exits with code 1 if any fails. CI (`.github/workflows/build.yml`)
runs it on every push and pull request in the oldest supported Blender (4.2) and the current release;
the extension is only built and released when it passes. The test characters in `tests/in/` are
not redistributable, so they are not part of CI, and files saved by Blender 5.x cannot be opened
in 4.2: run them locally on the Blender version they were saved with.

What is checked: bone set and hierarchy against the template; rest positions, weight sums,
normals and shape keys; deformation match in animation and in a random pose; scene restoration;
units and character height in the FBX. For animation: the baked pose matches the rig's
rotation and root location (within 0.1 mm), location keyed only on the root, no quaternion
flips, mesh deformation on real animation < 1 cm (on frames without stretch), the clip FBX has one
take named after the action with the right length, only the bind pose helper geometry, a bind
pose covering every bone, default transforms equal to the model's rest pose, no animated
translation except `pelvis`, the hierarchy matches the model FBX, and the action, slot, NLA and
frame are restored after export. If the file has no animation, a synthetic one is put on the rig
temporarily. Action selection: an action another armature plays is not listed, an unticked action
is not exported, the action's manual frame range is used, nothing ticked is reported. Meshes
parented to a bone (the hand's deform bone and the control the head follows) are exported with
weight 1 on the right bone, keep their rest position and follow the bone. With Keep Extra Chains:
no face or helper bones among the extras, mapped bones keep their parents, weight sums are
preserved, the posed mesh matches, the extras are baked with the rig's rotation and are in the
character and clip FBX. Also checked: the panel's pre-export checks, that saving with the preview built writes
the source names, and that a file saved with a preview is repaired when opened (the copies are
written to `out_dir`; the character .blend itself is never saved).

Before a release, also import the exported FBX files into Cascadeur and Unity and check them
manually (see the Cascadeur and Unity sections of the README).

## Building

```
blender --factory-startup --command extension validate cascadeur_export
blender --factory-startup --command extension build --source-dir cascadeur_export --output-filepath dist/humanoid-export-VERSION.zip
```

Replace `VERSION` with the manifest version. The output directory must exist. `dist/` is ignored
by git. CI reads the version from the manifest automatically and uses the same filename for
Actions artifacts and release assets.

## Releasing

1. Update `version` in `cascadeur_export/blender_manifest.toml` (semantic versioning) and add a
   section to `CHANGELOG.md`.
2. Commit, then tag and push. Replace `vX.Y.Z` with the manifest version prefixed with `v`:
   ```
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```
3. The `Build` workflow checks that the tag matches the manifest version, builds the zip and
   publishes a GitHub Release with it attached.
4. Optionally upload the same zip to [extensions.blender.org](https://extensions.blender.org).
