"""
Multi-part .3MF export for slicers (Bambu Studio, Orca, PrusaSlicer, ...).

Builds a single 3MF package containing the unprinted copies of every
selected part. Each unique geometry is stored once as an <object>; each
copy is an <item> referencing it with a placement transform, so file size
stays proportional to the geometry count, not the copy count.

Layout: items flow left-to-right in rows (10mm gaps), wrapping at ~350mm,
so copies never overlap and are easy to grab in the slicer.
"""
import io
import zipfile

ROW_GAP_MM = 10.0
ITEM_GAP_MM = 10.0
MAX_ROW_WIDTH_MM = 350.0

MIME_TYPE = 'application/vnd.ms-package.3dmanufacturing-3dmodel+xml'

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '</Types>'
)

_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    '</Relationships>'
)


def load_meshes(file_path):
    """
    Load every mesh in a part file.

    Returns a list of trimesh.Trimesh (possibly empty). Raises on unreadable
    files; callers decide how to report the failure.
    """
    import trimesh

    loaded = trimesh.load(file_path, process=False)
    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
    elif isinstance(loaded, trimesh.Trimesh):
        meshes = [loaded]
    else:
        meshes = []
    return [m for m in meshes if len(m.vertices) > 0 and len(m.faces) > 0]


def _fmt(value):
    return f'{float(value):.6f}'


def _mesh_xml(object_id, mesh):
    buf = io.StringIO()
    buf.write(f'<object id="{object_id}" type="model"><mesh><vertices>')
    for v in mesh.vertices:
        buf.write(f'<vertex x="{_fmt(v[0])}" y="{_fmt(v[1])}" z="{_fmt(v[2])}"/>')
    buf.write('</vertices><triangles>')
    for f in mesh.faces:
        buf.write(f'<triangle v1="{int(f[0])}" v2="{int(f[1])}" v3="{int(f[2])}"/>')
    buf.write('</triangles></mesh></object>')
    return buf.getvalue()


def build_3mf_package(entries):
    """
    Build a .3mf package (bytes) from part entries.

    entries: list of {'meshes': [Trimesh, ...], 'copies': int} — each mesh is
    placed once per copy; copies keep their meshes adjacent in the layout.

    Raises ValueError if no items would be produced.
    """
    items = []
    for entry in entries:
        for _ in range(entry['copies']):
            items.extend(entry['meshes'])
    if not items:
        raise ValueError('No items to export')

    # Assign object ids (dedup by identity) and precompute the small per-item
    # layout bounds so the heavy mesh arrays can be freed before the large XML
    # string and zip are built.
    objects = {}   # id(mesh) -> (object_id, mesh)
    item_object_ids = []
    layout = []    # per item: (width, depth, min_x, min_y)
    for mesh in items:
        if id(mesh) not in objects:
            objects[id(mesh)] = (len(objects) + 1, mesh)
        item_object_ids.append(objects[id(mesh)][0])
        bounds = mesh.bounds
        layout.append((
            float(bounds[1][0] - bounds[0][0]),
            float(bounds[1][1] - bounds[0][1]),
            float(bounds[0][0]),
            float(bounds[0][1]),
        ))

    transforms = []
    cursor_x = 0.0
    cursor_y = 0.0
    row_depth = 0.0
    for width, depth, min_x, min_y in layout:
        if cursor_x > 0 and cursor_x + width > MAX_ROW_WIDTH_MM:
            cursor_x = 0.0
            cursor_y += row_depth + ROW_GAP_MM
            row_depth = 0.0
        x = cursor_x - min_x
        y = cursor_y - min_y
        transforms.append(f'1,0,0,{_fmt(x)}, 0,1,0,{_fmt(y)}, 0,0,1,0, 0,0,0,1')
        cursor_x += width + ITEM_GAP_MM
        row_depth = max(row_depth, depth)

    resources = []
    for object_id, mesh in objects.values():
        resources.append(_mesh_xml(object_id, mesh))
    # Drop the geometry now that it is serialized; frees the numpy arrays
    # before model_xml + zip compression peak.
    objects.clear()
    items.clear()

    item_tags = []
    for object_id, transform in zip(item_object_ids, transforms):
        item_tags.append(f'<item objectid="{object_id}" transform="{transform}"/>')

    model_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f'<resources>{"".join(resources)}</resources>'
        f'<build>{"".join(item_tags)}</build>'
        '</model>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', _CONTENT_TYPES_XML)
        zf.writestr('_rels/.rels', _RELS_XML)
        zf.writestr('3D/3dmodel.model', model_xml)
    return buffer.getvalue()
