# overleaf_fs/gui/main_window.py

from __future__ import annotations

"""
Main window for the Overleaf Project Explorer GUI.

Design overview
---------------
In earlier steps we set up:

- A core data model in ``overleaf_fs.core.models`` that separates
  "remote" Overleaf fields (id, name, owner, last modified, URL) from
  local metadata (tags, notes, pinned, hidden).
- A Qt table model in ``overleaf_fs.gui.project_table_model.ProjectTableModel``
  that adapts a ``ProjectIndex`` (mapping project id -> ProjectRecord)
  into a ``QAbstractTableModel`` suitable for a ``QTableView``.
- A dummy ``load_project_index()`` function in
  ``overleaf_fs.core.project_index`` that currently returns a static
  set of sample projects so we can wire up and test the GUI before we
  implement real persistence and Overleaf scraping.

This module ties those pieces together into a minimal but functional
main window:

- The central widget is a ``QTableView`` backed by the
  ``ProjectTableModel`` and wrapped in a ``QSortFilterProxyModel`` to
  provide column-based sorting and simple text search.
- On startup, the window loads the dummy project index and displays it.
- A toolbar and menu bar provide a prominent "Refresh" action (with a
  keyboard shortcut) that re-loads the dummy index. Later, the same
  hook will trigger a real sync/refresh from Overleaf.
- A search box above the table filters projects by matching the search
  text against the Name, Owner, and Tags columns.
- Double-clicking a row in the table opens the corresponding Overleaf
  project in the default web browser (using the stored project URL).

The goal at this stage is to have an end-to-end, installable GUI that
already feels like a simple "project browser" while leaving plenty of
room to add the tag-based folder tree, real metadata loading, and
Overleaf integration.
"""

import sys
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QModelIndex, QSortFilterProxyModel
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QTableView,
    QHeaderView,
    QStatusBar,
    QLineEdit,
)

from overleaf_fs.core.project_index import load_project_index
from overleaf_fs.gui.project_table_model import ProjectTableModel


class _ProjectSortFilterProxyModel(QSortFilterProxyModel):
    """
    Proxy model that adds sorting and simple text filtering on top of
    ``ProjectTableModel``.

    The filter matches the search text (case-insensitive) against a
    subset of columns (currently: Name, Owner, Tags). This keeps the
    search semantics simple and predictable while still being useful
    for quickly locating a project.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._filter_text: str = ""
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        # We sort by the display role of the source model by default.
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)

    def setFilterText(self, text: str) -> None:
        """
        Update the filter text and invalidate the filter so that the
        attached views update immediately.
        """
        self._filter_text = text.strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """
        Return True if the row should be visible given the current filter.

        The current implementation checks the Name, Owner, and Tags
        columns for a substring match of the filter text (case-insensitive).
        """
        if not self._filter_text:
            return True

        text = self._filter_text.lower()
        model = self.sourceModel()
        if model is None:
            return True

        # Columns we consider for filtering.
        columns_to_check = [
            ProjectTableModel.COLUMN_NAME,
            ProjectTableModel.COLUMN_OWNER,
            ProjectTableModel.COLUMN_TAGS,
        ]

        for col in columns_to_check:
            idx = model.index(source_row, col, source_parent)
            value = model.data(idx, Qt.DisplayRole)
            if value is None:
                continue
            if text in str(value).lower():
                return True

        return False


class MainWindow(QMainWindow):
    """
    Main window for the Overleaf Project Explorer GUI.

    Currently this window contains a search box and a single table of
    projects backed by ``ProjectTableModel`` and wrapped in a
    ``QSortFilterProxyModel`` for sorting and filtering. A toolbar and
    menu bar expose a prominent "Refresh" action. Double-clicking a
    project row opens the project in the default web browser via its
    Overleaf URL.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Overleaf Project Explorer")

        # Core model and proxy for sorting/filtering.
        self._model = ProjectTableModel()
        self._proxy = _ProjectSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        # Table view.
        self._table = QTableView(self)
        self._table.setModel(self._proxy)
        self._configure_table()

        # Connect double-click to "open project in browser".
        self._table.doubleClicked.connect(self._on_table_double_clicked)

        # Search box above the table.
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search projects…")
        self._search.textChanged.connect(self._proxy.setFilterText)

        # Central layout: search box + table.
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.addWidget(self._search)
        layout.addWidget(self._table)
        central.setLayout(layout)
        self.setCentralWidget(central)

        # Toolbar and menu bar with a prominent Refresh action.
        self._refresh_action = self._create_actions()
        self._create_toolbar()
        self._create_menus()

        # Status bar for lightweight feedback messages.
        self.setStatusBar(QStatusBar(self))

        # Initial load of projects (currently from the dummy index).
        self._load_projects()

        self.resize(1000, 600)

    # ------------------------------------------------------------------
    # UI setup helpers
    # ------------------------------------------------------------------
    def _configure_table(self) -> None:
        """
        Configure basic properties of the project table view.
        """
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.Interactive)

        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        # Enable sorting via the proxy model. Users can click on column
        # headers to sort by any visible column.
        self._table.setSortingEnabled(True)
        # Start with sorting by Name ascending.
        self._table.sortByColumn(ProjectTableModel.COLUMN_NAME, Qt.AscendingOrder)

    def _create_actions(self) -> QAction:
        """
        Create shared actions (e.g. Refresh) used by the toolbar and menus.

        The Refresh action currently re-loads the dummy project index.
        In a later iteration it will trigger a real sync with Overleaf
        and the local metadata store.
        """
        refresh_action = QAction("Refresh", self)
        refresh_action.setStatusTip("Reload the project list")
        # Keyboard shortcut: Ctrl+R (Qt maps this appropriately on macOS).
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self._on_refresh)

        # Make the action available at the window level for shortcut handling.
        self.addAction(refresh_action)

        return refresh_action

    def _create_toolbar(self) -> None:
        """
        Create a toolbar that exposes the Refresh action prominently.

        The toolbar currently contains only the Refresh action, labeled
        with text. This keeps it visible without cluttering the UI or
        disrupting the editing flow.
        """
        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("MainToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.addAction(self._refresh_action)

    def _create_menus(self) -> None:
        """
        Create a minimal menu bar with a View menu containing Refresh.

        This provides another route to the Refresh action in addition to
        the toolbar button and keyboard shortcut.
        """
        menubar = self.menuBar()
        view_menu = menubar.addMenu("&View")
        view_menu.addAction(self._refresh_action)

    # ------------------------------------------------------------------
    # Data loading and actions
    # ------------------------------------------------------------------
    def _load_projects(self) -> None:
        """
        Load (or reload) the project index and update the table model.

        At this stage this simply calls ``load_project_index()`` from
        ``overleaf_fs.core.project_index``, which returns a static set
        of sample projects. This hook will later be replaced with logic
        that reads persistent metadata and refreshes from Overleaf.
        """
        index = load_project_index()
        self._model.set_projects(index)

        status = self.statusBar()
        if status is not None:
            status.showMessage(f"Loaded {len(index)} projects", 3000)

    def _on_refresh(self) -> None:
        """
        Slot connected to the Refresh action (toolbar, menu, shortcut).
        """
        self._load_projects()

    def _on_table_double_clicked(self, index: QModelIndex) -> None:
        """
        Slot invoked when the user double-clicks a row in the project table.

        Opens the corresponding project URL in the default web browser.
        """
        # Map the proxy index back to the source row in the underlying model.
        source_index = self._proxy.mapToSource(index)
        row = source_index.row()
        if row < 0:
            return

        record = self._model.project_at(row)
        if record is None:
            return

        url_str = record.url
        if not url_str:
            return

        QDesktopServices.openUrl(QUrl(url_str))

    # ------------------------------------------------------------------
    # Application entry points
    # ------------------------------------------------------------------


def run() -> None:
    """
    Start the Qt application and show the main window.

    This is intended for programmatic use:

        from overleaf_fs.gui.main_window import run
        run()
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    # Start the event loop.
    app.exec()


def main() -> None:
    """
    Console-script entry point.

    This is what ``overleaf-fs`` calls after installation.
    Keeping it tiny avoids mixing CLI parsing into the rest
    of the codebase.
    """
    run()