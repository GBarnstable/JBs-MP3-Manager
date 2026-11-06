import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from jbs_mp3_manager.duplicates import merge, read_report, recover_pending, review
from jbs_mp3_manager.library import Library
from jbs_mp3_manager.media import digest_range, inspect_mp3
from jbs_mp3_manager.review_dialog import ReviewDialog


def duplicate(library, song=None, name="Duplicate.mp3"):
    song = song or library.songs()[0]
    shutil.copy2(library.path(song), library.root / name)
    library.scan()
    return next(s for s in library.songs() if s["path"] == name)


@pytest.fixture
def unresolved(library, tmp_path):
    original = library.songs()[0]
    extra = duplicate(library, original)
    for song, name in ((original, "Original category"), (extra, "Extra category")):
        library.assign([song["id"]], [library.add_category(name)])
    folders = []
    for name in ("Dance", "Party"):
        folder = tmp_path / name
        folder.mkdir()
        shutil.copy2(library.path(original), folder / "copy.mp3")
        folders.append(folder)
    report = library.apply_import(library.preview_import(folders))
    assert report["unresolved_files"] == 2
    # The original category copies are no longer required to resolve saved report entries.
    for folder in folders:
        shutil.rmtree(folder)
    return library, original, extra, Path(report["report_path"])


@pytest.mark.parametrize("keep_extra", [False, True])
def test_merge_archives_extra_unions_categories_and_resolves_saved_import(unresolved, keep_extra):
    library, original, extra, report_path = unresolved
    snapshot = review(library)
    assert snapshot["unresolved"] == 2
    assert len(snapshot["groups"]) == 1
    keeper = extra if keep_extra else original
    discarded = original if keep_extra else extra
    keeper_bytes = library.path(keeper).read_bytes()
    extra_bytes = library.path(discarded).read_bytes()
    result = merge(library, snapshot, {snapshot["groups"][0]["key"]: keeper["id"]})
    assert result["files_archived"] == 1
    assert result["issues_resolved"] == 2
    assert result["remaining_issues"] == 0
    assert len(library.songs()) == 3
    assert library.songs("missing") == []
    assert library.path(keeper).read_bytes() == keeper_bytes
    assert not library.path(discarded).exists()
    memberships = {
        row[0]
        for row in library.db.execute(
            "SELECT c.name FROM categories c JOIN memberships m ON c.id=m.category_id WHERE m.song_id=?",
            (keeper["id"],),
        )
    }
    assert memberships == {"Original category", "Extra category", "Dance", "Party"}
    batch = Path(result["backup_folder"])
    assert (batch / "files" / "0001.mp3").read_bytes() == extra_bytes
    manifest = json.loads((batch / "manifest.json").read_text("utf-8"))
    assert manifest["state"] == "committed"
    assert manifest["moves"][0]["original"] == discarded["path"]
    with sqlite3.connect(batch / "library-before.sqlite3") as backup:
        assert backup.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 4
    after = review(library, report_path)
    assert after["unresolved"] == 0 and after["resolved"] == 2
    assert not after["groups"]
    assert library.scan(verify=True)["added"] == 0
    assert len(library.songs()) == 3


def test_missing_duplicate_record_is_removed_without_moving_a_file(unresolved):
    library, original, extra, _ = unresolved
    library.path(extra).unlink()
    library.scan()
    snapshot = review(library)
    result = merge(library, snapshot, {snapshot["groups"][0]["key"]: original["id"]})
    assert result["files_archived"] == 0
    assert result["records_removed"] == 1
    assert result["issues_resolved"] == 2
    assert not library.songs("missing")


def test_partial_merge_leaves_other_duplicate_groups_alone(library):
    songs = library.songs()
    duplicate(library, songs[0], "A copy.mp3")
    duplicate(library, songs[1], "B copy.mp3")
    snapshot = review(library)
    chosen = next(g for g in snapshot["groups"] if any(s["id"] == songs[0]["id"] for s in g["songs"]))
    result = merge(library, snapshot, {chosen["key"]: songs[0]["id"]})
    assert result["groups_merged"] == 1
    assert len(review(library)["groups"]) == 1
    assert (library.root / "B copy.mp3").is_file()


def test_changed_audio_is_rejected_before_any_moves(unresolved, tones):
    library, original, extra, _ = unresolved
    snapshot = review(library)
    shutil.copyfile(tones[2], library.path(extra))
    with pytest.raises(ValueError, match="Audio changed"):
        merge(library, snapshot, {snapshot["groups"][0]["key"]: original["id"]})
    assert library.path(original).is_file()
    assert library.path(extra).is_file()
    assert not (library.data / "duplicate-backups").exists()


def test_changed_report_is_rejected(unresolved):
    library, original, extra, path = unresolved
    snapshot = review(library)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="report changed"):
        merge(library, snapshot, {snapshot["groups"][0]["key"]: original["id"]})
    assert library.path(extra).is_file()


def test_filesystem_failure_rolls_back_files_and_database(library, monkeypatch):
    original = library.songs()[0]
    duplicate(library, original, "Copy one.mp3")
    duplicate(library, original, "Copy two.mp3")
    snapshot = review(library)
    before = {s["path"]: library.path(s).read_bytes() for s in library.songs()}
    real_rename = os.rename
    calls = []

    def failing_rename(source, target):
        calls.append((source, target))
        if len(calls) == 2:
            raise PermissionError("Simulated locked file")
        return real_rename(source, target)

    monkeypatch.setattr("jbs_mp3_manager.duplicates.os.rename", failing_rename)
    with pytest.raises(PermissionError):
        merge(library, snapshot, {snapshot["groups"][0]["key"]: original["id"]})
    assert {s["path"]: library.path(s).read_bytes() for s in library.songs()} == before
    assert library.db.execute("SELECT COUNT(*) FROM duplicate_batches").fetchone()[0] == 0
    manifest = next((library.data / "duplicate-backups").glob("*/manifest.json"))
    assert json.loads(manifest.read_text("utf-8"))["state"] == "rolled_back"


def test_database_failure_restores_archived_files_and_memberships(unresolved):
    library, original, extra, _ = unresolved
    snapshot = review(library)
    before = library.path(extra).read_bytes()
    library.db.executescript(
        "CREATE TRIGGER reject_delete BEFORE DELETE ON songs BEGIN "
        "SELECT RAISE(ABORT, 'Simulated failure'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError):
        merge(library, snapshot, {snapshot["groups"][0]["key"]: original["id"]})
    assert library.path(extra).read_bytes() == before
    assert review(library)["unresolved"] == 2
    assert library.db.execute("SELECT COUNT(*) FROM import_resolutions").fetchone()[0] == 0


def test_startup_recovery_restores_an_uncommitted_move(library):
    song = library.songs()[0]
    source = library.path(song)
    before = source.read_bytes()
    batch = library.data / "duplicate-backups" / "interrupted"
    (batch / "files").mkdir(parents=True)
    move = {"original": song["path"], "archive": "files/0001.mp3", "sha256": digest_range(source)}
    (batch / "manifest.json").write_text(json.dumps({"state": "prepared", "moves": [move]}))
    source.rename(batch / "files" / "0001.mp3")
    assert recover_pending(library) == 1
    assert source.read_bytes() == before
    assert json.loads((batch / "manifest.json").read_text())["state"] == "rolled_back"


def test_recovery_does_not_undo_a_committed_merge(unresolved):
    library, original, extra, _ = unresolved
    snapshot = review(library)
    result = merge(library, snapshot, {snapshot["groups"][0]["key"]: original["id"]})
    path = Path(result["backup_folder"]) / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest["state"] = "prepared"  # Simulate termination just after the SQLite commit.
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert recover_pending(library) == 0
    assert not library.path(extra).exists()
    assert json.loads(path.read_text("utf-8"))["state"] == "committed"


def test_recovery_never_overwrites_a_new_original(library):
    song = library.songs()[0]
    source = library.path(song)
    batch = library.data / "duplicate-backups" / "interrupted"
    (batch / "files").mkdir(parents=True)
    move = {"original": song["path"], "archive": "files/0001.mp3", "sha256": digest_range(source)}
    shutil.copy2(source, batch / "files" / "0001.mp3")
    (batch / "manifest.json").write_text(json.dumps({"state": "prepared", "moves": [move]}))
    source.write_bytes(b"a new file at the original name")
    with pytest.raises(ValueError, match="will not overwrite"):
        recover_pending(library)
    assert source.read_bytes() == b"a new file at the original name"
    assert (batch / "files" / "0001.mp3").is_file()


def test_exception_after_commit_does_not_restore_files_behind_committed_db(unresolved):
    library, original, extra, _ = unresolved
    snapshot = review(library)
    real_db = library.db

    class CommitThenRaise:
        def __getattr__(self, name):
            return getattr(real_db, name)

        def commit(self):
            real_db.commit()
            raise OSError("Simulated interruption just after commit")

    library.db = CommitThenRaise()
    try:
        with pytest.raises(OSError):
            merge(library, snapshot, {snapshot["groups"][0]["key"]: original["id"]})
    finally:
        library.db = real_db
    assert len(library.songs()) == 3
    assert not library.path(extra).exists()
    assert review(library)["unresolved"] == 0
    assert recover_pending(library) == 0


def test_report_parser_rejects_a_preview(tmp_path):
    path = tmp_path / "preview.json"
    path.write_text(json.dumps({"entries": [], "applied": False}))
    with pytest.raises(ValueError, match="completed import"):
        read_report(path)


def test_csv_reports_include_competing_filenames(unresolved):
    _, _, _, path = unresolved
    text = path.with_suffix(".csv").read_text("utf-8-sig")
    assert "candidates" in text.splitlines()[0]
    assert "Duplicate.mp3" in text


def test_review_dialog_requires_explicit_keeper_and_shows_report_issues(qapp, unresolved):
    library, _, _, _ = unresolved
    snapshot = review(library)
    dialog = ReviewDialog(snapshot)
    dialog.show()
    qapp.processEvents()
    assert not dialog.choices()
    selector = dialog.selectors[snapshot["groups"][0]["key"]]
    selector.setCurrentIndex(1)
    assert len(dialog.choices()) == 1
    assert "1 files will be archived" in dialog.plan.text()
    assert "Dance" in dialog.details.toPlainText()
    dialog.close()


def test_twelve_pairs_and_43_report_rows_resolve_to_37_memberships(tmp_path):
    root = tmp_path / "Music"
    root.mkdir()
    binary = shutil.which("ffmpeg")
    if not binary:
        import imageio_ffmpeg

        binary = imageio_ffmpeg.get_ffmpeg_exe()
    for index in range(12):
        path = root / f"Song {index:02}.mp3"
        subprocess.run(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={440 + index * 50}:duration=0.3",
                "-c:a",
                "libmp3lame",
                str(path),
            ],
            check=True,
        )
        shutil.copy2(path, root / f"Song {index:02} alternate.mp3")
    with Library(root, create=True) as library:
        library.scan()
        entries = []
        sizes = [4, 2, 4, 4, 3, 5, 4, 2, 2, 2, 3, 2]
        copies = [4, 2, 7, 7, 3, 5, 4, 2, 2, 2, 3, 2]
        for index, (size, count) in enumerate(zip(sizes, copies)):
            info = inspect_mp3(root / f"Song {index:02}.mp3")
            for occurrence in range(count):
                category = f"Category {occurrence % size}"
                entries.append(
                    {
                        "category": category,
                        "source": f"D:\\Old categories\\{category}\\{index}-{occurrence}.mp3",
                        "status": "ambiguous",
                        "audio_sha256": info["audio_sha256"],
                        "candidates": [f"Song {index:02}.mp3", f"Song {index:02} alternate.mp3"],
                    }
                )
        path = library.save_report({"applied": True, "entries": entries}, "import")
        snapshot = review(library, path)
        assert snapshot["unresolved"] == 43
        assert len(snapshot["groups"]) == 12
        choices = {
            g["key"]: next(s["id"] for s in g["songs"] if "alternate" not in s["path"])
            for g in snapshot["groups"]
        }
        result = merge(library, snapshot, choices)
        assert result["issues_resolved"] == 43
        assert result["files_archived"] == 12
        assert result["remaining_issues"] == 0
        assert len(library.songs()) == 12
        assert not library.songs("missing")
        assert library.db.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 37
