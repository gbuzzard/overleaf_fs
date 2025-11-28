"""
Project index handling.

This module loads and merges two sources of truth:

1. Remote project metadata (owner, modified time, name, URL, etc.)
   stored in the profile's `overleaf_projects.json` file.

2. Local project metadata (folder, pinned, hidden)
   stored in the profile's `local_state.json` file.

The function `load_project_index()` returns a `ProjectIndex` mapping
project IDs to `ProjectRecord` instances containing both parts.

Future versions will add automatic syncing with the Overleaf dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict

from overleaf_fs.core.models import (
    ProjectRemote,
    ProjectLocal,
    ProjectRecord,
    ProjectIndex,
)

from overleaf_fs.core.metadata_store import load_local_metadata
from overleaf_fs.core.metadata_store import save_local_metadata

from overleaf_fs.core.config import get_projects_info_path
import json
import logging


def load_project_index() -> ProjectIndex:
    index: ProjectIndex = {}

    # Load local metadata (folder, pinned, hidden)
    local_meta = load_local_metadata()

    # Load remote metadata from profile-aware metadata file
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
            # them. This may indicate that the profile metadata file is
            # corrupted or out of sync with Overleaf.
            logging.warning(
                "Skipping malformed project entry in %s: %r (error: %s)",
                projects_info_path,
                entry,
                exc,
            )
            # TODO: Consider offering the user an option to abort, resync
            # from Overleaf, and retry loading the project index.
            continue

        local = local_meta.get(remote.id, ProjectLocal())
        index[remote.id] = ProjectRecord(remote=remote, local=local)

    return index


def save_project_index(index: ProjectIndex) -> None:
    """
    Persist the local portion of the project index to the profile's
    local_state.json. Remote metadata is never written locally.
    """
    local = {proj_id: rec.local for proj_id, rec in index.items()}
    save_local_metadata(local)
