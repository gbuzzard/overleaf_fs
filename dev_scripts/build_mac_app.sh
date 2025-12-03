#!/usr/bin/env bash
#
# Build the OverleafFS macOS .app bundle with PyInstaller and create a ZIP
# suitable for upload to a GitHub Release.
#
# Usage:
#   ./dev_scripts/build_mac_app.sh [VERSION]
#
# If VERSION is not provided, the script will try to read it from
# pyproject.toml under [project].version.

main() {
  local ORIG_DIR
  ORIG_DIR="$(pwd)"

  # --- Locate project root ----------------------------------------------------
  local SCRIPT_DIR PROJECT_ROOT
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
  cd "${PROJECT_ROOT}" || {
    echo "Error: Failed to cd into project root: ${PROJECT_ROOT}" >&2
    return 1
  }

  echo "Project root: ${PROJECT_ROOT}"

  # --- Basic sanity checks ----------------------------------------------------
  if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: This script is intended to be run on macOS (Darwin)." >&2
    return 1
  fi

  if ! command -v python &>/dev/null; then
    echo "Error: 'python' not found on PATH. Activate your overleaf_fs environment first." >&2
    return 1
  fi

  # --- Ensure PyInstaller is available ---------------------------------------
  if ! python -m PyInstaller --version &>/dev/null; then
    echo "Installing PyInstaller to build the macOS app bundle...."
    if ! python -m pip install pyinstaller; then
      echo
      echo "❌ Failed to install PyInstaller."
      echo "   You can also install it manually with:"
      echo "     python -m pip install pyinstaller"
      echo
      return 1
    fi
    echo
    echo "✅ PyInstaller installed successfully."
  fi

  # --- Check icon and package paths ------------------------------------------
  local ICON_PATH="assets/OverleafFS.icns"
  if [[ ! -f "${ICON_PATH}" ]]; then
    echo "Error: macOS icon not found at ${ICON_PATH}." >&2
    echo "Make sure OverleafFS.icns exists there, or update ICON_PATH in this script." >&2
    return 1
  fi

  if [[ ! -d "overleaf_fs" ]]; then
    echo "Error: 'overleaf_fs' package directory not found in project root (${PROJECT_ROOT})." >&2
    return 1
  fi

  # --- Determine version ------------------------------------------------------
  local VERSION
  VERSION="${1:-}"

  if [[ -z "${VERSION}" ]]; then
    echo "No VERSION argument provided; attempting to read from pyproject.toml..."
    VERSION="$(
python - << 'PY'
import pathlib, sys

pyproject = pathlib.Path("pyproject.toml")
if not pyproject.is_file():
    # No pyproject: signal failure
    sys.exit(1)

text = pyproject.read_text(encoding="utf8")

# Try a simple line-based parse first: version = "0.1.2"
version = None
for line in text.splitlines():
    line = line.strip()
    if line.startswith("version") and "=" in line:
        _, rhs = line.split("=", 1)
        rhs = rhs.strip().strip('"').strip("'")
        if rhs:
            version = rhs
        break

if version:
    print(version)
    sys.exit(0)

sys.exit(1)
PY
    )" || true
  fi

  if [[ -z "${VERSION}" ]]; then
    echo "Warning: Could not determine version automatically."
    VERSION="unknown"
  fi

  echo "Building OverleafFS macOS app for version: ${VERSION}"

  # --- Clean previous mac build artifacts ------------------------------------
  echo "Cleaning previous build artifacts (if any)..."
  rm -rf build dist/OverleafFS dist/OverleafFS.app OverleafFS.spec

  # --- Run PyInstaller --------------------------------------------------------
  echo "Running PyInstaller..."
  python -m PyInstaller \
    --name OverleafFS \
    --windowed \
    --icon "${ICON_PATH}" \
    -p . \
    overleaf_fs/gui/main_window.py

  echo
  echo "PyInstaller build complete."

  local APP_PATH="dist/OverleafFS.app"
  if [[ ! -d "${APP_PATH}" ]]; then
    echo "Error: Expected app bundle not found at ${APP_PATH}." >&2
    return 1
  fi

  du -sh "${APP_PATH}" || true

  # --- Create ZIP for upload (using ditto, like Finder's Compress) ----------
  local ZIP_NAME="OverleafFS-macOS-v${VERSION}.zip"
  local ZIP_PATH="dist/${ZIP_NAME}"

  echo
  echo "Creating ZIP archive with ditto (this matches Finder's Compress):"
  echo "  ${ZIP_PATH}"
  (
    cd dist || exit 1
    rm -f "${ZIP_NAME}"
    # Equivalent to Finder's "Compress": preserves resource forks and hard links
    /usr/bin/ditto -c -k --sequesterRsrc --keepParent "OverleafFS.app" "${ZIP_NAME}"
  )

  du -sh "${ZIP_PATH}" || true

  # --- Final instructions -----------------------------------------------------
  cat <<EOF

Done!

Built files:
  - App bundle:   ${APP_PATH}
  - Release ZIP:  ${ZIP_PATH}

Suggested next steps:

1. Test the app locally:
   - Open Finder and navigate to:
       ${PROJECT_ROOT}/dist
   - Double-click "OverleafFS.app".
   - On first run you may need to:
       - Right-click "OverleafFS.app" -> Open -> confirm
     to work around Gatekeeper for unsigned apps.

2. When you're satisfied with the build, upload the ZIP to GitHub:
   - Go to your repository on GitHub.
   - Open the "Releases" tab.
   - Either create a new release for the corresponding tag (e.g. v${VERSION})
     or edit the existing one.
   - Drag and drop:
       ${ZIP_PATH}
     into the "Assets" section of the release.
   - Save/publish the release.

Users on macOS can then:
   - Download OverleafFS-macOS-v${VERSION}.zip from the release.
   - Unzip it to get "OverleafFS.app".
   - Drag "OverleafFS.app" into /Applications (optional but recommended).

EOF
  # Return to the original directory before exiting normally
  cd "${ORIG_DIR}" || true
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  # Executed directly
  ORIGINAL_DIR_OUTER="$(pwd)"
  main "$@"
  status=$?

  # Restore original dir on normal direct execution
  cd "${ORIGINAL_DIR_OUTER}" || true

  echo
  read -r -p "Press ENTER to close this window..." _
  exit "${status}"
else
  # Sourced into an existing shell (e.g., "source dev_scripts/build_mac_app.sh")
  main "$@"
fi