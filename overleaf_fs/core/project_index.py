"""
Dummy implementation of the project index.

This module provides a minimal stub version of the project index so the
GUI can be tested before we implement actual persistence or Overleaf
scraping. The real version will load a metadata JSON file, reconcile it
with Overleaf data, and return a ProjectIndex mapping project IDs to
ProjectRecord instances.

For now, ``load_project_index()`` returns a static set of fake projects.
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


def load_project_index() -> ProjectIndex:
    """
    Return a fake ProjectIndex containing a few hard-coded sample projects.

    This is *only* for initial wiring of the GUI. The real implementation
    will read metadata from disk, merge it with Overleaf dashboard data,
    and return a dynamic index.
    """
    index: ProjectIndex = {}

    local_meta = load_local_metadata()

    # Project 1
    remote1 = ProjectRemote(
        id="abcdef123456",
        name="Sample Paper",
        url="https://www.overleaf.com/project/abcdef123456",
        owner_label="Owned by you",
        last_modified_raw="2 days ago",
        last_modified=datetime(2025, 1, 15, 10, 30),
    )
    local1 = local_meta.get(remote1.id, ProjectLocal())
    index[remote1.id] = ProjectRecord(remote=remote1, local=local1)

    # Project 2
    remote2 = ProjectRemote(
        id="xyz987654321",
        name="Grant Proposal",
        url="https://www.overleaf.com/project/xyz987654321",
        owner_label="Shared",
        last_modified_raw="5 hours ago",
        last_modified=datetime(2025, 2, 8, 14, 5),
    )
    local2 = local_meta.get(remote2.id, ProjectLocal())
    index[remote2.id] = ProjectRecord(remote=remote2, local=local2)

    # Project 3
    remote3 = ProjectRemote(
        id="qqq111222333",
        name="Teaching Notes",
        url="https://www.overleaf.com/project/qqq111222333",
        owner_label="Owned by you",
        last_modified_raw="1 month ago",
        last_modified=datetime(2024, 12, 20, 8, 15),
    )
    local3 = local_meta.get(remote3.id, ProjectLocal())
    index[remote3.id] = ProjectRecord(remote=remote3, local=local3)

    return index


def save_project_index(index: ProjectIndex) -> None:
    """
    Persist the local portion of the project index to disk.

    This writes only the local metadata (folder, notes, pinned,
    hidden) for each project using ``save_local_metadata``.
    Remote metadata is never written locally.

    This helper is a placeholder for when the GUI modifies local
    metadata (e.g., when a project is moved to a folder or notes
    are edited).
    """
    local = {proj_id: rec.local for proj_id, rec in index.items()}
    save_local_metadata(local)
