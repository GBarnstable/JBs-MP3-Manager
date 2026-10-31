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
from .dialogs import CategoryDialog, EqualizerDialog, FolderDialog, ImportPreview, MetadataDialog
from .library import DATA_DIR, Library
from .playback import Playback

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
            return (value or Path(song["path"]).stem) if field == "title" else value
        if role == Qt.ItemDataRole.ForegroundRole and song["missing"]:
            return QColor("#b1544b")
        return None


class WorkerSignals(QObject):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, root, operation):
        super().__init__()
        self.root = root
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self):
        try:
            last = [0.0]

            def progress(message):
                now = time.monotonic()
                if now - last[0] >= 0.1:
                    last[0] = now
                    self.signals.progress.emit(message)

            with Library(self.root) as library:
                result = self.operation(library, progress)
            self.signals.completed.emit(result)
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
        top = QHBoxLayout()
        title = QLabel("JB's MP3 Manager")
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.button("Create library…", self.create_library, True))
        top.addWidget(self.button("Open library…", self.choose_library))
        layout.addLayout(top)
        self.library_label = QLabel("One music folder. As many categories as you need.")
        self.library_label.setObjectName("muted")
        self.library_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.library_label)
        self.tools = QWidget()
        tools = QHBoxLayout(self.tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.addWidget(self.button("Import category folders…", self.import_categories))
        tools.addWidget(self.button("Rescan", self.scan))
        tools.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, artist, album, or filename…")
        self.search.setMinimumWidth(300)
        self.search.textChanged.connect(self.refresh_songs)
        tools.addWidget(self.search)
        layout.addWidget(self.tools)

        splitter = QSplitter()
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
        body.addWidget(self.heading)
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
        self.table.doubleClicked.connect(self.play_selected)
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
        audio.addWidget(self.button("Play", self.play_selected, True))
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
        help_menu = self.menuBar().addMenu("Help")
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
            try:
                library = Library(root, create=create, recursive=recursive)
            except Exception:
                lock.unlock()
                raise
            if self.playback:
                self.playback.stop()
            if self.library:
                self.library.close()
                self.library_lock.unlock()
            self.library, self.library_lock = library, lock
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
                "Select songs to categorize or edit. Exports use your selection, or the full visible list."
            )

    def run_job(self, operation, completed=None, *, quiet=False):
        if self.busy or not self.library:
            return
        self.busy = True
        self.central.setEnabled(False)
        self.menuBar().setEnabled(False)
        self.progress.show()
        worker = Worker(self.library.root, operation)
        self.worker = worker
        worker.signals.progress.connect(self.job_progress)
        worker.signals.completed.connect(lambda result: self.job_done(result, completed, quiet))
        worker.signals.failed.connect(self.job_failed)
        self.pool.start(worker)

    def job_progress(self, message):
        self.statusBar().showMessage(message)

    def end_job(self):
        self.busy = False
        self.central.setEnabled(True)
        self.menuBar().setEnabled(True)
        self.progress.hide()
        self.refresh()

    def job_done(self, result, completed, quiet):
        self.end_job()
        if completed:
            self.guarded(lambda: completed(result))
        elif not quiet:
            self.statusBar().showMessage("Done.", 10000)

    def job_failed(self, details):
        self.end_job()
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
            QMessageBox.information(self, "Category import complete", message)

        self.run_job(preview, reviewed)

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

    def export_selection(self):
        return self.selected_songs() or self.visible_songs()

    def export_playlist(self):
        songs = self.export_selection()
        if not self.library or not songs:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export playlist", "playlist.m3u8", "UTF-8 playlist (*.m3u8)"
        )
        if path:
            self.run_job(
                lambda lib, progress: str(lib.export_playlist([s["id"] for s in songs], path)),
                lambda result: self.statusBar().showMessage(f"Playlist saved: {result}"),
            )

    def export_files(self):
        songs = self.export_selection()
        if not self.library or not songs:
            return
        path = QFileDialog.getExistingDirectory(self, f"Export {len(songs)} song(s) to folder")
        if path:
            self.run_job(
                lambda lib, progress: lib.export_files([s["id"] for s in songs], path, progress),
                lambda result: self.show_report(
                    "MP3 export", {"copied": len(result["copied"]), "errors": result["errors"]}
                ),
            )

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
        if "updated" in report and "copied" not in report:
            box.setText(f"Updated: {report['updated']} · Errors: {len(report.get('errors', []))}")
        elif "copied" in report:
            box.setText(f"Copied: {report['copied']} · Errors: {len(report.get('errors', []))}")
        else:
            box.setText("Review the operation details below.")
        box.setDetailedText(json.dumps(report, ensure_ascii=False, indent=2))
        box.exec()

    def play_selected(self, *_):
        if not self.playback or not self.library:
            return
        songs = self.visible_songs()
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
            "Independent pitch shifting is not available in version 0.1.\n\n"
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
