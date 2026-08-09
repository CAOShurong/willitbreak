"""Getting two versions of a package onto disk, without installing either.

Installing the version you are considering, in order to find out whether you
can install it, is the problem rather than the solution: it breaks the working
environment you are trying to protect, and on a conflict it may not install at
all. So both versions are downloaded and unpacked into a cache and read as
plain files. Nothing is imported and nothing is executed.

Everything here is standard library. A tool you reach for when a dependency
upgrade looks risky should not itself drag in dependencies that might need
upgrading.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

__all__ = [
    "FetchError",
    "cache_root",
    "fetch_version",
    "installed_version",
    "latest_version",
]

PYPI = "https://pypi.org/pypi"
USER_AGENT = "willitbreak (+https://github.com/CAOShurong/willitbreak)"
TIMEOUT = 30


class FetchError(Exception):
    """A version could not be obtained."""


@dataclass
class Fetched:
    """An unpacked version, ready to be read."""

    package: str
    version: str
    #: Directory that *contains* the importable package directory, laid out
    #: the way site-packages is.
    root: pathlib.Path
    #: The name you import, which is not always the name you install.
    import_name: str


def cache_root() -> pathlib.Path:
    """Where unpacked versions live between runs.

    Honours the usual environment overrides so a CI job can point it at a
    cached directory and stop re-downloading the same wheels every build.
    """
    override = os.environ.get("WILLITBREAK_CACHE")
    if override:
        return pathlib.Path(override)
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return pathlib.Path(base) / "willitbreak"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return pathlib.Path(local) / "willitbreak" / "cache"
    return pathlib.Path.home() / ".cache" / "willitbreak"


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FetchError(f"not found on PyPI: {url.rsplit('/', 2)[-2]}") from exc
        raise FetchError(f"PyPI returned {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"could not reach PyPI: {exc}") from exc


def latest_version(package: str) -> str:
    data = _get_json(f"{PYPI}/{package}/json")
    version = data.get("info", {}).get("version")
    if not version:
        raise FetchError(f"PyPI gave no version for {package}")
    return str(version)


def installed_version(package: str) -> str | None:
    """The version installed in the current environment, if any."""
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - Python < 3.8
        return None
    try:
        return metadata.version(package)
    except Exception:
        return None


def _release_files(package: str, version: str) -> list[dict]:
    data = _get_json(f"{PYPI}/{package}/{version}/json")
    files = data.get("urls") or []
    if not files:
        raise FetchError(f"{package} {version} has no files on PyPI")
    return files


def _pick_file(files: list[dict]) -> dict:
    """Prefer a pure-Python wheel, then any wheel, then the sdist.

    A universal wheel is smallest and always contains the sources this tool
    reads. Platform wheels are equivalent for the purpose, and an sdist is the
    last resort because its layout varies.
    """
    wheels = [f for f in files if f.get("packagetype") == "bdist_wheel"]
    for candidate in wheels:
        name = candidate.get("filename", "")
        if name.endswith("-none-any.whl"):
            return candidate
    if wheels:
        return wheels[0]
    for candidate in files:
        if candidate.get("packagetype") == "sdist":
            return candidate
    raise FetchError("no wheel or sdist available")


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"download failed: {exc}") from exc


def _archive_target(destination: pathlib.Path, member: str) -> pathlib.Path:
    """Resolve one archive member inside the exact destination directory.

    A string-prefix check is not a directory-boundary check: a sibling named
    ``package-escape`` starts with the path to ``package``.  ``relative_to``
    compares path components instead, so absolute paths, ``..`` traversal,
    and same-prefix siblings are all rejected.
    """
    root = destination.resolve()
    target = (root / member).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise FetchError(f"archive entry escapes its directory: {member}") from exc
    return target


def _safe_extract_zip(data: bytes, destination: pathlib.Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            # An archive entry that escapes the destination is either malice
            # or corruption; either way it does not get written.
            target = _archive_target(destination, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)


def _safe_extract_tar(data: bytes, destination: pathlib.Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive.getmembers():
            target = _archive_target(destination, member.name)
            # PyPI packages only need regular files and directories.  Links,
            # devices, and FIFOs can redirect writes or create host objects
            # that have no place in a source archive.
            if not (member.isfile() or member.isdir()):
                raise FetchError(
                    f"archive contains an unsupported entry: {member.name}"
                )
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = archive.extractfile(member)
            if source is None:  # Defensive: every regular file should open.
                raise FetchError(f"could not read archive entry: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)


def _find_import_root(
    extracted: pathlib.Path, package: str
) -> tuple[pathlib.Path, str]:
    """Locate the importable package inside an unpacked archive.

    The name you ``pip install`` is often not the name you ``import`` --
    ``python-dateutil`` gives ``dateutil``, ``PyYAML`` gives ``yaml`` -- so
    the layout is inspected rather than assumed.
    """
    normalised = package.replace("-", "_").lower()

    candidates = [extracted]
    # An sdist wraps everything in ``name-version/``, sometimes with the
    # sources under ``src/``.
    for child in sorted(extracted.iterdir()):
        if child.is_dir():
            candidates.append(child)
            source_dir = child / "src"
            if source_dir.is_dir():
                candidates.append(source_dir)
    source_dir = extracted / "src"
    if source_dir.is_dir():
        candidates.append(source_dir)

    for root in candidates:
        if not root.is_dir():
            continue
        # An exact match on the normalised name wins outright.
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not (child / "__init__.py").is_file():
                continue
            if child.name.replace("-", "_").lower() == normalised:
                return root, child.name
        single = root / f"{normalised}.py"
        if single.is_file():
            return root, normalised

    # Fall back to whatever top-level package the archive does contain,
    # ignoring the metadata and test directories that are not the library.
    for root in candidates:
        if not root.is_dir():
            continue
        packages = [
            child.name
            for child in sorted(root.iterdir())
            if child.is_dir()
            and (child / "__init__.py").is_file()
            and not child.name.endswith((".dist-info", ".data", ".egg-info"))
            and child.name not in ("tests", "test", "docs", "examples")
        ]
        if len(packages) == 1:
            return root, packages[0]
        if packages:
            raise FetchError(
                f"{package} ships several top-level modules "
                f"({', '.join(packages)}); pass --import-name to choose one"
            )

    raise FetchError(f"could not find an importable package inside {package}")


def fetch_version(
    package: str,
    version: str,
    *,
    cache: pathlib.Path | None = None,
    import_name: str | None = None,
) -> Fetched:
    """Download and unpack one version, reusing the cache when possible."""
    cache = cache or cache_root()
    destination = cache / f"{package}-{version}"
    marker = destination / ".willitbreak-complete"

    if not marker.is_file():
        # A half-written cache entry from an interrupted run would read as a
        # package with most of its API missing, which is exactly the wrong
        # answer, so the marker is written last and its absence means redo.
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        destination.mkdir(parents=True, exist_ok=True)
        chosen = _pick_file(_release_files(package, version))
        data = _download(chosen["url"])
        filename = chosen.get("filename", "")
        try:
            if filename.endswith(".whl") or filename.endswith(".zip"):
                _safe_extract_zip(data, destination)
            else:
                _safe_extract_tar(data, destination)
        except (zipfile.BadZipFile, tarfile.TarError) as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise FetchError(
                f"{package} {version} is not a readable archive: {exc}"
            ) from exc
        marker.write_text(filename, encoding="utf-8")

    root, found = _find_import_root(destination, package)
    return Fetched(
        package=package,
        version=version,
        root=root,
        import_name=import_name or found,
    )
