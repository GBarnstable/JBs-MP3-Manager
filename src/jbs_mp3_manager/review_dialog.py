from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .media import quality_text
from .table_columns import configure_columns


class ReviewDialog(QDialog):
    def __init__(self, snapshot, parent=None):
        super().__init__(parent)
        self.snapshot = snapshot
        self.action = "merge"
        self.chosen_report = None
        self.setWindowTitle("Review import issues and duplicate songs")
        self.resize(1080, 700)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"{len(snapshot['groups'])} groups to review · "
            f"{snapshot['unresolved']} unresolved import copies · "
            f"{snapshot['resolved']} previously resolved"
        )
        summary.setStyleSheet("font-size: 17px; font-weight: 600;")
        layout.addWidget(summary)
        source = QLabel(
            "Import report: "
            + (snapshot["report_path"] or "No import report found; showing library duplicates.")
        )
        source.setWordWrap(True)
        layout.addWidget(source)
        note = QLabel(
            "Choose the file to keep in each group you want to resolve. The app combines all existing "
            "categories and pending categories from this report. Extra files are permanently deleted "
            "when the deletion option below is checked; uncheck it to archive them instead. "
            "Redundant library records are removed. The chosen file's metadata is kept unchanged."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        duplicate_page = QWidget()
        duplicates = QVBoxLayout(duplicate_page)
        self.table = QTableWidget(len(snapshot["groups"]), 4)
        configure_columns(self.table, "duplicate_groups")
        self.table.setHorizontalHeaderLabels(
            ["Copies", "File to keep", "Combined categories", "Import issues"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 65)
        self.table.setColumnWidth(1, 420)
        self.table.setColumnWidth(2, 350)
        self.selectors = {}
        for row, group in enumerate(snapshot["groups"]):
            available = [song for song in group["songs"] if not song["missing"]]
            self.table.setItem(row, 0, QTableWidgetItem(str(len(group["songs"]))))
            selector = QComboBox()
            selector.addItem("Skip this group — choose a file to merge", None)
            for song in available:
                selector.addItem(song["path"], song["id"])
            self.selectors[group["key"]] = selector
            self.table.setCellWidget(row, 1, selector)
            selector.activated.connect(lambda _, r=row: self.table.setCurrentCell(r, 1))
            text = ", ".join(group["categories"]) or "Untagged"
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            self.table.setItem(row, 2, item)
            self.table.setItem(row, 3, QTableWidgetItem(str(len(group["pending_entries"]))))
        duplicates.addWidget(self.table, 2)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        duplicates.addWidget(self.details, 1)
        self.table.currentCellChanged.connect(self.show_details)
        if snapshot["groups"]:
            self.table.setCurrentCell(0, 0)
        else:
            self.details.setPlainText(
                "No duplicate groups remain to merge. The Import issues tab shows each report entry. "
                "Unmatched or unreadable files need their individual file problem corrected."
            )
        tabs.addTab(duplicate_page, f"Duplicates ({len(snapshot['groups'])})")
        issues = QTableWidget(len(snapshot["issues"]), 4)
        configure_columns(issues, "import_issues")
        issues.setHorizontalHeaderLabels(
            ["Category", "Category copy", "Current status", "Candidates / resolution"]
        )
        issues.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        issues.horizontalHeader().setStretchLastSection(True)
        issues.setColumnWidth(0, 150)
        issues.setColumnWidth(1, 340)
        issues.setColumnWidth(2, 130)
        for row, entry in enumerate(snapshot["issues"]):
            detail = (
                entry.get("keeper_path")
                if entry["resolved"]
                else "; ".join(entry.get("candidates", [])) or entry.get("error", "No matching master file.")
            )
            for column, value in enumerate(
                [
                    entry["category"],
                    entry["source"],
                    "Resolved" if entry["resolved"] else entry["status"],
                    detail,
                ]
            ):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                issues.setItem(row, column, item)
        tabs.addTab(issues, f"Import issues ({snapshot['unresolved']} unresolved)")
        layout.addWidget(tabs, 1)
        self.plan = QLabel()
        self.plan.setWordWrap(True)
        layout.addWidget(self.plan)
        self.delete_extras = QCheckBox("Permanently delete unselected copies after a successful merge")
        self.delete_extras.setChecked(True)
        self.delete_extras.toggled.connect(self.update_plan)
        layout.addWidget(self.delete_extras)
        buttons = QHBoxLayout()
        choose_report = QPushButton("Choose another import report…")
        choose_report.clicked.connect(self.choose_report)
        buttons.addWidget(choose_report)
        open_backup = QPushButton("Open duplicate backups")
        open_backup.setEnabled(Path(snapshot["backup_root"]).is_dir())
        open_backup.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(snapshot["backup_root"]))
        )
        buttons.addWidget(open_backup)
        buttons.addStretch()
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close
        )
        self.box.button(QDialogButtonBox.StandardButton.Apply).setText("Back up & merge selected groups")
        self.box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.box.rejected.connect(self.reject)
        buttons.addWidget(self.box)
        layout.addLayout(buttons)
        for selector in self.selectors.values():
            selector.currentIndexChanged.connect(self.update_plan)
        self.update_plan()

    def choices(self):
        return {
            key: selector.currentData() for key, selector in self.selectors.items() if selector.currentData()
        }

    def update_plan(self, *_):
        choices = self.choices()
        selected = [group for group in self.snapshot["groups"] if group["key"] in choices]
        files = sum(
            sum(not song["missing"] and song["id"] != choices[group["key"]] for song in group["songs"])
            for group in selected
        )
        pending = sum(len(group["pending_entries"]) for group in selected)
        action = "deleted" if self.delete_extras.isChecked() else "archived"
        self.box.button(QDialogButtonBox.StandardButton.Apply).setText(
            "Merge & delete extra copies"
            if self.delete_extras.isChecked()
            else "Back up & merge selected groups"
        )
        self.plan.setText(
            f"Selected: {len(selected)} groups · {files} files will be {action} · "
            f"{pending} import issues will be resolved.\n"
            "A complete database backup and recovery manifest are saved before the merge. "
            "Original category folders are left in place."
        )
        self.box.button(QDialogButtonBox.StandardButton.Apply).setEnabled(bool(choices))

    def show_details(self, row, *_):
        if not 0 <= row < len(self.snapshot["groups"]):
            return
        group = self.snapshot["groups"][row]
        lines = []
        for song in group["songs"]:
            lines.append(song["path"] + (" [missing]" if song["missing"] else ""))
            lines.append("  " + quality_text(song))
            lines.append(
                "  "
                + " · ".join(
                    f"{field.title()}: {song[field] or '—'}"
                    for field in ("title", "artist", "album", "genre", "year", "track")
                )
            )
            lines.append("  Existing categories: " + (", ".join(song["category_names"]) or "Untagged"))
        lines.append("\nCombined categories: " + (", ".join(group["categories"]) or "Untagged"))
        self.details.setPlainText("\n".join(lines))

    def choose_report(self):
        start = str(Path(self.snapshot["report_path"]).parent) if self.snapshot["report_path"] else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose completed import report", start, "Import report (*.json)"
        )
        if path:
            self.chosen_report = path
            self.action = "load"
            super().accept()

    def accept(self):
        if self.choices():
            super().accept()
