"""
Project index handling

This module loads and merges two sources of truth:

1. Remote projects info (id, name, URL, owner, modified time, archived, etc.)
   stored in the profile's cached projects‑info JSON file
   (``overleaf_projects_info.json``).

2. Local directory‑structure fields (folder, notes, pinned, hidden)
   stored in the profile's directory‑structure JSON file
   (``local_directory_structure.json``).

The function ``load_projects_index()`` performs the merge of these two JSON
sources, producing a ``ProjectsIndex`` mapping project IDs to full
``ProjectRecord`` instances. Each ``ProjectRecord`` contains:

* ``remote`` — Overleaf‑side fields loaded from the projects‑info JSON file.
* ``local`` — directory‑structure fields loaded from the directory‑structure
  JSON file.

The merge is keyed by Overleaf project ID; remote fields overwrite on refresh,
while local fields persist across refreshes.
"""

from __future__ import annotations

from datetime import datetime

from overleaf_fs.core.models import (
    ProjectRemote,
    ProjectLocal,
    ProjectRecord,
    ProjectsIndex,
)

from overleaf_fs.core.directory_structure_store import load_directory_structure

from overleaf_fs.core.profiles import get_projects_info_path
import json
import logging


def _parse_overleaf_timestamp(value: str) -> datetime:
    """Parse ISO 8601 timestamps from Overleaf, accepting a trailing 'Z'.

    Overleaf often returns timestamps like ``YYYY-MM-DDTHH:MM:SS.sssZ``.
    Python's ``datetime.fromisoformat`` does not accept the trailing ``'Z'``,
    so we normalize it to ``+00:00`` before parsing.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def load_projects_index() -> ProjectsIndex:
    """
    Load and merge remote Overleaf projects‑info data with local
    directory‑structure data to produce a unified ``ProjectsIndex``.

    This function reads:

    * the projects‑info JSON file (remote fields: id, name, URL, owner,
      timestamps, archived, etc.), and
    * the directory‑structure JSON file (local fields: folder, notes,
      pinned, hidden).

    For each project id appearing in the projects‑info file, a
    ``ProjectRecord`` is created. The ``remote`` portion is populated from
    the projects‑info JSON entry; the ``local`` portion is looked up in the
    directory‑structure data (or created empty via ``ProjectLocal()`` if
    absent).

    Remote fields are authoritative and overwritten whenever the projects‑info
    file is refreshed from Overleaf. Local fields persist across refreshes and
    represent machine‑local organization and folder layout.

    If the projects‑info JSON file is missing or cannot be parsed, an empty
    ``ProjectsIndex`` is returned. The caller may then trigger a fresh sync from
    Overleaf to regenerate the projects‑info file.

    Returns:
        A ``ProjectsIndex`` mapping project ids to merged ``ProjectRecord``
        objects.
    """
    index: ProjectsIndex = {}

    # Load local directory‑structure data (folder, notes, pinned, hidden)
    directory_structure = load_directory_structure()
    local_projects = directory_structure.projects

    # Load remote projects info from the profile's projects‑info JSON file.
    projects_info_path = get_projects_info_path()
    try:
        raw = projects_info_path.read_text(encoding="utf-8")
        projects_info_entries = json.loads(raw)
    except Exception:
        # If the projects-info file is missing or malformed, fall back to an
        # empty index rather than raising. The user can trigger a fresh sync
        # from Overleaf to regenerate this file.
        projects_info_entries = []

    for entry in projects_info_entries:
        try:
            remote = ProjectRemote(
                id=entry["id"],
                name=entry["name"],
                url=entry["url"],
                owner_label=entry.get("owner_label", ""),
                owner_display_name=entry.get("owner_display_name"),
                last_modified_raw=entry.get("last_modified_raw", ""),
                last_modified=(
                    _parse_overleaf_timestamp(entry["last_modified"])
                    if entry.get("last_modified")
                    else None
                ),
                archived=bool(entry.get("archived", False)),
            )
        except Exception as exc:
            # Warn about malformed entries rather than silently skipping
            # them. This may indicate that the cached projects‑info file is
            # corrupted or out of sync with Overleaf.
            logging.warning(
                "Skipping malformed project entry in %s: %r (error: %s)",
                projects_info_path,
                entry,
                exc,
            )
            # TODO: Consider offering the user an option to abort, resync
            # from Overleaf, and retry loading the projects index.
            continue

        local = local_projects.get(remote.id, ProjectLocal())
        index[remote.id] = ProjectRecord(remote=remote, local=local)

    return index
