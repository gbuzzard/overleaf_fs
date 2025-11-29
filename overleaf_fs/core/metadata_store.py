"""
Local directory-structure storage for Overleaf projects.

Design overview
---------------
The core data model (see ``overleaf_fs.core.models``) separates
"remote" projects info (what Overleaf reports about a project: id, name,
owner, last modified, URL, etc.) from "local" organization and
annotation data that lives only on your machine (folder, notes, pinned,
hidden).

This module focuses on persisting and loading the *local* directory
structure and per-project local fields, keyed by project id, using a
simple JSON file on disk. The JSON schema is intentionally lightweight
and stable:

.. code-block:: json

    {
      "folders": [
        "CT",
        "Teaching",
        "Teaching/2025",
        "Funding"
      ],
      "projects": {
        "abcdef123456": {
          "folder": "CT",
          "pinned": true,
          "hidden": false
        },
        "xyz987654321": {
          "folder": "Funding",
          "pinned": false,
          "hidden": false
        }
      },
      "version": 1,
    }
Note that the entry "" for "folder" indicates the top level directory,
"Home/", which is prepended to all folders.  E.g., "CT" maps to "Home/CT".
Only the local fields are stored. The remote projects info is refreshed
from Overleaf (or, currently, from dummy data) and merged with this
local directory-structure data into ``ProjectRecord`` instances
elsewhere.

At this stage the module provides two layers of API:

- ``load_directory_structure()`` / ``save_directory_structure()``: work with a
  ``LocalState`` object that includes both the explicit folder list
  and the per-project ``ProjectLocal`` fields (folder/notes/pinned/hidden).

By default the directory-structure JSON file is stored inside the active
profile's data directory. For a fresh installation this is typically
``~/.overleaf_fs/profiles/primary/local_directory_structure.json``. This keeps the
local directory structure and annotations separate from any particular
project working directory while remaining easy to inspect and
version-control if desired. The exact path is determined by
``overleaf_fs.core.config.get_directory_structure_path()``, so that
future multi-profile and shared-directory support can be added without
changing callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

from overleaf_fs.core.models import ProjectLocal
from overleaf_fs.core import config


def _metadata_path(path: Optional[Path] = None) -> Path:
    """Resolve the path to the local directory‑structure JSON file.

    If ``path`` is provided, it is returned as‑is (converted to a
    ``Path``). Otherwise, the centralized configuration helper
    ``config.get_directory_structure_path()`` is used.

    Centralizing this logic allows future multi‑profile and
    shared‑directory support without modifying callers.

    Args:
        path (Optional[Path]): Explicit directory‑structure file path.
            If provided, this path is returned directly.

    Returns:
        Path: The resolved directory‑structure file path, using
        ``config.get_directory_structure_path()`` when no explicit path is
        given.
    """
    if path is not None:
        return Path(path)
    return config.get_directory_structure_path()


def _project_local_to_dict(local: ProjectLocal) -> Dict:
    """
    Convert a ``ProjectLocal`` instance into a plain dict suitable
    for JSON serialization.

    Args:
        local (ProjectLocal): The local per‑project directory-structure
            fields to convert.

    Returns:
        Dict: A JSON‑serializable dictionary representation.
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
    directory‑structure JSON files remain compatible if new fields are
    added later.

    Args:
        data (Mapping): Raw mapping loaded from JSON.

    Returns:
        ProjectLocal: A populated local metadata object.
    """
    folder = data.get("folder")
    notes = data.get("notes")
    pinned = bool(data.get("pinned", False))
    hidden = bool(data.get("hidden", False))
    return ProjectLocal(folder=folder, notes=notes, pinned=pinned, hidden=hidden)


@dataclass
class LocalState:
    """
    Container for all local directory‑structure data persisted in the JSON file.

    Attributes:
        folders: Explicit list of folder paths known to the application, such
            as "CT" or "Teaching/2025". This allows empty folders to be
            persisted even if no project currently resides in them.
            The Home folder is represented by the empty string "".
        projects: Mapping from project id to ``ProjectLocal`` describing the
            local per‑project directory-structure fields (folder/notes/pinned/hidden)
            for each known project.
    """

    folders: List[str] = field(default_factory=list)
    projects: Dict[str, ProjectLocal] = field(default_factory=dict)


def _decode_state(raw: Mapping) -> LocalState:
    """
    Decode a raw JSON object into a LocalState.

    This is tolerant of missing keys and unexpected shapes so that
    older or partially written files do not cause hard failures.

    Args:
        raw (Mapping): Raw JSON object decoded from disk.

    Returns:
        LocalState: Decoded folder list and per‑project local
            directory-structure fields.
    """
    projects_raw = raw.get("projects", {})
    folders_raw = raw.get("folders", [])

    projects: Dict[str, ProjectLocal] = {}
    if isinstance(projects_raw, dict):
        for proj_id, proj_data in projects_raw.items():
            if not isinstance(proj_id, str):
                continue
            if not isinstance(proj_data, Mapping):
                continue
            projects[proj_id] = _project_local_from_dict(proj_data)

    folders: List[str] = []
    if isinstance(folders_raw, list):
        for entry in folders_raw:
            if isinstance(entry, str) and entry:
                folders.append(entry)

    return LocalState(folders=folders, projects=projects)


def load_directory_structure(path: Optional[Path] = None) -> LocalState:
    """
    Load the full local directory‑structure state (folders and per‑project
    local fields) from disk.

    Args:
        path (Optional[Path]): Optional explicit JSON path. If omitted,
            the default path from ``config.get_directory_structure_path()``
            is used.

    Returns:
        LocalState: Object containing folders and per‑project local fields.
        If the file is missing or invalid, an empty LocalState is returned.
    """
    metadata_file = _metadata_path(path)
    if not metadata_file.exists():
        return LocalState()

    try:
        with metadata_file.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        # On any I/O or JSON error, fall back to an empty state.
        return LocalState()

    if not isinstance(raw, Mapping):
        return LocalState()

    return _decode_state(raw)


def save_directory_structure(state: LocalState, path: Optional[Path] = None) -> None:
    """
    Save the full local directory‑structure (folders and per‑project
    local fields) to disk.

    Args:
        state (LocalState): Full local directory‑structure to write to disk.
        path (Optional[Path]): Optional explicit path. If omitted,
            the default directory‑structure path is used.

    Returns:
        None
    """
    metadata_file = _metadata_path(path)

    # Ensure parent directory exists.
    if metadata_file.parent and not metadata_file.parent.exists():
        metadata_file.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": 1,
        "folders": list(state.folders),
        "projects": {
            proj_id: _project_local_to_dict(local)
            for proj_id, local in state.projects.items()
        },
    }

    with metadata_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def create_folder(folder_path: str, path: Optional[Path] = None) -> LocalState:
    """Create a new folder path in the local directory‑structure, if it does not exist.

    This is a convenience helper that:

    * Loads the current LocalState from disk.
    * Adds ``folder_path`` to the ``folders`` list if it is not already
      present.
    * Saves the updated directory structure back to disk.

    It does not modify any project assignments; projects must be moved
    into the new folder separately.

    Args:
        folder_path (str): Folder path to create, e.g. ``"CT"`` or
            ``"Teaching/2025"``.
        path (Optional[Path]): Optional explicit JSON path. If omitted,
            the default directory‑structure path is used.

    Returns:
        LocalState: The updated local directory structure after creation.
    """
    state = load_directory_structure(path)
    if folder_path and folder_path not in state.folders:
        state.folders.append(folder_path)
        state.folders.sort()
        save_directory_structure(state, path)
    return state


def rename_folder(old_path: str, new_path: str, path: Optional[Path] = None) -> LocalState:
    """Rename a folder (and its subtree) in the local directory‑structure.

    This updates both the explicit ``folders`` list and any project
    assignments whose folder path lies within the renamed subtree.

    For example, renaming ``"Teaching"`` to ``"Teaching2025"`` will
    update:

    * folder entries:
        - ``"Teaching"`` -> ``"Teaching2025"``
        - ``"Teaching/2025"`` -> ``"Teaching2025/2025"``
    * project folder assignments:
        - ``"Teaching"`` -> ``"Teaching2025"``
        - ``"Teaching/2025"`` -> ``"Teaching2025/2025"``

    Args:
        old_path (str): Existing folder path to rename.
        new_path (str): New folder path to assign.
        path (Optional[Path]): Optional explicit JSON path. If omitted,
            the default directory‑structure path is used.

    Returns:
        LocalState: The updated local directory structure after renaming.
    """
    if not old_path or old_path == new_path:
        return load_directory_structure(path)

    state = load_directory_structure(path)

    # Update folder list: replace old_path and any descendants whose
    # paths start with old_path + "/".
    updated_folders: List[str] = []
    prefix = old_path + "/"
    for folder in state.folders:
        if folder == old_path:
            updated_folders.append(new_path)
        elif folder.startswith(prefix):
            updated_folders.append(new_path + folder[len(old_path) :])
        else:
            updated_folders.append(folder)
    state.folders = sorted({f for f in updated_folders if f})

    # Update project assignments.
    for proj_local in state.projects.values():
        f = proj_local.folder
        if not f:
            continue
        if f == old_path:
            proj_local.folder = new_path
        elif f.startswith(prefix):
            proj_local.folder = new_path + f[len(old_path) :]

    save_directory_structure(state, path)
    return state


def delete_folder(folder_path: str, path: Optional[Path] = None) -> LocalState:
    """Delete a folder and its subtree from the local directory structure, if empty.

    A folder subtree may be deleted only if there are no projects whose
    ``ProjectLocal.folder`` lies within that subtree. In particular, if
    any project has a folder equal to ``folder_path`` or starting with
    ``folder_path + "/"``, this function will raise a ``ValueError`` and
    leave the state unchanged.

    When deletion is allowed, this function:

    * Removes ``folder_path`` and any descendant folders from the
      ``folders`` list.
    * Leaves all project assignments unchanged (since the subtree is
      guaranteed to be empty of projects).

    Args:
        folder_path (str): Folder path to delete (subtree root).
        path (Optional[Path]): Optional explicit JSON path. If omitted,
            the default directory‑structure path is used.

    Returns:
        LocalState: The updated local directory structure after deletion.

    Raises:
        ValueError: If any project is assigned to a folder within the
        subtree rooted at ``folder_path``.
    """
    if not folder_path:
        return load_directory_structure(path)

    state = load_directory_structure(path)

    # Check for projects in this subtree.
    prefix = folder_path + "/"
    for proj_id, proj_local in state.projects.items():
        f = proj_local.folder or ""
        if f == folder_path or f.startswith(prefix):
            raise ValueError(
                f"Cannot delete folder '{folder_path}': project '{proj_id}' "
                f"is assigned to folder '{f}'."
            )

    # Remove the folder and all descendants from the folder list.
    updated_folders: List[str] = []
    for folder in state.folders:
        if folder == folder_path:
            continue
        if folder.startswith(prefix):
            continue
        updated_folders.append(folder)
    state.folders = updated_folders

    save_directory_structure(state, path)
    return state


def move_projects_to_folder(
    project_ids: Iterable[str],
    folder_path: Optional[str],
    path: Optional[Path] = None,
) -> LocalState:
    """Assign the given projects to a folder in the local directory‑structure.

    This helper updates ``ProjectLocal.folder`` for each project id in
    ``project_ids`` and persists the modified directory structure to disk.

    Semantics:

    * ``folder_path`` of ``None`` or ``""`` assigns projects to the Home
      folder (top-level). In the JSON representation this is stored as
      an empty string.
    * A non-empty ``folder_path`` (e.g. ``"CT"`` or ``"Teaching/2025"``)
      is used as-is. If it does not already appear in ``state.folders``,
      it is added to that list so that the tree view can display it.
    * If a project id does not yet have a ``ProjectLocal`` entry, one is
      created with default values for notes/pinned/hidden.

    Args:
        project_ids (Iterable[str]): Project ids to move.
        folder_path (Optional[str]): Target folder path, or ``None``/``""``
            for the Home folder.
        path (Optional[Path]): Optional explicit JSON path. If omitted,
            the default directory‑structure path is used.

    Returns:
        LocalState: The updated local directory structure after modifying project
        assignments.
    """
    # Normalize the target folder: None and "" mean Home.
    target = "" if folder_path in (None, "") else folder_path

    state = load_directory_structure(path)

    # Ensure the target folder exists in the folder list if it is
    # non-empty. Home (empty string) is implicit and not stored in
    # LocalState.folders.
    if target and target not in state.folders:
        state.folders.append(target)
        state.folders.sort()

    # Update or create per-project local metadata.
    for proj_id in project_ids:
        if not isinstance(proj_id, str):
            continue
        local = state.projects.get(proj_id)
        if local is None:
            local = ProjectLocal(folder=target, notes=None, pinned=False, hidden=False)
            state.projects[proj_id] = local
        else:
            local.folder = target

    save_directory_structure(state, path)
    return state
