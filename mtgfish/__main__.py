"""``mtgfish`` - one command in front of everything the package can do.

``pyproject.toml`` declares this module as the ``mtgfish`` console script, so
an installed copy gets a single command instead of a list of ``python -m``
paths to memorise. Every subcommand is the module that already existed; this
only routes to it.

    mtgfish web --reload
    mtgfish simulate deck.txt --games 1000
    mtgfish parse-report --card "Lightning Bolt"

Imports happen after the subcommand is chosen, and that is the point. The
desktop window needs PySide6, a simulation needs numpy and the card database,
and the web server wants neither Qt nor a card pool loaded to print its help.
Importing all of them up front would make ``mtgfish --help`` cost several
seconds on a good machine and fail outright on a server with no Qt.
"""

from __future__ import annotations

import importlib
import multiprocessing
import sys

# Subcommand -> the module holding its ``main``, and one line about it. Kept as
# strings so listing the commands needs no imports at all.
COMMANDS: dict[str, tuple[str, str]] = {
    "ui": ("mtgfish.ui.app", "open the desktop window"),
    "web": ("mtgfish.web.__main__", "serve the web app over HTTP"),
    "fetch": ("mtgfish.tools.fetch_scryfall", "build or rebuild the local card database"),
    "simulate": ("mtgfish.tools.simulate", "goldfish a deck over many games"),
    "swap": ("mtgfish.tools.swap", "compare card swaps on the same seeds"),
    "cost": ("mtgfish.tools.cost", "what a run costs in time, CPU and memory"),
    "parse-report": ("mtgfish.tools.parse_report", "parser coverage over the real card pool"),
    "play-rate": ("mtgfish.tools.play_rate_report", "parse coverage over the cards people play"),
    "inertia": ("mtgfish.tools.inertia_report", "cards that parse cleanly and then do nothing"),
    "rules-coverage": ("mtgfish.tools.rules_coverage", "Comprehensive Rules coverage"),
    "rules": ("mtgfish.tools.rules", "look up a rule, check citations, diff a release"),
}


# Subcommands that read the card pool. Without it they fail several frames
# deep in sqlite with "unable to open database file", which names neither the
# file nor the one command that creates it. `ui` and `web` are not here: they
# seed and report the database themselves, before opening a window on it.
NEEDS_CARDS = frozenset({"simulate", "swap", "cost", "parse-report", "play-rate", "inertia"})


def usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = [
        "usage: mtgfish <command> [options]",
        "",
        "A Magic: The Gathering Commander simulator and goldfisher.",
        "",
        "commands:",
    ]
    lines += [f"  {name:<{width}}  {summary}" for name, (_, summary) in COMMANDS.items()]
    lines += [
        "",
        "`mtgfish <command> --help` describes that command.",
        "Most of them read the card database, which `mtgfish fetch` builds once.",
    ]
    return "\n".join(lines)


def _have_cards() -> bool:
    """Seed the card database if it ships with the build, and say if it is missing."""
    from .bootstrap import seed_card_database

    database = seed_card_database(report=print)
    if database.exists() and database.stat().st_size > 0:
        return True
    print(
        f"No card database at {database}.\n"
        "Build it with:  mtgfish fetch\n"
        "or point MTGFISH_DATA_DIR at a directory holding cards.sqlite.",
        file=sys.stderr,
    )
    return False


def _import(name: str, module_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        # The one import failure that is a normal install state rather than a
        # bug: PySide6 is optional, and the web app is the same front end.
        if name == "ui":
            print(
                f"The desktop window needs PySide6, which is not installed ({exc}).\n"
                "  pip install PySide6\n"
                "Or run the same front end in a browser:  mtgfish web",
                file=sys.stderr,
            )
            return None
        raise


def main(argv: list[str] | None = None) -> int:
    # Before any import that could start a process pool, and for the same
    # reason `build/launcher.py` does it: a frozen build starts a pool worker
    # by re-executing the program, and a worker that reached the dispatcher
    # would run a second copy of whatever subcommand it was given. A no-op
    # when not frozen, which is every run from source.
    multiprocessing.freeze_support()

    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv[0] in ("-V", "--version"):
        from . import __version__

        print(f"mtgfish {__version__}")
        return 0

    # The modules are named with underscores and the commands with hyphens;
    # accepting either saves a pointless error over which one this is.
    name = argv[0].replace("_", "-")
    if name not in COMMANDS:
        print(f"mtgfish: unknown command {argv[0]!r}\n", file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 2

    # Not when the user only asked what the command does. Reading `mtgfish
    # simulate --help` before building a 100 MB database is the sensible order
    # to do this in, and refusing to answer would be punishing it.
    wants_help = "-h" in argv[1:] or "--help" in argv[1:]
    if name in NEEDS_CARDS and not wants_help and not _have_cards():
        return 2

    module_name, _ = COMMANDS[name]
    module = _import(name, module_name)
    if module is None:
        return 1

    # Each subcommand's ``main`` reads ``sys.argv`` when called with nothing,
    # and argparse names the program after ``sys.argv[0]``. Left alone that is
    # the console script's path, so every subcommand would head its usage with
    # a bare "mtgfish" - the one word that does not tell the reader which
    # command they are looking at.
    sys.argv = [f"mtgfish {name}", *argv[1:]]
    status = module.main()
    # `cost` reports by printing and returns nothing, which is success.
    return 0 if status is None else int(status)


if __name__ == "__main__":
    raise SystemExit(main())
