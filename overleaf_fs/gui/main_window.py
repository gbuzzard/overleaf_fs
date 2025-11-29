"""
Main window for the Overleaf File System GUI.

Design overview
---------------

The main window ties together all core subsystems of OverleafFS:

Profiles and configuration
--------------------------
- Each user has a *profile root directory* containing:
    * overleaf base URL (standard or institution-hosted),
    * saved Overleaf session cookie (optional),
    * scraped Overleaf project information (title, owner, etc) (JSON),
    * local directory structure (folders, project assignments, expanded-tree state).
- On first launch, the user is prompted to choose a profile-root directory
  (ideally in cloud storage to enable multi-machine access).
- Profiles can be moved to a new directory at any time; existing data files
  are migrated seamlessly.
- The Overleaf base URL is flexible, allowing institutional Overleaf
  instances (e.g., ORNL) by storing the URL in the profile.

Overleaf authentication and scraping
------------------------------------
- If Qt WebEngine is available, the application provides an embedded
  Overleaf login dialog. The user signs in, and OverleafFS captures
  the resulting session cookie automatically.
- If WebEngine is unavailable, the user can paste the Cookie header
  manually as a fallback.
- The scraper fetches the project list from the Overleaf JSON endpoint
  (the same data used by Overleaf’s dashboard). Project data (title, etc)
  is stored per-profile and used to populate the GUI.
- A toolbar “Sync with Overleaf” action refreshes project data using the
  saved cookie, prompting the user only if necessary.

Core model
----------
- `ProjectRecord` combines:
    * remote project data (id, name, owner, last_modified, archived, URL),
    * local directory structure (folder assignment, pinned, hidden).
- `ProjectIndex` holds all project records in-memory.

Views and models
----------------
- Left panel: `ProjectTree`, a hierarchical tree with:
    * special nodes: All Projects, Pinned, Archived, Home,
    * user-created folders with arbitrary nesting,
    * drag-and-drop support for moving folders and projects.
  The tree tracks expanded/collapsed state via QSettings and restores it
  across application restarts.
- Right panel: a search box + a `QTableView` backed by:
    * `ProjectTableModel` (source model),
    * `_ProjectSortFilterProxyModel` (sorting + text & folder filters).

Interaction flow
----------------
- Selecting a folder updates the proxy model to display only the projects
  *directly assigned* to that folder (not descendants), mirroring
  file-browser semantics.
- Searching filters by Name, Owner, and Local folder.
- Double-clicking:
    * a project row → opens its Overleaf URL,
    * the “All Projects” tree node → opens the Overleaf dashboard URL,
      based on the profile’s Overleaf base URL.
- Dragging:
    * projects → drop into folders to update folder assignment,
    * folders → reorganize folder hierarchy,
    * hover-expansion allows dropping into collapsed subfolders.

Toolbar and menus
-----------------
Toolbar:
    - Sync with Overleaf
    - Reload from disk (local directory structure only)
    - Help

Menus:
    - File: reload local data, change profile folder
    - Overleaf: sync, open dashboard
    - Help: help + about

Startup sequence
----------------
1. Show the main window.
2. Prompt user for the directory to hold all profiles if not yet configured.
3. Attempt initial sync (with saved cookie if available).
4. Load project data (scraped from overleaf) + local directory structure.
5. Restore folder expansion state and last selected folder.

Overall
-------
This module provides a responsive, intuitive desktop interface for
browsing, organizing, and synchronizing Overleaf projects, while keeping
the underlying directory structure portable and profile-aware.
"""

from __future__ import annotations
import sys
import json
import shutil
from datetime import datetime
from typing import Optional
from pathlib import Path
import requests

from PySide6.QtCore import Qt, QUrl, QModelIndex, QSortFilterProxyModel, QMimeData, QPoint, QSettings, Signal, QEvent
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
    QFileDialog,
    QLabel,
    QStyle,
)

from overleaf_fs.core.project_index import load_project_index
from overleaf_fs.core.metadata_store import (
    load_local_state,
    create_folder,
    rename_folder,
    delete_folder,
    move_projects_to_folder,
)
from overleaf_fs.core.config import (
    get_profile_root_dir_optional,
    set_profile_root_dir,
    get_overleaf_base_url,
    get_active_profile_data_dir,
    DEFAULT_PROJECTS_INFO_FILENAME,
    DEFAULT_DIRECTORY_STRUCTURE_FILENAME,
    DEFAULT_PROFILE_CONFIG_FILENAME,
)
from overleaf_fs.core.overleaf_scraper import (
    sync_overleaf_projects_for_active_profile,
    CookieRequiredError,
)
from overleaf_fs.gui.project_table_model import ProjectTableModel
from overleaf_fs.gui.project_tree import (
    ProjectTree,
    ALL_KEY,
    PINNED_KEY,
    ARCHIVED_KEY,
    FolderPathRole,
)
from overleaf_fs.gui.overleaf_login import OverleafLoginDialog, WEBENGINE_AVAILABLE


class _StatusLabel(QLabel):
    """Clickable label used in the status bar to show sync/load info."""

    clicked = Signal()

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _ProjectSortFilterProxyModel(QSortFilterProxyModel):
    """
    Proxy model that adds sorting and combined text/folder filtering
    on top of ``ProjectTableModel``.

    The text filter matches the search text (case-insensitive) against a
    subset of columns (currently: Name, Owner, Local folder). The folder
    filter restricts rows to those that match the selected node in the
    project tree (All Projects, Pinned, Archived, Home, or a specific
    folder).
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
                - ARCHIVED_KEY (only archived projects),
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
        elif key == ARCHIVED_KEY:
            # Only archived, non-hidden projects (virtual view based on
            # archived tag from overleaf; local folder assignment is ignored).
            folder_ok = getattr(record.remote, "archived", False) and not record.local.hidden
        elif key == "":
            # Home: projects without an explicit folder.
            folder_ok = (record.local.folder in (None, "")) and not record.local.hidden
        else:
            # Regular folder path: show only projects whose local folder
            # exactly matches the selected folder, rather than including
            # projects in subfolders. This mirrors a file-browser-style
            # view where each folder shows its own direct contents.
            folder = record.local.folder or ""
            folder_ok = not record.local.hidden and folder == key

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
    Main window for the Overleaf File System GUI.

    The window contains a folder tree on the left and, on the right, a
    search box and a table of projects backed by ``ProjectTableModel``
    and wrapped in a ``_ProjectSortFilterProxyModel`` for sorting and
    filtering. A toolbar and menu bar expose a prominent "Refresh"
    action. Double-clicking a project row opens the project in the
    default web browser via its Overleaf URL.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Overleaf File System")

        # Per-machine UI settings (e.g. expanded folders) are stored via
        # QSettings so that basic view state is restored across restarts
        # without affecting the profile directory structure.
        self._settings = QSettings("OverleafFS", "ProjectExplorer")

        # Track the last time directory structure was loaded from disk and the last
        # successful sync with Overleaf so we can show this in the UI.
        self._last_loaded: Optional[datetime] = None
        self._last_synced: Optional[datetime] = None
        self._load_persisted_sync_times()

        # Ensure that the profile root directory is configured before we
        # attempt to load directory structure or synchronize overleaf data.
        # On a clean first run, this will prompt the user to choose a
        # location (ideally a cloud-synced folder) for OverleafFS profiles.
        # (Data initialization is now performed after the main window is shown.)

        # Core model and proxy for sorting/filtering.
        self._model = ProjectTableModel()
        self._proxy = _ProjectSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        # Track the currently selected folder key so that we can
        # preserve the user's view across reloads. The empty string
        # represents the Home folder.
        self._current_folder_key: Optional[str] = ""

        # Table view (drag-capable).
        self._table = ProjectTableView(self)
        self._table.setModel(self._proxy)
        self._configure_table()
        # Connect selection change to check for external changes to
        # the directory structure or the overleaf project data.
        sel_model = self._table.selectionModel()
        if sel_model:
            sel_model.selectionChanged.connect(lambda *_: self._check_external_metadata_change())

        # Connect double-click to "open project in browser".
        self._table.doubleClicked.connect(self._on_table_double_clicked)

        # Search box above the table.
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search projects…")
        self._search.textChanged.connect(self._proxy.setFilterText)
        self._configure_search_bar()
        # Let the main window intercept certain keys (e.g. Escape) while
        # the search field has focus.
        self._search.installEventFilter(self)

        # Cached mtimes for external Overleaf data and directory-structure change detection
        self._cached_mtime_directory_structure_json = None
        self._cached_mtime_projects_info_json = None

        # Guard flag to temporarily disable external-change checks during
        # internal reloads (e.g., after a sync or metadata edit).
        self._suspend_external_change_checks = False

        # Folder tree on the left.
        self._tree = ProjectTree(self)
        self._tree.folderSelected.connect(self._on_folder_selected)
        self._tree.createFolderRequested.connect(self._on_create_folder)
        self._tree.renameFolderRequested.connect(self._on_rename_folder)
        self._tree.deleteFolderRequested.connect(self._on_delete_folder)
        self._tree.moveProjectsRequested.connect(self._on_move_projects)
        # Track user-driven expansion/collapse so we can persist the tree
        # state across application restarts.
        self._tree.expanded.connect(self._on_tree_expanded)
        self._tree.collapsed.connect(self._on_tree_collapsed)
        # Handle double-click on special nodes, e.g. All Projects.
        self._tree.doubleClicked.connect(self._on_tree_double_clicked)

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

        # Status bar for lightweight feedback messages, plus a persistent
        # summary of last sync/load times.
        self.setStatusBar(QStatusBar(self))
        status_bar = self.statusBar()
        self._status_last_sync_label = _StatusLabel(self)
        self._status_last_sync_label.setObjectName("LastSyncStatusLabel")
        self._status_last_sync_label.setToolTip(
            "Click for detailed sync and cache information"
        )
        self._status_last_sync_label.clicked.connect(self._show_sync_status_dialog)
        status_bar.addPermanentWidget(self._status_last_sync_label)
        self._update_status_sync_label()

        # Set a comfortable default window size so that the key columns
        # are visible on first launch. The OS may remember geometry in
        # subsequent runs, but this provides a sensible initial layout.
        self.resize(1100, 700)

        # Note: data initialization (choosing a profile root, initial
        # sync from Overleaf, and loading the project index) is
        # performed explicitly after the main window is shown, via
        # ``initialize_data()``. This keeps the UI responsive and makes
        # it clear why any dialogs are appearing.

    # ------------------------------------------------------------------
    # Sync/load timestamp helpers
    # ------------------------------------------------------------------
    def _load_persisted_sync_times(self) -> None:
        """Restore last-loaded and last-synced timestamps from QSettings."""
        sync_raw = self._settings.value("last_synced_iso", "")
        load_raw = self._settings.value("last_loaded_iso", "")

        self._last_synced = None
        self._last_loaded = None

        if isinstance(sync_raw, str) and sync_raw:
            try:
                self._last_synced = datetime.fromisoformat(sync_raw)
            except ValueError:
                self._last_synced = None

        if isinstance(load_raw, str) and load_raw:
            try:
                self._last_loaded = datetime.fromisoformat(load_raw)
            except ValueError:
                self._last_loaded = None

    def _save_sync_times(self) -> None:
        """Persist last-loaded and last-synced timestamps via QSettings."""
        if self._last_synced is not None:
            self._settings.setValue("last_synced_iso", self._last_synced.isoformat())
        else:
            self._settings.remove("last_synced_iso")

        if self._last_loaded is not None:
            self._settings.setValue("last_loaded_iso", self._last_loaded.isoformat())
        else:
            self._settings.remove("last_loaded_iso")

    def _set_last_loaded_now(self) -> None:
        """Record that we have just loaded projects and directory structure from disk."""
        self._last_loaded = datetime.now()
        self._save_sync_times()
        self._update_status_sync_label()

    def _set_last_synced_now(self) -> None:
        """Record that a sync with Overleaf just completed successfully."""
        self._last_synced = datetime.now()
        self._save_sync_times()
        self._update_status_sync_label()

    def _format_timestamp_for_display(self, value: Optional[datetime]) -> str:
        """Return a human-readable local-time string or 'never'."""
        if value is None:
            return "never"
        # Compact, predictable format.
        return value.strftime("%Y-%m-%d %H:%M")

    def _sync_status_summary(self) -> str:
        """Return a short multi-line summary of sync/load status."""
        loaded_str = self._format_timestamp_for_display(self._last_loaded)
        synced_str = self._format_timestamp_for_display(self._last_synced)

        # Include the active profile folder so users can see which
        # profile/location is currently in use.
        root = get_profile_root_dir_optional()
        if root is not None:
            try:
                root_str = str(root.expanduser().resolve())
            except Exception:
                root_str = str(root)
        else:
            root_str = "not configured"

        return (
            f"Active profile folder: {root_str}\n"
            f"Last loaded from disk: {loaded_str}\n"
            f"Last successful sync with Overleaf: {synced_str}\n"
            "(All times shown in local time.)"
        )

    def _update_status_sync_label(self) -> None:
        """Update the permanent status-bar label with sync/load info."""
        if not hasattr(self, "_status_last_sync_label"):
            return
        loaded_str = self._format_timestamp_for_display(self._last_loaded)
        synced_str = self._format_timestamp_for_display(self._last_synced)
        text = f"Synced: {synced_str} • Loaded: {loaded_str} (click for details)"
        self._status_last_sync_label.setText(text)

    def _show_sync_status_dialog(self) -> None:
        """Show a dialog with detailed sync/load information."""
        QMessageBox.information(
            self,
            "Sync and cache status",
            "Sync and cache status:\n\n" + self._sync_status_summary(),
        )

    def initialize_data(self) -> None:
        """Load projects and directory structure from disk and perform an initial Overleaf sync.

        On startup we always load whatever is available on disk so the
        UI becomes responsive quickly, then we attempt to refresh from
        Overleaf if a cookie is available.
        """
        self._ensure_profile_root_dir()

        # First, load what we have on disk so the user immediately sees
        # their existing folder structure and projects (if any).
        self._on_reload_from_disk()

        # Then, try to perform an initial sync from Overleaf. This may
        # prompt for login if no valid cookie is available.
        self._initial_sync_from_overleaf()

    def _initial_sync_from_overleaf(self) -> None:
        """Attempt an initial Overleaf sync for the active profile.

        This is called once on startup after local projects and
        directory structure have been loaded from disk. If a valid
        cookie is available, it refreshes the remote project list. If
        no cookie is available or the saved cookie has expired, the
        user is offered the option to log in via the embedded browser.
        """
        try:
            # Try with whatever cookie (if any) is already stored.
            sync_overleaf_projects_for_active_profile()
        except CookieRequiredError:
            # Either no cookie has ever been saved, or the saved cookie
            # is no longer accepted by Overleaf.
            if not self._confirm_overleaf_login():
                # User chose not to log in right now; keep the current
                # on-disk view.
                return

            cookie, remember = self._prompt_for_cookie_header()
            if not cookie:
                # Login dialog was cancelled.
                return

            try:
                sync_overleaf_projects_for_active_profile(
                    cookie_header=cookie,
                    remember_cookie=remember,
                )
            except CookieRequiredError:
                # Even with a freshly obtained cookie, Overleaf rejected
                # the request. For now we simply leave the local data
                # as-is; a future sync attempt may succeed.
                return
            except Exception as exc:  # pragma: no cover - defensive
                # Treat all other errors (network, parsing, etc.) via a
                # single helper so the user can decide whether to
                # continue with local data or exit the app.
                self._handle_sync_error(exc)
                return
        except Exception as exc:  # pragma: no cover - defensive
            # Any non-cookie-related errors during the initial attempt
            # are handled in the same way: offer the choice to continue
            # with local data or exit.
            self._handle_sync_error(exc)
            return

        # On success, record the sync time and reload from disk so the UI
        # reflects the new data.
        self._set_last_synced_now()
        self._on_reload_from_disk()

    # ------------------------------------------------------------------
    # UI setup helpers
    # ------------------------------------------------------------------
    def _standard_icon(self, pixmap):
        """Return a platform-appropriate standard icon."""
        return self.style().standardIcon(pixmap)

    def _configure_search_bar(self) -> None:
        """Configure the search field (clear button and leading icon)."""
        # Use the built-in clear button so the user can quickly reset
        # the search text. This uses a native-looking "X" icon on each
        # platform and hides itself when the field is empty.
        self._search.setClearButtonEnabled(True)

        # Add a small standard icon at the leading (left) edge to
        # signal that this field controls filtering / search.
        search_icon = self._standard_icon(QStyle.SP_FileDialogContentsView)
        self._search.addAction(search_icon, QLineEdit.LeadingPosition)

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Intercept Escape in the search field to clear the text.

        This keeps the standard Escape behavior elsewhere in the UI
        while making it easy to reset the filter when focus is in the
        search box.
        """
        if obj is self._search and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape and self._search.text():
                self._search.clear()
                return True

        return super().eventFilter(obj, event)

    def _update_cached_metadata_mtimes(self) -> None:
        """Refresh cached mtimes for the projects-info and directory-structure JSON files.

        This is called after we load or reload data from disk so that
        subsequent external-change checks only trigger on changes that
        happened outside this process.
        """
        try:
            state_dir = get_active_profile_data_dir()
        except RuntimeError:
            # No active profile configured yet; nothing to cache.
            return

        for path, attr in (
            (state_dir / DEFAULT_DIRECTORY_STRUCTURE_FILENAME, "_cached_mtime_directory_structure_json"),
            (state_dir / DEFAULT_PROJECTS_INFO_FILENAME, "_cached_mtime_projects_info_json"),
        ):
            if path.exists():
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    mtime = None
            else:
                mtime = None
            setattr(self, attr, mtime)

    def _check_external_metadata_change(self) -> bool:
        """Return True if on-disk projects/structure changed and user approves a reload."""
        if getattr(self, "_suspend_external_change_checks", False):
            return False
        try:
            # Look in the active profile's data directory, which is
            # where local_directory_structure.json (directory-structure data)
            # and overleaf_projects_info.json (cached projects info) live.
            state_dir = get_active_profile_data_dir()
        except RuntimeError:
            # No active profile configured yet; nothing to check.
            return False

        state_path = state_dir / DEFAULT_DIRECTORY_STRUCTURE_FILENAME
        projects_path = state_dir / DEFAULT_PROJECTS_INFO_FILENAME

        changed = False
        for p, attr in (
            (state_path, "_cached_mtime_directory_structure_json"),
            (projects_path, "_cached_mtime_projects_info_json"),
        ):
            if p.exists():
                mtime = p.stat().st_mtime
                last = getattr(self, attr)
                if last is not None and last != mtime:
                    changed = True
                setattr(self, attr, mtime)

        if not changed:
            return False

        reply = QMessageBox.question(
            self,
            "Detected external changes",
            "Project list or folder assignments changed on disk.\nReload now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self._on_reload_from_disk()
            return True
        return False

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
        # Start with sorting by Last Modified, newest first, to match the
        # default Overleaf dashboard ordering.
        self._table.sortByColumn(ProjectTableModel.COLUMN_LAST_MODIFIED, Qt.DescendingOrder)
        # Give the Name column a wider default width so that longer
        # project titles are visible without immediate manual resizing.

        header.resizeSection(ProjectTableModel.COLUMN_NAME, 200)

    def _ensure_profile_root_dir(self) -> None:
        """Ensure that the profile root directory has been configured.

        On a clean startup (where no ``profile_root_dir`` is present in
        the core config), this method prompts the user to choose a
        directory. If the user cancels the dialog, the application exits
        cleanly.

        The chosen directory is persisted via
        :func:`set_profile_root_dir` and then used by the core
        configuration, projects-info, and directory-structure helpers,
        along with the scraper code, to locate profile-specific data.
        """
        root = get_profile_root_dir_optional()
        if root is not None:
            return

        # Default suggestion: a subdirectory under the user's home
        # directory. The user is free to pick any path (e.g. a cloud
        # drive folder) to make OverleafFS data available across machines.
        default_dir = str((Path.home() / "overleaf_fs_profiles").expanduser())

        # Explain what this choice means so the file picker is not a
        # surprise on first startup.
        QMessageBox.information(
            self,
            "Choose a directory to hold one or more profiles",
            "OverleafFS can manage multiple Overleaf accounts with a separate "
            "profile for each, all stored under a common profile root directory, "
            "which you can select here.\n\n"
            "A typical root choice is 'overleaf_fs_profiles', which would then be used "
            "to hold subdirectories such as 'primary' and 'personal' created by "
            "OverleafFS after you authenticate to each Overleaf account.\n\n"
            "Recommendation: choose a folder in a cloud-synced location "
            "(e.g. Dropbox, OneDrive, iCloud) if you want to share the same "
            "profiles across multiple computers.  If you've created a cloud-based "
            "folder on another machine, choose it here to share."
        )

        dialog = QFileDialog(
            self,
            "Choose OverleafFS profile folder - typically 'overleaf_fs_profiles'",
            default_dir,
        )
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)

        # Pre-select the suggested default directory so that it appears
        # in the "Directory:" field and is highlighted in the central
        # view when the dialog opens.
        default_path = Path(default_dir).expanduser()
        parent_for_view = default_path.parent
        dialog.setDirectory(str(parent_for_view))
        dialog.selectFile(str(default_path))

        # Add common cloud-storage locations (if present) to the sidebar
        # so that Dropbox, Box, OneDrive, etc. are easy to access.
        sidebar_urls = self._cloud_sidebar_urls()
        if sidebar_urls:
            dialog.setSidebarUrls(sidebar_urls)

        if dialog.exec() != QFileDialog.Accepted:
            # User cancelled; exit the application to avoid running with
            # an undefined profile root.
            sys.exit(0)

        selected_files = dialog.selectedFiles()
        if not selected_files:
            sys.exit(0)

        set_profile_root_dir(Path(selected_files[0]))

    def _cloud_sidebar_urls(self) -> list[QUrl]:
        """Return a list of sidebar URLs for useful profile/data locations.

        The sidebar entries are intended to make it easy to navigate to:

        * The user's home directory,
        * The current OverleafFS profile root directory (if configured),
        * Common cloud-storage folders such as Dropbox, Box, and OneDrive,
        * Provider-specific folders under ``~/Library/CloudStorage`` on macOS.

        This improves discoverability of cloud-sync locations when using
        the non-native ``QFileDialog`` on macOS, where these folders may
        not appear as top-level items by default.
        """
        urls: list[QUrl] = []

        def add_path(path: Path, seen: set[str]) -> None:
            try:
                path = path.expanduser().resolve()
            except Exception:
                # Fall back to the raw path if resolution fails.
                pass
            as_str = str(path)
            if as_str in seen:
                return
            if not path.exists():
                return
            urls.append(QUrl.fromLocalFile(as_str))
            seen.add(as_str)

        seen: set[str] = set()

        home = Path.home()
        add_path(home, seen)

        # Include the current profile root directory, if configured and
        # distinct from the home directory. This makes it easy for the
        # user to re-select or inspect the active profile location.
        current_root = get_profile_root_dir_optional()
        if current_root is not None:
            add_path(current_root, seen)

        # Common top-level folders in the user's home directory.
        for name in ("Dropbox", "Box", "OneDrive"):
            candidate = home / name
            add_path(candidate, seen)

        # Modern macOS cloud integrations often live under:
        #   ~/Library/CloudStorage/<provider-specific folder>
        cloud_root = home / "Library" / "CloudStorage"
        if cloud_root.exists():
            try:
                for child in cloud_root.iterdir():
                    if child.is_dir():
                        add_path(child, seen)
            except Exception:
                # Best-effort only; ignore errors from iterating this directory.
                pass

        return urls

    def _looks_like_profile_root(self, path: Path) -> bool:
        """Return True if ``path`` appears to contain OverleafFS profile data.

        This is a heuristic used when the user chooses a new profile
        folder. If the selected directory already contains OverleafFS
        profile data files (e.g., project-info and directory-structure
        JSON from another machine), we treat it as an existing profile
        root and offer to switch to it rather than moving the current
        profile into that directory.
        """
        try:
            path = path.expanduser().resolve()
        except Exception:
            # Fall back to the raw path if resolution fails.
            pass

        if not path.exists() or not path.is_dir():
            return False

        # Heuristic: consider this a profile root if it contains one of
        # the known profile data files (project info, directory structure,
        # or profile config) either directly, or inside a single subfolder
        # such as "primary".
        sentinel_names = {
            DEFAULT_PROJECTS_INFO_FILENAME,
            DEFAULT_DIRECTORY_STRUCTURE_FILENAME,
            DEFAULT_PROFILE_CONFIG_FILENAME,
        }

        # First: check top-level files.
        try:
            for child in path.iterdir():
                if child.is_file() and child.name in sentinel_names:
                    return True
        except Exception:
            return False

        # Second: check exactly one level deeper, e.g. path/primary/*.
        try:
            for child in path.iterdir():
                if not child.is_dir():
                    continue
                try:
                    for sub in child.iterdir():
                        if sub.is_file() and sub.name in sentinel_names:
                            return True
                except Exception:
                    continue
        except Exception:
            return False

        return False

    def _load_expanded_folder_keys(self) -> list[str]:
        """Return the list of folder keys that were expanded last session.

        This uses ``QSettings`` to persist the expanded state across
        application restarts. The keys correspond to the values stored
        in ``FolderPathRole`` for tree items (e.g. "CT", "Grants/Ptychography").
        """
        value = self._settings.value("expanded_folders", [])
        if not isinstance(value, (list, tuple)):
            return []
        return [str(v) for v in value]

    def _save_expanded_folder_keys(self, keys: list[str]) -> None:
        """Persist the list of expanded folder keys via ``QSettings``.

        This is invoked after rebuilding the tree so that the current
        expansion state can be restored on the next restart.
        """
        self._settings.setValue("expanded_folders", list(keys))

    def _update_persisted_expanded_folders(self) -> None:
        """Recompute the set of expanded folders and persist it.

        This is called when the user expands or collapses folders so
        that the expansion state can be restored on the next restart,
        not just after operations that trigger a full reload.
        """
        tree_model = self._tree.model()
        if tree_model is None:
            return

        expanded_keys: set[str] = set()

        def _collect(parent_index: QModelIndex) -> None:
            row_count = tree_model.rowCount(parent_index)
            for row in range(row_count):
                idx = tree_model.index(row, 0, parent_index)
                if not idx.isValid():
                    continue
                item_key = tree_model.data(idx, FolderPathRole)
                if self._tree.isExpanded(idx) and isinstance(item_key, str):
                    expanded_keys.add(item_key)
                _collect(idx)

        _collect(QModelIndex())
        self._save_expanded_folder_keys(sorted(expanded_keys))

    def _on_tree_expanded(self, index: QModelIndex) -> None:
        """Slot called when the user expands a folder in the tree.

        Before only updating the persisted expansion state, this also
        checks whether the on-disk projects-info or directory-structure
        data changed externally and, if so, offers to reload from disk.
        """
        self._check_external_metadata_change()
        self._update_persisted_expanded_folders()

    def _on_tree_collapsed(self, index: QModelIndex) -> None:
        """Slot called when the user collapses a folder in the tree.

        Before only updating the persisted expansion state, this also
        checks whether the on-disk projects-info or directory-structure
        data changed externally and, if so, offers to reload from disk.
        """
        self._check_external_metadata_change()
        self._update_persisted_expanded_folders()

    def _create_actions(self) -> None:
        """Create shared actions used by the toolbar and menus.

        Actions are grouped conceptually into:

        * Local/data: "Reload from disk" (no network),
        * Overleaf/network: "Sync with Overleaf" and "Open Overleaf dashboard",
        * Profile/environment: "Change profile folder…",
        * Help: "Help" and "About" dialogs.
        """
        # Reload from disk: re-read local projects info and directory
        # structure without any network access. This is the safest way
        # to refresh the view when only local folder assignments or
        # other local organization have changed.
        self._reload_action = QAction("Reload from disk", self)
        # self._reload_action.setStatusTip("Reload projects from local metadata (no network)")
        self._reload_action.setToolTip("Reload directory structure from disk (e.g., if changed from another computer)")
        self._reload_action.setShortcut("Ctrl+R")
        self._reload_action.triggered.connect(self._on_reload_from_disk)
        self.addAction(self._reload_action)

        # Sync with Overleaf: contact Overleaf using a saved cookie
        # (prompting if needed), update the on-disk projects info, and
        # then reload the view.
        self._sync_action = QAction("Sync with Overleaf", self)
        # self._sync_action.setStatusTip("Synchronize project list with Overleaf and reload")
        self._sync_action.setToolTip("Synchronize project list with Overleaf and reload")
        self._sync_action.setShortcut("Ctrl+Shift+R")
        self._sync_action.triggered.connect(self._on_sync_with_overleaf)
        self.addAction(self._sync_action)

        # Open the Overleaf projects dashboard in the default browser.
        self._open_dashboard_action = QAction("Open Overleaf dashboard", self)
        # self._open_dashboard_action.setStatusTip("Open the Overleaf projects page in your browser")
        self._open_dashboard_action.setToolTip("Open the Overleaf projects page in your browser")
        self._open_dashboard_action.triggered.connect(self._on_open_overleaf_dashboard)

        # Change profile folder: allow the user to move the profile
        # data directory to a new folder (e.g. a different cloud-synced
        # location).
        self._change_profile_location_action = QAction("Change profile folder…", self)
        # self._change_profile_location_action.setStatusTip("Choose a new directory for OverleafFS profiles")
        self._change_profile_location_action.setToolTip("Choose a new directory for OverleafFS profiles")
        self._change_profile_location_action.triggered.connect(self._on_change_profile_location)

        # Help: brief overview of basic interactions in the GUI.
        self._help_action = QAction("Help", self)
        # self._help_action.setStatusTip("Show basic usage help for Overleaf File System")
        self._help_action.setToolTip("Show basic usage and status for Overleaf File System")
        self._help_action.triggered.connect(self._on_help)

        # About: show information about this application and its home
        # on GitHub.
        self._about_action = QAction("About", self)
        # self._about_action.setStatusTip("About Overleaf File System")
        self._about_action.setToolTip("About Overleaf File System")
        self._about_action.triggered.connect(self._on_about)

    def _create_toolbar(self) -> None:
        """Create a toolbar that exposes the key actions prominently.

        The toolbar contains:

        * "Sync with Overleaf" (primary network action),
        * "Reload from disk" (local refresh),
        * "Help" (quick access to basic usage information).
        """
        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("MainToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        toolbar.addAction(self._sync_action)
        toolbar.addAction(self._reload_action)
        toolbar.addSeparator()
        toolbar.addAction(self._help_action)

    def _create_menus(self) -> None:
        """Create the main menu bar: File, Overleaf, and Help.

        * File: local/data and profile actions,
        * Overleaf: network-related actions,
        * Help: usage help and About dialog.
        """
        menubar = self.menuBar()

        # File menu: local reload and profile folder.
        file_menu = menubar.addMenu("&File")
        file_menu.addAction(self._reload_action)
        file_menu.addSeparator()
        file_menu.addAction(self._change_profile_location_action)

        # Overleaf menu: network actions.
        overleaf_menu = menubar.addMenu("&Overleaf")
        overleaf_menu.addAction(self._sync_action)
        overleaf_menu.addAction(self._open_dashboard_action)

        # Help menu: usage help and About.
        help_menu = menubar.addMenu("&Help")
        help_menu.addAction(self._help_action)
        help_menu.addAction(self._about_action)

    # ------------------------------------------------------------------
    # Data loading and actions
    # ------------------------------------------------------------------
    def _load_projects(self) -> None:
        """
        Load (or reload) the project index and update the table model.

        This loads the project index and local directory structure,
        rebuilds the folder tree from the union of known folders and
        per-project assignments, and then attempts to restore the
        previously selected folder (All Projects, Pinned, Archived,
        Home, or a specific folder path).
        """
        # Temporarily suppress external-change checks while we perform
        # an internal reload. This prevents our own writes and the
        # resulting mtime updates from triggering the "Reload?" dialog.
        self._suspend_external_change_checks = True
        try:
            # Remember whichever folder key we were last told about.
            current_key = getattr(self, "_current_folder_key", ALL_KEY)

            index = load_project_index()
            self._model.set_projects(index)

            # Load any previously persisted expanded-folder state so that we
            # can restore it on the first load in a new session.
            persisted_expanded_keys = set(self._load_expanded_folder_keys())

            # Capture which folder nodes are currently expanded so we can
            # restore that state after rebuilding the tree model. This helps
            # avoid collapsing parent folders when operations such as
            # drag-and-drop trigger a reload.
            expanded_keys: set[str] = set()
            tree_model = self._tree.model()
            if tree_model is not None:
                def _collect_expanded(parent_index: QModelIndex) -> None:
                    row_count = tree_model.rowCount(parent_index)
                    for row in range(row_count):
                        idx = tree_model.index(row, 0, parent_index)
                        if not idx.isValid():
                            continue
                        item_key = tree_model.data(idx, FolderPathRole)
                        if self._tree.isExpanded(idx) and isinstance(item_key, str):
                            expanded_keys.add(item_key)
                        _collect_expanded(idx)

                _collect_expanded(QModelIndex())
            state = load_local_state()
            folder_paths = set(state.folders)
            for record in index.values():
                folder = record.local.folder
                if folder:
                    folder_paths.add(folder)

            self._tree.set_folders(sorted(folder_paths))

            # Restore expanded folders based on the keys we captured before
            # rebuilding the tree, or fall back to any persisted keys from
            # the previous session when there is no current-session state
            # (e.g. the first load after startup).
            tree_model = self._tree.model()
            union_expanded_keys = expanded_keys or persisted_expanded_keys
            if tree_model is not None and union_expanded_keys:
                def _expand_matching(parent_index: QModelIndex) -> None:
                    row_count = tree_model.rowCount(parent_index)
                    for row in range(row_count):
                        idx = tree_model.index(row, 0, parent_index)
                        if not idx.isValid():
                            continue
                        item_key = tree_model.data(idx, FolderPathRole)
                        if isinstance(item_key, str) and item_key in union_expanded_keys:
                            self._tree.setExpanded(idx, True)
                        _expand_matching(idx)

                _expand_matching(QModelIndex())

            # Try to restore the previous selection so that operations like
            # drag-and-drop do not unexpectedly change the user's view.
            try:
                self._tree.select_folder_key(current_key)
            except AttributeError:
                # Older versions of ProjectTree may not have select_folder_key;
                # in that case we simply leave whatever selection Qt chooses.
                pass

            # Record that we have just loaded projects and directory structure from disk.
            self._set_last_loaded_now()

            status = self.statusBar()
            if status is not None:
                status.showMessage(f"Loaded {len(index)} projects", 3000)

            # After a successful load, refresh the cached mtimes so that
            # external-change detection only triggers on changes that
            # occur outside this process.
            self._update_cached_metadata_mtimes()
        finally:
            # Re-enable external-change checks after the internal reload
            # has completed and the cached mtimes have been updated.
            self._suspend_external_change_checks = False

    def _on_reload_from_disk(self) -> None:
        """Reload projects and folders from local data on disk.

        This operation does not contact Overleaf; it simply re-reads the
        local project index and directory structure and refreshes the
        views.
        """
        self._load_projects()

    def _on_sync_with_overleaf(self) -> None:
        """Trigger a manual sync with Overleaf for the active profile."""
        try:
            sync_overleaf_projects_for_active_profile()
        except CookieRequiredError:
            if not self._confirm_overleaf_login():
                return

            cookie, remember = self._prompt_for_cookie_header()
            if not cookie:
                return

            try:
                sync_overleaf_projects_for_active_profile(
                    cookie_header=cookie,
                    remember_cookie=remember,
                )
            except CookieRequiredError:
                # If this still fails, we quietly keep the last-saved data.
                return
            except Exception as exc:  # pragma: no cover - defensive
                self._handle_sync_error(exc)
                return
            except Exception as exc:  # pragma: no cover - defensive
                self._handle_sync_error(exc)
                return

            # Successful sync; record timestamp and reload from disk.
            self._set_last_synced_now()
            self._on_reload_from_disk()

    def _handle_sync_error(self, exc: Exception) -> None:
        """Handle errors that occur during Overleaf sync operations.

        This helper is used by both the startup sync and manual "Sync
        with Overleaf" actions to provide a consistent user experience
        when network or other unexpected errors occur.

        The dialog gives the user a choice between continuing with the
        last-saved local data or exiting the application entirely.
        """
        # Classify the error as best we can using requests' exception
        # hierarchy. ConnectionError is the most specific case we care
        # about (e.g. no internet, DNS failure, Overleaf host
        # unreachable). Other RequestException subclasses are treated as
        # generic network errors.
        is_request_exc = isinstance(exc, requests.exceptions.RequestException)
        is_connection_exc = isinstance(exc, requests.exceptions.ConnectionError)

        if is_connection_exc:
            reason = (
                "Overleaf sync failed due to a connection error.\n"
                "This can happen if there is no internet connection or "
                "if Overleaf is temporarily unreachable."
            )
        elif is_request_exc:
            reason = (
                "Overleaf sync failed due to a network error.\n"
                "This may be caused by a temporary connectivity problem "
                "or an issue reaching Overleaf."
            )
        else:
            reason = (
                "Overleaf sync failed due to an unexpected error.\n"
                "You can continue using the last-saved data, or exit the "
                "application."
            )

        details = str(exc).strip()
        message = reason

        # Log to stderr for debugging purposes.
        print(f"[OverleafFS] Sync error: {exc}", file=sys.stderr)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Overleaf sync error")
        box.setText(message)
        if details:
            # Use Qt's standard expandable "Show Details" section so
            # that the main message remains readable while still
            # providing access to the underlying exception text.
            box.setDetailedText(details)

        continue_button = box.addButton(
            "Continue without syncing", QMessageBox.AcceptRole
        )
        exit_button = box.addButton(
            "Exit application", QMessageBox.RejectRole
        )
        box.setDefaultButton(continue_button)

        box.exec()

        if box.clickedButton() is exit_button:
            # Mark that the user requested an exit so that callers
            # (including the startup path) can avoid entering the main
            # event loop after this dialog.
            self._should_exit = True

            app = QApplication.instance()
            if app is not None:
                app.quit()

            # Also close the main window explicitly so that a running
            # event loop will terminate once there are no top-level
            # windows left.
            self.close()

    @staticmethod
    def _on_open_overleaf_dashboard() -> None:
        """Open the Overleaf projects dashboard in the default browser.

        The URL is derived from the active profile's Overleaf base URL
        so that institution-hosted Overleaf instances (e.g. ORNL) are
        supported transparently.
        """
        base = get_overleaf_base_url().strip().rstrip("/")
        if not base:
            base = "https://www.overleaf.com"
        overleaf_projects_url = QUrl(f"{base}/project")
        QDesktopServices.openUrl(overleaf_projects_url)

    def _on_change_profile_location(self) -> None:
        """Allow the user to choose a new profile root directory.

        This reuses the same logic as the initial profile selection but
        does not exit the application if the user cancels. When a new
        directory is chosen, it is saved and the data is reinitialized.
        Distinguishes between switching to an existing profile and moving the current profile.
        """
        current_root = get_profile_root_dir_optional()
        if current_root is not None:
            default_dir = str(current_root.parent)
        else:
            default_dir = str((Path.home() / "overleaf_fs_profiles").expanduser().parent)

        QMessageBox.information(
            self,
            "Change profile storage location",
            "Choose a new directory for OverleafFS profiles.\n\n"
            "Recommendation: use a cloud-synced folder if you want to\n"
            "share profiles across multiple machines.",
        )

        title = (
            "Choose OverleafFS profile folder - typically 'overleaf_fs_profiles'"
            if current_root is None
            else "Choose OverleafFS profile folder - current profile folder preselected"
        )
        dialog = QFileDialog(
            self,
            title,
            default_dir,
        )
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)

        # Add common cloud-storage locations (if present) to the sidebar.
        sidebar_urls = self._cloud_sidebar_urls()
        if sidebar_urls:
            dialog.setSidebarUrls(sidebar_urls)

        # If a current profile root exists, open the dialog on its parent
        # directory and pre-select the current root so that it appears
        # highlighted in the central list and in the "Directory:" field.
        if current_root is not None:
            try:
                current_root_path = current_root.expanduser().resolve()
            except Exception:
                current_root_path = current_root
            parent_for_view = current_root_path.parent
            dialog.setDirectory(str(parent_for_view))
            dialog.selectFile(str(current_root_path))

        if dialog.exec() != QFileDialog.Accepted:
            return

        selected_files = dialog.selectedFiles()
        if not selected_files:
            return

        new_root = Path(selected_files[0])

        # Resolve the currently active root (if any) and the newly
        # chosen root so that comparisons are stable.
        current_root = get_profile_root_dir_optional()
        if current_root is not None:
            try:
                current_root = current_root.expanduser().resolve()
            except Exception:
                current_root = current_root

        try:
            new_root_resolved = new_root.expanduser().resolve()
        except Exception:
            new_root_resolved = new_root

        # If the user selected the same directory, nothing to do.
        if current_root is not None and current_root == new_root_resolved:
            return

        # If the chosen directory already appears to contain OverleafFS
        # profile data, treat this as a "switch profile" operation
        # rather than moving the current profile into that directory.
        if self._looks_like_profile_root(new_root_resolved):
            reply = QMessageBox.question(
                self,
                "Use existing profile?",
                "The selected folder already contains OverleafFS profile data.\n\n"
                "Do you want to switch to this profile on this machine?\n"
                "No files will be moved.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return

            set_profile_root_dir(new_root_resolved)
            self.initialize_data()
            return

        # Otherwise, if there is an existing profile root and it differs
        # from the newly chosen root, attempt to move the existing
        # profile files into the new location so that cookies, project
        # metadata, and other state are preserved.
        if (
            current_root is not None
            and current_root.exists()
        ):
            try:
                # Ensure the new root exists.
                new_root_resolved.mkdir(parents=True, exist_ok=True)

                # If the new root is not empty, warn the user that files
                # may be overwritten and allow them to cancel the move.
                try:
                    is_empty = not any(new_root_resolved.iterdir())
                except Exception:
                    is_empty = False

                if not is_empty:
                    reply = QMessageBox.question(
                        self,
                        "Move existing profile data?",
                        "The selected directory is not empty but doesn't appear to have OverleafFS profiles.\n\n"
                        "Existing OverleafFS profile files will be moved into this "
                        "directory and may overwrite files with the same name.\n\n"
                        "Do you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply != QMessageBox.Yes:
                        return

                # Move all children (files and subdirectories) from the
                # current root into the new root.
                for child in current_root.iterdir():
                    dest = new_root_resolved / child.name
                    try:
                        shutil.move(str(child), str(dest))
                    except Exception as exc:
                        QMessageBox.warning(
                            self,
                            "Error moving profile data",
                            f"Could not move '{child}' to '{dest}':\n{exc}",
                        )
                        return
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Error moving profile data",
                    f"Could not move existing profile files to the new location:\n{exc}",
                )
                return

        # Persist the new root directory and re-run data initialization
        # so that subsequent loads use the updated location (which now
        # contains the moved profile files, including any saved cookie).
        set_profile_root_dir(new_root_resolved)
        self.initialize_data()

    def _on_help(self) -> None:
        """Show a brief help dialog with basic usage instructions."""
        help_text = (
            "Basic usage:\n\n"
            "- Use the folder tree on the left to select Home or a specific folder.\n"
            "- Right-click a folder to create, rename, or delete folders.\n"
            "- Drag projects from the table into folders to move them.\n"
            "- Double-click a project row to open it in Overleaf.\n"
            "- Double-click 'All Projects' in the tree to open the Overleaf\n"
            "  projects dashboard in your browser.\n\n"
            "Toolbar:\n"
            "- 'Sync with Overleaf' contacts Overleaf and updates the local record of each project status.\n"
            "- 'Reload from disk' re-reads the OverleafFS file structure and other data (e.g., if changed from another computer).\n\n"
            "Sync status:\n" + self._sync_status_summary()
        )

        QMessageBox.information(
            self,
            "Overleaf File System - Help",
            help_text,
        )

    def _on_about(self) -> None:
        """Show an About dialog pointing to the GitHub repository."""
        QMessageBox.information(
            self,
            "About Overleaf File System",
            "Overleaf File System\n\n"
            "GitHub repository:\n"
            "https://github.com/gbuzzard/overleaf_file_system",
        )

    def _on_refresh(self) -> None:
        """Backward-compatible alias for Sync with Overleaf."""
        self._on_sync_with_overleaf()

    def _confirm_overleaf_login(self) -> bool:
        """Ask the user whether to launch the embedded Overleaf login.

        This is used when a valid Overleaf cookie is missing or has
        likely expired. If the user declines, the application remains in
        its current state (using whatever local projects info and
        directory structure are available).
        """
        reply = QMessageBox.question(
            self,
            "Overleaf login required",
            "A valid Overleaf login for this profile is not available\n"
            "or may have expired.\n\n"
            "Do you want to log in through this application now to\n"
            "load or refresh your Overleaf project information?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return reply == QMessageBox.Yes

    def _prompt_for_cookie_header(self) -> tuple[Optional[str], bool]:
        """
        Obtain an Overleaf Cookie header string for the active profile.

        When Qt WebEngine is available, this first presents an embedded
        Overleaf login dialog so that the user can sign in inside the
        application and have the session cookies captured
        automatically. If WebEngine is not available (or in
        environments where it is not installed), the method falls back
        to a manual "paste Cookie header" dialog.

        Returns:
            A tuple ``(cookie_header, remember)`` where ``cookie_header``
            is the raw Cookie header string (or None if the user
            cancelled) and ``remember`` indicates whether the user
            agreed to remember this cookie for future refreshes.
        """
        # Preferred path: embedded login via Qt WebEngine, when
        # available. This lets the user log in to Overleaf in a small
        # browser window without having to copy/paste anything from
        # their regular browser.
        if WEBENGINE_AVAILABLE:
            dlg = OverleafLoginDialog(self)
            cookie_header = dlg.exec_login()
            if not cookie_header:
                # Treat cancellation of the embedded login dialog as a
                # user cancel for the sync operation; do not fall back
                # to manual paste in this case.
                return None, False

            # Ask whether to remember the cookie for future refreshes.
            reply = QMessageBox.question(
                self,
                "Remember cookie?",
                "Remember this Cookie header for future refreshes?\n\n"
                "You can always clear or replace it later by providing a\n"
                "new header the next time you are prompted.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            remember = reply == QMessageBox.Yes
            return cookie_header, remember

        # Fallback: manual cookie paste when Qt WebEngine is not
        # available in the environment. In this mode, the user logs in
        # to Overleaf in their regular browser and copies the Cookie
        # header from the browser's developer tools.
        text, ok = QInputDialog.getMultiLineText(
            self,
            "Overleaf Cookie Header",
            "Qt WebEngine is not available in this environment, so the\n"
            "embedded Overleaf login dialog cannot be used.\n\n"
            "Instead, please log in to Overleaf in your browser and\n"
            "paste the Cookie header for a request to the project\n"
            "dashboard:\n\n"
            "  1. Open the Overleaf projects page in your browser.\n"
            "  2. Use the browser's developer tools to inspect a request\n"
            "     to the project dashboard.\n"
            "  3. Copy the full Cookie header and paste it here.",
            "",
        )
        if not ok:
            return None, False

        cookie = text.strip()
        if not cookie:
            return None, False

        reply = QMessageBox.question(
            self,
            "Remember cookie?",
            "Remember this Cookie header for future refreshes?\n\n"
            "You can always clear or replace it later by providing a\n"
            "new header the next time you are prompted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        remember = reply == QMessageBox.Yes

        return cookie, remember

    def _on_folder_selected(self, key: object) -> None:
        """
        Slot called when the user selects a node in the project tree.

        Forwards the selection key (All Projects, Pinned, Archived,
        Home, or a specific folder path) to the proxy model's folder
        filter, which shows projects assigned directly to that folder.

        Before applying the filter, this checks whether the on-disk
        projects-info or directory-structure data changed externally
        (e.g., due to edits on this or another machine) and, if so,
        offers to reload from disk.
        """
        self._check_external_metadata_change()
        # Proceed normally regardless; reload already applied if chosen.
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
        the local directory-structure data via ``create_folder()``.
        Before performing any changes, this checks whether the on-disk
        projects-info or directory-structure data changed externally and,
        if so, offers to reload from disk. Finally reloads the project
        index and folder tree.
        """
        self._check_external_metadata_change()
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
        project assignments in the local directory-structure data via
        ``rename_folder()``, and reloads the project index and folder
        tree. Before performing any changes, this checks whether the
        on-disk projects-info or directory-structure data changed
        externally and, if so, offers to reload from disk.
        """
        self._check_external_metadata_change()
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
        are made. Before performing any changes, this checks whether the
        on-disk projects-info or directory-structure data changed
        externally and, if so, offers to reload from disk.
        """
        self._check_external_metadata_change()
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

        Updates the local directory-structure data via
        ``move_projects_to_folder()`` and reloads the project index and
        folder tree so that both the tree and the table reflect the new
        assignments. Before performing any changes, this checks whether
        the on-disk projects-info or directory-structure data changed
        externally and, if so, offers to reload from disk.
        """
        self._check_external_metadata_change()
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

    def _on_tree_double_clicked(self, index: QModelIndex) -> None:
        """
        Slot invoked when the user double-clicks a node in the folder tree.

        If the special "All Projects" node is double-clicked, open the
        Overleaf projects dashboard in the default web browser.

        Before opening the browser, this checks whether the on-disk
        projects-info or directory-structure data changed externally and,
        if so, offers to reload from disk.
        """
        # Check for external changes (e.g. edits from another machine)
        # before acting on the current selection.
        self._check_external_metadata_change()

        if not index.isValid():
            return

        # The ProjectTree stores the logical folder key (e.g. ALL_KEY,
        # PINNED_KEY, ARCHIVED_KEY, "" for Home, or a folder path)
        # under FolderPathRole. We use that to detect the All Projects
        # node.
        item_key = index.data(FolderPathRole)
        if item_key != ALL_KEY:
            return

        # Open the main Overleaf projects page. This URL mirrors the
        # dashboard that lists all projects in the user's Overleaf
        # account and is derived from the active profile's Overleaf
        # base URL.
        base = get_overleaf_base_url().strip().rstrip("/")
        if not base:
            base = "https://www.overleaf.com"
        overleaf_projects_url = QUrl(f"{base}/project")
        QDesktopServices.openUrl(overleaf_projects_url)

    def _on_table_double_clicked(self, index: QModelIndex) -> None:
        """
        Slot invoked when the user double-clicks a row in the project table.

        Opens the corresponding project URL in the default web browser.

        Before opening the browser, this checks whether the on-disk
        projects-info or directory-structure data changed externally and,
        if so, offers to reload from disk.
        """
        # Check for external changes (e.g. edits from another machine)
        # before acting on the current selection.
        self._check_external_metadata_change()

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

    # Perform data initialization (profile root, initial sync, load
    # projects) after the window is visible so that any dialogs (for
    # choosing a profile directory or pasting a cookie header) appear
    # in a clear context.
    window.initialize_data()

    # If initialization requested an early exit (e.g. the user chose
    # to exit from an error dialog), do not enter the event loop.
    if getattr(window, "_should_exit", False):
        return

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
