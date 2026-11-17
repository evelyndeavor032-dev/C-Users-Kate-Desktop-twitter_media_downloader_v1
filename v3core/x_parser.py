"""Defensive normalization of X/Twitter tweet and media payloads.

This module accepts both legacy REST-shaped tweets and common GraphQL
TweetWithVisibilityResults/Tweet shapes. It never attempts to bypass access
controls; it only normalizes payloads already delivered to the signed-in session.
"""
from __future__ import annotations
import re
from typing import Any

def _dict(value):
    return value if isinstance(value, dict) else {}

def _unwrap_tweet(node: dict) -> dict:
    node = _dict(node)
    for _ in range(5):
        typename = str(node.get("__typename", ""))
        if typename in {"TweetTombstone", "TweetUnavailable"}:
            return {}
        candidate = node.get("tweet") or node.get("result")
        if isinstance(candidate, dict) and candidate is not node:
            node = candidate
            continue
        break
    return node

def _walk(node: Any):
    if isinstance(node, dict):
        candidate = _unwrap_tweet(node)
        legacy = _dict(candidate.get("legacy"))
        rest_id = candidate.get("rest_id") or candidate.get("id_str") or legacy.get("id_str")
        if rest_id and (legacy or candidate.get("text") is not None):
            yield candidate
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)

def _best_mp4(variants):
    mp4 = [v for v in variants or [] if isinstance(v, dict)
           and "mp4" in str(v.get("content_type", "")).lower() and v.get("url")]
    def quality(v):
        try: return int(v.get("bitrate") or 0)
        except (TypeError, ValueError): return 0
    return max(mp4, key=quality).get("url", "") if mp4 else ""

def _media_from_entities(entities):
    output, seen = [], set()
    entities = _dict(entities)
    raw_items = entities.get("media") or []
    for item in raw_items:
        item = _dict(item)
        kind = str(item.get("type") or "photo").lower()
        video_info = _dict(item.get("video_info"))
        variants = video_info.get("variants") or []
        thumb = item.get("media_url_https") or item.get("media_url") or ""
        if kind in {"video", "animated_gif", "gif"}:
            url = _best_mp4(variants)
            media_type = "gif" if kind in {"animated_gif", "gif"} else "video"
            if url:
                key = (url, media_type)
                if key not in seen:
                    seen.add(key); output.append({"url": url, "type": media_type, "container": "mp4",
                        "thumbnail": thumb, "duration_ms": video_info.get("duration_millis"),
                        "alt": item.get("ext_alt_text") or ""})
        else:
            url = thumb
            if url:
                url = re.sub(r"([?&])name=[^&]+", r"\1name=orig", url)
                if "name=" not in url: url += ("&" if "?" in url else "?") + "name=orig"
                key = (url, "image")
                if key not in seen:
                    seen.add(key); output.append({"url": url, "type": "image",
                        "width": _dict(item.get("original_info")).get("width"),
                        "height": _dict(item.get("original_info")).get("height"),
                        "alt": item.get("ext_alt_text") or ""})
    return output

def _media_from_graphql(tweet):
    legacy = _dict(tweet.get("legacy"))
    result = _media_from_entities(legacy.get("extended_entities") or legacy.get("entities"))
    # Newer shapes may place media under note_tweet or card; preserve only
    # explicit, direct media URLs found in known media-bearing structures.
    for container in (tweet.get("attachments"), _dict(tweet.get("note_tweet")).get("attachments")):
        for item in _dict(container).get("media", []) or []:
            item = _dict(item)
            url = item.get("media_url_https") or item.get("url") or item.get("src")
            if url:
                kind = str(item.get("type") or "image").lower()
                result.append({"url": url, "type": "video" if "video" in kind else "image",
                               "alt": item.get("alt_text") or ""})
    deduped, seen = [], set()
    for item in result:
        key = (item.get("url"), item.get("type"))
        if key[0] and key not in seen: seen.add(key); deduped.append(item)
    return deduped

def normalize_tweet(tweet: dict) -> dict | None:
    tweet = _unwrap_tweet(tweet)
    legacy = _dict(tweet.get("legacy"))
    post_id = str(tweet.get("rest_id") or tweet.get("id_str") or legacy.get("id_str") or "")
    if not post_id: return None
    core = _dict(tweet.get("core"))
    user_result = _dict(_dict(core.get("user_results")).get("result"))
    user_legacy = _dict(user_result.get("legacy"))
    username = (user_legacy.get("screen_name") or user_result.get("screen_name")
                or _dict(tweet.get("user")).get("screen_name") or "")
    display_name = user_legacy.get("name") or user_result.get("name") or username
    text = (legacy.get("full_text") or legacy.get("text") or tweet.get("text")
            or _dict(tweet.get("note_tweet")).get("note_tweet_results", {}).get("result", {}).get("text") or "")
    entities = legacy.get("extended_entities") or legacy.get("entities") or {}
    media = _media_from_entities(entities)
    if not media: media = _media_from_graphql(tweet)
    return {"id": post_id, "post_id": post_id, "username": username,
            "display_name": display_name, "text": text,
            "created_at": legacy.get("created_at") or tweet.get("created_at") or "",
            "post_url": f"https://x.com/{username}/status/{post_id}" if username else f"https://x.com/i/status/{post_id}",
            "media": media, "raw": tweet}

def extract_posts(payload: Any) -> list[dict]:
    posts, seen = [], set()
    for tweet in _walk(payload):
        item = normalize_tweet(tweet)
        if item and item["id"] not in seen:
            seen.add(item["id"]); posts.append(item)
    return posts
