# ImgBB Desktop Manager

A production-style desktop image management tool built with Python + Tkinter for complete ImgBB workflow management:

- User authentication (register/login/logout)
- Upload images to ImgBB
- Browse upload history
- Copy/open hosted URLs
- Download hosted images locally
- Delete records locally and optionally delete from ImgBB

This project is designed as a secure, GitHub-ready desktop app with no hardcoded secrets.

---

## 1) Project Goals

This application was built to provide **full image lifecycle management from desktop UI**, not just upload:

1. Authenticate users locally
2. Accept ImgBB API key securely at runtime (session/env)
3. Upload images with optional expiration
4. Persist upload metadata in SQLite
5. Allow download/open/copy/delete actions from a library view
6. Keep repository safe for public publishing

---

## 2) Core Features

### Authentication
- Register new users
- Login with password verification
- Logout and clear session state

### ImgBB Integration
- Uses ImgBB API v1 upload endpoint
- Upload through `POST` + `multipart/form-data`
- Supports optional upload expiration (`60` to `15552000` seconds)

### Image Management
- Local upload history table (per user)
- Open selected image URL in browser
- Copy one/multiple selected URLs
- Download selected images to chosen folder
- Delete local records and optionally call ImgBB `delete_url`

### Security-focused behavior
- No hardcoded API key in source
- API key not persisted to project files/DB
- Supports `IMGBB_API_KEY` environment variable
- Input validation for username/password/API key
- File size guard for ImgBB max limit (32 MB)
- Download filename sanitization

---

## 3) Tech Stack

- **Language:** Python 3.10+
- **UI:** Tkinter + ttk
- **Database:** SQLite (`images.db`)
- **HTTP client:** `requests`
- **OS:** Works on Windows (current target), portable to other OS with Python/Tkinter support

---

## 4) Architecture & Design

The app follows a simple layered design:

### UI Layer
Implemented in `ImgBBDesktopApp` class inside `desktop_app.py`.

- Multi-tab interface: Authentication, Upload, Library, Settings
- Responsible for user interaction, feedback, and screen state

### Data Layer
Implemented by `Database` class in `desktop_app.py` and schema/migration helpers in `create_db.py`.

- Creates and migrates SQLite schema safely
- Stores users and image metadata
- Uses parameterized SQL queries

### Service Layer
Implemented by `ImgBBService` class in `desktop_app.py`.

- Upload requests to ImgBB
- Remote deletion call via `delete_url`
- File download from hosted URL

---

## 5) Methods and Practices Used

### Password handling
- Passwords are not stored in plain text
- Uses PBKDF2-HMAC-SHA256 with random salt (`120000` iterations)
- Legacy SHA256 hashes are supported for backward compatibility migration
- Constant-time comparison via `hmac.compare_digest`

### Database migration strategy
- `create_db.py` creates required tables if missing
- Adds missing legacy columns when old DB versions exist
- Preserves old data where possible
- Clears any legacy API-key DB values to reduce secret persistence risk

### Defensive coding
- Parameterized DB writes
- Request timeout enforcement
- Upload failure aggregation with user-visible warnings
- Guard clauses for invalid states (not logged in, no files selected, invalid expiration)

---

## 6) ImgBB API Details Used

### Endpoint
`https://api.imgbb.com/1/upload`

### Request method
`POST`

### Parameters
- `key` (required): API key
- `image` (required): binary image file
- `expiration` (optional): delete-after seconds (`60` to `15552000`)

### Response usage
The app stores:
- `id`
- `title`
- `url`
- `display_url`
- `delete_url`
- image metadata (`mime`, size, width, height)

---

## 7) Database Schema

Database file: `images.db`

### `users`
- `id` (PK)
- `username` (unique)
- `password_hash`
- `created_at`

### `user_settings`
- `user_id` (PK, FK to users)
- `updated_at`

### `images`
- `id` (PK)
- `user_id` (FK to users)
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

---

## 8) Project Structure

```text
ImgBB-Image-Uploader/
├─ desktop_app.py      # Main desktop app (UI + DB access + API service)
├─ create_db.py        # Schema creation and migration helpers
├─ requirements.txt    # Python dependencies
├─ .gitignore          # Prevents committing local/sensitive artifacts
└─ README.md           # Documentation
```

---

## 9) Setup & Run (No Virtual Environment Required)

> This project can run directly with your system Python.

1. Install dependencies:

```bash
py -m pip install -r requirements.txt
```

2. (Optional but recommended) set API key as environment variable:

```powershell
setx IMGBB_API_KEY "YOUR_IMGBB_API_KEY"
```

3. Start app:

```bash
py desktop_app.py
```

---

## 10) How to Use

1. Open app
2. Register with username/password
3. Login
4. Go to **Settings**:
   - Paste API key for session use, or
   - rely on `IMGBB_API_KEY` from environment
5. Go to **Upload**:
   - Select images
   - Optional expiration
   - Upload
6. Go to **Library** to:
   - Refresh records
   - Open URL
   - Copy URL(s)
   - Download selected images
   - Delete local records (and optionally remote ImgBB images)

---

## 11) Security Notes

### What is protected
- No API keys in source code
- No API keys written into SQLite by current implementation
- Sensitive/generated files excluded via `.gitignore`

### Before pushing to GitHub
- Confirm `.gitignore` is present
- Ensure `images.db` is not tracked
- Ensure no `.env` or secrets are committed
- Rotate key immediately if it was exposed anywhere

### Current limitations
- Local auth DB is app-level (not enterprise IAM)
- Deletion via `delete_url` depends on ImgBB endpoint behavior

---

## 12) Error Handling & Validation

- Upload expiration validated in allowed range
- Oversized files (>32 MB) are rejected before upload
- Network failures are captured and shown as warnings
- App keeps processing remaining files if one upload fails

---

## 13) Troubleshooting

### `Import "requests" could not be resolved`
Install dependencies with:

```bash
py -m pip install -r requirements.txt
```

### Login fails for legacy users
Legacy hash compatibility exists, but if old DB is inconsistent, delete `images.db` and start clean (if data retention is not required).

### Upload fails with API error
- Verify key is valid
- Confirm image size <= 32 MB
- Check network connectivity

---

## 14) Future Enhancements

- Add secure OS keyring integration (optional mode)
- Add background upload queue with progress bars
- Add search/filter/sort in library view
- Add image thumbnail previews
- Package as Windows `.exe` installer

---

## 15) License

Use your preferred license file (MIT recommended) when publishing publicly.

