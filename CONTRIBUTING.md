# Contributing

## Repository layout

```
cascadeur_export/        The add-on itself (a Blender extension). Everything in here ships in the zip.
  blender_manifest.toml  Extension manifest: id, version, Blender version, license, permissions.
  __init__.py            UI panel, properties, operators, register/unregister.
  mapping.py             Auto-Rig Pro / Rigify deform bones -> UE4 Mannequin skeleton.
  build.py               Builds the temporary export skeleton and skinned mesh copies.
  anim.py                Bakes the rig's animation onto the export skeleton.
  export.py              FBX export settings and calls.
tests/                   Headless test scripts (run inside Blender).
  run_tests.py           Checks a character .blend end to end.
  make_rigify_variant.py Generates Rigify test characters.
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
  another location.
- The script prints `RESULT: PASS` or the list of failures, and exits with code 1 on failure.

Rigify test characters can be generated without any external assets:

```
blender -b --factory-startup --python tests/make_rigify_variant.py -- <variant> <out.blend>
```

Variants: `human`, `basic`, `human_noface`, `human_torso2`, `human_torso5`, `human_neck1`,
`human_neck3` and combinations such as `human_torso2_neck3`.

What is checked: bone set and hierarchy against the template; rest positions, weight sums,
normals and shape keys; deformation match in animation and in a random pose; scene restoration;
units and character height in the FBX. For animation: the baked pose matches the rig's
deformation (within 0.1 mm), no quaternion flips, mesh deformation on real animation < 1 cm, the
clip FBX has one take named after the action with the right length, no geometry, the hierarchy
matches the model FBX, and the action, slot, NLA and frame are restored after export. If the file
has no animation, a synthetic one is put on the rig temporarily.

Before a release, also import the exported FBX files into Cascadeur and Unity and check them
manually (see the Cascadeur and Unity sections of the README).

## Building

```
blender --factory-startup --command extension validate cascadeur_export
blender --factory-startup --command extension build --source-dir cascadeur_export --output-dir dist
```

The output directory must exist. `dist/` is ignored by git.

## Releasing

1. Update `version` in `cascadeur_export/blender_manifest.toml` (semantic versioning) and add a
   section to `CHANGELOG.md`.
2. Commit, then tag and push:
   ```
   git tag v0.3.0
   git push origin main v0.3.0
   ```
3. The `Build` workflow checks that the tag matches the manifest version, builds the zip and
   publishes a GitHub Release with it attached.
4. Optionally upload the same zip to [extensions.blender.org](https://extensions.blender.org).
