"""
Configuration helpers for the Overleaf Project Explorer.

New requirement
---------------
On a clean startup, the application should prompt the user to select the
profile root directory before loading any state. This allows the user to
choose a custom location (e.g., a cloud-synced folder) for storing profile
data. Until the user makes this choice, the profile root directory remains
unset, and the GUI must handle prompting the user.

Design overview
---------------
This module centralizes decisions about where local configuration and
metadata are stored on disk, and it lays the groundwork for supporting
multiple Overleaf accounts ("profiles") in the future.

Local metadata
~~~~~~~~~~~~~~
By default, the metadata files are stored under a per-user
"bootstrap" directory, following a pattern similar to tools like
conda (``~/.conda``). For this application we use

    ~/.overleaf_fs/

as the bootstrap directory. Inside that directory we keep a small
JSON configuration file (``config.json``) that describes where the
actual profile state directories live and which profile is currently
active.

Each profile represents a local view of one Overleaf account (or
usage context) and has its own directory containing state such as:

- the cached list of Overleaf projects for that profile, and
- the local-only metadata for those projects (folders, pinned,
  hidden, etc.).

A typical layout might look like::

    ~/.overleaf_fs/config.json

    /path/to/profile_root_dir/
        primary/
            overleaf_projects.json
            local_state.json
        ornl/
            overleaf_projects.json
            local_state.json

where ``/path/to/profile_root_dir`` is either a local directory or a
cloud-synced directory (e.g. on Dropbox or iCloud) chosen by the
user. The bootstrap config remembers both the profile root directory
and the set of defined profiles, and it records which profile was
last active so we can reopen it by default on the next launch.

For now, this module initializes a single "Primary" profile and
stores all state under a default profile root directory inside
``~/.overleaf_fs``. The rest of the application should always obtain
paths via the helpers in this module so that future additions such
as profile switching or a profile picker UI do not require changes
elsewhere.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

# Name of the per-user "bootstrap" directory under the home directory.
# This directory holds only lightweight configuration for the Overleaf
# Project Explorer (e.g. config.json) and is not intended to contain
# large state files directly.
APP_DIR_NAME = ".overleaf_fs"

# Name of the JSON configuration file inside the bootstrap directory.
CONFIG_FILENAME = "config.json"

# Default internal id for the initial profile created on first run. The
# display name for this profile is "Primary".
DEFAULT_PROFILE_ID = "primary"
DEFAULT_PROFILE_DISPLAY_NAME = "Primary"

# Default names for the per-profile state files. These live inside the
# profile's own state directory (see ``get_active_profile_state_dir``).
DEFAULT_METADATA_FILENAME = "overleaf_projects.json"
DEFAULT_LOCAL_STATE_FILENAME = "local_state.json"


@dataclass
class ProfileConfig:
    """Configuration for a single profile.

    This describes where the profile's state lives relative to the
    shared profile root directory and which human-readable name should
    be shown in the UI.

    Attributes:
        profile_id: Internal identifier (e.g. "primary", "ornl").
        display_name: Human-readable name (e.g. "Primary", "ORNL").
        relative_path: Subdirectory name under the profile root
            directory where this profile's state files live.
    """

    profile_id: str
    display_name: str
    relative_path: str


# ---------------------------------------------------------------------------
# Low-level helpers for bootstrap directory and config.json
# ---------------------------------------------------------------------------


def get_bootstrap_dir() -> Path:
    """Return the per-user bootstrap directory.

    This directory is always local to the current machine (typically
    ``~/.overleaf_fs``) and is used to store lightweight configuration
    files such as ``config.json``. The actual profile state (project
    metadata, local folders, etc.) may live in a different directory
    chosen by the user, for example inside a cloud-synced folder.

    Returns:
        Path to the bootstrap directory.
    """

    return Path.home() / APP_DIR_NAME


def get_config_path() -> Path:
    """Return the full path to the JSON configuration file.

    Returns:
        Path to ``config.json`` inside the bootstrap directory.
    """

    return get_bootstrap_dir() / CONFIG_FILENAME


def _load_raw_config() -> Dict[str, Any]:
    """Load the raw configuration dictionary from disk.

    If the file does not exist or cannot be parsed, an empty dictionary
    is returned. Higher-level helpers are responsible for applying
    defaults and ensuring required keys are present.

    Returns:
        Parsed configuration dictionary, or an empty dict on error.
    """

    path = get_config_path()
    if not path.exists():
        return {}

    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        # If the config is corrupted we fall back to an empty
        # dictionary. Callers will layer defaults on top.
        return {}


def _save_raw_config(cfg: Dict[str, Any]) -> None:
    """Atomically write the given configuration dictionary to disk.

    Args:
        cfg: Configuration dictionary to save.
    """

    bootstrap = get_bootstrap_dir()
    bootstrap.mkdir(parents=True, exist_ok=True)

    path = get_config_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# High-level configuration model (single active profile)
# ---------------------------------------------------------------------------


def _ensure_default_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure that the configuration dictionary has basic required keys.

    This helper provides a minimal, single-profile configuration when no
    explicit config is present. It can be extended later to support
    multiple profiles and a richer UI for managing them.

    Args:
        raw: Existing configuration dictionary (possibly empty).

    Returns:
        A configuration dictionary with at least the keys
        ``profile_root_dir``, ``profiles``, and ``active_profile``.
    """

    cfg = dict(raw) if raw is not None else {}

    # Determine the root directory where all profile subdirectories
    # live. For now we do NOT set a default; leave unset so the GUI can prompt the user.
    profile_root_dir = cfg.get("profile_root_dir")
    if not profile_root_dir:
        # Leave unset so the GUI can prompt the user on first run.
        cfg["profile_root_dir"] = None

    # Ensure there is at least a single "primary" profile.
    profiles = cfg.get("profiles") or {}
    if DEFAULT_PROFILE_ID not in profiles:
        profiles[DEFAULT_PROFILE_ID] = {
            "display_name": DEFAULT_PROFILE_DISPLAY_NAME,
            "relative_path": DEFAULT_PROFILE_ID,
        }
    cfg["profiles"] = profiles

    # Ensure the active profile id is set and points to a known profile.
    active = cfg.get("active_profile") or DEFAULT_PROFILE_ID
    if active not in profiles:
        active = DEFAULT_PROFILE_ID
    cfg["active_profile"] = active

    return cfg


def load_config() -> Dict[str, Any]:
    """Load the application configuration, applying defaults as needed.

    This function is the main entry point for obtaining the current
    configuration. It merges any on-disk configuration with sensible
    defaults and writes the result back to disk if changes were needed.

    Returns:
        A configuration dictionary containing at least the keys
        ``profile_root_dir``, ``profiles``, and ``active_profile``.
    """

    raw = _load_raw_config()
    cfg = _ensure_default_config(raw)

    # If the defaulting logic added or modified keys, persist the
    # updated configuration so that subsequent runs see a consistent
    # view.
    if cfg != raw:
        _save_raw_config(cfg)

    return cfg


def get_profile_root_dir() -> Path:
    """Return the directory under which all profile state lives.

    This is typically configured to point at a directory that can be
    shared across machines (e.g. a Dropbox or iCloud folder). Each
    profile then uses a subdirectory of this root for its own state.

    Returns:
        Path to the profile root directory.
    """

    cfg = load_config()
    root = cfg.get("profile_root_dir")
    if not root:
        raise RuntimeError(
            "No profile_root_dir is configured. The GUI must prompt the user to choose a directory."
        )
    return Path(root).expanduser()


def get_active_profile_id() -> str:
    """Return the identifier of the active profile.

    Returns:
        Internal profile id (e.g. ``"primary"``).
    """

    cfg = load_config()
    return cfg["active_profile"]


def get_active_profile_config() -> ProfileConfig:
    """Return the configuration object for the active profile.

    Returns:
        ProfileConfig describing the active profile.
    """

    cfg = load_config()
    profile_id = cfg["active_profile"]
    profiles = cfg.get("profiles", {})
    pdata = profiles.get(profile_id) or {}

    display_name = pdata.get("display_name") or DEFAULT_PROFILE_DISPLAY_NAME
    relative_path = pdata.get("relative_path") or profile_id

    return ProfileConfig(
        profile_id=profile_id,
        display_name=display_name,
        relative_path=relative_path,
    )


def get_active_profile_state_dir() -> Path:
    """Return the directory where the active profile's state files live.

    The directory is created if it does not already exist.

    Returns:
        Path to the active profile's state directory.
    """

    root = get_profile_root_dir()
    profile_cfg = get_active_profile_config()
    state_dir = root / profile_cfg.relative_path
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def get_metadata_path() -> Path:
    """Return the full path to the metadata JSON file for the profile.

    For the active profile this is typically something like::

        get_active_profile_state_dir() / "overleaf_projects.json"

    The rest of the application should always use this helper rather
    than hard-coding paths so that future profile-related changes do not
    require updates elsewhere.

    Returns:
        Path to the metadata JSON file for the active profile.
    """

    return get_active_profile_state_dir() / DEFAULT_METADATA_FILENAME


def get_local_state_path() -> Path:
    """Return the full path to the local state JSON file for the profile.

    This file is intended to hold local-only metadata (folders, pinned,
    hidden flags, etc.) for the active profile. Keeping the path helper
    here avoids scattering assumptions about the file layout across the
    codebase.

    Returns:
        Path to the local state JSON file for the active profile.
    """

    return get_active_profile_state_dir() / DEFAULT_LOCAL_STATE_FILENAME


def get_profile_name() -> str:
    """Return a human-readable name for the active profile.

    This is primarily a convenience for UI code that wants to display a
    label such as "Profile: Primary". For now it simply returns the
    active profile's display name.

    Returns:
        Display name of the active profile.
    """

    return get_active_profile_config().display_name


def get_profile_root_dir_optional() -> Optional[Path]:
    """Return the configured profile root directory or None if unset."""
    cfg = load_config()
    root = cfg.get("profile_root_dir")
    if not root:
        return None
    return Path(root).expanduser()

def set_profile_root_dir(path: Path) -> None:
    """Set the profile_root_dir in config.json to the given path."""
    cfg = load_config()
    cfg["profile_root_dir"] = str(path)
    _save_raw_config(cfg)