from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .media import FIELDS


class FolderDialog(QDialog):
    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = root
        self.setWindowTitle("Import existing category folders")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        message = QLabel(
            "Select the folders containing your category copies. Each selected folder becomes "
            "one virtual category. The next step previews matches against your master songs."
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        self.folders = QListWidget()
        self.folders.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.folders)
        buttons = QHBoxLayout()
        for label, function in (
            ("Add category folder…", self.add_folder),
            ("Find folders in…", self.find_folders),
            ("Remove selected", self.remove),
        ):
            button = QPushButton(label)
            button.clicked.connect(function)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.box.button(QDialogButtonBox.StandardButton.Ok).setText("Preview matches")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)

    def append(self, path: Path):
        path = path.resolve()
        if path == self.root or self.root.is_relative_to(path) or path.name.startswith("."):
            return
        if str(path) not in self.paths():
            self.folders.addItem(str(path))

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select a category folder", str(self.root.parent))
        if path:
            self.append(Path(path))

    def find_folders(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select the folder containing your category folders", str(self.root.parent)
        )
        if path:
            try:
                for child in sorted(Path(path).iterdir()):
                    if child.is_dir() and not child.is_symlink():
                        self.append(child)
            except OSError as error:
                QMessageBox.warning(self, "Cannot read folders", str(error))

    def remove(self):
        for item in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(item))

    def paths(self):
        return [self.folders.item(i).text() for i in range(self.folders.count())]

    def accept(self):
        if self.paths():
            super().accept()


class ImportPreview(QDialog):
    def __init__(self, report: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review category import")
        self.resize(1000, 560)
        layout = QVBoxLayout(self)
        entries = report["entries"]
        matched = sum(e["status"] in ("exact", "audio") for e in entries)
        unresolved = len(entries) - matched
        summary = QLabel(
            f"{len(report['categories'])} categories · {matched} matched copies · "
            f"{unresolved} unresolved copies\n"
            "Import adds the matched memberships. Unresolved files remain in the report for review. "
            "Your folders and MP3s are left in place."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        table = QTableWidget(len(entries), 4)
        table.setHorizontalHeaderLabels(["Category", "Category copy", "Match", "Master / explanation"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, entry in enumerate(entries):
            for column, value in enumerate(
                [
                    entry["category"],
                    entry["source"],
                    entry["status"],
                    entry.get("master") or entry.get("error") or ", ".join(entry.get("candidates", [])),
                ]
            ):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(row, column, item)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(1, 310)
        table.setColumnWidth(0, 130)
        layout.addWidget(table)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.box.button(QDialogButtonBox.StandardButton.Ok).setText("Import matched memberships")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)


class CategoryDialog(QDialog):
    def __init__(self, categories, selection_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Categorize songs")
        self.resize(420, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Apply to {selection_count} selected song(s)."))
        self.operation = QComboBox()
        self.operation.addItems(["Add selected categories", "Remove selected categories"])
        layout.addWidget(self.operation)
        self.list = QListWidget()
        for category in categories:
            item = QListWidgetItem(category["name"])
            item.setData(Qt.ItemDataRole.UserRole, category["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def category_ids(self):
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]


class MetadataDialog(QDialog):
    def __init__(self, songs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit MP3 metadata")
        self.resize(530, 370)
        layout = QVBoxLayout(self)
        note = QLabel(
            f"Editing {len(songs)} song(s). Check the fields you want to write. "
            "A checked blank field clears that value. Artwork and other ID3 fields are preserved."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.fields = {}
        for field in FIELDS:
            values = {s[field] for s in songs}
            check = QCheckBox(field.title())
            check.setChecked(len(songs) == 1)
            edit = QLineEdit(next(iter(values)) if len(values) == 1 else "")
            if len(values) > 1:
                edit.setPlaceholderText("Multiple values")
            edit.textEdited.connect(lambda _, c=check: c.setChecked(True))
            self.fields[field] = (check, edit)
            form.addRow(check, edit)
        layout.addLayout(form)
        layout.addWidget(QLabel("Original ID3 tags are backed up inside the library before each edit."))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def values(self):
        return {field: edit.text() for field, (check, edit) in self.fields.items() if check.isChecked()}


class EqualizerDialog(QDialog):
    def __init__(self, playback, parent=None):
        super().__init__(parent)
        self.playback = playback
        self.setWindowTitle("Equalizer")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("10-band equalizer · positive boosts automatically lower the preamp."))
        bands = QHBoxLayout()
        self.sliders = []
        gains = getattr(playback, "saved_gains", [0] * 10)
        for frequency, gain in zip(["60", "170", "310", "600", "1k", "3k", "6k", "12k", "14k", "16k"], gains):
            column = QVBoxLayout()
            value = QLabel(f"{gain:+d}")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(value)
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(-12, 12)
            slider.setValue(gain)
            slider.setMinimumHeight(180)
            slider.valueChanged.connect(lambda v, label=value: label.setText(f"{v:+d}"))
            slider.valueChanged.connect(self.update_equalizer)
            self.sliders.append(slider)
            column.addWidget(slider, alignment=Qt.AlignmentFlag.AlignHCenter)
            column.addWidget(QLabel(frequency))
            bands.addLayout(column)
        layout.addLayout(bands)
        reset = QPushButton("Reset to flat")
        reset.clicked.connect(lambda: [s.setValue(0) for s in self.sliders])
        layout.addWidget(reset)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def update_equalizer(self):
        gains = [s.value() for s in self.sliders]
        self.playback.saved_gains = gains
        self.playback.set_equalizer(gains)
