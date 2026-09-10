"""tests/test_create_genie.py

Unit tests for the pure helper functions in deploy/create_genie.py.

No Databricks workspace is required -- these are offline, deterministic tests.

Covers:
  - table_identifiers(): correct identifiers, correct alphabetical sort order
  - build_serialized_space(): valid JSON, correct keys, table sort order,
    text_instructions count, id prefix conventions, no em dashes
"""
from __future__ import annotations

import json

import pytest

from deploy.create_genie import (
    SPACE_TITLE,
    _TABLE_NAMES,
    _full_space_title,
    build_serialized_space,
    table_identifiers,
)


# ---------------------------------------------------------------------------
# table_identifiers()
# ---------------------------------------------------------------------------

class TestTableIdentifiers:
    def test_returns_five_tables(self):
        result = table_identifiers("cat", "sch")
        assert len(result) == 5

    def test_all_identifiers_prefixed_correctly(self):
        result = table_identifiers("my_catalog", "my_schema")
        for item in result:
            assert item["identifier"].startswith("my_catalog.my_schema.")

    def test_sorted_alphabetically(self):
        result = table_identifiers("c", "s")
        ids = [r["identifier"] for r in result]
        assert ids == sorted(ids), "identifiers must be in alphabetical order"

    def test_exact_identifier_values(self):
        result = table_identifiers("c", "s")
        expected = [
            "c.s.demand",
            "c.s.plan_line",
            "c.s.projection_snapshot",
            "c.s.sku",
            "c.s.week",
        ]
        assert [r["identifier"] for r in result] == expected

    def test_table_names_constant_is_sorted(self):
        """_TABLE_NAMES itself must already be sorted (it drives the output)."""
        assert _TABLE_NAMES == sorted(_TABLE_NAMES)


# ---------------------------------------------------------------------------
# build_serialized_space()
# ---------------------------------------------------------------------------

class TestBuildSerializedSpace:
    @pytest.fixture
    def space(self) -> dict:
        raw = build_serialized_space("deep_test_1_catalog", "lactalis_pet_planner")
        return json.loads(raw)

    def test_returns_valid_json_string(self):
        raw = build_serialized_space("c", "s")
        assert isinstance(raw, str)
        parsed = json.loads(raw)  # must not raise
        assert isinstance(parsed, dict)

    def test_version_is_2(self, space):
        assert space["version"] == 2

    def test_has_required_top_level_keys(self, space):
        for key in ("version", "config", "data_sources", "instructions"):
            assert key in space, f"missing top-level key: {key}"

    def test_tables_are_sorted(self, space):
        ids = [t["identifier"] for t in space["data_sources"]["tables"]]
        assert ids == sorted(ids)

    def test_tables_contain_all_five(self, space):
        ids = [t["identifier"] for t in space["data_sources"]["tables"]]
        for table in ["demand", "plan_line", "projection_snapshot", "sku", "week"]:
            assert any(i.endswith(f".{table}") for i in ids), f"missing table: {table}"

    def test_text_instructions_count_at_most_one(self, space):
        count = len(space["instructions"]["text_instructions"])
        assert count <= 1, (
            f"API rejects more than one text_instructions item; got {count}"
        )

    def test_text_instructions_id_is_32_hex_chars(self, space):
        for item in space["instructions"]["text_instructions"]:
            id_val = item["id"]
            assert len(id_val) == 32, f"id '{id_val}' is not 32 chars"
            assert all(c in "0123456789abcdef" for c in id_val), (
                f"id '{id_val}' contains non-hex chars"
            )

    def test_example_question_sqls_ids_are_32_hex_chars(self, space):
        for item in space["instructions"]["example_question_sqls"]:
            id_val = item["id"]
            assert len(id_val) == 32
            assert all(c in "0123456789abcdef" for c in id_val)

    def test_example_question_sqls_sorted_by_id(self, space):
        ids = [i["id"] for i in space["instructions"]["example_question_sqls"]]
        assert ids == sorted(ids)

    def test_all_ids_unique_across_lists(self, space):
        """IDs must be unique across sample_questions, example_question_sqls,
        and text_instructions combined (API rejects duplicates)."""
        all_ids = (
            [i["id"] for i in space["config"].get("sample_questions", [])]
            + [i["id"] for i in space["instructions"].get("example_question_sqls", [])]
            + [i["id"] for i in space["instructions"].get("text_instructions", [])]
        )
        assert len(all_ids) == len(set(all_ids)), "duplicate id found across lists"

    def test_question_fields_are_arrays(self, space):
        for item in space["instructions"]["example_question_sqls"]:
            assert isinstance(item["question"], list), (
                f"question must be an array in item id={item['id']}"
            )

    def test_sql_fields_are_arrays(self, space):
        for item in space["instructions"]["example_question_sqls"]:
            assert isinstance(item["sql"], list), (
                f"sql must be an array in item id={item['id']}"
            )

    def test_text_instructions_content_is_array(self, space):
        for item in space["instructions"]["text_instructions"]:
            assert isinstance(item["content"], list), "content must be an array"

    def test_tables_use_correct_catalog_and_schema(self, space):
        ids = [t["identifier"] for t in space["data_sources"]["tables"]]
        for id_ in ids:
            assert id_.startswith("deep_test_1_catalog.lactalis_pet_planner.")

    def test_no_em_dashes_in_content(self):
        """No em dashes allowed in user-facing strings (project style rule)."""
        raw = build_serialized_space("c", "s")
        assert "—" not in raw, "em dash found in serialized_space"

    def test_sample_questions_empty(self, space):
        """sample_questions must be empty -- UI-only, not settable via API."""
        assert space["config"]["sample_questions"] == []

    def test_contains_glossary_keywords(self, space):
        """text_instructions must mention key domain terms."""
        content = "".join(space["instructions"]["text_instructions"][0]["content"])
        for term in ("SOH", "QA hold", "cover_weeks", "projection_snapshot"):
            assert term in content, f"glossary term '{term}' missing from text_instructions"

    def test_colour_band_names_present(self, space):
        """All seven colour band names must appear in the instructions."""
        content = "".join(space["instructions"]["text_instructions"][0]["content"])
        for colour in (
            "dark_blue", "light_blue", "green", "amber", "red", "dark_red", "black"
        ):
            assert colour in content, f"colour '{colour}' missing from instructions"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_space_title_has_no_em_dash(self):
        assert "—" not in SPACE_TITLE

    def test_space_title_nonempty(self):
        assert SPACE_TITLE.strip()


# ---------------------------------------------------------------------------
# _full_space_title()
# ---------------------------------------------------------------------------

class TestFullSpaceTitle:
    def test_contains_base_title(self):
        result = _full_space_title("lactalis_pet_planner")
        assert SPACE_TITLE in result

    def test_contains_schema(self):
        result = _full_space_title("lactalis_pet_planner")
        assert "lactalis_pet_planner" in result

    def test_schema_scoped_differs_from_base(self):
        assert _full_space_title("lactalis_pet_planner") != SPACE_TITLE

    def test_different_schemas_produce_different_titles(self):
        assert _full_space_title("schema_a") != _full_space_title("schema_b")

    def test_old_schema_title_differs_from_new(self):
        """Ensures the old lactalis_pet space is NOT reused for lactalis_pet_planner."""
        old = _full_space_title("lactalis_pet")
        new = _full_space_title("lactalis_pet_planner")
        assert old != new

    def test_no_em_dash(self):
        assert "—" not in _full_space_title("lactalis_pet_planner")
