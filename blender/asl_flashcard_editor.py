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
    "version": (0, 6, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > ASL Cards",
    "description": "Pose a signer figure, export ASL flashcards, and record signs with live webcam tracking",
    "category": "Import-Export",
}

import base64
import collections
import hashlib
import json
import math
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone

import bmesh
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Euler, Matrix, Quaternion, Vector

SIDES = ("R", "L")                 # signer's right / signer's left
FINGERS = ("index", "middle", "ring", "pinky")
CARD_COLLECTION = "ASL Card"
OUTLINE_COLLECTION = "ASL Outlined (internal)"
RECIPE_VERSION = 2
# Bump when the way figures are built changes; older figures are rebuilt automatically.
BUILD_VERSION = 6


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
# The thumb's first bone (metacarpal) runs inside the fleshy pad at the base of the palm,
# so the visible thumb starts about halfway up the palm, as on a real hand.
THUMB_BASE = Vector((-0.024, -0.004, -0.014))
THUMB_DIR = Vector((-0.43, -0.25, -0.87))      # resting thumb direction
THUMB_BEND_DIR = Vector((0.75, -0.62, 0.0))    # thumb bends across the palm
THUMB_LENS = (0.044, 0.031, 0.026)              # metacarpal, proximal, distal
THUMB_RADII = (0.016, 0.0098, 0.0092)           # first one is the thumb pad (thenar)
NECK_LEN = 0.07

# Default section lengths in millimeters: (base, middle, tip).
# The thumb's "base" section is the part inside the palm.
SEGMENTS = ("base", "mid", "tip")
DEFAULT_LENGTHS_MM = {
    "thumb": (44, 31, 26),
    "index": (44, 26, 21),
    "middle": (49, 30, 22),
    "ring": (46, 28, 21),
    "pinky": (36, 21, 19),
}
DEFAULT_NECK_MM = 70


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
    ("across", "Across the front of the palm", 0, -40, 90),
    ("out", "Out to the side (+) / toward pinky (-)", 0, -90, 80),
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
                                            _thumb(30, -10, 30, 55, 35))),
    "B": ("B (flat, thumb in)", _shape(_fingers(0, 0, 0, (2, 0, -2, -3)),
                                       _thumb(60, 5, 20, 25, 10))),
    "5": ("5 (open, spread)", _shape(_fingers(0, 0, 0, (14, 2, -12, -24)),
                                     _thumb(0, 14, 0, 0, 0))),
    "C": ("C (curved)", _shape(_fingers(35, 45, 30, (3, 0, -3, -6)),
                               _thumb(25, 45, 25, 15, 10))),
    "O": ("O (fingertips meet thumb)", _shape(_fingers(50, 60, 40, (3, 0, -3, -6)),
                                              _thumb(45, 40, 35, 25, 20))),
    "1": ("1 / D shape (index up)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                           _thumb(30, -10, 30, 55, 35),
                                           index_base=0, index_mid=0, index_tip=0)),
    "L": ("L (index + thumb)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                      _thumb(-5, 48, 0, 0, 0),
                                      index_base=0, index_mid=0, index_tip=0)),
    "V": ("V / 2 (index + middle spread)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                                  _thumb(30, -10, 30, 55, 35),
                                                  index_base=0, index_mid=0, index_tip=0, index_side=10,
                                                  middle_base=0, middle_mid=0, middle_tip=0, middle_side=-8)),
    "W": ("W / 6 (three fingers)", _shape(_fingers(0, 0, 0, (10, 0, -10, 0)),
                                          _thumb(40, 0, 30, 45, 35),
                                          pinky_base=85, pinky_mid=100, pinky_tip=60)),
    "I": ("I (pinky up)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                 _thumb(30, -10, 30, 55, 35),
                                 pinky_base=0, pinky_mid=0, pinky_tip=0)),
    "Y": ("Y (thumb + pinky)", _shape(_fingers(90, 105, 60, (0, 0, 0, -10)),
                                      _thumb(-5, 48, 0, 0, 0),
                                      pinky_base=0, pinky_mid=0, pinky_tip=0)),
    "X": ("X (hooked index)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                     _thumb(30, -10, 30, 55, 35),
                                     index_base=10, index_mid=80, index_tip=50)),
    "flatO": ("Flat O", _shape(_fingers(60, 10, 5, (2, 0, -2, -3)),
                               _thumb(40, 30, 30, 10, 5))),
    "bentB": ("Bent B", _shape(_fingers(80, 0, 0, (2, 0, -2, -3)),
                               _thumb(55, 5, 20, 20, 10))),
    "claw": ("Claw 5", _shape(_fingers(15, 70, 45, (14, 2, -12, -24)),
                              _thumb(5, 22, 10, 30, 30))),
    # --- remaining fingerspelling letters ---
    "D": ("D (index up, others touch thumb)", _shape(_fingers(55, 75, 40, (0, -2, -4, -6)),
                                                     _thumb(50, 30, 35, 25, 15),
                                                     index_base=0, index_mid=0, index_tip=0, index_side=0)),
    "E": ("E (fingertips down on thumb)", _shape(_fingers(25, 105, 75, (0, 0, -2, -4)),
                                                 _thumb(25, -55, 10, 20, 15))),
    "F": ("F (index meets thumb, 3 fingers up)", _shape(_fingers(0, 0, 0, (0, 0, -8, -16)),
                                                        _thumb(35, 30, 35, 20, 20),
                                                        index_base=40, index_mid=60, index_tip=35)),
    "G": ("G (index + thumb parallel)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                               _thumb(0, 8, 10, 0, 0),
                                               index_base=0, index_mid=0, index_tip=0)),
    "H": ("H / U sideways (two fingers)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                                 _thumb(30, -10, 30, 55, 35),
                                                 index_base=0, index_mid=0, index_tip=0, index_side=-2,
                                                 middle_base=0, middle_mid=0, middle_tip=0, middle_side=2)),
    "K": ("K / P (index up, middle out, thumb between)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                                                _thumb(25, 0, 20, 0, 0),
                                                                index_base=0, index_mid=0, index_tip=0, index_side=6,
                                                                middle_base=45, middle_mid=0, middle_tip=0,
                                                                middle_side=-6)),
    "M": ("M (thumb under three fingers)", _shape(_fingers(80, 95, 45, (0, 0, 0, 0)),
                                                  _thumb(15, -50, 10, 30, 15),
                                                  pinky_base=95, pinky_mid=110, pinky_tip=65)),
    "N": ("N (thumb under two fingers)", _shape(_fingers(80, 95, 45, (0, 0, 0, 0)),
                                                _thumb(20, -45, 10, 30, 15),
                                                ring_base=95, ring_mid=110, ring_tip=65,
                                                pinky_base=95, pinky_mid=110, pinky_tip=65)),
    "R": ("R (index and middle crossed)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                                 _thumb(30, -10, 30, 55, 35),
                                                 index_base=8, index_mid=0, index_tip=0, index_side=-9,
                                                 middle_base=0, middle_mid=0, middle_tip=0, middle_side=9)),
    "T": ("T (thumb between index and middle)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                                       _thumb(15, -20, 0, 0, 0),
                                                       index_base=60, index_mid=80, index_tip=30)),
    "U": ("U (two fingers up together)", _shape(_fingers(90, 105, 60, (0, 0, 0, 0)),
                                                _thumb(30, -10, 30, 55, 35),
                                                index_base=0, index_mid=0, index_tip=0, index_side=-2,
                                                middle_base=0, middle_mid=0, middle_tip=0, middle_side=2)),
}


# Tuned against a real hand: thumb tips are solved to land where they do in each
# handshape (on the fingertips for O/D/F, across the middle finger for S, under the
# fingers for M/N, between index and middle for T...), without passing through the palm.
_TUNED = {
    'A': {"thumb_across": -11.7, "thumb_out": 6.8, "thumb_roll": -7.3, "thumb_mid": 52.6, "thumb_tip": 19.5},
    'S': {"thumb_across": 27.4, "thumb_out": -5.8, "thumb_roll": -0.4, "thumb_mid": -17.1, "thumb_tip": 88.4},
    'B': {"thumb_across": 36.3, "thumb_out": 28.4, "thumb_roll": 29.0, "thumb_mid": 65.8, "thumb_tip": 70.8, "index_side": 1, "middle_side": 0, "ring_side": -1, "pinky_side": -2},
    'E': {"thumb_across": 32.8, "thumb_out": -19.6, "thumb_roll": -0.0, "thumb_mid": -20.0, "thumb_tip": -30.0},
    'C': {"index_base": 25, "index_mid": 40, "index_tip": 35, "middle_base": 25, "middle_mid": 40, "middle_tip": 35, "ring_base": 25, "ring_mid": 40, "ring_tip": 35, "pinky_base": 25, "pinky_mid": 40, "pinky_tip": 35, "index_side": 0, "middle_side": 0, "ring_side": 0, "pinky_side": 0, "thumb_across": 48.0, "thumb_out": 49.4, "thumb_roll": 6.3, "thumb_mid": 21.2, "thumb_tip": 27.4},
    'O': {"index_base": 45, "index_mid": 55, "index_tip": 45, "middle_base": 45, "middle_mid": 55, "middle_tip": 45, "ring_base": 45, "ring_mid": 55, "ring_tip": 45, "pinky_base": 45, "pinky_mid": 55, "pinky_tip": 45, "index_side": 0, "middle_side": 0, "ring_side": 0, "pinky_side": 0, "thumb_across": 56.4, "thumb_out": 49.4, "thumb_roll": 7.9, "thumb_mid": 18.0, "thumb_tip": 1.6},
    'D': {"thumb_across": 34.8, "thumb_out": 12.1, "thumb_roll": -3.8, "thumb_mid": 11.7, "thumb_tip": 53.2},
    'F': {"thumb_across": 29.1, "thumb_out": -2.9, "thumb_roll": -13.7, "thumb_mid": 2.8, "thumb_tip": -0.0},
    'T': {"thumb_across": 9.7, "thumb_out": -14.6, "thumb_roll": -0.5, "thumb_mid": -0.7, "thumb_tip": 46.3},
    'M': {"thumb_across": 12.5, "thumb_out": -31.7, "thumb_roll": 10.5, "thumb_mid": 15.3, "thumb_tip": 90.0},
    'N': {"thumb_across": 12.5, "thumb_out": -0.2, "thumb_roll": 10.5, "thumb_mid": 43.7, "thumb_tip": 90.0},
    'K': {"thumb_across": 24.8, "thumb_out": -13.1, "thumb_roll": 25.7, "thumb_mid": -9.8, "thumb_tip": -0.0},
    'G': {"thumb_across": -4.1, "thumb_out": 4.8, "thumb_roll": 60.0, "thumb_mid": 36.3, "thumb_tip": 17.1},
    'H': {"thumb_across": 22.3, "thumb_out": -8.0, "thumb_roll": 10.6, "thumb_mid": 19.7, "thumb_tip": 59.7},
    'U': {"thumb_across": 22.3, "thumb_out": -8.0, "thumb_roll": 10.6, "thumb_mid": 19.7, "thumb_tip": 59.7},
    'V': {"thumb_across": 22.3, "thumb_out": -8.0, "thumb_roll": 10.6, "thumb_mid": 19.7, "thumb_tip": 59.7},
    'R': {"thumb_across": 22.3, "thumb_out": -8.0, "thumb_roll": 10.6, "thumb_mid": 19.7, "thumb_tip": 59.7, "index_side": -9, "middle_side": 12, "middle_base": 10, "index_base": 0},
    'W': {"thumb_across": 33.4, "thumb_out": -0.7, "thumb_roll": 21.8, "thumb_mid": 39.8, "thumb_tip": 28.3},
    'I': {"thumb_across": 27.4, "thumb_out": -5.8, "thumb_roll": -0.4, "thumb_mid": -17.1, "thumb_tip": 88.4},
    '1': {"thumb_across": 27.4, "thumb_out": -5.8, "thumb_roll": -0.4, "thumb_mid": -17.1, "thumb_tip": 88.4},
    'X': {"thumb_across": 27.4, "thumb_out": -5.8, "thumb_roll": -0.4, "thumb_mid": -17.1, "thumb_tip": 88.4},
}
for _name, _vals in _TUNED.items():
    HANDSHAPES[_name][1].update(_vals)
# bent B and flat O hold the thumb like B and O do
HANDSHAPES["bentB"][1].update({k: v for k, v in _TUNED["B"].items() if k.startswith("thumb")})
HANDSHAPES["flatO"][1].update({k: v for k, v in _TUNED["O"].items() if k.startswith("thumb")})


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


# --- Face: eyebrows, mouth shapes, cheeks and eyelids (Face sliders and live tracking) ---
MOUTH_POS = (0.0, -0.088, 0.064)
MOUTH_RX, MOUTH_RZ = 0.016, 0.0042
CHEEK_R = 0.017
FACE_PARAMS = [
    # key, label, default, min, max
    ("brow_inner_R", "Right brow, inner end (up +)", 0.0, -1.0, 1.0),
    ("brow_mid_R", "Right brow, middle (up +)", 0.0, -1.0, 1.0),
    ("brow_outer_R", "Right brow, outer end (up +)", 0.0, -1.0, 1.0),
    ("brow_inner_L", "Left brow, inner end (up +)", 0.0, -1.0, 1.0),
    ("brow_mid_L", "Left brow, middle (up +)", 0.0, -1.0, 1.0),
    ("brow_outer_L", "Left brow, outer end (up +)", 0.0, -1.0, 1.0),
    ("brow_squeeze", "Squeeze brows together", 0.0, 0.0, 1.0),
    ("mouth_open", "Mouth open", 0.0, 0.0, 1.0),
    ("smile", "Smile (+) / frown (-)", 0.0, -1.0, 1.0),
    ("pucker", "Pucker (OO)", 0.0, 0.0, 1.0),
    ("cheek_puff", "Puffed cheeks", 0.0, 0.0, 1.0),
    ("eye_close_R", "Close right eye", 0.0, 0.0, 1.0),
    ("eye_close_L", "Close left eye", 0.0, 0.0, 1.0),
]
FACE_KEYS = ["brows"] + [p[0] for p in FACE_PARAMS]
BROW_NODES = ("inner", "mid", "outer")
BROW_KEYS = ["brows", "brow_squeeze"] + [f"brow_{n}_{s}" for s in SIDES for n in BROW_NODES]

# Skull ellipsoid (head space) that the eyebrows follow.
SKULL_C, SKULL_R = Vector((0, 0, 0.11)), Vector((0.082, 0.095, 0.112))
BROW_X = (0.015, 0.051)                 # inner and outer end, distance from the middle of the face
BROW_UP = {"inner": 0.010, "mid": 0.011, "outer": 0.009}
BROW_DOWN = {"inner": 0.0065, "mid": 0.0055, "outer": 0.005}


def _smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def brow_weights(s):
    """How much each of the 3 brow nodes moves the point at s (0 = inner end, 1 = outer end).
    The weights always add up to 1, so the brow stays one smooth curve."""
    if s < 0.5:
        return {"inner": _smooth(1 - 2 * s), "mid": _smooth(2 * s), "outer": 0.0}
    return {"inner": 0.0, "mid": _smooth(2 - 2 * s), "outer": _smooth(2 * s - 1)}


def skull_front_y(x, z, proud=0.0025):
    """y of the front of the skull at (x, z), plus a small gap so features sit on top."""
    q = 1 - (x / SKULL_R.x) ** 2 - ((z - SKULL_C.z) / SKULL_R.z) ** 2
    return -SKULL_R.y * math.sqrt(max(0.04, q)) - proud


def brow_vertices(sx, dz=None, dx=None, rings=18, around=8):
    """Vertices of one eyebrow (a tapered tube lying on the forehead), in head space.
    dz(s), dx(s): extra up and inward movement along the brow (for the shape keys)."""
    pts = []
    for i in range(rings):
        s = i / (rings - 1)
        x = BROW_X[0] + (BROW_X[1] - BROW_X[0]) * s
        z = 0.157 + 0.007 * math.sin(math.pi * (0.15 + 0.85 * s))
        if dx:
            x -= dx(s)
        if dz:
            z += dz(s)
        pts.append(Vector((x * sx, skull_front_y(x, z), z)))
    verts = []
    for i, c in enumerate(pts):
        s = i / (rings - 1)
        t = (pts[min(i + 1, rings - 1)] - pts[max(i - 1, 0)]).normalized()
        up = Vector((0, 0, 1))
        n = (up - t * up.dot(t)).normalized()           # "up" across the brow
        f = t.cross(n).normalized()
        if f.y > 0:
            f = -f
        r = 0.0041 - 0.0021 * s ** 0.8                     # thick at the inner end
        if i in (0, rings - 1):
            r *= 0.55                                      # rounded ends
        for k in range(around):
            a = 2 * math.pi * k / around
            verts.append(c + n * (r * math.cos(a)) + f * (0.65 * r * math.sin(a)))
    verts.append(pts[0] - (pts[1] - pts[0]).normalized() * 0.0012)
    verts.append(pts[-1] + (pts[-1] - pts[-2]).normalized() * 0.0010)
    return verts


def brow_mesh(name, sx, rings=18, around=8):
    """A brow with shape keys for each node: inner/mid/outer up and down, and squeeze."""
    verts = brow_vertices(sx, rings=rings, around=around)
    faces = []
    for i in range(rings - 1):
        for k in range(around):
            a, b = i * around + k, i * around + (k + 1) % around
            faces.append((a, b, b + around, a + around))
    c0, c1 = len(verts) - 2, len(verts) - 1
    last = (rings - 1) * around
    for k in range(around):
        faces.append((c0, (k + 1) % around, k))
        faces.append((c1, last + k, last + (k + 1) % around))
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    me.validate()
    me.update()
    # outward normals: flip if a side face points into the tube
    p0 = me.polygons[0]
    ring_c = sum((Vector(verts[k]) for k in range(around)), Vector()) / around
    if p0.normal.dot(Vector(p0.center) - ring_c) < 0:
        me.flip_normals()
    for p in me.polygons:
        p.use_smooth = True
    return me


def add_brow_shapes(obj, sx):
    obj.shape_key_add(name="Basis", from_mix=False)

    def key(name, dz=None, dx=None):
        kb = obj.shape_key_add(name=name, from_mix=False)
        for i, co in enumerate(brow_vertices(sx, dz, dx)):
            kb.data[i].co = co

    for n in BROW_NODES:
        key(f"{n}_up", dz=lambda s, n=n: BROW_UP[n] * brow_weights(s)[n])
        key(f"{n}_down", dz=lambda s, n=n: -BROW_DOWN[n] * brow_weights(s)[n])
    # squeeze (corrugator): inner ends slide toward each other and a little down
    key("squeeze", dz=lambda s: -0.0025 * _smooth(1 - 1.6 * s), dx=lambda s: 0.0045 * _smooth(1 - 1.4 * s))


def add_mouth_shapes(obj):
    """Shape keys that deform the mouth: open, smile, frown, pucker."""
    obj.shape_key_add(name="Basis", from_mix=False)
    base = [v.co.copy() for v in obj.data.vertices]

    def key(name, fn):
        kb = obj.shape_key_add(name=name, from_mix=False)
        for i, co in enumerate(base):
            kb.data[i].co = fn(co)

    rx = MOUTH_RX
    key("open", lambda c: Vector((c.x * 0.85, c.y, c.z * 4.6 - 0.007)))
    key("smile", lambda c: Vector((c.x * 1.22, c.y, c.z * 0.8 + 0.0085 * (c.x / rx) ** 2 - 0.0015)))
    key("frown", lambda c: Vector((c.x * 0.95, c.y, c.z * 0.9 - 0.0075 * (c.x / rx) ** 2 + 0.002)))
    key("pucker", lambda c: Vector((c.x * 0.45, c.y - 0.004, c.z * 2.1)))


def face_rig(root):
    """Objects that make up the face of one figure."""
    rig = {"brows": [], "eyes": [], "cheeks": [], "mouth": None,
           "brow_state": {k: 0.0 for k in BROW_KEYS}}
    for o in root.children_recursive:
        if "asl_brow" in o:
            rig["brows"].append((o, o["asl_brow"]))
        elif "asl_eye" in o:
            rig["eyes"].append((o, o["asl_eye"]))
        elif "asl_cheek" in o:
            rig["cheeks"].append(o)
        elif "asl_mouth" in o:
            rig["mouth"] = o
    return rig


def brow_node_values(v, side):
    """Final (inner, mid, outer, squeeze) for one brow: its own nodes plus the overall
    Eyebrows slider (raise lifts the middle most; furrow pulls the inner end down most)."""
    b = v.get("brows", 0.0)
    if b >= 0:
        add = (0.9 * b, b, 0.8 * b)
        sq = 0.0
    else:
        add = (b, 0.6 * b, 0.3 * b)
        sq = -0.8 * b
    vals = [max(-1.0, min(1.0, v.get(f"brow_{n}_{side}", 0.0) + a)) for n, a in zip(BROW_NODES, add)]
    return vals + [max(0.0, min(1.0, v.get("brow_squeeze", 0.0) + sq))]


def set_face(rig, v):
    """v: dict with any of FACE_KEYS. Missing keys are left as they are."""
    if any(k in v for k in BROW_KEYS):
        st = rig.setdefault("brow_state", {k: 0.0 for k in BROW_KEYS})
        st.update({k: v[k] for k in BROW_KEYS if k in v})
        for o, sx in rig["brows"]:
            side = "L" if sx > 0 else "R"                    # +X is the signer's left
            keys = o.data.shape_keys
            if keys is None:
                continue
            kb = keys.key_blocks
            vals = brow_node_values(st, side)
            for n, val in zip(BROW_NODES, vals):
                kb[f"{n}_up"].value = max(0.0, val)
                kb[f"{n}_down"].value = max(0.0, -val)
            kb["squeeze"].value = vals[3]
    m = rig["mouth"]
    if m is not None and m.data.shape_keys:
        kb = m.data.shape_keys.key_blocks
        if "mouth_open" in v:
            kb["open"].value = v["mouth_open"]
        if "smile" in v:
            kb["smile"].value = max(0.0, v["smile"])
            kb["frown"].value = max(0.0, -v["smile"])
        if "pucker" in v:
            kb["pucker"].value = v["pucker"]
    if "cheek_puff" in v:
        k = 1.0 + 0.7 * v["cheek_puff"]
        for o in rig["cheeks"]:
            o.scale = (k, k * 1.15, k)
    for o, sx in rig["eyes"]:
        key = "eye_close_L" if sx > 0 else "eye_close_R"     # +X is the signer's left
        if key in v:
            o.scale = (1.0, 1.0, max(0.12, 1.0 - 0.88 * v[key]))


def _tag_segment(obj, key, length, r1, r2, segments=14):
    """Remember how a finger/neck section was built so its length can change later."""
    obj["asl_seg"] = key
    obj["asl_len"] = length
    obj["asl_r"] = [r1, r2, segments]


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
    nm = b.part("neck_mesh", capsule_mesh("neck", NECK_LEN, 0.045, 0.048, segments=16), neck, "head", "skin",
                offset=(0, 0.006, NECK_LEN + 0.005))
    _tag_segment(nm, "neck", NECK_LEN, 0.045, 0.048, 16)
    head = b.joint("head", neck, (0, 0, 0.08))
    b.part("skull", ellipsoid_mesh("skull", 0.082, 0.095, 0.112, center=(0, 0, 0.11)),
           head, "head", "skin")
    b.part("nose", ellipsoid_mesh("nose", 0.0095, 0.013, 0.017, center=(0, -0.094, 0.105), segments=12),
           head, "head", "skin")
    for sx in (-1, 1):
        eye = b.part(f"eye_{sx}", ellipsoid_mesh("eye", 0.011, 0.007, 0.012, segments=12),
                     head, "head", "feature", offset=(0.031 * sx, -0.083, 0.128))
        eye["asl_eye"] = sx
        cheek = b.part(f"cheek_{sx}", ellipsoid_mesh("cheek", CHEEK_R, CHEEK_R * 0.9, CHEEK_R, segments=16),
                       head, "head", "skin", offset=(0.049 * sx, -0.051, 0.072))
        cheek["asl_cheek"] = sx
        b.part(f"ear_{sx}", ellipsoid_mesh("ear", 0.012, 0.022, 0.03,
                                             center=(0.081 * sx, 0.0, 0.11), segments=12),
               head, "head", "skin")
        bo = b.part(f"brow_{sx}", brow_mesh("brow", sx), head, "head", "feature")
        bo["asl_brow"] = sx
        add_brow_shapes(bo, sx)
    mo = b.part("mouth", ellipsoid_mesh("mouth", MOUTH_RX, 0.006, MOUTH_RZ, segments=24),
                head, "head", "feature", offset=MOUTH_POS)
    mo["asl_mouth"] = True
    add_mouth_shapes(mo)

    # arms and hands -----------------------------------------------------
    for side in SIDES:
        sh = b.joint(f"{side}_shoulder", torso, mirror_x(SHOULDER_POS, side), order='ZXY')
        ua = b.part(f"{side}_upper_arm", capsule_mesh("upper_arm", UPPER_ARM_LEN, 0.047, 0.041),
                    sh, f"arm_{side}", "shirt")
        _tag_segment(ua, "upper_arm", UPPER_ARM_LEN, 0.047, 0.041, 20)
        el = b.joint(f"{side}_elbow", sh, (0, 0, -UPPER_ARM_LEN), order='ZXY')
        fa = b.part(f"{side}_forearm", capsule_mesh("forearm", FOREARM_LEN, 0.041, 0.031),
                    el, f"arm_{side}", "shirt")
        _tag_segment(fa, "forearm", FOREARM_LEN, 0.041, 0.031, 20)
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
                seg = b.part(f"{side}_{f}_{jname}_seg", capsule_mesh("finger", seg_len, r, r * 0.95, segments=14),
                             jt, f"hand_{side}", "skin")
                _tag_segment(seg, f"{side}_{f}_{jname}", seg_len, r, r * 0.95)
                parent, offset = jt, (0, 0, -seg_len)

        # two joints at the thumb base: "cmc" swings the thumb sideways in the palm's
        # plane (out), "base" swings it across the front of the palm and rolls it.
        cmc = b.joint(f"{side}_thumb_cmc", wr, mirror_x(THUMB_BASE, side))
        rest = frame_matrix(mirror_x(THUMB_DIR, side), mirror_x(THUMB_BEND_DIR, side))
        parent = b.joint(f"{side}_thumb_base", cmc, (0, 0, 0), rest=rest)
        seg = b.part(f"{side}_thumb_base_seg", capsule_mesh("thumb", THUMB_LENS[0], THUMB_RADII[0], 0.0115, 16),
                     parent, f"hand_{side}", "skin")
        _tag_segment(seg, f"{side}_thumb_base", THUMB_LENS[0], THUMB_RADII[0], 0.0115, 16)
        offset = (0, 0, -THUMB_LENS[0])
        for jname, seg_len, r in (("mid", THUMB_LENS[1], THUMB_RADII[1]), ("tip", THUMB_LENS[2], THUMB_RADII[2])):
            jt = b.joint(f"{side}_thumb_{jname}", parent, offset)
            seg = b.part(f"{side}_thumb_{jname}_seg", capsule_mesh("thumb", seg_len, r, r * 0.95, 14),
                         jt, f"hand_{side}", "skin")
            _tag_segment(seg, f"{side}_thumb_{jname}", seg_len, r, r * 0.95)
            parent, offset = jt, (0, 0, -seg_len)

    root["asl_build"] = BUILD_VERSION
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
    for other in all_figures():
        if other is not root and custom_parts(other):
            copy_custom_parts(other, root)
            break
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
    apply_proportions(root, joints)

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

    # face: eyebrows (-1 furrowed ... +1 raised), mouth, cheeks, eyes
    set_face(face_rig(root), {k: getattr(fig, k) for k in FACE_KEYS})

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

    # arrows attached to this figure follow it
    if any(a.asl_tether.enabled and a.asl_tether.figure == root for a in all_arrows()):
        update_tethers(root)


def _rebuild_capsule(o, length, r1, r2, segs):
    old = o.data
    me = capsule_mesh(old.name, length, r1, r2, segments=int(segs))
    for m in old.materials:
        me.materials.append(m)
    o.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)


def apply_proportions(root, joints=None):
    """Resize body parts to match the Proportions and Body Shape sliders."""
    body = bpy.context.scene.asl_body
    shp = body_shape(body)
    if joints is None:
        joints = {o["asl_joint"]: o for o in root.children_recursive if "asl_joint" in o}
    for o in root.children_recursive:
        key = o.get("asl_seg")
        if key is None:
            continue
        thick = 1.0
        if key in ("upper_arm", "forearm"):
            length = shp["L1"] if key == "upper_arm" else shp["L2"]
            thick = shp["thick"]
        else:
            length = getattr(body, key) / 1000.0
        if o.get("asl_custom"):
            # a sculpted part keeps its shape and is stretched instead of rebuilt
            base = o.get("asl_custom_len", o.get("asl_len", length)) or length
            bt = o.get("asl_custom_thick", 1.0) or 1.0
            o.scale = (thick / bt, thick / bt, length / base)
            continue
        if abs(o.get("asl_len", 0.0) - length) > 1e-6 or abs(o.get("asl_thick", 1.0) - thick) > 1e-6:
            r1, r2, segs = o["asl_r"]
            _rebuild_capsule(o, length, r1 * thick, r2 * thick, segs)
            o["asl_len"] = length
            o["asl_thick"] = thick
    # each joint sits at the end of the section before it
    for side in SIDES:
        for f in ("thumb",) + FINGERS:
            for seg, nxt in (("base", "mid"), ("mid", "tip")):
                j = joints.get(f"{side}_{f}_{nxt}")
                if j:
                    j.matrix_parent_inverse = Matrix.Translation((0, 0, -getattr(body, f"{side}_{f}_{seg}") / 1000.0))
        m = 1 if side == "R" else -1
        el, wr = joints.get(f"{side}_elbow"), joints.get(f"{side}_wrist")
        if el:
            el.matrix_parent_inverse = Matrix.Translation((0, 0, -shp["L1"]))
        if wr:
            wr.matrix_parent_inverse = (Matrix.Translation((0, 0, -shp["L2"]))
                                        @ Matrix.Rotation(math.radians(90 * m), 4, 'Z'))
            wr.scale = (shp["hand"],) * 3
    # joint position sliders (millimeters, in each joint's parent space) + body width/height
    for side in SIDES:
        m = 1.0 if side == "R" else -1.0
        g = lambda k: getattr(body, f"{side}_{k}") / 1000.0
        cmc = joints.get(f"{side}_thumb_cmc")
        if cmc:   # wrist space: -Z toward fingers, +X toward pinky (right hand), -Y palm side
            cmc.location = (m * g("thumbpos_across"), -g("thumbpos_out"), -g("thumbpos_along"))
        sh = joints.get(f"{side}_shoulder")
        if sh:    # torso space: +Z up, -X is the right side, -Y forward
            sh.location = (-m * g("shoulderpos_out") - m * abs(SHOULDER_POS.x) * (shp["w"] - 1),
                           -g("shoulderpos_forward"),
                           g("shoulderpos_up") + SHOULDER_POS.z * (shp["h"] - 1))
    for o in root.children_recursive:
        if o.get("asl_part") == "torso" and o.parent is not None and o.parent.get("asl_joint") == "torso":
            o.scale = (shp["w"], shp["d"], shp["h"])
    neck = joints.get("neck")
    if neck:
        neck.location = (0, 0, 0.47 * (shp["h"] - 1))

    neck_len = body.neck / 1000.0
    head = joints.get("head")
    if head:
        head.matrix_parent_inverse = Matrix.Translation((0, 0, neck_len + 0.01))
        head.scale = (shp["head"],) * 3
    for o in root.children_recursive:
        if o.get("asl_seg") == "neck":
            o.matrix_parent_inverse = Matrix.Translation((0, 0.006, neck_len + 0.005))


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
    d = {spec[0]: round(getattr(fig, spec[0]), 2) for spec in all_param_specs()}
    for k in FACE_KEYS:
        d[k] = round(getattr(fig, k), 3)
    return d


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
    for key, label, d, lo, hi in FACE_PARAMS:
        ann[key] = FloatProperty(name=label, default=d, min=lo, max=hi, update=_update_figure)
    for part, label in (("head", "Head"), ("torso", "Body"), ("arm_R", "Right arm"),
                        ("arm_L", "Left arm"), ("hand_R", "Right hand"), ("hand_L", "Left hand")):
        ann[f"show_{part}"] = BoolProperty(name=label, default=True, update=_update_figure)
    for key, label, d, lo, hi in all_param_specs():
        ann[key] = FloatProperty(name=label, default=d, min=lo, max=hi, step=100, precision=0,
                                 update=_update_figure)
    return type("ASLFigure", (PropertyGroup,), {"__annotations__": ann})


ASLFigure = _make_figure_group()


# --- Proportions: finger section lengths and neck length (shared by every figure) ---

def length_specs():
    """Yield (key, label, default_mm) for every length slider."""
    labels = {"base": "Base section", "mid": "Middle section", "tip": "Tip section"}
    for side in SIDES:
        for f in ("thumb",) + FINGERS:
            for seg, mm in zip(SEGMENTS, DEFAULT_LENGTHS_MM[f]):
                label = "Base (inside palm)" if (f == "thumb" and seg == "base") else labels[seg]
                yield (f"{side}_{f}_{seg}", f"{label} (mm)", mm)
    yield ("neck", "Neck length (mm)", DEFAULT_NECK_MM)


# Joint position adjustments (mm), applied on top of the default joint positions.
POSITION_SPECS = [
    ("thumbpos_along", "Toward fingers +", 25),
    ("thumbpos_across", "Toward pinky +", 20),
    ("thumbpos_out", "Out of palm +", 15),
    ("shoulderpos_up", "Up +", 60),
    ("shoulderpos_out", "Away from body +", 60),
    ("shoulderpos_forward", "Forward +", 60),
]


def position_specs():
    """Yield (key, label, limit_mm) for every joint-position slider."""
    for side in SIDES:
        for key, label, lim in POSITION_SPECS:
            yield (f"{side}_{key}", label, lim)


# Body shape (shared by every figure). Percentages scale a body part; arm lengths are in mm.
SHAPE_SPECS = [
    ("head_size", "Head size (%)", 100, 70, 140),
    ("torso_width", "Body width (%)", 100, 70, 140),
    ("torso_height", "Body height (%)", 100, 75, 130),
    ("torso_depth", "Body depth, front to back (%)", 100, 70, 150),
    ("upper_arm", "Upper arm length (mm)", round(UPPER_ARM_LEN * 1000), 200, 380),
    ("forearm", "Forearm length (mm)", round(FOREARM_LEN * 1000), 180, 340),
    ("arm_thickness", "Arm thickness (%)", 100, 60, 170),
    ("hand_size", "Hand size (%)", 100, 75, 130),
]
SHAPE_DEFAULTS = {k: d for k, _, d, _, _ in SHAPE_SPECS}


def all_body_keys():
    return [k for k, _, _ in length_specs()] + [k for k, _, _ in position_specs()]


def body_shape(body):
    """Body shape values as plain numbers (factors and meters)."""
    g = lambda k: getattr(body, k, SHAPE_DEFAULTS[k])
    return {"head": g("head_size") / 100, "w": g("torso_width") / 100, "h": g("torso_height") / 100,
            "d": g("torso_depth") / 100, "L1": g("upper_arm") / 1000, "L2": g("forearm") / 1000,
            "thick": g("arm_thickness") / 100, "hand": g("hand_size") / 100}


_mirroring = False


def outdated_figures():
    return [r for r in all_figures() if r.get("asl_build", 1) < BUILD_VERSION]


# --- Sculpted (custom-shaped) body parts ---------------------------------------
# A sculpted part keeps its mesh when the figure is rebuilt, is shared by every figure in the
# scene (ghosts are the same person), and is stretched rather than rebuilt by length sliders.

def part_suffix(o):
    return o.name.split(".", 1)[1] if "." in o.name else o.name


def figure_of(obj):
    o = obj
    while o is not None:
        if o.asl_fig.is_figure:
            return o
        o = o.parent
    return None


def custom_parts(root):
    return {part_suffix(o): o for o in root.children_recursive if o.type == 'MESH' and o.get("asl_custom")}


CUSTOM_PROPS = ("asl_custom", "asl_custom_len", "asl_custom_thick", "asl_orig_mesh")


def make_custom(o):
    """Mark a part as sculpted, keeping a copy of its original shape for Reset Part."""
    if o.get("asl_custom"):
        return
    orig = o.data.copy()
    orig.name = "ASL_original_" + part_suffix(o)
    orig.use_fake_user = True
    o["asl_orig_mesh"] = orig.name
    if "asl_len" in o:
        o["asl_custom_len"] = o["asl_len"] / max(1e-6, o.scale.z)
        o["asl_custom_thick"] = o.get("asl_thick", 1.0)
    o["asl_custom"] = True


def _assign_mesh(target, mesh_src):
    """Give target a copy of mesh_src, keeping target's own (per-figure) materials."""
    mats = list(target.data.materials)
    old = target.data
    me = mesh_src.copy()
    me.materials.clear()
    for m in mats:
        me.materials.append(m)
    target.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)


def copy_custom_parts(src_root, dst_root):
    for suffix, o in custom_parts(src_root).items():
        t = bpy.data.objects.get(f"{dst_root.name}.{suffix}")
        if t is None or t is o:
            continue
        _assign_mesh(t, o.data)
        for k in CUSTOM_PROPS:
            if k in o:
                t[k] = o[k]
            elif k in t:
                del t[k]
        t.scale = o.scale


def share_custom_parts(src_root):
    for root in all_figures():
        if root is not src_root:
            copy_custom_parts(src_root, root)


def mirror_mesh_x(me):
    for v in me.vertices:
        v.co.x = -v.co.x
    if me.shape_keys:
        for kb in me.shape_keys.key_blocks:
            for d in kb.data:
                d.co.x = -d.co.x
    me.flip_normals()
    me.update()


def mirror_suffix(suffix):
    """Name of the same part on the other side of the body, or None for a middle part."""
    if suffix.startswith("R_"):
        return "L_" + suffix[2:]
    if suffix.startswith("L_"):
        return "R_" + suffix[2:]
    m = re.match(r"^(.*_)(-?1)$", suffix)
    if m:
        return m.group(1) + ("1" if m.group(2) == "-1" else "-1")
    return None


def upgrade_figure(scene, root):
    """Rebuild a figure made by an older version of the add-on, keeping its pose and settings."""
    global _suspend_updates
    f = root.asl_fig
    name, ghost, role = root.name, f.is_ghost, root.get("asl_role")
    pose = figure_pose_dict(f)
    shows = {k: getattr(f, f"show_{k}") for k in ("head", "torso", "arm_R", "arm_L", "hand_R", "hand_L")}
    opacity, angle = f.opacity, root.rotation_euler.z
    tethered = [a for a in all_arrows() if a.asl_tether.figure == root]
    was_active = scene.asl.active_figure == root
    custom = {sfx: (o.data, {k: o[k] for k in CUSTOM_PROPS if k in o}, tuple(o.scale))
              for sfx, o in custom_parts(root).items()}
    for me, _, _ in custom.values():
        me.use_fake_user = True          # keep the sculpted mesh while the old figure is removed

    coll = root.users_collection[0] if root.users_collection else None
    for o in figure_objects(root):
        bpy.data.objects.remove(o, do_unlink=True)
    if coll and coll.name != CARD_COLLECTION and not coll.objects:
        bpy.data.collections.remove(coll)

    new = build_figure(scene, ghost=ghost, name=name)
    for sfx, (me, props, scale) in custom.items():
        t = bpy.data.objects.get(f"{new.name}.{sfx}")
        if t is not None:
            _assign_mesh(t, me)
            for k, v in props.items():
                t[k] = v
            t.scale = scale
        me.use_fake_user = False
    if role:
        new["asl_role"] = role
    _suspend_updates = True
    try:
        for k, v in pose.items():
            if hasattr(new.asl_fig, k):
                setattr(new.asl_fig, k, v)
        for k, v in shows.items():
            setattr(new.asl_fig, f"show_{k}", v)
        new.asl_fig.opacity = opacity
    finally:
        _suspend_updates = False
    new.rotation_euler.z = angle
    for a in tethered:
        a.asl_tether.figure = new
    if was_active:
        scene.asl.active_figure = new
    apply_figure(new)
    return new


def upgrade_old_figures(scene=None):
    scene = scene or bpy.context.scene
    old = outdated_figures()
    for root in old:
        upgrade_figure(scene, root)
    return len(old)


def refresh_all_figures():
    upgrade_old_figures()
    for root in all_figures():
        apply_figure(root)


def _length_updater(key):
    def update(self, context):
        global _mirroring
        if _mirroring:
            return
        if self.mirror and key[:2] in ("R_", "L_"):
            other = ("L_" if key.startswith("R_") else "R_") + key[2:]
            _mirroring = True
            try:
                setattr(self, other, getattr(self, key))
            finally:
                _mirroring = False
        refresh_all_figures()
    return update


def _update_mirror(self, context):
    global _mirroring
    if self.mirror:
        _mirroring = True
        try:
            for key in all_body_keys():
                if key.startswith("R_"):
                    setattr(self, "L_" + key[2:], getattr(self, key))
        finally:
            _mirroring = False
    refresh_all_figures()


def _make_body_group():
    ann = {"mirror": BoolProperty(name="Mirror left and right hands", default=True, update=_update_mirror,
                                  description="Changing one hand's lengths changes the other hand too")}
    for key, label, mm in length_specs():
        ann[key] = FloatProperty(name=label, default=mm, min=round(mm * 0.5), max=round(mm * 1.6),
                                 step=50, precision=1, update=_length_updater(key))
    for key, label, lim in position_specs():
        ann[key] = FloatProperty(name=label, default=0.0, min=-lim, max=lim,
                                 step=50, precision=1, update=_length_updater(key))
    for key, label, d, lo, hi in SHAPE_SPECS:
        ann[key] = FloatProperty(name=label, default=d, min=lo, max=hi, step=50, precision=0,
                                 update=_length_updater(key))
    return type("ASLBody", (PropertyGroup,), {"__annotations__": ann})


ASLBody = _make_body_group()


def body_to_dict(body):
    d = {key: round(getattr(body, key), 2) for key in all_body_keys()}
    for key, _, dflt, _, _ in SHAPE_SPECS:
        v = round(getattr(body, key), 2)
        if v != dflt:
            d[key] = v
    d["mirror"] = body.mirror
    return d


def body_from_dict(body, d):
    global _mirroring
    _mirroring = True
    try:
        body.mirror = d.get("mirror", True)
        for key, label, mm in length_specs():
            setattr(body, key, d.get(key, mm))
        for key, label, lim in position_specs():
            setattr(body, key, d.get(key, 0.0))
        for key, _, dflt, _, _ in SHAPE_SPECS:
            setattr(body, key, d.get(key, dflt))
    finally:
        _mirroring = False


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


SIGNING_SPACE_BOTTOM = 0.12   # world height (m) of the waist; hands below it are resting


def sign_bounds(include_head=False):
    """Front-view box (min_x, max_x, min_z, max_z) around everything that shows the sign:
    every visible hand (solid and faded), all arrows, and optionally the head."""
    bpy.context.view_layer.update()
    xs, zs = [], []

    # A hand resting down by the hip is not part of the sign; only raised hands count.
    def raised(root, side):
        palm = bpy.data.objects.get(f"{root.name}.{side}_palm")
        return palm is not None and (palm.matrix_world @ Vector((0, 0, -0.045))).z > SIGNING_SPACE_BOTTOM

    def add(obj):
        for c in obj.bound_box:
            w = obj.matrix_world @ Vector(c)
            xs.append(w.x)
            zs.append(w.z)

    for root in all_figures():
        sides = [sd for sd in SIDES if raised(root, sd)] or list(SIDES)
        for o in root.children_recursive:
            grp = o.get("asl_part")
            if o.type != 'MESH' or grp is None or o.hide_render:
                continue
            if (grp.startswith("hand") and grp[-1] in sides) or (include_head and grp == "head"):
                add(o)
    for a in all_arrows():
        add(a)
        for h in arrow_heads(a):
            add(h)
    if not xs:
        return None
    return min(xs), max(xs), min(zs), max(zs)


def fit_frame(scene, include_head=False, margin=0.1, min_width=0.28, box=None):
    """Zoom and center the camera so the whole sign is visible above the label strip."""
    if box is None:
        box = sign_bounds(include_head)
    if box is None:
        return False
    s = scene.asl
    x0, x1, z0, z1 = box
    w, h = (x1 - x0), (z1 - z0)
    band = (s.label_size + 0.07) if (s.show_label and s.term.strip() and not s.transparent_bg) else 0.0
    r = scene.render
    aspect = r.resolution_y / r.resolution_x          # height / width of the picture
    # frame_zoom is the picture width when wider than tall, else its height
    pic_w_for_w = w * (1 + 2 * margin)
    pic_h_needed = h * (1 + 2 * margin) / max(0.4, 1 - band)
    if aspect <= 1:
        zoom = max(pic_w_for_w, pic_h_needed / aspect, min_width)
        half_h = zoom * aspect / 2
    else:
        zoom = max(pic_w_for_w * aspect, pic_h_needed, min_width)
        half_h = zoom / 2
    s.frame_zoom = zoom
    s.frame_x = (x0 + x1) / 2
    s.frame_z = (z0 + z1) / 2 - half_h * band     # centers the sign in the space above the strip
    return True


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
    set_arrow_points(cu, points)
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


def set_arrow_points(curve, points):
    """Replace a curve's shape. points: list of dicts {co, hl, hr} or {co, hl_type, hr_type}."""
    curve.splines.clear()
    sp = curve.splines.new('BEZIER')
    sp.bezier_points.add(len(points) - 1)
    for bp, p in zip(sp.bezier_points, points):
        bp.co = p["co"]
        bp.handle_left_type = p.get("hl_type", 'AUTO')
        bp.handle_right_type = p.get("hr_type", 'AUTO')
        if "hl" in p:
            bp.handle_left_type = bp.handle_right_type = 'FREE'
            bp.handle_left = p["hl"]
            bp.handle_right = p["hr"]


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


# --- Arrows attached ("tethered") to a body part -----------------------------
#
# An attached arrow is rebuilt from its settings every time the figure moves:
#   body part frame  ->  + Position offset  ->  turned to the chosen axis
#   ->  Turn / Tilt  ->  arrow shape (straight along the axis, or an arc around it)

TETHER_PARTS = [
    ("wrist", "Wrist", "Around/along the forearm, at the wrist"),
    ("palm", "Palm", "Center of the palm"),
    ("forearm", "Forearm", "Middle of the forearm"),
    ("elbow", "Elbow", ""),
    ("shoulder", "Shoulder", ""),
    ("thumb_tip", "Thumb tip", ""),
    ("index_tip", "Index fingertip", ""),
    ("middle_tip", "Middle fingertip", ""),
    ("ring_tip", "Ring fingertip", ""),
    ("pinky_tip", "Pinky fingertip", ""),
    ("head", "Forehead", ""),
    ("chin", "Chin", ""),
]

TETHER_AXES = {"BONE": (0, 0, -1), "BEND": (0, -1, 0), "SIDE": (1, 0, 0)}


def part_matrix(root, side, part, joints=None):
    """World position + orientation of a body part (local -Z runs along the bone)."""
    if joints is None:
        joints = {o["asl_joint"]: o for o in root.children_recursive if "asl_joint" in o}
    body = bpy.context.scene.asl_body
    if part.endswith("_tip"):
        finger = part[:-4]
        key, off = f"{side}_{finger}_tip", (0, 0, -getattr(body, f"{side}_{finger}_tip") / 1000.0)
    else:
        key, off = {
            "wrist": (f"{side}_elbow", (0, 0, -getattr(body, "forearm", FOREARM_LEN * 1000) / 1000.0)),
            "palm": (f"{side}_wrist", (0, 0, -0.05)),
            "forearm": (f"{side}_elbow", (0, 0, -getattr(body, "forearm", FOREARM_LEN * 1000) / 2000.0)),
            "elbow": (f"{side}_elbow", (0, 0, 0)),
            "shoulder": (f"{side}_shoulder", (0, 0, 0)),
            "head": ("head", (0, -0.1, 0.13)),
            "chin": ("head", (0, -0.088, 0.03)),
        }[part]
    j = joints.get(key)
    if j is None:
        return Matrix.Identity(4)
    mw = j.matrix_world
    m = mw.normalized()                   # orientation without the joint's scale...
    m.translation = mw @ Vector(off)      # ...but the position follows scaled body parts
    return m


def _axis_basis(d):
    """Rotation whose Z axis points along d."""
    d = Vector(d).normalized()
    helper = Vector((0, 1, 0)) if abs(d.y) < 0.9 else Vector((0, 0, 1))
    u = helper.cross(d).normalized()
    v = d.cross(u)
    return Matrix((u, v, d)).transposed().to_4x4()


def tether_points(t):
    """Arrow shape in its own space: the axis is +Z."""
    if t.mode == 'ALONG':
        pts = [{"co": (0, 0, 0), "hl_type": 'VECTOR', "hr_type": 'VECTOR'},
               {"co": (0, 0, t.length), "hl_type": 'VECTOR', "hr_type": 'VECTOR'}]
        return list(reversed(pts)) if t.reverse else pts
    sweep = math.radians(t.sweep)
    n = max(2, math.ceil(t.sweep / 90))
    step = sweep / n
    k = 4 / 3 * math.tan(step / 4) * t.radius        # makes Bezier arcs round
    sign = -1 if t.reverse else 1
    pts = []
    for i in range(n + 1):
        a = sign * i * step
        co = Vector((t.radius * math.cos(a), t.radius * math.sin(a), 0))
        tan = sign * Vector((-math.sin(a), math.cos(a), 0))
        pts.append({"co": co, "hl": co - k * tan, "hr": co + k * tan})
    return pts


def update_tethers(root=None, arrows=None):
    """Re-place attached arrows (all of them, those on one figure, or a given list)."""
    if arrows is None:
        arrows = [a for a in all_arrows() if a.asl_tether.enabled and (root is None or a.asl_tether.figure == root)]
    if not arrows:
        return
    bpy.context.view_layer.update()      # make sure joint positions are current
    cache = {}
    for a in arrows:
        t = a.asl_tether
        fig = t.figure
        if fig is None or not fig.asl_fig.is_figure:
            continue
        if fig.name not in cache:
            cache[fig.name] = {o["asl_joint"]: o for o in fig.children_recursive if "asl_joint" in o}
        m = (part_matrix(fig, t.side, t.part, cache[fig.name])
             @ Matrix.Translation(Vector(t.offset))
             @ _axis_basis(TETHER_AXES[t.axis])
             @ Matrix.Rotation(math.radians(t.spin), 4, 'Z')
             @ Matrix.Rotation(math.radians(t.tilt), 4, 'X'))
        if t.on_top:
            m.translation.y = ARROW_DEPTH
        set_arrow_points(a.data, tether_points(t))
        a.matrix_world = m


def _update_tether(self, context):
    update_tethers(arrows=[self.id_data])


def tether_to_dict(t, fig_index):
    return {"figure": fig_index.get(t.figure.name if t.figure else "", 0),
            "side": t.side, "part": t.part, "mode": t.mode, "axis": t.axis,
            "offset": [round(x, 4) for x in t.offset], "length": t.length, "radius": t.radius,
            "sweep": t.sweep, "spin": t.spin, "tilt": t.tilt, "reverse": t.reverse, "on_top": t.on_top}


def tether_from_dict(arrow, d, roots):
    t = arrow.asl_tether
    t.enabled = True
    for k in ("side", "part", "mode", "axis", "offset", "length", "radius", "sweep", "spin", "tilt",
              "reverse", "on_top"):
        if k in d:
            setattr(t, k, d[k])
    idx = d.get("figure", 0)
    t.figure = roots[idx] if 0 <= idx < len(roots) else (roots[0] if roots else None)
    update_tethers(arrows=[arrow])


class ASLTether(PropertyGroup):
    enabled: BoolProperty(default=False)
    figure: PointerProperty(name="Figure", type=bpy.types.Object,
                            poll=lambda self, obj: obj.asl_fig.is_figure, update=_update_tether)
    side: EnumProperty(name="Side", items=[("R", "Right", "Signer's right"), ("L", "Left", "Signer's left")],
                       update=_update_tether)
    part: EnumProperty(name="Body part", items=TETHER_PARTS, update=_update_tether)
    mode: EnumProperty(name="Type", items=[
        ("ALONG", "Along an axis", "A straight arrow pointing along the body part"),
        ("AROUND", "Around an axis", "A circular arrow going around the body part")], update=_update_tether)
    axis: EnumProperty(name="Axis", items=[
        ("BONE", "Along the bone", "The direction the arm, hand or finger points"),
        ("BEND", "Front / palm side", "The direction the joint bends toward (the palm side for hands)"),
        ("SIDE", "Sideways", "Across the body part")], update=_update_tether)
    offset: FloatVectorProperty(name="Position", size=3, subtype='TRANSLATION', unit='LENGTH',
                                step=0.1, precision=3, update=_update_tether,
                                description="Move the arrow relative to the body part (in the part's own directions)")
    length: FloatProperty(name="Length", default=0.08, min=0.01, max=0.6, unit='LENGTH', step=0.5,
                          update=_update_tether)
    radius: FloatProperty(name="Radius", default=0.045, min=0.01, max=0.3, unit='LENGTH', step=0.2,
                          update=_update_tether)
    sweep: FloatProperty(name="How far around (°)", default=270, min=30, max=350, update=_update_tether)
    spin: FloatProperty(name="Turn around axis (°)", default=0, min=-180, max=180, update=_update_tether)
    tilt: FloatProperty(name="Tilt (°)", default=0, min=-90, max=90, update=_update_tether)
    reverse: BoolProperty(name="Reverse direction", default=False, update=_update_tether)
    on_top: BoolProperty(name="Always draw on top", default=True, update=_update_tether,
                         description="On: the arrow is never hidden by the body. Off: it wraps around in 3D")


# ---------------------------------------------------------------------------
# 10. Sign "recipes" (JSON) - save, load, export, batch rebuild
# ---------------------------------------------------------------------------

def sign_to_dict(scene):
    s = scene.asl
    figs = []
    roots = sorted(all_figures(), key=lambda o: (not o.asl_fig.is_ghost, o.name))
    fig_index = {r.name: i for i, r in enumerate(roots)}
    for root in roots:
        f = root.asl_fig
        figs.append({
            "role": root.get("asl_role", ""),
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
            "auto": bool(a.get("asl_auto", False)),
            "tether": tether_to_dict(a.asl_tether, fig_index) if a.asl_tether.enabled else None,
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
        "proportions": body_to_dict(scene.asl_body),
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
    body_from_dict(scene.asl_body, data.get("proportions", {}))

    main = None
    roots = []
    for fd in data.get("figures", []):
        root = build_figure(scene, ghost=fd.get("ghost", False))
        roots.append(root)
        if fd.get("role"):
            root["asl_role"] = fd["role"]
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
        if ad.get("auto"):
            a["asl_auto"] = True
        if ad.get("tether"):
            tether_from_dict(a, ad["tether"], roots)
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


def _update_start_opacity(self, context):
    start = role_figure("start")
    if start:
        start.asl_fig.opacity = self.start_opacity


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
    ghost_opacity: FloatProperty(name="Middle position opacity", default=0.3, min=0.05, max=1.0)
    start_opacity: FloatProperty(name="Start Position Opacity", default=0.3, min=0.05, max=1.0,
                                 update=_update_start_opacity,
                                 description="How faded the start position looks")
    auto_arrow: BoolProperty(name="Draw movement arrow automatically", default=True,
                             description="Set End Position adds an arrow from the start to the end hand position")
    tether_side: EnumProperty(name="Side", items=[("R", "Right", "Signer's right"), ("L", "Left", "Signer's left")])
    tether_part: EnumProperty(name="Body part", items=TETHER_PARTS)
    tether_mode: EnumProperty(name="Type", items=[
        ("ALONG", "Along", "Straight arrow pointing along the body part"),
        ("AROUND", "Around", "Circular arrow around the body part")])
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

class ASL_OT_upgrade_figures(Operator):
    bl_idname = "asl.upgrade_figures"
    bl_label = "Update Figures"
    bl_description = "Rebuild figures made with an older version of the add-on (poses are kept)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = upgrade_old_figures(context.scene)
        self.report({'INFO'}, f"Updated {n} figure(s)")
        return {'FINISHED'}


class ASL_OT_new_scene(Operator):
    bl_idname = "asl.new_scene"
    bl_label = "New Card Scene"
    bl_description = "Set up camera, lights, label and a signer figure"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        upgrade_old_figures(context.scene)
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


def role_figure(role):
    for o in all_figures():
        if o.get("asl_role") == role:
            return o
    return None


def solid_figure(scene):
    """The figure the person is posing (the solid one, which becomes the end position)."""
    act = scene.asl.active_figure
    if act and act.asl_fig.is_figure and not act.asl_fig.is_ghost:
        return act
    for o in all_figures():
        if not o.asl_fig.is_ghost:
            return o
    return None


def copy_pose(src, dst, arms=("R",)):
    """Copy a pose onto a ghost, showing only the given arms (ghosts hide head and body)."""
    global _suspend_updates
    g, f = dst.asl_fig, src.asl_fig
    _suspend_updates = True
    try:
        for k, v in figure_pose_dict(f).items():
            setattr(g, k, v)
        for k in ("head", "torso", "arm_R", "arm_L", "hand_R", "hand_L"):
            keep = k[-1] in arms and k.startswith(("arm", "hand"))
            setattr(g, f"show_{k}", keep and getattr(f, f"show_{k}"))
    finally:
        _suspend_updates = False
    apply_figure(dst)


def sides_that_moved(a, b, tolerance=1.0):
    """Which arms/hands differ between two figures' poses."""
    moved = []
    for side in SIDES:
        keys = [f"{side}_{p[0]}" for p in ARM_PARAMS] + hand_param_keys(side)
        if any(abs(getattr(a, k) - getattr(b, k)) > tolerance for k in keys):
            moved.append(side)
    return moved


def hand_point(root, side):
    return part_matrix(root, side, "palm").translation.copy()


def add_motion_arrow(scene, a, b):
    """Curved arrow from point a to point b (seen from the front), bowed upward."""
    d = Vector((b.x - a.x, b.z - a.z))
    dist = d.length
    n = Vector((-d.y, d.x)).normalized()
    if n.y < 0:
        n = -n                                   # bow upward / outward
    mid = (a + b) / 2
    lift = 0.04                                   # keep the arrow off the hand
    bulge = min(0.12, 0.25 * dist)

    def pt(x, z, along):
        return (x - mid.x + n.x * along, 0.0, z - mid.z + n.y * along)

    s0 = a.lerp(b, 0.12)
    s1 = a.lerp(b, 0.88)
    pts = [{"co": pt(s0.x, s0.z, lift)}, {"co": pt(mid.x, mid.z, lift + bulge)}, {"co": pt(s1.x, s1.z, lift)}]
    arrow = create_arrow(scene, pts, location=(mid.x, ARROW_DEPTH, mid.z), kind="arc")
    arrow["asl_auto"] = True
    return arrow


class ASL_OT_set_start(Operator):
    bl_idname = "asl.set_start"
    bl_label = "Set Start Position"
    bl_description = "Save the current pose as the faded start of the movement"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        solid = solid_figure(scene)
        if solid is None:
            self.report({'ERROR'}, "Click New Card Scene first")
            return {'CANCELLED'}
        start = role_figure("start")
        if start is None:
            start = build_figure(scene, ghost=True)
            start["asl_role"] = "start"
        copy_pose(solid, start, ("R", "L") if scene.asl.ghost_both_arms else ("R",))
        start.asl_fig.opacity = scene.asl.start_opacity
        scene.asl.active_figure = solid
        self.report({'INFO'}, "Start saved. Now pose the end position and click Set End Position.")
        return {'FINISHED'}


class ASL_OT_set_end(Operator):
    bl_idname = "asl.set_end"
    bl_label = "Set End Position"
    bl_description = ("Use the current pose as the end of the movement. The start position then shows "
                      "only the arms that moved, and an arrow is drawn between them")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        start = role_figure("start")
        solid = solid_figure(scene)
        if start is None or solid is None:
            self.report({'ERROR'}, "Click Set Start Position first")
            return {'CANCELLED'}
        solid["asl_role"] = "end"
        moved = sides_that_moved(start.asl_fig, solid.asl_fig)
        global _suspend_updates
        _suspend_updates = True
        try:
            for side in SIDES:
                show = side in moved or (not moved and side == "R")
                setattr(start.asl_fig, f"show_arm_{side}", show)
                setattr(start.asl_fig, f"show_hand_{side}", show)
            start.asl_fig.opacity = scene.asl.start_opacity
        finally:
            _suspend_updates = False
        apply_figure(start)

        made = []
        if scene.asl.auto_arrow:
            for a in [a for a in all_arrows() if a.get("asl_auto")]:
                remove_arrow(a)
            context.view_layer.update()
            for side in moved:
                a, b = hand_point(start, side), hand_point(solid, side)
                if Vector((b.x - a.x, b.z - a.z)).length > 0.02:
                    add_motion_arrow(scene, a, b)
                    made.append("right" if side == "R" else "left")
        if not moved:
            self.report({'WARNING'}, "The end pose is the same as the start. Change the pose, then click again.")
        elif scene.asl.auto_arrow and not made:
            self.report({'INFO'}, "End saved. The hand barely moves across the card, so no arrow was drawn. "
                                  "Use an attached Around arrow to show twisting.")
        else:
            self.report({'INFO'}, "End saved" + (f", arrow drawn for the {' and '.join(made)} hand." if made else "."))
        return {'FINISHED'}


class ASL_OT_edit_role(Operator):
    bl_idname = "asl.edit_role"
    bl_label = "Edit Position"
    bl_description = "Choose which position the sliders change"
    bl_options = {'REGISTER', 'UNDO'}
    role: StringProperty()

    def execute(self, context):
        fig = role_figure(self.role) if self.role == "start" else solid_figure(context.scene)
        if fig:
            context.scene.asl.active_figure = fig
        return {'FINISHED'}


class ASL_OT_add_tethered_arrow(Operator):
    bl_idname = "asl.add_tethered_arrow"
    bl_label = "Add Attached Arrow"
    bl_description = "Add an arrow that stays attached to a body part as you pose the figure"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        s = scene.asl
        fig = main_figure(scene)
        if fig is None:
            self.report({'ERROR'}, "Click New Card Scene first")
            return {'CANCELLED'}
        dummy = [{"co": (0, 0, 0), "hl_type": 'VECTOR', "hr_type": 'VECTOR'},
                 {"co": (0, 0, 0.05), "hl_type": 'VECTOR', "hr_type": 'VECTOR'}]
        arrow = create_arrow(scene, dummy, kind="attached")
        t = arrow.asl_tether
        t.enabled = True
        t.side, t.part, t.mode = s.tether_side, s.tether_part, s.tether_mode
        if s.tether_part.endswith("_tip") and s.tether_mode == 'AROUND':
            t.radius = 0.025
        t.figure = fig          # triggers the first placement
        update_tethers(arrows=[arrow])
        for o in context.selected_objects:
            o.select_set(False)
        arrow.select_set(True)
        context.view_layer.objects.active = arrow
        return {'FINISHED'}


class ASL_OT_detach_arrow(Operator):
    bl_idname = "asl.detach_arrow"
    bl_label = "Detach"
    bl_description = "Stop following the body part; the arrow stays where it is and can be edited by hand"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        a = context.active_object
        if a and "asl_arrow" in a:
            a.asl_tether.enabled = False
            a["asl_arrow"] = "custom"
        return {'FINISHED'}


class ASL_OT_add_ghost(Operator):
    bl_idname = "asl.add_ghost"
    bl_label = "Add Middle Position"
    bl_description = ("Leave a faded copy of the current pose, for movements with more than "
                      "one step between start and end")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        src = main_figure(scene)
        if src is None:
            self.report({'ERROR'}, "Click New Card Scene first")
            return {'CANCELLED'}
        ghost = build_figure(scene, ghost=True)
        ghost["asl_role"] = "middle"
        # Ghosts only show the parts that move. A faded copy sitting exactly on
        # top of the solid head/body would just look smudged.
        copy_pose(src, ghost, ("R", "L") if scene.asl.ghost_both_arms else ("R",))
        return {'FINISHED'}


class ASL_OT_flip_arrows(Operator):
    bl_idname = "asl.flip_arrows"
    bl_label = "Flip Direction"
    bl_description = "Reverse the selected arrows so they point the other way"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for o in context.selected_objects:
            if "asl_arrow" in o and o.type == 'CURVE':
                if o.asl_tether.enabled:
                    o.asl_tether.reverse = not o.asl_tether.reverse
                else:
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


class ASL_OT_fit_frame(Operator):
    bl_idname = "asl.fit_frame"
    bl_label = "Fit Sign in Frame"
    bl_description = ("Zoom and center so every hand (including faded start positions) and every arrow "
                      "is fully visible above the label")
    bl_options = {'REGISTER', 'UNDO'}
    include_head: BoolProperty(name="Include face", default=False)

    def execute(self, context):
        if not fit_frame(context.scene, self.include_head):
            self.report({'WARNING'}, "Nothing to frame yet")
            return {'CANCELLED'}
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
# 15. Live tracking: webcam -> web app (MediaPipe) -> this add-on -> figure
#
# The web app's Record tab tracks the signer with the webcam and streams the
# landmark positions here over a local WebSocket (127.0.0.1 only). This section
#   * runs a tiny WebSocket server on a background thread (standard library only),
#   * turns the landmarks into exact joint rotations for the figure (retargeting),
#   * applies them on Blender's main thread through a fast timer, and
#   * replies with an "ack" so the web app can show the delay.
#
# Coordinates: MediaPipe uses x right, y down, z away from the camera, with the
# camera image NOT mirrored. Blender uses x right (as seen by the card camera),
# z up and -y toward the camera, so a MediaPipe vector (x, y, z) becomes
# Blender (x, z, -y). The signer's right side is -X, exactly like the figure.
# ---------------------------------------------------------------------------

LIVE_PROTOCOL = 1
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _unmask(data, mask):
    n = len(data)
    if n == 0:
        return data
    m = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "little") ^ int.from_bytes(m, "little")).to_bytes(n, "little")


class LiveLink:
    """Minimal WebSocket server for one browser tab at a time."""

    def __init__(self):
        self.port = None
        self._srv = None
        self._client = None
        self._running = False
        self._send_lock = threading.Lock()
        self.latest = None                  # newest "frame" message; older ones are dropped
        self.commands = collections.deque()  # every other message, in order
        self.connected = False
        self.frames_in = 0

    def start(self, port):
        self.stop()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(4)
        srv.settimeout(0.3)
        self._srv, self.port, self._running = srv, port, True
        threading.Thread(target=self._accept_loop, daemon=True, name="asl-live-accept").start()

    def stop(self):
        self._running = False
        for s in (self._client, self._srv):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._client = self._srv = None
        self.connected = False

    # --- receiving -------------------------------------------------------
    def _accept_loop(self):
        while self._running:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True, name="asl-live-client").start()

    def _serve(self, conn):
        try:
            conn.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk or len(data) > 65536:
                    return
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            headers = {}
            for line in lines[1:]:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
            key = headers.get("sec-websocket-key")
            if not key or "websocket" not in headers.get("upgrade", "").lower():
                body = b"ASL Study Tool live link is running. Open the Record tab in the web app."
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nAccess-Control-Allow-Origin: *\r\n"
                             b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
                return
            accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # no batching delay
            conn.settimeout(2.0)
            old, self._client = self._client, conn
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            self.connected = True
            self.commands.append({"type": "_connected"})
            self._read_loop(conn, rest)
        except (OSError, ConnectionError, ValueError):
            pass
        except Exception:
            traceback.print_exc()
        finally:
            if self._client is conn:
                self._client = None
                self.connected = False
                self.commands.append({"type": "_disconnected"})
            try:
                conn.close()
            except OSError:
                pass

    def _read_loop(self, conn, buf):
        buf = bytearray(buf)

        def need(n):
            while len(buf) < n:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    if not self._running or self._client is not conn:
                        raise ConnectionError("closed")
                    continue
                if not chunk:
                    raise ConnectionError("closed")
                buf.extend(chunk)
            out = bytes(buf[:n])
            del buf[:n]
            return out

        message, msg_op = bytearray(), None
        while self._running and self._client is conn:
            b1, b2 = need(2)
            fin, op, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", need(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", need(8))[0]
            if n > 16 * 1024 * 1024:
                raise ConnectionError("message too large")
            mask = need(4) if b2 & 0x80 else None
            payload = need(n)
            if mask:
                payload = _unmask(payload, mask)
            if op == 8:                                  # close
                self._send_frame(conn, 8, payload[:2])
                return
            if op == 9:                                  # ping
                self._send_frame(conn, 10, payload)
                continue
            if op == 10:
                continue
            if op in (1, 2):
                message, msg_op = bytearray(payload), op
            elif op == 0:
                message.extend(payload)
            if fin and msg_op == 1:
                self._on_text(bytes(message).decode("utf-8", "replace"))

    def _on_text(self, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "frame":
            self.latest = msg
            self.frames_in += 1
        else:
            self.commands.append(msg)

    # --- sending ---------------------------------------------------------
    def _send_frame(self, conn, op, payload):
        n = len(payload)
        if n < 126:
            hdr = struct.pack(">BB", 0x80 | op, n)
        elif n < 65536:
            hdr = struct.pack(">BBH", 0x80 | op, 126, n)
        else:
            hdr = struct.pack(">BBQ", 0x80 | op, 127, n)
        with self._send_lock:
            conn.sendall(hdr)
            conn.sendall(payload)

    def send(self, obj):
        conn = self._client
        if conn is None:
            return False
        try:
            self._send_frame(conn, 1, json.dumps(obj, separators=(",", ":")).encode())
            return True
        except OSError:
            return False

    def send_bytes(self, data):
        conn = self._client
        if conn is None:
            return False
        try:
            self._send_frame(conn, 2, data)
            return True
        except OSError:
            return False


# --- Retargeting: landmarks -> joint rotations ------------------------------

POSE_IDX = {"R": {"sh": 12, "el": 14, "wr": 16, "hip": 24}, "L": {"sh": 11, "el": 13, "wr": 15, "hip": 23}}
HAND_CHAINS = {"index": (5, 6, 7, 8), "middle": (9, 10, 11, 12), "ring": (13, 14, 15, 16), "pinky": (17, 18, 19, 20)}
R90 = math.radians(90)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _rot(axis, a):
    return Matrix.Rotation(a, 3, axis)


def _wrap_near(a, ref):
    """The angle equal to a (mod 360) that is closest to ref."""
    return a - 2 * math.pi * round((a - ref) / (2 * math.pi))


def _pick_angle(a, limits, ref=0.0):
    """Of a, a +/- 360 degrees, the value inside the limits that is closest to ref
    (angles that differ by a full turn describe the same rotation)."""
    lo, hi = limits
    cands = [a + k * 2 * math.pi for k in (-2, -1, 0, 1, 2)]
    inside = [c for c in cands if lo <= c <= hi]
    if inside:
        return min(inside, key=lambda c: abs(c - ref))
    return min((_clamp(c, lo, hi) for c in cands), key=lambda c: min(abs(c - x) for x in cands))


def _frame(x, y, z):
    return Matrix((x, y, z)).transposed()     # columns are the axes

# --- Depth: where the hands really are relative to the face and body -----------------
#
# MediaPipe's 3D body points are good at directions but poor at depth: a hand touching the nose
# can come out 20 cm in front of the face, which pulls the figure's arms forward and down. So
# instead of copying arm directions, the hands are placed by position:
#   1. Pinhole camera: how far away the face, body and each hand are follows from their size in
#      the picture (eye corners ~89 mm apart; MediaPipe's hand and body points are in meters).
#   2. A hand that overlaps the face in the picture and is about as far away as the face is
#      touching it, so its distance is snapped onto the face.
#   3. Each tracked point is carried onto the figure: near the face through "anchors" (nose tip,
#      chin, cheeks, temples... found on the figure's own head, so it works with any head shape),
#      elsewhere through the shoulders, scaled to the figure's size.
#   4. The wrist goes where the figure's own hand best covers those points (a touching fingertip
#      counts most), then a two-bone solve finds shoulder and elbow, bending the elbow the way
#      the person's elbow points. Hands are kept out of the head and chest.

IOD_M = 0.089          # distance between the outer eye corners of an average adult (m)

# MediaPipe face point -> where it is on the figure's head (head space, default figure):
#   ("F", x, z): first surface hit by a ray from the front at (x, z)
#   ("S", side, y, z): first surface hit by a ray from the side (side -1 = signer's right)
FACE_ANCHORS = {
    1: ("F", 0.0, 0.105), 4: ("F", 0.0, 0.116), 6: ("F", 0.0, 0.135), 168: ("F", 0.0, 0.148),
    151: ("F", 0.0, 0.185), 10: ("F", 0.0, 0.212), 152: ("F", 0.0, 0.014), 199: ("F", 0.0, 0.03),
    17: ("F", 0.0, 0.049), 14: ("F", 0.0, 0.061), 13: ("F", 0.0, 0.067), 0: ("F", 0.0, 0.074),
    61: ("F", -0.017, 0.064), 291: ("F", 0.017, 0.064),
    33: ("F", -0.043, 0.128), 133: ("F", -0.019, 0.128), 263: ("F", 0.043, 0.128), 362: ("F", 0.019, 0.128),
    159: ("F", -0.031, 0.141), 145: ("F", -0.031, 0.116), 386: ("F", 0.031, 0.141), 374: ("F", 0.031, 0.116),
    70: ("F", -0.05, 0.157), 105: ("F", -0.033, 0.164), 107: ("F", -0.016, 0.160),
    300: ("F", 0.05, 0.157), 334: ("F", 0.033, 0.164), 336: ("F", 0.016, 0.160),
    98: ("F", -0.012, 0.094), 327: ("F", 0.012, 0.094),
    50: ("F", -0.049, 0.085), 280: ("F", 0.049, 0.085), 58: ("F", -0.042, 0.05), 288: ("F", 0.042, 0.05),
    234: ("S", -1, -0.035, 0.11), 454: ("S", 1, -0.035, 0.11),
    127: ("S", -1, -0.05, 0.145), 356: ("S", 1, -0.05, 0.145),
    172: ("S", -1, -0.02, 0.04), 397: ("S", 1, -0.02, 0.04),
}
FACE_PAIRS = [(98, 327), (61, 291), (33, 263), (133, 362), (159, 386), (145, 374), (70, 300), (105, 334), (107, 336),
              (50, 280), (58, 288), (234, 454), (127, 356), (172, 397)]
FACE_MIRROR = {a: b for a, b in FACE_PAIRS}
FACE_MIRROR.update({b: a for a, b in FACE_PAIRS})


def _delaunay(pts):
    """Bowyer-Watson triangulation of 2D points {key: (x, y)}. Returns [(k1, k2, k3)]."""
    keys = list(pts)
    P = {k: pts[k] for k in keys}
    big = 10.0
    P["_a"], P["_b"], P["_c"] = (-big, -big), (big, -big), (0.0, big)
    tris = [("_a", "_b", "_c")]

    def circ(t):
        (ax, ay), (bx, by), (cx, cy) = P[t[0]], P[t[1]], P[t[2]]
        d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) < 1e-12:
            return (0.0, 0.0, float("inf"))
        ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
        uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
        return (ux, uy, (ax - ux) ** 2 + (ay - uy) ** 2)

    for k in keys:
        x, y = P[k]
        bad = []
        for t in tris:
            ux, uy, r2 = circ(t)
            if (x - ux) ** 2 + (y - uy) ** 2 < r2:
                bad.append(t)
        edges = {}
        for t in bad:
            for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                key = frozenset(e)
                edges[key] = None if key in edges else e
        tris = [t for t in tris if t not in bad]
        for e in edges.values():
            if e is not None:
                tris.append((e[0], e[1], k))
    return [t for t in tris if not any(str(v).startswith("_") for v in t)]


def _face_layout():
    out = {}
    for k, sp in FACE_ANCHORS.items():
        out[k] = (sp[1], sp[2]) if sp[0] == "F" else (sp[1] * 0.075, sp[3])
    return out


FACE_TRIS = _delaunay(_face_layout())
# eyebrow nodes (inner, middle, outer) and eye corners (outer, inner), by the person's side
BROW_POINTS = {"R": ((107, 105, 70), (33, 133)), "L": ((336, 334, 300), (263, 362))}


class EuroFilter:
    """One Euro filter for a vector (same idea as js/record/oneeuro.js)."""

    def __init__(self, min_cutoff=1.5, beta=0.5, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = None
        self.t = None
        self.speed = 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, v, t):
        v = Vector(v)
        if self.x is None or t is None or self.t is None or t - self.t > 0.5:
            self.x, self.t, self.speed = v.copy(), t, 0.0
            return v
        dt = max(1e-3, t - self.t)
        self.t = t
        raw = (v - self.x).length / dt
        self.speed += self._alpha(self.d_cutoff, dt) * (raw - self.speed)
        a = self._alpha(self.min_cutoff + self.beta * self.speed, dt)
        self.x = self.x + (v - self.x) * a
        return self.x.copy()


def fit_camera_translation(Q, uv, cam, weights=None):
    """Q: 3D points in camera axes (x right, y down, z away; meters, any origin).
    uv: where they are in the picture (pixels). Returns t such that Q + t projects onto uv
    (linear least squares), or None."""
    f, cx, cy = cam
    a11 = a13 = a23 = a33 = b1 = b2 = b3 = 0.0
    n = 0
    for i, (q, p) in enumerate(zip(Q, uv)):
        if q is None or p is None:
            continue
        w = weights[i] if weights else 1.0
        du, dv = p[0] - cx, p[1] - cy
        r1 = du * q[2] - f * q[0]          # row (f, 0, -du) . t = r1
        r2 = dv * q[2] - f * q[1]          # row (0, f, -dv) . t = r2
        a11 += w * f * f
        a13 -= w * f * du
        a23 -= w * f * dv
        a33 += w * (du * du + dv * dv)
        b1 += w * f * r1
        b2 += w * f * r2
        b3 -= w * (du * r1 + dv * r2)
        n += 1
    if n < 3:
        return None
    A = Matrix(((a11, 0.0, a13), (0.0, a11, a23), (a13, a23, a33)))
    try:
        t = A.inverted() @ Vector((b1, b2, b3))
    except ValueError:
        return None
    return t if t.z > 0.05 else None


def _back_project(uv, Z, cam):
    f, cx, cy = cam
    return Vector(((uv[0] - cx) * Z / f, (uv[1] - cy) * Z / f, Z))


class ModelSkeleton:
    """The figure's joints as plain numbers, so the solver can do forward kinematics (where each
    joint ends up for given rotations) without asking Blender to update the scene. Also finds the
    face anchors on the figure's head. Call refresh() after the body shape changes."""

    def __init__(self, root):
        self.root = root
        self.refresh()

    def refresh(self):
        from mathutils.bvhtree import BVHTree
        root = self.root
        objs = {o["asl_joint"]: o for o in root.children_recursive if "asl_joint" in o}
        self.objs = objs
        self.parent = {}
        for k, o in objs.items():
            p = o.parent
            self.parent[k] = p["asl_joint"] if (p is not None and "asl_joint" in p) else None
        order, seen = [], set()

        def visit(k):
            if k in seen:
                return
            if self.parent[k] is not None:
                visit(self.parent[k])
            seen.add(k)
            order.append(k)

        for k in objs:
            visit(k)
        self.order = order
        self.mpi = {k: o.matrix_parent_inverse.copy() for k, o in objs.items()}
        self.loc = {k: Matrix.Translation(o.location) for k, o in objs.items()}
        self.scl = {k: Matrix.Diagonal(o.scale).to_4x4() for k, o in objs.items()}
        self.mode = {k: o.rotation_mode for k, o in objs.items()}
        self.pose = {k: tuple(o.rotation_euler) for k, o in objs.items()}
        self.root_m = root.matrix_world.copy()
        segs = {o["asl_seg"]: o for o in root.children_recursive if "asl_seg" in o}
        self.tip_len = {}
        for side in SIDES:
            for f in ("thumb",) + FINGERS:
                o = segs.get(f"{side}_{f}_tip")
                self.tip_len[f"{side}_{f}"] = (o["asl_len"] * o.scale.z) if o else 0.02
        self.hand_keys = {}
        for side in SIDES:
            wk = f"{side}_wrist"
            keys = []
            for k in order:
                p = self.parent[k]
                while p is not None and p != wk:
                    p = self.parent[p]
                if p == wk:
                    keys.append(k)
            self.hand_keys[side] = keys
        rest = self.fk({k: (0.0, 0.0, 0.0) for k in objs}, [f"{s}_wrist" for s in SIDES])
        self.L1, self.L2 = {}, {}
        for side in SIDES:
            S = rest[f"{side}_shoulder"].translation
            E = rest[f"{side}_elbow"].translation
            W = rest[f"{side}_wrist"].translation
            self.L1[side], self.L2[side] = (E - S).length, (W - E).length
        # the head's surface, in head space, for anchors, touching and keeping hands out of it
        self.bvh, self.anchors, self.head_center, self.head_radius = None, {}, Vector((0, 0, 0.11)), 0.12
        head = objs.get("head")
        sig = []
        if head is not None:
            verts, polys = [], []
            for o in head.children:
                if o.type != 'MESH' or "asl_joint" in o:
                    continue
                M = o.matrix_parent_inverse.copy()
                if "asl_cheek" not in o and "asl_eye" not in o:
                    M = M @ o.matrix_basis      # puffed cheeks and blinks are expressions, not shape
                base = len(verts)
                verts += [M @ v.co for v in o.data.vertices]
                polys += [[base + i for i in p.vertices] for p in o.data.polygons]
            if polys:
                self.bvh = BVHTree.FromPolygons(verts, polys)
                lo = Vector([min(v[i] for v in verts) for i in range(3)])
                hi = Vector([max(v[i] for v in verts) for i in range(3)])
                self.head_center = (lo + hi) / 2
                self.head_radius = (hi - lo).length / 2
                sig.append(round(sum(v.x * v.x + 2 * v.y * v.y + 3 * v.z * v.z + v.y for v in verts), 6))
            for k, spec in FACE_ANCHORS.items():
                if spec[0] == "F":
                    origin, d = Vector((spec[1], -0.5, spec[2])), Vector((0, 1, 0))
                else:
                    origin, d = Vector((0.5 * spec[1], spec[2], spec[3])), Vector((-spec[1], 0, 0))
                hit = self.bvh.ray_cast(origin, d, 1.0) if self.bvh else (None,)
                if hit[0] is not None:
                    self.anchors[k] = hit[0].copy()
        # chest box in torso space (follows the Body Shape sliders and sculpting)
        self.chest = None
        torso = objs.get("torso")
        if torso is not None:
            for o in torso.children:
                if o.type == 'MESH' and o.name.endswith(".chest"):
                    M = o.matrix_parent_inverse @ o.matrix_basis
                    pts = [M @ Vector(c) for c in o.bound_box]
                    lo = Vector([min(p[i] for p in pts) for i in range(3)])
                    hi = Vector([max(p[i] for p in pts) for i in range(3)])
                    self.chest = (lo, hi)
        for k in order:
            sig.append(tuple(round(x, 5) for row in (self.mpi[k] @ self.loc[k] @ self.scl[k]) for x in row))
        sig.append(tuple(round(v, 5) for v in self.tip_len.values()))
        self.sig = hashlib.md5(repr(sig).encode()).hexdigest()[:12]
        self.refreshed = time.perf_counter()

    def local(self, k, rot):
        e = Euler(rot, self.mode[k]).to_matrix().to_4x4()
        return self.mpi[k] @ self.loc[k] @ e @ self.scl[k]

    def fk(self, rots, keys=None):
        """World matrices of the given joints (and their parents) for these rotations.
        Joints without a rotation in `rots` keep their current pose."""
        W = {}

        def get(k):
            m = W.get(k)
            if m is None:
                p = self.parent[k]
                base = get(p) if p is not None else self.root_m
                m = base @ self.local(k, rots.get(k, self.pose[k]))
                W[k] = m
            return m

        for k in (keys if keys is not None else self.order):
            if k in self.parent:
                get(k)
        return W

    def hand_points(self, side, rots):
        """The 21 MediaPipe hand points on the figure's hand, in the wrist joint's own frame."""
        wk = f"{side}_wrist"
        rel = {wk: self.scl[wk]}
        for k in self.hand_keys[side]:
            rel[k] = rel[self.parent[k]] @ self.local(k, rots.get(k, self.pose[k]))
        P = [Vector((0, 0, 0))] * 21

        def at(k, extra=0.0):
            m = rel.get(f"{side}_{k}")
            return (m @ Vector((0, 0, -extra))) if m is not None else Vector((0, 0, 0))

        P[1], P[2], P[3] = at("thumb_base"), at("thumb_mid"), at("thumb_tip")
        P[4] = at("thumb_tip", self.tip_len[f"{side}_thumb"])
        for fn, (a, b, c, d) in HAND_CHAINS.items():
            P[a], P[b], P[c] = at(f"{fn}_base"), at(f"{fn}_mid"), at(f"{fn}_tip")
            P[d] = at(f"{fn}_tip", self.tip_len[f"{side}_{fn}"])
        return P


class DepthPlacer:
    """Steps 1-4 above for one frame. Used by Retargeter.solve when the browser sends picture
    coordinates and a figure skeleton is known."""

    FACE_NEAR, FACE_FAR = 0.15, 0.27     # hand points this close to the face use the face anchors
    TOUCH_FULL, TOUCH_FADE = 0.025, 0.035
    SKIN = 0.008                         # finger joint centers sit this far from the skin
    TIP_BONUS = {4: 0.008, 8: 0.008, 12: 0.008, 16: 0.008, 20: 0.008, 3: 0.003, 7: 0.003, 11: 0.003,
                 15: 0.003, 19: 0.003}

    def __init__(self, rt):
        self.rt = rt
        self.reset()

    def reset(self):
        self.t_filter = {s: EuroFilter(2.0, 1.0) for s in SIDES}
        self.prev_pole = {}
        self.info = {}
        self.hand_k = 1.0          # learned correction of MediaPipe's hand size

    # --- 1. the person in camera space (camera axes, meters) --------------------
    @staticmethod
    def face_cam(face, cam):
        pts = (face or {}).get("pts") or {}
        a, b = pts.get("33"), pts.get("263")
        if a is None or b is None:
            return None
        f = cam[0]
        iod_px = math.dist(a, b)
        if iod_px < 4:
            return None
        Ze = f * IOD_M / iod_px                       # how far the eyes are
        zr = Ze - (a[2] + b[2]) / 2 * Ze / f          # distance of the face's own z = 0
        out = {}
        for k, p in pts.items():
            Z = zr + p[2] * Ze / f
            out[int(k)] = (_back_project(p, Z, cam), (p[0], p[1]))
        return {"pts": out, "iod_px": iod_px, "Z": Ze}

    def body_cam(self, pose, pose2d, cam, fc):
        rt = self.rt
        idx = [i for i in (0, 2, 5, 7, 8, 11, 12) if i < len(pose) and (len(pose[i]) < 4 or pose[i][3] > 0.5)]
        if 11 not in idx or 12 not in idx:
            return None
        t = fit_camera_translation([pose[i] for i in idx], [pose2d[i] for i in idx], cam)
        if t is None:
            return None
        if fc is not None and 1 in fc["pts"] and 0 in idx:
            t.z = fc["pts"][1][0].z - pose[0][2] + 0.01   # the face knows its distance better
        out = {}
        for side in SIDES:
            ps = rt._side(side)
            si, ei = POSE_IDX[ps]["sh"], POSE_IDX[ps]["el"]
            S = _back_project(pose2d[si], pose[si][2] + t.z, cam)
            out[side + "_sh"] = S
            if ei < len(pose) and (len(pose[ei]) < 4 or pose[ei][3] > 0.3):
                L1 = math.dist(pose[si][:3], pose[ei][:3])
                d = _back_project(pose2d[ei], 1.0, cam)    # ray through the elbow's pixel
                guess = pose[ei][2] + t.z
                a, bq, c = d.dot(d), -2 * d.dot(S), S.dot(S) - L1 * L1
                disc = bq * bq - 4 * a * c
                if disc >= 0:
                    r1 = (-bq - math.sqrt(disc)) / (2 * a)
                    r2 = (-bq + math.sqrt(disc)) / (2 * a)
                    lam = r1 if abs(r1 - guess) < abs(r2 - guess) else r2
                else:
                    lam = d.dot(S) / a
                out[side + "_el"] = d * lam
        return out

    def hand_cam(self, side, h, cam, t_sec):
        Q, uv = h.get("world"), h.get("img")
        if not Q or not uv or len(Q) != 21 or len(uv) != 21:
            return None
        t = fit_camera_translation(Q, uv, cam)
        if t is None:
            return None
        t = t * self.hand_k
        z = self.t_filter[side](Vector((0, 0, t.z)), t_sec).z
        t = t * (z / t.z)                           # filtered distance, same spot in the picture
        return [Vector(q) + t for q in Q], t

    # --- 2. touching the face ---------------------------------------------------------
    def face_gaps(self, P, uv, fc):
        """For each hand point that overlaps the face in the picture: how far it is from the skin
        along the camera's view (> 0 means it would be inside the head), and how surely it is over
        the face (0..1). Points beside the face get None. The skin's distance is interpolated
        across triangles between the face points."""
        fp = fc["pts"]
        A = [(p[1][0], p[1][1], p[0].z) for p in fp.values()]
        tris = []
        for a, b, c in FACE_TRIS:
            if a in fp and b in fp and c in fp:
                tris.append((fp[a], fp[b], fp[c]))
        sig2 = (0.3 * fc["iod_px"]) ** 2
        reach2 = (0.45 * fc["iod_px"]) ** 2
        out = [None] * 21
        for i in range(21):
            u, v = uv[i]
            d2s = [((u - au) ** 2 + (v - av) ** 2, Z) for au, av, Z in A]
            dmin2 = min(d for d, _ in d2s)
            g = math.exp(-dmin2 / reach2)
            if g < 0.05:
                continue
            Zs = None
            for (pa, ua), (pb, ub), (pc, uc) in tris:
                den = (ub[1] - uc[1]) * (ua[0] - uc[0]) + (uc[0] - ub[0]) * (ua[1] - uc[1])
                if abs(den) < 1e-9:
                    continue
                l1 = ((ub[1] - uc[1]) * (u - uc[0]) + (uc[0] - ub[0]) * (v - uc[1])) / den
                l2 = ((uc[1] - ua[1]) * (u - uc[0]) + (ua[0] - uc[0]) * (v - uc[1])) / den
                l3 = 1 - l1 - l2
                if l1 >= -1e-6 and l2 >= -1e-6 and l3 >= -1e-6:
                    Zs = l1 * pa.z + l2 * pb.z + l3 * pc.z
                    g = 1.0
                    break
            if Zs is None:
                num = den = 0.0
                for d2, Z in d2s:
                    w = math.exp(-(d2 - dmin2) / sig2) / (d2 + 1.0)
                    num += w * Z
                    den += w
                Zs = num / den
            out[i] = (P[i].z - Zs + self.SKIN, g)
        return out

    def face_contact(self, P, uv, fc):
        """Change in the hand's distance that puts the hand point nearest the face onto the skin
        (when it is close enough to count as touching), and how strongly each point touches."""
        gaps = self.face_gaps(P, uv, fc)
        cand = [(gp[0] + self.TIP_BONUS.get(i, 0.0), gp[0], gp[1], i)
                for i, gp in enumerate(gaps) if gp is not None and gp[0] < 0.15]
        if not cand:
            return [0.0] * 21, 0.0, 0.0
        # the point that reaches furthest toward the face (fingertips win close calls:
        # they are what touches the face in most signs)
        _, gap, g, _ = max(cand)
        if gap >= 0:
            c = g
        else:
            c = g * math.exp(-(max(0.0, -gap - self.TOUCH_FULL) / self.TOUCH_FADE) ** 2)
        dz = -gap * c
        strengths = [0.0] * 21
        for i, gp in enumerate(gaps):
            if gp is not None:
                after = gp[0] + dz
                strengths[i] = gp[1] * c * math.exp(-(min(0.0, after) / 0.012) ** 2) if after < 0.15 else 0.0
        return strengths, dz, c

    def chest_contact(self, M, Wt_inv, Wtorso):
        """Hand points just in front of the figure's chest are taken as touching it: they move
        onto its surface. Returns the strengths."""
        skel = self.rt.skel
        st = [0.0] * 21
        if skel.chest is None:
            return st
        lo, hi = skel.chest
        front = lo.y - self.SKIN
        best = (0.0, 0.0, -1)
        for i, p in enumerate(M):
            tl = Wt_inv @ p
            if not (lo.x + 0.02 < tl.x < hi.x - 0.02 and lo.z + 0.03 < tl.z < hi.z - 0.04):
                continue
            gap = tl.y - front                     # > 0: inside the chest
            if gap > (hi.y - lo.y) * 0.5:
                continue
            c = 1.0 if gap > -0.03 else math.exp(-((-gap - 0.03) / 0.025) ** 2)
            if c > 0.05 and (best[2] < 0 or gap > best[1]):    # the point reaching furthest in
                best = (c, gap, i)
        c, gap, i = best
        if i < 0:
            return st
        shift = Wtorso.to_3x3() @ Vector((0, -gap * c, 0))
        for k in range(21):
            M[k] = M[k] + shift
        for k, p in enumerate(M):
            tl = Wt_inv @ p
            st[k] = c * math.exp(-((tl.y - front) / 0.012) ** 2)
        return st

    # --- 3 and 4 ---------------------------------------------------------------------
    def place(self, msg, pose, hands_raw, T, hand_frames, rots):
        """Returns {side: (shoulder, elbow, wrist) in world space} for every arm it could place."""
        rt, skel = self.rt, self.rt.skel
        img, pose2d = msg.get("img"), msg.get("pose2d")
        self.info = {}
        if skel is None or not img or not pose2d or not pose or len(pose2d) < 17:
            return {}
        W_, H_ = img
        f = W_ / (2 * math.tan(math.radians(rt.fov) / 2))
        cam = (f, W_ / 2, H_ / 2)
        t_sec = float(msg.get("t", 0.0)) / 1000.0
        face = msg.get("face") or rt.recent_face(t_sec)
        fc = self.face_cam(face, cam) if face else None
        body = self.body_cam(pose, pose2d, cam, fc)
        if body is None:
            return {}
        conv = rt.conv

        # the figure's torso and head for this frame's rotations
        use = rt.tracked
        frots = {k: v for k, v in rots.items() if use(k)}
        Wm = skel.fk(frots, ["head"] + [f"{s}_shoulder" for s in SIDES])
        Rt = Wm["torso"].to_3x3().normalized()
        Wt_inv = Wm["torso"].inverted()
        Wh = Wm["head"]
        Wh_inv = Wh.inverted()

        # body map: shoulders onto shoulders, scaled
        Su = {s: conv(body[s + "_sh"]) for s in SIDES}
        Sm = {s: Wm[f"{s}_shoulder"].translation.copy() for s in SIDES}
        Ou, Om = (Su["R"] + Su["L"]) / 2, (Sm["R"] + Sm["L"]) / 2
        wu = (Su["R"] - Su["L"]).length
        s_b = (Sm["R"] - Sm["L"]).length / wu if wu > 1e-3 else 1.0
        R_b = Rt @ T.transposed()

        # face map: user face anchors onto the figure's, plus an exact fit at every anchor
        fmap = None
        if fc is not None and skel.anchors:
            pairs = []
            for k, (p, _) in fc["pts"].items():
                mk = FACE_MIRROR.get(k, k) if rt.mirror else k
                a = skel.anchors.get(mk)
                if a is not None:
                    pairs.append((conv(p), Wh @ a))
            Rh = rt.head_frame(face)
            if len(pairs) >= 6 and Rh is not None:
                off = (rt.calib or {}).get("head")
                Ruser = Rh @ off if off is not None else Rh
                R_f = Wh.to_3x3().normalized() @ Ruser.transposed()
                Uc = sum((u for u, _ in pairs), Vector()) / len(pairs)
                Mc = sum((m for _, m in pairs), Vector()) / len(pairs)
                su = sum((R_f @ (u - Uc)).length_squared for u, _ in pairs)
                sm = sum((m - Mc).length_squared for _, m in pairs)
                s_f = math.sqrt(sm / su) if su > 1e-9 else 1.0
                res = [(u, m - (Mc + (R_f @ (u - Uc)) * s_f)) for u, m in pairs]
                fmap = (Uc, Mc, R_f, s_f, res)

        def to_model(p):
            pb = Om + (R_b @ (p - Ou)) * s_b
            if fmap is None:
                return pb
            Uc, Mc, R_f, s_f, res = fmap
            d = (p - Uc).length
            wf = _smooth((self.FACE_FAR - d) / (self.FACE_FAR - self.FACE_NEAR))
            if wf <= 0:
                return pb
            pf = Mc + (R_f @ (p - Uc)) * s_f
            d2s = [((p - u).length_squared, r) for u, r in res]
            dmin2 = min(d for d, _ in d2s)
            fade = math.exp(-dmin2 / 0.04 ** 2)
            if fade > 1e-3:
                num, den = Vector(), 0.0
                for d2, r in d2s:
                    w = math.exp(-(d2 - dmin2) / 0.02 ** 2) / (d2 + 1e-8)
                    num += r * w
                    den += w
                pf = pf + num * (fade / den)
            return pf * wf + pb * (1 - wf)

        placed = {}
        for side in SIDES:
            h = hands_raw.get(side)
            if h is None or side not in hand_frames:
                continue
            hc = self.hand_cam(side, h, cam, t_sec)
            if hc is None:
                continue
            P, t = hc
            strengths = [0.0] * 21
            if fc is not None:
                strengths, dz, c = self.face_contact(P, h["img"], fc)
                if abs(dz) > 1e-4:
                    k = (t.z + dz) / t.z
                    P = [q + t * (k - 1) for q in P]
                    t = t * k
                    if c > 0.9 and abs(k - 1) < 0.2:
                        # touching the face tells how far off the hand-size estimate is: learn it
                        self.hand_k = _clamp(self.hand_k * (1 + 0.05 * (k - 1)), 0.8, 1.25)
            Pc = [conv(p) for p in P]
            M = [to_model(p) for p in Pc]

            # points touching the face go exactly onto the figure's skin
            if skel.bvh is not None:
                for i, c in enumerate(strengths):
                    if c > 0.3:
                        loc, nrm, _, _ = skel.bvh.find_nearest(Wh_inv @ M[i])
                        if loc is not None:
                            M[i] = M[i].lerp(Wh @ (loc + nrm * self.SKIN), c)
            if max(strengths) < 0.3:
                cs = self.chest_contact(M, Wt_inv, Wm["torso"])
                strengths = [max(a, b) for a, b in zip(strengths, cs)]

            # where the wrist must be so the figure's hand covers the points best
            R_hand = Rt @ (T.transposed() @ hand_frames[side])
            q = skel.hand_points(side, frots)
            num, den = Vector(), 0.0
            for i in range(21):
                w = 1.0 + 8.0 * strengths[i]
                num += (M[i] - R_hand @ q[i]) * w
                den += w
            Wt = num / den
            # the point that touches lands exactly on its spot
            k = max(range(21), key=lambda i: strengths[i])
            if strengths[k] > 0.05:
                Wt = Wt + (M[k] - (Wt + R_hand @ q[k])) * strengths[k]
            Wt = self.keep_out(Wt, R_hand, q, Wh, Wh_inv, Wm["torso"], Wt_inv)

            # two-bone solve; the elbow bends the way the person's elbow points
            S = Sm[side]
            L1, L2 = skel.L1[side], skel.L2[side]
            D = Wt - S
            dist = D.length
            lo, hi = abs(L1 - L2) + 1e-3, (L1 + L2) * 0.9995
            if dist < 1e-6:
                continue
            dhat = D / dist
            if dist > hi or dist < lo:
                dist = _clamp(dist, lo, hi)
                Wt = S + dhat * dist
            pole = None
            Eu = body.get(side + "_el")
            if Eu is not None:
                Eu, Wu = conv(Eu), Pc[0]
                a = Eu - Su[side]
                ax = Wu - Su[side]
                if ax.length > 1e-6:
                    ax.normalize()
                    pu = a - ax * a.dot(ax)
                    if pu.length > 0.025:
                        pole = R_b @ pu
            if pole is None:
                pole = self.prev_pole.get(side)
            if pole is None:
                sx = -1.0 if side == "R" else 1.0
                pole = Rt @ Vector((0.5 * sx, 0.25, -1.0))
            perp = pole - dhat * pole.dot(dhat)
            if perp.length < 1e-6:
                perp = dhat.orthogonal()
            perp.normalize()
            self.prev_pole[side] = perp.copy()
            cosA = _clamp((L1 * L1 + dist * dist - L2 * L2) / (2 * L1 * dist), -1.0, 1.0)
            E = S + (dhat * cosA + perp * math.sqrt(max(0.0, 1 - cosA * cosA))) * L1
            placed[side] = (S, E, Wt, Rt)
            self.info[side] = {"touch": max(strengths), "depth": round(t.z, 3),
                               "targets": M, "wrist": Wt, "hand_rot": R_hand, "strengths": strengths,
                               "model_hand": [R_hand @ v for v in q]}
        return placed

    def keep_out(self, Wt, R_hand, q, Wh, Wh_inv, Wtorso, Wt_inv):
        """Move the wrist so no hand point is inside the head or the chest."""
        skel = self.rt.skel
        if skel.bvh is not None:
            push, best = Vector(), 0.0
            for p in q:
                hl = Wh_inv @ (Wt + R_hand @ p)
                if (hl - skel.head_center).length > skel.head_radius + 0.02:
                    continue
                loc, nrm, _, _ = skel.bvh.find_nearest(hl)
                if loc is None:
                    continue
                depth = self.SKIN * 0.7 - (hl - loc).dot(nrm)
                if depth > best:
                    best, push = depth, nrm * depth
            if best > 0:
                Wt = Wt + (Wh.to_3x3() @ push)
        if skel.chest is not None:
            lo, hi = skel.chest
            worst = 0.0
            for p in q:
                tl = Wt_inv @ (Wt + R_hand @ p)
                if lo.x < tl.x < hi.x and lo.z < tl.z < hi.z * 0.95:
                    d = tl.y - (lo.y - self.SKIN)
                    if 0 < d < (hi.y - lo.y) * 0.6:      # in the front part of the chest
                        worst = max(worst, d)
            if worst > 0:
                Wt = Wt + Wtorso.to_3x3() @ Vector((0, -worst, 0))
        return Wt


class Retargeter:
    """Turns one frame of MediaPipe landmarks into joint rotations for a figure."""

    D = math.radians
    LIMITS = {   # radians, on the joint's own Euler axes
        "wrist_x": (D(-90), D(80)), "wrist_y_R": (D(-45), D(35)), "wrist_y_L": (D(-35), D(45)),
        "twist_R": (D(-130), D(170)), "twist_L": (D(-170), D(130)),
        "mcp": (D(-30), D(100)), "spread": (D(-35), D(35)), "pip": (D(-10), D(115)), "dip": (D(-20), D(90)),
        "th_across": (D(-90), D(40)), "th_roll": (D(-60), D(60)), "th_mid": (D(-20), D(90)), "th_tip": (D(-30), D(90)),
        "lean": (D(-30), D(40)), "turn": (D(-60), D(60)),
        "nod": (D(-40), D(50)), "tilt": (D(-40), D(40)), "hturn": (D(-80), D(80)),
    }

    def __init__(self):
        self.mirror = False
        self.skel = None          # ModelSkeleton of the figure (for depth-aware hand placement)
        self.use_depth = True
        self.fov = 60.0           # camera's horizontal field of view (degrees)
        self.groups = None        # tracked groups (None = all), see TRACK_GROUPS
        self.depth = DepthPlacer(self)
        self.reset()

    def reset(self):
        self.prev = {}            # joint -> Euler (keeps angles continuous between frames)
        self.prev_bend = {}       # side -> elbow hinge direction (torso space)
        self.prev_thumb = {}      # side -> thumb bend direction (hand space)
        self.calib = None         # neutral pose: yaw, lean, head offset, face baselines
        self.calib_left = 0       # frames still to collect for calibration
        self._calib_acc = []
        self.thumb_rest = {}      # side -> 3x3 rest rotation of the thumb base joint
        self.wrist_rest_z = {}    # side -> rest turn of the wrist joint (radians)
        self.last_face = None     # (t seconds, face) of the last frame that had a face
        self.depth.reset()

    def start_calibration(self, frames=20):
        self.calib_left = frames
        self._calib_acc = []

    @property
    def calibrating(self):
        return self.calib_left > 0

    # --- helpers --------------------------------------------------------
    def conv(self, v):
        x = -v[0] if self.mirror else v[0]
        return Vector((x, v[2], -v[1]))

    def _side(self, side):
        """Which of the person's sides drives the figure's `side`."""
        if not self.mirror:
            return side
        return "L" if side == "R" else "R"

    def _pose_pt(self, pose, side, key, min_vis=0.5):
        i = POSE_IDX[self._side(side)][key]
        if not pose or i >= len(pose):
            return None
        p = pose[i]
        if len(p) > 3 and p[3] < min_vis:
            return None
        return self.conv(p)

    def tracked(self, key):
        g = self.groups
        return g is None or any(TRACK_GROUPS[n](key) for n in g)

    def recent_face(self, t):
        """The face from a recent frame (the browser may run the face model every 2nd-3rd frame)."""
        lf = self.last_face
        return lf[1] if lf is not None and 0 <= t - lf[0] < 0.3 else None

    def _euler(self, key, m, order='XYZ'):
        prev = self.prev.get(key)
        e = m.to_euler(order, prev) if prev is not None and prev.order == order else m.to_euler(order)
        return e

    # --- body -----------------------------------------------------------
    def torso(self, pose):
        L, R = self._pose_pt(pose, "L", "sh"), self._pose_pt(pose, "R", "sh")
        if L is None or R is None:
            return None
        s = L - R
        yaw = math.atan2(s.y, s.x)
        lean = 0.0
        hl, hr = self._pose_pt(pose, "L", "hip", 0.7), self._pose_pt(pose, "R", "hip", 0.7)
        if hl is not None and hr is not None:
            up = (L + R) / 2 - (hl + hr) / 2
            if up.length > 1e-6:
                lean = math.atan2(-up.y, up.z)
        return yaw, lean

    def arm(self, side, pose, T, out):
        """Shoulder rotation and elbow bend from the tracked arm directions (used when the arm
        can't be placed by position). Returns the elbow frame before twist (torso space)."""
        S, E, W = (self._pose_pt(pose, side, k) for k in ("sh", "el", "wr"))
        if S is None or E is None or W is None:
            return None, None
        Ti = T.transposed()
        return self.arm_from_dirs(side, Ti @ (E - S), Ti @ (W - E), out)

    def arm_from_dirs(self, side, u, f, out):
        """u: shoulder->elbow, f: elbow->wrist, both in torso space."""
        if u.length < 1e-6 or f.length < 1e-6:
            return None, None
        u = u.normalized()
        f = f.normalized()
        bend = min(u.angle(f), math.radians(150))
        perp = f - u * f.dot(u)
        if perp.length > 0.17:                       # elbow clearly bent: hinge from the arm itself
            bdir = perp.normalized()
            self.prev_bend[side] = bdir.copy()
        else:                                        # nearly straight: keep the last hinge direction
            pb = self.prev_bend.get(side, Vector((0, -1, 0)))
            bdir = pb - u * pb.dot(u)
            if bdir.length < 1e-6:
                bdir = u.orthogonal()
            bdir.normalize()
        z = -u
        y = -bdir
        R_sh = _frame(y.cross(z), y, z)
        key = f"{side}_shoulder"
        e = self._euler(key, R_sh, 'ZXY')
        out[key] = e
        return R_sh @ _rot('X', -bend), bend

    @staticmethod
    def hand_frame(side, P):
        """Wrist frame from hand landmarks: -Z toward the fingers, -Y out of the palm."""
        knuckles = (P[5] + P[9] + P[13] + P[17]) / 4
        z = -(knuckles - P[0]).normalized()
        t = P[5] - P[17]                              # pinky -> index (toward the thumb)
        x = -t if side == "R" else t                  # the thumb is on -X for the right hand
        x = (x - z * x.dot(z)).normalized()
        return _frame(x, z.cross(x), z)

    def wrist(self, side, elbow_pre, bend, H_t, out):
        """Forearm twist (on the elbow joint) and wrist bend from the hand's orientation."""
        m = 1 if side == "R" else -1
        M = elbow_pre.transposed() @ H_t
        e = M.to_euler('XYZ')
        L = self.LIMITS
        twist = e.z - self.wrist_rest_z.get(side, m * R90)
        prev = self.prev.get(f"{side}_elbow")
        twist = _pick_angle(twist, L[f"twist_{side}"], prev.z if prev is not None else 0.0)
        wx = _clamp(e.x, *L["wrist_x"])
        wy = _clamp(e.y, *L[f"wrist_y_{side}"])
        out[f"{side}_elbow"] = Euler((-bend, 0.0, twist), 'ZXY')
        out[f"{side}_wrist"] = Euler((wx, wy, 0.0), 'XYZ')

    def fingers(self, side, P, H, out):
        L = self.LIMITS
        Ht = H.transposed()
        for name, (a, b, c, d) in HAND_CHAINS.items():
            d1 = Ht @ (P[b] - P[a])
            d2 = Ht @ (P[c] - P[b])
            d3 = Ht @ (P[d] - P[c])
            if min(d1.length, d2.length, d3.length) < 1e-6:
                continue
            d1.normalize()
            horiz = math.hypot(d1.x, d1.z)
            base = math.atan2(-d1.y, horiz)
            spread = math.atan2(-d1.x, -d1.z)
            if abs(spread) > R90 and horiz > 0.05:   # knuckle bent past 90 degrees
                spread -= math.copysign(math.pi, spread)
                base = math.copysign(math.pi, base) - base
            base = _clamp(base, *L["mcp"])
            # When the knuckle bends ~90 degrees (a fist), the first segment points straight out
            # of the palm and says nothing about sideways spread. The plane the rest of the finger
            # bends in still does: its hinge axis is (cos s, 0, -sin s) in hand space.
            if horiz < 0.6:
                d2n = d2.normalized()
                hinge = d1.cross(d2n)
                if hinge.length > 0.2 and abs(hinge.normalized().y) < 0.5:
                    hinge = -hinge.normalized()
                    s_h = math.atan2(-hinge.z, hinge.x)
                    if abs(s_h) > R90:                 # finger bent backwards: hinge points the other way
                        s_h -= math.copysign(math.pi, s_h)
                    w = _clamp((horiz - 0.15) / 0.45, 0.0, 1.0)
                    spread = w * spread + (1 - w) * s_h
                elif horiz < 0.15:
                    prev = self.prev.get(f"{side}_{name}_base")
                    spread = prev.y if prev is not None else 0.0
            spread = _clamp(spread, *L["spread"])
            F1 = _rot('Y', spread) @ _rot('X', -base)
            q2 = F1.transposed() @ d2
            mid = _clamp(math.atan2(-q2.y, -q2.z), *L["pip"])
            F2 = F1 @ _rot('X', -mid)
            q3 = F2.transposed() @ d3
            tip = _clamp(math.atan2(-q3.y, -q3.z), *L["dip"])
            out[f"{side}_{name}_base"] = Euler((-base, spread, 0.0))
            out[f"{side}_{name}_mid"] = Euler((-mid, 0.0, 0.0))
            out[f"{side}_{name}_tip"] = Euler((-tip, 0.0, 0.0))

    def thumb(self, side, P, H, out):
        """The thumb base has 3 rotations (out, across, roll) that must reproduce the metacarpal's
        full orientation. 'Out' sits before the fixed rest rotation, so it is found with a short
        1-D search; across and roll then follow directly."""
        rest = self.thumb_rest.get(side)
        if rest is None:
            return
        L = self.LIMITS
        Ht = H.transposed()
        q = [Ht @ P[i] for i in (1, 2, 3, 4)]
        dm, dp, dd = q[1] - q[0], q[2] - q[1], q[3] - q[2]
        if min(dm.length, dp.length, dd.length) < 1e-6:
            return
        dm.normalize()
        dp.normalize()
        dd.normalize()
        bend = dp - dm * dp.dot(dm)
        if bend.length < 0.12:
            bend = dd - dm * dd.dot(dm)
        if bend.length < 0.12:
            pb = self.prev_thumb.get(side, Vector((0, -1, 0)))
            bend = pb - dm * pb.dot(dm)
        if bend.length < 1e-6:
            bend = dm.orthogonal()
        bdir = bend.normalized()
        self.prev_thumb[side] = bdir.copy()
        rest_t = rest.transposed()
        lo, hi = (math.radians(-90), math.radians(80)) if side == "R" else (math.radians(-80), math.radians(90))
        prev = self.prev.get(f"{side}_thumb_cmc")

        def excess(v, lim):
            return max(0.0, lim[0] - v, v - lim[1])

        def solve_frame(Q):
            def resid(o):
                return (rest_t @ _rot('Y', -o) @ Q).to_euler('XYZ')

            def cost(o):
                e = resid(o)
                c = abs(e.y) + 2.0 * (excess(e.x, L["th_across"]) + excess(e.z, L["th_roll"]))
                if prev is not None:
                    c += 0.03 * abs(o - prev.y)
                return c

            best_o, best_v = lo, 1e9
            o, step = lo, math.radians(4)
            while o <= hi + 1e-9:
                v = cost(o)
                if v < best_v:
                    best_o, best_v = o, v
                o += step
            a_, b_ = max(lo, best_o - step), min(hi, best_o + step)
            for _ in range(12):
                m1, m2 = a_ + (b_ - a_) / 3, b_ - (b_ - a_) / 3
                if cost(m1) < cost(m2):
                    b_ = m2
                else:
                    a_ = m1
            o = (a_ + b_) / 2
            return o, resid(o)

        # The bend direction says which way the thumb curls, but a thumb whose middle joint
        # bends slightly backwards curls the "wrong" way. Try both and keep the one a real
        # thumb can do.
        best = None
        for sign in (1, -1):
            yv = -bdir * sign
            Q = _frame(yv.cross(-dm), yv, -dm)
            o, e = solve_frame(Q)
            score = abs(e.y) + 2.0 * (excess(e.x, L["th_across"]) + excess(e.z, L["th_roll"]))
            if sign < 0:
                score += 0.05                     # prefer the usual (forward) curl when both fit
            if prev is not None:
                score += 0.05 * abs(o - prev.y)
            if best is None or score < best[0]:
                best = (score, o, e)
        _, o, e = best
        ax = _clamp(e.x, *L["th_across"])
        rz = _clamp(e.z, *L["th_roll"])
        F = _rot('Y', o) @ rest @ _rot('Z', rz) @ _rot('X', ax)
        q2 = F.transposed() @ dp
        mid = _clamp(math.atan2(-q2.y, -q2.z), *L["th_mid"])
        F2 = F @ _rot('X', -mid)
        q3 = F2.transposed() @ dd
        tip = _clamp(math.atan2(-q3.y, -q3.z), *L["th_tip"])
        out[f"{side}_thumb_cmc"] = Euler((0.0, o, 0.0))
        out[f"{side}_thumb_base"] = Euler((ax, 0.0, rz))
        out[f"{side}_thumb_mid"] = Euler((-mid, 0.0, 0.0))
        out[f"{side}_thumb_tip"] = Euler((-tip, 0.0, 0.0))

    def head_frame(self, face):
        pts = face.get("pts") or {}
        try:
            le, re_, top, chin = (self.conv(pts[k]) for k in ("263", "33", "10", "152"))
        except KeyError:
            return None
        if self.mirror:
            le, re_ = re_, le
        X = le - re_
        U = top - chin
        if X.length < 1e-6 or U.length < 1e-6:
            return None
        X.normalize()
        Z = (U - X * U.dot(X)).normalized()
        return _frame(X, Z.cross(X), Z)

    def brow_measure(self, face, cam=None):
        """Height of each eyebrow node above its eye, and the gap between the inner ends,
        in head space and in units of the eye-corner distance (so camera distance doesn't matter).
        With the camera known (cam), the points are first put back in 3D with perspective, so a
        turned head doesn't look like a raised brow."""
        pts = (face or {}).get("pts") or {}
        need = [k for side in BROW_POINTS.values() for grp in side for k in grp]
        if any(str(k) not in pts for k in need + [10, 152]):
            return None
        if cam is not None:
            fc = DepthPlacer.face_cam(face, cam)
            if fc is None:
                return None
            Q = {k: self.conv(fc["pts"][k][0]) for k in need + [10, 152]}
        else:
            Q = {k: self.conv(pts[str(k)]) for k in need + [10, 152]}
        le, re_ = Q[263], Q[33]
        if self.mirror:
            le, re_ = re_, le
        X, U = le - re_, Q[10] - Q[152]
        if X.length < 1e-9 or U.length < 1e-9:
            return None
        X.normalize()
        Z = (U - X * U.dot(X)).normalized()
        Ri = _frame(X, Z.cross(X), Z).transposed()
        P = {k: Ri @ v for k, v in Q.items()}
        iod = (P[33] - P[263]).length
        if iod < 1e-9:
            return None
        out = {}
        for ps, (nodes, eye) in BROW_POINTS.items():
            ez = (P[eye[0]].z + P[eye[1]].z) / 2
            out[ps] = [(P[k].z - ez) / iod for k in nodes]
        out["gap"] = (P[107] - P[336]).length / iod
        return out

    def camera(self, msg):
        img = msg.get("img")
        if not img:
            return None
        f = img[0] / (2 * math.tan(math.radians(self.fov) / 2))
        return (f, img[0] / 2, img[1] / 2)

    def face_values(self, face, cam=None):
        blend = (face or {}).get("blend") or {}
        base = (self.calib or {}).get("blend", {})

        def g(k):
            k2 = k
            if self.mirror:
                k2 = k.replace("Left", "#").replace("Right", "Left").replace("#", "Right")
            b = base.get(k2, 0.0)
            return max(0.0, blend.get(k2, 0.0) - b) / max(0.25, 1.0 - b)

        smile = (g("mouthSmileLeft") + g("mouthSmileRight")) / 2
        frown = (g("mouthFrownLeft") + g("mouthFrownRight")) / 2

        def dz(v, d):          # small dead zone so a relaxed face stays still
            return 0.0 if abs(v) < d else (v - math.copysign(d, v)) / (1 - d)

        out = {
            "brows": 0.0,       # the three nodes per brow below carry the eyebrows
            "mouth_open": _clamp(dz(1.6 * g("jawOpen"), 0.08), 0.0, 1.0),
            "smile": _clamp(dz(1.6 * smile - 1.8 * frown, 0.12), -1.0, 1.0),
            "pucker": _clamp(dz(1.4 * max(g("mouthPucker"), g("mouthFunnel")), 0.12), 0.0, 1.0),
            "cheek_puff": _clamp(dz(1.8 * g("cheekPuff"), 0.12), 0.0, 1.0),
            # blendshape Left/Right are the person's own sides
            "eye_close_L": _clamp(dz(1.25 * max(g("eyeBlinkLeft"), 0.6 * g("eyeSquintLeft")), 0.25), 0.0, 1.0),
            "eye_close_R": _clamp(dz(1.25 * max(g("eyeBlinkRight"), 0.6 * g("eyeSquintRight")), 0.25), 0.0, 1.0),
        }

        # eyebrows: measured from the brow points once there is a neutral face to compare with,
        # otherwise estimated from MediaPipe's expression scores
        m = self.brow_measure(face, cam)
        neutral = (self.calib or {}).get("brow")
        down_avg = (g("browDownLeft") + g("browDownRight")) / 2
        for side in SIDES:
            suf = "Left" if side == "L" else "Right"
            if m is not None and neutral is not None:
                ps = self._side(side)
                vals = []
                for v, n0 in zip(m[ps], neutral[ps]):
                    d = v - n0
                    vals.append(d / 0.075 if d > 0 else d / 0.05)
            else:
                inner = 1.8 * g("browInnerUp") - 2.0 * g("browDown" + suf)
                outer = 1.8 * g("browOuterUp" + suf) - 1.0 * g("browDown" + suf)
                vals = [inner, (inner + outer) / 2, outer]
            for n, v in zip(BROW_NODES, vals):
                out[f"brow_{n}_{side}"] = _clamp(dz(v, 0.12), -1.0, 1.0)
        sq = 1.5 * down_avg
        if m is not None and neutral is not None:
            sq = max(sq * 0.5, (neutral["gap"] - m["gap"]) / 0.06)
        out["brow_squeeze"] = _clamp(dz(sq, 0.15), 0.0, 1.0)
        return out

    # --- one frame --------------------------------------------------------
    def solve(self, msg):
        """Returns (rotations {joint: Euler}, face values {key: float} or None, hands_seen)."""
        out = {}
        pose = msg.get("pose")
        hands, hands_raw = {}, {}
        for h in msg.get("hands") or []:
            side = h.get("side")
            if side in ("R", "L") and len(h.get("world") or []) == 21:
                if self.mirror:
                    side = "L" if side == "R" else "R"
                hands[side] = [self.conv(p) for p in h["world"]]
                hands_raw[side] = h
        t_sec = float(msg.get("t", 0.0)) / 1000.0

        tl = self.torso(pose) if pose else None
        calib = self.calib or {}
        L = self.LIMITS
        if tl is not None:
            yaw, lean = tl
            T = _rot('Z', yaw) @ _rot('X', lean)
            out["torso"] = Euler((_clamp(lean - calib.get("lean", 0.0), *L["lean"]), 0.0,
                                  _clamp(yaw - calib.get("yaw", 0.0), *L["turn"])))
        else:
            T = Matrix.Identity(3)

        # head and face first: the hands are placed relative to the head
        face = msg.get("face")
        face_vals = None
        if face:
            self.last_face = (t_sec, face)
            Rh = self.head_frame(face)
            if Rh is not None:
                local = T.transposed() @ Rh
                if self.calibrating:
                    self._calib_acc.append((tl, local.to_quaternion(), dict(face.get("blend") or {}),
                                            self.brow_measure(face, self.camera(msg))))
                off = calib.get("head")
                if off is not None:
                    local = local @ off
                e = self._euler("head", local, 'XYZ')
                out["head"] = Euler((_clamp(e.x, *L["nod"]), _clamp(e.y, *L["tilt"]), _clamp(e.z, *L["hturn"])))
            face_vals = self.face_values(face, self.camera(msg))
        elif self.calibrating and tl is not None:
            self._calib_acc.append((tl, None, {}, None))

        # hand shapes
        frames = {}
        for side, P in hands.items():
            H = self.hand_frame(side, P)
            frames[side] = H
            self.fingers(side, P, H, out)
            self.thumb(side, P, H, out)

        # arms: by position when possible, otherwise by direction
        placed = {}
        if tl is not None and self.use_depth and self.skel is not None and hands:
            try:
                placed = self.depth.place(msg, pose, hands_raw, T, frames, out)
            except Exception:
                traceback.print_exc()
                placed = {}
        for side in SIDES:
            if side in placed:
                S, E, W, Rt = placed[side]
                Ti = Rt.transposed()
                elbow_pre, bend = self.arm_from_dirs(side, Ti @ (E - S), Ti @ (W - E), out)
            elif tl is not None:
                elbow_pre, bend = self.arm(side, pose, T, out)
            else:
                elbow_pre = None
            if elbow_pre is not None and side in frames:
                self.wrist(side, elbow_pre, bend, T.transposed() @ frames[side], out)

        if self.calibrating:
            self.calib_left -= 1
            if self.calib_left <= 0:
                self._finish_calibration()

        for k, e in out.items():
            self.prev[k] = e
        return out, face_vals, bool(hands)

    def _finish_calibration(self):
        acc = self._calib_acc
        self.calib_left = 0
        if not acc:
            return
        calib = {"yaw": 0.0, "lean": 0.0, "blend": {}}
        tl = [a[0] for a in acc if a[0] is not None]
        if tl:
            calib["yaw"] = sum(t[0] for t in tl) / len(tl)
            calib["lean"] = sum(t[1] for t in tl) / len(tl)
        quats = [a[1] for a in acc if a[1] is not None]
        if quats:
            ref = quats[0]
            s = Quaternion((0, 0, 0, 0))
            for q in quats:
                s = s + (q if q.dot(ref) >= 0 else -q)
            s.normalize()
            calib["head"] = s.to_matrix().inverted()
        blends = [a[2] for a in acc if a[2]]
        if blends:
            keys = set().union(*blends)
            calib["blend"] = {k: sum(b.get(k, 0.0) for b in blends) / len(blends) for k in keys}
        brows = [a[3] for a in acc if len(a) > 3 and a[3]]
        if brows:
            calib["brow"] = {ps: [sum(b[ps][i] for b in brows) / len(brows) for i in range(3)] for ps in SIDES}
            calib["brow"]["gap"] = sum(b["gap"] for b in brows) / len(brows)
        self.calib = calib


class LiveFigure:
    """Fast access to one figure's joints and face for live updates."""

    def __init__(self, root):
        self.root = root
        self.name = root.name
        self.joints = {o["asl_joint"]: o for o in root.children_recursive if "asl_joint" in o}
        self.rig = face_rig(root)
        self.face = {k: getattr(root.asl_fig, k) for k in FACE_KEYS}

    def valid(self):
        try:
            return self.root.name == self.name and bpy.data.objects.get(self.name) is self.root
        except ReferenceError:
            return False

    def apply(self, rots, face=None):
        for k, e in rots.items():
            j = self.joints.get(k)
            if j is None:
                continue
            cur = j.rotation_euler
            if abs(cur[0] - e[0]) + abs(cur[1] - e[1]) + abs(cur[2] - e[2]) > 1e-5:
                j.rotation_euler = (e[0], e[1], e[2])
        if face:
            changed = {k: v for k, v in face.items() if abs(self.face.get(k, 0.0) - v) > 1e-3}
            if changed:
                set_face(self.rig, changed)
                self.face.update(changed)

    def snapshot(self):
        return ({k: tuple(round(a, 5) for a in j.rotation_euler) for k, j in self.joints.items()},
                {k: round(v, 4) for k, v in self.face.items()})

    def to_sliders(self):
        """Write the current live pose into the figure's sliders (so it can become a card)."""
        global _suspend_updates
        f = self.root.asl_fig
        d = math.degrees

        def eul(k):
            j = self.joints.get(k)
            return j.rotation_euler if j else (0.0, 0.0, 0.0)

        vals = {}
        t = eul("torso")
        vals.update(torso_lean=d(t[0]), torso_turn=-d(t[2]))
        h = eul("head")
        vals.update(head_nod=d(h[0]), head_tilt=-d(h[1]), head_turn=-d(h[2]))
        for side in SIDES:
            m = 1 if side == "R" else -1
            sh, el, wr = eul(f"{side}_shoulder"), eul(f"{side}_elbow"), eul(f"{side}_wrist")
            vals.update({f"{side}_shoulder_forward": -d(sh[0]), f"{side}_shoulder_out": m * d(sh[1]),
                         f"{side}_shoulder_twist": m * d(sh[2]), f"{side}_elbow_bend": -d(el[0]),
                         f"{side}_forearm_turn": m * d(el[2]), f"{side}_wrist_bend": -d(wr[0]),
                         f"{side}_wrist_side": m * d(wr[1])})
            for fn in FINGERS:
                b = eul(f"{side}_{fn}_base")
                vals.update({f"{side}_{fn}_base": -d(b[0]), f"{side}_{fn}_side": m * d(b[1]),
                             f"{side}_{fn}_mid": -d(eul(f"{side}_{fn}_mid")[0]),
                             f"{side}_{fn}_tip": -d(eul(f"{side}_{fn}_tip")[0])})
            c, tb = eul(f"{side}_thumb_cmc"), eul(f"{side}_thumb_base")
            vals.update({f"{side}_thumb_out": m * d(c[1]), f"{side}_thumb_across": -d(tb[0]),
                         f"{side}_thumb_roll": m * d(tb[2]),
                         f"{side}_thumb_mid": -d(eul(f"{side}_thumb_mid")[0]),
                         f"{side}_thumb_tip": -d(eul(f"{side}_thumb_tip")[0])})
        vals.update(self.face)
        _suspend_updates = True
        try:
            for k, v in vals.items():
                if hasattr(f, k):
                    setattr(f, k, v)       # FloatProperty clamps to the slider range
        finally:
            _suspend_updates = False
        apply_figure(self.root)


TRACK_GROUPS = {
    "arms": lambda k: k.endswith(("_shoulder", "_elbow", "_wrist")),
    "hands": lambda k: any(k.startswith(f"{s}_{f}") for s in SIDES for f in FINGERS + ("thumb",)),
    "head": lambda k: k == "head",
    "torso": lambda k: k == "torso",
}


# --- Recording ----------------------------------------------------------------

MAX_TAKE_SECONDS = 30.0


class Take:
    """One recorded attempt: the figure's joint rotations and face over time."""

    def __init__(self):
        self.samples = []        # (t seconds, rotations {joint: (x,y,z)}, face {key: v}, hands_seen)
        self.times = []
        self.msgs = []           # the tracking messages, so the take can be re-solved for a new body shape
        self.start = 0.0         # trim, in seconds from the first sample
        self.end = 0.0
        self.setup = None        # retargeter settings and calibration used while recording
        self.body_sig = None     # body shape the rotations were solved for
        self.arrows = []         # arrows drawn on the GIF (see TAKE_ARROW_DEFAULTS)

    @property
    def t0(self):
        return self.times[0] if self.times else 0.0

    @property
    def duration(self):
        return self.times[-1] - self.times[0] if len(self.times) > 1 else 0.0

    def add(self, t, rots, face, seen, msg=None):
        if self.times and t <= self.times[-1]:
            return
        self.samples.append((t, rots, face, seen))
        self.times.append(t)
        self.msgs.append(msg)

    def resolve(self, skel):
        """Solve every recorded frame again for the figure's current body shape (after the Body
        Shape sliders, proportions or sculpting changed), so hands still land on the face etc."""
        st = self.setup
        if not st or not self.msgs or any(m is None for m in self.msgs):
            return False
        rt = Retargeter()
        rt.mirror, rt.use_depth, rt.fov, rt.groups = st["mirror"], st["use_depth"], st["fov"], st["groups"]
        rt.calib = st["calib"]
        rt.thumb_rest, rt.wrist_rest_z = dict(st["thumb_rest"]), dict(st["wrist_rest_z"])
        rt.skel = skel
        rt.depth.hand_k = st.get("hand_k", 1.0)
        new = []
        for (t, rots, face, seen), msg in zip(self.samples, self.msgs):
            out, _, _ = rt.solve(msg)
            r = dict(rots)
            for k, e in out.items():
                if k in r and rt.tracked(k):
                    r[k] = tuple(round(a, 5) for a in e)
            new.append((t, r, face, seen))
        self.samples = new
        self.body_sig = skel.sig
        return True

    def pose_at(self, t):
        """Interpolated (rotations, face) at t seconds from the start of the take."""
        s = self.samples
        if not s:
            return {}, {}
        tt = self.t0 + t
        if tt <= s[0][0]:
            return s[0][1], s[0][2]
        if tt >= s[-1][0]:
            return s[-1][1], s[-1][2]
        import bisect
        i = bisect.bisect_right(self.times, tt)
        ta, ra, fa, _ = s[i - 1]
        tb, rb, fb, _ = s[i]
        w = (tt - ta) / (tb - ta) if tb > ta else 0.0
        rots = {}
        for k, va in ra.items():
            vb = rb.get(k, va)
            rots[k] = (va[0] + (vb[0] - va[0]) * w, va[1] + (vb[1] - va[1]) * w, va[2] + (vb[2] - va[2]) * w)
        face = {k: v + (fb.get(k, v) - v) * w for k, v in fa.items()}
        return rots, face

    def auto_trim(self):
        """Start just before the hands first appear, end just after they were last seen."""
        seen = [t - self.t0 for t, _, _, sn in self.samples if sn]
        if seen:
            self.start = max(0.0, seen[0] - 0.15)
            self.end = min(self.duration, seen[-1] + 0.2)
        else:
            self.start, self.end = 0.0, self.duration
        if self.end - self.start < 0.2:
            self.start, self.end = 0.0, self.duration

    def to_dict(self, fps, text, category):
        out = []
        for t, r, f, _ in self.samples:
            rel = t - self.t0
            if self.start - 1e-6 <= rel <= self.end + 1e-6:
                out.append({"t": round(rel - self.start, 4),
                            "r": {k: [round(a, 4) for a in v] for k, v in r.items()},
                            "f": {k: round(v, 3) for k, v in f.items()}})
        return {"version": 1, "kind": "recorded-sign", "term": text, "category": category,
                "fps": fps, "duration": round(self.end - self.start, 3), "samples": out,
                "arrows": [dict(a) for a in self.arrows]}


# --- Arrows on a recorded GIF ----------------------------------------------------------
# Each arrow is a small dict (set from the web app's Review panel or Blender). Kinds:
#   path     traces where a body part moved during part of the take
#   along    straight arrow attached to a body part (moves with it)
#   around   circular arrow around a body part (moves with it)
#   straight / arc / circle / double / zigzag   a fixed shape placed near a body part
# Times are seconds from the start of the trimmed take; None means "start" / "end".
# Offsets are in millimeters as seen from the front: x right, y up, z toward the viewer.

TAKE_ARROW_KINDS = ("path", "along", "around", "straight", "arc", "circle", "double", "zigzag")
TAKE_ARROW_PARTS = [p[0] for p in TETHER_PARTS]
TAKE_ARROW_DEFAULTS = {
    "kind": "path", "side": "R", "part": "index_tip",
    "from": None, "to": None,                 # path: the part of the take it traces
    "show_from": None, "show_to": None,       # when the arrow is visible in the GIF
    "x": 0.0, "y": 0.0, "z": 0.0,             # offset (mm)
    "size": 100.0, "rotate": 0.0,             # fixed shapes: size (%) and turn (degrees)
    "axis": "BONE", "length": 80.0, "radius": 45.0, "sweep": 270.0, "spin": 0.0, "tilt": 0.0,
    "trim_start": 15.0, "trim_end": 10.0,     # path: % cut off each end so it clears the hand
    "smooth": 50.0, "grow": False,            # path: smoothing (%), draw it as the hand moves
    "reverse": False, "double": False, "on_top": True,
    "color": "", "thickness": 100.0,
}
_ARROW_LIMITS = {"x": (-400, 400), "y": (-400, 400), "z": (-400, 400), "size": (20, 400), "rotate": (-180, 180),
                 "length": (10, 600), "radius": (10, 300), "sweep": (30, 350), "spin": (-180, 180),
                 "tilt": (-90, 90), "trim_start": (0, 45), "trim_end": (0, 45), "smooth": (0, 100),
                 "thickness": (30, 300)}


def clean_take_arrow(d, prev=None):
    """A complete, safe arrow spec from (partial) input."""
    out = dict(prev or TAKE_ARROW_DEFAULTS)
    for k, v in (d or {}).items():
        if k not in TAKE_ARROW_DEFAULTS or k == "id":
            continue
        dv = TAKE_ARROW_DEFAULTS[k]
        try:
            if k == "kind":
                v = v if v in TAKE_ARROW_KINDS else out[k]
            elif k == "part":
                v = v if v in TAKE_ARROW_PARTS else out[k]
            elif k == "side":
                v = v if v in SIDES else out[k]
            elif k == "axis":
                v = v if v in TETHER_AXES else out[k]
            elif k == "color":
                v = v if isinstance(v, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", v or "") else ""
            elif k in ("from", "to", "show_from", "show_to"):
                v = None if v in (None, "") else max(0.0, float(v))
            elif isinstance(dv, bool):
                v = bool(v)
            else:
                lo, hi = _ARROW_LIMITS.get(k, (-1e9, 1e9))
                v = _clamp(float(v), lo, hi)
        except (TypeError, ValueError):
            continue
        out[k] = v
    if prev and "id" in prev:
        out["id"] = prev["id"]
    return out


def _hex_color(h, fallback):
    if not h:
        return fallback
    h = h.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)


def _screen_offset(a):
    return Vector((a["x"], -a["z"], a["y"])) / 1000.0


def take_arrow_objects():
    return [o for o in bpy.data.objects if "asl_take_arrow" in o]


def clear_take_arrows():
    for name in [o.name for o in take_arrow_objects() if o.type == 'CURVE']:
        o = bpy.data.objects.get(name)
        if o is not None:
            remove_arrow(o)
    for o in take_arrow_objects():
        bpy.data.objects.remove(o, do_unlink=True)


def _window(a, take, key_from, key_to):
    length = take.end - take.start
    t0 = a.get(key_from)
    t1 = a.get(key_to)
    t0 = 0.0 if t0 is None else min(t0, length)
    t1 = length if t1 is None else min(t1, length)
    if t1 < t0:
        t0, t1 = t1, t0
    return t0, t1


def _part_pos(fig, side, part):
    return part_matrix(fig.root, side, part, fig.joints).translation.copy()


def _path_points(fig, take, a):
    """Where the body part was over the arrow's time window, smoothed and trimmed."""
    t0, t1 = _window(a, take, "from", "to")
    n = max(8, min(60, int((t1 - t0) * 30) + 2))
    pts = []
    for i in range(n):
        t = take.start + t0 + (t1 - t0) * i / (n - 1)
        fig.apply(*take.pose_at(t))
        bpy.context.view_layer.update()
        pts.append(_part_pos(fig, a["side"], a["part"]))
    # smooth with a moving average (ends pinned)
    win = int(round(a["smooth"] / 100 * n / 4))
    if win > 0:
        sm = []
        for i in range(n):
            lo, hi = max(0, i - win), min(n, i + win + 1)
            sm.append(sum(pts[lo:hi], Vector()) / (hi - lo))
        sm[0], sm[-1] = pts[0], pts[-1]
        pts = sm
    # cut a share of the length off each end so the arrow doesn't sit on the hand
    seg = [(pts[i + 1] - pts[i]).length for i in range(n - 1)]
    total = sum(seg)
    if total < 0.01:
        return None
    cut0, cut1 = total * a["trim_start"] / 100, total * (1 - a["trim_end"] / 100)

    def at(dist):
        acc = 0.0
        for i, sl in enumerate(seg):
            if acc + sl >= dist or i == len(seg) - 1:
                w = 0.0 if sl < 1e-9 else _clamp((dist - acc) / sl, 0.0, 1.0)
                return pts[i].lerp(pts[i + 1], w)
            acc += sl
        return pts[-1]

    k = 10                                        # resample evenly: smooth curve, few points
    out = [at(cut0 + (cut1 - cut0) * i / (k - 1)) for i in range(k)]
    off = _screen_offset(a)
    out = [p + off for p in out]
    if a["reverse"]:
        out.reverse()
    return out


def _fixed_points(a):
    shape = ARROW_SHAPES.get(a["kind"])
    if shape is None:
        return None, False
    s = a["size"] / 100
    rot = Matrix.Rotation(math.radians(-a["rotate"]), 3, 'Y')
    pts = [{"co": rot @ (Vector(co) * s), "hl_type": ht, "hr_type": ht} for co, ht in shape[1]]
    if a["reverse"]:
        pts.reverse()
    return pts, shape[2]


def build_take_arrows(scene, fig, take):
    """Create the Blender objects for the take's arrows (static shapes are computed here)."""
    clear_take_arrows()
    if take is None or not take.arrows:
        return
    saved = fig.snapshot()
    s = scene.asl
    try:
        for a in take.arrows:
            kind = a["kind"]
            double = a["double"] or kind == "double"
            if kind == "path":
                pts = _path_points(fig, take, a)
                if not pts:
                    continue
                if a["on_top"]:
                    pts = [Vector((p.x, ARROW_DEPTH, p.z)) for p in pts]
                obj = create_arrow(scene, [{"co": p} for p in pts], double=double, location=(0, 0, 0), kind="take")
            elif kind in ("along", "around"):
                obj = create_arrow(scene, [{"co": (0, 0, 0)}, {"co": (0, 0, 0.05)}], double=double,
                                   location=(0, 0, 0), kind="take")
            else:
                pts, dbl = _fixed_points(a)
                if not pts:
                    continue
                t0, _ = _window(a, take, "show_from", "show_to")
                fig.apply(*take.pose_at(take.start + t0))
                bpy.context.view_layer.update()
                c = _part_pos(fig, a["side"], a["part"]) + _screen_offset(a)
                obj = create_arrow(scene, pts, double=double or dbl,
                                   location=(c.x, ARROW_DEPTH if a["on_top"] else c.y, c.z), kind="take")
            obj["asl_take_arrow"] = a["id"]
            for h in arrow_heads(obj):
                h["asl_take_arrow"] = a["id"]
            cu = obj.data
            w = s.arrow_width * a["thickness"] / 100
            cu.bevel_depth = w
            cu.bevel_factor_mapping_end = 'SPLINE'
            color = _hex_color(a["color"], tuple(s.arrow_color))
            for m in cu.materials:
                set_material_color(m, color)
            for h in arrow_heads(obj):
                me = cone_mesh(h.data.name, w * 3.6, w * 7.5)
                me.materials.append(cu.materials[0])
                old = h.data
                h.data = me
                if old.users == 0:
                    bpy.data.meshes.remove(old)
    finally:
        fig.apply(*saved)


def _tethered_matrix(fig, a):
    m = (Matrix.Translation(_screen_offset(a))
         @ part_matrix(fig.root, a["side"], a["part"], fig.joints)
         @ _axis_basis(TETHER_AXES[a["axis"]])
         @ Matrix.Rotation(math.radians(a["spin"]), 4, 'Z')
         @ Matrix.Rotation(math.radians(a["tilt"]), 4, 'X'))
    if a["on_top"]:
        m.translation.y = ARROW_DEPTH
    return m


class _TetherSpec:
    """Lets tether_points() draw a take arrow."""

    def __init__(self, a):
        self.mode = "ALONG" if a["kind"] == "along" else "AROUND"
        self.length = a["length"] / 1000
        self.radius = a["radius"] / 1000
        self.sweep = a["sweep"]
        self.reverse = a["reverse"]


def update_take_arrows(take, fig, t):
    """Show, hide and move the take's arrows for time t (seconds from the start of the take).
    Call after the figure has been posed for t."""
    objs = {o["asl_take_arrow"]: o for o in take_arrow_objects() if o.type == 'CURVE'}
    if not objs or take is None:
        return
    rel = t - take.start
    need_update = any(a["kind"] in ("along", "around") for a in take.arrows)
    if need_update:
        bpy.context.view_layer.update()
    for a in take.arrows:
        obj = objs.get(a["id"])
        if obj is None:
            continue
        s0, s1 = _window(a, take, "show_from", "show_to")
        visible = s0 - 1e-6 <= rel <= s1 + 1e-6
        if a["kind"] in ("along", "around") and visible:
            set_arrow_points(obj.data, tether_points(_TetherSpec(a)))
            obj.matrix_world = _tethered_matrix(fig, a)
        grow = 1.0
        if a["kind"] == "path" and a["grow"]:
            p0, p1 = _window(a, take, "from", "to")
            grow = _clamp((rel - p0) / max(1e-3, p1 - p0), 0.0, 1.0)
            if a["reverse"]:
                grow = 1.0                        # a reversed path can't grow from its tail
            visible = visible and grow > 0.03
        obj.data.bevel_factor_end = grow
        heads = arrow_heads(obj)
        for h in heads:
            if h.name.endswith(".head0") and h.constraints:
                h.constraints[0].offset_factor = grow     # the main arrowhead rides the tip
        for o in [obj] + heads:
            o.hide_render = not visible
            o.hide_viewport = not visible


def take_arrow_bounds(take, fig, times):
    """Front-view box around the arrows over the given times (for framing the GIF)."""
    xs, zs = [], []
    for t in times:
        fig.apply(*take.pose_at(t))
        update_take_arrows(take, fig, t)
        for o in take_arrow_objects():
            if o.type != 'CURVE' or o.hide_render:
                continue
            mw = o.matrix_world
            r = o.data.bevel_depth * 8
            for sp in o.data.splines:
                for bp in sp.bezier_points:
                    for co in (bp.co, bp.handle_left, bp.handle_right):
                        w = mw @ co
                        xs += [w.x - r, w.x + r]
                        zs += [w.z - r, w.z + r]
    if not xs:
        return None
    return min(xs), max(xs), min(zs), max(zs)


# --- Animated GIF writer (pure Python + numpy, which ships with Blender) -----------

def _lzw_encode(data, min_size=8):
    """GIF-flavoured LZW. data: bytes of palette indices."""
    clear = 1 << min_size
    eoi = clear + 1
    code_size = min_size + 1
    next_code = eoi + 1
    table = {}
    out = bytearray()
    bitbuf, bitcnt = clear, code_size
    if not data:
        data = b"\x00"
    prefix = data[0]
    for byte in memoryview(data)[1:]:
        key = (prefix << 8) | byte
        code = table.get(key)
        if code is not None:
            prefix = code
            continue
        bitbuf |= prefix << bitcnt
        bitcnt += code_size
        while bitcnt >= 8:
            out.append(bitbuf & 255)
            bitbuf >>= 8
            bitcnt -= 8
        if next_code < 4096:
            table[key] = next_code
            next_code += 1
            if next_code > (1 << code_size) and code_size < 12:
                code_size += 1
        else:
            bitbuf |= clear << bitcnt
            bitcnt += code_size
            while bitcnt >= 8:
                out.append(bitbuf & 255)
                bitbuf >>= 8
                bitcnt -= 8
            table.clear()
            code_size = min_size + 1
            next_code = eoi + 1
        prefix = byte
    bitbuf |= prefix << bitcnt
    bitcnt += code_size
    if next_code == (1 << code_size) and code_size < 12:
        code_size += 1                     # the decoder grows its code size before reading EOI
    bitbuf |= eoi << bitcnt
    bitcnt += code_size
    while bitcnt > 0:
        out.append(bitbuf & 255)
        bitbuf >>= 8
        bitcnt -= 8
    return bytes(out)


def _gif_palette(frames, max_colors=255):
    import numpy as np
    step = max(1, len(frames) // 16)
    sample = np.concatenate([f.reshape(-1, 3) for f in frames[::step]] + [frames[-1].reshape(-1, 3)]).astype(np.int64)
    codes = ((sample[:, 0] >> 3) << 10) | ((sample[:, 1] >> 3) << 5) | (sample[:, 2] >> 3)
    cnt = np.bincount(codes, minlength=32768)
    sums = np.stack([np.bincount(codes, weights=sample[:, c], minlength=32768) for c in range(3)], 1)
    top = np.argsort(-cnt, kind="stable")[:max_colors]
    top = top[cnt[top] > 0]
    pal = np.round(sums[top] / cnt[top, None]).astype(np.int64)
    allc = np.arange(32768)
    allc = np.stack([((allc >> 10) & 31) * 8 + 4, ((allc >> 5) & 31) * 8 + 4, (allc & 31) * 8 + 4], 1)
    lut = np.empty(32768, np.uint8)
    for i in range(0, 32768, 4096):
        d = ((allc[i:i + 4096, None, :] - pal[None, :, :]) ** 2).sum(-1)
        lut[i:i + 4096] = d.argmin(1)
    lut[top] = np.arange(len(top))
    return pal.astype(np.uint8), lut


def encode_gif(frames, delays_cs):
    """frames: list of HxWx3 uint8 arrays. Only the pixels that change are stored for each frame."""
    import numpy as np
    pal, lut = _gif_palette(frames)
    H, W = frames[0].shape[:2]
    TR = 255                                   # reserved: "unchanged" (transparent)
    recs = []
    prev = None
    for f, delay in zip(frames, delays_cs):
        f = f.astype(np.int64)
        idx = lut[((f[..., 0] >> 3) << 10) | ((f[..., 1] >> 3) << 5) | (f[..., 2] >> 3)]
        if prev is None:
            recs.append([0, 0, W, H, False, idx.tobytes(), delay])
        else:
            diff = idx != prev
            rows, cols = np.nonzero(diff.any(1))[0], np.nonzero(diff.any(0))[0]
            if len(rows) == 0:
                recs[-1][6] += delay           # identical frame: just show the last one longer
                continue
            y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
            sub = idx[y0:y1, x0:x1].copy()
            sub[~diff[y0:y1, x0:x1]] = TR
            recs.append([int(x0), int(y0), int(x1 - x0), int(y1 - y0), True, sub.tobytes(), delay])
        prev = idx
    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", W, H, 0xF7, 0, 0)
    table = np.zeros((256, 3), np.uint8)
    table[:len(pal)] = pal
    out += table.tobytes()
    out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"       # loop forever
    for x, y, w, h, transparent, data, delay in recs:
        out += struct.pack("<BBBBHBB", 0x21, 0xF9, 4, (1 << 2) | (1 if transparent else 0),
                           max(2, int(delay)), TR, 0)
        out += struct.pack("<BHHHHB", 0x2C, x, y, w, h, 0)
        out.append(8)
        lz = _lzw_encode(data)
        for i in range(0, len(lz), 255):
            chunk = lz[i:i + 255]
            out.append(len(chunk))
            out += chunk
        out.append(0)
    out.append(0x3B)
    return bytes(out)


def gif_delays(n, fps):
    step = 100.0 / fps
    return [round((i + 1) * step) - round(i * step) for i in range(n)]


# --- Rendering a take ------------------------------------------------------------

def _render_op(scene):
    wm = bpy.context.window_manager
    if wm is not None and wm.windows:
        win = wm.windows[0]
        with bpy.context.temp_override(window=win, screen=win.screen, scene=scene):
            bpy.ops.render.render(write_still=True)
    else:
        bpy.ops.render.render(write_still=True)


def _render_array(scene, path):
    import numpy as np
    r = scene.render
    r.filepath = path
    r.image_settings.file_format = 'PNG'
    r.image_settings.color_mode = 'RGB'
    r.image_settings.color_depth = '8'
    _render_op(scene)
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    return (np.clip(buf.reshape(h, w, 4)[::-1, :, :3], 0, 1) * 255 + 0.5).astype(np.uint8)


def _use_workbench(scene):
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.use_freestyle = False
    sh = scene.display.shading
    # Flat colors (the same as the cards) with outlines and a little curvature shading,
    # on a plain white background.
    sh.light = 'FLAT'
    sh.color_type = 'MATERIAL'
    sh.show_object_outline = True
    sh.object_outline_color = (0.08, 0.07, 0.07)
    sh.show_cavity = True
    sh.cavity_type = 'SCREEN'
    sh.curvature_ridge_factor = 0.5
    sh.curvature_valley_factor = 1.0
    sh.show_shadows = False
    if hasattr(scene.display, "render_aa"):
        scene.display.render_aa = '8'
    if scene.world is not None:
        scene.world.color = (1.0, 1.0, 1.0)
    bd = _get("ASL_Backdrop")
    if bd is not None:
        bd.hide_render = True


def cards_dir(scene):
    """The cards folder as an absolute path, or None if it isn't usable yet."""
    d = scene.asl.out_dir
    if not d or (d.startswith("//") and not bpy.data.filepath):
        return None
    return bpy.path.abspath(d)


class _TakeRender:
    """Sets the scene up for rendering frames of a take (framing over the whole movement and its
    arrows, other figures and card arrows hidden, label strip) and puts everything back after."""

    def __init__(self, scene, fig, take, text, size, quality, label=True):
        self.scene, self.fig, self.take = scene, fig, take
        self.text, self.size, self.quality, self.label = text, size, quality, label
        self.band = None

    def __enter__(self):
        import numpy as np  # noqa: F401  (numpy ships with Blender)
        scene, fig, take, size = self.scene, self.fig, self.take, self.size
        s, r = scene.asl, scene.render
        self.saved_s = {k: getattr(s, k) for k in ("term", "show_label", "resolution", "transparent_bg",
                                                   "frame_x", "frame_z", "frame_zoom")}
        self.saved_r = {"engine": r.engine, "filepath": r.filepath, "use_freestyle": r.use_freestyle,
                        "resolution_x": r.resolution_x, "resolution_y": r.resolution_y}
        self.saved_world = tuple(scene.world.color) if scene.world is not None else None
        self.saved_pose = fig.snapshot()
        self.hidden = []
        for root in all_figures():
            if root != fig.root:
                for o in figure_objects(root):
                    if not o.hide_render:
                        o.hide_render = True
                        self.hidden.append(o)
        for a in all_arrows():
            if "asl_take_arrow" in a:
                continue
            for o in [a] + arrow_heads(a):
                if not o.hide_render:
                    o.hide_render = True
                    self.hidden.append(o)
        self.tmp = tempfile.mkdtemp(prefix="asl_gif_")
        s.transparent_bg = False
        s.term = self.text
        s.show_label = self.label and bool(self.text.strip())
        s.resolution = size
        # frame the whole movement, the face and the arrows so nothing leaves the picture
        n = max(2, int((take.end - take.start) * 15))
        times = [take.start + (take.end - take.start) * i / (n - 1) for i in range(n)]
        box = None
        for t in times[::max(1, n // 24)] + [times[-1]]:
            fig.apply(*take.pose_at(t))
            update_take_arrows(take, fig, t)
            b = sign_bounds(include_head=True)
            if b:
                box = b if box is None else (min(box[0], b[0]), max(box[1], b[1]), min(box[2], b[2]), max(box[3], b[3]))
        ab = take_arrow_bounds(take, fig, times[::max(1, n // 24)] + [times[-1]]) if take.arrows else None
        if ab and box:
            box = (min(box[0], ab[0]), max(box[1], ab[1]), min(box[2], ab[2]), max(box[3], ab[3]))
        fit_frame(scene, include_head=True, margin=0.08, box=box)
        apply_render_settings(scene)
        r.resolution_x = r.resolution_y = size
        if s.show_label:
            # The label strip is rendered once with the card renderer (white strip, crisp text,
            # same as the cards) and laid over every frame; the strip hides everything behind it.
            fig.apply(*take.pose_at(times[0]))
            update_take_arrows(take, fig, times[0])
            self.band = _render_array(scene, os.path.join(self.tmp, "label.png"))
            s.show_label = False
            update_label(scene)
        if self.quality == 'FAST':
            _use_workbench(scene)
        r.resolution_x = r.resolution_y = size
        return self

    def frame(self, t, name):
        """Render the take at time t; returns an RGB array."""
        self.fig.apply(*self.take.pose_at(t))
        update_take_arrows(self.take, self.fig, t)
        return _render_array(self.scene, os.path.join(self.tmp, name))

    def png(self, t):
        """Render the take at time t; returns PNG file bytes."""
        self.fig.apply(*self.take.pose_at(t))
        update_take_arrows(self.take, self.fig, t)
        r = self.scene.render
        path = os.path.join(self.tmp, "preview.png")
        r.filepath = path
        r.image_settings.file_format = 'PNG'
        r.image_settings.color_mode = 'RGB'
        r.image_settings.color_depth = '8'
        _render_op(self.scene)
        with open(path, "rb") as fh:
            return fh.read()

    def __exit__(self, *exc):
        scene, s, r = self.scene, self.scene.asl, self.scene.render
        for k, v in self.saved_s.items():
            setattr(s, k, v)
        for k, v in self.saved_r.items():
            setattr(r, k, v)
        if self.saved_world is not None and scene.world is not None:
            scene.world.color = self.saved_world
        apply_render_settings(scene)          # also brings the backdrop back
        update_framing(scene)
        for o in self.hidden:
            o.hide_render = False
        self.fig.apply(*self.saved_pose)
        try:
            for fn in os.listdir(self.tmp):
                os.remove(os.path.join(self.tmp, fn))
            os.rmdir(self.tmp)
        except OSError:
            pass
        return False


def render_take(scene, fig, take, text, category, fps, size, quality, progress=None):
    """Render the trimmed take to animated GIFs. Returns a result dict."""
    import numpy as np
    n = int(round((take.end - take.start) * fps)) + 1
    if n < 2:
        raise ValueError("The trimmed recording is too short. Move the end handle later.")
    n = min(n, 400)
    times = [take.start + i / fps for i in range(n)]
    with _TakeRender(scene, fig, take, text, size, quality) as tr:
        frames = []
        for i, t in enumerate(times):
            frames.append(tr.frame(t, "f%04d.png" % i))
            if progress:
                progress("render", i + 1, n)
        if progress:
            progress("encode", 0, 1)
        delays = gif_delays(len(frames), fps)
        quiz = encode_gif(frames, delays)
        labeled = quiz
        if tr.band is not None:
            rows = int(math.ceil((scene.asl.label_size + 0.07) * size))
            labeled = encode_gif([np.concatenate([f[:-rows], tr.band[-rows:]], 0) for f in frames], delays)
        return {"labeled": labeled, "quiz": quiz, "frames": len(frames)}


def render_take_preview(scene, fig, take, t, size=320):
    """One quick picture of the take at time t, framed like the GIF (for the web app)."""
    with _TakeRender(scene, fig, take, "", size, 'FAST', label=False) as tr:
        return tr.png(t)


def save_motion_card(scene, take, text, category, fps, result):
    """Write the GIFs and the take into the cards folder and list it in manifest.json."""
    out_dir = cards_dir(scene)
    if out_dir is None:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slugify(text) if text.strip() else f"recording-{stamp}"
    card_id = f"{category}-{slug}"
    rel = {"image": f"motion/{card_id}.gif", "quizImage": f"motion/quiz/{card_id}.gif",
           "recipe": f"motion/{card_id}.json"}
    os.makedirs(os.path.join(out_dir, "motion", "quiz"), exist_ok=True)
    with open(os.path.join(out_dir, rel["image"]), "wb") as fh:
        fh.write(result["labeled"])
    with open(os.path.join(out_dir, rel["quizImage"]), "wb") as fh:
        fh.write(result["quiz"])
    with open(os.path.join(out_dir, rel["recipe"]), "w", encoding="utf-8") as fh:
        json.dump(take.to_dict(fps, text, category), fh, separators=(",", ":"))
    replaced = False
    if text.strip():
        man_path = os.path.join(out_dir, "manifest.json")
        manifest = {"cards": []}
        if os.path.exists(man_path):
            try:
                with open(man_path, encoding="utf-8") as fh:
                    manifest = json.load(fh)
            except (OSError, ValueError):
                pass
        cards = manifest.get("cards", [])
        replaced = any(c.get("id") == card_id for c in cards)
        cards = [c for c in cards if c.get("id") != card_id]
        cards.append({"id": card_id, "term": text.strip(), "category": category,
                      "notes": "Recorded with live tracking.", "animated": True, **rel,
                      "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        cards.sort(key=lambda c: (c["category"], c["term"].lower()))
        manifest["cards"] = cards
        with open(man_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
    return {"card_id": card_id, "path": os.path.join(out_dir, rel["image"]), "replaced": replaced,
            "in_app": bool(text.strip())}


# --- The live session (one per Blender) ----------------------------------------------

def _tag_redraw():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class LiveSession:
    def __init__(self):
        self.link = LiveLink()
        self.rt = Retargeter()
        self.fig = None
        self.mode = "off"            # off, live, countdown, recording, review, playing, rendering
        self.take = None
        self.countdown_end = 0.0
        self.play_t0 = 0.0
        self.play_from = 0.0
        self.last_msg = None
        self.last_playhead = 0.0
        self.last_state = 0.0
        self.seen_any = False
        self.last_seen_t = 0.0
        self.error = ""
        self.status = ""
        self.last_gif = ""
        self.progress = None
        self._fps_times = collections.deque(maxlen=60)
        self.apply_ms = 0.0
        self._trim_guard = False
        self.view_t = 0.0

    @property
    def running(self):
        return self.link._running

    @property
    def fps(self):
        t = self._fps_times
        if len(t) < 2 or t[-1] - t[0] <= 0:
            return 0.0
        return (len(t) - 1) / (t[-1] - t[0])

    # --- start / stop -----------------------------------------------------------
    def start(self, port):
        self.link.start(port)
        self.mode = "live"
        self.error = ""
        if not bpy.app.timers.is_registered(_live_tick):
            bpy.app.timers.register(_live_tick, first_interval=0.01, persistent=True)

    def stop(self):
        self.link.stop()
        self.mode = "off"
        if bpy.app.timers.is_registered(_live_tick):
            bpy.app.timers.unregister(_live_tick)

    def ensure_figure(self, scene):
        if self.fig is not None and self.fig.valid():
            return self.fig
        self.fig = None
        root = solid_figure(scene) or main_figure(scene)
        if root is None:
            self.error = "Click New Card Scene in Blender first."
            return None
        self.fig = LiveFigure(root)
        self.rt.prev.clear()
        self.rt.skel = ModelSkeleton(root)
        for side in SIDES:
            tb = self.fig.joints.get(f"{side}_thumb_base")
            wr = self.fig.joints.get(f"{side}_wrist")
            if tb:
                self.rt.thumb_rest[side] = tb.matrix_parent_inverse.to_3x3().normalized()
            if wr:
                self.rt.wrist_rest_z[side] = wr.matrix_parent_inverse.to_3x3().to_euler().z
        return self.fig

    # --- messages ---------------------------------------------------------------
    def state(self):
        now = time.perf_counter()
        take = None
        if self.take is not None:
            take = {"duration": round(self.take.duration, 3), "start": round(self.take.start, 3),
                    "end": round(self.take.end, 3), "samples": len(self.take.samples),
                    "t0_ms": round(self.take.t0 * 1000, 2), "arrows": self.take.arrows}
        lv = bpy.context.scene.asl_live
        return {"type": "state", "mode": self.mode,
                "countdown": round(max(0.0, self.countdown_end - now), 2) if self.mode == "countdown" else 0,
                "take": take, "calibrating": self.rt.calibrating, "calibrated": self.rt.calib is not None,
                "figure": self.fig.name if self.fig else None, "error": self.error,
                "settings": {"mirror": lv.mirror, "auto_stop": lv.auto_stop, "countdown": lv.countdown,
                             "use_depth": lv.use_depth, "fov": lv.camera_fov},
                "progress": self.progress, "last_gif": self.last_gif}

    def send_state(self):
        self.last_state = time.perf_counter()
        self.link.send(self.state())
        _tag_redraw()

    def hello(self):
        self.link.send({"type": "hello", "app": "asl-flashcard-editor", "protocol": LIVE_PROTOCOL,
                        "addon": ".".join(map(str, bl_info["version"])), "blender": bpy.app.version_string})

    def handle(self, msg, scene):
        typ = msg.get("type")
        lv = scene.asl_live
        if typ == "_connected":
            self.ensure_figure(scene)
            self.hello()
            if self.rt.calib is None:
                self.rt.start_calibration()
            self.send_state()
        elif typ == "_disconnected":
            if self.mode in ("recording", "countdown"):
                self.stop_recording()
            _tag_redraw()
        elif typ == "hello":
            self.hello()
            self.send_state()
        elif typ == "settings":
            for k in ("mirror", "auto_stop", "use_depth", "track_arms", "track_hands", "track_head",
                      "track_face", "track_torso"):
                if k in msg:
                    setattr(lv, k, bool(msg[k]))
            if "fov" in msg:
                lv.camera_fov = _clamp(float(msg["fov"]), 30.0, 120.0)
            if "countdown" in msg:
                lv.countdown = int(msg["countdown"])
            self.send_state()
        elif typ == "calibrate":
            self.rt.start_calibration()
            self.send_state()
        elif typ == "record":
            act = msg.get("action")
            if act == "start":
                self.start_countdown(msg.get("countdown", lv.countdown))
            elif act == "stop":
                self.stop_recording()
            elif act == "retry":
                self.drop_take()
                self.start_countdown(msg.get("countdown", lv.countdown))
            elif act == "discard":
                self.drop_take()
                self.mode = "live"
                self.send_state()
        elif typ == "seek" and self.take is not None:
            if self.mode == "playing":
                self.mode = "review"
            if self.mode == "review":
                self.show_take_at(float(msg.get("t", 0.0)))
        elif typ == "trim" and self.take is not None:
            self.set_trim(msg.get("start", self.take.start), msg.get("end", self.take.end), scene)
            if self.take.arrows:
                build_take_arrows(scene, self.fig, self.take)
                self.show_take_at(self.view_t)
        elif typ == "play" and self.take is not None and self.mode in ("review", "playing"):
            self.play()
        elif typ == "pause" and self.mode == "playing":
            self.mode = "review"
            self.send_state()
        elif typ == "render_gif":
            lv.gif_text = str(msg.get("text", lv.gif_text))[:120]
            if msg.get("category") in ("word", "phrase"):
                lv.gif_category = msg["category"]
            if str(msg.get("fps")) in ("10", "15", "20", "30"):
                lv.gif_fps = str(msg["fps"])
            if str(msg.get("size")) in ("360", "480", "540", "720"):
                lv.gif_size = str(msg["size"])
            if msg.get("quality") in ("FAST", "CARD"):
                lv.gif_quality = msg["quality"]
            self.make_gif(scene)
        elif typ in ("arrow_add", "arrow_update", "arrow_delete", "arrows_clear") and self.take is not None:
            self.edit_arrows(typ, msg, scene)
        elif typ == "preview" and self.take is not None and self.mode == "review":
            self.send_preview(scene, msg.get("t"), int(_clamp(int(msg.get("size", 320)), 160, 720)))
        elif typ == "snapshot":
            if self.ensure_figure(scene):
                self.fig.to_sliders()
                self.link.send({"type": "notice", "message": "Pose copied to the figure's sliders in Blender."})

    # --- live frames --------------------------------------------------------------
    def configure(self, lv):
        rt = self.rt
        rt.mirror = lv.mirror
        rt.use_depth = lv.use_depth
        rt.fov = lv.camera_fov
        rt.groups = [g for g in TRACK_GROUPS if getattr(lv, "track_" + g)]
        sk = rt.skel
        if sk is not None and time.perf_counter() - sk.refreshed > 1.0:
            sk.refresh()             # follows body shape and sculpting changes, and slider poses

    def on_frame(self, msg, scene):
        t_start = time.perf_counter()
        self._fps_times.append(t_start)
        if self.mode in ("live", "countdown", "recording") and self.ensure_figure(scene):
            lv = scene.asl_live
            self.configure(lv)
            was_cal = self.rt.calibrating
            rots, face, seen = self.rt.solve(msg)
            groups = [g for g in TRACK_GROUPS if getattr(lv, "track_" + g)]
            rots = {k: v for k, v in rots.items() if any(TRACK_GROUPS[g](k) for g in groups)}
            self.fig.apply(rots, face if lv.track_face else None)
            if self.mode == "recording":
                t = float(msg.get("t", 0.0)) / 1000.0
                r, f = self.fig.snapshot()
                self.take.add(t, r, f, seen, msg)
                if seen:
                    self.seen_any, self.last_seen_t = True, t
                if self.take.duration >= MAX_TAKE_SECONDS:
                    self.stop_recording()
                elif lv.auto_stop and self.seen_any and t - self.last_seen_t > 1.0 and self.take.duration > 1.0:
                    self.stop_recording()
            if was_cal and not self.rt.calibrating:
                self.send_state()
            _tag_redraw()
        self.apply_ms = 0.8 * self.apply_ms + 0.2 * (time.perf_counter() - t_start) * 1000
        ack = {"type": "ack", "id": msg.get("id"), "ms": round((time.perf_counter() - t_start) * 1000, 2)}
        if self.rt.use_depth:
            ack["touch"] = {sd: round(v.get("touch", 0.0), 2) for sd, v in self.rt.depth.info.items()}
        self.link.send(ack)

    # --- recording ----------------------------------------------------------------
    def drop_take(self):
        self.take = None
        clear_take_arrows()

    def edit_arrows(self, typ, msg, scene):
        tk = self.take
        if typ == "arrow_add":
            nid = max([a["id"] for a in tk.arrows] + [0]) + 1
            spec = clean_take_arrow(msg.get("arrow") or {})
            spec["id"] = nid
            tk.arrows.append(spec)
        elif typ == "arrow_update":
            for i, a in enumerate(tk.arrows):
                if a["id"] == msg.get("id"):
                    tk.arrows[i] = clean_take_arrow(msg.get("arrow") or {}, a)
        elif typ == "arrow_delete":
            tk.arrows = [a for a in tk.arrows if a["id"] != msg.get("id")]
        else:
            tk.arrows = []
        if self.ensure_figure(scene):
            build_take_arrows(scene, self.fig, tk)
            self.show_take_at(self.view_t)
        self.link.send({"type": "arrows", "arrows": tk.arrows})
        if msg.get("preview") and self.mode == "review":
            self.send_preview(scene, msg.get("t"), int(msg.get("size", 320)))

    def send_preview(self, scene, t, size=320):
        if self.ensure_figure(scene) is None:
            return
        tk = self.take
        t = self.view_t if t is None else tk.start + _clamp(float(t), 0.0, tk.end - tk.start)
        try:
            png = render_take_preview(scene, self.fig, tk, t, size)
        except Exception as e:
            traceback.print_exc()
            self.link.send({"type": "notice", "message": f"Preview failed: {e}"})
            return
        finally:
            self.show_take_at(self.view_t)
        self.link.send({"type": "preview_ready", "bytes": len(png), "t": round(t - tk.start, 3)})
        self.link.send_bytes(png)

    def start_countdown(self, secs):
        if self.ensure_figure(bpy.context.scene) is None:
            self.send_state()
            return
        self.error = ""
        self.drop_take()
        secs = max(0.0, float(secs))
        if secs <= 0:
            self.begin_recording()
            return
        self.mode = "countdown"
        self.countdown_end = time.perf_counter() + secs
        self.send_state()

    def begin_recording(self):
        self.take = Take()
        rt = self.rt
        self.take.setup = {"mirror": rt.mirror, "use_depth": rt.use_depth, "fov": rt.fov,
                           "groups": list(rt.groups) if rt.groups is not None else None,
                           "calib": rt.calib, "thumb_rest": dict(rt.thumb_rest),
                           "wrist_rest_z": dict(rt.wrist_rest_z), "hand_k": rt.depth.hand_k}
        self.take.body_sig = rt.skel.sig if rt.skel else None
        self.seen_any = False
        self.mode = "recording"
        self.send_state()

    def stop_recording(self):
        if self.mode == "countdown":
            self.mode = "live"
            self.take = None
        elif self.mode == "recording":
            take = self.take
            if take is None or len(take.samples) < 3:
                self.take = None
                self.mode = "live"
                self.error = "Nothing was recorded. Is the web app sending camera frames?"
            else:
                take.auto_trim()
                self.mode = "review"
                self._sync_trim_props(bpy.context.scene)
                self.show_take_at(take.start)
        self.send_state()

    def follow_body(self, force=False):
        """If the body shape changed since the take was solved, solve it again."""
        tk, sk = self.take, self.rt.skel
        if tk is None or sk is None:
            return False
        now = time.perf_counter()
        if not force and now - sk.refreshed < 0.5:
            return False
        sk.refresh()
        if tk.body_sig is not None and sk.sig != tk.body_sig:
            if tk.resolve(sk):
                self.status = "Recording updated for the new body shape."
                if tk.arrows and self.fig is not None:
                    build_take_arrows(bpy.context.scene, self.fig, tk)
                return True
        return False

    def show_take_at(self, t):
        if self.take is None or self.fig is None or not self.fig.valid():
            return
        self.follow_body()
        self.view_t = t
        self.fig.apply(*self.take.pose_at(t))
        if self.take.arrows:
            update_take_arrows(self.take, self.fig, t)
        _tag_redraw()

    def set_trim(self, start, end, scene):
        tk = self.take
        start = _clamp(float(start), 0.0, tk.duration)
        end = _clamp(float(end), 0.0, tk.duration)
        if end - start < 0.1:
            if start != tk.start:
                start = max(0.0, end - 0.1)
            else:
                end = min(tk.duration, start + 0.1)
        moved_end = abs(end - tk.end) > abs(start - tk.start)
        tk.start, tk.end = start, end
        self._sync_trim_props(scene)
        if self.mode == "playing":
            self.mode = "review"
        self.show_take_at(tk.end if moved_end else tk.start)
        self.send_state()

    def _sync_trim_props(self, scene):
        self._trim_guard = True
        try:
            scene.asl_live.trim_start = self.take.start
            scene.asl_live.trim_end = self.take.end
        finally:
            self._trim_guard = False

    def play(self):
        self.follow_body(force=True)
        self.mode = "playing"
        self.play_t0 = time.perf_counter()
        self.play_from = self.take.start
        self.send_state()

    def update_playback(self, now):
        pos = self.play_from + (now - self.play_t0)
        if pos >= self.take.end:
            self.show_take_at(self.take.end)
            self.mode = "review"
            self.link.send({"type": "playhead", "t": round(self.take.end, 3)})
            self.send_state()
            return
        self.show_take_at(pos)
        if now - self.last_playhead > 0.04:
            self.last_playhead = now
            self.link.send({"type": "playhead", "t": round(pos, 3)})

    def make_gif(self, scene):
        if self.take is None or self.ensure_figure(scene) is None:
            self.error = "Record a sign first."
            self.send_state()
            return False
        lv = scene.asl_live
        text = lv.gif_text.strip()
        fps, size = int(lv.gif_fps), int(lv.gif_size)
        prev_mode = self.mode
        self.mode = "rendering"
        self.error = ""

        def progress(stage, done, total):
            self.progress = {"stage": stage, "done": done, "total": total}
            self.link.send({"type": "render_progress", **self.progress})
            time.sleep(0)            # let the network thread run

        try:
            self.follow_body(force=True)
            progress("render", 0, int(round((self.take.end - self.take.start) * fps)) + 1)
            res = render_take(scene, self.fig, self.take, text, lv.gif_category, fps, size, lv.gif_quality, progress)
            saved = save_motion_card(scene, self.take, text, lv.gif_category, fps, res)
            self.last_gif = saved["path"] if saved else ""
            info = {"type": "gif_ready", "bytes": len(res["labeled"]), "frames": res["frames"],
                    "name": (saved["card_id"] if saved else (slugify(text) or "recording")) + ".gif",
                    "saved": saved}
            self.link.send(info)
            self.link.send_bytes(res["labeled"])
            if saved is None:
                self.status = "GIF made. Set the Cards folder to save it into the app."
            elif not saved["in_app"]:
                self.status = "GIF saved. Add a description to also add it to the app."
            else:
                self.status = ("Replaced" if saved["replaced"] else "Added") + f" card {saved['card_id']}."
            return True
        except Exception as e:
            traceback.print_exc()
            self.error = f"Could not make the GIF: {e}"
            return False
        finally:
            self.progress = None
            self.mode = prev_mode if prev_mode in ("review", "playing") else "review"
            if self.mode == "playing":
                self.mode = "review"
            self.send_state()

    # --- the timer --------------------------------------------------------------
    def tick(self):
        scene = bpy.context.scene
        n = 0
        while self.link.commands and n < 50:
            self.handle(self.link.commands.popleft(), scene)
            n += 1
        now = time.perf_counter()
        if self.mode == "countdown":
            if now >= self.countdown_end:
                self.begin_recording()
            elif now - self.last_state > 0.25:
                self.send_state()
        msg = self.link.latest
        if msg is not None and msg is not self.last_msg:
            self.last_msg = msg
            self.on_frame(msg, scene)
        if self.mode == "playing" and self.take is not None:
            self.update_playback(now)


LIVE = LiveSession()


def _live_tick():
    if not LIVE.running:
        return None
    try:
        LIVE.tick()
    except Exception as e:
        traceback.print_exc()
        LIVE.error = str(e)
    return 0.005


def _update_trim(self, context):
    if LIVE._trim_guard or LIVE.take is None:
        return
    LIVE.set_trim(self.trim_start, self.trim_end, context.scene)


class ASLLiveSettings(PropertyGroup):
    port: IntProperty(name="Port", default=8766, min=1024, max=65535,
                      description="Must match the port in the web app's Record tab")
    mirror: BoolProperty(name="Mirror me", default=False,
                         description="The figure moves like your reflection (its right hand follows your left)")
    track_arms: BoolProperty(name="Arms", default=True)
    track_hands: BoolProperty(name="Hands", default=True)
    track_head: BoolProperty(name="Head", default=True)
    track_face: BoolProperty(name="Face", default=True)
    track_torso: BoolProperty(name="Body", default=True)
    use_depth: BoolProperty(name="Place hands by position (face and body contact)", default=True,
                            description="Work out how far away each hand is and place it relative to the face "
                                        "and body, so a hand touching your nose touches the figure's nose. "
                                        "Off: copy only the directions of the arms")
    camera_fov: FloatProperty(name="Camera field of view", default=60.0, min=30.0, max=120.0, precision=0,
                              description="Horizontal angle the webcam sees, in degrees. Most webcams are 60-78. "
                                          "If hands land in front of the face, raise it; behind, lower it")
    auto_stop: BoolProperty(name="Stop when hands drop", default=True,
                            description="Stop recording 1 second after both hands leave the camera view")
    countdown: IntProperty(name="Countdown", default=3, min=0, max=10, subtype='TIME')
    trim_start: FloatProperty(name="Start", default=0.0, min=0.0, precision=2, step=5, unit='TIME_ABSOLUTE',
                              update=_update_trim)
    trim_end: FloatProperty(name="End", default=0.0, min=0.0, precision=2, step=5, unit='TIME_ABSOLUTE',
                            update=_update_trim)
    gif_text: StringProperty(name="Description", default="",
                             description="The English meaning; shown on the GIF and used as the card name")
    gif_category: EnumProperty(name="Category", items=[("word", "Word", ""), ("phrase", "Phrase", "")],
                               default="phrase")
    gif_fps: EnumProperty(name="Frame rate", items=[("10", "10 fps", ""), ("15", "15 fps", ""),
                                                     ("20", "20 fps", ""), ("30", "30 fps", "")], default="15")
    gif_size: EnumProperty(name="Size", items=[("360", "360 px", ""), ("480", "480 px", ""),
                                               ("540", "540 px", ""), ("720", "720 px", "")], default="480")
    gif_quality: EnumProperty(name="Look", items=[("FAST", "Fast", "Simple shading with outlines; renders quickly"),
                                                  ("CARD", "Card look", "Same renderer as the cards; slower")],
                              default="FAST")


class ASL_OT_live_toggle(Operator):
    bl_idname = "asl.live_toggle"
    bl_label = "Start Live Link"
    bl_description = "Listen for the web app's Record tab on this computer (127.0.0.1 only)"

    def execute(self, context):
        if LIVE.running:
            LIVE.stop()
        else:
            try:
                LIVE.start(context.scene.asl_live.port)
            except OSError as e:
                self.report({'ERROR'}, f"Could not open port {context.scene.asl_live.port}: {e}")
                return {'CANCELLED'}
            LIVE.ensure_figure(context.scene)
        return {'FINISHED'}


class ASL_OT_live_action(Operator):
    bl_idname = "asl.live_action"
    bl_label = "Live Action"
    bl_options = {'REGISTER'}
    action: EnumProperty(items=[(k, k, "") for k in ("calibrate", "record", "stop", "retry", "discard",
                                                      "play", "pause", "gif", "snapshot",
                                                      "arrow_path", "arrows_clear")])

    @classmethod
    def description(cls, context, props):
        return {"calibrate": "Face the camera, relax your face and hold still for a moment",
                "record": "Count down, then record the figure's movements",
                "stop": "Stop recording", "retry": "Throw this take away and record again",
                "discard": "Throw this take away and go back to live tracking",
                "play": "Play the trimmed take", "pause": "Pause playback",
                "gif": "Render the trimmed take as an animated GIF and add it to the cards",
                "snapshot": "Copy the current pose into the figure's sliders so you can export it as a card",
                "arrow_path": "Add an arrow that traces the right index fingertip's movement "
                              "(adjust it in the web app's Review panel)",
                "arrows_clear": "Remove all arrows from this recording",
                }.get(props.action, "")

    def execute(self, context):
        a = self.action
        scene = context.scene
        lv = scene.asl_live
        if a == "calibrate":
            LIVE.rt.start_calibration()
        elif a == "record":
            LIVE.start_countdown(lv.countdown)
        elif a == "stop":
            LIVE.stop_recording()
        elif a == "retry":
            LIVE.drop_take()
            LIVE.start_countdown(lv.countdown)
        elif a == "discard":
            LIVE.drop_take()
            LIVE.mode = "live"
        elif a in ("arrow_path", "arrows_clear") and LIVE.take is not None:
            LIVE.edit_arrows("arrow_add" if a == "arrow_path" else "arrows_clear", {"arrow": {"kind": "path"}}, scene)
        elif a == "play" and LIVE.take is not None:
            LIVE.play()
        elif a == "pause" and LIVE.mode == "playing":
            LIVE.mode = "review"
        elif a == "gif":
            if not LIVE.make_gif(scene):
                self.report({'ERROR'}, LIVE.error or "Could not make the GIF")
                return {'CANCELLED'}
            self.report({'INFO'}, LIVE.status)
        elif a == "snapshot":
            if LIVE.ensure_figure(scene) is None:
                self.report({'ERROR'}, LIVE.error)
                return {'CANCELLED'}
            LIVE.fig.to_sliders()
        LIVE.send_state()
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
        if outdated_figures():
            box = lay.box()
            box.alert = True
            box.label(text="Figures from an older version", icon='ERROR')
            box.operator("asl.upgrade_figures", icon='FILE_REFRESH')
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
        row = lay.row(align=True)
        row.operator("asl.fit_frame", text="Fit Hands", icon='ZOOM_ALL').include_head = False
        row.operator("asl.fit_frame", text="Fit Hands + Face", icon='USER').include_head = True
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
            kind = root.get("asl_role") or ("ghost" if root.asl_fig.is_ghost else "solid")
            label = f"{root.name}  ({kind})"
            op = col.operator("asl.select_figure", text=label,
                              icon='RADIOBUT_ON' if root == cur else 'RADIOBUT_OFF', depress=root == cur)
            op.name = root.name
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


class ASL_PT_live(_ASLPanel, Panel):
    bl_label = "Live Tracking & Recording"

    @classmethod
    def poll(cls, context):
        return _get("ASL_Camera") is not None

    def draw(self, context):
        lv = context.scene.asl_live
        lay = self.layout
        if not LIVE.running:
            row = lay.row(align=True)
            row.prop(lv, "port")
            lay.operator("asl.live_toggle", text="Start Live Link", icon='LINKED')
            lay.label(text="Then open the Record tab in the web app.", icon='INFO')
            return
        box = lay.box()
        if LIVE.link.connected:
            box.label(text=f"Web app connected  |  {LIVE.fps:.0f} fps  |  {LIVE.apply_ms:.1f} ms per frame",
                      icon='CHECKMARK')
        else:
            box.label(text=f"Waiting for the web app on port {LIVE.link.port}...", icon='TIME')
        if LIVE.fig:
            box.label(text=f"Moving: {LIVE.fig.name}")
        if LIVE.rt.calibrating:
            box.label(text="Calibrating: face the camera and hold still", icon='VIEW_CAMERA')
        touching = [("right" if sd == "R" else "left") for sd, v in LIVE.rt.depth.info.items() if v.get("touch", 0) > 0.5]
        if touching and LIVE.mode in ("live", "recording"):
            box.label(text=f"Touching: {' and '.join(touching)} hand", icon='HAND')
        if LIVE.error:
            box.label(text=LIVE.error, icon='ERROR')
        row = lay.row(align=True)
        row.operator("asl.live_action", text="Calibrate", icon='PIVOT_CURSOR').action = "calibrate"
        row.operator("asl.live_toggle", text="Stop Link", icon='UNLINKED')
        lay.prop(lv, "mirror")
        lay.prop(lv, "use_depth")
        if lv.use_depth:
            lay.prop(lv, "camera_fov")
        grid = lay.grid_flow(columns=3, align=True)
        for k in ("arms", "hands", "head", "face", "torso"):
            grid.prop(lv, "track_" + k, toggle=True)

        box = lay.box()
        box.label(text="Record a sign", icon='REC')
        mode = LIVE.mode
        if mode == "live":
            row = box.row(align=True)
            row.scale_y = 1.4
            row.operator("asl.live_action", text="Record Sign", icon='REC').action = "record"
            row = box.row(align=True)
            row.prop(lv, "countdown")
            row.prop(lv, "auto_stop", text="Auto-stop")
            box.operator("asl.live_action", text="Copy Pose to Sliders", icon='COPYDOWN').action = "snapshot"
        elif mode == "countdown":
            left = max(0.0, LIVE.countdown_end - time.perf_counter())
            box.label(text=f"Get ready... {math.ceil(left)}")
            box.operator("asl.live_action", text="Cancel", icon='X').action = "stop"
        elif mode == "recording":
            box.alert = True
            dur = LIVE.take.duration if LIVE.take else 0.0
            box.label(text=f"Recording  {dur:.1f} s", icon='REC')
            row = box.row()
            row.scale_y = 1.4
            row.operator("asl.live_action", text="Stop", icon='SNAP_FACE').action = "stop"
        elif mode in ("review", "playing", "rendering") and LIVE.take is not None:
            tk = LIVE.take
            box.label(text=f"Take: {tk.duration:.1f} s, using {tk.start:.2f}-{tk.end:.2f} s")
            col = box.column(align=True)
            col.prop(lv, "trim_start", slider=False)
            col.prop(lv, "trim_end", slider=False)
            row = box.row(align=True)
            if mode == "playing":
                row.operator("asl.live_action", text="Pause", icon='PAUSE').action = "pause"
            else:
                row.operator("asl.live_action", text="Play", icon='PLAY').action = "play"
            row.operator("asl.live_action", text="Retry", icon='FILE_REFRESH').action = "retry"
            row.operator("asl.live_action", text="Discard", icon='TRASH').action = "discard"
            arow = box.row(align=True)
            arow.label(text=f"Arrows on the GIF: {len(tk.arrows)}", icon='FORWARD')
            arow.operator("asl.live_action", text="", icon='ADD').action = "arrow_path"
            arow.operator("asl.live_action", text="", icon='X').action = "arrows_clear"
            box.prop(lv, "gif_text")
            row = box.row(align=True)
            row.prop(lv, "gif_category", text="")
            row.prop(lv, "gif_quality", text="")
            row = box.row(align=True)
            row.prop(lv, "gif_fps", text="")
            row.prop(lv, "gif_size", text="")
            r = box.row()
            r.scale_y = 1.3
            r.operator("asl.live_action", text="Make GIF", icon='RENDER_ANIMATION').action = "gif"
            if LIVE.progress:
                p = LIVE.progress
                box.label(text=f"{p['stage'].capitalize()} {p['done']}/{p['total']}")
        if LIVE.status:
            lay.label(text=LIVE.status, icon='INFO')


class ASL_PT_motion(_ASLPanel, Panel):
    bl_label = "Start & End Position"

    @classmethod
    def poll(cls, context):
        return _get("ASL_Camera") is not None

    def draw(self, context):
        s = context.scene.asl
        lay = self.layout
        start = role_figure("start")
        col = lay.column(align=True)
        col.label(text="1. Pose where the sign starts")
        col.operator("asl.set_start", icon='TRIA_RIGHT')
        col.separator()
        col.label(text="2. Pose where it ends")
        row = col.row(align=True)
        row.enabled = start is not None
        row.operator("asl.set_end", icon='TRIA_RIGHT_BAR')
        lay.prop(s, "start_opacity", slider=True)
        lay.prop(s, "auto_arrow")
        lay.prop(s, "ghost_both_arms", text="Start shows both arms")
        if start is not None:
            cur = main_figure(context.scene)
            row = lay.row(align=True)
            row.label(text="Sliders change:")
            row.operator("asl.edit_role", text="Start", depress=cur == start).role = "start"
            is_end = cur is not None and cur != start and not cur.asl_fig.is_ghost
            row.operator("asl.edit_role", text="End", depress=is_end).role = "end"
        box = lay.box()
        box.label(text="Movements with extra steps:")
        row = box.row(align=True)
        row.operator("asl.add_ghost", icon='GHOST_ENABLED')
        row.prop(s, "ghost_opacity", text="")


class ASL_PT_proportions(_ASLPanel, Panel):
    bl_label = "Proportions"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _get("ASL_Camera") is not None

    def draw(self, context):
        body = context.scene.asl_body
        lay = self.layout
        lay.prop(body, "neck", slider=True)
        lay.prop(body, "mirror")
        sides = ("R",) if body.mirror else SIDES
        for side in sides:
            suffix = " (both sides)" if body.mirror else (" (right)" if side == "R" else " (left)")
            box = lay.box()
            box.label(text="Thumb base position" + suffix, icon='HAND')
            col = box.column(align=True)
            for key in ("thumbpos_along", "thumbpos_across", "thumbpos_out"):
                col.prop(body, f"{side}_{key}", slider=True)
            box = lay.box()
            box.label(text="Shoulder position" + suffix, icon='ARMATURE_DATA')
            col = box.column(align=True)
            for key in ("shoulderpos_up", "shoulderpos_out", "shoulderpos_forward"):
                col.prop(body, f"{side}_{key}", slider=True)
        for side in sides:
            if not body.mirror:
                lay.label(text="Right hand" if side == "R" else "Left hand", icon='VIEW_PAN')
            for f in ("thumb",) + FINGERS:
                box = lay.box()
                box.label(text=f.capitalize() + (" (both hands)" if body.mirror else ""))
                col = box.column(align=True)
                for seg in SEGMENTS:
                    col.prop(body, f"{side}_{f}_{seg}", slider=True)
        lay.operator("asl.reset_proportions", icon='LOOP_BACK')


class ASL_OT_reset_proportions(Operator):
    bl_idname = "asl.reset_proportions"
    bl_label = "Reset Proportions"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        body_from_dict(context.scene.asl_body, {})
        refresh_all_figures()
        return {'FINISHED'}


class ASL_OT_reset_shape(Operator):
    bl_idname = "asl.reset_shape"
    bl_label = "Reset Body Shape"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        body = context.scene.asl_body
        for key, _, d, _, _ in SHAPE_SPECS:
            setattr(body, key, d)
        refresh_all_figures()
        return {'FINISHED'}


def _active_part(context):
    o = context.active_object
    if o is None or o.type != 'MESH' or "asl_part" not in o:
        return None, None
    return o, figure_of(o)


class ASL_OT_sculpt_part(Operator):
    bl_idname = "asl.sculpt_part"
    bl_label = "Sculpt Selected Part"
    bl_description = ("Reshape the selected body part with Blender's sculpt brushes. The tracker and "
                      "recordings follow the new shape (for example, hands touch a reshaped nose)")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        o, root = _active_part(context)
        if o is None:
            self.report({'WARNING'}, "First click the body part you want to reshape (head, nose, chest, hand...).")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        make_custom(o)
        o.hide_viewport = False
        for other in context.selected_objects:
            other.select_set(False)
        o.select_set(True)
        context.view_layer.objects.active = o
        middle = mirror_suffix(part_suffix(o)) is None and o.get("asl_part") in ("head", "torso")
        try:
            o.data.use_mirror_x = middle          # sculpt both sides of the face/body at once
        except AttributeError:
            pass
        bpy.ops.object.mode_set(mode='SCULPT')
        self.report({'INFO'}, "Sculpting " + part_suffix(o) + ". Click Done Sculpting when finished.")
        return {'FINISHED'}


class ASL_OT_sculpt_done(Operator):
    bl_idname = "asl.sculpt_done"
    bl_label = "Done Sculpting"
    bl_description = "Leave sculpt mode and give every figure in the scene the new shape"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        o = context.active_object
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        root = figure_of(o) if o else None
        if root is not None:
            share_custom_parts(root)
            refresh_all_figures()
        if LIVE.rt.skel is not None:
            LIVE.rt.skel.refresh()
        return {'FINISHED'}


class ASL_OT_reset_part(Operator):
    bl_idname = "asl.reset_part"
    bl_label = "Reset Part"
    bl_description = "Put the selected sculpted part back to its original shape"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        o, root = _active_part(context)
        if o is None or not o.get("asl_custom"):
            self.report({'WARNING'}, "Select a sculpted body part first.")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        orig = bpy.data.meshes.get(o.get("asl_orig_mesh", ""))
        targets = [bpy.data.objects.get(f"{r.name}.{part_suffix(o)}") for r in all_figures()]
        for t in targets:
            if t is None:
                continue
            if orig is not None:
                _assign_mesh(t, orig)
            for k in CUSTOM_PROPS:
                if k in t:
                    del t[k]
            if "asl_len" in t:
                t["asl_len"] = -1.0           # rebuilt at the current length on the next update
            t.scale = (1, 1, 1)
        refresh_all_figures()
        if LIVE.rt.skel is not None:
            LIVE.rt.skel.refresh()
        return {'FINISHED'}


class ASL_OT_mirror_part(Operator):
    bl_idname = "asl.mirror_part"
    bl_label = "Copy to Other Side"
    bl_description = "Give the matching part on the other side of the body a mirrored copy of this shape"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        o, root = _active_part(context)
        other = mirror_suffix(part_suffix(o)) if o else None
        if o is None or other is None:
            self.report({'WARNING'}, "Select a part on one side of the body (a hand, arm, eye, ear, cheek or eyebrow).")
            return {'CANCELLED'}
        t = bpy.data.objects.get(f"{root.name}.{other}")
        if t is None:
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        make_custom(o)
        make_custom(t)
        _assign_mesh(t, o.data)
        mirror_mesh_x(t.data)
        t.scale = o.scale
        share_custom_parts(root)
        refresh_all_figures()
        return {'FINISHED'}


class ASL_PT_shape(_ASLPanel, Panel):
    bl_label = "Body Shape & Sculpting"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _get("ASL_Camera") is not None

    def draw(self, context):
        body = context.scene.asl_body
        lay = self.layout
        if context.mode == 'SCULPT':
            o = context.active_object
            box = lay.box()
            box.label(text=f"Sculpting: {part_suffix(o) if o else ''}", icon='SCULPTMODE_HLT')
            box.label(text="Drag on the part. Ctrl = push in, Shift = smooth.")
            box.label(text="F = brush size, Shift+F = strength.")
            r = box.row()
            r.scale_y = 1.4
            r.operator("asl.sculpt_done", icon='CHECKMARK')
            return
        col = lay.column(align=True)
        for key, _, _, _, _ in SHAPE_SPECS:
            col.prop(body, key, slider=True)
        lay.operator("asl.reset_shape", icon='LOOP_BACK')
        box = lay.box()
        box.label(text="Sculpt a body part", icon='SCULPTMODE_HLT')
        o, root = _active_part(context)
        if o is None:
            box.label(text="Click a part of the figure first (nose, head, chest, palm...).")
        else:
            box.label(text=f"Selected: {part_suffix(o)}" + ("  (sculpted)" if o.get("asl_custom") else ""))
        r = box.row()
        r.scale_y = 1.3
        r.enabled = o is not None
        r.operator("asl.sculpt_part", icon='BRUSH_DATA')
        row = box.row(align=True)
        row.enabled = o is not None
        row.operator("asl.mirror_part", icon='MOD_MIRROR')
        row.operator("asl.reset_part", icon='LOOP_BACK')
        box.label(text="Live tracking and recordings follow these changes.", icon='INFO')


class ASL_PT_body(_ASLPanel, Panel):
    bl_label = "Body & Face"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return main_figure(context.scene) is not None

    def draw(self, context):
        fig = main_figure(context.scene).asl_fig
        _sliders(self.layout, fig, [p[0] for p in BODY_PARAMS])
        lay = self.layout
        box = lay.box()
        box.label(text="Eyebrows", icon='HIDE_OFF')
        _sliders(box, fig, ["brows", "brow_squeeze"])
        cols = box.row()
        for side, title in (("R", "Right brow"), ("L", "Left brow")):
            c = cols.column(align=True)
            c.label(text=title)
            for n, lab in zip(BROW_NODES, ("Inner end", "Middle", "Outer end")):
                c.prop(fig, f"brow_{n}_{side}", text=lab, slider=True)
        lay.label(text="Mouth, cheeks and eyes")
        _sliders(lay, fig, [k for k in FACE_KEYS if not k.startswith("brow")])
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
        lay.label(text="Free arrows: move with G; Tab to edit points.", icon='INFO')

        box = lay.box()
        box.label(text="Attach an arrow to a body part", icon='CONSTRAINT')
        row = box.row(align=True)
        row.prop(s, "tether_side", expand=True)
        box.prop(s, "tether_part", text="")
        row = box.row(align=True)
        row.prop(s, "tether_mode", expand=True)
        box.operator("asl.add_tethered_arrow", icon='ADD')

        a = context.active_object
        if a is not None and "asl_arrow" in a and a.type == 'CURVE' and a.asl_tether.enabled:
            t = a.asl_tether
            box = lay.box()
            box.label(text=f"Selected: {a.name}", icon='FORWARD')
            box.prop(t, "figure")
            row = box.row(align=True)
            row.prop(t, "side", expand=True)
            box.prop(t, "part")
            box.prop(t, "mode")
            box.prop(t, "axis")
            col = box.column(align=True)
            col.label(text="Position")
            col.prop(t, "offset", text="")
            col = box.column(align=True)
            col.label(text="Size")
            if t.mode == 'ALONG':
                col.prop(t, "length")
            else:
                col.prop(t, "radius")
                col.prop(t, "sweep", slider=True)
            col = box.column(align=True)
            col.label(text="Direction")
            col.prop(t, "spin", slider=True)
            col.prop(t, "tilt", slider=True)
            col.prop(t, "reverse", toggle=True)
            box.prop(t, "on_top")
            box.operator("asl.detach_arrow", icon='UNLINKED')

        lay.prop(s, "arrow_color")
        lay.prop(s, "arrow_width")
        row = lay.row(align=True)
        row.operator("asl.flip_arrows", icon='ARROW_LEFTRIGHT')
        row.operator("asl.delete_arrows", icon='TRASH', text="Delete")


class ASL_PT_framing(_ASLPanel, Panel):
    bl_label = "Framing & Look"

    @classmethod
    def poll(cls, context):
        return _get("ASL_Camera") is not None

    def draw(self, context):
        s = context.scene.asl
        lay = self.layout
        row = lay.row(align=True)
        row.operator("asl.fit_frame", text="Fit Hands", icon='ZOOM_ALL').include_head = False
        row.operator("asl.fit_frame", text="Fit Hands + Face", icon='USER').include_head = True
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
    ASLFigure, ASLBody, ASLTether, ASLSceneSettings, ASLLiveSettings, ASL_OT_upgrade_figures,
    ASL_OT_live_toggle, ASL_OT_live_action,
    ASL_OT_set_start, ASL_OT_set_end, ASL_OT_edit_role, ASL_OT_add_tethered_arrow, ASL_OT_detach_arrow,
    ASL_OT_reset_proportions, ASL_OT_reset_shape, ASL_OT_sculpt_part, ASL_OT_sculpt_done, ASL_OT_reset_part,
    ASL_OT_mirror_part,
    ASL_OT_new_scene, ASL_OT_clear_sign, ASL_OT_add_ghost, ASL_OT_delete_figure, ASL_OT_select_figure,
    ASL_OT_apply_handshape, ASL_OT_save_handshape, ASL_OT_copy_hand, ASL_OT_reset_group,
    ASL_OT_fit_frame, ASL_OT_add_arrow, ASL_OT_flip_arrows, ASL_OT_delete_arrows, ASL_OT_framing, ASL_OT_look_through, ASL_OT_export,
    ASL_OT_load_sign,
    ASL_PT_card, ASL_PT_live, ASL_PT_motion, ASL_PT_figures, ASL_PT_proportions, ASL_PT_shape, ASL_PT_body,
    _make_arm_panel("R"), _make_hand_panel("R"), _make_arm_panel("L"), _make_hand_panel("L"),
    ASL_PT_arrows, ASL_PT_framing,
]


@bpy.app.handlers.persistent
def _on_file_loaded(*_):
    try:
        if bpy.context.scene and hasattr(bpy.context.scene, "asl_body"):
            upgrade_old_figures(bpy.context.scene)
    except Exception as e:      # never block opening a file
        print("[ASL] could not update figures:", e)


def _upgrade_after_install():
    _on_file_loaded()
    return None                 # run once


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    if _on_file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_file_loaded)
    bpy.app.timers.register(_upgrade_after_install, first_interval=0.5)
    bpy.types.Object.asl_fig = PointerProperty(type=ASLFigure)
    bpy.types.Object.asl_tether = PointerProperty(type=ASLTether)
    bpy.types.Scene.asl = PointerProperty(type=ASLSceneSettings)
    bpy.types.Scene.asl_body = PointerProperty(type=ASLBody)
    bpy.types.Scene.asl_live = PointerProperty(type=ASLLiveSettings)


def unregister():
    try:
        LIVE.stop()
    except Exception:
        pass
    if _on_file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_file_loaded)
    del bpy.types.Scene.asl_live
    del bpy.types.Scene.asl_body
    del bpy.types.Scene.asl
    del bpy.types.Object.asl_tether
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
