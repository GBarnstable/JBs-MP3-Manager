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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .exports import EXPORT_DIR, ExportGroup, ExportRequest, safe_name
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
        if path.parent == self.root and path.name.casefold() == EXPORT_DIR.casefold():
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
        layout.addWidget(QLabel("10-band equalizer · Reset to flat restores the original playback level."))
        bands = QHBoxLayout()
        self.sliders = []
        self.value_labels = []
        gains = getattr(playback, "saved_gains", [0] * 10)
        for frequency, gain in zip(["60", "170", "310", "600", "1k", "3k", "6k", "12k", "14k", "16k"], gains):
            column = QVBoxLayout()
            value = QLabel(f"{gain:+d}")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.value_labels.append(value)
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
        reset.clicked.connect(self.reset)
        layout.addWidget(reset)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def update_equalizer(self):
        gains = [s.value() for s in self.sliders]
        self.playback.saved_gains = gains
        self.playback.set_equalizer(gains)

    def reset(self):
        for slider in self.sliders:
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
        for label in self.value_labels:
            label.setText("+0")
        self.update_equalizer()


class ExportDialog(QDialog):
    """Source selection and destination are explicit; table selection is only a suggestion."""

    def __init__(self, library, current_category, selected_songs, kind, parent=None):
        super().__init__(parent)
        self.library = library
        self.kind = kind
        self.manual_name = False
        self.setWindowTitle("Export playlists" if kind == "playlist" else "Export MP3 files")
        self.resize(730, 670)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Choose the songs to export and name the new playlist or folder. "
            "Exports are saved under your music library's Exports folder."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.source = QComboBox()
        self.source.addItems(["A category", "Choose songs from the current category", "Multiple categories"])
        layout.addWidget(self.source)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        category_page = QWidget()
        form = QFormLayout(category_page)
        self.category = QComboBox()
        choices = [("All songs", None), ("Untagged", "untagged")]
        choices += [(category["name"], category["id"]) for category in library.categories()]
        for label, key in choices:
            self.category.addItem(label, key)
        index = next((i for i, (_, key) in enumerate(choices) if key == current_category), 0)
        self.category.setCurrentIndex(index)
        form.addRow("Category", self.category)
        category_note = QLabel(
            "Exports every available song in this category, regardless of the song "
            "highlight or search on the main screen."
        )
        category_note.setWordWrap(True)
        form.addRow(category_note)
        self.pages.addWidget(category_page)

        song_page = QWidget()
        song_layout = QVBoxLayout(song_page)
        song_layout.setContentsMargins(0, 0, 0, 0)
        self.song_search = QLineEdit()
        self.song_search.setPlaceholderText("Find songs in the current category…")
        song_layout.addWidget(self.song_search)
        self.song_list = QListWidget()
        selected_ids = {song["id"] for song in selected_songs}
        category_key = None if current_category == "missing" else current_category
        for song in library.songs(category_key):
            title = song["title"] or Path(song["path"]).stem
            item = QListWidgetItem(f"{title} · {song['artist'] or 'Unknown artist'} · {song['path']}")
            item.setData(Qt.ItemDataRole.UserRole, song["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if song["id"] in selected_ids else Qt.CheckState.Unchecked
            )
            self.song_list.addItem(item)
        song_layout.addWidget(self.song_list)
        buttons = QHBoxLayout()
        for label, checked in [("Select visible songs", True), ("Clear all", False)]:
            button = QPushButton(label)
            button.clicked.connect(lambda _, value=checked: self.check_songs(value))
            buttons.addWidget(button)
        song_layout.addLayout(buttons)
        self.pages.addWidget(song_page)

        multiple_page = QWidget()
        multiple_layout = QVBoxLayout(multiple_page)
        multiple_layout.setContentsMargins(0, 0, 0, 0)
        self.category_list = QListWidget()
        for label, key in choices[1:]:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.category_list.addItem(item)
        multiple_layout.addWidget(self.category_list)
        self.layout_choice = QComboBox()
        noun = "playlist" if kind == "playlist" else "folder"
        self.layout_choice.addItems(
            [f"Combine into one {noun} (each song once)", f"Create one {noun} per category"]
        )
        multiple_layout.addWidget(self.layout_choice)
        self.pages.addWidget(multiple_page)

        self.summary = QLabel()
        layout.addWidget(self.summary)
        destination_form = QFormLayout()
        self.name = QLineEdit()
        destination_form.addRow("Playlist name" if kind == "playlist" else "New folder name", self.name)
        layout.addLayout(destination_form)
        self.destination = QLabel()
        self.destination.setWordWrap(True)
        self.destination.setTextFormat(Qt.TextFormat.PlainText)
        self.destination.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.destination)
        note = QLabel(
            "New folders are created automatically. A repeated export gets a new name. "
            "Songs in Exports are excluded from library scans."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.box.button(QDialogButtonBox.StandardButton.Save).setText("Export")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)
        self.source.currentIndexChanged.connect(self.source_changed)
        self.category.currentIndexChanged.connect(self.update_plan)
        self.song_list.itemChanged.connect(self.update_plan)
        self.category_list.itemChanged.connect(self.update_plan)
        self.layout_choice.currentIndexChanged.connect(self.update_plan)
        self.name.textEdited.connect(self.name_edited)
        self.name.textChanged.connect(self.update_destination)
        self.song_search.textChanged.connect(self.filter_songs)
        self.update_plan()

    def source_changed(self, index):
        self.pages.setCurrentIndex(index)
        self.update_plan()

    def name_edited(self, *_):
        self.manual_name = True
        self.update_destination()

    def filter_songs(self, text):
        for i in range(self.song_list.count()):
            item = self.song_list.item(i)
            item.setHidden(text.casefold() not in item.text().casefold())

    def check_songs(self, checked):
        self.song_list.blockSignals(True)
        for index in range(self.song_list.count()):
            item = self.song_list.item(index)
            if not checked or not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.song_list.blockSignals(False)
        self.update_plan()

    def groups(self):
        if self.source.currentIndex() == 0:
            songs = self.library.songs(self.category.currentData())
            return [ExportGroup(self.category.currentText(), [song["id"] for song in songs])]
        if self.source.currentIndex() == 1:
            selected = [
                self.song_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.song_list.count())
                if self.song_list.item(i).checkState() == Qt.CheckState.Checked
            ]
            return [ExportGroup("Selected songs", selected)]
        groups = []
        for index in range(self.category_list.count()):
            item = self.category_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                groups.append(
                    ExportGroup(
                        item.text(),
                        [song["id"] for song in self.library.songs(item.data(Qt.ItemDataRole.UserRole))],
                    )
                )
        return groups

    def request(self):
        name = self.name.text()
        if self.kind == "playlist" and name.lower().endswith((".m3u8", ".m3u")):
            name = name.rsplit(".", 1)[0]
        return ExportRequest(
            self.kind,
            safe_name(name),
            self.groups(),
            self.source.currentIndex() == 2 and self.layout_choice.currentIndex() == 1,
        )

    def update_plan(self, *_):
        groups = self.groups()
        count = len({song_id for group in groups for song_id in group.song_ids})
        self.summary.setText(f"{len(groups)} group(s) · {count} unique songs")
        if not self.manual_name:
            self.name.setText(groups[0].name if len(groups) == 1 else "Category export")
        self.update_destination()

    def update_destination(self, *_):
        try:
            request = self.request()
            root = self.library.root / "Exports"
            if request.kind == "playlist":
                root = root / "Playlists"
            target = root / request.name
            if request.kind == "playlist" and not request.separate:
                target = target.with_name(target.name + ".m3u8")
            text = str(target) + (" / <category name>" if request.separate else "")
            self.destination.setText("Save to: " + text)
            self.box.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(request.unique_ids()))
        except ValueError as error:
            self.destination.setText(str(error))
            self.box.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)

    def accept(self):
        if self.request().unique_ids():
            super().accept()
