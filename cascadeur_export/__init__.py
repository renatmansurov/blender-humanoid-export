# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import textwrap

import bpy

from . import anim as anim_mod
from . import build as build_mod
from . import checks
from . import export as export_mod
from . import mapping

ADDON_TITLE = "Mannequin Export"
CHECK_ICONS = {'ERROR': 'CANCEL', 'WARNING': 'ERROR', 'INFO': 'INFO'}
# Line length in popups, which are not bound to a panel width.
POPUP_WIDTH = 90


def _poll_armature(self, ob):
    return ob.type == 'ARMATURE' and not ob.get(build_mod.TMP_PROP)


class CSC_Settings(bpy.types.PropertyGroup):
    armature: bpy.props.PointerProperty(
        name="Armature",
        type=bpy.types.Object,
        poll=_poll_armature,
        description="Auto-Rig Pro or Rigify rig to export. Empty = active object or its armature",
    )
    filepath: bpy.props.StringProperty(
        name="File",
        subtype='FILE_PATH',
        description="Output FBX. Empty = <blend name>_cascadeur.fbx next to the .blend",
    )
    keep_face: bpy.props.BoolProperty(
        name="Keep Jaw & Eyes",
        default=True,
        description="Export jaw and eye bones; all other face bones are merged into head/jaw",
    )
    anim_source: bpy.props.EnumProperty(
        name="Animation",
        items=(
            ('ACTIVE', "Current", "What the timeline plays now, in the scene frame range"),
            ('ALL', "All Actions", "Every action of the rig in its own frame range, one file each"),
        ),
        default='ACTIVE',
    )
    anim_dir: bpy.props.StringProperty(
        name="Folder",
        subtype='DIR_PATH',
        description="Folder for <blend name>@<action>.fbx files. Empty = next to the .blend",
    )
    anim_fps: bpy.props.IntProperty(
        name="FPS",
        default=30,
        min=0,
        max=240,
        description=("Frame rate of the exported clips. The animation is resampled and keeps its "
                     "duration and speed. 0 = scene frame rate, no resampling"),
    )


def _resolve_armature(context):
    settings = context.scene.csc_export
    return settings.armature or build_mod.find_source_armature(context)


def _resolve_filepath(context):
    path = context.scene.csc_export.filepath
    if path:
        path = bpy.path.abspath(path)
        if not path.lower().endswith(".fbx"):
            path += ".fbx"
        return path
    if not bpy.data.filepath:
        return None
    stem = os.path.splitext(bpy.path.basename(bpy.data.filepath))[0]
    return os.path.join(os.path.dirname(bpy.data.filepath), f"{stem}_cascadeur.fbx")


def _resolve_anim_dir(context):
    path = context.scene.csc_export.anim_dir
    if path:
        return bpy.path.abspath(path)
    return os.path.dirname(bpy.data.filepath) if bpy.data.filepath else None


def _anim_filename(clip, sanitize=True):
    stem = os.path.splitext(bpy.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else "animation"
    return f"{stem}@{anim_mod.safe_name(clip) if sanitize else clip}.fbx"


def _restore_mode(context, mode):
    ob = context.view_layer.objects.active
    if mode == 'POSE' and ob is not None and ob.type == 'ARMATURE':
        bpy.ops.object.mode_set(mode='POSE')


def _wrapped_labels(layout, lines, width, icon):
    """One label per wrapped line; the icon is on the first line of each entry."""
    col = layout.column(align=True)
    for text in lines:
        for i, line in enumerate(textwrap.wrap(text, width) or [""]):
            col.label(text=line, icon=icon if i == 0 else 'BLANK1')


def _popup(window_manager, lines, title, icon):
    def draw(menu, _context):
        _wrapped_labels(menu.layout, lines, POPUP_WIDTH, 'DOT')

    window_manager.popup_menu(draw, title=title, icon=icon)


def _notify(message):
    """Prints ``message`` and shows it in a popup once the UI can draw one (handlers run before)."""
    print(f"{ADDON_TITLE}: {message}")
    if bpy.app.background:
        return

    def show():
        wm = bpy.context.window_manager
        if wm is not None and wm.windows:
            with bpy.context.temp_override(window=wm.windows[0]):
                _popup(wm, [message], ADDON_TITLE, 'INFO')

    bpy.app.timers.register(show, first_interval=0.2)


def _report_build(op, context, result):
    for w in result.warnings:
        op.report({'WARNING'}, w)
    # The status bar only shows the last report; several warnings also go into one popup.
    if len(result.warnings) > 1 and not bpy.app.background and context.window is not None:
        _popup(context.window_manager, result.warnings, f"{len(result.warnings)} warnings", 'ERROR')


def _draw_checks(layout, context, items):
    """Draws the check results, or nothing when everything is fine."""
    if not items:
        return
    region = getattr(context, "region", None)
    width = max(20, int((region.width if region else 300) / (7 * context.preferences.view.ui_scale)))
    col = layout.box().column(align=True)
    for level, text in items:
        _wrapped_labels(col, [text], width, CHECK_ICONS[level])


class CSC_OT_export_character(bpy.types.Operator):
    bl_idname = "cascadeur.export_character"
    bl_label = "Export Character"
    bl_description = "Build a clean UE4-style skeleton from the rig and export it with its meshes to FBX"
    bl_options = {'REGISTER'}

    def execute(self, context):
        rig = _resolve_armature(context)
        if rig is None:
            self.report({'ERROR'}, "Select the character armature or one of its meshes")
            return {'CANCELLED'}
        filepath = _resolve_filepath(context)
        if filepath is None:
            self.report({'ERROR'}, "Save the .blend first or set an output file")
            return {'CANCELLED'}
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        mode = context.mode
        try:
            result = build_mod.build(context, rig, context.scene.csc_export.keep_face,
                                     export_mod.centimetre_scale(context.scene))
            export_mod.export_fbx(context, filepath, result)
        except build_mod.BuildError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            build_mod.clear(context)
            _restore_mode(context, mode)
        _report_build(self, context, result)
        self.report({'INFO'}, f"Exported {result.bone_count} bones, {len(result.meshes)} mesh(es) to {filepath}")
        return {'FINISHED'}


class CSC_OT_export_animation(bpy.types.Operator):
    bl_idname = "cascadeur.export_animation"
    bl_label = "Export Animation"
    bl_description = ("Bake the rig's animation onto the UE4-style skeleton and export one "
                      "skeleton-only FBX per clip for Unity")
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = context.scene.csc_export
        rig = _resolve_armature(context)
        if rig is None:
            self.report({'ERROR'}, "Select the character armature or one of its meshes")
            return {'CANCELLED'}
        directory = _resolve_anim_dir(context)
        if directory is None:
            self.report({'ERROR'}, "Save the .blend first or set an animation folder")
            return {'CANCELLED'}
        anim_rig = anim_mod.find_animated_rig(rig, build_mod.find_deform_rig(rig, context.view_layer))
        if settings.anim_source == 'ACTIVE' and not anim_mod.is_animated(anim_rig):
            self.report({'ERROR'}, f"'{anim_rig.name}' has no action or NLA strips")
            return {'CANCELLED'}
        clips = anim_mod.clips(context, anim_rig, settings.anim_source)
        if not clips:
            self.report({'ERROR'}, f"No actions animate '{anim_rig.name}'")
            return {'CANCELLED'}
        os.makedirs(directory, exist_ok=True)
        mode = context.mode
        written = []
        try:
            result = build_mod.build(context, rig, settings.keep_face,
                                     export_mod.centimetre_scale(context.scene), meshes=False)
            fps = settings.anim_fps or None
            for name, action, start, end in clips:
                with anim_mod.use_action(context, anim_rig, action):
                    baked = anim_mod.bake(context, result, start, end, name, fps)
                filepath = os.path.join(directory, _anim_filename(name))
                export_mod.export_anim_fbx(context, filepath, result, baked, name, fps)
                written.append(filepath)
        except build_mod.BuildError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            build_mod.clear(context)
            _restore_mode(context, mode)
        _report_build(self, context, result)
        where = written[0] if len(written) == 1 else f"{len(written)} files in {directory}"
        self.report({'INFO'}, f"Exported {result.bone_count} bones to {where}")
        return {'FINISHED'}


class CSC_OT_build_preview(bpy.types.Operator):
    bl_idname = "cascadeur.build_preview"
    bl_label = "Build Preview"
    bl_description = "Build the export skeleton and meshes in the scene for inspection (no export)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        rig = _resolve_armature(context)
        if rig is None:
            self.report({'ERROR'}, "Select the character armature or one of its meshes")
            return {'CANCELLED'}
        try:
            result = build_mod.build(context, rig, context.scene.csc_export.keep_face)
        except build_mod.BuildError as e:
            build_mod.clear(context)
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        _report_build(self, context, result)
        self.report({'INFO'}, f"Preview: {result.bone_count} bones in '{build_mod.TMP_COLLECTION}'")
        return {'FINISHED'}


class CSC_OT_clear_preview(bpy.types.Operator):
    bl_idname = "cascadeur.clear_preview"
    bl_label = "Clear Preview"
    bl_description = "Remove the preview skeleton and meshes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.mode != 'OBJECT' and context.active_object is not None:
            bpy.ops.object.mode_set(mode='OBJECT')
        build_mod.clear(context)
        return {'FINISHED'}


class CSC_PT_panel(bpy.types.Panel):
    bl_label = "Mannequin Export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Export"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.csc_export
        layout.prop(settings, "armature")
        selected = rig = _resolve_armature(context)
        if rig is not None:
            rig = build_mod.find_deform_rig(rig, context.view_layer)
            profile = mapping.detect(rig)
            kind = profile.name if profile else "unsupported rig"
            if not settings.armature or rig != settings.armature:
                layout.label(text=f"Using: {rig.name} ({kind})", icon='ARMATURE_DATA')
        elif not settings.armature:
            layout.label(text="No armature selected", icon='ARMATURE_DATA')
        layout.prop(settings, "filepath")
        if not settings.filepath:
            path = _resolve_filepath(context)
            layout.label(text=bpy.path.basename(path) if path else "Save the .blend first", icon='FILE')
        layout.prop(settings, "keep_face")
        if selected is not None:
            _draw_checks(layout, context, checks.safe(checks.character, context, selected, settings.keep_face))
        layout.operator(CSC_OT_export_character.bl_idname, icon='EXPORT')
        row = layout.row(align=True)
        row.operator(CSC_OT_build_preview.bl_idname, icon='HIDE_OFF')
        row.operator(CSC_OT_clear_preview.bl_idname, text="", icon='X')

        box = layout.box()
        box.label(text="Animation (Unity)", icon='ACTION')
        box.row().prop(settings, "anim_source", expand=True)
        box.prop(settings, "anim_dir")
        box.prop(settings, "anim_fps")
        scene = context.scene
        fps = anim_mod.scene_fps(scene)
        out = f" -> {settings.anim_fps} fps" if settings.anim_fps and abs(settings.anim_fps - fps) > 1e-6 else ""
        if settings.anim_source == 'ACTIVE':
            if rig is not None:
                anim_rig = anim_mod.find_animated_rig(selected, rig)
                box.label(text=_anim_filename(anim_mod.active_clip_name(anim_rig)), icon='FILE')
            seconds = (scene.frame_end - scene.frame_start) / fps
            box.label(text=f"Frames {scene.frame_start}-{scene.frame_end} at {fps:g} fps{out}, {seconds:.2f} s",
                      icon='TIME')
        else:
            box.label(text=_anim_filename("<action>", sanitize=False), icon='FILE')
            box.label(text=f"{fps:g} fps{out}", icon='TIME')
        if selected is not None:
            _draw_checks(box, context, checks.safe(checks.animation, context, selected, settings.anim_source,
                                                   _resolve_anim_dir(context)))
        box.operator(CSC_OT_export_animation.bl_idname, icon='EXPORT')


@bpy.app.handlers.persistent
def _on_save_pre(*_args):
    """A file saved with the preview would keep the renamed source objects and the temporary ones."""
    if not build_mod.has_preview():
        return
    context = bpy.context
    ob = context.view_layer.objects.active if context.view_layer else None
    if ob is not None and ob.get(build_mod.TMP_PROP) and ob.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            pass
    build_mod.clear(context)
    _notify("Export preview removed before saving; source object names restored")


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    """Repairs files saved with a preview (autosave, a crash, an older version of the add-on)."""
    if build_mod.has_preview():
        restored = build_mod.clear(bpy.context)
        _notify(f"Removed a leftover export preview; restored {restored} object name(s)")


HANDLERS = (
    (bpy.app.handlers.save_pre, _on_save_pre),
    (bpy.app.handlers.load_post, _on_load_post),
)


def _remove_handlers():
    # Also drops copies left by a previous load of the module (add-on reload).
    for handlers, fn in HANDLERS:
        for h in [h for h in handlers if getattr(h, "__qualname__", None) == fn.__qualname__
                  and getattr(h, "__module__", None) == fn.__module__]:
            handlers.remove(h)


classes = (
    CSC_Settings,
    CSC_OT_export_character,
    CSC_OT_export_animation,
    CSC_OT_build_preview,
    CSC_OT_clear_preview,
    CSC_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.csc_export = bpy.props.PointerProperty(type=CSC_Settings)
    _remove_handlers()
    for handlers, fn in HANDLERS:
        handlers.append(fn)


def unregister():
    _remove_handlers()
    del bpy.types.Scene.csc_export
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
