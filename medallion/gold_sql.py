"""medallion/gold_sql.py

The PET Line Planner rule engine, expressed as parameterised Databricks SQL.

This module builds the SQL that computes the Gold layer from the Silver tables.
It is the one place the projection + capacity rules live, imported by BOTH the
Lakeflow pipeline (batch materialisation) and the FastAPI app (interactive
recompute on the serverless warehouse), so there is exactly one implementation
of the maths.

Rule coverage (this reproduces the ORIGINAL app's computed rule set, proven
number-for-number at commit b081e51 -- it does not add rules the original
lacked):
  Implemented: RULE-001/002 (pack-size / SKU-count), 003/004 (ceilings),
  005 (configurable params), 006 (maintenance), 007 (changeovers), 008 (2-week
  QA receipt lag), 009/010 (floor-at-zero carryforward), 011 (reaction-window
  lost sale), 012 (traffic-light colours), 013/014/015 (max-cover / black
  over-cover), 020/021 (locked weeks / priority).
  Deferred (also absent from the original app): RULE-016 -- demand is
  GREATEST(forecast, sales_order) for EVERY week; the 4-week effective-demand
  horizon (forecast-only beyond demand_horizon) is not applied. RULE-017
  (add distributor demand) -- a business "candidate"; distr_demand_planned/tlb
  land in Silver but are not consumed. RULE-018/019 (manual material holds / ETA
  buffer) -- planner data-entry, not a computed rule.

Design notes
------------
* Every threshold is read from ``silver.parameter`` at query time (RULE-005:
  capacity/QA/reaction/horizon are configurable, never hard-coded here).
* The stock projection (RULE-008 two-week QA receipt lag, RULE-009 closing =
  prior + receipts - demand, RULE-010 floor-at-zero carry-forward) is a
  recursive CTE. Verified to run on the DEFAULT serverless warehouse; all
  numeric columns are cast to DOUBLE so the anchor and recursive terms merge.
* Cover / severity / colour (RULE-011 reaction window, RULE-012 traffic lights,
  RULE-013/014/015 max-cover + black over-cover) reproduce ``backend/engine.py``
  band-for-band.
* Capacity (RULE-001 one pack size, RULE-002 <=3 SKUs, RULE-003/004 ceilings,
  RULE-006 maintenance, RULE-007 changeovers) reproduces ``backend/capacity.py``.

The builders return SQL text. They never execute anything themselves.
"""
from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Reference to a set of Silver tables
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Silver:
    """Fully-qualified location of the Silver tables (one catalog, one schema)."""
    catalog: str
    schema: str

    def t(self, name: str) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`{name}`"


def _sql_str(value: str) -> str:
    """Safely embed a short identifier-like literal (scenario id) in SQL."""
    return "'" + value.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Shared CTEs
# ---------------------------------------------------------------------------

def _params_cte(s: Silver) -> str:
    """Flatten the parameter table into one row of named scalars."""
    return f"""params AS (
  SELECT
    MAX(CASE WHEN name = 'qa_hold_weeks'          THEN value END) AS qa_weeks,
    MAX(CASE WHEN name = 'demand_horizon'         THEN value END) AS demand_horizon,
    MAX(CASE WHEN name = 'reaction_window'        THEN value END) AS reaction_window,
    MAX(CASE WHEN name = 'cap_400ml_1_2_sku'      THEN value END) AS cap_400_12,
    MAX(CASE WHEN name = 'cap_400ml_3_sku'        THEN value END) AS cap_400_3,
    MAX(CASE WHEN name = 'cap_500ml_changeover'   THEN value END) AS cap_500_co,
    MAX(CASE WHEN name = 'cap_500ml_steady_1_2'   THEN value END) AS cap_500_steady,
    MAX(CASE WHEN name = 'cap_500ml_3sku_penalty' THEN value END) AS cap_500_penalty
  FROM {s.t('parameter')}
)"""


def _effective_plan_cte(s: Silver, scenario: str, plan_col: str, use_overlay: bool) -> str:
    """Effective production plan = app-owned overlay over the pipeline-seeded baseline.

    ``plan_col`` is 'planned_qty' (working plan) or 'orig_qty' (original baseline).
    When ``use_overlay`` is False the overlay is ignored (used for the "original"
    comparison column in the summary).
    """
    if use_overlay:
        qty_expr = f"COALESCE(o.planned_qty, p.`{plan_col}`)"
        join = f"""LEFT JOIN (
      SELECT sku_code, week_key, planned_qty
      FROM {s.t('plan_overlay')}
      WHERE scenario_id = {_sql_str(scenario)}
    ) o ON o.sku_code = p.sku_code AND o.week_key = p.week_key"""
    else:
        qty_expr = f"p.`{plan_col}`"
        join = ""
    return f"""eff_plan AS (
  SELECT p.sku_code, p.week_key, CAST({qty_expr} AS DOUBLE) AS qty
  FROM {s.t('plan_line')} p
  {join}
)"""


# ---------------------------------------------------------------------------
# Supply grid (the projection + traffic lights) — reproduces build_supply()
# ---------------------------------------------------------------------------

def supply_select(
    s: Silver,
    scenario: str = "working",
    plan_col: str = "planned_qty",
    use_overlay: bool = True,
) -> str:
    """SELECT producing the full supply grid for every Active SKU x week.

    Columns: sku_code, week_key, horizon_index, opening, recv, prod, demand,
    raw, close, cover_weeks, severity, colour, display_value, is_lost_sale.
    """
    return f"""WITH
{_params_cte(s)},
{_effective_plan_cte(s, scenario, plan_col, use_overlay)},
base AS (
  SELECT
    w.horizon_index                              AS hi,
    s0.sku_code                                  AS sku_code,
    w.week_key                                   AS week_key,
    CAST(s0.max_cover_weeks AS DOUBLE)           AS max_cover_weeks,
    CAST(GREATEST(d.forecast, d.sales_order) AS DOUBLE) AS demand,
    CAST(COALESCE(ep.qty, 0.0) AS DOUBLE)        AS prod
  FROM {s.t('week')} w
  CROSS JOIN {s.t('sku')} s0
  JOIN {s.t('demand')} d ON d.sku_code = s0.sku_code AND d.week_key = w.week_key
  LEFT JOIN eff_plan ep    ON ep.sku_code = s0.sku_code AND ep.week_key = w.week_key
  WHERE s0.status = 'Active'
),
recv AS (
  -- RULE-008: production entered in week (hi - qa_weeks) is available now.
  SELECT
    b.sku_code, b.hi, b.week_key, b.demand, b.prod, b.max_cover_weeks,
    CAST(COALESCE(pr.prod, 0.0) AS DOUBLE) AS recv
  FROM base b
  CROSS JOIN params pm
  LEFT JOIN base pr
    ON pr.sku_code = b.sku_code
   AND pr.hi = b.hi - CAST(pm.qa_weeks AS INT)
),
opening AS (
  SELECT sku_code, CAST(opening_ea AS DOUBLE) AS opening_ea FROM {s.t('opening_stock')}
),
-- RULE-008/009/010 projection via the closed-form (Lindley) reflected running
-- sum: close at week i = C_i minus min(0, running-min of C up to i), where
-- C_i = opening + cumulative(recv - demand). Pure window functions, no
-- recursion -- proven equal to the stepwise recurrence
-- (tests/test_engine.py::test_lindley_closed_form_matches_stepwise) and far
-- faster than a 52-iteration recursive CTE on Spark/DBSQL.
walk AS (
  SELECT
    r.sku_code, r.hi, r.week_key, r.demand, r.recv, r.prod, r.max_cover_weeks,
    CAST(o.opening_ea AS DOUBLE) AS opening0,
    CAST(o.opening_ea + SUM(r.recv - r.demand) OVER (
      PARTITION BY r.sku_code ORDER BY r.hi
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS DOUBLE) AS c
  FROM recv r
  JOIN opening o ON o.sku_code = r.sku_code
),
proj0 AS (
  SELECT
    w.sku_code, w.hi, w.week_key, w.demand, w.recv, w.prod, w.max_cover_weeks, w.opening0,
    CAST(w.c - LEAST(0.0, w.opening0, MIN(w.c) OVER (
      PARTITION BY w.sku_code ORDER BY w.hi
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )) AS DOUBLE) AS close
  FROM walk w
),
proj AS (
  SELECT
    p.sku_code, p.hi, p.week_key, p.demand, p.recv, p.prod, p.max_cover_weeks, p.close,
    CAST(COALESCE(LAG(p.close) OVER (PARTITION BY p.sku_code ORDER BY p.hi), p.opening0) AS DOUBLE) AS opening,
    CAST(COALESCE(LAG(p.close) OVER (PARTITION BY p.sku_code ORDER BY p.hi), p.opening0)
         + p.recv - p.demand AS DOUBLE) AS raw
  FROM proj0 p
),
fwd AS (
  SELECT
    p.*,
    COALESCE(LEAD(p.demand, 1) OVER (PARTITION BY p.sku_code ORDER BY p.hi), 0.0) AS d1,
    COALESCE(LEAD(p.demand, 2) OVER (PARTITION BY p.sku_code ORDER BY p.hi), 0.0) AS d2,
    COALESCE(LEAD(p.demand, 3) OVER (PARTITION BY p.sku_code ORDER BY p.hi), 0.0) AS d3,
    SUM(p.demand) OVER (
      PARTITION BY p.sku_code ORDER BY p.hi
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cum_demand,
    CAST(ROUND(p.max_cover_weeks) AS INT) AS mc_int
  FROM proj p
),
span AS (SELECT sku_code, MAX(hi) AS n FROM fwd GROUP BY sku_code),
dom AS (
  -- demand_over_maxcover = sum(demand[hi+1 .. min(hi+mc_int, N)]) (RULE-013/014).
  SELECT
    f.*,
    CASE WHEN f.mc_int > 0
         THEN COALESCE(g.cum_demand, 0.0) - f.cum_demand
         ELSE 0.0 END AS dom
  FROM fwd f
  JOIN span sp ON sp.sku_code = f.sku_code
  LEFT JOIN fwd g
    ON g.sku_code = f.sku_code
   AND g.hi = LEAST(f.hi + f.mc_int, sp.n)
),
graded AS (
  SELECT
    d.sku_code, d.week_key, d.hi AS horizon_index,
    d.opening, d.recv, d.prod, d.demand, d.raw, d.close,
    CASE
      WHEN d.close <= 0             THEN 0
      WHEN d.close <  d.d1          THEN 0
      WHEN d.close <  d.d1 + d.d2   THEN 1
      WHEN d.close <  d.d1 + d.d2 + d.d3 THEN 2
      ELSE 3
    END AS cover_weeks,
    CASE
      WHEN d.close <= 0 THEN (CASE WHEN d.hi <= pm.reaction_window THEN 1 ELSE 2 END)
      WHEN d.dom > 0 AND d.close > d.dom THEN 7
      WHEN d.close <  d.d1                THEN 3
      WHEN d.close <  d.d1 + d.d2         THEN 4
      WHEN d.close <  d.d1 + d.d2 + d.d3  THEN 5
      ELSE 6
    END AS severity,
    CASE WHEN d.raw < 0 THEN d.raw ELSE d.close END AS display_value
  FROM dom d
  CROSS JOIN params pm
)
SELECT
  g.sku_code, g.week_key, g.horizon_index,
  g.opening, g.recv, g.prod, g.demand, g.raw, g.close,
  g.cover_weeks, g.severity,
  CASE g.severity
    WHEN 1 THEN 'dark_red'
    WHEN 2 THEN 'red'
    WHEN 3 THEN 'amber'
    WHEN 4 THEN 'green'
    WHEN 5 THEN 'light_blue'
    WHEN 6 THEN 'dark_blue'
    WHEN 7 THEN 'black'
  END AS colour,
  g.display_value,
  (g.severity = 1) AS is_lost_sale
FROM graded g
"""


# ---------------------------------------------------------------------------
# Production / capacity grid — reproduces build_production()
# ---------------------------------------------------------------------------

def production_select(
    s: Silver,
    scenario: str = "working",
    plan_col: str = "planned_qty",
    use_overlay: bool = True,
) -> str:
    """SELECT producing per-week capacity flags (R1..R4, over, ceiling, changeover)."""
    return f"""WITH
{_params_cte(s)},
{_effective_plan_cte(s, scenario, plan_col, use_overlay)},
prod AS (
  SELECT
    w.week_key, w.horizon_index AS hi, w.maintenance_type,
    s0.sku_code, CAST(s0.pack_size_ml AS INT) AS pack_ml,
    CAST(COALESCE(ep.qty, 0.0) AS DOUBLE) AS qty
  FROM {s.t('week')} w
  CROSS JOIN {s.t('sku')} s0
  LEFT JOIN eff_plan ep ON ep.sku_code = s0.sku_code AND ep.week_key = w.week_key
  WHERE s0.status = 'Active'
),
wk AS (
  SELECT
    week_key, hi, maintenance_type,
    SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END)                 AS total,
    COUNT(CASE WHEN qty > 0 THEN 1 END)                        AS n_skus,
    COUNT(DISTINCT CASE WHEN qty > 0 THEN pack_ml END)         AS n_packs,
    MIN(CASE WHEN qty > 0 THEN pack_ml END)                    AS pack_ml
  FROM prod
  GROUP BY week_key, hi, maintenance_type
),
prod_weeks AS (
  SELECT
    week_key, hi, pack_ml,
    LAG(pack_ml) OVER (ORDER BY hi) AS prev_pack
  FROM wk
  WHERE total > 0 AND pack_ml IS NOT NULL
),
co AS (
  SELECT week_key,
    (prev_pack IS NOT NULL AND pack_ml <> prev_pack) AS is_changeover
  FROM prod_weeks
),
graded AS (
  SELECT
    wk.week_key, wk.hi, wk.maintenance_type, wk.total, wk.n_skus, wk.n_packs, wk.pack_ml,
    COALESCE(co.is_changeover, FALSE) AS is_changeover,
    CASE
      WHEN wk.n_skus = 0 OR wk.pack_ml IS NULL THEN NULL
      WHEN wk.pack_ml = 400 AND wk.n_skus >= 3 THEN pm.cap_400_3
      WHEN wk.pack_ml = 400                    THEN pm.cap_400_12
      WHEN wk.pack_ml = 500 THEN
        (CASE WHEN COALESCE(co.is_changeover, FALSE) THEN pm.cap_500_co ELSE pm.cap_500_steady END)
        - (CASE WHEN wk.n_skus >= 3 THEN pm.cap_500_penalty ELSE 0 END)
      ELSE NULL
    END AS ceiling
  FROM wk
  LEFT JOIN co ON co.week_key = wk.week_key
  CROSS JOIN params pm
)
SELECT
  week_key, hi AS horizon_index, maintenance_type,
  total, n_skus, n_packs, pack_ml, is_changeover, ceiling,
  (n_packs > 1)                                        AS r1,
  (n_skus > 3)                                         AS r2,
  (ceiling IS NOT NULL AND total > ceiling)            AS r3,
  (maintenance_type = 'Full' AND total > 0)            AS r4,
  (n_skus > 0 AND ceiling IS NULL)                     AS no_rule,
  CASE WHEN ceiling IS NOT NULL AND total > ceiling
       THEN CAST(total - ceiling AS BIGINT) ELSE 0 END AS over_units
FROM graded
ORDER BY horizon_index
"""


# ---------------------------------------------------------------------------
# Summary — colour distribution (reproduces summary())
# ---------------------------------------------------------------------------

def summary_select(s: Silver, scenario: str = "working") -> str:
    """Colour counts for the current working supply grid."""
    inner = supply_select(s, scenario=scenario, plan_col="planned_qty", use_overlay=True)
    return f"""SELECT colour, COUNT(*) AS n
FROM (
{inner}
) grid
GROUP BY colour
"""


# ---------------------------------------------------------------------------
# Materialisation (used by the pipeline's Gold step and the app recompute)
# ---------------------------------------------------------------------------

def materialize_statements(silver: Silver, gold: Silver, scenario: str = "working") -> list[str]:
    """CREATE OR REPLACE TABLE statements that materialise the Gold layer.

    Returns projection, week_capacity and summary as Delta tables in ``gold``.
    """
    return [
        f"CREATE OR REPLACE TABLE {gold.t('projection')} AS\n{supply_select(silver, scenario)}",
        f"CREATE OR REPLACE TABLE {gold.t('week_capacity')} AS\n{production_select(silver, scenario)}",
        f"CREATE OR REPLACE TABLE {gold.t('summary')} AS\n{summary_select(silver, scenario)}",
    ]
