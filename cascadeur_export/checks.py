# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pre-export checks shown in the panel: what an export would fail on or silently lose.

They run on every redraw, so they only look at objects, modifiers, bones and fcurves, never at
mesh data. Each returns [(level, text)], level being 'ERROR', 'WARNING' or 'INFO'.
"""

from . import anim as anim_mod
from . import build as build_mod
from . import mapping

# How many names a single message lists before it says "and N more".
MAX_NAMES = 4


def _names(names):
    names = list(names)
    shown = ", ".join(names[:MAX_NAMES])
    return shown + (f", … ({len(names)})" if len(names) > MAX_NAMES else "")


def _rigs(context, rig):
    """(deform rig the skeleton is built from, rig that carries the animation)."""
    deform = build_mod.find_deform_rig(rig, context.view_layer)
    return deform, anim_mod.find_animated_rig(rig, deform)


def character(context, rig, keep_face=True, extra_chains=False):
    """Checks for Export Character on the chosen (or resolved) armature ``rig``."""
    view_layer = context.view_layer
    deform = build_mod.find_deform_rig(rig, view_layer)
    profile = mapping.detect(deform)
    if profile is None:
        return [('ERROR', f"'{deform.name}' is not an Auto-Rig Pro or Rigify rig")]
    result = []
    warnings = []
    try:
        plan = build_mod._plan_skeleton(deform, keep_face, warnings)
    except build_mod.BuildError as e:
        return [('ERROR', str(e))]
    skipped = [t for t, _, _ in mapping.entries(deform, keep_face) if t not in plan]
    if skipped:
        result.append(('INFO', f"Not in the rig, skipped: {_names(skipped)}"))
    skip_prefixes = tuple(f"'{t}' skipped" for t in skipped)
    extras = build_mod._plan_extras(deform, plan, profile, warnings) if extra_chains else {}
    if extras:
        result.append(('INFO', f"Extra bones kept: {_names(extras)}"))
    result += [('WARNING', w) for w in warnings if not w.startswith(skip_prefixes)]

    meshes = build_mod.find_meshes(deform, view_layer)
    rigs = build_mod.linked_rigs(rig, deform)
    children = build_mod.find_bone_children(rigs, deform, view_layer)
    if not meshes and not children:
        result.append(('ERROR', f"No visible meshes deformed by '{deform.name}'"))
    hidden = [ob.name for ob in view_layer.objects
              if ob.type == 'MESH' and not ob.get(build_mod.TMP_PROP) and ob not in meshes
              and any(m.type == 'ARMATURE' and m.object == deform for m in ob.modifiers)]
    hidden += [ob.name for ob in build_mod.find_bone_children(rigs, deform, view_layer, visible=False)]
    if hidden:
        result.append(('INFO', f"Hidden, not exported: {_names(hidden)}"))
    for ob in meshes + children:
        dropping = build_mod.shape_key_dropping_modifiers(ob)
        if dropping:
            result.append(('WARNING', f"'{ob.name}': shape keys lost, modifiers "
                                      f"{_names(m.name for m in dropping)} are applied"))
    if children:
        plan.update(extras)
        bone_target = build_mod._weight_targets(deform, plan, profile.aliases, [])
        source_target = build_mod._source_targets(plan, profile.aliases)
        for ob in children:
            target, guessed = build_mod._bone_child_target(ob, deform, plan, bone_target, source_target)
            if guessed:
                result.append(('INFO', f"'{ob.name}' is parented to '{ob.parent_bone}', which no deform bone "
                                       f"follows: skinned to the nearest bone '{target}'"))
    if build_mod.has_preview():
        result.append(('INFO', "Preview is built: the source objects stay renamed until it is "
                               "cleared (✕) or the file is saved"))
    return result


def animation(context, rig, source='ACTIVE', directory=None):
    """Checks for Export Animation; ``directory`` is the resolved output folder (None if unknown)."""
    result = []
    if directory is None:
        result.append(('ERROR', "Save the .blend first or set an animation folder"))
    deform, anim_rig = _rigs(context, rig)
    if mapping.detect(deform) is None:
        return result
    if source == 'ACTIVE':
        if not anim_mod.is_animated(anim_rig):
            result.append(('ERROR', f"'{anim_rig.name}' has no action or NLA strips"))
    else:
        if not anim_mod.rig_actions(anim_rig, selected=False):
            result.append(('ERROR', f"No actions animate '{anim_rig.name}'"))
        elif not anim_mod.rig_actions(anim_rig):
            result.append(('ERROR', f"No actions of '{anim_rig.name}' are ticked for export"))
        ad = anim_rig.animation_data
        if ad is not None and ad.use_tweak_mode:
            result.append(('ERROR', f"'{anim_rig.name}' is in NLA tweak mode; leave it first"))
    for ob in dict.fromkeys((rig, deform, anim_rig)):
        if anim_mod.object_transform_animated(ob):
            result.append(('WARNING', f"'{ob.name}' is animated as an object: the skeleton uses its "
                                      f"transform at the current frame"))
    return result


def safe(check, *args, **kwargs):
    """Runs a check; a failure becomes a message instead of breaking the panel."""
    try:
        return check(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - the panel must keep drawing
        return [('WARNING', f"Checks failed: {e}")]
