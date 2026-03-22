# ImgBB Desktop Manager

Professional desktop image manager for ImgBB, built with Python + Tkinter.

The app provides end-to-end workflow from a native-feeling desktop UI:

- User authentication (register/login/logout)
- API key connection (environment or session mode)
- Background upload queue with retry
- Library table/grid view with bulk actions
- Import existing ImgBB URLs into local library
- History timeline for activity logs

## Features

### Enterprise-style desktop UI
- Fixed left sidebar navigation (Dashboard, Upload, Library, History, Settings)
- Minimal top header with page title, search, and API status
- Dynamic main content panels
- Toast notifications and destructive confirmation dialogs
- Startup in maximized mode

### Authentication and security
- Local account system with secure password hashing (`PBKDF2-HMAC-SHA256`, salted)
- Legacy SHA256 hash compatibility for older records
- API key is not hardcoded in source
- API key can be sourced from `IMGBB_API_KEY` env var or session entry in Settings

### Upload management
- Upload via ImgBB API v1 (`POST` + multipart form)
- Optional expiration support (`60` to `15552000` seconds)
- Queue-based non-blocking uploads (threaded worker)
- Per-file status/progress and retry failed uploads
- File size guard (`<= 32 MB`)

### Library management
- Table and grid views
- Multi-select actions: copy URLs, download, delete
- Right-click context menu
- Right-side preview panel with metadata
- Search/filter in library view
- Import existing ImgBB URLs (manual account-library sync)

### History and analytics
- Activity history (`LOGIN`, `LOGOUT`, `UPLOAD`, `DELETE`, `IMPORT`)
- Dashboard cards for total uploads, storage used, recent uploads (7 days)

## Important ImgBB API Limitation

ImgBB API upload keys do not provide a direct endpoint to fetch/list all images from your ImgBB account gallery.

Because of this, the app library shows:
1. images uploaded through this app, and
2. images manually imported through **Library → Import URL**.

If you already have images in your ImgBB account, use **Import URL** to bring them into the local app library.

## Tech Stack

- Python 3.10+
- Tkinter / ttk
- SQLite (`images.db`)
- Requests (`requests`)

## Project Structure

```text
ImgBB-Image-Uploader/
├─ desktop_app.py      # Main application (UI + services + interactions)
├─ create_db.py        # Schema creation and migration helpers
├─ requirements.txt    # Dependencies
├─ .gitignore          # Ignore local/sensitive artifacts
└─ README.md
```

## Setup (No Virtual Environment Required)

1. Install dependencies:

```bash
py -m pip install -r requirements.txt
```

2. Optional: set environment API key:

```powershell
setx IMGBB_API_KEY "YOUR_IMGBB_API_KEY"
```

3. Run:

```bash
py desktop_app.py
```

## Usage

1. Launch app (opens maximized)
2. Login/Register from sidebar profile card
3. Go to **Settings** and connect API key:
   - Enable environment mode, or
   - Apply session key manually
4. Go to **Upload**:
   - Add files
   - Choose expiration
   - Start upload queue
   - Retry failures if needed
5. Go to **Library**:
   - Manage uploaded/imported records
   - Copy URLs, download, delete
   - Use **Import URL** for existing ImgBB images
6. Go to **History** for activity tracking

## Database Schema (Current)

Database file: `images.db`

### users
- `id` (PK)
- `username` (unique)
- `password_hash`
- `created_at`

### user_settings
- `user_id` (PK/FK)
- `updated_at`

### images
- `id` (PK)
- `user_id` (FK)
- `title`
- `source_path`
- `imgbb_id`
- `url`
- `display_url`
- `delete_url`
- `mime`
- `size_bytes`
- `width`
- `height`
- `uploaded_at`

### activity_logs
- `id` (PK)
- `user_id` (FK)
- `action`
- `image_title`
- `details`
- `created_at`

## Keyboard Shortcuts

- `Ctrl+U` → Open Upload page and select files
- `Ctrl+C` → Copy selected URL(s) in Library page

## Security Checklist Before GitHub Push

- Do not commit `images.db`, `.env`, `.vscode`, or caches
- Keep API keys out of source and committed files
- Rotate ImgBB API key if exposed

## Troubleshooting

### Library shows empty but account has images
Use **Library → Import URL**. This is expected due to ImgBB API listing limitations for upload keys.

### Upload fails
- Confirm API key is valid
- Confirm file size is `<= 32 MB`
- Check network connectivity

### `Import "requests" could not be resolved`

```bash
py -m pip install -r requirements.txt
```

## License

Add your preferred license (MIT recommended) before public release.

