# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0]

First public version.

- Character export: Auto-Rig Pro or Rigify rig → UE4 Mannequin-style skeleton (55 bones) with
  transferred skin weights, FBX for Cascadeur and Unity.
- Animation export: the rig's current animation or all actions baked onto the same skeleton, one
  FBX clip per action, with optional resampling to a target frame rate.
- Build Preview to inspect the export skeleton and meshes in the scene.
