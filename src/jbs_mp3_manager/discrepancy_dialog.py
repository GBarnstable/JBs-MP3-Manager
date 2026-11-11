import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .discrepancies import CHOICES, preview
from .table_columns import configure_columns


class DiscrepancyDialog(QDialog):
    def __init__(self, songs, parent=None, *, on_apply, on_refresh):
        super().__init__(parent)
        self.songs = songs
        self.on_apply, self.on_refresh = on_apply, on_refresh
        self.choices = {}
        self.busy = False
        self.last_details = None
        self.setWindowTitle("Review filename / metadata discrepancies")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(1400, 720)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Choose a direction for each song. Nothing changes until you click Apply. "
            "Metadata from filename copies the parsed Artist and Title literally. Filename from metadata "
            "uses First Last--Title.mp3 and moves a leading The to ', The'. "
            "Other tags, artwork, audio and categories are preserved. Legacy spaced separators are recognized."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        filters = QHBoxLayout()
        self.include_conventions = QCheckBox(
            "Show convention-only differences (The / name order / characters)"
        )
        self.include_conventions.toggled.connect(self.rebuild)
        filters.addWidget(self.include_conventions)
        self.reason = QComboBox()
        self.reason.currentIndexChanged.connect(self.filter_rows)
        filters.addWidget(self.reason)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find filename, artist or title…")
        self.search.textChanged.connect(self.filter_rows)
        filters.addWidget(self.search, 1)
        layout.addLayout(filters)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                "Current filename",
                "Artist metadata",
                "Title metadata",
                "Discrepancy",
                "Your decision",
                "Preview of chosen update",
            ]
        )
        configure_columns(self.table, "discrepancies")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self.show_selection)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.detail = QLabel("Select a row to see the parsed filename and metadata details.")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        controls = QHBoxLayout()
        for label, callback in (
            ("Select all visible rows", self.select_visible),
            ("Selected: metadata from filename", lambda: self.set_choices("tags")),
            ("Selected: filename from metadata", lambda: self.set_choices("filename")),
            ("Selected: leave unchanged", lambda: self.set_choices("skip")),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.status = QLabel("Review the previews, then apply only the updates you have chosen.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Rescan")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        self.details_button = QPushButton("Last batch details…")
        self.details_button.clicked.connect(self.show_details)
        self.details_button.setEnabled(False)
        buttons.addWidget(self.details_button)
        buttons.addStretch()
        self.apply_button = QPushButton("Apply chosen updates")
        self.apply_button.setAutoDefault(False)
        self.apply_button.clicked.connect(self.apply)
        buttons.addWidget(self.apply_button)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.rebuild()
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        for column, width in enumerate((260, 160, 190, 145, 255, 350)):
            self.table.setColumnWidth(column, width)

    def entry(self, row):
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def rebuild(self, *_):
        rows = preview(self.songs, include_conventions=self.include_conventions.isChecked())
        self.choices = {e["id"]: self.choices.get(e["id"], "skip") for e in rows}
        old_reason = self.reason.currentText()
        self.reason.blockSignals(True)
        self.reason.clear()
        self.reason.addItems(["All discrepancies", *sorted({r for e in rows for r in e["reasons"]})])
        self.reason.setCurrentIndex(max(0, self.reason.findText(old_reason)))
        self.reason.blockSignals(False)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            values = [Path(entry["path"]).name, entry["artist"], entry["title"], "; ".join(entry["reasons"])]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry)
                self.table.setItem(row, column, item)
            combo = QComboBox()
            for choice, label in CHOICES.items():
                combo.addItem(label, choice)
                if choice in entry["blocked"]:
                    option = combo.model().item(combo.count() - 1)
                    option.setEnabled(False)
                    option.setToolTip(entry["blocked"][choice])
            choice = self.choices[entry["id"]]
            if choice in entry["blocked"]:
                choice = self.choices[entry["id"]] = "skip"
            combo.setCurrentIndex(combo.findData(choice))
            combo.currentIndexChanged.connect(
                lambda _, sid=entry["id"], c=combo: self.choice_changed(sid, c.currentData())
            )
            self.table.setCellWidget(row, 4, combo)
            self.table.setItem(row, 5, QTableWidgetItem())
            self.update_preview(row)
            self.table.setRowHeight(row, 42)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.filter_rows()

    def update_preview(self, row):
        entry = self.entry(row)
        choice = self.choices[entry["id"]]
        if choice == "tags":
            text = "; ".join(f"{field.title()} → {value}" for field, value in entry["tag_values"].items())
        elif choice == "filename":
            text = entry["proposed_filename"]
        else:
            text = "No change"
            if entry["blocked"]:
                text += " · " + " ".join(
                    f"{'Rename' if key == 'filename' else 'Tag update'} unavailable: {reason}"
                    for key, reason in entry["blocked"].items()
                )
        item = self.table.item(row, 5)
        item.setText(text)
        item.setToolTip(text)

    def choice_changed(self, song_id, choice):
        self.choices[song_id] = choice
        for row in range(self.table.rowCount()):
            if self.entry(row)["id"] == song_id:
                self.table.setCurrentCell(row, 0)
                self.update_preview(row)
                break
        self.update_summary()
        self.show_selection()

    def filter_rows(self, *_):
        query, reason = self.search.text().casefold(), self.reason.currentText()
        for row in range(self.table.rowCount()):
            entry = self.entry(row)
            matches = query in " ".join([entry["path"], entry["artist"], entry["title"]]).casefold()
            self.table.setRowHidden(
                row, not matches or (reason != "All discrepancies" and reason not in entry["reasons"])
            )
        self.update_summary()

    def select_visible(self):
        self.table.selectAll()

    def set_choices(self, choice):
        rows = {
            i.row() for i in self.table.selectionModel().selectedRows() if not self.table.isRowHidden(i.row())
        }
        self.table.setSortingEnabled(False)
        for row in rows:
            entry = self.entry(row)
            if choice not in entry["blocked"]:
                self.choices[entry["id"]] = choice
                combo = self.table.cellWidget(row, 4)
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.findData(choice))
                combo.blockSignals(False)
                self.update_preview(row)
        self.table.setSortingEnabled(True)
        self.update_summary()
        self.show_selection()

    def decisions(self):
        return [
            {**self.entry(row), "choice": self.choices[self.entry(row)["id"]]}
            for row in range(self.table.rowCount())
            if self.choices[self.entry(row)["id"]] != "skip"
        ]

    def update_summary(self):
        chosen = len(self.decisions())
        visible = sum(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        hidden_chosen = sum(
            self.table.isRowHidden(row) and self.choices[self.entry(row)["id"]] != "skip"
            for row in range(self.table.rowCount())
        )
        self.summary.setText(
            f"{visible} visible / {self.table.rowCount()} discrepancies · {chosen} chosen updates ({hidden_chosen} hidden by filters)"
        )
        self.apply_button.setText(f"Apply {chosen} chosen updates")
        self.apply_button.setEnabled(chosen > 0 and not self.busy)

    def show_selection(self):
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 0):
            return
        e = self.entry(row)
        self.detail.setText(
            f"{e['path']}\nFilename: Artist = {e['file_artist'] or '(unknown)'} · Title = {e['file_title'] or '(unknown)'}\nMetadata: Artist = {e['artist'] or '(empty)'} · Title = {e['title'] or '(empty)'}\nChosen: {self.table.item(row, 5).text()}"
        )

    def set_busy(self, busy):
        self.busy = busy
        for widget in [
            self.table,
            self.include_conventions,
            self.reason,
            self.search,
            *self.findChildren(QPushButton),
        ]:
            widget.setEnabled(not busy)
        if not busy:
            self.details_button.setEnabled(self.last_details is not None)
            self.update_summary()

    def apply(self):
        if self.busy or not self.decisions():
            return
        decisions = self.decisions()
        self.set_busy(True)
        self.on_apply(decisions)

    def refresh(self):
        if not self.busy:
            self.set_busy(True)
            self.on_refresh()

    def complete_action(self, context):
        self.songs = context["songs"]
        self.choices.clear()
        self.last_details = context
        self.rebuild()
        self.set_busy(False)
        result = context.get("result")
        text = (
            f"Metadata updated: {result['updated']} · Files renamed: {result['renamed']} · Errors: {len(result['errors'])}. "
            if result
            else "Scan complete. "
        )
        self.status.setText(
            text + "Remaining discrepancies are shown. See Last batch details for reports and scan errors."
        )

    def fail_action(self, details):
        self.songs = []
        self.choices.clear()
        self.last_details = str(details)
        self.rebuild()
        self.set_busy(False)
        self.status.setText(
            "Operation could not finish. Completed changes are kept; use Rescan before continuing."
        )

    def show_progress(self, message):
        if self.busy:
            self.status.setText(message)

    def show_details(self):
        box = QMessageBox(self)
        box.setWindowTitle("Filename / metadata review results")
        box.setText(self.status.text())
        box.setDetailedText(json.dumps(self.last_details, ensure_ascii=False, indent=2))
        box.exec()

    def reject(self):
        if not self.busy:
            super().reject()

    def accept(self):
        self.reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)
