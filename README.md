# ModelFoundry v2.1

<img width="1486" height="813" alt="image" src="https://github.com/user-attachments/assets/8bd36c10-a5c7-4eb7-b946-628161b3ad59" />

ModelFoundry is a premium, self-hosted Django web application for managing, visualizing, and tracking 3D printing projects. It provides a comprehensive Bill of Materials (BOM), real-time cost estimation, and a modular ecosystem for 3D utilities — all wrapped in a modern glassmorphic UI.

## What's New in v2.1?

- **Three-Phase Background Processing Pipeline:** Bulk part uploads now process files through a non-blocking pipeline — Upload → Thumbnail Rendering → Volume Calculation — with a real-time stepper UI showing per-part progress for each phase. Volume calculations leverage `numpy-stl` and `ProcessPoolExecutor` for multi-core parallelism.
- **Insights Dashboard:** A new analytics page with interactive Chart.js visualizations including financial breakdowns, material burn rate tracking by filament type, and project summary metrics (biggest volume, most expensive).
- **Slicer Inbox & Desktop Agent:** A companion desktop agent monitors your slicer output directories and automatically syncs print metadata (filament usage, print time) to ModelFoundry via API. An inbox view lets you assign or dismiss incoming slices.
- **MQTT Printer Integration:** Connect Bambu Lab printers via MQTT for real-time machine status monitoring, including online/offline detection and last-seen timestamps.
- **Open Filament Database (OFD) Integration:** Add materials from the community filament database with cascading brand → type → variant filtering and auto-populated material properties.
- **First-Login Onboarding Wizard:** New installations launch a guided setup wizard to create the first account, reducing friction for self-hosted deployments.
- **Dynamic Theming:** Choose from multiple themes (Midnight Blue, Sunset Ember, Modern Mocha) with persistent per-user preferences.
- **Settings & Diagnostics:** A unified settings page with theme selection, machine management, and live system/machine log viewers for debugging.
- **Premium UI Redesign:** A comprehensive visual overhaul across the entire platform implementing a modern "Glassmorphic" aesthetic. Features include unified premium card layouts, interactive hover states, smart search filtering with dynamic fade animations, and highly polished designer and project detail pages.

---

## Core Features

### Project Management
- Create and organize large 3D printing projects to get a comprehensive Bill of Materials and cost estimate before you print.
- Export and import projects for backup or sharing (fully syncing designer metadata and tags).
- Three-step project creation wizard: Create → Add Parts → Upload Instructions.
<img width="1459" height="185" alt="image" src="https://github.com/user-attachments/assets/ce381ed0-1eee-472a-8158-5afe016b6daa" />


### Advanced Part Tracking
- Add, organize, and group 3D printed parts with support for STL, 3MF, OBJ, and STEP files.
- Automatic 3MF explosion: multi-body 3MF files are parsed into individual sub-parts.
- Track completion status using interactive "one-tap" progress steppers.
- Real-time BOM calculations (costs and volumes) automatically updated in the UI.
- Integrated 3D STL viewer with quick "Forge Actions" dropdowns.
- Blender-powered thumbnail rendering via a dedicated background sidecar service.
<img width="1472" height="692" alt="image" src="https://github.com/user-attachments/assets/7905634a-d832-41c2-98a5-da255e4db840" />


### Bulk Upload with Live Progress
- Upload multiple parts at once with a three-phase progress stepper:
  - **Phase 1 — Upload Files (0-33%):** Real-time byte-level progress via XHR events.
  - **Phase 2 — Generate Thumbnails (34-66%):** Per-part progress as the Blender sidecar renders each thumbnail. Gracefully skipped if Blender is offline.
  - **Phase 3 — Calculate Volumes (67-100%):** Multi-core parallel computation using `numpy-stl` and `ProcessPoolExecutor`.
- Progress is tracked server-side and polled by the frontend every 500ms for a responsive, accurate UI.

### The Forge Ecosystem
- Install standalone 3D utilities directly from GitHub via our `manifest.json` architecture.
- Run compute-heavy tasks safely in isolated virtual environments managed by Docker.
- Available modules:
  - **Grid Slicer** — Split large models into printable grid sections with automatic connector generation.
  - **Rune Etcher** — Engrave text and patterns onto 3D model surfaces.
  - **Sizer** — Scale models to exact target dimensions.
<img width="1482" height="805" alt="image" src="https://github.com/user-attachments/assets/e00e125b-15a4-4f71-85b4-43a1cd5f3df9" />


### Dynamic Material & Designer Management
- Create material profiles tracking cost, density, and color.
- Import materials from the Open Filament Database with cascading brand/type/variant filters.
- Create designer profiles with links to MyMiniFactory, Patreon, Cults3D, and personal websites.
- Instantly search projects and designers via real-time global search.

### Interactive Assembly Instructions
- Build step-by-step assembly guides with embedded photos and videos.
- Cinematic viewing experience via `GLightbox` with custom hover effects.
<img width="1459" height="568" alt="image" src="https://github.com/user-attachments/assets/2a8ba44f-57b0-4a52-bad6-ccf08b2ee758" />


### Insights Dashboard
- Financial breakdown of costs across all projects.
- Material burn rate charts tracking consumption by filament type over time.
- Summary metrics: biggest volume, most expensive project, total parts tracked.

### Printer & Slicer Integration
- **MQTT Monitoring:** Connect Bambu Lab printers for real-time online/offline status.
- **Slicer Inbox:** Desktop agent watches your slicer output folder and syncs print metadata to ModelFoundry via API.
- **Machine Logs:** View real-time MQTT logs per machine directly from the settings page.

---

## Installation & Deployment

**ModelFoundry requires Docker Compose as the preferred and officially supported deployment method.** The multi-service architecture includes the core Django app, a PostgreSQL database, and a dedicated Blender geometry processing sidecar.

### Quick Start (Docker Compose)

1. Clone the repository:
```bash
git clone https://github.com/CodemonsterSoftware/ModelFoundry.git
cd ModelFoundry
```

2. Build and start the entire stack:
```bash
docker compose up --build -d
```
*This automatically spins up the Web App, PostgreSQL database, and Blender background service.*

3. Access ModelFoundry at `http://localhost` and follow the first-login setup wizard to create your account.

That's it!

---

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DEMO_BANNER_TEXT` | *(empty)* | If set, displays a dismissible banner at the top of every page with the specified text. Useful for demo instances. |
| `DJANGO_SECRET_KEY` | auto-generated | Django secret key for production deployments. |

---

### Local Development (Advanced)

If you are actively developing Forge modules or modifying core Django views, you may want to run the web application locally without Docker to take advantage of your IDE's debugger.

1. Start the backend services via Docker:
```bash
docker compose up db blender -d
```
2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```
3. Install dependencies:
```bash
pip install -r requirements.txt
```
4. Run migrations and start the server:
```bash
python manage.py migrate
python manage.py runserver
```

### Upgrading ModelFoundry

**Important:** Your data is stored in Docker volumes and will persist when you upgrade, as long as you don't use the `-v` flag.

#### Safe Upgrade Process (Preserves All Data):

1. **Pull the latest code:**
   ```bash
   git pull origin main
   ```

2. **Stop the containers (data is safe - volumes persist):**
   ```bash
   docker compose down
   ```

3. **Rebuild and start (migrations run automatically):**
   ```bash
   docker compose up --build -d
   ```

4. **Verify everything is working:**
   ```bash
   docker compose logs -f web
   ```


#### Data Backup (Recommended Before Upgrades):

```bash
# Backup database
docker compose exec db pg_dump -U postgres modelfoundry > backup_$(date +%Y%m%d_%H%M%S).sql

# Backup database volume
docker run --rm \
  -v modelfoundry_postgres_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/postgres_backup_$(date +%Y%m%d_%H%M%S).tar.gz /data

# Backup media volume (uploaded files)
docker run --rm \
  -v modelfoundry_media_volume:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/media_backup_$(date +%Y%m%d_%H%M%S).tar.gz /data
```

## Architecture

```
ModelFoundry/
├── modelfoundry/             # Django project configuration & settings
├── projects/                 # Core application
│   ├── models.py             # Data models (Project, Part, Material, Machine, etc.)
│   ├── views.py              # View functions & API endpoints
│   ├── signals.py            # Post-save hooks (thumbnail generation)
│   ├── volume.py             # Background processing pipeline (thumbnails + volumes)
│   ├── progress.py           # In-memory progress tracking for uploads
│   ├── templates/            # HTML templates
│   ├── static/               # CSS, JS, images
│   └── management/           # Custom management commands (MQTT listener)
├── forge/                    # Forge Module Framework
│   ├── modules/              # Installed module repositories
│   ├── module_registry.py    # Dynamic module discovery & loading
│   └── services/             # Service clients (BlenderClient)
├── blender_service/          # Headless Blender sidecar (thumbnail rendering)
├── mqtt/                     # MQTT printer integration
├── docker-compose.yml        # Production service orchestration
├── requirements.txt          # Python dependencies
└── manage.py                 # Django management script
```

## Technology Stack

- **Backend:** Django 5.x, PostgreSQL, Gunicorn
- **Frontend:** Bootstrap 5, jQuery, Chart.js, SplideJS, GLightbox, THREE.js
- **3D Processing:** numpy-stl, trimesh, Blender (headless sidecar)
- **Infrastructure:** Docker Compose, MQTT (Bambu Lab integration)
- **Image Processing:** django-imagekit

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.
