

"""Profile manager dialog for OverleafFS.

This module provides a small Qt-based dialog that allows the user to
view, create, rename, and delete profiles. It is intentionally focused
on filesystem-level profile management and delegates the notion of the
"active" profile to higher-level code.

Typical usage from the application entrypoint::

    from overleaf_fs.gui.profile_manager import ProfileManagerDialog

    dlg = ProfileManagerDialog(parent)
    if dlg.exec() == dlg.Accepted:
        selected = dlg.selected_profile
        if selected is not None:
            # Call set_active_profile_id(selected.id) in core.profiles
            # and then relaunch the main window bound to that profile.

The dialog itself does *not* change the active profile; it simply lets
the caller know which profile the user chose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QLabel,
)

from overleaf_fs.core import config
from overleaf_fs.core.profiles import (
    ProfileInfo,
    discover_profiles,
    ensure_default_profile,
    save_profile_info,
    load_profile_info,
    PROFILE_CONFIG_FILENAME,
)


@dataclass
class _ProfileListEntry:
    """Internal helper for representing a profile in the list widget."""

    id: str
    display_name: str

    @property
    def label(self) -> str:
        """Human-friendly label for the list item.

        We show both the display name and the id to make it clear which
        profile is which, especially if two profiles have similar names.
        """

        if self.display_name == self.id:
            return self.display_name
        return f"{self.display_name} ({self.id})"


class ProfileManagerDialog(QDialog):
    """Dialog for viewing and editing OverleafFS profiles.

    This dialog is responsible for basic profile management:

    * Listing existing profiles
    * Creating a new profile
    * Renaming the display name of a profile
    * Deleting a profile configuration (soft delete)

    It does **not** decide which profile is active; instead, callers
    inspect :attr:`selected_profile` after the dialog is accepted and
    call :func:`overleaf_fs.core.profiles.set_active_profile_id` as
    appropriate.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Profile Manager")

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SingleSelection)

        # Buttons along the bottom: New, Rename, Delete, Open, Exit.
        self._open_button = QPushButton("Open", self)
        self._rename_button = QPushButton("Rename…", self)
        self._new_button = QPushButton("New…", self)
        self._delete_button = QPushButton("Delete", self)
        # Make the delete button visually distinct to emphasize that it
        # is a destructive operation.
        self._delete_button.setStyleSheet("color: red;")
        self._exit_button = QPushButton("Exit app", self)

        self._open_button.clicked.connect(self._on_open_clicked)
        self._rename_button.clicked.connect(self._on_rename_profile)
        self._new_button.clicked.connect(self._on_new_profile)
        self._delete_button.clicked.connect(self._on_delete_profile)
        self._exit_button.clicked.connect(self.reject)

        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

        # Layout
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(QLabel("Select a profile to open, or manage profiles:", self))
        main_layout.addWidget(self._list)

        button_row = QHBoxLayout()
        button_row.addWidget(self._open_button)
        button_row.addWidget(self._rename_button)
        button_row.addWidget(self._new_button)
        button_row.addWidget(self._delete_button)
        button_row.addWidget(self._exit_button)

        main_layout.addLayout(button_row)

        self._selected_profile: Optional[ProfileInfo] = None

        self._refresh_profiles()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def selected_profile(self) -> Optional[ProfileInfo]:
        """Profile chosen when the dialog was accepted.

        This is ``None`` until the user clicks "Open" or double-clicks
        an entry that results in the dialog being accepted.
        """

        return self._selected_profile

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_profiles(self) -> None:
        """Reload the profile list from disk.

        The list is populated from the profile configurations found
        under the configured profile root directory. If no profiles
        exist yet, the list will simply be empty until the user creates
        one.
        """

        profiles = discover_profiles()
        if not profiles:
            # Preserve the legacy behavior of having a default "Primary"
            # profile available even if the user has not explicitly
            # created any profiles yet.
            profiles = [ensure_default_profile()]

        self._list.clear()
        for info in profiles:
            entry = _ProfileListEntry(id=info.id, display_name=info.display_name)
            item = QListWidgetItem(entry.label, self._list)
            # Store the profile id in UserRole for later lookup.
            item.setData(Qt.UserRole, info.id)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _current_profile_id(self) -> Optional[str]:
        """Return the id of the currently selected profile, if any."""

        item = self._list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        return value if isinstance(value, str) else None

    def _load_current_profile(self) -> Optional[ProfileInfo]:
        """Load the :class:`ProfileInfo` for the selected list item."""

        profile_id = self._current_profile_id()
        if profile_id is None:
            return None
        return load_profile_info(profile_id)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_new_profile(self) -> None:
        """Create a new profile and refresh the list.

        For now, this prompts for a profile id and optional display
        name. The id must be non-empty and unique. The display name
        defaults to the id if left blank.
        """

        from pathlib import Path as _Path

        profile_id, ok = QInputDialog.getText(
            self,
            "New profile",
            "Profile id (short, filesystem-friendly):",
        )
        if not ok or not profile_id.strip():
            return

        profile_id = profile_id.strip()

        # Check for collisions with existing profiles.
        existing_ids = {p.id for p in discover_profiles()}
        if profile_id in existing_ids:
            QMessageBox.warning(
                self,
                "Profile already exists",
                f"A profile with id '{profile_id}' already exists.",
            )
            return

        display_name, ok = QInputDialog.getText(
            self,
            "New profile",
            "Display name (optional):",
        )
        if not ok:
            return

        display_name = display_name.strip() or profile_id

        info = ProfileInfo(
            id=profile_id,
            display_name=display_name,
            relative_path=_Path(profile_id),
            overleaf_base_url="https://www.overleaf.com",
        )
        save_profile_info(info)
        self._refresh_profiles()

        # Select the newly created profile in the list.
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.data(Qt.UserRole) == profile_id:
                self._list.setCurrentRow(row)
                break

    def _on_rename_profile(self) -> None:
        """Rename the display name of the currently selected profile."""

        info = self._load_current_profile()
        if info is None:
            return

        new_name, ok = QInputDialog.getText(
            self,
            "Rename profile",
            "New display name:",
            text=info.display_name,
        )
        if not ok:
            return

        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(
                self,
                "Invalid name",
                "Display name cannot be empty.",
            )
            return

        info.display_name = new_name
        save_profile_info(info)
        self._refresh_profiles()

    def _on_delete_profile(self) -> None:
        """Delete the configuration for the currently selected profile.

        This performs a *soft* delete: the profile-config JSON file is
        removed, so the profile no longer appears in the profile
        manager, but the underlying directory and data files (such as
        ``overleaf_projects_info.json`` and
        ``local_directory_structure.json``) are left untouched.
        """

        info = self._load_current_profile()
        if info is None:
            return

        answer = QMessageBox.question(
            self,
            "Delete profile",
            f"Really delete profile '{info.display_name}' (id: {info.id})?\n\n"
            "This removes the profile configuration so it no longer appears "
            "in the manager, but leaves the underlying files on disk.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        root = config.get_profile_root_dir()
        profile_dir = root / info.id
        cfg_path = profile_dir / PROFILE_CONFIG_FILENAME
        try:
            if cfg_path.is_file():
                cfg_path.unlink()
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(
                self,
                "Error deleting profile",
                f"Could not delete profile configuration:\n{exc}",
            )
            return

        self._refresh_profiles()

    def _on_open_clicked(self) -> None:
        """Accept the dialog with the currently selected profile."""

        info = self._load_current_profile()
        if info is None:
            QMessageBox.warning(
                self,
                "No profile selected",
                "Please select a profile to open.",
            )
            return

        self._selected_profile = info
        self.accept()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Double-clicking an item is equivalent to clicking Open."""

        # Ensure the double-clicked item is the current selection, then
        # reuse the same logic as the Open button.
        row = self._list.row(item)
        if row >= 0:
            self._list.setCurrentRow(row)
        self._on_open_clicked()