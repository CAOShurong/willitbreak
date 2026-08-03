"""Building throwaway packages on disk for the tests to read.

The surface reader works on files, so the tests give it files. Nothing is
imported, which is the whole point of reading source: these fixtures include
signatures that would not survive being executed.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from willitbreak.diff import diff_surfaces
from willitbreak.report import assess
from willitbreak.surface import Surface, read_surface
from willitbreak.usage import scan_source


def write_package(root: pathlib.Path, package: str, modules: dict[str, str]) -> None:
    """Write ``{"__init__": "...", "sub/__init__": "..."}`` under ``root``."""
    for name, source in modules.items():
        path = root / package / (name + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Not Path.write_text(newline=...), which only exists from 3.10.
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source)


class PackageCase(unittest.TestCase):
    """A test that needs one or two versions of a package on disk."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = pathlib.Path(self._temp.name)

    def surface(
        self,
        modules: dict[str, str],
        *,
        version: str = "1.0",
        package: str = "pkg",
        label: str = "a",
    ) -> Surface:
        root = self.root / label
        write_package(root, package, modules)
        return read_surface(root, package, version)

    def two(self, before: dict[str, str], after: dict[str, str]):
        old = self.surface(before, version="1.0", label="old")
        new = self.surface(after, version="2.0", label="new")
        return old, new, diff_surfaces(old, new)

    def outcome(self, before: dict[str, str], after: dict[str, str], code: str):
        """Full pipeline: two versions plus caller source, intersected."""
        old, new, changes = self.two(before, after)

        class _Scan:
            references = scan_source(code, "pkg", "app.py")
            files_scanned = 1
            unparsed: list = []

        return assess("pkg", old, new, changes, _Scan())

    def kinds(self, changes) -> set[tuple[str, str]]:
        return {(c.qualname, c.kind) for c in changes}
