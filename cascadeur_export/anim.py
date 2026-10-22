# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bakes a rig's animation onto the skeleton built by ``build.build()``.

Every target bone gets the world-space deformation of its first source bone, frame by frame,
with scale dropped: only location and rotation are keyed.
"""

import math
import os
import re
from contextlib import contextmanager

import bpy
from mathutils import Matrix

from . import build as build_mod


def is_animated(ob):
    """Whether the active action or an NLA strip of ``ob`` animates its bones."""
    ad = ob.animation_data
    if ad is None:
        return False
    actions = [ad.action] + [s.action for t in ad.nla_tracks for s in t.strips]
    return any(a is not None and animates_bones(a, ob) for a in actions)


def find_animated_rig(rig, deform_rig):
    """The armature that carries the animation: the chosen rig or its deform rig if either is
    animated, otherwise the armature the deform rig's bones are constrained to (Rigify control
    rig of a deform-only copy)."""
    for ob in (rig, deform_rig):
        if is_animated(ob):
            return ob
    counts = {}
    for pb in deform_rig.pose.bones:
        for c in pb.constraints:
            target = getattr(c, "target", None)
            if target is not None and target is not deform_rig and target.type == 'ARMATURE':
                counts[target] = counts.get(target, 0) + 1
    return max(counts, key=counts.get) if counts else rig


def _fcurves(action):
    layers = getattr(action, "layers", None)
    if layers:
        return [fc for layer in layers for strip in layer.strips
                for bag in strip.channelbags for fc in bag.fcurves]
    # Blender before 4.4 (legacy actions).
    return list(getattr(action, "fcurves", ()))


def animates_bones(action, rig):
    for fc in _fcurves(action):
        if not fc.data_path.startswith("pose.bones[") or not fc.keyframe_points:
            continue
        try:
            rig.path_resolve(fc.data_path)
        except ValueError:
            continue
        return True
    return False


def rig_actions(rig):
    """Actions that animate bones of ``rig``."""
    return [a for a in bpy.data.actions if not a.get(build_mod.TMP_PROP) and animates_bones(a, rig)]


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "clip"


def active_clip_name(rig):
    ad = rig.animation_data
    if ad is not None and ad.action is not None:
        return ad.action.name
    if bpy.data.filepath:
        return os.path.splitext(bpy.path.basename(bpy.data.filepath))[0]
    return "clip"


def clips(context, rig, mode):
    """[(clip name, action to play or None for what plays now, first frame, last frame)].
    'ACTIVE' is what the timeline plays in the scene frame range; 'ALL' is every action of the rig
    in its own frame range."""
    scene = context.scene
    if mode == 'ACTIVE':
        return [(active_clip_name(rig), None, scene.frame_start, scene.frame_end)]
    return [(a.name, a, math.floor(a.frame_range[0]), math.ceil(a.frame_range[1]))
            for a in rig_actions(rig)]


@contextmanager
def use_action(context, rig, action):
    """Plays ``action`` alone (NLA off) on ``rig``; restores the animation state and frame."""
    scene = context.scene
    frame = scene.frame_current, scene.frame_subframe
    saved = None
    if action is not None:
        ad = rig.animation_data_create()
        if ad.is_property_readonly("action"):
            raise build_mod.BuildError(f"'{rig.name}' is in NLA tweak mode; leave it first")
        saved = ad.action, getattr(ad, "action_slot", None), ad.use_nla
        ad.use_nla = False
        ad.action = action
        if hasattr(ad, "action_slot") and ad.action_slot is None:
            slots = list(ad.action_suitable_slots) or list(action.slots)
            if slots:
                ad.action_slot = slots[0]
    try:
        yield
    finally:
        if saved is not None:
            ad.action = saved[0]
            if saved[0] is not None and saved[1] is not None:
                ad.action_slot = saved[1]
            ad.use_nla = saved[2]
        scene.frame_set(frame[0], subframe=frame[1])


def _fcurve(action, arm, path, index, group):
    if hasattr(action, "fcurve_ensure_for_datablock"):
        return action.fcurve_ensure_for_datablock(arm, path, index=index, group_name=group)
    return action.fcurves.new(path, index=index, action_group=group)


def scene_fps(scene):
    return scene.render.fps / scene.render.fps_base


def resampled_frames(scene, frame_start, frame_end, fps):
    """[(key frame, scene time)]. Without ``fps`` every scene frame is keyed where it is; with it
    the range is resampled to ``fps`` keys per second from frame 0, keeping its duration."""
    if not fps:
        return [(f, f) for f in range(frame_start, frame_end + 1)]
    ratio = fps / scene_fps(scene)
    return [(i, frame_start + i / ratio) for i in range(round((frame_end - frame_start) * ratio) + 1)]


def bake(context, build, frame_start, frame_end, name, fps=None):
    """Samples the source rig (at subframes when resampling to ``fps``, see resampled_frames())
    and returns an action for ``build.armature`` (left assigned to it) keying location and
    rotation_quaternion of every bone."""
    scene = context.scene
    rig, arm, plan = build.source, build.armature, build.plan
    scale, scale_inv = build.scale, build.scale.inverted()
    bones = arm.data.bones
    rest_inv = {t: (build.rig_matrix @ rig.data.bones[sources[0]].matrix_local).inverted()
                for t, (sources, _) in plan.items()}
    samples = resampled_frames(scene, frame_start, frame_end, fps)
    frames = [key for key, _ in samples]
    locs = {t: [] for t in plan}
    rots = {t: [] for t in plan}
    for _, time in samples:
        whole = math.floor(time)
        scene.frame_set(whole, subframe=time - whole)
        pose = {}
        for t, (sources, parent) in plan.items():
            b = bones[t]
            deform = scale @ rig.matrix_world @ rig.pose.bones[sources[0]].matrix @ rest_inv[t] @ scale_inv
            loc, rot, _ = (deform @ b.matrix_local).decompose()
            pose[t] = Matrix.LocRotScale(loc, rot, None)
            if parent is None:
                basis = b.convert_local_to_pose(pose[t], b.matrix_local, invert=True)
            else:
                basis = b.convert_local_to_pose(pose[t], b.matrix_local, parent_matrix=pose[parent],
                                                parent_matrix_local=bones[parent].matrix_local, invert=True)
            loc, rot, _ = basis.decompose()
            if rots[t]:
                rot.make_compatible(rots[t][-1])
            locs[t].append(loc)
            rots[t].append(rot)

    action = bpy.data.actions.new(name)
    action[build_mod.TMP_PROP] = True
    ad = arm.animation_data_create()
    ad.action = action
    for t in plan:
        arm.pose.bones[t].rotation_mode = 'QUATERNION'
        for prop, values, size in (("location", locs[t], 3), ("rotation_quaternion", rots[t], 4)):
            for i in range(size):
                fc = _fcurve(action, arm, f'pose.bones["{t}"].{prop}', i, t)
                fc.keyframe_points.add(len(frames))
                fc.keyframe_points.foreach_set("co", [x for f, v in zip(frames, values) for x in (f, v[i])])
                fc.update()
    return action
