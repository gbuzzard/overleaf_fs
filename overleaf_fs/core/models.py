from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class ProjectRemote:
    """
    Fields that mirror what Overleaf reports for a project.

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

    #: Owner label as reported by Overleaf (e.g. "Owned by you",
    #: "Shared", "Read-only"). This is intentionally a free-form string
    #: so we are not tightly coupled to Overleaf's exact wording.
    owner_label: Optional[str] = None

    #: Raw "last modified" string as shown by Overleaf, if available.
    #: Keeping the raw string allows us to display it exactly as the
    #: site does, even if parsing fails or the format changes.
    last_modified_raw: Optional[str] = None

    #: Parsed last modified timestamp, if we are able to parse
    #: ``last_modified_raw`` into a datetime. This is useful for
    #: sorting and advanced filtering. It is optional because parsing
    #: may fail or the value may be missing.
    last_modified: Optional[datetime] = None


@dataclass
class ProjectLocal:
    """
    Local-only metadata used by the Overleaf Project Explorer.

    These fields are never pushed back to Overleaf; they represent how
    you choose to organize and annotate projects on your own machine.
    """

    #: Free-form tags used to build virtual folder trees and saved
    #: views. A project can have multiple tags and appear in multiple
    #: "folders" in the GUI.
    tags: List[str] = field(default_factory=list)

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
    Full record for a single Overleaf project, combining remote and
    local metadata.

    The ``remote`` part is overwritten whenever we refresh from
    Overleaf. The ``local`` part is only modified by user actions in
    the GUI or by local configuration, and it should be preserved
    across refreshes.
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


# Simple in-memory index type: maps project id -> project record.
ProjectIndex = Dict[str, ProjectRecord]
