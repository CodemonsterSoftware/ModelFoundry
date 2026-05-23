"""
Background processing pipeline for bulk part uploads.

Three-phase pipeline with progress tracking:
  Phase 1: Upload         (0-33%)  — tracked by frontend XHR progress
  Phase 2: Thumbnails     (34-66%) — Blender sidecar renders, tracked here
  Phase 3: Volume calcs   (67-100%) — numpy-stl + ProcessPoolExecutor, tracked here

Uses numpy-stl for STL files (fast vectorized math) and falls back
to trimesh for other formats (OBJ from 3MF explosion, etc.).

All compute functions are module-level and pickle-safe for multiprocessing.
"""
import os
import logging
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed

logger = logging.getLogger(__name__)


# ─── Volume Computation (pickle-safe, no Django) ───────────────────────────

def compute_volume(file_path):
    """
    Compute the volume of a 3D mesh file in cubic millimeters.

    Uses numpy-stl for STL files (faster binary parse, vectorized math)
    and falls back to trimesh for other formats.

    This is a pure function (file path → number) safe for ProcessPoolExecutor.
    """
    if not file_path or not os.path.exists(file_path):
        return None

    try:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == '.stl':
            from stl import mesh as stl_mesh
            m = stl_mesh.Mesh.from_file(file_path)
            volume, _, _ = m.get_mass_properties()
            return abs(float(volume))
        else:
            import trimesh
            mesh_data = trimesh.load(file_path, force='mesh')
            return abs(float(mesh_data.volume))

    except Exception as e:
        logger.error(f"Error computing volume for {file_path}: {e}")
        return None


# ─── Background Pipeline ──────────────────────────────────────────────────

def process_parts_background(task_id, part_ids_and_paths, max_workers=None):
    """
    Fire-and-forget background processing pipeline.

    Spawns a daemon thread that runs two phases:
      Phase 2: Generate thumbnails via Blender (sequential HTTP calls)
      Phase 3: Compute volumes via ProcessPoolExecutor (parallel across cores)

    Updates the progress store after each part completes.

    Args:
        task_id: Unique task ID for progress tracking.
        part_ids_and_paths: List of (part_id, file_path) tuples.
        max_workers: Max parallel processes for volume calc.
    """
    if not part_ids_and_paths:
        return

    if max_workers is None:
        max_workers = min(4, os.cpu_count() or 2)

    def _pipeline():
        from django.db import connection
        try:
            from projects.models import Part
            from projects import progress

            # ── Phase 1.5: 3MF Explosion ─────────────────────────────
            expanded_parts = _run_3mf_explosion_phase(task_id, part_ids_and_paths)
            total = len(expanded_parts)

            # ── Phase 2: Thumbnails ──────────────────────────────────
            _run_thumbnail_phase(task_id, expanded_parts, total)

            # ── Phase 3: Volumes ─────────────────────────────────────
            _run_volume_phase(task_id, expanded_parts, total, max_workers)

            # ── Done ─────────────────────────────────────────────────
            progress.complete_task(task_id)
            logger.info(f"[pipeline] Task {task_id}: all phases complete")

        except Exception as e:
            logger.error(f"[pipeline] Task {task_id} error: {e}")
            from projects import progress
            progress.complete_task(task_id)  # Mark done so frontend stops polling
        finally:
            connection.close()

    thread = threading.Thread(target=_pipeline, daemon=True)
    thread.start()
    logger.info(f"[pipeline] Task {task_id}: dispatched {len(part_ids_and_paths)} parts to background")


def _run_3mf_explosion_phase(task_id, part_ids_and_paths):
    """Phase 1.5: Explode 3MF files into individual OBJ parts."""
    from projects.models import Part
    from projects import progress
    import tempfile
    import os

    expanded_parts = []
    
    for i, (part_id, file_path) in enumerate(part_ids_and_paths):
        # We always keep the original part in the list (even if it's 3MF, as it serves as the parent)
        expanded_parts.append((part_id, file_path))
        
        if not file_path or not file_path.lower().endswith('.3mf'):
            continue
            
        progress.update_task(task_id, status=f'Extracting parts from 3MF...')
        
        try:
            import trimesh
            from django.core.files import File
            
            part = Part.objects.get(id=part_id)
            scene = trimesh.load(file_path, force='scene')
            
            if isinstance(scene, trimesh.Scene) and hasattr(scene, 'graph'):
                count = 1
                for node_name in scene.graph.nodes_geometry:
                    geometry_id = scene.graph[node_name][1]
                    mesh = scene.geometry[geometry_id]
                    
                    child_filename = f"{part.name}_{count}.obj"
                    
                    with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as temp_obj:
                        mesh.export(temp_obj.name)
                        
                        with open(temp_obj.name, 'rb') as f:
                            child_part = Part.objects.create(
                                project=part.project,
                                name=f"{part.name} - Part {count}",
                                quantity=part.quantity,
                                material=part.material,
                                color=part.color,
                                group=part.group,
                                parent=part
                            )
                            # Skip synchronous processing for children
                            child_part._skip_volume = True
                            child_part._skip_thumbnail = True
                            child_part.stl_file.save(child_filename, File(f), save=True)
                            
                            expanded_parts.append((child_part.id, child_part.stl_file.path))
                            
                    os.unlink(temp_obj.name)
                    count += 1
                    progress.update_task(task_id, status=f'Extracting parts from 3MF... ({count-1} extracted)')
                    
        except Exception as e:
            logger.error(f"[pipeline] 3MF explosion failed for part {part_id}: {e}")
            
    # Update total count in progress tracker to reflect new parts
    progress.update_task(task_id, total=len(expanded_parts))
    return expanded_parts

def _run_thumbnail_phase(task_id, part_ids_and_paths, total):
    """Phase 2: Generate thumbnails via Blender sidecar."""
    from projects import progress
    from projects.models import Part

    progress.update_task(task_id, phase='thumbnails', completed=0,
                         status='Generating thumbnails...')

    try:
        from forge.services.blender_client import BlenderClient
        client = BlenderClient()

        if not client.is_available():
            logger.warning("[pipeline] Blender sidecar offline — skipping thumbnails")
            progress.skip_phase(task_id, 'thumbnails', reason='Blender offline')
            progress.update_task(task_id, status='Blender offline — skipping thumbnails')
            return

        for i, (part_id, file_path) in enumerate(part_ids_and_paths):
            try:
                part = Part.objects.get(id=part_id)
                if part.stl_file and not part.thumbnail:
                    import uuid as _uuid
                    base_name = os.path.splitext(os.path.basename(part.stl_file.name))[0]
                    filename = f"{base_name}_{_uuid.uuid4().hex[:8]}.png"
                    output_rel_path = f"thumbnails/{filename}"
                    input_rel_path = part.stl_file.name

                    success = client.generate_thumbnail(input_rel_path, output_rel_path)
                    if success:
                        part.thumbnail.name = output_rel_path
                        part.save(update_fields=['thumbnail'])
                        logger.info(f"[pipeline] Thumbnail generated for part {part_id}")

            except Exception as e:
                logger.error(f"[pipeline] Thumbnail failed for part {part_id}: {e}")

            progress.update_task(task_id, completed=i + 1,
                                 status=f'Generating thumbnails... ({i + 1}/{total})')

    except ImportError:
        logger.warning("[pipeline] BlenderClient not available — skipping thumbnails")
        progress.skip_phase(task_id, 'thumbnails', reason='BlenderClient unavailable')


def _run_volume_phase(task_id, part_ids_and_paths, total, max_workers):
    """Phase 3: Compute volumes in parallel via ProcessPoolExecutor."""
    from projects import progress
    from projects.models import Part

    progress.update_task(task_id, phase='volumes', completed=0,
                         status='Calculating volumes...')

    completed_count = 0

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        future_to_part = {
            pool.submit(compute_volume, path): part_id
            for part_id, path in part_ids_and_paths
        }

        for future in as_completed(future_to_part):
            part_id = future_to_part[future]
            try:
                volume = future.result(timeout=120)
                if volume is not None:
                    Part.objects.filter(id=part_id).update(volume=volume)
            except Exception as e:
                logger.error(f"[pipeline] Volume failed for part {part_id}: {e}")

            completed_count += 1
            progress.update_task(task_id, completed=completed_count,
                                 status=f'Calculating volumes... ({completed_count}/{total})')


# ─── Standalone helpers (for non-bulk operations) ─────────────────────────

def generate_task_id():
    """Generate a unique task ID."""
    return uuid.uuid4().hex[:12]
