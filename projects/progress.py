"""
In-memory thread-safe progress tracking for upload tasks.

Stores progress per task_id with phase tracking:
  Phase 1: upload    (0-33%)
  Phase 2: thumbnails (34-66%)
  Phase 3: volumes   (67-100%)

Single-user design — no Redis/cache needed.
Auto-cleans stale tasks after 5 minutes.
"""
import threading
import time

_lock = threading.Lock()
_tasks = {}  # {task_id: {phase, completed, total, status, skipped_phases}}

# Auto-cleanup threshold (seconds)
_STALE_THRESHOLD = 300  # 5 minutes


def create_task(task_id, total_parts):
    """Create a new progress task."""
    with _lock:
        _cleanup_stale()
        _tasks[task_id] = {
            'phase': 'upload',
            'completed': 0,
            'total': total_parts,
            'status': 'Uploading files...',
            'created_at': time.time(),
            'skipped_phases': [],
            'done': False,
        }


def update_task(task_id, phase=None, completed=None, status=None, total=None):
    """Update progress for a task."""
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return
        if phase is not None:
            task['phase'] = phase
        if completed is not None:
            task['completed'] = completed
        if status is not None:
            task['status'] = status
        if total is not None:
            task['total'] = total


def skip_phase(task_id, phase, reason=''):
    """Mark a phase as skipped (e.g. Blender offline)."""
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task['skipped_phases'].append({'phase': phase, 'reason': reason})


def complete_task(task_id):
    """Mark task as fully done."""
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task['done'] = True
            task['phase'] = 'complete'
            task['status'] = 'Complete!'


def get_task(task_id):
    """Get current progress for a task. Returns None if not found."""
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return None

        # Calculate overall percentage from phase + completed/total
        total = max(task['total'], 1)
        completed = task['completed']
        phase = task['phase']
        skipped = [s['phase'] for s in task.get('skipped_phases', [])]

        if phase == 'complete' or task['done']:
            percent = 100
        elif phase == 'upload':
            # 0-33%
            percent = int((completed / total) * 33)
        elif phase == 'thumbnails':
            # 34-66%
            percent = 34 + int((completed / total) * 32)
        elif phase == 'volumes':
            # 67-100%
            percent = 67 + int((completed / total) * 33)
        else:
            percent = 0

        return {
            'phase': phase,
            'completed': completed,
            'total': total,
            'percent': percent,
            'status': task['status'],
            'done': task['done'],
            'skipped_phases': task.get('skipped_phases', []),
        }


def cleanup_task(task_id):
    """Remove a task from the store."""
    with _lock:
        _tasks.pop(task_id, None)


def _cleanup_stale():
    """Remove tasks older than threshold. Called inside lock."""
    now = time.time()
    stale = [tid for tid, t in _tasks.items()
             if now - t.get('created_at', 0) > _STALE_THRESHOLD]
    for tid in stale:
        del _tasks[tid]
