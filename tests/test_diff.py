"""Diff and intersection tests.

The diff decides what changed; the intersection decides whether the reader
cares. Both halves have to be right, and the second one is where the value is:
a big release changes hundreds of things and touches almost none of any given
codebase.
"""

from __future__ import annotations

import unittest

from support import PackageCase


class TestRemoval(PackageCase):
    def test_a_removed_function(self):
        _, _, changes = self.two({"__init__": "def f():\n    pass\n"}, {"__init__": ""})
        self.assertIn(("pkg.f", "removed"), self.kinds(changes))

    def test_a_removed_class_does_not_also_report_each_method(self):
        # One fact, one line. Reporting the class and all twenty of its
        # methods turns a clear answer into a wall.
        before = {
            "__init__": "class C:\n    def a(self):\n        pass\n    def b(self):\n        pass\n"
        }
        _, _, changes = self.two(before, {"__init__": ""})
        self.assertEqual(
            [c.qualname for c in changes if c.kind == "removed"], ["pkg.C"]
        )

    def test_a_removed_module(self):
        _, _, changes = self.two(
            {"__init__": "", "sub": "def f():\n    pass\n"}, {"__init__": ""}
        )
        self.assertIn(("pkg.sub", "removed"), self.kinds(changes))

    def test_an_unchanged_api_produces_nothing(self):
        same = {"__init__": "def f(a, b=1):\n    pass\n"}
        _, _, changes = self.two(same, same)
        self.assertEqual(changes, [])

    def test_an_added_name_is_not_a_change(self):
        _, _, changes = self.two({"__init__": ""}, {"__init__": "def f():\n    pass\n"})
        self.assertEqual(changes, [])


class TestSignatures(PackageCase):
    def test_a_removed_keyword(self):
        _, _, changes = self.two(
            {"__init__": "def f(a, timeout=1):\n    pass\n"},
            {"__init__": "def f(a):\n    pass\n"},
        )
        self.assertIn(("pkg.f", "parameter-removed"), self.kinds(changes))

    def test_kwargs_in_the_new_version_absorbs_a_removed_keyword(self):
        # The call still works, so nothing is reported. Claiming a break here
        # is the sort of false alarm that gets a checker switched off.
        _, _, changes = self.two(
            {"__init__": "def f(a, timeout=1):\n    pass\n"},
            {"__init__": "def f(a, **kw):\n    pass\n"},
        )
        self.assertNotIn(("pkg.f", "parameter-removed"), self.kinds(changes))

    def test_a_lost_default(self):
        _, _, changes = self.two(
            {"__init__": "def f(a=1):\n    pass\n"},
            {"__init__": "def f(a):\n    pass\n"},
        )
        change = next(c for c in changes if c.kind == "now-required")
        self.assertTrue(change.when_omitted)

    def test_a_new_required_parameter(self):
        _, _, changes = self.two(
            {"__init__": "def f(a):\n    pass\n"},
            {"__init__": "def f(a, b):\n    pass\n"},
        )
        self.assertIn(("pkg.f", "new-required"), self.kinds(changes))

    def test_a_new_optional_parameter_is_not_a_change(self):
        _, _, changes = self.two(
            {"__init__": "def f(a):\n    pass\n"},
            {"__init__": "def f(a, b=1):\n    pass\n"},
        )
        self.assertEqual(changes, [])

    def test_becoming_keyword_only(self):
        _, _, changes = self.two(
            {"__init__": "def f(a, b):\n    pass\n"},
            {"__init__": "def f(a, *, b):\n    pass\n"},
        )
        change = next(c for c in changes if c.kind == "keyword-only")
        self.assertEqual(change.min_positional, 2)

    def test_a_rename_is_named_as_such(self):
        _, _, changes = self.two(
            {"__init__": "def f(a, params=None):\n    pass\n"},
            {"__init__": "def f(a, headers=None):\n    pass\n"},
        )
        change = next(c for c in changes if c.kind == "parameter-removed")
        self.assertIn("renamed", change.detail)
        self.assertIn("headers", change.detail)

    def test_becoming_a_non_callable(self):
        _, _, changes = self.two(
            {"__init__": "def f():\n    pass\n"},
            {"__init__": "f = 1\n"},
        )
        self.assertIn(("pkg.f", "not-callable"), self.kinds(changes))


class TestIntersection(PackageCase):
    """Only changes that land on a real line get reported."""

    def test_a_change_nobody_uses_is_counted_not_listed(self):
        outcome = self.outcome(
            {"__init__": "def used():\n    pass\ndef unused(a=1):\n    pass\n"},
            {"__init__": "def used():\n    pass\n"},
            "import pkg\npkg.used()\n",
        )
        self.assertEqual(outcome.findings, [])
        self.assertEqual(outcome.untouched, 1)
        self.assertTrue(outcome.ok)

    def test_a_removed_name_the_caller_uses_is_reported_with_its_line(self):
        outcome = self.outcome(
            {"__init__": "def gone():\n    pass\n"},
            {"__init__": ""},
            "import pkg\n\npkg.gone()\n",
        )
        self.assertEqual(len(outcome.breaking), 1)
        self.assertEqual(outcome.breaking[0].references[0].lineno, 3)

    def test_a_removed_keyword_only_hits_callers_that_pass_it(self):
        before = {"__init__": "def f(a, timeout=1):\n    pass\n"}
        after = {"__init__": "def f(a):\n    pass\n"}
        passes = self.outcome(before, after, "import pkg\npkg.f(1, timeout=2)\n")
        omits = self.outcome(before, after, "import pkg\npkg.f(1)\n")
        self.assertEqual(len(passes.breaking), 1)
        self.assertEqual(omits.breaking, [])

    def test_a_lost_default_only_hits_callers_that_omit_it(self):
        before = {"__init__": "def f(a=1):\n    pass\n"}
        after = {"__init__": "def f(a):\n    pass\n"}
        omits = self.outcome(before, after, "import pkg\npkg.f()\n")
        passes = self.outcome(before, after, "import pkg\npkg.f(a=2)\n")
        self.assertEqual(len(omits.breaking), 1)
        self.assertEqual(passes.breaking, [])

    def test_a_lost_default_is_satisfied_positionally_too(self):
        outcome = self.outcome(
            {"__init__": "def f(a=1):\n    pass\n"},
            {"__init__": "def f(a):\n    pass\n"},
            "import pkg\npkg.f(2)\n",
        )
        self.assertEqual(outcome.breaking, [])

    def test_keyword_only_hits_positional_callers_and_not_keyword_ones(self):
        before = {"__init__": "def f(a, b):\n    pass\n"}
        after = {"__init__": "def f(a, *, b):\n    pass\n"}
        positional = self.outcome(before, after, "import pkg\npkg.f(1, 2)\n")
        keyword = self.outcome(before, after, "import pkg\npkg.f(1, b=2)\n")
        self.assertEqual(len(positional.breaking), 1)
        self.assertEqual(keyword.breaking, [])

    def test_a_splat_is_never_claimed_as_a_break(self):
        # The arguments are unknowable, so a break is unprovable. Guessing
        # here is exactly what makes a checker untrustworthy.
        outcome = self.outcome(
            {"__init__": "def f(a=1):\n    pass\n"},
            {"__init__": "def f(a):\n    pass\n"},
            "import pkg\npkg.f(**opts)\n",
        )
        self.assertEqual(outcome.breaking, [])

    def test_a_bare_reference_survives_an_argument_change(self):
        outcome = self.outcome(
            {"__init__": "def f(a, timeout=1):\n    pass\n"},
            {"__init__": "def f(a):\n    pass\n"},
            "import pkg\nhandler = pkg.f\n",
        )
        self.assertEqual(outcome.breaking, [])

    def test_a_bare_reference_does_not_survive_removal(self):
        outcome = self.outcome(
            {"__init__": "def f():\n    pass\n"},
            {"__init__": ""},
            "import pkg\nhandler = pkg.f\n",
        )
        self.assertEqual(len(outcome.breaking), 1)

    def test_a_method_reached_through_an_instance(self):
        outcome = self.outcome(
            {"__init__": "class C:\n    def go(self, a, timeout=1):\n        pass\n"},
            {"__init__": "class C:\n    def go(self, a):\n        pass\n"},
            "from pkg import C\nc = C()\nc.go(1, timeout=2)\n",
        )
        self.assertEqual(len(outcome.breaking), 1)
        self.assertEqual(outcome.breaking[0].change.qualname, "pkg.C.go")

    def test_a_constructor_change_is_reported_once(self):
        outcome = self.outcome(
            {
                "__init__": "class C:\n    def __init__(self, a, timeout=1):\n        pass\n"
            },
            {"__init__": "class C:\n    def __init__(self, a):\n        pass\n"},
            "from pkg import C\nC(1, timeout=2)\n",
        )
        self.assertEqual(len(outcome.breaking), 1)

    def test_every_call_site_is_listed(self):
        outcome = self.outcome(
            {"__init__": "def f(timeout=1):\n    pass\n"},
            {"__init__": "def f():\n    pass\n"},
            "import pkg\npkg.f(timeout=1)\npkg.f(timeout=2)\n",
        )
        self.assertEqual(len(outcome.breaking[0].references), 2)

    def test_the_outcome_serialises(self):
        import json

        outcome = self.outcome(
            {"__init__": "def f():\n    pass\n"},
            {"__init__": ""},
            "import pkg\npkg.f()\n",
        )
        raw = json.loads(json.dumps(outcome.as_dict()))
        self.assertEqual(raw["breaking"], 1)
        self.assertEqual(raw["schema"], "willitbreak/outcome-v1")


if __name__ == "__main__":
    unittest.main()
