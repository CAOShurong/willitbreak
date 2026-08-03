# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/CAOShurong/willitbreak/releases/tag/v0.1.0
