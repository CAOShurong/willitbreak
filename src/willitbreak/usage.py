"""Where your code touches somebody else's package.

This is the half nothing else does. Comparing two versions of a library tells
you what its authors changed; it does not tell you whether *you* used any of
it. A large release changes hundreds of things and touches almost none of any
given codebase, so the changelog is mostly noise to any particular reader.

The resolver turns each reference into a fully qualified name -- ``r.Session``
becomes ``requests.Session`` -- and records the arguments at every call, so a
removed keyword can be matched against the calls that actually pass it.

The governing rule is the same one that governs any linter people keep
installed: **never guess**. A tool that cries wolf about a call that was fine
gets uninstalled after the second false alarm, and then catches nothing at
all. So resolution stops the moment it would have to assume something, and a
reference that cannot be pinned down is simply not reported.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

__all__ = ["Reference", "scan_paths", "scan_source"]

#: Directories that are never the caller's own code.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "build",
    "dist",
    "site-packages",
}


@dataclass(frozen=True)
class Reference:
    """One place the caller's code touches the package."""

    qualname: str
    path: str
    lineno: int
    col: int
    #: True when this reference is a call, as opposed to a mention.
    called: bool = False
    #: Keywords passed at this call site.
    keywords: frozenset[str] = frozenset()
    #: Positional arguments passed. ``None`` when a ``*args`` splat makes the
    #: count unknowable -- which must not be read as zero.
    positional: int | None = 0
    #: True when the call splats ``**kwargs``, so the keywords are unknown.
    splatted_keywords: bool = False

    @property
    def source(self) -> str:
        return f"{self.path}:{self.lineno}"

    def as_dict(self) -> dict:
        return {
            "qualname": self.qualname,
            "path": self.path,
            "lineno": self.lineno,
            "col": self.col,
            "called": self.called,
            "keywords": sorted(self.keywords),
            "positional": self.positional,
            "splatted_keywords": self.splatted_keywords,
        }


@dataclass
class _Scope:
    """Names bound in one function or module body.

    Only one level of assignment is tracked: ``client = pkg.Client()`` makes
    ``client`` an instance of ``pkg.Client``, so ``client.get(...)`` resolves.
    Anything deeper -- an instance stored on self, passed through a factory,
    returned from a helper -- would need real type inference, and guessing
    there is how a checker starts reporting things that are not true.
    """

    #: local name -> qualified name of a module or class
    names: dict[str, str] = field(default_factory=dict)
    #: local name -> qualified class it is an instance of
    instances: dict[str, str] = field(default_factory=dict)
    #: Names rebound in a way that makes them untrustworthy.
    poisoned: set[str] = field(default_factory=set)

    def child(self) -> _Scope:
        return _Scope(
            names=dict(self.names),
            instances=dict(self.instances),
            poisoned=set(self.poisoned),
        )


class _Resolver(ast.NodeVisitor):
    def __init__(self, package: str, path: str) -> None:
        self.package = package
        self.path = path
        self.references: list[Reference] = []
        self.scope = _Scope()

    # -- imports -----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root != self.package:
                continue
            if alias.asname:
                # ``import pkg.sub as s`` binds s to pkg.sub.
                self.scope.names[alias.asname] = alias.name
            else:
                # ``import pkg.sub`` binds only ``pkg``.
                self.scope.names[root] = root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Relative imports point inside the caller's own project, never at the
        # package under test.
        if node.level or not node.module:
            self.generic_visit(node)
            return
        root = node.module.split(".")[0]
        if root != self.package:
            self.generic_visit(node)
            return
        for alias in node.names:
            if alias.name == "*":
                # Star imports make every bare name ambiguous. Rather than
                # guess which ones came from the package, record nothing --
                # the report says the file was skipped instead of quietly
                # under-reporting.
                continue
            local = alias.asname or alias.name
            self.scope.names[local] = f"{node.module}.{alias.name}"
            self.references.append(
                Reference(
                    qualname=f"{node.module}.{alias.name}",
                    path=self.path,
                    lineno=node.lineno,
                    col=node.col_offset,
                )
            )
        self.generic_visit(node)

    # -- scopes ------------------------------------------------------------

    def _visit_scoped(self, node) -> None:
        outer = self.scope
        self.scope = outer.child()
        # Parameters shadow anything bound outside.
        args = node.args
        for arg in [
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
            *(a for a in (args.vararg, args.kwarg) if a),
        ]:
            self.scope.names.pop(arg.arg, None)
            self.scope.instances.pop(arg.arg, None)
            self.scope.poisoned.add(arg.arg)
        for statement in node.body:
            self.visit(statement)
        self.scope = outer

    visit_FunctionDef = _visit_scoped
    visit_AsyncFunctionDef = _visit_scoped

    # -- assignment --------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        qualified = self._qualname(node.value)
        constructed = (
            self._qualname(node.value.func)
            if isinstance(node.value, ast.Call)
            else None
        )
        for target in node.targets:
            if not isinstance(target, ast.Name):
                self.generic_visit(target)
                continue
            name = target.id
            self.scope.names.pop(name, None)
            self.scope.instances.pop(name, None)
            if constructed is not None:
                self.scope.instances[name] = constructed
            elif qualified is not None:
                self.scope.names[name] = qualified
            else:
                # Rebound to something unknown. Anything through this name
                # from here on is unresolvable, and saying so is the point.
                self.scope.poisoned.add(name)

    # -- uses --------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        qualified = self._qualname(node.func)
        if qualified is not None:
            keywords = {k.arg for k in node.keywords if k.arg is not None}
            splatted = any(k.arg is None for k in node.keywords)
            positional: int | None = sum(
                1 for a in node.args if not isinstance(a, ast.Starred)
            )
            if any(isinstance(a, ast.Starred) for a in node.args):
                positional = None
            self.references.append(
                Reference(
                    qualname=qualified,
                    path=self.path,
                    lineno=node.lineno,
                    col=node.col_offset,
                    called=True,
                    keywords=frozenset(keywords),
                    positional=positional,
                    splatted_keywords=splatted,
                )
            )
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)
        # The callee itself is walked so that ``pkg.a(pkg.b())`` records both,
        # but not re-recorded as a bare attribute reference.
        if isinstance(node.func, ast.Attribute):
            self.visit(node.func.value)
        elif not isinstance(node.func, ast.Name):
            self.visit(node.func)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        qualified = self._qualname(node)
        if qualified is not None:
            self.references.append(
                Reference(
                    qualname=qualified,
                    path=self.path,
                    lineno=node.lineno,
                    col=node.col_offset,
                )
            )
            return
        self.visit(node.value)

    # -- resolution --------------------------------------------------------

    def _qualname(self, node) -> str | None:
        """Fully qualified name for an expression, or ``None`` if unprovable."""
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        base = current.id
        if base in self.scope.poisoned:
            return None
        parts.reverse()

        if base in self.scope.instances:
            # ``client = pkg.Client()`` then ``client.get`` -> pkg.Client.get
            root = self.scope.instances[base]
            return ".".join([root, *parts]) if parts else None
        if base in self.scope.names:
            root = self.scope.names[base]
            return ".".join([root, *parts]) if parts else root
        return None


def scan_source(source: str, package: str, path: str = "<string>") -> list[Reference]:
    """Every provable reference to ``package`` in one file's source."""
    tree = ast.parse(source, filename=path)
    resolver = _Resolver(package, path)
    resolver.visit(tree)
    return resolver.references


@dataclass
class ScanResult:
    references: list[Reference] = field(default_factory=list)
    files_scanned: int = 0
    #: Files that would not parse. Reported, never silently dropped: a file
    #: this tool could not read is a file it cannot vouch for.
    unparsed: list[tuple[str, str]] = field(default_factory=list)

    def for_qualname(self, qualname: str) -> list[Reference]:
        return [r for r in self.references if r.qualname == qualname]


def scan_paths(
    paths: list[pathlib.Path],
    package: str,
    *,
    root: pathlib.Path | None = None,
) -> ScanResult:
    """Scan files and directories for references to ``package``."""
    result = ScanResult()
    root = root or pathlib.Path.cwd()
    for target in paths:
        for path in _python_files(target):
            try:
                display = str(path.relative_to(root))
            except ValueError:
                display = str(path)
            display = display.replace("\\", "/")
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # pragma: no cover - permissions
                result.unparsed.append((display, str(exc)))
                continue
            try:
                result.references.extend(scan_source(source, package, display))
            except SyntaxError as exc:
                result.unparsed.append((display, f"line {exc.lineno}: {exc.msg}"))
                continue
            result.files_scanned += 1
    return result


def _python_files(target: pathlib.Path):
    if target.is_file():
        if target.suffix == ".py":
            yield target
        return
    for path in sorted(target.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path
