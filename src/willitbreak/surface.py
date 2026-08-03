"""The public API of a package, read out of its source.

Everything downstream is a comparison between two of these, so what counts as
"the public API" is decided here, once. Two rules do most of the work:

* A name is private if any part of its path starts with an underscore, unless
  the module said otherwise with ``__all__``. ``pkg._client`` is private;
  ``pkg.Client`` re-exported from it is not.
* A re-export is the real name. Libraries almost universally implement in
  ``pkg/_client.py`` and expose in ``pkg/__init__.py``, so a tool that only
  looked at where a class was *defined* would report ``pkg._client.Client``
  and never match the ``pkg.Client`` everyone actually writes.

Reading source rather than importing is deliberate. Importing an arbitrary
version of an arbitrary package to inspect it means executing its code, which
is both a security problem and unreliable -- the version being examined is
usually not the one installed, and often will not even import against the
current interpreter.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

__all__ = ["Parameter", "Surface", "Symbol", "read_surface"]

#: Parameter kinds, named as the language does. The distinction matters: a
#: parameter moving from positional-or-keyword to keyword-only breaks every
#: caller that passed it positionally, and nothing else notices.
POSITIONAL_ONLY = "positional-only"
POSITIONAL_OR_KEYWORD = "positional-or-keyword"
VAR_POSITIONAL = "var-positional"
KEYWORD_ONLY = "keyword-only"
VAR_KEYWORD = "var-keyword"

#: How many passes of re-export resolution to run, and how deep a single
#: chain may go. Real packages nest two or three layers; anything past this is
#: a cycle, and the cap is what makes one survivable.
_MAX_REEXPORT_DEPTH = 8


@dataclass(frozen=True)
class Parameter:
    name: str
    kind: str
    has_default: bool

    @property
    def can_be_positional(self) -> bool:
        return self.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL)

    @property
    def can_be_keyword(self) -> bool:
        return self.kind in (POSITIONAL_OR_KEYWORD, KEYWORD_ONLY, VAR_KEYWORD)

    def as_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "has_default": self.has_default}

    @classmethod
    def from_dict(cls, raw: dict) -> Parameter:
        return cls(
            name=str(raw["name"]),
            kind=str(raw["kind"]),
            has_default=bool(raw["has_default"]),
        )


@dataclass
class Symbol:
    """One public name, and what a caller can do with it."""

    qualname: str
    #: ``module``, ``class``, ``function``, ``method``, or ``attribute``.
    kind: str
    #: ``None`` for things that are not called. Never confuse that with an
    #: empty tuple, which means "callable, takes nothing".
    parameters: tuple[Parameter, ...] | None = None
    #: True when the name is reachable but was defined elsewhere, e.g.
    #: ``pkg.Client`` implemented in ``pkg._client``.
    reexported: bool = False

    @property
    def is_callable(self) -> bool:
        return self.parameters is not None

    def parameter(self, name: str) -> Parameter | None:
        for parameter in self.parameters or ():
            if parameter.name == name:
                return parameter
        return None

    @property
    def accepts_arbitrary_keywords(self) -> bool:
        """``**kwargs`` swallows anything, so no keyword can be proven wrong."""
        return any(p.kind == VAR_KEYWORD for p in self.parameters or ())

    def as_dict(self) -> dict:
        return {
            "qualname": self.qualname,
            "kind": self.kind,
            "parameters": (
                None
                if self.parameters is None
                else [p.as_dict() for p in self.parameters]
            ),
            "reexported": self.reexported,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Symbol:
        parameters = raw.get("parameters")
        return cls(
            qualname=str(raw["qualname"]),
            kind=str(raw["kind"]),
            parameters=(
                None
                if parameters is None
                else tuple(Parameter.from_dict(p) for p in parameters)
            ),
            reexported=bool(raw.get("reexported", False)),
        )


@dataclass
class Surface:
    """Every public name in one version of a package."""

    package: str
    version: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    #: Modules that would not parse. Reported rather than swallowed: a
    #: comparison that silently skipped half a package would read as "nothing
    #: changed", which is the most dangerous wrong answer this tool could give.
    unreadable: list[str] = field(default_factory=list)

    def __contains__(self, qualname: str) -> bool:
        return qualname in self.symbols

    def get(self, qualname: str) -> Symbol | None:
        return self.symbols.get(qualname)

    def as_dict(self) -> dict:
        return {
            "schema": "willitbreak/surface-v1",
            "package": self.package,
            "version": self.version,
            "unreadable": list(self.unreadable),
            "symbols": [s.as_dict() for s in self.symbols.values()],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Surface:
        surface = cls(
            package=str(raw.get("package", "")),
            version=str(raw.get("version", "")),
            unreadable=list(raw.get("unreadable", ())),
        )
        for entry in raw.get("symbols", ()):
            symbol = Symbol.from_dict(entry)
            surface.symbols[symbol.qualname] = symbol
        return surface


def is_private(name: str) -> bool:
    """A single leading underscore, but not a dunder."""
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef, *, drop_first: bool):
    """Signature as a caller sees it.

    ``self`` and ``cls`` are dropped for methods: the caller never passes them,
    so a rename would be reported as a breaking change that breaks nobody.
    """
    args = node.args
    out: list[Parameter] = []
    defaults = list(args.defaults)
    positional = list(args.posonlyargs) + list(args.args)
    # Defaults bind to the *end* of the positional parameters, so the offset
    # is what decides whether a given parameter is required.
    offset = len(positional) - len(defaults)

    for index, arg in enumerate(positional):
        kind = (
            POSITIONAL_ONLY if index < len(args.posonlyargs) else POSITIONAL_OR_KEYWORD
        )
        out.append(Parameter(name=arg.arg, kind=kind, has_default=index >= offset))
    if args.vararg is not None:
        out.append(
            Parameter(name=args.vararg.arg, kind=VAR_POSITIONAL, has_default=False)
        )
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        out.append(
            Parameter(name=arg.arg, kind=KEYWORD_ONLY, has_default=default is not None)
        )
    if args.kwarg is not None:
        out.append(Parameter(name=args.kwarg.arg, kind=VAR_KEYWORD, has_default=False))

    if drop_first and out and out[0].kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD):
        out = out[1:]
    return tuple(out)


def _module_name(root: pathlib.Path, path: pathlib.Path) -> str | None:
    """Dotted module name for a file, or ``None`` if it is not importable.

    ``root`` is the directory containing the package, so the relative path
    already begins with the package name.
    """
    parts = list(path.relative_to(root).parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    if not parts:
        return None
    for part in parts:
        if not part.isidentifier():
            return None
    return ".".join(parts)


def _is_decorated_property(node) -> bool:
    for decorator in node.decorator_list:
        name = decorator
        if isinstance(name, ast.Attribute) and name.attr in (
            "setter",
            "deleter",
            "getter",
        ):
            return True
        if isinstance(name, ast.Name) and name.id in ("property", "cached_property"):
            return True
        if isinstance(name, ast.Attribute) and name.attr == "cached_property":
            return True
    return False


def _is_staticmethod(node) -> bool:
    return any(
        isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list
    )


class _ModuleReader(ast.NodeVisitor):
    """Collect the public names one module defines."""

    def __init__(self, module: str, *, is_package: bool = False) -> None:
        self.module = module
        # ``from . import x`` means something different in ``pkg/__init__.py``
        # than in ``pkg/mid.py``: for a package the dot is the module itself,
        # for a plain module it is the module's parent.
        self.is_package = is_package
        self.symbols: dict[str, Symbol] = {}
        #: ``local name -> fully qualified source`` for ``from x import y``.
        self.aliases: dict[str, str] = {}
        self.dunder_all: set[str] | None = None

    def visit_Module(self, node: ast.Module) -> None:
        for statement in node.body:
            self._top_level(statement)

    def _top_level(self, statement) -> None:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._function(statement, self.module, kind="function", drop_first=False)
        elif isinstance(statement, ast.ClassDef):
            self._class(statement)
        elif isinstance(statement, ast.Assign):
            self._assign(statement)
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name):
                self._attribute(statement.target.id)
        elif isinstance(statement, ast.ImportFrom):
            self._import_from(statement)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                local = alias.asname or alias.name.split(".")[0]
                self.aliases[local] = alias.name
        elif isinstance(statement, (ast.If, ast.Try)):
            # Conditional definitions are ordinary in real packages -- version
            # guards, optional imports -- and the names they bind are just as
            # public as any other. Both branches are taken, since which one
            # runs depends on an environment this tool is not in.
            for child in ast.iter_child_nodes(statement):
                if isinstance(child, ast.stmt):
                    self._top_level(child)
                elif isinstance(child, list):  # pragma: no cover - defensive
                    for item in child:
                        self._top_level(item)

    def _assign(self, statement: ast.Assign) -> None:
        for target in statement.targets:
            if isinstance(target, ast.Name):
                if target.id == "__all__":
                    self.dunder_all = _literal_names(statement.value)
                else:
                    self._attribute(target.id)

    def _attribute(self, name: str) -> None:
        qualname = f"{self.module}.{name}"
        self.symbols[qualname] = Symbol(qualname=qualname, kind="attribute")

    def _import_from(self, statement: ast.ImportFrom) -> None:
        if statement.level:
            # A relative import. Resolve it against this module's package so
            # that ``from ._client import Client`` inside ``pkg/__init__.py``
            # records ``pkg._client.Client`` and can be followed later.
            parts = self.module.split(".")
            package_parts = parts if self.is_package else parts[:-1]
            base = package_parts[: len(package_parts) - statement.level + 1]
            source = ".".join(base + ([statement.module] if statement.module else []))
        elif statement.module:
            source = statement.module
        else:  # pragma: no cover - syntactically impossible
            return
        for alias in statement.names:
            if alias.name == "*":
                continue
            local = alias.asname or alias.name
            self.aliases[local] = f"{source}.{alias.name}"

    def _function(self, node, prefix: str, *, kind: str, drop_first: bool) -> None:
        qualname = f"{prefix}.{node.name}"
        if _is_decorated_property(node):
            self.symbols[qualname] = Symbol(qualname=qualname, kind="attribute")
            return
        self.symbols[qualname] = Symbol(
            qualname=qualname,
            kind=kind,
            parameters=_parameters(node, drop_first=drop_first),
        )

    def _class(self, node: ast.ClassDef) -> None:
        qualname = f"{self.module}.{node.name}"
        constructor = None
        members: list = []
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if statement.name == "__init__":
                    constructor = statement
                members.append(statement)
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        member = f"{qualname}.{target.id}"
                        self.symbols[member] = Symbol(qualname=member, kind="attribute")
            elif isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                member = f"{qualname}.{statement.target.id}"
                self.symbols[member] = Symbol(member, kind="attribute")

        # A class is called to construct it, so its signature is __init__'s.
        self.symbols[qualname] = Symbol(
            qualname=qualname,
            kind="class",
            parameters=(
                _parameters(constructor, drop_first=True)
                if constructor is not None
                else None
            ),
        )
        for statement in members:
            # ``__init__`` is not published separately: the class symbol
            # already carries the constructor signature, and callers write
            # ``Client(...)`` rather than ``Client.__init__(...)``. Recording
            # both would report every constructor change twice.
            if statement.name == "__init__":
                continue
            self._function(
                statement,
                qualname,
                kind="method",
                drop_first=not _is_staticmethod(statement),
            )


def _literal_names(node) -> set[str] | None:
    """The strings in an ``__all__`` literal, if it is one."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    names = set()
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            names.add(element.value)
        else:
            # A computed __all__ cannot be trusted as the definitive list, so
            # fall back to the underscore rule rather than half-believe it.
            return None
    return names


def read_surface(root: pathlib.Path, package: str, version: str = "") -> Surface:
    """Read the public API of the package rooted at ``root``.

    ``root`` is the directory *containing* the package directory, the way a
    ``site-packages`` or an unpacked wheel is laid out.
    """
    surface = Surface(package=package, version=version)
    package_root = root / package.replace(".", "/")
    if not package_root.is_dir():
        single = root / f"{package}.py"
        if not single.is_file():
            return surface
        return _read_single_module(single, package, surface)

    readers: dict[str, _ModuleReader] = {}
    for path in sorted(package_root.rglob("*.py")):
        module = _module_name(root, path)
        if module is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError, OSError):
            # A module written for a newer Python than this one, or genuinely
            # broken. Recorded so the report can say the picture is partial.
            surface.unreadable.append(module)
            continue
        reader = _ModuleReader(module, is_package=path.name == "__init__.py")
        reader.visit(tree)
        readers[module] = reader

    _collect(surface, readers)
    return surface


def _read_single_module(path: pathlib.Path, package: str, surface: Surface) -> Surface:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        surface.unreadable.append(package)
        return surface
    reader = _ModuleReader(package)
    reader.visit(tree)
    _collect(surface, {package: reader})
    return surface


def _collect(surface: Surface, readers: dict[str, _ModuleReader]) -> None:
    """Fold every module's names into one surface, following re-exports."""
    for module, reader in readers.items():
        if not _module_is_public(module, surface.package):
            continue
        surface.symbols[module] = Symbol(qualname=module, kind="module")
        # ``__all__`` is not used to exclude anything. It governs
        # ``from pkg import *``, not attribute access, so a name left out of
        # it is still reachable and still breaks a caller when it disappears.
        # Leaving it in costs nothing either, because every symbol is later
        # intersected with what the caller's code actually touches -- an
        # over-generous surface gets filtered, a missing one is a silent
        # false negative. It does the one thing it is good for: promoting a
        # deliberately published name that happens to look private.
        exported = reader.dunder_all or set()
        for qualname, symbol in reader.symbols.items():
            local = qualname[len(module) + 1 :].split(".")
            if any(is_private(part) for part in local) and local[0] not in exported:
                continue
            surface.symbols[qualname] = symbol

    # Re-exports resolve to a fixed point rather than in one pass. A chain --
    # pkg/__init__ imports from pkg.mid, which imports from pkg._deep -- can
    # only be followed once the middle link is itself resolved, and the
    # modules are visited in whatever order the filesystem gave them. Looping
    # until nothing new appears removes the dependence on that order.
    for _ in range(_MAX_REEXPORT_DEPTH):
        added = False
        for module, reader in readers.items():
            if not _module_is_public(module, surface.package):
                continue
            exported = reader.dunder_all
            for local, source in reader.aliases.items():
                # A private-looking name listed in ``__all__`` was published
                # on purpose, so here __all__ does the one thing it is good
                # for: promoting, never excluding.
                if is_private(local) and (exported is None or local not in exported):
                    continue
                if (
                    not source.startswith(surface.package + ".")
                    and source != surface.package
                ):
                    # Imported from a third party. Whether that name breaks is
                    # that package's business, not this one's.
                    continue
                before = len(surface.symbols)
                _alias(surface, f"{module}.{local}", source, readers)
                added = added or len(surface.symbols) != before
        if not added:
            break


def _alias(
    surface: Surface,
    alias_name: str,
    source: str,
    readers: dict[str, _ModuleReader],
    depth: int = 0,
) -> None:
    """Publish ``alias_name`` with whatever ``source`` turned out to be."""
    if depth > _MAX_REEXPORT_DEPTH or alias_name in surface.symbols:
        # A chain of re-exports through several layers is normal; a cycle is
        # not, and the depth cap is what makes the difference survivable.
        return
    target = None
    for reader in readers.values():
        if source in reader.symbols:
            target = reader.symbols[source]
            break
    if target is None:
        # An alias already resolved on an earlier pass counts as a definition
        # for anything pointing at it, which is what lets chains converge.
        target = surface.symbols.get(source)
    if target is None:
        if source in readers:  # a module re-exported as a name
            surface.symbols[alias_name] = Symbol(
                qualname=alias_name, kind="module", reexported=True
            )
        return
    surface.symbols[alias_name] = Symbol(
        qualname=alias_name,
        kind=target.kind,
        parameters=target.parameters,
        reexported=True,
    )
    # Members come along with a re-exported class: if pkg.Client is really
    # pkg._client.Client, then pkg.Client.get has to exist too, or every call
    # through the public name would look like a removal.
    prefix = source + "."
    for reader in readers.values():
        for qualname, symbol in reader.symbols.items():
            if qualname.startswith(prefix):
                member = alias_name + "." + qualname[len(prefix) :]
                surface.symbols.setdefault(
                    member,
                    Symbol(
                        qualname=member,
                        kind=symbol.kind,
                        parameters=symbol.parameters,
                        reexported=True,
                    ),
                )


def _module_is_public(module: str, package: str) -> bool:
    parts = module.split(".")
    return not any(is_private(part) for part in parts[1:]) or parts[0] != package
