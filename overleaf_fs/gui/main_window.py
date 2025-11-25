

"""
Main window for the Overleaf Project Explorer GUI.

Design overview
---------------
In earlier steps we set up:

- A core data model in ``overleaf_fs.core.models`` that separates
  "remote" Overleaf fields (id, name, owner, last modified, URL) from
  local metadata (folder, notes, pinned, hidden).
- A Qt table model in ``overleaf_fs.gui.project_table_model.ProjectTableModel``
  that adapts a ``ProjectIndex`` (mapping project id -> ProjectRecord)
  into a ``QAbstractTableModel`` suitable for a ``QTableView``.
- A dummy ``load_project_index()`` function in
  ``overleaf_fs.core.project_index`` that currently returns a static
  set of sample projects so we can wire up and test the GUI before we
  implement real persistence and Overleaf scraping.

This module ties those pieces together into a minimal but functional
main window:

- The central area is split vertically using a ``QSplitter``:
    * Left: a ``ProjectTree`` showing special nodes (All Projects,
      Pinned) and the virtual folder hierarchy derived from local
      metadata, rooted at "Home".
    * Right: a search box and a ``QTableView`` backed by the
      ``ProjectTableModel`` and wrapped in a
      ``QSortFilterProxyModel`` to provide column-based sorting and
      simple text search.
- On startup, the window loads the dummy project index and displays it,
  and populates the folder tree from local state.
- A toolbar and menu bar provide a prominent "Refresh" action (with a
  keyboard shortcut) that re-loads the dummy index. Later, the same
  hook will trigger a real sync/refresh from Overleaf.
- The search box filters projects by matching the search text
  (case-insensitive) against the Name, Owner, and Local folder
  columns.
- Selecting nodes in the tree applies an additional folder-based
  filter (All projects, pinned projects, the Home folder, or a
  specific folder subtree).
- Double-clicking a row in the table opens the corresponding Overleaf
  project in the default web browser (using the stored project URL).

The goal at this stage is to have an end-to-end, installable GUI that
already feels like a simple "project browser" while leaving plenty of
room to add richer folder operations, real metadata loading, and
Overleaf integration.
"""

from __future__ import annotations
import sys
import json
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QModelIndex, QSortFilterProxyModel, QMimeData, QPoint
from PySide6.QtGui import QAction, QDesktopServices, QDrag
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QTableView,
    QHeaderView,
    QStatusBar,
    QLineEdit,
    QSplitter,
    QInputDialog,
    QMessageBox,
    QAbstractItemView,
)

from overleaf_fs.core.project_index import load_project_index
from overleaf_fs.core.metadata_store import (
    load_local_state,
    create_folder,
    rename_folder,
    delete_folder,
    move_projects_to_folder,
)
from overleaf_fs.gui.project_table_model import ProjectTableModel
from overleaf_fs.gui.project_tree import (
    ProjectTree,
    ALL_KEY,
    PINNED_KEY,
)



class _ProjectSortFilterProxyModel(QSortFilterProxyModel):
    """
    Proxy model that adds sorting and combined text/folder filtering
    on top of ``ProjectTableModel``.

    The text filter matches the search text (case-insensitive) against a
    subset of columns (currently: Name, Owner, Local folder). The folder
    filter restricts rows to those that match the selected node in the
    project tree (All Projects, Pinned, Home, or a specific folder
    subtree).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._filter_text: str = ""
        # Folder filter key: one of:
        #   - ALL_KEY (all non-hidden projects),
        #   - PINNED_KEY (only pinned projects),
        #   - "" (the Home folder: projects with folder in (None, "")),
        #   - a folder path string ("CT", "Teaching/2025").
        self._folder_key: Optional[str] = ALL_KEY

        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        # We sort by the display role of the source model by default.
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)

    # ------------------------------------------------------------------
    # Public API for filters
    # ------------------------------------------------------------------
    def setFilterText(self, text: str) -> None:
        """
        Update the text filter and invalidate the filter so that the
        attached views update immediately.

        Args:
            text (str): Case-insensitive substring to match against
                Name, Owner, and Local folder.
        """
        self._filter_text = text.strip()
        self.invalidateFilter()

    def setFolderKey(self, key: Optional[str]) -> None:
        """
        Update the folder filter key and invalidate the filter.

        Args:
            key (Optional[str]): One of:
                - ALL_KEY (show all projects),
                - PINNED_KEY (only pinned projects),
                - "" (Home: projects without an explicit folder),
                - a folder path string ("CT", "Teaching/2025"),
                - None (treated like Home / root).
        """
        # Treat None from the tree as the Home folder (root).
        if key is None:
            self._folder_key = ""
        else:
            self._folder_key = key
        self.invalidateFilter()

    # ------------------------------------------------------------------
    # QSortFilterProxyModel overrides
    # ------------------------------------------------------------------
    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """
        Return True if the row should be visible given the current
        text and folder filters.
        """
        model = self.sourceModel()
        if model is None:
            return True

        # We expect the source model to be a ProjectTableModel so we can
        # access the underlying ProjectRecord for folder/pinned data.
        from overleaf_fs.gui.project_table_model import ProjectTableModel as _PTM  # local import to avoid cycles

        if not isinstance(model, _PTM):
            return True

        record = model.project_at(source_row)
        if record is None:
            return False

        # 1. Folder-based filter
        key = self._folder_key

        if key is None or key == ALL_KEY:
            # All non-hidden projects.
            folder_ok = not record.local.hidden
        elif key == PINNED_KEY:
            # Only pinned, non-hidden projects.
            folder_ok = record.local.pinned and not record.local.hidden
        elif key == "":
            # Home: projects without an explicit folder.
            folder_ok = (record.local.folder in (None, "")) and not record.local.hidden
        else:
            # Regular folder path: match the folder or any of its descendants.
            folder = record.local.folder or ""
            folder_ok = (
                not record.local.hidden
                and (folder == key or folder.startswith(key + "/"))
            )

        if not folder_ok:
            return False

        # 2. Text-based filter
        if not self._filter_text:
            return True

        text = self._filter_text.lower()

        # Check Name, Owner, and Local folder columns via the source model.
        columns_to_check = [
            ProjectTableModel.COLUMN_NAME,
            ProjectTableModel.COLUMN_OWNER,
            ProjectTableModel.COLUMN_FOLDER,
        ]

        for col in columns_to_check:
            idx = model.index(source_row, col, source_parent)
            value = model.data(idx, Qt.DisplayRole)
            if value is None:
                continue
            if text in str(value).lower():
                return True

        return False


# --------------------------------------------------------------------------
# Drag-capable table view for project rows
# --------------------------------------------------------------------------
class ProjectTableView(QTableView):
    """
    QTableView subclass that initiates drag operations containing the
    ids of the selected projects.

    The view assumes that its model is a ``QSortFilterProxyModel`` whose
    source model is a ``ProjectTableModel``. When a drag is started, the
    selected rows are mapped back to the underlying ``ProjectRecord``
    instances and the project ids are packaged into the mime data under
    the custom type ``"application/x-overleaf-fs-project-ids"``.

    The corresponding drop logic lives in ``ProjectTree``, which can
    interpret this mime data and emit a high-level signal asking the
    controller to move the projects into a target folder.
    """

    MIME_TYPE = "application/x-overleaf-fs-project-ids"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Drag source; we do not accept drops here.
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        # Select whole rows; allow multiple selection so users can move
        # more than one project at a time.
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Track where a potential drag started so we can trigger a drag
        # once the mouse has moved far enough.
        self._drag_start_pos: Optional[QPoint] = None

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """
        Remember the position where a potential drag started.

        This lets us explicitly start a drag once the cursor has moved
        far enough, rather than relying on the base class heuristics.

        To support dragging multiple selected rows, we avoid resetting
        the selection when the user clicks on an already-selected row
        without any modifier keys. In that case, we simply record the
        drag start position and let ``mouseMoveEvent`` initiate the drag.
        """
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            self._drag_start_pos = pos

            index = self.indexAt(pos)
            selection_model = self.selectionModel()

            # If the user clicks on an already-selected row with no
            # modifiers, do not change the selection. This preserves
            # multi-row selection so that all selected rows participate
            # in the subsequent drag.
            if (
                index.isValid()
                and selection_model is not None
                and selection_model.isSelected(index)
                and not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.MetaModifier))
            ):
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        """
        Start a drag when the left button is held and the cursor has
        moved further than the platform drag threshold.
        """
        from PySide6.QtWidgets import QApplication

        if (
            event.buttons() & Qt.LeftButton
            and self._drag_start_pos is not None
        ):
            # Check whether we've moved far enough to initiate a drag.
            distance = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                # Start a drag with Move semantics; the implementation
                # of startDrag() below will package the selected project
                # ids into the mime data.
                self.startDrag(Qt.MoveAction)
                return

        super().mouseMoveEvent(event)

    def startDrag(self, supportedActions) -> None:  # type: ignore[override]
        """
        Start a drag containing the ids of the selected projects.

        If the model is not a ``QSortFilterProxyModel`` backed by a
        ``ProjectTableModel``, this falls back to the default behavior.
        """
        model = self.model()
        if model is None:
            return

        # We expect the view to be backed by a proxy model, but fall
        # back gracefully if that is not the case.
        proxy_model = None
        source_model = model

        if isinstance(model, QSortFilterProxyModel):
            proxy_model = model
            source_model = model.sourceModel()

        from overleaf_fs.gui.project_table_model import ProjectTableModel as _PTM

        if not isinstance(source_model, _PTM):
            # Unknown model type; delegate to default implementation.
            return super().startDrag(supportedActions)

        selection_model = self.selectionModel()
        if selection_model is None:
            return

        selected_rows = selection_model.selectedRows()
        if not selected_rows:
            return

        project_ids = []
        for index in selected_rows:
            if proxy_model is not None:
                source_index = proxy_model.mapToSource(index)
            else:
                source_index = index

            row = source_index.row()
            if row < 0:
                continue

            record = source_model.project_at(row)
            if record is None:
                continue

            # Prefer a direct id attribute; fall back to remote.id if needed.
            project_id = getattr(record, "id", None)
            if project_id is None and getattr(record, "remote", None) is not None:
                project_id = getattr(record.remote, "id", None)

            if not isinstance(project_id, str):
                continue

            project_ids.append(project_id)

        if not project_ids:
            return

        mime = QMimeData()
        payload = json.dumps(project_ids).encode("utf-8")
        mime.setData(self.MIME_TYPE, payload)

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)


class MainWindow(QMainWindow):
    """
    Main window for the Overleaf Project Explorer GUI.

    The window contains a folder tree on the left and, on the right, a
    search box and a table of projects backed by ``ProjectTableModel``
    and wrapped in a ``_ProjectSortFilterProxyModel`` for sorting and
    filtering. A toolbar and menu bar expose a prominent "Refresh"
    action. Double-clicking a project row opens the project in the
    default web browser via its Overleaf URL.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Overleaf Project Explorer")

        # Core model and proxy for sorting/filtering.
        self._model = ProjectTableModel()
        self._proxy = _ProjectSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        # Track the currently selected folder key so that we can
        # preserve the user's view across reloads.
        self._current_folder_key: Optional[str] = ALL_KEY

        # Table view (drag-capable).
        self._table = ProjectTableView(self)
        self._table.setModel(self._proxy)
        self._configure_table()

        # Connect double-click to "open project in browser".
        self._table.doubleClicked.connect(self._on_table_double_clicked)

        # Search box above the table.
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search projects…")
        self._search.textChanged.connect(self._proxy.setFilterText)

        # Folder tree on the left.
        self._tree = ProjectTree(self)
        self._tree.folderSelected.connect(self._on_folder_selected)
        self._tree.createFolderRequested.connect(self._on_create_folder)
        self._tree.renameFolderRequested.connect(self._on_rename_folder)
        self._tree.deleteFolderRequested.connect(self._on_delete_folder)
        self._tree.moveProjectsRequested.connect(self._on_move_projects)

        # Right-hand panel: search box + table.
        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._search)
        right_layout.addWidget(self._table)

        # Left-hand panel: folder tree.
        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._tree)

        # Splitter: tree on the left, table on the right.
        splitter = QSplitter(self)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

        # Toolbar and menu bar with a prominent Refresh action.
        self._refresh_action = self._create_actions()
        self._create_toolbar()
        self._create_menus()

        # Status bar for lightweight feedback messages.
        self.setStatusBar(QStatusBar(self))

        # Initial load of projects (currently from the dummy index).
        self._load_projects()

        self.resize(1100, 700)

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

        Returns:
            QAction: The Refresh action.
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

        This loads the project index and local state, rebuilds the
        folder tree from the union of known folders and per-project
        assignments, and then attempts to restore the previously
        selected folder (All Projects, Pinned, Home, or a specific
        folder path).
        """
        # Remember whichever folder key we were last told about.
        current_key = getattr(self, "_current_folder_key", ALL_KEY)

        index = load_project_index()
        self._model.set_projects(index)

        # Build the folder list for the tree using both explicit folders
        # from the local state and any folders referenced by projects.
        state = load_local_state()
        folder_paths = set(state.folders)
        for record in index.values():
            folder = record.local.folder
            if folder:
                folder_paths.add(folder)

        self._tree.set_folders(sorted(folder_paths))

        # Try to restore the previous selection so that operations like
        # drag-and-drop do not unexpectedly change the user's view.
        try:
            self._tree.select_folder_key(current_key)
        except AttributeError:
            # Older versions of ProjectTree may not have select_folder_key;
            # in that case we simply leave whatever selection Qt chooses.
            pass

        status = self.statusBar()
        if status is not None:
            status.showMessage(f"Loaded {len(index)} projects", 3000)

    def _on_refresh(self) -> None:
        """
        Slot connected to the Refresh action (toolbar, menu, shortcut).
        """
        self._load_projects()

    def _on_folder_selected(self, key: object) -> None:
        """
        Slot called when the user selects a node in the project tree.

        Forwards the selection key (All Projects, Pinned, Home, or a
        specific folder path) to the proxy model's folder filter.
        """
        if not isinstance(key, (str, type(None))):
            return

        # Remember the current folder key so we can restore this view
        # after reloading projects and folders.
        self._current_folder_key = key
        self._proxy.setFolderKey(key)

    def _on_create_folder(self, parent_path: object) -> None:
        """
        Slot called when the tree requests creation of a new folder.

        Prompts the user for a folder name, constructs the full folder
        path (optionally as a subfolder of ``parent_path``), and updates
        the local metadata via ``create_folder()``. Finally reloads the
        project index and folder tree.
        """
        if not isinstance(parent_path, (str, type(None))):
            return

        name, ok = QInputDialog.getText(
            self,
            "New folder",
            "Folder name:",
        )
        if not ok:
            return

        folder_name = name.strip()
        if not folder_name:
            return

        # For simplicity, disallow "/" in a single folder name segment.
        if "/" in folder_name:
            QMessageBox.warning(
                self,
                "Invalid folder name",
                "Folder names cannot contain '/'.\n"
                "Use the tree to create nested folders.",
            )
            return

        if parent_path:
            folder_path = f"{parent_path}/{folder_name}"
        else:
            folder_path = folder_name

        try:
            create_folder(folder_path)
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(
                self,
                "Error creating folder",
                f"Could not create folder '{folder_path}':\n{exc}",
            )
            return

        # Reload projects and folders so the tree and table reflect the change.
        self._load_projects()

    def _on_rename_folder(self, folder_path: str) -> None:
        """
        Slot called when the tree requests renaming an existing folder.

        Prompts the user for a new name, updates the folder path and any
        project assignments in the local metadata via ``rename_folder()``,
        and reloads the project index and folder tree.
        """
        if not isinstance(folder_path, str) or not folder_path:
            return

        # Default to the last segment of the folder path.
        default_name = folder_path.split("/")[-1]

        name, ok = QInputDialog.getText(
            self,
            "Rename folder",
            f"New name for '{folder_path}':",
            text=default_name,
        )
        if not ok:
            return

        new_name = name.strip()
        if not new_name or new_name == default_name:
            return

        if "/" in new_name:
            QMessageBox.warning(
                self,
                "Invalid folder name",
                "Folder names cannot contain '/'.\n"
                "Use the tree to create nested folders.",
            )
            return

        # Reconstruct the new full path.
        if "/" in folder_path:
            parent = folder_path.rsplit("/", 1)[0]
            new_path = f"{parent}/{new_name}"
        else:
            new_path = new_name

        if new_path == folder_path:
            return

        try:
            rename_folder(folder_path, new_path)
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(
                self,
                "Error renaming folder",
                f"Could not rename folder '{folder_path}' to '{new_path}':\n{exc}",
            )
            return

        self._load_projects()

    def _on_delete_folder(self, folder_path: str) -> None:
        """
        Slot called when the tree requests deletion of a folder subtree.

        Confirms the deletion with the user, then attempts to delete the
        folder via ``delete_folder()``. If any projects are still assigned
        to the folder or its descendants, a message is shown and no changes
        are made.
        """
        if not isinstance(folder_path, str) or not folder_path:
            return

        reply = QMessageBox.question(
            self,
            "Delete folder",
            f"Delete folder '{folder_path}' and all of its subfolders?\n\n"
            "This is only allowed if no projects are assigned to this folder "
            "or its descendants.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            delete_folder(folder_path)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                "Cannot delete folder",
                str(exc),
            )
            return
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(
                self,
                "Error deleting folder",
                f"Could not delete folder '{folder_path}':\n{exc}",
            )
            return

        self._load_projects()

    def _on_move_projects(self, project_ids: list, folder_path: object) -> None:
        """
        Slot called when the tree requests moving one or more projects
        into a folder via drag-and-drop.

        Updates local metadata via ``move_projects_to_folder()`` and
        reloads the project index and folder tree so that both the tree
        and the table reflect the new assignments.
        """
        if not isinstance(project_ids, list):
            return
        if not all(isinstance(pid, str) for pid in project_ids):
            return

        # folder_path may be "" (Home) or a real folder string, or None
        # (treated like Home by the metadata helper).
        if not isinstance(folder_path, (str, type(None))):
            return

        try:
            move_projects_to_folder(project_ids, folder_path)
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(
                self,
                "Error moving projects",
                f"Could not move projects:\n{exc}",
            )
            return

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
