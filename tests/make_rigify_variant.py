# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

"""Builds a test character: Rigify metarig variant -> generated rig -> skinned box mesh. Saves a .blend.

blender -b --factory-startup --python tests/make_rigify_variant.py -- <variant> <out.blend>

Variants: human, basic, human_noface, human_torso2, human_torso5, human_neck1, human_neck3,
human_tail and combinations such as human_torso2_neck3. ``tail`` adds a three-bone tail off the
hips and a two-bone hair chain off the head (extra deform chains). Then run tests/run_tests.py on
the saved .blend.
"""
import sys

import bmesh
import bpy
from mathutils import Vector

variant, out = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.preferences.addon_enable(module="rigify")
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)

if variant.startswith("basic"):
    bpy.ops.object.armature_basic_human_metarig_add()
else:
    bpy.ops.object.armature_human_metarig_add()
meta = bpy.context.active_object
ctx = bpy.context


def remove_bone(name):
    """Deletes a connected chain bone and reconnects its children to its parent."""
    eb = meta.data.edit_bones
    b = eb[name]
    parent = b.parent
    for c in list(b.children):
        if c.use_connect:
            c.use_connect = False
            c.head = b.head
            c.use_connect = True
        c.parent = parent
    eb.remove(b)


def add_chain(name, parent, points):
    """Adds a connected chain name, name.001, ... through ``points``, parented to ``parent``."""
    eb = meta.data.edit_bones
    names = []
    for i, (head, tail) in enumerate(zip(points, points[1:])):
        b = eb.new(name if i == 0 else f"{name}.{i:03d}")
        b.head, b.tail = head, tail
        b.parent = eb[names[-1]] if names else eb[parent]
        b.use_connect = bool(names)
        names.append(b.name)
    return names


def subdivide(name):
    eb = meta.data.edit_bones
    for b in eb:
        b.select = b.select_head = b.select_tail = False
    b = eb[name]
    b.select = b.select_head = b.select_tail = True
    eb.active = b
    bpy.ops.armature.subdivide(number_cuts=1)


bpy.ops.object.mode_set(mode='EDIT')
if "torso2" in variant:
    remove_bone("spine.002")
if "torso5" in variant:
    subdivide("spine.002")
if "neck1" in variant:
    remove_bone("spine.005")
if "neck3" in variant:
    subdivide("spine.004")
if "noface" in variant and "face" in meta.data.edit_bones:
    for b in [b for b in meta.data.edit_bones if b.name == "face" or "face" in [p.name for p in b.parent_recursive]]:
        meta.data.edit_bones.remove(b)
chains = {}
if "tail" in variant:
    eb = meta.data.edit_bones
    hips, head = eb["spine"], eb[[b.name for b in eb if b.name.startswith("spine")][-1]]
    back = Vector((0.0, 1.0, 0.0))
    start = hips.head + back * 0.1
    chains["tail"] = add_chain("tail", "spine", [start + Vector((0.0, 0.15 * i, -0.08 * i)) for i in range(4)])
    start = head.head + Vector((0.0, 0.08, 0.12))
    chains["hair"] = add_chain("hair", head.name, [start + Vector((0.0, 0.06 * i, -0.1 * i)) for i in range(3)])
bpy.ops.object.mode_set(mode='POSE')
if chains:
    meta.pose.bones[chains["tail"][0]].rigify_type = "spines.basic_tail"
    meta.pose.bones[chains["hair"][0]].rigify_type = "limbs.simple_tentacle"
if "torso2" in variant:
    meta.pose.bones["spine"].rigify_parameters.pivot_pos = 1
print("METARIG centre:", [b.name for b in meta.data.bones if b.name.startswith("spine")])

bpy.ops.object.mode_set(mode='OBJECT')
ctx.view_layer.objects.active = meta
meta.select_set(True)
bpy.ops.pose.rigify_generate()
rig = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and o is not meta and o.data.get("rig_id"))
meta.hide_set(True)

# One box per deform bone. Vertices near the head end share weight with the parent's deform
# bone, both at 0.6, so merged segments exceed 1 and blending across joints is exercised.
deform = [b for b in rig.data.bones if b.use_deform]
bm = bmesh.new()
weights = []
for b in deform:
    axis = b.tail_local - b.head_local
    r = max(0.008, min(0.04, axis.length * 0.3))
    side = axis.orthogonal().normalized() * r
    side2 = axis.normalized().cross(side)
    parent = b.parent
    while parent is not None and not parent.use_deform:
        parent = parent.parent
    ring_verts = []
    for t in (0.0, 0.5, 1.0):
        ring = []
        for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
            ring.append(bm.verts.new(b.head_local + axis * t + side * sx + side2 * sy))
            weights.append([(b.name, 1.0)] if t > 0.0 or parent is None else [(b.name, 0.6), (parent.name, 0.6)])
        ring_verts.append(ring)
    for a, c in zip(ring_verts, ring_verts[1:]):
        for i in range(4):
            bm.faces.new((a[i], a[(i + 1) % 4], c[(i + 1) % 4], c[i]))
me = bpy.data.meshes.new("body")
bm.to_mesh(me)
ob = bpy.data.objects.new("body", me)
ctx.scene.collection.objects.link(ob)
for i, ws in enumerate(weights):
    for name, w in ws:
        vg = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
        vg.add([i], w, 'REPLACE')
mod = ob.modifiers.new("Armature", 'ARMATURE')
mod.object = rig
print("RIG", rig.name, "bones", len(rig.data.bones), "deform", len(deform))
print("DEF centre:", [b.name for b in deform if not b.name.endswith((".L", ".R")) and ".L." not in b.name and ".R." not in b.name][:30])
bpy.ops.wm.save_as_mainfile(filepath=out)
