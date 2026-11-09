from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QDialog

from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.delete_dialog import DeleteFilesDialog


def test_menu_trigger_opens_untagged_chooser_from_all_songs_without_selection(qapp, library, monkeypatch):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    opened = []

    def review(dialog):
        opened.append(dialog)
        assert dialog.table.rowCount() == 3
        assert not dialog.selected_songs()
        assert not dialog.delete_button.isEnabled()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(DeleteFilesDialog, "exec", review)
    assert window.current_category is None and not window.selected_songs()
    window.delete_files_action.trigger()
    assert len(opened) == 1
    assert len(library.songs()) == 3
    window.close()


def test_menu_accepts_only_checked_files_from_all_songs(qapp, library, tmp_path, monkeypatch):
    recycled = tmp_path / "Recycled"
    recycled.mkdir()
    monkeypatch.setattr("jbs_mp3_manager.trash.recycle_file", lambda path: path.rename(recycled / path.name))
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    first = window.model.rows[0]
    window.table.selectionModel().select(
        window.proxy.mapFromSource(window.model.index(0, 0)),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )

    def review(dialog):
        assert [song["id"] for song in dialog.selected_songs()] == [first["id"]]
        assert dialog.box.button(dialog.box.StandardButton.Cancel).isDefault()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DeleteFilesDialog, "exec", review)
    monkeypatch.setattr(
        window, "run_job", lambda operation, completed: completed(operation(window.library, lambda _: None))
    )
    results = []
    monkeypatch.setattr(window, "show_report", lambda title, result: results.append(result))
    window.delete_files_action.trigger()
    assert results[0]["trashed"] == 1
    assert not library.path(first).exists()
    assert (recycled / first["path"]).is_file()
    assert len(library.songs()) == 2
    window.close()


def test_chooser_excludes_categorized_files_even_if_selected_in_main_window(qapp, library, monkeypatch):
    song = library.songs()[0]
    category = library.add_category("Keep")
    library.assign([song["id"]], [category])
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    source_row = next(row for row, entry in enumerate(window.model.rows) if entry["id"] == song["id"])
    window.table.selectionModel().select(
        window.proxy.mapFromSource(window.model.index(source_row, 0)),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    opened = []

    def review(dialog):
        opened.append(True)
        assert dialog.table.rowCount() == 2
        assert not dialog.selected_songs()
        assert all(
            dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole)["id"] != song["id"]
            for row in range(dialog.table.rowCount())
        )
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(DeleteFilesDialog, "exec", review)
    window.delete_files_action.trigger()
    assert opened == [True]
    assert library.songs(category)[0]["id"] == song["id"]
    window.close()


def test_empty_untagged_chooser_opens_with_clear_message(qapp, library, monkeypatch):
    category = library.add_category("All categorized")
    library.assign([s["id"] for s in library.songs()], [category])
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    opened = []

    def review(dialog):
        opened.append(True)
        assert dialog.table.rowCount() == 0
        assert "No Untagged" in dialog.summary.text()
        assert not dialog.delete_button.isEnabled()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(DeleteFilesDialog, "exec", review)
    window.delete_files_action.trigger()
    assert opened == [True]
    window.close()


def test_delete_chooser_sort_search_and_bulk_checks_follow_song_ids(qapp, library):
    songs = library.songs()
    dialog = DeleteFilesDialog(library.root, songs, [songs[0]["id"]])
    assert [s["id"] for s in dialog.selected_songs()] == [songs[0]["id"]]
    dialog.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    dialog.search.setText(songs[1]["path"])
    assert "1 hidden" in dialog.summary.text()
    dialog.set_checks(True)
    assert {s["id"] for s in dialog.selected_songs()} == {songs[0]["id"], songs[1]["id"]}
    dialog.set_checks(False, all_rows=True)
    assert not dialog.selected_songs()
    assert not dialog.delete_button.isEnabled()
    dialog.search.clear()
    dialog.table.selectionModel().select(
        dialog.table.model().index(0, 1),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    highlighted = dialog.table.item(0, 0).data(Qt.ItemDataRole.UserRole)["id"]
    dialog.set_checks(True, highlighted=True)
    dialog.table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert [s["id"] for s in dialog.selected_songs()] == [highlighted]


def test_untagged_button_without_selection_opens_chooser_instead_of_returning(qapp, library, monkeypatch):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    window.category_list.setCurrentRow(1)
    calls = []
    monkeypatch.setattr(window, "choose_untagged_deletion", lambda: calls.append(True))
    window.delete_untagged_files()
    assert calls == [True]
    window.close()
