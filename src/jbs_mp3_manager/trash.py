"""Recycle selected Untagged master files without a permanent-delete fallback."""

import json
import uuid

from PySide6.QtCore import QFile

from .exports import EXPORT_DIR
from .library import atomic_text
from .media import digest_range


def recycle_file(path):
    file = QFile(str(path))
    if not file.moveToTrash():
        raise OSError(f"Could not move to Recycle Bin: {file.errorString()}")


def trash_untagged(library, approved, progress=lambda message: None):
    result = {"trashed": 0, "files": [], "errors": []}
    if not approved:
        return result
    token = uuid.uuid4().hex
    backups = library.data / "trash-backups"
    if backups.is_symlink() or library.data.is_symlink():
        raise ValueError("File deletion backups cannot use symbolic links.")
    backups.mkdir(exist_ok=True)
    result["database_backup"] = str(library.backup(backups / f"{token}.sqlite3"))
    report = library.data / "reports" / f"trash-{token}.json"
    result["report_path"] = str(report)
    result["planned"] = [{"id": s["id"], "path": s["path"]} for s in approved]
    atomic_text(report, json.dumps(result, ensure_ascii=False, indent=2))
    seen = set()
    for entry in approved:
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        moved = False
        try:
            song = library.song(entry["id"])
            progress(f"Moving to Recycle Bin: {song['path']}")
            if library.db.execute("SELECT 1 FROM memberships WHERE song_id=?", (song["id"],)).fetchone():
                raise ValueError("Song now has a category; only Untagged songs can be deleted here.")
            path = library.path(song)
            raw = library.root / song["path"]
            if raw.is_symlink() or raw.absolute() != path or path.is_relative_to(library.root / EXPORT_DIR):
                raise ValueError("Only master files without symbolic links can be deleted.")
            if song["path"] != entry["path"] or digest_range(path) != entry["sha256"]:
                raise ValueError("File changed since confirmation. Rescan and select it again.")
            recycle_file(path)
            moved = True
            result["trashed"] += 1
            result["files"].append(song["path"])
            with library.db:
                library.db.execute("DELETE FROM import_resolutions WHERE song_id=?", (song["id"],))
                library.db.execute("DELETE FROM songs WHERE id=?", (song["id"],))
        except Exception as error:
            message = str(error)
            if moved:
                message = "File moved to Recycle Bin, but library update failed. Rescan. " + message
            result["errors"].append({"path": entry["path"], "error": message})
    try:
        atomic_text(report, json.dumps(result, ensure_ascii=False, indent=2))
    except OSError as error:
        result["errors"].append({"path": str(report), "error": f"Could not save final report: {error}"})
    return result
