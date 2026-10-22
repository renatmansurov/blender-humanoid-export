# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
