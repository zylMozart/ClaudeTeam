"""Unit tests for ``claudeteam.runtime.profile_naming``.

Covers the architect's F/E test rows from
``workspace/architect/multiteam_isolation_design_2026-04-30.md``:

  • Stability  — same (session, root) always returns the same name.
  • Uniqueness — different roots OR different sessions produce different names.
  • Format     — ``{session}-{6 hex chars}``.
  • Path resolution — relative paths and ``../`` traversal hash to the same
    value as the resolved absolute path.

Run with the project's no-live test harness::

    PYTHONPATH=src python3 tests/test_profile_naming.py
"""
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from claudeteam.runtime.profile_naming import (  # noqa: E402
    HASH_LEN,
    generate_unique_profile_name,
)


class GenerateUniqueProfileNameTests(unittest.TestCase):
    def test_format_session_dash_hex6(self):
        name = generate_unique_profile_name("claudeteam", "/tmp/x")
        self.assertTrue(name.startswith("claudeteam-"))
        suffix = name.split("-")[-1]
        self.assertEqual(len(suffix), HASH_LEN)
        # Hex only
        int(suffix, 16)

    def test_stability_same_inputs_same_output(self):
        a = generate_unique_profile_name("claudeteam", "/tmp/clone-A")
        b = generate_unique_profile_name("claudeteam", "/tmp/clone-A")
        self.assertEqual(a, b)

    def test_uniqueness_different_paths(self):
        a = generate_unique_profile_name("claudeteam", "/tmp/clone-A")
        b = generate_unique_profile_name("claudeteam", "/tmp/clone-B")
        self.assertNotEqual(a, b)

    def test_uniqueness_different_sessions(self):
        a = generate_unique_profile_name("claudeteam", "/tmp/clone-A")
        b = generate_unique_profile_name("team-life", "/tmp/clone-A")
        self.assertNotEqual(a, b)
        # But hash6 is the same — uniqueness comes from session prefix.
        self.assertEqual(a.split("-")[-1], b.split("-")[-1])

    def test_path_resolution_relative_equals_absolute(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "sub")
            os.makedirs(sub)
            cwd = os.getcwd()
            try:
                os.chdir(sub)
                relative = generate_unique_profile_name("claudeteam", ".")
                absolute = generate_unique_profile_name("claudeteam", sub)
                # `.` resolves to abs(sub), so names must match.
                self.assertEqual(relative, absolute)
            finally:
                os.chdir(cwd)

    def test_path_resolution_handles_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "sub")
            os.makedirs(sub)
            via_traversal = generate_unique_profile_name(
                "claudeteam", os.path.join(sub, "..", "sub")
            )
            direct = generate_unique_profile_name("claudeteam", sub)
            self.assertEqual(via_traversal, direct)

    def test_session_with_dashes_or_unicode(self):
        # Accept whatever session string the user typed; we don't validate.
        n1 = generate_unique_profile_name("team-life-pm", "/tmp/x")
        n2 = generate_unique_profile_name("团队-生活", "/tmp/x")
        self.assertTrue(n1.startswith("team-life-pm-"))
        self.assertTrue(n2.startswith("团队-生活-"))
        # Both end in the same hash6 since path is the same.
        self.assertEqual(n1.split("-")[-1], n2.split("-")[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
