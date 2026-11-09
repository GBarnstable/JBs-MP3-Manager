# JB's MP3 Manager

A local Windows desktop application for one master MP3 folder and any number of virtual categories. A song can belong to several categories without making extra copies.

**Version 0.4.7** adds **Metadata from filenames** for selected songs: preview Artist and Title from `artist--title.mp3`, choose either field or both, and approve individual files or the whole batch. Filename spelling is copied literally; other tags, artwork, audio, and categories are preserved. Files without both filename parts are skipped. Tag backups and per-file failures use the existing metadata editor. All previous rename, duplicate review, playback, and export features remain available. Existing libraries open directly and retain their categories. See [QUICK_START.md](QUICK_START.md) for the approval workflow and moving D: → C:.

## Included

- Create/open a portable SQLite library stored in `.jbs-mp3-manager` inside the master music folder.
- Import category folders with a match preview and permanent JSON/CSV reports. Matches use SHA-256 of the complete file, then SHA-256 of the encoded MP3 audio excluding supported ID3/APE tags. Different filenames and changed ID3 tags can match. Ambiguous or absent matches are reported rather than guessed.
- Many-to-many categories; create, rename, delete, batch add, and batch remove memberships. Untagged automatically shows songs with no categories.
- Search, sortable song table, metadata inspection, and single/batch editing of title, artist, album, genre, year, and track. Original ID3 tags are backed up; extra frames and artwork are preserved. Writes use a temporary file and atomic replacement.
- Scan at startup, react to master-folder changes, and rescan every minute while running. Optional recursive master scan. Incomplete/unreadable files retry on subsequent scans. Missing songs retain their memberships. Unique same-audio renames preserve song identity.
- Built-in VLC playback from the highlighted selection or the category, shuffle, repeat, prepared two-deck crossfade (0–12 seconds), 10-band EQ, volume, seeking, and speed (0.5×–2×). The Windows engine uses independent DirectSound buffer volumes, compensates VLC's volume taper during fades, and prepares the next decoder before fading the current song. VLC's tempo filter is enabled for speed changes. EQ compensates VLC's fixed 12 dB attenuation, and Reset to flat removes the filter completely.
- UTF-8 M3U8 playlists with relative paths where possible; absolute fallback across Windows drives.
- Explicit export dialogs choose a category, individual songs, or several categories. Combine categories with each song once or export one playlist/folder per category. Playlists default to `Exports/Playlists`; new MP3 export folders are created under `Exports`, with an accompanying playlist and manifest. Repeat exports use new names, and the entire Exports tree is excluded from scans, including recursive scans. Originals stay in the master folder.
- Duplicate-audio reporting, full verification scan, database backup, and protection against opening the same library twice in the desktop app.
- **Review issues** shows duplicate groups, complete candidate filenames/metadata, and unresolved/resolved import entries. Each keeper must be explicitly chosen. Merging preserves the keeper's ID, filename, and MP3 tags; combines memberships from every copy and pending report row; deletes extra files when the explicit deletion option is checked (or archives them when unchecked); and removes redundant database records without leaving Missing files. Saved import reports can resolve directly even if the original category folders are unavailable. Future CSV reports include candidate filenames too.
- Merge backups live in `.jbs-mp3-manager/duplicate-backups/<batch>`: a complete pre-merge SQLite snapshot, numbered MP3 archives when deletion is unchecked, and a manifest mapping their original filenames. An unfinished file move is rolled back before the next startup scan; a committed database ledger prevents incorrectly undoing a completed merge. Backups are excluded from scanning and travel with the library.

- Sortable bitrate, file size, and sample-rate columns, with fuller quality details in duplicate reviews.
- Manual rename review starts with filenames only; Artist/Title metadata is optional. Choose all suggestions or apply one reason while retaining other details for a later pass. The convention is `Artist--Song title.mp3`, moving a leading `The` to `, The` for the artist only (except `The The`). Review individually in an editable dialog, or explicitly approve suggestions in the table. Choose selected songs, a category, or the whole library. Renames preserve bytes, IDs, memberships, and ID3 tags.
- New arrivals persist in a review inbox. Automatic review compares possible duplicate recordings before offering per-song filename approval; disable automatic prompting from the Library menu or return later with **Review new songs**. Initial library creation does not prompt for every existing song. Reviews handle up to 20 arrivals at a time.
- Offline Chromaprint comparisons identify some re-encoded duplicates. The comparison dialog shows encoding indicators and offers keep both, replace current with incoming and delete old, or keep current and delete incoming. Replacement retains the current song ID and filename, transfers all supported ID3 frames (including artwork, ratings, and custom tags; current values win conflicts), and combines categories from both records. Unverified metadata-only matches require explicit acknowledgment.
- Renames and replacements stage original files with a database snapshot and recovery manifest before committing. Only after a successful database commit and checksum verification are obsolete copies deleted. Recovery restores interrupted uncommitted changes before scanning; committed changes are retained. **Delete previously archived duplicate copies** previews and removes verified obsolete archives left by older versions while preserving database snapshots and manifests.

## Known limits

- Independent musical pitch/key shifting is not implemented. Tempo processing and crossfade quality still need listening tests on the target Windows PC.
- MP3 only. Category-folder imports use exact encoded-audio matching; incoming recording comparisons also use local Chromaprint fingerprints. Covers, live versions, edits, very short clips, and badly labeled files can still need manual review. Incoming checks compare cached fingerprints and up to eight uncached duration/metadata candidates per song; the optional audio index improves coverage. Fingerprints compare up to the first 90 seconds and are evidence, not proof of an identical recording.
- Higher bitrate, sample rate, channels, and file size are comparison indicators, not a guarantee of source quality. No automatic replacement or deletion occurs. Approved permanent deletions cannot be recovered from the retained database snapshots.
- Renaming changes filenames without rewriting ID3 Artist/Title tags. External playlists referencing the old filenames need re-exporting. Existing destination files are never overwritten.
- On import, each selected folder becomes one category, including MP3s in its descendants. Categories are flat and independent of the MP3 genre tag. Reserved view names cannot be used as categories; rename such source folders before importing them.
- The monitor runs while the application is open. Startup scans catch files added while it was closed. Recursive subfolder changes are caught by the periodic scan.
- A quick scan checks size and modification time. Use **Library → Verify all files** to detect changes that deliberately preserve both.
- Replacing a file's actual audio at the same path clears that song's old memberships and reports it for review; editing only its tags retains them.
- Do not scan category-copy subfolders recursively as masters. The default scans only MP3s directly inside the chosen folder.
- Metadata changes are applied per file, with failures reported individually. Original ID3 snapshots live in `.jbs-mp3-manager/tag-backups`; restoration currently requires an ID3-capable tool or developer assistance.
- This is a local desktop app, not a hosted website. No music uploads, user account, or cloud service are required.

## Windows download

Open the repository's **Actions** tab, select a successful **Check and build Windows app** run for the implementation branch, and download its **JBs-MP3-Manager-0.4.7-Windows** artifact. Extract the download and open `JBs-MP3-Manager.exe`. Keep its `_internal` folder beside the EXE. Close the old application before opening the new version; there is no need to recreate your library or reimport categories.

If you already have the portable 0.3 application, the **0.4.7-UPDATE-from-0.3** artifact is a smaller alternative: close the app and merge all update files into the existing 0.3 application folder, accepting file replacements. Keep the original `_internal` folder and merge its new contents. This update cannot run on its own. The workflow verifies the update over the original 0.3 package, including startup, VLC, and the fingerprint tool.

The CI package includes the 64-bit VLC runtime, so Python and a separate VLC installation are not needed. The application is unsigned; Windows may show its normal unknown-publisher prompt. Windows 10/11, 64-bit, is the target. Automated build/decoding checks cannot replace listening and migration tests on your own computer.

## Run from source

Install Python 3.11 or later (64-bit) and [VLC 3.x, 64-bit](https://www.videolan.org/vlc/) in its normal location. In PowerShell in the repository folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe scripts\fetch_fpcalc.py
.\.venv\Scripts\python.exe -m jbs_mp3_manager
```

Without VLC, library management, tagging, and exports still work; the player displays the missing-runtime explanation. Do not use a 32-bit VLC with 64-bit Python.

Build a portable Windows folder and ZIP on Windows:

```powershell
.\.venv\Scripts\python.exe scripts\build_windows.py --vlc-dir "C:\Program Files\VideoLAN\VLC"
```

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests scripts launch.py
.\.venv\Scripts\ruff.exe format --check src tests scripts launch.py
.\.venv\Scripts\python.exe -m jbs_mp3_manager --smoke-test
```

Tests generate their own synthetic MP3s with FFmpeg (system FFmpeg or the development dependency `imageio-ffmpeg`). No personal music files are in this repository. Real VLC checks cover decoding, concurrently playing crossfade decks, and PCM level before/after EQ and reset. They skip if VLC is unavailable; CI sets `JBS_REQUIRE_VLC=1` so missing VLC fails the build. Additional playback transition tests use a controlled fake engine to simulate delayed startup. Export tests verify explicit selections, multiple-category layouts, deduplication, new folders, and recursive scan exclusion. UI tests use Qt's offscreen platform.

The new file-operation tests cover stable IDs, categories, ID3 artwork/ratings/custom frames, collision rejection, SQL rollback, interrupted rename/replacement recovery, and post-commit recovery. A generated musical signal re-encoded at two bitrates exercises the actual fingerprint tool.

The code separates MP3 inspection (`media.py`), portable library operations (`library.py`), audio playback (`playback.py`), and desktop views (`app.py`, `dialogs.py`). File operations run on a background worker so the event loop and playback timer remain responsive.

After the first independent rename batch, the library uses recovery format 2 and requires version 0.4.4 or newer. Older applications refuse to open it, preventing them from undoing successful files during interrupted-batch recovery. The pre-batch database snapshot and recovery journal remain in `.jbs-mp3-manager/file-actions`.
