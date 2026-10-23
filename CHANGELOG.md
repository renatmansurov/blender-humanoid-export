# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Pre-export checks in the panel, shown above the export buttons only when there is a problem:
  errors the export would stop on (unsupported rig, no visible meshes, no animation, NLA tweak mode,
  no output folder) and what would be lost (shape keys dropped by a modifier, meshes parented to a
  bone, hidden meshes, bones the rig does not have, a rig animated as an object).
- When an export reports several warnings, they are also shown together in a popup; the status bar
  only shows the last one.
- CI runs the tests on generated Rigify characters in Blender 4.2 and 5.2
  (`tests/run_rigify_variants.sh`); the extension is only built and released when they pass.

### Changed

- The sidebar tab is called **Export** instead of **Mannequin**.

### Fixed

- Saving the .blend while Build Preview existed stored the renamed source objects (`*__csc_src`)
  and the temporary `CSC_Export_TMP` collection. The preview is now removed before saving, and a
  file saved that way is repaired when it is opened.
- In Blender 4.2 the take in an animation clip was named `<action>.001` instead of `<action>`.

## [0.3.1]

### Fixed

- Animation clips retargeted wrongly in Unity with Avatar "Create From This Model" (distorted arms):
  a skeleton-only FBX has no bind pose, so Unity built the clip's avatar from the first animation
  frame. Clip files now contain a tiny helper mesh `csc_bind_pose` skinned to every bone, which
  stores the rest pose as the bind pose.
- "Copy From Other Avatar" for clips failed with "Parent for 'pelvis' differs" unless *Preserve
  Hierarchy* was on, and with "bone length mismatch" when the animation stretched bones.
- The default transforms in clip files are the rest pose, the same as in the character FBX, instead
  of the pose at the current frame.

### Changed

- Clips key location only on `pelvis`; the other bones keep their rest length (Humanoid ignored
  their translation anyway and Unity warned about it). If the animation stretches bones, the export
  reports the largest stretch and its frame.

## [0.3.0]

First public version.

- Character export: Auto-Rig Pro or Rigify rig → UE4 Mannequin-style skeleton (55 bones) with
  transferred skin weights, FBX for Cascadeur and Unity.
- Animation export: the rig's current animation or all actions baked onto the same skeleton, one
  FBX clip per action, with optional resampling to a target frame rate.
- Build Preview to inspect the export skeleton and meshes in the scene.
