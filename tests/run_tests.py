# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

"""Headless checks for cascadeur_export. Never saves the .blend.

    blender -b <character.blend> --factory-startup --python tests/run_tests.py -- [out_dir]
"""

import json
import math
import os
import random
import sys
import time

import bpy
from mathutils import Matrix, Vector

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import cascadeur_export  # noqa: E402
from cascadeur_export import anim as A, build as B, checks as C, export as E, mapping as M  # noqa: E402

# Cascadeur's Quick Rigging Tool template the skeleton is checked against; CSC_UE4_TEMPLATE overrides it.
UE4_TEMPLATE = os.environ.get(
    "CSC_UE4_TEMPLATE", r"C:\Program Files\Cascadeur\resources\autorig_templates\UE4_New.qrigcasc")
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT_DIR = argv[0] if argv else os.path.join(REPO, "tests", "out")
os.makedirs(OUT_DIR, exist_ok=True)

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def world_coords(ob, evaluated=False):
    if evaluated:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        coords = [ev.matrix_world @ v.co for v in me.vertices]
        ev.to_mesh_clear()
        return coords
    return [ob.matrix_world @ v.co for v in ob.data.vertices]


def transfer_pose(rig, arm, plan):
    """Gives each target bone the world-space deformation of its first source bone."""
    for target, (sources, _) in plan.items():
        sb = rig.data.bones[sources[0]]
        deform = rig.matrix_world @ rig.pose.bones[sources[0]].matrix @ sb.matrix_local.inverted() @ rig.matrix_world.inverted()
        arm.pose.bones[target].matrix = (arm.matrix_world.inverted() @ deform @ arm.matrix_world
                                         @ arm.data.bones[target].matrix_local)
        bpy.context.view_layer.update()


def corner_normals(ob):
    """Rest-pose corner normals in world space, with modifiers but without armature deformation."""
    mods = [m for m in ob.modifiers if m.type == 'ARMATURE' and m.show_viewport]
    for m in mods:
        m.show_viewport = False
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    rot = ev.matrix_world.to_3x3()
    result = [(rot @ n.vector).normalized() for n in ev.data.corner_normals]
    for m in mods:
        m.show_viewport = True
    return result


def deform_weight_sums(ob, deform_names):
    idx = {g.index for g in ob.vertex_groups if g.name in deform_names}
    return [sum(g.weight for g in v.groups if g.group in idx) for v in ob.data.vertices]


def builtin_paths():
    """UE4_New joint paths without the twist bones, for machines without Cascadeur (CI)."""
    parents = {"pelvis": "root", "spine_01": "pelvis", "spine_02": "spine_01", "spine_03": "spine_02",
               "neck_01": "spine_03", "head": "neck_01"}
    for s in "lr":
        parents.update({f"clavicle_{s}": "spine_03", f"upperarm_{s}": f"clavicle_{s}",
                        f"lowerarm_{s}": f"upperarm_{s}", f"hand_{s}": f"lowerarm_{s}",
                        f"thigh_{s}": "pelvis", f"calf_{s}": f"thigh_{s}", f"foot_{s}": f"calf_{s}",
                        f"ball_{s}": f"foot_{s}"})
        for f in M.FINGERS:
            parents.update({f"{f}_01_{s}": f"hand_{s}", f"{f}_02_{s}": f"{f}_01_{s}", f"{f}_03_{s}": f"{f}_02_{s}"})
    paths = {}
    for joint, parent in parents.items():
        path = []
        while parent != "root":
            path.insert(0, parent)
            parent = parents[parent]
        paths[joint] = ["root"] + path
    # UE4_New has no slot for spine_03, it is only on the paths.
    del paths["spine_03"]
    return paths


def template_paths():
    builtin = builtin_paths()
    if not os.path.exists(UE4_TEMPLATE):
        print(f"  {UE4_TEMPLATE} not found, using the built-in UE4_New hierarchy")
        return builtin
    with open(UE4_TEMPLATE, encoding="utf-8") as f:
        doc = json.load(f)
    paths = {}
    for d in doc["Document"]:
        if d["Title"] == "Twist bones":
            continue
        for s in d["Sections"]:
            for n in s["Names"]:
                paths[n["Joint name"]] = n.get("Joint path") or []
    check(paths == builtin, "built-in UE4_New hierarchy matches the template")
    return paths


def fbx_summary(path):
    from io_scene_fbx import parse_fbx

    root, _version = parse_fbx.parse(path)

    def child(elem, eid):
        return next((e for e in elem.elems if e.id == eid), None)

    def props70(elem):
        p = child(elem, b"Properties70")
        return {e.props[0]: e.props[4:] for e in p.elems} if p else {}

    info = {"unit": props70(child(root, b"GlobalSettings")).get(b"UnitScaleFactor")}
    objects = child(root, b"Objects")
    for e in objects.elems:
        if e.id == b"Model":
            name = e.props[1].split(b"\x00")[0].decode()
            if name in ("root", "pelvis"):
                info[name] = {k.decode(): v for k, v in props70(e).items() if k.startswith(b"Lcl")}
        if e.id == b"Geometry":
            verts = child(e, b"Vertices").props[0]
            zs = verts[2::3]
            ys = verts[1::3]
            info.setdefault("geom", []).append((round(min(ys), 3), round(max(ys), 3), round(min(zs), 3), round(max(zs), 3)))
    return info


FBX_TICKS = 46186158000


def fbx_anim_summary(path):
    """Unit, take names and lengths (seconds), geometry count and {model: parent model}."""
    from io_scene_fbx import parse_fbx

    root, _version = parse_fbx.parse(path)

    def child(elem, eid):
        return next((e for e in elem.elems if e.id == eid), None)

    def props70(elem):
        p = child(elem, b"Properties70")
        return {e.props[0]: e.props[4:] for e in p.elems} if p else {}

    def name(e):
        return e.props[1].split(b"\x00")[0].decode()

    settings = props70(child(root, b"GlobalSettings"))
    info = {"unit": settings.get(b"UnitScaleFactor")[0], "fps": settings.get(b"CustomFrameRate", [None])[0],
            "takes": {}, "geometry": 0, "keys": 0, "bind_pose": set(), "lcl": {}, "moving": set()}
    models = {}
    curves = {}
    for e in child(root, b"Objects").elems:
        if e.id == b"Model":
            models[e.props[0]] = name(e)
            p = props70(e)
            info["lcl"][name(e)] = (p.get(b"Lcl Translation", (0.0, 0.0, 0.0)), p.get(b"Lcl Rotation", (0.0, 0.0, 0.0)))
        elif e.id == b"AnimationCurve":
            values = child(e, b"KeyValueFloat").props[0]
            info["keys"] = max(info["keys"], len(values))
            curves[e.props[0]] = max(values) - min(values) if len(values) else 0.0
        elif e.id == b"Geometry":
            info["geometry"] += 1
        elif e.id == b"AnimationStack":
            p = props70(e)
            info["takes"][name(e)] = (p.get(b"LocalStop", [0])[0] - p.get(b"LocalStart", [0])[0]) / FBX_TICKS
        elif e.id == b"Pose" and e.props[2] == b"BindPose":
            info["bind_pose"] = {n.props[0] for pn in e.elems if pn.id == b"PoseNode" for n in pn.elems if n.id == b"Node"}
    parents = {}
    node_range = {}
    translation_nodes = {}
    for c in child(root, b"Connections").elems:
        # Bones are also connected to skin clusters; only Model -> Model (or scene root 0) counts.
        if c.props[0] == b"OO" and c.props[1] in models and (c.props[2] in models or c.props[2] == 0):
            parents[models[c.props[1]]] = models.get(c.props[2])
        elif c.props[0] == b"OP" and c.props[1] in curves:
            node_range[c.props[2]] = max(node_range.get(c.props[2], 0.0), curves[c.props[1]])
        elif c.props[0] == b"OP" and c.props[2] in models and c.props[3] == b"Lcl Translation":
            translation_nodes[c.props[1]] = models[c.props[2]]
    info["parents"] = parents
    info["bind_pose"] = {models[i] for i in info["bind_pose"] if i in models}
    # Models whose translation changes during the take by more than 0.01 cm.
    info["moving"] = {m for n, m in translation_nodes.items() if node_range.get(n, 0.0) > 1e-2}
    return info


def anim_state(ob):
    ad = ob.animation_data
    if ad is None:
        return None
    return ad.action, getattr(ad, "action_slot", None), ad.use_nla


def synthetic_action(rig, scene):
    """Keys random control rotations at three frames when the file has no animation."""
    rng = random.Random(2)
    action = bpy.data.actions.new("test_synthetic")
    rig.animation_data_create().action = action
    controls = [pb for pb in rig.pose.bones if not pb.name.startswith(("DEF-", "ORG-", "MCH-", "VIS"))
                and (M.detect(rig) is not M.ARP or pb.name.startswith("c_"))]
    for frame in (scene.frame_start, (scene.frame_start + scene.frame_end) // 2, scene.frame_end):
        for pb in controls:
            pb.rotation_mode = 'XYZ'
            pb.rotation_euler = [math.radians(rng.uniform(-10, 10)) for _ in range(3)]
            pb.keyframe_insert("rotation_euler", frame=frame)
    return action


def test_animation(ctx, rig, names_before):
    print("[animation]")
    scene = ctx.scene
    anim_rig = A.find_animated_rig(rig, rig)
    synthetic = not A.is_animated(anim_rig)
    if synthetic:
        synthetic_action(anim_rig, scene)
        print(f"  no animation on {anim_rig.name}: keyed a synthetic one")
    print(f"  animated rig {anim_rig.name}, action {anim_state(anim_rig)[0].name}")

    result = B.build(ctx, rig, keep_face=True)
    srcs = {m[B.ORIGINAL_NAME_PROP]: m for m in bpy.data.objects
            if m.get(B.ORIGINAL_NAME_PROP) and not m.get(B.TMP_PROP)}
    start, end = scene.frame_start, scene.frame_end
    with A.use_action(ctx, anim_rig, None):
        action = A.bake(ctx, result, start, end, "bake_test")
    check(result.armature.animation_data.action == action, "baked action assigned to the skeleton")
    flips = 0
    by_bone = {}
    for fc in A._fcurves(action):
        if fc.data_path.endswith("rotation_quaternion"):
            by_bone.setdefault(fc.data_path, [None] * 4)[fc.array_index] = [k.co[1] for k in fc.keyframe_points]
    for comps in by_bone.values():
        for i in range(1, len(comps[0])):
            if sum(comps[c][i] * comps[c][i - 1] for c in range(4)) < 0.0:
                flips += 1
    check(len(by_bone) == len(result.plan), f"rotation keyed on all {len(result.plan)} bones")
    check(flips == 0, f"no quaternion sign flips ({flips})")
    arm, src = result.armature, result.source
    keyed = {fc.data_path.split('"')[1] for fc in A._fcurves(action) if fc.data_path.endswith(".location")}
    roots = {t for t, (_, parent) in result.plan.items() if parent is None}
    check(keyed == roots, f"location keyed only on the root bone(s) {sorted(keyed)}")
    for frame in sorted({start, (start + end) // 2, end}):
        scene.frame_set(frame)
        # The baked pose must be exactly the transfer_pose() deformation with scale dropped: the
        # rotation of every bone and the location of the root; the other bones keep rest offsets.
        max_loc = max_rot = stretch = 0.0
        for target, (sources, _) in result.plan.items():
            sb = src.data.bones[sources[0]]
            deform = src.matrix_world @ src.pose.bones[sources[0]].matrix @ (result.rig_matrix @ sb.matrix_local).inverted()
            loc, rot, _ = (deform @ arm.data.bones[target].matrix_local).decompose()
            got_loc, got_rot, _ = (arm.matrix_world @ arm.pose.bones[target].matrix).decompose()
            if target in roots:
                max_loc = max(max_loc, (loc - got_loc).length)
            else:
                stretch = max(stretch, (loc - got_loc).length)
            max_rot = max(max_rot, math.degrees(rot.rotation_difference(got_rot).angle))
        check(max_loc < 1e-4 and max_rot < 0.01,
              f"frame {frame}: baked pose = rig deformation (root max {max_loc*100:.4f} cm, {max_rot:.4f} deg)")
        # Merged bones lose some detail (see [pose comparison]); real animation is gated, a random
        # synthetic one or a frame with dropped stretch is only reported.
        stretched = stretch > A.STRETCH_TOLERANCE
        if stretched:
            print(f"  frame {frame}: stretch up to {stretch*100:.2f} cm dropped")
        for ob in result.meshes:
            a = world_coords(srcs[ob.name], evaluated=True)
            b = world_coords(ob, evaluated=True)
            d = [(x - y).length for x, y in zip(a, b)]
            mean = sum(d) / len(d)
            print(f"  frame {frame} {ob.name}: mean {mean*100:.2f} cm, max {max(d)*100:.2f} cm")
            if ob.name.lower().startswith("body") and not synthetic and not stretched:
                check(mean < 0.01, f"baked frame {frame} {ob.name}: mean deviation < 1 cm")

    # Resampled to 3x the scene rate, every third key must be the key of the matching scene frame.
    values = {(fc.data_path, fc.array_index): [k.co[1] for k in fc.keyframe_points] for fc in A._fcurves(action)}
    scene_fps = A.scene_fps(scene)
    with A.use_action(ctx, anim_rig, None):
        resampled = A.bake(ctx, result, start, end, "bake_test_x3", fps=3 * scene_fps)
    max_dev = 0.0
    count = 0
    for fc in A._fcurves(resampled):
        keys = fc.keyframe_points
        count = len(keys)
        ref = values[(fc.data_path, fc.array_index)]
        max_dev = max([max_dev] + [abs(keys[3 * i].co[1] - v) for i, v in enumerate(ref)])
    check(count == 3 * (end - start) + 1, f"resampled to {count} keys = 3 x {end - start} frames + 1")
    check(max_dev < 1e-4, f"resampled keys match scene-frame keys (max {max_dev:.2e})")
    B.clear(ctx)
    check(not any(a.get(B.TMP_PROP) for a in bpy.data.actions), "baked actions removed by clear")

    character = fbx_anim_summary(ctx.scene.csc_export.filepath)
    settings = ctx.scene.csc_export
    settings.anim_dir = OUT_DIR
    fps = A.scene_fps(scene)
    # Current at 30 fps (resampled unless the scene already runs at 30), All at the scene rate.
    for mode, out_fps in (('ACTIVE', 30), ('ALL', 0)):
        settings.anim_source = mode
        settings.anim_fps = out_fps
        before = anim_state(anim_rig), scene.frame_current, scene.render.fps, scene.render.fps_base
        clips = A.clips(ctx, anim_rig, mode)
        paths = [os.path.join(OUT_DIR, cascadeur_export._anim_filename(c[0])) for c in clips]
        for p in paths:
            if os.path.exists(p):
                os.remove(p)
        ctx.view_layer.objects.active = rig
        check(bpy.ops.cascadeur.export_animation() == {'FINISHED'}, f"{mode}: animation export finished")
        check((anim_state(anim_rig), scene.frame_current, scene.render.fps, scene.render.fps_base) == before,
              f"{mode}: rig action, slot, NLA, frame and scene fps restored")
        check(sorted(o.name for o in bpy.data.objects) == names_before, f"{mode}: object names restored")
        check(not any(a.get(B.TMP_PROP) for a in bpy.data.actions), f"{mode}: no baked actions left")
        for (name, _action, first, last), path in zip(clips, paths):
            if not os.path.exists(path):
                check(False, f"{mode}: {os.path.basename(path)} written")
                continue
            info = fbx_anim_summary(path)
            print(f"  {os.path.basename(path)}: takes {info['takes']}, {len(info['parents'])} models")
            check(list(info["takes"]) == [name], f"{mode}: one take named {name} ({list(info['takes'])})")
            length = next(iter(info["takes"].values()), 0.0)
            rate = out_fps or fps
            # Resampling rounds the duration to whole output frames.
            check(abs(length - (last - first) / fps) < 0.5 / rate + 1e-3,
                  f"{mode}: take length {length:.3f}s = {last - first} frames at {fps:g} fps")
            keys = round((last - first) * rate / fps) + 1
            check(info["keys"] == keys and abs(info["fps"] - rate) < 1e-3,
                  f"{mode}: {info['keys']} keys at {info['fps']:g} fps (expected {keys} at {rate:g})")
            check(info["geometry"] == 1, f"{mode}: only the bind pose helper geometry ({info['geometry']})")
            check(abs(info["unit"] - 1.0) < 1e-6, f"{mode}: FBX unit is centimetres")
            bones = set(info["parents"]) - {"root", E.BIND_POSE_MESH}
            common = set(info["parents"]) & set(character["parents"])
            check("root" in common and bones <= common, f"{mode}: skeleton is a subset of the character's")
            diff = {n for n in bones if info["parents"][n] != character["parents"][n]}
            check(not diff, f"{mode}: bone parents match the character FBX {sorted(diff)[:5]}")
            check(info["parents"].get(E.BIND_POSE_MESH, "") is None,
                  f"{mode}: helper mesh is a second top-level node next to root")
            missing = bones - info["bind_pose"]
            check(not missing, f"{mode}: bind pose covers every bone (missing {sorted(missing)[:5]})")
            # Default transforms are the rest pose, the same as in the character FBX.
            lcl_diff = [n for n in bones | {"root"} if any(
                abs(a - b) > 1e-3 for x, y in zip(info["lcl"][n], character["lcl"][n]) for a, b in zip(x, y))]
            check(not lcl_diff, f"{mode}: default transforms = character rest pose {sorted(lcl_diff)[:5]}")
            check(info["moving"] <= {"pelvis"}, f"{mode}: only pelvis translation animated {sorted(info['moving'])[:5]}")


def saved_object_names(path):
    with bpy.data.libraries.load(path) as (data_from, _data_to):
        return list(data_from.objects), list(data_from.collections)


def test_save(ctx, rig, names_before):
    """Saving with the preview built must write the source names; returns a file saved without
    the add-on's save handler, for test_load_repair()."""
    print("[save]")
    result = B.build(ctx, rig, keep_face=True)
    ctx.view_layer.objects.active = result.armature
    bpy.ops.object.mode_set(mode='POSE')
    path = os.path.join(OUT_DIR, "save_test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    check(not B.has_preview(), "preview removed before saving (from pose mode)")
    check(sorted(o.name for o in bpy.data.objects) == names_before, "object names restored by saving")
    objects, collections = saved_object_names(path)
    # Objects without users are not written, so the saved names are a subset.
    check(set(objects) <= set(names_before) and B.TMP_COLLECTION not in collections,
          f"saved file has the source names and no preview ({sorted(set(objects) - set(names_before))[:5]})")
    ctx.view_layer.objects.active = rig

    B.build(ctx, rig, keep_face=True)
    bpy.app.handlers.save_pre.remove(cascadeur_export._on_save_pre)
    broken = os.path.join(OUT_DIR, "broken_preview.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=broken, copy=True)
    finally:
        bpy.app.handlers.save_pre.append(cascadeur_export._on_save_pre)
    B.clear(ctx)
    ctx.view_layer.objects.active = rig
    objects, _ = saved_object_names(broken)
    check(any(n.endswith("__csc_src") for n in objects), "without the handler the file keeps renamed objects")
    return broken


def remove_object(ctx, ob):
    # Without the sync the view layer still lists the removed object (as None).
    bpy.data.objects.remove(ob)
    ctx.view_layer.update()


def test_checks(ctx, rig, names_before):
    print("[checks]")
    start = time.perf_counter()
    items = C.character(ctx, rig, True) + C.animation(ctx, rig, 'ACTIVE', OUT_DIR) + C.animation(ctx, rig, 'ALL', OUT_DIR)
    print(f"  took {(time.perf_counter() - start) * 1000:.1f} ms")
    for level, text in items:
        print(f"  {level.lower()}: {text}")
    check(not any(level == 'ERROR' for level, _ in items), "no check errors on the test character")
    check(any(level == 'ERROR' for level, _ in C.animation(ctx, rig, 'ACTIVE', None)), "missing folder reported")

    def reported(level, *words):
        found = C.character(ctx, rig, True)
        return any(lv == level and all(w in t for w in words) for lv, t in found)

    me = bpy.data.meshes.new("csc_test_prop")
    prop = bpy.data.objects.new("csc_test_prop", me)
    ctx.scene.collection.objects.link(prop)
    prop.parent, prop.parent_type, prop.parent_bone = rig, 'BONE', rig.data.bones[0].name
    check(reported('WARNING', "csc_test_prop", "bone"), "mesh parented to a bone reported")
    remove_object(ctx, prop)
    bpy.data.meshes.remove(me)

    me = bpy.data.meshes.new("csc_test_keys")
    me.from_pydata([(0, 0, 0), (0.1, 0, 0), (0, 0.1, 0)], [], [(0, 1, 2)])
    ob = bpy.data.objects.new("csc_test_keys", me)
    ctx.scene.collection.objects.link(ob)
    ob.shape_key_add(name="Basis")
    ob.shape_key_add(name="Key")
    ob.modifiers.new("Armature", 'ARMATURE').object = rig
    ob.modifiers.new("Subdivision", 'SUBSURF')
    check(reported('WARNING', "csc_test_keys", "shape keys"), "shape keys dropped by a modifier reported")
    ob.modifiers.remove(ob.modifiers["Subdivision"])
    ob.modifiers.new("Weighted Normal", 'WEIGHTED_NORMAL')
    check(not reported('WARNING', "csc_test_keys"), "normal-only modifier keeps shape keys, no warning")
    ob.hide_set(True)
    check(reported('INFO', "Hidden", "csc_test_keys"), "hidden bound mesh reported")
    remove_object(ctx, ob)
    bpy.data.meshes.remove(me)

    empty = bpy.data.objects.new("csc_test_empty", None)
    ctx.scene.collection.objects.link(empty)
    empty.keyframe_insert("location", frame=1)
    check(A.object_transform_animated(empty), "object-level animation detected")
    action = empty.animation_data.action
    remove_object(ctx, empty)
    bpy.data.actions.remove(action)

    arm = bpy.data.objects.new("csc_test_arm", bpy.data.armatures.new("csc_test_arm"))
    ctx.scene.collection.objects.link(arm)
    ctx.view_layer.update()  # creates arm.pose
    check(C.character(ctx, arm, True)[0][0] == 'ERROR', "unsupported rig reported")
    data = arm.data
    remove_object(ctx, arm)
    bpy.data.armatures.remove(data)

    B.build(ctx, rig, keep_face=True)
    check(reported('INFO', "Preview is built"), "built preview reported")
    B.clear(ctx)
    ctx.view_layer.objects.active = rig
    check(sorted(o.name for o in bpy.data.objects) == names_before, "checks: object names restored")


def test_load_repair(path, names_before, mesh_names):
    """Opens a file saved with the preview (replaces the session, so it runs last)."""
    print("[load repair]")
    bpy.ops.wm.open_mainfile(filepath=path)
    names = {o.name for o in bpy.data.objects}
    check(not B.has_preview(), "leftover preview removed on load")
    check(names <= set(names_before) and set(mesh_names) <= names,
          f"source names restored on load ({sorted(names - set(names_before))[:5]})")


def main():
    cascadeur_export.register()
    ctx = bpy.context
    rig = next(o for o in ctx.view_layer.objects
               if o.type == 'ARMATURE' and M.detect(o) and B.find_meshes(o, ctx.view_layer))
    profile = M.detect(rig)
    ctx.view_layer.objects.active = rig
    names_before = sorted(o.name for o in bpy.data.objects)
    meshes = B.find_meshes(rig, ctx.view_layer)
    deform = {b.name for b in rig.data.bones if b.use_deform}
    orig = {m.name: (world_coords(m), deform_weight_sums(m, deform)) for m in meshes}
    print(f"rig={rig.name} ({profile.name}) meshes={[m.name for m in meshes]}")
    shape_keys = {m.name: [k.name for k in m.data.shape_keys.key_blocks] for m in meshes if m.data.shape_keys}
    normals = {m.name: corner_normals(m) for m in meshes}

    print("[build]")
    result = B.build(ctx, rig, keep_face=True)
    for w in result.warnings:
        print("  warn", w)
    arm = result.armature
    check(arm.name == "root", f"armature object named root ({arm.name})")
    plan = B._plan_skeleton(rig, True, [])
    expected = 55 if profile is M.ARP else len(plan)
    check(len(arm.data.bones) == expected, f"{expected} bones ({len(arm.data.bones)})")

    paths = template_paths()
    for joint, path in paths.items():
        bone = arm.data.bones.get(joint)
        if bone is None:
            # Optional joints the rig does not have (e.g. fingers of Rigify's Basic Human) are
            # skipped with a build warning.
            check(joint not in M.REQUIRED and joint not in plan, f"template joint {joint} exists")
            continue
        actual = ["root"] + [p.name for p in reversed(bone.parent_recursive)]
        # A rig with two torso bones has no spine_03; everything else must match.
        path = [p for p in path if p in arm.data.bones or p == "root"]
        check(actual == path, f"{joint} path {actual[-2:]}") if actual != path else None
    check(all(j in arm.data.bones or j not in M.REQUIRED for j in paths), "UE4 template joints present with matching paths")

    bone_target = B._weight_targets(rig, plan, profile.aliases, [])
    face = {"DEF-lip.B.L": "jaw", "DEF-chin": "jaw", "DEF-tongue": "jaw", "DEF-lip.T.L": "head", "DEF-brow.T.L": "head"}
    if "jaw" in plan and all(n in bone_target for n in face):
        got = {n: bone_target[n] for n in face}
        check(got == face, f"face weights go to jaw/head {got}")
    lost = [n for n, t in bone_target.items() if t == "pelvis" and n not in plan["pelvis"][0]
            and not n.startswith(("DEF-pelvis", "c_root"))]
    check(len(lost) <= 2, f"deform bones merged into pelvis look intended ({lost[:6]})")

    targets = {b.name for b in arm.data.bones}
    for ob in result.meshes:
        coords, sums = orig[ob.name]
        new_coords = world_coords(ob)
        max_pos = max((a - b).length for a, b in zip(coords, new_coords))
        check(max_pos < 1e-5, f"{ob.name}: rest positions preserved (max {max_pos:.2e})")
        new_sums = deform_weight_sums(ob, targets)
        # Vertices whose merged weights exceed 1 are normalized to a sum of 1.
        max_w = max(abs(b - (1.0 if a > 1.0 and abs(b - 1.0) < 1e-4 else a)) for a, b in zip(sums, new_sums))
        check(max_w < 1e-4, f"{ob.name}: weight sums preserved (max {max_w:.2e})")
        check(ob.parent == arm and any(m.type == 'ARMATURE' and m.object == arm for m in ob.modifiers),
              f"{ob.name}: bound to new armature")
        check(all(g.name in targets for g in ob.vertex_groups if g.name in deform), f"{ob.name}: no source deform groups left")
        if ob.name in shape_keys:
            keys = [k.name for k in ob.data.shape_keys.key_blocks] if ob.data.shape_keys else []
            check(keys == shape_keys[ob.name], f"{ob.name}: shape keys kept {keys}")
        dev = max((a.angle(b, 0.0) for a, b in zip(normals[ob.name], corner_normals(ob))), default=0.0)
        check(math.degrees(dev) < 1.0, f"{ob.name}: normals preserved (max {math.degrees(dev):.2f} deg)")

    print("[bone axes]")
    for target, (sources, _) in plan.items():
        src_z = (rig.matrix_world.to_3x3() @ rig.data.bones[sources[0]].matrix_local.col[2].xyz).normalized()
        new_m = arm.matrix_world.to_3x3() @ arm.data.bones[target].matrix_local.to_3x3()
        new_y, new_z = new_m.col[1].normalized(), new_m.col[2].normalized()
        # A bone merged from non-collinear segments points elsewhere; its roll is right when Z is
        # the source Z projected onto the plane across the new bone.
        src_z = (src_z - new_y * src_z.dot(new_y)).normalized()
        angle = math.degrees(src_z.angle(new_z))
        if angle > 1.0:
            check(False, f"{target}: Z axis matches {sources[0]} ({angle:.1f} deg off)")
    check(not any(f.endswith("deg off)") for f in failures), "all bone Z axes match their source")
    bones = arm.data.bones
    check(bones["spine_01"].head_local.z - bones["pelvis"].head_local.z > 0.05,
          "pelvis joint is below spine_01 joint")
    check(bones["pelvis"].tail_local.z > bones["pelvis"].head_local.z, "pelvis points up")
    coincident = [b.name for b in bones if b.parent and (b.head_local - b.parent.head_local).length < 0.005]
    check(not coincident, f"no joint sits on its parent's joint {coincident}")
    check(not any(m.get(B.ORIGINAL_NAME_PROP) for m in result.meshes), "copies carry no original-name marker")

    print("[pose comparison]")
    srcs = {m[B.ORIGINAL_NAME_PROP]: m for m in bpy.data.objects
            if m.get(B.ORIGINAL_NAME_PROP) and not m.get(B.TMP_PROP)}
    scene = ctx.scene
    for frame in (scene.frame_start, (scene.frame_start + scene.frame_end) // 2):
        scene.frame_set(frame)
        transfer_pose(rig, arm, plan)
        hand = rig.matrix_world @ rig.pose.bones[plan["hand_l"][0][0]].head
        print(f"  frame {frame}: rig hand_l source at {tuple(round(c, 3) for c in hand)}")
        for ob in result.meshes:
            src = srcs[ob.name]
            a = world_coords(src, evaluated=True)
            b = world_coords(ob, evaluated=True)
            d = [(x - y).length for x, y in zip(a, b)]
            mean = sum(d) / len(d)
            print(f"  frame {frame} {ob.name}: mean {mean*100:.2f} cm, max {max(d)*100:.2f} cm")
            # Face meshes lose facial animation by design (face bones are merged), so only the body is gated.
            if ob.name.lower().startswith("body"):
                check(mean < 0.01, f"frame {frame} {ob.name}: mean deviation < 1 cm")

    print("[synthetic pose]")
    # The scene animation may be in rest pose, so the rig is posed here. A generated Rigify rig
    # hangs many DEF- bones off ORG-/MCH- bones, so it is posed through its controls with the
    # constraints on; merged twist segments then deviate a little, which the looser limit allows.
    # Other rigs: each target's first source bone is rotated with constraints muted, and other
    # segments of a merged target keep their rest relation, as the merged bone does.
    controls = profile is M.RIGIFY and rig.data.get("rig_id") is not None
    muted = [] if controls else [c for pb in rig.pose.bones for c in pb.constraints if not c.mute]
    for c in muted:
        c.mute = True
    saved = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in rig.pose.bones}
    rng = random.Random(1)
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    if controls:
        posed = [pb for pb in rig.pose.bones if not pb.name.startswith(("DEF-", "ORG-", "MCH-", "VIS"))]
    else:
        posed = [rig.pose.bones[sources[0]] for sources, _ in plan.values()]
    for pb in posed:
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = [math.radians(rng.uniform(-10, 10) if controls else rng.uniform(-15, 15))
                             for _ in range(3)]
    ctx.view_layer.update()
    transfer_pose(rig, arm, plan)
    # ARP's twist/stretch segments are siblings held together by the muted constraints, so
    # they cannot follow here; the check only gates rigs whose segments are parented in a chain.
    chained = controls or all(rig.data.bones[s].parent_recursive.count(rig.data.bones[sources[0]])
                              for sources, _ in plan.values() for s in sources[1:])
    limit = 0.01 if controls else 0.002
    for ob in result.meshes:
        src = srcs[ob.name]
        a = world_coords(src, evaluated=True)
        b = world_coords(ob, evaluated=True)
        d = [(x - y).length for x, y in zip(a, b)]
        mean = sum(d) / len(d)
        moved = max((x - y).length for x, y in zip(a, world_coords(src)))
        print(f"  {ob.name}: moved up to {moved*100:.1f} cm; deviation mean {mean*100:.3f} cm, max {max(d)*100:.3f} cm")
        if chained:
            check(mean < limit, f"posed {ob.name}: mean deviation < {limit * 1000:.0f} mm")
    for pb in rig.pose.bones:
        pb.rotation_mode, pb.matrix_basis = saved[pb.name]
    for c in muted:
        c.mute = False
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    ctx.view_layer.update()

    print("[clear]")
    B.clear(ctx)
    check(sorted(o.name for o in bpy.data.objects) == names_before, "object names restored after clear")
    check(B.TMP_COLLECTION not in bpy.data.collections, "temp collection removed")

    broken = test_save(ctx, rig, names_before)

    print("[export]")
    ctx.view_layer.objects.active = rig
    ctx.scene.csc_export.filepath = os.path.join(OUT_DIR, "operator_export.fbx")
    check(bpy.ops.cascadeur.export_character() == {'FINISHED'}, "operator export finished")
    check(ctx.view_layer.objects.active == rig, "active object restored")
    check(sorted(o.name for o in bpy.data.objects) == names_before, "object names restored after export")
    info = fbx_summary(ctx.scene.csc_export.filepath)
    print(f"  {info}")
    check(abs(info["unit"][0] - 1.0) < 1e-6, "FBX unit is centimetres")
    check("Lcl Scaling" not in info["root"], "root node has no scaling")
    body_top = max(g[3] for g in info["geom"])
    check(150 < body_top < 250, f"character height in cm ({body_top})")

    test_animation(ctx, rig, names_before)
    test_checks(ctx, rig, names_before)
    test_load_repair(broken, names_before, [m.name for m in meshes])

    print("\nRESULT:", "PASS" if not failures else f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)


main()
sys.exit(1 if failures else 0)
