from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QFileSystemWatcher,
    QLockFile,
    QModelIndex,
    QObject,
    QRunnable,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .arrival_dialogs import ComparisonDialog, RenameDialog
from .delete_dialog import DeleteFilesDialog
from .dialogs import (
    CategoryDialog,
    EqualizerDialog,
    ExportDialog,
    FolderDialog,
    ImportPreview,
    MetadataDialog,
)
from .discrepancies import apply as apply_discrepancies
from .discrepancy_dialog import DiscrepancyDialog
from .duplicates import archive_inventory, purge_archives, recover_pending
from .duplicates import merge as merge_duplicates
from .duplicates import review as duplicate_review
from .exports import execute_export
from .filename_tag_dialog import FilenameTagDialog
from .filename_tags import apply as apply_filename_tags
from .fingerprinting import apply_decisions, find_candidates, index_library
from .library import DATA_DIR, Library
from .naming import build_proposals, mark_names_correct, rename_files, rename_marks
from .playback import Playback
from .review_dialog import ReviewDialog
from .table_columns import configure_columns
from .trash import trash_untagged

STYLE = """
QWidget { font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 13px; color: #24354a; }
QMainWindow, QDialog { background: #f4f6f8; }
QMenuBar, QMenu { background: white; }
QPushButton { background: white; border: 1px solid #cdd6df; border-radius: 5px;
              padding: 7px 12px; }
QPushButton:hover { background: #e8f4f1; border-color: #308b7c; }
QPushButton:disabled { color: #9ba7b3; background: #eef1f4; }
QPushButton#primary { background: #137e70; color: white; border-color: #137e70; }
QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #cdd6df;
                               padding: 6px; border-radius: 4px; }
QListWidget, QTableView, QTableWidget { background: white; border: 1px solid #dce2e8;
                                      selection-background-color: #dceeea; selection-color: #153f39; }
QListWidget::item { padding: 9px; }
QHeaderView::section { background: #eaf0f4; border: 0; padding: 9px; font-weight: 600; }
QTableView { gridline-color: #eef1f4; alternate-background-color: #f9fbfc; }
QLabel#title { font-size: 25px; font-weight: 650; color: #15364b; }
QLabel#muted { color: #627387; }
QFrame#player { background: white; border: 1px solid #dce2e8; border-radius: 7px; }
QProgressBar { border: 0; background: #dce2e8; max-height: 4px; }
QProgressBar::chunk { background: #137e70; }
"""


def clock_text(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class SongModel(QAbstractTableModel):
    columns = [
        ("title", "Title"),
        ("artist", "Artist"),
        ("album", "Album"),
        ("duration", "Time"),
        ("categories", "Categories"),
        ("genre", "Genre"),
        ("bitrate", "Bitrate"),
        ("size", "File size"),
        ("sample_rate", "Sample rate"),
        ("path", "File"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def set_songs(self, songs):
        self.beginResetModel()
        self.rows = songs
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section][1]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        song = self.rows[index.row()]
        field = self.columns[index.column()][0]
        value = song.get(field) or ""
        if role == Qt.ItemDataRole.UserRole:
            return value
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if field == "duration":
                return clock_text(value or 0)
            if field == "bitrate":
                return f"{value / 1000:.0f} kbps" if value else "—"
            if field == "sample_rate":
                return f"{value / 1000:g} kHz" if value else "—"
            if field == "size":
                return f"{value / (1024 * 1024):.2f} MiB" if value else "—"
            return (value or Path(song["path"]).stem) if field == "title" else value
        if role == Qt.ItemDataRole.ForegroundRole and song["missing"]:
            return QColor("#b1544b")
        return None


class WorkerSignals(QObject):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()


class OperationCancelled(Exception):
    pass


class Worker(QRunnable):
    def __init__(self, root, operation):
        super().__init__()
        self.root = root
        self.operation = operation
        self.signals = WorkerSignals()
        self.cancel_requested = False

    def run(self):
        try:
            last = [0.0]

            def progress(message):
                if self.cancel_requested:
                    raise OperationCancelled()
                now = time.monotonic()
                if now - last[0] >= 0.1:
                    last[0] = now
                    self.signals.progress.emit(message)

            with Library(self.root) as library:
                result = self.operation(library, progress)
            self.signals.completed.emit(result)
        except OperationCancelled:
            self.signals.cancelled.emit()
        except Exception:
            self.signals.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self, *, restore=True, enable_audio=True):
        super().__init__()
        self.setWindowTitle(f"JB's MP3 Manager · {__version__}")
        self.resize(1320, 840)
        self.setMinimumSize(980, 660)
        self.library = None
        self.library_lock = None
        self.busy = False
        self.worker = None
        self._offer_pending = False
        self.current_category = None
        self.settings = QSettings("JBsMP3Manager", "Desktop")
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.playback = None
        self.audio_error = "Audio disabled for automated interface checks."
        if enable_audio:
            try:
                self.playback = Playback(self)
            except Exception as error:
                self.audio_error = f"Playback needs 64-bit VLC 3.x. Library tools are available. ({error})"
        self.build_ui()
        self.build_menu()
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.schedule_scan)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(2500)
        self.debounce.timeout.connect(self.auto_scan)
        self.rescan_timer = QTimer(self)
        self.rescan_timer.setInterval(60000)
        self.rescan_timer.timeout.connect(self.auto_scan)
        self.rescan_timer.start()
        self.refresh()
        if restore:
            previous = self.settings.value("last_library", "")
            if previous and (Path(previous) / DATA_DIR / "library.sqlite3").is_file():
                QTimer.singleShot(0, lambda: self.open_library(previous))

    def button(self, label, callback, primary=False):
        button = QPushButton(label)
        button.clicked.connect(callback)
        if primary:
            button.setObjectName("primary")
        return button

    def build_ui(self):
        self.central = QWidget()
        self.setCentralWidget(self.central)
        layout = QVBoxLayout(self.central)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(12)
        self.header = QWidget()
        top = QHBoxLayout(self.header)
        top.setContentsMargins(0, 0, 0, 0)
        title = QLabel("JB's MP3 Manager")
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.button("Create library…", self.create_library, True))
        top.addWidget(self.button("Open library…", self.choose_library))
        layout.addWidget(self.header)
        self.library_label = QLabel("One music folder. As many categories as you need.")
        self.library_label.setObjectName("muted")
        self.library_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.library_label)
        self.tools = QWidget()
        tools = QHBoxLayout(self.tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.addWidget(self.button("Import category folders…", self.import_categories))
        tools.addWidget(self.button("Rescan", self.scan))
        tools.addWidget(self.button("Review issues…", self.review_issues))
        tools.addWidget(self.button("Review filenames / tags…", self.review_discrepancies))
        tools.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, artist, album, or filename…")
        self.search.setMinimumWidth(300)
        self.search.textChanged.connect(self.refresh_songs)
        tools.addWidget(self.search)
        layout.addWidget(self.tools)

        splitter = QSplitter()
        self.browser = splitter
        side = QWidget()
        sidebar = QVBoxLayout(side)
        sidebar.setContentsMargins(0, 0, 8, 0)
        sidebar.addWidget(QLabel("YOUR LIBRARY"))
        self.category_list = QListWidget()
        self.category_list.currentItemChanged.connect(self.category_changed)
        sidebar.addWidget(self.category_list)
        controls = QHBoxLayout()
        controls.addWidget(self.button("+ Category", self.new_category))
        controls.addWidget(self.button("Rename", self.rename_category))
        sidebar.addLayout(controls)
        sidebar.addWidget(self.button("Delete selected category", self.delete_category))
        side.setMinimumWidth(230)
        splitter.addWidget(side)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(4, 0, 0, 0)
        self.heading = QLabel("Welcome")
        self.heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        heading_row = QHBoxLayout()
        heading_row.addWidget(self.heading)
        heading_row.addStretch()
        self.delete_files_button = self.button("Delete selected MP3s…", self.delete_untagged_files)
        self.delete_files_button.hide()
        heading_row.addWidget(self.delete_files_button)
        body.addLayout(heading_row)
        self.count = QLabel("Create a library by selecting the folder containing your master MP3 files.")
        self.count.setObjectName("muted")
        self.count.setWordWrap(True)
        body.addWidget(self.count)
        self.model = SongModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        configure_columns(self.table, "songs")
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(33)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate([220, 160, 170, 65, 180, 100]):
            self.table.setColumnWidth(column, width)
        self.table.doubleClicked.connect(self.play_category)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        body.addWidget(self.table)
        self.detail = QLabel("Select a song to inspect its metadata. Double-click to play the visible list.")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.detail)
        self.song_tools = QWidget()
        actions = QHBoxLayout(self.song_tools)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.button("Categorize…", self.categorize))
        actions.addWidget(self.button("Edit metadata…", self.edit_metadata))
        actions.addWidget(self.button("Metadata from filenames…", self.metadata_from_filenames))
        actions.addWidget(self.button("Clear selection", self.table.clearSelection))
        actions.addStretch()
        actions.addWidget(self.button("Export playlist…", self.export_playlist))
        actions.addWidget(self.button("Export MP3s…", self.export_files))
        body.addWidget(self.song_tools)
        splitter.addWidget(content)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([245, 1040])
        layout.addWidget(splitter, 1)

        player_frame = QFrame()
        player_frame.setObjectName("player")
        player_layout = QVBoxLayout(player_frame)
        self.now_playing = QLabel("Ready to play" if self.playback else self.audio_error)
        self.now_playing.setWordWrap(True)
        self.now_playing.setTextFormat(Qt.TextFormat.PlainText)
        player_layout.addWidget(self.now_playing)
        transport = QHBoxLayout()
        self.audio_controls = QWidget()
        audio = QHBoxLayout(self.audio_controls)
        audio.setContentsMargins(0, 0, 0, 0)
        audio.addWidget(self.button("Previous", lambda: self.playback.previous()))
        self.play_button = self.button("Play", self.play_selected, True)
        audio.addWidget(self.play_button)
        audio.addWidget(self.button("Play category", self.play_category))
        audio.addWidget(self.button("Pause / Resume", lambda: self.playback.toggle_pause()))
        audio.addWidget(self.button("Next", lambda: self.playback.next()))
        audio.addWidget(self.button("Stop", lambda: self.playback.stop()))
        transport.addWidget(self.audio_controls)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.sliderReleased.connect(
            lambda: self.playback.seek(self.seek.value()) if self.playback else None
        )
        transport.addWidget(self.seek, 1)
        self.position = QLabel("0:00 / 0:00")
        transport.addWidget(self.position)
        player_layout.addLayout(transport)
        self.audio_options = QWidget()
        options = QHBoxLayout(self.audio_options)
        options.setContentsMargins(0, 0, 0, 0)
        shuffle = QCheckBox("Shuffle")
        shuffle.setToolTip("Applies when you start a new playback list.")
        shuffle.toggled.connect(lambda value: setattr(self.playback, "shuffle", value))
        options.addWidget(shuffle)
        options.addWidget(QLabel("Repeat"))
        repeat = QComboBox()
        repeat.addItems(["Off", "All", "One"])
        repeat.currentTextChanged.connect(lambda value: setattr(self.playback, "repeat", value))
        options.addWidget(repeat)
        options.addWidget(QLabel("Crossfade"))
        fade = QSpinBox()
        fade.setRange(0, 12)
        fade.setSuffix(" s")
        fade.setValue(3)
        fade.valueChanged.connect(lambda value: setattr(self.playback, "crossfade", value))
        options.addWidget(fade)
        options.addWidget(QLabel("Speed"))
        speed = QComboBox()
        speed.addItems(["0.5×", "0.75×", "1.0×", "1.25×", "1.5×", "2.0×"])
        speed.setCurrentIndex(2)
        speed.currentTextChanged.connect(lambda value: self.playback.set_rate(float(value.rstrip("×"))))
        options.addWidget(speed)
        options.addWidget(self.button("Equalizer…", self.equalizer))
        options.addStretch()
        options.addWidget(QLabel("Volume"))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(80)
        volume.setMaximumWidth(140)
        volume.valueChanged.connect(lambda value: self.playback.set_volume(value))
        options.addWidget(volume)
        player_layout.addWidget(self.audio_options)
        self.audio_controls.setEnabled(self.playback is not None)
        self.audio_options.setEnabled(self.playback is not None)
        layout.addWidget(player_frame)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.cancel_button = self.button("Cancel audio indexing", self.cancel_job)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.statusBar().showMessage("Local music. Virtual categories. No account required.")
        if self.playback:
            self.playback.track_changed.connect(self.track_changed)
            self.playback.position_changed.connect(self.update_position)
            self.playback.error.connect(lambda message: QMessageBox.warning(self, "Playback", message))

    def build_menu(self):
        file_menu = self.menuBar().addMenu("Library")
        self.library_actions = []
        for text, callback, shortcut, requires in [
            ("Create library…", self.create_library, "Ctrl+N", False),
            ("Open library…", self.choose_library, "Ctrl+O", False),
            ("Rescan", self.scan, "F5", True),
            ("Verify all files…", self.verify, "", True),
            ("Import category folders…", self.import_categories, "", True),
            ("Review import issues / duplicates…", self.review_issues, "", True),
            ("Rename selected / category / all files…", self.review_names, "", True),
            ("Update selected metadata from filenames…", self.metadata_from_filenames, "", True),
            ("Review filename / metadata discrepancies…", self.review_discrepancies, "", True),
            ("Delete Untagged MP3 files…", self.choose_untagged_deletion, "", True),
            ("Review new songs…", self.review_new_songs, "", True),
            ("Compare selected recordings…", self.compare_selected, "", True),
            ("Build / refresh audio fingerprint index…", self.build_audio_index, "", True),
            ("Delete previously archived duplicate copies…", self.clean_duplicate_archives, "", True),
            ("Back up category database…", self.backup, "", True),
            ("Open reports folder", self.open_reports, "", True),
            ("Open music folder", self.open_music_folder, "", True),
        ]:
            action = QAction(text, self)
            action.triggered.connect(callback)
            if shortcut:
                action.setShortcut(shortcut)
            file_menu.addAction(action)
            if requires:
                self.library_actions.append(action)
            if callback == self.choose_untagged_deletion:
                self.delete_files_action = action
                action.setEnabled(False)
        help_menu = self.menuBar().addMenu("Help")
        self.auto_review_action = QAction("Review new song names and duplicates automatically", self)
        self.auto_review_action.setCheckable(True)
        self.auto_review_action.setChecked(True)
        self.auto_review_action.toggled.connect(
            lambda value: self.library.set_setting("auto_review_new", value) if self.library else None
        )
        file_menu.addAction(self.auto_review_action)
        action = QAction("About / moving your library", self)
        action.triggered.connect(self.about)
        help_menu.addAction(action)

    def guarded(self, function):
        try:
            return function()
        except Exception as error:
            QMessageBox.warning(self, "Operation could not finish", str(error))
            return None

    def create_library(self):
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(self, "Select the master MP3 folder")
        if not path:
            return
        recursive = False
        if not (Path(path) / DATA_DIR / "library.sqlite3").is_file():
            box = QMessageBox(self)
            box.setWindowTitle("Master folder scan")
            box.setText(
                "Scan MP3s directly in this folder?\n\n"
                "Leave subfolders excluded if they contain your old category copies."
            )
            check = QCheckBox("Also scan subfolders (only if they contain master files)")
            box.setCheckBox(check)
            box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Ok:
                return
            recursive = check.isChecked()
        self.open_library(path, create=True, recursive=recursive)

    def choose_library(self):
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(
            self, "Open master music folder (including its category database)"
        )
        if path:
            self.open_library(path)

    def open_library(self, path, *, create=False, recursive=False, scan=True):
        def operation():
            root = Path(path).resolve(strict=True)
            if self.library and self.library.root == root:
                if scan:
                    self.scan()
                return
            if not create and not (root / DATA_DIR / "library.sqlite3").is_file():
                raise ValueError("No library found. Use Create library for a new folder.")
            (root / DATA_DIR).mkdir(exist_ok=True)
            lock = QLockFile(str(root / DATA_DIR / "desktop.lock"))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                raise ValueError("This library is already open in another instance. Close that window first.")
            library = None
            try:
                library = Library(root, create=create, recursive=recursive)
                recover_pending(library)
            except Exception:
                if library:
                    library.close()
                lock.unlock()
                raise
            if self.playback:
                self.playback.stop()
            if self.library:
                self.library.close()
                self.library_lock.unlock()
            self.library, self.library_lock = library, lock
            self.auto_review_action.blockSignals(True)
            self.auto_review_action.setChecked(library.setting("auto_review_new", True))
            self.auto_review_action.blockSignals(False)
            self._offer_pending = True
            watched = self.watcher.directories()
            if watched:
                self.watcher.removePaths(watched)
            self.watcher.addPath(str(root))
            self.settings.setValue("last_library", str(root))
            self.current_category = None
            self.search.clear()
            self.refresh()
            if scan:
                self.scan()

        self.guarded(operation)

    def refresh(self):
        active = self.library is not None
        self.tools.setEnabled(active)
        self.category_list.setEnabled(active)
        self.song_tools.setEnabled(active)
        for action in getattr(self, "library_actions", []):
            action.setEnabled(active)
        self.category_list.blockSignals(True)
        self.category_list.clear()
        if active:
            self.library_label.setText(str(self.library.root))
            for name, key in [("All songs", None), ("Untagged", "untagged"), ("Missing files", "missing")]:
                count = len(self.library.songs(key))
                self.add_category_item(f"{name}   {count}", key)
            for category in self.library.categories():
                self.add_category_item(f"{category['name']}   {category['count']}", category["id"])
            match = None
            for index in range(self.category_list.count()):
                item = self.category_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == self.current_category:
                    match = item
                    break
            if match is None:
                self.current_category = None
                match = self.category_list.item(0)
            self.category_list.setCurrentItem(match)
        self.category_list.blockSignals(False)
        self.refresh_songs()

    def add_category_item(self, label, key):
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.category_list.addItem(item)

    def category_changed(self, current, previous):
        if current:
            self.current_category = current.data(Qt.ItemDataRole.UserRole)
            self.refresh_songs()

    def refresh_songs(self, *_):
        if not self.library:
            return
        songs = self.library.songs(self.current_category, self.search.text())
        self.model.set_songs(songs)
        title = {None: "All songs", "untagged": "Untagged", "missing": "Missing files"}.get(
            self.current_category
        )
        if title is None:
            title = next(
                (c["name"] for c in self.library.categories() if c["id"] == self.current_category),
                "All songs",
            )
        self.heading.setText(title)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        duration = sum(s["duration"] for s in songs)
        self.count.setText(
            f"{len(songs):,} songs · {duration / 3600:.1f} hours"
            + (" · New songs appear here until categorized." if self.current_category == "untagged" else "")
        )
        self.selection_changed()

    def selected_songs(self):
        return [
            self.model.rows[self.proxy.mapToSource(index).row()]
            for index in self.table.selectionModel().selectedRows()
        ]

    def visible_songs(self):
        return [
            self.model.rows[self.proxy.mapToSource(self.proxy.index(row, 0)).row()]
            for row in range(self.proxy.rowCount())
        ]

    def selection_changed(self, *_):
        selected = self.selected_songs()
        can_delete = bool(self.library and self.current_category == "untagged" and selected and not self.busy)
        self.delete_files_button.setVisible(bool(self.library and self.current_category == "untagged"))
        self.delete_files_button.setEnabled(can_delete)
        if hasattr(self, "delete_files_action"):
            self.delete_files_action.setEnabled(bool(self.library and not self.busy))
        if hasattr(self, "play_button"):
            self.play_button.setText("Play selection" if selected else "Play view")
        if len(selected) == 1:
            song = selected[0]
            self.detail.setText(
                " · ".join(
                    [
                        song["path"],
                        f"Year: {song['year'] or '—'}",
                        f"Track: {song['track'] or '—'}",
                        f"Genre: {song['genre'] or '—'}",
                        f"Categories: {song.get('categories') or 'Untagged'}",
                    ]
                )
            )
            self.detail.setTextFormat(Qt.TextFormat.PlainText)
        elif selected:
            self.detail.setText(
                f"{len(selected)} songs selected. Category and metadata changes apply to this selection."
            )
        else:
            self.detail.setText(
                "Play uses highlighted songs. Play category uses the category. Exports let you choose songs or categories."
            )

    def run_job(self, operation, completed=None, *, quiet=False, cancellable=False, failed=None):
        if self.busy or not self.library:
            return
        self.busy = True
        for widget in (self.header, self.tools, self.browser):
            widget.setEnabled(False)
        self.menuBar().setEnabled(False)
        self.progress.show()
        self.cancel_button.setVisible(cancellable)
        worker = Worker(self.library.root, operation)
        self.worker = worker
        worker.signals.progress.connect(self.job_progress)
        worker.signals.completed.connect(lambda result: self.job_done(result, completed, quiet))
        worker.signals.failed.connect(lambda details: self.job_failed(details, failed))
        worker.signals.cancelled.connect(self.job_cancelled)
        self.pool.start(worker)

    def job_progress(self, message):
        self.statusBar().showMessage(message)

    def end_job(self):
        self.busy = False
        for widget in (self.header, self.tools, self.browser):
            widget.setEnabled(True)
        self.menuBar().setEnabled(True)
        self.progress.hide()
        self.cancel_button.hide()
        self.refresh()

    def job_done(self, result, completed, quiet):
        self.end_job()
        if completed:
            self.guarded(lambda: completed(result))
        elif not quiet:
            self.statusBar().showMessage("Done.", 10000)

    def job_failed(self, details, failed=None):
        self.end_job()
        if failed:
            self.guarded(lambda: failed(details))
            return
        message = details.strip().splitlines()[-1]
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            "Operation could not finish",
            message,
            QMessageBox.StandardButton.Ok,
            self,
        )
        box.setDetailedText(details)
        box.exec()

    def cancel_job(self):
        if self.worker and self.busy:
            self.worker.cancel_requested = True
            self.statusBar().showMessage(
                "Stopping after the current audio file; completed index entries are saved."
            )

    def job_cancelled(self):
        self.end_job()
        self.statusBar().showMessage("Audio indexing stopped. Run it again to continue from the saved index.")

    def stop_for_file_changes(self):
        if self.playback:
            self.playback.stop()
            for deck in self.playback.decks:
                deck.set_media(None)

    def build_audio_index(self):
        self.run_job(
            lambda library, progress: index_library(library, progress),
            lambda result: self.show_report("Audio index", result),
            cancellable=True,
        )

    def review_discrepancies(self, checked=False, *, song_ids=None):
        if not self.library or self.busy:
            return
        category_scope = False
        if song_ids is None:
            selected = self.selected_songs()
            options = ([f"Selected songs ({len(selected)})"] if selected else []) + [
                "Current category",
                "Entire library",
            ]
            scope, accepted = QInputDialog.getItem(
                self, "Filename / metadata review", "Choose songs to compare:", options, editable=False
            )
            if not accepted:
                return
            if scope.startswith("Selected songs"):
                song_ids = {song["id"] for song in selected}
            elif scope == "Current category":
                category_scope = True
        category = self.current_category

        def read_scope(library):
            songs = library.songs(category if category_scope else None)
            return songs if song_ids is None else [song for song in songs if song["id"] in song_ids]

        def operate(library, progress, decisions=None):
            result = apply_discrepancies(library, decisions, progress) if decisions else None
            scan = library.scan(progress=progress)
            return {"songs": read_scope(library), "result": result, "scan": scan}

        def loaded(context):
            def launch(decisions=None):
                if decisions:
                    self.stop_for_file_changes()
                    if self.playback:
                        self.playback.queue = []
                        self.playback.index = 0
                self.run_job(
                    lambda lib, progress: operate(lib, progress, decisions),
                    dialog.complete_action,
                    failed=dialog.fail_action,
                )
                if self.worker:
                    self.worker.signals.progress.connect(dialog.show_progress)

            dialog = DiscrepancyDialog(context["songs"], self, on_apply=launch, on_refresh=launch)
            dialog.last_details = context
            dialog.details_button.setEnabled(True)
            dialog.exec()

        self.run_job(operate, loaded)

    def review_names(self, checked=False, *, song_ids=None, auto_review=False):
        if not self.library or self.busy:
            return
        if song_ids is None:
            selected = self.selected_songs()
            options = ([f"Selected songs ({len(selected)})"] if selected else []) + [
                "Current category",
                "Entire library",
            ]
            scope, accepted = QInputDialog.getItem(
                self, "Rename scope", "Choose songs to review:", options, editable=False
            )
            if not accepted:
                return
            if scope.startswith("Selected songs"):
                song_ids = [song["id"] for song in selected]
            elif scope == "Current category":
                song_ids = [song["id"] for song in self.library.songs(self.current_category)]

        def loaded(context):
            items = context["items"]
            if not context["can_review"]:
                if not auto_review:
                    QMessageBox.information(
                        self, "Filename review complete", "No proposed filename changes in this selection."
                    )
                return

            def launch(approved=None):
                if approved:
                    self.stop_for_file_changes()

                def operate(library, progress):
                    result = rename_files(library, approved, progress) if approved else None
                    scan_error = None
                    scan_report = None
                    try:
                        progress("Rescanning library after rename…" if approved else "Rescanning library…")
                        scan_report = library.scan(progress)
                    except Exception as error:
                        scan_error = str(error)
                        if result is not None:
                            result["warnings"].append("Rescan: " + scan_error)
                    songs = (
                        library.songs()
                        if song_ids is None
                        else [library.song(song_id) for song_id in song_ids]
                    )
                    return {
                        "songs": songs,
                        "accepted": rename_marks(library),
                        "result": result,
                        "scan_error": scan_error,
                        "scan": scan_report,
                    }

                self.run_job(operate, dialog.complete_action, failed=dialog.fail_action)
                if self.worker:
                    self.worker.signals.progress.connect(dialog.show_progress)

            dialog = RenameDialog(
                items,
                self,
                auto_review=auto_review,
                songs=context["songs"],
                accepted=context["accepted"],
                use_metadata=context["use_metadata"],
                on_mark_correct=lambda songs, correct: mark_names_correct(self.library, songs, correct),
                on_apply=launch,
                on_refresh=launch,
            )
            dialog.exec()
            self.guarded(
                lambda: self.library.set_setting("rename_use_metadata", dialog.use_metadata.isChecked())
            )

        def prepare(library, progress):
            songs = library.songs() if song_ids is None else [library.song(song_id) for song_id in song_ids]
            accepted = rename_marks(library)
            use_metadata = library.setting("rename_use_metadata", auto_review)
            items = build_proposals(songs, use_metadata=use_metadata, accepted=accepted)
            if auto_review:
                pending = {item["id"] for item in items}
                unchanged = set(song_ids or ()) - pending
                with library.db:
                    library.db.executemany(
                        "DELETE FROM new_arrivals WHERE song_id=?", [(song_id,) for song_id in unchanged]
                    )
            can_review = bool(items)
            if not auto_review and not can_review:
                can_review = bool(
                    build_proposals(songs, use_metadata=not use_metadata, accepted=accepted)
                    or build_proposals(
                        songs, use_metadata=use_metadata, accepted=accepted, include_accepted=True
                    )
                )
            return {
                "songs": songs,
                "accepted": accepted,
                "items": items,
                "use_metadata": use_metadata,
                "can_review": can_review,
            }

        self.run_job(prepare, loaded)

    def review_new_songs(self, checked=False, *, song_ids=None):
        if not self.library or self.busy:
            return
        if song_ids is None:
            song_ids = [
                row[0]
                for row in self.library.db.execute(
                    "SELECT n.song_id FROM new_arrivals n "
                    "JOIN songs s ON s.id=n.song_id WHERE s.missing=0 ORDER BY n.rowid LIMIT 20"
                )
            ]
        song_ids = list(song_ids)[:20]
        if not song_ids:
            QMessageBox.information(
                self,
                "Review new songs",
                "No pending new songs. Use Library → Compare selected recordings to compare highlighted songs.",
            )
            return

        def continue_names(keepers=()):
            wanted = set(song_ids) | set(keepers)
            active = [song["id"] for song in self.library.songs() if song["id"] in wanted]
            self.review_names(song_ids=active, auto_review=True)

        def loaded(result):
            if not result["pairs"]:
                continue_names()
                return
            dialog = ComparisonDialog(result, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            decisions = dialog.decisions()
            if not decisions:
                continue_names()
                return
            self.stop_for_file_changes()

            def applied(summary):
                self.show_report("Recording comparison", summary)
                continue_names(summary["keeper_ids"])

            self.run_job(lambda library, progress: apply_decisions(library, decisions, progress), applied)

        self.run_job(lambda library, progress: find_candidates(library, song_ids, progress), loaded)

    def compare_selected(self):
        selected = self.selected_songs()
        if not selected:
            QMessageBox.information(
                self, "Compare recordings", "Select one or more songs to compare with the library."
            )
            return
        self.review_new_songs(song_ids=[song["id"] for song in selected])

    def clean_duplicate_archives(self):
        if not self.library or self.busy:
            return
        batches = self.guarded(lambda: archive_inventory(self.library))
        if not batches:
            QMessageBox.information(
                self, "Delete archived duplicate copies", "No archived duplicate audio files were found."
            )
            return
        files = [file for batch in batches for file in batch["files"]]
        total = sum(file["size"] for file in files) / (1024 * 1024)
        box = QMessageBox(self)
        box.setWindowTitle("Permanently delete archived duplicate audio")
        box.setText(
            f"Delete {len(files)} archived MP3 copies ({total:.1f} MiB)?\n\n"
            "Their current master recordings will be checked first. Database backups and manifests remain. "
            "Deleted audio copies cannot be restored from those database backups."
        )
        box.setDetailedText("\n".join(file["original"] for file in files))
        delete = box.addButton("Delete archived audio", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() == delete:
            self.run_job(
                lambda library, progress: purge_archives(library, [batch["id"] for batch in batches]),
                lambda result: self.show_report("Archive cleanup", result),
            )

    def schedule_scan(self, *_):
        self.debounce.start()

    def auto_scan(self):
        if not self.library or self.busy or QApplication.activeModalWidget():
            return
        self.scan(quiet=True)

    def scan(self, checked=False, *, quiet=False, verify=False):
        def done(report):
            text = (
                f"Scan complete: {report['added']} new · {report['updated']} updated · "
                f"{report['renamed']} moved/renamed · {report['missing']} missing · "
                f"{len(report['duplicates'])} duplicate groups · {len(report['errors'])} errors"
            )
            self.statusBar().showMessage(text)
            ids = report.get("new_song_ids", [])
            if self._offer_pending and not ids:
                ids = [
                    row[0]
                    for row in self.library.db.execute(
                        "SELECT n.song_id FROM new_arrivals n "
                        "JOIN songs s ON s.id=n.song_id WHERE s.missing=0 ORDER BY n.rowid LIMIT 20"
                    )
                ]
            self._offer_pending = False
            if ids and self.library.setting("auto_review_new", True):
                self.review_new_songs(song_ids=ids)
                return
            if not quiet and (report["errors"] or report["duplicates"]):
                self.show_report("Scan report", report)

        self.run_job(lambda lib, progress: lib.scan(progress, verify=verify), done, quiet=quiet)

    def verify(self):
        self.scan(verify=True)

    def new_category(self):
        if not self.library:
            return
        name, accepted = QInputDialog.getText(self, "New category", "Category name:")
        if accepted:
            result = self.guarded(lambda: self.library.add_category(name))
            if result is not None:
                self.current_category = result
                self.refresh()

    def rename_category(self):
        if not self.library or not isinstance(self.current_category, int):
            return
        current = next(c for c in self.library.categories() if c["id"] == self.current_category)
        name, accepted = QInputDialog.getText(self, "Rename category", "Category name:", text=current["name"])
        if accepted:
            self.guarded(lambda: self.library.rename_category(self.current_category, name))
            self.refresh()

    def delete_category(self):
        if not self.library or not isinstance(self.current_category, int):
            return
        if (
            QMessageBox.question(
                self,
                "Delete category",
                "Remove this category and its memberships? Songs remain in your music folder.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.guarded(lambda: self.library.delete_category(self.current_category))
            self.current_category = None
            self.refresh()

    def categorize(self):
        songs = self.selected_songs()
        if not self.library or not songs:
            return
        if not self.library.categories():
            self.new_category()
            return
        dialog = CategoryDialog(self.library.categories(), len(songs), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.guarded(
                lambda: self.library.assign(
                    [s["id"] for s in songs],
                    dialog.category_ids(),
                    remove=dialog.operation.currentIndex() == 1,
                )
            )
            self.refresh()

    def import_categories(self):
        if not self.library:
            return
        dialog = FolderDialog(self.library.root, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        folders = dialog.paths()

        def preview(lib, progress):
            lib.scan(progress, verify=True)
            report = lib.preview_import(folders, progress)
            report["report_path"] = str(lib.save_report(report, "preview"))
            return report

        def reviewed(report):
            if ImportPreview(report, self).exec() == QDialog.DialogCode.Accepted:
                self.run_job(lambda lib, progress: lib.apply_import(report, progress), imported)

        def imported(report):
            message = (
                f"Imported {report['matched_files']} matched copies into virtual categories.\n\n"
                f"Unresolved copies: {report['unresolved_files']}\n"
                + (
                    "Keep the source folders until the unresolved files are accounted for.\n\n"
                    if report["unresolved_files"]
                    else "Review the report before deleting old category folders.\n\n"
                )
                + f"Report: {report['report_path']}"
            )
            box = QMessageBox(self)
            box.setWindowTitle("Category import complete")
            box.setText(message)
            review_button = None
            if report["unresolved_files"]:
                review_button = box.addButton("Review unresolved issues…", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()
            if review_button and box.clickedButton() == review_button:
                self.review_issues(report_path=report["report_path"], rescan=False)

        self.run_job(preview, reviewed)

    def review_issues(self, checked=False, *, report_path=None, rescan=True):
        if not self.library or self.busy:
            return

        def load(library, progress):
            recover_pending(library)
            if rescan:
                library.scan(progress)
            return duplicate_review(library, report_path)

        def loaded(snapshot):
            dialog = ReviewDialog(snapshot, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if dialog.action == "load":
                self.review_issues(report_path=dialog.chosen_report, rescan=False)
                return
            choices = dialog.choices()
            if self.playback:
                self.playback.stop()
                for deck in self.playback.decks:
                    deck.set_media(None)
            delete_extras = dialog.delete_extras.isChecked()
            self.run_job(
                lambda library, progress: merge_duplicates(
                    library, snapshot, choices, progress, delete_extras=delete_extras
                ),
                finished,
            )

        def finished(result):
            message = (
                f"Merged {result['groups_merged']} duplicate groups.\n"
                f"Deleted {result['files_deleted']} extra copies, retained "
                f"{result['files_archived'] - result['files_deleted']} archived copies, and removed {result['records_removed']} "
                "redundant library records.\n"
                f"Resolved {result['issues_resolved']} import entries; "
                f"{result['remaining_issues']} remain.\n\n"
                f"Database backup and recovery manifest:\n{result['backup_folder']}"
            )
            if result["delete_errors"]:
                message += "\nSome obsolete copies could not be deleted and were retained:\n" + "\n".join(
                    str(error) for error in result["delete_errors"]
                )
            if result["audit_error"]:
                message += (
                    "\nThe final manifest could not be updated; the committed database records the merge."
                )
            QMessageBox.information(self, "Duplicate merge complete", message)
            self.review_issues(report_path=result["report_path"], rescan=False)

        self.run_job(load, loaded)

    def edit_metadata(self):
        songs = self.selected_songs()
        if not self.library or not songs:
            return
        dialog = MetadataDialog(songs, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.values():
            if self.playback:
                self.playback.stop()
                for deck in self.playback.decks:
                    deck.set_media(None)  # Release Windows file handles before replacing tagged files.
            self.run_job(
                lambda lib, progress: lib.edit_metadata([s["id"] for s in songs], dialog.values(), progress),
                lambda result: self.show_report("Metadata update", result),
            )

    def metadata_from_filenames(self):
        if not self.library or self.busy:
            return
        songs = self.selected_songs()
        if not songs:
            QMessageBox.information(
                self, "Select songs", "Select the songs whose metadata you want to update."
            )
            return
        dialog = FilenameTagDialog(songs, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updates = dialog.updates()
        if updates:
            self.stop_for_file_changes()
            self.run_job(
                lambda lib, progress: apply_filename_tags(lib, updates, progress),
                lambda result: self.show_report("Metadata from filenames", result),
            )

    def confirm_file_deletion(self, songs):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete selected MP3 files?")
        box.setText(f"Move {len(songs)} selected Untagged MP3 file(s) to the Recycle Bin?")
        names = "\n".join(song["path"] for song in songs[:8])
        if len(songs) > 8:
            names += f"\n…and {len(songs) - 8} more (see Show Details)."
        box.setInformativeText(
            "The actual files will be removed from your music folder and library. "
            "You can recover them from the Windows Recycle Bin.\n\n" + names
        )
        box.setDetailedText("\n".join(str(self.library.path(song)) for song in songs))
        recycle = box.addButton("Move to Recycle Bin", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() == recycle

    def choose_untagged_deletion(self):
        if not self.library or self.busy:
            return
        dialog = DeleteFilesDialog(
            self.library.root,
            self.library.songs("untagged"),
            [song["id"] for song in self.selected_songs()],
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.recycle_approved_files(dialog.selected_songs())

    def delete_untagged_files(self):
        if not self.library or self.busy:
            return
        if self.current_category != "untagged":
            self.choose_untagged_deletion()
            return
        songs = self.selected_songs()
        if not songs:
            self.choose_untagged_deletion()
            return
        if self.confirm_file_deletion(songs):
            self.recycle_approved_files(songs)

    def recycle_approved_files(self, songs):
        if not songs or not self.library or self.busy:
            return
        self.stop_for_file_changes()
        if self.playback:
            self.playback.queue = []
            self.playback.index = 0
        self.run_job(
            lambda lib, progress: trash_untagged(lib, songs, progress),
            lambda result: self.show_report("Delete Untagged MP3 files", result),
        )

    def export_playlist(self):
        self.choose_export("playlist")

    def export_files(self):
        self.choose_export("files")

    def choose_export(self, kind):
        if not self.library or self.busy:
            return
        dialog = ExportDialog(self.library, self.current_category, self.selected_songs(), kind, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            request = dialog.request()
            self.run_job(lambda lib, progress: execute_export(lib, request, progress), self.export_completed)

    def export_completed(self, result):
        box = QMessageBox(self)
        box.setWindowTitle("Export complete")
        box.setText(
            f"MP3 copies: {result['copied']} · Playlists: {len(result['playlists'])} · "
            f"Errors: {len(result['errors'])}\n\nSaved to: {result['destination']}"
        )
        box.setDetailedText(json.dumps(result, ensure_ascii=False, indent=2))
        open_folder = box.addButton("Open export folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() == open_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(result["destination"]))

    def backup(self):
        if not self.library:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Back up categories and metadata cache",
            "jbs-library-backup.sqlite3",
            "SQLite database (*.sqlite3)",
        )
        if path:
            self.run_job(
                lambda lib, progress: str(lib.backup(path)),
                lambda result: self.statusBar().showMessage(f"Database backup saved: {result}"),
            )

    def open_reports(self):
        if self.library:
            path = self.library.data / "reports"
            path.mkdir(exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_music_folder(self):
        if self.library:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.root)))

    def show_report(self, title, report):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        if "renamed" in report and "report_path" in report:
            box.setText(
                f"Renamed: {report['renamed']} · Already correct: {report.get('unchanged', 0)}"
                f" · Failed / not attempted: {len(report.get('errors', []))}"
                + (f" · Warnings: {len(report['warnings'])}" if report.get("warnings") else "")
            )
            info = (
                "Completed renames were kept. Categories and metadata are preserved. "
                "Show Details lists any failed files and warnings. Re-export playlists that use old filenames."
            )
            if report.get("report_path"):
                info += f"\n\nSaved report: {report['report_path']}"
            box.setInformativeText(info)
        elif "updated" in report and "copied" not in report:
            box.setText(f"Updated: {report['updated']} · Errors: {len(report.get('errors', []))}")
        elif "copied" in report:
            box.setText(f"Copied: {report['copied']} · Errors: {len(report.get('errors', []))}")
        elif "indexed" in report:
            box.setText(
                f"Audio fingerprints ready: {report['indexed']} · Errors: {len(report.get('errors', []))}"
            )
        elif "replaced" in report:
            box.setText(
                f"Recordings replaced: {report['replaced']} · Incoming duplicates deleted: {report['discarded']}"
                f" · Errors or retained copies: {len(report.get('errors', []))}"
            )
        elif "trashed" in report:
            box.setText(
                f"Moved to Recycle Bin: {report['trashed']} · Errors: {len(report.get('errors', []))}"
            )
            box.setInformativeText(
                "Files that could not be recycled were left in place. "
                "To undo a deletion, restore the file from the Recycle Bin and rescan the library.\n\n"
                f"Saved report: {report.get('report_path', '')}"
            )
        elif "deleted" in report:
            box.setText(
                f"Archived copies deleted: {report['deleted']} · Errors: {len(report.get('errors', []))}"
            )
        else:
            box.setText("Review the operation details below.")
        box.setDetailedText(json.dumps(report, ensure_ascii=False, indent=2))
        box.exec()

    def play_selected(self, *_):
        self.start_playback(self.selected_songs() or self.visible_songs())

    def play_category(self, *_):
        if self.library:
            self.start_playback(self.library.songs(self.current_category))

    def start_playback(self, songs):
        if not self.playback or not self.library:
            return
        if not songs:
            return
        selected = self.selected_songs()
        first_id = selected[0]["id"] if selected else songs[0]["id"]
        tracks = [
            {**song, "absolute_path": str(self.library.path(song))} for song in songs if not song["missing"]
        ]
        if tracks:
            first = next((i for i, song in enumerate(tracks) if song["id"] == first_id), 0)
            self.playback.set_queue(tracks, first)

    def track_changed(self, song):
        self.now_playing.setText(
            f"{song['title'] or Path(song['path']).stem}  ·  {song['artist'] or 'Unknown artist'}"
        )

    def update_position(self, position, length):
        if not self.seek.isSliderDown():
            self.seek.setRange(0, length)
            self.seek.setValue(position)
        self.position.setText(f"{clock_text(position / 1000)} / {clock_text(length / 1000)}")

    def equalizer(self):
        if self.playback:
            EqualizerDialog(self.playback, self).exec()

    def about(self):
        QMessageBox.information(
            self,
            "About JB's MP3 Manager",
            f"Version {__version__}\n\n"
            "Categories live in .jbs-mp3-manager inside your master music folder. "
            "Keep that companion folder with your MP3s.\n\n"
            "To move D: → C:, close the app, copy the entire master folder including "
            ".jbs-mp3-manager, then choose Open library at the new location. "
            "Mirror C: → D: with FreeFileSync while the app is closed.\n\n"
            "Playback uses VLC 3.x. Speed control uses VLC's tempo filter. "
            "Independent pitch shifting is not available in this version.\n\n"
            "New files are detected while running and on startup. Re-encoded audio "
            "requires manual review during category import.",
        )

    def closeEvent(self, event):
        if self.busy:
            self.statusBar().showMessage(
                "Please wait for the current file operation to finish before closing."
            )
            event.ignore()
            return
        if self.playback:
            self.playback.close()
        if self.library:
            self.library.close()
            self.library_lock.unlock()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("JB's MP3 Manager")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    smoke = "--smoke-test" in sys.argv
    require_audio = "--require-audio" in sys.argv
    if "--require-fingerprint" in sys.argv:
        import subprocess

        from .fingerprinting import tool_path

        binary = tool_path()
        if not binary:
            sys.exit(3)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        subprocess.run([binary, "-version"], check=True, capture_output=True, timeout=15, **options)
    window = MainWindow(restore=not smoke, enable_audio=not smoke or require_audio)
    if require_audio and window.playback is None:
        sys.exit(2)
    window.show()
    if smoke:
        screenshot = os.environ.get("JBS_SCREENSHOT")
        if screenshot:
            QTimer.singleShot(300, lambda: window.grab().save(screenshot))
        QTimer.singleShot(600, window.close)
        QTimer.singleShot(800, app.quit)
    sys.exit(app.exec())
