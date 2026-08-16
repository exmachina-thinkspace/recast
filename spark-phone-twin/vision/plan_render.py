"""Render plan walls as solid extruded surfaces instead of stacked outlines.

Drawing each wall as a line at floor height and again at ceiling height gives
four flat plans floating in space — which is what it looked like, because that
is what it was. A wall needs the surface *between* those two lines.

Each segment becomes a quad (floor edge -> ceiling edge), depth-sorted back to
front and filled with painter's algorithm. Shading comes from the segment's
orientation so perpendicular walls read as different planes and the eye can
resolve the massing.

Shared by the live app and the offscreen PNG renderer so both show the same
geometry.
"""
import numpy as np
import cv2


def wall_quads(walls, base_z, ceil, stride=1):
    """(N,4) metre segments -> (M,4,3) quad corners, floor edge then ceiling edge.

    stride=1: the default used to be 2, which silently dropped every second wall,
    so the 3D model showed half the plan the 2D view drew. Skipping walls to save
    a little drawing time is not worth showing a different building.
    """
    w = np.asarray(walls, np.float32)[::stride]
    if len(w) == 0:
        return np.zeros((0, 4, 3), np.float32)
    x0, y0, x1, y1 = w[:, 0], w[:, 1], w[:, 2], w[:, 3]
    zf = np.full_like(x0, base_z)
    zc = np.full_like(x0, base_z + ceil)
    return np.stack([
        np.stack([x0, y0, zf], -1), np.stack([x1, y1, zf], -1),
        np.stack([x1, y1, zc], -1), np.stack([x0, y0, zc], -1),
    ], 1).astype(np.float32)


def quad_shade(quads, base_col):
    """Per-quad colour from segment orientation — flat fills read as one blob."""
    d = quads[:, 1, :2] - quads[:, 0, :2]
    ang = np.abs(np.degrees(np.arctan2(d[:, 1], d[:, 0]))) % 90.0
    t = 0.55 + 0.45 * (ang / 90.0)                  # 0.55..1.0
    return (np.asarray(base_col, np.float32)[None, :] * t[:, None]).astype(np.uint8)


def draw_walls(pane, quads, cols, R, cam, f, cen, ow, oh, edge=True):
    """Project and fill quads back-to-front. Returns the number drawn."""
    if len(quads) == 0:
        return 0
    P = (quads.reshape(-1, 3) - cen) @ R.T + cam
    P = P.reshape(-1, 4, 3)
    # ANY corner in front, not ALL. Requiring all four silently deleted every
    # wall that straddles the camera plane — at a low viewing angle that is a
    # large fraction of them, which is why the model looked like scattered
    # fragments instead of the floor plan it is built from.
    vis = (P[:, :, 2] > 0.25).any(1)
    P, cols = P[vis], cols[vis]
    if not len(P):
        return 0
    z = np.maximum(P[:, :, 2], 0.25)      # clamp, so a straddling corner still
    u = f * P[:, :, 0] / z + ow / 2.0     # projects somewhere sane instead of
    v = oh / 2.0 - f * P[:, :, 1] / z     # flying off to infinity
    pts = np.stack([u, v], -1)
    pts = np.clip(pts, -4 * max(ow, oh), 4 * max(ow, oh))
    # cull quads entirely offscreen before rasterising
    on = ((pts[:, :, 0] > -ow).any(1) & (pts[:, :, 0] < 2 * ow).any(1) &
          (pts[:, :, 1] > -oh).any(1) & (pts[:, :, 1] < 2 * oh).any(1))
    pts, cols, P = pts[on], cols[on], P[on]
    if not len(pts):
        return 0
    order = np.argsort(-P[:, :, 2].mean(1))          # far -> near
    pts, cols = pts[order].astype(np.int32), cols[order]
    for q, c in zip(pts, cols):
        cv2.fillConvexPoly(pane, q, (int(c[0]), int(c[1]), int(c[2])), cv2.LINE_8)
        if edge:
            cv2.polylines(pane, [q], True, (int(c[0] * .45), int(c[1] * .45),
                                            int(c[2] * .45)), 1, cv2.LINE_AA)
    return len(pts)


def box_quads(center, size, yaw_deg=0.0):
    """Axis-ish box -> (6,4,3) face quads. Objects render as primitives, not points.

    center is the ground-contact point (x, y, z_floor); the box rises from there,
    which is how furniture actually sits.
    """
    sx, sy, sz = [s / 2.0 for s in size[:2]] + [size[2]]
    c = np.asarray(center, np.float32)
    a = np.radians(yaw_deg)
    ca, sa = np.cos(a), np.sin(a)
    # 8 corners: 0-3 bottom (CCW), 4-7 top
    base = np.array([[-sx, -sy, 0], [sx, -sy, 0], [sx, sy, 0], [-sx, sy, 0],
                     [-sx, -sy, sz], [sx, -sy, sz], [sx, sy, sz], [-sx, sy, sz]],
                    np.float32)
    R = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]], np.float32)
    v = base @ R.T + c
    idx = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
           (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return np.stack([v[list(i)] for i in idx]).astype(np.float32)


def shade_faces(base_col, n=6):
    """Per-face shading so a box reads as a solid, not a flat silhouette."""
    t = np.array([0.55, 1.0, 0.75, 0.9, 0.7, 0.85], np.float32)[:n]
    return (np.asarray(base_col, np.float32)[None, :] * t[:, None]).astype(np.uint8)


def floor_slab(walls, base_z, inset=0.0):
    """Bounding slab corners for a level — grounds the massing visually."""
    w = np.asarray(walls, np.float32)
    x0 = min(w[:, 0].min(), w[:, 2].min()) + inset
    x1 = max(w[:, 0].max(), w[:, 2].max()) - inset
    y0 = min(w[:, 1].min(), w[:, 3].min()) + inset
    y1 = max(w[:, 1].max(), w[:, 3].max()) - inset
    return np.array([[[x0, y0, base_z], [x1, y0, base_z],
                      [x1, y1, base_z], [x0, y1, base_z]]], np.float32)


# ---------------------------------------------------------------- meshes
_MESH_CACHE = {}


def load_meshes(path=None):
    """Low-poly Objaverse meshes, keyed by class.

    Stored normalised: Z-up, centred in x/y on the ground-contact point, base at
    z=0, unit height. Classes whose decimation destroyed them are absent (marked
    fallback:box in index.json) and keep using a plain box, which is honestly
    better than a mangled mesh.
    """
    import json, os
    global _MESH_CACHE
    if _MESH_CACHE:
        return _MESH_CACHE
    root = path or os.path.expanduser("~/plans/primitives")
    idx = os.path.join(root, "index.json")
    meta = {}
    if os.path.exists(idx):
        try:
            meta = json.load(open(idx))
        except Exception:
            meta = {}
    out = {}
    for cls, m in (meta or {}).items():
        if isinstance(m, dict) and m.get("fallback") == "box":
            continue
        f = os.path.join(root, (m or {}).get("file", "%s.npz" % cls))
        if not os.path.exists(f):
            continue
        try:
            z = np.load(f)
            v = np.asarray(z["vertices"], np.float32)
            fc = np.asarray(z["faces"], np.int32)
            if len(v) < 3 or len(fc) < 1:
                continue
            ext = np.maximum(v[:, :2].max(0) - v[:, :2].min(0), 1e-3)
            entry = (v, fc, ext.astype(np.float32))
            # the index uses underscores ("dining_table") while detector classes
            # use spaces ("dining table"); register both so lookups cannot
            # silently miss and fall back to a box
            out[cls] = entry
            out[cls.replace("_", " ")] = entry
            out[cls.replace(" ", "_")] = entry
        except Exception:
            continue
    _MESH_CACHE = out
    return out


def mesh_quads(cls, center, size, yaw_deg=0.0, meshes=None):
    """Object mesh as (F,4,3) quads (triangles padded), or None if unavailable.

    Scaled to the object's measured extent so a mesh never misrepresents size:
    the shape comes from Objaverse, the dimensions come from what we measured.
    """
    meshes = meshes if meshes is not None else load_meshes()
    got = meshes.get(cls)
    if got is None:
        return None
    v, fc, ext = got
    sx = float(size[0]) / float(ext[0])
    sy = float(size[1]) / float(ext[1])
    sz = float(size[2])                      # unit height by construction
    q = v * np.array([sx, sy, sz], np.float32)
    a = np.radians(float(yaw_deg))
    c, s = np.cos(a), np.sin(a)
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float32)
    q = q @ R.T + np.asarray(center, np.float32)
    tri = q[fc]                              # (F,3,3)
    # pad each triangle to a quad by repeating its last vertex; fillConvexPoly
    # renders it identically and the whole pipeline stays quad-shaped
    return np.concatenate([tri, tri[:, 2:3, :]], axis=1).astype(np.float32)
