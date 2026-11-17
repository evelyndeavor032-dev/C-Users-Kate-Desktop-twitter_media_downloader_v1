# Twitter Media Downloader — v3.0.0 (Rebuild Preview)

This branch starts the v3.0.0 backend rebuild while retaining the existing PyQt5
application window, dark X-inspired styling, post cards, settings and in-app
image/GIF/video viewer from the previous application.

> **Status:** development preview, not a validated production release. The
> existing GUI is retained, the new X payload normalizer is connected to the
> browser response collector, and new persistent queue/downloader/export
> foundation modules are included. Full migration of every existing GUI worker
> to the new queue is still in progress. Do not treat this ZIP as proof that all
> account scans or media downloads now work.

## Windows installation

1. Extract the ZIP into a writable folder (do not extract over an older install).
2. Install 64-bit Python 3.11 or newer and enable **Add Python to PATH**.
3. Double-click `launch.bat`.
4. Sign in to X only in the visible Playwright browser window if prompted.
5. Keep the existing installation's `data` and `Twitter_Archive` folders backed
   up. This preview does not delete or overwrite those folders.

The launcher installs Python requirements, installs the Playwright Chromium
runtime, and starts the GUI. It requires an internet connection for setup.

## Included files

- `app.py` — retained desktop GUI, existing settings, archive display and media viewer.
- `browser_scraper.py` — visible-browser discovery; captured JSON payloads are
  normalized through the v3 parser.
- `v3core/x_parser.py` — normalizes legacy and common GraphQL tweet/media shapes.
- `v3core/downloads.py` — persistent SQLite download queue, bounded retries,
  streamed downloads, temporary `.part` files, file-signature checks and SHA-256.
- `v3core/export_manifest.py` — deterministic chronological JSON archive manifest
  and categorized media-folder layout.
- `tests/` — offline unit tests for parser and queue foundations.
- `requirements.txt` — runtime dependencies.
- `launch.bat` — Windows setup and GUI launcher.

## Run offline tests

From the extracted project folder:

```bat
py -3 -m unittest discover -s tests -v
```

## Archive and access limitations

The browser scanner can only collect content available to the signed-in account
and delivered to the browser. It does not bypass protected-account permissions,
CAPTCHAs, anti-bot challenges or X rate limits. Deleted, protected or otherwise
inaccessible posts cannot be guaranteed. Keep a backup of your existing database
and media before testing any preview build.

## Upgrade safety

This preview does not intentionally reset the existing SQLite archive. Use a
copy of your current `data` and `Twitter_Archive` directories for testing, and
retain your original installation until the new backend has been integrated and
validated on Windows.
