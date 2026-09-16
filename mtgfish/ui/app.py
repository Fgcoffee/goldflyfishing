"""The desktop shell: a Qt window hosting the web UI.

Qt draws the window and owns the process; everything visible is HTML in a
``QWebEngineView``, talking to Python over a ``QWebChannel``. Charts are the
reason - Plotly is far better at interactive charting than any Qt widget, and
"every datapoint is clickable" is a requirement rather than a nicety.

Run it with::

    python -m mtgfish.ui

If the window comes up blank, the Chromium sandbox could not start - common in
containers, over remote desktop, and under some security software. Setting
``QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox`` fixes it. The shell says so on
screen rather than leaving a blank window, because a silent blank page is
close to undiagnosable.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

from .bridge import Bridge

WEB = Path(__file__).parent / "web"


class Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MTG Commander Goldfisher")
        self.resize(1500, 950)

        self.view = QWebEngineView()
        # The page is a local file, and by default a local page may not load
        # anything from the internet - which blanks every card image on the
        # Deck tab, since those come from Scryfall's CDN.
        self.view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self.bridge = Bridge()

        # The channel has to be registered before the page loads, or the page's
        # own setup runs against a channel with nothing on it.
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.view.loadFinished.connect(self._loaded)
        self.view.load(QUrl.fromLocalFile(str(WEB / "index.html")))
        self.setCentralWidget(self.view)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Stop any run before the window goes.

        Otherwise the run's worker processes keep playing games for a window
        that no longer exists, and the program does not exit until they end.
        """
        self.bridge.shutdown()
        super().closeEvent(event)

    def _loaded(self, ok: bool) -> None:
        """Say something useful when the page will not load.

        A failed load leaves a blank white window and no error anywhere, which
        is close to impossible to diagnose. The usual cause is the Chromium
        sandbox failing to start - it does that in containers, over remote
        sessions, and under some security software - and the fix is one
        environment variable.
        """
        if ok:
            return
        self.view.setHtml(
            "<body style='background:#14161a;color:#dfe3ea;"
            "font:14px/1.6 system-ui;padding:40px'>"
            "<h1>The interface could not start</h1>"
            "<p>QtWebEngine failed to load the page. That is almost always "
            "the Chromium sandbox failing to start rather than anything "
            "wrong with the program.</p>"
            "<p>Try running it again with the sandbox off:</p>"
            "<pre style='background:#10131a;padding:12px;border-radius:6px'>"
            "set QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox<br>"
            "python -m mtgfish.ui</pre>"
            "<p>Everything here is also available without a window:</p>"
            "<pre style='background:#10131a;padding:12px;border-radius:6px'>"
            "python -m mtgfish.tools.simulate deck.txt --games 1000<br>"
            "python -m mtgfish.tools.parse_report --card 'Lightning Bolt'"
            "</pre></body>"
        )


def main(argv: list[str] | None = None) -> int:
    # The packaged build ships the card database as a read-only asset and it
    # has to reach a writable directory before anything opens it. Done before
    # the window exists so a first run shows a window that already works
    # rather than one that opens empty and fills in later.
    from ..bootstrap import seed_card_database

    seed_card_database(report=print)

    app = QApplication(argv if argv is not None else sys.argv)
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
