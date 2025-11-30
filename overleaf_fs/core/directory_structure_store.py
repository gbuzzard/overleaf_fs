"""
Local directory-structure storage for Overleaf projects.

Design overview
---------------
The core data model (see ``overleaf_fs.core.models``) separates
"remote" project info (what Overleaf reports about a project: id, name,
owner, last modified, URL, etc., typically in `overleaf_projects_info.json`)
from "local" organization and annotation data that lives only on your
machine (folder, notes, pinned, hidden, typically `local_directory_structure.json`).

This module focuses on persisting and loading the *local* directory
structure and per-project local fields, keyed by project id, using a
simple JSON file on disk.  The JSON schema is intentionally lightweight
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
      "version": 1
    }
In the per-project fields, the empty string ``""`` for ``"folder"``
indicates the top-level Home directory. For example, a project whose
folder is stored as ``"CT"`` will appear under ``"Home/CT"`` in the GUI.
Only the local fields are stored. The remote projects info is refreshed
from Overleaf and merged with this local directory-structure data into
``ProjectRecord`` instances elsewhere.

The module provides two layers of API:

- ``load_directory_structure()`` / ``save_directory_structure()``: work with a
  ``LocalDirectoryStructure`` object that includes both the explicit folder list
  and the per-project ``ProjectLocal`` fields (folder/notes/pinned/hidden).

- Convenience helpers such as ``create_folder()``, ``rename_folder()``,
  ``delete_folder()``, and ``move_projects_to_folder()`` that operate on the
  on-disk JSON by loading, modifying, and re-saving the directory structure.

By default, the directory-structure JSON file is stored inside the active
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
from typing import Dict, Iterable, List, Mapping, Optional, Union

from overleaf_fs.core.models import ProjectLocal
from overleaf_fs.core import config


def _directory_structure_path(path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve the path to the local directory‑structure JSON file.

    If ``path`` is provided, it may be a ``str`` or ``Path`` and is
    returned as‑is (converted to a ``Path``). Otherwise, the centralized
    configuration helper ``config.get_directory_structure_path()`` is
    used.

    Centralizing this logic allows future multi‑profile and
    shared‑directory support without modifying callers.

    Args:
        path (Optional[Union[str, Path]]): Explicit directory‑structure
            file path (``str`` or ``Path``). If provided, this path is
            returned directly.

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
        ProjectLocal: An object containing local project info - folder, pinned, etc.
    """
    folder = data.get("folder")
    notes = data.get("notes")
    pinned = bool(data.get("pinned", False))
    hidden = bool(data.get("hidden", False))
    return ProjectLocal(folder=folder, notes=notes, pinned=pinned, hidden=hidden)


@dataclass
class LocalDirectoryStructure:
    """
    Container for all local directory‑structure data persisted in the JSON file.

    Attributes:
        folders: Explicit list of folder paths known to the application, such
            as "CT" or "Teaching/2025". This allows empty folders to be
            persisted even if no project currently resides in them. The Home
            folder is implicit and is not stored in this list; it is
            represented by the empty string ``""`` in ``ProjectLocal.folder``.
        projects: Mapping from project id to ``ProjectLocal`` describing the
            local per‑project directory-structure fields (folder/notes/pinned/hidden)
            for each known project.
    """

    folders: List[str] = field(default_factory=list)
    projects: Dict[str, ProjectLocal] = field(default_factory=dict)


def _decode_json_dir_structure(raw: Mapping) -> LocalDirectoryStructure:
    """
    Decode a raw JSON object into a LocalDirectoryStructure.

    This is tolerant of missing keys and unexpected shapes so that
    older or partially written files do not cause hard failures.

    Args:
        raw (Mapping): Raw JSON object decoded from disk.

    Returns:
        LocalDirectoryStructure: Decoded folder list and per‑project local
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
            # Ignore empty-string entries: the Home folder is implicit and
            # represented by "" in ProjectLocal.folder, not in LocalDirectoryStructure.folders.
            if isinstance(entry, str) and entry:
                folders.append(entry)

    return LocalDirectoryStructure(folders=folders, projects=projects)


def load_directory_structure(path: Optional[Path] = None) -> LocalDirectoryStructure:
    """
    Load the full local directory‑structure (folders and per‑project
    local fields) from disk.

    Args:
        path (Optional[Path]): Optional explicit JSON path. If omitted,
            the default path from ``config.get_directory_structure_path()``
            is used.

    Returns:
        LocalDirectoryStructure: Object containing folders and per‑project local fields.
        If the file is missing or invalid, an empty LocalDirectoryStructure is returned.
    """
    dir_struct_path = _directory_structure_path(path)
    if not dir_struct_path.exists():
        return LocalDirectoryStructure()

    try:
        with dir_struct_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        # On any I/O or JSON error, fall back to an empty directory structure.
        return LocalDirectoryStructure()

    if not isinstance(raw, Mapping):
        return LocalDirectoryStructure()

    return _decode_json_dir_structure(raw)


def save_directory_structure(loc_dir_struct: LocalDirectoryStructure, path: Optional[Path] = None) -> None:
    """
    Save the full local directory‑structure (folders and per‑project
    local fields) to disk.

    Args:
        loc_dir_struct (LocalDirectoryStructure): Full local directory‑structure to write to disk.
        path (Optional[Path]): Optional explicit path. If omitted,
            the default directory‑structure path is used.

    Returns:
        None
    """
    dir_struct_path = _directory_structure_path(path)

    # Ensure parent directory exists.
    if dir_struct_path.parent and not dir_struct_path.parent.exists():
        dir_struct_path.parent.mkdir(parents=True, exist_ok=True)

    # NOTE: The "version" field is currently written but ignored on load.
    # It exists to support future changes to the on-disk JSON format.
    data = {
        "version": config.FILE_FORMAT_VERSION,
        "folders": list(loc_dir_struct.folders),
        "projects": {
            proj_id: _project_local_to_dict(local)
            for proj_id, local in loc_dir_struct.projects.items()
        },
    }

    with dir_struct_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def create_folder(folder_path: str, path: Optional[Path] = None) -> LocalDirectoryStructure:
    """Create a new folder path in the local directory‑structure, if it does not exist.

    This is a convenience helper that:

    * Loads the current LocalDirectoryStructure from disk.
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
        LocalDirectoryStructure: The updated local directory structure after creation.
    """
    loc_dir_struct = load_directory_structure(path)
    if folder_path and folder_path not in loc_dir_struct.folders:
        loc_dir_struct.folders.append(folder_path)
        loc_dir_struct.folders.sort()
        save_directory_structure(loc_dir_struct, path)
    return loc_dir_struct


def rename_folder(old_path: str, new_path: str, path: Optional[Path] = None) -> LocalDirectoryStructure:
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
        LocalDirectoryStructure: The updated local directory structure after renaming.
    """
    if not old_path or old_path == new_path:
        return load_directory_structure(path)

    loc_dir_struct = load_directory_structure(path)

    # Update folder list: replace old_path and any descendants whose
    # paths start with old_path + "/".
    updated_folders: List[str] = []
    prefix = old_path + "/"
    for folder in loc_dir_struct.folders:
        if folder == old_path:
            updated_folders.append(new_path)
        elif folder.startswith(prefix):
            updated_folders.append(new_path + folder[len(old_path) :])
        else:
            updated_folders.append(folder)
    loc_dir_struct.folders = sorted({f for f in updated_folders if f})

    # Update project assignments.
    for proj_local in loc_dir_struct.projects.values():
        f = proj_local.folder
        if not f:
            continue
        if f == old_path:
            proj_local.folder = new_path
        elif f.startswith(prefix):
            proj_local.folder = new_path + f[len(old_path) :]

    save_directory_structure(loc_dir_struct, path)
    return loc_dir_struct


def delete_folder(folder_path: str, path: Optional[Path] = None) -> LocalDirectoryStructure:
    """Delete a folder and its subtree from the local directory structure, if empty.

    A folder subtree may be deleted only if there are no projects whose
    ``ProjectLocal.folder`` lies within that subtree. In particular, if
    any project has a folder equal to ``folder_path`` or starting with
    ``folder_path + "/"``, this function will raise a ``ValueError`` and
    leave the directory structure unchanged. Projects assigned to the
    Home folder (empty string) are not considered part of this subtree.

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
        LocalDirectoryStructure: The updated local directory structure after deletion.

    Raises:
        ValueError: If any project is assigned to a folder within the
        subtree rooted at ``folder_path``.
    """
    if not folder_path:
        return load_directory_structure(path)

    loc_dir_struct = load_directory_structure(path)

    # Check for projects in this subtree.
    prefix = folder_path + "/"
    for proj_id, proj_local in loc_dir_struct.projects.items():
        f = proj_local.folder or ""
        if f == folder_path or f.startswith(prefix):
            raise ValueError(
                f"Cannot delete folder '{folder_path}': project '{proj_id}' "
                f"is assigned to folder '{f}'."
            )

    # Remove the folder and all descendants from the folder list.
    updated_folders: List[str] = []
    for folder in loc_dir_struct.folders:
        if folder == folder_path:
            continue
        if folder.startswith(prefix):
            continue
        updated_folders.append(folder)
    loc_dir_struct.folders = updated_folders

    save_directory_structure(loc_dir_struct, path)
    return loc_dir_struct


def move_projects_to_folder(
    project_ids: Iterable[str],
    folder_path: Optional[str],
    path: Optional[Path] = None,
) -> LocalDirectoryStructure:
    """Assign the given projects to a folder in the local directory‑structure.

    This helper updates ``ProjectLocal.folder`` for each project id in
    ``project_ids`` and persists the modified directory structure to disk.

    Semantics:

    * ``folder_path`` of ``None`` or ``""`` assigns projects to the Home
      folder (top-level). In the JSON representation this is stored as
      an empty string.
    * A non-empty ``folder_path`` (e.g. ``"CT"`` or ``"Teaching/2025"``)
      is used as-is. If it does not already appear in ``loc_dir_struct.folders``,
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
        LocalDirectoryStructure: The updated local directory structure after modifying project
        assignments.
    """
    # Normalize the target folder: None and "" mean Home.
    target = "" if folder_path in (None, "") else folder_path

    loc_dir_struct = load_directory_structure(path)

    # Ensure the target folder exists in the folder list if it is
    # non-empty. Home (empty string) is implicit and not stored in
    # LocalDirectoryStructure.folders.
    if target and target not in loc_dir_struct.folders:
        loc_dir_struct.folders.append(target)
        loc_dir_struct.folders.sort()

    # Update or create per-project local project data - folder, pinned, etc.
    for proj_id in project_ids:
        if not isinstance(proj_id, str):
            continue
        local = loc_dir_struct.projects.get(proj_id)
        if local is None:
            local = ProjectLocal(folder=target, notes=None, pinned=False, hidden=False)
            loc_dir_struct.projects[proj_id] = local
        else:
            local.folder = target

    save_directory_structure(loc_dir_struct, path)
    return loc_dir_struct
