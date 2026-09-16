"""The web deployment: the same UI and bridge as the desktop app, over HTTP.

The desktop app puts the page in a Qt window and connects it to ``Bridge``
with a QWebChannel. This package serves the same page to a browser and exposes
the same ``Bridge`` slots as ``POST /api/call/<slot>``, with its signals pushed
over server-sent events. The page picks whichever transport it finds, so there
is one front end, not two.

Run it with::

    python -m mtgfish.web --port 8000
    python -m mtgfish.web --reload     # restart when the code changes

Nothing about a slot is registered here. The server asks the bridge which
slots it has when it starts, so a slot added to ``bridge.py`` is reachable from
the browser without touching this package.
"""
