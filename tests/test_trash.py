import json
import os
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QTimer
from PySide6.QtWidgets import QMessageBox

from jbs_mp3_manager import trash as trash_module
from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.trash import recycle_file, trash_untagged


@pytest.fixture
def recycle_bin(tmp_path, monkeypatch):
    folder = tmp_path / "Recycle Bin"
    folder.mkdir()
    monkeypatch.setattr(
        "jbs_mp3_manager.trash.recycle_file", lambda path: shutil.move(path, folder / path.name)
    )
    return folder


def test_trash_removes_actual_selected_file_and_database_rows(library, recycle_bin):
    song = library.songs()[0]
    library.db.execute(
        "INSERT INTO rename_acceptances VALUES (?, ?, ?)", (song["id"], song["path"], song["audio_sha256"])
    )
    library.db.commit()
    result = trash_untagged(library, [song, song])
    assert result["trashed"] == 1
    assert not result["errors"]
    assert not library.path(song).exists()
    assert (recycle_bin / song["path"]).is_file()
    assert len(library.songs()) == 2
    assert not library.songs("missing")
    assert not library.db.execute("SELECT 1 FROM new_arrivals WHERE song_id=?", (song["id"],)).fetchone()
    assert not library.db.execute(
        "SELECT 1 FROM rename_acceptances WHERE song_id=?", (song["id"],)
    ).fetchone()
    assert Path(result["database_backup"]).is_file()
    assert json.loads(Path(result["report_path"]).read_text())["files"] == [song["path"]]
    library.scan()
    assert len(library.songs()) == 2
    shutil.move(recycle_bin / song["path"], library.path(song))
    library.scan()
    assert len(library.songs("untagged")) == 3


def test_trash_skips_changed_and_now_categorized_songs(library, recycle_bin):
    songs = library.songs()
    category = library.add_category("Keep")
    library.assign([songs[0]["id"]], [category])
    library.edit_metadata([songs[1]["id"]], {"title": "Changed after confirmation"})
    result = trash_untagged(library, songs)
    assert result["trashed"] == 1
    assert len(result["errors"]) == 2
    assert "only Untagged" in result["errors"][0]["error"]
    assert "changed since confirmation" in result["errors"][1]["error"]
    assert all(library.path(song).is_file() for song in songs[:2])
    assert library.songs(category)[0]["id"] == songs[0]["id"]


def test_recycle_failure_leaves_file_and_database_and_continues(library, recycle_bin, monkeypatch):
    songs = library.songs()
    original_recycle = trash_module.recycle_file

    def recycle(path):
        if path.name == songs[0]["path"]:
            raise PermissionError("Locked file")
        original_recycle(path)

    monkeypatch.setattr("jbs_mp3_manager.trash.recycle_file", recycle)
    result = trash_untagged(library, songs[:2])
    assert result["trashed"] == 1
    assert result["errors"][0]["error"] == "Locked file"
    assert library.path(songs[0]).is_file()
    assert library.song(songs[0]["id"])
    assert len(library.songs()) == 2


def test_database_failure_after_recycle_keeps_a_recoverable_record(library, recycle_bin):
    song = library.songs()[0]
    library.db.execute(
        "CREATE TRIGGER block_delete BEFORE DELETE ON songs BEGIN SELECT RAISE(ABORT, 'Database test failure'); END"
    )
    result = trash_untagged(library, [song])
    assert result["trashed"] == 1
    assert "library update failed" in result["errors"][0]["error"]
    assert library.song(song["id"])
    assert (recycle_bin / song["path"]).is_file()
    library.scan()
    assert library.songs("missing")[0]["id"] == song["id"]


def test_recycle_adapter_never_falls_back_to_permanent_delete(tmp_path, monkeypatch):
    path = tmp_path / "Leave me.mp3"
    path.write_bytes(b"generated data")

    class UnavailableTrash:
        def __init__(self, path):
            pass

        def moveToTrash(self):
            return False

        def errorString(self):
            return "Unavailable on this drive"

    monkeypatch.setattr("jbs_mp3_manager.trash.QFile", UnavailableTrash)
    with pytest.raises(OSError, match="Unavailable on this drive"):
        recycle_file(path)
    assert path.read_bytes() == b"generated data"


@pytest.mark.skipif(os.name != "nt", reason="Windows native Recycle Bin integration")
def test_native_windows_recycle_removes_generated_file(qapp, tmp_path):
    path = tmp_path / "JBs-generated-recycle-test.txt"
    path.write_text("Generated test data; safe to empty from Recycle Bin.")
    recycle_file(path)
    assert not path.exists()


def test_untagged_delete_button_requires_selection_and_confirmation(qapp, library, recycle_bin, monkeypatch):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    assert window.delete_files_action.isEnabled()
    assert window.delete_files_button.isHidden()
    window.category_list.setCurrentRow(1)
    window.table.selectionModel().select(
        window.proxy.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert window.delete_files_action.isEnabled()
    assert not window.delete_files_button.isHidden()
    selected = window.selected_songs()
    monkeypatch.setattr(window, "confirm_file_deletion", lambda songs: False)
    window.delete_untagged_files()
    assert library.path(selected[0]).exists()
    monkeypatch.setattr(window, "confirm_file_deletion", lambda songs: songs == selected)
    monkeypatch.setattr(
        window, "run_job", lambda operation, completed: completed(operation(window.library, lambda _: None))
    )
    results = []
    monkeypatch.setattr(window, "show_report", lambda title, result: results.append(result))
    window.delete_untagged_files()
    assert results[0]["trashed"] == 1
    assert not library.path(selected[0]).exists()
    window.refresh()
    assert window.model.rowCount() == 2
    window.category_list.setCurrentRow(0)
    assert window.delete_files_action.isEnabled()
    window.close()


def test_delete_confirmation_lists_paths_and_defaults_to_cancel(qapp, library):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    songs = library.songs()[:2]

    def cancel_confirmation():
        box = next(w for w in qapp.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible())
        assert all(song["path"] in box.informativeText() for song in songs)
        assert all(str(library.path(song)) in box.detailedText() for song in songs)
        assert box.defaultButton() == box.button(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Cancel).click()

    QTimer.singleShot(0, cancel_confirmation)
    assert not window.confirm_file_deletion(songs)
    assert all(library.path(song).exists() for song in songs)
    window.close()
