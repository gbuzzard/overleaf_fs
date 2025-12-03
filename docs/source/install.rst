.. _InstallationDocs:

==================
Installation Guide
==================

OverleafFS runs on macOS, Linux, and Windows.

Installation
------------

The ``OverleafFS`` package is available through PyPI, as standalone
binaries for Windows and macOS, or from source on GitHub.

===========================
Installing from Binaries
===========================

Pre-built application bundles are available on the project's GitHub
Releases page:

    https://github.com/gbuzzard/overleaf_fs/releases

These builds require **no Python installation** and are the easiest way
to run OverleafFS on macOS and Windows.

Windows
-------

Download the file named something like::

    OverleafFS-windows-vX.Y.Z.zip

Extract the archive and run::

    OverleafFS.exe

You may create a shortcut to the executable or place it on the desktop.

macOS
-----

Download the macOS application bundle::

    OverleafFS-macOS-vX.Y.Z.zip

Then:

1. Unzip the file to obtain ``OverleafFS.app``.
2. Attempt to open it once by double-clicking.
   macOS Gatekeeper will block the application and display a warning.
3. Click **Cancel**.
4. Open **System Settings → Privacy & Security**.
5. Scroll down near the bottom of the right-hand panel.
   You should see a message indicating that ``OverleafFS.app`` was blocked.
6. Click **Allow Anyway** or **Open Anyway**.
7. Return to Finder and open ``OverleafFS.app`` again.

After this first authorization, the application will open normally.

Note: The macOS application is unsigned (i.e., not notarized), so
Gatekeeper approval is required on first launch.

===========================
Installing from PyPI
===========================

Requirements
------------

You will need a recent version of Python (3.10 or later recommended)
and either **pip** or **conda** installed. A working installation of
``pip`` is sufficient for most users. If you plan to install from
source, ``git`` is also required.

To install from PyPI:

    pip install --upgrade overleaf_fs

This command installs the core application and its Python dependencies.
If you prefer to isolate the installation, consider first creating a
virtual environment using ``python -m venv`` or ``conda create``.

===========================
Installing from source
===========================

1. Download the source code

In order to download the python code, move to a directory of your choice and run the following two commands::

    git clone https://github.com/gbuzzard/overleaf_fs.git
    cd overleaf_fs

2. Install the environment and the OverleafFS package

Clean install using overleaf_fs/dev_scripts - We provide bash scripts that will do a clean install of ``OverleafFS`` in a new conda environment using the following commands::

    cd dev_scripts
    source clean_install_all.sh
    cd ../overleaf_fs
    conda activate overleaf_fs
    python -m app.py

Getting Started
---------------

See :doc:`quick_start` for a guide to launching OverleafFS.