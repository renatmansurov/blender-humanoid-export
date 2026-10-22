# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

"""Builds a test character: Rigify metarig variant -> generated rig -> skinned box mesh. Saves a .blend.

blender -b --factory-startup --python tests/make_rigify_variant.py -- <variant> <out.blend>

Variants: human, basic, human_noface, human_torso2, human_torso5, human_neck1, human_neck3 and
combinations such as human_torso2_neck3. Then run tests/run_tests.py on the saved .blend.
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
bpy.ops.object.mode_set(mode='POSE')
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
