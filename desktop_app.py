import base64
import hashlib
import hmac
import os
import re
import sqlite3
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests

from create_db import create_db

DB_PATH = "images.db"
UPLOAD_ENDPOINT = "https://api.imgbb.com/1/upload"
DEFAULT_TIMEOUT = 30
MAX_IMAGE_SIZE_BYTES = 32 * 1024 * 1024


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, stored_hash):
    try:
        if "$" in stored_hash:
            salt_b64, digest_b64 = stored_hash.split("$", 1)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
            return hmac.compare_digest(candidate, expected)

        legacy_sha256 = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy_sha256, stored_hash)
    except Exception:
        return False


class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def create_user(self, username, password):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, hash_password(password)),
            )

    def get_user_by_username(self, username):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def insert_image(self, user_id, source_path, payload):
        image_info = payload.get("image") or {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO images (
                    user_id, title, source_path, imgbb_id, url, display_url, delete_url,
                    mime, size_bytes, width, height, uploaded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    user_id,
                    payload.get("title"),
                    source_path,
                    payload.get("id"),
                    payload.get("url"),
                    payload.get("display_url"),
                    payload.get("delete_url"),
                    image_info.get("mime"),
                    self._to_int(payload.get("size")),
                    self._to_int(payload.get("width")),
                    self._to_int(payload.get("height")),
                ),
            )

    def list_images(self, user_id):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT id, title, url, display_url, delete_url, source_path, uploaded_at
                FROM images
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()

    def delete_images(self, image_ids, user_id):
        if not image_ids:
            return
        placeholders = ",".join("?" for _ in image_ids)
        values = [*image_ids, user_id]
        with self.connect() as conn:
            conn.execute(
                f"DELETE FROM images WHERE id IN ({placeholders}) AND user_id = ?",
                values,
            )

    @staticmethod
    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class ImgBBService:
    def __init__(self, api_key):
        self.api_key = api_key

    def upload_file(self, file_path, expiration=None):
        params = {"key": self.api_key}
        if expiration:
            params["expiration"] = expiration

        with open(file_path, "rb") as source:
            response = requests.post(
                UPLOAD_ENDPOINT,
                params=params,
                files={"image": source},
                timeout=DEFAULT_TIMEOUT,
            )

        data = response.json()
        if response.status_code != 200 or not data.get("success"):
            message = data.get("error", {}).get("message") or "ImgBB upload failed"
            raise RuntimeError(message)
        return data["data"]

    @staticmethod
    def delete_remote(delete_url):
        if not delete_url:
            return False
        response = requests.get(delete_url, timeout=DEFAULT_TIMEOUT)
        return response.ok

    @staticmethod
    def download_file(url, target_path):
        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        with open(target_path, "wb") as output:
            output.write(response.content)


class ImgBBDesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ImgBB Desktop Manager")
        self.root.geometry("1020x680")
        self.root.minsize(960, 620)

        self.db = Database()
        self.current_user = None
        self.selected_files = []
        self.session_api_key = ""

        self._setup_style()
        self._build_ui()

    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 10))

    def _build_ui(self):
        shell = ttk.Frame(self.root, padding=16)
        shell.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(shell)
        header.pack(fill=tk.X)
        ttk.Label(header, text="ImgBB Professional Desktop Manager", style="Title.TLabel").pack(side=tk.LEFT)

        self.auth_status_var = tk.StringVar(value="Not logged in")
        ttk.Label(header, textvariable=self.auth_status_var, style="Header.TLabel").pack(side=tk.RIGHT)

        self.notebook = ttk.Notebook(shell)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(12, 8))

        self.auth_frame = ttk.Frame(self.notebook, padding=12)
        self.upload_frame = ttk.Frame(self.notebook, padding=12)
        self.library_frame = ttk.Frame(self.notebook, padding=12)
        self.settings_frame = ttk.Frame(self.notebook, padding=12)

        self.notebook.add(self.auth_frame, text="Authentication")
        self.notebook.add(self.upload_frame, text="Upload")
        self.notebook.add(self.library_frame, text="Library")
        self.notebook.add(self.settings_frame, text="Settings")

        self._build_auth_tab()
        self._build_upload_tab()
        self._build_library_tab()
        self._build_settings_tab()

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(shell, textvariable=self.status_var, style="Status.TLabel").pack(fill=tk.X)

        self._set_logged_out_state()

    def _build_auth_tab(self):
        frame = self.auth_frame
        ttk.Label(frame, text="Username", style="Header.TLabel").grid(row=0, column=0, sticky="w", pady=(8, 4))
        self.username_entry = ttk.Entry(frame, width=40)
        self.username_entry.grid(row=1, column=0, sticky="w")

        ttk.Label(frame, text="Password", style="Header.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.password_entry = ttk.Entry(frame, width=40, show="*")
        self.password_entry.grid(row=3, column=0, sticky="w")

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, sticky="w", pady=16)
        ttk.Button(actions, text="Register", command=self.register).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Login", command=self.login).pack(side=tk.LEFT, padx=(0, 8))
        self.logout_button = ttk.Button(actions, text="Logout", command=self.logout)
        self.logout_button.pack(side=tk.LEFT)

        ttk.Label(
            frame,
            text="Create an account, then login to manage uploads, downloads, and deletions.",
        ).grid(row=5, column=0, sticky="w", pady=(6, 0))

    def _build_upload_tab(self):
        frame = self.upload_frame

        top = ttk.Frame(frame)
        top.pack(fill=tk.X)

        ttk.Button(top, text="Select Images", command=self.select_images).pack(side=tk.LEFT)
        self.upload_button = ttk.Button(top, text="Upload Selected", command=self.upload_selected)
        self.upload_button.pack(side=tk.LEFT, padx=8)

        ttk.Label(top, text="Expiration (seconds, optional):").pack(side=tk.LEFT, padx=(20, 6))
        self.expiration_var = tk.StringVar()
        self.expiration_entry = ttk.Entry(top, width=12, textvariable=self.expiration_var)
        self.expiration_entry.pack(side=tk.LEFT)

        self.selected_var = tk.StringVar(value="No files selected")
        ttk.Label(frame, textvariable=self.selected_var).pack(anchor="w", pady=(10, 8))

        self.files_list = tk.Listbox(frame, height=18)
        self.files_list.pack(fill=tk.BOTH, expand=True)

    def _build_library_tab(self):
        frame = self.library_frame

        controls = ttk.Frame(frame)
        controls.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(controls, text="Refresh", command=self.refresh_library).pack(side=tk.LEFT)
        ttk.Button(controls, text="Open URL", command=self.open_selected_url).pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="Copy URL", command=self.copy_selected_url).pack(side=tk.LEFT)
        ttk.Button(controls, text="Download", command=self.download_selected).pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="Delete", command=self.delete_selected).pack(side=tk.LEFT)

        columns = ("id", "title", "url", "uploaded")
        self.images_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        self.images_tree.heading("id", text="ID")
        self.images_tree.heading("title", text="Title")
        self.images_tree.heading("url", text="Image URL")
        self.images_tree.heading("uploaded", text="Uploaded At")
        self.images_tree.column("id", width=80, anchor="center")
        self.images_tree.column("title", width=180)
        self.images_tree.column("url", width=500)
        self.images_tree.column("uploaded", width=180)
        self.images_tree.pack(fill=tk.BOTH, expand=True)

    def _build_settings_tab(self):
        frame = self.settings_frame

        ttk.Label(frame, text="ImgBB API Key", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Use session-only key or IMGBB_API_KEY environment variable (not stored in project files).",
        ).pack(anchor="w", pady=(2, 10))

        self.api_key_var = tk.StringVar(value=os.getenv("IMGBB_API_KEY", ""))
        self.api_key_entry = ttk.Entry(frame, width=70, textvariable=self.api_key_var, show="*")
        self.api_key_entry.pack(anchor="w")

        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w", pady=12)
        ttk.Button(buttons, text="Save API Key", command=self.save_api_key).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Reveal/Hide", command=self.toggle_api_key_visibility).pack(side=tk.LEFT, padx=8)

    def _set_logged_out_state(self):
        self.current_user = None
        self.session_api_key = ""
        self.api_key_var.set("")
        self.auth_status_var.set("Not logged in")
        self.logout_button.state(["disabled"])
        self.upload_button.state(["disabled"])
        self.notebook.tab(1, state="disabled")
        self.notebook.tab(2, state="disabled")
        self.notebook.tab(3, state="disabled")

    def _set_logged_in_state(self, user):
        self.current_user = user
        self.auth_status_var.set(f"Logged in as: {user['username']}")
        self.logout_button.state(["!disabled"])
        self.upload_button.state(["!disabled"])
        self.notebook.tab(1, state="normal")
        self.notebook.tab(2, state="normal")
        self.notebook.tab(3, state="normal")

    def register(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        if not username or not password:
            messagebox.showerror("Validation", "Username and password are required.")
            return
        if len(username) < 3 or len(username) > 64:
            messagebox.showerror("Validation", "Username must be between 3 and 64 characters.")
            return
        if len(password) < 8:
            messagebox.showerror("Validation", "Password must be at least 8 characters.")
            return

        try:
            self.db.create_user(username, password)
        except sqlite3.IntegrityError:
            messagebox.showerror("Register", "Username already exists.")
            return

        messagebox.showinfo("Register", "Registration successful. You can now login.")
        self.status_var.set("Registered new user.")

    def login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        user = self.db.get_user_by_username(username)

        if not user or not verify_password(password, user["password_hash"]):
            messagebox.showerror("Login", "Invalid username or password.")
            return

        self._set_logged_in_state(user)
        saved_key = self._get_saved_api_key(user["username"])
        if saved_key:
            self.api_key_var.set(saved_key)
            self.session_api_key = saved_key

        self.refresh_library()
        self.status_var.set("Login successful.")
        self.notebook.select(self.upload_frame)

    def logout(self):
        self._set_logged_out_state()
        self.selected_files = []
        self.files_list.delete(0, tk.END)
        self.selected_var.set("No files selected")
        self.images_tree.delete(*self.images_tree.get_children())
        self.status_var.set("Logged out.")

    def save_api_key(self):
        if not self.current_user:
            messagebox.showerror("Settings", "Login first to save API key.")
            return

        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showerror("Settings", "API key cannot be empty.")
            return
        if not re.fullmatch(r"[A-Za-z0-9]{20,80}", api_key):
            messagebox.showerror("Settings", "API key format looks invalid.")
            return

        self.session_api_key = api_key
        self.status_var.set("API key applied for current session.")
        messagebox.showinfo("Settings", "API key applied for current app session.")

    def toggle_api_key_visibility(self):
        current_show = self.api_key_entry.cget("show")
        self.api_key_entry.configure(show="" if current_show else "*")

    def select_images(self):
        if not self.current_user:
            messagebox.showerror("Upload", "Login first.")
            return

        files = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp")],
        )

        valid_files = []
        rejected = []
        for file_path in files:
            try:
                size = Path(file_path).stat().st_size
                if size > MAX_IMAGE_SIZE_BYTES:
                    rejected.append(f"{Path(file_path).name} > 32MB")
                    continue
                valid_files.append(file_path)
            except OSError:
                rejected.append(f"{Path(file_path).name} unreadable")

        self.selected_files = valid_files
        self.files_list.delete(0, tk.END)
        for file_path in self.selected_files:
            self.files_list.insert(tk.END, file_path)

        self.selected_var.set(f"{len(self.selected_files)} file(s) selected")
        if rejected:
            messagebox.showwarning("Some files skipped", "\n".join(rejected[:10]))

    def upload_selected(self):
        if not self.current_user:
            messagebox.showerror("Upload", "Login first.")
            return

        api_key = (self.api_key_var.get().strip() or self.session_api_key).strip()
        if not api_key:
            messagebox.showerror("Upload", "Add your API key in Settings.")
            self.notebook.select(self.settings_frame)
            return

        if not self.selected_files:
            messagebox.showerror("Upload", "No files selected.")
            return

        expiration = self.expiration_var.get().strip() or None
        if expiration and (not expiration.isdigit() or not 60 <= int(expiration) <= 15552000):
            messagebox.showerror("Upload", "Expiration must be 60 to 15552000 seconds.")
            return

        service = ImgBBService(api_key)
        uploaded = 0
        failures = []

        for file_path in self.selected_files:
            try:
                payload = service.upload_file(file_path, expiration=expiration)
                self.db.insert_image(self.current_user["id"], file_path, payload)
                uploaded += 1
            except Exception as exc:
                failures.append(f"{Path(file_path).name}: {exc}")

        self.refresh_library()
        if uploaded:
            self.status_var.set(f"Uploaded {uploaded} image(s)")
        if failures:
            messagebox.showwarning("Upload completed with errors", "\n".join(failures[:10]))

    def refresh_library(self):
        if not self.current_user:
            return

        rows = self.db.list_images(self.current_user["id"])
        self.images_tree.delete(*self.images_tree.get_children())
        for row in rows:
            self.images_tree.insert(
                "",
                tk.END,
                iid=str(row["id"]),
                values=(
                    row["id"],
                    row["title"] or "",
                    row["url"],
                    row["uploaded_at"] or "",
                ),
            )
        self.status_var.set(f"Loaded {len(rows)} image(s)")

    def _selected_rows(self):
        if not self.current_user:
            return []

        selected_ids = [int(item_id) for item_id in self.images_tree.selection()]
        if not selected_ids:
            return []

        images = {row["id"]: row for row in self.db.list_images(self.current_user["id"])}
        return [images[image_id] for image_id in selected_ids if image_id in images]

    def open_selected_url(self):
        rows = self._selected_rows()
        if not rows:
            messagebox.showerror("Library", "Select at least one image.")
            return

        webbrowser.open(rows[0]["url"])
        self.status_var.set("Opened image URL in browser.")

    def copy_selected_url(self):
        rows = self._selected_rows()
        if not rows:
            messagebox.showerror("Library", "Select at least one image.")
            return

        urls = "\n".join(row["url"] for row in rows)
        self.root.clipboard_clear()
        self.root.clipboard_append(urls)
        self.status_var.set(f"Copied {len(rows)} URL(s) to clipboard")

    def download_selected(self):
        rows = self._selected_rows()
        if not rows:
            messagebox.showerror("Library", "Select image(s) to download.")
            return

        target_dir = filedialog.askdirectory(title="Select destination folder")
        if not target_dir:
            return

        success = 0
        failures = []
        for row in rows:
            url = row["url"]
            target_name = self._file_name_from_row(row, success)
            target_path = Path(target_dir) / target_name
            try:
                ImgBBService.download_file(url, target_path)
                success += 1
            except Exception as exc:
                failures.append(f"{target_name}: {exc}")

        self.status_var.set(f"Downloaded {success} file(s)")
        if failures:
            messagebox.showwarning("Download completed with errors", "\n".join(failures[:10]))

    def delete_selected(self):
        rows = self._selected_rows()
        if not rows:
            messagebox.showerror("Library", "Select image(s) to delete.")
            return

        remote_delete = messagebox.askyesno(
            "Delete",
            "Delete on ImgBB too? Choose No for local record only.",
        )

        failed_remote = []
        if remote_delete:
            for row in rows:
                try:
                    deleted = ImgBBService.delete_remote(row["delete_url"])
                    if not deleted:
                        failed_remote.append(str(row["id"]))
                except Exception:
                    failed_remote.append(str(row["id"]))

        image_ids = [row["id"] for row in rows]
        self.db.delete_images(image_ids, self.current_user["id"])
        self.refresh_library()

        if failed_remote:
            messagebox.showwarning(
                "Delete",
                "Local records removed. Remote deletion could not be confirmed for IDs: " + ", ".join(failed_remote),
            )
        self.status_var.set(f"Deleted {len(rows)} image record(s)")

    @staticmethod
    def _get_saved_api_key(username):
        del username
        return os.getenv("IMGBB_API_KEY", "").strip()

    @staticmethod
    def _file_name_from_row(row, fallback_index):
        url = row["url"] or ""
        slug = url.split("/")[-1] if "/" in url else ""
        if slug:
            safe_slug = re.sub(r"[^A-Za-z0-9._-]", "_", Path(slug).name)
            if safe_slug:
                return safe_slug
        title = (row["title"] or "image").replace(" ", "_")
        safe_title = re.sub(r"[^A-Za-z0-9._-]", "_", title)
        return f"{safe_title}_{fallback_index}.bin"


def main():
    create_db(DB_PATH)

    root = tk.Tk()
    app = ImgBBDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
