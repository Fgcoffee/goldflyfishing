"""Entry point for the packaged application.

Not ``mtgfish/ui/__main__.py``: PyInstaller runs the entry script as
``__main__`` with no package around it, so the relative imports in that module
fail immediately with "attempted relative import with no known parent package".
The packaged app needs a script that imports absolutely.

It also needs somewhere to put a traceback. This is a windowed build - there is
no console - so an exception before the Qt window exists produces a process
that vanishes with no message anywhere. Writing the crash to the user's data
directory means there is something to read afterwards.
"""

from __future__ import annotations

import multiprocessing
import sys
import traceback

# Before anything else, and before any import that might start a process pool.
#
# The simulator runs games across a process pool. On Windows a new process is
# started by re-executing this program, and a frozen build has no way to tell
# "I am a worker" from "I was double-clicked" - so every worker opened its own
# copy of the application window. Four workers, four extra windows.
#
# freeze_support() reads the arguments the parent passed, and in a worker it
# runs the assigned task and exits instead of returning here.
multiprocessing.freeze_support()


def _crash(exc: BaseException) -> None:
    """Record a failure that happened before, or instead of, a window."""
    text = "".join(traceback.format_exception(exc))
    try:
        from mtgfish.paths import data_root, ensure

        path = ensure(data_root()) / "crash.log"
        path.write_text(text, encoding="utf8")
        where = str(path)
    except Exception:  # noqa: BLE001 - the crash matters more than the log
        where = "(could not be written)"

    # A dialog if Qt got far enough to offer one, so the user is not left
    # staring at a window that never appeared.
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None,
            "MTG Goldfisher could not start",
            f"{type(exc).__name__}: {exc}\n\nDetails written to:\n{where}",
        )
        del app
    except Exception:  # noqa: BLE001
        print(text, file=sys.stderr)


def main() -> int:
    try:
        # A headless mode, so the packaged build can be driven without a
        # window - for batch runs, and because it is the only way to exercise
        # the worker-process path of the *frozen* binary, which is where the
        # extra-windows bug lived.
        if "--simulate" in sys.argv:
            from mtgfish.tools.simulate import main as simulate

            argv = [a for a in sys.argv[1:] if a != "--simulate"]
            return simulate(argv)

        # The swap lab, headless. Same reason as --simulate: it is the only
        # way to exercise this path in the *frozen* binary, where a traceback
        # has no console to reach and the worker-process behaviour differs
        # from a source run.
        if "--swap-lab" in sys.argv:
            from mtgfish.tools.swap import main as swap

            argv = [a for a in sys.argv[1:] if a != "--swap-lab"]
            return swap(argv)

        from mtgfish.ui.app import main as run

        return run()
    except BaseException as exc:  # noqa: BLE001 - reported, then re-raised
        _crash(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
