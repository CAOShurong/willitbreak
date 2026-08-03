"""What changed between two versions, and what it takes to be hurt by it.

A list of API changes is not the answer on its own. "``timeout`` was removed
from ``Client.get``" only matters to code that passes ``timeout``, and
"``retries`` lost its default" only matters to code that *omits* it. So every
change here carries the condition under which a caller is actually affected,
and the intersection with real call sites happens in :mod:`willitbreak.usage`.

Splitting it that way is what keeps the tool quiet. A version bump of a large
library produces hundreds of changes; almost none of them touch any given
codebase, and a tool that reads out all of them is a changelog with extra
steps.
"""

from __future__ import annotations

from dataclasses import dataclass

from .surface import (
    KEYWORD_ONLY,
    POSITIONAL_ONLY,
    POSITIONAL_OR_KEYWORD,
    VAR_KEYWORD,
    VAR_POSITIONAL,
    Parameter,
    Surface,
    Symbol,
)

__all__ = ["Change", "diff_surfaces"]

#: A caller doing the matching thing is broken. Not "might be".
BREAKING = "breaking"
#: Behaviour changed under the caller's feet without an error being raised.
#: Worth a look, never worth failing a build over.
RISKY = "risky"


@dataclass(frozen=True)
class Change:
    """One API change, plus what a caller must do to be affected by it."""

    qualname: str
    kind: str
    detail: str
    severity: str = BREAKING
    #: Set when only callers passing this keyword are affected.
    parameter: str | None = None
    #: Set when only callers passing at least this many positional arguments
    #: are affected.
    min_positional: int | None = None
    #: Set when only callers *omitting* :attr:`parameter` are affected -- the
    #: mirror image of the usual case, and easy to get backwards.
    when_omitted: bool = False

    def as_dict(self) -> dict:
        return {
            "qualname": self.qualname,
            "kind": self.kind,
            "detail": self.detail,
            "severity": self.severity,
            "parameter": self.parameter,
            "min_positional": self.min_positional,
            "when_omitted": self.when_omitted,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Change:
        return cls(
            qualname=str(raw["qualname"]),
            kind=str(raw["kind"]),
            detail=str(raw["detail"]),
            severity=str(raw.get("severity", BREAKING)),
            parameter=raw.get("parameter"),
            min_positional=raw.get("min_positional"),
            when_omitted=bool(raw.get("when_omitted", False)),
        )


def diff_surfaces(old: Surface, new: Surface) -> list[Change]:
    """Every way ``new`` could break a caller who was fine on ``old``."""
    changes: list[Change] = []

    for qualname, before in sorted(old.symbols.items()):
        after = new.get(qualname)
        if after is None:
            if _shadowed_by_removed_parent(qualname, old, new):
                # The whole class went, so reporting each of its methods
                # separately would turn one fact into twenty lines.
                continue
            changes.append(
                Change(
                    qualname=qualname,
                    kind="removed",
                    detail=f"{before.kind} no longer exists",
                )
            )
            continue
        changes.extend(_compare_symbol(before, after))

    return changes


def _shadowed_by_removed_parent(qualname: str, old: Surface, new: Surface) -> bool:
    parts = qualname.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:cut])
        if parent in old.symbols and parent not in new.symbols:
            return True
    return False


def _compare_symbol(before: Symbol, after: Symbol) -> list[Change]:
    changes: list[Change] = []

    if before.is_callable and not after.is_callable:
        return [
            Change(
                qualname=before.qualname,
                kind="not-callable",
                detail=f"was a {before.kind}, is now a {after.kind}",
            )
        ]
    if not before.is_callable or not after.is_callable:
        return changes

    old_params = list(before.parameters or ())
    new_params = list(after.parameters or ())
    new_by_name = {p.name: p for p in new_params}
    old_by_name = {p.name: p for p in old_params}

    swallows_keywords = after.accepts_arbitrary_keywords
    swallows_positional = any(p.kind == VAR_POSITIONAL for p in new_params)

    for index, parameter in enumerate(old_params):
        if parameter.kind in (VAR_POSITIONAL, VAR_KEYWORD):
            continue
        replacement = new_by_name.get(parameter.name)

        if replacement is None:
            renamed = _renamed_to(parameter, index, old_params, new_params, old_by_name)
            if parameter.can_be_keyword and not swallows_keywords:
                detail = (
                    f"parameter {parameter.name!r} was removed"
                    if renamed is None
                    else f"parameter {parameter.name!r} appears to be "
                    f"renamed to {renamed!r}"
                )
                changes.append(
                    Change(
                        qualname=before.qualname,
                        kind="parameter-removed",
                        detail=detail,
                        parameter=parameter.name,
                    )
                )
            if parameter.can_be_positional and not swallows_positional:
                positions = _positional_slots(new_params)
                if index >= positions:
                    changes.append(
                        Change(
                            qualname=before.qualname,
                            kind="fewer-positional",
                            detail=(
                                f"takes {positions} positional argument"
                                f"{'' if positions == 1 else 's'} now, "
                                f"{_positional_slots(old_params)} before"
                            ),
                            min_positional=index + 1,
                        )
                    )
            continue

        if parameter.has_default and not replacement.has_default:
            # A caller who passes it positionally is fine, so record which
            # positional count covers it. Positional parameters come first in
            # the tuple, so the index is the slot. A keyword-only parameter
            # has no slot, and stays None.
            covered_by = (
                index + 1
                if replacement.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)
                else None
            )
            changes.append(
                Change(
                    qualname=before.qualname,
                    kind="now-required",
                    detail=f"parameter {parameter.name!r} no longer has a default",
                    parameter=parameter.name,
                    min_positional=covered_by,
                    when_omitted=True,
                )
            )
        if parameter.can_be_positional and replacement.kind == KEYWORD_ONLY:
            changes.append(
                Change(
                    qualname=before.qualname,
                    kind="keyword-only",
                    detail=f"parameter {parameter.name!r} is now keyword-only",
                    parameter=parameter.name,
                    min_positional=index + 1,
                )
            )
        if parameter.can_be_keyword and replacement.kind == POSITIONAL_ONLY:
            changes.append(
                Change(
                    qualname=before.qualname,
                    kind="positional-only",
                    detail=f"parameter {parameter.name!r} is now positional-only",
                    parameter=parameter.name,
                )
            )

    # A new parameter with no default breaks every existing call, since no
    # existing call can be passing something that did not exist.
    for parameter in new_params:
        if parameter.name in old_by_name:
            continue
        if parameter.kind in (VAR_POSITIONAL, VAR_KEYWORD) or parameter.has_default:
            continue
        if _renamed_from(parameter, new_params, old_params, new_by_name) is not None:
            continue  # already reported as a rename
        changes.append(
            Change(
                qualname=before.qualname,
                kind="new-required",
                detail=f"new required parameter {parameter.name!r}",
            )
        )

    return changes


def _positional_slots(parameters: list[Parameter]) -> int:
    return sum(
        1 for p in parameters if p.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)
    )


def _renamed_to(
    parameter: Parameter,
    index: int,
    old_params: list[Parameter],
    new_params: list[Parameter],
    old_by_name: dict[str, Parameter],
) -> str | None:
    """Guess whether a vanished parameter was renamed rather than dropped.

    Only claimed when the same slot now holds exactly one unfamiliar name.
    The guess never changes whether something is reported -- a removed
    keyword breaks the same caller either way -- it only makes the message
    say something more useful than "gone".
    """
    if index >= len(new_params):
        return None
    candidate = new_params[index]
    if candidate.name in old_by_name:
        return None
    if candidate.kind != parameter.kind:
        return None
    return candidate.name


def _renamed_from(
    parameter: Parameter,
    new_params: list[Parameter],
    old_params: list[Parameter],
    new_by_name: dict[str, Parameter],
) -> str | None:
    index = new_params.index(parameter)
    if index >= len(old_params):
        return None
    candidate = old_params[index]
    if candidate.name in new_by_name:
        return None
    return candidate.name
