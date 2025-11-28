# overleaf_fs/__init__.py

from __future__ import annotations

from .gui.main_window import run as _run


def run_gui() -> None:
    """
    Start the Overleaf File System GUI.

    This is a convenience wrapper around :func:`overleaf_fs.gui.main_window.run`
    so that users can do:

        from overleaf_fs import run_gui
        run_gui()
    """
    return _run()


# Optional convenience alias so users can also do:
#   from overleaf_fs import run
#   run()
run = _run

__all__ = ["run_gui", "run"]
