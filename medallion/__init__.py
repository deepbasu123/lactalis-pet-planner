"""medallion — the backend rule/table engine for the Lactalis PET Line Planner.

This package is the SINGLE SOURCE OF TRUTH for all business logic. The same SQL
builders in ``gold_sql`` are used by:

  * the Lakeflow Declarative Pipeline (batch: Excel -> Bronze -> Silver -> Gold), and
  * the FastAPI app's interactive recompute (a warm serverless SQL warehouse re-runs
    the identical Gold SQL after a planner edit).

Nothing in the app or the React frontend computes projections, colours, or rule
breaches any more; they read the Gold tables/views this package defines.
"""
