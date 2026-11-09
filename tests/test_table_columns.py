import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTableWidget

from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.arrival_dialogs import ComparisonDialog, RenameDialog
from jbs_mp3_manager.delete_dialog import DeleteFilesDialog
from jbs_mp3_manager.dialogs import ImportPreview
from jbs_mp3_manager.discrepancy_dialog import DiscrepancyDialog
from jbs_mp3_manager.duplicates import review
from jbs_mp3_manager.filename_tag_dialog import FilenameTagDialog
from jbs_mp3_manager.naming import proposals
from jbs_mp3_manager.review_dialog import ReviewDialog
from jbs_mp3_manager.table_columns import configure_columns


@pytest.fixture(autouse=True)
def isolated_orders(qapp):
    settings = QSettings("JBsMP3Manager", "Desktop")
    settings.remove("table_columns")
    yield
    settings.remove("table_columns")
    settings.sync()


def order(table):
    header = table.horizontalHeader()
    return [header.logicalIndex(i) for i in range(header.count())]


def test_header_order_survives_reopen_and_is_separate_for_each_list(qapp):
    first = QTableWidget(0, 4)
    configure_columns(first, "test_first")
    assert first.horizontalHeader().sectionsMovable()
    assert first.horizontalHeader().isFirstSectionMovable()
    first.horizontalHeader().moveSection(0, 3)
    assert order(first) == [1, 2, 3, 0]
    first.close()
    reopened = QTableWidget(0, 4)
    configure_columns(reopened, "test_first")
    other_list = QTableWidget(0, 4)
    configure_columns(other_list, "test_other")
    assert order(reopened) == [1, 2, 3, 0]
    assert order(other_list) == [0, 1, 2, 3]
    reopened.reset_column_order()
    assert order(reopened) == [0, 1, 2, 3]
    reset_reopened = QTableWidget(0, 4)
    configure_columns(reset_reopened, "test_first")
    assert order(reset_reopened) == [0, 1, 2, 3]


def test_column_order_from_an_older_table_shape_is_ignored(qapp):
    settings = QSettings("JBsMP3Manager", "Desktop")
    settings.setValue("table_columns/test_shape", [1, 0, 2])
    table = QTableWidget(0, 5)
    configure_columns(table, "test_shape")
    assert order(table) == [0, 1, 2, 3, 4]


def test_real_mouse_drag_moves_header(qapp):
    table = QTableWidget(2, 4)
    table.setHorizontalHeaderLabels(["Artist", "Title", "Album", "Filename"])
    configure_columns(table, "test_drag")
    table.resize(700, 220)
    table.show()
    qapp.processEvents()
    header = table.horizontalHeader()
    start = QPoint(header.sectionViewportPosition(0) + header.sectionSize(0) // 2, header.height() // 2)
    end = QPoint(header.sectionViewportPosition(2) + header.sectionSize(2) - 8, header.height() // 2)
    QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=start)
    for x in range(start.x(), end.x(), 12):
        QTest.mouseMove(header.viewport(), QPoint(x, start.y()), delay=5)
    QTest.mouseMove(header.viewport(), end, delay=5)
    QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=end)
    qapp.processEvents()
    assert header.visualIndex(0) > 0


def test_all_song_tables_support_reordering_and_main_order_survives_refresh(qapp, library):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    dialogs = [
        ImportPreview({"entries": [], "categories": []}),
        RenameDialog(proposals(library)),
        ComparisonDialog({"pairs": [], "note": "Generated test data"}),
        ReviewDialog(review(library)),
        FilenameTagDialog(library.songs()),
        DiscrepancyDialog(library.songs(), on_apply=lambda _: None, on_refresh=lambda: None),
        DeleteFilesDialog(library.root, library.songs()),
    ]
    tables = [window.table]
    for dialog in dialogs:
        tables.extend(dialog.findChildren(QTableWidget))
    assert len(tables) == 9
    for table in tables:
        header = table.horizontalHeader()
        assert header.sectionsMovable()
        header.moveSection(0, header.count() - 1)
        assert header.logicalIndex(header.count() - 1) == 0
    expected = order(window.table)
    window.refresh()
    assert order(window.table) == expected
    assert window.model.columnCount() == len(expected)
    for dialog in dialogs:
        dialog.close()
    window.close()


def test_reordering_delete_checkbox_column_keeps_checked_song_ids(qapp, library):
    songs = library.songs()
    dialog = DeleteFilesDialog(library.root, songs, [songs[0]["id"]])
    dialog.table.horizontalHeader().moveSection(0, 4)
    assert order(dialog.table)[-1] == 0
    assert [song["id"] for song in dialog.selected_songs()] == [songs[0]["id"]]
    dialog.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    dialog.set_checks(False, all_rows=True)
    dialog.search.setText(songs[1]["path"])
    dialog.set_checks(True)
    assert [song["id"] for song in dialog.selected_songs()] == [songs[1]["id"]]
