# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

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


def export_anim_fbx(context, filepath, build, action, name, fps=None):
    """Exports the skeleton of a mesh-less build with ``action`` (from anim.bake()) as one take.

    The action is played from an NLA strip, so the take is named ``name`` and starts at 0, and no
    other action in the file ends up in the FBX. Every frame is keyed, keyframe reduction is left
    to Unity. ``fps`` (for an action baked with the same ``fps``) is the frame rate the FBX gets;
    the scene's is set to it while exporting.
    """
    arm = build.armature
    render = context.scene.render
    saved_fps = render.fps, render.fps_base
    ad = arm.animation_data_create()
    ad.action = None
    track = ad.nla_tracks.new()
    track.name = name
    strip = track.strips.new(name, int(action.frame_range[0]), action)
    if hasattr(strip, "action_slot") and strip.action_slot is None and action.slots:
        strip.action_slot = action.slots[0]
    try:
        if fps:
            render.fps, render.fps_base = fps, 1.0
        _export(
            context, filepath, [arm],
            object_types={'ARMATURE'},
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
