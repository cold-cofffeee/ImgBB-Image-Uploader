import sqlite3


DB_PATH = "images.db"


def _table_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def _add_column_if_missing(cursor, table_name, column_name, definition):
    columns = _table_columns(cursor, table_name)
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def create_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA foreign_keys = ON")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    user_columns = _table_columns(cursor, "users")
    if "password_hash" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if "created_at" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    if "password" in user_columns:
        cursor.execute(
            """
            UPDATE users
            SET password_hash = password
            WHERE (password_hash IS NULL OR password_hash = '') AND password IS NOT NULL
            """
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    settings_columns = _table_columns(cursor, "user_settings")
    if "imgbb_api_key" in settings_columns:
        cursor.execute("UPDATE user_settings SET imgbb_api_key = NULL")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT,
            source_path TEXT,
            imgbb_id TEXT,
            url TEXT NOT NULL,
            display_url TEXT,
            delete_url TEXT,
            mime TEXT,
            size_bytes INTEGER,
            width INTEGER,
            height INTEGER,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    legacy_columns = {
        "title": "TEXT",
        "source_path": "TEXT",
        "imgbb_id": "TEXT",
        "display_url": "TEXT",
        "mime": "TEXT",
        "size_bytes": "INTEGER",
        "width": "INTEGER",
        "height": "INTEGER",
        "uploaded_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "user_id": "INTEGER",
    }
    for column_name, definition in legacy_columns.items():
        _add_column_if_missing(cursor, "images", column_name, definition)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    create_db()
