"""Command line entry point."""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys

from . import __version__
from .diff import diff_surfaces
from .fetch import (
    FetchError,
    cache_root,
    fetch_version,
    installed_version,
    latest_version,
)
from .report import Palette, assess, render
from .surface import read_surface
from .usage import scan_paths

__all__ = ["main"]

#: Exit status when the upgrade breaks something. Distinct from 1 so a CI step
#: can tell "this upgrade is unsafe" from "the check itself fell over" and act
#: differently on each.
EXIT_BREAKING = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="willitbreak",
        description=(
            "Check whether upgrading a dependency breaks code you actually "
            "wrote. Compares two versions of a package's API, then reports "
            "only the changes that land on one of your call sites."
        ),
        epilog=(
            "Examples:\n"
            "  willitbreak requests                      installed version vs latest\n"
            "  willitbreak requests --to 3.0.0\n"
            "  willitbreak requests --from 2.28.0 --to 2.31.0\n"
            "  willitbreak httpx --to 0.28.0 src/ tests/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("package", help="the distribution name on PyPI")
    parser.add_argument(
        "paths",
        nargs="*",
        type=pathlib.Path,
        help="your code to check (default: the current directory)",
    )
    parser.add_argument(
        "--from",
        dest="old",
        metavar="VERSION",
        help="version to upgrade from (default: the one installed here)",
    )
    parser.add_argument(
        "--to",
        dest="new",
        metavar="VERSION",
        help="version to upgrade to (default: the latest on PyPI)",
    )
    parser.add_argument(
        "--import-name",
        metavar="NAME",
        help="module name to look for, when it differs from the package name",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="list every API change, including ones your code never touches",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colour output (default: auto; NO_COLOR is always honoured)",
    )
    parser.add_argument(
        "--ascii", action="store_true", help="avoid non-ASCII characters"
    )
    parser.add_argument(
        "--cache", type=pathlib.Path, help="where to keep downloaded versions"
    )
    parser.add_argument(
        "--version", action="version", version=f"willitbreak {__version__}"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _use_utf8(sys.stdout)
    _use_utf8(sys.stderr)

    try:
        old_version, new_version = _resolve_versions(args)
    except FetchError as exc:
        print(f"willitbreak: {exc}", file=sys.stderr)
        return 1

    if old_version == new_version:
        print(
            f"willitbreak: {args.package} {old_version} is already the target "
            "version; nothing to compare",
            file=sys.stderr,
        )
        return 0

    cache = args.cache or cache_root()
    try:
        old = fetch_version(
            args.package, old_version, cache=cache, import_name=args.import_name
        )
        new = fetch_version(
            args.package, new_version, cache=cache, import_name=args.import_name
        )
    except FetchError as exc:
        print(f"willitbreak: {exc}", file=sys.stderr)
        return 1

    old_surface = read_surface(old.root, old.import_name, old_version)
    new_surface = read_surface(new.root, new.import_name, new_version)
    if not old_surface.symbols:
        print(
            f"willitbreak: found no public API in {args.package} {old_version}",
            file=sys.stderr,
        )
        return 1

    changes = diff_surfaces(old_surface, new_surface)
    paths = args.paths or [pathlib.Path.cwd()]
    scan = scan_paths(paths, old.import_name, root=pathlib.Path.cwd())
    outcome = assess(args.package, old_surface, new_surface, changes, scan)

    if args.json:
        payload = outcome.as_dict()
        if args.all:
            payload["all_changes"] = [c.as_dict() for c in changes]
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return EXIT_BREAKING if outcome.breaking else 0

    palette = Palette(args.color if args.color != "auto" else "auto")
    if args.color == "never":
        palette.enabled = False
    print(render(outcome, palette, ascii_only=args.ascii))

    if args.all and changes:
        print()
        print(palette.bold("Every change, including ones you do not use"))
        for change in changes:
            print(
                f"  {palette.paint(change.kind, 'muted')}  "
                f"{change.qualname}  {change.detail}"
            )

    return EXIT_BREAKING if outcome.breaking else 0


def _resolve_versions(args) -> tuple[str, str]:
    old = args.old
    if old is None:
        old = installed_version(args.package)
        if old is None:
            raise FetchError(
                f"{args.package} is not installed here, so there is no version "
                "to upgrade from; pass --from"
            )
    new = args.new or latest_version(args.package)
    return old, new


def _use_utf8(stream) -> None:
    """Keep the arrow and bullet printable on a non-UTF-8 console."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    with contextlib.suppress(OSError, ValueError):
        reconfigure(encoding="utf-8", errors="replace")
