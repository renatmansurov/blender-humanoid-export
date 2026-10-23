# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from . import anim as anim_mod
from . import build as build_mod

# Name of the helper mesh in animation FBX files, see _bind_pose_mesh().
BIND_POSE_MESH = "csc_bind_pose"

# Settings shared by the character and the animation FBX, so both get the same axes, units and
# root node.
COMMON_OPTIONS = dict(
    global_scale=0.01,
    apply_unit_scale=False,
    apply_scale_options='FBX_SCALE_ALL',
    axis_forward='-Z',
    axis_up='Y',
    bake_space_transform=False,
    add_leaf_bones=False,
    primary_bone_axis='Y',
    secondary_bone_axis='X',
    use_armature_deform_only=True,
    armature_nodetype='NULL',
)


def centimetre_scale(scene):
    """Factor that turns scene units into centimetres; pass it to build.build() before export_fbx()."""
    return 100.0 * scene.unit_settings.scale_length


def _export(context, filepath, objects, **options):
    view_layer = context.view_layer
    previous_selection = [ob for ob in view_layer.objects if ob.select_get()]
    previous_active = view_layer.objects.active
    try:
        for ob in previous_selection:
            ob.select_set(False)
        for ob in objects:
            ob.select_set(True)
        view_layer.objects.active = objects[0]
        result = bpy.ops.export_scene.fbx(filepath=filepath, use_selection=True, **COMMON_OPTIONS, **options)
    finally:
        for ob in objects:
            if ob.name in view_layer.objects:
                ob.select_set(False)
        for ob in previous_selection:
            if ob.name in view_layer.objects:
                ob.select_set(True)
        view_layer.objects.active = previous_active
    if 'FINISHED' not in result:
        raise RuntimeError(f"FBX export failed: {result}")


def export_fbx(context, filepath, build):
    """Exports a build whose data is already in centimetres.

    The FBX gets UnitScaleFactor 1 (cm) and no scaling on the root node, which is
    what Cascadeur expects; Unity converts the units on import.
    """
    _export(
        context, filepath, [build.armature, *build.meshes],
        object_types={'ARMATURE', 'MESH'},
        use_mesh_modifiers=True,
        mesh_smooth_type='FACE',
        bake_anim=False,
    )


def _bind_pose_mesh(build):
    """A tiny helper mesh skinned to every bone of ``build.armature``, one triangle per bone.

    A skeleton-only FBX has no bind pose, and Unity then takes the reference pose of a
    "Create From This Model" avatar from the first animation frame. With this mesh the FBX
    carries the rest pose as its bind pose, which Unity uses instead. It also gives the file a
    second top-level node, so Unity keeps the ``root`` node as in the character FBX.
    """
    arm = build.armature
    me = bpy.data.meshes.new(BIND_POSE_MESH)
    size = 0.001 * build.scale[0][0]
    verts = []
    for b in arm.data.bones:
        verts += [b.head_local, b.head_local + b.x_axis * size, b.head_local + b.z_axis * size]
    me.from_pydata([tuple(v) for v in verts], [], [(i, i + 1, i + 2) for i in range(0, len(verts), 3)])
    ob = bpy.data.objects.new(BIND_POSE_MESH, me)
    ob[build_mod.TMP_PROP] = True
    for coll in arm.users_collection:
        coll.objects.link(ob)
    ob.parent = arm
    for i, b in enumerate(arm.data.bones):
        ob.vertex_groups.new(name=b.name).add([3 * i, 3 * i + 1, 3 * i + 2], 1.0, 'REPLACE')
    ob.modifiers.new("Armature", 'ARMATURE').object = arm
    return ob


def export_anim_fbx(context, filepath, build, action, name, fps=None):
    """Exports the skeleton of a mesh-less build with ``action`` (from anim.bake()) as one take.

    The action is played from an NLA strip, so the take is named ``name`` and starts at 0, and no
    other action in the file ends up in the FBX. Every frame is keyed, keyframe reduction is left
    to Unity. ``fps`` (for an action baked with the same ``fps``) is the frame rate the FBX gets;
    the scene's is set to it while exporting. The file also gets the helper mesh from
    _bind_pose_mesh().
    """
    arm = build.armature
    render = context.scene.render
    saved_fps = render.fps, render.fps_base
    # The exporter clears the active action while baking the NLA strips, then restores it and
    # writes the pose of the current frame as the default (Lcl) transforms; a rest-pose action
    # makes those the rest pose, as in the character FBX.
    rest = anim_mod.rest_action(arm)
    ad = arm.animation_data
    track = ad.nla_tracks.new()
    track.name = name
    strip = track.strips.new(name, int(action.frame_range[0]), action)
    # The take is named after the strip. Blender 4.2 names a new strip after its action, which got
    # a ".001" suffix next to the source action of the same name.
    strip.name = name
    if hasattr(strip, "action_slot") and strip.action_slot is None and action.slots:
        strip.action_slot = action.slots[0]
    helper = _bind_pose_mesh(build)
    try:
        if fps:
            render.fps, render.fps_base = fps, 1.0
        _export(
            context, filepath, [arm, helper],
            object_types={'ARMATURE', 'MESH'},
            bake_anim=True,
            bake_anim_use_all_bones=True,
            bake_anim_use_nla_strips=True,
            bake_anim_use_all_actions=False,
            bake_anim_force_startend_keying=True,
            bake_anim_step=1.0,
            bake_anim_simplify_factor=0.0,
        )
    finally:
        render.fps, render.fps_base = saved_fps
        ad.nla_tracks.remove(track)
        ad.action = action
        bpy.data.actions.remove(rest)
        me = helper.data
        bpy.data.objects.remove(helper)
        bpy.data.meshes.remove(me)
