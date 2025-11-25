"""
Local metadata storage for Overleaf projects.

Design overview
---------------
The core data model (see ``overleaf_fs.core.models``) separates
"remote" metadata that mirrors what Overleaf reports about a project
(id, name, owner, last modified, URL) from "local" metadata that
captures how you organize and annotate projects on your own machine
(folder, notes, pinned, hidden).

This module focuses on persisting and loading the *local* metadata,
keyed by project id, using a simple JSON file on disk. The JSON schema
is intentionally lightweight and stable:

.. code-block:: json

    {
      "version": 1,
      "projects": {
        "abcdef123456": {
          "folder": "CT",
          "notes": "Draft due soon",
          "pinned": true,
          "hidden": false
        },
        "xyz987654321": {
          "folder": "Funding",
          "notes": null,
          "pinned": false,
          "hidden": false
        }
      }
    }

Only the local fields are stored. The remote fields are refreshed from
Overleaf (or, currently, from dummy data) and merged with this local
metadata into ``ProjectRecord`` instances elsewhere.

At this stage the module provides:

- ``load_local_metadata()``: load a mapping from project id to
  ``ProjectLocal`` from the JSON file, or return an empty mapping if
  the file does not exist or is unreadable.
- ``save_local_metadata()``: write a mapping from project id to
  ``ProjectLocal`` back to disk.

By default the metadata file is stored under the user's home directory
in ``~/.overleaf_fs/overleaf_projects.json``. This keeps the metadata
separate from any particular project working directory while remaining
easy to inspect and version-control if desired. Later we can
centralize the path selection (e.g. under a config directory) in
``config.py`` if needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Mapping, Optional

from overleaf_fs.core.models import ProjectLocal

# Default location for the local metadata JSON, following the pattern of
# tools like conda (~/.conda). This keeps the metadata under a single
# hidden directory in the user's home.
DEFAULT_METADATA_DIRNAME = ".overleaf_fs"
DEFAULT_METADATA_FILENAME = "overleaf_projects.json"


def _default_metadata_path() -> Path:
    """
    Return the default full path to the metadata JSON file,
    stored under the user's home directory in ``~/.overleaf_fs/``.
    """
    home = Path.home()
    return home / DEFAULT_METADATA_DIRNAME / DEFAULT_METADATA_FILENAME


def _metadata_path(path: Optional[Path] = None) -> Path:
    """
    Resolve the path to the metadata JSON file.

    If ``path`` is provided, it is returned as-is (converted to a
    ``Path``). Otherwise, the default location
    ``~/.overleaf_fs/overleaf_projects.json`` is used.
    """
    if path is not None:
        return Path(path)
    return _default_metadata_path()


def _project_local_to_dict(local: ProjectLocal) -> Dict:
    """
    Convert a ``ProjectLocal`` instance into a plain dict suitable
    for JSON serialization.
    """
    return {
        "folder": local.folder,
        "notes": local.notes,
        "pinned": bool(local.pinned),
        "hidden": bool(local.hidden),
    }


def _project_local_from_dict(data: Mapping) -> ProjectLocal:
    """
    Construct a ``ProjectLocal`` from a plain mapping (e.g. decoded JSON).

    Missing fields are filled with sensible defaults so that older
    metadata files remain compatible if new fields are added later.
    """
    folder = data.get("folder")
    notes = data.get("notes")
    pinned = bool(data.get("pinned", False))
    hidden = bool(data.get("hidden", False))
    return ProjectLocal(folder=folder, notes=notes, pinned=pinned, hidden=hidden)


def load_local_metadata(path: Optional[Path] = None) -> Dict[str, ProjectLocal]:
    """
    Load local project metadata from the JSON file.

    Parameters
    ----------
    path:
        Optional explicit path to the metadata JSON file. If omitted,
        the default location ``~/.overleaf_fs/overleaf_projects.json``
        is used.

    Returns
    -------
    dict
        A mapping from project id (str) to ``ProjectLocal``. If the
        file does not exist or cannot be parsed, an empty dict is
        returned.
    """
    metadata_file = _metadata_path(path)
    if not metadata_file.exists():
        return {}

    try:
        with metadata_file.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        # On any I/O or JSON error, fall back to an empty mapping.
        return {}

    projects_raw = raw.get("projects", {})
    result: Dict[str, ProjectLocal] = {}

    if isinstance(projects_raw, dict):
        for proj_id, proj_data in projects_raw.items():
            if not isinstance(proj_id, str):
                continue
            if not isinstance(proj_data, Mapping):
                continue
            result[proj_id] = _project_local_from_dict(proj_data)

    return result


def save_local_metadata(
    metadata: Mapping[str, ProjectLocal],
    path: Optional[Path] = None,
) -> None:
    """
    Save local project metadata to the JSON file.

    Parameters
    ----------
    metadata:
        A mapping from project id (str) to ``ProjectLocal``.
    path:
        Optional explicit path to the metadata JSON file. If omitted,
        the default location ``~/.overleaf_fs/overleaf_projects.json``
        is used.
    """
    metadata_file = _metadata_path(path)

    # Ensure parent directory exists.
    if metadata_file.parent and not metadata_file.parent.exists():
        metadata_file.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": 1,
        "projects": {
            proj_id: _project_local_to_dict(local)
            for proj_id, local in metadata.items()
        },
    }

    with metadata_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")