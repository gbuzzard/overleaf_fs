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

import overleaf_fs.core.profiles
from overleaf_fs.core.models import ProjectLocal
from overleaf_fs.core import config


@dataclass
class FolderMovePlan:
    """Read-only description of a prospective folder move.

    This does not modify the on-disk directory structure. Instead, it
    describes what *would* change if the folder subtree rooted at
    ``old_root`` were moved under ``new_parent``.

    Attributes:
        old_root: Existing folder path that is being moved (subtree root),
            e.g. ``"Admin/Misc"``.
        new_parent: Target parent folder path, or ``None``/``""`` for the
            top-level Home folder. For example, moving ``"Admin/Misc"``
            under ``"Admin/2025"`` yields a new root of
            ``"Admin/2025/Misc"``.
        new_root: The computed new root path for the subtree (e.g.
            ``"Admin/2025/Misc"``), or ``None`` if the plan is invalid.
        folder_renames: Mapping from old folder paths to new folder
            paths for the subtree rooted at ``old_root``. This corresponds
            to how ``LocalDirectoryStructure.folders`` would change.
        project_folder_changes: Mapping from project id to a small
            dict with ``"old_folder"`` and ``"new_folder"`` keys, showing
            how ``ProjectLocal.folder`` would change for each affected
            project.
        num_folders_changed: Number of entries in ``folder_renames``.
        num_projects_changed: Number of entries in ``project_folder_changes``.
        conflicting_folders: List of target folder paths that already
            exist *outside* the moved subtree. These indicate that the
            move would merge content into existing folders at those paths.
        is_valid: False if the requested move is structurally invalid
            (e.g., moving a folder into its own subtree). In that case
            no changes should be applied.
        error: Optional human-readable explanation if ``is_valid`` is False.
    """

    old_root: str
    new_parent: Optional[str]
    new_root: Optional[str]
    folder_renames: Dict[str, str]
    project_folder_changes: Dict[str, Dict[str, str]]
    num_folders_changed: int
    num_projects_changed: int
    conflicting_folders: List[str] = field(default_factory=list)
    is_valid: bool = True
    error: Optional[str] = None


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
    return overleaf_fs.core.profiles.get_directory_structure_path()


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


def plan_folder_move(
    old_root: str,
    new_parent: Optional[str],
    path: Optional[Path] = None,
) -> FolderMovePlan:
    """Compute a read-only plan for moving a folder subtree.

    This function inspects the current on-disk directory structure and
    returns a ``FolderMovePlan`` describing what *would* change if the
    folder subtree rooted at ``old_root`` were moved under
    ``new_parent``.

    Semantics are parallel to ``rename_folder``:

    * All folder paths in ``LocalDirectoryStructure.folders`` that are
      equal to ``old_root`` or start with ``old_root + "/"`` are
      considered part of the subtree and would be rewritten with a
      ``new_root`` prefix.
    * All projects whose ``ProjectLocal.folder`` is equal to
      ``old_root`` or starts with ``old_root + "/"`` would have their
      folder updated in the same way.
    * ``new_parent`` of ``None`` or ``""`` denotes the top-level Home
      folder. In that case, the new root is just the last path segment
      of ``old_root`` (e.g. ``"Admin/Misc"`` -> ``"Misc"``).
    * If the computed ``new_root`` is identical to ``old_root``, the
      plan is treated as a no-op (no changes).

    This function does **not** modify the on-disk JSON. Callers can use
    the returned plan for previews or confirmations before adding a
    separate mutating helper.

    Args:
        old_root: Existing folder path to move (subtree root), e.g.
            ``"Admin/Misc"``.
        new_parent: Target parent folder path, or ``None``/``""`` for
            the Home (top-level) folder.
        path: Optional explicit path to the directory-structure JSON. If
            omitted, the default path is used.

    Returns:
        FolderMovePlan: A plan describing the prospective move. If the
        move is structurally invalid (e.g. moving into its own subtree),
        ``is_valid`` will be False and ``error`` will contain a message.
    """
    old_root_norm = old_root.rstrip("/")

    # Base kwargs shared by all return paths; we mutate this dict per scenario.
    plan_kwargs: Dict[str, object] = {
        "old_root": old_root_norm,
        "new_parent": new_parent,
        "new_root": None,
        "folder_renames": {},
        "project_folder_changes": {},
        "num_folders_changed": 0,
        "num_projects_changed": 0,
        "conflicting_folders": [],
        "is_valid": True,
        "error": None,
    }

    # Moving the implicit Home/root folder is unsupported here.
    if not old_root_norm:
        plan_kwargs["new_root"] = None
        plan_kwargs["is_valid"] = False
        plan_kwargs["error"] = "Cannot move the Home/root folder."
        return FolderMovePlan(**plan_kwargs)  # type: ignore[arg-type]

    # Normalize new_parent: None/"" -> Home; strip trailing slashes.
    parent = (new_parent or "").rstrip("/")

    # Compute the new root path for the subtree.
    last_segment = old_root_norm.split("/")[-1]
    if parent:
        new_root = f"{parent}/{last_segment}"
    else:
        # Moving to top-level: keep only the last segment.
        new_root = last_segment
    plan_kwargs["new_root"] = new_root

    # If the new root is the same as the old root, this is a no-op.
    if new_root == old_root_norm:
        # Valid but empty plan: nothing would change.
        return FolderMovePlan(**plan_kwargs)  # type: ignore[arg-type]

    # Prevent moving a folder into its own subtree, e.g. moving "Admin"
    # under "Admin/Misc".
    if parent and (parent == old_root_norm or parent.startswith(old_root_norm + "/")):
        plan_kwargs["new_root"] = None
        plan_kwargs["is_valid"] = False
        plan_kwargs["error"] = (
            f"Cannot move folder '{old_root_norm}' into its own subtree "
            f"under '{parent}'."
        )
        return FolderMovePlan(**plan_kwargs)  # type: ignore[arg-type]

    # Load current directory structure without mutating it.
    loc_dir_struct = load_directory_structure(path)

    # Compute folder renames within the subtree.
    folder_renames: Dict[str, str] = {}
    prefix = old_root_norm + "/"
    for folder in loc_dir_struct.folders:
        if folder == old_root_norm:
            folder_renames[folder] = new_root
        elif folder.startswith(prefix):
            folder_renames[folder] = new_root + folder[len(old_root_norm) :]
        # Folders outside the subtree are unchanged and omitted.

    # Compute project folder changes within the subtree.
    project_changes: Dict[str, Dict[str, str]] = {}
    for proj_id, proj_local in loc_dir_struct.projects.items():
        f = proj_local.folder
        if not f:
            # Projects in Home are not part of this subtree.
            continue
        if f == old_root_norm:
            project_changes[proj_id] = {"old_folder": f, "new_folder": new_root}
        elif f.startswith(prefix):
            project_changes[proj_id] = {
                "old_folder": f,
                "new_folder": new_root + f[len(old_root_norm) :],
            }

    num_folders_changed = len(folder_renames)
    num_projects_changed = len(project_changes)

    plan_kwargs["folder_renames"] = folder_renames
    plan_kwargs["project_folder_changes"] = project_changes
    plan_kwargs["num_folders_changed"] = num_folders_changed
    plan_kwargs["num_projects_changed"] = num_projects_changed

    # Summarize folder "conflicts" (i.e., merges): new folder targets that
    # already exist outside the moved subtree.
    if num_folders_changed > 0:
        subtree_folders = set(folder_renames.keys())
        existing_folders = set(loc_dir_struct.folders)
        other_folders = existing_folders - subtree_folders
        target_paths = set(folder_renames.values())
        conflicting = sorted(tp for tp in target_paths if tp in other_folders)
        plan_kwargs["conflicting_folders"] = conflicting

    return FolderMovePlan(**plan_kwargs)  # type: ignore[arg-type]


# --- New helpers for applying and performing folder moves ---

def apply_folder_move(
    plan: FolderMovePlan,
    path: Optional[Path] = None,
) -> LocalDirectoryStructure:
    """Apply a previously computed ``FolderMovePlan`` to the on-disk structure.

    This mutating helper loads the current ``LocalDirectoryStructure``
    from disk, applies the folder and project-folder rewrites
    described in ``plan``, and then saves the result back to disk.

    The plan itself is treated as read-only; callers are responsible
    for ensuring that it was computed against the same directory-
    structure file (or at least a compatible snapshot). In typical
    usage, callers will:

    * Call :func:`plan_folder_move` to compute a plan.
    * Optionally present a summary/confirmation to the user.
    * If confirmed, call :func:`apply_folder_move` to apply it.

    Args:
        plan: A ``FolderMovePlan`` previously returned by
            :func:`plan_folder_move`.
        path: Optional explicit path to the directory-structure JSON.
            If omitted, the default path is used.

    Returns:
        LocalDirectoryStructure: The updated local directory structure
        after applying the move.

    Raises:
        ValueError: If ``plan.is_valid`` is False.
    """
    if not plan.is_valid:
        raise ValueError(plan.error or "Invalid folder move plan.")

    # Load the current structure from disk.
    loc_dir_struct = load_directory_structure(path)

    # If the plan is a no-op (no folders/projects changed), simply
    # return the current structure unchanged.
    if plan.num_folders_changed == 0 and plan.num_projects_changed == 0:
        return loc_dir_struct

    # Apply folder renames to the explicit folder list.
    if plan.folder_renames:
        updated_folders: List[str] = []
        renames = plan.folder_renames
        for folder in loc_dir_struct.folders:
            new_folder = renames.get(folder, folder)
            if new_folder:
                updated_folders.append(new_folder)
        # De-duplicate and sort for a stable, compact representation.
        loc_dir_struct.folders = sorted({f for f in updated_folders if f})

    # Apply project-folder changes.
    if plan.project_folder_changes:
        changes = plan.project_folder_changes
        for proj_id, change in changes.items():
            local = loc_dir_struct.projects.get(proj_id)
            if local is None:
                # It is unlikely but possible that a project appears in
                # the plan but is missing from the current structure.
                # In that case, create a minimal entry anchored at the
                # new folder so that the tree remains consistent.
                new_folder = change.get("new_folder")
                local = ProjectLocal(
                    folder=new_folder,
                    notes=None,
                    pinned=False,
                    hidden=False,
                )
                loc_dir_struct.projects[proj_id] = local
            else:
                new_folder = change.get("new_folder")
                if new_folder is not None:
                    local.folder = new_folder

    # Persist the updated structure.
    save_directory_structure(loc_dir_struct, path)
    return loc_dir_struct


def move_folder(
    old_root: str,
    new_parent: Optional[str],
    path: Optional[Path] = None,
) -> FolderMovePlan:
    """High-level helper to move a folder subtree on disk.

    This convenience function combines :func:`plan_folder_move` and
    :func:`apply_folder_move` into a single call:

    * A ``FolderMovePlan`` is computed for the requested move.
    * If the plan is invalid (``is_valid`` is False), a ``ValueError``
      is raised and no changes are applied.
    * If the plan is valid and non-empty, it is applied to the on-disk
      directory structure.
    * The plan is returned to the caller for logging or UI feedback.

    Args:
        old_root: Existing folder path to move (subtree root), e.g.
            ``"Admin/Misc"``.
        new_parent: Target parent folder path, or ``None``/``""`` for
            the Home (top-level) folder.
        path: Optional explicit path to the directory-structure JSON.
            If omitted, the default path is used.

    Returns:
        FolderMovePlan: The plan describing the move that was applied.

    Raises:
        ValueError: If the prospective move is structurally invalid.
    """
    plan = plan_folder_move(old_root=old_root, new_parent=new_parent, path=path)
    if not plan.is_valid:
        raise ValueError(plan.error or "Invalid folder move request.")

    # Apply the move only if there is something to do; this keeps the
    # operation idempotent for no-op moves.
    if plan.num_folders_changed > 0 or plan.num_projects_changed > 0:
        apply_folder_move(plan, path=path)

    return plan


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


def set_projects_pinned(
    project_ids: Iterable[str],
    pinned: bool,
    path: Optional[Path] = None,
) -> LocalDirectoryStructure:
    """Set the ``pinned`` flag for the given projects and save the result.

    This helper loads the current directory-structure JSON, updates
    ``ProjectLocal.pinned`` for each project id in ``project_ids``, and
    writes the modified structure back to disk.

    Semantics:

    * If a project id does not yet have a ``ProjectLocal`` entry, one is
      created with default values for folder/notes/hidden and the requested
      ``pinned`` value.
    * Existing ``ProjectLocal`` entries are updated in place; only the
      ``pinned`` field is modified.

    Args:
        project_ids (Iterable[str]): Project ids whose pinned status will
            be updated.
        pinned (bool): Desired value for the pinned flag.
        path (Optional[Path]): Optional explicit JSON path. If omitted,
            the default directory-structure path is used.

    Returns:
        LocalDirectoryStructure: The updated local directory structure after
        modifying the pinned flags.
    """
    loc_dir_struct = load_directory_structure(path)

    for proj_id in project_ids:
        if not isinstance(proj_id, str):
            continue
        local = loc_dir_struct.projects.get(proj_id)
        if local is None:
            # Create a new ProjectLocal with the requested pinned value.
            local = ProjectLocal(folder=None, notes=None, pinned=pinned, hidden=False)
            loc_dir_struct.projects[proj_id] = local
        else:
            local.pinned = pinned

    save_directory_structure(loc_dir_struct, path)
    return loc_dir_struct
