"""willitbreak -- will this dependency upgrade break code you actually wrote?

Comparing two versions of a library tells you what its authors changed. It
does not tell you whether you used any of it. This reads both versions'
public API, diffs them, then resolves the call sites in your own source and
reports only the changes that land on one of them.

    from willitbreak import check

    outcome = check("requests", "2.28.0", "2.31.0", ["src"])
    for finding in outcome.breaking:
        print(finding.change.detail)
        for reference in finding.references:
            print("   ", reference.source)
"""

from __future__ import annotations

import pathlib

from .diff import Change, diff_surfaces
from .fetch import FetchError, fetch_version
from .report import Finding, Outcome, assess
from .surface import Surface, Symbol, read_surface
from .usage import Reference, scan_paths, scan_source

__version__ = "0.1.1"

__all__ = [
    "Change",
    "FetchError",
    "Finding",
    "Outcome",
    "Reference",
    "Surface",
    "Symbol",
    "__version__",
    "assess",
    "check",
    "diff_surfaces",
    "read_surface",
    "scan_paths",
    "scan_source",
]


def check(
    package: str,
    old_version: str,
    new_version: str,
    paths,
    *,
    import_name: str | None = None,
) -> Outcome:
    """Download both versions, diff them, and intersect with ``paths``."""
    old = fetch_version(package, old_version, import_name=import_name)
    new = fetch_version(package, new_version, import_name=import_name)
    old_surface = read_surface(old.root, old.import_name, old_version)
    new_surface = read_surface(new.root, new.import_name, new_version)
    changes = diff_surfaces(old_surface, new_surface)
    scan = scan_paths([pathlib.Path(p) for p in paths], old.import_name)
    return assess(package, old_surface, new_surface, changes, scan)
