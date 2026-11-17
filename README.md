# Twitter Media Downloader v1.2.1

X-inspired Windows desktop archive browser with SocialData and TwitterAPIs provider adapters.

## Install / run (Windows 10/11)

1. Extract the ZIP to a normal writable folder.
2. Install Python 3.11+ (64-bit) and enable **Add Python to PATH**.
3. Double-click `launch.bat`.
4. Open **Settings → API Providers** and configure the provider keys.
5. Scan an account, then use **Accounts → View posts**.

The launcher installs packages from `requirements.txt` and starts the GUI.

## v1.1.3 changes

- Fixed media opening to use a reusable, non-modal in-app media viewer. Closing the viewer does not close the main application; it can be reopened repeatedly for other posts/media.
- Added previous/next controls for media attached to a post.
- The viewer previews downloaded images and plays supported local/remote video media when Qt Multimedia is available. If a codec/format is unsupported, the source can still be opened externally while the archive remains running.
- Added X-style account tabs in this exact order: **Posts → Replies → Reposts → Media**.
- Categories are derived from the API's post flags/references and media attachments. Media is an overlapping filter, as on X: a post with media can also belong to Posts, Replies or Reposts.
- Account tabs are sorted newest-first. API timestamps are normalized to UTC ISO timestamps so sorting does not mix RFC date strings and ISO strings incorrectly.
- Account scanning now uses TwitterAPIs' cursor-paginated `user/tweets_and_replies` activity timeline, which includes the account's own replies and retweets; items are classified locally into the four profile tabs. This avoids calling overlapping timeline endpoints just to build the tabs.
- Increased the per-run page safety cap to 200 pages. If the cap is reached while a cursor remains, the app clearly reports that the checkpoint is saved and another Scan again resumes from it.
- Existing databases are migrated automatically with a `post_kind` column and backfilled from stored raw post JSON. Existing posts, profile assets, API settings and download records are preserved.

## Provider behavior and limits

TwitterAPIs documents `user/tweets_and_replies` as a cursor-paginated profile activity timeline containing the account's own replies, and says each result includes `is_retweet` and `is_reply` flags for client-side filtering. The app uses those flags plus reference/legacy fields when present. The provider/X may still limit what is retrievable; deleted, protected or otherwise inaccessible posts cannot be recovered. A scan that reaches the configured page cap can be resumed with **Scan again**.

SocialData support remains available for public profile/timeline retrieval. The exact fields returned depend on provider response shape. API use may incur charges; review each provider's pricing and credits before deep scans.

## Data safety

- Do not paste X session cookies into this app or into chat. Use only the provider's documented secure session registration flow for account-specific collections.
- The app does not bypass access controls, protected-account permissions or provider rate limits.
- Keep the `data` folder when upgrading. It contains the local SQLite archive and settings.

## Project files

- `app.py` — GUI, API adapters, SQLite archive, scan/download workers and media viewer.
- `launch.bat` — Windows launcher.
- `requirements.txt` — Python dependencies.
- `data/` — created automatically for settings and archive database.
- `Twitter_Archive/` — created automatically for downloaded media and account profile assets.


## v1.1.4 media viewer improvements

- Preserves the reusable in-app video viewer from v1.1.3.
- Adds in-app image previews with Fit, 100%, zoom-in and zoom-out controls.
- Adds animated GIF playback through Qt's QMovie, with Play/Pause and automatic looping.
- Remote image/GIF previews are cached locally under `data/media_preview_cache` so the same media can be reopened without repeatedly fetching it.
- Previous/Next navigation and the non-modal reusable viewer remain available.
- If a GIF codec/plugin is unavailable in the local Qt installation, the viewer reports that clearly instead of silently displaying a broken frame.


## v1.2.0 full-account export

- Adds an **Export account** action when viewing an archived account.
- Exports the posts currently stored in the local SQLite archive into `Twitter_Archive/<username>/`.
- Keeps account content in chronological order in `Metadata/account_archive.json` and provides a newest-first, self-contained `Posts/index.html` browser.
- Creates separate `Media/Images`, `Media/Videos`, `Media/GIFs`, `Media/Audios` and `Media/Other` folders. Media is copied from existing completed downloads when available; otherwise direct media URLs are downloaded and yt-dlp is used for supported video/audio/HLS sources with best-available format selection.
- Writes individual post JSON files and category indexes for Posts, Replies and Reposts, plus grouped conversation data in `Threads/conversations.json` when provider metadata supplies a shared conversation ID. Text-only posts are included.
- Saves account profile avatar/banner files when their existing local paths are available.
- Uses deterministic media filenames, reuses existing export files, and writes `Metadata/export_checkpoint.json` so interrupted exports can be resumed. Failed media URLs are recorded in `Metadata/media_failures.json`.
- Existing database, provider settings, scan checkpoints and the reusable in-app image/GIF/video viewer are preserved.

### Important scope note
The exporter exports everything that has already been collected into the local archive; it does not claim to retrieve an account's entire lifetime history by itself. First use **Scan again** until the provider reports that no further pages remain, then choose **Export account**. If the provider page safety cap is reached, repeat scanning to continue from its saved cursor. Availability of deleted, protected, or provider-restricted content cannot be guaranteed. Audio is exported when the provider supplies a downloadable audio attachment URL.

The JSON manifest and individual post records are the portable source of truth. HTML is a convenient browser view; media codecs and playback behavior depend on the user's Windows/Qt environment.


## v1.2.1 media download fix

- Fixes direct X/Twitter CDN media downloads (`video.twimg.com` / `pbs.twimg.com`) by streaming the supplied media URL directly with Requests instead of invoking yt-dlp. This avoids the Chrome cookie-database copy error for direct MP4 links, including `amplify_video` URLs.
- Uses temporary `.part` files and only moves them into the archive after a successful, non-empty download, preventing incomplete files from appearing as completed media.
- Keeps yt-dlp for HLS/DASH and extractor-only URLs. If yt-dlp fails specifically because it cannot copy a browser cookie database, it retries once without browser cookies.
- Preserves the existing account archive layout, SQLite database, provider settings, checkpoints, and reusable in-app image/GIF/video viewer.

### Upgrade note
Close v1.2.0 before extracting this version. Keep your existing `data` and `Twitter_Archive` folders. Run **Export account** again for the affected account; previously failed media are retried automatically because they were not recorded as completed.
