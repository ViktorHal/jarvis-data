"""The tests must not hide each other.

A second class or method with a name already used in the same file silently replaces the first, and the
tests it held stop running without any failure to show it. That happened on 28 Nov 2026: a new Budget class
in test_oddsapi.py shadowed the one holding the monthly-cap tests, so the cap was no longer tested at all.
"""
import ast
import collections
import glob
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class NoShadowedTests(unittest.TestCase):
    def test_no_two_test_classes_or_methods_share_a_name_in_one_file(self):
        bad = []
        for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
            tree = ast.parse(open(path, encoding="utf-8").read())
            classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
            for name, n in collections.Counter(c.name for c in classes).items():
                if n > 1:
                    bad.append(f"{os.path.basename(path)}: class {name} defined {n} times")
            for c in classes:
                methods = [f.name for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
                for name, n in collections.Counter(methods).items():
                    if n > 1:
                        bad.append(f"{os.path.basename(path)}: {c.name}.{name} defined {n} times")
        self.assertEqual(bad, [], "a shadowed test never runs and never fails")

    def test_every_test_file_is_collected_by_name(self):
        for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
            base = os.path.basename(path)
            if base in ("conftest.py", "__init__.py"):
                continue
            self.assertTrue(base.startswith("test_"), f"{base} would never be collected by pytest")
