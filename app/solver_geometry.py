from __future__ import annotations

"""Pure geometry helpers used by the 3D planning solver.

This module intentionally contains no database, FastAPI or project-state access.  Keeping these
operations isolated makes collision/orientation behavior independently testable and reduces the
surface area of the main application module.
"""

EPS=1e-9


def orientations(l,w,h,rotatable=True):
    """Shipping orientation: keep vertical axis upright; optional 90° rotation on the floor."""
    vals=[(float(l),float(w),float(h))]
    if rotatable and abs(float(l)-float(w))>EPS:
        vals.append((float(w),float(l),float(h)))
    return vals


def boxes_overlap(a,b):
    return (a["x"] < b["x"] + b["l"] and a["x"] + a["l"] > b["x"] and
            a["y"] < b["y"] + b["w"] and a["y"] + a["w"] > b["y"] and
            a["z"] < b["z"] + b["h"] and a["z"] + a["h"] > b["z"])


def overlap_area_xy(a,b):
    dx=max(0.0,min(a["x"]+a["l"],b["x"]+b["l"])-max(a["x"],b["x"]))
    dy=max(0.0,min(a["y"]+a["w"],b["y"]+b["w"])-max(a["y"],b["y"]))
    return dx*dy


def _shape_kind(box):
    s=str(box.get('category') or box.get('fitting_category') or box.get('source_name') or box.get('name') or '')
    if '弯头' in s: return 'elbow'
    if '三通' in s: return 'tee'
    return 'box'


def _subbox_abs(b,rx,ry,rz,rl,rw,rh):
    return {'x':b['x']+b['l']*rx,'y':b['y']+b['w']*ry,'z':b['z']+b['h']*rz,'l':b['l']*rl,'w':b['w']*rw,'h':b['h']*rh}


def _shape_primitives(b):
    k=_shape_kind(b)
    if k=='elbow':
        return [_subbox_abs(b,0.00,0.00,0.05,0.72,0.36,0.90),
                _subbox_abs(b,0.58,0.00,0.05,0.42,0.78,0.90)]
    if k=='tee':
        return [_subbox_abs(b,0.00,0.22,0.05,1.00,0.36,0.90),
                _subbox_abs(b,0.36,0.22,0.05,0.28,0.78,0.90)]
    return [b]


def occupied_overlap(a,b):
    if not boxes_overlap(a,b): return False
    # Shape nesting is allowed only within the same vertical layer.  Vertical stacking remains
    # conservative and is governed by support/top-load rules outside this module.
    if abs(float(a.get('z',0))-float(b.get('z',0)))>1e-6:
        return True
    if _shape_kind(a)=='box' and _shape_kind(b)=='box': return True
    for pa in _shape_primitives(a):
        for pb in _shape_primitives(b):
            if boxes_overlap(pa,pb): return True
    return False


def _point_inside_box(pt,b):
    x,y,z=pt
    return (b["x"]-1e-6 <= x < b["x"]+b["l"]-1e-6 and
            b["y"]-1e-6 <= y < b["y"]+b["w"]-1e-6 and
            b["z"]-1e-6 <= z < b["z"]+b["h"]-1e-6)


def point_inside_occupied_shape(pt,b):
    return any(_point_inside_box(pt,p) for p in _shape_primitives(b))


def shape_anchor_points(pl):
    k=_shape_kind(pl); z=pl['z']
    if k=='elbow':
        return [(pl['x']+pl['l']*0.05,pl['y']+pl['w']*0.46,z),
                (pl['x']+pl['l']*0.10,pl['y']+pl['w']*0.70,z)]
    if k=='tee':
        return [(pl['x']+pl['l']*0.06,pl['y']+pl['w']*0.62,z),
                (pl['x']+pl['l']*0.70,pl['y']+pl['w']*0.02,z)]
    return []
