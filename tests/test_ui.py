from PySide6.QtCore import QItemSelectionModel

from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.dialogs import CategoryDialog, FolderDialog, ImportPreview, MetadataDialog


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
