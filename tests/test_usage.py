"""Resolver tests.

Most of this file is about what the resolver must *refuse* to say. A tool
that reports a break in code that is fine gets uninstalled after the second
false alarm, and then catches nothing at all -- so silence on anything
unprovable is the feature, not a limitation to be worked around.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from willitbreak.usage import scan_paths, scan_source


def names(code: str, package: str = "pkg") -> list[str]:
    return [r.qualname for r in scan_source(code, package)]


def calls(code: str, package: str = "pkg") -> dict[str, object]:
    return {r.qualname: r for r in scan_source(code, package) if r.called}


class TestResolution(unittest.TestCase):
    def test_plain_import(self):
        self.assertIn("pkg.f", names("import pkg\npkg.f()\n"))

    def test_aliased_import(self):
        self.assertIn("pkg.f", names("import pkg as p\np.f()\n"))

    def test_submodule_import_with_alias(self):
        self.assertIn("pkg.sub.f", names("import pkg.sub as s\ns.f()\n"))

    def test_from_import(self):
        self.assertIn("pkg.f", names("from pkg import f\nf()\n"))

    def test_from_import_with_alias(self):
        self.assertIn("pkg.f", names("from pkg import f as g\ng()\n"))

    def test_nested_attribute_chain(self):
        self.assertIn("pkg.a.b.c", names("import pkg\npkg.a.b.c()\n"))

    def test_the_import_itself_counts_as_a_reference(self):
        # `from pkg import gone` breaks at import time, before any call.
        self.assertIn("pkg.gone", names("from pkg import gone\n"))

    def test_an_instance_resolves_one_level(self):
        code = "from pkg import Client\nc = Client()\nc.get(1)\n"
        self.assertIn("pkg.Client.get", names(code))

    def test_an_instance_of_a_dotted_class(self):
        code = "import pkg\nc = pkg.sub.Client()\nc.get(1)\n"
        self.assertIn("pkg.sub.Client.get", names(code))


class TestRefusals(unittest.TestCase):
    def test_an_object_of_unknown_origin_is_not_attributed(self):
        code = "import pkg\nthing = make_it()\nthing.get(1)\n"
        self.assertNotIn("pkg.get", names(code))
        self.assertEqual([n for n in names(code) if n.endswith(".get")], [])

    def test_rebinding_poisons_the_name(self):
        # The name meant something from the package, then stopped. Anything
        # after that point is not the package's.
        code = (
            "from pkg import Client\n"
            "c = Client()\n"
            "c.get(1)\n"
            "c = something_else()\n"
            "c.get(2)\n"
        )
        got = [
            r.lineno for r in scan_source(code, "pkg") if r.qualname == "pkg.Client.get"
        ]
        self.assertEqual(got, [3])

    def test_a_parameter_shadows_an_imported_name(self):
        code = "from pkg import Client\ndef f(Client):\n    Client.get(1)\n"
        self.assertNotIn("pkg.Client.get", names(code))

    def test_a_star_import_is_not_guessed_at(self):
        # Which bare names came from the package is unknowable, so none are
        # claimed rather than some being invented.
        self.assertEqual(names("from pkg import *\nf()\n"), [])

    def test_a_relative_import_is_the_callers_own_code(self):
        self.assertEqual(names("from . import pkg\npkg.f()\n"), [])

    def test_another_package_is_ignored(self):
        self.assertEqual(names("import other\nother.f()\n"), [])

    def test_a_local_variable_does_not_leak_out_of_its_function(self):
        code = (
            "from pkg import Client\n"
            "def a():\n"
            "    c = Client()\n"
            "def b():\n"
            "    c.get(1)\n"
        )
        self.assertNotIn("pkg.Client.get", names(code))


class TestCallShape(unittest.TestCase):
    def test_keywords_are_recorded(self):
        call = calls("import pkg\npkg.f(1, timeout=2, retries=3)\n")["pkg.f"]
        self.assertEqual(call.keywords, frozenset({"timeout", "retries"}))
        self.assertEqual(call.positional, 1)

    def test_a_bare_reference_is_not_a_call(self):
        reference = scan_source("import pkg\nx = pkg.f\n", "pkg")[0]
        self.assertFalse(reference.called)

    def test_a_positional_splat_makes_the_count_unknown(self):
        # None must never be read as zero: a *args call might be passing the
        # very argument that was removed.
        call = calls("import pkg\npkg.f(*args)\n")["pkg.f"]
        self.assertIsNone(call.positional)

    def test_a_keyword_splat_is_flagged(self):
        call = calls("import pkg\npkg.f(**opts)\n")["pkg.f"]
        self.assertTrue(call.splatted_keywords)
        self.assertEqual(call.keywords, frozenset())

    def test_nested_calls_are_both_recorded(self):
        found = calls("import pkg\npkg.outer(pkg.inner())\n")
        self.assertIn("pkg.outer", found)
        self.assertIn("pkg.inner", found)

    def test_line_numbers_point_at_the_call(self):
        call = calls("import pkg\n\n\npkg.f()\n")["pkg.f"]
        self.assertEqual(call.lineno, 4)


class TestBom(unittest.TestCase):
    def test_a_leading_utf8_bom_does_not_hide_a_call(self):
        # Notepad and some Windows tools write a BOM. ast.parse treats that
        # as a SyntaxError, so a naive utf-8 read would skip the file and
        # miss the break.
        refs = scan_source("\ufeffimport pkg\npkg.f()\n", "pkg")
        self.assertIn("pkg.f", [r.qualname for r in refs])

    def test_scan_paths_reads_a_bom_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            path = root / "app.py"
            path.write_bytes(b"\xef\xbb\xbfimport pkg\npkg.f()\n")
            result = scan_paths([path], "pkg", root=root)
            self.assertEqual(result.unparsed, [])
            self.assertEqual(result.files_scanned, 1)
            self.assertIn("pkg.f", [r.qualname for r in result.references])


if __name__ == "__main__":
    unittest.main()
