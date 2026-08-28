# MEMORY.md — ModelFoundry

Persistent context for AI sessions. Keep this file updated when making key decisions.

## Project Overview
- Django 3D-printing project manager: projects → parts (STL/3MF) + purchased parts, materials, designers, slicer/MQTT integration, print history.
- Apps: `projects` (main), `forge` (services, e.g. `forge.services.blender_client`), `blender_service` (Docker sidecar for thumbnails).
- Dev machine: Windows, PowerShell 5.1. Python venv at `venv\Scripts\python.exe` (global python lacks deps — always use the venv).
- Tests: `venv\Scripts\python.exe manage.py test projects` (run from repo root). No lint config in repo.

## Deployment
- Docker: `docker-compose.yml` (+ `.override.yml` for dev), Postgres on host `db`.
- `entrypoint.sh` waits for DB, then runs `manage.py migrate --noinput` + `collectstatic` at container start. Migrations apply automatically on deploy — never need manual migrate in CI/deploy.

## Background Processing Pipeline
- Part uploads (Add Parts) defer thumbnails + volume to a background pipeline: `projects/volume.py::process_parts_background(task_id, [(part_id, path), ...])`, progress in-memory via `projects/progress.py`, polled by frontend at `/api/upload-progress/<task_id>/`.
- Parts created on upload paths set `_skip_volume = True` / `_skip_thumbnail = True` before save so the pipeline owns both.
- Blender sidecar is optional: when offline, thumbnails are skipped gracefully (log warning) — tests must not require it.
- In tests, patch `projects.volume.process_parts_background` to keep upload tests hermetic (no real thread touching the DB).

## Chunked Part Upload (added this session)
- **Why:** big multi-part uploads OOM'd the gunicorn worker (`FILE_UPLOAD_MAX_MEMORY_SIZE=5GB` → Django buffers every file ≤ limit fully in RAM; whole batch = whole batch in memory) and hit `DATA_UPLOAD_MAX_NUMBER_FILES` (200) → opaque HTML 400. Monolithic single-POST design had no recovery on mid-batch failure.
- **Settings hardening** (`modelfoundry/settings.py`, env-overridable): `FILE_UPLOAD_MAX_MEMORY_SIZE=8MB` (files stream to disk via FileUploadHandler), `DATA_UPLOAD_MAX_MEMORY_SIZE=16MB`, `DATA_UPLOAD_MAX_NUMBER_FILES=2000`.
- **Two-phase Add Parts flow** (`add_parts.html` → new endpoints in views.py + urls.py):
  - Phase 1 `parts_create` (POST JSON `parts`, no files): creates Part rows + mirror counterparts fileless via shared helper `_create_part_rows(project, parts_data, file_count=None)`; returns `{task_id, rows: [{part_ids: [main, ...mirrors], has_file}], pipeline_total}`. Progress task created up front with the FINAL total.
  - Phase 2 `parts_upload` (repeated chunked multipart POSTs): FormData `files[]` + JSON `assignments` (`[{part_ids}]`, 1:1 with files) + `task_id`/`uploaded_rows`/`file_rows`. Saves file to main part, links mirrors via `stl_file` reference, `progress.append_items` + `dispatch_pipeline(task_id)`; last chunk calls `set_uploads_done`.
  - Frontend chunks ≤10 files or ≤256MB per request, sequential XHRs. On chunk failure: pause + show error in modal footer + **Resume Upload** button re-sends from first un-uploaded chunk (Phase 1 never re-runs; re-sending a file to an existing part id just overwrites it).
- **progress.py chunking API:** `append_items` (dedupes by part_id via task['enqueued'] set — resume-safe), `set_uploads_done`/`wait_uploads_done(timeout=600)`, `drain_pending`, `start_consumer_once`; upload-phase percent = `max(items_received, completed)/total * 33`.
- **volume.py:** `dispatch_pipeline(task_id, ...)` starts the consumer thread once (`_pipeline_loop`: wait for uploads → drain → 3MF explosion → thumbnails → volumes). Legacy `process_parts_background` is now a wrapper (append + set_uploads_done + dispatch) — existing callers (thumbnails/volumes regen views) unchanged.
- **Legacy compat:** `add_multiple_parts` still works end-to-end (old single POST); `_create_part_rows` treats missing `has_file` as True capped by `file_count=len(files)` (old index-aligned behavior). Multipart parse errors (`TooManyFilesSent`, `RequestDataTooBig`, `MultiPartParserError`) → JSON 400 via `_upload_error_message` in all three endpoints.
- **Gotcha:** views.py has `def settings(request)` (line ~1790) that shadows any top-level `from django.conf import settings` — use aliased local imports (`as django_settings`) as elsewhere in the file.

## Smart Import (added this session)
- `projects/smart_import.py` is the single source of truth for name-parsing helpers; **JS in `add_parts.html` mirrors it — keep both in sync**.
  - `parse_quantity_from_name(name) -> (cleaned, qty)`: conservative trailing tokens only — `_x3`, `_3x`, `_3pcs/pc/pieces` (space/underscore/hyphen separator, case-insensitive). Parentheses `(N)` are deliberately NOT quantities (duplicate downloads). No match or empty-name result → `(name, 1)`. **While matching, a trailing `(Left)/(Right)` side marker and any `mirror` token are ignored, then the side marker is reattached** — so `earcup mirror x2 (Left)` → `('earcup (Left)', 2)` and `earcup_x2_mirror` → `('earcup', 2)`. Without this, quantity text survived in part names after mirror stripping (user-reported).
  - `resolve_manifest_quantity(part_data)`: explicit valid int qty ≥ 1 → else parse STL filename → else part name → else 1.
  - `mirror_pair_names(name) -> (main, counterpart)`: unmarked → `"(Right)"`/`"(Left)"`; marked keeps its name. `mirror_counterpart_name` = `[1]` of that.
- Mirror pairs (Add Parts toggle + backend): counterpart **shares the main part's STL file**, copies material/color/group, `_skip_volume=True`, appended to pipeline tasks. No geometry flip — slicer handles mirroring. Matches `copy_mirror_part` (views.py) convention: main becomes `(Right)`.
- **User decision: Add Parts mirror pairs SPLIT the row's quantity across both parts** (total preserved: 2→1+1, 3→2+1; a single piece can't be halved → 1+1). Implemented in `_create_part_rows` (shared by `parts_create` + legacy `add_multiple_parts`). The manual `copy_mirror_part` view and retroactive smart-parsing mirror pass still copy the FULL quantity (explicit user actions, not auto-detection).
- Frontend auto-pair rule: dropping only one of `part (Left).stl` / `part (Right).stl` auto-checks the mirror toggle; if both are dropped, both are real parts and any earlier auto-check is reverted. Manual toggles (`data-autoMirror` unset) are never auto-reverted.
- **ZIP drop support on Add Parts** (`add_parts.html`, frontend-only): `.zip` files are extracted client-side with fflate, **vendored locally at `projects/static/projects/js/fflate.min.js`** (v0.8.0 UMD; served via `{% static %}` — deliberately NOT CDN so it works when the browser can't reach jsdelivr; `project_detail.html` still loads the same version from CDN for three.js's 3MFLoader). Only stl/obj/3mf entries become rows; folder paths stripped to basename, nested zips count as skipped; each entry flows through the normal `addRowFromFile` path (smart import + two-phase chunked upload unchanged). No backend changes. **User decision: no success alert** — rows appearing is the feedback; only errors alert (missing fflate global, corrupt/unreadable zip with `err.message` surfaced e.g. "invalid zip data", or a zip with zero 3D entries). **Gotcha: fflate's async `unzip` is callback-only — it does NOT return a Promise** (calling it without a callback throws fflate error 7 "no callback"); wrap in a Promise in `extractZipFile`. Verified end-to-end via Node against the vendored build.
- `import_project` body lives in `@transaction.atomic _import_project_data(project_data, temp_dir, user)` (views.py) — mid-import failure rolls back.
- Dead code removed: `upload_files` view + URL + `BulkUploadForm` (broken bulk ZIP upload, superseded by Add Parts).

## Project Default Material (added this session)
- `Project.default_material` FK (nullable, SET_NULL; migration `0032_project_default_material`).
- **User decision: future parts only — NO backfill of existing unassigned parts** (material is required in form validation, so unassigned parts shouldn't exist).
- Where it applies:
  - Add Parts (`add_parts.html`): new rows pre-select the default via `updateMaterialDropdownUI()`; still overridable per row.
  - Single add part (`PartForm`): material is **required** (model allows blank, form enforces) and initial pre-selects the project default.
  - ZIP import: manifest metadata `default_material_id` → fallback `default_material_name`; parts with missing/unknown `material_id` get the default.
  - `add_multiple_parts` backend: row without material falls back to default; if neither exists → 400 JSON error.
- Export writes `default_material_id` + `default_material_name` in metadata and includes the default in the materials list even when unused by parts (round-trip fidelity).
- Set via New/Edit Project form only (`ProjectForm`, crispy layout). No quick-set control on project detail page (deliberate scope cut).

## 3MF Export (added this session)
- `projects/export_3mf.py`: pure helpers — `load_meshes(path)` (trimesh; Scene → list of Trimesh), `build_3mf_package(entries)` where entries = `[{'meshes': [Trimesh...], 'copies': int}]`. Each unique geometry stored once as `<object>`; each copy is an `<item objectid=N transform=.../>` (spec format: 16 comma-separated floats, row-major — Bambu/Orca/lib3mf compatible). NOTE: trimesh's own 3MF loader wants 12 whitespace-separated values and will NOT parse our output — validate via ElementTree instead.
- Layout: items flow in rows, 10mm item gap / 10mm row gap, wrap at 350mm (constants in the module). Copies of a part stay adjacent.
- View `export_3mf` (views.py) + URL `project/<id>/export-3mf/` (POST JSON `{part_ids, mark_complete}`):
  - **User decision: exports REMAINING copies only** (`quantity - completed`); fully-complete parts contribute 0; selection with 0 total copies → 400.
  - Parents with sub-parts expand to their children (deduped) — selecting parent + children never double-counts.
  - Strict validation: unknown/foreign part ids, missing files, unloadable geometry → 400 JSON naming the parts.
  - `mark_complete: true` → `completed = quantity` on all resolved leaf parts (same semantics as `bulk_complete_parts`).
- Frontend (project_detail.html): "Export .3MF" button in the floating bulk action bar (bulk mode only — deliberate scope cut); confirm modal shows "N parts → M copies" (computed client-side from row `data-quantity`/`data-completed`) + default-on "Mark parts as complete after export" checkbox; fetch-blob download with filename parsed from Content-Disposition; reloads page when marked complete.
  - Error handler is defensive: only calls `FileReader.readAsText` when `xhr.response instanceof Blob`; falls back to `xhr.responseText` / generic message with HTTP status otherwise (a TypeError here previously hid the real server error — null response = server process died mid-export, e.g. OOM on many/large meshes). 120s ajax timeout.
  - **Download race (actual root cause of "no file, no error"):** `URL.revokeObjectURL()` called synchronously after `link.click()` + immediate `location.reload()` (mark-complete default-on) cancelled the in-flight blob download — server returned 200 fine (verified via docker logs), browser silently dropped it. Fix: revoke after 10s, reload after 2s (only when mark_complete). Success handler wrapped in try/catch with alert fallback.
- Memory: `build_3mf_package` precomputes per-item layout bounds, then frees all mesh refs (`objects.clear()`/`items.clear()`) before building the big XML string + zip; `_mesh_xml` streams via StringIO (no per-vertex string lists). View logs start ("N part(s) selected") and load progress — if "started" is logged but no response arrives in the browser, the process died during build → export in smaller batches.
- Tests: `Export3MFTests` (12) — fixtures use real STL bytes from `trimesh.creation.box().export(file_type='stl')` (fake-byte fixtures can't be parsed) and set `_skip_volume`/`_skip_thumbnail` before save to avoid side effects. Includes file-deleted-from-disk → 400 "Could not load geometry" case.

## Retroactive Smart Parsing (added this session)
- "Apply Smart Parsing" option in the **Process STL dropdown** (Parts section header, project_detail.html — NOT the index page). Synchronous AJAX POST → confirm/alert/reload; no pipeline/progress polling.
- View `apply_smart_parsing` (views.py, after `export_3mf`) + URL `project/<id>/apply-smart-parsing/` (`@require_POST`, `@transaction.atomic`). Two passes over all project parts:
  1. Quantity: **only parts with `quantity == 1`** (manual values preserved, name included). Token source: STL filename first, then part name. Name is cleaned when it carries the token (`bracket_x3` → `bracket`, qty 3). `_skip_volume=True` before save (name/qty change only — never recompute geometry stats).
  2. Mirror: for parts with a file, no sub-parts, name matching `(Left)`/`(Right)` (case-insensitive) and no existing counterpart (casefold name match) → create counterpart sharing the file, copying material/color/group/quantity **plus volume + thumbnail** from the main part (same geometry = same stats; `_skip_volume`/`_skip_thumbnail` set). Runs after pass 1 so counterparts inherit corrected quantities.
- Fully idempotent — safe to re-run on any project. Response: `{status, message, quantities_updated: ["name (1→N)"], mirrors_created: [...]}`.
- New helper in smart_import.py: `is_mirror_marked(name)` (bool; wraps `_SIDE_RE`).
- **Django 4.2 gotcha:** accessing a reverse FK (`part.sub_parts`) returns a bound RelatedManager — truthiness is ALWAYS true, even with `prefetch_related` (cache is only consulted inside the manager's queryset). Never do `if part.sub_parts:`; use `.exists()`/`.count()` or compute parent-ness in-memory (`{p.parent_id for p in parts if p.parent_id}` = ids of parts that have children — note: that's the *values*, not the child ids).
- **Test storage gotcha:** Django appends a random suffix (`name_ABC123.stl`) when the file already exists on disk (leftovers from prior runs persist in `media/`), which breaks trailing-token filename parsing. `ApplySmartParsingTests` tracks created files and deletes them in tearDown; also clean stale fixture STLs out of `media/stl_files` if tests start failing with suffixed names.

## Auto Name Parsing Setting (added this session)
- User setting toggling smart import on Add Parts: stored in the existing `UserSettings` JSON blob (`settings_type='general'`, key `auto_name_parsing`) — no migration. **Defaults ON when absent** (preserves old behavior).
- UI: form-switch in the General tab of settings.html; saved through the standard `settings()` view (POST `settings_type=general`).
- Consumed by `add_multiple_parts` GET, which reads the user's general JSON (same try/except pattern as `part_details`) and passes `auto_name_parsing` to the template → `const AUTO_NAME_PARSING` in add_parts.html. When OFF: name = filename minus extension, quantity 1, no mirror auto-pairing. ZIP entries inherit automatically (same `addRowFromFile` path).
- **User decision (scope): Add Parts page only.** The retroactive "Apply Smart Parsing" button and the manifest-import fallback (`resolve_manifest_quantity`) are deliberately NOT gated — explicit user action / separate flow. A manually checked mirror toggle is also never gated (submit-time pairing logic runs regardless of the setting).
- **Pre-existing bug fixed in passing:** settings.html referenced `general_settings` but the view context never included it (only appearance/api/system) → General tab select states silently reset on reload. Now passes `'general_settings': forms.get('general', {})`.

## Locked User Decisions (do not re-litigate)
- Quantity parsing: conservative patterns only; no `(N)` support.
- Mirror behavior: share STL file, no mesh flip.
- Default material: no backfill; set at project creation AND edit (shared `ProjectForm`); optional field.
- 3MF export: remaining copies only (`quantity - completed`); entry point in bulk action bar only (no per-part / export-all); auto row layout with gaps (user still arranges in slicer).
- Retroactive smart parsing: clean part names when the token is found (consistent with new uploads); only touch parts whose quantity is still 1 (manual quantities always win); entry point = Process STL dropdown option on project detail page.
- Auto name parsing toggle: Add Parts page only (retroactive button + import fallback stay ungated); defaults ON.

## Conventions
- Views return JSON `{status: 'success'|'error', message?}` for AJAX endpoints; redirects + messages for full-page flows.
- Templates: Bootstrap 5 + Font Awesome + jQuery, dark theme (`premium-card`, `--bg-dark` etc.); Django template tags inline in JS via `{{ ... }}`.
- Tests live in `projects/tests.py`; existing classes: `ViewTests`, `ModelTests`, `ProjectActionTests`, `SmartImportParserTests` (SimpleTestCase), `AddMultiplePartsMirrorTests`, `PartsTwoPhaseUploadTests`, `ProgressChunkedUploadTests`, `ProjectDefaultMaterialTests`, `Export3MFTests`, `ApplySmartParsingTests`, `AutoNameParsingSettingTests`. 106 tests total.
- Working tree note: smart import + default material + 3MF export changes are uncommitted (along with earlier session work in `volume.py`, `project_detail.html`, `settings.py`, `blender_service/Dockerfile`).
