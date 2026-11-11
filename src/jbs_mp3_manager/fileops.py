"""Journaled master-file changes with stable song IDs and crash recovery."""

import copy
import json
import os
import shutil
import uuid
from datetime import datetime, timezone

from mutagen.id3 import ID3, ID3NoHeaderError

from .category_origins import merge_memberships
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
        _original_path(library, move["original"])
        original = library.root / move["original"]
        stage = _archive_path(batch, move["archive"])
        if move.get("target"):
            _original_path(library, move["target"])
            target = library.root / move["target"]
        else:
            target = None
        if not stage.exists() and original.exists():
            if digest_range(original) != move["sha256"]:
                raise ValueError("An original file changed after interruption.")
            if (
                target
                and os.name == "nt"
                and os.path.normcase(str(original)) == os.path.normcase(str(target))
            ):
                os.rename(target, stage)
                os.rename(stage, original)
            continue  # Not moved yet, or already restored during an earlier recovery.
        if not stage.exists() and target and target.exists():
            if digest_range(target) != move["sha256"]:
                raise ValueError("Recovery will not overwrite changed rename destinations.")
            if original.exists() and original != target:
                raise ValueError("Recovery will not overwrite an existing original filename.")
            os.rename(target, stage)
        if stage.exists():
            if target and target.exists() and os.path.samefile(stage, target):
                if digest_range(stage) != move["sha256"]:
                    raise ValueError("An interrupted rename changed after linking its destination.")
                target.unlink()
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
        if manifest["kind"] == "rename-independent":
            active = path.parent / "active.json"
            if active.is_symlink():
                raise ValueError("A rename intent cannot be a symbolic link.")
            if active.exists():
                index = json.loads(active.read_text("utf-8"))["index"]
                move = manifest["moves"][index]
                if not library.db.execute(
                    "SELECT 1 FROM file_actions WHERE id=?", (move["action_id"],)
                ).fetchone():
                    _restore(library, path.parent, {"moves": [move], "installed": []})
                active.unlink()
            manifest["state"] = "committed"
            manifest["interrupted"] = True
            _save(path.parent, manifest)
            continue
        if library.db.execute("SELECT 1 FROM file_actions WHERE id=?", (path.parent.name,)).fetchone():
            manifest["state"] = "committed"
        else:
            _restore(library, path.parent, manifest)
            manifest["state"] = "rolled_back"
        _save(path.parent, manifest)


def _batch(library, kind, sources, *, verify_sources=True):
    recover_actions(library)
    if verify_sources:
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


def rename_independently(library, plans, progress=lambda message: None):
    """One snapshot, with an independent durable transaction for each approved file."""
    batch, manifest = _batch(
        library, "rename-independent", [(song, sha) for song, _, sha in plans], verify_sources=False
    )
    for index, (move, (song, target, _)) in enumerate(zip(manifest["moves"], plans)):
        move.update(target=target, song_id=song["id"], action_id=f"{batch.name}:{index}")
    # Older applications restore an entire batch and cannot recover independent commits.
    # Keep them from opening a library that contains these journals.
    with library.db:
        library.db.execute("PRAGMA user_version=2")
    _save(batch, manifest)
    active = batch / "active.json"
    result = {"renamed": 0, "errors": [], "warnings": [], "backup_folder": str(batch)}
    for index, move in enumerate(manifest["moves"]):
        intent = False
        recovered = True
        try:
            progress(f"Renaming {index + 1}/{len(plans)}: {move['original']}")
            library.db.execute("BEGIN IMMEDIATE")
            source = _original_path(library, move["original"])
            stage = _archive_path(batch, move["archive"])
            _original_path(library, move["target"])
            target = library.root / move["target"]  # Preserve requested capitalization on Windows.
            if digest_range(source) != move["sha256"]:
                raise ValueError("File changed since review; rescan and review it again.")
            atomic_text(active, json.dumps({"index": index}))
            intent = True
            os.rename(source, stage)
            if os.name == "nt":
                os.rename(stage, target)  # Windows rename refuses to overwrite an occupied destination.
            else:
                os.link(stage, target, follow_symlinks=False)  # Atomically refuse overwrites on POSIX too.
                stage.unlink()
            library.db.execute("UPDATE songs SET path=? WHERE id=?", (move["target"], move["song_id"]))
            library.db.execute(
                "UPDATE import_resolutions SET keeper_path=? WHERE song_id=?",
                (move["target"], move["song_id"]),
            )
            library.db.execute("DELETE FROM new_arrivals WHERE song_id=?", (move["song_id"],))
            library.db.execute("INSERT INTO file_actions VALUES (?)", (move["action_id"],))
            library.db.commit()
        except BaseException as error:
            library.db.rollback()
            if library.db.execute("SELECT 1 FROM file_actions WHERE id=?", (move["action_id"],)).fetchone():
                result["renamed"] += 1
                result["warnings"].append(f"Rename committed for {move['original']}: {error}")
            else:
                if intent:
                    try:
                        _restore(library, batch, {"moves": [move], "installed": []})
                    except Exception as recovery_error:
                        recovered = False
                        result["warnings"].append(f"Recovery needs attention in {batch}: {recovery_error}")
                result["errors"].append(
                    {"path": move["original"], "target": move["target"], "error": str(error)}
                )
            if not isinstance(error, Exception):
                raise
        else:
            result["renamed"] += 1
        if not recovered:
            manifest["state"] = "needs_recovery"
            for remaining in manifest["moves"][index + 1 :]:
                result["errors"].append(
                    {
                        "path": remaining["original"],
                        "target": remaining["target"],
                        "error": "Not attempted because an interrupted file move needs recovery.",
                    }
                )
            break
        if intent:
            try:
                active.unlink(missing_ok=True)
            except OSError as error:
                result["warnings"].append(f"Could not remove completed rename intent: {error}")
    else:
        manifest["state"] = "committed"
    manifest["result"] = result
    try:
        _save(batch, manifest)
    except OSError as error:
        result["warnings"].append(f"Final rename journal write failed: {error}")
    return result


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
        merge_memberships(library, current_id, [incoming_id])
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
        merge_memberships(library, keeper_id, [incoming_id])
        library.db.execute(
            "UPDATE import_resolutions SET song_id=?, keeper_path=? WHERE song_id=?",
            (keeper_id, keeper["path"], incoming_id),
        )
        library.db.execute("DELETE FROM songs WHERE id=?", (incoming_id,))

    return _execute(library, batch, manifest, mutate, progress, delete_staged=True)
