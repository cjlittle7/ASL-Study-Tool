"""
ASL Flashcard Editor  -  a Blender add-on
=========================================

Pose a simple signer figure (body, head, arms, hands, every finger joint),
add faded "start position" ghost figures and movement arrows, and export a
flashcard PNG with the English term printed at the same size on every card.

Install:  Blender > Edit > Preferences > Add-ons > (drop-down arrow) >
          Install from Disk... > choose this file > tick the checkbox.
Use:      In the 3D Viewport press N, open the "ASL Cards" tab,
          click "New Card Scene".

Batch re-render every saved sign (no Blender window opens):
    blender -b -P asl_flashcard_editor.py -- --rebuild path/to/cards

How the figure works (worth reading if you want to change the code):
  * Every joint (shoulder, elbow, each finger knuckle...) is an Empty object.
  * Body-part meshes are children of the joint they move with.
  * Each joint stores its resting position/orientation in
    `matrix_parent_inverse`, so its own rotation (`rotation_euler`) is purely
    the pose. The sliders in the panel just set those rotations.
  * Joint frames all follow one rule: local -Z points along the bone, and
    local -Y is the direction the joint bends (toward the palm, forward...).
"""

bl_info = {
    "name": "ASL Flashcard Editor",
    "author": "ASL Study Tool",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > ASL Cards",
    "description": "Pose a signer figure and export ASL flashcard PNGs",
    "category": "Import-Export",
}

import json
import math
import os
import re
import sys
from datetime import datetime, timezone

import bmesh
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector

SIDES = ("R", "L")                 # signer's right / signer's left
FINGERS = ("index", "middle", "ring", "pinky")
CARD_COLLECTION = "ASL Card"
OUTLINE_COLLECTION = "ASL Outlined (internal)"
RECIPE_VERSION = 1


# ---------------------------------------------------------------------------
# 1. Body measurements (meters, signer's RIGHT side; the left side mirrors X)
# ---------------------------------------------------------------------------

SHOULDER_POS = Vector((-0.19, 0.0, 0.46))
UPPER_ARM_LEN = 0.28
FOREARM_LEN = 0.25
PALM_LEN = 0.088

# name: (knuckle position on palm, radius, (proximal, middle, distal) lengths)
FINGER_GEOM = {
    "index":  (Vector((-0.029, 0.0, -0.088)), 0.0086, (0.044, 0.026, 0.021)),
    "middle": (Vector((-0.0095, 0.0, -0.092)), 0.0089, (0.049, 0.030, 0.022)),
    "ring":   (Vector((0.010, 0.0, -0.089)), 0.0083, (0.046, 0.028, 0.021)),
    "pinky":  (Vector((0.028, 0.0, -0.081)), 0.0073, (0.036, 0.021, 0.019)),
}
THUMB_BASE = Vector((-0.030, -0.006, -0.018))
THUMB_DIR = Vector((-0.30, -0.36, -0.88))      # resting thumb direction
THUMB_BEND_DIR = Vector((0.75, -0.62, 0.0))    # thumb bends across the palm
THUMB_LENS = (0.046, 0.032, 0.028)              # metacarpal, proximal, distal
THUMB_RADII = (0.0125, 0.0098, 0.0092)


# ---------------------------------------------------------------------------
# 2. Pose parameters.  Every slider in the UI is one of these.
#    (key, label, default, min, max)  - all values are degrees.
# ---------------------------------------------------------------------------

BODY_PARAMS = [
    ("torso_lean", "Lean forward", 0, -30, 40),
    ("torso_turn", "Turn (toward signer's right +)", 0, -60, 60),
    ("head_nod", "Nod (chin down +)", 0, -40, 50),
    ("head_turn", "Turn (toward signer's right +)", 0, -80, 80),
    ("head_tilt", "Tilt (toward right shoulder +)", 0, -40, 40),
]

ARM_PARAMS = [
    ("shoulder_forward", "Raise forward", 0, -40, 180),
    ("shoulder_out", "Raise sideways", 0, -30, 180),
    ("shoulder_twist", "Rotate inward", 0, -90, 120),
    ("elbow_bend", "Elbow bend", 0, 0, 150),
    ("forearm_turn", "Forearm turn (palm out/down +)", 0, -120, 160),
    ("wrist_bend", "Wrist bend (toward palm +)", 0, -80, 90),
    ("wrist_side", "Wrist side (toward thumb +)", 0, -40, 30),
]

FINGER_PARAMS = [
    ("base", "Knuckle", 0, -30, 100),
    ("mid", "Middle joint", 0, -10, 115),
    ("tip", "Tip joint", 0, -20, 90),
    ("side", "Side (toward thumb +)", 0, -35, 35),
]

THUMB_PARAMS = [
    ("across", "Across palm", 0, -40, 90),
    ("out", "Away from palm", 0, -40, 80),
    ("roll", "Roll", 0, -60, 60),
    ("mid", "Middle joint", 0, -20, 90),
    ("tip", "Tip joint", 0, -30, 90),
]

# A relaxed starting pose: hands in front of the chest, palms facing the body.
DEFAULT_POSE = {
    "R_shoulder_forward": 15, "R_shoulder_out": 12, "R_shoulder_twist": 35,
    "R_elbow_bend": 105, "R_forearm_turn": 0,
    "L_shoulder_forward": 5, "L_shoulder_out": 8, "L_shoulder_twist": 10,
    "L_elbow_bend": 15,
}


def hand_param_keys(side):
    keys = []
    for f in FINGERS:
        keys += [f"{side}_{f}_{p[0]}" for p in FINGER_PARAMS]
    keys += [f"{side}_thumb_{p[0]}" for p in THUMB_PARAMS]
    return keys


def all_param_specs():
    """Yield (key, label, default, min, max) for every pose parameter."""
    for p in BODY_PARAMS:
        yield p
    for s in SIDES:
        for k, label, d, lo, hi in ARM_PARAMS:
            yield (f"{s}_{k}", label, d, lo, hi)
        for f in FINGERS:
            for k, label, d, lo, hi in FINGER_PARAMS:
                yield (f"{s}_{f}_{k}", label, d, lo, hi)
        for k, label, d, lo, hi in THUMB_PARAMS:
            yield (f"{s}_thumb_{k}", label, d, lo, hi)


# ---------------------------------------------------------------------------
# 3. Handshape presets (finger + thumb values only; side-independent keys).
#    These are starting points: always compare with a fluent signer or a
#    trusted dictionary and fine-tune with the sliders.
# ---------------------------------------------------------------------------

def _fingers(base, mid, tip, sides=(4, 0, -6, -12)):
    d = {}
    for f, s in zip(FINGERS, sides):
        d[f"{f}_base"], d[f"{f}_mid"], d[f"{f}_tip"], d[f"{f}_side"] = base, mid, tip, s
    return d


def _thumb(across, out, roll, mid, tip):
    return {"thumb_across": across, "thumb_out": out, "thumb_roll": roll,
            "thumb_mid": mid, "thumb_tip": tip}


def _shape(fingers, thumb, **overrides):
    d = dict(fingers)
    d.update(thumb)
    d.update(overrides)
    return d


HANDSHAPES = {
    "relaxed": ("Relaxed", _shape(_fingers(12, 18, 10), _thumb(10, 0, 0, 10, 8))),
    "A": ("A (fist, thumb at side)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                             _thumb(0, -14, 15, 0, 0))),
    "S": ("S (fist, thumb across)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                            _thumb(65, 20, 30, 30, 20))),
    "B": ("B (flat, thumb in)", _shape(_fingers(0, 0, 0, (2, 0, -2, -3)),
                                       _thumb(60, 5, 20, 25, 10))),
    "5": ("5 (open, spread)", _shape(_fingers(0, 0, 0, (14, 2, -12, -24)),
                                     _thumb(0, 14, 0, 0, 0))),
    "C": ("C (curved)", _shape(_fingers(35, 45, 30, (3, 0, -3, -6)),
                               _thumb(25, 45, 25, 15, 10))),
    "O": ("O (fingertips meet thumb)", _shape(_fingers(50, 60, 40, (3, 0, -3, -6)),
                                              _thumb(45, 40, 35, 25, 20))),
    "1": ("1 / D shape (index up)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                           _thumb(55, 20, 30, 30, 25),
                                           index_base=0, index_mid=0, index_tip=0)),
    "L": ("L (index + thumb)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                      _thumb(-5, 48, 0, 0, 0),
                                      index_base=0, index_mid=0, index_tip=0)),
    "V": ("V / 2 (index + middle spread)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                                  _thumb(55, 20, 30, 30, 25),
                                                  index_base=0, index_mid=0, index_tip=0, index_side=10,
                                                  middle_base=0, middle_mid=0, middle_tip=0, middle_side=-8)),
    "W": ("W / 6 (three fingers)", _shape(_fingers(0, 0, 0, (10, 0, -10, 0)),
                                          _thumb(60, 25, 35, 40, 30),
                                          pinky_base=85, pinky_mid=100, pinky_tip=60)),
    "I": ("I (pinky up)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                 _thumb(60, 20, 30, 30, 25),
                                 pinky_base=0, pinky_mid=0, pinky_tip=0)),
    "Y": ("Y (thumb + pinky)", _shape(_fingers(90, 105, 60, (0, 0, 0, -10)),
                                      _thumb(-5, 48, 0, 0, 0),
                                      pinky_base=0, pinky_mid=0, pinky_tip=0)),
    "X": ("X (hooked index)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                     _thumb(55, 20, 30, 30, 25),
                                     index_base=10, index_mid=80, index_tip=50)),
    "flatO": ("Flat O", _shape(_fingers(60, 10, 5, (2, 0, -2, -3)),
                               _thumb(40, 30, 30, 10, 5))),
    "bentB": ("Bent B", _shape(_fingers(80, 0, 0, (2, 0, -2, -3)),
                               _thumb(55, 5, 20, 20, 10))),
    "claw": ("Claw 5", _shape(_fingers(15, 70, 45, (14, 2, -12, -24)),
                              _thumb(5, 22, 10, 30, 30))),
}


# ---------------------------------------------------------------------------
# 4. Small geometry helpers
# ---------------------------------------------------------------------------

def frame_matrix(bone_dir, bend_dir):
    """Rotation whose -Z follows the bone and whose -Y is the bend direction."""
    z = -Vector(bone_dir).normalized()
    f = Vector(bend_dir)
    f = (f - f.dot(z) * z).normalized()
    y = -f
    x = y.cross(z)
    return Matrix((x, y, z)).transposed().to_4x4()


def mirror_x(v, side):
    v = Vector(v)
    if side == "L":
        v.x = -v.x
    return v


def _finish_mesh(bm, name):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    return me


def capsule_mesh(name, length, r1, r2=None, segments=20):
    """A rounded tube from z=0 down to z=-length (radius r1 at top, r2 at bottom)."""
    r2 = r1 if r2 is None else r2
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=segments // 2 + 2, radius=1.0)
    for v in bm.verts:
        if v.co.z < -1e-5:
            v.co.x *= r2
            v.co.y *= r2
            v.co.z = v.co.z * r2 - length
        else:
            v.co *= r1
    return _finish_mesh(bm, name)


def ellipsoid_mesh(name, rx, ry, rz, center=(0, 0, 0), segments=32):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=segments // 2 + 2, radius=1.0)
    c = Vector(center)
    for v in bm.verts:
        v.co = Vector((v.co.x * rx, v.co.y * ry, v.co.z * rz)) + c
    return _finish_mesh(bm, name)


def rounded_box_mesh(name, sx, sy, sz, center=(0, 0, 0), bevel=0.01):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * sx, v.co.y * sy, v.co.z * sz)) + Vector(center)
    try:
        bmesh.ops.bevel(bm, geom=list(bm.verts) + list(bm.edges), offset=bevel,
                        segments=4, affect='EDGES', profile=0.5)
    except Exception:
        pass
    return _finish_mesh(bm, name)


def cone_mesh(name, radius, length, segments=24):
    """A cone whose base is at y=0 and tip points along +Y."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, segments=segments,
                          radius1=radius, radius2=0.0, depth=length)
    for v in bm.verts:   # create_cone points along Z, centered: rotate to +Y, move base to 0
        v.co = Vector((v.co.x, v.co.z + length / 2, -v.co.y))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "card"


# ---------------------------------------------------------------------------
# 5. Materials
# ---------------------------------------------------------------------------

def _set_alpha_mode(mat, transparent):
    if hasattr(mat, "surface_render_method"):          # Blender 4.2+
        mat.surface_render_method = 'DITHERED'
    elif hasattr(mat, "blend_method"):
        mat.blend_method = 'BLEND' if transparent else 'OPAQUE'


def make_shaded_material(name, color, alpha=1.0, roughness=0.6):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "ASL_BSDF"
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    set_material_color(mat, color, alpha)
    bsdf.inputs["Roughness"].default_value = roughness
    return mat


def make_flat_material(name, color):
    """Unlit solid color (used for arrows, label text and the backdrop)."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.name = "ASL_EMIT"
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs[0], out.inputs[0])
    set_material_color(mat, color)
    return mat


def set_material_color(mat, color, alpha=1.0):
    rgba = (color[0], color[1], color[2], 1.0)
    mat.diffuse_color = (color[0], color[1], color[2], alpha)
    nodes = mat.node_tree.nodes
    if "ASL_BSDF" in nodes:
        bsdf = nodes["ASL_BSDF"]
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Alpha"].default_value = alpha
        _set_alpha_mode(mat, alpha < 0.999)
    if "ASL_EMIT" in nodes:
        nodes["ASL_EMIT"].inputs["Color"].default_value = rgba


# ---------------------------------------------------------------------------
# 6. Building a figure
# ---------------------------------------------------------------------------

def card_collection(scene):
    coll = bpy.data.collections.get(CARD_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(CARD_COLLECTION)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def outline_collection():
    """Freestyle only draws outlines on objects in this collection (solid figures)."""
    coll = bpy.data.collections.get(OUTLINE_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(OUTLINE_COLLECTION)
        coll.use_fake_user = True
    return coll


class _Builder:
    def __init__(self, coll, root, mats):
        self.coll = coll
        self.root = root
        self.mats = mats
        self.prefix = root.name

    def joint(self, key, parent, offset, rest=None, order='XYZ'):
        e = bpy.data.objects.new(f"{self.prefix}.{key}", None)
        e.empty_display_type = 'SPHERE'
        e.empty_display_size = 0.012
        e.show_in_front = True
        self.coll.objects.link(e)
        e.parent = parent
        m = Matrix.Translation(Vector(offset))
        if rest is not None:
            m = m @ rest
        e.matrix_parent_inverse = m
        e.rotation_mode = order
        e["asl_joint"] = key
        return e

    def part(self, key, mesh, parent, group, mat, offset=(0, 0, 0)):
        mesh.materials.append(self.mats[mat])
        o = bpy.data.objects.new(f"{self.prefix}.{key}", mesh)
        self.coll.objects.link(o)
        o.parent = parent
        o.matrix_parent_inverse = Matrix.Translation(Vector(offset))
        o["asl_part"] = group
        o.hide_select = False
        return o


def build_figure(scene, ghost=False, name=None):
    """Create one complete signer figure and return its root Empty."""
    parent_coll = card_collection(scene)
    idx = 1
    while bpy.data.objects.get(f"Figure{idx}"):
        idx += 1
    name = name or f"Figure{idx}"

    coll = bpy.data.collections.new(name)
    parent_coll.children.link(coll)

    root = bpy.data.objects.new(name, None)
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 0.15
    coll.objects.link(root)

    s = scene.asl
    mats = {
        "skin": make_shaded_material(f"ASL_Skin_{name}", s.skin_color),
        "shirt": make_shaded_material(f"ASL_Shirt_{name}", s.shirt_color, roughness=0.8),
        "feature": make_shaded_material(f"ASL_Feature_{name}", (0.08, 0.06, 0.06)),
    }
    b = _Builder(coll, root, mats)

    # torso, neck, head --------------------------------------------------
    torso = b.joint("torso", root, (0, 0, 0))
    b.part("chest", rounded_box_mesh("chest", 0.33, 0.19, 0.52, center=(0, 0.01, 0.215), bevel=0.075),
           torso, "torso", "shirt")
    yoke = capsule_mesh("yoke", 0.32, 0.05)
    yoke.transform(Matrix.Translation((-0.16, 0.005, 0.425)) @ Matrix.Rotation(math.radians(-90), 4, 'Y'))
    b.part("yoke", yoke, torso, "torso", "shirt")

    neck = b.joint("neck", torso, (0, 0.0, 0.47))
    b.part("neck_mesh", capsule_mesh("neck", 0.07, 0.045, 0.048, segments=16), neck, "head", "skin",
           offset=(0, 0.006, 0.075))
    head = b.joint("head", neck, (0, 0, 0.08))
    b.part("skull", ellipsoid_mesh("skull", 0.082, 0.095, 0.112, center=(0, 0, 0.11)),
           head, "head", "skin")
    b.part("nose", ellipsoid_mesh("nose", 0.0095, 0.013, 0.017, center=(0, -0.094, 0.105), segments=12),
           head, "head", "skin")
    for sx in (-1, 1):
        b.part(f"eye_{sx}", ellipsoid_mesh("eye", 0.011, 0.007, 0.012,
                                             center=(0.031 * sx, -0.083, 0.128), segments=12),
               head, "head", "feature")
        b.part(f"ear_{sx}", ellipsoid_mesh("ear", 0.012, 0.022, 0.03,
                                             center=(0.081 * sx, 0.0, 0.11), segments=12),
               head, "head", "skin")
        brow = capsule_mesh("brow", 0.028, 0.0034, segments=10)
        brow.transform(Matrix.Rotation(math.radians(-90), 4, 'Y'))
        brow.transform(Matrix.Translation((-0.014, 0, 0)))
        bo = b.part(f"brow_{sx}", brow, head, "head", "feature")
        bo["asl_brow"] = sx
    mouth = capsule_mesh("mouth", 0.032, 0.0042, segments=10)
    mouth.transform(Matrix.Translation((-0.016, -0.088, 0.064)) @ Matrix.Rotation(math.radians(-90), 4, 'Y'))
    b.part("mouth", mouth, head, "head", "feature")

    # arms and hands -----------------------------------------------------
    for side in SIDES:
        sh = b.joint(f"{side}_shoulder", torso, mirror_x(SHOULDER_POS, side), order='ZXY')
        b.part(f"{side}_upper_arm", capsule_mesh("upper_arm", UPPER_ARM_LEN, 0.047, 0.041),
               sh, f"arm_{side}", "shirt")
        el = b.joint(f"{side}_elbow", sh, (0, 0, -UPPER_ARM_LEN), order='ZXY')
        b.part(f"{side}_forearm", capsule_mesh("forearm", FOREARM_LEN, 0.041, 0.031),
               el, f"arm_{side}", "shirt")
        # wrist rest: palm faces the body, so "forearm turn 0" = palm in, thumb forward
        m = 1 if side == "R" else -1
        wr = b.joint(f"{side}_wrist", el, (0, 0, -FOREARM_LEN),
                     rest=Matrix.Rotation(math.radians(90 * m), 4, 'Z'))
        b.part(f"{side}_wrist_mesh", capsule_mesh("wrist", 0.012, 0.024, 0.026, segments=16),
               wr, f"hand_{side}", "skin")
        b.part(f"{side}_palm", rounded_box_mesh("palm", 0.08, 0.027, PALM_LEN,
                                                 center=(0, 0.002, -PALM_LEN / 2 - 0.004), bevel=0.011),
               wr, f"hand_{side}", "skin")

        for f in FINGERS:
            base_pos, radius, lens = FINGER_GEOM[f]
            parent = wr
            offset = mirror_x(base_pos, side)
            for j, (jname, seg_len) in enumerate(zip(("base", "mid", "tip"), lens)):
                jt = b.joint(f"{side}_{f}_{jname}", parent, offset)
                r = radius * (1.0 - 0.08 * j)
                b.part(f"{side}_{f}_{jname}_seg", capsule_mesh("finger", seg_len, r, r * 0.95, segments=14),
                       jt, f"hand_{side}", "skin")
                parent, offset = jt, (0, 0, -seg_len)

        # two joints at the thumb base: "cmc" swings the thumb sideways in the palm's
        # plane (out), "base" swings it across the front of the palm and rolls it.
        cmc = b.joint(f"{side}_thumb_cmc", wr, mirror_x(THUMB_BASE, side))
        rest = frame_matrix(mirror_x(THUMB_DIR, side), mirror_x(THUMB_BEND_DIR, side))
        parent = b.joint(f"{side}_thumb_base", cmc, (0, 0, 0), rest=rest)
        b.part(f"{side}_thumb_base_seg", capsule_mesh("thumb", THUMB_LENS[0], THUMB_RADII[0], 0.0105, 14),
               parent, f"hand_{side}", "skin")
        offset = (0, 0, -THUMB_LENS[0])
        for jname, seg_len, r in (("mid", THUMB_LENS[1], THUMB_RADII[1]), ("tip", THUMB_LENS[2], THUMB_RADII[2])):
            jt = b.joint(f"{side}_thumb_{jname}", parent, offset)
            b.part(f"{side}_thumb_{jname}_seg", capsule_mesh("thumb", seg_len, r, r * 0.95, 14),
                   jt, f"hand_{side}", "skin")
            parent, offset = jt, (0, 0, -seg_len)

    fig = root.asl_fig
    fig.is_figure = True
    fig.is_ghost = ghost
    fig.opacity = s.ghost_opacity if ghost else 1.0
    if not ghost:
        oc = outline_collection()
        for o in coll.objects:
            if o.type == 'MESH':
                oc.objects.link(o)
    for k, v in DEFAULT_POSE.items():
        setattr(fig, k, v)
    for side in SIDES:
        apply_handshape(fig, side, HANDSHAPES["relaxed"][1])
    root.rotation_euler.z = math.radians(s.view_angle)
    apply_figure(root)
    return root


# ---------------------------------------------------------------------------
# 7. Applying the sliders to the joints
# ---------------------------------------------------------------------------

_suspend_updates = False


def figure_objects(root):
    return [root] + list(root.children_recursive)


def apply_figure(root):
    if root is None or not root.asl_fig.is_figure:
        return
    fig = root.asl_fig
    r = math.radians
    joints = {o["asl_joint"]: o for o in root.children_recursive if "asl_joint" in o}

    def setrot(key, x=0.0, y=0.0, z=0.0):
        j = joints.get(key)
        if j:
            j.rotation_euler = (x, y, z)

    setrot("torso", x=r(fig.torso_lean), z=r(-fig.torso_turn))
    setrot("head", x=r(fig.head_nod), y=r(-fig.head_tilt), z=r(-fig.head_turn))

    for side in SIDES:
        m = 1.0 if side == "R" else -1.0   # sideways/twist rotations mirror on the left
        g = lambda k: getattr(fig, f"{side}_{k}")
        setrot(f"{side}_shoulder", x=-r(g("shoulder_forward")), y=m * r(g("shoulder_out")),
               z=m * r(g("shoulder_twist")))
        setrot(f"{side}_elbow", x=-r(g("elbow_bend")), z=m * r(g("forearm_turn")))
        setrot(f"{side}_wrist", x=-r(g("wrist_bend")), y=m * r(g("wrist_side")))
        for f in FINGERS:
            setrot(f"{side}_{f}_base", x=-r(g(f"{f}_base")), y=m * r(g(f"{f}_side")))
            setrot(f"{side}_{f}_mid", x=-r(g(f"{f}_mid")))
            setrot(f"{side}_{f}_tip", x=-r(g(f"{f}_tip")))
        setrot(f"{side}_thumb_cmc", y=m * r(g("thumb_out")))
        setrot(f"{side}_thumb_base", x=-r(g("thumb_across")), z=m * r(g("thumb_roll")))
        setrot(f"{side}_thumb_mid", x=-r(g("thumb_mid")))
        setrot(f"{side}_thumb_tip", x=-r(g("thumb_tip")))

    # eyebrows: -1 furrowed ... +1 raised
    for o in root.children_recursive:
        if "asl_brow" in o:
            sx = o["asl_brow"]
            o.location = (0.032 * sx, -0.084 + 0.003 * fig.brows, 0.163 + 0.012 * fig.brows)
            o.rotation_euler = (0, sx * r(5 - 16 * fig.brows if fig.brows < 0 else 5 + 4 * fig.brows), 0)

    # visibility of body parts
    show = {
        "head": fig.show_head, "torso": fig.show_torso,
        "arm_R": fig.show_arm_R, "arm_L": fig.show_arm_L,
        "hand_R": fig.show_hand_R, "hand_L": fig.show_hand_L,
    }
    for o in root.children_recursive:
        grp = o.get("asl_part")
        if grp is not None:
            hidden = not show.get(grp, True)
            o.hide_render = hidden
            o.hide_viewport = hidden

    # colors + opacity
    s = bpy.context.scene.asl if bpy.context.scene else None
    alpha = fig.opacity
    for slot, color in (("Skin", s.skin_color if s else (0.85, 0.68, 0.56)),
                        ("Shirt", s.shirt_color if s else (0.16, 0.2, 0.3)),
                        ("Feature", (0.08, 0.06, 0.06))):
        mat = bpy.data.materials.get(f"ASL_{slot}_{root.name}")
        if mat:
            set_material_color(mat, color, alpha)


def _update_figure(self, context):
    if not _suspend_updates:
        apply_figure(self.id_data)


def apply_handshape(fig, side, shape):
    global _suspend_updates
    _suspend_updates = True
    try:
        for k, v in shape.items():
            key = f"{side}_{k}"
            if hasattr(fig, key):
                setattr(fig, key, v)
    finally:
        _suspend_updates = False


def read_handshape(fig, side):
    out = {}
    for key in hand_param_keys(side):
        out[key[2:]] = round(getattr(fig, key), 2)
    return out


def figure_pose_dict(fig):
    return {spec[0]: round(getattr(fig, spec[0]), 2) for spec in all_param_specs()} | {"brows": round(fig.brows, 2)}


def all_figures():
    return [o for o in bpy.data.objects if o.asl_fig.is_figure]


def main_figure(scene):
    act = scene.asl.active_figure
    if act and act.asl_fig.is_figure:
        return act
    for o in all_figures():
        if not o.asl_fig.is_ghost:
            return o
    figs = all_figures()
    return figs[0] if figs else None


def _make_figure_group():
    ann = {
        "is_figure": BoolProperty(default=False),
        "is_ghost": BoolProperty(name="Ghost", default=False),
        "opacity": FloatProperty(name="Opacity", default=1.0, min=0.05, max=1.0, update=_update_figure),
        "brows": FloatProperty(name="Eyebrows (raised +, furrowed -)", default=0.0, min=-1.0, max=1.0,
                               update=_update_figure),
    }
    for part, label in (("head", "Head"), ("torso", "Body"), ("arm_R", "Right arm"),
                        ("arm_L", "Left arm"), ("hand_R", "Right hand"), ("hand_L", "Left hand")):
        ann[f"show_{part}"] = BoolProperty(name=label, default=True, update=_update_figure)
    for key, label, d, lo, hi in all_param_specs():
        ann[key] = FloatProperty(name=label, default=d, min=lo, max=hi, step=100, precision=0,
                                 update=_update_figure)
    return type("ASLFigure", (PropertyGroup,), {"__annotations__": ann})


ASLFigure = _make_figure_group()


# ---------------------------------------------------------------------------
# 8. Scene: camera, lights, backdrop, label, framing
# ---------------------------------------------------------------------------

def _get(name):
    return bpy.data.objects.get(name)


def setup_scene(scene, with_figure=True):
    coll = card_collection(scene)

    # remove Blender's default cube if it is still around
    cube = _get("Cube")
    if cube and cube.type == 'MESH' and len(cube.data.vertices) == 8:
        bpy.data.objects.remove(cube, do_unlink=True)

    cam = _get("ASL_Camera")
    if cam is None:
        cam = bpy.data.objects.new("ASL_Camera", bpy.data.cameras.new("ASL_Camera"))
        coll.objects.link(cam)
    cam.data.type = 'ORTHO'
    cam.data.clip_end = 60
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.camera = cam

    if _get("ASL_Backdrop") is None:
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.0)
        me = bpy.data.meshes.new("ASL_Backdrop")
        bm.to_mesh(me)
        bm.free()
        me.materials.append(make_flat_material("ASL_Backdrop", (1, 1, 1)))
        bd = bpy.data.objects.new("ASL_Backdrop", me)
        coll.objects.link(bd)
        bd.parent = cam
        bd.location = (0, 0, -40)
        bd.hide_select = True
        for attr in ("visible_diffuse", "visible_glossy", "visible_shadow",
                     "visible_transmission", "visible_volume_scatter"):
            if hasattr(bd, attr):
                setattr(bd, attr, False)

    if _get("ASL_Label") is None:
        cu = bpy.data.curves.new("ASL_Label", 'FONT')
        cu.align_x = 'CENTER'
        cu.align_y = 'BOTTOM'
        lab = bpy.data.objects.new("ASL_Label", cu)
        coll.objects.link(lab)
        cu.materials.append(make_flat_material("ASL_Label", (0.05, 0.05, 0.07)))
        lab.parent = cam
        lab.hide_select = True

    if _get("ASL_LabelBand") is None:
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)   # 1 x 1 square
        me = bpy.data.meshes.new("ASL_LabelBand")
        bm.to_mesh(me)
        bm.free()
        me.materials.append(make_flat_material("ASL_LabelBand", (1, 1, 1)))
        band = bpy.data.objects.new("ASL_LabelBand", me)
        coll.objects.link(band)
        band.parent = cam
        band.hide_select = True
        for attr in ("visible_diffuse", "visible_glossy", "visible_shadow",
                     "visible_transmission", "visible_volume_scatter"):
            if hasattr(band, attr):
                setattr(band, attr, False)

    if _get("ASL_Key") is None:
        key = bpy.data.objects.new("ASL_Key", bpy.data.lights.new("ASL_Key", 'SUN'))
        key.data.energy = 3.2
        key.data.angle = math.radians(25)
        key.rotation_euler = (math.radians(60), math.radians(-25), math.radians(-25))
        coll.objects.link(key)
        fill = bpy.data.objects.new("ASL_Fill", bpy.data.lights.new("ASL_Fill", 'SUN'))
        fill.data.energy = 1.2
        fill.data.angle = math.radians(40)
        fill.rotation_euler = (math.radians(75), math.radians(30), math.radians(40))
        coll.objects.link(fill)

    world = scene.world or bpy.data.worlds.new("ASL_World")
    scene.world = world
    try:
        world.use_nodes = True
    except Exception:
        pass
    bg = world.node_tree.nodes.get("Background") if world.node_tree else None
    if bg:
        bg.inputs[0].default_value = (0.45, 0.45, 0.48, 1)
        bg.inputs[1].default_value = 0.8

    scene.view_settings.view_transform = 'Standard'
    apply_render_settings(scene)

    if with_figure and not any(not f.asl_fig.is_ghost for f in all_figures()):
        root = build_figure(scene)
        scene.asl.active_figure = root
    update_framing(scene)


def apply_render_settings(scene):
    s = scene.asl
    r = scene.render
    r.resolution_x = s.resolution
    r.resolution_y = s.resolution
    r.resolution_percentage = 100
    r.image_settings.file_format = 'PNG'
    r.image_settings.color_mode = 'RGBA' if s.transparent_bg else 'RGB'
    r.film_transparent = s.transparent_bg
    bd = _get("ASL_Backdrop")
    if bd:
        bd.hide_render = s.transparent_bg

    engines = {e.identifier for e in r.bl_rna.properties["engine"].enum_items}
    if s.engine == 'CYCLES':
        r.engine = 'CYCLES'
        scene.cycles.samples = 64
        scene.cycles.use_denoising = True
    else:
        for cand in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
            if cand in engines:
                r.engine = cand
                break
        if hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = 64

    r.use_freestyle = s.outlines
    r.line_thickness_mode = 'ABSOLUTE'
    r.line_thickness = max(0.5, s.outline_width * s.resolution / 1080.0)
    vl = scene.view_layers[0]
    vl.use_freestyle = s.outlines
    fs = vl.freestyle_settings
    ls = fs.linesets.get("ASL_Outline")
    if ls is None:
        for old in list(fs.linesets):
            fs.linesets.remove(old)
        ls = fs.linesets.new("ASL_Outline")
    ls.select_by_collection = True
    ls.collection = outline_collection()
    ls.select_silhouette = True
    ls.select_border = False
    ls.select_crease = True
    ls.linestyle.color = (0.08, 0.07, 0.07)


def visible_half_height(scene):
    r = scene.render
    ortho = scene.camera.data.ortho_scale
    if r.resolution_x >= r.resolution_y:
        return ortho / 2 * r.resolution_y / r.resolution_x
    return ortho / 2


def update_framing(scene):
    s = scene.asl
    cam = _get("ASL_Camera")
    if cam is None:
        return
    cam.data.ortho_scale = s.frame_zoom
    cam.location = (s.frame_x, -10.0, s.frame_z)
    bd = _get("ASL_Backdrop")
    if bd:
        bd.scale = (s.frame_zoom * 2, s.frame_zoom * 2, 1)
    update_label(scene)


def update_label(scene):
    s = scene.asl
    lab = _get("ASL_Label")
    if lab is None or scene.camera is None:
        return
    text = s.term.strip()
    lab.data.body = text.upper() if s.uppercase else text
    half_h = visible_half_height(scene)
    lab.data.size = s.label_size * 2 * half_h          # fraction of picture height
    lab.location = (0, -half_h + 0.045 * 2 * half_h, -1.0)
    lab.hide_render = not s.show_label or not text
    lab.hide_viewport = lab.hide_render
    band = _get("ASL_LabelBand")
    if band:
        # white strip behind the label so it never sits on top of the figure
        band_h = (s.label_size + 0.07) * 2 * half_h
        band.scale = (s.frame_zoom * 2, band_h, 1)
        band.location = (0, -half_h + band_h / 2, -1.5)
        band.hide_render = lab.hide_render or s.transparent_bg
        band.hide_viewport = band.hide_render


def _update_scene(self, context):
    apply_render_settings(context.scene)
    update_framing(context.scene)
    for root in all_figures():
        apply_figure(root)


def _update_view_angle(self, context):
    for root in all_figures():
        root.rotation_euler.z = math.radians(self.view_angle)


FRAMINGS = {
    "upper": ("Upper body (signing space)", 0.0, 0.5, 1.05),
    "face": ("Face + hands", 0.0, 0.55, 0.75),
    "hand": ("Close-up on right hand", None, None, 0.36),
}


def set_framing(scene, key):
    s = scene.asl
    label, x, z, zoom = FRAMINGS[key]
    if key == "hand":
        root = main_figure(scene)
        palm = _get(f"{root.name}.R_palm") if root else None
        if palm:
            bpy.context.view_layer.update()
            c = palm.matrix_world @ Vector((0, 0, -0.06))
            x, z = c.x, c.z - 0.02
        else:
            x, z = -0.15, 0.3
    s.frame_x, s.frame_z, s.frame_zoom = x, z, zoom


# ---------------------------------------------------------------------------
# 9. Arrows
# ---------------------------------------------------------------------------

ARROW_SHAPES = {
    "straight": ("Straight", [((-0.1, 0, 0), 'VECTOR'), ((0.1, 0, 0), 'VECTOR')], False),
    "arc": ("Arc", [((-0.1, 0, 0), 'AUTO'), ((0, 0, 0.06), 'AUTO'), ((0.1, 0, 0), 'AUTO')], False),
    "double": ("Back and forth", [((-0.1, 0, 0), 'VECTOR'), ((0.1, 0, 0), 'VECTOR')], True),
    "circle": ("Circle", [((0, 0, 0.07), 'AUTO'), ((0.07, 0, 0), 'AUTO'),
                          ((0, 0, -0.07), 'AUTO'), ((-0.07, 0, 0), 'AUTO'),
                          ((-0.0495, 0, 0.0495), 'AUTO')], False),
    "zigzag": ("Zigzag", [((-0.1, 0, 0), 'VECTOR'), ((-0.05, 0, 0.04), 'VECTOR'),
                          ((0, 0, 0), 'VECTOR'), ((0.05, 0, 0.04), 'VECTOR'),
                          ((0.1, 0, 0), 'VECTOR')], False),
}

ARROW_DEPTH = -0.75   # arrows sit between the figure and the camera so they draw on top


def create_arrow(scene, points, double=False, location=(0, ARROW_DEPTH, 0), kind="custom"):
    """points: list of dicts {co, hl, hr, hl_type, hr_type}."""
    s = scene.asl
    coll = card_collection(scene)
    idx = 1
    while _get(f"Arrow{idx}"):
        idx += 1
    name = f"Arrow{idx}"
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    cu.use_path = True
    cu.bevel_depth = s.arrow_width
    cu.bevel_resolution = 4
    cu.use_fill_caps = True
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(len(points) - 1)
    for bp, p in zip(sp.bezier_points, points):
        bp.co = p["co"]
        bp.handle_left_type = p.get("hl_type", 'AUTO')
        bp.handle_right_type = p.get("hr_type", 'AUTO')
        if "hl" in p:
            bp.handle_left_type = bp.handle_right_type = 'FREE'
            bp.handle_left = p["hl"]
            bp.handle_right = p["hr"]
    mat = make_flat_material(f"ASL_Arrow_{name}", s.arrow_color)
    cu.materials.append(mat)
    obj = bpy.data.objects.new(name, cu)
    coll.objects.link(obj)
    obj.location = location
    obj["asl_arrow"] = kind
    obj["asl_double"] = bool(double)

    heads = [(1.0, 'FORWARD_Y')] + ([(0.0, 'TRACK_NEGATIVE_Y')] if double else [])
    for i, (factor, axis) in enumerate(heads):
        me = cone_mesh(f"{name}_head", s.arrow_width * 3.6, s.arrow_width * 7.5)
        me.materials.append(mat)
        head = bpy.data.objects.new(f"{name}.head{i}", me)
        coll.objects.link(head)
        head.hide_select = True
        head["asl_arrow_head_of"] = name
        con = head.constraints.new('FOLLOW_PATH')
        con.target = obj
        con.use_curve_follow = True
        con.use_fixed_location = True
        con.offset_factor = factor
        con.forward_axis = axis
        con.up_axis = 'UP_Z'
    return obj


def flip_arrow(arrow):
    """Reverse the point order so the arrowhead moves to the other end."""
    sp = arrow.data.splines[0]
    pts = [(bp.co.copy(), bp.handle_left.copy(), bp.handle_right.copy()) for bp in sp.bezier_points]
    for bp, (co, hl, hr) in zip(sp.bezier_points, reversed(pts)):
        bp.handle_left_type = bp.handle_right_type = 'FREE'
        bp.co, bp.handle_left, bp.handle_right = co, hr, hl


def arrow_heads(arrow):
    return [o for o in bpy.data.objects if o.get("asl_arrow_head_of") == arrow.name]


def remove_arrow(arrow):
    for h in arrow_heads(arrow):
        bpy.data.objects.remove(h, do_unlink=True)
    bpy.data.objects.remove(arrow, do_unlink=True)


def all_arrows():
    return [o for o in bpy.data.objects if "asl_arrow" in o and o.type == 'CURVE']


def restyle_arrows(scene):
    s = scene.asl
    for a in all_arrows():
        a.data.bevel_depth = s.arrow_width
        for m in a.data.materials:
            set_material_color(m, s.arrow_color)
        for h in arrow_heads(a):
            me = cone_mesh(h.data.name, s.arrow_width * 3.6, s.arrow_width * 7.5)
            me.materials.append(a.data.materials[0])
            old = h.data
            h.data = me
            if old.users == 0:
                bpy.data.meshes.remove(old)


def _update_arrows(self, context):
    restyle_arrows(context.scene)


# ---------------------------------------------------------------------------
# 10. Sign "recipes" (JSON) - save, load, export, batch rebuild
# ---------------------------------------------------------------------------

def sign_to_dict(scene):
    s = scene.asl
    figs = []
    for root in sorted(all_figures(), key=lambda o: (not o.asl_fig.is_ghost, o.name)):
        f = root.asl_fig
        figs.append({
            "ghost": f.is_ghost,
            "opacity": round(f.opacity, 3),
            "show": {k: getattr(f, f"show_{k}") for k in ("head", "torso", "arm_R", "arm_L", "hand_R", "hand_L")},
            "pose": figure_pose_dict(f),
        })
    arrows = []
    for a in all_arrows():
        sp = a.data.splines[0]
        arrows.append({
            "kind": a.get("asl_arrow", "custom"),
            "double": bool(a.get("asl_double", False)),
            "matrix": [list(row) for row in a.matrix_world],
            "points": [{"co": list(bp.co), "hl": list(bp.handle_left), "hr": list(bp.handle_right)}
                       for bp in sp.bezier_points],
        })
    return {
        "version": RECIPE_VERSION,
        "term": s.term, "category": s.category, "notes": s.notes,
        "card": {"uppercase": s.uppercase, "label_size": s.label_size, "show_label": s.show_label},
        "framing": {"x": s.frame_x, "z": s.frame_z, "zoom": s.frame_zoom, "view_angle": s.view_angle},
        "arrow_style": {"color": list(s.arrow_color), "width": s.arrow_width},
        "figures": figs,
        "arrows": arrows,
    }


def clear_sign(scene):
    for a in all_arrows():
        remove_arrow(a)
    for root in all_figures():
        coll = root.users_collection[0] if root.users_collection else None
        for o in figure_objects(root):
            bpy.data.objects.remove(o, do_unlink=True)
        if coll and coll.name != CARD_COLLECTION and not coll.objects:
            bpy.data.collections.remove(coll)
    for m in list(bpy.data.materials):
        if m.users == 0 and m.name.startswith("ASL_"):
            bpy.data.materials.remove(m)


def sign_from_dict(scene, data):
    s = scene.asl
    setup_scene(scene, with_figure=False)
    clear_sign(scene)
    s.term = data.get("term", "")
    s.category = data.get("category", "word")
    s.notes = data.get("notes", "")
    card = data.get("card", {})
    s.uppercase = card.get("uppercase", s.uppercase)
    s.label_size = card.get("label_size", s.label_size)
    s.show_label = card.get("show_label", s.show_label)
    fr = data.get("framing", {})
    s.view_angle = fr.get("view_angle", 0.0)
    s.frame_x, s.frame_z, s.frame_zoom = fr.get("x", 0.0), fr.get("z", 0.5), fr.get("zoom", 1.05)
    st = data.get("arrow_style", {})
    if "color" in st:
        s.arrow_color = st["color"]
    if "width" in st:
        s.arrow_width = st["width"]

    main = None
    for fd in data.get("figures", []):
        root = build_figure(scene, ghost=fd.get("ghost", False))
        f = root.asl_fig
        global _suspend_updates
        _suspend_updates = True
        try:
            for k, v in fd.get("pose", {}).items():
                if hasattr(f, k):
                    setattr(f, k, v)
            for k, v in fd.get("show", {}).items():
                setattr(f, f"show_{k}", v)
            f.opacity = fd.get("opacity", f.opacity)
        finally:
            _suspend_updates = False
        apply_figure(root)
        if not f.is_ghost and main is None:
            main = root
    s.active_figure = main

    for ad in data.get("arrows", []):
        pts = [{"co": p["co"], "hl": p["hl"], "hr": p["hr"]} for p in ad["points"]]
        a = create_arrow(scene, pts, double=ad.get("double", False), kind=ad.get("kind", "custom"))
        a.matrix_world = Matrix(ad["matrix"])
    update_framing(scene)


def export_card(scene, out_dir):
    """Render the PNG, save the recipe JSON and update manifest.json."""
    s = scene.asl
    if not s.term.strip():
        raise ValueError("Type the English term first.")
    out_dir = bpy.path.abspath(out_dir)
    os.makedirs(os.path.join(out_dir, "signs"), exist_ok=True)
    card_id = f"{s.category}-{slugify(s.term)}"
    image_rel = f"{card_id}.png"
    recipe_rel = f"signs/{card_id}.json"

    apply_render_settings(scene)
    update_framing(scene)
    scene.render.filepath = os.path.join(out_dir, image_rel)
    bpy.ops.render.render(write_still=True)

    # A second copy without the English label, used when the web app quizzes you.
    quiz_rel = None
    if s.export_quiz and s.show_label:
        quiz_rel = f"quiz/{card_id}.png"
        os.makedirs(os.path.join(out_dir, "quiz"), exist_ok=True)
        s.show_label = False
        try:
            update_label(scene)
            scene.render.filepath = os.path.join(out_dir, quiz_rel)
            bpy.ops.render.render(write_still=True)
        finally:
            s.show_label = True
            update_label(scene)

    with open(os.path.join(out_dir, recipe_rel), "w", encoding="utf-8") as fh:
        json.dump(sign_to_dict(scene), fh, indent=2)

    man_path = os.path.join(out_dir, "manifest.json")
    manifest = {"cards": []}
    if os.path.exists(man_path):
        try:
            with open(man_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception:
            pass
    cards = [c for c in manifest.get("cards", []) if c.get("id") != card_id]
    cards.append({
        "id": card_id, "term": s.term.strip(), "category": s.category, "notes": s.notes.strip(),
        "image": image_rel, "quizImage": quiz_rel or image_rel, "recipe": recipe_rel,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    cards.sort(key=lambda c: (c["category"], c["term"].lower()))
    manifest["cards"] = cards
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return os.path.join(out_dir, image_rel)


def rebuild_all(cards_dir):
    """Re-render every recipe in cards_dir/signs (used by the command line)."""
    scene = bpy.context.scene
    signs = os.path.join(cards_dir, "signs")
    files = sorted(f for f in os.listdir(signs) if f.endswith(".json"))
    for f in files:
        with open(os.path.join(signs, f), encoding="utf-8") as fh:
            data = json.load(fh)
        sign_from_dict(scene, data)
        path = export_card(scene, cards_dir)
        print(f"[ASL] rendered {path}")


# ---------------------------------------------------------------------------
# 11. Custom handshape library (saved next to Blender's user config)
# ---------------------------------------------------------------------------

def user_shapes_path():
    base = bpy.utils.user_resource('CONFIG')
    return os.path.join(base, "asl_handshapes.json")


def load_user_shapes():
    try:
        with open(user_shapes_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


_shape_items_cache = []


def handshape_items(self, context):
    global _shape_items_cache
    items = [(k, v[0], "") for k, v in HANDSHAPES.items()]
    for k in sorted(load_user_shapes()):
        items.append((f"user:{k}", f"★ {k}", "Your saved handshape"))
    _shape_items_cache = items
    return items


def get_shape(key):
    if key.startswith("user:"):
        return load_user_shapes().get(key[5:], {})
    return HANDSHAPES.get(key, (None, {}))[1]


# ---------------------------------------------------------------------------
# 12. Scene settings
# ---------------------------------------------------------------------------

def _figure_poll(self, obj):
    return obj.asl_fig.is_figure


class ASLSceneSettings(PropertyGroup):
    term: StringProperty(name="English term", default="", update=_update_scene)
    category: EnumProperty(name="Category", items=[
        ("letter", "Letter", ""), ("number", "Number", ""), ("word", "Word", ""),
        ("phrase", "Phrase", ""), ("grammar", "Grammar example", "")], default="word")
    notes: StringProperty(name="Notes", description="How to make the sign (shown on the card back)")
    uppercase: BoolProperty(name="Uppercase (gloss style)", default=True, update=_update_scene)
    show_label: BoolProperty(name="Show label", default=True, update=_update_scene)
    label_size: FloatProperty(name="Label size", description="Text height as a fraction of the picture",
                              default=0.075, min=0.02, max=0.2, update=_update_scene)
    out_dir: StringProperty(name="Cards folder", subtype='DIR_PATH', default="//cards/")
    export_quiz: BoolProperty(name="Also export quiz copy (no label)", default=True,
                              description="Saves a second PNG without the English term, for practice mode")
    resolution: IntProperty(name="Size (px)", default=1080, min=256, max=4096, update=_update_scene)
    engine: EnumProperty(name="Renderer", items=[("EEVEE", "EEVEE (fast)", ""), ("CYCLES", "Cycles (slower)", "")],
                         default="EEVEE", update=_update_scene)
    outlines: BoolProperty(name="Outlines", default=True, update=_update_scene)
    outline_width: FloatProperty(name="Outline width", default=2.2, min=0.5, max=8, update=_update_scene)
    transparent_bg: BoolProperty(name="Transparent background", default=False, update=_update_scene)
    skin_color: FloatVectorProperty(name="Skin", subtype='COLOR', size=3, min=0, max=1,
                                    default=(0.86, 0.66, 0.53), update=_update_scene)
    shirt_color: FloatVectorProperty(name="Shirt", subtype='COLOR', size=3, min=0, max=1,
                                     default=(0.035, 0.05, 0.1), update=_update_scene)
    ghost_opacity: FloatProperty(name="New ghost opacity", default=0.3, min=0.05, max=1.0)
    ghost_both_arms: BoolProperty(name="Ghost both arms", default=False,
                                  description="New ghosts show both arms (for two-handed movements)")
    arrow_color: FloatVectorProperty(name="Arrow color", subtype='COLOR', size=3, min=0, max=1,
                                     default=(0.85, 0.1, 0.12), update=_update_arrows)
    arrow_width: FloatProperty(name="Arrow thickness", default=0.006, min=0.002, max=0.03, precision=3,
                               update=_update_arrows)
    frame_x: FloatProperty(name="Center X", default=0.0, update=_update_scene, step=1)
    frame_z: FloatProperty(name="Center height", default=0.5, update=_update_scene, step=1)
    frame_zoom: FloatProperty(name="Frame width", default=1.05, min=0.1, max=5, update=_update_scene, step=1)
    view_angle: FloatProperty(name="View angle", description="Turn every figure to show a 3/4 view",
                              default=0.0, min=-90, max=90, update=_update_view_angle)
    active_figure: PointerProperty(name="Editing", type=bpy.types.Object, poll=_figure_poll)
    handshape: EnumProperty(name="Handshape", items=handshape_items)
    new_shape_name: StringProperty(name="Name", default="")


# ---------------------------------------------------------------------------
# 13. Operators (the buttons)
# ---------------------------------------------------------------------------

class ASL_OT_new_scene(Operator):
    bl_idname = "asl.new_scene"
    bl_label = "New Card Scene"
    bl_description = "Set up camera, lights, label and a signer figure"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        setup_scene(context.scene)
        return {'FINISHED'}


class ASL_OT_clear_sign(Operator):
    bl_idname = "asl.clear_sign"
    bl_label = "Start New Sign"
    bl_description = "Remove all ghosts and arrows and reset the figure"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        clear_sign(context.scene)
        setup_scene(context.scene)
        context.scene.asl.term = ""
        context.scene.asl.notes = ""
        return {'FINISHED'}


class ASL_OT_add_ghost(Operator):
    bl_idname = "asl.add_ghost"
    bl_label = "Snapshot as Ghost"
    bl_description = ("Copy the figure you are editing into a faded ghost. Pose the ghost as the "
                      "START of the movement and the solid figure as the END")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        src = main_figure(scene)
        if src is None:
            self.report({'ERROR'}, "Click New Card Scene first")
            return {'CANCELLED'}
        ghost = build_figure(scene, ghost=True)
        g, f = ghost.asl_fig, src.asl_fig
        global _suspend_updates
        _suspend_updates = True
        try:
            for k, v in figure_pose_dict(f).items():
                setattr(g, k, v)
            # Ghosts only show the parts that move. A faded copy sitting exactly on
            # top of the solid head/body would just look smudged.
            arms = ("R", "L") if scene.asl.ghost_both_arms else ("R",)
            for k in ("head", "torso", "arm_R", "arm_L", "hand_R", "hand_L"):
                keep = k[-1] in arms and k.startswith(("arm", "hand"))
                setattr(g, f"show_{k}", keep and getattr(f, f"show_{k}"))
        finally:
            _suspend_updates = False
        apply_figure(ghost)
        return {'FINISHED'}


class ASL_OT_flip_arrows(Operator):
    bl_idname = "asl.flip_arrows"
    bl_label = "Flip Direction"
    bl_description = "Reverse the selected arrows so they point the other way"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for o in context.selected_objects:
            if "asl_arrow" in o and o.type == 'CURVE':
                flip_arrow(o)
        return {'FINISHED'}


class ASL_OT_delete_figure(Operator):
    bl_idname = "asl.delete_figure"
    bl_label = "Delete This Figure"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = main_figure(context.scene)
        if root is None:
            return {'CANCELLED'}
        if not root.asl_fig.is_ghost and sum(1 for f in all_figures() if not f.asl_fig.is_ghost) == 1:
            self.report({'WARNING'}, "Keep at least one solid figure")
            return {'CANCELLED'}
        coll = root.users_collection[0]
        for o in figure_objects(root):
            bpy.data.objects.remove(o, do_unlink=True)
        if coll.name != CARD_COLLECTION:
            bpy.data.collections.remove(coll)
        context.scene.asl.active_figure = None
        return {'FINISHED'}


class ASL_OT_select_figure(Operator):
    bl_idname = "asl.select_figure"
    bl_label = "Edit Figure"
    bl_options = {'REGISTER', 'UNDO'}
    name: StringProperty()

    def execute(self, context):
        context.scene.asl.active_figure = bpy.data.objects.get(self.name)
        return {'FINISHED'}


class ASL_OT_apply_handshape(Operator):
    bl_idname = "asl.apply_handshape"
    bl_label = "Apply Handshape"
    bl_options = {'REGISTER', 'UNDO'}
    side: StringProperty(default="R")

    def execute(self, context):
        root = main_figure(context.scene)
        shape = get_shape(context.scene.asl.handshape)
        if root and shape:
            apply_handshape(root.asl_fig, self.side, shape)
            apply_figure(root)
        return {'FINISHED'}


class ASL_OT_save_handshape(Operator):
    bl_idname = "asl.save_handshape"
    bl_label = "Save Right Hand as Handshape"
    bl_description = "Save the right hand's finger positions to your handshape library"

    def execute(self, context):
        s = context.scene.asl
        name = s.new_shape_name.strip()
        root = main_figure(context.scene)
        if not name or root is None:
            self.report({'ERROR'}, "Type a name for the handshape first")
            return {'CANCELLED'}
        shapes = load_user_shapes()
        shapes[name] = read_handshape(root.asl_fig, "R")
        os.makedirs(os.path.dirname(user_shapes_path()), exist_ok=True)
        with open(user_shapes_path(), "w", encoding="utf-8") as fh:
            json.dump(shapes, fh, indent=2)
        self.report({'INFO'}, f"Saved handshape '{name}'")
        return {'FINISHED'}


class ASL_OT_copy_hand(Operator):
    bl_idname = "asl.copy_hand"
    bl_label = "Copy Hand"
    bl_description = "Copy the finger positions from one hand to the other"
    bl_options = {'REGISTER', 'UNDO'}
    src: StringProperty(default="R")

    def execute(self, context):
        root = main_figure(context.scene)
        if root:
            dst = "L" if self.src == "R" else "R"
            apply_handshape(root.asl_fig, dst, read_handshape(root.asl_fig, self.src))
            apply_figure(root)
        return {'FINISHED'}


class ASL_OT_reset_group(Operator):
    bl_idname = "asl.reset_group"
    bl_label = "Reset"
    bl_options = {'REGISTER', 'UNDO'}
    prefix: StringProperty()

    def execute(self, context):
        root = main_figure(context.scene)
        if root:
            f = root.asl_fig
            global _suspend_updates
            _suspend_updates = True
            try:
                for key, label, d, lo, hi in all_param_specs():
                    if key.startswith(self.prefix):
                        setattr(f, key, DEFAULT_POSE.get(key, d))
            finally:
                _suspend_updates = False
            apply_figure(root)
        return {'FINISHED'}


class ASL_OT_add_arrow(Operator):
    bl_idname = "asl.add_arrow"
    bl_label = "Add Arrow"
    bl_options = {'REGISTER', 'UNDO'}
    kind: EnumProperty(items=[(k, v[0], "") for k, v in ARROW_SHAPES.items()])

    def execute(self, context):
        scene = context.scene
        label, pts, double = ARROW_SHAPES[self.kind]
        points = [{"co": co, "hl_type": t, "hr_type": t} for co, t in pts]
        loc = Vector((0.0, ARROW_DEPTH, 0.45))
        root = main_figure(scene)
        palm = _get(f"{root.name}.R_palm") if root else None
        if palm:
            context.view_layer.update()
            c = palm.matrix_world @ Vector((0, 0, -0.05))
            loc = Vector((c.x - 0.1, ARROW_DEPTH, c.z + 0.12))
        arrow = create_arrow(scene, points, double=double, location=loc, kind=self.kind)
        for o in context.selected_objects:
            o.select_set(False)
        arrow.select_set(True)
        context.view_layer.objects.active = arrow
        self.report({'INFO'}, "Arrow added. Move it with G, or press Tab to edit its points.")
        return {'FINISHED'}


class ASL_OT_delete_arrows(Operator):
    bl_idname = "asl.delete_arrows"
    bl_label = "Delete Selected Arrows"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for o in list(context.selected_objects):
            if "asl_arrow" in o:
                remove_arrow(o)
        return {'FINISHED'}


class ASL_OT_framing(Operator):
    bl_idname = "asl.framing"
    bl_label = "Frame"
    bl_options = {'REGISTER', 'UNDO'}
    key: EnumProperty(items=[(k, v[0], "") for k, v in FRAMINGS.items()])

    def execute(self, context):
        set_framing(context.scene, self.key)
        return {'FINISHED'}


class ASL_OT_look_through(Operator):
    bl_idname = "asl.look_through"
    bl_label = "View Through Card Camera"
    bl_description = "Show exactly what the exported card will contain"

    def execute(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces[0].region_3d.view_perspective = 'CAMERA'
        return {'FINISHED'}


class ASL_OT_export(Operator):
    bl_idname = "asl.export_card"
    bl_label = "Export Card PNG"
    bl_description = "Render the card, and save its recipe and manifest entry into the cards folder"

    def execute(self, context):
        s = context.scene.asl
        if s.out_dir.startswith("//") and not bpy.data.filepath:
            self.report({'ERROR'}, "Pick a Cards folder (or save this .blend file first)")
            return {'CANCELLED'}
        try:
            path = export_card(context.scene, s.out_dir)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved {path}")
        return {'FINISHED'}


class ASL_OT_load_sign(Operator):
    bl_idname = "asl.load_sign"
    bl_label = "Open Sign Recipe"
    bl_description = "Load a sign saved earlier (cards/signs/*.json) to edit it again"
    bl_options = {'REGISTER', 'UNDO'}
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        with open(self.filepath, encoding="utf-8") as fh:
            data = json.load(fh)
        sign_from_dict(context.scene, data)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 14. Panels (the sidebar UI)
# ---------------------------------------------------------------------------

class _ASLPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "ASL Cards"


def _sliders(layout, fig, keys):
    col = layout.column(align=True)
    for k in keys:
        col.prop(fig, k, slider=True)


class ASL_PT_card(_ASLPanel, Panel):
    bl_label = "Card"

    def draw(self, context):
        s = context.scene.asl
        lay = self.layout
        if _get("ASL_Camera") is None:
            lay.operator("asl.new_scene", icon='ADD')
            return
        lay.prop(s, "term")
        lay.prop(s, "category")
        lay.prop(s, "notes")
        row = lay.row(align=True)
        row.prop(s, "show_label")
        row.prop(s, "uppercase", text="Uppercase")
        lay.prop(s, "label_size", slider=True)
        lay.separator()
        lay.prop(s, "out_dir")
        lay.prop(s, "export_quiz")
        lay.operator("asl.export_card", icon='RENDER_STILL')
        row = lay.row(align=True)
        row.operator("asl.look_through", icon='CAMERA_DATA', text="Camera View")
        row.operator("asl.clear_sign", icon='FILE_NEW', text="New Sign")
        lay.operator("asl.load_sign", icon='FILE_FOLDER')


class ASL_PT_figures(_ASLPanel, Panel):
    bl_label = "Figures & Ghosts"

    @classmethod
    def poll(cls, context):
        return _get("ASL_Camera") is not None

    def draw(self, context):
        s = context.scene.asl
        lay = self.layout
        cur = main_figure(context.scene)
        col = lay.column(align=True)
        for root in sorted(all_figures(), key=lambda o: o.name):
            label = f"{root.name}  ({'ghost' if root.asl_fig.is_ghost else 'solid'})"
            op = col.operator("asl.select_figure", text=label,
                              icon='RADIOBUT_ON' if root == cur else 'RADIOBUT_OFF', depress=root == cur)
            op.name = root.name
        row = lay.row(align=True)
        row.operator("asl.add_ghost", icon='GHOST_ENABLED')
        row.prop(s, "ghost_opacity", text="")
        lay.prop(s, "ghost_both_arms")
        if cur:
            f = cur.asl_fig
            box = lay.box()
            box.label(text=f"Editing: {cur.name}")
            box.prop(f, "opacity", slider=True)
            box.label(text="Show:")
            grid = box.grid_flow(columns=2, align=True)
            for k in ("head", "torso", "arm_R", "arm_L", "hand_R", "hand_L"):
                grid.prop(f, f"show_{k}", toggle=True)
            box.operator("asl.delete_figure", icon='TRASH')


class ASL_PT_body(_ASLPanel, Panel):
    bl_label = "Body & Face"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return main_figure(context.scene) is not None

    def draw(self, context):
        fig = main_figure(context.scene).asl_fig
        _sliders(self.layout, fig, [p[0] for p in BODY_PARAMS] + ["brows"])
        self.layout.operator("asl.reset_group", text="Reset Body").prefix = "torso_"
        self.layout.operator("asl.reset_group", text="Reset Head").prefix = "head_"


def _make_arm_panel(side):
    title = "Right Arm" if side == "R" else "Left Arm"

    class P(_ASLPanel, Panel):
        bl_label = title
        bl_idname = f"ASL_PT_arm_{side}"
        bl_options = {'DEFAULT_CLOSED'}

        @classmethod
        def poll(cls, context):
            return main_figure(context.scene) is not None

        def draw(self, context):
            fig = main_figure(context.scene).asl_fig
            if side == "R":
                self.layout.label(text="Signer's right (viewer's left)", icon='INFO')
            _sliders(self.layout, fig, [f"{side}_{p[0]}" for p in ARM_PARAMS])
            row = self.layout.row(align=True)
            for pre in ("shoulder", "elbow", "forearm", "wrist"):
                row.operator("asl.reset_group", text=f"↺ {pre}").prefix = f"{side}_{pre}"
    P.__name__ = f"ASL_PT_arm_{side}"
    return P


def _make_hand_panel(side):
    title = "Right Hand" if side == "R" else "Left Hand"

    class P(_ASLPanel, Panel):
        bl_label = title
        bl_idname = f"ASL_PT_hand_{side}"

        @classmethod
        def poll(cls, context):
            return main_figure(context.scene) is not None

        def draw(self, context):
            s = context.scene.asl
            fig = main_figure(context.scene).asl_fig
            lay = self.layout
            row = lay.row(align=True)
            row.prop(s, "handshape", text="")
            row.operator("asl.apply_handshape", text="Apply").side = side
            row = lay.row(align=True)
            row.operator("asl.copy_hand", text=f"Copy to {'left' if side == 'R' else 'right'} hand",
                         icon='MOD_MIRROR').src = side
            for f in ("thumb",) + FINGERS:
                box = lay.box()
                box.label(text=f.capitalize())
                params = THUMB_PARAMS if f == "thumb" else FINGER_PARAMS
                _sliders(box, fig, [f"{side}_{f}_{p[0]}" for p in params])
            if side == "R":
                box = lay.box()
                box.label(text="Save this hand to your library")
                row = box.row(align=True)
                row.prop(s, "new_shape_name", text="")
                row.operator("asl.save_handshape", text="Save", icon='FILE_TICK')
    P.__name__ = f"ASL_PT_hand_{side}"
    return P


class ASL_PT_arrows(_ASLPanel, Panel):
    bl_label = "Movement Arrows"

    @classmethod
    def poll(cls, context):
        return _get("ASL_Camera") is not None

    def draw(self, context):
        s = context.scene.asl
        lay = self.layout
        grid = lay.grid_flow(columns=2, align=True)
        for k, v in ARROW_SHAPES.items():
            grid.operator("asl.add_arrow", text=v[0]).kind = k
        lay.prop(s, "arrow_color")
        lay.prop(s, "arrow_width")
        row = lay.row(align=True)
        row.operator("asl.flip_arrows", icon='ARROW_LEFTRIGHT')
        row.operator("asl.delete_arrows", icon='TRASH', text="Delete")
        lay.label(text="Tip: select an arrow, press Tab, then move", icon='INFO')
        lay.label(text="its points with G. Tab again to finish.")


class ASL_PT_framing(_ASLPanel, Panel):
    bl_label = "Framing & Look"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _get("ASL_Camera") is not None

    def draw(self, context):
        s = context.scene.asl
        lay = self.layout
        col = lay.column(align=True)
        for k, v in FRAMINGS.items():
            col.operator("asl.framing", text=v[0]).key = k
        col = lay.column(align=True)
        col.prop(s, "frame_x")
        col.prop(s, "frame_z")
        col.prop(s, "frame_zoom")
        lay.prop(s, "view_angle", slider=True)
        lay.separator()
        lay.prop(s, "skin_color")
        lay.prop(s, "shirt_color")
        lay.prop(s, "outlines")
        if s.outlines:
            lay.prop(s, "outline_width")
        lay.prop(s, "transparent_bg")
        lay.prop(s, "resolution")
        lay.prop(s, "engine")


CLASSES = [
    ASLFigure, ASLSceneSettings,
    ASL_OT_new_scene, ASL_OT_clear_sign, ASL_OT_add_ghost, ASL_OT_delete_figure, ASL_OT_select_figure,
    ASL_OT_apply_handshape, ASL_OT_save_handshape, ASL_OT_copy_hand, ASL_OT_reset_group,
    ASL_OT_add_arrow, ASL_OT_flip_arrows, ASL_OT_delete_arrows, ASL_OT_framing, ASL_OT_look_through, ASL_OT_export,
    ASL_OT_load_sign,
    ASL_PT_card, ASL_PT_figures, ASL_PT_body,
    _make_arm_panel("R"), _make_hand_panel("R"), _make_arm_panel("L"), _make_hand_panel("L"),
    ASL_PT_arrows, ASL_PT_framing,
]


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.asl_fig = PointerProperty(type=ASLFigure)
    bpy.types.Scene.asl = PointerProperty(type=ASLSceneSettings)


def unregister():
    del bpy.types.Scene.asl
    del bpy.types.Object.asl_fig
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


def _cli_args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
    args = _cli_args()
    if "--rebuild" in args:
        rebuild_all(os.path.abspath(args[args.index("--rebuild") + 1]))
