import base64
import hashlib
import hmac
import os
import queue
import re
import sqlite3
import threading
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

BG = "#F8FAFC"
SURFACE = "#FFFFFF"
SURFACE_MUTED = "#F9FAFB"
PRIMARY = "#2563EB"
TEXT = "#111827"
TEXT_MUTED = "#6B7280"
BORDER = "#E5E7EB"
SUCCESS = "#16A34A"
DANGER = "#DC2626"


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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def create_user(self, username, password):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, hash_password(password)),
            )

    def get_user_by_username(self, username):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def list_images(self, user_id):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT id, title, source_path, url, display_url, delete_url, mime, size_bytes, width, height, uploaded_at
                FROM images
                WHERE user_id=?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()

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
                    payload.get("title") or Path(source_path).stem,
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

    def delete_images(self, user_id, image_ids):
        if not image_ids:
            return
        placeholders = ",".join("?" for _ in image_ids)
        args = [user_id, *image_ids]
        with self.connect() as conn:
            conn.execute(f"DELETE FROM images WHERE user_id=? AND id IN ({placeholders})", args)

    def get_images_by_ids(self, user_id, image_ids):
        if not image_ids:
            return []
        placeholders = ",".join("?" for _ in image_ids)
        args = [user_id, *image_ids]
        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM images WHERE user_id=? AND id IN ({placeholders})",
                args,
            ).fetchall()

    def log_activity(self, user_id, action, image_title="", details=""):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO activity_logs (user_id, action, image_title, details) VALUES (?, ?, ?, ?)",
                (user_id, action, image_title, details),
            )

    def list_activity(self, user_id, limit=200):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT action, image_title, details, created_at
                FROM activity_logs
                WHERE user_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

    def dashboard_stats(self, user_id):
        with self.connect() as conn:
            total_uploads = conn.execute(
                "SELECT COUNT(*) AS c FROM images WHERE user_id=?",
                (user_id,),
            ).fetchone()["c"]
            storage_used = conn.execute(
                "SELECT COALESCE(SUM(size_bytes),0) AS s FROM images WHERE user_id=?",
                (user_id,),
            ).fetchone()["s"]
            recent_uploads = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM images
                WHERE user_id=? AND datetime(uploaded_at) >= datetime('now', '-7 day')
                """,
                (user_id,),
            ).fetchone()["c"]
        return {
            "total_uploads": int(total_uploads or 0),
            "storage_used": int(storage_used or 0),
            "recent_uploads": int(recent_uploads or 0),
        }

    @staticmethod
    def _to_int(value):
        try:
            return int(value)
        except Exception:
            return None


class ImgBBService:
    def __init__(self, api_key):
        self.api_key = api_key

    def upload_file(self, file_path, expiration=None):
        params = {"key": self.api_key}
        if expiration:
            params["expiration"] = str(expiration)
        with open(file_path, "rb") as source:
            response = requests.post(
                UPLOAD_ENDPOINT,
                params=params,
                files={"image": source},
                timeout=DEFAULT_TIMEOUT,
            )
        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"Invalid server response: {exc}")
        if response.status_code != 200 or not data.get("success"):
            message = data.get("error", {}).get("message") or f"Upload failed with status {response.status_code}"
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


class ToastHost:
    def __init__(self, root):
        self.root = root
        self.toasts = []

    def show(self, message, level="info"):
        color = PRIMARY
        if level == "success":
            color = SUCCESS
        elif level == "error":
            color = DANGER

        toast = tk.Frame(self.root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        stripe = tk.Frame(toast, bg=color, width=5)
        stripe.pack(side=tk.LEFT, fill=tk.Y)
        label = tk.Label(toast, text=message, bg=SURFACE, fg=TEXT, font=("Segoe UI", 10), padx=10, pady=8)
        label.pack(side=tk.LEFT, fill=tk.BOTH)

        y_offset = 16 + (len(self.toasts) * 58)
        toast.place(relx=1.0, x=-16, y=y_offset, anchor="ne")
        self.toasts.append(toast)
        self.root.after(3000, lambda: self._dismiss(toast))

    def _dismiss(self, toast):
        if toast in self.toasts:
            self.toasts.remove(toast)
            toast.destroy()
            for index, item in enumerate(self.toasts):
                item.place_configure(y=16 + (index * 58))


class ImgBBDesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ImgBB Desktop Manager")
        self.root.geometry("1320x820")
        self.root.minsize(1080, 700)
        self.root.configure(bg=BG)

        self.db = Database()
        self.toast = ToastHost(root)

        self.current_user = None
        self.session_api_key = ""
        self.use_env_key = tk.BooleanVar(value=True)

        self.library_rows = []
        self.upload_rows = {}
        self.failed_uploads = []
        self.upload_thread = None
        self.upload_queue = queue.Queue()
        self.pending_uploads = queue.Queue()
        self.search_var = tk.StringVar()
        self.expiration_var = tk.StringVar(value="No expiration")
        self.table_mode = tk.StringVar(value="table")
        self.active_page = "Dashboard"
        self.preview_image = None

        self._setup_style()
        self._build_shell()
        self._bind_shortcuts()
        self._poll_upload_events()
        self.switch_page("Dashboard")
        self._refresh_api_status()

    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Base.TFrame", background=BG)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("Sidebar.TFrame", background=SURFACE)
        style.configure("Header.TFrame", background=SURFACE)
        style.configure("Title.TLabel", background=SURFACE, foreground=TEXT, font=("Segoe UI", 14, "bold"))
        style.configure("Body.TLabel", background=SURFACE, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=SURFACE, foreground=TEXT_MUTED, font=("Segoe UI", 9))
        style.configure("SidebarItem.TButton", background=SURFACE, foreground=TEXT, padding=(10, 9), borderwidth=0)
        style.map("SidebarItem.TButton", background=[("active", SURFACE_MUTED)])
        style.configure("Primary.TButton", background=PRIMARY, foreground="#FFFFFF", borderwidth=0, padding=(12, 8))
        style.map("Primary.TButton", background=[("active", "#1D4ED8"), ("disabled", BORDER)])
        style.configure("Soft.TButton", background=SURFACE_MUTED, foreground=TEXT, borderwidth=0, padding=(10, 7))
        style.map("Soft.TButton", background=[("active", "#EEF2FF")])
        style.configure("Danger.TButton", background=DANGER, foreground="#FFFFFF", borderwidth=0, padding=(10, 7))
        style.map("Danger.TButton", background=[("active", "#B91C1C")])

    def _build_shell(self):
        self.shell = tk.Frame(self.root, bg=BG)
        self.shell.pack(fill=tk.BOTH, expand=True)

        self.sidebar = tk.Frame(self.shell, bg=SURFACE, width=240, highlightbackground=BORDER, highlightthickness=1)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        self.main = tk.Frame(self.shell, bg=BG)
        self.main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.header = tk.Frame(self.main, bg=SURFACE, height=68, highlightbackground=BORDER, highlightthickness=1)
        self.header.pack(side=tk.TOP, fill=tk.X)
        self.header.pack_propagate(False)

        self.content = tk.Frame(self.main, bg=BG)
        self.content.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=16, pady=16)

        self._build_sidebar()
        self._build_header()
        self._build_pages()

    def _build_sidebar(self):
        logo = tk.Frame(self.sidebar, bg=SURFACE)
        logo.pack(fill=tk.X, padx=16, pady=(16, 8))
        tk.Label(logo, text="🖼️ ImgBB", bg=SURFACE, fg=TEXT, font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(logo, text="Desktop Manager", bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        self.nav_buttons = {}
        nav = [
            ("Dashboard", "📊"),
            ("Upload", "⬆️"),
            ("Library", "🗂️"),
            ("History", "🕒"),
            ("Settings", "⚙️"),
        ]
        nav_container = tk.Frame(self.sidebar, bg=SURFACE)
        nav_container.pack(fill=tk.X, padx=10, pady=8)

        for name, icon in nav:
            button = tk.Button(
                nav_container,
                text=f"{icon}  {name}",
                relief=tk.FLAT,
                bd=0,
                anchor="w",
                bg=SURFACE,
                fg=TEXT,
                activebackground="#EEF2FF",
                activeforeground=TEXT,
                font=("Segoe UI", 10),
                padx=12,
                pady=9,
                command=lambda n=name: self.switch_page(n),
            )
            button.pack(fill=tk.X, pady=3)
            self.nav_buttons[name] = button

        spacer = tk.Frame(self.sidebar, bg=SURFACE)
        spacer.pack(fill=tk.BOTH, expand=True)

        self.profile_frame = tk.Frame(self.sidebar, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        self.profile_frame.pack(fill=tk.X, padx=12, pady=12)
        self.profile_name = tk.Label(self.profile_frame, text="Guest", bg=SURFACE, fg=TEXT, font=("Segoe UI", 10, "bold"))
        self.profile_name.pack(anchor="w", padx=10, pady=(8, 2))
        self.profile_sub = tk.Label(self.profile_frame, text="Not logged in", bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 9))
        self.profile_sub.pack(anchor="w", padx=10, pady=(0, 8))

        action_row = tk.Frame(self.profile_frame, bg=SURFACE)
        action_row.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.login_btn = tk.Button(
            action_row,
            text="Login",
            relief=tk.FLAT,
            bg=SURFACE_MUTED,
            fg=TEXT,
            activebackground="#E5E7EB",
            padx=10,
            pady=6,
            command=self._open_auth_modal,
        )
        self.login_btn.pack(side=tk.LEFT)
        self.logout_btn = tk.Button(
            action_row,
            text="Logout",
            relief=tk.FLAT,
            bg="#FEE2E2",
            fg=DANGER,
            activebackground="#FECACA",
            padx=10,
            pady=6,
            command=self.logout,
            state=tk.DISABLED,
        )
        self.logout_btn.pack(side=tk.RIGHT)

    def _build_header(self):
        self.header_title = tk.Label(self.header, text="Dashboard", bg=SURFACE, fg=TEXT, font=("Segoe UI", 16, "bold"))
        self.header_title.pack(side=tk.LEFT, padx=18)

        right = tk.Frame(self.header, bg=SURFACE)
        right.pack(side=tk.RIGHT, padx=14)

        search_wrap = tk.Frame(right, bg=SURFACE_MUTED, highlightbackground=BORDER, highlightthickness=1)
        search_wrap.pack(side=tk.LEFT, padx=(0, 10), pady=14)
        tk.Label(search_wrap, text="🔎", bg=SURFACE_MUTED, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(7, 2))
        self.search_entry = tk.Entry(search_wrap, textvariable=self.search_var, bd=0, relief=tk.FLAT, bg=SURFACE_MUTED, fg=TEXT, width=26)
        self.search_entry.pack(side=tk.LEFT, padx=(0, 8), pady=6)
        self.search_var.trace_add("write", lambda *_: self._on_search())

        self.api_status = tk.Label(
            right,
            text="● API Disconnected",
            bg=SURFACE,
            fg=DANGER,
            font=("Segoe UI", 10, "bold"),
        )
        self.api_status.pack(side=tk.LEFT, pady=14)

    def _build_pages(self):
        self.pages = {}
        self._build_dashboard_page()
        self._build_upload_page()
        self._build_library_page()
        self._build_history_page()
        self._build_settings_page()

    def _new_page(self, name):
        page = tk.Frame(self.content, bg=BG)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.pages[name] = page
        return page

    def _build_dashboard_page(self):
        page = self._new_page("Dashboard")

        self.dashboard_cards = tk.Frame(page, bg=BG)
        self.dashboard_cards.pack(fill=tk.X)

        self.card_total = self._create_stat_card(self.dashboard_cards, "Total uploads", "0", 0)
        self.card_storage = self._create_stat_card(self.dashboard_cards, "Storage used", "0 B", 1)
        self.card_recent = self._create_stat_card(self.dashboard_cards, "Recent uploads (7d)", "0", 2)

        info = tk.Frame(page, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        info.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        tk.Label(info, text="Welcome", bg=SURFACE, fg=TEXT, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        tk.Label(
            info,
            text="Use the left navigation to upload images, browse library records, and manage history.",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=16, pady=(0, 12))

        quick = tk.Frame(info, bg=SURFACE)
        quick.pack(anchor="w", padx=16, pady=(0, 16))
        tk.Button(quick, text="Go to Upload", bg=PRIMARY, fg="#FFFFFF", relief=tk.FLAT, padx=12, pady=8, command=lambda: self.switch_page("Upload")).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(quick, text="Open Library", bg=SURFACE_MUTED, fg=TEXT, relief=tk.FLAT, padx=12, pady=8, command=lambda: self.switch_page("Library")).pack(side=tk.LEFT)

    def _create_stat_card(self, container, title, value, column):
        card = tk.Frame(container, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 10, 0), pady=0)
        container.grid_columnconfigure(column, weight=1)
        tk.Label(card, text=title, bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(12, 4))
        value_label = tk.Label(card, text=value, bg=SURFACE, fg=TEXT, font=("Segoe UI", 21, "bold"))
        value_label.pack(anchor="w", padx=14, pady=(0, 12))
        return value_label

    def _build_upload_page(self):
        page = self._new_page("Upload")
        top = tk.Frame(page, bg=BG)
        top.pack(fill=tk.X)

        drop = tk.Frame(top, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, height=120)
        drop.pack(fill=tk.X)
        drop.pack_propagate(False)
        tk.Label(drop, text="⬇️", bg=SURFACE, fg=PRIMARY, font=("Segoe UI", 24)).pack(pady=(18, 0))
        tk.Label(drop, text="Drag & drop zone (click to add files)", bg=SURFACE, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(pady=(2, 0))
        tk.Label(drop, text="Supports PNG, JPG, JPEG, GIF, BMP, WEBP • Max 32MB", bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(pady=(2, 10))
        drop.bind("<Button-1>", lambda _e: self.select_files())

        controls = tk.Frame(page, bg=BG)
        controls.pack(fill=tk.X, pady=(12, 8))

        tk.Button(controls, text="Select Files", bg=SURFACE_MUTED, fg=TEXT, relief=tk.FLAT, padx=12, pady=8, command=self.select_files).pack(side=tk.LEFT)
        tk.Button(controls, text="Start Upload", bg=PRIMARY, fg="#FFFFFF", relief=tk.FLAT, padx=12, pady=8, command=self.start_upload_queue).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(controls, text="Retry Failed", bg=SURFACE_MUTED, fg=TEXT, relief=tk.FLAT, padx=12, pady=8, command=self.retry_failed_uploads).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(controls, text="Expiration:", bg=BG, fg=TEXT, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(20, 6))
        self.expiration_combo = ttk.Combobox(
            controls,
            textvariable=self.expiration_var,
            values=["No expiration", "600", "3600", "86400", "604800", "2592000"],
            width=14,
            state="readonly",
        )
        self.expiration_combo.pack(side=tk.LEFT)

        table_wrap = tk.Frame(page, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        table_wrap.pack(fill=tk.BOTH, expand=True)

        cols = ("name", "size", "progress", "status")
        self.upload_tree = ttk.Treeview(table_wrap, columns=cols, show="headings", selectmode="extended")
        self.upload_tree.heading("name", text="File")
        self.upload_tree.heading("size", text="Size")
        self.upload_tree.heading("progress", text="Progress")
        self.upload_tree.heading("status", text="Status")
        self.upload_tree.column("name", width=440)
        self.upload_tree.column("size", width=120, anchor="center")
        self.upload_tree.column("progress", width=140, anchor="center")
        self.upload_tree.column("status", width=180, anchor="center")

        y_scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.upload_tree.yview)
        self.upload_tree.configure(yscroll=y_scroll.set)
        self.upload_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_library_page(self):
        page = self._new_page("Library")

        top = tk.Frame(page, bg=BG)
        top.pack(fill=tk.X, pady=(0, 8))

        tk.Label(top, text="View:", bg=BG, fg=TEXT, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        ttk.Radiobutton(top, text="Table", variable=self.table_mode, value="table", command=self._render_library_view).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Radiobutton(top, text="Grid", variable=self.table_mode, value="grid", command=self._render_library_view).pack(side=tk.LEFT)

        self.bulk_toolbar = tk.Frame(top, bg=BG)
        self.bulk_toolbar.pack(side=tk.RIGHT)
        tk.Button(self.bulk_toolbar, text="Copy URLs", bg=SURFACE_MUTED, fg=TEXT, relief=tk.FLAT, padx=10, pady=7, command=self.copy_selected_urls).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(self.bulk_toolbar, text="Download", bg=SURFACE_MUTED, fg=TEXT, relief=tk.FLAT, padx=10, pady=7, command=self.download_selected).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(self.bulk_toolbar, text="Delete", bg="#FEE2E2", fg=DANGER, relief=tk.FLAT, padx=10, pady=7, command=self.delete_selected).pack(side=tk.LEFT)

        body = tk.Frame(page, bg=BG)
        body.pack(fill=tk.BOTH, expand=True)

        self.library_left = tk.Frame(body, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        self.library_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.preview_panel = tk.Frame(body, bg=SURFACE, width=320, highlightbackground=BORDER, highlightthickness=1)
        self.preview_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        self.preview_panel.pack_propagate(False)

        self.preview_canvas = tk.Label(self.preview_panel, text="No Image Selected", bg=SURFACE_MUTED, fg=TEXT_MUTED, width=34, height=12)
        self.preview_canvas.pack(fill=tk.X, padx=12, pady=(12, 10))

        self.preview_meta = tk.Label(self.preview_panel, text="Select an item to view metadata", justify=tk.LEFT, bg=SURFACE, fg=TEXT, font=("Segoe UI", 10))
        self.preview_meta.pack(anchor="w", padx=12)

        quick = tk.Frame(self.preview_panel, bg=SURFACE)
        quick.pack(fill=tk.X, padx=12, pady=(12, 0))
        tk.Button(quick, text="Copy URL", bg=SURFACE_MUTED, fg=TEXT, relief=tk.FLAT, command=self.copy_selected_urls).pack(fill=tk.X, pady=(0, 6))
        tk.Button(quick, text="Open URL", bg=SURFACE_MUTED, fg=TEXT, relief=tk.FLAT, command=self.open_selected_url).pack(fill=tk.X, pady=(0, 6))
        tk.Button(quick, text="Download", bg=PRIMARY, fg="#FFFFFF", relief=tk.FLAT, command=self.download_selected).pack(fill=tk.X)

        self._build_library_table_view()
        self._build_library_grid_view()
        self._build_library_context_menu()
        self._render_library_view()

    def _build_library_table_view(self):
        self.table_container = tk.Frame(self.library_left, bg=SURFACE)

        cols = ("id", "thumb", "title", "size", "date", "url")
        self.library_tree = ttk.Treeview(self.table_container, columns=cols, show="headings", selectmode="extended")
        self.library_tree.heading("id", text="ID")
        self.library_tree.heading("thumb", text="Thumbnail")
        self.library_tree.heading("title", text="Title")
        self.library_tree.heading("size", text="Size")
        self.library_tree.heading("date", text="Date")
        self.library_tree.heading("url", text="URL")
        self.library_tree.column("id", width=50, anchor="center")
        self.library_tree.column("thumb", width=90, anchor="center")
        self.library_tree.column("title", width=220)
        self.library_tree.column("size", width=90, anchor="center")
        self.library_tree.column("date", width=160, anchor="center")
        self.library_tree.column("url", width=330)

        y_scroll = ttk.Scrollbar(self.table_container, orient="vertical", command=self.library_tree.yview)
        self.library_tree.configure(yscrollcommand=y_scroll.set)
        self.library_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.library_tree.bind("<<TreeviewSelect>>", lambda _e: self.update_preview())
        self.library_tree.bind("<Button-3>", self._show_context_menu)

    def _build_library_grid_view(self):
        self.grid_container = tk.Frame(self.library_left, bg=SURFACE)
        self.grid_canvas = tk.Canvas(self.grid_container, bg=SURFACE, highlightthickness=0)
        self.grid_scroll = ttk.Scrollbar(self.grid_container, orient="vertical", command=self.grid_canvas.yview)
        self.grid_canvas.configure(yscrollcommand=self.grid_scroll.set)
        self.grid_inner = tk.Frame(self.grid_canvas, bg=SURFACE)
        self.grid_canvas.create_window((0, 0), window=self.grid_inner, anchor="nw")
        self.grid_inner.bind("<Configure>", lambda _e: self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all")))
        self.grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.grid_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_library_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Copy URL", command=self.copy_selected_urls)
        self.context_menu.add_command(label="Open URL", command=self.open_selected_url)
        self.context_menu.add_command(label="Download", command=self.download_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Delete", command=self.delete_selected)

    def _build_history_page(self):
        page = self._new_page("History")

        wrap = tk.Frame(page, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill=tk.BOTH, expand=True)

        cols = ("action", "image", "details", "date")
        self.history_tree = ttk.Treeview(wrap, columns=cols, show="headings")
        self.history_tree.heading("action", text="Action")
        self.history_tree.heading("image", text="Image")
        self.history_tree.heading("details", text="Details")
        self.history_tree.heading("date", text="Date")
        self.history_tree.column("action", width=140, anchor="center")
        self.history_tree.column("image", width=220)
        self.history_tree.column("details", width=420)
        self.history_tree.column("date", width=180, anchor="center")

        y_scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=y_scroll.set)
        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_settings_page(self):
        page = self._new_page("Settings")

        card = tk.Frame(page, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill=tk.X)

        tk.Label(card, text="API Key", bg=SURFACE, fg=TEXT, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        self.api_key_var = tk.StringVar(value=os.getenv("IMGBB_API_KEY", ""))
        entry = tk.Entry(card, textvariable=self.api_key_var, show="*", relief=tk.FLAT, bg=SURFACE_MUTED, fg=TEXT, font=("Segoe UI", 10))
        entry.pack(fill=tk.X, padx=16, pady=(0, 10), ipady=8)

        mode_row = tk.Frame(card, bg=SURFACE)
        mode_row.pack(fill=tk.X, padx=16, pady=(0, 10))
        tk.Checkbutton(
            mode_row,
            text="Use environment key (IMGBB_API_KEY)",
            variable=self.use_env_key,
            onvalue=True,
            offvalue=False,
            bg=SURFACE,
            fg=TEXT,
            activebackground=SURFACE,
            selectcolor=SURFACE,
            font=("Segoe UI", 10),
            command=self._refresh_api_status,
        ).pack(anchor="w")

        self.validation_label = tk.Label(card, text="Status: Disconnected", bg=SURFACE, fg=DANGER, font=("Segoe UI", 10, "bold"))
        self.validation_label.pack(anchor="w", padx=16, pady=(0, 10))

        action = tk.Frame(card, bg=SURFACE)
        action.pack(fill=tk.X, padx=16, pady=(0, 16))
        tk.Button(action, text="Apply Session Key", bg=PRIMARY, fg="#FFFFFF", relief=tk.FLAT, padx=12, pady=8, command=self.apply_api_key).pack(side=tk.LEFT)
        tk.Button(action, text="Reveal/Hide", bg=SURFACE_MUTED, fg=TEXT, relief=tk.FLAT, padx=12, pady=8, command=lambda: entry.config(show="" if entry.cget("show") else "*")).pack(side=tk.LEFT, padx=(8, 0))

        notice = tk.Frame(page, bg="#EFF6FF", highlightbackground="#BFDBFE", highlightthickness=1)
        notice.pack(fill=tk.X, pady=(12, 0))
        tk.Label(
            notice,
            text="Security Notice: API keys are not stored in source files or committed data. Keep your key private and rotate if exposed.",
            bg="#EFF6FF",
            fg="#1E3A8A",
            font=("Segoe UI", 9),
            wraplength=1000,
            justify=tk.LEFT,
        ).pack(anchor="w", padx=12, pady=10)

    def _bind_shortcuts(self):
        self.root.bind("<Control-u>", lambda _e: self.shortcut_upload())
        self.root.bind("<Control-c>", lambda _e: self.shortcut_copy())

    def shortcut_upload(self):
        self.switch_page("Upload")
        self.select_files()

    def shortcut_copy(self):
        if self.active_page == "Library":
            self.copy_selected_urls()

    def switch_page(self, page_name):
        self.active_page = page_name
        self.header_title.configure(text=page_name)
        self.pages[page_name].tkraise()
        self._highlight_nav(page_name)
        self._set_search_visibility(page_name)

        if page_name == "Dashboard":
            self.refresh_dashboard()
        elif page_name == "Library":
            self.refresh_library()
        elif page_name == "History":
            self.refresh_history()
        elif page_name == "Settings":
            self._refresh_api_status()

    def _highlight_nav(self, active):
        for name, button in self.nav_buttons.items():
            if name == active:
                button.configure(bg="#DBEAFE", fg="#1E3A8A")
            else:
                button.configure(bg=SURFACE, fg=TEXT)

    def _set_search_visibility(self, page_name):
        if page_name == "Library":
            self.search_entry.configure(state=tk.NORMAL)
        else:
            self.search_var.set("")
            self.search_entry.configure(state=tk.DISABLED)

    def _open_auth_modal(self):
        modal = tk.Toplevel(self.root)
        modal.title("Authentication")
        modal.geometry("420x320")
        modal.configure(bg=SURFACE)
        modal.transient(self.root)
        modal.grab_set()

        tab = ttk.Notebook(modal)
        tab.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        login_frame = tk.Frame(tab, bg=SURFACE)
        register_frame = tk.Frame(tab, bg=SURFACE)
        tab.add(login_frame, text="Login")
        tab.add(register_frame, text="Register")

        login_user = tk.StringVar()
        login_pass = tk.StringVar()
        self._auth_form(login_frame, login_user, login_pass)

        tk.Button(
            login_frame,
            text="Login",
            bg=PRIMARY,
            fg="#FFFFFF",
            relief=tk.FLAT,
            padx=12,
            pady=8,
            command=lambda: self._handle_login(login_user.get().strip(), login_pass.get().strip(), modal),
        ).pack(anchor="w", padx=20, pady=(6, 0))

        reg_user = tk.StringVar()
        reg_pass = tk.StringVar()
        self._auth_form(register_frame, reg_user, reg_pass)

        tk.Button(
            register_frame,
            text="Create Account",
            bg=PRIMARY,
            fg="#FFFFFF",
            relief=tk.FLAT,
            padx=12,
            pady=8,
            command=lambda: self._handle_register(reg_user.get().strip(), reg_pass.get().strip()),
        ).pack(anchor="w", padx=20, pady=(6, 0))

    def _auth_form(self, parent, user_var, pass_var):
        tk.Label(parent, text="Username", bg=SURFACE, fg=TEXT, font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Entry(parent, textvariable=user_var, bg=SURFACE_MUTED, relief=tk.FLAT, fg=TEXT).pack(fill=tk.X, padx=20, ipady=8)
        tk.Label(parent, text="Password", bg=SURFACE, fg=TEXT, font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(14, 4))
        tk.Entry(parent, textvariable=pass_var, show="*", bg=SURFACE_MUTED, relief=tk.FLAT, fg=TEXT).pack(fill=tk.X, padx=20, ipady=8)

    def _handle_register(self, username, password):
        if len(username) < 3 or len(username) > 64:
            self.toast.show("Username must be 3-64 characters", "error")
            return
        if len(password) < 8:
            self.toast.show("Password must be at least 8 characters", "error")
            return
        try:
            self.db.create_user(username, password)
        except sqlite3.IntegrityError:
            self.toast.show("Username already exists", "error")
            return
        self.toast.show("Account created successfully", "success")

    def _handle_login(self, username, password, modal=None):
        user = self.db.get_user_by_username(username)
        if not user or not verify_password(password, user["password_hash"]):
            self.toast.show("Invalid username or password", "error")
            return

        self.current_user = user
        self.profile_name.configure(text=user["username"])
        self.profile_sub.configure(text="Signed in")
        self.login_btn.configure(state=tk.DISABLED)
        self.logout_btn.configure(state=tk.NORMAL)
        self.db.log_activity(user["id"], "LOGIN", "", "User logged in")
        self.toast.show("Login successful", "success")
        self._refresh_api_status()
        self.refresh_dashboard()
        if modal:
            modal.destroy()

    def logout(self):
        if self.current_user:
            self.db.log_activity(self.current_user["id"], "LOGOUT", "", "User logged out")
        self.current_user = None
        self.profile_name.configure(text="Guest")
        self.profile_sub.configure(text="Not logged in")
        self.login_btn.configure(state=tk.NORMAL)
        self.logout_btn.configure(state=tk.DISABLED)
        self.toast.show("Logged out", "success")
        self.refresh_dashboard()
        self.refresh_library()
        self.refresh_history()
        self._refresh_api_status()

    def apply_api_key(self):
        api_key = self.api_key_var.get().strip()
        if not api_key:
            self.session_api_key = ""
            self._refresh_api_status()
            self.toast.show("Session key cleared", "success")
            return
        if not re.fullmatch(r"[A-Za-z0-9]{20,80}", api_key):
            self.toast.show("API key format looks invalid", "error")
            return
        self.session_api_key = api_key
        self._refresh_api_status()
        self.toast.show("Session API key applied", "success")

    def _resolve_api_key(self):
        if self.use_env_key.get():
            env_key = os.getenv("IMGBB_API_KEY", "").strip()
            if env_key:
                return env_key
        return self.session_api_key.strip()

    def _refresh_api_status(self):
        connected = bool(self._resolve_api_key())
        if connected:
            self.api_status.configure(text="● API Connected", fg=SUCCESS)
            self.validation_label.configure(text="Status: Connected", fg=SUCCESS)
        else:
            self.api_status.configure(text="● API Disconnected", fg=DANGER)
            self.validation_label.configure(text="Status: Disconnected", fg=DANGER)

    def select_files(self):
        if not self._require_login():
            return
        files = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp")],
        )
        if not files:
            return

        skipped = 0
        for file_path in files:
            path_obj = Path(file_path)
            try:
                size = path_obj.stat().st_size
            except OSError:
                skipped += 1
                continue
            if size > MAX_IMAGE_SIZE_BYTES:
                skipped += 1
                continue

            if file_path in self.upload_rows:
                continue

            item_id = self.upload_tree.insert(
                "",
                tk.END,
                values=(path_obj.name, self._format_size(size), "0%", "Queued"),
            )
            self.upload_rows[file_path] = item_id
            self.pending_uploads.put(file_path)

        if skipped:
            self.toast.show(f"{skipped} files skipped (unreadable or >32MB)", "error")
        if files:
            self.toast.show("Files added to upload queue", "success")

    def start_upload_queue(self):
        if not self._require_login():
            return
        api_key = self._resolve_api_key()
        if not api_key:
            self.toast.show("Connect API key first in Settings", "error")
            self.switch_page("Settings")
            return

        if self.upload_thread and self.upload_thread.is_alive():
            self.toast.show("Upload queue already running", "info")
            return

        if self.pending_uploads.empty():
            self.toast.show("No files queued", "error")
            return

        expiration = self._parse_expiration()
        self.upload_thread = threading.Thread(
            target=self._upload_worker,
            args=(api_key, expiration),
            daemon=True,
        )
        self.upload_thread.start()
        self.toast.show("Upload queue started", "info")

    def retry_failed_uploads(self):
        if not self.failed_uploads:
            self.toast.show("No failed uploads to retry", "info")
            return
        for file_path in self.failed_uploads:
            if file_path not in self.upload_rows:
                continue
            self.upload_tree.set(self.upload_rows[file_path], "progress", "0%")
            self.upload_tree.set(self.upload_rows[file_path], "status", "Queued")
            self.pending_uploads.put(file_path)
        self.failed_uploads.clear()
        self.toast.show("Failed uploads re-queued", "info")

    def _parse_expiration(self):
        value = self.expiration_var.get().strip()
        if value == "No expiration" or value == "":
            return None
        if value.isdigit() and 60 <= int(value) <= 15552000:
            return int(value)
        self.toast.show("Invalid expiration; using no expiration", "error")
        return None

    def _upload_worker(self, api_key, expiration):
        service = ImgBBService(api_key)
        while not self.pending_uploads.empty():
            file_path = self.pending_uploads.get()
            item_id = self.upload_rows.get(file_path)
            if not item_id:
                continue

            self.upload_queue.put(("progress", file_path, 10, "Uploading"))
            try:
                payload = service.upload_file(file_path, expiration)
                self.db.insert_image(self.current_user["id"], file_path, payload)
                self.db.log_activity(self.current_user["id"], "UPLOAD", Path(file_path).name, payload.get("url", ""))
                self.upload_queue.put(("done", file_path, 100, "Uploaded"))
            except Exception as exc:
                self.upload_queue.put(("error", file_path, 0, f"Failed: {exc}"))

        self.upload_queue.put(("finished", None, None, "Upload queue completed"))

    def _poll_upload_events(self):
        try:
            while True:
                event, file_path, percent, status = self.upload_queue.get_nowait()
                if event in {"progress", "done", "error"}:
                    item_id = self.upload_rows.get(file_path)
                    if item_id:
                        self.upload_tree.set(item_id, "progress", f"{percent}%")
                        self.upload_tree.set(item_id, "status", status)
                    if event == "error" and file_path not in self.failed_uploads:
                        self.failed_uploads.append(file_path)
                    if event == "done":
                        self.refresh_dashboard()
                elif event == "finished":
                    self.toast.show(status, "success")
                    self.refresh_library()
                    self.refresh_history()
                    self.refresh_dashboard()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_upload_events)

    def refresh_dashboard(self):
        if not self.current_user:
            self.card_total.configure(text="0")
            self.card_storage.configure(text="0 B")
            self.card_recent.configure(text="0")
            return
        stats = self.db.dashboard_stats(self.current_user["id"])
        self.card_total.configure(text=str(stats["total_uploads"]))
        self.card_storage.configure(text=self._format_size(stats["storage_used"]))
        self.card_recent.configure(text=str(stats["recent_uploads"]))

    def refresh_library(self):
        self._show_library_skeleton()
        self.root.after(170, self._load_library_rows)

    def _show_library_skeleton(self):
        self.library_tree.delete(*self.library_tree.get_children())
        for index in range(6):
            self.library_tree.insert("", tk.END, values=("", "", f"Loading row {index + 1}...", "", "", ""))

    def _load_library_rows(self):
        if not self.current_user:
            self.library_rows = []
            self.library_tree.delete(*self.library_tree.get_children())
            self._render_grid([])
            return

        rows = self.db.list_images(self.current_user["id"])
        self.library_rows = rows
        self._render_library_view()

    def _render_library_view(self):
        if self.table_mode.get() == "table":
            self.grid_container.pack_forget()
            self.table_container.pack(fill=tk.BOTH, expand=True)
            self._render_table(self._filtered_rows())
        else:
            self.table_container.pack_forget()
            self.grid_container.pack(fill=tk.BOTH, expand=True)
            self._render_grid(self._filtered_rows())

    def _render_table(self, rows):
        self.library_tree.delete(*self.library_tree.get_children())
        if not rows:
            self.library_tree.insert("", tk.END, values=("", "", "No images yet", "", "", ""))
            return
        for row in rows:
            self.library_tree.insert(
                "",
                tk.END,
                iid=str(row["id"]),
                values=(
                    row["id"],
                    "🖼️",
                    row["title"] or "",
                    self._format_size(row["size_bytes"] or 0),
                    row["uploaded_at"] or "",
                    row["url"] or "",
                ),
            )

    def _render_grid(self, rows):
        for child in self.grid_inner.winfo_children():
            child.destroy()

        if not rows:
            tk.Label(self.grid_inner, text="No images found", bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 11)).grid(row=0, column=0, padx=12, pady=12)
            return

        for index, row in enumerate(rows):
            card = tk.Frame(self.grid_inner, bg=SURFACE_MUTED, highlightbackground=BORDER, highlightthickness=1, width=220, height=140)
            card.grid(row=index // 3, column=index % 3, padx=10, pady=10, sticky="nsew")
            card.grid_propagate(False)
            tk.Label(card, text="🖼️", bg=SURFACE_MUTED, fg=PRIMARY, font=("Segoe UI", 20)).pack(pady=(12, 2))
            tk.Label(card, text=(row["title"] or "Untitled")[:26], bg=SURFACE_MUTED, fg=TEXT, font=("Segoe UI", 10, "bold")).pack()
            tk.Label(card, text=self._format_size(row["size_bytes"] or 0), bg=SURFACE_MUTED, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack()
            tk.Label(card, text=(row["uploaded_at"] or "")[:16], bg=SURFACE_MUTED, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(pady=(0, 8))
            card.bind("<Button-1>", lambda _e, rid=row["id"]: self._select_row_by_id(rid))

    def _select_row_by_id(self, row_id):
        if self.table_mode.get() == "table":
            self.library_tree.selection_set(str(row_id))
        self.update_preview(row_id)

    def _on_search(self):
        if self.active_page == "Library":
            self._render_library_view()

    def _filtered_rows(self):
        query = self.search_var.get().strip().lower()
        if not query:
            return self.library_rows
        filtered = []
        for row in self.library_rows:
            hay = " ".join([
                str(row["title"] or ""),
                str(row["url"] or ""),
                str(row["mime"] or ""),
                str(row["uploaded_at"] or ""),
            ]).lower()
            if query in hay:
                filtered.append(row)
        return filtered

    def _show_context_menu(self, event):
        region = self.library_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row_id = self.library_tree.identify_row(event.y)
        if row_id:
            self.library_tree.selection_set(row_id)
            self.update_preview()
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def update_preview(self, explicit_row_id=None):
        row = None
        if explicit_row_id is not None:
            for item in self.library_rows:
                if int(item["id"]) == int(explicit_row_id):
                    row = item
                    break
        else:
            if self.table_mode.get() == "table":
                sel = self.library_tree.selection()
                if sel:
                    rid = int(sel[0])
                    for item in self.library_rows:
                        if int(item["id"]) == rid:
                            row = item
                            break

        if not row:
            self.preview_canvas.configure(image="", text="No Image Selected")
            self.preview_meta.configure(text="Select an item to view metadata")
            self.preview_image = None
            return

        self.preview_canvas.configure(image="", text="Preview")
        source_path = row["source_path"] or ""
        shown = False
        if source_path and Path(source_path).exists() and Path(source_path).suffix.lower() in {".png", ".gif"}:
            try:
                img = tk.PhotoImage(file=source_path)
                if img.width() > 280:
                    scale = max(1, img.width() // 280)
                    img = img.subsample(scale, scale)
                self.preview_canvas.configure(image=img, text="")
                self.preview_image = img
                shown = True
            except Exception:
                shown = False

        if not shown:
            self.preview_canvas.configure(image="", text="Image Preview")
            self.preview_image = None

        meta = (
            f"Title: {row['title'] or '-'}\n"
            f"Size: {self._format_size(row['size_bytes'] or 0)}\n"
            f"Type: {row['mime'] or '-'}\n"
            f"Dimensions: {row['width'] or '-'} x {row['height'] or '-'}\n"
            f"Uploaded: {row['uploaded_at'] or '-'}"
        )
        self.preview_meta.configure(text=meta)

    def _selected_library_ids(self):
        if self.table_mode.get() == "table":
            ids = []
            for rid in self.library_tree.selection():
                try:
                    ids.append(int(rid))
                except ValueError:
                    continue
            return ids
        return []

    def _selected_library_rows(self):
        ids = self._selected_library_ids()
        if not ids or not self.current_user:
            return []
        return self.db.get_images_by_ids(self.current_user["id"], ids)

    def copy_selected_urls(self):
        rows = self._selected_library_rows()
        if not rows:
            self.toast.show("No images selected", "error")
            return
        urls = "\n".join(row["url"] for row in rows if row["url"])
        self.root.clipboard_clear()
        self.root.clipboard_append(urls)
        self.toast.show(f"Copied {len(rows)} URL(s)", "success")

    def open_selected_url(self):
        rows = self._selected_library_rows()
        if not rows:
            self.toast.show("No image selected", "error")
            return
        webbrowser.open(rows[0]["url"])

    def download_selected(self):
        rows = self._selected_library_rows()
        if not rows:
            self.toast.show("No images selected", "error")
            return

        target_dir = filedialog.askdirectory(title="Select destination folder")
        if not target_dir:
            return

        ok = 0
        fails = 0
        for index, row in enumerate(rows):
            target_name = self._safe_filename(row["url"], row["title"], index)
            target = Path(target_dir) / target_name
            try:
                ImgBBService.download_file(row["url"], target)
                ok += 1
            except Exception:
                fails += 1

        self.toast.show(f"Downloaded {ok} file(s), failed {fails}", "success" if fails == 0 else "error")

    def delete_selected(self):
        rows = self._selected_library_rows()
        if not rows:
            self.toast.show("No images selected", "error")
            return

        confirm = messagebox.askyesno(
            "Confirm Delete",
            "Delete selected records?\nChoose Yes to continue.",
            parent=self.root,
        )
        if not confirm:
            return

        remote = messagebox.askyesno(
            "Remote Delete",
            "Also delete selected images on ImgBB?",
            parent=self.root,
        )

        failed_remote = 0
        if remote:
            for row in rows:
                try:
                    if not ImgBBService.delete_remote(row["delete_url"]):
                        failed_remote += 1
                except Exception:
                    failed_remote += 1

        ids = [int(row["id"]) for row in rows]
        self.db.delete_images(self.current_user["id"], ids)
        for row in rows:
            self.db.log_activity(self.current_user["id"], "DELETE", row["title"] or "", row["url"] or "")

        self.refresh_library()
        self.refresh_dashboard()
        self.refresh_history()
        if failed_remote:
            self.toast.show(f"Deleted locally. Remote delete failed for {failed_remote}", "error")
        else:
            self.toast.show("Delete completed", "success")

    def refresh_history(self):
        self.history_tree.delete(*self.history_tree.get_children())
        if not self.current_user:
            self.history_tree.insert("", tk.END, values=("-", "-", "Login to see activity", "-"))
            return

        rows = self.db.list_activity(self.current_user["id"])
        if not rows:
            self.history_tree.insert("", tk.END, values=("-", "-", "No activity yet", "-"))
            return

        for row in rows:
            self.history_tree.insert(
                "",
                tk.END,
                values=(
                    row["action"],
                    row["image_title"] or "-",
                    row["details"] or "-",
                    row["created_at"],
                ),
            )

    def _require_login(self):
        if self.current_user:
            return True
        self.toast.show("Please login first", "error")
        self._open_auth_modal()
        return False

    @staticmethod
    def _format_size(size):
        try:
            size = int(size)
        except Exception:
            size = 0
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        index = 0
        while value >= 1024 and index < len(units) - 1:
            value /= 1024.0
            index += 1
        return f"{value:.1f} {units[index]}"

    @staticmethod
    def _safe_filename(url, title, fallback_index):
        slug = (url or "").split("/")[-1]
        if slug:
            clean = re.sub(r"[^A-Za-z0-9._-]", "_", Path(slug).name)
            if clean:
                return clean
        base = re.sub(r"[^A-Za-z0-9._-]", "_", (title or "image"))
        return f"{base}_{fallback_index}.bin"


def main():
    create_db(DB_PATH)
    root = tk.Tk()
    app = ImgBBDesktopApp(root)
    app.refresh_dashboard()
    app.refresh_library()
    app.refresh_history()
    root.mainloop()


if __name__ == "__main__":
    main()
