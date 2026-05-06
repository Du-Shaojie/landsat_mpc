"""SQLite + CSV file-level download status tracking."""

from __future__ import annotations

import csv
import os
import sqlite3
import threading
from datetime import datetime

import config


class DownloadDB:
    _local = threading.local()

    def __init__(self, db_path: str = config.DB_PATH, csv_path: str = config.CSV_PATH):
        self._db_path = db_path
        self._csv_path = csv_path
        self._write_lock = threading.Lock()
        self._csv_dirty = False
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path, timeout=60)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA busy_timeout=60000")
            self._local.conn.execute("PRAGMA journal_mode=DELETE")
        return self._local.conn

    def _execute(self, func):
        with self._write_lock:
            result = func(self._get_conn())
            self._get_conn().commit()
            return result

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS download_status (
                path             INTEGER NOT NULL,
                row              INTEGER NOT NULL,
                year_month       TEXT    NOT NULL,
                scene_id         TEXT    NOT NULL,
                product          TEXT    NOT NULL,
                source           TEXT,
                band_name        TEXT,
                filename         TEXT    NOT NULL,
                status           TEXT    NOT NULL DEFAULT 'pending',
                remote_href      TEXT,
                s3_key           TEXT,
                local_dir        TEXT,
                cloud_cover      REAL,
                file_size_bytes  INTEGER,
                error_message    TEXT,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (path, row, year_month, scene_id, product, filename)
            )
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(download_status)").fetchall()}
        migrations = [
            ("source", "ALTER TABLE download_status ADD COLUMN source TEXT"),
            ("remote_href", "ALTER TABLE download_status ADD COLUMN remote_href TEXT"),
            ("s3_key", "ALTER TABLE download_status ADD COLUMN s3_key TEXT"),
            ("band_name", "ALTER TABLE download_status ADD COLUMN band_name TEXT"),
            ("file_size_bytes", "ALTER TABLE download_status ADD COLUMN file_size_bytes INTEGER"),
            ("error_message", "ALTER TABLE download_status ADD COLUMN error_message TEXT"),
        ]
        for column, sql in migrations:
            if column not in existing:
                conn.execute(sql)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON download_status(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scene ON download_status(scene_id, product)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scene_selection (
                path        INTEGER NOT NULL,
                row         INTEGER NOT NULL,
                year_month  TEXT    NOT NULL,
                scene_id_l2 TEXT    NOT NULL,
                scene_id_l1 TEXT,
                cloud_cover REAL,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (path, row, year_month)
            )
        """)
        conn.commit()

    def save_scene_selection(self, path: int, row: int, year_month: str, scene_id_l2: str, scene_id_l1: str | None, cloud_cover: float):
        def _do(conn):
            conn.execute("""
                INSERT OR REPLACE INTO scene_selection
                    (path, row, year_month, scene_id_l2, scene_id_l1, cloud_cover, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (path, row, year_month, scene_id_l2, scene_id_l1, cloud_cover, datetime.now().isoformat()))
        self._execute(_do)

    def get_scene_selection(self, path: int, row: int, year_month: str) -> dict | None:
        cur = self._get_conn().execute("""
            SELECT * FROM scene_selection WHERE path = ? AND row = ? AND year_month = ?
        """, (path, row, year_month))
        row_data = cur.fetchone()
        return dict(row_data) if row_data else None

    def mark_file_downloading(self, path: int, row: int, year_month: str, scene_id: str, product: str, filename: str, local_dir: str, source: str, remote_href: str, band_name: str | None):
        def _do(conn):
            now = datetime.now().isoformat()
            conn.execute("""
                INSERT OR IGNORE INTO download_status
                    (path, row, year_month, scene_id, product, source, band_name, filename,
                     status, remote_href, s3_key, local_dir, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'downloading', ?, ?, ?, ?)
            """, (path, row, year_month, scene_id, product, source, band_name, filename, remote_href, remote_href, local_dir, now))
            conn.execute("""
                UPDATE download_status
                SET status = 'downloading', source = ?, band_name = COALESCE(?, band_name),
                    remote_href = ?, s3_key = ?, local_dir = ?, updated_at = ?
                WHERE path = ? AND row = ? AND year_month = ? AND scene_id = ?
                  AND product = ? AND filename = ?
            """, (source, band_name, remote_href, remote_href, local_dir, now, path, row, year_month, scene_id, product, filename))
        self._execute(_do)

    def mark_file_downloaded(self, path: int, row: int, year_month: str, scene_id: str, product: str, filename: str, local_dir: str, source: str, remote_href: str, band_name: str | None = None, file_size_bytes: int | None = None):
        def _do(conn):
            conn.execute("""
                INSERT OR REPLACE INTO download_status
                    (path, row, year_month, scene_id, product, source, band_name, filename,
                     status, remote_href, s3_key, local_dir, file_size_bytes, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'downloaded', ?, ?, ?, ?, NULL, ?)
            """, (path, row, year_month, scene_id, product, source, band_name, filename, remote_href, remote_href, local_dir, file_size_bytes, datetime.now().isoformat()))
            self._csv_dirty = True
        self._execute(_do)

    def mark_file_failed(self, path: int, row: int, year_month: str, scene_id: str, product: str, filename: str, source: str, remote_href: str, band_name: str | None = None, error_message: str | None = None):
        def _do(conn):
            conn.execute("""
                INSERT OR REPLACE INTO download_status
                    (path, row, year_month, scene_id, product, source, band_name, filename,
                     status, remote_href, s3_key, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?, ?)
            """, (path, row, year_month, scene_id, product, source, band_name, filename, remote_href, remote_href, error_message, datetime.now().isoformat()))
            self._csv_dirty = True
        self._execute(_do)

    def is_file_downloaded(self, path: int, row: int, year_month: str, scene_id: str, product: str, filename: str) -> bool:
        cur = self._get_conn().execute("""
            SELECT status FROM download_status
            WHERE path = ? AND row = ? AND year_month = ? AND scene_id = ?
              AND product = ? AND filename = ?
        """, (path, row, year_month, scene_id, product, filename))
        row_data = cur.fetchone()
        return row_data is not None and row_data["status"] == "downloaded"

    def is_scene_fully_downloaded(self, path: int, row: int, year_month: str) -> bool:
        cur = self._get_conn().execute("""
            SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'downloaded' THEN 1 ELSE 0 END) AS done
            FROM download_status
            WHERE path = ? AND row = ? AND year_month = ? AND status != 'data_missing'
        """, (path, row, year_month))
        row_data = cur.fetchone()
        return row_data["total"] > 0 and row_data["total"] == row_data["done"]

    def get_failed_records(self) -> list[dict]:
        cur = self._get_conn().execute("SELECT * FROM download_status WHERE status = 'failed'")
        return [dict(r) for r in cur.fetchall()]

    def reset_failed_to_pending(self):
        def _do(conn):
            conn.execute("UPDATE download_status SET status = 'pending', updated_at = ? WHERE status = 'failed'", (datetime.now().isoformat(),))
        self._execute(_do)

    def reset_downloading_to_pending(self):
        def _do(conn):
            conn.execute("UPDATE download_status SET status = 'pending', updated_at = ? WHERE status = 'downloading'", (datetime.now().isoformat(),))
        self._execute(_do)

    def sync_csv_if_dirty(self):
        if not self._csv_dirty:
            return
        with self._write_lock:
            _sync_csv(self._get_conn(), self._csv_path)
            self._csv_dirty = False


def should_skip_downloaded(db: DownloadDB, path: int, row: int, year_month: str) -> bool:
    return db.is_scene_fully_downloaded(path, row, year_month)


def _sync_csv(conn: sqlite3.Connection, csv_path: str):
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    rows = conn.execute("""
        SELECT path, row, year_month, scene_id, product, source, band_name, filename, status,
               file_size_bytes, local_dir, remote_href, cloud_cover, error_message, created_at, updated_at
        FROM download_status
        ORDER BY path, row, year_month, scene_id, product, filename
    """).fetchall()
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "path", "row", "year_month", "scene_id", "product", "source", "band_name",
            "filename", "status", "file_size_bytes", "local_dir", "remote_href",
            "cloud_cover", "error_message", "created_at", "updated_at",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
