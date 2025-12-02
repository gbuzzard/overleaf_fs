"""Embedded Overleaf login dialog using Qt WebEngine.

This module provides :class:`OverleafLoginDialog`, a small dialog that
hosts an embedded browser pointed at the configured Overleaf base URL
for the active profile. When Qt WebEngine is available, the user can
log in to Overleaf inside the dialog and the application will read the
session cookies from the WebEngine cookie store to construct a
``Cookie`` header string.

If Qt WebEngine is not available (for example, because the
``PySide6-WebEngine`` wheels are not installed), the dialog falls back
to a stub implementation that simply reports that embedded login is not
supported. In that case the GUI should fall back to the manual
"paste Cookie header" workflow.
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QDialogButtonBox,
    QMessageBox,
    QSizePolicy,
    QLineEdit,
    QHBoxLayout,
    QPushButton,
)

from overleaf_fs.core.profiles import get_overleaf_base_url, set_overleaf_base_url

# Try to import Qt WebEngine. If this fails, we provide a stub dialog
# below that informs the user that embedded login is unavailable.
try:  # pragma: no cover - import guarded by runtime environment
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWebEngineCore import QWebEnginePage

    WEBENGINE_AVAILABLE = True
except ImportError:  # pragma: no cover - environment without WebEngine
    QWebEngineView = None  # type: ignore[assignment]
    QWebEngineProfile = None  # type: ignore[assignment]
    WEBENGINE_AVAILABLE = False


if WEBENGINE_AVAILABLE:

    class OverleafLoginDialog(QDialog):
        """Dialog that lets the user log in to Overleaf via WebEngine.

        When accepted, :meth:`exec_login` returns a ``Cookie`` header
        string assembled from the cookies set by the Overleaf site in
        the embedded browser. The caller can then pass this header to
        :func:`sync_overleaf_projects_for_active_profile` and optionally
        persist it in the profile data directory under the filename
        given in config.COOKIE_FILENAME: overleaf_cookie.json.
        """

        def __init__(self, parent=None) -> None:
            super().__init__(parent)

            self._cookie_header: Optional[str] = None
            self._cookies: Dict[str, str] = {}

            base_url = get_overleaf_base_url().strip().rstrip("/")
            if not base_url:
                base_url = "https://www.overleaf.com"
            self._login_url = f"{base_url}/project"
            self._target_host = QUrl(self._login_url).host()

            layout = QVBoxLayout(self)

            # URL override controls
            url_row = QHBoxLayout()
            info_label1 = QLabel(
                "Log in to Overleaf in the embedded browser below. "
                "If needed, enter a nonstandard Overleaf URL below."
            )
            info_label1.setWordWrap(True)
            # Do not allow the label to expand vertically; it should
            # take only the space it needs so that the browser view
            # can occupy most of the dialog height.
            info_policy = info_label1.sizePolicy()
            info_policy.setVerticalPolicy(QSizePolicy.Fixed)
            info_label1.setSizePolicy(info_policy)
            layout.addWidget(info_label1)

            url_label = QLabel("Overleaf login URL:")
            self._base_url_edit = QLineEdit(self._login_url, self)
            self._base_url_edit.setPlaceholderText("https://www.overleaf.com/project")
            self._base_url_edit.editingFinished.connect(self._on_base_url_changed)
            self._base_url_edit.returnPressed.connect(self._on_base_url_changed)
            url_row.addWidget(url_label)
            url_row.addWidget(self._base_url_edit)
            self._refresh_button = QPushButton("Refresh", self)
            self._refresh_button.setToolTip(
                "Reload the login page using the URL above"
            )
            self._refresh_button.clicked.connect(self._reload_login_page)
            url_row.addWidget(self._refresh_button)
            layout.addLayout(url_row)

            # Display the URL of the currently displayed page so that
            # users can see where any redirects (for example, via SSO)
            # have taken them.
            current_row = QHBoxLayout()
            current_label = QLabel("Current page:")
            self._current_url_edit = QLineEdit(self._login_url, self)
            self._current_url_edit.setReadOnly(True)
            self._current_url_edit.setCursorPosition(0)
            current_row.addWidget(current_label)
            current_row.addWidget(self._current_url_edit)
            layout.addLayout(current_row)

            info_label = QLabel(
                "Once you are logged in and can see your projects, "
                "click 'Use this login' to continue.\n\n"
                ">>>>> IMPORTANT: Accept essential cookies or all cookies"
                " to minimize logins on future sessions!! <<<<<"
            )
            info_label.setWordWrap(True)
            # Do not allow the label to expand vertically; it should
            # take only the space it needs so that the browser view
            # can occupy most of the dialog height.
            info_policy = info_label.sizePolicy()
            info_policy.setVerticalPolicy(QSizePolicy.Fixed)
            info_label.setSizePolicy(info_policy)
            layout.addWidget(info_label)

            # Dedicated WebEngine profile so that cookies are scoped to
            # this dialog rather than any global profile.
            profile = QWebEngineProfile(self)
            cookie_store = profile.cookieStore()
            cookie_store.cookieAdded.connect(self._on_cookie_added)  # type: ignore[arg-type]

            page = QWebEnginePage(profile, self)
            self._view = QWebEngineView(self)
            self._view.setPage(page)
            self._view.load(QUrl(self._login_url))
            layout.addWidget(self._view)
            self._view.urlChanged.connect(self._on_view_url_changed)

            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                parent=self,
            )
            buttons.accepted.connect(self._on_use_login_clicked)
            buttons.rejected.connect(self.reject)
            # Rename the OK button to make its purpose clearer.
            ok_button = buttons.button(QDialogButtonBox.Ok)
            if ok_button is not None:
                ok_button.setText("Use this login - see note about cookies above")
                # Avoid treating Enter in the URL field as "accept" for the dialog.
                ok_button.setAutoDefault(False)
                ok_button.setDefault(False)
            layout.addWidget(buttons)

            # Give most of the vertical space to the embedded browser
            # view, with minimal height reserved for the labels and
            # button rows. The exact indices are less important than
            # ensuring the view expands.
            layout.setStretch(0, 0)
            layout.setStretch(1, 0)
            layout.setStretch(2, 0)
            layout.setStretch(3, 1)  # QWebEngineView row
            layout.setStretch(4, 0)

            self.setWindowTitle("Log in to Overleaf")
            # Start with a reasonably large default size so that the
            # embedded browser is comfortably usable, even before the
            # user resizes the window.
            self.resize(1000, 700)

        def keyPressEvent(self, event) -> None:
            """Handle Return in the URL field without closing the dialog.

            When the URL line-edit has focus, pressing Enter/Return should
            update and reload the login URL rather than accepting the
            dialog. For other keys, fall back to the default behavior.
            """
            if (
                event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and self._base_url_edit is not None
                and self._base_url_edit.hasFocus()
            ):
                # Commit the URL change and refresh the page, but do not
                # close the dialog.
                self._on_base_url_changed()
                event.accept()
                return

            super().keyPressEvent(event)

        def _on_view_url_changed(self, url: QUrl) -> None:
            """Update the read-only field showing the current page URL."""
            try:
                text = url.toString()
            except Exception:
                text = ""
            if hasattr(self, "_current_url_edit") and self._current_url_edit is not None:
                self._current_url_edit.setText(text)
                self._current_url_edit.setCursorPosition(0)

        # ------------------------------------------------------------------
        # Public API
        # ------------------------------------------------------------------
        def exec_login(self) -> Optional[str]:
            """Run the dialog and return a Cookie header if login succeeds.

            Returns:
                Optional[str]: A Cookie header string constructed from
                the cookies set by Overleaf in the embedded browser, or
                ``None`` if the user cancels the dialog.
            """
            self._cookie_header = None
            result = self.exec()
            if result == QDialog.Accepted:
                return self._cookie_header
            return None

        def _on_base_url_changed(self) -> None:
            """Reload the login page when the user edits the base URL."""
            self._reload_login_page()

        def _reload_login_page(self) -> None:
            """Reload the login page using the URL in the edit field.

            This always reads the current contents of the URL line-edit,
            so that the Refresh button works even if the user has not
            pressed Enter yet. The text is treated as the full login
            URL (for example, ``https://latex.ornl.gov/project``), not
            just a base URL.
            """
            if self._base_url_edit is not None:
                new_url = self._base_url_edit.text().strip()
                if new_url:
                    self._login_url = new_url
                    self._target_host = QUrl(self._login_url).host()
            self._view.load(QUrl(self._login_url))

        # ------------------------------------------------------------------
        # Internal helpers
        # ------------------------------------------------------------------
        def _on_cookie_added(self, cookie) -> None:  # type: ignore[override]
            """Handle cookie-added events from the WebEngine cookie store.

            We watch for cookies whose domain matches the configured
            Overleaf host and accumulate them in ``self._cookies``.
            When the user clicks "Use this login", all collected
            cookies are assembled into a Cookie header string and
            returned to the caller.
            """
            try:
                name_bytes = cookie.name()
                value_bytes = cookie.value()
                domain_bytes = cookie.domain()
            except Exception:
                return

            try:
                name = _decode_cookie_field(name_bytes)
                value = _decode_cookie_field(value_bytes)
                domain = _decode_cookie_field(domain_bytes)
            except Exception:
                return

            # Filter by host/domain
            if domain:
                host = self._target_host or ""
                dom = domain.lstrip(".")
                if not (host == dom or host.endswith("." + dom)):
                    return

            if not name:
                return

            # Store decoded strings, not raw bytes.
            self._cookies[name] = value

        def _on_use_login_clicked(self) -> None:
            """Assemble a Cookie header from collected cookies and accept.

            This is invoked when the user clicks the "Use this login"
            button. If no cookies have been captured yet, a warning is
            shown and the dialog remains open.
            """
            if not self._cookies:
                QMessageBox.warning(
                    self,
                    "No cookies captured",
                    "No Overleaf cookies have been captured yet.\n\n"
                    "Please make sure you have logged in to Overleaf in "
                    "the embedded browser before continuing.",
                )
                return

            pairs = []
            for raw_name, raw_value in self._cookies.items():
                name = _decode_cookie_field(raw_name).strip()
                value = _decode_cookie_field(raw_value)
                if not name:
                    continue
                pairs.append(f"{name}={value}")

            if not pairs:
                QMessageBox.warning(
                    self,
                    "No valid cookies",
                    "Overleaf cookies could not be converted to a valid Cookie header.",
                )
                return

            header = "; ".join(pairs)

            # Derive the effective base URL (scheme + host) from the
            # current login URL and store it in the active profile so
            # that subsequent scraping uses the same Overleaf host.
            try:
                url = QUrl(self._login_url)
                scheme = url.scheme() or "https"
                host = url.host()
                if host:
                    base = f"{scheme}://{host}"
                    set_overleaf_base_url(base)
            except Exception:
                # If anything goes wrong here, we still proceed with the
                # captured cookies; the user can retry if necessary.
                pass

            self._cookie_header = header
            self.accept()


else:

    class OverleafLoginDialog(QDialog):
        """Fallback dialog when Qt WebEngine is not available.

        This implementation simply reports that embedded Overleaf login
        is not supported in the current environment. Callers should
        fall back to the manual "paste Cookie header" workflow.
        """

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._cookie_header: Optional[str] = None

        def exec_login(self) -> Optional[str]:
            """Show an informational message and return ``None``."""
            QMessageBox.information(
                self,
                "Embedded login not available",
                "Qt WebEngine is not available in this environment, so "
                "embedded Overleaf login cannot be used.\n\n"
                "Please log in to Overleaf in your normal browser, copy "
                "the Cookie header for a request to the Overleaf project "
                "dashboard from the developer tools, and paste it into "
                "the manual cookie prompt.",
            )
            return None


def _decode_cookie_field(field) -> str:
    """Return a text representation of a cookie field.

    This helper normalizes Qt WebEngine cookie fields, which may be
    bytes, bytearray, memoryview, or str. It never returns a
    representation like "b'foo'"; instead, it decodes bytes-like
    objects as UTF-8 and uses ``str()`` for everything else.
    """
    # Handle bytes-like
    if isinstance(field, (bytes, bytearray, memoryview)):
        try:
            return field.decode("utf-8", "ignore")
        except Exception:
            return repr(field)

    # Handle strings that still contain literal b'...' wrappers
    if isinstance(field, str):
        s = field.strip()
        if s.startswith("b'") and s.endswith("'"):
            return s[2:-1]
        if s.startswith('b"') and s.endswith('"'):
            return s[2:-1]
        return s

    return str(field)
