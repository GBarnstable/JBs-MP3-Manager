import json
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .table_columns import configure_columns


class CategorySuggestionsDialog(QDialog):
    def __init__(self, review, parent=None, *, on_apply, on_refresh, on_play=None, playback=None):
        super().__init__(parent)
        self.review = review
        self.on_apply, self.on_refresh = on_apply, on_refresh
        self.on_play, self.playback = on_play, playback
        self.busy = False
        self.last_details = None
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
        self.warnings = QLabel()
        self.warnings.setTextFormat(Qt.TextFormat.PlainText)
        self.warnings.setWordWrap(True)
        layout.addWidget(self.warnings)
        filters = QHBoxLayout()
        self.category = QComboBox()
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
        self.tabs = QTabWidget()
        page = QWidget()
        rows_layout = QVBoxLayout(page)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 7)
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
        self.table.itemChanged.connect(self.update_summary)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.table.currentCellChanged.connect(self.show_details)
        self.table.cellDoubleClicked.connect(self.play_selected)
        rows_layout.addWidget(self.table)
        self.tabs.addTab(page, "Remaining song assignments")
        self.new_table = QTableWidget(0, 3)
        self.new_table.setHorizontalHeaderLabels(
            ["Proposed new category", "Description", "Approved assignments"]
        )
        configure_columns(self.new_table, "suggested_categories")
        self.new_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.new_table.horizontalHeader().setStretchLastSection(True)
        self.new_table.setColumnWidth(0, 260)
        self.new_table.setColumnWidth(1, 680)
        self.tabs.addTab(self.new_table, "Proposed new categories")
        layout.addWidget(self.tabs, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(125)
        self.details.setPlaceholderText("Select a song to read the full recommendation and warnings.")
        self.new_table.itemSelectionChanged.connect(self.show_category_details)
        layout.addWidget(self.details)
        player = QHBoxLayout()
        self.play_button = QPushButton("Play highlighted song")
        self.play_button.clicked.connect(self.play_selected)
        self.pause_button = QPushButton("Pause / Resume")
        self.pause_button.clicked.connect(lambda: self.playback.toggle_pause())
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(lambda: self.playback.stop())
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderReleased.connect(lambda: self.playback.seek(self.seek.value()))
        self.position = QLabel("0:00 / 0:00")
        for button in (self.play_button, self.pause_button, self.stop_button):
            button.setAutoDefault(False)
            player.addWidget(button)
        player.addWidget(self.seek, 1)
        player.addWidget(self.position)
        layout.addLayout(player)
        self.player_status = QLabel(
            "Select a song and click Play, or double-click its row. Only that song is queued."
            if playback
            else "Playback is unavailable because the audio engine could not start."
        )
        self.player_status.setWordWrap(True)
        self.player_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.player_status)
        if playback:
            playback.track_changed.connect(self.track_changed)
            playback.position_changed.connect(self.update_position)
            playback.state_changed.connect(self.playback_state_changed)
            playback.error.connect(self.player_status.setText)
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
        self.status = QLabel("Approve a batch and apply it. This screen stays open for further review.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("Rescan")
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        self.details_button = QPushButton("Last batch details…")
        self.details_button.setAutoDefault(False)
        self.details_button.clicked.connect(self.show_batch_details)
        self.details_button.setEnabled(False)
        actions.addWidget(self.details_button)
        actions.addStretch()
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close
        )
        self.apply_button = self.box.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setAutoDefault(False)
        self.box.button(QDialogButtonBox.StandardButton.Close).setDefault(True)
        self.box.rejected.connect(self.reject)
        actions.addWidget(self.box)
        layout.addLayout(actions)
        for column, width in enumerate((70, 300, 180, 100, 350, 280, 115)):
            self.table.setColumnWidth(column, width)
        self.rebuild(review)
        self.table.sortItems(2, Qt.SortOrder.AscendingOrder)

    def rebuild(self, review):
        self.review = review
        self.by_index = {row["index"]: row for row in review["rows"]}
        rows = [row for row in review["rows"] if row["status"] != "Already assigned"]
        self.warnings.setText("\n".join(review["warnings"]))
        self.warnings.setVisible(bool(review["warnings"]))
        category = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All categories", None)
        for name in sorted({r["category"] for r in rows if r["category"]}, key=str.casefold):
            self.category.addItem(name, name)
        self.category.setCurrentIndex(max(0, self.category.findData(category)))
        self.category.blockSignals(False)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for position, row in enumerate(rows):
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
        self.table.blockSignals(False)
        needed = {row["category"] for row in rows if row["new"]}
        categories = [c for c in review["new_categories"] if c["name"] in needed]
        self.new_table.blockSignals(True)
        self.new_table.setRowCount(0)
        self.new_table.setRowCount(len(categories))
        for index, entry in enumerate(categories):
            for column, value in enumerate([entry["name"], entry["description"], "0"]):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.new_table.setItem(index, column, item)
        self.new_table.blockSignals(False)
        self.tabs.setTabText(0, f"Remaining song assignments ({len(rows)})")
        self.tabs.setTabText(1, f"Proposed new categories ({len(categories)})")
        self.details.clear()
        self.filter_rows()
        self.update_playback_controls()

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
        self.apply_button.setEnabled(count > 0 and not self.busy)
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
        self.update_playback_controls()

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

    def show_details(self, *_):
        index = self.table.currentRow()
        if index >= 0 and self.table.item(index, 0):
            row = self.row(index)
            self.details.setPlainText(
                f"{row['path']}\nArtist: {row['artist']} · Title: {row['title']}\nCategory: {row['category']} · Confidence: {row['confidence']}\nReason: {row['reason']}\nStatus: {row['error'] or row['status']}\n{row['warning']}"
            )
        self.update_playback_controls()

    def update_playback_controls(self, *_):
        index = self.table.currentRow()
        playable = (
            index >= 0
            and not self.table.isRowHidden(index)
            and self.table.item(index, 0)
            and self.row(index)["song_id"]
            and self.row(index)["path"]
        )
        self.play_button.setEnabled(bool(self.playback and self.on_play and playable and not self.busy))
        active = bool(self.playback and self.playback.queue and not self.playback._stopped)
        self.pause_button.setEnabled(active)
        self.stop_button.setEnabled(active)
        self.seek.setEnabled(active and self.seek.maximum() > 0)

    def play_selected(self, *_):
        if not self.play_button.isEnabled():
            return
        try:
            self.on_play(self.row(self.table.currentRow())["song_id"])
        except Exception as error:
            self.player_status.setText(f"Cannot play this song: {error}")

    def track_changed(self, song):
        self.playing_name = f"{song['artist']}--{song['title']}" if song["title"] else song["path"]
        self.player_status.setText("Playing: " + self.playing_name)
        self.update_playback_controls()

    def playback_state_changed(self, state):
        self.pause_button.setText("Resume" if state == "Paused" else "Pause")
        self.player_status.setText(
            f"{state}: {self.playing_name}" if state != "Stopped" and hasattr(self, "playing_name") else state
        )
        self.update_playback_controls()

    def update_position(self, position, length):
        if not self.seek.isSliderDown():
            self.seek.setRange(0, length)
            self.seek.setValue(position)
        self.position.setText(
            f"{position // 60000}:{position // 1000 % 60:02d} / {length // 60000}:{length // 1000 % 60:02d}"
        )
        self.update_playback_controls()

    def set_busy(self, busy):
        self.busy = busy
        for widget in [
            self.table,
            self.new_table,
            self.category,
            self.confidence,
            self.search,
            *self.findChildren(QPushButton),
        ]:
            widget.setEnabled(not busy)
        self.update_playback_controls()
        if not busy:
            self.details_button.setEnabled(self.last_details is not None)
            self.update_summary()

    def apply(self):
        if not self.busy and self.approved_indices():
            approved = self.approved_indices()
            self.set_busy(True)
            self.status.setText("Applying approved additions and refreshing remaining suggestions…")
            self.on_apply(approved)

    def refresh(self):
        if not self.busy:
            self.set_busy(True)
            self.status.setText("Rescanning and refreshing suggestions…")
            self.on_refresh()

    def complete_action(self, context):
        self.last_details = {"result": context.get("result"), "warnings": context["review"]["warnings"]}
        self.rebuild(context["review"])
        self.set_busy(False)
        result = context.get("result")
        text = (
            f"Added: {result['added']} · New categories: {len(result['created_categories'])} · "
            f"Skipped: {len(result['skipped'])} · Errors: {len(result['errors'])}. "
            if result is not None
            else "Rescan complete. "
        )
        if result is not None and result["warnings"]:
            text += f"Warnings: {len(result['warnings'])}; see Last batch details. "
        text += (
            "Remaining suggestions are shown. Approve another batch or close when finished."
            if self.table.rowCount()
            else "No suggestions remain. You can close this screen."
        )
        self.status.setText(text)

    def fail_action(self, details):
        self.last_details = str(details)
        self.rebuild({**self.review, "rows": [], "new_categories": [], "warnings": []})
        self.set_busy(False)
        self.status.setText(
            "Operation could not finish. Any saved additions are kept. Click Rescan before continuing; "
            "Last batch details shows the error."
        )

    def show_progress(self, message):
        if self.busy:
            self.status.setText(message)

    def show_batch_details(self):
        box = QMessageBox(self)
        box.setWindowTitle("Category suggestion batch results")
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

    def show_category_details(self):
        row = self.new_table.currentRow()
        if row >= 0:
            self.details.setPlainText(
                f"Proposed category: {self.new_table.item(row, 0).text()}\n"
                f"Description: {self.new_table.item(row, 1).text()}\n"
                f"Approved assignments: {self.new_table.item(row, 2).text()}\n"
                "Created only when needed by an approved song assignment."
            )
