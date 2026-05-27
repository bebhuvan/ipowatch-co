"""Tests for the upstream drift detector."""

from __future__ import annotations

import unittest

from ipo_portal.orchestrator.drift import (
    _compatible_types,
    diff_paths,
    walk_leaf_paths,
)


class WalkLeafPathsTests(unittest.TestCase):
    def test_flat_dict(self) -> None:
        result = walk_leaf_paths({"a": 1, "b": "x"})
        self.assertEqual(result, {"$.a": "int", "$.b": "str"})

    def test_list_of_objects(self) -> None:
        body = [{"symbol": "ABCD", "n": 1}, {"symbol": "EFGH", "n": 2}]
        result = walk_leaf_paths(body)
        self.assertEqual(
            result,
            {"$[*].symbol": "str", "$[*].n": "int"},
        )

    def test_mixed_types_merged(self) -> None:
        body = [{"x": 1}, {"x": "two"}]
        result = walk_leaf_paths(body)
        self.assertEqual(result["$[*].x"], "int|str")

    def test_empty_list(self) -> None:
        self.assertEqual(walk_leaf_paths({"docs": []}), {"$.docs": "list_empty"})


class DiffPathsTests(unittest.TestCase):
    def test_added_path(self) -> None:
        catalog = {"$[*].a": "string"}
        observed = {"$[*].a": "str", "$[*].b": "int"}
        added, removed, type_changes = diff_paths(catalog, observed)
        self.assertEqual(added, {"$[*].b"})
        self.assertEqual(removed, set())
        self.assertEqual(type_changes, {})

    def test_removed_path(self) -> None:
        catalog = {"$[*].a": "string", "$[*].b": "integer"}
        observed = {"$[*].a": "str"}
        added, removed, type_changes = diff_paths(catalog, observed)
        self.assertEqual(removed, {"$[*].b"})

    def test_type_change_flagged(self) -> None:
        catalog = {"$[*].a": "boolean"}
        observed = {"$[*].a": "list"}  # not compatible
        _, _, type_changes = diff_paths(catalog, observed)
        self.assertEqual(type_changes, {"$[*].a": ("boolean", "list")})

    def test_string_int_for_integer_is_compatible(self) -> None:
        # E.NUM.003: NSE reports integers as strings — should NOT count as drift.
        catalog = {"$[*].n": "integer"}
        observed = {"$[*].n": "str"}
        _, _, type_changes = diff_paths(catalog, observed)
        self.assertEqual(type_changes, {})


class CompatibilityTests(unittest.TestCase):
    def test_known_pairs(self) -> None:
        self.assertTrue(_compatible_types("integer", "str"))
        self.assertTrue(_compatible_types("integer", "int"))
        self.assertTrue(_compatible_types("boolean", "str"))
        self.assertTrue(_compatible_types("array", "list"))
        self.assertFalse(_compatible_types("integer", "list"))
        self.assertFalse(_compatible_types("date", "int"))


if __name__ == "__main__":
    unittest.main()
