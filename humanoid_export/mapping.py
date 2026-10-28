# SPDX-FileCopyrightText: 2026 Renat Mansurov
# SPDX-License-Identifier: GPL-3.0-or-later

"""Auto-Rig Pro / Rigify deform bones -> UE4 Mannequin skeleton.

Bone names and hierarchy follow Cascadeur's built-in Quick Rigging Tool
template ``UE4_New.qrigcasc`` (paths start at ``root``, which is the armature
object itself), so the template recognises the exported skeleton as is.
"""

import re
from collections import namedtuple

ROOT_NAME = "root"

# Bones the export cannot do without. Everything else is skipped with a warning.
REQUIRED = {
    "pelvis", "spine_01", "neck_01", "head",
    "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r",
    "upperarm_l", "lowerarm_l", "hand_l", "upperarm_r", "lowerarm_r", "hand_r",
}

FINGERS = ("thumb", "index", "middle", "ring", "pinky")

SPINE = ("spine_01", "spine_02", "spine_03")


def _spread_torso(torso):
    """Torso bones above the pelvis -> sources of spine_01..03. UE4_New has slots for spine_01 and
    spine_02 only; spine_03 just has to be on the path to the neck and clavicles. With more than
    three bones the middle ones are merged into spine_02."""
    if len(torso) > 3:
        return [[torso[0]], torso[1:-1], [torso[-1]]]
    return [[t] for t in torso]

# entries(rig, keep_face) -> [(target, [source bones], parent target)] in parent-first order.
# reversed: targets built tail-to-head.
# aliases(source) -> other bone names that stand for ``source`` when deform bones look for a
# mapped ancestor to give their weights to.
# merged(bone) -> whether an unmapped deform bone is always merged into its ancestor, even when
# extra deform chains are kept (face, finger bases, helper bones).
Profile = namedtuple("Profile", "name signature entries reversed aliases merged")


# --- Auto-Rig Pro ---------------------------------------------------------------------------

ARP_SIGNATURE = ("c_root_bend.x", "c_thigh_twist.l")


def _arp_body(bones):
    # ARP rigs have 1 to 4+ spine bones c_spine_NN_bend.x. The first three are always listed, so
    # a rig with fewer gets a warning and its neck and clavicles hang off the last one present.
    torso = [f"c_spine_{i:02d}_bend.x" for i in range(1, 4)]
    while f"c_spine_{len(torso) + 1:02d}_bend.x" in bones:
        torso.append(f"c_spine_{len(torso) + 1:02d}_bend.x")
    result = [("pelvis", ["c_root_bend.x"], None)]
    parent = "pelvis"
    for target, sources in zip(SPINE, _spread_torso(torso)):
        result.append((target, sources, parent))
        parent = target
    return result + [
        ("neck_01", ["neck.x"], "spine_03"),
        ("head", ["head.x"], "neck_01"),
    ]


def _arp_face():
    return [
        ("jaw", ["jawbone.x"], "head"),
        ("eye_l", ["c_eye.l"], "head"),
        ("eye_r", ["c_eye.r"], "head"),
    ]


def _arp_side(s):
    """Entries for one side; ``s`` is 'l' or 'r'. Segment order is fixed later by distance."""
    entries = [
        (f"clavicle_{s}", [f"shoulder.{s}"], "spine_03"),
        (f"upperarm_{s}", [f"c_arm_twist.{s}", f"c_arm_stretch.{s}"], f"clavicle_{s}"),
        (f"lowerarm_{s}", [f"c_forearm_stretch.{s}", f"c_forearm_twist.{s}"], f"upperarm_{s}"),
        (f"hand_{s}", [f"hand.{s}"], f"lowerarm_{s}"),
    ]
    for f in FINGERS:
        entries += [
            (f"{f}_01_{s}", [f"{f}1.{s}"], f"hand_{s}"),
            (f"{f}_02_{s}", [f"c_{f}2.{s}"], f"{f}_01_{s}"),
            (f"{f}_03_{s}", [f"c_{f}3.{s}"], f"{f}_02_{s}"),
        ]
    entries += [
        (f"thigh_{s}", [f"c_thigh_twist.{s}", f"c_thigh_stretch.{s}"], "pelvis"),
        (f"calf_{s}", [f"c_leg_stretch.{s}", f"c_leg_twist.{s}"], f"thigh_{s}"),
        (f"foot_{s}", [f"foot.{s}"], f"calf_{s}"),
        (f"ball_{s}", [f"toes_01.{s}"], f"foot_{s}"),
    ]
    return entries


def _arp_entries(rig, keep_face=True):
    result = _arp_body(rig.data.bones)
    if keep_face:
        result += _arp_face()
    return result + _arp_side("l") + _arp_side("r")


# ARP facial module bones; with Keep Jaw & Eyes only jaw and eyes are kept, the rest is merged.
ARP_FACE = ("c_lips_", "c_eye", "c_nose_", "c_chin_", "c_cheek_", "c_teeth_", "tong_", "c_tong_",
            "c_ear_", "c_bingl", "c_jaw", "jawbone")


def _arp_merged(bone):
    # Finger *_base bones belong to the hand; extra twist and *_bend bones are segment helpers.
    name = bone.name
    return name.startswith(ARP_FACE) or "_base." in name or "twist" in name or "_bend" in name


# ARP's c_root_bend.x starts at the spine base and points down to the hips; reversed, the pelvis
# joint sits at hip level below spine_01 instead of on top of it, which Cascadeur needs to get a
# defined pelvis direction.
ARP = Profile("Auto-Rig Pro", ARP_SIGNATURE, _arp_entries, {"pelvis"}, lambda source: (), _arp_merged)


# --- Rigify ---------------------------------------------------------------------------------
# Works on the generated rig itself and on a deform-only copy of its DEF- bones. DEF-spine is the
# hips bone (pointing up, so no reversal); the bones up to the one carrying the shoulders are the
# torso, the rest of the centre chain is neck and head. Metarigs differ in how many torso and neck
# bones they have and what the head is called, so the chain is read from the rig.

RIGIFY_SIGNATURE = ("DEF-spine", "DEF-thigh.L", "DEF-upper_arm.L")


def _sided(name):
    return name.endswith((".L", ".R")) or ".L." in name or ".R." in name


def _segments(bones, base, side):
    """DEF-<base>.<side> and its B-Bone split segments DEF-<base>.<side>.001, .002, ..."""
    first = f"DEF-{base}.{side}"
    if first not in bones:
        return [first]
    result = [first]
    while f"{first}.{len(result):03d}" in bones:
        result.append(f"{first}.{len(result):03d}")
    return result


def _def_twin(name):
    """The DEF- bone a rig bone stands for: itself, or the DEF- twin of an ORG- bone."""
    if name.startswith("DEF-"):
        return name
    if name.startswith("ORG-"):
        return "DEF-" + name[4:]
    return None


def _def_parent(bones, b):
    """Nearest ancestor that is, or has, a DEF- bone other than ``b`` itself. In a generated rig
    many DEF- bones hang off ORG-/MCH- bones; in a deform-only copy they hang off DEF- ones."""
    for p in b.parent_recursive:
        n = _def_twin(p.name)
        if n is not None and n in bones and n != b.name:
            return bones[n]
    return None


def _rigify_centre(bones):
    """Returns (torso bones above the hips, neck bones, head bone) of the centre chain."""
    chest = _def_parent(bones, bones["DEF-shoulder.L" if "DEF-shoulder.L" in bones else "DEF-upper_arm.L"])
    while chest is not None and _sided(chest.name):
        chest = _def_parent(bones, chest)
    torso = []
    b = chest
    while b is not None and b.name != "DEF-spine":
        torso.insert(0, b.name)
        b = _def_parent(bones, b)

    # Follow DEF-spine.NNN / *neck* / *head* children; face bones under the head end the walk.
    children = {}
    for c in bones:
        if c.name.startswith("DEF-") and not _sided(c.name):
            p = _def_parent(bones, c)
            if p is not None:
                children.setdefault(p.name, []).append(c)
    chain = []
    b = chest
    while b is not None:
        centre = children.get(b.name, [])
        b = next((c for c in centre if c.name.startswith("DEF-spine")), None)             or next((c for c in centre if any(k in c.name.lower() for k in ("neck", "head"))), None)
        if b is not None:
            chain.append(b.name)
            if "head" in b.name.lower():
                break
    if not chain:
        return torso, [], None
    named_head = [n for n in chain if "head" in n.lower()]
    head = named_head[0] if named_head else chain[-1]
    return torso, chain[:chain.index(head)], head


def _rigify_side(bones, s, chest):
    side = s.upper()
    entries = [
        (f"clavicle_{s}", [f"DEF-shoulder.{side}"], chest),
        (f"upperarm_{s}", _segments(bones, "upper_arm", side), f"clavicle_{s}"),
        (f"lowerarm_{s}", _segments(bones, "forearm", side), f"upperarm_{s}"),
        (f"hand_{s}", [f"DEF-hand.{side}"], f"lowerarm_{s}"),
    ]
    for f in FINGERS:
        stem = "thumb" if f == "thumb" else f"f_{f}"
        entries += [
            (f"{f}_01_{s}", [f"DEF-{stem}.01.{side}"], f"hand_{s}"),
            (f"{f}_02_{s}", [f"DEF-{stem}.02.{side}"], f"{f}_01_{s}"),
            (f"{f}_03_{s}", [f"DEF-{stem}.03.{side}"], f"{f}_02_{s}"),
        ]
    entries += [
        (f"thigh_{s}", _segments(bones, "thigh", side), "pelvis"),
        (f"calf_{s}", _segments(bones, "shin", side), f"thigh_{s}"),
        (f"foot_{s}", [f"DEF-foot.{side}"], f"calf_{s}"),
        (f"ball_{s}", [f"DEF-toe.{side}"], f"foot_{s}"),
    ]
    return entries


def _first_present(bones, *names):
    return [next((n for n in names if n in bones), names[0])]


def _rigify_entries(rig, keep_face=True):
    bones = rig.data.bones
    torso, neck, head = _rigify_centre(bones)
    result = [("pelvis", ["DEF-spine"], None)]
    parent = "pelvis"
    for target, sources in zip(SPINE, _spread_torso(torso)):
        result.append((target, sources, parent))
        parent = target
    chest = parent
    result.append(("neck_01", neck or ["DEF-neck"], chest))
    result.append(("head", [head or "DEF-head"], "neck_01"))
    if keep_face:
        result += [
            ("jaw", _first_present(bones, "DEF-jaw", "DEF-jaw.master"), "head"),
            ("eye_l", _first_present(bones, "DEF-eye.L", "ORG-eye.L"), "head"),
            ("eye_r", _first_present(bones, "DEF-eye.R", "ORG-eye.R"), "head"),
        ]
    return result + _rigify_side(bones, "l", chest) + _rigify_side(bones, "r", chest)


def _rigify_aliases(source):
    # In a generated rig, face and other DEF bones hang off ORG/MCH bones rather than DEF ones.
    # Face bones under jaw_master (chin, lower lip, tongue) follow the jaw fully; those under
    # MCH-jaw_master.001+ (upper lip, cheeks) only partly and stay with the head.
    if source == "DEF-jaw":
        return ("ORG-jaw", "jaw_master", "MCH-jaw_master")
    return ("ORG-" + source[4:],) if source.startswith("DEF-") else ()


# First word of Rigify face bone names (DEF-lip.T.L, DEF-brow.B.L, DEF-jaw_master, ...).
RIGIFY_FACE = {"face", "forehead", "temple", "brow", "lid", "ear", "lip", "lips", "chin", "jaw", "tongue",
               "cheek", "nose", "nostril", "teeth", "eye", "eyes"}


def _rigify_merged(bone):
    # Palm bones belong to the hand, DEF-pelvis.L/R to the hips. Face bones are recognised by name
    # and, in a generated rig, by hanging off ORG-face.
    name = bone.name
    if name.startswith(("DEF-palm", "DEF-pelvis")):
        return True
    if name.startswith("DEF-") and re.split(r"[._]", name[4:])[0].lower() in RIGIFY_FACE:
        return True
    return any(p.name in ("ORG-face", "face") for p in bone.parent_recursive)


RIGIFY = Profile("Rigify", RIGIFY_SIGNATURE, _rigify_entries, set(), _rigify_aliases, _rigify_merged)

PROFILES = (ARP, RIGIFY)


def detect(rig):
    """The profile whose signature bones the rig has, or None."""
    return next((p for p in PROFILES if all(n in rig.data.bones for n in p.signature)), None)


def entries(rig, keep_face=True):
    """(target, [source deform bones], parent target) in parent-first order."""
    return detect(rig).entries(rig, keep_face)


def is_arp(rig):
    return detect(rig) is ARP
