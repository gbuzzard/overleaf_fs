"""
Configuration helpers for the Overleaf File System.

New requirement
---------------
On a clean startup, the application should prompt the user to select the
profile root directory before loading any profile data from disk. This allows the user to
choose a custom location (e.g., a cloud-synced folder) for storing profile
data. Until the user makes this choice, the profile root directory remains
unset, and the GUI must handle prompting the user.

Design overview
---------------
This module centralizes decisions about where overleaf project info and
local directory structure are stored on disk, and it cooperates with
:mod:`overleaf_fs.core.profiles` to support multiple Overleaf profiles,
each stored in its own directory under the profile root.

Local overleaf project info and directory structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
By default, the local data files are stored under a per-user
"bootstrap" directory, following a pattern similar to tools like
conda (``~/.conda``). For this application we use

    ~/.overleaf_fs/

as the bootstrap directory. Inside that directory we keep a small
JSON configuration file (``config.json``) that records where the
actual profile data directories live. The choice of active profile
and other per-profile settings are stored alongside the profile
data, rather than in this bootstrap config.

Each profile represents a local view of one Overleaf account (or
usage context) and has its own directory containing data files such as:

- the cached list of Overleaf projects for that profile, and
- the local-only directory structure for those projects (folders,
  pinned, hidden, etc.).

A typical layout might look like::

    ~/.overleaf_fs/config.json

    /path/to/profile_root_dir/
        primary/
            local_directory_structure.json
            overleaf_cookie.json
            overleaf_projects_info.json
        ornl/
            local_directory_structure.json
            overleaf_cookie.json
            overleaf_projects_info.json

where ``/path/to/profile_root_dir`` is either a local directory or a
cloud-synced directory (e.g. on Dropbox or iCloud) chosen by the
user. The bootstrap config remembers only the profile root directory.
Information about individual profiles and which profile was last
active is stored under the profile root itself (for example, in
profile-specific configuration files).

This module manages the bootstrap configuration only; profile discovery,
active-profile selection, and per-profile settings are handled entirely
in :mod:`overleaf_fs.core.profiles`, which builds on top of this module
to manage all per-profile data.

This module initializes a single "Primary" profile and
stores all profile data under a default profile root directory inside
``~/.overleaf_fs``. The rest of the application should always obtain
paths via the helpers in this module so that future additions such
as profile switching or a profile picker UI do not require changes
elsewhere.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

# Name of the per-user "bootstrap" directory under the home directory.
# This directory holds only lightweight configuration for the Overleaf
# File System (e.g. config.json) and is not intended to contain
# large data files directly.
APP_DIR_NAME = ".overleaf_fs"

# Name of the JSON configuration file inside the bootstrap directory.
CONFIG_FILENAME = "config.json"

# Default names for the per-profile data files. These live inside the
# profile's own directory (see ``profiles.get_active_profile_data_dir``).
# - DEFAULT_PROJECTS_INFO_FILENAME: cached Overleaf projects info for this profile.
#   This is the "projects-info JSON file" (``overleaf_projects_info.json``).
# - DEFAULT_DIRECTORY_STRUCTURE_FILENAME: local-only directory structure and
#   related flags. This is the "directory-structure JSON file"
#   (``local_directory_structure.json``).
# - DEFAULT_PROFILE_CONFIG_FILENAME: per-profile configuration JSON file
#   (``profile_config.json``) stored in the profile root.
DEFAULT_PROJECTS_INFO_FILENAME = "overleaf_projects_info.json"
DEFAULT_DIRECTORY_STRUCTURE_FILENAME = "local_directory_structure.json"
DEFAULT_PROFILE_CONFIG_FILENAME = "profile_config.json"

# Default base URL for the Overleaf server used by a profile. This can
# be overridden per profile (for example, to support institution-hosted
# Overleaf instances such as an ORNL deployment).
DEFAULT_OVERLEAF_BASE_URL = "https://www.overleaf.com"

# Name of the JSON file where we optionally store a browser-derived
# cookie header for the active profile.
COOKIE_FILENAME = "overleaf_cookie.json"

# Version information
FILE_FORMAT_VERSION = 1

# ---------------------------------------------------------------------------
# Low-level helpers for bootstrap directory and config.json
# ---------------------------------------------------------------------------


def get_bootstrap_dir() -> Path:
    """Return the per-user bootstrap directory.

    This directory is always local to the current machine (typically
    ``~/.overleaf_fs``) and is used to store lightweight configuration
    files such as ``config.json``. The actual profile data (overleaf project
    info, local directory structure, etc.) may live in a different directory
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
# High-level configuration model (bootstrap-only)
# ---------------------------------------------------------------------------


def _ensure_default_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure that the configuration dictionary has basic required keys.

    At this stage the global configuration is intentionally minimal:
    it only tracks the ``profile_root_dir`` that points to the
    directory under which all profile data directories live. All
    per-profile configuration (including the active profile and
    per-profile settings) is handled in :mod:`overleaf_fs.core.profiles`.

    Args:
        raw: Existing configuration dictionary (possibly empty).

    Returns:
        A configuration dictionary with at least the key
        ``profile_root_dir``.
    """
    cfg = dict(raw) if raw is not None else {}

    # Determine the root directory under which all profile data
    # directories live. For now we do NOT set a default; leave this
    # unset so the GUI can prompt the user on first run.
    profile_root_dir = cfg.get("profile_root_dir")
    if not profile_root_dir:
        # Leave unset so the GUI can prompt the user on first run.
        cfg["profile_root_dir"] = None

    return cfg


def load_config() -> Dict[str, Any]:
    """Load the application configuration, applying defaults as needed.

    This function is the main entry point for obtaining the current
    configuration. It merges any on-disk configuration with sensible
    defaults and writes the result back to disk if changes were needed.

    Returns:
        A configuration dictionary containing at least the key
        ``profile_root_dir``.
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
    """Return the directory under which all profile data directories live.

    This is typically configured to point at a directory that can be
    shared across machines (e.g. a Dropbox or iCloud folder). Each
    profile then uses a subdirectory of this root for its own data files.

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
