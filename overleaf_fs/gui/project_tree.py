"""
Tree widget for navigating Overleaf projects by virtual folder.

Design
------
The project tree shows a Finder-like folder hierarchy on the left side
of the GUI. It is not derived from Overleaf; it reflects only the local
organizational structure stored in the directory-structure store
(``directory_structure_store`` module):

- Special nodes:
    * All Projects
    * Pinned
    * Archived (virtual view of archived projects)

- Folder nodes:
    A nested hierarchy defined by the explicit list of folder paths
    stored in the directory-structure JSON file (e.g. "CT",
    "Teaching/2025").

Selecting a node emits ``folderSelected`` with one of:
    "__ALL__"        → show all projects
    "__PINNED__"     → show locally pinned projects
    "__ARCHIVED__"   → show archived projects (virtual view)
    "folder/path"    → a real virtual folder path

The MainWindow listens for ``folderSelected`` and updates the proxy
filter accordingly.
"""
from __future__ import annotations

import json

from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QModelIndex, QTimer, QMimeData, QPoint
from PySide6.QtGui import QStandardItem, QStandardItemModel, QDrag
from PySide6.QtWidgets import (
    QTreeView,
    QMenu,
    QAbstractItemView,
    QStyleOptionViewItem,
    QStyle,
)


# Custom role for storing folder paths on tree items.
FolderPathRole = Qt.UserRole + 1

# Sentinel values for special nodes.
ALL_KEY = "__ALL__"
PINNED_KEY = "__PINNED__"
ARCHIVED_KEY = "__ARCHIVED__"

# MIME type for drags originating from the project table view.
PROJECT_IDS_MIME = "application/x-overleaf-fs-project-ids"

# MIME type for drags originating from the tree's folder nodes.
FOLDER_PATH_MIME = "application/x-overleaf-fs-folder-path"

class ProjectTree(QTreeView):
    """
    Tree widget displaying the virtual folder hierarchy and special
    filtering nodes for Overleaf projects.

    Emits:
        folderSelected (str | None): A folder path or sentinel string
            indicating what filter should be applied (e.g. All Projects,
            Pinned, or a specific folder).
        createFolderRequested (str | None): Request to create a new folder
            under the given parent folder path (or at the top level if
            None).
        renameFolderRequested (str): Request to rename the folder with
            the given path.
        deleteFolderRequested (str): Request to delete the folder subtree
            rooted at the given path.
        moveProjectsRequested (List[str], str | None): Request to move the
            given project ids into the target folder path (or Home if the
            path is None/empty).
        moveFolderRequested (str, str | None): Request to move the folder
            subtree rooted at the first path under the given parent folder
            path (or Home if the parent is None/empty).
    """

    folderSelected = Signal(object)
    createFolderRequested = Signal(object)
    renameFolderRequested = Signal(str)
    deleteFolderRequested = Signal(str)
    moveProjectsRequested = Signal(list, object)
    moveFolderRequested = Signal(object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.setHeaderHidden(True)

        # Accept drops from the project table and from internal folder
        # drags. We override dropEvent so that the tree does not directly
        # mutate its model when a drop occurs.
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

        # Index of the folder currently highlighted as a potential drop
        # target during a drag-and-drop operation. This is used only for
        # painting and does not affect the actual selection or the
        # folder filter.
        self._drop_highlight_index: QModelIndex = QModelIndex()

        # Timer for auto-expanding folders while hovering during a drag.
        # When the user hovers over a collapsed folder for a short time,
        # we expand that folder so that subfolders become available as
        # drop targets.
        self._expand_timer = QTimer(self)
        self._expand_timer.setSingleShot(True)
        self._expand_timer.setInterval(600)  # milliseconds
        self._expand_timer.timeout.connect(self._on_expand_timer_timeout)
        self._pending_expand_index: QModelIndex = QModelIndex()

        # Source folder path for the current internal folder drag, if any.
        # This is populated by _handle_drag_event for folder drags and
        # consumed by dropEvent so we do not have to re-parse the mime
        # data when the drop occurs.
        self._current_folder_drag_source: Optional[str] = None

        # Clicking on a tree node updates the current folder selection,
        # which drives the table's folder filter in the main window.
        self.selectionModel().currentChanged.connect(self._on_current_changed)
    def _schedule_expand_index(self, index: QModelIndex) -> None:
        """Schedule auto-expand for the given folder index.

        If the index is invalid, corresponds to a non-folder node, or is
        already expanded, this cancels any pending expand.
        """
        # Only consider valid indices that correspond to folder drop
        # targets (Home or a real folder path).
        folder_path = self._folder_path_for_index(index)
        item = self._model.itemFromIndex(index) if index.isValid() else None

        if (
            not index.isValid()
            or folder_path is None
            or item is None
            or item.rowCount() == 0
            or self.isExpanded(index)
        ):
            # Nothing to expand or not a real folder node.
            self._expand_timer.stop()
            self._pending_expand_index = QModelIndex()
            return

        if self._pending_expand_index == index and self._expand_timer.isActive():
            # Already scheduled for this index.
            return

        self._pending_expand_index = index
        self._expand_timer.start()

    def _on_expand_timer_timeout(self) -> None:
        """Expand the folder we have been hovering over during a drag."""
        if not self._pending_expand_index.isValid():
            return

        # Double-check that the index is still a valid folder target
        # before expanding.
        folder_path = self._folder_path_for_index(self._pending_expand_index)
        if folder_path is None:
            return

        self.expand(self._pending_expand_index)
        self._pending_expand_index = QModelIndex()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_folders(self, folder_paths: List[str]) -> None:
        """
        Populate the tree structure based on the given folder paths.

        Args:
            folder_paths (List[str]): List of folder paths such as
                ["CT", "Teaching", "Teaching/2025"].
        """
        self._model.clear()
        self._model.setHorizontalHeaderLabels(["Home"])
        root = self._model.invisibleRootItem()

        # Special top-level nodes.
        self._add_special_node(root, "All Projects", ALL_KEY)
        self._add_special_node(root, "Pinned", PINNED_KEY)
        self._add_special_node(root, "Archived", ARCHIVED_KEY)

        # Container for actual folders.
        folders_root = QStandardItem("Home")
        folders_root.setEditable(False)
        folders_root.setData(None, FolderPathRole)
        root.appendRow(folders_root)

        # Build folder hierarchy.
        for path in sorted(folder_paths):
            self._insert_folder_path(folders_root, path)

        # By default expand only the top-level Home container. Nested
        # folders can be expanded manually or via drag-hover
        # auto-expand; we avoid expanding every folder on each reload.
        self.expand(folders_root.index())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _add_special_node(self, parent: QStandardItem, label: str, key: str) -> None:
        item = QStandardItem(label)
        item.setEditable(False)
        item.setData(key, FolderPathRole)
        parent.appendRow(item)

    def _insert_folder_path(self, parent: QStandardItem, path: str) -> None:
        """Insert a folder path like 'Teaching/2025' into the tree."""
        parts = path.split("/")
        node = parent

        for depth, name in enumerate(parts):
            child = self._find_child(node, name)
            if child is None:
                child = QStandardItem(name)
                child.setEditable(False)
                if depth == len(parts) - 1:
                    child.setData(path, FolderPathRole)
                else:
                    child.setData(None, FolderPathRole)
                node.appendRow(child)
            node = child

    def _find_child(self, parent: QStandardItem, label: str) -> Optional[QStandardItem]:
        """Return the first child of parent whose text matches label."""
        for row in range(parent.rowCount()):
            child = parent.child(row)
            if child.text() == label:
                return child
        return None

    def _set_drop_highlight_index(self, index: QModelIndex) -> None:
        """Update which row is highlighted as the current drop target.

        An invalid index (``QModelIndex()``) means "no highlight".
        """
        if self._drop_highlight_index == index:
            return
        self._drop_highlight_index = index
        self.viewport().update()

    def _folder_path_for_index(self, index) -> Optional[str]:
        """
        Resolve the effective folder path for a drop/selection index.

        Returns:
            str | None: The folder path string, or "" for the Home node,
            or None if the index does not correspond to a valid folder
            drop target (e.g. All Projects or Pinned or Archived).
        """
        if not index.isValid():
            return None

        item = self._model.itemFromIndex(index)
        if item is None:
            return None

        key = item.data(FolderPathRole)
        text = item.text()

        if key in (ALL_KEY, PINNED_KEY, ARCHIVED_KEY):
            # Not a valid drop target.
            return None
        if key is None and text == "Home":
            # Home root node.
            return ""
        if isinstance(key, str):
            # Real folder path.
            return key
        return None

    def mimeData(self, indexes):  # type: ignore[override]
        """Attach folder-path information for internal drags.

        For drags that originate from folder nodes in this tree, we
        encode the source folder path using a custom MIME type so that
        drop handlers can infer the subtree root being moved.

        We construct a fresh QMimeData instance here rather than
        delegating to the base implementation, since the view's role
        as a drag source is limited to folder moves.
        """
        mime = QMimeData()

        # Find the first index that corresponds to a real folder path.
        for index in indexes:
            folder_path = self._folder_path_for_index(index)
            # We only allow drags for real folder paths (not Home or
            # special nodes), so skip empty-string and None.
            if not folder_path:
                continue
            try:
                payload = json.dumps({"folder_path": folder_path}).encode("utf-8")
            except Exception:
                continue
            mime.setData(FOLDER_PATH_MIME, payload)
            break

        return mime

    def supportedDropActions(self) -> Qt.DropActions:  # type: ignore[override]
        """Indicate that drops represent move operations.

        The tree itself does not apply the move directly; it emits
        high-level signals (e.g. moveProjectsRequested,
        moveFolderRequested) which a controller can handle.
        """
        return Qt.MoveAction

    def select_folder_key(self, key: Optional[str]) -> None:
        """
        Select the tree node corresponding to the given folder key.

        Args:
            key (Optional[str]): One of:
                - ALL_KEY,
                - PINNED_KEY,
                - ARCHIVED_KEY,
                - "" or None (Home),
                - a folder path string.
        """
        root = self._model.invisibleRootItem()
        if root is None:
            return

        def _match_item(item) -> bool:
            item_key = item.data(FolderPathRole)
            text = item.text()
            if key == ALL_KEY:
                return item_key == ALL_KEY
            if key == PINNED_KEY:
                return item_key == PINNED_KEY
            if key == ARCHIVED_KEY:
                return item_key == ARCHIVED_KEY
            if key in (None, ""):
                return item_key is None and text == "Home"
            return isinstance(item_key, str) and item_key == key

        def _dfs_find(item):
            if _match_item(item):
                return item
            for i in range(item.rowCount()):
                child = item.child(i)
                if child is None:
                    continue
                found = _dfs_find(child)
                if found is not None:
                    return found
            return None

        for r in range(root.rowCount()):
            top = root.child(r)
            if top is None:
                continue
            found = _dfs_find(top)
            if found is not None:
                self.setCurrentIndex(found.index())
                return

    # ------------------------------------------------------------------
    # Selection handling
    # ------------------------------------------------------------------

    def _on_current_changed(self, current, previous) -> None:
        """Emit folderSelected when a tree item is selected."""
        item = self._model.itemFromIndex(current)
        if item is None:
            return
        key = item.data(FolderPathRole)
        self.folderSelected.emit(key)

    def contextMenuEvent(self, event) -> None:
        """
        Show a context menu for basic folder operations.

        The tree itself does not directly modify the directory-structure
        data; instead it emits high-level signals that a controller
        (e.g. MainWindow) can handle by updating the local directory-
        structure (``LocalDirectoryStructure``) via ``directory_structure_store``.
        """
        index = self.indexAt(event.pos())
        item = self._model.itemFromIndex(index) if index.isValid() else None

        menu = QMenu(self)

        # Determine what kind of node we are on.
        if item is None:
            # Clicked on empty area: allow creating a top-level folder
            # under the "Home" root.
            parent_path = None
            new_action = menu.addAction("New folder…")
            new_action.triggered.connect(
                lambda checked=False, pp=parent_path: self.createFolderRequested.emit(pp)
            )
        else:
            key = item.data(FolderPathRole)
            text = item.text()

            if key in (ALL_KEY, PINNED_KEY, ARCHIVED_KEY):
                # Special nodes: no rename/delete, but allow new folder
                # under the top-level "Home" container.
                parent_path = None
                new_action = menu.addAction("New folder…")
                new_action.triggered.connect(
                    lambda checked=False, pp=parent_path: self.createFolderRequested.emit(pp)
                )
            elif key is None and text == "Home":
                # The "Home" root: new folder at top level.
                parent_path = None
                new_action = menu.addAction("New folder…")
                new_action.triggered.connect(
                    lambda checked=False, pp=parent_path: self.createFolderRequested.emit(pp)
                )
            else:
                # A real folder node.
                folder_path = key
                # New subfolder under this folder.
                new_action = menu.addAction("New subfolder…")
                new_action.triggered.connect(
                    lambda checked=False, fp=folder_path: self.createFolderRequested.emit(fp)
                )
                # Rename this folder.
                rename_action = menu.addAction("Rename folder…")
                rename_action.triggered.connect(
                    lambda checked=False, fp=folder_path: self.renameFolderRequested.emit(fp)
                )
                # Delete this folder.
                delete_action = menu.addAction("Delete folder…")
                delete_action.triggered.connect(
                    lambda checked=False, fp=folder_path: self.deleteFolderRequested.emit(fp)
                )

        if not menu.isEmpty():
            menu.exec(event.globalPos())

    # ------------------------------------------------------------------
    # Drag-and-drop handling (drop target for project ids)
    # ------------------------------------------------------------------
    def startDrag(self, supportedActions) -> None:  # type: ignore[override]
        """Start a drag operation for folder nodes in the tree.

        We override the default implementation so that the drag uses
        the view's ``mimeData`` method, which attaches the custom
        ``FOLDER_PATH_MIME`` payload for folder drags. This ensures
        that dragEnterEvent/dragMoveEvent/dropEvent can treat folder
        drags consistently with project drags (highlighting, auto-
        expand, and high-level moveFolderRequested signals).

        If the current selection does not correspond to a real folder
        path (e.g. All Projects, Pinned, Archived, or Home), we fall
        back to the base implementation so that any default Qt
        behavior remains intact.
        """
        # Prefer the current index as the source of a folder drag; this
        # allows the user to start a drag anywhere along the row for
        # the currently selected folder (including the whitespace to
        # the right of the label).
        folder_index: Optional[QModelIndex] = None

        current = self.currentIndex()
        if current.isValid() and self._folder_path_for_index(current):
            folder_index = current
        else:
            # Fallback: search the selected indexes for any folder node.
            for index in self.selectedIndexes():
                if self._folder_path_for_index(index):
                    folder_index = index
                    break

        if folder_index is None:
            # No real folder node is selected; delegate to the default
            # behavior so that any built-in Qt drag handling remains
            # available for non-folder items.
            super().startDrag(supportedActions)
            return

        mime = self.mimeData([folder_index])
        if mime is None:
            super().startDrag(supportedActions)
            return

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def _index_for_drag_position(self, pos) -> QModelIndex:
        """Return a reasonable index for a drag position within the tree.

        We first try indexAt(pos). If that fails (e.g. because the
        cursor is slightly left/right of the item rectangle), we fall
        back to probing along the same vertical position but with the
        x-coordinate clamped into the viewport. This makes drag-hover
        behavior more forgiving to horizontal wobble during a drag.
        """
        index = self.indexAt(pos)
        if index.isValid():
            return index

        viewport = self.viewport()
        rect = viewport.rect()

        # If the y-position is outside the visible area, we cannot infer
        # a reasonable row.
        if pos.y() < rect.top() or pos.y() > rect.bottom():
            return QModelIndex()

        # Clamp x into the viewport and probe again at the same y.
        clamped_x = min(max(pos.x(), rect.left() + 1), rect.right() - 1)
        probe = QPoint(clamped_x, pos.y())
        index = self.indexAt(probe)
        if index.isValid():
            return index

        return QModelIndex()

    def _handle_drag_event(self, event) -> None:
        """Shared handler for drag enter/move events over the tree.

        This centralizes the logic for validating mime data, updating
        the drop-highlight index, and scheduling auto-expansion so that
        dragEnterEvent and dragMoveEvent behave consistently.
        """
        mime = event.mimeData()
        # Default to no known source folder for this drag event; we will
        # populate this when we successfully decode a folder-path drag.
        self._current_folder_drag_source = None

        if not mime:
            self._set_drop_highlight_index(QModelIndex())
            self._schedule_expand_index(QModelIndex())
            event.ignore()
            return

        has_projects = mime.hasFormat(PROJECT_IDS_MIME)
        has_folder = mime.hasFormat(FOLDER_PATH_MIME)
        if not has_projects and not has_folder:
            self._set_drop_highlight_index(QModelIndex())
            self._schedule_expand_index(QModelIndex())
            event.ignore()
            return

        # Decode source folder for folder drags so we can avoid
        # obviously invalid targets (e.g. moving into its own subtree).
        source_folder: Optional[str] = None
        if has_folder:
            try:
                data = bytes(mime.data(FOLDER_PATH_MIME))
                payload = json.loads(data.decode("utf-8"))
                if isinstance(payload, dict):
                    sf = payload.get("folder_path")
                    if isinstance(sf, str) and sf:
                        source_folder = sf.rstrip("/")
            except Exception:
                source_folder = None
        # Remember the decoded source folder path so that dropEvent can
        # reuse it without re-parsing the mime data.
        self._current_folder_drag_source = source_folder

        index = self._index_for_drag_position(event.pos())

        folder_path = self._folder_path_for_index(index)

        # For project drops, we simply require a valid folder target.
        # For folder drops, additionally avoid obviously invalid
        # targets such as dropping into the same folder or a descendant.
        if folder_path is None:
            # We care about this drag (it carries our MIME type), but
            # the current position is not a valid drop target. Clear
            # any visual highlight and cancel pending expand, but keep
            # accepting the drag so that we continue to receive
            # dragMove events as the user moves over other rows.
            self._set_drop_highlight_index(QModelIndex())
            self._schedule_expand_index(QModelIndex())
            event.acceptProposedAction()
            return

        if source_folder:
            # Prevent dropping a folder onto itself or into its own subtree.
            if folder_path == source_folder or folder_path.startswith(source_folder + "/"):
                # Again, this drag is one we understand, but this
                # particular hover location is not a valid target.
                # Clear highlight and continue accepting so that the
                # user can move to a different folder without losing
                # drag feedback.
                self._set_drop_highlight_index(QModelIndex())
                self._schedule_expand_index(QModelIndex())
                event.acceptProposedAction()
                return

        # Highlight the potential drop target and schedule auto-expand.
        self._set_drop_highlight_index(index)
        self._schedule_expand_index(index)
        event.acceptProposedAction()

    def dragEnterEvent(self, event) -> None:
        """Accept drags that carry project ids or folder paths."""
        self._handle_drag_event(event)

    def dragMoveEvent(self, event) -> None:
        """Continue to accept drags over valid folder targets."""
        self._handle_drag_event(event)

    def dragLeaveEvent(self, event) -> None:
        """Clear any drop highlight when the drag leaves the tree."""
        self._set_drop_highlight_index(QModelIndex())
        self._schedule_expand_index(QModelIndex())
        self._current_folder_drag_source = None
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        """
        Handle a drop of project ids or folder paths onto a folder node.

        For project drops, the tree simply parses the project ids from
        the mime data and emits a high-level moveProjectsRequested
        signal. For folder drops, it emits moveFolderRequested with
        the source folder path and the target parent folder path.

        The tree itself does not mutate the directory-structure data;
        instead a controller (e.g. MainWindow) is responsible for
        applying the move via ``directory_structure_store``.
        """
        # Clear any visual drop highlight and pending auto-expand now
        # that the drop is complete.
        self._set_drop_highlight_index(QModelIndex())
        self._schedule_expand_index(QModelIndex())

        mime = event.mimeData()
        if not mime:
            event.ignore()
            return

        has_projects = mime.hasFormat(PROJECT_IDS_MIME)
        has_folder = mime.hasFormat(FOLDER_PATH_MIME)
        if not has_projects and not has_folder:
            event.ignore()
            return

        index = self._index_for_drag_position(event.pos())
        folder_path = self._folder_path_for_index(index)
        if folder_path is None:
            event.ignore()
            return

        # Folder move: emit moveFolderRequested(old_root, new_parent)
        if has_folder:
            # Prefer the source folder decoded during dragEnter/dragMove.
            source_folder = self._current_folder_drag_source

            # As a fallback, attempt to decode again from the mime data
            # in case this drop did not pass through our drag handlers.
            if not source_folder:
                try:
                    data = bytes(mime.data(FOLDER_PATH_MIME))
                    payload = json.loads(data.decode("utf-8"))
                    if isinstance(payload, dict):
                        sf = payload.get("folder_path")
                        if isinstance(sf, str) and sf:
                            source_folder = sf.rstrip("/")
                except Exception:
                    source_folder = None

            if not source_folder:
                event.ignore()
                return

            # Avoid obviously invalid moves (into itself or into a descendant).
            if folder_path == source_folder or folder_path.startswith(source_folder + "/"):
                event.ignore()
                return

            # Home is represented by the empty string "" at this stage;
            # callers may normalize this if they wish to treat None
            # and "" equivalently.
            self.moveFolderRequested.emit(source_folder, folder_path)
            event.acceptProposedAction()
            # Clear the remembered source for this drag now that the
            # drop has been handled.
            self._current_folder_drag_source = None
            return

        # Project move: fall back to the existing behavior.
        if not has_projects:
            event.ignore()
            return

        try:
            data = bytes(mime.data(PROJECT_IDS_MIME))
            project_ids = json.loads(data.decode("utf-8"))
        except Exception:
            event.ignore()
            return

        if not isinstance(project_ids, list):
            event.ignore()
            return

        # Filter to strings only.
        cleaned_ids = [pid for pid in project_ids if isinstance(pid, str)]
        if not cleaned_ids:
            event.ignore()
            return

        # Emit the high-level move request. Home is represented by the
        # empty string "" at this stage; callers may normalize this if
        # they wish to treat None and "" equivalently.
        self.moveProjectsRequested.emit(cleaned_ids, folder_path)
        event.acceptProposedAction()

    def drawRow(self, painter, options, index) -> None:  # type: ignore[override]
        """Draw rows, adding a highlight for the current drop target.

        The currently selected folder is normally drawn using the active
        (blue) selection color so that it remains visually distinct as
        the "current folder" even when focus is on the project table.

        The drop target row (if any) is drawn using the inactive
        selection appearance (typically gray) so that it is visible as a
        hover target without being confused with the current folder.
        """
        opt = QStyleOptionViewItem(options)

        selection_model = self.selectionModel()
        current_index = selection_model.currentIndex() if selection_model is not None else QModelIndex()

        is_drop = self._drop_highlight_index.isValid() and index == self._drop_highlight_index
        is_current = current_index.isValid() and index == current_index

        # Start from a neutral selection state so that we fully control
        # how the row is painted, rather than inheriting any "active"
        # flags from the base options.
        opt.state &= ~QStyle.State_Selected
        opt.state &= ~QStyle.State_Active
        opt.state &= ~QStyle.State_HasFocus

        # Give the drop target precedence over the "current" selection
        # so that, during a drag, the hovered row is drawn using the
        # inactive selection appearance (typically gray) even if it also
        # happens to be the current folder.
        if is_drop:
            opt.state |= QStyle.State_Selected
        elif is_current:
            # Ensure the current folder is painted as an active, focused
            # selection when it is not also the drop target.
            opt.state |= QStyle.State_Selected
            opt.state |= QStyle.State_Active
            opt.state |= QStyle.State_HasFocus

        super().drawRow(painter, opt, index)
