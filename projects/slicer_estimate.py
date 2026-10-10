"""
Kiri:Moto slicer sidecar client for filament estimation.

Loads a part's mesh, auto-orients it (projects/orient.py), centers it on the
virtual bed, and asks the headless Kiri:Moto sidecar to slice it. The sidecar
returns total extruded path length in mm (+ per-feature breakdown); grams are
derived at read time from the material's current density (Part.slicer_weight_g).

estimate_part() raises SlicerFitError when the part cannot fit the machine
(a user error: no fallback) and SlicerError on any other failure (the caller
falls back to the geometry heuristic).
"""
import base64
import logging
import os
import struct

import numpy as np
import requests

logger = logging.getLogger(__name__)


class SlicerError(Exception):
    pass


class SlicerFitError(SlicerError):
    """Part does not fit the machine's print volume in any tried orientation."""


# Quarter turn about Z: lets a long part lie diagonal-free along the other bed axis
_RZ90 = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])


def load_mesh_tris(file_path):
    """(N,3,3) vertex array in mm from a mesh file (STL via numpy-stl, else trimesh)."""
    if os.path.splitext(file_path)[1].lower() == '.stl':
        from stl import mesh as stl_mesh
        m = stl_mesh.Mesh.from_file(file_path)
        return np.asarray(m.vectors, dtype=float)
    import trimesh
    mesh_data = trimesh.load(file_path, force='mesh')
    return np.asarray(mesh_data.vertices[mesh_data.faces], dtype=float).reshape(-1, 3, 3)


def build_stl_bytes(tris):
    """Binary STL (84-byte header + 50 bytes/tri) from an (N,3,3) vertex array."""
    tris = np.ascontiguousarray(tris, dtype=np.float32)
    n = len(tris)
    p0, p1, p2 = tris[:, 0], tris[:, 1], tris[:, 2]
    normals = np.cross(p1 - p0, p2 - p0)
    norm = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.where(norm > 0, norm, 1.0)
    recs = np.empty(n, dtype=[('n', 'f4', (3,)), ('p0', 'f4', (3,)),
                              ('p1', 'f4', (3,)), ('p2', 'f4', (3,)), ('attr', '<u2')])
    recs['n'] = normals
    recs['p0'] = p0
    recs['p1'] = p1
    recs['p2'] = p2
    recs['attr'] = 0
    return b'\0' * 80 + struct.pack('<I', n) + recs.tobytes()


def estimate_part(part, machine, base_url, timeout=660):
    """
    Slice `part` on `machine` via the Kiri:Moto sidecar at base_url.

    Returns {'source': 'slicer', 'machine_id', 'machine_name', 'filament_mm',
    'filament_diameter', 'time_s', 'breakdown_mm', 'stl_name', 'profile'}.
    Raises SlicerFitError if the part does not fit, SlicerError on mesh or
    sidecar failure. Default timeout exceeds the sidecar's own 600s deadline so
    its 504 arrives first.
    """
    profile = machine.get_slice_profile()

    tris = load_mesh_tris(part.stl_file.path)
    if len(tris) == 0:
        raise SlicerError('Mesh has no triangles')

    # Auto-orient so the part prints with minimal supports; the profile's
    # support angle doubles as the overhang threshold.
    try:
        from projects import orient
        R, _info = orient.best_orientation(tris, overhang_angle=profile.get('support_angle', 45))
    except Exception as e:
        logger.warning(f"Auto-orientation failed for {part.name}: {e}; slicing as-modeled")
        R = np.eye(3)

    # Best orientation first; if it doesn't fit, try it turned 90 deg on the bed,
    # then as-modeled (and turned) before giving up.
    bed_w, bed_d, bed_h = machine.print_volume_x, machine.print_volume_y, machine.print_volume_z
    flat = tris.reshape(-1, 3)
    first_ext = None
    for cand in (R, _RZ90 @ R, np.eye(3), _RZ90):
        pts = flat @ cand.T
        minp = pts.min(axis=0)
        ext = pts.max(axis=0) - minp
        if first_ext is None:
            first_ext = ext
        if ext[0] <= bed_w and ext[1] <= bed_d and ext[2] <= bed_h:
            break
    else:
        raise SlicerFitError(
            f'Part does not fit in the print volume of {machine.name} '
            f'({first_ext[0]:.1f} x {first_ext[1]:.1f} x {first_ext[2]:.1f} mm vs {bed_w} x {bed_d} x {bed_h} mm)'
        )

    # Center on the bed, first layer at z=0
    placed = pts - minp + np.array([(bed_w - ext[0]) / 2, (bed_d - ext[1]) / 2, 0.0])

    device = {
        'bedWidth': bed_w,
        'bedDepth': bed_d,
        'maxHeight': bed_h,
        'extruders': [{
            'extNozzle': profile.get('nozzle_diameter', 0.4),
            'extFilament': profile.get('filament_diameter', 1.75),
        }],
    }
    process = {
        'sliceHeight': profile['layer_height'],
        'sliceShells': profile['shells'],
        'sliceFillSparse': profile['infill_pct'] / 100,
        'sliceSupportType': 'automatic' if profile['support_enabled'] else 'disabled',
        'sliceSupportAngle': profile['support_angle'],
        'sliceSupportDensity': profile['support_density'] / 100,
        'firstLayerBrim': 3 if profile['brim'] else 0,
    }

    try:
        resp = requests.post(
            f'{base_url}/slice',
            json={
                'stl': base64.b64encode(build_stl_bytes(placed.reshape(-1, 3, 3))).decode(),
                'device': device,
                'process': process,
            },
            timeout=timeout,
        )
    except requests.Timeout as e:
        raise SlicerError(f'slicer service did not answer within {timeout}s') from e
    except requests.RequestException as e:
        raise SlicerError('could not reach the slicer service') from e
    if resp.status_code != 200:
        try:
            detail = resp.json().get('error', '')
        except ValueError:
            detail = ''
        raise SlicerError(f'Slicer sidecar returned {resp.status_code} {detail}'.strip())
    data = resp.json()
    if not data.get('ok'):
        raise SlicerError(data.get('error', 'Slicer sidecar failed'))

    return {
        'source': 'slicer',
        'machine_id': machine.id,
        'machine_name': machine.name,
        'filament_mm': round(float(data['filament_mm']), 1),
        'filament_diameter': profile.get('filament_diameter', 1.75),
        'time_s': int(data.get('time_s') or 0),
        'breakdown_mm': data.get('breakdown_mm') or {},
        'stl_name': part.stl_file.name,
        'profile': {k: profile[k] for k in ('layer_height', 'shells', 'infill_pct', 'support_enabled')},
    }
