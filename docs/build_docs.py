#!/usr/bin/env python3
"""Generate README.md, figures included, by running the tool for real.

The demo is a genuine upgrade -- urllib3 1.26 to 2.x, one of the more
disruptive releases in the ecosystem -- run against a small example file. That
means the numbers in the README are the tool's actual output, and CI fails if
they drift.

    python docs/build_docs.py            # regenerate
    python docs/build_docs.py --check    # fail if it would change (for CI)

Needs the network the first time, then reads the version cache. Images need
Pillow, which is not a runtime dependency:

    python -m pip install pillow
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "readme_template.md"
README = ROOT / "README.md"
EXAMPLE = "docs/example"

#: The upgrade being demonstrated. Pinned so the figures are reproducible.
PACKAGE = "urllib3"
OLD = "1.26.18"
NEW = "2.2.1"

SGR = re.compile(r"\x1b\[([0-9;]*)m")
BACKGROUND = "#161719"
DEFAULT_INK = "#c8c9c4"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\cour.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

FIGURES = {
    "<!--SHOT_REPORT-->": ("report", [PACKAGE, "--from", OLD, "--to", NEW, EXAMPLE]),
}

BLOCKS = {
    "<!--TEXT_REPORT-->": [PACKAGE, "--from", OLD, "--to", NEW, EXAMPLE],
}


def run_tool(arguments: list[str], *, colour: bool) -> str:
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "willitbreak",
        *arguments,
        "--color",
        "always" if colour else "never",
    ]
    merged = dict(os.environ)
    merged["PYTHONPATH"] = str(ROOT / "src")
    merged["PYTHONIOENCODING"] = "utf-8"
    merged.pop("NO_COLOR", None)
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
    )
    # Exit 2 means the demo upgrade breaks the demo code, which is the whole
    # point of the figure rather than a build failure.
    if result.returncode not in (0, 2):
        raise SystemExit(
            f"willitbreak {' '.join(arguments)} failed ({result.returncode}):\n"
            f"{result.stderr}"
        )
    return result.stdout.rstrip("\n")


def load_font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        candidate = pathlib.Path(path)
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:  # pragma: no cover - broken font file
                continue
    raise SystemExit("no monospace font found; edit FONT_CANDIDATES")


def parse_sgr(line: str) -> list[tuple[str, str]]:
    """Split an ANSI line into (text, colour) runs."""
    runs: list[tuple[str, str]] = []
    colour = DEFAULT_INK
    position = 0
    for match in SGR.finditer(line):
        if match.start() > position:
            runs.append((line[position : match.start()], colour))
        codes = [c for c in match.group(1).split(";") if c]
        if not codes or codes == ["0"]:
            colour = DEFAULT_INK
        elif codes[0] == "38" and len(codes) >= 3 and codes[1] == "5":
            colour = _from_256(int(codes[2]))
        position = match.end()
    if position < len(line):
        runs.append((line[position:], colour))
    return runs


def _from_256(index: int) -> str:
    """Convert an xterm-256 index to a hex colour.

    The report emits 256-colour codes because they work on far more terminals
    than truecolor; the figure has to render the same palette a user sees.
    """
    if index < 16:  # pragma: no cover - the report does not emit these
        return DEFAULT_INK
    if index < 232:
        index -= 16
        levels = (0, 95, 135, 175, 215, 255)
        red = levels[index // 36]
        green = levels[(index % 36) // 6]
        blue = levels[index % 6]
        return f"#{red:02x}{green:02x}{blue:02x}"
    grey = 8 + (index - 232) * 10
    return f"#{grey:02x}{grey:02x}{grey:02x}"


def render_png(text: str, out: pathlib.Path, *, font_size: int = 15) -> None:
    from PIL import Image, ImageDraw

    font = load_font(font_size)
    advance = font.getlength("M")
    line_height = int(font_size * 1.45)
    pad = 18

    lines = text.split("\n")
    columns = max((len(SGR.sub("", line)) for line in lines), default=1)
    width = int(columns * advance) + pad * 2
    height = line_height * len(lines) + pad * 2

    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for row, line in enumerate(lines):
        x = float(pad)
        y = pad + row * line_height
        for chunk, colour in parse_sgr(line):
            draw.text((x, y), chunk, font=font, fill=colour)
            x += font.getlength(chunk)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, optimize=True)


def build(check: bool) -> int:
    if not TEMPLATE.exists():
        raise SystemExit(f"missing template: {TEMPLATE}")
    text = TEMPLATE.read_text(encoding="utf-8")

    for placeholder, (stem, arguments) in FIGURES.items():
        if placeholder not in text:
            raise SystemExit(f"template has no {placeholder}")
        captured = run_tool(arguments, colour=True)
        if not check:
            render_png(captured, ROOT / "docs" / f"{stem}.png")
        url = f"https://raw.githubusercontent.com/CAOShurong/willitbreak/main/docs/{stem}.png"
        text = text.replace(placeholder, f"![{stem}]({url})")

    for placeholder, arguments in BLOCKS.items():
        if placeholder not in text:
            raise SystemExit(f"template has no {placeholder}")
        captured = run_tool(arguments, colour=False)
        text = text.replace(placeholder, f"```text\n{captured}\n```")

    if check:
        current = README.read_text(encoding="utf-8") if README.exists() else ""
        if current != text:
            print(
                "README.md is out of date. Run:\n\n    python docs/build_docs.py\n",
                file=sys.stderr,
            )
            return 1
        print("README.md is current")
        return 0

    README.write_text(text, encoding="utf-8", newline="\n")
    print(f"README.md: {len(text.splitlines())} lines")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if it would change")
    return build(parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
