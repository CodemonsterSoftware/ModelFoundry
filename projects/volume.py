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


def compute_mesh_stats(file_path):
    """
    Compute the volume (mm³) and surface area (mm²) of a 3D mesh file.

    Uses numpy-stl for STL files (faster binary parse, vectorized math)
    and falls back to trimesh for other formats.

    Returns (volume, surface_area) tuple of floats, or None on error.
    This is a pure function (file path → tuple) safe for ProcessPoolExecutor.
    """
    if not file_path or not os.path.exists(file_path):
        return None

    try:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == '.stl':
            from stl import mesh as stl_mesh
            m = stl_mesh.Mesh.from_file(file_path)
            volume, _, _ = m.get_mass_properties()
            area = float(m.areas.sum())
            return (abs(float(volume)), abs(area))
        else:
            import trimesh
            mesh_data = trimesh.load(file_path, force='mesh')
            return (abs(float(mesh_data.volume)), abs(float(mesh_data.area)))

    except Exception as e:
        logger.error(f"Error computing mesh stats for {file_path}: {e}")
        return None


def compute_volume(file_path):
    """
    Compute the volume of a 3D mesh file in cubic millimeters.

    Uses numpy-stl for STL files (faster binary parse, vectorized math)
    and falls back to trimesh for other formats.

    Legacy wrapper around compute_mesh_stats.
    """
    stats = compute_mesh_stats(file_path)
    return stats[0] if stats else None


# ─── Background Pipeline ──────────────────────────────────────────────────

def dispatch_pipeline(task_id, max_workers=None, run_thumbnails=True, run_volumes=True):
    """
    Start the background pipeline consumer for a task (at most once per task).

    The consumer waits until chunked uploads are finished
    (progress.set_uploads_done), drains all enqueued items, then runs:
      Phase 1.5: 3MF explosion
      Phase 2: Generate thumbnails via Blender (sequential HTTP calls)
      Phase 3: Compute volumes via ProcessPoolExecutor (parallel across cores)

    Args:
        task_id: Unique task ID for progress tracking.
        max_workers: Max parallel processes for volume calc.
        run_thumbnails: Whether to run Phase 2 (thumbnails).
        run_volumes: Whether to run Phase 3 (volumes).
    """
    from projects import progress

    if not progress.start_consumer_once(task_id):
        return

    if max_workers is None:
        max_workers = min(4, os.cpu_count() or 2)

    thread = threading.Thread(
        target=_pipeline_loop,
        args=(task_id, max_workers, run_thumbnails, run_volumes),
        daemon=True,
    )
    thread.start()
    logger.info(f"[pipeline] Task {task_id}: consumer dispatched")


def process_parts_background(task_id, part_ids_and_paths, max_workers=None, run_thumbnails=True, run_volumes=True):
    """
    Legacy single-shot dispatch: enqueue all items, mark uploads done, start the consumer.

    Args:
        task_id: Unique task ID for progress tracking.
        part_ids_and_paths: List of (part_id, file_path) tuples.
        max_workers: Max parallel processes for volume calc.
        run_thumbnails: Whether to run Phase 2 (thumbnails).
        run_volumes: Whether to run Phase 3 (volumes).
    """
    if not part_ids_and_paths:
        return

    from projects import progress
    progress.append_items(task_id, list(part_ids_and_paths))
    progress.set_uploads_done(task_id)
    dispatch_pipeline(task_id, max_workers=max_workers, run_thumbnails=run_thumbnails, run_volumes=run_volumes)


def _pipeline_loop(task_id, max_workers, run_thumbnails, run_volumes):
    """Consumer thread: wait for uploads to finish, then run all phases."""
    from django.db import close_old_connections
    # Close stale/old connections to force a fresh thread-safe connection check
    close_old_connections()
    try:
        from projects import progress

        logger.info(f"[pipeline] Task {task_id}: waiting for uploads to finish")
        uploads_done = progress.wait_uploads_done(task_id)
        part_ids_and_paths = progress.drain_pending(task_id)

        if not uploads_done:
            logger.warning(
                f"[pipeline] Task {task_id}: upload wait timed out; "
                f"processing {len(part_ids_and_paths)} received item(s)"
            )
        if not part_ids_and_paths:
            progress.complete_task(task_id)
            return

        logger.info(f"[pipeline] Task {task_id}: starting phases with {len(part_ids_and_paths)} items")

        # ── Phase 1.5: 3MF Explosion ─────────────────────────────
        expanded_parts = _run_3mf_explosion_phase(task_id, part_ids_and_paths)
        total = len(expanded_parts)
        logger.info(f"[pipeline] Task {task_id}: finished 3MF explosion phase. Total: {total}")

        # ── Phase 2: Thumbnails ──────────────────────────────────
        if run_thumbnails:
            _run_thumbnail_phase(task_id, expanded_parts, total)
        else:
            progress.skip_phase(task_id, 'thumbnails', reason='Skipped')

        # ── Phase 3: Volumes ─────────────────────────────────────
        if run_volumes:
            _run_volume_phase(task_id, expanded_parts, total, max_workers)
        else:
            progress.skip_phase(task_id, 'volumes', reason='Skipped')

        # ── Done ─────────────────────────────────────────────────
        progress.complete_task(task_id)
        logger.info(f"[pipeline] Task {task_id}: all phases complete")

    except Exception as e:
        logger.error(f"[pipeline] Task {task_id} error: {e}")
        from projects import progress
        progress.complete_task(task_id)  # Mark done so frontend stops polling
    finally:
        close_old_connections()


def _run_3mf_explosion_phase(task_id, part_ids_and_paths):
    """Phase 1.5: Explode 3MF files into individual OBJ parts."""
    from projects.models import Part
    from projects import progress
    from django.db import close_old_connections
    import tempfile
    import os

    expanded_parts = []
    
    for i, (part_id, file_path) in enumerate(part_ids_and_paths):
        # We always keep the original part in the list (even if it's 3MF, as it serves as the parent)
        expanded_parts.append((part_id, file_path))
        
        if not file_path or not file_path.lower().endswith('.3mf'):
            continue
            
        logger.info(f"[pipeline] Exploding 3MF file {file_path} for part {part_id}")
        close_old_connections()
        progress.update_task(task_id, status=f'Extracting parts from 3MF...')
        
        try:
            import trimesh
            from django.core.files import File
            
            part = Part.objects.get(id=part_id)
            logger.info(f"[pipeline] Loading 3MF scene from {file_path}")
            scene = trimesh.load(file_path, force='scene')
            logger.info(f"[pipeline] 3MF scene loaded successfully. Graph nodes: {len(scene.graph.nodes_geometry) if hasattr(scene, 'graph') else 0}")
            
            if isinstance(scene, trimesh.Scene) and hasattr(scene, 'graph'):
                count = 1
                for node_name in scene.graph.nodes_geometry:
                    try:
                        geometry_id = scene.graph[node_name][1]
                        mesh = scene.geometry.get(geometry_id)
                        
                        if not mesh or not isinstance(mesh, trimesh.Trimesh):
                            logger.info(f"[pipeline] Node {node_name} has no valid 3D mesh geometry. Skipping.")
                            continue
                            
                        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
                            logger.info(f"[pipeline] Mesh for node {node_name} is empty. Skipping.")
                            continue

                        child_filename = f"{part.name}_{count}.obj"
                        temp_name = None
                        
                        try:
                            with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as temp_obj:
                                temp_name = temp_obj.name
                                mesh.export(temp_name)
                                
                            with open(temp_name, 'rb') as f:
                                close_old_connections()
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
                        finally:
                            if temp_name and os.path.exists(temp_name):
                                os.unlink(temp_name)
                                
                        count += 1
                        progress.update_task(task_id, status=f'Extracting parts from 3MF... ({count-1} extracted)')
                        
                    except Exception as child_err:
                        logger.error(f"[pipeline] 3MF child extraction failed for part {part_id}, node {node_name}: {child_err}")
                    
        except Exception as e:
            logger.error(f"[pipeline] 3MF explosion failed for part {part_id}: {e}")
            
    # Update total count in progress tracker to reflect new parts
    progress.update_task(task_id, total=len(expanded_parts))
    return expanded_parts

def _run_thumbnail_phase(task_id, part_ids_and_paths, total):
    """Phase 2: Generate thumbnails via Blender sidecar."""
    from projects import progress
    from projects.models import Part
    from django.db import close_old_connections

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
                close_old_connections()
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
    from django.db import connections, close_old_connections

    progress.update_task(task_id, phase='volumes', completed=0,
                         status='Calculating volumes...')

    completed_count = 0

    # Close default connection before spawning process pool to prevent socket descriptors being shared in child forks
    from django.db import connection
    connection.close()

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        future_to_part = {
            pool.submit(compute_mesh_stats, path): part_id
            for part_id, path in part_ids_and_paths
        }

        for future in as_completed(future_to_part):
            part_id = future_to_part[future]
            try:
                close_old_connections()
                stats = future.result(timeout=120)
                if stats is not None:
                    volume, surface_area = stats
                    Part.objects.filter(id=part_id).update(volume=volume, surface_area=surface_area)
            except Exception as e:
                logger.error(f"[pipeline] Volume failed for part {part_id}: {e}")

            completed_count += 1
            progress.update_task(task_id, completed=completed_count,
                                 status=f'Calculating volumes... ({completed_count}/{total})')


# ─── Standalone helpers (for non-bulk operations) ─────────────────────────

def generate_task_id():
    """Generate a unique task ID."""
    return uuid.uuid4().hex[:12]
