"""Rendering and CLI-surface tests.

Offline throughout: everything here works on packages built on disk, so the
suite is fast and does not go red when PyPI has a bad day.
"""

from __future__ import annotations

import io
import re
import unittest
from dataclasses import replace

from support import PackageCase

from willitbreak.cli import build_parser
from willitbreak.report import Palette, render

ANSI = re.compile(r"\x1b\[[0-9;]*m")

BEFORE = {"__init__": "def f(a, timeout=1):\n    pass\n"}
AFTER = {"__init__": "def f(a):\n    pass\n"}
CODE = "import pkg\npkg.f(1, timeout=2)\n"


class TestRender(PackageCase):
    def outcome_with_break(self):
        return self.outcome(BEFORE, AFTER, CODE)

    def test_the_break_and_its_location_are_both_shown(self):
        text = render(self.outcome_with_break(), Palette("never"))
        self.assertIn("BREAKS", text)
        self.assertIn("pkg.f", text)
        self.assertIn("app.py:2", text)
        self.assertIn("timeout", text)

    def test_a_clean_upgrade_says_so_rather_than_printing_nothing(self):
        outcome = self.outcome(BEFORE, BEFORE, CODE)
        text = render(outcome, Palette("never"))
        self.assertIn("nothing in your code is affected", text)

    def test_untouched_changes_are_counted_not_listed(self):
        outcome = self.outcome(BEFORE, AFTER, "import pkg\npkg.f(1)\n")
        text = render(outcome, Palette("never"))
        self.assertIn("do not touch your code", text)
        self.assertNotIn("BREAKS", text)

    def test_ascii_mode_emits_only_ascii(self):
        text = render(self.outcome_with_break(), Palette("never"), ascii_only=True)
        text.encode("ascii", errors="strict")

    def test_ascii_mode_escapes_a_unicode_source_path(self):
        outcome = self.outcome_with_break()
        reference = outcome.findings[0].references[0]
        outcome.findings[0].references[0] = replace(reference, path="目录/调用_Ω.py")

        text = render(outcome, Palette("never"), ascii_only=True)

        text.encode("ascii", errors="strict")
        self.assertIn(r"\u76ee\u5f55/\u8c03\u7528_\u03a9.py:2", text)

    def test_unicode_mode_uses_the_arrow(self):
        self.assertIn("→", render(self.outcome_with_break(), Palette("never")))

    def test_colour_never_leaves_escapes(self):
        text = render(self.outcome_with_break(), Palette("never"))
        self.assertEqual(text, ANSI.sub("", text))

    def test_colour_does_not_change_the_visible_text(self):
        outcome = self.outcome_with_break()
        plain = render(outcome, Palette("never"))
        painted = render(outcome, Palette("always"))
        self.assertNotEqual(plain, painted)
        self.assertEqual(ANSI.sub("", painted), plain)

    def test_the_call_shape_is_shown(self):
        text = render(self.outcome_with_break(), Palette("never"))
        self.assertIn("1 positional", text)
        self.assertIn("timeout=", text)

    def test_a_pipe_gets_no_colour(self):
        self.assertFalse(Palette("auto", io.StringIO()).enabled)

    def test_singular_and_plural_agree(self):
        text = render(self.outcome_with_break(), Palette("never"))
        self.assertIn("1 file scanned", text)
        self.assertIn("1 breaking change across 1 call site", text)


class TestParser(unittest.TestCase):
    def test_the_package_is_required(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])

    def test_paths_default_to_empty_so_the_cli_can_choose(self):
        args = build_parser().parse_args(["requests"])
        self.assertEqual(args.paths, [])
        self.assertIsNone(args.old)
        self.assertIsNone(args.new)

    def test_versions_and_paths_parse_together(self):
        # An option between two positionals is the invocation everyone types
        # first, and plain parse_args cannot match it before Python 3.12.
        args = build_parser().parse_intermixed_args(
            ["requests", "--from", "1.0", "--to", "2.0", "src", "tests"]
        )
        self.assertEqual(args.old, "1.0")
        self.assertEqual(args.new, "2.0")
        self.assertEqual([str(p) for p in args.paths], ["src", "tests"])

    def test_paths_before_the_options_also_parse(self):
        args = build_parser().parse_intermixed_args(["requests", "src", "--to", "2.0"])
        self.assertEqual([str(p) for p in args.paths], ["src"])
        self.assertEqual(args.new, "2.0")

    def test_an_unknown_colour_choice_is_rejected(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["requests", "--color", "rainbow"])


if __name__ == "__main__":
    unittest.main()
