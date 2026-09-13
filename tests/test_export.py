"""tests/test_export.py

TDD tests for the export endpoints and the backend.export builders.

PET_LIVE is unset so all calls use synthetic data produced by
data_gen.generate() -- no Databricks workspace needed. Mirrors the style of
tests/test_api.py: a shared TestClient for read-only checks, a dedicated
TestClient where session-overlay isolation matters.
"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from backend import export as export_mod
from backend.data_gen import generate
from backend.main import app

_client = TestClient(app)

_EXPECTED_SHEETS = [
    "Overview", "Parameters", "SKUs", "Weeks", "Summary", "Capacity Flags",
    "Supply Grid", "Supply Colour Map", "Production Plan", "Production Qty Map",
]

_CANONICAL_COLOURS = {
    "dark_blue", "light_blue", "green", "amber", "red", "dark_red", "black",
}


# ---------------------------------------------------------------------------
# GET /api/export/excel
# ---------------------------------------------------------------------------

class TestExportExcelEndpoint:
    def test_status_200(self):
        r = _client.get("/api/export/excel")
        assert r.status_code == 200

    def test_content_type(self):
        r = _client.get("/api/export/excel")
        assert r.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_content_disposition_is_attachment_xlsx(self):
        r = _client.get("/api/export/excel")
        cd = r.headers["content-disposition"]
        assert cd.startswith("attachment;")
        assert ".xlsx" in cd

    def test_body_is_a_valid_zip_xlsx(self):
        r = _client.get("/api/export/excel")
        assert r.content[:4] == b"PK\x03\x04"
        # Must actually load as a workbook, not just have the right magic bytes.
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == _EXPECTED_SHEETS

    def test_sets_session_cookie_for_a_fresh_client(self):
        fresh_client = TestClient(app)
        r = fresh_client.get("/api/export/excel")
        assert "pet_session" in r.cookies

    def test_skus_sheet_has_eleven_rows(self):
        r = _client.get("/api/export/excel")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb["SKUs"]
        assert ws.max_row == 12  # header + 11 SKUs

    def test_weeks_sheet_has_fiftytwo_rows(self):
        r = _client.get("/api/export/excel")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb["Weeks"]
        assert ws.max_row == 53  # header + 52 weeks

    def test_supply_grid_sheet_has_572_data_rows(self):
        r = _client.get("/api/export/excel")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb["Supply Grid"]
        assert ws.max_row == 573  # header + 11 SKUs x 52 weeks

    def test_supply_colour_map_cells_use_canonical_short_codes(self):
        r = _client.get("/api/export/excel")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb["Supply Colour Map"]
        codes_seen = set()
        for row in ws.iter_rows(min_row=2, max_row=12, min_col=2, max_col=53):
            for cell in row:
                if cell.value:
                    codes_seen.add(cell.value)
        assert codes_seen, "expected at least one colour code cell"
        assert codes_seen.issubset(set(export_mod.COLOUR_CODE.values()))

    def test_production_qty_map_total_row_matches_production_api(self):
        """Cross-check: the Excel TOTAL row must match GET /api/production week_totals
        for the *same session* (same TestClient instance -> same cookie)."""
        client = TestClient(app)
        prod = client.get("/api/production").json()
        r = client.get("/api/export/excel")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb["Production Qty Map"]

        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        total_row = [c.value for c in next(ws.iter_rows(min_row=ws.max_row, max_row=ws.max_row))]
        assert total_row[0] == "TOTAL UNITS PRODUCED"

        # Spot-check a handful of week columns against the JSON API's week_totals.
        for col_idx in (1, 10, 25, 51):  # 0-based offsets into the week columns
            week_label = header[1 + col_idx]
            week_key = next(wk for wk in prod["week_totals"] if wk.endswith(week_label))
            expected = round(prod["week_totals"][week_key])
            assert round(total_row[1 + col_idx]) == expected

    def test_overlay_edit_reflected_in_export(self):
        """Editing a cell then exporting on the same session must show the edit,
        not the untouched baseline -- proves the endpoint reads the overlay."""
        client = TestClient(app)
        sku, week = "61108", "2026-W38"

        baseline = client.get("/api/export/excel")
        wb0 = openpyxl.load_workbook(io.BytesIO(baseline.content))
        ws0 = wb0["Production Plan"]
        before = next(
            row[3].value for row in ws0.iter_rows(min_row=2)
            if row[0].value == sku and row[2].value == week
        )
        assert before != 0.0, "pre-condition: baseline planned_qty should be non-zero"

        r_edit = client.post(
            "/api/production/edit", json={"sku_code": sku, "week_key": week, "qty": 0.0}
        )
        assert r_edit.status_code == 200

        r = client.get("/api/export/excel")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb["Production Plan"]
        after = next(
            row[3].value for row in ws.iter_rows(min_row=2)
            if row[0].value == sku and row[2].value == week
        )
        assert after == 0.0


# ---------------------------------------------------------------------------
# GET /api/export/pdf
# ---------------------------------------------------------------------------

class TestExportPdfEndpoint:
    def test_status_200(self):
        r = _client.get("/api/export/pdf")
        assert r.status_code == 200

    def test_content_type(self):
        r = _client.get("/api/export/pdf")
        assert r.headers["content-type"] == "application/pdf"

    def test_content_disposition_is_attachment_pdf(self):
        r = _client.get("/api/export/pdf")
        cd = r.headers["content-disposition"]
        assert cd.startswith("attachment;")
        assert ".pdf" in cd

    def test_body_is_a_valid_pdf(self):
        r = _client.get("/api/export/pdf")
        assert r.content[:5] == b"%PDF-"
        assert b"%%EOF" in r.content[-64:]
        assert len(r.content) > 4000  # sanity: not an empty/broken document

    def test_sets_session_cookie_for_a_fresh_client(self):
        fresh_client = TestClient(app)
        r = fresh_client.get("/api/export/pdf")
        assert "pet_session" in r.cookies


# ---------------------------------------------------------------------------
# backend.export builders (unit-level, no HTTP)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ds():
    return generate()


class TestGatherExportData:
    def test_returns_expected_keys(self, ds):
        data = export_mod.gather_export_data(ds, {})
        assert set(data.keys()) == {"supply_df", "prod", "summ"}

    def test_supply_df_has_572_rows(self, ds):
        data = export_mod.gather_export_data(ds, {})
        assert len(data["supply_df"]) == 572


class TestColourPalette:
    def test_colour_order_matches_hex_and_label_and_desc_keys(self):
        for name in export_mod.COLOUR_ORDER:
            assert name in export_mod.COLOUR_HEX
            assert name in export_mod.COLOUR_LABEL
            assert name in export_mod.COLOUR_DESC
            assert name in export_mod.COLOUR_CODE

    def test_seven_canonical_colours(self):
        assert set(export_mod.COLOUR_ORDER) == _CANONICAL_COLOURS

    def test_amber_uses_dark_foreground(self):
        assert export_mod._fg_hex("amber") == "1A2733"

    def test_non_amber_uses_light_foreground(self):
        for name in _CANONICAL_COLOURS - {"amber"}:
            assert export_mod._fg_hex(name) == "FFFFFF"


class TestWeekShort:
    def test_strips_year_prefix(self):
        assert export_mod._week_short("2026-W35") == "W35"

    def test_handles_year_rollover(self):
        assert export_mod._week_short("2027-W01") == "W01"

    def test_returns_input_unchanged_if_not_iso_week(self):
        assert export_mod._week_short("not-a-week") == "not-a-week"


class TestBuildExcelExportUnit:
    def test_returns_nonempty_bytes(self, ds):
        content = export_mod.build_excel_export(ds, {})
        assert isinstance(content, bytes)
        assert len(content) > 1000

    def test_respects_plan_overlay(self, ds):
        """A large overlay value for one cell should show up in the Supply Grid sheet."""
        overlay = {("61108", "2026-W38"): 999_999.0}
        content = export_mod.build_excel_export(ds, overlay)
        wb = openpyxl.load_workbook(io.BytesIO(content))
        ws = wb["Supply Grid"]
        prod_values = [
            row[6].value for row in ws.iter_rows(min_row=2)
            if row[0].value == "61108" and row[2].value == "2026-W38"
        ]
        assert prod_values == [999_999.0]


class TestBuildPdfExportUnit:
    def test_returns_nonempty_pdf_bytes(self, ds):
        content = export_mod.build_pdf_export(ds, {})
        assert content[:5] == b"%PDF-"
        assert len(content) > 4000

    def test_unsaved_edit_count_note_changes_with_overlay(self, ds):
        """Not asserting exact PDF text (no parser available) -- just that a
        non-empty overlay produces a different (larger, since more content is
        never smaller here) document than an empty overlay, i.e. the overlay
        argument actually flows into the render rather than being ignored."""
        empty = export_mod.build_pdf_export(ds, {})
        edited = export_mod.build_pdf_export(ds, {("61108", "2026-W38"): 0.0})
        assert empty != edited
