"""
In-memory thread-safe progress tracking for upload tasks.

Stores progress per task_id with phase tracking:
  Phase 1: upload    (0-33%)
  Phase 2: thumbnails (34-66%)
  Phase 3: volumes   (67-100%)

Tasks support chunked uploads: pipeline work items are appended as file
chunks arrive (append_items) and the background consumer starts only once
(start_consumer_once), waiting for set_uploads_done before draining the queue.

Single-user design — no Redis/cache needed.
Auto-cleans stale tasks after 5 minutes of inactivity.
"""
import queue
import threading
import time

_lock = threading.Lock()
_tasks = {}  # {task_id: task dict}

# Auto-cleanup threshold (seconds)
_STALE_THRESHOLD = 300  # 5 minutes


def create_task(task_id, total_parts):
    """Create a new progress task.

    total_parts is the FINAL expected number of pipeline items (main parts +
    mirror counterparts). Upload progress during Phase 1 is derived from how
    many items have been appended so far.
    """
    with _lock:
        _cleanup_stale()
        _tasks[task_id] = {
            'phase': 'upload',
            'completed': 0,
            'total': total_parts,
            'status': 'Uploading files...',
            'created_at': time.time(),
            'last_activity': time.time(),
            'skipped_phases': [],
            'done': False,
            'pending': queue.Queue(),
            'items_received': 0,
            'enqueued': set(),
            'uploads_done': threading.Event(),
            'consumer_started': False,
            # Generic tasks (e.g. project import) drive their own percent and
            # report a terminal error / redirect target:
            'percent_override': None,
            'error': None,
            'redirect_url': None,
        }


def update_task(task_id, phase=None, completed=None, status=None, total=None, percent=None):
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
        if percent is not None:
            task['percent_override'] = percent
        task['last_activity'] = time.time()


def append_items(task_id, items):
    """Enqueue (part_id, file_path) pipeline work for a task.

    Does not change the task's total — create_task is expected to have been
    given the final item count up front. Items are deduplicated by part_id so
    re-sending an already-processed chunk (e.g. after a network timeout) does
    not enqueue duplicate work.
    """
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return
        for item in items:
            part_id = item[0]
            if part_id in task['enqueued']:
                continue
            task['enqueued'].add(part_id)
            task['pending'].put(item)
            task['items_received'] += 1
        task['last_activity'] = time.time()


def set_uploads_done(task_id):
    """Signal that all file chunks have been received."""
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return
        task['uploads_done'].set()
        task['last_activity'] = time.time()


def wait_uploads_done(task_id, timeout=600):
    """Block until uploads are signalled done or the timeout expires.

    Returns True if the done signal was received. Keeps the task fresh while
    waiting so stale cleanup does not reap it mid-upload.
    """
    with _lock:
        task = _tasks.get(task_id)
    if not task:
        return False
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return False
        if task['uploads_done'].wait(timeout=min(remaining, 30)):
            return True
        with _lock:
            if task_id in _tasks:
                _tasks[task_id]['last_activity'] = time.time()


def drain_pending(task_id):
    """Remove and return all enqueued pipeline items."""
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return []
    items = []
    while True:
        try:
            items.append(task['pending'].get_nowait())
        except queue.Empty:
            return items


def start_consumer_once(task_id):
    """Return True exactly once per task — the first caller owns the pipeline thread."""
    with _lock:
        task = _tasks.get(task_id)
        if not task or task['consumer_started']:
            return False
        task['consumer_started'] = True
        return True


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


def finish(task_id, success=True, error=None, redirect_url=None):
    """Mark a generic task done, optionally with an error or redirect target."""
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return
        task['done'] = True
        task['phase'] = 'complete'
        task['percent_override'] = 100 if success else task.get('percent_override')
        task['status'] = 'Complete!' if success else 'Failed'
        task['error'] = error
        task['redirect_url'] = redirect_url


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

        if task.get('percent_override') is not None:
            percent = int(task['percent_override'])
        elif task['done'] and task.get('error'):
            percent = 0
        elif phase == 'complete' or task['done']:
            percent = 100
        elif phase == 'upload':
            # 0-33% — driven by items appended so far (chunked uploads),
            # falling back to completed for legacy single-shot tasks
            received = max(task.get('items_received', 0), completed)
            percent = int((min(received, total) / total) * 33)
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
            'error': task.get('error'),
            'redirect_url': task.get('redirect_url'),
        }


def cleanup_task(task_id):
    """Remove a task from the store."""
    with _lock:
        _tasks.pop(task_id, None)


def _cleanup_stale():
    """Remove tasks idle longer than threshold. Called inside lock."""
    now = time.time()
    stale = [tid for tid, t in _tasks.items()
             if now - max(t.get('created_at', 0), t.get('last_activity', 0)) > _STALE_THRESHOLD]
    for tid in stale:
        del _tasks[tid]
