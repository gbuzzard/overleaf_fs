"""
Profiles
========

Helpers for discovering and managing OverleafFS profiles.

A *profile* represents a single logical workspace for OverleafFS. Each
profile keeps its own projects-info JSON file and directory-structure
JSON file in a dedicated subdirectory of the shared profile root
directory.

This module introduces a small :class:`ProfileInfo` dataclass plus
helpers for reading and writing per-profile configuration files.

At this stage, profiles are intentionally simple:

* Each profile lives under the profile root directory returned by
  :func:`overleaf_fs.core.config.get_profile_root_dir`.
* Each profile has a configuration file (``profile_config.json``) stored
  inside its profile directory.
* The configuration file records the profile's id, display name,
  relative path, and Overleaf base URL.

The higher-level GUI (for example, a profile selector dialog) can use
these helpers to list profiles, create new ones, and choose which
profile should be active before launching the main window.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import List, Optional

from overleaf_fs.core import config
from overleaf_fs.core.config import (
    DEFAULT_PROJECTS_INFO_FILENAME,
    DEFAULT_DIRECTORY_STRUCTURE_FILENAME,
)

# Reuse the centralized filename constant from config to avoid
# hard-coding the profile-config filename in multiple places.
PROFILE_CONFIG_FILENAME = config.DEFAULT_PROFILE_CONFIG_FILENAME
ACTIVE_PROFILE_FILENAME = "active_profile.json"


# Default internal id for the initial profile created on first run. The
# display name for this profile is "Primary".
DEFAULT_PROFILE_ID = "primary"
DEFAULT_PROFILE_DISPLAY_NAME = "Primary"


@dataclass
class ProfileInfo:
    """Lightweight description of a single OverleafFS profile.

    Attributes:
        id: Stable identifier for the profile (e.g. ``\"primary\"`` or
            ``\"personal\"``). This is used as the lookup key and is
            typically a short, filesystem-friendly string.
        display_name: Human-readable name shown in the UI (e.g.
            ``\"Primary (Purdue)\"``).
        relative_path: Relative path (within the profile root directory)
            where this profile's data files live. In the simplest cases
            this may be the same as ``id``, but they are kept separate
            so that the stored layout can evolve independently of the
            identifier.
        overleaf_base_url: Base URL for the Overleaf instance associated
            with this profile. For most users this will be
            ``\"https://www.overleaf.com\"``.
    """

    id: str
    display_name: str
    relative_path: Path
    overleaf_base_url: str = "https://www.overleaf.com"

    def data_dir(self) -> Path:
        """Return the absolute path to this profile's data directory.

        This joins the shared profile root directory (as configured in
        :mod:`overleaf_fs.core.config`) with the profile's
        :attr:`relative_path`.
        """
        root = config.get_profile_root_dir()
        return root / self.relative_path


def _profile_dir_for_id(profile_id: str) -> Path:
    """Return the directory for a profile, given its id.

    For now we follow a simple convention: the directory name is the
    same as the profile id. This helper centralizes that convention so
    it can be adjusted in one place later if needed.
    """
    root = config.get_profile_root_dir()
    return root / profile_id


def _profile_config_path(profile_dir: Path) -> Path:
    """Return the path to the profile-config JSON file for a directory."""
    return profile_dir / PROFILE_CONFIG_FILENAME


def load_profile_info(profile_id: str) -> Optional[ProfileInfo]:
    """Load :class:`ProfileInfo` for a given profile id.

    This reads the profile's configuration JSON file if it exists. If
    the file is missing or cannot be parsed, ``None`` is returned.

    Args:
        profile_id: Identifier for the profile to load.

    Returns:
        A :class:`ProfileInfo` instance if a valid configuration file
        exists, or ``None`` otherwise.
    """
    profile_dir = _profile_dir_for_id(profile_id)
    cfg_path = _profile_config_path(profile_dir)

    if not cfg_path.is_file():
        return None

    try:
        raw = cfg_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return None

    try:
        display_name = data.get("display_name") or profile_id
        relative_path_str = data.get("relative_path") or profile_id
        overleaf_base_url = data.get("overleaf_base_url") or "https://www.overleaf.com"
    except Exception:
        return None

    return ProfileInfo(
        id=profile_id,
        display_name=display_name,
        relative_path=Path(relative_path_str),
        overleaf_base_url=overleaf_base_url,
    )


def save_profile_info(info: ProfileInfo) -> None:
    """Persist a :class:`ProfileInfo` to its configuration file.

    This ensures that the profile directory exists under the shared
    profile root and writes a small JSON config file describing the
    profile.

    Args:
        info: Profile information to save.
    """
    profile_dir = _profile_dir_for_id(info.id)
    profile_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = _profile_config_path(profile_dir)
    payload = {
        "id": info.id,
        "display_name": info.display_name,
        "relative_path": str(info.relative_path),
        "overleaf_base_url": info.overleaf_base_url,
    }
    cfg_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def discover_profiles() -> List[ProfileInfo]:
    """Discover profiles under the configured profile root directory.

    This scans the profile root directory for subdirectories and, for
    each one, attempts to read its profile-config JSON file. Only
    directories with readable configuration files are returned.

    If no profile root has been configured yet, this returns an empty
    list.

    Returns:
        A list of :class:`ProfileInfo` objects corresponding to the
        profiles that could be successfully loaded.
    """
    try:
        root = config.get_profile_root_dir()
    except Exception:
        # If there is no configured profile root directory yet, treat
        # this as "no profiles are available".
        return []

    if not root.exists() or not root.is_dir():
        return []

    profiles: List[ProfileInfo] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        info = load_profile_info(entry.name)
        if info is not None:
            profiles.append(info)

    # Sort by display_name for a stable, user-friendly order.
    profiles.sort(key=lambda p: p.display_name.lower())
    return profiles


def ensure_default_profile() -> ProfileInfo:
    """Ensure that at least a default profile exists.

    For early versions of OverleafFS, a single \"Primary\" profile was
    assumed by default. This helper preserves that behavior by
    constructing (and, if necessary, creating) a default profile with
    id ``\"primary\"`` and a simple display name.

    Returns:
        The :class:`ProfileInfo` for the default profile.
    """
    default_id = "primary"
    info = load_profile_info(default_id)
    if info is not None:
        return info

    # Create a simple default profile that stores its data in a
    # subdirectory named after its id.
    info = ProfileInfo(
        id=default_id,
        display_name="Primary",
        relative_path=Path(default_id),
        overleaf_base_url="https://www.overleaf.com",
    )
    save_profile_info(info)
    return info


def get_active_profile_id() -> Optional[str]:
    """Return the id of the most recently used (active) profile.

    The active profile id is stored in a small JSON file at the
    profile root directory so that it can be shared across machines
    when the profile root is synced via cloud storage.

    If no active profile has been recorded yet, or if the file
    cannot be read, this returns ``None``.
    """
    try:
        root = config.get_profile_root_dir()
    except Exception:
        return None

    path = root / ACTIVE_PROFILE_FILENAME
    if not path.is_file():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return None

    profile_id = data.get("id") if isinstance(data, dict) else None
    return profile_id if isinstance(profile_id, str) and profile_id else None


def set_active_profile_id(profile_id: str) -> None:
    """Record the id of the most recently used (active) profile.

    This writes a small JSON file at the profile root directory with
    the chosen profile id. The file lives alongside the per-profile
    directories (such as ``primary/``) so that the active profile
    choice can be shared across machines when the profile root is in
    a cloud-synced location.
    """
    root = config.get_profile_root_dir()
    root.mkdir(parents=True, exist_ok=True)

    path = root / ACTIVE_PROFILE_FILENAME
    payload = {"id": profile_id}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def get_active_profile_info() -> ProfileInfo:
    """Return the :class:`ProfileInfo` for the active profile.

    The active profile id is stored in ``active_profile.json`` under
    the profile root directory. If no active profile has been
    recorded yet, or if the recorded profile cannot be loaded, this
    falls back to either the first discovered profile or (if none
    exist) a default ``\"primary\"`` profile created via
    :func:`ensure_default_profile`. The chosen profile id is then
    persisted as the active profile id.
    """
    # Try the explicitly recorded active profile id first.
    profile_id = get_active_profile_id()
    info: Optional[ProfileInfo] = None
    if profile_id:
        info = load_profile_info(profile_id)

    # If that failed, fall back to any existing profile, or create
    # a default one if necessary.
    if info is None:
        profiles = discover_profiles()
        if profiles:
            info = profiles[0]
        else:
            info = ensure_default_profile()

    # Make sure the active-profile record is in sync with the
    # profile we are about to return.
    set_active_profile_id(info.id)
    return info


def get_active_profile_data_dir() -> Path:
    """Return the directory where the active profile's data files live.

    This directory typically contains the Overleaf projects info JSON
    file and the local directory-structure JSON file for the profile.
    The directory is created if it does not already exist.

    Returns:
        Path to the active profile's data directory.
    """
    info = get_active_profile_info()
    data_dir = info.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_projects_info_path() -> Path:
    """Return the full path to the projects-info JSON file.

    For the active profile this is typically something like::

        get_active_profile_data_dir() / "overleaf_projects_info.json"

    This file holds the cached list of Overleaf projects and related
    info for the profile (id, title, timestamps, etc.).

    The rest of the application should always use this helper rather
    than hard-coding paths so that future profile-related changes do
    not require updates elsewhere.

    Returns:
        Path to the projects-info JSON file (``overleaf_projects_info.json``)
        for the active profile.
    """

    return get_active_profile_data_dir() / DEFAULT_PROJECTS_INFO_FILENAME


def get_directory_structure_path() -> Path:
    """Return the full path to the local directory-structure JSON file.

    This file holds OverleafFS (local-only) data (directory structure,
    pinned/hidden flags, etc.) for the active profile. Keeping the path
    helper here avoids scattering assumptions about the file layout
    across the codebase.

    Returns:
        Path to the directory-structure JSON file
        (``local_directory_structure.json``) for the active profile.
    """

    return get_active_profile_data_dir() / DEFAULT_DIRECTORY_STRUCTURE_FILENAME


def get_profile_name() -> str:
    """Return a human-readable name for the active profile.

    This is primarily a convenience for UI code that wants to display a
    label such as "Profile: Primary". For now it simply returns the
    active profile's display name.

    Returns:
        Display name of the active profile.
    """
    return get_active_profile_info().display_name


def get_overleaf_base_url() -> str:
    """Return the Overleaf base URL for the active profile.

    This value is used by the scraper and embedded login dialog to
    determine which Overleaf server to talk to (for example,
    "https://www.overleaf.com" for the public service or an
    institution-hosted instance).
    """
    return get_active_profile_info().overleaf_base_url


def set_overleaf_base_url(url: str) -> None:
    """Set the Overleaf base URL for the active profile.

    Args:
        url: Base URL for the Overleaf server associated with the
            active profile. This should include the scheme, e.g.
            "https://www.overleaf.com".
    """
    info = get_active_profile_info()
    info.overleaf_base_url = url
    save_profile_info(info)
