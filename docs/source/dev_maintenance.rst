===================
Package Maintenance
===================

This page is intended for developers working on OverleafFS. It
summarizes how the package is built and published, and how the
GitHub–PyPI link is configured.

Overview
========

OverleafFS is packaged as a standard Python project using
``pyproject.toml`` and setuptools. The project can be installed locally
with ``pip``, and new releases are published to PyPI using a GitHub
Actions workflow together with PyPI's *Trusted Publisher* integration.

In normal day-to-day work you will:

* Bump the version in ``pyproject.toml``
* Commit and push changes to the main repository
* Tag a release (e.g. ``v0.1.1``)
* Create a GitHub Release for that tag

The GitHub Actions workflow then builds and uploads the package to PyPI
without requiring local credentials.

Local Installation and Testing
==============================

Before publishing a new release, it is a good idea to test a local
install from the current source tree.

From the repository root:

.. code-block:: bash

    # optional: create/activate a virtual environment
    python -m pip install --upgrade pip

    # install the package in editable mode
    pip install -e .

    # run the GUI
    overleaf-fs

You can also build the distribution artifacts locally:

.. code-block:: bash

    python -m pip install --upgrade build
    python -m build

This will create ``dist/`` containing a source distribution (``.tar.gz``)
and a wheel (``.whl``). These are the same types of artifacts that will
be uploaded to PyPI by the GitHub workflow.

Release Workflow
================

The release process is driven by Git tags and GitHub Releases. The
high-level steps for making a new release are:

1. **Choose a new version number.**
   Use semantic versioning, e.g. ``0.1.1``.

2. **Update ``pyproject.toml``.**

   In the ``[project]`` section, set::

       name = "overleaf_fs"
       version = "X.Y.Z"

   where ``X.Y.Z`` is the chosen version.

3. **Commit and push.**

   .. code-block:: bash

       git add pyproject.toml
       git commit -m "Bump version to X.Y.Z"
       git push

4. **Create a tag and push it.**

   Tags are typically of the form ``vX.Y.Z``:

   .. code-block:: bash

       git tag vX.Y.Z
       git push origin vX.Y.Z

5. **Create a GitHub Release.**

   * Go to the *Releases* page in the GitHub repository
   * Click "Draft a new release"
   * Select the tag (e.g. ``vX.Y.Z``)
   * Give the release a title and (optionally) release notes
   * Click **Publish release**

This final step triggers the GitHub Actions workflow that builds and
publishes the new version to PyPI.

GitHub–PyPI Integration (Trusted Publisher)
==========================================

Publishing is handled by a GitHub Actions workflow configured as a
*Trusted Publisher* on PyPI (see https://pypi.org/manage/account/publishing/).
This avoids storing long-lived API tokens in secret and instead
uses short-lived credentials obtained via OpenID Connect (OIDC).

Workflow location
-----------------

The publishing workflow lives at::

    .github/workflows/publish.yml

The workflow:

* Runs when a GitHub Release is published (and can also be run manually)
* Checks out the repository
* Installs Python and the ``build`` tool
* Builds the wheel and source distribution via ``python -m build``
* Uses ``pypa/gh-action-pypi-publish`` to upload to PyPI

The key portion of the workflow looks like:

.. code-block:: yaml

    on:
      release:
        types: [published]
      workflow_dispatch:

    jobs:
      build-and-publish:
        runs-on: ubuntu-latest
        permissions:
          id-token: write
          contents: read

        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with:
              python-version: "3.12"

          - name: Install build tooling
            run: |
              python -m pip install --upgrade pip
              pip install build

          - name: Build distributions
            run: python -m build

          - name: Publish to PyPI
            uses: pypa/gh-action-pypi-publish@release/v1

PyPI Trusted Publisher setup
----------------------------

On the PyPI side, the project ``overleaf_fs`` is configured to trust this
GitHub workflow as a publisher:

* Log in to PyPI and go to **Account → Publishing**
* Add a new pending publisher with:

  * Project name: ``overleaf_fs``
  * Provider: GitHub
  * Owner: the GitHub user or organization that owns the repository
  * Repository: the repository name
  * Workflow filename: ``.github/workflows/publish.yml``
  * Environment: left blank (no explicit GitHub environment is used)

* After the workflow runs successfully for the first time from the
  configured repository and workflow file, PyPI recognizes it as a
  Trusted Publisher and associates it with the project.

Once this is set up, future releases do not require API tokens or manual
``twine`` commands.

Troubleshooting
===============

A few common issues and checks:

* **Version already exists on PyPI**

  If the workflow fails with an error about an existing version, make
  sure you bumped ``version`` in ``pyproject.toml`` and created a new tag
  (e.g. ``v0.1.2``).

* **Workflow not triggered**

  Ensure that a GitHub Release was actually published for the tag. The
  workflow triggers on ``release: [published]``, not just on pushing a
  tag.

* **Trusted Publisher mismatch**

  If PyPI rejects the upload with an authorization error, verify that:

  * The project name on PyPI is ``overleaf_fs``
  * The repository owner, name, and workflow filename on the PyPI
    Publishing page match the actual GitHub repository and
    ``.github/workflows/publish.yml``
  * The workflow has ``permissions: id-token: write``

* **Local vs installed behavior**

  If you see different behavior between a locally installed editable
  version (``pip install -e .``) and the PyPI release, confirm that all
  relevant changes have been committed, tagged, and that you are
  installing the expected version from PyPI when testing.

This page should be updated as the release process evolves (for example,
if additional checks, tests, or deployment steps are added).
