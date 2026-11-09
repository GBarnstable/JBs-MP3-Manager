from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .filename_tags import TAG_FIELDS, preview
from .table_columns import configure_columns


class FilenameTagDialog(QDialog):
    def __init__(self, songs, parent=None):
        super().__init__(parent)
        self.songs = songs
        self.excluded = set()
        self.setWindowTitle("Update Artist / Title metadata from filenames")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(1200, 620)
        layout = QVBoxLayout(self)
        note = QLabel(
            f"Review {len(songs)} selected song(s). Uses artist--title filenames, keeping their spelling "
            "and name order exactly (including ', The'). Spaces beside -- are trimmed. "
            "Only approved rows and checked fields are updated. Other tags and artwork are preserved. "
            "Original ID3 tags are backed up before each edit."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        fields = QHBoxLayout()
        fields.addWidget(QLabel("Fields to update:"))
        self.fields = {}
        for field in TAG_FIELDS:
            check = QCheckBox(field.title())
            check.setChecked(True)
            check.toggled.connect(self.rebuild)
            self.fields[field] = check
            fields.addWidget(check)
        fields.addStretch()
        layout.addLayout(fields)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Approve",
                "Filename",
                "Current Artist",
                "Artist from\nfilename",
                "Current Title",
                "Title from\nfilename",
                "Status",
            ]
        )
        configure_columns(self.table, "filename_tags")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self.approval_changed)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        for label, callback in (
            ("Select all changes", lambda: self.set_approvals(True)),
            ("Clear approvals", lambda: self.set_approvals(False)),
            ("Approve selected rows", lambda: self.set_approvals(True, selected=True)),
            ("Unapprove selected rows", lambda: self.set_approvals(False, selected=True)),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.save = self.box.button(QDialogButtonBox.StandardButton.Save)
        self.save.setText("Update approved metadata")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)
        self.rebuild()
        for column, width in enumerate((70, 225, 135, 140, 160, 160, 260)):
            self.table.setColumnWidth(column, width)

    def rebuild(self, *_):
        fields = [field for field, check in self.fields.items() if check.isChecked()]
        entries = preview(self.songs, fields)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, entry)
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if entry["values"]:
                check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                check.setCheckState(
                    Qt.CheckState.Unchecked if entry["id"] in self.excluded else Qt.CheckState.Checked
                )
            self.table.setItem(row, 0, check)
            values = [
                Path(entry["path"]).name,
                entry["artist"],
                entry["derived"].get("artist", ""),
                entry["title"],
                entry["derived"].get("title", ""),
                entry["status"],
            ]
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.update_summary()

    def approval_changed(self, item):
        if item.column() == 0:
            song_id = item.data(Qt.ItemDataRole.UserRole)["id"]
            if item.checkState() == Qt.CheckState.Checked:
                self.excluded.discard(song_id)
            else:
                self.excluded.add(song_id)
            self.update_summary()

    def set_approvals(self, approved, *, selected=False):
        rows = (
            {index.row() for index in self.table.selectionModel().selectedRows()}
            if selected
            else range(self.table.rowCount())
        )
        items = [self.table.item(row, 0) for row in rows]
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        for item in items:
            if item.data(Qt.ItemDataRole.UserRole)["values"]:
                item.setCheckState(Qt.CheckState.Checked if approved else Qt.CheckState.Unchecked)
                song_id = item.data(Qt.ItemDataRole.UserRole)["id"]
                if approved:
                    self.excluded.discard(song_id)
                else:
                    self.excluded.add(song_id)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.update_summary()

    def updates(self):
        return [
            self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def update_summary(self):
        count = len(self.updates())
        self.summary.setText(f"{count} approved update(s) · rows without changes are skipped")
        self.save.setEnabled(count > 0)

    def accept(self):
        if self.updates():
            super().accept()
