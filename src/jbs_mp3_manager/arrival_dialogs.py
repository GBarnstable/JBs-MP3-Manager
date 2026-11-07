from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
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
from .naming import suggested_name, validate_name


class RenameOneDialog(QDialog):
    def __init__(self, proposal, parent=None):
        super().__init__(parent)
        self.stop_review = False
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
        self.filename = QLineEdit(proposal["proposed"])
        form.addRow("Artist", self.artist)
        form.addRow("Song title", self.title)
        form.addRow("New filename", self.filename)
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
    def __init__(self, proposals, parent=None, *, auto_review=False):
        super().__init__(parent)
        self.proposals = proposals
        self.setWindowTitle("Rename library files — review and approve")
        self.resize(1080, 660)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Names use Artist/Title metadata first, then artist--title filenames where needed. "
            "Only checked rows are renamed. Edit proposed names in the table or review songs one by one. "
            "Categories and song IDs are preserved. Old playlists may need re-export after filenames change."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(len(proposals), 4)
        self.table.setHorizontalHeaderLabels(
            ["Approve", "Current filename", "Proposed filename (editable)", "Source"]
        )
        self.table.setColumnWidth(0, 65)
        self.table.setColumnWidth(1, 350)
        self.table.setColumnWidth(2, 400)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, proposal in enumerate(proposals):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            for column, value in enumerate([proposal["path"], proposal["proposed"], proposal["basis"]], 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column != 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for label, action in [
            ("Review one by one…", self.review_one_by_one),
            ("Approve valid suggestions", self.approve_valid),
            ("Clear approvals", self.clear),
        ]:
            button = QPushButton(label)
            button.clicked.connect(action)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        self.box.button(QDialogButtonBox.StandardButton.Apply).setText("Rename approved files")
        self.box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)
        if auto_review and proposals:
            QTimer.singleShot(0, self.review_one_by_one)

    def approve_valid(self):
        for row in range(self.table.rowCount()):
            try:
                validate_name(self.table.item(row, 2).text())
                self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            except ValueError:
                pass

    def clear(self):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)

    def review_one_by_one(self):
        for row, proposal in enumerate(self.proposals):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                continue
            dialog = RenameOneDialog({**proposal, "proposed": self.table.item(row, 2).text()}, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.table.item(row, 2).setText(dialog.filename.text().strip())
                self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            if dialog.stop_review:
                break

    def approved(self):
        return [
            {
                "id": proposal["id"],
                "name": self.table.item(row, 2).text().strip(),
                "sha256": proposal["sha256"],
            }
            for row, proposal in enumerate(self.proposals)
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def accept(self):
        try:
            for proposal in self.approved():
                validate_name(proposal["name"])
        except ValueError as error:
            QMessageBox.warning(self, "Filename needs editing", str(error))
            return
        if self.approved():
            super().accept()


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
