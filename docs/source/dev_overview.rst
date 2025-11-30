

==================
Developer Overview
==================

This document provides a high-level overview of the main modules in the
``overleaf_fs`` package and their responsibilities. It is intended as a
starting point for new contributors and as a quick reminder for future
maintenance and refactoring work.

Package layout
==============

The package is organized into a small core layer (data model, config,
persistence, scraping, and index construction) and a GUI layer
(Qt-based desktop application), plus minimal top-level entry points.

Top-level package (``overleaf_fs``)
-----------------------------------

* ``overleaf_fs/__init__.py``

  Lightweight package initializer. Currently provides package metadata
  (such as the version) and a convenient place for any future
  top-level imports or helpers that should be exposed as
  ``overleaf_fs.*``.

* ``overleaf_fs/app.py``

  GUI application bootstrap. Creates the Qt application object,
  constructs the main window, and starts the event loop. This is the
  canonical way to launch the desktop GUI from Python code.

* ``overleaf_fs/cli.py``

  Command-line entry points. Provides the functions that are wired up
  by packaging (e.g. console scripts) and can be extended with
  additional subcommands for scripting or headless utilities.

Core layer (``overleaf_fs.core``)
---------------------------------

The core layer contains configuration handling, persistent metadata
storage, the data model, and logic for connecting Overleaf's project
information with local directory-structure data.

* ``overleaf_fs/core/__init__.py``

  Marks the ``core`` subpackage and may re-export a small number of
  public helpers in the future. At the moment it is intentionally
  minimal to keep the public surface explicit.

* ``overleaf_fs/core/config.py``

  Central configuration and path logic.

  - Knows where the profile root directory lives on disk.
  - Defines the canonical filenames for the per-profile JSON files:

    * projects-info JSON file: ``overleaf_projects_info.json``
    * directory-structure JSON file: ``local_directory_structure.json``
    * profile-config JSON file: ``profile_config.json``

  - Provides helpers such as ``get_active_profile_data_dir()``,
    ``get_projects_info_path()``, and ``get_directory_structure_path()``
    so that other modules never hard-code paths or filenames.
  - Contains ``FILE_FORMAT_VERSION`` and related constants that control
    the on-disk layout.

* ``overleaf_fs/core/metadata_store.py``

  Persistence for *local* directory-structure data.

  - Defines ``LocalState``, which holds:

    * ``folders``: explicit list of known folder paths (e.g. ``"CT"``,
      ``"Teaching/2025"``), used to persist empty folders.
    * ``projects``: mapping from project id to ``ProjectLocal``
      (folder, notes, pinned/hidden flags).

  - Implements ``load_directory_structure()`` and
    ``save_directory_structure()`` which read/write the
    directory-structure JSON file located at
    ``get_directory_structure_path()``.
  - Does **not** know about the Overleaf-side projects-info JSON; it
    is strictly responsible for local-only metadata.

* ``overleaf_fs/core/models.py``

  Core data model definitions.

  - ``ProjectRemote``: Overleaf-side fields for a project, as stored in
    the projects-info JSON file.
  - ``ProjectLocal``: local-only directory-structure fields for a
    project, as stored in the directory-structure JSON file.
  - ``ProjectRecord``: in-memory combination of ``remote`` and
    ``local`` data for a single project id.
  - ``ProjectsIndex``: type alias for the mapping
    ``Dict[str, ProjectRecord]`` that represents all known projects.

* ``overleaf_fs/core/overleaf_scraper.py``

  Scraping and synchronization with the Overleaf project dashboard.

  - Builds an authenticated HTTP session from a browser Cookie header.
  - Scrapes the Overleaf projects dashboard and converts the results to
    ``OverleafProjectDTO`` objects.
  - Writes the projects-info JSON file at
    ``get_projects_info_path()``.
  - Exposes an entry point used by the GUI to refresh the cached
    projects-info data for the active profile.

* ``overleaf_fs/core/project_index.py``

  Construction of the in-memory projects index.

  - Loads the projects-info JSON (remote metadata) and the
    directory-structure JSON (local metadata).
  - Merges these two sources into a ``ProjectsIndex`` mapping from
    project id to ``ProjectRecord``.
  - The merge is keyed by Overleaf project id:

    * ``ProjectRecord.remote`` is overwritten on each refresh from
      Overleaf.
    * ``ProjectRecord.local`` is preserved across refreshes and reflects
      the user’s local organization and annotations.

GUI layer (``overleaf_fs.gui``)
-------------------------------

The GUI layer provides the Qt-based desktop application. It is split
into a main window, a login dialog, and supporting widgets plus their
models.

* ``overleaf_fs/gui/__init__.py``

  Marks the ``gui`` subpackage. Kept minimal so that importing the GUI
  layer is explicit and does not accidentally pull in Qt when used as a
  library.

* ``overleaf_fs/gui/main_window.py``

  The main application window and high-level GUI controller.

  - Owns the project tree (folders) and project table widgets.
  - Connects user actions (selection, drag-and-drop, sync buttons) to
    core-layer operations such as ``load_projects_index()``,
    ``load_directory_structure()``, and Overleaf refresh calls.
  - Implements external-change detection for the metadata JSON files and
    offers to reload when they change on disk.

* ``overleaf_fs/gui/overleaf_login.py``

  Login / cookie-entry dialog.

  - Provides a small dialog where the user can paste a browser Cookie
    header used to authenticate with Overleaf.
  - Hands the Cookie header off to ``overleaf_scraper`` to refresh the
    projects-info JSON.
  - Can be extended in the future to support richer authentication
    flows.

* ``overleaf_fs/gui/project_table.py``

  Project table widget and related utilities.

  - Contains the Qt widget that displays the list of projects in tabular
    form.
  - Handles interactions such as row selection and double-click
    behavior, delegating data access to the table model.

* ``overleaf_fs/gui/project_table_model.py``

  Qt table model for projects.

  - Implements ``QAbstractTableModel`` (or a subclass) backed by the
    ``ProjectsIndex``.
  - Provides the data needed by the project table widget: project name,
    owner, folder, last modified time, pinned/hidden flags, etc.
  - Encapsulates sorting and column configuration logic.

* ``overleaf_fs/gui/project_tree.py``

  Project folder tree widget.

  - Represents the folder hierarchy (Home, subfolders, pinned/archived
    views) as a Qt tree widget.
  - Supports folder selection, expansion/collapse, and drag-and-drop of
    projects between folders.
  - Works in concert with the main window to keep the tree in sync with
    the current directory-structure metadata.

Notes for future maintenance
============================

- Keep file and directory names centralized in ``config.py`` so other
  modules do not hard-code paths or filenames.
- Treat ``metadata_store`` as the single source of truth for the
  directory-structure JSON file, and ``project_index`` as the place
  where remote (Overleaf) and local (directory-structure) metadata are
  merged.
- When evolving the on-disk format, update ``FILE_FORMAT_VERSION`` and
  document any migration steps here so that future refactors remain
  understandable.