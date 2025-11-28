"""
Manual test for external metadata change detection.

Run with:
    python test_external_change.py

This launches a minimal version of the GUI using a temporary profile
directory. It guides the user through steps to confirm that external
metadata changes (e.g., edits to local_state.json) are detected when a
folder or project row is selected.

This is **not** an automated test. It is a developer sanity check.
"""

import tempfile
import shutil
from pathlib import Path

from overleaf_fs.core import get_profile_root_dir_optional

from PySide6.QtWidgets import QApplication
from overleaf_fs.gui.main_window import MainWindow


def main():
    print("\n=== External Metadata Change Manual Test ===")
    print("This test uses a copy of an existing OverleafFS profile's metadata.")
    print("Instructions will appear in this console.\n")

    default_root = get_profile_root_dir_optional()
    if default_root is not None:
        print(f"Default profile directory detected:\n  {default_root}")
        src_str = input(
            "Press ENTER to copy the default for testing, or enter another profile directory:\n> "
        ).strip()
        if not src_str:
            src_root = Path(default_root)
        else:
            src_root = Path(src_str).expanduser()
    else:
        src_str = input(
            "Enter the path to an existing OverleafFS profile directory\n"
            "(the directory that contains local_state.json and overleaf_projects.json):\n> "
        ).strip()
        if not src_str:
            print("No source directory provided. Aborting manual test.")
            return
        src_root = Path(src_str).expanduser()
    local_state_src = src_root / "local_state.json"
    projects_src = src_root / "overleaf_projects.json"

    if not local_state_src.exists() or not projects_src.exists():
        print("\nERROR: Could not find required files in the source directory:")
        print(f"  {local_state_src if local_state_src.exists() else 'local_state.json missing'}")
        print(f"  {projects_src if projects_src.exists() else 'overleaf_projects.json missing'}")
        print("Aborting manual test.\n")
        return

    tmp = Path(tempfile.mkdtemp(prefix="ofs_manual_test_"))
    print(f"\nTemporary test profile directory: {tmp}")

    local_state = tmp / "local_state.json"
    projects_file = tmp / "overleaf_projects.json"
    shutil.copy2(local_state_src, local_state)
    shutil.copy2(projects_src, projects_file)

    print("\nStep 1: The GUI will launch now using the copied metadata.")
    print("  - You should see your usual folders/projects (copied into the temp profile).")
    print("\nStep 2: While the GUI is running, modify local_state.json externally:")
    print(f"  {local_state}")
    print("  For example, add a new folder or change a folder assignment.")
    print("  Then, in the GUI, click on a folder or project again.")
    print("  You SHOULD see the reload dialog at that point.")
    print("\nClose the GUI window to end the test.\n")

    app = QApplication([])
    win = MainWindow()
    # Load the metadata from the temporary profile directory so that
    # the copied folders and projects appear without requiring any network sync.
    win._on_reload_from_disk()
    win.show()

    app.exec()

    print("Test finished. Temporary test profile directory was:")
    print(f"  {tmp}")


if __name__ == "__main__":
    main()