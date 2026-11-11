from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .table_columns import configure_columns


class DeleteFilesDialog(QDialog):
    """Select and explicitly approve actual Untagged files for recycling."""

    def __init__(self, root, songs, preselected=(), parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.setWindowTitle("Delete Untagged MP3 files — select and confirm")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(1100, 650)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Check the files to delete. This removes the actual MP3 files from your music folder and library "
            "by moving them to the Windows Recycle Bin. Only checked files are removed. "
            "You can restore them from the Recycle Bin and rescan."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        folder = QLabel("Music folder: " + str(self.root))
        folder.setTextFormat(Qt.TextFormat.PlainText)
        folder.setWordWrap(True)
        layout.addWidget(folder)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find filename, artist or title…")
        self.search.textChanged.connect(self.filter_rows)
        layout.addWidget(self.search)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.table = QTableWidget(len(songs), 5)
        self.table.setHorizontalHeaderLabels(["Delete", "Filename", "Artist", "Title", "File size"])
        configure_columns(self.table, "delete_untagged")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        selected = set(preselected)
        for row, song in enumerate(songs):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
            )
            check.setData(Qt.ItemDataRole.UserRole, song)
            check.setCheckState(Qt.CheckState.Checked if song["id"] in selected else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            for column, value in enumerate(
                [song["path"], song["artist"], song["title"], f"{song['size'] / (1024 * 1024):.2f} MiB"], 1
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(str(self.root / song["path"]) if column == 1 else value)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.table.itemChanged.connect(self.update_summary)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        for label, callback in (
            ("Check all visible files", lambda: self.set_checks(True)),
            ("Clear all checks", lambda: self.set_checks(False, all_rows=True)),
            ("Check highlighted rows", lambda: self.set_checks(True, highlighted=True)),
            ("Uncheck highlighted rows", lambda: self.set_checks(False, highlighted=True)),
        ):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.delete_button = self.box.button(QDialogButtonBox.StandardButton.Save)
        self.delete_button.setText("Move checked files to Recycle Bin")
        self.delete_button.setAutoDefault(False)
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)
        for column, width in enumerate((60, 420, 180, 240, 100)):
            self.table.setColumnWidth(column, width)
        self.update_summary()

    def selected_songs(self):
        return [
            self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def filter_rows(self, *_):
        query = self.search.text().casefold()
        for row in range(self.table.rowCount()):
            song = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.table.setRowHidden(
                row, query not in " ".join([song["path"], song["artist"], song["title"]]).casefold()
            )
        self.update_summary()

    def update_summary(self, *_):
        selected = len(self.selected_songs())
        hidden = sum(
            self.table.isRowHidden(row) and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
            for row in range(self.table.rowCount())
        )
        self.summary.setText(
            f"{self.table.rowCount()} Untagged file(s) available · {selected} checked for deletion ({hidden} hidden by search)"
            if self.table.rowCount()
            else "No Untagged MP3 files are available to delete."
        )
        self.delete_button.setEnabled(selected > 0)

    def set_checks(self, checked, *, highlighted=False, all_rows=False):
        rows = (
            {i.row() for i in self.table.selectionModel().selectedRows()}
            if highlighted
            else range(self.table.rowCount())
        )
        items = [self.table.item(row, 0) for row in rows if all_rows or not self.table.isRowHidden(row)]
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        for item in items:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.update_summary()

    def accept(self):
        if self.selected_songs():
            super().accept()
