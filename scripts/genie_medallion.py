"""scripts/genie_medallion.py

Medallion-aware Genie space for the PET Line Planner. Curated over the Silver
dimensions/facts (sku, week, demand, plan_line) and the Gold projection
(projection_snapshot). Adapted from deploy/create_genie.py for the split-schema
layout. Idempotent: reuse-by-title.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

SPACE_TITLE = "Lactalis PET Line Planner [medallion]"
_PARENT_PATH = "/Workspace/Shared/lactalis-pet-planner-v2"


def table_identifiers(catalog: str, silver: str, gold: str) -> list[dict]:
    ids = [
        f"{catalog}.{gold}.projection_snapshot",
        f"{catalog}.{silver}.demand",
        f"{catalog}.{silver}.plan_line",
        f"{catalog}.{silver}.sku",
        f"{catalog}.{silver}.week",
    ]
    return [{"identifier": i} for i in sorted(ids)]


def build_serialized_space(catalog: str, silver: str, gold: str) -> str:
    g = f"{catalog}.{gold}"
    s = f"{catalog}.{silver}"
    space_obj = {
        "version": 2,
        "config": {"sample_questions": []},
        "data_sources": {"tables": table_identifiers(catalog, silver, gold)},
        "instructions": {
            "example_question_sqls": [
                {
                    "id": "20000000000000000000000000000001",
                    "question": ["How many SKUs are in each traffic-light colour band right now?"],
                    "sql": [
                        f"SELECT colour, COUNT(DISTINCT sku_code) AS sku_count\n",
                        f"FROM {g}.projection_snapshot\n",
                        f"WHERE run_ts = (SELECT MAX(run_ts) FROM {g}.projection_snapshot)\n",
                        "GROUP BY colour ORDER BY colour",
                    ],
                },
                {
                    "id": "20000000000000000000000000000002",
                    "question": ["Which SKUs have a stock-out in the next 4 weeks?"],
                    "sql": [
                        f"SELECT ps.sku_code, sk.description, ps.week_key, ps.close, ps.colour\n",
                        f"FROM {g}.projection_snapshot ps\n",
                        f"JOIN {s}.sku sk ON ps.sku_code = sk.sku_code\n",
                        "WHERE ps.close <= 0 AND ps.horizon_index <= 4\n",
                        "ORDER BY ps.horizon_index, ps.sku_code",
                    ],
                },
                {
                    "id": "20000000000000000000000000000003",
                    "question": ["What is the total planned production per SKU across the full horizon?"],
                    "sql": [
                        f"SELECT sk.description, pl.sku_code, SUM(pl.planned_qty) AS total_planned_ea\n",
                        f"FROM {s}.plan_line pl JOIN {s}.sku sk ON pl.sku_code = sk.sku_code\n",
                        "GROUP BY sk.description, pl.sku_code ORDER BY total_planned_ea DESC",
                    ],
                },
                {
                    "id": "20000000000000000000000000000004",
                    "question": ["What is the average forward cover (weeks) per SKU?"],
                    "sql": [
                        f"SELECT sk.description, ps.sku_code, ROUND(AVG(ps.cover_weeks),1) AS avg_cover\n",
                        f"FROM {g}.projection_snapshot ps\n",
                        f"JOIN {s}.sku sk ON ps.sku_code = sk.sku_code\n",
                        "WHERE ps.run_ts = (SELECT MAX(run_ts) FROM " + g + ".projection_snapshot)\n",
                        "GROUP BY sk.description, ps.sku_code ORDER BY avg_cover ASC",
                    ],
                },
            ],
            "text_instructions": [
                {
                    "id": "30000000000000000000000000000001",
                    "content": [
                        "You are a supply-planning assistant for the Lactalis PET line "
                        "(OAK and PAULS flavoured milks, 400 ml and 500 ml).\n\n",
                        "This is a medallion model. Silver holds the conformed inputs "
                        "(sku, week, demand, plan_line); Gold holds the computed supply "
                        "projection (projection_snapshot) written by the pipeline.\n\n",
                        "GLOSSARY\n",
                        "- close: projected closing stock for a week (projection_snapshot.close); "
                        "close <= 0 is a stock-out.\n",
                        "- cover_weeks: whole forward weeks of demand the closing stock covers "
                        "(0-3, projection_snapshot.cover_weeks).\n",
                        "- QA hold (2 weeks): production in week W is available as receipts in W+2.\n",
                        "- demand = MAX(forecast, sales_order); resolved in projection_snapshot.demand.\n\n",
                        "COLOURS (projection_snapshot.colour): dark_blue >3wk cover; light_blue 2-3; "
                        "green 1-2; amber <1wk; red stock-out beyond the reaction window; "
                        "dark_red stock-out within it (urgent); black over-cover past max weeks "
                        "cover (waste risk).\n\n",
                        "Always query the latest snapshot with "
                        "WHERE run_ts = (SELECT MAX(run_ts) FROM <gold>.projection_snapshot). "
                        "Join sku on sku_code to show the description. Refer to weeks by week_key "
                        "(e.g. 2026-W35).",
                    ],
                }
            ],
        },
    }
    return json.dumps(space_obj, separators=(",", ":"))


def ensure_space(client: "WorkspaceClient", catalog: str, silver: str, gold: str, warehouse_id: str) -> str:
    resp = client.api_client.do("GET", "/api/2.0/genie/spaces")
    spaces = resp.get("genie_spaces") or resp.get("spaces") or []
    for sp in spaces:
        if sp.get("title") == SPACE_TITLE:
            sid = sp.get("space_id") or sp.get("id")
            if sid:
                logger.info("Reusing Genie space %s (%s)", SPACE_TITLE, sid)
                return sid
    client.workspace.mkdirs(_PARENT_PATH)
    payload = {
        "warehouse_id": warehouse_id,
        "title": SPACE_TITLE,
        "description": "Natural-language supply-planning assistant over the PET medallion (Silver inputs + Gold projection).",
        "parent_path": _PARENT_PATH,
        "serialized_space": build_serialized_space(catalog, silver, gold),
    }
    created = client.api_client.do("POST", "/api/2.0/genie/spaces", body=payload)
    sid = created.get("space_id") or created.get("id")
    if not sid:
        raise RuntimeError(f"Genie creation returned no id: {list(created.keys())}")
    logger.info("Created Genie space %s (%s)", SPACE_TITLE, sid)
    return sid
