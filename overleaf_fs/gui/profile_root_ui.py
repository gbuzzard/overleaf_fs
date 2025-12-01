"""
UI helpers for selecting the OverleafFS profile root directory.

This module centralizes the small pieces of Qt UI needed to let the user
choose where OverleafFS stores all profile data (the *profile root
directory*). It is used both on first run (when no profile root has been
configured yet) and from the Profile Manager's "Move…" action.

By keeping this logic in one place we ensure that:

* The explanatory text shown to the user is consistent.
* The directory chooser dialog is configured in the same way everywhere
  (directory-only mode, non-native dialog, useful cloud-storage sidebar
  entries).
* Higher-level code (such as :class:`MainWindow` and the profile
  manager dialog) can focus on policy decisions: what to do when the
  user cancels, and whether to move or reuse existing profile data.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from overleaf_fs.core.profiles import PROFILE_CONFIG_FILENAME, ACTIVE_PROFILE_FILENAME

DEFAULT_PROFILE_ROOT_NAME = "overleaf_fs_profiles"


def compute_cloud_sidebar_urls(current_root: Optional[Path]) -> List[QUrl]:
    """Return a list of sidebar URLs for the profile-root file dialog.

    The goal is to make it easy for users to pick a cloud-synced folder
    so that their OverleafFS profiles are available across machines.
    We include:

    * The user's home directory,
    * The current profile root (if known),
    * Common cloud storage folders (Dropbox, Box, OneDrive),
    * Any providers discovered under ``~/Library/CloudStorage`` on
      platforms that use that convention (such as macOS).

    Args:
        current_root: Currently configured profile root directory, or
            ``None`` if no root has been configured yet.

    Returns:
        A list of :class:`QUrl` objects suitable for passing to
        :meth:`QFileDialog.setSidebarUrls`.
    """
    urls: List[QUrl] = []

    def _add_path(p: Path) -> None:
        try:
            if p.exists():
                url = QUrl.fromLocalFile(str(p))
                if url not in urls:
                    urls.append(url)
        except Exception:
            # Best-effort only; ignore weird paths.
            pass

    home = Path.home()
    _add_path(home)

    if current_root is not None:
        _add_path(current_root)

    # Common top-level cloud storage folders in the home directory.
    cloud_names = [
        "Dropbox",
        "Dropbox (Personal)",
        "Dropbox (Business)",
        "Box",
        "OneDrive",
    ]
    for name in cloud_names:
        _add_path(home / name)

    # On macOS and similar setups, additional cloud providers may live
    # under ~/Library/CloudStorage.
    cloud_storage_root = home / "Library" / "CloudStorage"
    if cloud_storage_root.is_dir():
        try:
            for entry in cloud_storage_root.iterdir():
                if entry.is_dir():
                    _add_path(entry)
        except Exception:
            # If we cannot list this directory for any reason, just skip it.
            pass

    return urls


def choose_profile_root_directory(
    parent: QWidget,
    *,
    current_root: Optional[Path] = None,
) -> Optional[Path]:
    """Show a dialog to choose the OverleafFS profile root directory.

    This helper is shared between the initial setup flow (when no
    profile root directory has been configured yet) and the Profile
    Manager's \"Move…\" operation. It presents a brief explanation of
    what the profile root directory is and then opens a directory-only
    file dialog with a sidebar populated by :func:`compute_cloud_sidebar_urls`.

    Args:
        parent: Parent widget for the dialogs.
        current_root: Currently configured profile root directory, or
            ``None`` if no root has been configured yet. When provided,
            it is used to seed the dialog so that the existing root is
            easy to re-select or inspect.

    Returns:
        The chosen directory as a :class:`Path`, or ``None`` if the user
        cancelled.
    """
    # Informational message: what is the profile root and why might you
    # want it in a cloud-synced location?
    QMessageBox.information(
        parent,
        "Profile storage folder",
        (
            "OverleafFS stores all of its local data (profiles, project\n"
            "information, and directory structure) under a single folder,\n"
            "called the profile storage folder.\n\n"
            "It is often convenient to place this folder inside a\n"
            "cloud-synced directory (such as Dropbox, Box, or OneDrive)\n"
            "so that your OverleafFS profiles are available on multiple\n"
            "machines.\n\n"
            "Please choose the folder where OverleafFS should store its\n"
            "profiles. You can change this later from the Profile Manager."
        ),
    )

    # Suggest a default location: either the current root (if known) or
    # a conventional folder inside the user's home directory.
    if current_root is not None:
        suggested = current_root
    else:
        suggested = Path.home() / DEFAULT_PROFILE_ROOT_NAME

    dialog = QFileDialog(parent, "Select profile storage folder")
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)

    # Start the dialog in the suggested folder's parent so that the
    # suggested folder itself is visible and easy to select.
    if suggested.exists():
        dialog.setDirectory(str(suggested.parent))
    else:
        dialog.setDirectory(str(suggested.parent))

    # Preselect the suggested folder so it is highlighted when the dialog opens.
    dialog.selectFile(str(suggested))

    dialog.setSidebarUrls(compute_cloud_sidebar_urls(current_root))

    if dialog.exec() != QFileDialog.Accepted:
        return None

    selected = dialog.selectedFiles()
    if not selected:
        return None

    return Path(selected[0]).resolve()


def looks_like_profile_root(path: Path) -> bool:
    """Heuristic to decide whether *path* looks like an OverleafFS profile root.

    A directory is considered a likely profile root if it contains an
    ``active_profile.json`` file or at least one subdirectory with a
    profile configuration file (``PROFILE_CONFIG_FILENAME``). This is
    used when the user selects a folder as a potential profile storage
    location to decide whether we should offer to reuse existing data
    or move the current profiles into that folder.

    Args:
        path: Directory to inspect.

    Returns:
        ``True`` if the directory appears to contain OverleafFS profile
        data, ``False`` otherwise.
    """
    try:
        if not path.exists() or not path.is_dir():
            return False
    except Exception:
        return False

    # Explicit marker file for the active profile.
    if (path / ACTIVE_PROFILE_FILENAME).is_file():
        return True

    # Any subdirectory with a profile-config file is a strong signal.
    try:
        for entry in path.iterdir():
            if entry.is_dir() and (entry / PROFILE_CONFIG_FILENAME).is_file():
                return True
    except Exception:
        # If the directory cannot be listed, treat it as not a profile root.
        return False

    return False
