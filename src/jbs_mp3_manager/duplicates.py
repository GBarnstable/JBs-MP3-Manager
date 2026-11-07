"""Review and recoverable consolidation of same-audio master files."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .library import atomic_text
from .media import digest_range, inspect_mp3

ISSUE_STATES = {"ambiguous", "unmatched", "error"}
MAX_REPORT_BYTES = 50 * 1024 * 1024


def read_report(path: Path) -> tuple[dict, str]:
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("This report is too large to review (maximum 50 MB).")
    raw = path.read_bytes()
    report = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(report, dict) or report.get("applied") is not True:
        raise ValueError("Choose a completed import report, not a preview.")
    entries = report.get("entries")
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise ValueError("This file does not contain a valid import report.")
    for entry in entries:
        if entry.get("status") not in ISSUE_STATES:
            continue
        if not isinstance(entry.get("category"), str) or not isinstance(entry.get("source"), str):
            raise ValueError("An issue is missing its category or source filename.")
        candidates = entry.get("candidates", [])
        if not isinstance(candidates, list) or any(not isinstance(p, str) for p in candidates):
            raise ValueError("An issue contains invalid candidate filenames.")
        if entry.get("audio_sha256") is not None and not isinstance(entry["audio_sha256"], str):
            raise ValueError("An issue contains an invalid audio hash.")
    return report, hashlib.sha256(raw).hexdigest()


def latest_report(library) -> Path | None:
    remembered = library.setting("last_import_report", "")
    if remembered and Path(remembered).name == remembered:
        path = library.data / "reports" / remembered
        if path.is_file():
            return path
    paths = sorted((library.data / "reports").glob("import-*.json"), reverse=True)
    return paths[0] if paths else None


def review(library, report_path=None) -> dict:
    report_path = Path(report_path).resolve() if report_path else latest_report(library)
    report, report_hash = read_report(report_path) if report_path else ({"entries": []}, "")
    resolutions = {
        row["entry_index"]: dict(row)
        for row in library.db.execute("SELECT * FROM import_resolutions WHERE report_hash=?", (report_hash,))
    }
    memberships = defaultdict(list)
    for row in library.db.execute(
        "SELECT m.song_id,c.name FROM memberships m "
        "JOIN categories c ON c.id=m.category_id ORDER BY c.name COLLATE NOCASE"
    ):
        memberships[row["song_id"]].append(row["name"])
    by_hash = defaultdict(list)
    for row in library.db.execute("SELECT * FROM songs ORDER BY path COLLATE NOCASE"):
        song = dict(row)
        song["category_names"] = memberships[song["id"]]
        by_hash[song["audio_sha256"]].append(song)
    issues, pending = [], defaultdict(list)
    for index, entry in enumerate(report["entries"]):
        if entry.get("status") not in ISSUE_STATES:
            continue
        item = {**entry, "index": index, "resolved": index in resolutions}
        if item["resolved"]:
            item["keeper_path"] = resolutions[index]["keeper_path"]
        else:
            key = entry.get("audio_sha256")
            if key:
                pending[key].append(item)
        issues.append(item)
    groups = []
    for key, songs in by_hash.items():
        available = [song for song in songs if not song["missing"]]
        if not available or (len(songs) < 2 and not pending[key]):
            continue
        categories = {name for song in songs for name in song["category_names"]}
        categories.update(entry["category"] for entry in pending[key])
        groups.append(
            {
                "key": key,
                "songs": songs,
                "pending_entries": pending[key],
                "categories": sorted(categories, key=str.casefold),
            }
        )
    groups.sort(key=lambda group: group["songs"][0]["path"].casefold())
    return {
        "report_path": str(report_path) if report_path else None,
        "report_hash": report_hash,
        "groups": groups,
        "issues": issues,
        "unresolved": sum(not item["resolved"] for item in issues),
        "resolved": sum(item["resolved"] for item in issues),
        "backup_root": str(library.data / "duplicate-backups"),
    }


def _write_manifest(batch: Path, manifest: dict):
    atomic_text(batch / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def _original_path(library, relative: str) -> Path:
    path = library.path({"path": relative})
    if path.is_relative_to(library.data) or path.is_relative_to(library.root / "Exports"):
        raise ValueError("Duplicate operations only apply to master songs.")
    raw = library.root / relative
    if raw.is_symlink() or raw.resolve() != raw.absolute():
        raise ValueError("Duplicate operations cannot move songs through symbolic links.")
    return path


def _archive_path(batch: Path, relative: str) -> Path:
    if (batch / "files").is_symlink():
        raise ValueError("The duplicate archive files folder cannot be a symbolic link.")
    path = (batch / relative).resolve()
    if not path.is_relative_to(batch.resolve() / "files") or (batch / relative).is_symlink():
        raise ValueError("Invalid duplicate archive path.")
    return path


def _rollback_moves(library, batch: Path, moves: list[dict]):
    for move in reversed(moves):
        archive = _archive_path(batch, move["archive"])
        original = _original_path(library, move["original"])
        if not archive.exists():
            if not original.exists():
                raise ValueError(f"Both the original and archived copy are missing: {move['original']}")
            continue
        if original.exists():
            raise ValueError(f"Recovery will not overwrite an existing file: {move['original']}")
        if digest_range(archive) != move["sha256"]:
            raise ValueError(f"The archived copy has changed: {move['original']}")
        original.parent.mkdir(parents=True, exist_ok=True)
        os.rename(archive, original)


def recover_pending(library) -> int:
    """Recover a killed/failed operation before the next library scan can run."""
    count = 0
    root = library.data / "duplicate-backups"
    if library.data.is_symlink() or root.is_symlink():
        raise ValueError("The duplicate backup folder cannot be a symbolic link.")
    for path in sorted(root.glob("*/manifest.json")):
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("A duplicate backup manifest cannot be a symbolic link.")
        manifest = json.loads(path.read_text("utf-8"))
        if manifest.get("state") in ("committed", "rolled_back"):
            continue
        batch = path.parent
        committed = library.db.execute("SELECT 1 FROM duplicate_batches WHERE id=?", (batch.name,)).fetchone()
        if committed:
            manifest["state"] = "committed"
        else:
            _rollback_moves(library, batch, manifest["moves"])
            manifest["state"] = "rolled_back"
            count += 1
        _write_manifest(batch, manifest)
    from .fileops import recover_actions

    recover_actions(library)
    return count


def merge(
    library, snapshot: dict, choices: dict[str, str], progress=lambda message: None, *, delete_extras=False
) -> dict:
    """Archive extras, union memberships, resolve report rows, and remove duplicate records."""
    if not choices:
        raise ValueError("Choose at least one file to keep.")
    recover_pending(library)
    current = review(library, snapshot.get("report_path"))
    if current["report_hash"] != snapshot["report_hash"]:
        raise ValueError("The import report changed. Open the review again.")
    current_groups = {group["key"]: group for group in current["groups"]}
    prior_groups = {group["key"]: group for group in snapshot["groups"]}
    selected, infos = [], {}
    claimed_paths = set()
    for key, keeper in choices.items():
        group, prior = current_groups.get(key), prior_groups.get(key)
        if (
            group is None
            or prior is None
            or {s["id"] for s in group["songs"]} != {s["id"] for s in prior["songs"]}
        ):
            raise ValueError("The duplicate group changed. Open the review again.")
        if not any(s["id"] == keeper and not s["missing"] for s in group["songs"]):
            raise ValueError("The chosen keeper is unavailable. Rescan and review again.")
        for song in group["songs"]:
            progress(f"Verifying duplicate: {song['path']}")
            path = _original_path(library, song["path"])
            if song["missing"]:
                if path.exists():
                    raise ValueError("A missing song has reappeared. Rescan and review again.")
                continue
            if path in claimed_paths:
                raise ValueError("Two database entries refer to the same physical path.")
            claimed_paths.add(path)
            info = inspect_mp3(path)
            if info["audio_sha256"] != key:
                raise ValueError(f"Audio changed since the review: {song['path']}. Rescan first.")
            infos[song["id"]] = info
        for category in group["categories"]:
            library.validate_category(category)
        selected.append((group, keeper))
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup_root = library.data / "duplicate-backups"
    if backup_root.is_symlink():
        raise ValueError("The duplicate backup folder cannot be a symbolic link.")
    batch = backup_root / batch_id
    (batch / "files").mkdir(parents=True)
    library.backup(batch / "library-before.sqlite3")
    moves, removed_ids, pending_rows = [], [], []
    manifest = {
        "batch_id": batch_id,
        "state": "prepared",
        "moves": moves,
        "groups": [],
        "report_path": current["report_path"],
        "report_hash": current["report_hash"],
    }
    for group, keeper in selected:
        manifest["groups"].append(
            {
                "key": group["key"],
                "keeper_id": keeper,
                "songs_before": group["songs"],
                "categories_after": group["categories"],
            }
        )
        for song in group["songs"]:
            if song["id"] == keeper:
                continue
            removed_ids.append(song["id"])
            if not song["missing"]:
                moves.append(
                    {
                        "song_id": song["id"],
                        "original": song["path"],
                        "archive": f"files/{len(moves) + 1:04d}.mp3",
                        "sha256": infos[song["id"]]["sha256"],
                    }
                )
        pending_rows.extend((entry, keeper) for entry in group["pending_entries"])
    _write_manifest(batch, manifest)
    committed = False
    try:
        library.db.execute("BEGIN IMMEDIATE")
        for move in moves:
            source = _original_path(library, move["original"])
            target = _archive_path(batch, move["archive"])
            if target.exists():
                raise ValueError("The backup destination already exists.")
            if digest_range(source) != move["sha256"]:
                raise ValueError("A duplicate changed during verification; open the review again.")
            progress(f"Archiving duplicate: {move['original']}")
            os.rename(source, target)
        for group, keeper in selected:
            song = library.song(keeper)
            if digest_range(_original_path(library, song["path"])) != infos[keeper]["sha256"]:
                raise ValueError("The chosen keeper changed during the merge. Review the group again.")
            library._store(keeper, song["path"], infos[keeper])
            for original in group["songs"]:
                library.db.execute(
                    "INSERT OR IGNORE INTO memberships(song_id, category_id) "
                    "SELECT ?, category_id FROM memberships WHERE song_id=?",
                    (keeper, original["id"]),
                )
            for name in group["categories"]:
                library.db.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))
                category_id = library.db.execute(
                    "SELECT id FROM categories WHERE name=? COLLATE NOCASE", (name,)
                ).fetchone()[0]
                library.db.execute("INSERT OR IGNORE INTO memberships VALUES (?, ?)", (keeper, category_id))
            for old in group["songs"]:
                if old["id"] != keeper:
                    library.db.execute(
                        "UPDATE import_resolutions SET song_id=?, keeper_path=? WHERE song_id=?",
                        (keeper, song["path"], old["id"]),
                    )
                    library.db.execute("DELETE FROM songs WHERE id=?", (old["id"],))
        for entry, keeper in pending_rows:
            library.db.execute(
                "INSERT OR REPLACE INTO import_resolutions VALUES (?, ?, ?, ?, ?)",
                (current["report_hash"], entry["index"], keeper, library.song(keeper)["path"], batch_id),
            )
        library.db.execute(
            "INSERT INTO duplicate_batches VALUES (?, ?)",
            (batch_id, json.dumps(manifest, ensure_ascii=False)),
        )
        library.db.commit()
        committed = True
    except BaseException:
        if not committed:
            library.db.rollback()
            if library.db.execute("SELECT 1 FROM duplicate_batches WHERE id=?", (batch_id,)).fetchone():
                # A signal/I/O wrapper can raise just after COMMIT succeeded. The
                # durable ledger wins; never move files back behind a committed DB.
                manifest["state"] = "committed"
                _write_manifest(batch, manifest)
                raise
            try:
                _rollback_moves(library, batch, moves)
                manifest["state"] = "rolled_back"
            except Exception as recovery_error:
                manifest["state"] = "needs_recovery"
                _write_manifest(batch, manifest)
                raise RuntimeError(
                    f"Merge stopped. Recovery needs attention in {batch}: {recovery_error}"
                ) from recovery_error
            _write_manifest(batch, manifest)
        raise
    manifest["state"] = "committed"
    # The committed database is authoritative if writing the final audit file is interrupted.
    audit_error = None
    try:
        _write_manifest(batch, manifest)
    except OSError as error:
        audit_error = str(error)
    after = review(library, current["report_path"])
    cleanup = purge_archives(library, [batch.name]) if delete_extras else {"deleted": 0, "errors": []}
    return {
        "groups_merged": len(selected),
        "files_archived": len(moves),
        "records_removed": len(removed_ids),
        "issues_resolved": len(pending_rows),
        "remaining_issues": after["unresolved"],
        "backup_folder": str(batch),
        "report_path": current["report_path"],
        "audit_error": audit_error,
        "files_deleted": cleanup["deleted"],
        "delete_errors": cleanup["errors"],
    }


def archive_inventory(library):
    result = []
    root = library.data / "duplicate-backups"
    if root.is_symlink() or library.data.is_symlink():
        raise ValueError("Duplicate archives cannot use symbolic links.")
    for path in sorted(root.glob("*/manifest.json")):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("Archive manifests cannot use symbolic links.")
        manifest = json.loads(path.read_text("utf-8"))
        if manifest.get("state") != "committed":
            continue
        files = []
        for move in manifest["moves"]:
            archive = _archive_path(path.parent, move["archive"])
            if archive.is_file():
                files.append({"original": move["original"], "size": archive.stat().st_size})
        if files:
            result.append({"id": path.parent.name, "files": files})
    return result


def purge_archives(library, batch_ids):
    deleted, errors, removed_bytes = 0, [], 0
    root = library.data / "duplicate-backups"
    if root.is_symlink() or library.data.is_symlink():
        raise ValueError("Duplicate archives cannot use symbolic links.")
    for batch_id in batch_ids:
        try:
            if Path(batch_id).name != batch_id or batch_id in (".", ".."):
                raise ValueError("Invalid archive batch")
            batch = root / batch_id
            if batch.is_symlink() or (batch / "manifest.json").is_symlink():
                raise ValueError("Archive paths cannot use symbolic links")
            manifest = json.loads((batch / "manifest.json").read_text("utf-8"))
            if (
                manifest["state"] != "committed"
                or not library.db.execute(
                    "SELECT 1 FROM duplicate_batches WHERE id=?", (batch_id,)
                ).fetchone()
            ):
                raise ValueError("Only committed merge archives can be deleted")
            for group in manifest["groups"]:
                available = library.db.execute(
                    "SELECT * FROM songs WHERE missing=0 AND audio_sha256=?", (group["key"],)
                ).fetchall()
                if not any(
                    inspect_mp3(library.path(dict(song)))["audio_sha256"] == group["key"]
                    for song in available
                    if library.path(dict(song)).is_file()
                ):
                    raise ValueError("No verified matching master remains; the archive was retained")
            for move in manifest["moves"]:
                path = _archive_path(batch, move["archive"])
                if not path.exists():
                    continue
                if digest_range(path) != move["sha256"]:
                    raise ValueError("An archived file changed; it was retained")
                size = path.stat().st_size
                path.unlink()
                deleted += 1
                removed_bytes += size
            manifest["audio_deleted"] = True
            _write_manifest(batch, manifest)
        except (OSError, ValueError, KeyError) as error:
            errors.append({"batch": batch_id, "error": str(error)})
    return {"deleted": deleted, "bytes_freed": removed_bytes, "errors": errors}
