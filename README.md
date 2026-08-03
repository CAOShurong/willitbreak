# willitbreak

**Will this dependency upgrade break code you actually wrote?**

[![CI](https://github.com/CAOShurong/willitbreak/actions/workflows/ci.yml/badge.svg)](https://github.com/CAOShurong/willitbreak/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/willitbreak.svg)](https://pypi.org/project/willitbreak/)
[![Python](https://img.shields.io/pypi/pyversions/willitbreak.svg)](https://pypi.org/project/willitbreak/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Dependabot opens eleven pull requests. The changelog for one of them lists
forty breaking changes. Thirty-nine are about parts of the library you have
never imported, and the fortieth is on line 218 of a file you forgot existed.

There is no good way to find that out today. So people merge and hope, or read
the whole changelog, or upgrade in a branch and wait for the test suite to
maybe catch it.

```bash
pip install willitbreak
willitbreak urllib3 --to 2.2.1
```

![report](https://raw.githubusercontent.com/CAOShurong/willitbreak/main/docs/report.png)

284 API changes in that release. Two of them touch this code. Those two come
with file and line numbers; the rest are a number you can ignore.

No dependencies. Python 3.9+.

## Why this isn't just a changelog

Every other tool in this space is built for the people *publishing* a library,
so it can tell you what changed but not whether you used any of it.
[griffe](https://mkdocstrings.github.io/griffe/) — the most capable of them —
puts it plainly in its own documentation: users must review the reported
breakages and assess their own codebase for impacts.

That assessment is the work. This does it.

```text
their API diff          your source            what you get
──────────────────      ─────────────────      ─────────────────────
284 things changed  ×   217 call sites     =   2 problems, with line numbers
```

## What it takes seriously

**Never guessing.** A checker that cries wolf gets uninstalled after the
second false alarm, and then catches nothing at all. So resolution stops the
moment it would have to assume:

```python
client = pkg.Client()
client.get(timeout=5)  # resolved: pkg.Client.get

thing = make_it()
thing.get(timeout=5)  # not reported — origin unprovable

c = pkg.Client()
c = something_else()
c.get(timeout=5)  # not reported — the name was rebound
```

The same applies to arguments. `f(**options)` might be passing the parameter
that was removed, or might not, so it is never claimed as a break.

**The condition attached to every change.** A removed keyword only matters to
code that passes it. A parameter that lost its default only matters to code
that *omits* it — pass it positionally and you are fine. A parameter that
became keyword-only only breaks callers who passed it positionally. Each of
those is checked against the actual call:

```text
urllib3 1.26.18 → 2.2.1
  1 file scanned · 284 API changes that do not touch your code

  BREAKS urllib3.HTTPResponse
      parameter 'strict' was removed
      • docs/example/client.py:18  2 positional  strict=

  BREAKS urllib3.HTTPResponse.from_httplib
      method no longer exists
      • docs/example/client.py:22  1 positional

2 breaking changes across 2 call sites
```

**Where libraries really live.** Almost every package implements in
`pkg/_client.py` and exposes in `pkg/__init__.py`. A tool that only looked at
where a class was defined would report `pkg._client.Client` and never match
the `pkg.Client` everyone writes, so re-export chains are followed to a fixed
point.

**Not importing anything.** Both versions are downloaded and read as source.
Importing a package to inspect it means executing it, and the version you are
asking about is by definition not the one installed — it may not even import
on your interpreter.

## In CI

```yaml
- run: pip install willitbreak
- run: willitbreak urllib3 --to ${{ matrix.candidate }} src/
```

Exit `2` means this upgrade breaks something you wrote. Exit `1` is reserved
for the tool itself failing, so a pipeline can tell those apart and act
differently on each. Exit `0` means the changes do not reach your code.

## Isn't this what mypy does?

Partly, and it is worth being honest about the overlap. If a package ships
type information, you install the new version, and your code is annotated,
mypy will flag a removed attribute.

The differences that matter:

- **You have to install the upgrade first.** That is the thing you were trying
  to evaluate, and on a conflict it may not install at all.
- **It reports everything, not what changed.** A pre-existing error and one
  introduced by this upgrade look identical.
- **It needs types.** Untyped packages, and `**kwargs`-heavy APIs, are exactly
  where signature changes hide.

This answers a narrower question — *what does this specific version bump do to
me* — without touching your environment.

## Options

| Flag | What it does |
|---|---|
| `--from VERSION` | Upgrade from this (default: the version installed here) |
| `--to VERSION` | Upgrade to this (default: latest on PyPI) |
| `--all` | List every API change, including ones you never touch |
| `--json` | Machine-readable, for a bot that files the summary |
| `--import-name NAME` | When the import name differs from the package name |
| `--cache DIR` | Where downloaded versions live (`WILLITBREAK_CACHE`) |
| `--ascii` | No non-ASCII characters |
| `--color` | `auto`, `always`, `never`. `NO_COLOR` is honoured |

Paths default to the current directory:

```bash
willitbreak httpx --to 0.28.0 src/ tests/
```

## As a library

```python
from willitbreak import check

outcome = check("urllib3", "1.26.18", "2.2.1", ["src"])
for finding in outcome.breaking:
    print(finding.change.detail)
    for reference in finding.references:
        print("   ", reference.source)
```

## Honest limits

- Resolution is one level deep. An instance stored on `self`, handed through a
  factory, or returned from a helper is not tracked, and is silently skipped
  rather than guessed at.
- A package whose API is built at runtime cannot be read from source. Modules
  that will not parse are reported, never quietly dropped.
- Changed *behaviour* behind an unchanged signature is invisible to this and
  to every other static tool. Read the changelog for those.

## Development

```bash
git clone https://github.com/CAOShurong/willitbreak
cd willitbreak
python -m unittest discover -s tests
```

The suite runs entirely offline against packages built on disk, so it is fast
and does not depend on PyPI being up. Regenerate this README, which is
produced by running the tool for real:

```bash
python docs/build_docs.py
python docs/build_docs.py --check   # what CI runs
```

CI runs on Ubuntu, Windows and macOS across Python 3.9–3.13, checks this
README still matches the output, and verifies the exit codes.

## License

MIT. See [LICENSE](LICENSE).
