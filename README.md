# JB's MP3 Manager

A local Windows desktop application for one master MP3 folder and any number of virtual categories. A song can belong to several categories without making extra copies.

**Version 0.1 is an initial version for testing with a copy of your library before the first real migration.** It does not delete MP3s or old category folders. See [QUICK_START.md](QUICK_START.md) for the first import and the D: → C: move.

## Included

- Create/open a portable SQLite library stored in `.jbs-mp3-manager` inside the master music folder.
- Import category folders with a match preview and permanent JSON/CSV reports. Matches use SHA-256 of the complete file, then SHA-256 of the encoded MP3 audio excluding supported ID3/APE tags. Different filenames and changed ID3 tags can match. Ambiguous or absent matches are reported rather than guessed.
- Many-to-many categories; create, rename, delete, batch add, and batch remove memberships. Untagged automatically shows songs with no categories.
- Search, sortable song table, metadata inspection, and single/batch editing of title, artist, album, genre, year, and track. Original ID3 tags are backed up; extra frames and artwork are preserved. Writes use a temporary file and atomic replacement.
- Scan at startup, react to master-folder changes, and rescan every minute while running. Optional recursive master scan. Incomplete/unreadable files retry on subsequent scans. Missing songs retain their memberships. Unique same-audio renames preserve song identity.
- Built-in VLC playback with a queue derived from the visible song list, shuffle, repeat, two-deck crossfade (0–12 seconds), 10-band EQ, volume, seeking, and speed (0.5×–2×). VLC's tempo filter is enabled for speed changes.
- UTF-8 M3U8 playlists with relative paths where possible; absolute fallback across Windows drives.
- Export selected songs or the visible category/search list to another folder, with collision-safe names, an accompanying playlist, and an export manifest. Originals stay in the master folder.
- Duplicate-audio reporting, full verification scan, database backup, and protection against opening the same library twice in the desktop app.

## Known limits

- Independent musical pitch/key shifting is not implemented. Tempo processing and crossfade quality still need listening tests on the target Windows PC.
- MP3 only. Category matching does not use acoustic fingerprints: re-encoded files, different edits, or different recordings of the same song require review. No automatic deletion or merging of duplicate masters.
- On import, each selected folder becomes one category, including MP3s in its descendants. Categories are flat and independent of the MP3 genre tag. Reserved view names cannot be used as categories; rename such source folders before importing them.
- The monitor runs while the application is open. Startup scans catch files added while it was closed. Recursive subfolder changes are caught by the periodic scan.
- A quick scan checks size and modification time. Use **Library → Verify all files** to detect changes that deliberately preserve both.
- Replacing a file's actual audio at the same path clears that song's old memberships and reports it for review; editing only its tags retains them.
- Do not scan category-copy subfolders recursively as masters. The default scans only MP3s directly inside the chosen folder.
- Metadata changes are applied per file, with failures reported individually. Original ID3 snapshots live in `.jbs-mp3-manager/tag-backups`; restoration currently requires an ID3-capable tool or developer assistance.
- This is a local desktop app, not a hosted website. No music uploads, user account, or cloud service are required.

## Windows download

Open the repository's **Actions** tab, select a successful **Check and build Windows app** run for the implementation branch, and download its **JBs-MP3-Manager-Windows** artifact. Extract the artifact, then extract the inner ZIP. Open `JBs-MP3-Manager.exe` inside the extracted application folder. Keep its `_internal` folder beside the EXE.

The CI package includes the 64-bit VLC runtime, so Python and a separate VLC installation are not needed. The application is unsigned; Windows may show its normal unknown-publisher prompt. Windows 10/11, 64-bit, is the target. Automated build/decoding checks cannot replace listening and migration tests on your own computer.

## Run from source

Install Python 3.11 or later (64-bit) and [VLC 3.x, 64-bit](https://www.videolan.org/vlc/) in its normal location. In PowerShell in the repository folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
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

Tests generate their own synthetic MP3s with FFmpeg (system FFmpeg or the development dependency `imageio-ffmpeg`). No personal music files are in this repository. The real VLC decoding test skips if VLC is unavailable; CI sets `JBS_REQUIRE_VLC=1` so missing VLC fails the build. Playback transition tests use a controlled fake audio engine. UI tests use Qt's offscreen platform.

The code separates MP3 inspection (`media.py`), portable library operations (`library.py`), audio playback (`playback.py`), and desktop views (`app.py`, `dialogs.py`). File operations run on a background worker so the event loop and playback timer remain responsive.
