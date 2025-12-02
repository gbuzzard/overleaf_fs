"""
Manual test for external directory‑structure change detection.

Run with:
    python test_external_change.py

This launches the GUI using the real active profile’s data directory.
Before starting, the test makes a backup of the current profile's
directory‑structure JSON file and restores it afterwards (even if
the user aborts). The test guides you through confirming that
external directory‑structure changes are detected when a folder or
project row is selected.

This is **not** an automated test. It is a developer sanity check.
"""

import shutil
from pathlib import Path

from overleaf_fs.core.profiles import get_active_profile_data_dir
from overleaf_fs.core.config import (
    DEFAULT_DIRECTORY_STRUCTURE_FILENAME,
    DEFAULT_PROJECTS_INFO_FILENAME,
)

from PySide6.QtWidgets import QApplication
from overleaf_fs.gui.main_window import MainWindow


def main():
    print("\n=== External Directory‑Structure Change Manual Test ===")
    print("This test uses a copy of an existing OverleafFS profile's directory‑structure and projects‑info files.")
    print("Instructions will appear in this console.\n")

    # Try to infer the active profile's data directory (the directory
    # that actually contains the directory‑structure and projects‑info
    # JSON files).
    try:
        default_dir = get_active_profile_data_dir()
    except RuntimeError:
        default_dir = None

    if default_dir is not None:
        print(f"Default profile data directory detected:\n  {default_dir}")
        src_str = input(
            "Press ENTER to copy the default profile’s data files for testing, "
            "or enter another profile data directory:\n> "
        ).strip()
        if not src_str:
            src_root = Path(default_dir)
        else:
            src_root = Path(src_str).expanduser()
    else:
        src_str = input(
            "Enter the path to an existing OverleafFS profile data directory\n"
            "(the directory containing the directory‑structure and projects‑info JSON files):\n> "
        ).strip()
        if not src_str:
            print("No source directory provided. Aborting manual test.")
            return
        src_root = Path(src_str).expanduser()
    directory_structure_src = src_root / DEFAULT_DIRECTORY_STRUCTURE_FILENAME
    projects_info_src = src_root / DEFAULT_PROJECTS_INFO_FILENAME

    if not directory_structure_src.exists() or not projects_info_src.exists():
        print("\nERROR: Could not find required files in the source directory:")
        print(
            f"  {directory_structure_src if directory_structure_src.exists() else 'local_directory_structure.json missing'}"
        )
        print(
            f"  {projects_info_src if projects_info_src.exists() else 'overleaf_projects_info.json missing'}"
        )
        print("Aborting manual test.\n")
        return

    directory_structure = src_root / DEFAULT_DIRECTORY_STRUCTURE_FILENAME
    projects_info_file = src_root / DEFAULT_PROJECTS_INFO_FILENAME

    backup_directory_structure = src_root / (
        DEFAULT_DIRECTORY_STRUCTURE_FILENAME + ".manual_test_backup"
    )
    shutil.copy2(directory_structure, backup_directory_structure)
    print(f"\nBackup created:\n  {backup_directory_structure}")

    print("\nStep 1: The GUI will launch now using your REAL profile data.")
    print("  A backup copy of the directory‑structure JSON file has been made and will be restored")
    print("  automatically when the test ends.")

    print("\nStep 2: While the GUI is running, modify the original directory‑structure file:")
    print(f"    {directory_structure}")
    print("  For example, add a new folder or change a folder assignment.")
    print("  Then click on a folder or project in the GUI.")
    print("  You SHOULD see the reload dialog.\n")
    print("NOTE:  Exit by closing the app window rather than killing the process so that files are cleaned up.")

    try:
        app = QApplication([])
        win = MainWindow()
        # Load the directory‑structure and projects‑info data from the profile
        # directory so that the folders and projects appear without requiring any network sync.
        win._on_reload_from_disk()
        win.show()

        app.exec()
    finally:
        if backup_directory_structure.exists():
            shutil.copy2(backup_directory_structure, directory_structure)
            backup_directory_structure.unlink()
            print("\nThe directory‑structure JSON file has been restored from backup.")


if __name__ == "__main__":
    main()