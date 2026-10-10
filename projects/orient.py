"""
Auto-orientation for print estimation.

Port of OrcaSlicer's "Smart Model Orientation" (src/libslic3r/Orient.cpp,
also used by Bambu Studio). Candidate up-directions come from area-weighted
face normals of the mesh and its convex hull plus a fixed set of axis and
45-degree diagonal supplements. Each candidate is scored with a support
volume proxy (overhang area x height x angular excess) against bed adhesion
(bottom area, footprint contour, convex hull bottom). The lowest cost wins;
ties prefer staying close to the original upright pose.

Pure numpy + scipy. No Django imports, safe to call from worker processes.
"""
import math

import numpy as np
from scipy.spatial import ConvexHull

# Tuned weights from OrcaSlicer OrientParams (kept verbatim)
TAR_A = 0.01
TAR_B = 0.177
RELATIVE_F = 6.610621027964314
CONTOUR_F = 0.23228623269775997
BOTTOM_F = 1.167152017941474
BOTTOM_HULL_F = 0.1
TAR_C = 0.24308070476924726
TAR_D = 0.6284515508160871
FIRST_LAY_H = 0.2
LAF_MAX = 0.999    # cos(1.4 deg)
LAF_MIN = 0.9703   # cos(14 deg)
TAR_LAF = 0.01
BOTTOM_MIN = 0.1   # mm^2, below this the part is unstable
APPEARANCE_SUPP = 3.0  # plain STL: every face counts as exterior appearance

# Fixed supplement candidates: stored face normal that should end up pointing
# down (touching the bed). Axes + in-plane 45 deg diagonals.
SUPPLEMENTS = np.array([
    [0.0, 0.0, -1.0],
    [0.70710678, 0.0, -0.70710678], [0.0, 0.70710678, -0.70710678],
    [-0.70710678, 0.0, -0.70710678], [0.0, -0.70710678, -0.70710678],
    [1.0, 0.0, 0.0], [0.70710678, 0.70710678, 0.0], [0.0, 1.0, 0.0],
    [-0.70710678, 0.70710678, 0.0], [-1.0, 0.0, 0.0],
    [-0.70710678, -0.70710678, 0.0], [0.0, -1.0, 0.0], [0.70710678, -0.70710678, 0.0],
    [0.70710678, 0.0, 0.70710678], [0.0, 0.70710678, 0.70710678],
    [-0.70710678, 0.0, 0.70710678], [0.0, -0.70710678, 0.70710678], [0.0, 0.0, 1.0],
])

EPS = 1e-9


def _face_data(tris):
    """Per-face unit normals and areas from (N,3,3) triangles."""
    p0, p1, p2 = tris[:, 0], tris[:, 1], tris[:, 2]
    cross = np.cross(p1 - p0, p2 - p0)
    areas = 0.5 * np.linalg.norm(cross, axis=1)
    scale = np.where(areas > 0, areas * 2.0, 1.0)
    normals = cross / scale[:, None]
    return normals, areas


def _top_normals(normals, areas, count):
    """Top `count` candidate directions by summed face area (1/1000 buckets)."""
    keys = np.floor(normals * 1000).astype(np.int64)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    sums = np.zeros(len(uniq))
    np.add.at(sums, inv, areas)
    top = np.argsort(-sums)[:count]
    dirs = uniq[top].astype(float) / 1000.0
    n = np.linalg.norm(dirs, axis=1, keepdims=True)
    return dirs / np.where(n > 0, n, 1.0)


def _hull_tris(points):
    """Convex hull triangles of the point cloud, or None on degenerate input."""
    try:
        # ponytail: subsample for the hull; a dense mesh's hull ~ its subset's hull
        if len(points) > 20000:
            points = points[::len(points) // 20000]
        pts = np.unique(points, axis=0)
        if len(pts) < 4:
            return None
        hull = ConvexHull(pts)
        return pts[hull.simplices]
    except Exception:
        return None


def _score(tris, normals, areas, hull_tris, hull_areas, u, ascent):
    """Score one candidate. u = unit up-direction in original coordinates."""
    z = (tris.reshape(-1, 3) @ u).reshape(-1, 3)
    z_max = z.max(axis=1)
    min_z = float(z.min())

    bottom_mask = z_max < min_z + FIRST_LAY_H - EPS
    bottom_half = z_max < min_z + FIRST_LAY_H * 0.5 - EPS
    bottom = 0.5 * float(areas[bottom_mask].sum()) + float(areas[bottom_half].sum())

    nz = normals @ u
    excess = np.minimum(nz - ascent, 0.0)
    overhang_mask = (nz < ascent) & ~bottom_half
    heights = tris.mean(axis=1) @ u - min_z
    overhang = float(
        (areas * (1.0 + APPEARANCE_SUPP) * heights * np.abs(excess))[overhang_mask].sum()
    )

    contour = 4.0 * math.sqrt(bottom) if bottom > 0 else 0.0
    if hull_tris is not None:
        hz = (hull_tris.reshape(-1, 3) @ u).reshape(-1, 3).max(axis=1)
        bottom_hull = float(hull_areas[hz < min_z + FIRST_LAY_H - EPS].sum())
    else:
        bottom_hull = 0.0

    nz_abs = np.abs(nz)
    laf = (nz_abs < LAF_MAX) & (nz_abs > LAF_MIN) & (z_max > min_z + FIRST_LAY_H)
    area_laf = float(areas[laf].sum())

    o = overhang / 25.0
    cost = (TAR_A * (o + TAR_B)
            + RELATIVE_F * (o * TAR_C + TAR_D + TAR_LAF * area_laf)
              / (TAR_D + CONTOUR_F * contour + BOTTOM_F * bottom + BOTTOM_HULL_F * bottom_hull))
    if bottom < BOTTOM_MIN:
        cost += 100.0
    return cost, {
        "bottom_mm2": bottom,
        "overhang_score": overhang,
        "area_laf_mm2": area_laf,
    }


def rotation_from_to(v_from, v_to):
    """Minimal rotation matrix R with R @ v_from = v_to."""
    f = np.asarray(v_from, dtype=float)
    t = np.asarray(v_to, dtype=float)
    f = f / np.linalg.norm(f)
    t = t / np.linalg.norm(t)
    c = float(f @ t)
    if c >= 1.0 - 1e-9:
        return np.eye(3)
    k = np.cross(f, t)
    s = float(k @ k)
    if s < 1e-18:
        a = np.array([1.0, 0.0, 0.0]) if abs(f[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        a = a - (a @ f) * f
        a /= np.linalg.norm(a)
        return 2.0 * np.outer(a, a) - np.eye(3)
    K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + K + K @ K * ((1.0 - c) / s)


def best_orientation(triangles, overhang_angle=45.0):
    """
    Find the rotation that minimizes estimated support material for printing.

    triangles: (N,3,3) vertex array in mm (e.g. numpy-stl Mesh.vectors).
    Returns (R, info): R is a 3x3 orthonormal matrix to apply as p' = R @ p,
    info carries the score breakdown for the chosen orientation.
    """
    tris = np.asarray(triangles, dtype=float)
    if tris.size == 0:
        return np.eye(3), {}

    normals, areas = _face_data(tris)
    points = tris.reshape(-1, 3)

    cands = [np.array([[0.0, 0.0, -1.0]])]
    cands.append(_top_normals(normals, areas, 10))
    hull_tris = _hull_tris(points)
    hull_areas = None
    if hull_tris is not None:
        hn, ha = _face_data(hull_tris)
        cands.append(_top_normals(hn, ha, 14))
        hull_areas = ha
    cands.append(SUPPLEMENTS)

    all_cands = np.vstack(cands)
    keys = np.floor(all_cands * 100).astype(np.int64)
    _, idx = np.unique(keys, axis=0, return_index=True)
    all_cands = all_cands[idx]
    keep = np.linalg.norm(all_cands, axis=1) > 1e-9
    all_cands = all_cands[keep]

    ascent = -math.cos(math.radians(overhang_angle))
    us = -all_cands / np.linalg.norm(all_cands, axis=1, keepdims=True)

    z_up = np.array([0.0, 0.0, 1.0])
    costs = np.empty(len(us))
    infos = []
    for i, u in enumerate(us):
        cost, info = _score(tris, normals, areas, hull_tris, hull_areas, u, ascent)
        costs[i] = cost
        infos.append(info)

    min_cost = float(costs.min())
    pool = np.where(costs <= min_cost + 1e-6)[0]
    best_i = int(pool[np.argmax(us[pool] @ z_up)])

    R = rotation_from_to(us[best_i], z_up)
    info = dict(infos[best_i])
    info["cost"] = float(costs[best_i])
    info["n_candidates"] = len(us)
    return R, info
