from __future__ import annotations

import json
import os
import re
import sys
import sqlite3
import threading
import traceback
import shutil
import hashlib
import html
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests
from browser_scraper import BrowserScraper
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QSize
from PyQt5.QtGui import QFont, QPixmap, QDesktopServices, QMovie, QImageReader
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QLineEdit,
    QTextEdit, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QStackedWidget,
    QListWidget, QListWidgetItem, QFileDialog, QMessageBox, QDialog, QFormLayout,
    QDialogButtonBox, QCheckBox, QSpinBox, QComboBox, QSplitter, QProgressBar,
    QSizePolicy, QMenu, QAction, QInputDialog, QTabBar
)
try:
    from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
    from PyQt5.QtMultimediaWidgets import QVideoWidget
    MULTIMEDIA_AVAILABLE = True
except Exception:
    MULTIMEDIA_AVAILABLE = False

APP_NAME = "Twitter Media Downloader"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DOWNLOAD_DIR = ROOT / "Twitter_Archive"
DB_PATH = DATA_DIR / "archive.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

DARK_QSS = r"""
* { font-family: "Segoe UI", Arial; }
QMainWindow, QWidget { background: #000000; color: #e7e9ea; }
QFrame#sidebar { background: #000000; border-right: 1px solid #2f3336; }
QFrame#rightbar { background: #000000; }
QFrame#topbar { background: #000000; border-bottom: 1px solid #2f3336; }
QLabel { color: #e7e9ea; }
QLabel#muted { color: #71767b; }
QLabel#brand { font-size: 28px; font-weight: 700; padding: 8px 0 12px 8px; }
QPushButton { background: transparent; color: #e7e9ea; border: 0; padding: 10px 14px; text-align: left; border-radius: 22px; font-size: 15px; }
QPushButton:hover { background: #181818; }
QPushButton#nav { font-size: 18px; padding: 11px 15px; }
QPushButton#nav[active="true"] { font-weight: 800; }
QPushButton#primary { background: #eff3f4; color: #0f1419; font-weight: 800; text-align: center; padding: 13px; }
QPushButton#primary:hover { background: #d7dbdc; }
QPushButton#blue { color: #1d9bf0; }
QLineEdit, QTextEdit, QComboBox, QSpinBox { background: #000; border: 1px solid #333639; border-radius: 18px; padding: 10px 13px; color: #e7e9ea; selection-background-color: #1d9bf0; }
QLineEdit:focus, QTextEdit:focus { border: 1px solid #1d9bf0; }
QFrame#post { background: #000; border-bottom: 1px solid #2f3336; }
QFrame#post:hover { background: #050505; }
QFrame#panel { border: 1px solid #2f3336; border-radius: 18px; background: #000; }
QLabel#postText { font-size: 14px; color: #e7e9ea; }
QLabel#handle { color: #71767b; font-size: 13px; }
QLabel#sectionTitle { font-size: 21px; font-weight: 800; }
QLabel#postName { font-weight: 800; font-size: 14px; }
QLabel#blueText { color: #1d9bf0; }
QScrollArea { border: 0; background: #000; }
QListWidget { background: #000; border: 0; }
QProgressBar { border: 0; background: #202327; border-radius: 4px; height: 7px; text-align: center; }
QProgressBar::chunk { background: #1d9bf0; border-radius: 4px; }
QMenu { background: #16181c; border: 1px solid #333; }
QMenu::item:selected { background: #24272b; }
QDialog { background: #000; }
"""

DEFAULT_SETTINGS = {
    "api_base": "http://127.0.0.1:8000",
    "api_token": "",
    "socialdata_api_key": "",
    "twitterapis_api_key": "",
    "timeline_provider": "TwitterAPIs",
    "socialdata_base": "https://api.socialdata.tools",
    "twitterapis_base": "https://api.twitterapis.com/twitter",
    "timeline_path": "/api/user/{username}/tweets?limit={limit}&include_replies=true",
    "bookmarks_path": "/api/bookmarks?limit={limit}",
    "likes_path": "/api/user/{username}/likes?limit={limit}",
    "following_path": "/api/user/{username}/following?limit={limit}",
    "lists_path": "/api/user/{username}/lists",
    "download_dir": str(DOWNLOAD_DIR),
    "browser_cookies": "none",
    "max_height": 0,
    "request_timeout": 30,
}



def classify_post(post: dict, media_items=None) -> str:
    """Return X profile-tab category: post, reply, repost or media.

    Media is a view/filter across all posts, not an exclusive post kind.
    """
    flags = post if isinstance(post, dict) else {}
    legacy = flags.get("legacy") if isinstance(flags.get("legacy"), dict) else {}
    refs = flags.get("referenced_tweets") or flags.get("references") or legacy.get("referenced_tweets") or []
    ref_types = {str(x.get("type", "")).lower() for x in refs if isinstance(x, dict)}
    is_repost = bool(flags.get("is_retweet") or flags.get("is_repost") or flags.get("retweeted_status") or flags.get("retweeted_tweet") or flags.get("retweeted")) or "retweeted" in ref_types
    if is_repost:
        return "repost"
    is_reply = bool(flags.get("is_reply") or flags.get("in_reply_to_status_id") or flags.get("in_reply_to_tweet_id") or flags.get("reply_to") or legacy.get("is_reply") or legacy.get("in_reply_to_status_id")) or "replied_to" in ref_types
    if is_reply:
        return "reply"
    return "post"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_created_at(value: Any) -> str:
    """Normalize common X/API timestamp formats to sortable UTC ISO strings."""
    if value is None or value == "":
        return ""
    raw = str(value).strip()
    try:
        if raw.isdigit():
            number = float(raw)
            if number > 10_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, timezone.utc).isoformat(timespec="seconds")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        pass
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return raw


def safe_name(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")
    return value[:90] or "unknown"


IMAGE_FORMATS = {
    b"\xff\xd8\xff": (".jpg", "image/jpeg"),
    b"\x89PNG\r\n\x1a\n": (".png", "image/png"),
    b"GIF87a": (".gif", "image/gif"),
    b"GIF89a": (".gif", "image/gif"),
    b"BM": (".bmp", "image/bmp"),
    b"II*\x00": (".tif", "image/tiff"),
    b"MM\x00*": (".tif", "image/tiff"),
    b"\x00\x00\x01\x00": (".ico", "image/x-icon"),
}


def detect_image_format(data: bytes, content_type: str = "", url: str = ""):
    """Identify an image by its file signature, never by its URL suffix alone."""
    head = bytes(data[:64])
    for signature, result in IMAGE_FORMATS.items():
        if head.startswith(signature):
            return result
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis", b"mif1", b"heic", b"heix", b"hevc", b"hevx"):
            return (".avif" if brand in (b"avif", b"avis") else ".heic"), ("image/avif" if brand in (b"avif", b"avis") else "image/heic")
    # A response MIME type is only a hint. It is not enough to accept a file
    # without a recognized signature because CDN error pages can be mislabeled.
    return None


def image_file_is_valid(path: Path) -> bool:
    """Cheap integrity check used before reusing an old archive image."""
    try:
        if not path.is_file() or path.stat().st_size < 16:
            return False
        with path.open("rb") as stream:
            head = stream.read(64)
        return detect_image_format(head, url=str(path)) is not None
    except Exception:
        return False


def download_image_response(url: str, target: Path, headers=None, timeout=(20, 120)) -> Path:
    """Stream an image to disk, verify its signature, and correct its extension.

    The temporary file is never exposed as a completed download. The actual
    format is derived from the bytes returned by the CDN, so query-string
    transformations and misleading URL suffixes cannot create mislabeled files.
    """
    if not url:
        raise ValueError("Image URL is empty")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part")
    request_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://x.com/",
    }
    if headers:
        request_headers.update(headers)
    try:
        with requests.get(url, headers=request_headers, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if any(x in content_type for x in ("text/html", "application/json", "text/plain")):
                raise IOError(f"CDN returned {content_type or 'a text response'} instead of an image")
            expected = response.headers.get("Content-Length")
            expected_size = int(expected) if expected and str(expected).isdigit() else None
            total = 0
            with temp.open("wb") as out:
                for chunk in response.iter_content(256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 150 * 1024 * 1024:
                        raise IOError("Image exceeded the 150 MB safety limit")
                    out.write(chunk)
            if total < 16:
                raise IOError("Downloaded image is empty or truncated")
            if expected_size is not None and total != expected_size:
                raise IOError(f"Incomplete image download ({total} of {expected_size} bytes)")
            with temp.open("rb") as stream:
                signature = stream.read(64)
            detected = detect_image_format(signature, content_type, url)
            if not detected:
                raise IOError(f"Downloaded response is not a recognized image (Content-Type: {content_type or 'unknown'})")
            extension, _mime = detected
            # Validate with Qt when its image plugin supports this format. A
            # missing optional decoder must not corrupt or discard a valid file.
            try:
                reader = QImageReader(str(temp))
                reader.setDecideFormatFromContent(True)
                if reader.canRead():
                    image = reader.read()
                    if image.isNull():
                        raise IOError("Image decoder rejected the downloaded image")
            except IOError:
                raise
            except Exception:
                pass
            final = target.with_suffix(extension)
            if final.exists():
                final.unlink()
            temp.replace(final)
            return final
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def load_image_pixmap(path: str) -> QPixmap:
    """Decode image content independently of a possibly incorrect file suffix."""
    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)
    image = reader.read()
    if not image.isNull():
        return QPixmap.fromImage(image)
    pixmap = QPixmap()
    pixmap.load(str(path))
    return pixmap



class ArchiveDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self._init()

    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
              post_id TEXT PRIMARY KEY, username TEXT, display_name TEXT, text TEXT,
              created_at TEXT, post_url TEXT, media_json TEXT DEFAULT '[]',
              raw_json TEXT DEFAULT '{}', source_collection TEXT DEFAULT 'timeline',
              is_bookmarked INTEGER DEFAULT 0, is_liked INTEGER DEFAULT 0,
              is_watch_later INTEGER DEFAULT 0, watched INTEGER DEFAULT 0, archived_at TEXT, post_kind TEXT DEFAULT 'post'
            );
            CREATE TABLE IF NOT EXISTS accounts (
              username TEXT PRIMARY KEY, display_name TEXT, bio TEXT, avatar_url TEXT,
              banner_url TEXT, followers INTEGER, following INTEGER, last_scan TEXT,
              avatar_path TEXT, banner_path TEXT
            );
            CREATE TABLE IF NOT EXISTS downloads (
              id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, media_url TEXT,
              local_path TEXT, status TEXT, error TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS scan_state (
              username TEXT PRIMARY KEY, cursor TEXT, scanned_count INTEGER DEFAULT 0,
              updated_at TEXT
            );
            """)
            # Lightweight schema migration for an existing v1 database.
            columns = {row[1] for row in c.execute("PRAGMA table_info(posts)").fetchall()}
            if "is_watch_later" not in columns:
                c.execute("ALTER TABLE posts ADD COLUMN is_watch_later INTEGER DEFAULT 0")
            if "post_kind" not in columns:
                c.execute("ALTER TABLE posts ADD COLUMN post_kind TEXT DEFAULT 'post'")
            account_columns = {row[1] for row in c.execute("PRAGMA table_info(accounts)").fetchall()}
            if "avatar_path" not in account_columns:
                c.execute("ALTER TABLE accounts ADD COLUMN avatar_path TEXT")
            if "banner_path" not in account_columns:
                c.execute("ALTER TABLE accounts ADD COLUMN banner_path TEXT")
            c.executescript("""
            CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(username);
            CREATE INDEX IF NOT EXISTS idx_posts_date ON posts(created_at);
            CREATE INDEX IF NOT EXISTS idx_posts_collection ON posts(source_collection);
            """)
            # Backfill category labels for databases created before v1.1.3.
            legacy_rows = c.execute("SELECT post_id,raw_json,media_json,created_at FROM posts").fetchall()
            for legacy_row in legacy_rows:
                try:
                    raw_post = json.loads(legacy_row["raw_json"] or "{}")
                    media_post = json.loads(legacy_row["media_json"] or "[]")
                    kind = classify_post(raw_post, media_post)
                    normalized_date = normalize_created_at(legacy_row["created_at"])
                    c.execute("UPDATE posts SET post_kind=?,created_at=? WHERE post_id=?", (kind, normalized_date, legacy_row["post_id"]))
                except Exception:
                    continue

    def upsert_account(self, data: dict):
        username = (data.get("username") or data.get("screen_name") or "").lstrip("@")
        if not username:
            return
        with self.connect() as c:
            c.execute("""INSERT INTO accounts(username,display_name,bio,avatar_url,banner_url,followers,following,last_scan)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET
              display_name=COALESCE(excluded.display_name,accounts.display_name),
              bio=COALESCE(excluded.bio,accounts.bio),
              avatar_url=COALESCE(excluded.avatar_url,accounts.avatar_url),
              banner_url=COALESCE(excluded.banner_url,accounts.banner_url),
              followers=COALESCE(excluded.followers,accounts.followers),
              following=COALESCE(excluded.following,accounts.following),last_scan=excluded.last_scan""",
              (username, data.get("display_name") or data.get("name"), data.get("bio") or data.get("description"),
               data.get("avatar_url") or data.get("avatar"), data.get("banner_url") or data.get("banner"),
               data.get("followers_count") or data.get("followers"), data.get("following_count") or data.get("following"), now_iso()))

    def update_account_asset_paths(self, username: str, avatar_path: str = "", banner_path: str = ""):
        with self.connect() as c:
            c.execute("""UPDATE accounts SET
                avatar_path=COALESCE(NULLIF(?,''),avatar_path),
                banner_path=COALESCE(NULLIF(?,''),banner_path)
                WHERE LOWER(username)=LOWER(?)""", (avatar_path, banner_path, username.lstrip("@")))

    def save_post(self, post: dict, collection: str = "timeline", fallback_username: str = "") -> bool:
        pid = str(post.get("post_id") or post.get("id") or post.get("rest_id") or "")
        if not pid:
            return False
        user = (post.get("user") or post.get("author") or post.get("user_info") or post.get("account") or {})
        if isinstance(user, str):
            username, display = user.lstrip("@"), user.lstrip("@")
        elif isinstance(user, dict):
            username = (post.get("username") or post.get("screen_name") or user.get("username") or
                        user.get("screen_name") or user.get("handle") or user.get("screen_name_with_twitter") or "").lstrip("@")
            display = post.get("display_name") or user.get("display_name") or user.get("name") or username
        else:
            username, display = "", ""
        # Provider tweet payloads sometimes omit the author handle or return only an ID.
        # Preserve the account being scanned instead of filing those posts under 'unknown'.
        if not username or username.lower() == "unknown":
            username = (fallback_username or "unknown").lstrip("@")
        if not display:
            display = username
        text = post.get("text") or post.get("full_text") or post.get("content") or ""
        created = normalize_created_at(post.get("created_at") or post.get("date") or post.get("timestamp") or "")
        url = post.get("url") or post.get("post_url") or f"https://x.com/{username}/status/{pid}"
        media = post.get("media") or post.get("attachments") or []
        if not media:
            entities = post.get("extended_entities") or post.get("entities") or {}
            media = entities.get("media") or []
        if isinstance(media, dict):
            media = media.get("items") or media.get("media") or [media]
        clean_media = []
        for m in media if isinstance(media, list) else []:
            if isinstance(m, str):
                clean_media.append({"url": m, "type": "unknown"})
            elif isinstance(m, dict):
                media_url = m.get("url") or m.get("media_url_https") or m.get("download_url") or m.get("src") or ""
                media_type = m.get("type") or m.get("media_type") or "unknown"
                video_info = m.get("video_info") or {}
                variants = video_info.get("variants") or []
                mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
                if mp4_variants:
                    media_url = max(mp4_variants, key=lambda v: int(v.get("bitrate") or 0)).get("url")
                    media_type = "video"
                clean_media.append({
                    "url": media_url,
                    "type": media_type,
                    "thumbnail": m.get("thumbnail_url") or m.get("preview_image_url") or m.get("media_url_https") or "",
                    "width": (m.get("original_info") or {}).get("width") or m.get("width"),
                    "height": (m.get("original_info") or {}).get("height") or m.get("height"),
                    "alt": m.get("alt_text") or m.get("alt") or ""
                })
        raw = json.dumps(post, ensure_ascii=False)
        with self.connect() as c:
            old = c.execute("SELECT is_bookmarked,is_liked,watched FROM posts WHERE post_id=?", (pid,)).fetchone()
            bookmarked = old[0] if old else int(collection == "bookmarks")
            liked = old[1] if old else int(collection == "likes")
            watched = old[2] if old else 0
            post_kind = classify_post(post, clean_media)
            c.execute("""INSERT INTO posts(post_id,username,display_name,text,created_at,post_url,media_json,raw_json,source_collection,is_bookmarked,is_liked,watched,archived_at,post_kind)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET
              username=excluded.username,display_name=excluded.display_name,text=excluded.text,
              created_at=excluded.created_at,post_url=excluded.post_url,media_json=excluded.media_json,
              raw_json=excluded.raw_json,source_collection=excluded.source_collection,post_kind=excluded.post_kind,
              is_bookmarked=MAX(posts.is_bookmarked,excluded.is_bookmarked),is_liked=MAX(posts.is_liked,excluded.is_liked)""",
              (pid, username, display, text, str(created), url, json.dumps(clean_media), raw, collection, bookmarked, liked, watched, now_iso(), post_kind))
        return True

    def list_posts(self, section="Home", query="", username="", limit=500, account_tab="Posts"):
        where, args = [], []
        if username:
            # X handles are case-insensitive; normalize both sides to avoid empty account views.
            where.append("LOWER(username)=LOWER(?)"); args.append(username.lstrip("@"))
        if username and section == "Home":
            if account_tab == "Posts": where.append("post_kind='post'")
            elif account_tab == "Replies": where.append("post_kind='reply'")
            elif account_tab == "Reposts": where.append("post_kind='repost'")
            elif account_tab == "Media": where.append("media_json!='[]'")
        if section == "Bookmarks": where.append("is_bookmarked=1")
        elif section == "Likes": where.append("is_liked=1")
        elif section == "Watch Later": where.append("is_watch_later=1")
        elif section == "Download History": where.append("EXISTS (SELECT 1 FROM downloads d WHERE d.post_id=posts.post_id)")
        elif section == "Media Library": where.append("media_json!='[]'")
        elif section == "Archive": pass
        elif section == "Accounts": pass
        if query:
            where.append("(text LIKE ? OR username LIKE ? OR display_name LIKE ? OR post_id LIKE ?)")
            args += [f"%{query}%"] * 4
        sql = "SELECT * FROM posts" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def repair_post_authors(self, selected_username: str = ""):
        """Repair v1.1 rows whose provider payload omitted the author's handle.

        First recover a handle from each row's raw provider JSON. If it is still
        missing and the archive contains exactly one account, associate those
        legacy orphan rows with that sole account. Never guess when there are
        multiple archived accounts.
        """
        selected_username = (selected_username or "").lstrip("@")
        with self.connect() as c:
            account_rows = c.execute("SELECT username FROM accounts").fetchall()
            sole_account = account_rows[0][0] if len(account_rows) == 1 else ""
            rows = c.execute("SELECT post_id,raw_json FROM posts WHERE username IS NULL OR username='' OR LOWER(username)='unknown'").fetchall()
            repaired = 0
            for row in rows:
                try:
                    raw = json.loads(row["raw_json"] or "{}")
                except Exception:
                    raw = {}
                author = raw.get("user") or raw.get("author") or raw.get("user_info") or raw.get("account") or {}
                handle = ""
                if isinstance(author, str):
                    handle = author.lstrip("@")
                elif isinstance(author, dict):
                    handle = (raw.get("username") or raw.get("screen_name") or author.get("username") or
                              author.get("screen_name") or author.get("handle") or "").lstrip("@")
                if not handle:
                    handle = sole_account
                if handle:
                    c.execute("UPDATE posts SET username=? WHERE post_id=?", (handle, row["post_id"]))
                    repaired += 1
            return repaired

    def counts(self):
        with self.connect() as c:
            posts = c.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            media = c.execute("SELECT COUNT(*) FROM posts WHERE media_json!='[]'").fetchone()[0]
            accounts = c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            downloads = c.execute("SELECT COUNT(*) FROM downloads WHERE status='complete'").fetchone()[0]
        return posts, media, accounts, downloads

    def toggle_flag(self, pid, field, value):
        if field not in ("is_bookmarked", "is_liked", "watched"):
            return
        with self.connect() as c:
            c.execute(f"UPDATE posts SET {field}=? WHERE post_id=?", (int(value), pid))

    def record_download(self, pid, url, path, status, error=""):
        with self.connect() as c:
            c.execute("INSERT INTO downloads(post_id,media_url,local_path,status,error,created_at) VALUES(?,?,?,?,?,?)",
                      (pid, url, str(path), status, error, now_iso()))

    def get_scan_state(self, username):
        with self.connect() as c:
            r = c.execute("SELECT cursor,scanned_count FROM scan_state WHERE username=?", (username,)).fetchone()
            return (r[0], r[1]) if r else (None, 0)

    def set_scan_state(self, username, cursor, count):
        with self.connect() as c:
            c.execute("INSERT INTO scan_state(username,cursor,scanned_count,updated_at) VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET cursor=excluded.cursor,scanned_count=excluded.scanned_count,updated_at=excluded.updated_at",
                      (username, cursor, count, now_iso()))


class Settings:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = DEFAULT_SETTINGS.copy()
        if SETTINGS_PATH.exists():
            try: self.data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
            except Exception: pass
        try:
            import keyring
            for key in ("twitterapis_api_key",):
                secret = keyring.get_password(APP_NAME, key)
                if secret: self.data[key] = secret
        except Exception:
            pass

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        serializable = self.data.copy()
        try:
            import keyring
            for key in ("socialdata_api_key", "twitterapis_api_key"):
                secret = str(serializable.pop(key, ""))
                if secret: keyring.set_password(APP_NAME, key, secret)
                else:
                    try: keyring.delete_password(APP_NAME, key)
                    except Exception: pass
        except Exception:
            # Fallback keeps the app usable if the OS credential store is unavailable.
            serializable = self.data.copy()
        SETTINGS_PATH.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


@dataclass
class EngineResult:
    posts: list[dict]
    cursor: Optional[str] = None
    account: Optional[dict] = None


class ProviderAPIError(RuntimeError):
    pass


class XApiCompatibleEngine:
    """Documented adapters for SocialData and TwitterAPIs.

    SocialData is used for public profile/timeline/following/list data. TwitterAPIs
    is used for public timelines plus account-scoped bookmarks/likes when a session
    has been registered with that provider. No X login cookies are collected here.
    """
    def __init__(self, settings: Settings):
        self.s = settings
        self.session = requests.Session()
        self.user_cache = {}

    @staticmethod
    def _extract_items(payload):
        if isinstance(payload, list): return payload
        if not isinstance(payload, dict): return []
        for key in ("tweets", "posts", "items", "results", "users", "lists", "data", "timeline"):
            value = payload.get(key)
            if isinstance(value, list): return value
            if isinstance(value, dict):
                for nested in ("tweets", "posts", "items", "results", "users", "lists"):
                    if isinstance(value.get(nested), list): return value[nested]
        return []

    @staticmethod
    def _cursor(payload):
        if not isinstance(payload, dict): return None
        return payload.get("next_cursor") or payload.get("nextCursor") or payload.get("next_token")

    def _request(self, base, path, key, params=None):
        if not key:
            raise ProviderAPIError("API key is missing. Open Settings → API Providers and add the provider key.")
        url = base.rstrip("/") + "/" + path.lstrip("/")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {key}"}
        try:
            r = self.session.get(url, headers=headers, params=params or {}, timeout=int(self.s.data.get("request_timeout", 30)))
            if r.status_code == 401: raise ProviderAPIError("401 Unauthorized: check the API key.")
            if r.status_code == 402: raise ProviderAPIError("402 Payment Required: the provider balance/credits are exhausted.")
            if r.status_code == 403: raise ProviderAPIError("403 Forbidden: the requested content is not accessible to this provider/session.")
            if r.status_code == 409: raise ProviderAPIError("409 Session required: register your X session with TwitterAPIs first (the app does not collect your X cookies).")
            if r.status_code == 429: raise ProviderAPIError("429 Rate limited: wait and retry; the scanner preserves its cursor.")
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, dict) and payload.get("error"):
                raise ProviderAPIError(f"{payload.get('error')}: {payload.get('message', 'Provider returned an error')}")
            return payload
        except requests.RequestException as e:
            raise ProviderAPIError(f"Network/API request failed: {e}") from e

    def _social_profile(self, username):
        username = username.lstrip("@")
        if username in self.user_cache: return self.user_cache[username]
        payload = self._request(self.s.data.get("socialdata_base", "https://api.socialdata.tools"),
                                f"twitter/user/{quote(username)}", self.s.data.get("socialdata_api_key", ""))
        profile = payload.get("user") or payload.get("profile") or payload
        uid = str(profile.get("id_str") or profile.get("id") or profile.get("user_id") or "")
        if not uid: raise ProviderAPIError("SocialData profile response did not include a numeric user ID.")
        account = {"username": profile.get("screen_name") or profile.get("username") or username,
                   "display_name": profile.get("name") or profile.get("display_name") or username,
                   "bio": profile.get("description") or profile.get("bio"),
                   "avatar_url": profile.get("profile_image_url_https") or profile.get("avatar_url"),
                   "banner_url": profile.get("profile_banner_url") or profile.get("banner_url"),
                   "followers_count": profile.get("followers_count"), "following_count": profile.get("friends_count") or profile.get("following_count"),
                   "id": uid}
        self.user_cache[username] = account
        return account

    def _twitter_profile(self, username):
        username = username.lstrip("@")
        payload = self._request(self.s.data.get("twitterapis_base", "https://api.twitterapis.com/twitter"),
                                "user/info", self.s.data.get("twitterapis_api_key", ""), {"username": username})
        profile = payload.get("user") or payload.get("data") or payload
        uid = str(profile.get("id_str") or profile.get("id") or profile.get("user_id") or "")
        if not uid: raise ProviderAPIError("TwitterAPIs profile response did not include a numeric user ID.")
        account = {"username": profile.get("screen_name") or profile.get("username") or username,
                   "display_name": profile.get("name") or profile.get("display_name") or username,
                   "bio": profile.get("description") or profile.get("bio"),
                   "avatar_url": profile.get("profile_image_url_https") or profile.get("avatar_url"),
                   "banner_url": profile.get("profile_banner_url") or profile.get("banner_url"),
                   "followers_count": profile.get("followers_count"), "following_count": profile.get("friends_count") or profile.get("following_count"),
                   "id": uid}
        self.user_cache[username] = account
        return account

    def test_provider(self, provider):
        if provider == "SocialData":
            key=self.s.data.get("socialdata_api_key", "")
            if not key: raise ProviderAPIError("Enter your SocialData API key first.")
            # Profile endpoint is a safe low-volume validation call.
            payload=self._request(self.s.data.get("socialdata_base", "https://api.socialdata.tools"), "twitter/user/openai", key)
            return "SocialData connection successful. Profile endpoint responded."
        key=self.s.data.get("twitterapis_api_key", "")
        if not key: raise ProviderAPIError("Enter your TwitterAPIs API key first.")
        payload=self._request(self.s.data.get("twitterapis_base", "https://api.twitterapis.com/twitter"), "user/info", key, {"username":"OpenAI"})
        return "TwitterAPIs connection successful. Profile endpoint responded."

    def fetch(self, section: str, username: str, limit: int, cursor: str = "") -> EngineResult:
        tw_base = self.s.data.get("twitterapis_base", "https://api.twitterapis.com/twitter")
        key = self.s.data.get("twitterapis_api_key", "")
        account = None
        if section in ("Home", "Explore"):
            try:
                scraper = BrowserScraper(self.s.data.get("browser_engine", "Edge"),
                    headless=bool(self.s.data.get("browser_headless", False)),
                    timeout_ms=int(self.s.data.get("request_timeout", 30)) * 1000)
                posts, account, next_cursor = scraper.fetch_page(username, int(cursor or 0), section)
                return EngineResult(posts=posts, cursor=next_cursor, account=account)
            except Exception as browser_error:
                if not key:
                    raise ProviderAPIError("Browser scraper failed: " + str(browser_error) +
                        "\nTwitterAPIs fallback is not configured. Add its API key in Settings or sign in to X in the opened browser.") from browser_error
                try:
                    account = self._twitter_profile(username)
                    params = {"username": username.lstrip("@"), "user_id": account["id"], "count": limit}
                    if cursor: params["cursor"] = cursor
                    payload = self._request(tw_base, "user/tweets_and_replies", key, params)
                    return EngineResult(posts=self._extract_items(payload), cursor=self._cursor(payload), account=account)
                except Exception as api_error:
                    raise ProviderAPIError("Browser scraper failed: " + str(browser_error) +
                        "\nTwitterAPIs fallback failed: " + str(api_error)) from api_error
        if section == "Bookmarks":
            params = {"count": min(limit, 100)}
            if cursor: params["cursor"] = cursor
            payload = self._request(tw_base, "user/bookmarks", key, params)
        elif section == "Likes":
            account = self._twitter_profile(username)
            params = {"user_id": account["id"], "count": min(limit, 100)}
            if cursor: params["cursor"] = cursor
            payload = self._request(tw_base, "user/likes", key, params)
        else:
            raise ProviderAPIError(f"{section} is not supported by the browser engine or TwitterAPIs fallback.")
        return EngineResult(posts=self._extract_items(payload), cursor=self._cursor(payload), account=account)



class ScanWorker(QThread):
    progress = pyqtSignal(str, int, int)
    message = pyqtSignal(str)
    finished_scan = pyqtSignal(int, str)

    def __init__(self, db: ArchiveDB, engine: XApiCompatibleEngine, section: str, username: str, limit: int, max_pages: int = 200):
        super().__init__(); self.db=db; self.engine=engine; self.section=section; self.username=username.lstrip("@"); self.limit=limit; self.max_pages=max_pages; self.stop_requested=False

    def stop(self): self.stop_requested=True

    def _download_profile_assets(self, account: dict):
        """Save the account avatar and profile header locally, without replacing
        previously downloaded files when a provider omits one of the URLs."""
        if not account:
            return
        username = (account.get("username") or self.username).lstrip("@")
        with self.db.connect() as c:
            row = c.execute("SELECT avatar_url,banner_url,avatar_path,banner_path FROM accounts WHERE LOWER(username)=LOWER(?)", (username,)).fetchone()
        old = dict(row) if row else {}
        self.db.upsert_account(account)
        base = Path(self.engine.s.data.get("download_dir", str(DOWNLOAD_DIR))) / safe_name(username) / "Profile"
        base.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": "Mozilla/5.0 (compatible; TwitterMediaDownloader/1.1.2)", "Accept": "image/*,*/*;q=0.8"}
        assets = (
            ("avatar", account.get("avatar_url") or account.get("profile_image_url_https") or account.get("avatar"), old.get("avatar_url"), old.get("avatar_path")),
            ("banner", account.get("banner_url") or account.get("profile_banner_url") or account.get("banner"), old.get("banner_url"), old.get("banner_path")),
        )
        saved = {}
        for kind, url, old_url, existing in assets:
            if not url or (existing and Path(existing).is_file() and str(url) == str(old_url)):
                continue
            url = str(url).replace("\\u0026", "&")
            if kind == "avatar":
                # X's *_normal avatar is a small thumbnail; request the larger variant.
                url = re.sub(r"_normal(?=\.(?:jpg|jpeg|png|webp)(?:\?|$))", "_400x400", url, flags=re.I)
            if existing and Path(existing).is_file():
                continue
            try:
                response = requests.get(url, headers=headers, timeout=25, stream=True)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if content_type and not content_type.startswith("image/"):
                    continue
                suffix = Path(url.split("?", 1)[0]).suffix.lower()
                if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
                    suffix = ".jpg"
                target = base / f"{kind}{suffix}"
                total = 0
                with target.open("wb") as out:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > 20 * 1024 * 1024:
                            raise ValueError("Profile image exceeded the 20 MB safety limit")
                        out.write(chunk)
                if total:
                    saved[kind] = str(target.resolve())
                else:
                    target.unlink(missing_ok=True)
            except Exception as exc:
                self.message.emit(f"Could not save {kind} for @{username}: {exc}")
        if saved:
            self.db.update_account_asset_paths(username, saved.get("avatar", ""), saved.get("banner", ""))

    def run(self):
        scan_key = f"{self.username}::{self.section}"
        saved_cursor, total = self.db.get_scan_state(scan_key)
        cursor = saved_cursor or ""
        added = 0
        try:
            reached_page_cap = True
            for page in range(self.max_pages):
                if self.stop_requested:
                    reached_page_cap = False
                    break
                self.message.emit(f"Fetching {self.section.lower()} · page {page+1}")
                result = self.engine.fetch(self.section, self.username, self.limit, cursor)
                if result.account:
                    self._download_profile_assets(result.account)
                if not result.posts:
                    self.message.emit("No more posts returned by the provider; end of accessible timeline.")
                    reached_page_cap = False
                    break
                for post in result.posts:
                    if self.stop_requested: break
                    if not isinstance(post, dict): continue
                    if self.db.save_post(post, self.section.lower(), fallback_username=self.username): added += 1
                total += len(result.posts)
                cursor = result.cursor or ""
                self.db.set_scan_state(scan_key, cursor, total)
                self.progress.emit(self.username, total, added)
                if not cursor:
                    reached_page_cap = False
                    break
            if self.stop_requested:
                final_message = "Stopped · checkpoint saved; scan again to resume"
            elif reached_page_cap and cursor:
                final_message = f"Page safety limit reached ({self.max_pages}); checkpoint saved. Scan again to continue."
            else:
                final_message = "Scan finished"
            self.finished_scan.emit(added, final_message)
        except Exception as e:
            self.finished_scan.emit(added, f"Scan error: {e}")


class DownloadWorker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(str, bool, str)

    def __init__(self, db: ArchiveDB, post: dict, settings: Settings):
        super().__init__(); self.db=db; self.post=post; self.s=settings

    def run(self):
        try:
            import yt_dlp
            media = json.loads(self.post.get("media_json") or "[]")
            items = [m for m in media if m.get("url")]
            if not items:
                # For a single post URL, yt-dlp may still be able to resolve attached media.
                items = [{"url": self.post.get("post_url", ""), "type": "post"}]
            user_dir = Path(self.s.data.get("download_dir", str(DOWNLOAD_DIR))) / safe_name(self.post.get("username", "unknown"))
            user_dir.mkdir(parents=True, exist_ok=True)
            ok = 0; errors=[]
            for idx, item in enumerate(items, 1):
                url = item.get("url", "")
                if not url: continue
                self.progress.emit(f"Downloading media {idx}/{len(items)}")
                ext = Path(url.split("?")[0]).suffix.lower()
                media_type = (item.get("type") or "").lower()
                if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff") or media_type in ("photo", "image", "photo_image"):
                    try:
                        target = user_dir / f"{safe_name(self.post.get('post_id','post'))}_{idx}.jpg"
                        saved_image = download_image_response(url, target, timeout=(20, 120))
                        self.db.record_download(self.post["post_id"], url, saved_image, "complete")
                        ok += 1
                    except Exception as ex:
                        self.db.record_download(self.post["post_id"], url, "", "failed", str(ex)); errors.append(str(ex))
                    continue
                opts = {
                    "outtmpl": str(user_dir / f"%(id)s_%(title).80B.%(ext)s"),
                    "format": "bestvideo*+bestaudio/best" if int(self.s.data.get("max_height",0)) == 0 else f"best[height<={int(self.s.data['max_height'])}]/best",
                    "merge_output_format": "mp4", "noplaylist": True, "quiet": True,
                    "no_warnings": True, "retries": 3, "fragment_retries": 3,
                }
                browser = self.s.data.get("browser_cookies", "none")
                if browser and browser != "none": opts["cookiesfrombrowser"] = (browser,)
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        filepath = ydl.prepare_filename(info)
                    self.db.record_download(self.post["post_id"], url, filepath, "complete")
                    ok += 1
                except Exception as ex:
                    self.db.record_download(self.post["post_id"], url, "", "failed", str(ex))
                    errors.append(str(ex))
            if ok:
                self.done.emit(self.post["post_id"], True, f"Downloaded {ok} item(s)." + (f" {len(errors)} failed." if errors else ""))
            else:
                self.done.emit(self.post["post_id"], False, "; ".join(errors) or "No downloadable media found.")
        except Exception as e:
            self.done.emit(self.post.get("post_id", ""), False, f"Download setup failed: {e}. Install requirements.txt first.")


class AccountExportWorker(QThread):
    progress = pyqtSignal(str)
    finished_export = pyqtSignal(bool, str, str)

    MEDIA_FOLDERS = {
        "image": "Images", "photo": "Images", "photo_image": "Images",
        "video": "Videos", "animated_gif": "GIFs", "gif": "GIFs",
        "audio": "Audios", "voice": "Audios", "music": "Audios",
    }

    def __init__(self, db: ArchiveDB, settings: Settings, username: str):
        super().__init__(); self.db=db; self.settings=settings; self.username=username.lstrip("@")
        self._stop=False

    def stop(self): self._stop=True

    @staticmethod
    def _media_kind(item):
        raw=str(item.get("type") or item.get("media_type") or "").lower()
        url=str(item.get("url") or item.get("media_url") or "").lower().split("?")[0]
        ext=Path(url).suffix.lower()
        if raw in ("photo", "image", "photo_image") or ext in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tif", ".tiff"):
            return "image"
        if raw in ("animated_gif", "gif") or ext == ".gif": return "gif"
        if raw in ("video", "mp4", "hls") or ext in (".mp4", ".m3u8", ".mov", ".webm", ".mkv"): return "video"
        if raw in ("audio", "voice", "music") or ext in (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"): return "audio"
        return "other"

    @staticmethod
    def _extension(item, kind):
        url=str(item.get("url") or item.get("media_url") or "")
        ext=Path(url.split("?")[0]).suffix.lower()
        allowed={"image":(".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"),
                 "gif":(".gif", ".mp4"), "video":(".mp4", ".m3u8", ".mov", ".webm", ".mkv"),
                 "audio":(".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac")}
        if ext in allowed.get(kind, ()): return ext
        mime=str(item.get("content_type") or item.get("mime_type") or "")
        guess=mimetypes.guess_extension(mime.split(";")[0]) if mime else None
        return guess or ({"image":".jpg", "gif":".gif", "video":".mp4", "audio":".mp3"}.get(kind,".bin"))

    def _download(self, item, target, post_url):
        url=item.get("url") or item.get("media_url") or ""
        if not url: raise ValueError("Media item has no downloadable URL")
        kind=self._media_kind(item)
        # X/Twitter CDN MP4s are already direct media files. Download them with
        # Requests instead of yt-dlp: importing Chrome/Edge cookies can fail when
        # the browser is open and its SQLite cookie database is locked.
        ext=Path(url.split("?",1)[0]).suffix.lower()
        direct_exts={".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif",
                     ".mp4", ".mov", ".webm", ".mkv", ".mp3", ".m4a",
                     ".aac", ".wav", ".ogg", ".opus", ".flac"}
        direct_cdn = "video.twimg.com" in url.lower() or "pbs.twimg.com" in url.lower()
        if kind == "image":
            return str(download_image_response(url, target, timeout=(20, 120)))
        if ext in direct_exts and (direct_cdn or kind in ("other", "audio", "gif", "video")):
            headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
                     "Accept":"*/*", "Referer":"https://x.com/"}
            temp=target.with_name(target.name+".part")
            try:
                with requests.get(url, timeout=(20,120), stream=True, headers=headers) as response:
                    response.raise_for_status()
                    content_type=response.headers.get("Content-Type", "").lower()
                    if "text/html" in content_type:
                        raise IOError("CDN returned an HTML page instead of media")
                    with temp.open("wb") as out:
                        for chunk in response.iter_content(1024*256):
                            if chunk: out.write(chunk)
                if not temp.is_file() or temp.stat().st_size == 0:
                    raise IOError("Downloaded media file was empty")
                temp.replace(target)
                return str(target)
            except Exception:
                temp.unlink(missing_ok=True)
                raise
        # yt-dlp remains the fallback for HLS/DASH and extractor-only URLs.
        import yt_dlp
        outtmpl=str(target.with_suffix("").as_posix()) + ".%(ext)s"
        opts={"outtmpl":outtmpl,"format":"bestvideo*+bestaudio/best","merge_output_format":"mp4",
              "noplaylist":True,"quiet":True,"no_warnings":True,"retries":4,"fragment_retries":4,
              "continuedl":True,"overwrites":False}
        browser=self.settings.data.get("browser_cookies","none")
        if browser and browser!="none": opts["cookiesfrombrowser"]=(browser,)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info=ydl.extract_info(url,download=True)
                filename=ydl.prepare_filename(info)
        except Exception as exc:
            # Retry once without browser cookies when the browser database is locked.
            message=str(exc).lower()
            cookie_copy_error=("could not copy" in message and "cookie" in message) or "cookies database" in message
            if not cookie_copy_error or "cookiesfrombrowser" not in opts:
                raise
            opts.pop("cookiesfrombrowser",None)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info=ydl.extract_info(url,download=True)
                filename=ydl.prepare_filename(info)
            candidates=[Path(filename), Path(filename).with_suffix(".mp4")]
            found=next((x for x in candidates if x.is_file()),None)
            if not found:
                stem=Path(filename).stem
                found=next(iter(target.parent.glob(stem+".*")),None)
            if not found: raise IOError("yt-dlp completed but output file was not found")
            if found != target: 
                final=target.with_suffix(found.suffix)
                if final.exists(): final.unlink()
                shutil.move(str(found),str(final)); found=final
            return str(found)

    def run(self):
        try:
            base=Path(self.settings.data.get("download_dir",str(DOWNLOAD_DIR)))
            root=base/safe_name(self.username)
            root.mkdir(parents=True,exist_ok=True)
            for folder in ("Posts","Replies","Reposts","Threads","Media/Images","Media/Videos","Media/GIFs","Media/Audios","Media/Other","Metadata","Profile"):
                (root/folder).mkdir(parents=True,exist_ok=True)
            with self.db.connect() as c:
                rows=c.execute("SELECT * FROM posts WHERE LOWER(username)=LOWER(?) ORDER BY created_at ASC, post_id ASC",(self.username,)).fetchall()
                account=c.execute("SELECT * FROM accounts WHERE LOWER(username)=LOWER(?)",(self.username,)).fetchone()
                completed=c.execute("SELECT post_id,media_url,local_path FROM downloads WHERE status='complete' ORDER BY id",()).fetchall()
            posts=[dict(r) for r in rows]
            if not posts: raise RuntimeError("No locally archived posts found for this account. Scan the account first.")
            existing={(r[0],r[1]):r[2] for r in completed}
            checkpoint_path=root/"Metadata"/"export_checkpoint.json"
            try: checkpoint=json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except Exception: checkpoint={"completed_media":{},"completed_posts":[]}
            completed_media=checkpoint.setdefault("completed_media",{})
            completed_posts=set(checkpoint.setdefault("completed_posts",[]))
            export_posts=[]; failures=[]; total_media=sum(len(json.loads(x.get("media_json") or "[]")) for x in posts)
            if account:
                for key, filename in (("avatar_path","avatar.jpg"),("banner_path","banner.jpg")):
                    source=Path(account[key]) if account[key] else None
                    if source and source.is_file(): shutil.copy2(source,root/"Profile"/filename)
            media_done=0
            for index, post in enumerate(posts,1):
                if self._stop: break
                pid=str(post.get("post_id") or "")
                try: media=json.loads(post.get("media_json") or "[]")
                except Exception: media=[]
                exported_media=[]
                for mi,item in enumerate(media,1):
                    if self._stop: break
                    item=dict(item); url=item.get("url") or item.get("media_url") or ""
                    if not url: continue
                    kind=self._media_kind(item); folder=self.MEDIA_FOLDERS.get(kind,"Other")
                    key=hashlib.sha256((pid+"|"+url).encode("utf-8")).hexdigest()
                    ext=self._extension(item,kind); filename=f"{safe_name(pid)}_{mi:02d}_{key[:10]}{ext}"
                    target=root/"Media"/folder/filename
                    saved=completed_media.get(key,"" )
                    saved_path=Path(saved) if saved else Path()
                    if saved and not saved_path.is_absolute(): saved_path=root/saved_path
                    # Older v1.2.1 archives may contain images saved with the
                    # wrong extension or HTML/error payloads. Never reuse them
                    # as completed images: fall through to a verified redownload.
                    if kind == "image" and saved and not image_file_is_valid(saved_path):
                        saved = ""
                    if saved and saved_path.is_file():
                        source=saved_path
                        if source.resolve()!=target.resolve():
                            target=target.with_suffix(source.suffix)
                            if not target.exists(): shutil.copy2(source,target)
                    elif target.is_file() and target.stat().st_size>0 and (kind != "image" or image_file_is_valid(target)):
                        pass
                    else:
                        prior=existing.get((pid,url),"")
                        if prior and Path(prior).is_file() and (kind != "image" or image_file_is_valid(Path(prior))):
                            source=Path(prior); target=target.with_suffix(source.suffix)
                            shutil.copy2(source,target)
                        else:
                            try:
                                self.progress.emit(f"@{self.username} · post {index}/{len(posts)} · media {media_done+1}/{total_media}")
                                saved_path=self._download(item,target,post.get("post_url",""))
                                target=Path(saved_path)
                            except Exception as exc:
                                failures.append({"post_id":pid,"url":url,"error":str(exc)})
                                continue
                    completed_media[key]=str(target.relative_to(root)).replace("\\","/")
                    media_done+=1
                    exported_media.append({**item,"archive_path":completed_media[key],"kind":kind})
                created=post.get("created_at") or ""
                kind=str(post.get("post_kind") or "post").lower()
                bucket="Replies" if kind=="reply" else "Reposts" if kind=="repost" else "Posts"
                record={"post_id":pid,"username":post.get("username"),"display_name":post.get("display_name"),
                        "text":post.get("text") or "","created_at":created,"post_url":post.get("post_url") or "",
                        "post_kind":kind,"source_collection":post.get("source_collection"),"media":exported_media,
                        "raw":json.loads(post.get("raw_json") or "{}")}
                export_posts.append(record)
                # One self-contained JSON record per post, plus category index files.
                category_file=root/bucket/f"{safe_name(pid)}.json"
                category_file.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
                completed_posts.add(pid)
                if index%25==0 or index==len(posts):
                    checkpoint.update({"username":self.username,"updated_at":now_iso(),"completed_posts":sorted(completed_posts),"completed_media":completed_media})
                    checkpoint_path.write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2),encoding="utf-8")
                self.progress.emit(f"Exporting @{self.username} · post {index}/{len(posts)} · {media_done} media saved")
            # Stable chronological manifest and portable HTML index.
            export_posts.sort(key=lambda x:(x.get("created_at") or "",x.get("post_id") or ""))
            manifest={"archive_version":"1.2.2","username":self.username,"exported_at":now_iso(),
                      "post_count":len(export_posts),"media_count":sum(len(x["media"]) for x in export_posts),
                      "ordering":"oldest_to_newest","account":dict(account) if account else {},"posts":export_posts}
            (root/"Metadata"/"account_archive.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
            for cat in ("Posts","Replies","Reposts"):
                selected=[x for x in export_posts if ((x["post_kind"]=="reply" and cat=="Replies") or (x["post_kind"]=="repost" and cat=="Reposts") or (x["post_kind"] not in ("reply","repost") and cat=="Posts"))]
                (root/cat/"index.json").write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding="utf-8")
            # Build conversation/thread groupings from provider conversation identifiers.
            conversations={}
            for item in export_posts:
                raw=item.get("raw") if isinstance(item.get("raw"),dict) else {}
                legacy=raw.get("legacy") if isinstance(raw.get("legacy"),dict) else {}
                cid=(raw.get("conversation_id") or raw.get("conversation_id_str") or raw.get("conversationId") or legacy.get("conversation_id_str") or item["post_id"])
                conversations.setdefault(str(cid),[]).append(item)
            thread_groups=[{"conversation_id":cid,"posts":sorted(group,key=lambda x:(x.get("created_at") or "",x.get("post_id") or ""))}
                           for cid,group in conversations.items() if len(group)>1]
            (root/"Threads"/"conversations.json").write_text(json.dumps(thread_groups,ensure_ascii=False,indent=2),encoding="utf-8")
            esc=html.escape
            html_rows=[]
            for item in reversed(export_posts):
                media_links=" ".join(f'<a href="../{esc(m["archive_path"])}">{esc(m.get("kind","media"))}</a>' for m in item["media"] if m.get("archive_path"))
                html_rows.append(f'<article><small>{esc(item.get("created_at", ""))} · {esc(item.get("post_kind", "post"))}</small><p>{esc(item.get("text", ""))}</p><p>{media_links}</p><a href="{esc(item.get("post_url", ""))}">Original post</a></article>')
            page='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>@'+esc(self.username)+' archive</title><style>body{font:16px Segoe UI,Arial;background:#000;color:#e7e9ea;max-width:850px;margin:30px auto;padding:0 18px}article{padding:18px;border:1px solid #333;margin:12px 0;border-radius:12px;white-space:pre-wrap}small{color:#888}a{color:#1d9bf0}</style></head><body><h1>@'+esc(self.username)+' archive</h1><p>'+str(len(export_posts))+' posts · '+str(manifest['media_count'])+' media items · newest first</p>'+''.join(html_rows)+'</body></html>'
            (root/"Posts"/"index.html").write_text(page,encoding="utf-8")
            (root/"Archive_Report.txt").write_text(f"X ACCOUNT ARCHIVE\nAccount: @{self.username}\nExported: {now_iso()}\nPosts exported: {len(export_posts)}\nMedia files indexed: {manifest['media_count']}\nMedia download failures: {len(failures)}\nOrder: oldest-to-newest in JSON; newest-first in HTML.\n\nOnly content already present in the local database is exported. Run Scan again to retrieve more accessible history.\n",encoding="utf-8")
            (root/"Metadata"/"media_failures.json").write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding="utf-8")
            checkpoint.update({"username":self.username,"updated_at":now_iso(),"completed_posts":sorted(completed_posts),"completed_media":completed_media,"finished":not self._stop})
            checkpoint_path.write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2),encoding="utf-8")
            if self._stop: self.finished_export.emit(False,str(root),f"Export paused. Progress saved; run export again to resume. {len(export_posts)} posts processed.")
            else: self.finished_export.emit(True,str(root),f"Export complete: {len(export_posts)} posts, {manifest['media_count']} media entries, {len(failures)} media failures.")
        except Exception as exc:
            self.finished_export.emit(False,"",f"Account export failed: {exc}")


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent); self.setWindowTitle("Settings · Browser scraper & fallback"); self.resize(700, 650); self.s=settings
        layout=QVBoxLayout(self)
        intro=QLabel("Playwright with Edge/Chrome is the primary scraper for public profiles and timelines. Sign in directly in the browser window if X requests it. TwitterAPIs is retained as a fallback and for supported account collections.")
        intro.setWordWrap(True); intro.setStyleSheet("color:#aab0b6;padding:4px 2px 10px;"); layout.addWidget(intro)
        form=QFormLayout(); layout.addLayout(form)
        self.fields={}
        entries=[("twitterapis_api_key","TwitterAPIs fallback API key"),("twitterapis_base","TwitterAPIs API base"),("download_dir","Download directory")]
        for key,label in entries:
            w=QLineEdit(str(settings.data.get(key,"")))
            if key.endswith("api_key"): w.setEchoMode(QLineEdit.Password)
            self.fields[key]=w; form.addRow(label,w)
        self.browser=QComboBox(); self.browser.addItems(["Edge","Chrome","Auto"]); self.browser.setCurrentText(settings.data.get("browser_engine","Edge")); form.addRow("Primary browser",self.browser)
        self.full_history=QCheckBox("Paginate the account activity feed until the provider has no more results"); self.full_history.setChecked(True); self.full_history.setEnabled(False); form.addRow("Historical scan",self.full_history)
        testrow=QHBoxLayout(); self.test_tw=QPushButton("Test TwitterAPIs fallback"); testrow.addWidget(self.test_tw); testrow.addStretch(1); layout.addLayout(testrow)
        self.test_tw.clicked.connect(lambda:self.test("TwitterAPIs"))
        self.cookies=QComboBox(); self.cookies.addItems(["none","edge","chrome","firefox","brave"]); self.cookies.setCurrentText(settings.data.get("browser_cookies","none")); form.addRow("yt-dlp browser cookies",self.cookies)
        self.height=QComboBox(); self.height.addItems(["Best available","2160p","1440p","1080p","720p"])
        h=int(settings.data.get("max_height",0)); self.height.setCurrentText({0:"Best available",2160:"2160p",1440:"1440p",1080:"1080p",720:"720p"}.get(h,"Best available")); form.addRow("Maximum video height",self.height)
        note=QLabel("Sign in only in the visible X browser window. The app does not ask for or export your cookies. TwitterAPIs fallback features require its API key and any session setup supported by that provider.")
        note.setWordWrap(True); note.setStyleSheet("color:#f0b45b;padding:10px;border:1px solid #5b4826;border-radius:10px;"); layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _collect(self):
        data={k:w.text().strip() for k,w in self.fields.items()}
        return data

    def test(self, provider):
        data=self._collect()
        old=self.s.data.copy(); self.s.data.update(data)
        engine=XApiCompatibleEngine(self.s)
        try:
            msg=engine.test_provider(provider); QMessageBox.information(self,"Connection successful",msg)
        except Exception as e: QMessageBox.warning(self,"Connection test failed",str(e))
        finally: self.s.data=old

    def accept(self):
        for key,w in self.fields.items(): self.s.data[key]=w.text().strip()
        self.s.data["browser_engine"]=self.browser.currentText()
        self.s.data["prefer_full_history"]=self.full_history.isChecked()
        self.s.data["browser_cookies"]=self.cookies.currentText()
        self.s.data["max_height"]={"Best available":0,"2160p":2160,"1440p":1440,"1080p":1080,"720p":720}[self.height.currentText()]
        self.s.save(); super().accept()


class PostCard(QFrame):
    action = pyqtSignal(str, str)
    media_requested = pyqtSignal(dict, int)
    def __init__(self, post: dict):
        super().__init__(); self.post=post; self.setObjectName("post"); self.setFrameShape(QFrame.NoFrame)
        root=QHBoxLayout(self); root.setContentsMargins(16,13,16,12); root.setSpacing(11)
        avatar=QLabel("●"); avatar.setFixedSize(42,42); avatar.setAlignment(Qt.AlignCenter); avatar.setStyleSheet("background:#202327;border-radius:21px;color:#8b98a5;font-size:22px;")
        root.addWidget(avatar,0,Qt.AlignTop)
        col=QVBoxLayout(); col.setSpacing(7); root.addLayout(col,1)
        header=QHBoxLayout(); name=QLabel(post.get("display_name") or post.get("username") or "Unknown"); name.setObjectName("postName")
        handle=QLabel(f"@{post.get('username','unknown')} · {self._date(post.get('created_at',''))}"); handle.setObjectName("handle")
        header.addWidget(name); header.addWidget(handle); header.addStretch(1)
        menu=QPushButton("···"); menu.setFixedWidth(34); menu.setStyleSheet("color:#71767b;font-size:18px;padding:0;")
        menu.clicked.connect(lambda: self._menu(menu)); header.addWidget(menu); col.addLayout(header)
        text=QLabel(post.get("text") or ""); text.setObjectName("postText"); text.setWordWrap(True); text.setTextInteractionFlags(Qt.TextSelectableByMouse); col.addWidget(text)
        media=json.loads(post.get("media_json") or "[]")
        for media_index, item in enumerate(media[:4]):
            url=item.get("url") or item.get("thumbnail")
            if not url: continue
            label=QLabel(f"▶  Open {item.get('type','media').upper()}  ·  {item.get('width') or '?'} × {item.get('height') or '?'}")
            label.setWordWrap(True); label.setMinimumHeight(58); label.setStyleSheet("border:1px solid #2f3336;border-radius:14px;padding:18px;color:#1d9bf0;"); label.setCursor(Qt.PointingHandCursor)
            label.mousePressEvent=lambda ev,i=media_index: self.media_requested.emit(self.post, i)
            col.addWidget(label)
        actions=QHBoxLayout()
        for title, key in [("♡  Like","like"),("▢  Bookmark","bookmark"),("▶  Watch later","watch"),("▶  Play","play"),("↓  Download","download"),("↗  Open","open")]:
            b=QPushButton(title); b.setStyleSheet("color:#71767b;font-size:12px;padding:5px 7px;"); b.clicked.connect(lambda checked=False,k=key:self.action.emit(self.post.get("post_id",""),k)); actions.addWidget(b)
        actions.addStretch(1); col.addLayout(actions)

    @staticmethod
    def _date(value):
        if not value: return ""
        return str(value)[:16].replace("T"," ")

    def _menu(self, button):
        menu=QMenu(self); open_action=menu.addAction("Open original post"); copy_action=menu.addAction("Copy post URL")
        chosen=menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen==open_action: QDesktopServices.openUrl(QUrl(self.post.get("post_url","")))
        elif chosen==copy_action: QApplication.clipboard().setText(self.post.get("post_url",""))


class MediaViewerDialog(QDialog):
    """Reusable in-app viewer for images, animated GIFs and video/audio."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Twitter Media Downloader · Media viewer")
        self.resize(1000, 720)
        self.items=[]; self.index=0; self.player=None; self.movie=None
        self.original_pixmap=QPixmap(); self.zoom=1.0; self.fit_mode=True; self._movie_source=""
        self.layout_root=QVBoxLayout(self)
        self.info=QLabel(""); self.info.setObjectName("muted"); self.layout_root.addWidget(self.info)
        self.image=QLabel(); self.image.setAlignment(Qt.AlignCenter); self.image.setMinimumSize(400,300); self.image.setStyleSheet("background:#080808;"); self.image.setScaledContents(False); self.layout_root.addWidget(self.image,1)
        self.video=QVideoWidget(); self.video.hide(); self.layout_root.addWidget(self.video,1)
        self.controls=QHBoxLayout()
        self.prev=QPushButton("← Previous"); self.zoom_out=QPushButton("−"); self.zoom_in=QPushButton("+"); self.fit=QPushButton("Fit"); self.actual=QPushButton("100%")
        self.play=QPushButton("Play / Pause"); self.next=QPushButton("Next →"); self.open=QPushButton("Open source"); self.close_btn=QPushButton("Close")
        for w in (self.prev,self.zoom_out,self.zoom_in,self.fit,self.actual,self.play,self.next,self.open,self.close_btn): self.controls.addWidget(w)
        self.layout_root.addLayout(self.controls)
        self.prev.clicked.connect(lambda:self.show_item(self.index-1)); self.next.clicked.connect(lambda:self.show_item(self.index+1))
        self.play.clicked.connect(self.toggle_play); self.close_btn.clicked.connect(self.close); self.open.clicked.connect(self.open_source)
        self.zoom_in.clicked.connect(lambda:self.set_zoom(self.zoom*1.25)); self.zoom_out.clicked.connect(lambda:self.set_zoom(self.zoom/1.25))
        self.fit.clicked.connect(self.fit_image); self.actual.clicked.connect(lambda:self.set_zoom(1.0))

    def set_items(self, items, index=0):
        self.items=items or []; self.show_item(index)

    def _stop_player(self):
        if self.player is not None:
            self.player.stop(); self.player.deleteLater(); self.player=None
        if self.movie is not None:
            self.movie.stop(); self.movie.deleteLater(); self.movie=None
        self._movie_source=""

    def _local_media_path(self, item, url):
        path=item.get("local_path") or item.get("path") or ""
        kind=str(item.get("type") or "").lower()
        if path and Path(path).is_file():
            local=Path(path)
            # Video-backed animated GIFs must remain video files; ordinary
            # images are reused only when their signature is valid.
            if local.suffix.lower() not in (".jpg",".jpeg",".png",".webp",".gif",".bmp",".tif",".tiff",".avif",".heic") or image_file_is_valid(local):
                return str(local)
        if url:
            cache=DATA_DIR/"media_preview_cache"; cache.mkdir(parents=True,exist_ok=True)
            stem=safe_name(str(item.get("post_id") or "preview"))+"_"+str(abs(hash(url)))
            # X photo URLs may have no suffix or a misleading suffix. Detect
            # the actual image type from the downloaded bytes before caching.
            target=cache/(stem+".jpg")
            saved=download_image_response(url,target,timeout=(20,60))
            return str(saved)
        return str(path) if path and Path(path).is_file() else ""

    def show_item(self, index):
        if not self.items: return
        self.index=max(0,min(index,len(self.items)-1)); item=self.items[self.index]
        self._stop_player(); self.video.hide(); self.image.show(); self.image.clear(); self.original_pixmap=QPixmap(); self.zoom=1.0; self.fit_mode=True
        path=item.get("local_path") or item.get("path") or ""; url=item.get("url") or item.get("media_url") or ""
        target=path if path and Path(path).is_file() else url; self.current_target=target
        self.info.setText(f"Media {self.index+1}/{len(self.items)}  ·  {item.get('type','media')}  ·  {item.get('width') or '?'} × {item.get('height') or '?'}")
        suffix=Path(target.split('?',1)[0]).suffix.lower() if target else ""
        kind=str(item.get("type","")).lower()
        video_suffixes=(".mp4",".m4v",".mov",".avi",".mkv")
        is_gif=suffix==".gif" or kind=="gif" or (kind=="animated_gif" and suffix not in video_suffixes)
        is_image=suffix in (".jpg",".jpeg",".png",".webp",".bmp",".gif") or kind in ("photo","image","photo_image")
        try:
            if is_gif:
                local=self._local_media_path(item,url)
                if local and Path(local).suffix.lower()==".gif":
                    self.movie=QMovie(local); self.movie.setCacheMode(QMovie.CacheAll)
                    if self.movie.isValid():
                        self._movie_source=local; self.movie.frameChanged.connect(self._scale_movie_frame); self.movie.start()
                    else:
                        self.movie.deleteLater(); self.movie=None; self.image.setText("This GIF could not be decoded by the installed Qt image plugins.")
                elif local:
                    self.original_pixmap=load_image_pixmap(local); self._render_image()
                else: self.image.setText("GIF preview unavailable. Download the media first.")
            elif is_image:
                local=self._local_media_path(item,url)
                if local:
                    self.original_pixmap=load_image_pixmap(local)
                    if not self.original_pixmap.isNull(): self._render_image()
                    else: self.image.setText("The image format could not be previewed.")
                else: self.image.setText("Image preview unavailable. Download the media first.")
            elif MULTIMEDIA_AVAILABLE and (suffix in (".mp4",".m4v",".mov",".avi",".mkv",".mp3",".m4a",".wav",".ogg") or kind in ("video","audio")) and target:
                self.image.hide(); self.video.show(); self.player=QMediaPlayer(self); self.player.setVideoOutput(self.video)
                media_url=QUrl.fromLocalFile(str(Path(path).resolve())) if path and Path(path).is_file() else QUrl(target)
                self.player.setMedia(QMediaContent(media_url))
                if kind in ("animated_gif","gif"): self.player.mediaStatusChanged.connect(self._loop_animated_video)
                self.player.play()
            else: self.image.setText("Media is not downloaded locally yet.\nDownload it first, or open the source URL.")
        except Exception as exc:
            self.image.setText(f"Could not preview media: {exc}")
        self.prev.setEnabled(self.index>0); self.next.setEnabled(self.index<len(self.items)-1)
        self.play.setEnabled(self.player is not None or self.movie is not None)
        for button in (self.zoom_in,self.zoom_out,self.fit,self.actual): button.setEnabled(is_image and self.movie is None and not self.original_pixmap.isNull())

    def _loop_animated_video(self, status):
        if self.player and status == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0); self.player.play()

    def _render_image(self):
        if self.original_pixmap.isNull(): return
        if self.fit_mode:
            scaled=self.original_pixmap.scaled(self.image.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation)
        else:
            size=self.original_pixmap.size()*self.zoom
            scaled=self.original_pixmap.scaled(size,Qt.KeepAspectRatio,Qt.SmoothTransformation)
        self.image.setPixmap(scaled)

    def _scale_movie_frame(self, _frame):
        if self.movie:
            size=self.image.size()
            self.movie.setScaledSize(size if self.fit_mode else QSize(max(1,int(size.width()*self.zoom)),max(1,int(size.height()*self.zoom))))

    def set_zoom(self, value):
        self.zoom=max(0.1,min(8.0,float(value))); self.fit_mode=False; self._render_image()

    def fit_image(self):
        self.fit_mode=True; self.zoom=1.0; self._render_image()
        if self.movie: self.movie.setScaledSize(self.image.size())

    def toggle_play(self):
        if self.player:
            if self.player.state()==QMediaPlayer.PlayingState: self.player.pause()
            else: self.player.play()
        elif self.movie:
            if self.movie.state()==QMovie.Running: self.movie.setPaused(True)
            else: self.movie.setPaused(False)

    def open_source(self):
        if getattr(self,"current_target",""):
            target=self.current_target
            if target.startswith("http"): QDesktopServices.openUrl(QUrl(target))
            elif Path(target).exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.movie: self.movie.setScaledSize(self.image.size())
        elif not self.original_pixmap.isNull() and self.fit_mode: self._render_image()

    def closeEvent(self,event):
        self._stop_player(); super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(APP_NAME); self.resize(1450,920); self.setMinimumSize(1100,700)
        self.settings=Settings(); self.db=ArchiveDB(DB_PATH); self.engine=XApiCompatibleEngine(self.settings)
        self.section="Home"; self.current_user=""; self.query=""; self.account_tab="Posts"; self.scan_worker=None; self.download_workers=[]; self.media_viewer=None
        self._build(); self.refresh()

    def _build(self):
        central=QWidget(); self.setCentralWidget(central); outer=QHBoxLayout(central); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        self.sidebar=QFrame(); self.sidebar.setObjectName("sidebar"); self.sidebar.setFixedWidth(245); side=QVBoxLayout(self.sidebar); side.setContentsMargins(14,8,14,12); side.setSpacing(4)
        brand=QLabel("𝕏"); brand.setObjectName("brand"); side.addWidget(brand)
        navs=[("⌂","Home"),("⌕","Explore"),("♧","Notifications"),("♙","Follow"),("▱","Bookmarks"),("♡","Likes"),("◷","Watch Later"),("▤","Lists"),("◉","Accounts"),("▣","Archive"),("▧","Media Library"),("◴","Download History")]
        self.nav_buttons={}
        for icon,label in navs:
            b=QPushButton(f"{icon}   {label}"); b.setObjectName("nav"); b.clicked.connect(lambda checked=False,s=label:self.navigate(s)); side.addWidget(b); self.nav_buttons[label]=b
        side.addStretch(1)
        scan=QPushButton("＋   Archive account"); scan.setObjectName("primary"); scan.clicked.connect(self.add_account); side.addWidget(scan)
        settings=QPushButton("⚙   Settings"); settings.clicked.connect(self.open_settings); side.addWidget(settings)
        self.profile_label=QLabel("●   Local archive\n      Not connected to X"); self.profile_label.setStyleSheet("padding:12px;color:#71767b;"); side.addWidget(self.profile_label)
        outer.addWidget(self.sidebar)
        self.center=QFrame(); self.center.setMinimumWidth(520); center_layout=QVBoxLayout(self.center); center_layout.setContentsMargins(0,0,0,0); center_layout.setSpacing(0)
        top=QFrame(); top.setObjectName("topbar"); top_l=QVBoxLayout(top); top_l.setContentsMargins(18,12,18,10)
        heading_row=QHBoxLayout()
        self.heading=QLabel("Home"); self.heading.setObjectName("sectionTitle"); heading_row.addWidget(self.heading); heading_row.addStretch(1)
        self.export_button=QPushButton("↓  Export account"); self.export_button.setObjectName("blue"); self.export_button.clicked.connect(self.export_account); self.export_button.hide(); heading_row.addWidget(self.export_button)
        self.sync_button=QPushButton("Sync collection"); self.sync_button.setObjectName("blue"); self.sync_button.clicked.connect(self.sync_collection); self.sync_button.hide(); heading_row.addWidget(self.sync_button)
        top_l.addLayout(heading_row)
        self.subheading=QLabel("Your locally archived X timeline"); self.subheading.setObjectName("muted"); top_l.addWidget(self.subheading)
        self.account_tabs=QTabBar(); self.account_tabs.addTab("Posts"); self.account_tabs.addTab("Replies"); self.account_tabs.addTab("Reposts"); self.account_tabs.addTab("Media")
        self.account_tabs.setDrawBase(False); self.account_tabs.setExpanding(False); self.account_tabs.hide(); self.account_tabs.currentChanged.connect(self._account_tab_changed); top_l.addWidget(self.account_tabs)
        center_layout.addWidget(top)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True); self.feed=QWidget(); self.feed_layout=QVBoxLayout(self.feed); self.feed_layout.setContentsMargins(0,0,0,0); self.feed_layout.setSpacing(0); self.feed_layout.addStretch(1); self.scroll.setWidget(self.feed); center_layout.addWidget(self.scroll,1)
        outer.addWidget(self.center,1)
        self.right=QFrame(); self.right.setObjectName("rightbar"); self.right.setFixedWidth(335); right=QVBoxLayout(self.right); right.setContentsMargins(28,10,24,16); right.setSpacing(14)
        self.search=QLineEdit(); self.search.setPlaceholderText("⌕  Search your archive"); self.search.returnPressed.connect(self.search_archive); right.addWidget(self.search)
        self.stats_panel=self.panel("Your archive", "Posts · Media · Accounts · Downloads")
        self.stats_value_label = self.stats_panel.findChildren(QLabel)[1]
        right.addWidget(self.stats_panel)
        self.api_panel=QFrame(); self.api_panel.setObjectName("panel"); ap=QVBoxLayout(self.api_panel); ap.setContentsMargins(16,14,16,14)
        title=QLabel("Connect an archive engine"); title.setStyleSheet("font-size:17px;font-weight:800;"); ap.addWidget(title)
        desc=QLabel("Playwright browser scraper is primary. Sign in to X in the opened Edge/Chrome window; TwitterAPIs is the fallback."); desc.setWordWrap(True); desc.setObjectName("muted"); ap.addWidget(desc)
        btn=QPushButton("Configure engine"); btn.setObjectName("blue"); btn.clicked.connect(self.open_settings); ap.addWidget(btn)
        imp=QPushButton("Import archive JSON"); imp.setObjectName("blue"); imp.clicked.connect(self.import_json); ap.addWidget(imp); right.addWidget(self.api_panel)
        self.activity=QFrame(); self.activity.setObjectName("panel"); al=QVBoxLayout(self.activity); al.setContentsMargins(16,14,16,14)
        al.addWidget(QLabel("Archive activity")); self.activity_text=QLabel("No active tasks"); self.activity_text.setObjectName("muted"); self.activity_text.setWordWrap(True); al.addWidget(self.activity_text)
        self.progress=QProgressBar(); self.progress.setRange(0,0); self.progress.hide(); al.addWidget(self.progress)
        self.stop_button=QPushButton("Stop scan"); self.stop_button.clicked.connect(self.stop_scan); self.stop_button.hide(); al.addWidget(self.stop_button); right.addWidget(self.activity)
        right.addStretch(1); outer.addWidget(self.right)
        self.statusBar().showMessage("Ready · Local archive")
        self._set_active_nav("Home")

    def panel(self,title,desc):
        frame=QFrame(); frame.setObjectName("panel"); l=QVBoxLayout(frame); l.setContentsMargins(16,14,16,14)
        a=QLabel(title); a.setStyleSheet("font-size:17px;font-weight:800;"); l.addWidget(a)
        b=QLabel(desc); b.setObjectName("muted"); b.setWordWrap(True); l.addWidget(b)
        return frame

    def _set_active_nav(self, section):
        for name,b in self.nav_buttons.items(): b.setProperty("active", name==section); b.style().unpolish(b); b.style().polish(b)

    def navigate(self, section):
        self.section=section; self.current_user=""; self.account_tabs.hide(); self.heading.setText(section); self._set_active_nav(section)
        descriptions={"Home":"Your locally archived X timeline","Bookmarks":"Posts saved to your X account or imported into your archive","Watch Later":"Downloaded videos queued for later viewing","Likes":"Posts liked by your account","Media Library":"All media attachments found in your archive","Archive":"Your saved posts, profiles and media metadata","Explore":"Search and discover posts in your local archive","Follow":"Accounts you follow (provider support required)","Lists":"Lists available through your configured provider","Accounts":"Your archived accounts","Download History":"Media download activity"}
        self.subheading.setText(descriptions.get(section,"Browse your local archive"))
        self.sync_button.setVisible(section in ("Bookmarks", "Likes", "Follow", "Lists"))
        self.export_button.hide()
        self.sync_button.setText({"Bookmarks":"Sync bookmarks", "Likes":"Sync likes", "Follow":"Sync following", "Lists":"Sync lists"}.get(section,"Sync collection"))
        self.refresh()

    def refresh(self):
        while self.feed_layout.count():
            item=self.feed_layout.takeAt(0); w=item.widget()
            if w: w.deleteLater()
        posts=self.db.list_posts(self.section,self.query,self.current_user,account_tab=self.account_tab)
        if self.section=="Accounts":
            self._show_accounts(); self._update_stats(); return
        if not posts:
            empty=QLabel("Your archive is waiting for its first posts.\n\nUse ‘Archive account’ to scan an account, or import a JSON archive.")
            empty.setAlignment(Qt.AlignCenter); empty.setStyleSheet("color:#71767b;padding:70px 35px;font-size:15px;"); self.feed_layout.addWidget(empty)
        else:
            for p in posts:
                card=PostCard(p); card.action.connect(self.post_action); card.media_requested.connect(self.open_post_media); self.feed_layout.addWidget(card)
        self.feed_layout.addStretch(1); self._update_stats()

    def _show_accounts(self):
        with self.db.connect() as c: rows=c.execute("SELECT * FROM accounts ORDER BY username").fetchall()
        if not rows:
            self.feed_layout.addWidget(QLabel("No accounts archived yet. Use Archive account to begin.")); return
        for r in rows:
            row=QFrame(); row.setObjectName("post"); l=QHBoxLayout(row); l.setContentsMargins(18,16,18,16)
            avatar=QLabel(); avatar.setFixedSize(52,52); avatar.setAlignment(Qt.AlignCenter)
            avatar.setStyleSheet("background:#202327;border-radius:26px;color:#71767b;")
            avatar.setText("◉")
            if r["avatar_path"] and Path(r["avatar_path"]).is_file():
                pix=QPixmap(r["avatar_path"])
                if not pix.isNull(): avatar.setPixmap(pix.scaled(52,52,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
            l.addWidget(avatar,0,Qt.AlignVCenter)
            title=QLabel(f"{r['display_name'] or r['username']}\n@{r['username']} · Last scan: {r['last_scan'] or 'Never'}"); title.setWordWrap(True); l.addWidget(title,1)
            banner=QLabel(); banner.setFixedSize(112,52); banner.setAlignment(Qt.AlignCenter)
            banner.setStyleSheet("background:#16181c;border:1px solid #2f3336;border-radius:8px;color:#71767b;")
            banner.setText("No header")
            if r["banner_path"] and Path(r["banner_path"]).is_file():
                pix=QPixmap(r["banner_path"])
                if not pix.isNull(): banner.setPixmap(pix.scaled(112,52,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
            l.addWidget(banner,0,Qt.AlignVCenter)
            b=QPushButton("View posts"); b.clicked.connect(lambda checked=False,u=r['username']:self.view_account(u)); l.addWidget(b)
            scan=QPushButton("Scan again"); scan.clicked.connect(lambda checked=False,u=r['username']:self.start_scan(u,"Home")); l.addWidget(scan)
            self.feed_layout.addWidget(row)
        self.feed_layout.addStretch(1)

    def view_account(self, username):
        # Keep the account filter as view state; clear any previous global search so
        # opening an account cannot accidentally filter every post away.
        self.section = "Home"
        self.current_user = username.lstrip("@")
        self.account_tab = "Posts"
        self.account_tabs.blockSignals(True); self.account_tabs.setCurrentIndex(0); self.account_tabs.blockSignals(False); self.account_tabs.show()
        self.export_button.show()
        repaired = self.db.repair_post_authors(self.current_user)
        self.query = ""
        self.search.clear()
        self.heading.setText("@" + self.current_user)
        self.subheading.setText("Archived account timeline · local posts" + (f" · repaired {repaired} legacy post author(s)" if repaired else ""))
        self._set_active_nav("Accounts")
        self.refresh()

    def _account_tab_changed(self, index):
        self.account_tab = self.account_tabs.tabText(index)
        if self.current_user:
            self.subheading.setText(f"@{self.current_user} · {self.account_tab} · newest first · local archive")
            self.refresh()

    def open_post_media(self, post, selected_index=0):
        try:
            media = json.loads(post.get("media_json") or "[]")
        except Exception:
            media = []
        if not media:
            return
        with self.db.connect() as c:
            rows = c.execute("SELECT media_url,local_path FROM downloads WHERE post_id=? AND status='complete' ORDER BY id", (post.get("post_id"),)).fetchall()
        by_url = {r[0]: r[1] for r in rows if r[0] and r[1] and Path(r[1]).is_file()}
        items=[]
        for item in media:
            entry=dict(item); entry["local_path"] = by_url.get(entry.get("url") or entry.get("media_url"), "")
            items.append(entry)
        if self.media_viewer is None:
            self.media_viewer = MediaViewerDialog(self)
            self.media_viewer.setAttribute(Qt.WA_DeleteOnClose, False)
        self.media_viewer.set_items(items, selected_index)
        self.media_viewer.show(); self.media_viewer.raise_(); self.media_viewer.activateWindow()

    def _update_stats(self):
        p,m,a,d=self.db.counts()
        self.stats_value_label.setText(f"{p:,} posts  ·  {m:,} with media\n{a:,} accounts  ·  {d:,} downloads")

    def search_archive(self):
        self.query=self.search.text().strip(); self.section="Explore"; self.heading.setText("Search"); self.subheading.setText(f"Results in your local archive for ‘{self.query}’"); self.refresh()

    def sync_collection(self):
        section = self.section
        username = "me"
        if section in ("Likes", "Follow", "Lists"):
            username, ok = QInputDialog.getText(self, f"Sync {section.lower()}", "X account username (without @):")
            if not ok or not username.strip(): return
        self.start_scan(username, section)

    def export_account(self):
        if not self.current_user:
            QMessageBox.information(self,"Export account","Open an archived account first."); return
        if hasattr(self,"export_worker") and self.export_worker and self.export_worker.isRunning():
            QMessageBox.information(self,"Export running","An account export is already running."); return
        answer=QMessageBox.question(self,"Export full account",
            f"Export all locally archived posts and available media for @{self.current_user}?\n\nThe export will be saved under the configured archive directory. Existing files will be reused and the export can resume later.",
            QMessageBox.Yes|QMessageBox.No,QMessageBox.Yes)
        if answer!=QMessageBox.Yes: return
        self.export_worker=AccountExportWorker(self.db,self.settings,self.current_user)
        self.export_worker.progress.connect(self.activity_text.setText)
        self.export_worker.finished_export.connect(self.export_finished)
        self.progress.show(); self.stop_button.setText("Pause export"); self.stop_button.show()
        self.export_worker.start()

    def export_finished(self,ok,path,message):
        self.progress.hide(); self.stop_button.hide(); self.stop_button.setText("Stop scan")
        self.activity_text.setText(message); self.statusBar().showMessage(message,10000)
        if path and Path(path).exists():
            QMessageBox.information(self,"Account export",message+"\n\nFolder: "+path)
        elif not ok: QMessageBox.warning(self,"Account export",message)

    def add_account(self):
        username,ok=QInputDialog.getText(self,"Archive an X account","Enter an X username or profile URL:")
        if not ok or not username.strip(): return
        match=re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)",username)
        username=match.group(1) if match else username.strip().lstrip("@").split("/")[0]
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}",username):
            QMessageBox.warning(self,"Invalid username","Enter a valid X username or profile URL."); return
        self.start_scan(username,"Home")

    def start_scan(self, username, section="Home"):
        if self.scan_worker and self.scan_worker.isRunning():
            QMessageBox.information(self,"Scan running","Stop the current scan before starting another."); return
        self.engine=XApiCompatibleEngine(self.settings)
        self.scan_worker=ScanWorker(self.db,self.engine,section,username,100,200)
        self.scan_worker.message.connect(self.activity_text.setText)
        self.scan_worker.progress.connect(self.scan_progress)
        self.scan_worker.finished_scan.connect(self.scan_finished)
        self.progress.show(); self.stop_button.show(); self.activity_text.setText(f"Starting {section.lower()} scan for @{username}…")
        self.scan_worker.start()

    def scan_progress(self, username, total, added):
        self.activity_text.setText(f"@{username}\n{total:,} posts received · {added:,} newly archived")

    def scan_finished(self, added, message):
        self.progress.hide(); self.stop_button.hide(); self.activity_text.setText(message+f"\n{added:,} posts newly archived")
        self.statusBar().showMessage(message,8000); self.refresh()
        if message.startswith("Scan error:"):
            QMessageBox.warning(self,"Scan failed",message+"\n\nCheck browser sign-in, Playwright installation, and TwitterAPIs fallback settings.")

    def stop_scan(self):
        if hasattr(self,"export_worker") and self.export_worker and self.export_worker.isRunning():
            self.export_worker.stop(); self.activity_text.setText("Pausing export after the current media item…"); return
        if self.scan_worker: self.scan_worker.stop(); self.activity_text.setText("Stopping after the current request…")

    def post_action(self,pid,action):
        with self.db.connect() as c: row=c.execute("SELECT * FROM posts WHERE post_id=?",(pid,)).fetchone()
        if not row: return
        post=dict(row)
        if action in ("like","bookmark","watch"):
            field={"like":"is_liked","bookmark":"is_bookmarked","watch":"is_watch_later"}[action]
            self.db.toggle_flag(pid,field,not bool(post[field])); self.refresh()
            self.statusBar().showMessage({"like":"Like state updated locally.","bookmark":"Bookmark saved locally.","watch":"Watch Later collection updated."}[action],4000)
        elif action=="open": QDesktopServices.openUrl(QUrl(post["post_url"]))
        elif action=="download": self.download_post(post)
        elif action=="play":
            with self.db.connect() as c:
                rows=c.execute("SELECT media_url,local_path FROM downloads WHERE post_id=? AND status='complete' ORDER BY id",(pid,)).fetchall()
            if rows:
                items=[{"url":r[0],"local_path":r[1],"type":Path(r[1]).suffix.lstrip(".")} for r in rows if r[1] and Path(r[1]).exists()]
                if items: self.open_post_media({**post,"media_json":json.dumps(items)},0)
                else: QMessageBox.information(self,"Media unavailable","The downloaded file path no longer exists.")
            else:
                QMessageBox.information(self,"Not downloaded yet","Download this post's media first. Then use Play to watch the local copy.")

    def download_post(self,post):
        worker=DownloadWorker(self.db,post,self.settings); self.download_workers.append(worker)
        worker.progress.connect(self.activity_text.setText)
        worker.done.connect(self.download_finished); worker.start()
        self.activity_text.setText("Download queued…")

    def download_finished(self,pid,ok,message):
        self.activity_text.setText(message); self.statusBar().showMessage(message,8000); self.refresh()

    def open_settings(self):
        dlg=SettingsDialog(self.settings,self)
        if dlg.exec_()==QDialog.Accepted:
            self.engine=XApiCompatibleEngine(self.settings); self.statusBar().showMessage("Settings saved",4000)

    def import_json(self):
        path,_=QFileDialog.getOpenFileName(self,"Import posts JSON","","JSON files (*.json)")
        if not path: return
        try:
            payload=json.loads(Path(path).read_text(encoding="utf-8-sig")); posts,_,account=XApiCompatibleEngine._unwrap(payload)
            if not posts and isinstance(payload,list): posts=payload
            count=0
            for p in posts:
                if isinstance(p,dict) and self.db.save_post(p,"imported"): count+=1
            if account: self.db.upsert_account(account)
            self.statusBar().showMessage(f"Imported {count} posts",7000); self.refresh()
        except Exception as e: QMessageBox.critical(self,"Import failed",str(e))

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_I and event.modifiers() & Qt.ControlModifier: self.import_json()
        else: super().keyPressEvent(event)


def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyle("Fusion"); app.setStyleSheet(DARK_QSS)
    window=MainWindow(); window.show(); sys.exit(app.exec_())

if __name__=="__main__": main()
