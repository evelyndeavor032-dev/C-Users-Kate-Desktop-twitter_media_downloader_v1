"""Deterministic account archive manifest and media folder mapping."""
from __future__ import annotations
import json, re
from pathlib import Path
from datetime import datetime, timezone

FOLDERS={"image":"Images","photo":"Images","video":"Videos","gif":"GIFs",
         "audio":"Audios","other":"Other","document":"Other"}

def safe_component(value):
    value=re.sub(r"[^\w.-]+","_",str(value or "unknown"),flags=re.UNICODE).strip("._")
    return value[:100] or "unknown"

def media_folder(media_type):
    return FOLDERS.get(str(media_type or "other").lower(),"Other")

def build_manifest(username, posts, root):
    root=Path(root); username=safe_component(username)
    ordered=sorted(posts,key=lambda p:(str(p.get("created_at") or ""),str(p.get("post_id") or p.get("id") or "")))
    records=[]
    for post in ordered:
        record={k:post.get(k) for k in ("post_id","id","username","display_name","text","created_at","post_url","post_kind","conversation_id")}
        record["media"]=[]
        for pos,media in enumerate(post.get("media") or []):
            m=dict(media); m["position"]=pos
            m["folder"]=f"Media/{media_folder(m.get('type'))}"
            record["media"].append(m)
        records.append(record)
    for folder in ("Profile","Posts","Replies","Reposts","Threads","Media/Images","Media/Videos","Media/GIFs","Media/Audios","Media/Other","Metadata"):
        (root/username/folder).mkdir(parents=True,exist_ok=True)
    output=root/username/"Metadata"/"account_archive.json"
    tmp=output.with_suffix(".json.part")
    tmp.write_text(json.dumps({"schema_version":3,"username":username,"exported_at":datetime.now(timezone.utc).isoformat(),"posts":records},ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(output)
    return output,records
