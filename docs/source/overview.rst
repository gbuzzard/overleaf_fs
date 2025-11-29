===================
OverleafFS Overview
===================
Overleaf (https://www.overleaf.com/) is a powerful web‑based system for
maintaining and collaborating on multiple LaTeX projects. Overleaf
organizes projects using a flexible tag-based system. Some users,
however, prefer thinking in terms of a traditional folder-style
directory structure. OverleafFS provides this familiar tree-based view
without changing anything on Overleaf itself.

The Overleaf File System (OverleafFS) is a small desktop application that
helps you organize, browse, and interact with your Overleaf projects
using a familiar folder-based interface. It is designed for users who
work with many projects and want an easier way to keep them organized
locally while still syncing with Overleaf.

What OverleafFS provides
========================

OverleafFS offers a clean and simple way to:

* View all of your Overleaf projects in one place.
* Organize projects into folders such as ``Teaching/2025`` or ``CT``.
* Add notes, pin important projects, or temporarily hide others.
* Keep your local organization **separate from Overleaf’s actual project
  structure**, so you can organize things however you like without
  affecting collaborators.
* Refresh your local list of projects directly from Overleaf with a
  single click.

OverleafFS does **not** modify your actual Overleaf documents or file
contents. It only organizes and annotates the list of projects.

OverleafFS also does not upload your local folder organization to
Overleaf; your Overleaf project list remains unchanged.

How it works
============

OverleafFS stores two kinds of information on your machine:

1. **Projects info** (downloaded from Overleaf)
2. **Directory structure** (your local organization)

These are kept in two small JSON files inside a per‑profile data
directory. Whenever you refresh from Overleaf, OverleafFS updates the
projects info file. Your folder structure and annotations are stored
only locally and are never sent to Overleaf.


.. _connecting_to_overleaf:

Connecting to Overleaf
======================

The first time you start OverleafFS, the application will open a small
embedded web browser displaying the Overleaf login page:

1. Log in to Overleaf using your usual credentials.
2. You may be prompted to accept necessary or optional cookies.
3. After successfully logging in, click **Use this login**.
4. OverleafFS will ask whether to save the login cookie so that you do
   not need to log in again in future sessions.

Once a login cookie has been saved, OverleafFS uses it automatically.
On each restart, it loads your locally stored project information and
then refreshes your project list from Overleaf. You may also click
**Sync with Overleaf** at any time to fetch the latest changes.

Organizing your projects
========================

The left panel of the application shows a **folder tree** with:

* ``Home`` – your top-level view
* Any folders you have created
* ``Pinned`` – a special virtual folder
* ``Archived`` – projects Overleaf marks as archived

You can drag and drop projects into folders to reorganize them. You can
also create new folders, rename them, or delete them (as long as they’re
empty).

The right panel displays the list of projects in the selected folder.
You can sort by name, owner, last modified date, or status flags.

What does and does not sync
===========================

**Synced with Overleaf:**

* Project name
* Owner
* Last modified time
* Archived status
* Project URL
* Any changes made in Overleaf itself

**Stored locally only:**

* Folder assignments
* Pinned/hidden flags
* Notes
* Custom organization

Closing and reopening the application preserves all of your local
organization.

Profiles
========

OverleafFS supports multiple *profiles*. Each profile has its own
independent project organization and its own Overleaf project list. This
is useful if you want to:

* Separate work and teaching projects
* Use different Overleaf accounts
* Maintain an alternate organization for the same set of projects

Profile settings and data files are stored under
``~/.overleaf_fs/profiles/<profilename>/``.

External changes
================

If OverleafFS detects that the local metadata files have changed on
disk—for example, if you edited them manually or copied a profile
directory from another machine—it will offer to reload the data so
that the GUI stays consistent with the filesystem.

That’s all—OverleafFS is designed to stay out of your way and let you
organize your Overleaf projects quickly and intuitively.
