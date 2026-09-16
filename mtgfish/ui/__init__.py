"""The desktop UI: a Qt window hosting an HTML front end.

Qt owns the process and the window; everything visible is HTML in a
``QWebEngineView`` talking to Python over a ``QWebChannel``. Plotly does the
charting, because "every datapoint is clickable" is a requirement and no Qt
widget does that as well.

The logic worth testing lives in ``sandbox``, which is headless and returns
plain dictionaries. ``bridge`` only encodes; ``app`` only opens a window.
"""

from __future__ import annotations

from .sandbox import PassiveOpponent, Sandbox

__all__ = ["PassiveOpponent", "Sandbox"]
