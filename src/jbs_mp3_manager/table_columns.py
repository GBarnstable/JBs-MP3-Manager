"""Movable column headers with a separate remembered order for each list."""

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMenu


def configure_columns(table, key):
    header = table.horizontalHeader()
    header.setSectionsMovable(True)
    header.setFirstSectionMovable(True)
    header.setToolTip("Drag column headers to change their order. Right-click to reset the order.")
    table.setObjectName("columns_" + key)
    settings = QSettings("JBsMP3Manager", "Desktop")
    setting = "table_columns/" + key
    try:
        order = [int(value) for value in settings.value(setting, [])]
    except (TypeError, ValueError):
        order = []
    if sorted(order) == list(range(header.count())):
        for visual, logical in enumerate(order):
            header.moveSection(header.visualIndex(logical), visual)

    restoring = False

    def save(*_):
        if not restoring:
            settings.setValue(setting, [header.logicalIndex(i) for i in range(header.count())])
            settings.sync()

    def reset():
        nonlocal restoring
        restoring = True
        try:
            for logical in range(header.count()):
                header.moveSection(header.visualIndex(logical), logical)
        finally:
            restoring = False
        save()

    def show_menu(point):
        menu = QMenu(header)
        action = menu.addAction("Reset column order")
        try:
            if menu.exec(header.mapToGlobal(point)) == action:
                reset()
        finally:
            menu.deleteLater()

    header.sectionMoved.connect(save)
    header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    header.customContextMenuRequested.connect(show_menu)
    table.reset_column_order = reset
