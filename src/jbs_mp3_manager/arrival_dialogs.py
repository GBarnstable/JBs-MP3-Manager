import json
from contextlib import contextmanager

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .media import quality_text
from .naming import (
    RENAME_RULES,
    _join_components,
    _replace_content,
    build_proposals,
    change_reason,
    filename_components,
    rename_operations,
    suggested_name,
    validate_name,
)


class RenameOneDialog(QDialog):
    def __init__(self, proposal, parent=None):
        super().__init__(parent)
        self.stop_review = False
        self.proposal = proposal
        self.setWindowTitle("Approve song filename")
        self.resize(720, 290)
        layout = QVBoxLayout(self)
        old = QLabel("Current: " + proposal["path"])
        old.setWordWrap(True)
        old.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(old)
        form = QFormLayout()
        self.artist = QLineEdit(proposal["rename_artist"])
        self.title = QLineEdit(proposal["rename_title"])
        only = proposal.get("only_reason")
        if only:
            self.artist.setEnabled(only in ("artist_order", "the", "metadata_artist", "metadata_name"))
            self.title.setEnabled(only in ("metadata_title", "metadata_name"))
        self.filename = QLineEdit(proposal["proposed"])
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        self.reason.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.textChanged.connect(
            lambda value: self.reason.setText(
                proposal.get("reason", "")
                if value == proposal["proposed"]
                else "Custom filename edit: " + change_reason(proposal["path"], value)
            )
        )
        self.reason.setText(proposal.get("reason") or change_reason(proposal["path"], proposal["proposed"]))
        form.addRow("Artist", self.artist)
        form.addRow("Song title", self.title)
        form.addRow("New filename", self.filename)
        form.addRow("Change", self.reason)
        layout.addLayout(form)
        self.artist.textEdited.connect(self.suggest)
        self.title.textEdited.connect(self.suggest)
        note = QLabel(
            "Artist and title are used to suggest the filename; the song's metadata stays unchanged. "
            "You can edit the filename directly. A leading The becomes ', The', except The The."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        for label, action in [("Approve", self.accept), ("Skip", self.reject), ("Stop reviewing", self.stop)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def suggest(self, *_):
        only = self.proposal.get("only_reason")
        parts = filename_components(self.proposal["path"])
        if only and parts:
            if only in ("artist_order", "the", "metadata_artist"):
                parts["artist"] = _replace_content(parts["artist"], self.artist.text().strip())
            elif only == "metadata_title":
                parts["title"] = _replace_content(parts["title"], self.title.text().strip())
            self.filename.setText(_join_components(parts))
        else:
            self.filename.setText(suggested_name(self.artist.text(), self.title.text()))

    def stop(self):
        self.stop_review = True
        self.reject()

    def closeEvent(self, event):
        self.stop_review = True
        super().closeEvent(event)

    def accept(self):
        try:
            validate_name(self.filename.text().strip())
        except ValueError as error:
            QMessageBox.warning(self, "Filename needs editing", str(error))
            return
        super().accept()


class RenameDialog(QDialog):
    def __init__(
        self,
        proposals,
        parent=None,
        *,
        auto_review=False,
        songs=None,
        accepted=None,
        use_metadata=True,
        on_mark_correct=None,
        on_apply=None,
        on_refresh=None,
    ):
        super().__init__(parent)
        self.proposals = proposals
        self.by_id = {proposal["id"]: proposal for proposal in proposals}
        self.source_songs = songs if songs is not None else list(proposals)
        self.accepted = dict(accepted or {})
        self.on_mark_correct = on_mark_correct
        self.on_apply = on_apply
        self.on_refresh = on_refresh
        self.busy = False
        self.last_result = None
        self.last_error = None
        self.last_scan = None
        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setSizeGripEnabled(True)
        self.setWindowTitle("Rename library files — review and approve")
        self.resize(1200, 760)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Choose one reason to change only that detail, or choose all suggested changes. "
            "Metadata is optional. Changing the reason or metadata option rebuilds proposals and clears approvals/edits. "
            "Mark correct saves an exception for the current filename immediately. "
            "Only approved rows are renamed; categories and song IDs are preserved."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        options = QHBoxLayout()
        options.addWidget(QLabel("Reason to apply"))
        self.reason_combo = QComboBox()
        self.reason_combo.addItem("All suggested changes", None)
        for key, label in RENAME_RULES.items():
            self.reason_combo.addItem(label + " only", key)
        options.addWidget(self.reason_combo, 1)
        self.use_metadata = QCheckBox("Use Artist/Title metadata")
        self.use_metadata.setChecked(use_metadata)
        options.addWidget(self.use_metadata)
        self.show_correct = QCheckBox("Show marked correct")
        options.addWidget(self.show_correct)
        self.show_missing = QCheckBox("Show files needing details")
        options.addWidget(self.show_missing)
        layout.addLayout(options)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.table = QTableWidget(len(proposals), 4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setHorizontalHeaderLabels(
            ["Approve", "Current filename", "Proposed filename (editable)", "Change / reason"]
        )
        self.table.setColumnWidth(0, 85)
        self.table.setColumnWidth(1, 300)
        self.table.setColumnWidth(2, 330)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, proposal in enumerate(proposals):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
            )
            check.setData(Qt.ItemDataRole.UserRole, proposal["id"])
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            for column, value in enumerate(
                [
                    proposal["path"],
                    proposal["proposed"],
                    change_reason(proposal["path"], proposal["proposed"]),
                ],
                1,
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column != 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            self.table.setRowHidden(row, not bool(proposal["proposed"]))
        layout.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(125)
        layout.addWidget(self.detail)
        batch_line = QHBoxLayout()
        self.batch_status = QLabel("Ready. Apply a batch to refresh the remaining candidates here.")
        self.batch_status.setTextFormat(Qt.TextFormat.PlainText)
        self.batch_status.setWordWrap(True)
        batch_line.addWidget(self.batch_status, 1)
        self.batch_details = QPushButton("Last batch details…")
        self.batch_details.setEnabled(False)
        self.batch_details.clicked.connect(self.show_batch_details)
        batch_line.addWidget(self.batch_details)
        layout.addLayout(batch_line)
        group_actions = QHBoxLayout()
        self.selection_label = QLabel("Shift selects a range; Ctrl selects separate rows.")
        group_actions.addWidget(self.selection_label, 1)
        self.approve_selected_button = QPushButton("Approve selected")
        self.approve_selected_button.clicked.connect(lambda: self.set_selected_approval(True))
        self.unapprove_selected_button = QPushButton("Unapprove selected")
        self.unapprove_selected_button.clicked.connect(lambda: self.set_selected_approval(False))
        group_actions.addWidget(self.approve_selected_button)
        group_actions.addWidget(self.unapprove_selected_button)
        self.mark_correct_button = QPushButton("Mark selected correct")
        self.mark_correct_button.clicked.connect(self.mark_selected_correct)
        group_actions.addWidget(self.mark_correct_button)
        layout.addLayout(group_actions)
        actions = QHBoxLayout()
        for label, action in [
            ("Review one by one…", self.review_one_by_one),
            ("Select all", self.approve_valid),
            ("Clear all", self.clear),
        ]:
            button = QPushButton(label)
            button.clicked.connect(action)
            actions.addWidget(button)
        self.missing_button = QPushButton()
        self.missing_button.clicked.connect(lambda: self.review_one_by_one(missing_only=True))
        actions.addWidget(self.missing_button)
        layout.addLayout(actions)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        self.box.button(QDialogButtonBox.StandardButton.Apply).setText("Rename approved files")
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText("Close")
        self.box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.box.rejected.connect(self.reject)
        self.refresh_button = self.box.addButton("Rescan", QDialogButtonBox.ButtonRole.ActionRole)
        self.refresh_button.setEnabled(bool(on_refresh))
        self.refresh_button.clicked.connect(self.request_refresh)
        layout.addWidget(self.box)
        self.table.currentCellChanged.connect(self.show_details)
        self.table.itemChanged.connect(self.row_changed)
        self.table.selectionModel().selectionChanged.connect(self.update_selection)
        self.table.model().layoutChanged.connect(self.refresh_visibility)
        self.sync_reasons()
        self.reason_combo.currentIndexChanged.connect(lambda: self.rebuild())
        self.use_metadata.toggled.connect(lambda: self.rebuild())
        self.show_correct.toggled.connect(lambda: self.rebuild(preserve=True))
        self.show_missing.toggled.connect(self.refresh_visibility)
        self.table.setSortingEnabled(True)
        self.table.sortItems(3, Qt.SortOrder.AscendingOrder)
        self.update_summary()
        first = next((row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)), None)
        if first is not None:
            self.table.setCurrentCell(first, 1)
        else:
            self.detail.setPlainText(
                "No matching proposals. Change the metadata option or show marked-correct files to review exceptions."
                if not self.table.rowCount()
                else "These files do not have enough artist/title information for a proposed filename. "
                "Choose Enter missing artist/title to review them. No filename is guessed."
            )
        if auto_review and proposals:
            QTimer.singleShot(0, self.review_one_by_one)
        self.update_selection()

    def rebuild(self, *, preserve=False):
        old_reason = self.reason_combo.currentData()
        self.sync_reasons()
        if old_reason != self.reason_combo.currentData():
            preserve = False
        previous = (
            {
                self.proposal_at(row)["id"]: (
                    self.table.item(row, 2).text(),
                    self.table.item(row, 0).checkState(),
                )
                for row in range(self.table.rowCount())
            }
            if preserve
            else {}
        )
        items = build_proposals(
            self.source_songs,
            use_metadata=self.use_metadata.isChecked(),
            reason=self.reason_combo.currentData(),
            accepted=self.accepted,
            include_accepted=self.show_correct.isChecked(),
        )
        with self.stable_rows(), QSignalBlocker(self.table), QSignalBlocker(self.table.selectionModel()):
            self.proposals = items
            self.by_id = {item["id"]: item for item in items}
            self.table.clearContents()
            self.table.setRowCount(len(items))
            for row, proposal in enumerate(items):
                check = QTableWidgetItem()
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if not proposal.get("accepted"):
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                check.setFlags(flags)
                check.setData(Qt.ItemDataRole.UserRole, proposal["id"])
                name, state = previous.get(proposal["id"], (proposal["proposed"], Qt.CheckState.Unchecked))
                if proposal.get("accepted"):
                    state = Qt.CheckState.Unchecked
                check.setCheckState(state)
                self.table.setItem(row, 0, check)
                reason_text = (
                    proposal["reason"]
                    if name == proposal["proposed"]
                    else "Custom filename edit: " + change_reason(proposal["path"], name)
                )
                for column, value in enumerate([proposal["path"], name, reason_text], 1):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column != 2 or proposal.get("accepted"):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row, column, item)
        self.refresh_visibility()
        self.update_summary()
        first = next((row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)), None)
        if first is not None:
            self.table.setCurrentCell(first, 1)
        else:
            self.detail.setPlainText(
                "No matching proposals. Change the reason or metadata option, or show marked-correct files to review exceptions."
            )

    def sync_reasons(self):
        current = self.reason_combo.currentData()
        counts = {}
        for song in self.source_songs:
            if song.get("missing"):
                continue
            mark = self.accepted.get(song["id"])
            if mark and (mark["path"], mark["audio_hash"]) == (song["path"], song.get("audio_sha256", "")):
                continue
            for rule in rename_operations(song, use_metadata=self.use_metadata.isChecked()):
                counts[rule] = counts.get(rule, 0) + 1
        with QSignalBlocker(self.reason_combo):
            self.reason_combo.clear()
            self.reason_combo.addItem("All suggested changes", None)
            for rule, label in RENAME_RULES.items():
                if counts.get(rule):
                    self.reason_combo.addItem(f"{label} only ({counts[rule]:,})", rule)
            index = self.reason_combo.findData(current)
            self.reason_combo.setCurrentIndex(max(0, index))

    def proposal_at(self, row):
        return self.by_id[self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)]

    @contextmanager
    def stable_rows(self):
        sorting = self.table.isSortingEnabled()
        if sorting:
            self.table.setSortingEnabled(False)
        try:
            yield
        finally:
            if sorting:
                self.table.setSortingEnabled(True)

    def refresh_visibility(self, *_):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            if item is not None:
                self.table.setRowHidden(
                    row, not bool(item.text().strip()) and not self.show_missing.isChecked()
                )
        self.show_details(self.table.currentRow())
        self.update_selection()

    def selected_rows(self):
        return [
            index.row()
            for index in self.table.selectionModel().selectedRows()
            if not self.table.isRowHidden(index.row())
        ]

    def update_selection(self, *_):
        count = len(self.selected_rows())
        self.selection_label.setText(f"{count:,} selected · Shift: range · Ctrl: separate rows")
        self.approve_selected_button.setEnabled(bool(count) and not self.busy)
        self.unapprove_selected_button.setEnabled(bool(count) and not self.busy)
        self.mark_correct_button.setEnabled(bool(count) and not self.busy)
        all_correct = bool(count) and all(
            self.proposal_at(row).get("accepted") for row in self.selected_rows()
        )
        self.mark_correct_button.setText("Review selected again" if all_correct else "Mark selected correct")

    def mark_selected_correct(self):
        items = [self.proposal_at(row) for row in self.selected_rows()]
        if not items:
            return
        correct = not all(item.get("accepted") for item in items)
        try:
            if self.on_mark_correct:
                self.accepted = self.on_mark_correct(items, correct)
            else:
                for item in items:
                    if correct:
                        self.accepted[item["id"]] = {
                            "path": item["path"],
                            "audio_hash": item.get("audio_sha256", ""),
                        }
                    else:
                        self.accepted.pop(item["id"], None)
        except Exception as error:
            QMessageBox.warning(self, "Could not save correct-name marks", str(error))
            return
        self.rebuild(preserve=True)

    def set_selected_approval(self, approve):
        rows = self.selected_rows()
        with self.stable_rows(), QSignalBlocker(self.table):
            for row in rows:
                if self.proposal_at(row).get("accepted"):
                    continue
                if approve:
                    try:
                        validate_name(self.table.item(row, 2).text())
                    except ValueError:
                        continue
                self.table.item(row, 0).setCheckState(
                    Qt.CheckState.Checked if approve else Qt.CheckState.Unchecked
                )
        self.update_summary()

    def update_summary(self):
        missing = sum(not self.table.item(row, 2).text().strip() for row in range(self.table.rowCount()))
        marked = sum(bool(self.proposal_at(row).get("accepted")) for row in range(self.table.rowCount()))
        approved = len(self.approved())
        self.summary.setText(
            f"{self.table.rowCount() - missing - marked:,} proposed filenames \u00b7 {approved:,} approved \u00b7 "
            f"{marked:,} marked correct shown \u00b7 "
            f"{missing} {'needs' if missing == 1 else 'need'} artist/title details"
        )
        self.missing_button.setText(f"Enter missing artist/title ({missing})\u2026")
        self.missing_button.setEnabled(bool(missing) and not self.busy)
        apply = self.box.button(QDialogButtonBox.StandardButton.Apply)
        apply.setText(f"Rename approved files ({approved:,})")
        apply.setEnabled(bool(approved) and not self.busy)

    def row_changed(self, item):
        if item.column() == 2:
            with self.stable_rows(), QSignalBlocker(self.table):
                row = item.row()
                proposal = self.proposal_at(row)
                reason = (
                    proposal.get("reason", change_reason(proposal["path"], item.text().strip()))
                    if item.text().strip() == proposal["proposed"]
                    else "Custom filename edit: " + change_reason(proposal["path"], item.text().strip())
                )
                self.table.item(row, 3).setText(reason)
                self.table.item(row, 3).setToolTip(reason)
                self.table.item(row, 2).setToolTip(item.text())
                self.table.setRowHidden(
                    row, not bool(item.text().strip()) and not self.show_missing.isChecked()
                )
                if not item.text().strip():
                    self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
            self.show_details(item.row())
        self.update_summary()

    def show_details(self, row, *_):
        if not 0 <= row < self.table.rowCount():
            return
        item = self.proposal_at(row)
        proposed = self.table.item(row, 2).text()
        reason = self.table.item(row, 3).text()
        text = (
            f"Current: {item['path']}\nProposed: {proposed or '(needs artist/title details)'}\n"
            f"Change: {reason}\nSource: {item['basis']}"
        )
        if item.get("only_reason"):
            text += "\nApplying: " + RENAME_RULES[item["only_reason"]] + " only"
            if proposed != item["proposed"]:
                text += " (custom filename edit overrides this rule)"
            text += "\nOther available changes left for later: " + (
                ", ".join(item.get("other_reasons", [])) or "None"
            )
        if "spac" in reason.casefold() or any(
            "spac" in label.casefold() for label in item.get("other_reasons", [])
        ):
            text += (
                "\nSpaces shown as \u00b7: "
                + item["path"].replace(" ", "\u00b7")
                + "\nProposed spaces: "
                + proposed.replace(" ", "\u00b7")
            )
        self.detail.setPlainText(text)

    def approve_valid(self):
        with self.stable_rows(), QSignalBlocker(self.table):
            for row in range(self.table.rowCount()):
                if self.proposal_at(row).get("accepted"):
                    continue
                try:
                    validate_name(self.table.item(row, 2).text())
                    self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
                except ValueError:
                    self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self.update_summary()

    def clear(self):
        with self.stable_rows(), QSignalBlocker(self.table):
            for row in range(self.table.rowCount()):
                self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self.update_summary()

    def review_one_by_one(self, checked=False, *, missing_only=False):
        approvals = [self.table.item(row, 0) for row in range(self.table.rowCount())]
        for approval in approvals:
            row = approval.row()
            proposal = self.proposal_at(row)
            if proposal.get("accepted"):
                continue
            if missing_only and self.table.item(row, 2).text().strip():
                continue
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                continue
            dialog = RenameOneDialog({**proposal, "proposed": self.table.item(row, 2).text()}, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                with self.stable_rows():
                    row = approval.row()
                    self.table.item(row, 2).setText(dialog.filename.text().strip())
                    approval.setCheckState(Qt.CheckState.Checked)
            if dialog.stop_review:
                break

    def approved(self):
        return [
            {
                "id": self.proposal_at(row)["id"],
                "name": self.table.item(row, 2).text().strip(),
                "sha256": self.proposal_at(row)["sha256"],
            }
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
            and not self.proposal_at(row).get("accepted")
        ]

    def accept(self):
        approved = self.approved()
        if self.busy or not approved:
            return
        if not self.on_apply:
            super().accept()
            return
        self.set_busy(True)
        self.batch_status.setText("Renaming approved files, then rescanning…")
        try:
            self.on_apply(approved)
        except Exception as error:
            self.fail_action(str(error))

    def request_refresh(self):
        if self.busy or not self.on_refresh:
            return
        self.set_busy(True)
        self.batch_status.setText("Rescanning library…")
        try:
            self.on_refresh()
        except Exception as error:
            self.fail_action(str(error))

    def set_busy(self, busy):
        self.busy = busy
        for widget in [
            self.table,
            self.reason_combo,
            self.use_metadata,
            self.show_correct,
            self.show_missing,
            *self.findChildren(QPushButton),
        ]:
            widget.setEnabled(not busy)
        if not busy:
            self.update_selection()
            self.update_summary()
            self.refresh_button.setEnabled(bool(self.on_refresh))
            self.batch_details.setEnabled(bool(self.last_result or self.last_error or self.last_scan))

    def show_progress(self, message):
        if self.busy:
            self.batch_status.setText(message)

    def complete_action(self, context):
        self.source_songs = context["songs"]
        self.accepted = context["accepted"]
        self.last_error = context.get("scan_error")
        self.last_scan = context.get("scan")
        result = context.get("result")
        if result is not None:
            self.last_result = result
        self.rebuild()
        self.set_busy(False)
        if result is None:
            message = "Scan complete. Remaining candidates are shown above."
        else:
            message = f"Renamed {result['renamed']:,} · Failed/not attempted {len(result.get('errors', [])):,}. Remaining candidates are shown above."
            if result.get("warnings"):
                message += " See last batch details for warnings."
        if context.get("scan_error"):
            message += " The rescan reported an error; see details before continuing."
        elif self.last_scan and self.last_scan.get("errors"):
            message += f" Rescan: {len(self.last_scan['errors'])} file errors; see details."
        self.batch_status.setText(message)

    def fail_action(self, details):
        self.last_error = str(details)
        # A failed connection or recovery can leave committed renames; do not reuse stale paths.
        self.source_songs = []
        self.rebuild()
        self.set_busy(False)
        message = self.last_error.strip().splitlines() or ["Unknown error"]
        self.batch_status.setText(
            "Operation could not finish: " + message[-1] + ". Use Rescan to reload candidates."
        )

    def show_batch_details(self):
        box = QMessageBox(self)
        box.setWindowTitle("Last rename batch")
        box.setText(self.batch_status.text())
        box.setDetailedText(
            self.last_error
            or json.dumps({"rename": self.last_result, "scan": self.last_scan}, ensure_ascii=False, indent=2)
        )
        box.exec()

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)


class ComparisonDialog(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.pairs = result["pairs"]
        self.setWindowTitle("Review possible duplicate recordings and quality")
        self.resize(1160, 700)
        layout = QVBoxLayout(self)
        upgrades = sum(pair["possible_upgrade"] for pair in self.pairs)
        label = QLabel(
            f"{len(self.pairs)} possible matches · {upgrades} possible encoding-quality upgrades. "
            "Nothing is replaced or deleted until you choose an action and apply it."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QTableWidget(len(self.pairs), 4)
        self.table.setHorizontalHeaderLabels(
            ["New recording", "Current recording", "Quality / match", "Action"]
        )
        self.table.setColumnWidth(0, 280)
        self.table.setColumnWidth(1, 280)
        self.table.setColumnWidth(2, 300)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.actions = []
        for row, pair in enumerate(self.pairs):
            for column, text in enumerate([pair["incoming"]["path"], pair["current"]["path"], pair["text"]]):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
            action = QComboBox()
            action.addItems(
                [
                    "Keep both / decide later",
                    "Replace current with new; delete old",
                    "Keep current; delete new",
                ]
            )
            self.actions.append(action)
            self.table.setCellWidget(row, 3, action)
            action.activated.connect(lambda _, r=row: self.table.setCurrentCell(r, 0))
        layout.addWidget(self.table, 2)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(180)
        layout.addWidget(self.detail, 1)
        self.table.currentCellChanged.connect(self.show_details)
        if self.pairs:
            self.table.setCurrentCell(0, 0)
        note = QLabel(
            result["note"] + " Replacement retains the current filename and transfers its metadata, "
            "artwork, ratings, and categories onto the new audio. Approved deletions are permanent."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.verified = QCheckBox(
            "I checked that my selected metadata-only matches are the same recording/version"
        )
        layout.addWidget(self.verified)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        self.box.button(QDialogButtonBox.StandardButton.Apply).setText("Apply choices & review filenames")
        self.box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)

    def show_details(self, row, *_):
        if not 0 <= row < len(self.pairs):
            return
        pair = self.pairs[row]
        self.detail.setPlainText(
            f"NEW: {pair['incoming']['path']}\n{quality_text(pair['incoming'])}\n\n"
            f"CURRENT: {pair['current']['path']}\n{quality_text(pair['current'])}\n"
            f"Current categories: {pair['current'].get('categories') or 'Untagged'}\n\n"
            f"Match: {pair['match']}\n{pair['text']}"
        )

    def decisions(self):
        return [
            {"pair": pair, "action": action.currentIndex()}
            for pair, action in zip(self.pairs, self.actions)
            if action.currentIndex()
        ]

    def accept(self):
        decisions = self.decisions()
        ids = [
            song["id"]
            for decision in decisions
            for song in (decision["pair"]["incoming"], decision["pair"]["current"])
        ]
        if len(ids) != len(set(ids)):
            QMessageBox.warning(
                self,
                "Choose one action per recording",
                "One song is selected in more than one pair. Handle those alternatives in separate reviews.",
            )
            return
        if (
            any(not decision["pair"]["verified_audio"] for decision in decisions)
            and not self.verified.isChecked()
        ):
            QMessageBox.warning(
                self,
                "Verify the recording/version",
                "Metadata alone does not establish identical audio. "
                "Check the recordings and tick the verification box before replacing or deleting them.",
            )
            return
        super().accept()
