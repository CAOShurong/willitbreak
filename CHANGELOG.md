# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2026-08-12

### Fixed

- Make `--ascii` escape non-ASCII text in source paths and other report
  content, so the option now guarantees an entirely ASCII terminal report.
  Normal terminal and JSON output continue to preserve Unicode.

## [0.1.2] - 2026-08-11

### Security

- Validate distribution names against the Python packaging name specification
  and prove each cache destination remains inside the selected cache before
  deleting or creating it. A crafted project name or version can no longer
  remove a same-named directory outside the cache.
- Pin every third-party GitHub Action to an immutable commit and enable weekly
  Dependabot checks for Action updates.

### Changed

- Test Python 3.14 on Linux, Windows, macOS, and in the release gate.
- Publish a SHA-256 manifest and GitHub build-provenance attestations with each
  GitHub release.

## [0.1.1] - 2026-08-09

### Security

- Require every downloaded ZIP or source-distribution member to remain inside
  the exact cache destination by path component, preventing a crafted archive
  from writing into a same-prefix sibling directory.
- Reject non-file and non-directory tar members, including links, devices, and
  FIFOs, before extraction.

## [0.1.0] - 2026-08-03

First release.

### Added

- Public API extraction from source, without importing anything: modules,
  classes, functions, methods, signatures, and re-export chains resolved to a
  fixed point.
- A diff that carries the condition under which a caller is affected, rather
  than a flat list of changes.
- Call-site resolution in the caller's own code: import aliases, one level of
  instance tracking, keyword and positional argument shapes.
- Intersection of the two, so only changes landing on a real line are
  reported, with file and line numbers.
- Both versions downloaded from PyPI and cached; nothing is installed and
  nothing is executed.
- `--from`, `--to`, `--all`, `--json`, `--import-name`, `--cache`, `--ascii`,
  and colour handling that honours `NO_COLOR`.
- Exit code 2 for a breaking upgrade, 1 for the tool itself failing.

[0.1.3]: https://github.com/CAOShurong/willitbreak/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/CAOShurong/willitbreak/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/CAOShurong/willitbreak/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/CAOShurong/willitbreak/releases/tag/v0.1.0
