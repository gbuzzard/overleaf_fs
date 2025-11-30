.. _InstallationDocs:

==================
Installation Guide
==================

Requirements
------------

You will need a recent version of Python (3.10 or later recommended)
and either **pip** or **conda** installed. A working installation of
``pip`` is sufficient for most users. If you plan to install from
source, ``git`` is also required.

OverleafFS runs on macOS, Linux, and Windows.

Installation
------------

The ``OverleafFS`` package is available through PyPI or from source on GitHub.

**Install from PyPI**

To install from PyPI:

    pip install --upgrade overleaf_fs

This command installs the core application and its Python dependencies.
If you prefer to isolate the installation, consider first creating a
virtual environment using ``python -m venv`` or ``conda create``.

**Installing from source**

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
