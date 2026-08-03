"""Surface tests.

What counts as the public API decides everything downstream. Miss a name and
a real break goes unreported; invent one and the tool reports a break in code
nobody can even reach.
"""

from __future__ import annotations

import unittest

from support import PackageCase

from willitbreak.surface import (
    KEYWORD_ONLY,
    POSITIONAL_ONLY,
    POSITIONAL_OR_KEYWORD,
    VAR_KEYWORD,
    VAR_POSITIONAL,
    is_private,
)


class TestPrivacy(unittest.TestCase):
    def test_single_underscore_is_private(self):
        self.assertTrue(is_private("_internal"))

    def test_dunder_is_not_private(self):
        # __init__ and friends are part of the protocol, not hidden.
        self.assertFalse(is_private("__init__"))

    def test_plain_names_are_public(self):
        self.assertFalse(is_private("Client"))


class TestExtraction(PackageCase):
    def test_functions_classes_and_attributes(self):
        surface = self.surface(
            {"__init__": "VERSION = '1'\nclass C:\n    pass\ndef f(a):\n    pass\n"}
        )
        self.assertEqual(surface.get("pkg.VERSION").kind, "attribute")
        self.assertEqual(surface.get("pkg.C").kind, "class")
        self.assertEqual(surface.get("pkg.f").kind, "function")

    def test_private_names_are_left_out(self):
        surface = self.surface({"__init__": "_x = 1\ndef _f():\n    pass\n"})
        self.assertNotIn("pkg._x", surface)
        self.assertNotIn("pkg._f", surface)

    def test_private_modules_are_left_out_but_their_exports_are_not(self):
        # The near-universal layout: implement privately, expose publicly. A
        # reader that only looked at definitions would report pkg._impl.C and
        # never match the pkg.C everyone writes.
        surface = self.surface(
            {
                "_impl": "class C:\n    def go(self, x):\n        pass\n",
                "__init__": "from ._impl import C\n",
            }
        )
        self.assertNotIn("pkg._impl", surface)
        self.assertIn("pkg.C", surface)
        self.assertIn("pkg.C.go", surface)
        self.assertTrue(surface.get("pkg.C").reexported)

    def test_self_is_not_part_of_the_signature(self):
        surface = self.surface(
            {"__init__": "class C:\n    def go(self, x):\n        pass\n"}
        )
        self.assertEqual([p.name for p in surface.get("pkg.C.go").parameters], ["x"])

    def test_staticmethod_keeps_its_first_parameter(self):
        surface = self.surface(
            {"__init__": "class C:\n    @staticmethod\n    def go(x):\n        pass\n"}
        )
        self.assertEqual([p.name for p in surface.get("pkg.C.go").parameters], ["x"])

    def test_a_class_carries_its_constructor_signature(self):
        surface = self.surface(
            {
                "__init__": "class C:\n    def __init__(self, url, timeout=1):\n        pass\n"
            }
        )
        names = [p.name for p in surface.get("pkg.C").parameters]
        self.assertEqual(names, ["url", "timeout"])

    def test_init_is_not_published_separately(self):
        # Callers write C(...), not C.__init__(...). Publishing both would
        # report every constructor change twice.
        surface = self.surface(
            {"__init__": "class C:\n    def __init__(self, a):\n        pass\n"}
        )
        self.assertNotIn("pkg.C.__init__", surface)

    def test_a_property_is_an_attribute_not_a_call(self):
        surface = self.surface(
            {
                "__init__": "class C:\n    @property\n    def base(self):\n        return 1\n"
            }
        )
        self.assertEqual(surface.get("pkg.C.base").kind, "attribute")
        self.assertIsNone(surface.get("pkg.C.base").parameters)

    def test_a_callable_taking_nothing_is_not_a_non_callable(self):
        surface = self.surface({"__init__": "def f():\n    pass\n"})
        self.assertEqual(surface.get("pkg.f").parameters, ())
        self.assertTrue(surface.get("pkg.f").is_callable)

    def test_all_promotes_a_private_looking_export(self):
        surface = self.surface(
            {
                "_impl": "class _Thing:\n    pass\n",
                "__init__": "from ._impl import _Thing\n__all__ = ['_Thing']\n",
            }
        )
        self.assertIn("pkg._Thing", surface)

    def test_all_does_not_exclude_reachable_names(self):
        # __all__ governs `from pkg import *`, not attribute access. A name
        # left out of it still breaks a caller when it disappears, and this
        # tool only reports names the caller actually touched anyway.
        surface = self.surface(
            {"__init__": "__all__ = ['a']\ndef a():\n    pass\ndef b():\n    pass\n"}
        )
        self.assertIn("pkg.a", surface)
        self.assertIn("pkg.b", surface)

    def test_names_defined_under_a_version_guard_are_public(self):
        surface = self.surface(
            {
                "__init__": "import sys\nif sys.version_info >= (3, 9):\n    def modern():\n        pass\n"
            }
        )
        self.assertIn("pkg.modern", surface)

    def test_names_defined_in_a_try_block_are_public(self):
        surface = self.surface(
            {
                "__init__": "try:\n    def maybe():\n        pass\nexcept ImportError:\n    pass\n"
            }
        )
        self.assertIn("pkg.maybe", surface)

    def test_a_module_that_will_not_parse_is_recorded(self):
        # Silence here would read as "nothing changed", the most dangerous
        # wrong answer this tool could give.
        surface = self.surface({"__init__": "", "broken": "def (:\n"})
        self.assertIn("pkg.broken", surface.unreadable)

    def test_a_single_file_package(self):
        root = self.root / "solo"
        root.mkdir(parents=True)
        (root / "pkg.py").write_text("def f(a):\n    pass\n", encoding="utf-8")
        from willitbreak.surface import read_surface

        surface = read_surface(root, "pkg", "1.0")
        self.assertIn("pkg.f", surface)

    def test_a_missing_package_yields_an_empty_surface(self):
        from willitbreak.surface import read_surface

        self.assertEqual(read_surface(self.root, "absent", "1.0").symbols, {})

    def test_a_reexport_chain_is_followed(self):
        surface = self.surface(
            {
                "_deep": "class C:\n    pass\n",
                "mid": "from ._deep import C\n",
                "__init__": "from .mid import C\n",
            }
        )
        self.assertIn("pkg.C", surface)


class TestParameterKinds(PackageCase):
    def test_every_kind_is_distinguished(self):
        surface = self.surface(
            {"__init__": "def f(a, /, b, *args, c, **kw):\n    pass\n"}
        )
        kinds = {p.name: p.kind for p in surface.get("pkg.f").parameters}
        self.assertEqual(kinds["a"], POSITIONAL_ONLY)
        self.assertEqual(kinds["b"], POSITIONAL_OR_KEYWORD)
        self.assertEqual(kinds["args"], VAR_POSITIONAL)
        self.assertEqual(kinds["c"], KEYWORD_ONLY)
        self.assertEqual(kinds["kw"], VAR_KEYWORD)

    def test_defaults_bind_to_the_end_of_the_positionals(self):
        surface = self.surface({"__init__": "def f(a, b, c=1, d=2):\n    pass\n"})
        required = {p.name: p.has_default for p in surface.get("pkg.f").parameters}
        self.assertEqual(required, {"a": False, "b": False, "c": True, "d": True})

    def test_keyword_only_defaults(self):
        surface = self.surface({"__init__": "def f(*, a, b=1):\n    pass\n"})
        required = {p.name: p.has_default for p in surface.get("pkg.f").parameters}
        self.assertEqual(required, {"a": False, "b": True})

    def test_kwargs_is_detected(self):
        surface = self.surface({"__init__": "def f(**kw):\n    pass\n"})
        self.assertTrue(surface.get("pkg.f").accepts_arbitrary_keywords)


class TestSerialisation(PackageCase):
    def test_round_trip(self):
        import json

        from willitbreak.surface import Surface

        surface = self.surface(
            {"__init__": "class C:\n    def go(self, a, *, b=1):\n        pass\n"}
        )
        restored = Surface.from_dict(json.loads(json.dumps(surface.as_dict())))
        self.assertEqual(
            restored.get("pkg.C.go").parameters, surface.get("pkg.C.go").parameters
        )
        self.assertEqual(len(restored.symbols), len(surface.symbols))


if __name__ == "__main__":
    unittest.main()
