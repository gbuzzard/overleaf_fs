from __future__ import annotations

"""
Configuration helpers for the Overleaf Project Explorer.

Design overview
---------------
This module centralizes decisions about where local configuration and
metadata are stored on disk, and it lays the groundwork for supporting
multiple Overleaf accounts ("profiles") in the future.

Local metadata
~~~~~~~~~~~~~~
The application maintains local-only metadata for each Overleaf
project (see ``overleaf_fs.core.models.ProjectLocal``), such as:

- the virtual folder path the project belongs to (single-folder
  membership, e.g. "CT" or "Teaching/2025"),
- pinned / hidden flags.

This metadata does **not** live on Overleaf and is never pushed back
to Overleaf; it is stored entirely on the local machine in a JSON
file that can be inspected or deleted without affecting the remote
projects.

By default, the metadata file is stored under the user's home
directory, following a pattern similar to tools like conda
(``~/.conda``). For now we use a single "default" profile and keep
all metadata in:

    ~/.overleaf_fs/overleaf_projects.json

In a future iteration, when we add actual Overleaf scraping and
account detection, we expect to extend this scheme to support
multiple profiles (e.g. separate metadata for a Purdue account and an
ORNL account). A plausible layout would be:

    ~/.overleaf_fs/profiles/default/overleaf_projects.json
    ~/.overleaf_fs/profiles/purdue/overleaf_projects.json
    ~/.overleaf_fs/profiles/ornl/overleaf_projects.json

At that point, ``get_metadata_path()`` would incorporate the current
profile name and point to the appropriate file. We may also expose
UI for switching profiles and for deleting local metadata for a
particular profile (without touching any Overleaf projects).

For now, this module provides a simple, single-profile implementation
that still keeps all path logic in one place so it can be evolved
later without touching the rest of the codebase.
"""

from pathlib import Path

# Base hidden directory under the user's home where all Overleaf Project
# Explorer state lives. This mirrors tools like conda (~/.conda).
DEFAULT_BASE_DIRNAME = ".overleaf_fs"

# Current metadata filename within the base directory. The full default
# path is "~/.overleaf_fs/overleaf_projects.json".
DEFAULT_METADATA_FILENAME = "overleaf_projects.json"

# Name of the currently active profile. For now we only support a single
# profile ("default"), but we keep this as a separate constant so that
# future multi-profile support can expand on it without changing call
# sites that rely on get_metadata_path().
DEFAULT_PROFILE_NAME = "default"


def get_base_dir() -> Path:
    """
    Return the base directory where all Overleaf Project Explorer state
    is stored for the current user.

    At present this is simply ``~/.overleaf_fs``. If we later introduce
    a more complex layout (e.g. per-profile subdirectories or
    platform-specific config locations), this helper can encapsulate
    that logic.
    """
    return Path.home() / DEFAULT_BASE_DIRNAME


def get_profile_name() -> str:
    """
    Return the name of the active profile.

    Currently this always returns ``"default"``. In a future iteration
    this may be derived from user preferences, environment variables, or
    the detected Overleaf account (e.g. different profiles for different
    Overleaf logins such as Purdue vs ORNL).
    """
    return DEFAULT_PROFILE_NAME


def get_metadata_path() -> Path:
    """
    Return the full path to the local metadata JSON file.

    For now this is::

        get_base_dir() / "overleaf_projects.json"

    which corresponds to ``~/.overleaf_fs/overleaf_projects.json`` for
    the default profile.

    A future multi-profile implementation might instead return a path
    under a per-profile directory, such as::

        get_base_dir() / "profiles" / get_profile_name() / "overleaf_projects.json"

    The rest of the application should not depend on the exact layout
    and should always obtain the metadata path via this helper.
    """
    base = get_base_dir()
    return base / DEFAULT_METADATA_FILENAME