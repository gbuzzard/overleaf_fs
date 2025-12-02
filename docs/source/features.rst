Features
========

This page provides a brief overview of the major features of OverleafFS
and how they fit together. It is intended as a practical guide for users
who want to understand what the application can do and how to use it
effectively in day‑to‑day work.

Overview
--------

OverleafFS is a desktop application that synchronizes your Overleaf
projects with a local, browsable workspace. It provides:

* A unified tree view of your Overleaf folders and projects
* Local organization tools (folders, pinned projects)
* Automatic syncing of Overleaf project lists using your saved login
* Drag‑and‑drop project management
* Multi‑profile support (e.g., work and personal projects)
* Tools for keeping your local directory structure consistent across devices

Everything is stored as simple JSON files in your profile directory, so
your local organization can be shared between machines using Dropbox,
iCloud, or similar services.

Key Features
------------

The sections below summarize the main functionality exposed in the application.

Project Browser
~~~~~~~~~~~~~~~

The main window displays a tree of folders and projects similar to what
you see on Overleaf, but with additional local‑only organization tools.
A few highlights:

* **Expand / collapse folders** to navigate quickly.
* **Drag projects into folders** to reorganize them locally.
* **Pin projects** to keep them visible at the top of their folder.

* **Open on Overleaf**: double-clicking a project opens it in your default web browser.

Hovering over any project displays a multi‑line summary including
owner, folder, last modified time, and project URL.

Folder and Project Organization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

OverleafFS maintains a local *directory‑structure* file that contains:

* The directory structure you create through OverleafFS
* Each project’s folder assignment and Overleaf ID

These settings are stored locally (or in your cloud-synced profile root) and do not change your Overleaf folders.

You can:

* Create, rename, and delete local folders
* Move one or more projects into a new location
* Rename or clean up folder hierarchies

The Home folder corresponds to the top level on Overleaf.

Syncing With Overleaf
~~~~~~~~~~~~~~~~~~~~~

When you first log in using the embedded browser, OverleafFS captures
your Overleaf session cookie and uses it for automatic project refreshes.

Your login cookie is stored inside your active profile so each profile can maintain its own Overleaf session.

OverleafFS automatically synchronizes:

* **At login**
* **On every startup**
* **When you manually select *Sync with Overleaf***

The sync downloads the Overleaf *projects‑info* list but never modifies
your Overleaf account or project contents.

Profiles
~~~~~~~~

OverleafFS supports multiple named profiles. Each profile keeps its
directory‑structure JSON and projects‑info JSON in its own folder inside
your chosen profile root. This allows you to maintain:

* Separate personal vs. work project lists
* Different folder organizations
* Different login cookies

The profile root can live inside a cloud‑synced directory if you want
your organization to follow you across devices.

You can relocate the entire profile root using the Profile Manager’s *Move…* command.

Reloading Local Data
~~~~~~~~~~~~~~~~~~~~

If you edit the JSON files outside the application or switch machines,
you can select *Reload from Disk* to import the updated directory‑structure
information without touching the Overleaf sync.

This does not affect your Overleaf project list; only local structure is reloaded.

Search and Filtering
~~~~~~~~~~~~~~~~~~~~

The search box filters projects across the full list using:

* Project name
* Owner display name
* Owner email/login

Search is case-insensitive and matches substrings in any of these fields.

This makes it easy to find a project even with a vague query.


Feedback and contributions are welcome.