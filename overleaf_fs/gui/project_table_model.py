"""
Qt table model for displaying Overleaf projects in a QTableView.

Summary of design:
- The core data model for projects lives in ``overleaf_fs.core.models`` as
  ``ProjectRecord`` and ``ProjectsIndex``, which cleanly separate
  "remote" projects‑info fields (mirrored from Overleaf: id, name, owner,
  last modified, URL, archived) from "local" directory‑structure fields
  (folder, notes, pinned, hidden).
- This file provides a thin adapter layer between that in-memory
  representation and the Qt view system by implementing a
  ``QAbstractTableModel``. The GUI can attach a ``QTableView`` (and
  optionally a ``QSortFilterProxyModel`` for searching and sorting)
  to this model to present a Finder-like table of Overleaf projects.
- The table currently exposes four columns: Name, Owner, Last
  Modified, and Local folder. These are enough to make the GUI useful
  while keeping the model simple. Additional columns can be added
  later by extending ``_COLUMN_DEFINITIONS`` without changing the rest
  of the application.
- Archived projects (as indicated by the remote projects‑info fields) are
  displayed using a muted text color to provide a subtle visual cue
  without changing their names or local folder assignments.
- The model is read-only for now: editing of local directory‑structure fields
  (e.g. folder assignment, notes, pinned, hidden) will be handled via
  dedicated dialogs or other UI elements that update the underlying
  ``ProjectRecord`` objects and then notify the model to refresh.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from datetime import datetime

from overleaf_fs.core.models import ProjectsIndex, ProjectRecord


def _format_last_modified_local(remote) -> str:
    """
    Format the remote last-modified timestamp in local time, if possible.

    If a parsed datetime object is available on ``remote.last_modified``,
    it is converted to the local timezone (when timezone-aware) and
    formatted as ``YYYY-MM-DD HH:MM:SS``. If no parsed datetime is
    available, fall back to the raw string reported by Overleaf.
    """
    dt = getattr(remote, "last_modified", None)
    if dt is not None:
        try:
            # If the datetime is timezone-aware, convert to local time;
            # otherwise, assume it is already in local time.
            if isinstance(dt, datetime) and dt.tzinfo is not None:
                local_dt = dt.astimezone()
            else:
                local_dt = dt
            return local_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            # As a fallback, use ISO formatting similar to the previous behavior.
            try:
                return dt.isoformat(sep=" ", timespec="seconds")
            except Exception:
                pass
    # Fall back to the raw string if no parsed datetime is available.
    return getattr(remote, "last_modified_raw", "") or ""


class ProjectTableModel(QAbstractTableModel):
    """
    Qt table model for a list of Overleaf projects.

    The model is backed by a flat list of ``ProjectRecord`` instances.
    Higher-level code (e.g. the main window or a controller) is
    responsible for constructing a ``ProjectsIndex`` from the combined
    projects‑info and directory‑structure stores and passing it into this model via :meth:`set_projects`.

    Rows correspond to individual projects; columns correspond to
    specific fields (remote name/owner/last‑modified and the local folder
    assignment). The model is read-only and intended to be combined with a
    ``QSortFilterProxyModel`` for sorting and filtering in the GUI.
    """

    # Column indices for clarity and to avoid magic numbers.
    COLUMN_PINNED = 0
    COLUMN_NAME = 1
    COLUMN_OWNER = 2
    COLUMN_FOLDER = 3
    COLUMN_LAST_MODIFIED = 4

    _COLUMN_DEFINITIONS = [
        (COLUMN_PINNED, "Pin"),
        (COLUMN_NAME, "Name"),
        (COLUMN_OWNER, "Owner"),
        (COLUMN_LAST_MODIFIED, "Last modified"),
        (COLUMN_FOLDER, "Local folder"),
    ]

    def __init__(
        self,
        project_index: Optional[ProjectsIndex] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._records: List[ProjectRecord] = []
        if project_index is not None:
            self.set_projects(project_index)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_projects(self, project_index: ProjectsIndex) -> None:
        """
        Replace the current list of projects with the values from
        ``project_index`` and notify attached views.

        The provided index is a mapping from project id to
        ``ProjectRecord``. We convert this to a flat list, which is
        what Qt expects for a table model. The ordering is not
        guaranteed; a higher-level ``QSortFilterProxyModel`` can
        impose a user-facing sorting when attached to a view.

        This method wraps the internal data replacement in
        ``beginResetModel()``/``endResetModel()`` so that any attached
        views (and proxy models) are fully refreshed whenever the
        project list changes, including after a reload from disk or a
        sync with Overleaf.

        Args:
            project_index (ProjectsIndex): Mapping from project id to
                ProjectRecord.
        """
        records = list(project_index.values())
        self.beginResetModel()
        self._records = records
        self.endResetModel()

    def project_at(self, row: int) -> Optional[ProjectRecord]:
        """
        Return the project record at the given row, or ``None`` if the
        row is out of range.

        This is useful for controllers or view code that need to act on
        the underlying ``ProjectRecord`` when a table row is selected or
        activated.

        Args:
            row (int): Row index in the table.

        Returns:
            Optional[ProjectRecord]: The project record at the given row,
            or ``None`` if the row is invalid.
        """
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    # ------------------------------------------------------------------
    # QAbstractTableModel implementation
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._COLUMN_DEFINITIONS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if not (0 <= row < len(self._records)):
            return None

        record = self._records[row]

        if role == Qt.DisplayRole:
            if col == self.COLUMN_PINNED:
                return "★" if record.local.pinned else ""
            if col == self.COLUMN_NAME:
                return record.remote.name
            if col == self.COLUMN_OWNER:
                display_name = getattr(record.remote, "owner_display_name", None)
                owner_label = record.remote.owner_label or ""
                if display_name and owner_label and display_name != owner_label:
                    # Show both the human-friendly name and the stable label
                    # (typically email/login), so that both are visible and
                    # searchable in the table.
                    return f"{display_name} ({owner_label})"
                # Fall back to whichever identifier we have.
                return display_name or owner_label or ""
            if col == self.COLUMN_LAST_MODIFIED:
                # Prefer the parsed datetime and display it in local time if
                # available; otherwise fall back to the raw string reported
                # by Overleaf in the remote projects-info data.
                return _format_last_modified_local(record.remote)
            if col == self.COLUMN_FOLDER:
                # Show the local folder assignment from the directory‑structure fields,
                # treating the Home folder (no explicit folder) as "Home" for readability.
                folder = record.local.folder
                return folder if folder not in (None, "") else "Home"

        if role == Qt.ForegroundRole:
            # Use a muted text color for archived projects (as indicated by the
            # remote projects‑info fields) to provide a subtle visual cue while
            # leaving underlying data unchanged.
            if getattr(record.remote, "archived", False):
                return QColor(Qt.darkGray)

        if role == Qt.TextAlignmentRole:
            if col == self.COLUMN_PINNED:
                return int(Qt.AlignVCenter | Qt.AlignCenter)
            # Left-align text in all columns for now.
            return int(Qt.AlignVCenter | Qt.AlignLeft)

        if role == Qt.ToolTipRole:
            # Show a multi-line summary of the project when hovering over any cell.
            remote = record.remote
            local = record.local

            name = remote.name
            owner_display = getattr(remote, "owner_display_name", None)
            owner_label = remote.owner_label or ""
            if owner_display and owner_label and owner_display != owner_label:
                owner_str = f"{owner_display} ({owner_label})"
            else:
                owner_str = owner_display or owner_label or ""

            # Prefer the parsed datetime for the tooltip as well, formatted
            # in local time when possible.
            last_mod_str = _format_last_modified_local(remote)

            folder = local.folder if local.folder not in (None, "") else "Home"
            archived_str = "Yes" if getattr(remote, "archived", False) else "No"
            url = remote.url

            lines = [
                f"Name: {name}",
                f"Owner: {owner_str}" if owner_str else "Owner: (unknown)",
                f"Folder: {folder}",
            ]
            if last_mod_str:
                lines.append(f"Last modified: {last_mod_str}")
            lines.append(f"Archived: {archived_str}")
            if url:
                lines.append(f"URL: {url}")

            return "\n".join(lines)

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ):
        """
        Provide human-readable headers for the columns.

        We only customize horizontal headers; vertical headers (row
        numbers) are left to Qt's default implementation.

        Args:
            section (int): Section index (column or row).
            orientation (Qt.Orientation): Header orientation.
            role (int): Data role.

        Returns:
            Any: Header text for horizontal headers, or the default
            implementation's result for vertical headers and other roles.
        """
        if role != Qt.DisplayRole:
            return None

        if orientation == Qt.Horizontal:
            for col_index, title in self._COLUMN_DEFINITIONS:
                if section == col_index:
                    return title
            return ""

        return super().headerData(section, orientation, role)

    def flags(self, index: QModelIndex):  # type: ignore[override]
        """
        Items are selectable and enabled but not editable.

        Editing of local directory‑structure fields (folder, notes, pinned, hidden)
        can be implemented later by overriding this method and adding the
        ``Qt.ItemIsEditable`` flag for specific columns, together with
        an implementation of ``setData``.

        Args:
            index (QModelIndex): Index of the item.

        Returns:
            Qt.ItemFlags: Item flags indicating enabled/selectable state.
        """
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled
