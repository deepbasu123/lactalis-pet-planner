"""deploy/create_genie.py

Idempotent Genie space provisioning for the Lactalis PET Line Planner.

Public interface:
    ensure_space(client, catalog, schema, warehouse_id) -> str
        Creates (or finds by title) the PET Line Planner Genie space over the
        five PET Unity Catalog tables and returns the space id.

API surface used (space management is NOT in GenieAPI in the SDK; that class
only covers conversation-level operations):

    List:   GET  /api/2.0/genie/spaces    via client.api_client.do()
    Create: POST /api/2.0/genie/spaces    via client.api_client.do()

serialized_space format follows the skill spec (databricks-genie-agents,
confirmed 2026-09-10):
    - version: 2
    - data_sources.tables: sorted alphabetically by identifier (skill requirement)
    - instructions.text_instructions: at most ONE item (API limit)
    - instructions.example_question_sqls: sorted by id
    - config.sample_questions: empty list (sample questions are UI-only;
      they cannot be set via the API)

Suggested questions for Genie users are embedded in example_question_sqls
so they appear as guided queries inside the space.

Auth:
    The caller constructs and passes a WorkspaceClient.  This module never
    constructs one itself.  Dual-mode auth (injected SP on Apps, profile
    locally) lives entirely in deploy.py.

DEPLOY-TIME NOTE:
    This script was written with the correct API shape and passes an import
    check, but has NOT been validated against a live workspace (UC tables do
    not exist before deploy.py runs).  Live verification happens during the
    deploy step.  If the list-spaces response uses a key other than
    "genie_spaces" (e.g. "spaces"), the defensive fallback in ensure_space()
    handles it.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Base title -- kept as a constant so existing tests remain valid.
#: Do NOT use SPACE_TITLE directly in ensure_space: use _full_space_title(schema)
#: which includes the schema name so deploys against different schemas create
#: separate spaces and do not reuse a space from a prior effort.
SPACE_TITLE = "Lactalis PET Line Planner"


def _full_space_title(schema: str) -> str:
    """Return the schema-scoped Genie space title used for creation and lookup.

    Including the schema prevents ensure_space from reusing a space from an
    earlier deployment that may point at different (old) UC tables.

    Example: "Lactalis PET Line Planner [lactalis_pet_planner]"
    """
    return f"{SPACE_TITLE} [{schema}]"


#: Workspace path that holds the Genie space object.
#: Created automatically via w.workspace.mkdirs() if it does not exist yet.
_PARENT_PATH = "/Workspace/Shared/lactalis-pet-planner"

#: PET tables included in the Genie space, in alphabetical order (skill
#: requirement: data_sources.tables must be sorted by identifier).
_TABLE_NAMES: list[str] = sorted(
    ["demand", "plan_line", "projection_snapshot", "sku", "week"]
)


# ---------------------------------------------------------------------------
# Pure helpers (tested independently in tests/test_create_genie.py)
# ---------------------------------------------------------------------------

def table_identifiers(catalog: str, schema: str) -> list[dict]:
    """Return data_sources.tables list, sorted alphabetically by identifier.

    Each element is {"identifier": "<catalog>.<schema>.<table>"}.

    >>> table_identifiers("c", "s")  # doctest: +NORMALIZE_WHITESPACE
    [{'identifier': 'c.s.demand'}, {'identifier': 'c.s.plan_line'},
     {'identifier': 'c.s.projection_snapshot'}, {'identifier': 'c.s.sku'},
     {'identifier': 'c.s.week'}]
    """
    return [{"identifier": f"{catalog}.{schema}.{t}"} for t in _TABLE_NAMES]


def build_serialized_space(catalog: str, schema: str) -> str:
    """Return the serialized_space value as a compact JSON string.

    The returned string is passed directly in the POST body as
    ``serialized_space``.  Structure per skill spec (version 2):

    - config.sample_questions: [] (UI-only; not settable via API)
    - data_sources.tables: sorted by identifier
    - instructions.example_question_sqls: sorted by 32-char hex id (prefix 2...)
    - instructions.text_instructions: one item (API limit), prefix 3...,
      containing the domain glossary so Genie answers PET planning questions.
    """
    s = f"{catalog}.{schema}"  # shorthand for SQL references

    space_obj: dict = {
        "version": 2,
        "config": {
            "sample_questions": []
        },
        "data_sources": {
            "tables": table_identifiers(catalog, schema)
        },
        "instructions": {
            # Sorted by id (IDs already in ascending order).
            "example_question_sqls": [
                {
                    "id": "20000000000000000000000000000001",
                    "question": [
                        "How many SKUs are in each traffic-light colour band right now?"
                    ],
                    "sql": [
                        f"SELECT colour, COUNT(DISTINCT sku_code) AS sku_count\n",
                        f"FROM {s}.projection_snapshot\n",
                        f"WHERE updated_at = (\n",
                        f"  SELECT MAX(updated_at) FROM {s}.projection_snapshot\n",
                        ")\n",
                        "GROUP BY colour\n",
                        "ORDER BY colour"
                    ]
                },
                {
                    "id": "20000000000000000000000000000002",
                    "question": [
                        "Which SKUs have a stock-out in the next 4 weeks?"
                    ],
                    "sql": [
                        f"SELECT ps.sku_code, sk.description,\n",
                        f"       ps.week_key, ps.close, ps.colour\n",
                        f"FROM {s}.projection_snapshot ps\n",
                        f"JOIN {s}.sku sk ON ps.sku_code = sk.sku_code\n",
                        "WHERE ps.close <= 0\n",
                        "  AND ps.horizon_index <= 4\n",
                        "ORDER BY ps.horizon_index, ps.sku_code"
                    ]
                },
                {
                    "id": "20000000000000000000000000000003",
                    "question": [
                        "What is the total planned production per SKU across the full horizon?"
                    ],
                    "sql": [
                        f"SELECT sk.description, pl.sku_code,\n",
                        f"       SUM(pl.planned_qty) AS total_planned_ea\n",
                        f"FROM {s}.plan_line pl\n",
                        f"JOIN {s}.sku sk ON pl.sku_code = sk.sku_code\n",
                        "GROUP BY sk.description, pl.sku_code\n",
                        "ORDER BY total_planned_ea DESC"
                    ]
                },
                {
                    "id": "20000000000000000000000000000004",
                    "question": [
                        "Show the demand forecast for OAK UHT CHOCOLATE 500ML week by week."
                    ],
                    "sql": [
                        f"SELECT d.week_key,\n",
                        f"       d.forecast, d.sales_order,\n",
                        f"       GREATEST(d.forecast, d.sales_order) AS demand\n",
                        f"FROM {s}.demand d\n",
                        "WHERE d.sku_code = '61108'\n",
                        "ORDER BY d.week_key"
                    ]
                },
                {
                    "id": "20000000000000000000000000000005",
                    "question": [
                        "What is the average forward cover (in weeks) per SKU?"
                    ],
                    "sql": [
                        f"SELECT sk.description, ps.sku_code,\n",
                        f"       ROUND(AVG(ps.cover_weeks), 1) AS avg_cover_weeks\n",
                        f"FROM {s}.projection_snapshot ps\n",
                        f"JOIN {s}.sku sk ON ps.sku_code = sk.sku_code\n",
                        "GROUP BY sk.description, ps.sku_code\n",
                        "ORDER BY avg_cover_weeks ASC"
                    ]
                },
            ],
            # Exactly one text_instructions item (API rejects more than one).
            "text_instructions": [
                {
                    "id": "30000000000000000000000000000001",
                    "content": [
                        "You are a supply planning assistant for the Lactalis PET line"
                        " (OAK and PAULS flavoured milks, 400 ml and 500 ml).\n",
                        "\n",
                        "DOMAIN GLOSSARY\n",
                        "- SOH (stock on hand): closing stock at the end of a week."
                        " Stored in projection_snapshot.close."
                        " When close <= 0 the SKU is in a stock-out for that week.\n",
                        "- Forward cover weeks: how many full future weeks of demand the"
                        " current closing stock covers (integer 0-3, stored in"
                        " projection_snapshot.cover_weeks).\n",
                        "- QA hold (default 2 weeks): production completed in week W is"
                        " only available as receipts in week W+2 because it must pass"
                        " quality assurance before it can be released to stock.\n",
                        "- demand = MAX(forecast, sales_order). This is the open business"
                        " decision default. Raw values are in the demand table"
                        " (columns: forecast, sales_order). The resolved figure is in"
                        " projection_snapshot.demand.\n",
                        "- raw: unclamped closing stock (closePrev + recv - demand),"
                        " can be negative. Stored in projection_snapshot.raw.\n",
                        "\n",
                        "TRAFFIC-LIGHT COLOUR MEANINGS (projection_snapshot.colour)\n",
                        "- dark_blue:  cover > 3 weeks. Healthy, ample stock.\n",
                        "- light_blue: cover 2-3 weeks.\n",
                        "- green:      cover 1-2 weeks.\n",
                        "- amber:      cover < 1 week. Warning, approaching stock-out.\n",
                        "- red:        stock-out, but more than reaction_window weeks away"
                        " (plan at risk).\n",
                        "- dark_red:   stock-out within the reaction_window. Urgent, lost"
                        " sale risk.\n",
                        "- black:      over-cover past the MLOR (minimum life on receipt)"
                        " window. Waste risk.\n",
                        "\n",
                        "TABLES\n",
                        "- sku: master data for the 11 PET SKUs"
                        " (sku_code, description, pack_size_ml, priority, max_cover_weeks).\n",
                        "- week: planning horizon"
                        " (week_key, week_commencing date, horizon_index,"
                        " is_locked, maintenance_type).\n",
                        "- demand: per-SKU per-week demand inputs"
                        " (sku_code, week_key, forecast, sales_order).\n",
                        "- plan_line: production schedule"
                        " (sku_code, week_key, planned_qty, orig_qty, is_locked).\n",
                        "- projection_snapshot: computed supply projection written by the"
                        " planning engine on each save."
                        " Columns: sku_code, week_key, horizon_index, opening, recv,"
                        " prod, demand, raw, close, cover_weeks, severity, colour,"
                        " updated_at.\n",
                        "  To query the latest snapshot, filter:"
                        " WHERE updated_at = (SELECT MAX(updated_at)"
                        " FROM <schema>.projection_snapshot).\n",
                        "\n",
                        "ANSWER STYLE\n",
                        "- When asked about stock health, summarise by colour band and"
                        " call out any dark_red or stock-out cells explicitly.\n",
                        "- When asked about a specific SKU, join sku on sku_code to"
                        " show the description alongside the code.\n",
                        "- Refer to weeks by week_key (e.g. 2026-W35) or"
                        " week_commencing date from the week table.\n",
                        "- For cover questions, use projection_snapshot.cover_weeks"
                        " (integer band) not a computed average of raw close/demand.\n",
                    ]
                }
            ]
        }
    }

    return json.dumps(space_obj, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def ensure_space(
    client: "WorkspaceClient",
    catalog: str,
    schema: str,
    warehouse_id: str,
) -> str:
    """Create (or reuse) the PET Line Planner Genie space and return its id.

    Idempotent: if a space whose title matches SPACE_TITLE already exists in
    the workspace, its id is returned without creating a duplicate.

    Args:
        client:       A configured databricks.sdk.WorkspaceClient.
        catalog:      Unity Catalog catalog name (e.g. "deep_test_1_catalog").
        schema:       Schema name within catalog (e.g. "lactalis_pet_planner").
        warehouse_id: SQL warehouse id to attach to the Genie space.

    Returns:
        The Genie space id string.

    Raises:
        RuntimeError if the API returns a response with no recognisable id.
    """
    # Use a schema-scoped title so this deploy creates a fresh space for
    # lactalis_pet_planner and does NOT reuse any space from a prior effort
    # (e.g. an older space pointing at the deprecated lactalis_pet tables).
    title = _full_space_title(schema)

    # ------------------------------------------------------------------
    # 1. Check for an existing space with the schema-scoped title.
    # ------------------------------------------------------------------
    resp: dict = client.api_client.do("GET", "/api/2.0/genie/spaces")
    # The list key is "genie_spaces" per the REST API convention; fall back to
    # "spaces" defensively in case the key differs across workspace versions.
    spaces: list[dict] = (
        resp.get("genie_spaces")
        or resp.get("spaces")
        or []
    )
    for sp in spaces:
        if sp.get("title") == title:
            space_id: str | None = sp.get("space_id") or sp.get("id")
            if space_id:
                logger.info(
                    "Reusing existing Genie space '%s' (id=%s)", title, space_id
                )
                return space_id

    # ------------------------------------------------------------------
    # 2. Ensure the parent workspace path exists.
    # ------------------------------------------------------------------
    logger.info("Creating workspace path: %s", _PARENT_PATH)
    client.workspace.mkdirs(_PARENT_PATH)

    # ------------------------------------------------------------------
    # 3. Create the Genie space.
    # ------------------------------------------------------------------
    payload: dict = {
        "warehouse_id": warehouse_id,
        "title": title,
        "description": (
            "Natural language supply planning assistant for the Lactalis PET line."
            " Ask questions about stock health, forward cover, production plans,"
            " demand forecasts, and traffic-light colour distributions."
        ),
        "parent_path": _PARENT_PATH,
        "serialized_space": build_serialized_space(catalog, schema),
    }
    logger.info("Creating Genie space: %s", title)
    created: dict = client.api_client.do("POST", "/api/2.0/genie/spaces", body=payload)

    space_id = created.get("space_id") or created.get("id")
    if not space_id:
        raise RuntimeError(
            f"Genie space creation returned no id. Response keys: {list(created.keys())}"
        )

    logger.info("Created Genie space '%s' (id=%s)", title, space_id)
    return space_id
