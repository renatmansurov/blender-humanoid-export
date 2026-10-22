# Cascadeur Export

A Blender add-on (Blender 4.2+ / 5.x) that turns an Auto-Rig Pro or Rigify character into a clean
UE4 Mannequin-style skeleton for [Cascadeur](https://cascadeur.com) and Unity.

One button builds the skeleton, transfers the skin weights and writes an FBX for Cascadeur and
Unity. A second button bakes the rig's animation onto the same skeleton and writes clip FBX files
for Unity. The source scene is left unchanged.

Russian documentation: [docs/ru/README.md](docs/ru/README.md).

## Installation

1. Download `cascadeur_export-<version>.zip` from the
   [Releases](https://github.com/renatmansurov/blender-to-cascadeur/releases) page.
2. In Blender: Edit → Preferences → Get Extensions → ⌄ → **Install from Disk** → pick the zip.

To build the zip yourself, see [CONTRIBUTING.md](CONTRIBUTING.md#building).

## Usage

3D View → N panel → **Cascadeur** tab.

- **Armature**: an Auto-Rig Pro or Rigify rig. Empty = the active object (or the armature of the
  selected mesh). If a Rigify control rig is selected but the meshes are bound to its deform copy
  (whose bones follow the rig through constraints), the copy is used; the panel shows which rig and
  rig type are in use.
- **File**: output FBX. Empty = `<blend name>_cascadeur.fbx` next to the .blend.
- **Keep Jaw & Eyes**: keep `jaw`, `eye_l`, `eye_r`; all other face bones are merged into
  head/jaw.
- **Export FBX**: run the export.
- **Build Preview** / ✕: build the temporary skeleton and meshes in the `CSC_Export_TMP`
  collection for inspection / remove them. While the preview exists, the source objects are
  temporarily renamed (`*__csc_src`); ✕ restores the names.

All visible meshes with an Armature modifier pointing at the rig are exported.

### Animation (Unity)

The **Animation (Unity)** box in the same panel. The rig and **Keep Jaw & Eyes** are taken from
above, so the skeleton is built exactly as for the model.

- **Current**: what the timeline plays now (the rig's active action or NLA), in the scene frame
  range. **All Actions**: every action that animates the rig's bones, each in its own range (the
  action's manual Frame Range if set), one file per action. During export the action is temporarily
  assigned to the rig with NLA disabled; everything is restored afterwards.
- **Folder**: output folder. Empty = next to the .blend. File name: `<blend name>@<action>.fbx`;
  the take inside is named after the action.
- **FPS** (default 30): clip frame rate. The animation is resampled (the rig is evaluated on
  subframes), so speed and duration in seconds are preserved; a 24 fps scene exported at 30 fps
  gets 30 keys per second instead of 24. 0 = scene frame rate, no resampling. The scene and the animation
  in the .blend are not changed.
- **Export Animation**: run the export. If the animation lives on a Rigify control rig and the
  meshes on its deform copy, the animation is taken from the control rig.

How keys are baked:

- on every frame each bone gets the world-space rotation of its (first) source bone, with all
  constraints, IK and drivers;
- rotation is keyed on every bone (quaternions without sign flips), location only on `pelvis`;
  the other bones keep their rest length, so stretch/squash and scale are not transferred
  (Humanoid ignores them anyway). If the animation stretches bones (e.g. Auto-Rig Pro IK
  auto-stretch), the export warns with the largest stretch and its frame; fix it in the animation
  if that pose matters;
- every frame is keyed, with no simplification in Blender: keyframe reduction is left to Unity
  (Anim. Compression);
- the FBX contains the skeleton and a tiny helper mesh `csc_bind_pose` (one triangle per bone)
  that stores the rest pose as the FBX bind pose. Without a bind pose Unity would build the clip's
  avatar from the first animation frame and distort the retargeting. The default transforms are
  the rest pose too; centimetres, axes and the `root` node match the model;
- root motion is stored in the `pelvis` translation, the `root` object is not animated; Humanoid
  computes root motion from the hips.

## Output

- Armature object `root` (at the origin) → `pelvis` → `spine_01..03` → `neck_01` → `head`
  (+ `jaw`, `eye_l/r`), arms `clavicle/upperarm/lowerarm/hand` + fingers
  `thumb/index/middle/ring/pinky_01..03`, legs `thigh/calf/foot/ball`. 55 bones.
- Auto-Rig Pro twist/stretch segments are merged into one bone per segment, spine bend bones become
  `spine_*`, finger `*_base` bones are merged into `hand`. Weights of unmapped deform bones go to
  the nearest mapped ancestor; if there is none (the bone hangs off a non-deform control), they go
  to the closest export bone by position, with a warning.
- Auto-Rig Pro spine: 1–4+ `c_spine_NN_bend.x` bones. The first → `spine_01`, the last →
  `spine_03`, the middle ones are merged into `spine_02` (with three, one to one).
- Rigify: works on both the generated rig and its deform copy. `DEF-*` bones are used; where a
  `DEF-` bone hangs off an `ORG-` bone, its `DEF-` counterpart is taken as the parent.
  `DEF-spine` → `pelvis`; B-Bone segments (`DEF-upper_arm.L` + `.001` …) are merged into one bone;
  `DEF-pelvis.L/R`, `DEF-breast.*` and the rest go to their ancestor (in a generated rig, an
  `ORG-*` bone also counts as an ancestor for the face). The spine chain is read from the rig:
  bones up to the one the shoulders hang off form the torso (`spine_01..03`; with only two,
  `spine_03` is absent — QRT UE4_New still fills all slots), then the neck (several bones are
  merged into `neck_01`) and the head (`*head*` or the last `DEF-spine.NNN`). Jaw/eyes:
  `DEF-jaw`, `DEF-eye.L/R` (or `ORG-eye.L/R`) if present. Face bones under `jaw_master` (chin,
  lower lip, tongue) go to `jaw`, the rest to `head`.
- Not transferred: B-Bone curvature (FBX/Unity have none — every bone is straight) and meshes
  attached to a bone by parenting rather than an Armature modifier (e.g. eyes parented to
  `ORG-eye`).
- If the merged weights of a vertex exceed 1, that vertex's weights are normalized (otherwise
  Blender would clamp them to 1 and distort proportions; deformation is unchanged).
- Modifiers that only change normals (Weighted Normal, Normal Edit) are baked into custom normals
  on meshes with shape keys, so shape keys are kept. Other modifiers are applied (shape keys are
  lost then, with a warning).
- FBX in centimetres (UnitScaleFactor 1), `root` scale 1, no leaf bones, no animation,
  -Z Forward / Y Up.
- The bone mapping table is in `cascadeur_export/mapping.py`.

## Cascadeur

1. File → Import → FBX, preset **Model**.
2. Rig Mode → **Quick Rigging Tool**: the **UE4_New** template matches the bone names and paths
   (`root/pelvis/...`). Leave the twist slots empty.
3. Generate rig → animate.
4. Export → FBX, preset **Animation**.

## Unity

1. Model: the FBX from Blender. Rig → **Humanoid**, Avatar: Create From This Model.
2. Animations from Cascadeur: Rig → Humanoid, Avatar: **Copy From Other Avatar** (the model's
   avatar). On the Model tab enable *Preserve Hierarchy*.
3. Animations from Blender (`*@*.fbx`, **Export Animation** button): Rig → **Humanoid**, Avatar:
   **Create From This Model**. The avatar is built from the rest pose stored in the file, so the
   clip skeleton only has to be compatible with the model, not identical (fingers, number of spine
   bones): Unity retargets through muscles. If only one side has fingers, they are simply not
   animated. When the clip comes from the same character, **Copy From Other Avatar** (the model's
   avatar) works as well. Animation: *Resample Curves* on, Anim. Compression *Optimal*; *Loop
   Time* for cycles; Root Transform Rotation / Position (Y) / Position (XZ) → *Bake Into Pose*
   (Based Upon *Original*) for in-place animation, or off together with *Apply Root Motion* on the
   Animator for root motion. If the character turns during the clip, the turn is root rotation:
   with Bake Into Pose off and Apply Root Motion off it is lost. Unity may report that some bones
   have scale animation that will be discarded: that is float rounding noise, harmless.

Animation limitations: avoid animating the rig at the object level (moving the root control),
otherwise the skeleton's rest pose depends on the frame the export is built on. Facial animation
other than jaw and eyes is lost, as in the model export.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the repository layout, running the tests and the
release process.

## License

[GPL-3.0-or-later](LICENSE).
