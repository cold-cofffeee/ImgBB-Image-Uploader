# ImgBB Desktop Manager (Python)

A desktop UI application for complete ImgBB image management with account-based login.

## What this app does

- User registration and login (local SQLite database)
- Per-user ImgBB API key storage
- Upload local images to ImgBB using API v1
- Save upload history (image URL, delete URL, metadata)
- Download uploaded images to any folder
- Delete image records locally and optionally attempt remote deletion via ImgBB delete URL

## Tech stack

- Python 3.10+
- Tkinter (desktop UI)
- SQLite (local persistence)
- Requests (HTTP calls)

## Setup

1. Install dependency:

   ```bash
   pip install -r requirements.txt
   ```

2. Run the desktop app:

   ```bash
   python desktop_app.py
   ```

3. In the app:
   - Register a user and login
   - Open **Settings** and apply your ImgBB API key for current session (or set `IMGBB_API_KEY` in OS environment)
   - Open **Upload** to select and upload images
   - Use **Library** to open URL, copy URL, download, or delete records/images

## Security checklist before GitHub push

- Do not commit `images.db`, `.env`, or editor-local folders.
- Keep API keys out of source files and out of SQLite; this app uses session memory or `IMGBB_API_KEY` environment variable.
- Rotate your ImgBB API key immediately if it was ever exposed publicly.

## API details used

- Endpoint: `https://api.imgbb.com/1/upload`
- Method: `POST` (multipart/form-data)
- Required fields: `key`, `image`
- Optional field: `expiration` (60 to 15552000 seconds)

## Database

- `images.db` is created automatically on first launch.
- Schema creation and safe migrations are handled in `create_db.py`.
