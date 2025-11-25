from __future__ import annotations

"""
Tree widget for navigating Overleaf projects by virtual folder.

Design
------
The project tree shows a Finder-like folder hierarchy on the left side
of the GUI. It is not derived from Overleaf; it reflects only the local
organizational structure stored in ``metadata_store``:

- Special nodes:
    * All Projects
    * Pinned

- Folder nodes:
    A nested hierarchy defined by the explicit list of folder paths
    stored in the metadata file (e.g. "CT", "Teaching/2025").

Selecting a node emits ``folderSelected`` with one of:
    "__ALL__"      → show all projects
    "__PINNED__"   → show locally pinned projects
    "folder/path"  → a real virtual folder path

The MainWindow listens for ``folderSelected`` and updates the proxy
filter accordingly.
"""

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QTreeView, QMenu


# Custom role for storing folder paths on tree items.
FolderPathRole = Qt.UserRole + 1

# Sentinel values for special nodes.
ALL_KEY = "__ALL__"
PINNED_KEY = "__PINNED__"


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
    """

    folderSelected = Signal(object)
    createFolderRequested = Signal(object)
    renameFolderRequested = Signal(str)
    deleteFolderRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.setHeaderHidden(True)

        # Clicking updates selection.
        self.selectionModel().currentChanged.connect(self._on_current_changed)

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

        # Container for actual folders.
        folders_root = QStandardItem("Home")
        folders_root.setEditable(False)
        folders_root.setData(None, FolderPathRole)
        root.appendRow(folders_root)

        # Build folder hierarchy.
        for path in sorted(folder_paths):
            self._insert_folder_path(folders_root, path)

        self.expandAll()

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

        The tree itself does not modify metadata; instead it emits
        high-level signals that a controller (e.g. MainWindow) can
        handle by updating LocalState via ``metadata_store``.
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

            if key in (ALL_KEY, PINNED_KEY):
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
