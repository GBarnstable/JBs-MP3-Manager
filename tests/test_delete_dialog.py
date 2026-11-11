import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtWidgets import QDialog, QMenu

from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.delete_dialog import DeleteFilesDialog


def handle_song_menu(monkeypatch, handler):
    def create(parent):
        menu = QMenu(parent)
        # QMenu.exec is a Qt static/instance overload; patch the instance to avoid
        # entering a native popup loop in automated selection/recycling checks.
        menu.exec = lambda position: handler(menu, position)
        return menu

    monkeypatch.setattr("jbs_mp3_manager.app.QMenu", create)


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


def test_right_click_selects_clicked_song_instead_of_previous_selection_and_cancel_keeps_file(
    qapp, library, monkeypatch
):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    window.category_list.setCurrentRow(1)
    window.show()
    qapp.processEvents()
    window.table.selectRow(0)
    clicked = window.proxy.index(2, 1)
    expected = window.model.rows[window.proxy.mapToSource(clicked).row()]["id"]
    confirmations = []
    monkeypatch.setattr(
        window, "confirm_file_deletion", lambda songs: confirmations.append([s["id"] for s in songs]) or False
    )
    monkeypatch.setattr(window, "choose_untagged_deletion", lambda: pytest.fail("Unexpected file chooser"))

    def choose_delete(menu, position):
        assert [action.text() for action in menu.actions()] == ["Delete…"]
        menu.actions()[0].trigger()

    handle_song_menu(monkeypatch, choose_delete)
    window.table.customContextMenuRequested.emit(window.table.visualRect(clicked).center())
    assert confirmations == [[expected]]
    assert [s["id"] for s in window.selected_songs()] == [expected]
    assert all(library.path(s).exists() for s in library.songs())
    window.close()


def test_right_click_preserves_selected_group_and_recycles_only_its_snapshot(
    qapp, library, tmp_path, monkeypatch
):
    recycled = tmp_path / "Recycled"
    recycled.mkdir()
    monkeypatch.setattr("jbs_mp3_manager.trash.recycle_file", lambda path: path.rename(recycled / path.name))
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    window.category_list.setCurrentRow(1)
    window.show()
    qapp.processEvents()
    for row in (0, 1):
        window.table.selectionModel().select(
            window.proxy.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    expected = {song["id"] for song in window.selected_songs()}
    remaining = next(s for s in library.songs() if s["id"] not in expected)
    confirmations, reports = [], []
    monkeypatch.setattr(
        window, "confirm_file_deletion", lambda songs: confirmations.append({s["id"] for s in songs}) or True
    )
    monkeypatch.setattr(
        window, "run_job", lambda operation, completed: completed(operation(window.library, lambda _: None))
    )
    monkeypatch.setattr(window, "show_report", lambda title, result: reports.append(result))

    def choose_delete(menu, position):
        assert {s["id"] for s in window.selected_songs()} == expected
        assert menu.actions()[0].text() == "Delete selected songs…"
        # A view refresh/selection change while the popup is open cannot retarget deletion.
        window.table.selectRow(2)
        menu.actions()[0].trigger()

    handle_song_menu(monkeypatch, choose_delete)
    window.table.customContextMenuRequested.emit(window.table.visualRect(window.proxy.index(1, 1)).center())
    assert confirmations == [expected]
    assert reports[0]["trashed"] == 2 and not reports[0]["errors"]
    assert library.path(remaining).is_file()
    assert len(list(recycled.iterdir())) == 2
    assert {s["id"] for s in library.songs()} == {remaining["id"]}
    window.close()


def test_right_click_delete_uses_sorted_filtered_song_identity(qapp, library, monkeypatch):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    window.category_list.setCurrentRow(1)
    window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    window.search.setText("Bravo")
    window.show()
    qapp.processEvents()
    assert window.proxy.rowCount() == 1
    confirmations = []
    monkeypatch.setattr(
        window,
        "confirm_file_deletion",
        lambda songs: confirmations.append([s["path"] for s in songs]) or False,
    )
    handle_song_menu(monkeypatch, lambda menu, position: menu.actions()[0].trigger())
    window.table.customContextMenuRequested.emit(window.table.visualRect(window.proxy.index(0, 2)).center())
    assert confirmations == [["Bravo.MP3"]]
    window.close()


@pytest.mark.parametrize("scope", ["blank", "all songs", "busy", "no library"])
def test_right_click_delete_does_not_offer_action_without_an_eligible_target(
    qapp, library, monkeypatch, scope
):
    window = MainWindow(restore=False, enable_audio=False)
    if scope != "no library":
        window.open_library(library.root, scan=False)
        window.category_list.setCurrentRow(1)
    window.show()
    qapp.processEvents()
    position = window.table.visualRect(window.proxy.index(0, 0)).center()
    if scope == "blank":
        position = QPoint(10, window.table.viewport().height() - 2)
    elif scope == "all songs":
        window.category_list.setCurrentRow(0)
    elif scope == "busy":
        window.busy = True
    handle_song_menu(monkeypatch, lambda *args: pytest.fail("Unexpected deletion menu"))
    window.table.customContextMenuRequested.emit(position)
    assert len(library.songs()) == 3
    window.busy = False
    window.close()
