"""Persistent, resumable media queue and verified HTTP downloader."""
from __future__ import annotations
import hashlib, mimetypes, os, sqlite3, time
from pathlib import Path
from urllib.parse import urlparse
import requests

SCHEMA = """
CREATE TABLE IF NOT EXISTS v3_download_queue (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 post_id TEXT NOT NULL, media_url TEXT NOT NULL, media_type TEXT NOT NULL DEFAULT 'other',
 target_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
 last_error TEXT, sha256 TEXT, size_bytes INTEGER, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(post_id, media_url)
);
CREATE INDEX IF NOT EXISTS idx_v3_queue_status ON v3_download_queue(status, id);
"""

def _signature(path: Path, media_type: str):
    with path.open("rb") as f: head=f.read(32)
    if media_type in ("image", "photo", "gif"):
        if head.startswith(b"\xff\xd8\xff"): return ".jpg"
        if head.startswith(b"\x89PNG\r\n\x1a\n"): return ".png"
        if head.startswith((b"GIF87a", b"GIF89a")): return ".gif"
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP": return ".webp"
        if len(head)>=12 and head[4:8]==b"ftyp" and head[8:12] in (b"avif",b"avis"): return ".avif"
        raise ValueError("Response is not a recognized image file")
    if media_type in ("video", "gif_video") or media_type=="gif":
        if len(head)>=12 and head[4:8]==b"ftyp": return ".mp4"
        if head.startswith(b"\x1aE\xdf\xa3"): return ".mkv"
        raise ValueError("Response is not a recognized video container")
    if len(head)<16: raise ValueError("Downloaded file is too small")
    return Path(urlparse(str(path)).path).suffix or ".bin"

class DownloadQueue:
    def __init__(self, db_path):
        self.db_path=Path(db_path); self.db_path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db: db.executescript(SCHEMA)
    def connect(self):
        db=sqlite3.connect(self.db_path,timeout=30)
        db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=FULL")
        return db
    def enqueue(self, post_id, url, media_type, target_path, max_attempts=5):
        with self.connect() as db:
            db.execute("""INSERT INTO v3_download_queue(post_id,media_url,media_type,target_path,max_attempts)
              VALUES(?,?,?,?,?) ON CONFLICT(post_id,media_url) DO UPDATE SET
              target_path=excluded.target_path, media_type=excluded.media_type
              WHERE v3_download_queue.status IN ('failed','queued')""",
              (str(post_id),str(url),str(media_type),str(target_path),int(max_attempts)))
    def next_items(self, limit=20):
        with self.connect() as db:
            return db.execute("""SELECT id,post_id,media_url,media_type,target_path,attempts,max_attempts
              FROM v3_download_queue WHERE status IN ('queued','retry') AND attempts<max_attempts
              ORDER BY id LIMIT ?""",(max(1,int(limit)),)).fetchall()
    def mark(self, item_id, status, error=None, sha256=None, size=None):
        with self.connect() as db:
            db.execute("""UPDATE v3_download_queue SET status=?,last_error=?,sha256=?,size_bytes=?,
              updated_at=CURRENT_TIMESTAMP WHERE id=?""",(status,error,sha256,size,item_id))
    def increment_attempt(self, item_id):
        with self.connect() as db:
            db.execute("UPDATE v3_download_queue SET attempts=attempts+1,status='downloading',updated_at=CURRENT_TIMESTAMP WHERE id=?",(item_id,))
    def summary(self):
        with self.connect() as db:
            return dict(db.execute("SELECT status,COUNT(*) FROM v3_download_queue GROUP BY status").fetchall())

class VerifiedDownloader:
    def __init__(self, timeout=(20,180), max_bytes=2*1024**3, user_agent=None):
        self.timeout=timeout; self.max_bytes=max_bytes
        self.headers={"User-Agent":user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
                      "Referer":"https://x.com/","Accept":"*/*"}
    def download(self, url, target_path, media_type="other"):
        target=Path(target_path); target.parent.mkdir(parents=True,exist_ok=True)
        part=target.with_name(target.name+".part")
        digest=hashlib.sha256(); total=0
        try:
            with requests.get(url,headers=self.headers,stream=True,timeout=self.timeout) as response:
                response.raise_for_status()
                ctype=response.headers.get("Content-Type","").lower()
                if any(x in ctype for x in ("text/html","application/json")):
                    raise ValueError(f"Server returned {ctype}, not media")
                expected=response.headers.get("Content-Length")
                expected=int(expected) if expected and expected.isdigit() else None
                with part.open("wb") as out:
                    for chunk in response.iter_content(256*1024):
                        if not chunk: continue
                        total+=len(chunk)
                        if total>self.max_bytes: raise ValueError("Media exceeds configured safety limit")
                        out.write(chunk); digest.update(chunk)
                if total<16: raise ValueError("Empty or truncated media response")
                if expected is not None and total!=expected: raise IOError(f"Incomplete response: {total}/{expected} bytes")
            suffix=_signature(part,media_type)
            final=target if target.suffix.lower()==suffix else target.with_suffix(suffix)
            if final.exists(): final.unlink()
            os.replace(part,final)
            return {"path":str(final),"size":total,"sha256":digest.hexdigest(),"content_type":ctype}
        except Exception:
            part.unlink(missing_ok=True); raise

    def process_one(self, queue: DownloadQueue, row):
        item_id,post_id,url,kind,target,attempts,max_attempts=row
        queue.increment_attempt(item_id)
        try:
            result=self.download(url,target,kind)
            queue.mark(item_id,"complete",None,result["sha256"],result["size"])
            return result
        except Exception as exc:
            new_attempts=attempts+1
            queue.mark(item_id,"retry" if new_attempts<max_attempts else "failed",str(exc))
            raise

    def drain(self, queue: DownloadQueue, batch_size=20, stop_check=None, on_item=None):
        completed, failed=0,0
        while not (stop_check and stop_check()):
            rows=queue.next_items(batch_size)
            if not rows: break
            for row in rows:
                if stop_check and stop_check(): break
                try: self.process_one(queue,row); completed+=1
                except Exception: failed+=1
                if on_item: on_item(row,completed,failed)
                time.sleep(0.15)
        return {"completed":completed,"failed":failed,"remaining":queue.summary()}
