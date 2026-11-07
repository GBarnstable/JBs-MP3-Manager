import pytest
from PySide6.QtCore import QItemSelectionModel

from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.dialogs import CategoryDialog, FolderDialog, ImportPreview, MetadataDialog
from jbs_mp3_manager.naming import rename_files


def test_window_displays_real_library_and_categories(qapp, library):
    category = library.add_category("Favorites")
    library.assign([library.songs()[0]["id"]], [category])
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    window.show()
    qapp.processEvents()
    assert window.model.rowCount() == 3
    assert window.category_list.count() == 4
    window.category_list.setCurrentRow(1)
    assert window.current_category == "untagged"
    assert window.model.rowCount() == 2
    window.search.setText("no match")
    assert window.model.rowCount() == 0
    window.search.clear()
    window.table.selectionModel().select(
        window.proxy.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert len(window.selected_songs()) == 1
    window.close()


def test_import_and_metadata_dialogs_construct(qapp, library):
    category = library.add_category("Jazz")
    dialogs = [
        FolderDialog(library.root),
        CategoryDialog(library.categories(), 2),
        ImportPreview({"entries": [], "categories": ["Jazz"]}),
        MetadataDialog(library.songs()),
    ]
    for dialog in dialogs:
        dialog.show()
        qapp.processEvents()
        dialog.close()
    assert category
    assert dialogs[-1].values() == {}  # Batch edits do not implicitly clear any fields.


@pytest.mark.parametrize("auto_review", [False, True])
def test_correctly_named_song_needs_no_dialog_and_new_review_does_not_repeat(
    qapp, library, monkeypatch, auto_review
):
    song, other = library.songs()[:2]
    library.edit_metadata([song["id"]], {"artist": "The Who", "title": "My Generation"})
    song = library.song(song["id"])
    rename_files(
        library, [{"id": song["id"], "name": "Who, The--My Generation.mp3", "sha256": song["sha256"]}]
    )
    category = library.add_category("Favorites")
    library.assign([song["id"]], [category])
    with library.db:
        library.db.executemany("INSERT INTO new_arrivals VALUES (?)", [(song["id"],), (other["id"],)])
    before = library.path(library.song(song["id"])).read_bytes()
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    messages = []
    monkeypatch.setattr("jbs_mp3_manager.app.QMessageBox.information", lambda *args: messages.append(args[2]))
    monkeypatch.setattr(
        "jbs_mp3_manager.app.RenameDialog", lambda *args, **kwargs: pytest.fail("Unchanged song prompted")
    )
    monkeypatch.setattr(
        window, "run_job", lambda operation, completed: completed(operation(window.library, lambda _: None))
    )
    window.review_names(song_ids=[song["id"]], auto_review=auto_review)
    assert bool(messages) is not auto_review
    pending = {row[0] for row in library.db.execute("SELECT song_id FROM new_arrivals")}
    assert (song["id"] in pending) is not auto_review
    assert other["id"] in pending  # An unrelated arrival still needs review.
    assert library.path(library.song(song["id"])).read_bytes() == before
    assert library.songs(category)[0]["id"] == song["id"]
    window.close()
