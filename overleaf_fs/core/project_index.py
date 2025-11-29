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
from typing import Dict

from overleaf_fs.core.models import (
    ProjectRemote,
    ProjectLocal,
    ProjectRecord,
    ProjectsIndex,
)

from overleaf_fs.core.metadata_store import (
    load_directory_structure,
    save_directory_structure,
    LocalState
)

from overleaf_fs.core.config import get_projects_info_path
import json
import logging


def load_projects_index() -> ProjectsIndex:
    """
    Load and merge remote Overleaf project metadata with local
    directory‑structure metadata to produce a unified ``ProjectsIndex``.

    This function reads:

    * the projects‑info JSON file (remote fields: id, name, URL, owner,
      timestamps, archived, etc.), and
    * the directory‑structure JSON file (local fields: folder, notes,
      pinned, hidden).

    For each project id appearing in the projects‑info file, a
    ``ProjectRecord`` is created. The ``remote`` portion is populated from
    the projects‑info JSON entry; the ``local`` portion is looked up in the
    directory‑structure metadata (or created empty via ``ProjectLocal()`` if
    absent).

    Remote fields are authoritative and overwritten whenever the projects‑info
    file is refreshed from Overleaf. Local fields persist across refreshes and
    represent machine‑local organization.

    Returns:
        A ``ProjectsIndex`` mapping project ids to merged ``ProjectRecord``
        objects.
    """
    index: ProjectsIndex = {}

    # Load local directory‑structure fields (folder, notes, pinned, hidden)
    state = load_directory_structure()
    local_meta = state.projects

    # Load remote projects info from the profile's projects‑info file
    projects_info_path = get_projects_info_path()
    try:
        raw = projects_info_path.read_text(encoding='utf-8')
        remote_entries = json.loads(raw)
    except Exception:
        remote_entries = []

    for entry in remote_entries:
        try:
            remote = ProjectRemote(
                id=entry["id"],
                name=entry["name"],
                url=entry["url"],
                owner_label=entry.get("owner_label", ""),
                last_modified_raw=entry.get("last_modified_raw", ""),
                last_modified=(
                    datetime.fromisoformat(entry["last_modified"])
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

        local = local_meta.get(remote.id, ProjectLocal())
        index[remote.id] = ProjectRecord(remote=remote, local=local)

    return index


def save_project_index(index: ProjectsIndex) -> None:
    """
    Persist the local directory‑structure portion of the project index to
    the profile's directory‑structure JSON file. Remote project info is
    never written locally.
    """
    local = {proj_id: rec.local for proj_id, rec in index.items()}

    existing = load_directory_structure()
    new_state = LocalState(
        folders=list(existing.folders),
        projects=dict(local),
    )
    save_directory_structure(new_state)
