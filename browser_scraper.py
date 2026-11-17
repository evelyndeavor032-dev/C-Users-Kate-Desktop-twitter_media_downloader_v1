"""Primary X browser scraper for Twitter Media Downloader v1.3.0.

Uses a user-visible Playwright browser context. The user signs in directly on X;
the application never reads, exports, or asks the user to paste browser cookies.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

APP_DATA = Path.home() / ".twitter_media_downloader" / "browser_profile"

def _walk_tweets(node: Any, found: dict[str, dict]) -> None:
    if isinstance(node, dict):
        legacy = node.get("legacy")
        rest_id = node.get("rest_id") or node.get("id_str")
        if isinstance(legacy, dict) and rest_id and (legacy.get("full_text") is not None or legacy.get("text") is not None):
            core = node.get("core") or {}
            user_result = ((core.get("user_results") or {}).get("result") or {})
            ul = user_result.get("legacy") or {}
            user = {"username": ul.get("screen_name") or user_result.get("screen_name") or "",
                    "name": ul.get("name") or "",
                    "profile_image_url_https": ul.get("profile_image_url_https") or ""}
            entities = legacy.get("extended_entities") or legacy.get("entities") or {}
            media = []
            for item in entities.get("media", []) or []:
                mtype = item.get("type", "photo")
                vi = item.get("video_info") or {}
                variants = vi.get("variants") or []
                mp4 = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
                if mtype == "animated_gif":
                    url = max(mp4, key=lambda v: int(v.get("bitrate") or 0)).get("url") if mp4 else ""
                    media.append({"url": url, "type": "gif", "container": "mp4", "thumbnail": item.get("media_url_https", "")})
                elif mtype == "video":
                    url = max(mp4, key=lambda v: int(v.get("bitrate") or 0)).get("url") if mp4 else item.get("media_url_https", "")
                    media.append({"url": url, "type": "video", "thumbnail": item.get("media_url_https", "")})
                else:
                    url = item.get("media_url_https") or item.get("media_url") or ""
                    if url:
                        url = re.sub(r"([?&])name=[^&]+", r"\1name=orig", url)
                        if "name=" not in url: url += ("&" if "?" in url else "?") + "name=orig"
                    media.append({"url": url, "type": "photo", "width": (item.get("original_info") or {}).get("width"),
                                  "height": (item.get("original_info") or {}).get("height"), "alt": item.get("ext_alt_text") or ""})
            pid = str(rest_id)
            found[pid] = {"id": pid, "post_id": pid, "text": legacy.get("full_text") or legacy.get("text") or "",
                          "created_at": legacy.get("created_at") or "", "user": user, "username": user["username"],
                          "display_name": user["name"], "media": media, "post_url": "https://x.com/i/status/" + pid}
        for value in node.values(): _walk_tweets(value, found)
    elif isinstance(node, list):
        for value in node: _walk_tweets(value, found)

def _dom_posts(page, username: str) -> list[dict]:
    return page.locator('article[data-testid="tweet"]').evaluate_all("""els => els.map(el => {
      const text = el.querySelector('[data-testid="tweetText"]')?.innerText || '';
      const time = el.querySelector('time');
      const link = time?.closest('a')?.href || '';
      const id = (link.match(/status\/(\d+)/) || [])[1] || '';
      const imgs = [...el.querySelectorAll('img')].filter(x => /pbs.twimg.com\/media/.test(x.src));
      const media = imgs.map(x => ({url:x.src.replace(/([?&])name=[^&]+/, '$1name=orig'),type:'photo',alt:x.alt||''}));
      const videos = [...el.querySelectorAll('video')].map(v => ({url:v.currentSrc||v.src||v.querySelector('source')?.src||'',type:'video',thumbnail:v.poster||''})).filter(x=>x.url);
      const name = el.querySelector('[data-testid="User-Name"]')?.innerText || '';
      const handle = (name.match(/@([A-Za-z0-9_]+)/) || [])[1] || '';
      return {id,post_id:id,text,created_at:time?.dateTime||'',username:handle,display_name:name.split('\n')[0]||handle,
              user:{username:handle,name:name.split('\n')[0]||handle},media:[...media,...videos],
              post_url:link||(id?'https://x.com/i/status/'+id:'')};
    }).filter(x=>x.id&&(x.text||x.media.length))""") or []

class BrowserScraper:
    def __init__(self, browser_name="Edge", headless=False, timeout_ms=30000):
        self.browser_name, self.headless, self.timeout_ms = browser_name, headless, timeout_ms

    def fetch_page(self, username: str, page_index: int = 0, section: str = "Home", stop_check=None):
        if section not in ("Home", "Explore"):
            raise RuntimeError(f"Browser scraping for {section} is not available; use TwitterAPIs fallback for supported collections.")
        username = username.lstrip("@")
        APP_DATA.mkdir(parents=True, exist_ok=True)
        found, account = {}, {"username": username, "display_name": username}
        with sync_playwright() as p:
            errors, context = [], None
            channels = ["msedge", "chrome"] if self.browser_name.lower() in ("edge", "auto") else ["chrome", "msedge"]
            channels.append(None)  # Playwright-managed Chromium fallback
            for channel in channels:
                try:
                    launch_options = {"user_data_dir":str(APP_DATA), "headless":self.headless,
                        "viewport":{"width":1440,"height":1000},
                        "args":["--disable-blink-features=AutomationControlled"], "timeout":self.timeout_ms}
                    if channel: launch_options["channel"] = channel
                    context = p.chromium.launch_persistent_context(**launch_options)
                    break
                except Exception as exc: errors.append(f"{channel}: {exc}")
            if context is None:
                raise RuntimeError("Could not launch Edge or Chrome with Playwright. Install Playwright and run python -m playwright install chromium. " + " | ".join(errors))
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(self.timeout_ms)
                def on_response(response):
                    try:
                        if "json" not in response.headers.get("content-type","").lower(): return
                        if not any(t in response.url.lower() for t in ("graphql","timeline","tweet","user")): return
                        _walk_tweets(response.json(), found)
                    except Exception: pass
                page.on("response", on_response)
                page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=self.timeout_ms)
                try: page.wait_for_selector('article[data-testid="tweet"], [data-testid="primaryColumn"]', timeout=25000)
                except PlaywrightTimeoutError:
                    if "login" in page.url.lower() or page.locator('input[name="text"]').count():
                        raise RuntimeError("X sign-in is required. Sign in manually in the opened browser window, then retry.")
                    raise RuntimeError("X profile did not load. Check connectivity and profile visibility.")
                if page.locator('input[name="text"]').count() and page.locator('article[data-testid="tweet"]').count()==0:
                    raise RuntimeError("X sign-in is required. Sign in manually in the browser, then retry.")
                name = page.locator('[data-testid="UserName"]').first
                if name.count():
                    try: account["display_name"] = name.inner_text(timeout=3000).split("\n")[0]
                    except Exception: pass
                for _ in range(max(0,page_index)*5):
                    if stop_check and stop_check(): break
                    page.mouse.wheel(0,1800); page.wait_for_timeout(850)
                for _ in range(5):
                    if stop_check and stop_check(): break
                    page.mouse.wheel(0,1500); page.wait_for_timeout(1000)
                    try:
                        for post in _dom_posts(page,username): found.setdefault(str(post["id"]),post)
                    except Exception: pass
                for post in _dom_posts(page,username): found.setdefault(str(post["id"]),post)
                posts=sorted(found.values(),key=lambda x:int(x.get("id") or 0),reverse=True)
                if not posts: raise RuntimeError("X loaded but no accessible posts were discovered. Sign in manually and retry.")
                return posts, account, str(page_index+1) if len(posts)>=5 else None
            finally: context.close()
