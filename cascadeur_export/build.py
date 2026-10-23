# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

"""Builds a temporary clean skeleton plus skinned mesh copies from an ARP or Rigify rig.

Nothing in the source scene is modified except that objects whose names the
export needs (``root`` and the mesh names) are renamed while the temporary
objects exist; ``clear()`` restores them.
"""

from collections import defaultdict

import bpy
from mathutils import Matrix
from mathutils.geometry import intersect_point_line

from . import mapping

TMP_COLLECTION = "CSC_Export_TMP"
ORIGINAL_NAME_PROP = "csc_original_name"
TMP_PROP = "csc_export_tmp"
# Constraints through which a deform bone follows a control bone that a mesh is parented to.
FOLLOW_CONSTRAINTS = {'COPY_TRANSFORMS', 'CHILD_OF', 'COPY_ROTATION', 'COPY_LOCATION'}
# Modifiers that only change normals; on meshes with shape keys they are baked, not applied.
NORMAL_ONLY_MODIFIERS = {'WEIGHTED_NORMAL', 'NORMAL_EDIT'}


class BuildError(Exception):
    pass


class Build:
    def __init__(self):
        self.armature = None
        self.meshes = []
        self.bone_count = 0
        self.warnings = []
        # Targets built from extra deform chains (see _plan_extras()).
        self.extras = []
        # What anim.bake() needs: the deform rig, its plan, the unit scale and the rig's world
        # matrix the skeleton was built from.
        self.source = None
        self.plan = {}
        self.scale = Matrix.Identity(4)
        self.rig_matrix = Matrix.Identity(4)


def find_source_armature(context):
    ob = context.active_object
    if ob is None:
        return None
    if ob.type == 'ARMATURE':
        return ob
    if ob.type == 'MESH':
        for mod in ob.modifiers:
            if mod.type == 'ARMATURE' and mod.object:
                return mod.object
    return None


def find_meshes(rig, view_layer):
    return [
        ob for ob in view_layer.objects
        if ob.type == 'MESH'
        and not ob.get(TMP_PROP)
        and ob.visible_get(view_layer=view_layer)
        and any(m.type == 'ARMATURE' and m.object == rig for m in ob.modifiers)
    ]


def linked_rigs(rig, deform_rig):
    """Armatures a mesh can be parented to for ``rig``: itself, its deform rig and the armatures
    the deform rig's bones are constrained to (the control rig of a deform-only copy)."""
    rigs = {rig, deform_rig}
    for pb in deform_rig.pose.bones:
        for c in pb.constraints:
            target = getattr(c, "target", None)
            if target is not None and target.type == 'ARMATURE':
                rigs.add(target)
    return rigs


def find_bone_children(rigs, deform_rig, view_layer, visible=True):
    """Meshes parented to a bone of one of ``rigs`` that ``deform_rig`` does not deform (eyes,
    helmets, props); they are exported skinned rigidly to one bone. ``visible=False`` returns the
    hidden ones instead."""
    return [
        ob for ob in view_layer.objects
        if ob.type == 'MESH'
        and ob.parent in rigs
        and ob.parent_type == 'BONE'
        and ob.parent_bone in ob.parent.data.bones
        and not ob.get(TMP_PROP)
        and ob.visible_get(view_layer=view_layer) == visible
        and not any(m.type == 'ARMATURE' and m.object == deform_rig for m in ob.modifiers)
    ]


def find_deform_rig(rig, view_layer):
    """For a control rig whose meshes are bound to a deform-only copy (its bones constrained to
    the control rig, a common Rigify game-export setup), returns that copy; otherwise ``rig``."""
    if find_meshes(rig, view_layer):
        return rig
    for ob in view_layer.objects:
        if (ob.type == 'ARMATURE' and ob is not rig and not ob.get(TMP_PROP)
                and any(getattr(c, "target", None) == rig for pb in ob.pose.bones for c in pb.constraints)
                and find_meshes(ob, view_layer)):
            return ob
    return rig


def _plan_skeleton(rig, keep_face, warnings):
    """Returns {target: (sources in head-to-tail order, parent target)} in parent-first order."""
    bones = rig.data.bones
    table = mapping.entries(rig, keep_face)
    table_parent = {t: p for t, _, p in table}
    plan = {}
    for target, sources, parent in table:
        present = [s for s in sources if s in bones]
        missing = [s for s in sources if s not in bones]
        if not present:
            if target in mapping.REQUIRED:
                raise BuildError(f"Source bone(s) {sources} for '{target}' not found in '{rig.name}'")
            warnings.append(f"'{target}' skipped: {sources} not found")
            continue
        if missing:
            warnings.append(f"'{target}': missing {missing}, built from {present}")
        while parent is not None and parent not in plan:
            parent = table_parent[parent]
        if parent is not None and len(present) > 1:
            origin = bones[plan[parent][0][0]].head_local
            present.sort(key=lambda s: (bones[s].head_local - origin).length)
        plan[target] = (present, parent)
    return plan


def _source_targets(plan, aliases):
    """Source bone, or a bone that stands for it (see Profile.aliases), -> its target."""
    source_target = {}
    for t, (sources, _) in plan.items():
        for s in sources:
            for a in aliases(s):
                source_target.setdefault(a, t)
    source_target.update({s: t for t, (sources, _) in plan.items() for s in sources})
    return source_target


def _plan_extras(rig, plan, profile, warnings):
    """Extra deform chains (tail, hair, skirt, cape): every unmapped deform bone the profile does
    not merge on purpose becomes a target of its own, named like the source bone. Returns
    {target: ([source], parent target)} in parent-first order; each one hangs off the target of
    its nearest exported ancestor, or off the nearest target by position if it has none."""
    sources = {s for ss, _ in plan.values() for s in ss}
    source_target = _source_targets(plan, profile.aliases)
    bones = sorted((b for b in rig.data.bones
                    if b.use_deform and b.name not in sources and not profile.merged(b)),
                   key=lambda b: len(b.parent_recursive))
    extras = {}
    orphans = []
    for b in bones:
        parent = next((source_target[p.name] for p in b.parent_recursive if p.name in source_target), None)
        if parent is None:
            parent = _nearest_target(rig, {**plan, **extras}, b.head_local, b.tail_local)
            orphans.append(f"{b.name}->{parent}")
        target = b.name
        while target in plan or target in extras:
            target += "_extra"
        extras[target] = ([b.name], parent)
        for a in profile.aliases(b.name):
            source_target.setdefault(a, target)
        source_target[b.name] = target
    if orphans:
        warnings.append(f"{len(orphans)} extra deform bone(s) without exported ancestor placed under "
                        f"the nearest bone: {orphans[:5]}")
    return extras


def _claim_name(name):
    """Frees an object name by renaming whoever holds it; clear() renames it back."""
    holder = bpy.data.objects.get(name)
    if holder is not None:
        holder[ORIGINAL_NAME_PROP] = name
        holder.name = name + "__csc_src"


def _ensure_object_mode(context):
    if context.mode != 'OBJECT' and context.active_object is not None:
        bpy.ops.object.mode_set(mode='OBJECT')


def _build_armature(context, rig, plan, collection, scale, reversed_targets):
    _claim_name(mapping.ROOT_NAME)
    data = bpy.data.armatures.new(mapping.ROOT_NAME)
    arm = bpy.data.objects.new(mapping.ROOT_NAME, data)
    arm[TMP_PROP] = True
    collection.objects.link(arm)

    context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bones = rig.data.bones
        mat = scale @ rig.matrix_world
        rot = rig.matrix_world.to_3x3()
        for target, (sources, parent) in plan.items():
            first, last = bones[sources[0]], bones[sources[-1]]
            eb = data.edit_bones.new(target)
            head, tail = mat @ first.head_local, mat @ last.tail_local
            if target in reversed_targets:
                head, tail = tail, head
            eb.head = head
            eb.tail = tail
            # Bone.z_axis is relative to the parent; matrix_local is in armature space.
            eb.align_roll((rot @ first.matrix_local.col[2].xyz).normalized())
            eb.use_deform = True
            eb.use_connect = False
            if parent is not None:
                eb.parent = data.edit_bones[parent]
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    return arm


def _weight_targets(rig, plan, aliases, warnings):
    """Maps every deform bone of the rig to the target bone that receives its weights."""
    source_target = _source_targets(plan, aliases)
    result = {}
    orphans = []
    for bone in rig.data.bones:
        if not bone.use_deform:
            continue
        b = bone
        while b is not None and b.name not in source_target:
            b = b.parent
        if b is None:
            target = _nearest_target(rig, plan, bone.head_local, bone.tail_local)
            orphans.append(f"{bone.name}->{target}")
            result[bone.name] = target
        else:
            result[bone.name] = source_target[b.name]
    if orphans:
        warnings.append(f"{len(orphans)} deform bone(s) without mapped ancestor merged into the nearest bone: {orphans[:5]}")
    return result


def _nearest_target(rig, plan, head, tail):
    """Target whose source bones pass closest to the middle of the segment ``head``-``tail`` (in
    the rig's armature space)."""
    bones = rig.data.bones
    mid = (head + tail) / 2

    def distance(source):
        b = bones[source]
        point, factor = intersect_point_line(mid, b.head_local, b.tail_local)
        if factor < 0.0:
            point = b.head_local
        elif factor > 1.0:
            point = b.tail_local
        return (point - mid).length

    return min(plan, key=lambda t: min(distance(s) for s in plan[t][0]))


def _bone_child_target(ob, rig, plan, bone_target, source_target):
    """(target, guessed) for a mesh parented to a bone (see find_bone_children()): the target of
    that bone if it is a source or a deform bone, else of a source or deform bone that follows it
    as its child or through a constraint (controls such as ARP's c_head.x or Rigify's head), else
    the nearest target by position (``guessed``). A control's ancestors are not used: they often
    lead elsewhere (c_head.x hangs off the neck controls)."""
    name = ob.parent_bone
    for known in (source_target, bone_target):
        if name in known:
            return known[name], False
    for known in (source_target, bone_target):
        for pb in rig.pose.bones:
            if pb.name in known and (
                    (ob.parent == rig and pb.parent is not None and pb.parent.name == name)
                    or any(c.type in FOLLOW_CONSTRAINTS and getattr(c, "target", None) == ob.parent
                           and getattr(c, "subtarget", "") == name for c in pb.constraints)):
                return known[pb.name], False
    bone = ob.parent.data.bones[name]
    to_rig = rig.matrix_world.inverted() @ ob.parent.matrix_world
    return _nearest_target(rig, plan, to_rig @ bone.head_local, to_rig @ bone.tail_local), True


def _rest_world(ob):
    """World matrix of a mesh parented to a bone with the armature in rest pose (its matrix_world
    follows the current pose)."""
    bone = ob.parent.data.bones[ob.parent_bone]
    return (ob.parent.matrix_world @ bone.matrix_local @ Matrix.Translation((0.0, bone.length, 0.0))
            @ ob.matrix_parent_inverse @ ob.matrix_basis)


def _rigid_skin(ob, target, targets, arm):
    """Skins the whole of ``ob`` to ``target`` with weight 1."""
    for g in list(ob.vertex_groups):
        if g.name in targets:
            ob.vertex_groups.remove(g)
    ob.vertex_groups.new(name=target).add(list(range(len(ob.data.vertices))), 1.0, 'REPLACE')
    mod = ob.modifiers.new("Armature", 'ARMATURE')
    mod.object = arm
    if len(ob.modifiers) > 1:
        ob.modifiers.move(len(ob.modifiers) - 1, 0)


def _remap_weights(ob, bone_target, targets):
    index_target = {g.index: bone_target[g.name] for g in ob.vertex_groups if g.name in bone_target}
    weights = defaultdict(dict)
    for v in ob.data.vertices:
        for ge in v.groups:
            t = index_target.get(ge.group)
            if t is not None and ge.weight > 0.0:
                acc = weights[t]
                acc[v.index] = acc.get(v.index, 0.0) + ge.weight

    # Merged weights above 1 would be clamped by Blender and change the vertex's weight ratios.
    # Such vertices are normalized instead; the Armature modifier normalizes anyway.
    totals = defaultdict(float)
    over = set()
    for acc in weights.values():
        for i, w in acc.items():
            totals[i] += w
            if w > 1.0:
                over.add(i)
    for acc in weights.values():
        for i in over.intersection(acc):
            acc[i] /= totals[i]

    # Deform groups are replaced; other groups (masks etc.) stay unless they clash with a new name.
    for g in list(ob.vertex_groups):
        if g.index in index_target or g.name in targets:
            ob.vertex_groups.remove(g)

    for t in targets:
        acc = weights.get(t)
        if not acc:
            continue
        vg = ob.vertex_groups.new(name=t)
        by_weight = defaultdict(list)
        for i, w in acc.items():
            by_weight[w].append(i)
        for w, indices in by_weight.items():
            vg.add(indices, w, 'REPLACE')


def _bake_normals(context, ob, modifiers):
    """Stores the normals the modifiers produce as custom normals, keeping the shape keys that
    applying them (here or in the FBX exporter) would drop."""
    armatures = [m for m in ob.modifiers if m.type == 'ARMATURE']
    for m in armatures:
        m.show_viewport = False
    show_only, active_key = ob.show_only_shape_key, ob.active_shape_key_index
    ob.show_only_shape_key, ob.active_shape_key_index = True, 0
    depsgraph = context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(depsgraph)
    normals = [n.vector.copy() for n in ev.data.corner_normals]
    ob.show_only_shape_key, ob.active_shape_key_index = show_only, active_key
    for m in armatures:
        m.show_viewport = True
    for m in modifiers:
        ob.modifiers.remove(m)
    ob.data.normals_split_custom_set(normals)


def shape_key_dropping_modifiers(ob):
    """Modifiers whose application on export drops the shape keys of ``ob`` (empty if it keeps them)."""
    others = [m for m in ob.modifiers if m.type != 'ARMATURE']
    if not ob.data.shape_keys or all(m.type in NORMAL_ONLY_MODIFIERS for m in others):
        return []
    return others


def _apply_modifiers(context, ob, warnings):
    """Bakes non-armature modifiers into the mesh, so scaling the data cannot change their effect."""
    others = [m for m in ob.modifiers if m.type != 'ARMATURE']
    if not others:
        return
    if ob.data.shape_keys and not shape_key_dropping_modifiers(ob):
        _bake_normals(context, ob, others)
        return
    if ob.data.shape_keys:
        warnings.append(f"'{ob.name}': modifiers {[m.name for m in others]} applied, shape keys dropped")
    armatures = [m for m in ob.modifiers if m.type == 'ARMATURE']
    for m in armatures:
        m.show_viewport = False
    depsgraph = context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(
        ob.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
    old = ob.data
    ob.data = me
    bpy.data.meshes.remove(old)
    for m in others:
        ob.modifiers.remove(m)
    for m in armatures:
        m.show_viewport = True


def _copy_mesh(context, src, rig, arm, collection, scale, warnings, world=None):
    """Copies ``src`` into the export armature's space; ``world`` replaces its world matrix (see
    _rest_world())."""
    name = src.name
    _claim_name(name)
    me = src.data.copy()
    ob = src.copy()
    ob.data = me
    ob.name = name
    del ob[ORIGINAL_NAME_PROP]
    ob[TMP_PROP] = True
    ob.animation_data_clear()
    ob.constraints.clear()
    collection.objects.link(ob)
    if scale != Matrix.Identity(4):
        _apply_modifiers(context, ob, warnings)
        me = ob.data
    ob.parent = None
    if world is not None:
        ob.parent_type, ob.parent_bone = 'OBJECT', ""
    world = scale @ (src.matrix_world if world is None else world)
    me.transform(world, shape_keys=True)
    if world.determinant() < 0.0:
        me.flip_normals()
    ob.matrix_basis = Matrix.Identity(4)
    for mod in list(ob.modifiers):
        if mod.type == 'ARMATURE':
            if mod.object == rig:
                mod.object = arm
            else:
                ob.modifiers.remove(mod)
    ob.parent = arm
    ob.matrix_parent_inverse = Matrix.Identity(4)
    return ob


def build(context, rig, keep_face=True, unit_scale=1.0, meshes=True, extra_chains=False):
    """``unit_scale`` multiplies all coordinates (100 = bake metres into centimetres).
    ``meshes=False`` builds the skeleton only, also for a rig that deforms no meshes.
    ``extra_chains`` keeps unmapped deform chains as bones of their own (see _plan_extras())."""
    chosen = rig
    rig = find_deform_rig(rig, context.view_layer)
    profile = mapping.detect(rig)
    if profile is None:
        raise BuildError(f"'{rig.name}' is not an Auto-Rig Pro or Rigify rig "
                         f"(no {mapping.ARP.signature} or {mapping.RIGIFY.signature})")
    clear(context)
    _ensure_object_mode(context)

    result = Build()
    sources = find_meshes(rig, context.view_layer) if meshes else []
    children = find_bone_children(linked_rigs(chosen, rig), rig, context.view_layer) if meshes else []
    if meshes and not sources and not children:
        raise BuildError(f"No visible meshes deformed by '{rig.name}'")

    plan = _plan_skeleton(rig, keep_face, result.warnings)
    if extra_chains:
        extras = _plan_extras(rig, plan, profile, result.warnings)
        plan.update(extras)
        result.extras = list(extras)
    collection = bpy.data.collections.new(TMP_COLLECTION)
    context.scene.collection.children.link(collection)

    scale = Matrix.Scale(unit_scale, 4)
    previous_active = context.view_layer.objects.active
    result.armature = _build_armature(context, rig, plan, collection, scale, profile.reversed)
    context.view_layer.objects.active = previous_active
    result.bone_count = len(plan)
    result.source, result.plan, result.scale = rig, plan, scale
    result.rig_matrix = rig.matrix_world.copy()
    if not sources and not children:
        context.view_layer.update()
        return result
    bone_target = _weight_targets(rig, plan, profile.aliases, result.warnings)
    for src in sources:
        ob = _copy_mesh(context, src, rig, result.armature, collection, scale, result.warnings)
        _remap_weights(ob, bone_target, list(plan))
        result.meshes.append(ob)
    source_target = _source_targets(plan, profile.aliases)
    for src in children:
        target, _ = _bone_child_target(src, rig, plan, bone_target, source_target)
        ob = _copy_mesh(context, src, rig, result.armature, collection, scale, result.warnings,
                        world=_rest_world(src))
        _rigid_skin(ob, target, list(plan), result.armature)
        result.meshes.append(ob)
    context.view_layer.update()
    return result


def has_preview():
    """Whether temporary export data or renamed source objects are in the file."""
    return (TMP_COLLECTION in bpy.data.collections
            or any(ob.library is None and (ob.get(TMP_PROP) or ob.get(ORIGINAL_NAME_PROP) is not None)
                   for ob in bpy.data.objects)
            or any(a.get(TMP_PROP) for a in bpy.data.actions))


def clear(context):
    """Removes temporary objects and baked actions and restores renamed originals. Returns the
    number of restored names."""
    for action in [a for a in bpy.data.actions if a.get(TMP_PROP)]:
        bpy.data.actions.remove(action)
    coll = bpy.data.collections.get(TMP_COLLECTION)
    for ob in [o for o in bpy.data.objects if o.get(TMP_PROP)]:
        data = ob.data
        bpy.data.objects.remove(ob)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)
    if coll is not None:
        bpy.data.collections.remove(coll)
    restored = 0
    for ob in bpy.data.objects:
        original = ob.get(ORIGINAL_NAME_PROP)
        # Linked objects cannot be renamed; only local ones are ever renamed by build().
        if original is not None and ob.library is None:
            del ob[ORIGINAL_NAME_PROP]
            ob.name = original
            restored += 1
    # Removed objects stay in the view layer's object list until it is synced again.
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()
    return restored
