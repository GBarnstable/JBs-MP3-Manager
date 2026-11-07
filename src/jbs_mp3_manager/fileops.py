"""Journaled master-file changes with stable song IDs and crash recovery."""

import copy
import json
import os
import shutil
import uuid
from datetime import datetime, timezone

from mutagen.id3 import ID3, ID3NoHeaderError

from .duplicates import _archive_path, _original_path
from .library import atomic_text
from .media import digest_range, inspect_mp3


def _save(batch, manifest):
    atomic_text(batch / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def _restore(library, batch, manifest):
    for installed in manifest.get("installed", []):
        path = _original_path(library, installed["path"])
        if path.exists():
            if digest_range(path) != installed["sha256"]:
                original = next((m for m in manifest["moves"] if m["original"] == installed["path"]), None)
                if original and not _archive_path(batch, original["archive"]).exists():
                    continue  # The operation failed before moving the original.
                raise ValueError("Recovery will not remove a file that changed after interruption.")
            path.unlink()
    for move in reversed(manifest["moves"]):
        original = _original_path(library, move["original"])
        stage = _archive_path(batch, move["archive"])
        target = _original_path(library, move["target"]) if move.get("target") else None
        if not stage.exists() and original.exists():
            if digest_range(original) != move["sha256"]:
                raise ValueError("An original file changed after interruption.")
            continue  # Not moved yet, or already restored during an earlier recovery.
        if not stage.exists() and target and target.exists():
            if digest_range(target) != move["sha256"]:
                raise ValueError("Recovery will not overwrite changed rename destinations.")
            if original.exists() and original != target:
                raise ValueError("Recovery will not overwrite an existing original filename.")
            os.rename(target, stage)
        if stage.exists():
            if original.exists():
                raise ValueError("Recovery found an occupied original filename.")
            if digest_range(stage) != move["sha256"]:
                raise ValueError("An archived source changed after interruption.")
            os.rename(stage, original)
        elif not original.exists():
            raise ValueError("A source and its recovery copy are both missing.")


def recover_actions(library):
    root = library.data / "file-actions"
    if root.is_symlink() or library.data.is_symlink():
        raise ValueError("File-action backups cannot use symbolic links.")
    for path in sorted(root.glob("*/manifest.json")):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("File-action journals cannot use symbolic links.")
        manifest = json.loads(path.read_text("utf-8"))
        if manifest["state"] in ("committed", "rolled_back"):
            continue
        if library.db.execute("SELECT 1 FROM file_actions WHERE id=?", (path.parent.name,)).fetchone():
            manifest["state"] = "committed"
        else:
            _restore(library, path.parent, manifest)
            manifest["state"] = "rolled_back"
        _save(path.parent, manifest)


def _batch(library, kind, sources):
    recover_actions(library)
    for song, expected in sources:
        if digest_range(_original_path(library, song["path"])) != expected:
            raise ValueError(f"File changed since review: {song['path']}")
    root = library.data / "file-actions"
    if root.is_symlink() or library.data.is_symlink():
        raise ValueError("File-action backups cannot use symbolic links.")
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    batch = root / token
    (batch / "files").mkdir(parents=True)
    library.backup(batch / "library-before.sqlite3")
    manifest = {"state": "prepared", "kind": kind, "moves": [], "installed": [], "kept": {}}
    for song, expected in sources:
        manifest["moves"].append(
            {
                "original": song["path"],
                "sha256": expected,
                "archive": f"files/{len(manifest['moves']) + 1:04d}.mp3",
                "target": None,
            }
        )
    return batch, manifest


def purge_staged(library, batch, manifest):
    """Delete only verified obsolete staged files after a durable successful commit."""
    errors, deleted = [], 0
    if not library.db.execute("SELECT 1 FROM file_actions WHERE id=?", (batch.name,)).fetchone():
        raise ValueError("Cannot delete recovery audio before its database commit.")
    for relative, expected in manifest.get("kept", {}).items():
        path = _original_path(library, relative)
        if not path.is_file() or digest_range(path) != expected:
            raise ValueError("Kept audio changed or disappeared; obsolete copies were retained.")
    for move in manifest["moves"]:
        path = _archive_path(batch, move["archive"])
        if move.get("target") or not path.exists():
            continue
        try:
            if digest_range(path) != move["sha256"]:
                raise ValueError("Archived copy has changed")
            path.unlink()
            deleted += 1
        except OSError as error:
            errors.append(str(error))
        except ValueError as error:
            errors.append(str(error))
    manifest["purge_errors"] = errors
    try:
        _save(batch, manifest)
    except OSError as error:
        errors.append(str(error))
    return {"deleted": deleted, "errors": errors}


def _execute(library, batch, manifest, mutate, progress, *, install=None, delete_staged=False):
    _save(batch, manifest)
    try:
        library.db.execute("BEGIN IMMEDIATE")
        for move in manifest["moves"]:
            path = _original_path(library, move["original"])
            if digest_range(path) != move["sha256"]:
                raise ValueError("A source changed during the operation; review again.")
            progress(f"Updating {move['original']}")
            os.rename(path, _archive_path(batch, move["archive"]))
        for move in manifest["moves"]:
            if move.get("target"):
                target = _original_path(library, move["target"])
                if target.exists():
                    raise ValueError("A rename destination became occupied.")
                os.rename(_archive_path(batch, move["archive"]), target)
        if install:
            target = _original_path(library, manifest["installed"][0]["path"])
            if target.exists():
                raise ValueError("The replacement destination became occupied.")
            os.rename(install, target)
        mutate()
        library.db.execute("INSERT INTO file_actions VALUES (?)", (batch.name,))
        library.db.commit()
    except BaseException:
        library.db.rollback()
        if library.db.execute("SELECT 1 FROM file_actions WHERE id=?", (batch.name,)).fetchone():
            manifest["state"] = "committed"
        else:
            try:
                _restore(library, batch, manifest)
                manifest["state"] = "rolled_back"
            except Exception:
                manifest["state"] = "needs_recovery"
                _save(batch, manifest)
                raise
        _save(batch, manifest)
        raise
    manifest["state"] = "committed"
    audit_errors = []
    try:
        _save(batch, manifest)
    except OSError as error:
        audit_errors.append(f"Files updated; final journal write failed: {error}")
    cleanup = purge_staged(library, batch, manifest) if delete_staged else {"deleted": 0, "errors": []}
    cleanup["errors"].extend(audit_errors)
    return {"backup_folder": str(batch), **cleanup}


def replace_recording(
    library,
    current_id,
    incoming_id,
    expected_current,
    expected_incoming,
    progress=lambda message: None,
    *,
    delete_old=True,
):
    if current_id == incoming_id:
        raise ValueError("Choose two different songs.")
    current, incoming = library.song(current_id), library.song(incoming_id)
    if current["missing"] or incoming["missing"]:
        raise ValueError("Both recordings must be available.")
    batch, manifest = _batch(library, "replace", [(current, expected_current), (incoming, expected_incoming)])
    prepared = batch / "replacement.mp3"
    shutil.copy2(_original_path(library, incoming["path"]), prepared)
    try:
        old_tags = ID3(_original_path(library, current["path"]))
    except ID3NoHeaderError:
        old_tags = ID3()
    try:
        tags = ID3(prepared)
    except ID3NoHeaderError:
        tags = ID3()
    if old_tags.getall("APIC"):
        tags.delall("APIC")  # Keep the current artwork as the chosen cover, including its description.
    for key, frame in old_tags.items():
        tags[key] = copy.deepcopy(frame)
    tags.unknown_frames = list(dict.fromkeys(tags.unknown_frames + old_tags.unknown_frames))
    tags.save(prepared, v2_version=4)
    info = inspect_mp3(prepared)
    if info["audio_sha256"] != inspect_mp3(_original_path(library, incoming["path"]))["audio_sha256"]:
        raise ValueError("The incoming audio changed while preparing its metadata.")
    manifest["installed"] = [{"path": current["path"], "sha256": info["sha256"]}]
    manifest["kept"] = {current["path"]: info["sha256"]}

    def mutate():
        library._store(current_id, current["path"], info)
        library.db.execute(
            "INSERT OR IGNORE INTO memberships SELECT ?, category_id FROM memberships WHERE song_id=?",
            (current_id, incoming_id),
        )
        library.db.execute(
            "UPDATE import_resolutions SET song_id=?, keeper_path=? WHERE song_id=?",
            (current_id, current["path"], incoming_id),
        )
        library.db.execute("DELETE FROM songs WHERE id=?", (incoming_id,))
        library.db.execute("DELETE FROM new_arrivals WHERE song_id=?", (current_id,))

    result = _execute(library, batch, manifest, mutate, progress, install=prepared, delete_staged=delete_old)
    return {**result, "keeper_id": current_id, "path": current["path"]}


def delete_incoming(
    library, incoming_id, keeper_id, expected_incoming, expected_keeper, progress=lambda message: None
):
    incoming, keeper = library.song(incoming_id), library.song(keeper_id)
    if incoming_id == keeper_id or keeper["missing"]:
        raise ValueError("Choose an available different keeper.")
    if digest_range(_original_path(library, keeper["path"])) != expected_keeper:
        raise ValueError("The kept recording changed since review.")
    batch, manifest = _batch(library, "discard duplicate", [(incoming, expected_incoming)])
    manifest["kept"] = {keeper["path"]: expected_keeper}

    def mutate():
        if digest_range(_original_path(library, keeper["path"])) != expected_keeper:
            raise ValueError("The kept recording changed during review; the incoming file was retained.")
        library.db.execute(
            "INSERT OR IGNORE INTO memberships SELECT ?, category_id FROM memberships WHERE song_id=?",
            (keeper_id, incoming_id),
        )
        library.db.execute(
            "UPDATE import_resolutions SET song_id=?, keeper_path=? WHERE song_id=?",
            (keeper_id, keeper["path"], incoming_id),
        )
        library.db.execute("DELETE FROM songs WHERE id=?", (incoming_id,))

    return _execute(library, batch, manifest, mutate, progress, delete_staged=True)
