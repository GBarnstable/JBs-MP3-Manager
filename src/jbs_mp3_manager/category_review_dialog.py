from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .table_columns import configure_columns


class CategorySuggestionsDialog(QDialog):
    def __init__(self, review, parent=None):
        super().__init__(parent)
        self.review = review
        self.by_index = {row["index"]: row for row in review["rows"]}
        self.setWindowTitle("Review category suggestions — approve additions")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(1400, 760)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Review each recommendation and its confidence/reason, then approve the additions you want. "
            "Existing memberships are kept. New categories are created only when an approved song assignment "
            "needs them. MP3 files, filenames and metadata are unchanged. The category database is backed up."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        if review["warnings"]:
            warnings = QLabel("\n".join(review["warnings"]))
            warnings.setTextFormat(Qt.TextFormat.PlainText)
            warnings.setWordWrap(True)
            layout.addWidget(warnings)
        filters = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItem("All categories", None)
        for name in sorted({r["category"] for r in review["rows"] if r["category"]}, key=str.casefold):
            self.category.addItem(name, name)
        self.category.currentIndexChanged.connect(self.filter_rows)
        filters.addWidget(self.category)
        self.confidence = QComboBox()
        for text, value in [
            ("All confidence levels", None),
            ("High confidence", "high"),
            ("Medium confidence", "medium"),
            ("Low confidence", "low"),
        ]:
            self.confidence.addItem(text, value)
        self.confidence.currentIndexChanged.connect(self.filter_rows)
        filters.addWidget(self.confidence)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find song, artist, category or explanation…")
        self.search.textChanged.connect(self.filter_rows)
        filters.addWidget(self.search, 1)
        layout.addLayout(filters)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        tabs = QTabWidget()
        page = QWidget()
        rows_layout = QVBoxLayout(page)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(len(review["rows"]), 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Approve",
                "Song",
                "Category",
                "Confidence",
                "Recommendation / reason",
                "Status / warnings",
                "New category",
            ]
        )
        configure_columns(self.table, "category_suggestions")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        for position, row in enumerate(review["rows"]):
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, row["index"])
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if row["status"] == "Ready":
                check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(position, 0, check)
            song = f"{row['artist']}--{row['title']}" if row["title"] else row["path"] or row["song_id"]
            status = row["error"] or row["status"]
            if row["warning"]:
                status += "; " + row["warning"]
            for column, value in enumerate(
                [
                    song,
                    row["category"],
                    row["confidence"],
                    row["reason"],
                    status,
                    "Yes" if row["new"] else "",
                ],
                1,
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(row["path"] if column == 1 else value)
                self.table.setItem(position, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(2, Qt.SortOrder.AscendingOrder)
        self.table.itemChanged.connect(self.update_summary)
        self.table.itemSelectionChanged.connect(self.show_details)
        rows_layout.addWidget(self.table)
        tabs.addTab(page, f"Song assignments ({len(review['rows'])})")
        self.new_table = QTableWidget(len(review["new_categories"]), 3)
        self.new_table.setHorizontalHeaderLabels(
            ["Proposed new category", "Description", "Approved assignments"]
        )
        configure_columns(self.new_table, "suggested_categories")
        self.new_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.new_table.horizontalHeader().setStretchLastSection(True)
        for index, category in enumerate(review["new_categories"]):
            for column, value in enumerate([category["name"], category["description"], "0"]):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.new_table.setItem(index, column, item)
        self.new_table.setColumnWidth(0, 260)
        self.new_table.setColumnWidth(1, 680)
        tabs.addTab(self.new_table, f"Proposed new categories ({len(review['new_categories'])})")
        layout.addWidget(tabs, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(125)
        self.details.setPlaceholderText("Select a song to read the full recommendation and warnings.")
        self.new_table.itemSelectionChanged.connect(self.show_category_details)
        layout.addWidget(self.details)
        controls = QHBoxLayout()
        for label, callback in (
            ("Approve all visible valid suggestions", lambda: self.set_approvals(True)),
            ("Clear all approvals", lambda: self.set_approvals(False, all_rows=True)),
            ("Approve highlighted rows", lambda: self.set_approvals(True, selected=True)),
            ("Unapprove highlighted rows", lambda: self.set_approvals(False, selected=True)),
        ):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_button = self.box.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self.accept)
        self.apply_button.setAutoDefault(False)
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)
        for column, width in enumerate((70, 300, 180, 100, 350, 280, 115)):
            self.table.setColumnWidth(column, width)
        self.update_summary()

    def row(self, position):
        return self.by_index[self.table.item(position, 0).data(Qt.ItemDataRole.UserRole)]

    def approved_indices(self):
        return [
            self.row(i)["index"]
            for i in range(self.table.rowCount())
            if self.table.item(i, 0).checkState() == Qt.CheckState.Checked
        ]

    def update_summary(self, *_):
        approved = {self.by_index[i]["category"] for i in self.approved_indices() if self.by_index[i]["new"]}
        hidden = sum(
            self.table.isRowHidden(i) and self.table.item(i, 0).checkState() == Qt.CheckState.Checked
            for i in range(self.table.rowCount())
        )
        statuses = Counter(r["status"] for r in self.review["rows"])
        count = len(self.approved_indices())
        self.summary.setText(
            f"{count} approved assignments ({hidden} hidden by filters) · {len(approved)} new categories to create · "
            f"{statuses['Ready']} ready · {statuses['Already assigned']} already assigned · {statuses['Invalid']} invalid"
        )
        self.apply_button.setText(f"Apply {count} approved additions")
        self.apply_button.setEnabled(count > 0)
        counts = Counter(self.by_index[i]["category"].casefold() for i in self.approved_indices())
        for index in range(self.new_table.rowCount()):
            name = self.new_table.item(index, 0).text()
            self.new_table.item(index, 2).setText(str(counts[name.casefold()]))

    def filter_rows(self, *_):
        category, confidence, query = (
            self.category.currentData(),
            self.confidence.currentData(),
            self.search.text().casefold(),
        )
        for i in range(self.table.rowCount()):
            row = self.row(i)
            text = " ".join(
                row[k] for k in ("path", "artist", "title", "category", "reason", "error", "warning")
            ).casefold()
            self.table.setRowHidden(
                i,
                bool(
                    (category and row["category"] != category)
                    or (confidence and row["confidence"] != confidence)
                    or query not in text
                ),
            )
        self.update_summary()

    def set_approvals(self, approved, *, selected=False, all_rows=False):
        rows = (
            {i.row() for i in self.table.selectionModel().selectedRows()}
            if selected
            else range(self.table.rowCount())
        )
        items = [
            self.table.item(i, 0)
            for i in rows
            if (all_rows or not self.table.isRowHidden(i)) and self.row(i)["status"] == "Ready"
        ]
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        for item in items:
            item.setCheckState(Qt.CheckState.Checked if approved else Qt.CheckState.Unchecked)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.update_summary()

    def show_details(self):
        index = self.table.currentRow()
        if index >= 0 and self.table.item(index, 0):
            row = self.row(index)
            self.details.setPlainText(
                f"{row['path']}\nArtist: {row['artist']} · Title: {row['title']}\nCategory: {row['category']} · Confidence: {row['confidence']}\nReason: {row['reason']}\nStatus: {row['error'] or row['status']}\n{row['warning']}"
            )

    def accept(self):
        if self.approved_indices():
            super().accept()

    def show_category_details(self):
        row = self.new_table.currentRow()
        if row >= 0:
            self.details.setPlainText(
                f"Proposed category: {self.new_table.item(row, 0).text()}\n"
                f"Description: {self.new_table.item(row, 1).text()}\n"
                f"Approved assignments: {self.new_table.item(row, 2).text()}\n"
                "Created only when needed by an approved song assignment."
            )
