"""
Helpers for synchronizing the local project index with Overleaf.

This module implements a minimal HTML-scraping workflow for refreshing
the profile's cached Overleaf projects-info JSON file
(``overleaf_projects_info.json``) from the Overleaf project dashboard.
It is built around two key ideas:

* Each profile is associated with an Overleaf **base URL** (for
  example, ``https://www.overleaf.com`` for the public service or an
  institution-hosted deployment such as an ORNL instance). All
  scraping and project URLs are derived from this base URL.
* Authentication uses a browser-style session cookie (typically the
  ``overleaf_session2`` cookie) so that we can reuse the same login
  credentials as the user's normal browser.

There are two ways for GUI code to obtain the cookie header used by the
functions in this module:

1. **Embedded login dialog (preferred when Qt WebEngine is available).**
   The GUI presents a small browser window pointed at the configured
   Overleaf base URL. The user logs in as usual, and the application
   reads the session cookies from the embedded browser's cookie store to
   construct a ``Cookie`` header string.
2. **Manual cookie paste (fallback).** The user logs into Overleaf in
   their normal browser, uses the browser's developer tools to copy the
   ``Cookie`` header for a request to the project dashboard, and pastes
   that header into the GUI.

To avoid forcing the user to supply the cookie on every sync, we
optionally store the raw Cookie header in a small JSON file in the
active profile's directory. This file contains only the cookie
header and a timestamp. The GUI is responsible for asking the user
whether they consent to saving the cookie locally.

The current Overleaf dashboard exposes the project list via a JSON blob
embedded in a ``<meta>`` tag (``name="ol-prefetchedProjectsBlob"``). We
parse that blob rather than scraping visual table markup. The parsing
logic is intentionally defensive: if it cannot find any projects, it
raises a clear error instead of silently writing an empty project info
file.

Future enhancements may include using a more direct JSON API if Overleaf
exposes one for the dashboard or adding richer diagnostics around
cookie expiry and login failures.
"""


from __future__ import annotations

import json
import logging
from html import unescape
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup  # type: ignore[import]

import overleaf_fs.core.profiles
from overleaf_fs.core import config
from overleaf_fs.core.config import COOKIE_FILENAME
from overleaf_fs.core.profiles import get_projects_info_path, get_overleaf_base_url, get_active_profile_id

LOGGER = logging.getLogger(__name__)

DASHBOARD_PATH = "/project"


def _get_overleaf_base_url() -> str:
    """Return the normalized Overleaf base URL for the active profile.

    The base URL is obtained from the profile configuration via
    :func:`get_overleaf_base_url` and normalized by stripping any
    trailing slash. This allows institution-hosted Overleaf instances
    (for example an ORNL deployment) to be used transparently.
    """
    base = get_overleaf_base_url().strip()
    if not base:
        # Fallback to the default if the config is empty for any reason.
        base = "https://www.overleaf.com"
    # Normalize by removing any trailing slash.
    return base.rstrip("/")


def _get_overleaf_host() -> str:
    """Return the hostname component of the Overleaf base URL.

    This is used when attaching cookies to a requests.Session so that
    the cookie domain matches the configured Overleaf server for the
    active profile.
    """
    base = _get_overleaf_base_url()
    parsed = urlparse(base)
    host = parsed.hostname or "www.overleaf.com"
    return host


class CookieRequiredError(ValueError):
    """Raised when a refresh requires a browser cookie header.

    This is used to signal to GUI code that it should prompt the user
    for a Cookie header copied from their browser, rather than treating
    the error as a generic scrape failure.
    """


@dataclass
class OverleafProjectDTO:
    """Simple data transfer object for a single Overleaf project.

    This is the intermediate representation used by the scraper logic
    before we write the profile's projects-info JSON file
    (``overleaf_projects_info.json``).

    Attributes:
        id: Overleaf project identifier.
        name: Human-readable project name.
        url: Full URL to the project on Overleaf.
        owner_label: Label derived from Overleaf project info.
            At the moment this is typically the owner's email address,
            but it is intentionally kept as a free-form string so we
            are not tightly coupled to a particular field.
        owner_display_name: Human-friendly owner name suitable for
            display and searching (for example, "First Last"), if
            available. This is kept separate from ``owner_label`` so
            that UI code can use a readable name while still retaining
            a stable identifier.
        last_modified_iso: ISO 8601 timestamp string for the last
            modified time, or None if not available.
        last_modified_raw: Raw "last modified" value as obtained
            from Overleaf info, typically the same ISO 8601
            timestamp string. Keeping the raw string allows us to
            display or log it even if parsing fails or the format
            changes.
        archived: Whether Overleaf marks this project as archived.
    """

    id: str
    name: str
    url: str
    owner_label: str
    owner_display_name: Optional[str]
    last_modified_iso: Optional[str]
    last_modified_raw: Optional[str]
    archived: bool = False

# NOTE: When adding new fields (for example, ``owner_display_name``),
# remember to keep OverleafProjectDTO, the projects-info JSON format
# written by ``_dto_to_projects_info_entry``, and the ``ProjectRemote``
# dataclass in ``models.py`` in sync.


# ---------------------------------------------------------------------------
# Cookie storage helpers
# ---------------------------------------------------------------------------


def _get_cookie_path() -> Path:
    """Return the path to the cookie JSON file for the active profile.

    Returns:
        Path to the cookie file in the active profile's data directory.
    """
    active_profile_dir = overleaf_fs.core.profiles.get_active_profile_data_dir()
    return active_profile_dir / COOKIE_FILENAME


def load_saved_cookie_header() -> Optional[str]:
    """Load a previously saved cookie header for the active profile.

    The cookie header is stored as a small JSON document under the
    profile's data directory. If the file does not exist, or cannot be
    parsed, this returns None.

    Returns:
        The raw cookie header string, or None if not available.
    """
    path = _get_cookie_path()
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("Failed to parse saved cookie file %s: %s", path, exc)
        return None

    header = data.get("cookie_header")
    if not header or not isinstance(header, str):
        return None

    return header


def save_cookie_header(cookie_header: str) -> None:
    """Persist the given cookie header for the active profile.

    The caller (typically GUI code) is responsible for asking the user
    whether they want to remember the cookie on this machine. This
    helper simply writes the header and a timestamp into a small JSON
    file under the active profile's data directory.

    Args:
        cookie_header: Raw Cookie header string copied from the browser.
    """
    path = _get_cookie_path()
    payload = {
        "cookie_header": cookie_header,
        "saved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    LOGGER.info("Saved Overleaf cookie header for profile '%s' to %s",
                get_active_profile_id(), path)


# ---------------------------------------------------------------------------
# Session construction
# ---------------------------------------------------------------------------


def build_session_from_cookie(cookie_header: str) -> requests.Session:
    """Build a requests.Session configured with a browser-derived cookie.

    Args:
        cookie_header: Raw Cookie header string copied from the browser,
            e.g. \"overleaf_session=...; another_cookie=...\".

    Returns:
        A configured requests.Session instance suitable for scraping
        the Overleaf dashboard.
    """
    session = requests.Session()
    # Minimal headers to look browser-like.
    session.headers.update(
        {
            "User-Agent": "overleaf-fs/0.1 (+https://www.overleaf.com)",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
        }
    )

    # Parse the cookie header into individual cookies. We set them for
    # the configured Overleaf host (e.g. "www.overleaf.com" or an
    # institution-hosted instance) so that requests to the dashboard
    # carry the same cookies as the browser.
    host = _get_overleaf_host()
    cookie_domain = f".{host.lstrip('.')}"
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        session.cookies.set(name.strip(), value.strip(), domain=cookie_domain)

    return session


# ---------------------------------------------------------------------------
# HTML scraping helpers
# ---------------------------------------------------------------------------


def _is_login_page(html: str) -> bool:
    """Heuristically detect whether the response is a login page.

    Because we are scraping with a browser-derived cookie, an expired or
    invalid cookie may still produce a 200 OK response but show a login
    form instead of the project dashboard. This helper uses a few simple
    heuristics to detect that situation so we can raise a clear error.

    Args:
        html: Raw HTML text of the response.

    Returns:
        True if the HTML looks like a login page, False otherwise.
    """
    markers = [
        "Log in to Overleaf",
        "Sign in to Overleaf",
        "log in to your Overleaf account",
        "overleaf-login",  # CSS/JS ids/classes sometimes used
    ]
    lowered = html.lower()
    return any(m.lower() in lowered for m in markers)


def parse_projects_from_html(html: str) -> List[OverleafProjectDTO]:
    """Parse the Overleaf dashboard HTML into project DTOs.

    This function is intentionally conservative: if it cannot find any
    projects, it raises a ValueError rather than silently returning an
    empty list, to avoid accidentally overwriting the cached projects info file with
    an empty project list due to a parsing failure.

    The current Overleaf dashboard embeds a JSON blob of project
    info in a ``<meta>`` tag with ``name="ol-prefetchedProjectsBlob"``
    and ``data-type="json"``. The ``content`` attribute of this tag
    contains an HTML-escaped JSON array of project objects. We decode
    and parse that blob rather than scraping visual table markup.

    Args:
        html: Raw HTML from the Overleaf dashboard.

    Returns:
        List of OverleafProjectDTO instances.

    Raises:
        ValueError: If the HTML appears to be a login page or if no
        project entries can be parsed.
    """
    if _is_login_page(html):
        raise ValueError(
            "Overleaf appears to have returned a login page instead of the "
            "project dashboard. Your cookie may be expired or invalid."
        )

    soup = BeautifulSoup(html, "html.parser")

    projects: List[OverleafProjectDTO] = []

    # The Overleaf dashboard embeds a JSON array of project info in a
    # <meta> tag with name="ol-prefetchedProjectsBlob" and data-type="json".
    # The content attribute holds an HTML-escaped JSON string such as:
    #
    #   [{"id": "...", "name": "...", "lastUpdated": "...", ...}, ...]
    #
    meta = soup.find(
        "meta",
        attrs={"name": "ol-prefetchedProjectsBlob"},
    )
    if meta is None or not meta.get("content"):
        raise ValueError(
            "Could not find the Overleaf prefetched projects blob in the HTML."
        )

    raw_content = meta.get("content", "")
    try:
        decoded = unescape(raw_content)
        data = json.loads(decoded)
        num_projects = data['totalSize']
        data = data['projects']
    except Exception as exc:
        raise ValueError(
            "Failed to decode Overleaf prefetched projects JSON blob."
        ) from exc

    if not isinstance(data, list):
        raise ValueError(
            "Unexpected format for Overleaf prefetched projects blob: "
            "expected a JSON list."
        )

    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            project_id = str(entry.get("id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if not project_id or not name:
                continue

            # Construct the project URL from the id and the configured
            # Overleaf base URL for this profile.
            base_url = _get_overleaf_base_url()
            url = f"{base_url}/project/{project_id}"

            owner = entry.get("owner") or {}
            owner_email = owner.get("email") or ""
            owner_first = owner.get("firstName") or ""
            owner_last = owner.get("lastName") or ""
            # Prefer email for disambiguation; fall back to name parts.
            owner_label = (
                str(owner_email)
                or str(owner_first)
                or str(owner_last)
                or ""
            ).strip()
            owner_name_parts = [str(part).strip() for part in (owner_first, owner_last) if str(part).strip()]
            owner_display_name: Optional[str]
            if owner_name_parts:
                owner_display_name = " ".join(owner_name_parts)
            else:
                owner_display_name = None

            last_iso = entry.get("lastUpdated")
            last_iso_str = str(last_iso) if last_iso is not None else None
            # For now, we reuse the ISO timestamp for the raw field as well.
            last_raw = last_iso_str

            archived = bool(entry.get("archived", False))

            # Optionally skip trashed projects; we keep archived for now so
            # that the local view can surface them if desired.
            if entry.get("trashed"):
                continue

            projects.append(
                OverleafProjectDTO(
                    id=project_id,
                    name=name,
                    url=url,
                    owner_label=owner_label,
                    owner_display_name=owner_display_name,
                    last_modified_iso=last_iso_str,
                    last_modified_raw=last_raw,
                    archived=archived,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning(
                "Failed to parse a project entry from prefetched JSON blob: %s",
                exc,
            )
            continue

    if not projects:
        raise ValueError(
            "Failed to parse any projects from the Overleaf prefetched "
            "projects blob. The page structure or embedded data may have "
            "changed."
        )

    return projects


def scrape_overleaf_projects(session: requests.Session) -> List[OverleafProjectDTO]:
    """Fetch and parse the Overleaf project dashboard for the current user.

    Args:
        session: Authenticated requests.Session built from a browser
            cookie or other authentication mechanism.

    Returns:
        List of OverleafProjectDTO instances.

    Raises:
        ValueError: If the response looks like a login page or does not
        contain any recognizable project entries.
    """
    base_url = _get_overleaf_base_url()
    url = f"{base_url}{DASHBOARD_PATH}"
    LOGGER.info("Fetching Overleaf dashboard from %s", url)
    resp = session.get(url, timeout=15)
    resp.raise_for_status()

    return parse_projects_from_html(resp.text)


# ---------------------------------------------------------------------------
# High-level refresh helpers
# ---------------------------------------------------------------------------


def _dto_to_projects_info_entry(dto: OverleafProjectDTO) -> dict:
    """Convert a DTO into the projects-info JSON entry format used by project_index.

    Args:
        dto: Project DTO to convert.

    Returns:
        Dictionary suitable for serializing into the projects-info JSON file
        (``overleaf_projects_info.json``).
    """
    return {
        "id": dto.id,
        "name": dto.name,
        "url": dto.url,
        "owner_label": dto.owner_label,
        "owner_display_name": dto.owner_display_name,
        "last_modified": dto.last_modified_iso,
        "last_modified_raw": dto.last_modified_raw,
        "archived": dto.archived,
    }


def write_projects_info(projects: Iterable[OverleafProjectDTO]) -> Path:
    """Write the given projects into the profile's projects-info JSON file.

    Args:
        projects: Iterable of OverleafProjectDTO instances.

    Returns:
        Path to the projects-info file that was written.
    """
    projects_info_path = get_projects_info_path()
    payload = [_dto_to_projects_info_entry(dto) for dto in projects]
    projects_info_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    LOGGER.info(
        "Wrote %d Overleaf projects to projects-info file %s",
        len(payload),
        projects_info_path,
    )
    return projects_info_path


def refresh_projects_with_cookie(
    cookie_header: str,
    remember_cookie: bool = False,
) -> List[OverleafProjectDTO]:
    """Refresh the cached Overleaf projects info using a browser-derived cookie header.

    This is the main entry point for GUI code when the user pastes a
    Cookie header string. It will:

    * Build an authenticated HTTP session from the cookie header.
    * Scrape the project dashboard.
    * Write the resulting project list to the cached projects-info JSON
      file (``overleaf_projects_info.json``) for the active profile.
    * Optionally persist the cookie header so that future refreshes can
      reuse it without requiring the user to paste it again.

    Args:
        cookie_header: Raw Cookie header string copied from the browser.
        remember_cookie: If True, the cookie header is saved to disk for
            the active profile. The caller is responsible for only
            enabling this when the user has consented.

    Returns:
        List of OverleafProjectDTO instances corresponding to the
        refreshed project list.

    Raises:
        ValueError: If scraping fails (e.g. due to an expired cookie or
        unexpected HTML structure).
        requests.RequestException: If the network request fails.
    """
    session = build_session_from_cookie(cookie_header)
    try:
        projects = scrape_overleaf_projects(session)
    except ValueError as exc:
        # Most parsing failures for the dashboard HTML in practice are
        # due to the cookie no longer being accepted by Overleaf,
        # which causes a login form to be returned instead of the
        # expected project blob. We surface this to the GUI as a
        # CookieRequiredError so it can prompt the user for a fresh
        # login or cookie.
        raise CookieRequiredError(
            "Overleaf did not return a usable project dashboard for the "
            "current cookie. The cookie may be expired or invalid."
        ) from exc

    write_projects_info(projects)

    if remember_cookie:
        save_cookie_header(cookie_header)

    return projects


def refresh_projects_with_saved_cookie() -> List[OverleafProjectDTO]:
    """Refresh the cached Overleaf projects info using a previously saved cookie.

    This helper is intended for use by a \"Refresh from Overleaf\" action
    that does not prompt the user for a cookie every time. If no saved
    cookie header is available, it raises a ValueError so the caller can
    fall back to a workflow that asks the user to paste a fresh cookie.

    Returns:
        List of OverleafProjectDTO instances corresponding to the
        refreshed project list.

    Raises:
        ValueError: If no saved cookie header is available for the
        active profile or if scraping fails.
        requests.RequestException: If the network request fails.
    """
    cookie_header = load_saved_cookie_header()
    if not cookie_header:
        raise CookieRequiredError(
            "No saved Overleaf cookie header is available for the active "
            "profile. Please provide a fresh cookie from your browser."
        )

    return refresh_projects_with_cookie(cookie_header, remember_cookie=True)


def sync_overleaf_projects_for_active_profile(
    cookie_header: Optional[str] = None,
    remember_cookie: bool = False,
) -> List[OverleafProjectDTO]:
    """Synchronize Overleaf projects for the active profile.

    This convenience helper is intended as the main entry point for
    GUI code. It encapsulates the two common refresh workflows:

    * If ``cookie_header`` is provided, it is used to build a new
      session and refresh the cached projects info. If ``remember_cookie`` is
      True, the header is also persisted for future use.
    * If ``cookie_header`` is None, the function attempts to use a
      previously saved cookie header via
      :func:`refresh_projects_with_saved_cookie`. If no saved cookie
      is available, :class:`CookieRequiredError` is raised so that the
      caller can prompt the user for a Cookie header.

    Args:
        cookie_header: Optional raw Cookie header string copied from
            the browser. If provided, it takes precedence over any
            saved cookie.
        remember_cookie: If True and ``cookie_header`` is provided,
            the header is saved for the active profile.

    Returns:
        List of OverleafProjectDTO instances corresponding to the
        refreshed project list.
    """
    if cookie_header:
        return refresh_projects_with_cookie(
            cookie_header=cookie_header,
            remember_cookie=remember_cookie,
        )

    # Fall back to using a previously saved cookie. This may raise
    # CookieRequiredError if no cookie has been saved yet.
    return refresh_projects_with_saved_cookie()


# ---------------------------------------------------------------------------
# Manual test harness CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover - manual test harness
    """Simple CLI entry point for testing the Overleaf scraper.

    This tool does **not** modify the profile's configured projects-info
    file or saved cookie. It is intended for manual testing only: it
    fetches the project list from Overleaf using a browser-derived
    Cookie header and writes the resulting JSON payload to a local file
    (by default ``overleaf_projects_test.json``) in the current working
    directory.

    Usage examples::

        # Prompt for Cookie header on stdin and write to the default file
        python -m overleaf_fs.core.overleaf_scraper

        # Read the Cookie header from a file and choose an explicit
        # output path
        python -m overleaf_fs.core.overleaf_scraper \
            --cookie-file=/path/to/cookie.txt \
            --output=/path/to/overleaf_projects_test.json
    """

    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=(
            "Test the Overleaf project scraper using a browser-derived "
            "Cookie header. The resulting project list is written to a "
            "JSON file for inspection."
        )
    )
    parser.add_argument(
        "--cookie-file",
        type=str,
        default=None,
        help=(
            "Optional path to a text file containing a single line "
            "Cookie header copied from your browser. If omitted, the "
            "header is read from standard input."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default="overleaf_projects_test.json",
        help=(
            "Path to the output JSON file. Defaults to "
            "'overleaf_projects_test.json' in the current directory."
        ),
    )

    args = parser.parse_args()

    if args.cookie_file:
        cookie_path = Path(args.cookie_file)
        if not cookie_path.exists():
            print(f"Cookie file not found: {cookie_path}", file=sys.stderr)
            sys.exit(1)
        cookie_header = cookie_path.read_text(encoding="utf-8").strip()
    else:
        print(
            "Paste the Cookie header from an authenticated Overleaf "
            "browser session and press Enter:",
            file=sys.stderr,
        )
        cookie_header = sys.stdin.readline().strip()

    if not cookie_header:
        print("No Cookie header provided; aborting.", file=sys.stderr)
        sys.exit(1)

    try:
        session = build_session_from_cookie(cookie_header)
        projects = scrape_overleaf_projects(session)
    except Exception as exc:  # pragma: no cover - manual testing
        print(f"Error while scraping Overleaf projects: {exc}", file=sys.stderr)
        sys.exit(1)

    payload = [_dto_to_projects_info_entry(dto) for dto in projects]

    output_path = Path(args.output)
    try:
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover - manual testing
        print(f"Failed to write output JSON to {output_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Wrote {len(payload)} projects to {output_path}",
        file=sys.stderr,
    )