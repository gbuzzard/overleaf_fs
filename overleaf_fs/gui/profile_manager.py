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
            pass
        # If the user clicked "Cancel", ``selected`` will be the profile
        # that was active when the dialog was opened (if any), so the
        # caller can simply relaunch the main window using ``selected``
        # without special-casing Cancel.

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
    get_active_profile_info,
)

from pathlib import Path
import shutil

from overleaf_fs.gui.profile_root_ui import (
    choose_profile_root_directory,
    looks_like_profile_root,
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
    * Moving the profile storage location (profile root directory)
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Profile Manager")

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SingleSelection)

        # Buttons along the bottom: Open, Rename, New, Move, Delete, Cancel, Exit.
        self._open_button = QPushButton("Open", self)
        self._rename_button = QPushButton("Rename…", self)
        self._new_button = QPushButton("New…", self)
        self._move_button = QPushButton("Move…", self)
        self._move_button.setToolTip(
            "Change the folder where OverleafFS profiles are stored"
        )
        self._delete_button = QPushButton("Delete", self)
        # Make the delete button visually distinct to emphasize that it
        # is a destructive operation.
        self._delete_button.setStyleSheet("color: red;")
        self._cancel_button = QPushButton("Cancel", self)
        self._exit_button = QPushButton("Exit app", self)

        self._open_button.clicked.connect(self._on_open_clicked)
        self._rename_button.clicked.connect(self._on_rename_profile)
        self._new_button.clicked.connect(self._on_new_profile)
        self._move_button.clicked.connect(self._on_move_root)
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._delete_button.clicked.connect(self._on_delete_profile)
        self._exit_button.clicked.connect(self.reject)

        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

        # Layout
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(QLabel("Select a profile to open or manage profiles:", self))
        main_layout.addWidget(self._list)

        button_row = QHBoxLayout()
        button_row.addWidget(self._open_button)
        button_row.addWidget(self._rename_button)
        button_row.addWidget(self._new_button)
        button_row.addWidget(self._move_button)
        button_row.addWidget(self._delete_button)
        button_row.addWidget(self._cancel_button)
        button_row.addWidget(self._exit_button)

        main_layout.addLayout(button_row)

        self._selected_profile: Optional[ProfileInfo] = None
        self._initial_profile: Optional[ProfileInfo] = None

        self._refresh_profiles()

        # Remember the profile that was active when the dialog was opened.
        # This allows the Cancel button to return to that profile instead
        # of exiting the application.
        try:
            self._initial_profile = get_active_profile_info()
        except Exception:
            # If we cannot determine an active profile for any reason,
            # leave this as None and let the caller handle the default.
            self._initial_profile = None

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

    def _on_move_root(self) -> None:
        """Change the profile root directory and optionally move data.

        This allows the user to change where OverleafFS stores all profile
        data. The new location is recorded in the global config
        (``profile_root_dir``). If the chosen folder is empty, the current
        profile root directory's contents are moved there. If the folder
        already contains OverleafFS data, the user can either switch to
        using that existing data or move the current data into the folder,
        potentially overwriting files.
        """
        try:
            old_root_str = config.get_profile_root_dir()
        except Exception:
            old_root_str = ""
        old_root = Path(old_root_str) if old_root_str else None

        # Use the shared helper so that the explanatory text and
        # directory-chooser behavior are consistent with the initial
        # setup flow in the main window.
        new_root = choose_profile_root_directory(self, current_root=old_root)
        if new_root is None:
            return

        new_root = new_root.resolve()
        if old_root is not None and new_root == old_root.resolve():
            # No change.
            return

        looks_like_root = looks_like_profile_root(new_root)

        # If the directory looks like it already contains OverleafFS
        # data, ask whether to switch or move.
        if looks_like_root:
            box = QMessageBox(self)
            box.setWindowTitle("Existing OverleafFS data found")
            box.setText(
                "The selected folder already appears to contain OverleafFS data.\n\n"
                "What would you like to do?"
            )
            use_button = box.addButton("Use existing data in selected folder", QMessageBox.AcceptRole)
            move_button = box.addButton(
                "Move current data to selected folder (may overwrite)",
                QMessageBox.DestructiveRole,
            )
            cancel_button = box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(use_button)
            box.exec()

            clicked = box.clickedButton()
            if clicked is cancel_button:
                return
            elif clicked is use_button:
                # Just point the config at the new root and refresh.
                config.set_profile_root_dir(str(new_root))
                self._refresh_profiles()
                QMessageBox.information(
                    self,
                    "Profile storage updated",
                    f"Profile storage folder updated to:\n{new_root}",
                )
                return
            # Otherwise, the user chose to move current data into the
            # target folder; fall through to the move logic below.

        # At this point we are moving the current root contents into
        # the new root. This is used when the target is empty or when
        # the user explicitly requested an overwrite.
        if old_root is None or not old_root.exists() or not old_root.is_dir():
            QMessageBox.warning(
                self,
                "No existing profile root",
                "The current profile root directory could not be found. "
                "Nothing was moved, but the new folder will be used for "
                "future profiles.",
            )
            config.set_profile_root_dir(str(new_root))
            self._refresh_profiles()
            QMessageBox.information(
                self,
                "Profile storage updated",
                f"Profile storage folder updated to:\n{new_root}",
            )
            return

        # Perform the move: move all items from old_root into new_root.
        try:
            new_root.mkdir(parents=True, exist_ok=True)
            for entry in old_root.iterdir():
                target = new_root / entry.name
                # If a target already exists, attempt to remove it first
                # to honor the user's overwrite choice.
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(str(entry), str(target))

            # Attempt to remove the old root directory itself so that the
            # entire tree is moved. Ignore errors if the directory is not
            # empty or cannot be removed.
            try:
                old_root.rmdir()
            except Exception:
                pass
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(
                self,
                "Error moving profile data",
                f"An error occurred while moving profile data:\n{exc}",
            )
            return

        # Update the config to point at the new root and refresh the list.
        config.set_profile_root_dir(str(new_root))
        self._refresh_profiles()
        QMessageBox.information(
            self,
            "Profile storage updated",
            f"Profile storage folder moved to:\n{new_root}",
        )

    def _on_delete_profile(self) -> None:
        """Delete the directory for the currently selected profile.

        This performs a full delete of the profile directory under the
        profile root. The profile configuration and all data files
        within that directory (such as ``overleaf_projects_info.json``
        and ``local_directory_structure.json``) are removed.
        """

        info = self._load_current_profile()
        if info is None:
            return

        answer = QMessageBox.question(
            self,
            "Delete profile",
            f"Really delete profile '{info.display_name}' (id: {info.id})?\n\n"
            "This will remove the entire profile directory under the profile "
            "root, including the profile configuration and all data files.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        root = config.get_profile_root_dir()
        profile_dir = root / info.id
        try:
            if profile_dir.exists():
                shutil.rmtree(profile_dir)
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(
                self,
                "Error deleting profile",
                f"Could not delete profile directory:\n{exc}",
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

    def _on_cancel_clicked(self) -> None:
        """Close the dialog and keep using the current active profile.

        This is intended for the common case where the user wants to
        return to the main application using the most recently active
        profile. The dialog is accepted, and ``selected_profile`` is
        set back to the profile that was active when the dialog was
        opened (if known), so callers can distinguish between "open
        this profile" and "keep the existing active profile".
        """
        # Restore the initially active profile (if we know it) so that
        # callers can simply check ``selected_profile`` after exec().
        self._selected_profile = self._initial_profile
        self.accept()