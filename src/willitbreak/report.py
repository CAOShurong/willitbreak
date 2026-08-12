"""Intersecting the diff with the call sites, and saying what came out.

The intersection is the product. A change is only reported when a specific
line of the caller's code satisfies the condition attached to it: a removed
keyword needs a call that passes it, a parameter gone keyword-only needs a
call that passes it positionally, a lost default needs a call that omits it.

Everything else is filed as "changed, does not touch you", which is the
answer people actually want and the one no changelog can give them.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from .diff import BREAKING, Change
from .surface import Surface
from .usage import Reference, ScanResult

__all__ = ["Finding", "Outcome", "Palette", "assess", "render"]


@dataclass
class Finding:
    """A change, and the lines of the caller's code that it breaks."""

    change: Change
    references: list[Reference] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            **self.change.as_dict(),
            "references": [r.as_dict() for r in self.references],
        }


@dataclass
class Outcome:
    package: str
    old_version: str
    new_version: str
    findings: list[Finding] = field(default_factory=list)
    #: Changes that touch nothing the caller wrote. Counted, not listed.
    untouched: int = 0
    files_scanned: int = 0
    unparsed: list[tuple[str, str]] = field(default_factory=list)
    unreadable_modules: list[str] = field(default_factory=list)

    @property
    def breaking(self) -> list[Finding]:
        return [f for f in self.findings if f.change.severity == BREAKING]

    @property
    def ok(self) -> bool:
        return not self.breaking

    def as_dict(self) -> dict:
        return {
            "schema": "willitbreak/outcome-v1",
            "package": self.package,
            "from": self.old_version,
            "to": self.new_version,
            "breaking": len(self.breaking),
            "untouched_changes": self.untouched,
            "files_scanned": self.files_scanned,
            "unparsed": [{"path": p, "error": e} for p, e in self.unparsed],
            "unreadable_modules": list(self.unreadable_modules),
            "findings": [f.as_dict() for f in self.findings],
        }


def _affects(change: Change, reference: Reference) -> bool:
    """Does this specific line of the caller's code hit this change?

    Every branch here answers "can I prove this call is affected", never "does
    it look affected". Where a splat makes the arguments unknowable the answer
    is no, because a maybe reported as a break is the failure mode that gets
    the tool switched off.
    """
    if change.kind in ("removed", "not-callable"):
        return True

    if change.kind == "new-required":
        # Every existing call is missing a parameter that did not exist.
        return reference.called

    if not reference.called:
        # The remaining changes are about arguments, and a bare mention of the
        # name passes none.
        return False

    if change.when_omitted:
        # A parameter that lost its default breaks callers who leave it out.
        # A ``**kwargs`` splat might be supplying it, so nothing is provable.
        if reference.splatted_keywords or reference.positional is None:
            return False
        if change.parameter in reference.keywords:
            return False
        # Passing it positionally satisfies the now-required parameter just as
        # well, so only a caller who did neither is broken.
        position = change.min_positional
        return position is None or reference.positional < position

    if change.min_positional is not None:
        if reference.positional is None:
            return False
        if reference.positional >= change.min_positional:
            return True
        # A keyword-only change also breaks the keyword form when the name
        # itself went away, which is covered by the parameter branch below.

    if change.parameter is not None and change.kind != "keyword-only":
        if reference.splatted_keywords:
            return False
        return change.parameter in reference.keywords

    return False


def assess(
    package: str,
    old: Surface,
    new: Surface,
    changes: list[Change],
    scan: ScanResult,
) -> Outcome:
    """Keep only the changes that land on a line the caller wrote."""
    by_name: dict[str, list[Reference]] = {}
    for reference in scan.references:
        by_name.setdefault(reference.qualname, []).append(reference)

    outcome = Outcome(
        package=package,
        old_version=old.version,
        new_version=new.version,
        files_scanned=scan.files_scanned,
        unparsed=list(scan.unparsed),
        unreadable_modules=sorted(set(old.unreadable) | set(new.unreadable)),
    )

    for change in changes:
        hits = [
            reference
            for reference in by_name.get(change.qualname, ())
            if _affects(change, reference)
        ]
        if hits:
            hits.sort(key=lambda r: (r.path, r.lineno))
            outcome.findings.append(Finding(change=change, references=hits))
        else:
            outcome.untouched += 1

    outcome.findings.sort(
        key=lambda f: (
            f.change.severity != BREAKING,
            -len(f.references),
            f.change.qualname,
        )
    )
    return outcome


# -- rendering -------------------------------------------------------------


class Palette:
    """Colour that degrades, and is never the only signal."""

    ROLES = {
        "bad": "\x1b[38;5;167m",
        "warn": "\x1b[38;5;179m",
        "ok": "\x1b[38;5;71m",
        "muted": "\x1b[38;5;244m",
        "accent": "\x1b[38;5;74m",
    }

    def __init__(self, enabled: str = "auto", stream=None) -> None:
        stream = stream or sys.stdout
        if enabled == "auto":
            self.enabled = (
                os.environ.get("NO_COLOR") is None
                and hasattr(stream, "isatty")
                and stream.isatty()
                and os.environ.get("TERM") != "dumb"
            )
        else:
            self.enabled = enabled == "always"

    def paint(self, text: str, role: str) -> str:
        if not self.enabled or not text:
            return text
        return f"{self.ROLES[role]}{text}\x1b[0m"

    def bold(self, text: str) -> str:
        return f"\x1b[1m{text}\x1b[0m" if self.enabled and text else text


def render(outcome: Outcome, palette: Palette, *, ascii_only: bool = False) -> str:
    arrow = "->" if ascii_only else "→"
    bullet = "-" if ascii_only else "•"
    separator = " - " if ascii_only else " · "
    rows: list[str] = []

    header = f"{outcome.package} {outcome.old_version} {arrow} {outcome.new_version}"
    rows.append(palette.bold(header))

    scanned = (
        f"{outcome.files_scanned} file"
        f"{'' if outcome.files_scanned == 1 else 's'} scanned"
    )
    rows.append(
        "  "
        + palette.paint(
            f"{scanned}{separator}{outcome.untouched} API change"
            f"{'' if outcome.untouched == 1 else 's'} that do not touch your code",
            "muted",
        )
    )

    if not outcome.findings:
        rows.append("")
        rows.append(
            "  "
            + palette.paint("nothing in your code is affected by this upgrade", "ok")
        )
    else:
        for finding in outcome.findings:
            change = finding.change
            role = "bad" if change.severity == BREAKING else "warn"
            label = "BREAKS" if change.severity == BREAKING else "check "
            rows.append("")
            rows.append(
                f"  {palette.paint(label, role)} "
                f"{palette.paint(change.qualname, 'accent')}"
            )
            rows.append(f"      {palette.paint(change.detail, 'muted')}")
            for reference in finding.references[:8]:
                detail = _call_shape(reference, ascii_only)
                rows.append(
                    f"      {bullet} {reference.source}"
                    + (f"  {palette.paint(detail, 'muted')}" if detail else "")
                )
            if len(finding.references) > 8:
                rows.append(
                    "      "
                    + palette.paint(
                        f"+{len(finding.references) - 8} more call sites", "muted"
                    )
                )

    notes: list[str] = []
    if outcome.unparsed:
        shown = ", ".join(path for path, _ in outcome.unparsed[:3])
        notes.append(
            f"{len(outcome.unparsed)} of your files would not parse and were "
            f"not checked: {shown}"
        )
    if outcome.unreadable_modules:
        notes.append(
            f"{len(outcome.unreadable_modules)} module"
            f"{'' if len(outcome.unreadable_modules) == 1 else 's'} of "
            f"{outcome.package} would not parse, so its API is partial"
        )
    if notes:
        rows.append("")
        for note in notes:
            rows.append("  " + palette.paint(note, "warn"))

    if outcome.breaking:
        rows.append("")
        count = len(outcome.breaking)
        sites = sum(len(f.references) for f in outcome.breaking)
        rows.append(
            palette.paint(
                f"{count} breaking change{'' if count == 1 else 's'} across "
                f"{sites} call site{'' if sites == 1 else 's'}",
                "bad",
            )
        )
    text = "\n".join(rows)
    if ascii_only:
        return text.encode("ascii", errors="backslashreplace").decode("ascii")
    return text


def _call_shape(reference: Reference, ascii_only: bool) -> str:
    if not reference.called:
        return "referenced"
    bits = []
    if reference.positional is None:
        bits.append("*args")
    elif reference.positional:
        bits.append(f"{reference.positional} positional")
    if reference.keywords:
        bits.append(", ".join(sorted(reference.keywords)) + "=")
    if reference.splatted_keywords:
        bits.append("**kwargs")
    return "  ".join(bits)
