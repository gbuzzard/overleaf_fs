from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class ProjectRemote:
    """
    Remote (Overleaf-side) fields for a project, as stored in the
    projects-info JSON file.

    These values are populated/updated when we "refresh from Overleaf".
    They should be treated as read-only from the GUI; edits happen on
    Overleaf itself and are pulled in on the next refresh.
    """

    #: Stable Overleaf project id, derived from the project URL.
    #: Example: for "https://www.overleaf.com/project/abcdef123456",
    #: the id is "abcdef123456".
    id: str

    #: Project title as shown in the Overleaf UI.
    name: str

    #: Full Overleaf URL for this project.
    url: str

    #: Owner label derived from Overleaf project info. At the
    #: moment this is typically the owner's email address (e.g.
    #: "user@example.com"), but it is intentionally kept as a
    #: free-form string so we are not tightly coupled to any
    #: particular Overleaf wording or field.
    owner_label: Optional[str] = None

    #: Human-friendly owner name (e.g. "First Last"), if available. This is
    #: used for display and search in the UI. Unlike ``owner_label`` (which
    #: is typically the owner's email/login and serves as a stable identifier),
    #: this field is intended purely for user-facing presentation.
    owner_display_name: Optional[str] = None

    #: Raw "last modified" value as obtained from Overleaf project info,
    #: typically an ISO 8601 timestamp string (e.g.
    #: "2025-11-26T15:48:18.875Z"). Keeping the raw string allows us
    #: to display or log it even if parsing fails or the format
    #: changes.
    last_modified_raw: Optional[str] = None

    #: Parsed last modified timestamp, if we are able to parse
    #: ``last_modified_raw`` into a ``datetime``. This is useful for
    #: sorting and advanced filtering. It is optional because parsing
    #: may fail or the value may be missing.
    last_modified: Optional[datetime] = None

    #: Whether Overleaf marks this project as archived.
    archived: bool = False


@dataclass
class ProjectLocal:
    """
    Local-only directory-structure fields used by OverleafFS,
    as stored in the directory-structure JSON file.

    These fields are never pushed back to Overleaf; they represent how
    you choose to organize and annotate projects on your own machine
    (folder, notes, pinned/hidden flags).
    """
    # Folder path in the virtual project tree, or None/"" for the Home
    # folder (root). Examples: "CT" and "Teaching/2025" map to
    # "Home/CT" and "Home/Teaching/2025".
    folder: Optional[str] = None

    #: Optional free-form notes about the project (e.g. status,
    #: deadlines, TODOs).
    notes: Optional[str] = None

    #: Whether this project is "pinned" in the UI. Exact semantics are
    #: up to the GUI (e.g. show pinned projects at the top of a list).
    pinned: bool = False

    #: Whether this project should be hidden in normal views. This is
    #: a local analogue of "archived" or "muted" projects.
    hidden: bool = False


@dataclass
class ProjectRecord:
    """
    In-memory record for a single Overleaf project, combining:

    * ``remote``: Overleaf-side fields loaded from the projects-info JSON
      file (``overleaf_projects_info.json``).
    * ``local``: directory-structure fields loaded from the
      directory-structure JSON file (``local_directory_structure.json``).

    Conceptually, these two JSON files are merged into a single
    ``ProjectRecord`` for each project id:

    * The ``remote`` part mirrors what Overleaf reports and is overwritten
      whenever we refresh from Overleaf.
    * The ``local`` part contains only local directory-structure fields
      (folder/notes/pinned/hidden) and is modified only by user actions
      in the GUI or by local configuration; it should be preserved across
      refreshes. If no local entry exists for a given project id, a
      default ``ProjectLocal()`` is used.

    The remote part also includes the ``archived`` boolean field, which
    reflects whether Overleaf marks the project as archived on the server.
    If a project is removed from Overleaf, it will no longer appear in
    the projects-info JSON and will therefore be dropped from the
    in-memory index on the next refresh.
    """

    remote: ProjectRemote
    local: ProjectLocal = field(default_factory=ProjectLocal)

    @property
    def id(self) -> str:
        """Convenience alias for ``self.remote.id``."""
        return self.remote.id

    @property
    def name(self) -> str:
        """Convenience alias for ``self.remote.name``."""
        return self.remote.name

    @property
    def url(self) -> str:
        """Convenience alias for ``self.remote.url``."""
        return self.remote.url


# Simple in-memory index type: maps project id -> ProjectRecord,
# combining remote (projects-info) and local (directory-structure)
# data for each known project. This is the merged view that the GUI
# works with when displaying and organizing projects.
ProjectsIndex = Dict[str, ProjectRecord]
