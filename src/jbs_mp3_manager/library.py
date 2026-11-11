"""Portable SQLite library and non-destructive migration/export operations."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .category_origins import REVIEW_CATEGORY
from .category_origins import initialize as initialize_category_origins
from .exports import EXPORT_DIR
from .media import FIELDS, audio_properties, inspect_mp3, write_tags

DATA_DIR = ".jbs-mp3-manager"
RESERVED = {"all songs", "untagged", "missing files"}


def mp3_files(root: Path, recursive: bool, excluded: list[Path] | None = None) -> list[Path]:
    """Fail the entire scan on unreadable directories; never silently mark them missing."""
    excluded = [p.resolve() for p in (excluded or [])]
    result = []

    def failed(error):
        raise error

    for parent, directories, files in os.walk(root, onerror=failed, followlinks=False):
        directories[:] = sorted(
            d
            for d in directories
            if d != DATA_DIR
            and not Path(parent, d).is_symlink()
            and not any(Path(parent, d).resolve().is_relative_to(p) for p in excluded)
        )
        for name in sorted(files):
            path = Path(parent, name)
            if path.suffix.lower() == ".mp3" and not path.is_symlink():
                result.append(path)
        if not recursive:
            break
    return result


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Library:
    def __init__(self, root: str | Path, *, create: bool = False, recursive: bool = False):
        self.root = Path(root).expanduser().resolve(strict=True)
        self.data = self.root / DATA_DIR
        database = self.data / "library.sqlite3"
        if not database.exists() and not create:
            raise ValueError("No library database in this folder. Choose Create library first.")
        self.data.mkdir(exist_ok=True)
        self.db = sqlite3.connect(database, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        # DELETE journal keeps a closed library self-contained for FreeFileSync.
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self.db.close()
            raise ValueError(f"Library version {version} needs a newer application.")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS songs (
                id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT NOT NULL,
                genre TEXT NOT NULL, year TEXT NOT NULL, track TEXT NOT NULL,
                duration REAL NOT NULL, sha256 TEXT NOT NULL, audio_sha256 TEXT NOT NULL,
                size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                missing INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );
            CREATE TABLE IF NOT EXISTS memberships (
                song_id TEXT REFERENCES songs(id) ON DELETE CASCADE,
                category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                PRIMARY KEY (song_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS ai_memberships (
                song_id TEXT NOT NULL, category_id INTEGER NOT NULL,
                PRIMARY KEY (song_id, category_id),
                FOREIGN KEY (song_id, category_id) REFERENCES memberships(song_id, category_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS song_audio ON songs(audio_sha256);
            CREATE TABLE IF NOT EXISTS import_resolutions (
                report_hash TEXT NOT NULL, entry_index INTEGER NOT NULL,
                song_id TEXT NOT NULL, keeper_path TEXT NOT NULL, batch_id TEXT NOT NULL,
                PRIMARY KEY (report_hash, entry_index)
            );
            CREATE TABLE IF NOT EXISTS duplicate_batches (
                id TEXT PRIMARY KEY, manifest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_actions (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS fingerprints (
                audio_hash TEXT PRIMARY KEY, algorithm TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS new_arrivals (
                song_id TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS rename_acceptances (
                song_id TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
                path TEXT NOT NULL, audio_hash TEXT NOT NULL
            );
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(songs)")}
        for name, definition in [
            ("bitrate", "INTEGER NOT NULL DEFAULT 0"),
            ("sample_rate", "INTEGER NOT NULL DEFAULT 0"),
            ("channels", "INTEGER NOT NULL DEFAULT 0"),
            ("bitrate_mode", "TEXT NOT NULL DEFAULT ''"),
        ]:
            if name not in columns:
                self.db.execute(f"ALTER TABLE songs ADD COLUMN {name} {definition}")
        with self.db:
            self.db.execute(f"PRAGMA user_version={max(1, version)}")
            self.db.execute(
                "INSERT OR IGNORE INTO settings VALUES ('recursive', ?)", (json.dumps(recursive),)
            )
        initialize_category_origins(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()

    def setting(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))

    def path(self, song: dict) -> Path:
        target = (self.root / song["path"]).resolve()
        if not target.is_relative_to(self.root) or DATA_DIR in Path(song["path"]).parts:
            raise ValueError("Song path is outside the music folder.")
        return target

    def songs(self, category: int | str | None = None, search: str = "") -> list[dict]:
        conditions, params = [], []
        if category == "untagged":
            conditions.append("NOT EXISTS (SELECT 1 FROM memberships m WHERE m.song_id=s.id)")
        elif category == "missing":
            conditions.append("s.missing=1")
        elif isinstance(category, int):
            conditions.append("EXISTS (SELECT 1 FROM memberships m WHERE m.song_id=s.id AND m.category_id=?)")
            params.append(category)
        if category != "missing":
            conditions.append("s.missing=0")
        if search:
            conditions.append(
                "(s.title LIKE ? ESCAPE '\\' OR s.artist LIKE ? ESCAPE '\\' "
                "OR s.album LIKE ? ESCAPE '\\' OR s.path LIKE ? ESCAPE '\\')"
            )
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend([f"%{escaped}%"] * 4)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT s.*, (SELECT group_concat(name, ', ') FROM categories c JOIN memberships m "
                "ON c.id=m.category_id WHERE m.song_id=s.id) AS categories FROM songs s"
                + where
                + " ORDER BY s.artist COLLATE NOCASE, s.title COLLATE NOCASE, s.path COLLATE NOCASE",
                params,
            )
        ]

    def song(self, song_id: str) -> dict:
        row = self.db.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        if row is None:
            raise ValueError("Song no longer exists in this library.")
        return dict(row)

    def categories(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT c.*, COUNT(CASE WHEN s.missing=0 THEN 1 END) AS count FROM categories c "
                "LEFT JOIN memberships m ON m.category_id=c.id LEFT JOIN songs s ON s.id=m.song_id "
                "GROUP BY c.id ORDER BY c.name COLLATE NOCASE"
            )
        ]

    @staticmethod
    def validate_category(name: str) -> str:
        name = name.strip()
        if not name or name.casefold() in RESERVED or any(ord(c) < 32 for c in name):
            raise ValueError(
                "Use a non-empty category name other than All songs, Untagged, or Missing files."
            )
        return name

    def add_category(self, name: str) -> int:
        name = self.validate_category(name)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))
            return self.db.execute(
                "SELECT id FROM categories WHERE name=? COLLATE NOCASE", (name,)
            ).fetchone()[0]

    def rename_category(self, category_id: int, name: str):
        with self.db:
            self.db.execute(
                "UPDATE categories SET name=? WHERE id=?", (self.validate_category(name), category_id)
            )

    def delete_category(self, category_id: int):
        with self.db:
            self.db.execute("DELETE FROM categories WHERE id=?", (category_id,))

    def assign(self, song_ids: list[str], category_ids: list[int], *, remove: bool = False):
        sql = (
            "DELETE FROM memberships WHERE song_id=? AND category_id=?"
            if remove
            else "INSERT OR IGNORE INTO memberships(song_id, category_id) VALUES (?, ?)"
        )
        with self.db:
            pairs = [(s, c) for s in song_ids for c in category_ids]
            reviewed = {
                s
                for s, c in pairs
                if remove
                and self.db.execute(
                    "SELECT 1 FROM memberships m JOIN categories c ON c.id=m.category_id "
                    "WHERE m.song_id=? AND m.category_id=? AND c.name=? COLLATE NOCASE",
                    (s, c, REVIEW_CATEGORY),
                ).fetchone()
            }
            self.db.executemany(sql, pairs)
            if not remove:
                self.db.executemany("DELETE FROM ai_memberships WHERE song_id=? AND category_id=?", pairs)
            # Explicitly removing the review flag confirms the remaining tags.
            self.db.executemany("DELETE FROM ai_memberships WHERE song_id=?", [(s,) for s in reviewed])

    def _store(self, song_id: str, relative: str, info: dict):
        columns = ["id", "path", *FIELDS, "duration", "sha256", "audio_sha256", "size", "mtime_ns", "missing"]
        values = [
            song_id,
            relative,
            *[info[f] for f in FIELDS],
            info["duration"],
            info["sha256"],
            info["audio_sha256"],
            info["size"],
            info["mtime_ns"],
            0,
        ]
        for field in ("bitrate", "sample_rate", "channels", "bitrate_mode"):
            columns.append(field)
            values.append(info.get(field, "" if field == "bitrate_mode" else 0))
        update = ", ".join(f"{c}=excluded.{c}" for c in columns if c != "id")
        self.db.execute(
            f"INSERT INTO songs ({', '.join(columns)}) VALUES "
            f"({', '.join('?' for _ in columns)}) ON CONFLICT(id) DO UPDATE SET {update}",
            values,
        )

    def scan(self, progress=lambda message: None, *, verify: bool = False) -> dict:
        from .duplicates import recover_pending

        recover_pending(self)
        excluded = [p for p in self.root.iterdir() if p.name.casefold() == EXPORT_DIR.casefold()]
        files = mp3_files(self.root, self.setting("recursive", False), excluded=excluded)
        existing = {row["path"]: dict(row) for row in self.db.execute("SELECT * FROM songs")}
        monitor_new = bool(existing) or self.setting("monitor_initialized", False)
        seen = {p.relative_to(self.root).as_posix() for p in files}
        absent = [s for p, s in existing.items() if p not in seen]
        by_audio = defaultdict(list)
        for song in absent:
            by_audio[song["audio_sha256"]].append(song)
        report = {
            "added": 0,
            "updated": 0,
            "renamed": 0,
            "unchanged": 0,
            "missing": 0,
            "errors": [],
            "duplicates": [],
            "new_song_ids": [],
        }
        # Inspect before committing so a directory enumeration error cannot invalidate the library.
        updates, claimed = [], set()
        inspected = []
        for index, file in enumerate(files):
            relative = file.relative_to(self.root).as_posix()
            progress(f"Scanning {index + 1}/{len(files)}: {relative}")
            previous = existing.get(relative)
            try:
                stat = file.stat()
                if (
                    not verify
                    and previous
                    and not previous["missing"]
                    and (previous["size"], previous["mtime_ns"]) == (stat.st_size, stat.st_mtime_ns)
                ):
                    if not previous.get("bitrate"):
                        info = {**previous, **audio_properties(file)}
                        inspected.append((relative, previous, info))
                        continue
                    report["unchanged"] += 1
                    continue
                info = inspect_mp3(file)
                inspected.append((relative, previous, info))
            except Exception as error:
                report["errors"].append({"path": relative, "error": str(error)})
        new_counts = defaultdict(int)
        for relative, previous, info in inspected:
            if previous is None:
                new_counts[info["audio_sha256"]] += 1
        for relative, previous, info in inspected:
            if previous:
                song_id = previous["id"]
                # Replacing audio at the same filename must not inherit the old song's categories.
                replaced = previous["audio_sha256"] != info["audio_sha256"]
                if replaced and monitor_new:
                    report["new_song_ids"].append(song_id)
                report["updated"] += 1
            else:
                matches = [s for s in by_audio[info["audio_sha256"]] if s["id"] not in claimed]
                if len(matches) == 1 and new_counts[info["audio_sha256"]] == 1:
                    song_id = matches[0]["id"]
                    claimed.add(song_id)
                    report["renamed"] += 1
                else:
                    song_id = str(uuid.uuid4())
                    report["added"] += 1
                    if monitor_new:
                        report["new_song_ids"].append(song_id)
                replaced = False
            updates.append((song_id, relative, info, replaced))
        with self.db:
            for relative, previous in existing.items():
                if relative not in seen and previous["id"] not in claimed:
                    self.db.execute("UPDATE songs SET missing=1 WHERE id=?", (previous["id"],))
            for song_id, relative, info, replaced in updates:
                if replaced:
                    self.db.execute("DELETE FROM memberships WHERE song_id=?", (song_id,))
                    report["errors"].append(
                        {
                            "path": relative,
                            "error": "Audio was replaced; prior categories cleared for review.",
                        }
                    )
                self._store(song_id, relative, info)
            if monitor_new:
                self.db.executemany(
                    "INSERT OR IGNORE INTO new_arrivals VALUES (?)",
                    [(song_id,) for song_id in report["new_song_ids"]],
                )
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('monitor_initialized','true')")
        report["missing"] = self.db.execute("SELECT COUNT(*) FROM songs WHERE missing=1").fetchone()[0]
        groups = defaultdict(list)
        for song in self.songs():
            groups[song["audio_sha256"]].append(song["path"])
        report["duplicates"] = [paths for paths in groups.values() if len(paths) > 1]
        return report

    def preview_import(self, folders: list[str | Path], progress=lambda message: None) -> dict:
        exact, audio = defaultdict(list), defaultdict(list)
        for song in self.songs():
            exact[song["sha256"]].append(song)
            audio[song["audio_sha256"]].append(song)
        report = {"created_at": datetime.now(timezone.utc).isoformat(), "entries": [], "categories": []}
        used_names = set()
        for folder in folders:
            source = Path(folder).resolve(strict=True)
            if source.is_relative_to(self.root / EXPORT_DIR):
                raise ValueError("Exports contains output copies and cannot be imported as a category.")
            if not source.is_dir() or source == self.root or self.root.is_relative_to(source):
                raise ValueError("Select each category folder itself, not the master folder or its parent.")
            name = self.validate_category(source.name)
            if name.casefold() in used_names:
                raise ValueError(
                    f"Two source folders have the category name '{name}'. Import them separately."
                )
            used_names.add(name.casefold())
            report["categories"].append(name)
            for file in mp3_files(source, True):
                progress(f"Matching {name}: {file.name}")
                entry = {"category": name, "source": str(file), "status": "error", "song_id": None}
                try:
                    if source.is_relative_to(self.root) and self.setting("recursive", False):
                        raise ValueError(
                            "Category copies are inside a recursively scanned master. "
                            "Use a master folder containing only original songs."
                        )
                    info = inspect_mp3(file)
                    candidates = exact[info["sha256"]]
                    kind = "exact"
                    if not candidates:
                        candidates = audio[info["audio_sha256"]]
                        kind = "audio"
                    entry.update(audio_sha256=info["audio_sha256"], sha256=info["sha256"])
                    if len(candidates) == 1:
                        entry.update(status=kind, song_id=candidates[0]["id"], master=candidates[0]["path"])
                    elif candidates:
                        entry.update(status="ambiguous", candidates=[s["path"] for s in candidates])
                    else:
                        entry["status"] = "unmatched"
                except Exception as error:
                    entry["error"] = str(error)
                report["entries"].append(entry)
        return report

    def apply_import(self, report: dict, progress=lambda message: None) -> dict:
        matches = [e for e in report["entries"] if e["status"] in ("exact", "audio")]
        checked = set()
        for entry in matches:
            progress(f"Verifying {entry['category']}: {Path(entry['source']).name}")
            song = self.song(entry["song_id"])
            if song["missing"]:
                raise ValueError("Library changed since preview. Scan and preview the import again.")
            if song["id"] not in checked:
                if inspect_mp3(self.path(song))["audio_sha256"] != entry["audio_sha256"]:
                    raise ValueError("Master audio changed since preview. Scan and preview again.")
                checked.add(song["id"])
            if inspect_mp3(Path(entry["source"]))["sha256"] != entry["sha256"]:
                raise ValueError("A category copy changed since preview. Preview the import again.")
        # Category creation and all memberships commit together, including empty categories.
        with self.db:
            for name in report["categories"]:
                self.db.execute(
                    "INSERT OR IGNORE INTO categories(name) VALUES (?)", (self.validate_category(name),)
                )
            for entry in matches:
                category_id = self.db.execute(
                    "SELECT id FROM categories WHERE name=? COLLATE NOCASE", (entry["category"],)
                ).fetchone()[0]
                self.db.execute(
                    "INSERT OR IGNORE INTO memberships VALUES (?, ?)", (entry["song_id"], category_id)
                )
                self.db.execute(
                    "DELETE FROM ai_memberships WHERE song_id=? AND category_id=?",
                    (entry["song_id"], category_id),
                )
        unresolved = [e for e in report["entries"] if e["status"] not in ("exact", "audio")]
        report.update(
            applied=True,
            matched_files=len(matches),
            unresolved_files=len(unresolved),
            safe_to_remove_sources=not unresolved,
        )
        report_path = self.save_report(report, "import")
        self.set_setting("last_import_report", report_path.name)
        return {**report, "report_path": str(report_path)}

    def save_report(self, report: dict, prefix: str) -> Path:
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        path = self.data / "reports" / f"{prefix}-{name}.json"
        atomic_text(path, json.dumps(report, ensure_ascii=False, indent=2))
        if "entries" in report:
            output = io.StringIO(newline="")
            writer = csv.DictWriter(
                output,
                fieldnames=["category", "source", "status", "master", "candidates", "error"],
                extrasaction="ignore",
            )
            writer.writeheader()
            # Protect spreadsheet applications from interpreting names as formulas.
            for original in report["entries"]:
                entry = dict(original)
                if isinstance(entry.get("candidates"), list):
                    entry["candidates"] = "; ".join(entry["candidates"])
                writer.writerow(
                    {
                        k: ("'" + str(v) if str(v).startswith(("=", "+", "-", "@")) else v)
                        for k, v in entry.items()
                    }
                )
            atomic_text(path.with_suffix(".csv"), "\ufeff" + output.getvalue())
        return path

    def edit_metadata(
        self, song_ids: list[str], values: dict[str, str], progress=lambda message: None
    ) -> dict:
        result = {"updated": 0, "errors": []}
        for song_id in song_ids:
            song = self.song(song_id)
            progress(f"Updating tags: {song['path']}")
            try:
                path = self.path(song)
                write_tags(path, values, self.data / "tag-backups")
                info = inspect_mp3(path)
                with self.db:
                    self._store(song_id, song["path"], info)
                result["updated"] += 1
            except Exception as error:
                result["errors"].append({"path": song["path"], "error": str(error)})
        return result

    def export_playlist(self, song_ids: list[str], destination: str | Path, *, relative: bool = True) -> Path:
        destination = Path(destination).resolve()
        if destination.suffix.lower() not in (".m3u", ".m3u8"):
            destination = destination.with_suffix(".m3u8")
        lines = ["#EXTM3U"]
        for song_id in song_ids:
            song = self.song(song_id)
            path = self.path(song)
            if song["missing"] or not path.is_file():
                raise ValueError(f"Missing file: {song['path']}. Rescan before exporting.")
            label = f"{song['artist']} - {song['title'] or path.stem}".replace("\n", " ").replace("\r", " ")
            lines.append(f"#EXTINF:{int(song['duration'])},{label}")
            try:
                reference = os.path.relpath(path, destination.parent) if relative else str(path)
            except ValueError:  # Windows: playlist and music on different drives.
                reference = str(path)
            if "\n" in reference or "\r" in reference:
                raise ValueError("Playlist paths cannot contain newlines.")
            if reference.startswith("#"):
                reference = "." + os.sep + reference
            lines.append(reference)
        atomic_text(destination, "\n".join(lines) + "\n")
        return destination

    def export_files(
        self, song_ids: list[str], destination: str | Path, progress=lambda message: None
    ) -> dict:
        destination = Path(destination).resolve()
        if destination.is_relative_to(self.root) and not destination.is_relative_to(self.root / EXPORT_DIR):
            raise ValueError(
                "Choose a folder under the library's Exports folder, or outside the music folder."
            )
        destination.mkdir(parents=True, exist_ok=True)
        result = {"copied": [], "errors": []}
        reserved = {p.name.casefold() for p in destination.iterdir()}
        exported = []
        for song_id in dict.fromkeys(song_ids):
            song = self.song(song_id)
            source = self.path(song)
            progress(f"Exporting {source.name}")
            target, temporary, owned = None, None, False
            try:
                if song["missing"] or not source.is_file():
                    raise ValueError("File is missing. Rescan the library.")
                name, number = source.name, 2
                while name.casefold() in reserved:
                    name = f"{source.stem} ({number}){source.suffix}"
                    number += 1
                reserved.add(name.casefold())
                target = destination / name
                # Reserve the name exclusively; never overwrite another file, even in a race.
                with target.open("xb"):
                    pass
                owned = True
                with tempfile.NamedTemporaryFile(
                    dir=destination, suffix=".jbs-export-tmp", delete=False
                ) as tmp:
                    temporary = Path(tmp.name)
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
                result["copied"].append({"source": song["path"], "destination": name})
                exported.append((name, song))
            except Exception as error:
                if owned and target is not None and target.exists() and target.stat().st_size == 0:
                    target.unlink()
                result["errors"].append({"path": song["path"], "error": str(error)})
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        if exported:
            lines = ["#EXTM3U"]
            for name, song in exported:
                title = (song["title"] or Path(name).stem).replace("\n", " ").replace("\r", " ")
                lines.extend([f"#EXTINF:{int(song['duration'])},{title}", "./" + name])
            # Unique playlist/report names also leave prior exports intact.
            token = uuid.uuid4().hex[:8]
            atomic_text(destination / f"playlist-{token}.m3u8", "\n".join(lines) + "\n")
            atomic_text(
                destination / f"export-{token}.json", json.dumps(result, ensure_ascii=False, indent=2)
            )
        return result

    def export_root(self) -> Path:
        root = self.root / EXPORT_DIR
        if root.is_symlink():
            raise ValueError("The library's Exports folder cannot be a link to another folder.")
        root.mkdir(exist_ok=True)
        return root

    def backup(self, destination: str | Path) -> Path:
        destination = Path(destination).resolve()
        if destination.suffix.lower() not in (".sqlite", ".sqlite3", ".db"):
            raise ValueError("Use a .sqlite3, .sqlite, or .db backup filename.")
        if destination == self.data / "library.sqlite3":
            raise ValueError("Choose a different backup filename.")
        with closing(sqlite3.connect(destination)) as target:
            self.db.backup(target)
        return destination
